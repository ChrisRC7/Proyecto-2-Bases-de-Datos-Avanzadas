
"""Pruebas HTTP de locks - B2.2."""

import json
import urllib.error
import urllib.request
import uuid

URL = f"http://localhost:8002/locks/prueba-http-{uuid.uuid4().hex}"


def probar(accion, token):
    datos = json.dumps({
        "accion": accion,
        "token": token,
    }).encode("utf-8")

    solicitud = urllib.request.Request(
        URL,
        data=datos,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(solicitud, timeout=5) as respuesta:
            return respuesta.status
    except urllib.error.HTTPError as error:
        return error.code


pruebas = [
    ("Adquirir lock libre", "adquirir", "token-a", 200),
    ("Otro token no puede adquirir", "adquirir", "token-b", 409),
    ("Otro token no puede renovar", "renovar", "token-b", 409),
    ("Token correcto puede renovar", "renovar", "token-a", 200),
    ("Otro token no puede soltar", "soltar", "token-b", 409),
    ("Token correcto puede soltar", "soltar", "token-a", 200),
    ("Lock libre puede adquirirse otra vez", "adquirir", "token-b", 200),
    ("Liberar lock de prueba", "soltar", "token-b", 200),
]

for descripcion, accion, token, esperado in pruebas:
    obtenido = probar(accion, token)
    resultado = "OK" if obtenido == esperado else "FALLO"
    print(f"{resultado} | {descripcion}: HTTP {obtenido} (esperado {esperado})")
    assert obtenido == esperado, descripcion

print("B2.2_HTTP_OK")
