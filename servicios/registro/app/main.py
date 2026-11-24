"""Servicio registro — única fuente de verdad del estado de las tareas (MongoDB, nunca Valkey).

Rutas (endpoints/registro.md): POST /proyectos · POST /proyectos/{id}/lotes ·
POST /tareas/asignar · POST /tareas/{id}/transicion · GET /proyectos/{id}/exportar · GET /salud.
"""
import json
import os
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Query, Response
from pymongo import ReturnDocument
from pymongo.errors import ConnectionFailure, PyMongoError, WriteConcernError

from app.bd import bd, crear_indices, estado_mongo
from app.comun import SERVICIO, ahora_iso, crear_app, error, log
from app.esquemas import (
    TIPOS_POR_MODALIDAD, Asignar, NuevoLote, NuevoProyecto, Transicion, error_geometria, mime_valido,
)

# Plazo de una asignación, URL del servicio de sesión y cada cuánto se publica la salud.
LOCK_SEGUNDOS = int(os.getenv("LOCK_SEGUNDOS", "120"))  # igual que el lock de Valkey
SESION_URL = os.getenv("SESION_URL", "http://sesion:8002")
LATIDO_SEGUNDOS = 5

# Aplicación FastAPI con el middleware y errores comunes.
app = crear_app(SERVICIO)

# accion -> (estado requerido, nuevo estado en Mongo, estado de la fila del lago)
REGLAS = {
    "enviar": ("abierta", "anotada", "anotada"),
    "liberar": ("abierta", "disponible", "liberada"),
    "empezar_revision": ("anotada", "en_revision", "en_revision"),
    "aprobar": ("en_revision", "aprobada", "aprobada"),
    "rechazar": ("en_revision", "disponible", "rechazada"),
}


@app.exception_handler(ConnectionFailure)
async def mongo_sin_primario(_request, exc: ConnectionFailure):
    """Cualquier petición que no encuentre primario en MongoDB responde 503."""
    # Solo la primera frase; el resto es la topología completa que ve el driver.
    return error(503, "mongodb_no_disponible", str(exc).split(", Topology Description")[0])


@app.exception_handler(WriteConcernError)
async def mongo_sin_mayoria(_request, exc: WriteConcernError):
    """Una escritura que no logró confirmación de la mayoría responde 503."""
    # wtimeout vencido o el primario se degradó mientras esperaba (11602 InterruptedDueToReplStateChange).
    # La escritura pudo quedar en el primario aunque no llegó a la mayoría: error ≠ no escrito.
    return error(503, "mongodb_sin_mayoria", str(exc).split(", full error")[0])


