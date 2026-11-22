
"""B4.2: borradores periodicos del simulador.

Versión de pruebas/ de simulador/b42.py (sin medición): guarda un borrador y envía un latido
cada `intervalo` segundos durante `duracion` segundos.
"""

import time

# Segundos entre cada borrador + latido.
INTERVALO_BORRADOR = 2.0


def guardar_periodicamente(
    cliente,
    tarea_id,
    anotador_id,
    token,
    contenido,
    duracion,
    intervalo=INTERVALO_BORRADOR,
    interrumpir=False,
):
    """Guarda borradores y latidos periódicamente; devuelve estado y cantidad de borradores.

    estado: "completado" o "interrumpido" si terminó bien; "error"/"error_latido" si falló.
    """
    inicio = time.monotonic()
    siguiente = inicio + intervalo
    guardados = 0

    while True:
        ahora = time.monotonic()
        fin = inicio + duracion

        # Terminó el tiempo de trabajo.
        if ahora >= fin:
            break

        # Aún no toca guardar: duerme hasta el próximo guardado.

        if ahora < siguiente:
            time.sleep(min(siguiente - ahora, fin - ahora))
            continue

        respuesta = cliente.put(
            f"/tareas/{tarea_id}/borrador",
            headers={"X-Anotador-Id": str(anotador_id)},
            json={
                "token": str(token),
                "contenido": contenido,
            },
        )

        if respuesta.status_code != 200:
            return {
                "estado": "error",
                "status": respuesta.status_code,
                "borradores": guardados,
            }


        guardados += 1

        # Latido: renueva el lock y el plazo de la asignación.
        latido = cliente.post(
            "/sesiones/latido",
            headers={"X-Anotador-Id": str(anotador_id)},
            json={"token": str(token)},
        )

        if latido.status_code != 200:
            return {
                "estado": "error_latido",
                "status": latido.status_code,
                "borradores": guardados,
            }

        siguiente += intervalo


    return {
        "estado": "interrumpido" if interrumpir else "completado",
        "borradores": guardados,
    }


def recuperar(cliente, anotador_id):
    """POST /sesiones/recuperar del anotador; devuelve (status, JSON)."""
    respuesta = cliente.post(
        "/sesiones/recuperar",
        headers={"X-Anotador-Id": str(anotador_id)},
    )

    return respuesta.status_code, respuesta.json()
