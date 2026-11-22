"""Servicio sesion - API interna (endpoints/sesion.md).

Rutas (seis, contando método y ruta): POST /locks/{tarea_id} · PUT /borradores/{tarea_id} ·
POST /sesiones/{anotador_id} · POST /estado-capas · GET /estado-capas · GET /salud. Único servicio con acceso al Valkey de sesión; no decide el
estado de una tarea (eso es del registro).
"""

from typing import Literal

from fastapi import Body
from pydantic import BaseModel

from app.comun import SERVICIO, ahora_iso, crear_app, error

import valkey
from app import locks

from app import sesiones as almacenamiento_sesiones

from app import borradores
from app import estado_capas


# Aplicación FastAPI con el middleware de X-Request-Id y el formato de error común.
app = crear_app(SERVICIO)


class SolicitudLock(BaseModel):
    """Cuerpo de POST /locks/{tarea_id}: qué hacer con el lock y con qué token."""
    accion: Literal["adquirir", "renovar", "soltar"]
    token: str


class SolicitudSesion(BaseModel):
    """Cuerpo de POST /sesiones/{anotador_id}. Los campos que hacen falta dependen de la acción."""
    # borrar_borrador: limpieza del gateway tras un envío con 200 (va aquí y no como DELETE
    # /borradores para no pasar de seis rutas, D17).
    accion: Literal["abrir", "latido", "cerrar", "recuperar", "borrar_borrador"]
    tarea_id: str | None = None
    proyecto_id: str | None = None
    etapa: str | None = None
    token: str | None = None


class EstadoCapa(BaseModel):
    """Cuerpo de POST /estado-capas: estado que publica una capa del sistema."""
    servicio: str
    estado: str
    detalle: dict | str | None = None


@app.post("/locks/{tarea_id}")
def gestionar_lock(tarea_id: str, solicitud: SolicitudLock):
    """Adquiere, renueva o suelta el lock de una tarea.

    Respuestas: 200 si la operación se hizo, 409 si el lock está ocupado/vencido o el
    token no coincide, 503 si Valkey no responde.
    """
    try:
        # Se llama a la función de locks.py que corresponde a la acción pedida.
        if solicitud.accion == "adquirir":
            exito = locks.adquirir(tarea_id, solicitud.token)
        elif solicitud.accion == "renovar":
            exito = locks.renovar(tarea_id, solicitud.token)
        else:
            exito = locks.soltar(tarea_id, solicitud.token)

    except valkey.ValkeyError:
        return error(
            503,
            "valkey_no_disponible",
            "No se pudo acceder al almacenamiento de locks",
        )

    if not exito:
        return error(
            409,
            "conflicto_lock",
            "El lock está ocupado, venció o el token no coincide",
        )

    return {
        "tarea_id": tarea_id,
        "accion": solicitud.accion,
        "estado": "ok",
    }



@app.put("/borradores/{tarea_id}")
def guardar_borrador(tarea_id: str, cuerpo: dict = Body(...)):
    """Guarda el borrador de una tarea (y renueva su lock).

    El cuerpo debe traer anotador_id, token y contenido. Respuestas: 200 guardado,
    409 si el lock no es de ese token o el borrador es de otro anotador, 422 si faltan
    campos, 503 si Valkey no responde.
    """
    anotador_id = cuerpo.get("anotador_id")
    token = cuerpo.get("token")
    contenido = cuerpo.get("contenido")

    # Validación manual: el contenido puede ser cualquier JSON, por eso no hay modelo.
    if not anotador_id or not token or contenido is None:
        return error(
            422,
            "validacion",
            "Se requieren anotador_id, token y contenido",
        )

    try:
        resultado = borradores.guardar(
            tarea_id,
            anotador_id,
            token,
            contenido,
        )
    except valkey.ValkeyError:
        return error(
            503,
            "valkey_no_disponible",
            "No se pudo guardar el borrador",
        )


    # None: el token ya no tiene el lock.
    if resultado is None:
        return error(
            409,
            "conflicto_lock",
            "El lock vencio o el token no coincide",
        )

    # Ya existe un borrador de otro anotador para esta tarea.
    if resultado == "otro_anotador":
        return error(
            409,
            "borrador_de_otro_anotador",
            "El borrador pertenece a otro anotador",
        )

    return {"estado": "ok", "borrador": resultado}




