
"""B3.6: limpieza durable despues de confirmar un envio."""

import asyncio
import json
import time

from app import eventos
from app.cliente_interno import ServicioNoDisponible, solicitar
from app.comun import log

_trabajador = None


def _inicializar():
    with eventos._conexion() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS limpiezas (
                tarea_id TEXT NOT NULL,
                token TEXT NOT NULL,
                accion TEXT NOT NULL,
                metodo TEXT NOT NULL,
                ruta TEXT NOT NULL,
                cuerpo TEXT NOT NULL,
                intentos INTEGER NOT NULL DEFAULT 0,
                siguiente REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (tarea_id, token, accion)
            )
        """)


def _registrar(tarea_id, anotador_id, token):
    """Registra todas las limpiezas en una sola transaccion SQLite."""
    acciones = [
        (
            "eliminar_borrador",
            "DELETE",
            f"/borradores/{tarea_id}",
            {"anotador_id": anotador_id, "token": token},
        ),
        (
            "soltar_lock",
            "POST",
            f"/locks/{tarea_id}",
            {"accion": "soltar", "token": token},
        ),
        (
            "cerrar_sesion",
            "POST",
            f"/sesiones/{anotador_id}",
            {"accion": "cerrar", "token": token},
        ),
    ]

    with eventos._conexion() as db:
        for nombre, metodo, ruta, cuerpo in acciones:
            db.execute(
                """
                INSERT OR IGNORE INTO limpiezas
                (tarea_id, token, accion, metodo, ruta, cuerpo)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    tarea_id,
                    token,
                    nombre,
                    metodo,
                    ruta,
                    json.dumps(cuerpo),
                ),
            )


def _pendientes():
    with eventos._conexion() as db:
        return db.execute(
            """
            SELECT tarea_id, token, accion, metodo, ruta, cuerpo, intentos
            FROM limpiezas
            WHERE siguiente <= ?
            LIMIT 50
            """,
            (time.time(),),
        ).fetchall()


def _confirmar(tarea_id, token, accion):
    with eventos._conexion() as db:
        db.execute(
            """
            DELETE FROM limpiezas
            WHERE tarea_id = ? AND token = ? AND accion = ?
            """,
            (tarea_id, token, accion),
        )


def _reprogramar(tarea_id, token, accion, intentos):
    demora = min(60, 2 ** min(intentos + 1, 6))

    with eventos._conexion() as db:
        db.execute(
            """
            UPDATE limpiezas
            SET intentos = ?, siguiente = ?
            WHERE tarea_id = ? AND token = ? AND accion = ?
            """,
            (
                intentos + 1,
                time.time() + demora,
                tarea_id,
                token,
                accion,
            ),
        )


async def registrar(tarea_id, anotador_id, token):
    await asyncio.to_thread(
        _registrar,
        str(tarea_id),
        str(anotador_id),
        str(token),
    )


async def procesar_pendientes():
    filas = await asyncio.to_thread(_pendientes)

    for tarea_id, token, accion, metodo, ruta, cuerpo, intentos in filas:
        try:
            respuesta = await solicitar(
                "sesion",
                metodo,
                ruta,
                json=json.loads(cuerpo),
            )

            # 404/409: ya no podemos completar esta limpieza con
            # el token original. No debemos tocar un dueño nuevo.
            if respuesta.status_code == 200:
                await asyncio.to_thread(
                    _confirmar, tarea_id, token, accion
                )

                log.info(
                    "limpieza_finalizada",
                    extra={"datos": {
                        "tarea_id": tarea_id,
                        "accion": accion,
                        "status": respuesta.status_code,
                    }},
                )

            else:
                await asyncio.to_thread(
                    _reprogramar,
                    tarea_id, token, accion, intentos,
                )

                log.warning(
                    "limpieza_no_confirmada",
                    extra={"datos": {
                        "tarea_id": tarea_id,
                        "accion": accion,
                        "status": respuesta.status_code,
                    }},
                )


        except ServicioNoDisponible:
            await asyncio.to_thread(
                _reprogramar,
                tarea_id, token, accion, intentos,
            )

            log.warning(
                "limpieza_reprogramada",
                extra={"datos": {
                    "tarea_id": tarea_id,
                    "accion": accion,
                }},
            )


async def _trabajar():
    while True:
        try:
            await procesar_pendientes()
        except Exception:
            log.exception("error_trabajador_limpiezas")

        await asyncio.sleep(2)


async def iniciar():
    global _trabajador

    await asyncio.to_thread(_inicializar)
    _trabajador = asyncio.create_task(_trabajar())


async def detener():
    global _trabajador

    if _trabajador is not None:
        _trabajador.cancel()
        try:
            await _trabajador
        except asyncio.CancelledError:
            pass
        _trabajador = None
