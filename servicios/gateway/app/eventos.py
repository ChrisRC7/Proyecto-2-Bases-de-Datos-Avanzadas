
"""B3.3: publicación durable de eventos hacia Historial."""

import asyncio
import json
import os
import sqlite3
import time
from contextlib import contextmanager
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


@contextmanager
def _conexion():
    """Conexión a SQLite que confirma al salir (o deshace si hubo error) y siempre se cierra.

    Antes la conexión quedaba abierta hasta que Python la recolectara. Con varias peticiones
    escribiendo a la vez aparecían esperas de 10 s y «database is locked»
    (evidence/A6.2_simulador_gateway_20261110-1721.txt).
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conexion = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        conexion.execute("PRAGMA synchronous=FULL")
        with conexion:
            yield conexion
    finally:
        conexion.close()


def _inicializar():
    with _conexion() as db:
        # WAL: leer no bloquea a quien escribe y cada commit hace un solo fsync
        # (unos 10 ms en el volumen de Docker frente a ~22 ms con el journal por defecto).
        db.execute("PRAGMA journal_mode=WAL")
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


def _cerrar_pasada(entregados, fallidos):
    """Borra los entregados y reprograma los fallidos en una sola transacción."""
    ahora = time.time()

    with _conexion() as db:
        db.executemany(
            "DELETE FROM pendientes WHERE event_id = ?",
            [(identificador,) for identificador in entregados],
        )
        db.executemany(
            """
            UPDATE pendientes
            SET intentos = ?, siguiente = ?
            WHERE event_id = ?
            """,
            [
                (intentos + 1, ahora + min(60, 2 ** min(intentos + 1, 6)), identificador)
                for identificador, intentos in fallidos
            ],
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
    entregados, fallidos = [], []

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

            entregados.append(identificador)

            log.info(
                "evento_entregado",
                extra={"datos": {"event_id": identificador}},
            )

        except Exception as exc:
            # El evento permanece en SQLite para el siguiente intento.
            fallidos.append((identificador, intentos))

            log.warning(
                "evento_pendiente",
                extra={"datos": {
                    "event_id": identificador,
                    "causa": type(exc).__name__,
                }},
            )

    # Un solo commit por pasada en lugar de uno por evento. Si el gateway se cae
    # antes, los entregados se vuelven a mandar y historial los descarta por event_id.
    if entregados or fallidos:
        await asyncio.to_thread(_cerrar_pasada, entregados, fallidos)


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
