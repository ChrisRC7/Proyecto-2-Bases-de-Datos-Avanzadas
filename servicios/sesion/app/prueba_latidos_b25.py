
"""Pruebas de latidos de sesion - B2.5.

Verifica que un latido renueva el lock a 120 s, actualiza `actualizada_en` de la sesión
y el puntaje en `editando:{proyecto}`, y que responde 409 con token incorrecto o con el
lock vencido. Imprime B2.5_LATIDOS_OK si todo pasa.
"""

import json
import time
import urllib.error
import urllib.request
import uuid

from app import locks, sesiones

# Identificadores únicos de la corrida.
identificador = uuid.uuid4().hex
tarea = f"prueba-b25-{identificador}"
anotador = f"anotador-b25-{identificador}"
proyecto = f"proyecto-b25-{identificador}"
token = f"token-b25-{identificador}"

url = f"http://localhost:8002/sesiones/{anotador}"


def enviar(accion, token_sesion=token, **campos):
    """POST /sesiones/{anotador} con acción y token; devuelve (status, JSON)."""
    cuerpo = json.dumps({
        "accion": accion,
        "token": token_sesion,
        **campos,
    }).encode("utf-8")

    solicitud = urllib.request.Request(
        url,
        data=cuerpo,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(solicitud, timeout=5) as respuesta:
            return respuesta.status, json.load(respuesta)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


try:
    # Preparación: lock tomado y sesión abierta.
    assert locks.adquirir(tarea, token)
    print("OK | Lock adquirido")

    codigo, cuerpo = enviar(
        "abrir",
        tarea_id=tarea,
        proyecto_id=proyecto,
        etapa="anotando",
    )
    assert codigo == 200, (codigo, cuerpo)
    print("OK | Sesion abierta: HTTP 200")

    # Reducimos el TTL solo en esta tarea de prueba.
    locks.cliente.pexpire(f"lock:tarea:{tarea}", 30_000)
    ttl_antes = locks.cliente.pttl(f"lock:tarea:{tarea}")

    sesion_antes = sesiones.consultar(anotador)
    actividad_antes = locks.cliente.zscore(
        f"editando:{proyecto}", anotador
    )

    # Espera para que el nuevo timestamp sea mayor que el anterior.
    time.sleep(1.1)

    codigo, cuerpo = enviar("latido")
    assert codigo == 200, (codigo, cuerpo)
    print("OK | Latido: HTTP 200")

    ttl_despues = locks.cliente.pttl(f"lock:tarea:{tarea}")
    assert ttl_antes <= 30_000
    assert 110_000 <= ttl_despues <= 120_000
    print("OK | TTL renovado a 120 segundos")

    sesion_despues = sesiones.consultar(anotador)
    assert int(sesion_despues["actualizada_en"]) > int(
        sesion_antes["actualizada_en"]
    )
    print("OK | Timestamp de sesion actualizado")

    actividad_despues = locks.cliente.zscore(
        f"editando:{proyecto}", anotador
    )
    assert actividad_despues > actividad_antes
    print("OK | ZADD editando actualizado")

    codigo, _ = enviar("latido", token_sesion="incorrecto")
    assert codigo == 409
    print("OK | Token incorrecto: HTTP 409")

    # Simulamos que el lock venció sin esperar dos minutos.
    locks.cliente.delete(f"lock:tarea:{tarea}")

    codigo, _ = enviar("latido")
    assert codigo == 409
    print("OK | Lock vencido: HTTP 409")

    print("B2.5_LATIDOS_OK")

finally:
    # Limpieza: borra el lock y cierra la sesión de prueba.
    locks.cliente.delete(f"lock:tarea:{tarea}")
    datos = sesiones.consultar(anotador)
    if datos:
        sesiones.cerrar(anotador, datos["token"])
