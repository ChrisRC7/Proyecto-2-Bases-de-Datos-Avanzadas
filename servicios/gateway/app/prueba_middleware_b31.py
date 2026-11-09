
"""Pruebas B3.1: middleware, validacion y formato de errores."""

import asyncio
import json
import logging
import uuid
from io import StringIO

from fastapi import FastAPI
from pydantic import BaseModel, Field
from starlette.requests import Request

from app.comun import FormatoJSON, crear_app


class DatosPrueba(BaseModel):
    cantidad: int = Field(gt=0)
    nombre: str = Field(min_length=1)


app = crear_app("gateway-prueba")


@app.post("/validar")
def validar(cuerpo: DatosPrueba):
    return {"estado": "ok", "cantidad": cuerpo.cantidad}


async def solicitar(ruta, metodo="GET", cuerpo=None, headers=None):
    """Ejecuta una peticion ASGI sin levantar otro servidor."""
    enviados = []
    contenido = json.dumps(cuerpo).encode() if cuerpo is not None else b""
    encabezados = [
        (b"content-type", b"application/json"),
        *[(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    ]

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": metodo,
        "scheme": "http",
        "path": ruta,
        "raw_path": ruta.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": encabezados,
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 8000),
    }

    recibido = False

    async def receive():
        nonlocal recibido
        if not recibido:
            recibido = True
            return {"type": "http.request", "body": contenido, "more_body": False}
        await asyncio.sleep(0.01)
        return {"type": "http.disconnect"}

    async def send(mensaje):
        enviados.append(mensaje)

    await app(scope, receive, send)

    inicio = next(m for m in enviados if m["type"] == "http.response.start")
    datos = b"".join(
        m.get("body", b"")
        for m in enviados
        if m["type"] == "http.response.body"
    )
    cabeceras = {
        k.decode().lower(): v.decode()
        for k, v in inicio["headers"]
    }
    return inicio["status"], cabeceras, json.loads(datos)


async def pruebas():
    codigo, cabeceras, _ = await solicitar("/validar", "POST", {"cantidad": 1, "nombre": "ok"})
    assert codigo == 200
    assert cabeceras.get("x-request-id")
    print("OK | X-Request-Id generado")

    identificador = str(uuid.uuid4())
    codigo, cabeceras, _ = await solicitar(
        "/validar",
        "POST",
        {"cantidad": 1, "nombre": "ok"},
        {"X-Request-Id": identificador},
    )
    assert codigo == 200
    assert cabeceras["x-request-id"] == identificador
    print("OK | X-Request-Id propagado")

    codigo, _, _ = await solicitar(
        "/validar", "POST", {"cantidad": 5, "nombre": "prueba"}
    )
    assert codigo == 200
    print("OK | Validacion correcta")

    codigo, _, cuerpo = await solicitar(
        "/validar", "POST", {"cantidad": -1, "nombre": ""}
    )
    assert codigo == 422
    assert cuerpo["error"] == "validacion"
    print("OK | Validacion HTTP 422")

    codigo, _, cuerpo = await solicitar("/ruta-inexistente-b31")
    assert codigo == 404
    assert "error" in cuerpo and "detalle" in cuerpo
    print("OK | Formato comun HTTP 404")

    registro = logging.LogRecord(
        "gateway", logging.INFO, __file__, 1, "peticion", (), None
    )
    registro.datos = {
        "request_id": identificador,
        "metodo": "POST",
        "ruta": "/validar",
        "status": 200,
        "ms": 1.2,
    }
    linea = json.loads(FormatoJSON().format(registro))
    assert linea["request_id"] == identificador
    assert linea["status"] == 200
    print("OK | Logging JSON")

    print("B3.1_MIDDLEWARE_OK")


if __name__ == "__main__":
    asyncio.run(pruebas())
