
"""Servicio gateway - API de entrada.

Único servicio expuesto al host (puerto 8000). Recibe las peticiones del simulador y las
reparte entre los servicios internos: registro (MongoDB), sesión (Valkey), historial
(lago Parquet) y analítica (Spark). Valida lo básico, propaga X-Request-Id, convierte los
fallos de los servicios internos en 503 y publica en el historial los eventos que le
devuelve el registro.
"""

from fastapi import Request,Header, Response

from app.cliente_interno import ServicioNoDisponible, cerrar_cliente
from app.comun import SERVICIO, ahora_iso, crear_app, error
from app import eventos

from uuid import UUID

from pydantic import BaseModel, Field

from app import asignacion

from app.cliente_interno import solicitar

from app import envio

from app import limpiezas

from app import liberacion


from app import salud as salud_capas

from typing import Literal

from fastapi.responses import JSONResponse
from pydantic import model_validator

from app import revision

# Aplicación FastAPI con el middleware de X-Request-Id y el formato de error común.
app = crear_app(SERVICIO)


class SolicitudSiguiente(BaseModel):
    """Cuerpo de POST /tareas/siguiente: proyecto opcional para limitar la búsqueda."""
    proyecto_id: UUID | None = None


@app.post("/tareas/siguiente")
async def tareas_siguiente(
    request: Request,
    cuerpo: SolicitudSiguiente,
    anotador_id: UUID = Header(alias="X-Anotador-Id"),
):
    """Asigna al anotador la siguiente tarea disponible (o 204 si no hay ninguna)."""
    codigo, datos = await asignacion.siguiente(
        anotador_id=anotador_id,
        proyecto_id=cuerpo.proyecto_id,
        request_id=request.state.request_id,
    )

    if codigo == 204:
        return Response(status_code=204)

    if codigo != 200:
        return error(
            codigo,
            datos.get("error", "error_registro"),
            datos.get("detalle"),
        )

    return datos



@app.on_event("startup")
async def startup():
    """Al arrancar: inicia el publicador de eventos y el proceso de limpiezas pendientes."""
    await eventos.iniciar_publicador()
    await limpiezas.iniciar()


@app.on_event("shutdown")
async def shutdown():
    """Al apagar: detiene las tareas de fondo y cierra el cliente HTTP compartido."""
    await limpiezas.detener()
    await eventos.detener_publicador()
    await cerrar_cliente()



@app.exception_handler(ServicioNoDisponible)
async def servicio_no_disponible(_request: Request, exc: ServicioNoDisponible):
    """Si un servicio interno no responde (o responde 5xx), el cliente recibe 503 con su nombre."""
    return error(
        503,
        "servicio_no_disponible",
        {
            "servicio": exc.servicio,
            "mensaje": exc.detalle,
        },
    )


@app.get("/salud")
async def salud():
    """GET /salud: estado del gateway y de todas las capas internas."""
    return await salud_capas.consultar_salud()



async def renovar_registro(tarea_id, token, anotador_id, request_id) -> bool | None:
    """Corre `asignacion.vence_en` en MongoDB junto con el lock de Valkey.

    True/False: el registro respondió (False = no hacía falta escribir todavía). None: el registro
    no respondió; el lock de Valkey ya se renovó y el próximo latido lo vuelve a intentar.
    Lanza AsignacionVencida si el registro ya no tiene la tarea abierta con ese token.
    """
    try:
        respuesta = await solicitar(
            "registro", "POST", f"/tareas/{tarea_id}/transicion",
            json={"accion": "renovar", "token": str(token), "actor_id": str(anotador_id)},
            request_id=request_id,
        )
    except ServicioNoDisponible:
        return None
    # 200: el registro respondió; 404/409: la asignación ya no es de este token.
    if respuesta.status_code == 200:
        return bool(respuesta.json().get("renovado"))
    if respuesta.status_code in (404, 409):
        raise AsignacionVencida(respuesta.json())
    return None


class AsignacionVencida(Exception):
    """El registro ya no tiene la tarea abierta con ese token (la asignación venció)."""
    pass


class SolicitudBorrador(BaseModel):
    """Cuerpo de PUT /tareas/{id}/borrador: token de la asignación y contenido libre."""
    token: UUID
    contenido: dict = Field(...)