@app.post("/sesiones/{anotador_id}")
def sesiones(anotador_id: str, solicitud: SolicitudSesion):
    """Maneja la sesión de un anotador según `accion`.

    - abrir: registra la sesión de una tarea recién asignada y reconcilia el borrador.
    - cerrar: borra la sesión si el token coincide.
    - recuperar: devuelve la sesión vigente y su borrador (si el lock sigue siendo suyo).
    - borrar_borrador: borra el borrador tras un envío exitoso.
    - latido: renueva el lock y marca actividad.
    Cualquier fallo de Valkey responde 503.
    """
    try:
        # ---- abrir: el gateway acaba de asignar una tarea a este anotador ----
        if solicitud.accion == "abrir":
            if not all([
                solicitud.tarea_id,
                solicitud.proyecto_id,
                solicitud.etapa,
                solicitud.token,
            ]):
                return error(
                    422,
                    "validacion",
                    "Abrir requiere tarea_id, proyecto_id, etapa y token",
                )

            if solicitud.etapa not in ("anotando", "revisando"):
                return error(422, "validacion", "Etapa no valida")



            # Conserva el borrador propio o archiva el de otro anotador (solo con lock válido).
            estado_borrador, borrador = (
                borradores.preparar_para_asignacion(
                    solicitud.tarea_id,
                    anotador_id,
                    solicitud.token,
                )
            )

            if estado_borrador == "conflicto":
                return error(
                    409,
                    "conflicto_lock",
                    "El token no posee el lock vigente",
                )


            resultado = almacenamiento_sesiones.abrir(
                anotador_id,
                solicitud.tarea_id,
                solicitud.proyecto_id,
                solicitud.etapa,
                solicitud.token,
            )

            return {
                "estado": "ok",
                "sesion": resultado,
                "borrador": borrador,
            }


        # ---- cerrar: termina la sesión del anotador ----
        if solicitud.accion == "cerrar":
            if not solicitud.token:
                return error(422, "validacion", "Falta el token")

            resultado = almacenamiento_sesiones.cerrar(
                anotador_id,
                solicitud.token,
            )

            if resultado == "no_existe":
                return error(404, "sesion_no_existe")

            if resultado == "conflicto":
                return error(409, "token_incorrecto")

            return {"estado": "ok", "accion": "cerrar"}



        # ---- recuperar: el anotador retoma su tarea (por ejemplo tras reconectarse) ----
        if solicitud.accion == "recuperar":
            sesion_actual = almacenamiento_sesiones.consultar(
                anotador_id
            )

            if sesion_actual is None:
                return error(404, "sesion_no_existe")

            # El Gateway recupera usando X-Anotador-Id.
            # Si se proporciona un token, debe coincidir.
            if (
                solicitud.token is not None
                and sesion_actual["token"] != solicitud.token
            ):
                return error(409, "token_incorrecto")

            # El lock venció o la tarea ya es de otro: la sesión no se puede retomar.
            if almacenamiento_sesiones.descartar_si_vencida(
                anotador_id, sesion_actual
            ):
                return error(
                    404,
                    "sesion_no_existe",
                    "El lock de la tarea vencio; la sesion se descarto",
                )

            tarea_id = sesion_actual["tarea"]
            borrador = borradores.recuperar(tarea_id)

            # Nunca se entrega el borrador de otro anotador.
            if (
                borrador is not None
                and borrador["anotador_id"] != anotador_id
            ):
                return error(409, "borrador_de_otro_anotador")

            return {
                "estado": "ok",
                "sesion": sesion_actual,
                "borrador": borrador,
            }




        # ---- borrar_borrador: limpieza después de un envío aceptado ----
        if solicitud.accion == "borrar_borrador":
            if not solicitud.tarea_id or not solicitud.token:
                return error(422, "validacion", "Borrar requiere tarea_id y token")

            # Solo si el borrador es de este anotador y de este token (Lua en borradores.eliminar).
            if not borradores.eliminar(solicitud.tarea_id, anotador_id, solicitud.token):
                return error(
                    409,
                    "borrador_no_coincide",
                    "El borrador no existe o pertenece a otro token",
                )

            return {"estado": "ok", "eliminado": True}


        # ---- latido: el anotador sigue trabajando; se renueva el lock ----
        if solicitud.accion == "latido":
            if not solicitud.token:
                return error(422, "validacion", "Falta el token")

            resultado = almacenamiento_sesiones.latido(
                anotador_id,
                solicitud.token,
            )

            if resultado == "no_existe":
                return error(404, "sesion_no_existe")

            if resultado == "conflicto":
                return error(
                    409,
                    "conflicto_lock",
                    "El token no coincide o el lock vencio",
                )

            return {"estado": "ok", "sesion": resultado}



    except valkey.ValkeyError:
        return error(
            503,
            "valkey_no_disponible",
            "No se pudo acceder al almacenamiento de sesiones",
        )




@app.post("/estado-capas")
def publicar_estado(cuerpo: EstadoCapa):
    """Publica el estado de una capa (expira a los 15 s). 422 si faltan datos, 503 sin Valkey."""
    if not cuerpo.servicio.strip() or not cuerpo.estado.strip():
        return error(
            422,
            "validacion",
            "Servicio y estado son obligatorios",
        )

    try:
        resultado = estado_capas.publicar(
            cuerpo.servicio,
            cuerpo.estado,
            cuerpo.detalle,
        )
    except valkey.ValkeyError:
        return error(
            503,
            "valkey_no_disponible",
            "No se pudo registrar el estado de la capa",
        )

    return {"estado": "ok", "capa": resultado}


@app.get("/estado-capas")
def consultar_estados():
    """Devuelve los estados vigentes de todas las capas. 503 si Valkey no responde."""
    try:
        return estado_capas.consultar()
    except valkey.ValkeyError:
        return error(
            503,
            "valkey_no_disponible",
            "No se pudieron consultar los estados",
        )




@app.get("/salud")
def salud():
    """Comprueba conexión con Valkey y persistencia AOF (B2.8).

    estado = "operativo" si Valkey responde y el AOF está activo y sin errores de
    escritura; "degradado" si responde pero el AOF falla; "caido" si no responde.
    """

    try:
        ping = bool(locks.cliente.ping())
        # INFO persistence trae el estado del AOF (aof_enabled, aof_last_write_status...).
        persistencia = locks.cliente.info("persistence")

        aof_activo = bool(persistencia.get("aof_enabled", 0))
        aof_escribiendo = not bool(
            persistencia.get("aof_last_write_status") == "err"
        )

        operativo = ping and aof_activo and aof_escribiendo

        return {
            "servicio": SERVICIO,
            "estado": "operativo" if operativo else "degradado",
            "valkey": {
                "ping": ping,
                "aof_activo": aof_activo,
                "aof_escribiendo": aof_escribiendo,
            },
            "ts": ahora_iso(),
        }

    except valkey.ValkeyError:
        # Valkey no responde: el servicio sigue vivo pero no puede trabajar.
        return {
            "servicio": SERVICIO,
            "estado": "caido",
            "valkey": {
                "ping": False,
                "aof_activo": False,
                "aof_escribiendo": False,
            },
            "ts": ahora_iso(),
        }