def ahora() -> datetime:
    """Hora actual en UTC truncada a milisegundos (la misma precisión que guarda MongoDB)."""
    t = datetime.now(timezone.utc)
    return t.replace(microsecond=t.microsecond // 1000 * 1000)  # BSON guarda milisegundos


def iso(t: datetime | None) -> str | None:
    """Convierte una fecha a texto ISO-8601 UTC con milisegundos y "Z" (None se queda None)."""
    if t is None:
        return None
    t = t.astimezone(timezone.utc)
    return f"{t:%Y-%m-%dT%H:%M:%S}.{t.microsecond // 1000:03d}Z"


def a_json(valor):
    """Fechas a ISO-8601 UTC en cualquier nivel del documento."""
    if isinstance(valor, datetime):
        return iso(valor)
    if isinstance(valor, dict):
        return {k: a_json(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [a_json(v) for v in valor]
    return valor


def evento(tarea: dict, estado: str, version: int, ts: datetime, event_id: str,
           revisor_id: str | None = None, motivo: str | None = None) -> dict:
    """Fila del lago según endpoints/historial.md; el gateway la reenvía al historial."""
    asig = tarea.get("asignacion") or {}
    return {
        "event_id": event_id,
        "ts": iso(ts),
        "tarea_id": tarea["_id"],
        "proyecto_id": tarea["proyecto_id"],
        "cliente_id": tarea["cliente_id"],
        "batch_id": tarea["batch_id"],
        "modalidad": tarea["modalidad"],
        "tipo_tarea": tarea["tipo_tarea"],
        "estado": estado,
        "version": version,
        "anotador_id": asig.get("anotador_id"),
        "equipo_id": asig.get("equipo_id"),
        "revisor_id": revisor_id,
        "motivo_rechazo": motivo,
        "tomada_ts": iso(asig.get("tomada_en")),
        "sla_horas": tarea["sla_horas"],
        "micro_lote_id": None,
    }


@app.post("/proyectos", status_code=201)
def crear_proyecto(cuerpo: NuevoProyecto):
    """Crea un proyecto activo para un cliente existente y devuelve su id (404 si no hay cliente)."""
    if bd.clientes.find_one({"_id": cuerpo.cliente_id}, {"_id": 1}) is None:
        return error(404, "cliente_no_existe", cuerpo.cliente_id)
    proyecto_id = str(uuid.uuid4())
    bd.proyectos.insert_one({
        "_id": proyecto_id,
        "cliente_id": cuerpo.cliente_id,
        "nombre": cuerpo.nombre,
        "estado": "activo",
        "modalidad": cuerpo.modalidad,
        "taxonomia": [e.model_dump() for e in cuerpo.taxonomia],
        "sla_horas": cuerpo.sla_horas,
        "prioridad": cuerpo.prioridad,
        "creado_en": ahora(),
    })
    return {"proyecto_id": proyecto_id}


@app.post("/proyectos/{proyecto_id}/lotes", status_code=201)
def crear_lote(proyecto_id: str, cuerpo: NuevoLote):
    """Crea un lote (batch) y todas sus tareas en estado "disponible".

    Valida que el proyecto exista y esté activo, y que cada tarea tenga un tipo y un MIME
    compatibles con la modalidad del proyecto; si alguna falla, no se inserta nada (422).
    """
    proyecto = bd.proyectos.find_one({"_id": proyecto_id})
    if proyecto is None:
        return error(404, "proyecto_no_existe", proyecto_id)
    if proyecto["estado"] != "activo":
        return error(422, "proyecto_cerrado", proyecto_id)

    # Revisa todas las tareas y junta los problemas para reportarlos de una vez.
    modalidad = proyecto["modalidad"]
    problemas = []
    for i, t in enumerate(cuerpo.tareas):
        if t.tipo_tarea not in TIPOS_POR_MODALIDAD[modalidad]:
            problemas.append({"tarea": i, "error": f"tipo_tarea '{t.tipo_tarea}' no corresponde a '{modalidad}'"})
        if not mime_valido(modalidad, t.archivo.mime):
            problemas.append({"tarea": i, "error": f"mime '{t.archivo.mime}' no corresponde a '{modalidad}'"})
    if problemas:
        return error(422, "lote_invalido", problemas)

    # Documento del lote y documentos de las tareas (copian datos del proyecto para no hacer joins).
    batch_id, creado = str(uuid.uuid4()), ahora()
    bd.batches.insert_one({"_id": batch_id, "proyecto_id": proyecto_id,
                           "total_tareas": len(cuerpo.tareas), "creado_en": creado})
    tareas = [{
        "_id": str(uuid.uuid4()),
        "proyecto_id": proyecto_id,
        "cliente_id": proyecto["cliente_id"],
        "batch_id": batch_id,
        "modalidad": modalidad,
        "tipo_tarea": t.tipo_tarea,
        "prioridad": t.prioridad or proyecto["prioridad"],
        "sla_horas": proyecto["sla_horas"],
        "archivo": t.archivo.model_dump(),
        "estado": "disponible",
        "version": 0,
        "asignacion": None,
        "anotacion": None,
        "ultima_revision": None,
        "ultimo_evento_id": None,
    } for t in cuerpo.tareas]
    # ordered=False: si una tarea falla, el resto del lote se inserta igual.
    insertadas = len(bd.tareas.insert_many(tareas, ordered=False).inserted_ids)
    return {"batch_id": batch_id, "tareas": insertadas}


@app.post("/tareas/asignar")
def asignar(cuerpo: Asignar):
    """Asigna de forma exclusiva la tarea disponible de mayor prioridad a un anotador.

    Devuelve la tarea (con la taxonomía del proyecto) y el evento "abierta" para el lago;
    204 si no hay tareas disponibles; 404 si el anotador no existe.
    """
    anotador = bd.anotadores.find_one({"_id": cuerpo.anotador_id}, {"equipo_id": 1})
    if anotador is None:
        return error(404, "anotador_no_existe", cuerpo.anotador_id)

    t = ahora()
    # Libre, o abierta con el plazo vencido (se vuelve a ofrecer).
    filtro = {"$or": [{"estado": "disponible"},
                      {"estado": "abierta", "asignacion.vence_en": {"$lt": t}}]}
    if cuerpo.proyecto_id:
        filtro["proyecto_id"] = cuerpo.proyecto_id
    event_id = str(uuid.uuid4())
    asignacion = {
        "anotador_id": cuerpo.anotador_id,
        "equipo_id": anotador["equipo_id"],
        "token": cuerpo.token,
        "tomada_en": t,
        "vence_en": t + timedelta(seconds=LOCK_SEGUNDOS),
    }
    # Una sola operación atómica sobre un documento: si llegan dos peticiones juntas,
    # solo una encuentra la tarea en "disponible" y la cambia a "abierta".
    tarea = bd.tareas.find_one_and_update(
        filtro,
        {"$set": {"estado": "abierta", "asignacion": asignacion, "ultimo_evento_id": event_id},
         "$inc": {"version": 1}},
        sort=[("prioridad", -1)],
        return_document=ReturnDocument.AFTER,
    )
    # Ninguna tarea cumplió el filtro: no hay trabajo disponible.
    if tarea is None:
        return Response(status_code=204)

    # La taxonomía se lee del proyecto para que el anotador sepa qué etiquetas usar.
    proyecto = bd.proyectos.find_one({"_id": tarea["proyecto_id"]}, {"taxonomia": 1}) or {}
    return {
        "tarea": a_json({
            "tarea_id": tarea["_id"],
            "proyecto_id": tarea["proyecto_id"],
            "modalidad": tarea["modalidad"],
            "tipo_tarea": tarea["tipo_tarea"],
            "archivo": tarea["archivo"],
            "taxonomia": proyecto.get("taxonomia", []),
            "estado": tarea["estado"],
            "version": tarea["version"],
            "vence_en": asignacion["vence_en"],
        }),
        "evento": evento(tarea, "abierta", tarea["version"], t, event_id),
    }


def renovar(tarea_id: str, token: str):
    """Corre el plazo de la asignación otros LOCK_SEGUNDOS, igual que el latido renueva el lock.

    Solo si la tarea sigue abierta con ese token y el plazo no ha vencido: una asignación vencida
    ya pudo ofrecerse a otro y no se revive. Para no escribir en MongoDB en cada latido (cada 2 s
    por anotador), solo escribe cuando queda menos de la mitad del plazo; si no, responde
    `renovado: false`. No cambia la versión ni genera evento (no es una transición de estado).
    """
    t = ahora()
    vence = t + timedelta(seconds=LOCK_SEGUNDOS)
    propia = {"_id": tarea_id, "estado": "abierta", "asignacion.token": token}
    # Solo actualiza si es propia, no ha vencido y ya pasó la mitad del plazo.
    resultado = bd.tareas.update_one(
        {**propia, "asignacion.vence_en": {"$gte": t, "$lt": t + timedelta(seconds=LOCK_SEGUNDOS / 2)}},
        {"$set": {"asignacion.vence_en": vence}},
    )
    if resultado.matched_count == 1:
        return {"tarea_id": tarea_id, "renovado": True, "vence_en": iso(vence)}

    # No se escribió: ¿sigue vigente (simplemente no hacía falta renovar todavía)?
    vigente = bd.tareas.find_one({**propia, "asignacion.vence_en": {"$gte": t}}, {"asignacion.vence_en": 1})
    if vigente is not None:
        return {"tarea_id": tarea_id, "renovado": False, "vence_en": iso(vigente["asignacion"]["vence_en"])}

    # Ni propia ni vigente: o la tarea no existe (404) o la asignación ya venció (409).
    actual = bd.tareas.find_one({"_id": tarea_id}, {"estado": 1, "version": 1})
    if actual is None:
        return error(404, "tarea_no_existe", tarea_id)
    return error(409, "asignacion_vencida", {"estado": actual["estado"], "version": actual["version"]})


@app.post("/tareas/{tarea_id}/transicion")
def transicion(tarea_id: str, cuerpo: Transicion):
    """Aplica una transición de estado a la tarea (enviar, liberar, revisar, aprobar, rechazar).

    Solo cambia si la tarea está en el estado requerido por REGLAS (y, si aplica, con el
    token y la versión indicados). Devuelve el nuevo estado y el evento para el lago;
    409 si la transición no es válida, 404 si no existe, 422 si la anotación es inválida.
    """
    if cuerpo.accion == "renovar":  # sin cambio de estado ni evento (la ruta 7 pasaría el límite de 6)
        return renovar(tarea_id, cuerpo.token)
    # Estado requerido, estado nuevo y estado que se registra en el lago para esta acción.
    requerido, nuevo, estado_evento = REGLAS[cuerpo.accion]
    t = ahora()
    event_id = str(uuid.uuid4())
    filtro: dict = {"_id": tarea_id, "estado": requerido}
    cambios: dict = {"estado": nuevo, "ultimo_evento_id": event_id}
    revisor_id = motivo = None

    # Enviar y liberar solo los puede hacer quien tiene la asignación (mismo token).
    if cuerpo.accion in ("enviar", "liberar"):
        filtro["asignacion.token"] = cuerpo.token
    # Control optimista: si el cliente manda la versión, debe coincidir con la actual.
    if cuerpo.version is not None:
        filtro["version"] = cuerpo.version

    if cuerpo.accion == "enviar":
        # Valida la geometría según el tipo de tarea y las etiquetas contra la taxonomía.
        actual = bd.tareas.find_one({"_id": tarea_id}, {"tipo_tarea": 1, "proyecto_id": 1})
        if actual is None:
            return error(404, "tarea_no_existe", tarea_id)
        problema = error_geometria(actual["tipo_tarea"], cuerpo.anotacion.geometria)
        proyecto = bd.proyectos.find_one({"_id": actual["proyecto_id"]}, {"taxonomia": 1}) or {}
        validas = {e["etiqueta"] for e in proyecto.get("taxonomia", [])}
        fuera = [e for e in cuerpo.anotacion.etiquetas if e not in validas]
        if problema or fuera:
            return error(422, "anotacion_invalida",
                         {"geometria": problema, "etiquetas_fuera_de_taxonomia": fuera})
        cambios["anotacion"] = {**cuerpo.anotacion.model_dump(), "anotador_id": cuerpo.actor_id, "enviada_en": t}
    elif cuerpo.accion == "liberar":
        # La tarea vuelve a estar disponible sin dueño.
        cambios["asignacion"] = None
    else:
        # empezar_revision / aprobar / rechazar: solo un anotador con rol "revisor".
        if bd.anotadores.find_one({"_id": cuerpo.actor_id, "rol": "revisor"}, {"_id": 1}) is None:
            return error(422, "actor_no_es_revisor", cuerpo.actor_id)
        revisor_id = cuerpo.actor_id
        resultado = {"empezar_revision": None, "aprobar": "aprobada", "rechazar": "rechazada"}[cuerpo.accion]
        if cuerpo.accion == "rechazar":
            motivo = cuerpo.motivo
            cambios["asignacion"] = None  # vuelve a la cola para retrabajo
        cambios["ultima_revision"] = {"revisor_id": revisor_id, "resultado": resultado, "motivo": motivo, "en": t}

    # Mismo patrón que la asignación: el filtro (estado, token, versión) y el cambio van en una
    # sola operación. BEFORE conserva la asignación que el rechazo y la liberación borran.
    antes = bd.tareas.find_one_and_update(
        filtro, {"$set": cambios, "$inc": {"version": 1}}, return_document=ReturnDocument.BEFORE,
    )
    # No coincidió el filtro: se distingue entre tarea inexistente (404) y estado/token/versión (409).
    if antes is None:
        actual = bd.tareas.find_one({"_id": tarea_id}, {"estado": 1, "version": 1})
        if actual is None:
            return error(404, "tarea_no_existe", tarea_id)
        return error(409, "transicion_invalida",
                     {"accion": cuerpo.accion, "estado": actual["estado"], "version": actual["version"]})

    # Versión nueva (la de "antes" más el $inc aplicado).
    version = antes["version"] + 1
    return {
        "tarea": {"tarea_id": tarea_id, "estado": nuevo, "version": version},
        "evento": evento(antes, estado_evento, version, t, event_id, revisor_id, motivo),
    }


@app.get("/proyectos/{proyecto_id}/exportar")
def exportar(proyecto_id: str, cursor: str | None = None, limite: int = Query(100, ge=1, le=1000)):
    """Exporta las tareas aprobadas del proyecto por páginas de `limite`, ordenadas por _id.

    `siguiente_cursor` es el último _id de la página (se pasa como `cursor` para pedir la
    siguiente) o None si ya no hay más.
    """
    if bd.proyectos.find_one({"_id": proyecto_id}, {"_id": 1}) is None:
        return error(404, "proyecto_no_existe", proyecto_id)
    filtro: dict = {"proyecto_id": proyecto_id, "estado": "aprobada"}
    if cursor:
        filtro["_id"] = {"$gt": cursor}  # paginación por llave, sin skip
    # Solo los campos que se exportan.
    campos = {"batch_id": 1, "modalidad": 1, "tipo_tarea": 1, "archivo": 1,
              "anotacion": 1, "ultima_revision": 1, "version": 1}
    docs = [a_json({"tarea_id": d.pop("_id"), **d})
            for d in bd.tareas.find(filtro, campos).sort("_id", 1).limit(limite)]
    return {"tareas": docs, "siguiente_cursor": docs[-1]["tarea_id"] if len(docs) == limite else None}


@app.get("/salud")
def salud():
    """Estado del registro según MongoDB: operativo si hay primario, caído si no."""
    # Sin primario el registro no puede asignar ni guardar: el gateway lo lee como "caido".
    mongo = estado_mongo()
    return {"servicio": SERVICIO, "estado": mongo["estado"], "mongodb": mongo, "ts": ahora_iso()}


def _latido():
    """Cada 5 s publica su estado en sesion:/estado-capas (D4). Nunca escribe en Valkey."""
    indices_listos = False
    # Hilo infinito: asegura los índices (una vez) y publica la salud cada LATIDO_SEGUNDOS.
    while True:
        if not indices_listos:
            try:
                crear_indices()
                indices_listos = True
                log.info("indices asegurados")
            except PyMongoError as exc:
                log.warning("indices pendientes", extra={"datos": {"error": str(exc).splitlines()[0]}})
        # Envía el estado de MongoDB al servicio de sesión por HTTP.
        mongo = estado_mongo()
        cuerpo = json.dumps({"servicio": SERVICIO, "estado": mongo["estado"], "detalle": {"mongodb": mongo}})
        peticion = urllib.request.Request(f"{SESION_URL}/estado-capas", data=cuerpo.encode(),
                                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(peticion, timeout=1).close()
        except Exception as exc:  # sesion caída o todavía sin la ruta: se reintenta en 5 s
            log.debug("latido sin respuesta", extra={"datos": {"error": str(exc)}})
        time.sleep(LATIDO_SEGUNDOS)


@app.on_event("startup")
def iniciar_latido():
    """Al arrancar el servicio lanza el hilo de latido en segundo plano (daemon)."""
    threading.Thread(target=_latido, name="latido", daemon=True).start()
