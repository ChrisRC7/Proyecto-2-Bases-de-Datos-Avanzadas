
"""B3.3: publicación durable de eventos hacia Historial.

Cada evento que devuelve el registro se guarda primero en una tabla SQLite local
(volumen del gateway) y después una tarea en segundo plano lo envía al historial. Si el
historial está caído, el evento se queda en SQLite y se reintenta con espera exponencial
(máx. 60 s). Así un evento confirmado por MongoDB no se pierde aunque el historial falle.
"""

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

# Archivo SQLite con los eventos pendientes de entregar.
DB_PATH = Path(os.getenv(
    "EVENTOS_PENDIENTES_DB",
    "/srv/pendientes/eventos.sqlite3",
))

LOTE = 50  # eventos por pasada (y por POST a historial)

# _aviso: despierta al trabajador cuando llega un evento nuevo.
# _trabajador: tarea asyncio que envía los pendientes.
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
    """Crea la tabla `pendientes` (si no existe) y activa el modo WAL.

    Columnas: event_id, contenido (JSON), intentos fallidos y `siguiente` (epoch en que
    toca volver a intentar).
    """
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
    """Inserta el evento en pendientes (si ya estaba con el mismo contenido, no hace nada).

    Lanza ValueError si el mismo event_id ya existe con otro contenido. Devuelve el event_id.
    """
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
    """Hasta LOTE eventos cuyo próximo intento ya llegó, del más atrasado al más reciente."""
    with _conexion() as db:
        return db.execute(
            """
            SELECT event_id, contenido, intentos
            FROM pendientes
            WHERE siguiente <= ?
            ORDER BY siguiente, rowid
            LIMIT ?
            """,
            (time.time(), LOTE),
        ).fetchall()


def _cerrar_pasada(entregados, fallidos):
    """Borra los entregados y reprograma los fallidos en una sola transacción."""
    ahora = time.time()

    with _conexion() as db:
        db.executemany(
            "DELETE FROM pendientes WHERE event_id = ?",
            [(identificador,) for identificador in entregados],
        )
        # Espera exponencial: 2, 4, 8, ... segundos, con tope de 60 s.
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
    """Cantidad total de eventos pendientes (para /salud)."""
    with _conexion() as db:
        return db.execute(
            "SELECT COUNT(*) FROM pendientes"
        ).fetchone()[0]


async def publicar_evento(evento: dict) -> dict:
    """Guarda primero; el envío HTTP ocurre en segundo plano."""
    if not isinstance(evento, dict) or "event_id" not in evento:
        raise ValueError("El evento necesita un event_id")

    # SQLite es bloqueante: se ejecuta en un hilo para no frenar el event loop.
    identificador = await asyncio.to_thread(_guardar, evento)

    # Despierta al trabajador para que lo envíe ya.

    if _aviso is not None:
        _aviso.set()

    return {
        "estado": "pendiente",
        "event_id": identificador,
    }


async def _enviar(eventos_json):
    """POST a historial. 5xx o sin respuesta: ServicioNoDisponible (lo lanza solicitar)."""
    respuesta = await solicitar("historial", "POST", "/eventos", json=eventos_json)
    return respuesta.status_code


async def despachar_pendientes():
    """Intenta enviar los eventos vencidos, conservando los fallidos.

    Historial acepta una lista: los eventos de la pasada van en un solo POST.
    Antes iba uno por POST y, con 100 usuarios, el publicador quedaba miles de
    eventos atrás (evidence/P6_carga_20261110-1727.md). Si historial rechaza la
    lista (4xx), se prueban de a uno para que un evento malo no frene a los demás.
    Devuelve cuántos eventos tomó la pasada.
    """
    filas = await asyncio.to_thread(_pendientes)

    if not filas:
        return 0

    entregados, fallidos = [], []

    try:
        status = await _enviar([json.loads(contenido) for _, contenido, _ in filas])
    except ServicioNoDisponible as exc:
        # Historial caído o con error: toda la pasada queda para después.
        status = None
        fallidos = [(identificador, intentos) for identificador, _, intentos in filas]
        log.warning(
            "eventos_pendientes",
            extra={"datos": {"cantidad": len(filas), "causa": exc.detalle}},
        )

    # 202: historial aceptó toda la lista.
    if status == 202:
        entregados = [identificador for identificador, _, _ in filas]

    # Historial respondió pero no aceptó la lista: se prueba evento por evento.
    elif status is not None:
        for identificador, contenido, intentos in filas:
            try:
                if await _enviar(json.loads(contenido)) != 202:
                    raise ValueError("historial no aceptó el evento")
                entregados.append(identificador)
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

    if entregados:
        log.info(
            "eventos_entregados",
            extra={"datos": {"cantidad": len(entregados)}},
        )

    # Un solo commit por pasada. Si el gateway se cae antes, los entregados se
    # vuelven a mandar y historial los descarta por event_id.
    await asyncio.to_thread(_cerrar_pasada, entregados, fallidos)
    return len(filas)


async def _procesar_cola():
    """Bucle del trabajador: despacha pendientes y espera un aviso o 2 s entre pasadas."""
    while True:
        try:
            tomados = await despachar_pendientes()
        except Exception:
            tomados = 0
            log.exception("error_procesando_eventos")

        if tomados >= LOTE:
            continue  # quedan más pendientes: otra pasada sin esperar

        try:
            await asyncio.wait_for(_aviso.wait(), timeout=2)
            _aviso.clear()
        except asyncio.TimeoutError:
            pass


async def iniciar_publicador():
    """Prepara SQLite y lanza el trabajador en segundo plano (al arrancar el gateway)."""
    global _aviso, _trabajador

    await asyncio.to_thread(_inicializar)
    _aviso = asyncio.Event()
    _trabajador = asyncio.create_task(_procesar_cola())


async def detener_publicador():
    """Cancela el trabajador y espera a que termine (al apagar el gateway)."""
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
    """Versión asíncrona de _contar."""
    return await asyncio.to_thread(_contar)