@app.put("/tareas/{tarea_id}/borrador")
async def guardar_borrador(
    request: Request,
    tarea_id: UUID,
    cuerpo: SolicitudBorrador,
    anotador_id: UUID = Header(alias="X-Anotador-Id"),
):
    """Guarda el borrador en sesión (renueva el lock) y extiende también el plazo en el registro."""
    respuesta = await solicitar(
        "sesion",
        "PUT",
        f"/borradores/{tarea_id}",
        json={
            "anotador_id": str(anotador_id),
            "token": str(cuerpo.token),
            "contenido": cuerpo.contenido,
        },
        request_id=request.state.request_id,
    )

    datos = respuesta.json()

    # Error de sesión (409 lock perdido, 422...): se devuelve tal cual al cliente.
    if respuesta.status_code != 200:
        return error(
            respuesta.status_code,
            datos.get("error", "error_borrador"),
            datos.get("detalle"),
        )

    try:
        datos["registro_renovado"] = await renovar_registro(
            tarea_id, cuerpo.token, anotador_id, request.state.request_id
        )
    except AsignacionVencida as exc:
        return error(409, "asignacion_vencida", exc.args[0].get("detalle"))

    return datos



class SolicitudEnviar(BaseModel):
    """Cuerpo de POST /tareas/{id}/enviar: token y anotación final."""
    token: UUID
    anotacion: dict


@app.post("/tareas/{tarea_id}/enviar")
async def enviar_anotacion(
    request: Request,
    tarea_id: UUID,
    cuerpo: SolicitudEnviar,
    anotador_id: UUID = Header(alias="X-Anotador-Id"),
):
    """Envía la anotación final al registro (ver app/envio.py)."""
    codigo, datos = await envio.enviar(
        tarea_id=tarea_id,
        anotador_id=anotador_id,
        token=cuerpo.token,
        anotacion=cuerpo.anotacion,
        request_id=request.state.request_id,
    )

    if codigo != 200:
        return error(
            codigo,
            datos.get("error", "error_envio"),
            datos.get("detalle"),
        )

    return datos

class SolicitudLiberar(BaseModel):
    """Cuerpo de POST /tareas/{id}/liberar: token de la asignación."""
    token: UUID


@app.post("/tareas/{tarea_id}/liberar")
async def liberar_tarea(
    request: Request,
    tarea_id: UUID,
    cuerpo: SolicitudLiberar,
    anotador_id: UUID = Header(alias="X-Anotador-Id"),
):
    """Devuelve la tarea a la cola sin anotarla (ver app/liberacion.py)."""
    codigo, datos = await liberacion.liberar(
        tarea_id=tarea_id,
        anotador_id=anotador_id,
        token=cuerpo.token,
        request_id=request.state.request_id,
    )

    if codigo != 200:
        return error(
            codigo,
            datos.get("error", "error_liberacion"),
            datos.get("detalle"),
        )

    return datos


@app.post("/sesiones/recuperar")
async def recuperar_sesion(
    request: Request,
    anotador_id: UUID = Header(alias="X-Anotador-Id"),
):
    """Devuelve la tarea en curso del anotador, su token y su borrador (si existe)."""
    respuesta = await solicitar(
        "sesion",
        "POST",
        f"/sesiones/{anotador_id}",
        json={"accion": "recuperar"},
        request_id=request.state.request_id,
    )

    datos = respuesta.json()

    if respuesta.status_code != 200:
        return error(
            respuesta.status_code,
            datos.get("error", "error_recuperacion"),
            datos.get("detalle"),
        )

    # Se traduce el formato interno de sesión al formato público del gateway.
    sesion = datos["sesion"]

    return {
        "tarea": {
            "tarea_id": sesion["tarea"],
            "proyecto_id": sesion["proyecto"],
            "etapa": sesion["etapa"],
        },
        "token": sesion["token"],
        # Mismo formato que el borrador de /tareas/siguiente.
        "borrador": asignacion.borrador_publico(datos.get("borrador")),
    }


# --- A4.1: rutas de proyectos, lotes y exportación (pasan directo a registro) ---

def _reenviar(respuesta):
    """Devuelve el código y el cuerpo de registro tal cual (sus errores ya usan el formato común)."""
    return JSONResponse(status_code=respuesta.status_code, content=respuesta.json())


@app.post("/proyectos", status_code=201)
async def crear_proyecto(request: Request, cuerpo: dict):
    """Crea un proyecto (reenvío directo a registro)."""
    # La validación completa (cliente, taxonomía, sla_horas) la hace registro.
    respuesta = await solicitar(
        "registro", "POST", "/proyectos",
        json=cuerpo, request_id=request.state.request_id,
    )
    return _reenviar(respuesta)


@app.post("/proyectos/{proyecto_id}/lotes", status_code=201)
async def crear_lote(request: Request, proyecto_id: UUID, cuerpo: dict):
    """Carga un lote de tareas en el proyecto (reenvío directo a registro)."""
    respuesta = await solicitar(
        "registro", "POST", f"/proyectos/{proyecto_id}/lotes",
        json=cuerpo, request_id=request.state.request_id,
    )
    return _reenviar(respuesta)


