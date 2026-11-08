
"""Pruebas HTTP de sesiones - B2.3."""

import json
import urllib.error
import urllib.request
import uuid

from app import sesiones

anotador = f"prueba-http-b23-{uuid.uuid4().hex}"
url = f"http://localhost:8002/sesiones/{anotador}"
token = "token-correcto"


def enviar(accion, **campos):
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
    datos = sesiones.consultar(anotador)
    if datos:
        sesiones.cerrar(anotador, datos["token"])
