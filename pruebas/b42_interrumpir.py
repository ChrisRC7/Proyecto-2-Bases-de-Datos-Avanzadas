
"""B4.2: usuario virtual interrumpible."""

import os
import time

import httpx

from b42_borradores import guardar_periodicamente


ANOTADOR = "adbafed0-b600-460c-bbdc-fd55304374b3"
PROYECTO = "d2347c44-16a6-43cf-ad47-1c1e435b4b09"
GATEWAY = os.getenv("GATEWAY_URL", "http://gateway:8000")


def main():
    with httpx.Client(base_url=GATEWAY, timeout=10) as cliente:
        headers = {"X-Anotador-Id": ANOTADOR}

        r = cliente.post(
            "/sesiones/recuperar",
            headers=headers,
        )
        if r.status_code == 200:
            raise RuntimeError("Sesion existente; no se interrumpe")
        if r.status_code != 404:
            raise RuntimeError(f"Error de sesion: {r.status_code}")

        r = cliente.post(
            "/tareas/siguiente",
            headers=headers,
            json={"proyecto_id": PROYECTO},
        )
        r.raise_for_status()

        datos = r.json()
        tarea_id = datos["tarea"]["tarea_id"]
        token = datos["token"]

        print(f"TAREA={tarea_id}", flush=True)
        print(f"TOKEN={token}", flush=True)
        print("EDITANDO: guardar borradores cada 2 s", flush=True)

        guardar_periodicamente(
            cliente=cliente,
            tarea_id=tarea_id,
            anotador_id=ANOTADOR,
            token=token,
            contenido={
                "etiquetas": ["lidar#ciclista"],
                "origen": "interrupcion_real_b42",
            },
            duracion=60,
            intervalo=2,
            interrumpir=True,
        )

        print("FIN_NORMAL", flush=True)


if __name__ == "__main__":
    main()