@app.get("/proyectos/{proyecto_id}/exportar")
async def exportar(
    request: Request,
    proyecto_id: UUID,
    cursor: UUID | None = None,
    limite: int = 100,
):
    """Exporta las tareas aprobadas del proyecto, paginadas con cursor."""
    params = {"limite": limite}
    if cursor is not None:
        params["cursor"] = str(cursor)

    # GET: el cliente interno lo reintenta una vez si registro no responde.
    respuesta = await solicitar(
        "registro", "GET", f"/proyectos/{proyecto_id}/exportar",
        params=params, request_id=request.state.request_id,
    )
    return _reenviar(respuesta)


# --- A4.2: revisión (empezar, aprobar, rechazar) ---

# Motivos de rechazo aceptados (los mismos del registro).
MOTIVOS = Literal[
    "ETIQUETA_INCORRECTA", "GEOMETRIA_IMPRECISA", "OBJETO_OMITIDO",
    "INSTRUCCION_NO_SEGUIDA", "OTRO",
]


class SolicitudRevision(BaseModel):
    """Cuerpo de POST /tareas/{id}/revision: acción del revisor y motivo si rechaza."""
    accion: Literal["empezar", "aprobar", "rechazar"]
    motivo: MOTIVOS | None = None

    @model_validator(mode="after")
    def motivo_en_rechazo(self):
        """Rechazar sin motivo es un error de validación (422)."""
        if self.accion == "rechazar" and self.motivo is None:
            raise ValueError("'rechazar' requiere un motivo")
        return self


@app.post("/tareas/{tarea_id}/revision")
async def revisar_tarea(
    request: Request,
    tarea_id: UUID,
    cuerpo: SolicitudRevision,
    revisor_id: UUID = Header(alias="X-Anotador-Id"),
):
    """Empieza, aprueba o rechaza la revisión de una tarea (ver app/revision.py)."""
    codigo, datos = await revision.revisar(
        tarea_id=tarea_id,
        revisor_id=revisor_id,
        accion=cuerpo.accion,
        motivo=cuerpo.motivo,
        request_id=request.state.request_id,
    )

    if codigo != 200:
        return error(
            codigo,
            datos.get("error", "error_revision"),
            datos.get("detalle"),
        )

    return datos


# --- C4.9: analítica (Q1–Q5 con Spark, solo sobre el lago Parquet) ---

# La analítica espera hasta 60 s a que la SparkSession esté lista y una consulta sobre "grande"
# tarda hasta ~35 s (evidence/P5_analitica_20261030-1410.md). Sin reintento: un timeout repetido
# volvería a lanzar la misma consulta en Spark.
TIMEOUT_ANALITICA = 180.0


@app.get("/analitica/{consulta}")
async def analitica(
    request: Request,
    consulta: Literal["q1", "q2", "q3", "q4", "q5"],
    conjunto: Literal["principal", "grande", "prueba"] = "principal",
):
    """Ejecuta la consulta analítica q1..q5 sobre el conjunto indicado."""
    respuesta = await solicitar(
        "analitica", "GET", f"/q/{consulta[1]}",
        params={"conjunto": conjunto}, request_id=request.state.request_id,
        timeout=TIMEOUT_ANALITICA, reintentar=False,
    )
    # 200 con las filas, 404 conjunto_no_existe; los 503 de Spark salen por ServicioNoDisponible.
    return _reenviar(respuesta)


# --- B4.2: latido de sesión desde el simulador ---

class SolicitudLatido(BaseModel):
    """Cuerpo de POST /sesiones/latido: token de la asignación."""
    token: UUID


@app.post("/sesiones/latido")
async def latido_sesion(
    request: Request,
    cuerpo: SolicitudLatido,
    anotador_id: UUID = Header(alias="X-Anotador-Id"),
):
    """Latido del anotador: renueva el lock en sesión y el plazo de la asignación en registro."""
    respuesta = await solicitar(
        "sesion",
        "POST",
        f"/sesiones/{anotador_id}",
        json={
            "accion": "latido",
            "token": str(cuerpo.token),
        },
        request_id=request.state.request_id,
    )

    datos = respuesta.json()

    if respuesta.status_code != 200:
        return error(
            respuesta.status_code,
            datos.get("error", "error_latido"),
            datos.get("detalle"),
        )

    try:
        datos["sesion"]["registro_renovado"] = await renovar_registro(
            datos["sesion"]["tarea_id"], cuerpo.token, anotador_id,
            request.state.request_id,
        )
    except AsignacionVencida as exc:
        return error(409, "asignacion_vencida", exc.args[0].get("detalle"))

    return datos
