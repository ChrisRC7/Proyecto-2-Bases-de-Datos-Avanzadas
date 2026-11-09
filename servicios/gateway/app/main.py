"""Servicio gateway - API de entrada."""

from app.comun import SERVICIO, ahora_iso, crear_app

app = crear_app(SERVICIO)


@app.get("/salud")
async def salud():
    return {
        "servicio": SERVICIO,
        "estado": "operativo",
        "ts": ahora_iso(),
    }