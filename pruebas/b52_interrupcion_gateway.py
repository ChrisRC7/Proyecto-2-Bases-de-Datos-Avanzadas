
"""B5.2: interrupcion y recuperacion de borradores por Gateway."""

import json
import time
import urllib.error
import urllib.request
from proyecto_prueba import crear_proyecto

GATEWAY = "http://localhost:8000"
ANOTADOR = "adbafed0-b600-460c-bbdc-fd55304374b3"
PROYECTO = crear_proyecto("b52-interrupcion")  # una sola tarea: la primera asignación es esa


def solicitar(metodo, ruta, cuerpo=None):
    datos = None if cuerpo is None else json.dumps(cuerpo).encode("utf-8")
    peticion = urllib.request.Request(
        GATEWAY + ruta,
        data=datos,
        headers={
            "X-Anotador-Id": ANOTADOR,
            "Content-Type": "application/json",
        },
        method=metodo,
    )
    try:
        with urllib.request.urlopen(peticion, timeout=15) as respuesta:
            contenido = respuesta.read().decode("utf-8")
            return respuesta.status, json.loads(contenido) if contenido else None
    except urllib.error.HTTPError as error:
        contenido = error.read().decode("utf-8")
        return error.code, json.loads(contenido) if contenido else None


def main():
    token = None
    tarea = None

    try:
        estado, datos = solicitar(
            "POST", "/sesiones/recuperar"
        )
        assert estado == 404, (
            f"El anotador ya tiene sesion: {estado} {datos}"
        )

        estado, datos = solicitar(
            "POST",
            "/tareas/siguiente",
            {"proyecto_id": PROYECTO},
        )
        assert estado == 200, f"Asignacion fallida: {estado} {datos}"

        tarea = datos["tarea"]["tarea_id"]
        token = datos["token"]

        print(f"ASIGNACION_OK tarea={tarea}", flush=True)

        contenido = {
            "etiquetas": ["lidar#ciclista"],
            "origen": "b52_interrupcion_automatica",
            "marcador": "recuperacion_borrador",
        }

        inicio_guardado = time.monotonic()

        estado, datos = solicitar(
            "PUT",
            f"/tareas/{tarea}/borrador",
            {"token": token, "contenido": contenido},
        )
        assert estado == 200, f"Guardado fallido: {estado} {datos}"

        print("BORRADOR_GUARDADO_OK", flush=True)

        # Simulacion de interrupcion: dejamos de editar
        # sin enviar ni liberar la tarea.
        print("INTERRUPCION_SIMULADA", flush=True)

        estado, recuperado = solicitar(
            "POST", "/sesiones/recuperar"
        )

        segundos = time.monotonic() - inicio_guardado

        assert estado == 200, (
            f"Recuperacion fallida: {estado} {recuperado}"
        )

        print(
            "RESPUESTA_RECUPERACION="
            + json.dumps(recuperado, ensure_ascii=False),
            flush=True,
        )

        assert segundos <= 5, (
            f"Recuperacion fuera de ventana: {segundos:.3f}s"
        )

        print(f"RECUPERACION_EN_{segundos:.3f}_SEGUNDOS", flush=True)
        print("B5_2_INTERRUPCION_OK", flush=True)

    finally:
        if tarea is not None and token is not None:
            estado, datos = solicitar(
                "POST",
                f"/tareas/{tarea}/liberar",
                {"token": token},
            )
            print(
                f"LIMPIEZA_HTTP_{estado}={datos}",
                flush=True,
            )


if __name__ == "__main__":
    main()
