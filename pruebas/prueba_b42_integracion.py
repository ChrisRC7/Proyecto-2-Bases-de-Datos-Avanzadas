
"""B4.2: prueba real de borradores, latidos y recuperacion."""

import os

import httpx

from b42_borradores import guardar_periodicamente, recuperar


GATEWAY = os.getenv("GATEWAY_URL", "http://gateway:8000")

# Anotador del dataset (rol: anotador).
ANOTADOR = "adbafed0-b600-460c-bbdc-fd55304374b3"

# Proyecto aislado creado para B4.1.
PROYECTO = "d2347c44-16a6-43cf-ad47-1c1e435b4b09"

CONTENIDO = {
    "etiquetas": ["lidar#ciclista"],
    "origen": "prueba_b42",
    "avance": 75,
}


def main():
    with httpx.Client(base_url=GATEWAY, timeout=10) as cliente:
        headers = {"X-Anotador-Id": ANOTADOR}

        # Evita interferir con una sesion existente.
        codigo_previo, _ = recuperar(cliente, ANOTADOR)

        if codigo_previo == 200:
            raise RuntimeError(
                "El anotador ya tiene sesion activa. "
                "No se modificara esa sesion."
            )

        if codigo_previo != 404:
            raise RuntimeError(
                f"Error consultando sesion previa: {codigo_previo}"
            )

        print("OK | Anotador sin sesion previa")

        # Solicita una tarea del proyecto controlado.
        respuesta = cliente.post(
            "/tareas/siguiente",
            headers=headers,
            json={"proyecto_id": PROYECTO},
        )

        if respuesta.status_code != 200:
            raise RuntimeError(
                f"No se pudo asignar tarea: "
                f"{respuesta.status_code} {respuesta.text}"
            )

        datos = respuesta.json()
        tarea = datos["tarea"]
        token = datos["token"]
        tarea_id = tarea["tarea_id"]

        assert tarea["proyecto_id"] == PROYECTO

        print(f"OK | Tarea asignada: {tarea_id}")

        # Guarda borradores y envia latidos cada 2 segundos.
        resultado = guardar_periodicamente(
            cliente=cliente,
            tarea_id=tarea_id,
            anotador_id=ANOTADOR,
            token=token,
            contenido=CONTENIDO,
            duracion=6.5,
            intervalo=2.0,
            interrumpir=True,
        )

        print(f"Resultado de simulacion: {resultado}")

        assert resultado["estado"] == "interrumpido", resultado
        assert resultado["borradores"] == 3, resultado

        print("OK | Tres borradores y latidos reales")

        # Simula que el usuario vuelve a entrar.
        codigo, recuperada = recuperar(cliente, ANOTADOR)

        assert codigo == 200, (codigo, recuperada)
        assert recuperada["tarea"]["tarea_id"] == tarea_id
        assert recuperada["token"] == token
        assert recuperada["borrador"] is not None
        assert recuperada["borrador"]["contenido"] == CONTENIDO

        print("OK | Misma tarea recuperada")
        print("OK | Mismo token recuperado")
        print("OK | Ultimo borrador recuperado")
        print("B4.2_INTEGRACION_OK")


if __name__ == "__main__":
    main()
