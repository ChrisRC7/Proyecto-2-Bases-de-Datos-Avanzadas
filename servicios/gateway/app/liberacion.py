
"""B3.7: liberar una tarea desde el gateway."""

from app import eventos, limpiezas
from app.cliente_interno import solicitar


async def liberar(tarea_id, anotador_id, token, request_id=None):
    tarea_id = str(tarea_id)
    anotador_id = str(anotador_id)
    token = str(token)

    # La transición en Registro NO se reintenta.
    respuesta = await solicitar(
        "registro",
        "POST",
        f"/tareas/{tarea_id}/transicion",
        json={
            "accion": "liberar",
            "token": token,
            "actor_id": anotador_id,
        },
        request_id=request_id,
    )

    if respuesta.status_code != 200:
        return respuesta.status_code, respuesta.json()

    datos = respuesta.json()

    # MongoDB ya confirmó: persistir evento para Historial.
    await eventos.publicar_evento(datos["evento"])

    # La limpieza corre en el momento; si Sesión no está disponible
    # queda en SQLite y se reintenta automáticamente.
    await limpiezas.ejecutar(
        tarea_id,
        anotador_id,
        token,
    )

    return 200, {
        "estado": "disponible",
    }
