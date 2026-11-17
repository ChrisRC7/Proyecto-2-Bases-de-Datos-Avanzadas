
"""B5.3 (P8): reinicio de Valkey durante actividad y medicion RTO/RPO."""

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from proyecto_prueba import crear_proyecto

GATEWAY = "http://localhost:8000"
ANOTADOR = "adbafed0-b600-460c-bbdc-fd55304374b3"
PROYECTO = crear_proyecto("b53")  # una sola tarea: la primera asignación es esa


def pedir(metodo, ruta, cuerpo=None, timeout=3):
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
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            texto = respuesta.read().decode("utf-8")
            return respuesta.status, json.loads(texto) if texto else None
    except urllib.error.HTTPError as error:
        texto = error.read().decode("utf-8")
        return error.code, json.loads(texto) if texto else None


def main():
    token = None
    tarea = None
    detener = threading.Event()
    muestras = []

    def actividad():
        numero = 0
        while not detener.is_set():
            numero += 1
            try:
                estado, datos = pedir(
                    "POST",
                    "/sesiones/latido",
                    {"token": token},
                    timeout=2,
                )
                muestras.append((time.monotonic(), estado))
                print(f"LATIDO_{numero}_HTTP_{estado}", flush=True)
            except Exception as error:
                muestras.append((time.monotonic(), "sin_respuesta"))
                print(
                    f"LATIDO_{numero}_SIN_RESPUESTA={type(error).__name__}",
                    flush=True,
                )
            detener.wait(0.3)

    hilo = None

    try:
        estado, datos = pedir("POST", "/sesiones/recuperar")
        assert estado == 404, f"Sesion previa: {estado} {datos}"

        estado, datos = pedir(
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
            "origen": "b53_recuperacion_fallos",
            "marcador": "p8_aof_everysec",
        }

        estado, datos = pedir(
            "PUT",
            f"/tareas/{tarea}/borrador",
            {"token": token, "contenido": contenido},
        )
        assert estado == 200, f"Guardado fallido: {estado} {datos}"

        guardado = datetime.now(timezone.utc)
        print("BORRADOR_GUARDADO_OK", flush=True)

        # Generar actividad real mientras ocurre el reinicio.
        hilo = threading.Thread(target=actividad, daemon=True)
        hilo.start()
        time.sleep(1)

        inicio = time.monotonic()
        print("FALLO_INYECTADO_REINICIO_VALKEY", flush=True)

        subprocess.run(
            ["docker", "compose", "restart", "valkey"],
            check=True,
            timeout=70,
        )

        # Medir desde el comienzo del reinicio hasta recuperar sesion y borrador.
        limite = inicio + 60
        recuperado = None
        ultimo = None

        while time.monotonic() < limite:
            try:
                estado, datos = pedir("POST", "/sesiones/recuperar", timeout=3)
                if estado == 200 and datos.get("borrador"):
                    recuperado = datos
                    break
                ultimo = f"HTTP {estado}: {datos}"
            except Exception as error:
                ultimo = str(error)
            time.sleep(0.25)

        rto = time.monotonic() - inicio
        assert recuperado is not None, (
            f"No se recupero el borrador: {ultimo}"
        )

        assert recuperado["token"] == token, "Token alterado"
        assert recuperado["tarea"]["tarea_id"] == tarea, "Tarea alterada"
        assert recuperado["borrador"]["contenido"] == contenido, (
            "Borrador incompleto o diferente"
        )

        fecha_guardada = datetime.fromisoformat(
            recuperado["borrador"]["guardado_en"].replace("Z", "+00:00")
        )

        # RPO observado: el borrador confirmado sobrevivio sin perdida.
        # La antiguedad es una medida distinta del RPO.
        antiguedad = (
            datetime.now(timezone.utc) - fecha_guardada
        ).total_seconds()

        print(f"RTO_OBSERVADO_SEGUNDOS={rto:.3f}", flush=True)
        print("RPO_OBSERVADO=sin_perdida_del_borrador_confirmado", flush=True)
        print(
            f"ANTIGUEDAD_BORRADOR_SEGUNDOS={antiguedad:.3f}",
            flush=True,
        )
        print(f"LATIDOS_REGISTRADOS={len(muestras)}", flush=True)
        print("SESION_Y_TOKEN_RECUPERADOS_OK", flush=True)
        print("B5_3_P8_OK", flush=True)

    finally:
        detener.set()
        if hilo is not None:
            hilo.join(timeout=5)

        if tarea and token:
            try:
                estado, datos = pedir(
                    "POST",
                    f"/tareas/{tarea}/liberar",
                    {"token": token},
                    timeout=10,
                )
                print(f"LIMPIEZA_HTTP_{estado}={datos}", flush=True)
            except Exception as error:
                print(f"ATENCION_LIMPIEZA_PENDIENTE={error}", flush=True)


if __name__ == "__main__":
    main()
