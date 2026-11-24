
"""Pruebas HTTP de sesiones - B2.3.

Recorre POST /sesiones/{anotador_id}: abrir (con lock), borrar_borrador (solo con el
token dueño), cerrar con token incorrecto (409), cerrar bien (200) y cerrar una sesión
que ya no existe (404). Imprime B2.3_HTTP_OK si todo pasa.
"""

import json
import urllib.error
import urllib.request
import uuid

from app import borradores, locks, sesiones

# Anotador único por corrida y URL de su sesión.
anotador = f"prueba-http-b23-{uuid.uuid4().hex}"
url = f"http://localhost:8002/sesiones/{anotador}"
token = "token-correcto"


def enviar(accion, **campos):
    """POST a la sesión del anotador con la acción dada; devuelve (status, JSON)."""
    datos = json.dumps({"accion": accion, **campos}).encode("utf-8")

    solicitud = urllib.request.Request(
        url,
        data=datos,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(solicitud, timeout=5) as respuesta:
            return respuesta.status, json.load(respuesta)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


# Abrir exige que el token tenga el lock de la tarea (reasignación segura de borradores).
locks.adquirir("tarea-b23", token)

try:
    codigo, cuerpo = enviar(
        "abrir",
        tarea_id="tarea-b23",
        proyecto_id="proyecto-b23",
        etapa="anotando",
        token=token,
    )
    assert codigo == 200, (codigo, cuerpo)
    assert cuerpo["sesion"]["anotador_id"] == anotador
    print("OK | Abrir sesion: HTTP 200")

    # borrar_borrador: solo con el token y el anotador dueños del borrador (limpieza tras enviar).
    assert borradores.guardar("tarea-b23", anotador, token, {"avance": 1})
    codigo, cuerpo = enviar("borrar_borrador", tarea_id="tarea-b23", token="otro-token")
    assert codigo == 409, (codigo, cuerpo)
    assert borradores.recuperar("tarea-b23") is not None
    codigo, cuerpo = enviar("borrar_borrador", tarea_id="tarea-b23", token=token)
    assert codigo == 200, (codigo, cuerpo)
    assert borradores.recuperar("tarea-b23") is None
    print("OK | borrar_borrador: 409 con otro token, 200 con el propio")

    codigo, cuerpo = enviar("cerrar", token="otro-token")
    assert codigo == 409, (codigo, cuerpo)
    print("OK | Cierre con token incorrecto: HTTP 409")

    codigo, cuerpo = enviar("cerrar", token=token)
    assert codigo == 200, (codigo, cuerpo)
    print("OK | Cerrar sesion: HTTP 200")

    codigo, cuerpo = enviar("cerrar", token=token)
    assert codigo == 404, (codigo, cuerpo)
    print("OK | Sesion inexistente: HTTP 404")

    print("B2.3_HTTP_OK")

finally:
    # Limpieza: suelta el lock, borra el borrador y cierra la sesión si quedó abierta.
    locks.soltar("tarea-b23", token)
    locks.cliente.delete("borrador:tarea-b23")
    datos = sesiones.consultar(anotador)
    if datos:
        sesiones.cerrar(anotador, datos["token"])
