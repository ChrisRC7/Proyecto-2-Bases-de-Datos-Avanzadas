
"""B4.2: borradores periodicos del simulador."""

import time

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
    inicio = time.monotonic()
    siguiente = inicio + intervalo
    guardados = 0

    while True:
        ahora = time.monotonic()
        fin = inicio + duracion

        if ahora >= fin:
            break

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
    respuesta = cliente.post(
        "/sesiones/recuperar",
        headers={"X-Anotador-Id": str(anotador_id)},
    )

    return respuesta.status_code, respuesta.json()
