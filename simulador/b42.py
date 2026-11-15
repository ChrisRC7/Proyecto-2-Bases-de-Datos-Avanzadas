
"""B4.2: borradores periodicos y latidos del simulador."""

import time

import httpx

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
    enviar=None,
):
    """Guarda borradores y envia latidos periodicamente.

    enviar es opcional: permite registrar las peticiones
    usando la funcion de medicion del simulador.
    """

    if intervalo <= 0 or duracion < 0:
        raise ValueError("Intervalo y duracion invalidos")

    headers = {"X-Anotador-Id": str(anotador_id)}
    inicio = time.monotonic()
    siguiente = inicio + intervalo
    fin = inicio + duracion
    guardados = 0

    def solicitar(metodo, ruta, endpoint, cuerpo):
        if enviar is not None:
            return enviar(metodo, ruta, endpoint, cuerpo)

        return cliente.request(
            metodo,
            ruta,
            headers=headers,
            json=cuerpo,
        )

    while True:
        ahora = time.monotonic()

        if ahora >= fin:
            break

        if ahora < siguiente:
            time.sleep(min(siguiente - ahora, fin - ahora))
            continue

        try:
            respuesta = solicitar(
                "PUT",
                f"/tareas/{tarea_id}/borrador",
                "PUT /tareas/{id}/borrador",
                {
                    "token": str(token),
                    "contenido": contenido,
                },
            )

            if respuesta is None or respuesta.status_code != 200:
                return {
                    "estado": "error_borrador",
                    "status": respuesta.status_code if respuesta else 0,
                    "borradores": guardados,
                }

            guardados += 1

            latido = solicitar(
                "POST",
                "/sesiones/latido",
                "POST /sesiones/latido",
                {"token": str(token)},
            )

            if latido is None or latido.status_code != 200:
                return {
                    "estado": "error_latido",
                    "status": latido.status_code if latido else 0,
                    "borradores": guardados,
                }

        except httpx.HTTPError:
            return {
                "estado": "error_conexion",
                "status": 0,
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
