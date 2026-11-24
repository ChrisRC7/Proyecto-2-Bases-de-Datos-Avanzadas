
"""B3.4: coordinación de asignación, lock, sesión y evento.

Pasos de POST /tareas/siguiente: el registro asigna la tarea (MongoDB) -> la sesión toma
el lock y abre la sesión (Valkey) -> se guarda el evento para el historial. Si algo falla
después de que el registro asignó, se compensa: se cierra la sesión, se suelta el lock y
se libera la tarea en el registro.
"""

from datetime import datetime, timezone
from uuid import uuid4

from app import eventos
from app.cliente_interno import ServicioNoDisponible, solicitar
from app.comun import log


class FalloAsignacion(Exception):
    """Un paso posterior a la asignación en el registro no se pudo completar."""
    pass


def borrador_publico(borrador):
    """El borrador como lo devuelve /sesiones/recuperar: contenido y guardado_en en ISO-8601 UTC.

    La sesión lo guarda con el anotador, el token y guardado_en en epoch ms; al cliente solo le
    sirven el contenido y la fecha.
    """
    if not borrador:
        return None
    guardado = borrador.get("guardado_en")
    if isinstance(guardado, (int, float)):
        guardado = datetime.fromtimestamp(guardado / 1000, tz=timezone.utc).isoformat()
    return {"contenido": borrador.get("contenido"), "guardado_en": guardado}


async def _compensar(tarea_id, anotador_id, token, request_id):
    """Intenta cerrar sesión, soltar lock y liberar la tarea."""
    # (nombre, servicio, ruta, cuerpo) de cada paso de reversión, en orden.
    acciones = [
        (
            "cerrar_sesion",
            "sesion",
            f"/sesiones/{anotador_id}",
            {"accion": "cerrar", "token": token},
        ),
        (
            "soltar_lock",
            "sesion",
            f"/locks/{tarea_id}",
            {"accion": "soltar", "token": token},
        ),
        (
            "liberar_registro",
            "registro",
            f"/tareas/{tarea_id}/transicion",
            {
                "accion": "liberar",
                "token": token,
                "actor_id": anotador_id,
            },
        ),
    ]

    for nombre, servicio, ruta, cuerpo in acciones:
        try:
            respuesta = await solicitar(
                servicio,
                "POST",
                ruta,
                json=cuerpo,
                request_id=request_id,
            )

            # Si el registro liberó la tarea, su evento "liberada" también va al lago.
            # 404/409 significan que ya no había nada que revertir.
            if respuesta.status_code == 200:
                if nombre == "liberar_registro":
                    evento = respuesta.json().get("evento")
                    if evento:
                        await eventos.publicar_evento(evento)
            elif respuesta.status_code not in (404, 409):
                log.warning(
                    "compensacion_no_confirmada",
                    extra={"datos": {
                        "accion": nombre,
                        "tarea_id": tarea_id,
                        "status": respuesta.status_code,
                    }},
                )

        # La compensación es de mejor esfuerzo: un paso que falla no detiene los demás.
        except Exception:
            log.exception(
                "error_compensacion",
                extra={"datos": {
                    "accion": nombre,
                    "tarea_id": tarea_id,
                }},
            )


async def siguiente(anotador_id, proyecto_id=None, request_id=None):
    """Asigna la siguiente tarea al anotador y devuelve (código, datos).

    204 si no hay tareas; el código y cuerpo del registro si este rechaza; 200 con tarea,
    token, vencimiento y borrador recuperable si todo sale bien. Lanza
    ServicioNoDisponible (503) si falla un paso posterior (tras compensar).
    """
    # Token nuevo de esta asignación: lo comparten registro, lock y sesión.
    token = str(uuid4())

    cuerpo = {
        "anotador_id": str(anotador_id),
        "token": token,
    }

    if proyecto_id is not None:
        cuerpo["proyecto_id"] = str(proyecto_id)

    # POST no idempotente: nunca se reintenta automáticamente.
    respuesta = await solicitar(
        "registro",
        "POST",
        "/tareas/asignar",
        json=cuerpo,
        request_id=request_id,
    )

    # Sin tareas disponibles, o error del registro (404 anotador, etc.).
    if respuesta.status_code == 204:
        return 204, None

    if respuesta.status_code != 200:
        return respuesta.status_code, respuesta.json()

    datos = respuesta.json()
    tarea = datos["tarea"]
    tarea_id = tarea["tarea_id"]

    try:
        # Lock exclusivo de la tarea en Valkey con el mismo token.
        bloqueo = await solicitar(
            "sesion",
            "POST",
            f"/locks/{tarea_id}",
            json={"accion": "adquirir", "token": token},
            request_id=request_id,
        )

        if bloqueo.status_code != 200:
            raise FalloAsignacion("No se pudo adquirir el lock")

        # Abre la sesión del anotador (y reconcilia el borrador anterior, si había).
        sesion = await solicitar(
            "sesion",
            "POST",
            f"/sesiones/{anotador_id}",
            json={
                "accion": "abrir",
                "tarea_id": tarea_id,
                "proyecto_id": tarea["proyecto_id"],
                "etapa": "anotando",
                "token": token,
            },
            request_id=request_id,
        )

        if sesion.status_code != 200:
            raise FalloAsignacion("No se pudo abrir la sesión")

        # Guardar el evento antes de confirmar éxito al cliente.
        await eventos.publicar_evento(datos["evento"])

        return 200, {
            "tarea": tarea,
            "token": token,
            "vence_en": tarea["vence_en"],
            "borrador": borrador_publico(sesion.json().get("borrador")),
        }

    # Cualquier fallo después de asignar: se revierte para no dejar la tarea tomada sin lock.
    except (FalloAsignacion, ServicioNoDisponible, ValueError, KeyError):
        log.exception(
            "asignacion_fallida",
            extra={"datos": {"tarea_id": tarea_id}},
        )
        await _compensar(tarea_id, str(anotador_id), token, request_id)
        raise ServicioNoDisponible(
            "asignacion",
            "No se pudo completar la asignación; se intentó revertir",
        )
