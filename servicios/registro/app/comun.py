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
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Nombre del servicio que aparece en cada línea de log (lo fija docker-compose).
SERVICIO = os.getenv("SERVICIO", "servicio")
# Encabezado HTTP con el que viaja el identificador de cada petición entre servicios.
HEADER_REQUEST_ID = "X-Request-Id"


def ahora_iso() -> str:
    """Devuelve la fecha y hora actual en UTC con formato ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


class FormatoJSON(logging.Formatter):
    """Formateador de logging que escribe cada registro como una línea JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Convierte un registro de log en JSON con hora, nivel, servicio y mensaje.

        Si el registro trae un diccionario en `extra={"datos": {...}}`, sus campos se
        agregan a la misma línea JSON (por ejemplo request_id, ruta, status, ms).
        """
        datos = {
            "ts": ahora_iso(),
            "nivel": record.levelname,
            "servicio": SERVICIO,
            "msg": record.getMessage(),
        }
        datos.update(getattr(record, "datos", {}))
        return json.dumps(datos, ensure_ascii=False)


def configurar_logging() -> logging.Logger:
    """Crea el logger del servicio: escribe JSON a stdout con el nivel de LOG_LEVEL.

    Se reemplazan los manejadores existentes y se apaga la propagación para que cada
    mensaje salga una sola vez y siempre en formato JSON.
    """
    manejador = logging.StreamHandler()
    manejador.setFormatter(FormatoJSON())
    log = logging.getLogger(SERVICIO)
    log.handlers = [manejador]
    log.setLevel(os.getenv("LOG_LEVEL", "INFO"))
    log.propagate = False
    return log


# Logger compartido que importan los demás módulos del servicio.
log = configurar_logging()


def error(status: int, codigo: str, detalle=None) -> JSONResponse:
    """Formato de error común: {"error": "<codigo>", "detalle": ...}."""
    return JSONResponse(status_code=status, content={"error": codigo, "detalle": detalle})


def crear_app(titulo: str) -> FastAPI:
    """Crea la aplicación FastAPI del servicio con el middleware y los manejadores comunes.

    - Middleware `request_id`: reutiliza o genera el X-Request-Id, lo devuelve en la
      respuesta y escribe una línea de log por petición con su duración en ms.
    - Manejadores de error: convierten HTTPException y errores de validación (422) al
      formato común {"error", "detalle"}.
    """
    app = FastAPI(title=titulo)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        """Propaga el X-Request-Id y registra método, ruta, status y duración."""
        # Se usa el id que manda quien llama; si no viene, se genera uno nuevo.
        rid = request.headers.get(HEADER_REQUEST_ID) or str(uuid.uuid4())
        request.state.request_id = rid
        inicio = time.perf_counter()
        respuesta = await call_next(request)
        # El mismo id se devuelve para poder seguir la petición entre servicios.
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
        """Devuelve cualquier HTTPException (404, 405, ...) con el formato común."""
        return error(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validacion_error(_request: Request, exc: RequestValidationError):
        """Devuelve los errores de validación del cuerpo como 422 con el formato común."""
        # Los validadores propios dejan la excepción en "ctx"; se pasa a texto para poder
        # serializarla (si no, el 422 termina en 500).
        return error(422, "validacion", jsonable_encoder(exc.errors(), custom_encoder={Exception: str}))

    return app
