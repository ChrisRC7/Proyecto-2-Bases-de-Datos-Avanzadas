
"""B5.2: vencimiento real del lock y exclusividad por Gateway.

A toma la única tarea de un proyecto y deja de mandar latidos. Mientras la asignación está
vigente, B recibe 204; cuando vence (120 s), B la recibe con un token nuevo, A ya no puede
usar su token viejo y B sí puede trabajar. Tarda unos 2 min.

    python3 pruebas/b52_vencimiento_gateway.py
"""

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from proyecto_prueba import crear_proyecto

# Gateway en el host, proyecto propio de una sola tarea y dos anotadores fijos (A y B).
GATEWAY = "http://localhost:8000"
PROYECTO = crear_proyecto("b52-vencimiento")  # una sola tarea: la primera asignación es esa
TAREA = None  # la única tarea del proyecto, se conoce al asignarla a A

A = "adbafed0-b600-460c-bbdc-fd55304374b3"
B = "a5497863-210f-4de3-853d-82cbe0b89bcb"


def pedir(anotador, metodo, ruta, cuerpo=None):
    """Petición al gateway como `anotador`; devuelve (status, JSON)."""
    datos = None if cuerpo is None else json.dumps(cuerpo).encode("utf-8")
    peticion = urllib.request.Request(
        GATEWAY + ruta,
        data=datos,
        headers={
            "X-Anotador-Id": anotador,
            "Content-Type": "application/json",
        },
        method=metodo,
    )
    try:
        with urllib.request.urlopen(peticion, timeout=15) as respuesta:
            texto = respuesta.read().decode("utf-8")
            return respuesta.status, json.loads(texto) if texto else None
    except urllib.error.HTTPError as error:
        texto = error.read().decode("utf-8")
        return error.code, json.loads(texto) if texto else None


def liberar(anotador, tarea, token, nombre):
    """Intenta liberar la tarea (limpieza) e imprime el resultado."""
    if not token:
        return
    try:
        estado, datos = pedir(
            anotador,
            "POST",
            f"/tareas/{tarea}/liberar",
            {"token": token},
        )
        print(f"LIMPIEZA_{nombre}_HTTP_{estado}={datos}", flush=True)
    except Exception as error:
        print(f"LIMPIEZA_{nombre}_ERROR={error}", flush=True)


def main():
    """Ejecuta el caso de vencimiento completo e imprime el resultado de cada paso."""
    token_a = None
    token_b = None

    try:
        for nombre, anotador in [("A", A), ("B", B)]:
            estado, datos = pedir(anotador, "POST", "/sesiones/recuperar")
            assert estado == 404, (
                f"El anotador {nombre} tiene sesion previa: {estado} {datos}"
            )

        estado, datos = pedir(
            A, "POST", "/tareas/siguiente", {"proyecto_id": PROYECTO}
        )
        assert estado == 200, f"No se asigno A: {estado} {datos}"
        token_a = datos["token"]
        global TAREA
        TAREA = datos["tarea"]["tarea_id"]

        print("A_ASIGNADO_OK", flush=True)

        # B no debe obtener la tarea mientras la asignacion de A siga vigente.
        estado, datos_b = pedir(
            B, "POST", "/tareas/siguiente", {"proyecto_id": PROYECTO}
        )
        assert estado == 204, (
            f"B obtuvo respuesta inesperada antes de vencer: "
            f"{estado} {datos_b}"
        )
        print("EXCLUSIVIDAD_ANTES_VENCIMIENTO_OK", flush=True)

        # Esperar el vencimiento real comunicado por Registro.
        vencimiento = datetime.fromisoformat(
            datos["vence_en"].replace("Z", "+00:00")
        )
        segundos = (
            vencimiento - datetime.now(timezone.utc)
        ).total_seconds() + 3

        if segundos > 0:
            print(
                f"ESPERANDO_VENCIMIENTO_{segundos:.1f}_SEGUNDOS",
                flush=True,
            )
            time.sleep(segundos)

        # Intentar reasignar. Damos un pequeño margen al lock de Valkey.
        respuesta_b = None
        for intento in range(5):
            estado, respuesta_b = pedir(
                B, "POST", "/tareas/siguiente",
                {"proyecto_id": PROYECTO},
            )
            if estado == 200:
                break
            print(
                f"REINTENTO_B_{intento + 1}_HTTP_{estado}",
                flush=True,
            )
            time.sleep(2)

        assert estado == 200, (
            f"No hubo reasignacion: {estado} {respuesta_b}"
        )

        token_b = respuesta_b["token"]
        assert respuesta_b["tarea"]["tarea_id"] == TAREA
        assert token_a != token_b, "Los tokens son iguales"

        print("REASIGNACION_TRAS_VENCIMIENTO_OK", flush=True)
        print("TOKENS_DIFERENTES_OK", flush=True)

        # A ya no puede modificar la tarea con su token vencido.
        estado_a, respuesta_a = pedir(
            A,
            "PUT",
            f"/tareas/{TAREA}/borrador",
            {
                "token": token_a,
                "contenido": {"origen": "A_token_vencido"},
            },
        )
        assert estado_a != 200, (
            f"PELIGRO: A guardo con token vencido: {respuesta_a}"
        )
        print(f"TOKEN_A_RECHAZADO_HTTP_{estado_a}", flush=True)

        # B si puede trabajar con el nuevo token.
        estado_b, respuesta_b = pedir(
            B,
            "PUT",
            f"/tareas/{TAREA}/borrador",
            {
                "token": token_b,
                "contenido": {"origen": "B_nuevo_propietario"},
            },
        )
        assert estado_b == 200, (
            f"B no pudo guardar: {estado_b} {respuesta_b}"
        )
        print("TOKEN_B_VALIDO_OK", flush=True)
        print("B5_2_VENCIMIENTO_EXCLUSIVIDAD_OK", flush=True)

    finally:
        # Primero liberar al propietario nuevo; despues intentar
        # cerrar cualquier sesion residual del propietario anterior.
        if TAREA:
            liberar(B, TAREA, token_b, "B")
            liberar(A, TAREA, token_a, "A")


if __name__ == "__main__":
    main()
