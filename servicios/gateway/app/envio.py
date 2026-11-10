
"""B3.6: envío de anotaciones y limpieza posterior."""

from app import eventos
from app.cliente_interno import ServicioNoDisponible, solicitar
from app.comun import log


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

    # 4. Mongo ya confirmó. La limpieza es posterior.
    acciones = [
        (
            "eliminar_borrador",
            "DELETE",
            f"/borradores/{tarea_id}",
            {"anotador_id": anotador_id, "token": token},
        ),
        (
            "soltar_lock",
            "POST",
            f"/locks/{tarea_id}",
            {"accion": "soltar", "token": token},
        ),
        (
            "cerrar_sesion",
            "POST",
            f"/sesiones/{anotador_id}",
            {"accion": "cerrar", "token": token},
        ),
    ]

    for nombre, metodo, ruta, cuerpo in acciones:
        try:
            resultado = await solicitar(
                "sesion",
                metodo,
                ruta,
                json=cuerpo,
                request_id=request_id,
            )

            if resultado.status_code != 200:
                log.warning(
                    "limpieza_envio_pendiente",
                    extra={"datos": {
                        "accion": nombre,
                        "tarea_id": tarea_id,
                        "status": resultado.status_code,
                    }},
                )

        except ServicioNoDisponible:
            log.warning(
                "limpieza_envio_pendiente",
                extra={"datos": {
                    "accion": nombre,
                    "tarea_id": tarea_id,
                    "causa": "servicio_no_disponible",
                }},
            )

    return 200, {
        "estado": datos["tarea"]["estado"],
        "version": datos["tarea"]["version"],
        "event_id": evento["event_id"],
    }
