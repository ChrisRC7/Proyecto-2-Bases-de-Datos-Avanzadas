
"""B3.6: envío de anotaciones y limpieza posterior."""

from app import eventos
from app.cliente_interno import ServicioNoDisponible, solicitar
from app.comun import log
from app import limpiezas

async def enviar(tarea_id, anotador_id, token, anotacion, request_id=None):
    tarea_id = str(tarea_id)
    anotador_id = str(anotador_id)
    token = str(token)

    # 1. Verificar token y renovar lock.
    bloqueo = await solicitar(
        "sesion",
        "POST",
        f"/locks/{tarea_id}",
        json={"accion": "renovar", "token": token},
        request_id=request_id,
    )

    if bloqueo.status_code != 200:
        return bloqueo.status_code, bloqueo.json()

    # 2. La transición NO se reintenta.
    respuesta = await solicitar(
        "registro",
        "POST",
        f"/tareas/{tarea_id}/transicion",
        json={
            "accion": "enviar",
            "token": token,
            "actor_id": anotador_id,
            "anotacion": anotacion,
        },
        request_id=request_id,
    )

    if respuesta.status_code != 200:
        # 409, 422, etc.: no borrar borrador ni liberar lock.
        return respuesta.status_code, respuesta.json()

    datos = respuesta.json()
    evento = datos["evento"]

    # 3. Persistir el evento antes de limpiar.
    # Si falla SQLite, no eliminamos el borrador.
    await eventos.publicar_evento(evento)


    # 4. Mongo ya confirmó. Registrar las limpiezas
    # para que sobrevivan a reinicios del gateway.
    await limpiezas.registrar(
        tarea_id,
        anotador_id,
        token,
    )

    return 200, {
        "estado": datos["tarea"]["estado"],
        "version": datos["tarea"]["version"],
        "event_id": evento["event_id"],
    }
