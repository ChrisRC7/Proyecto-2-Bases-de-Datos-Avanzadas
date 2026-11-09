
"""Servicio gateway - API de entrada."""

from fastapi import Request

from app.cliente_interno import ServicioNoDisponible
from app.comun import SERVICIO, ahora_iso, crear_app, error

app = crear_app(SERVICIO)


@app.exception_handler(ServicioNoDisponible)
async def servicio_no_disponible(_request: Request, exc: ServicioNoDisponible):
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
    return {
        "servicio": SERVICIO,
        "estado": "operativo",
        "ts": ahora_iso(),
    }
