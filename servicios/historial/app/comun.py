"""Piezas comunes a todos los servicios: logging JSON, X-Request-Id y formato de error.

Este archivo es igual en todos los servicios (cada servicio tiene su propia copia
porque se construyen como imágenes independientes). Si se cambia, copiarlo a los demás.
"""
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

SERVICIO = os.getenv("SERVICIO", "servicio")
HEADER_REQUEST_ID = "X-Request-Id"


def ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class FormatoJSON(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        datos = {
            "ts": ahora_iso(),
            "nivel": record.levelname,
            "servicio": SERVICIO,
            "msg": record.getMessage(),
        }
        datos.update(getattr(record, "datos", {}))
        return json.dumps(datos, ensure_ascii=False)


def configurar_logging() -> logging.Logger:
    manejador = logging.StreamHandler()
    manejador.setFormatter(FormatoJSON())
    log = logging.getLogger(SERVICIO)
    log.handlers = [manejador]
    log.setLevel(os.getenv("LOG_LEVEL", "INFO"))
    log.propagate = False
    return log


log = configurar_logging()


def error(status: int, codigo: str, detalle=None) -> JSONResponse:
    """Formato de error común: {"error": "<codigo>", "detalle": ...}."""
    return JSONResponse(status_code=status, content={"error": codigo, "detalle": detalle})


def crear_app(titulo: str) -> FastAPI:
    app = FastAPI(title=titulo)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        rid = request.headers.get(HEADER_REQUEST_ID) or str(uuid.uuid4())
        request.state.request_id = rid
        inicio = time.perf_counter()
        respuesta = await call_next(request)
        respuesta.headers[HEADER_REQUEST_ID] = rid
        log.info(
            "peticion",
            extra={"datos": {
                "request_id": rid,
                "metodo": request.method,
                "ruta": request.url.path,
                "status": respuesta.status_code,
                "ms": round((time.perf_counter() - inicio) * 1000, 2),
            }},
        )
        return respuesta

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException):
        return error(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validacion_error(_request: Request, exc: RequestValidationError):
        return error(422, "validacion", exc.errors())

    return app
