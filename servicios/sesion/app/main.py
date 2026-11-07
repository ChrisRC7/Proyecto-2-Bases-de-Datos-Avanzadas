
"""Servicio sesion - API interna S1 (B2.1).

Esqueleto de las seis rutas del contrato endpoints/sesion.md.
La persistencia, los locks y los borradores se implementan
en las siguientes actividades B2.
"""

from typing import Literal

from fastapi import Body
from pydantic import BaseModel

from app.comun import SERVICIO, ahora_iso, crear_app, error

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


def pendiente(actividad: str):
    return error(
        501,
        "no_implementado",
        {"actividad": actividad},
    )


@app.post("/locks/{tarea_id}")
def locks(tarea_id: str, solicitud: SolicitudLock):
    return pendiente("B2.2")


@app.put("/borradores/{tarea_id}")
def guardar_borrador(
    tarea_id: str,
    cuerpo: dict = Body(...),
):
    return pendiente("B2.4")


@app.post("/sesiones/{anotador_id}")
def sesiones(anotador_id: str, solicitud: SolicitudSesion):
    return pendiente("B2.3-B2.5")


@app.post("/estado-capas")
def publicar_estado(cuerpo: EstadoCapa):
    return pendiente("B2.7")


@app.get("/estado-capas")
def consultar_estados():
    return pendiente("B2.7")


@app.get("/salud")
def salud():
    # Pendiente B2.8: PING e INFO persistence de Valkey.
    return {
        "servicio": SERVICIO,
        "estado": "operativo",
        "ts": ahora_iso(),
    }
