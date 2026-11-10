"""A4.2: revisión de una tarea anotada (empezar, aprobar, rechazar)."""

from app import eventos
from app.cliente_interno import solicitar

# Nombre de la acción en el gateway -> nombre en registro.
ACCIONES = {
    "empezar": "empezar_revision",
    "aprobar": "aprobar",
    "rechazar": "rechazar",
}


async def revisar(tarea_id, revisor_id, accion, motivo=None, request_id=None):
    # Sin token ni versión: el filtro por estado en registro deja pasar una sola petición.
    # La transición NO se reintenta.
    respuesta = await solicitar(
        "registro",
        "POST",
        f"/tareas/{tarea_id}/transicion",
        json={
            "accion": ACCIONES[accion],
            "actor_id": str(revisor_id),
            "motivo": motivo,
        },
        request_id=request_id,
    )

    datos = respuesta.json()

    if respuesta.status_code != 200:
        # 404, 409, 422: se devuelven tal cual.
        return respuesta.status_code, datos

    evento = datos["evento"]

    # MongoDB ya confirmó: el evento queda en SQLite y se envía al historial en segundo plano.
    # En un rechazo la tarea vuelve a disponible en Mongo y el evento lleva estado "rechazada".
    await eventos.publicar_evento(evento)

    return 200, {
        "estado": datos["tarea"]["estado"],
        "version": datos["tarea"]["version"],
        "event_id": evento["event_id"],
    }
