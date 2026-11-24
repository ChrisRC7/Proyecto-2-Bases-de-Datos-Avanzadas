
"""Pruebas B3.1: middleware, validacion y formato de errores.

Se ejecuta dentro del contenedor del gateway:
    docker compose exec -T gateway python -m app.prueba_middleware_b31
No necesita los demás servicios: las llamadas internas se reemplazan con mocks.
Imprime una línea OK por cada caso y una marca final si todo pasa.
"""

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
    """Modelo de prueba para provocar errores de validación (cantidad > 0, nombre no vacío)."""
    cantidad: int = Field(gt=0)
    nombre: str = Field(min_length=1)


# App mínima creada con la misma función que usan los servicios reales.
app = crear_app("gateway-prueba")


@app.post("/validar")
def validar(cuerpo: DatosPrueba):
    """Ruta de prueba: responde ok si el cuerpo es válido."""
    return {"estado": "ok", "cantidad": cuerpo.cantidad}


async def solicitar(ruta, metodo="GET", cuerpo=None, headers=None):
    """Ejecuta una peticion ASGI sin levantar otro servidor."""
    enviados = []
    contenido = json.dumps(cuerpo).encode() if cuerpo is not None else b""
    encabezados = [
        (b"content-type", b"application/json"),
        *[(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    ]

    # "scope" ASGI: describe la petición HTTP como la recibiría la app.
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
        """Entrega el cuerpo una vez; después simula que el cliente se desconectó."""
        nonlocal recibido
        if not recibido:
            recibido = True
            return {"type": "http.request", "body": contenido, "more_body": False}
        await asyncio.sleep(0.01)
        return {"type": "http.disconnect"}

    async def send(mensaje):
        """Guarda cada mensaje de respuesta que emite la app."""
        enviados.append(mensaje)

    await app(scope, receive, send)

    # Junta status, cabeceras y cuerpo de los mensajes recibidos.
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
    """Revisa X-Request-Id (generado y propagado), validación 422, formato 404 y log JSON."""
    # 1. Sin X-Request-Id: el middleware genera uno.
    codigo, cabeceras, _ = await solicitar("/validar", "POST", {"cantidad": 1, "nombre": "ok"})
    assert codigo == 200
    assert cabeceras.get("x-request-id")
    print("OK | X-Request-Id generado")

    # 2. Con X-Request-Id: se devuelve el mismo.
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

    # 3. Cuerpo válido.
    codigo, _, _ = await solicitar(
        "/validar", "POST", {"cantidad": 5, "nombre": "prueba"}
    )
    assert codigo == 200
    print("OK | Validacion correcta")

    # 4. Cuerpo inválido: 422 con error "validacion".
    codigo, _, cuerpo = await solicitar(
        "/validar", "POST", {"cantidad": -1, "nombre": ""}
    )
    assert codigo == 422
    assert cuerpo["error"] == "validacion"
    print("OK | Validacion HTTP 422")

    # 5. Ruta inexistente: 404 con el formato común.
    codigo, _, cuerpo = await solicitar("/ruta-inexistente-b31")
    assert codigo == 404
    assert "error" in cuerpo and "detalle" in cuerpo
    print("OK | Formato comun HTTP 404")

    # 6. El formateador escribe JSON e incluye los datos extra.
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
