
"""B3.3: publicación durable de eventos hacia Historial."""

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from uuid import UUID

from app.cliente_interno import ServicioNoDisponible, solicitar
from app.comun import log

DB_PATH = Path(os.getenv(
    "EVENTOS_PENDIENTES_DB",
    "/srv/pendientes/eventos.sqlite3",
))

_aviso = None
_trabajador = None


def _conexion():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conexion = sqlite3.connect(str(DB_PATH), timeout=10)
    conexion.execute("PRAGMA synchronous=FULL")
    return conexion


def _inicializar():
    with _conexion() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS pendientes (
                event_id TEXT PRIMARY KEY,
                contenido TEXT NOT NULL,
                intentos INTEGER NOT NULL DEFAULT 0,
                siguiente REAL NOT NULL DEFAULT 0
            )
        """)


def _guardar(evento):
    identificador = str(UUID(str(evento["event_id"])))
    contenido = json.dumps(evento, ensure_ascii=False)

    with _conexion() as db:
        db.execute(
            """
            INSERT OR IGNORE INTO pendientes (event_id, contenido)
            VALUES (?, ?)
            """,
            (identificador, contenido),
        )

        anterior = db.execute(
            "SELECT contenido FROM pendientes WHERE event_id = ?",
            (identificador,),
        ).fetchone()

        if anterior and json.loads(anterior[0]) != evento:
            raise ValueError(
                "El event_id ya está pendiente con otro contenido"
            )

    return identificador


def _pendientes():
    with _conexion() as db:
        return db.execute(
            """
            SELECT event_id, contenido, intentos
            FROM pendientes
            WHERE siguiente <= ?
            ORDER BY siguiente, rowid
            LIMIT 50
            """,
            (time.time(),),
        ).fetchall()


def _confirmar(identificador):
    with _conexion() as db:
        db.execute(
            "DELETE FROM pendientes WHERE event_id = ?",
            (identificador,),
        )


def _reprogramar(identificador, intentos):
    demora = min(60, 2 ** min(intentos + 1, 6))

    with _conexion() as db:
        db.execute(
            """
            UPDATE pendientes
            SET intentos = ?, siguiente = ?
            WHERE event_id = ?
            """,
            (intentos + 1, time.time() + demora, identificador),
        )


def _contar():
    with _conexion() as db:
        return db.execute(
            "SELECT COUNT(*) FROM pendientes"
        ).fetchone()[0]


async def publicar_evento(evento: dict) -> dict:
    """Guarda primero; el envío HTTP ocurre en segundo plano."""
    if not isinstance(evento, dict) or "event_id" not in evento:
        raise ValueError("El evento necesita un event_id")

    identificador = await asyncio.to_thread(_guardar, evento)

    if _aviso is not None:
        _aviso.set()

    return {
        "estado": "pendiente",
        "event_id": identificador,
    }


async def despachar_pendientes():
    """Intenta enviar los eventos vencidos, conservando los fallidos."""
    filas = await asyncio.to_thread(_pendientes)

    for identificador, contenido, intentos in filas:
        try:
            respuesta = await solicitar(
                "historial",
                "POST",
                "/eventos",
                json=json.loads(contenido),
            )

            if respuesta.status_code != 202:
                raise ServicioNoDisponible(
                    "historial",
                    f"Respuesta inesperada: {respuesta.status_code}",
                )

            await asyncio.to_thread(_confirmar, identificador)

            log.info(
                "evento_entregado",
                extra={"datos": {"event_id": identificador}},
            )

        except Exception as exc:
            # El evento permanece en SQLite para el siguiente intento.
            await asyncio.to_thread(
                _reprogramar, identificador, intentos
            )

            log.warning(
                "evento_pendiente",
                extra={"datos": {
                    "event_id": identificador,
                    "causa": type(exc).__name__,
                }},
            )


async def _procesar_cola():
    while True:
        try:
            await despachar_pendientes()
        except Exception:
            log.exception("error_procesando_eventos")

        try:
            await asyncio.wait_for(_aviso.wait(), timeout=2)
            _aviso.clear()
        except asyncio.TimeoutError:
            pass


async def iniciar_publicador():
    global _aviso, _trabajador

    await asyncio.to_thread(_inicializar)
    _aviso = asyncio.Event()
    _trabajador = asyncio.create_task(_procesar_cola())


async def detener_publicador():
    global _trabajador, _aviso

    if _trabajador is not None:
        _trabajador.cancel()
        try:
            await _trabajador
        except asyncio.CancelledError:
            pass

    _trabajador = None
    _aviso = None


async def contar_pendientes():
    return await asyncio.to_thread(_contar)
