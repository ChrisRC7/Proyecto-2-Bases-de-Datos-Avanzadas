
"""Servicio sesion - API interna S1 (B2.1).

Esqueleto de las seis rutas del contrato endpoints/sesion.md.
La persistencia, los locks y los borradores se implementan
en las siguientes actividades B2.
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


app = crear_app(SERVICIO)


class SolicitudLock(BaseModel):
    accion: Literal["adquirir", "renovar", "soltar"]
    token: str


class SolicitudSesion(BaseModel):
    accion: Literal["abrir", "latido", "cerrar", "recuperar"]
    tarea_id: str | None = None
    proyecto_id: str | None = None
    etapa: str | None = None
    token: str | None = None


class EstadoCapa(BaseModel):
    servicio: str
    estado: str
    detalle: dict | str | None = None

class SolicitudEliminarBorrador(BaseModel):
    anotador_id: str
    token: str

def pendiente(actividad: str):
    return error(
        501,
        "no_implementado",
        {"actividad": actividad},
    )


@app.post("/locks/{tarea_id}")
def gestionar_lock(tarea_id: str, solicitud: SolicitudLock):
    try:
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
    anotador_id = cuerpo.get("anotador_id")
    token = cuerpo.get("token")
    contenido = cuerpo.get("contenido")

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


    if resultado is None:
        return error(
            409,
            "conflicto_lock",
            "El lock vencio o el token no coincide",
        )

    if resultado == "otro_anotador":
        return error(
            409,
            "borrador_de_otro_anotador",
            "El borrador pertenece a otro anotador",
        )

    return {"estado": "ok", "borrador": resultado}




@app.post("/sesiones/{anotador_id}")
def sesiones(anotador_id: str, solicitud: SolicitudSesion):
    try:
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


            borrador = borradores.recuperar(solicitud.tarea_id)

            if borrador is not None and borrador["anotador_id"] != anotador_id:
                return error(409, "borrador_de_otro_anotador")

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


        if solicitud.accion == "recuperar":
            sesion_actual = almacenamiento_sesiones.consultar(anotador_id)

            if sesion_actual is None:
                return error(404, "sesion_no_existe")

            if not solicitud.token or sesion_actual["token"] != solicitud.token:
                return error(409, "token_incorrecto")

            tarea_id = sesion_actual["tarea"]
            borrador = borradores.recuperar(tarea_id)
            if borrador is not None and borrador["anotador_id"] != anotador_id:
                return error(409, "borrador_de_otro_anotador")

            return {
                "estado": "ok",
                "sesion": sesion_actual,
                "borrador": borrador,
            }


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
    """Comprueba conexión con Valkey y persistencia AOF (B2.8)."""

    try:
        ping = bool(locks.cliente.ping())
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


@app.delete("/borradores/{tarea_id}")
def eliminar_borrador(
    tarea_id: str,
    cuerpo: SolicitudEliminarBorrador,
):
    try:
        eliminado = borradores.eliminar(
            tarea_id,
            cuerpo.anotador_id,
            cuerpo.token,
        )
    except valkey.ValkeyError:
        return error(
            503,
            "valkey_no_disponible",
            "No se pudo eliminar el borrador",
        )

    if not eliminado:
        return error(
            409,
            "borrador_no_coincide",
            "El borrador no existe o pertenece a otro token",
        )

    return {"estado": "ok", "eliminado": True}
