
"""B5.2: comprobar persistencia AOF tras reiniciar Valkey."""

import json
import subprocess
import time
import urllib.error
import urllib.request
from proyecto_prueba import crear_proyecto

GATEWAY = "http://localhost:8000"
ANOTADOR = "adbafed0-b600-460c-bbdc-fd55304374b3"
PROYECTO = crear_proyecto("b52-persistencia")  # una sola tarea: la primera asignación es esa


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
        with urllib.request.urlopen(peticion, timeout=8) as respuesta:
            texto = respuesta.read().decode("utf-8")
            return respuesta.status, json.loads(texto) if texto else None
    except urllib.error.HTTPError as error:
        texto = error.read().decode("utf-8")
        return error.code, json.loads(texto) if texto else None


def recuperar_con_reintentos():
    limite = time.monotonic() + 35
    ultimo_error = None

    while time.monotonic() < limite:
        try:
            estado, datos = solicitar("POST", "/sesiones/recuperar")
            if estado == 200:
                return datos
            ultimo_error = f"HTTP {estado}: {datos}"
        except (urllib.error.URLError, TimeoutError) as error:
            ultimo_error = str(error)

        time.sleep(1)

    raise AssertionError(f"No se recupero la sesion: {ultimo_error}")


def main():
    tarea = None
    token = None

    try:
        estado, datos = solicitar("POST", "/sesiones/recuperar")
        assert estado == 404, f"Sesion anterior activa: {estado} {datos}"

        estado, datos = solicitar(
            "POST",
            "/tareas/siguiente",
            {"proyecto_id": PROYECTO},
        )
        assert estado == 200, f"Asignacion fallida: {estado} {datos}"

        tarea = datos["tarea"]["tarea_id"]
        token = datos["token"]

        print("ASIGNACION_OK", flush=True)

        contenido = {
            "etiquetas": ["lidar#ciclista"],
            "origen": "b52_persistencia_aof",
            "marcador": "sobrevive_reinicio_valkey",
        }

        estado, datos = solicitar(
            "PUT",
            f"/tareas/{tarea}/borrador",
            {"token": token, "contenido": contenido},
        )
        assert estado == 200, f"Guardado fallido: {estado} {datos}"
        print("BORRADOR_GUARDADO_OK", flush=True)

        # AOF everysec: dar margen a la sincronizacion en disco.
        time.sleep(2)

        print("REINICIANDO_VALKEY...", flush=True)
        subprocess.run(
            ["docker", "compose", "restart", "valkey"],
            check=True,
            timeout=70,
        )
        print("VALKEY_REINICIADO_OK", flush=True)

        recuperado = recuperar_con_reintentos()
        print(
            "RESPUESTA_RECUPERACION="
            + json.dumps(recuperado, ensure_ascii=False),
            flush=True,
        )

        assert recuperado["token"] == token, "Token diferente"
        assert recuperado["tarea"]["tarea_id"] == tarea, "Tarea diferente"

        borrador = recuperado["borrador"]
        assert borrador is not None, "Se perdio el borrador"
        assert borrador["contenido"] == contenido, "Contenido diferente"
        assert borrador.get("guardado_en"), "Falta fecha de guardado"

        print("TOKEN_CONSERVADO_OK", flush=True)
        print("CONTENIDO_CONSERVADO_OK", flush=True)
        print("B5_2_PERSISTENCIA_AOF_OK", flush=True)

    finally:
        if tarea is not None and token is not None:
            try:
                estado, datos = solicitar(
                    "POST",
                    f"/tareas/{tarea}/liberar",
                    {"token": token},
                )
                print(
                    f"LIMPIEZA_HTTP_{estado}={datos}",
                    flush=True,
                )
                if estado != 200:
                    print("ATENCION: verificar limpieza manualmente", flush=True)
            except Exception as error:
                print(f"ATENCION: limpieza pendiente: {error}", flush=True)


if __name__ == "__main__":
    main()
