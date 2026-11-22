
"""B3.6: limpieza durable despues de confirmar un envio.

Cuando el registro confirma un envío (o una liberación), hay que borrar el borrador,
soltar el lock y cerrar la sesión en el servicio de sesión. Esas tres acciones se anotan
primero en la tabla SQLite `limpiezas` (misma base que los eventos pendientes) y luego se
ejecutan; si sesión no responde, quedan en la tabla y un trabajador en segundo plano las
reintenta cada 2 s con espera exponencial.
"""

import asyncio
import json
import time

from app import eventos
from app.cliente_interno import ServicioNoDisponible, solicitar
from app.comun import log

# Tarea asyncio que reintenta las limpiezas pendientes.
_trabajador = None


def _inicializar():
    """Crea la tabla `limpiezas` (una fila por tarea, token y acción) si no existe."""
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


def _registrar(tarea_id, anotador_id, token, espera=0):
    """Registra todas las limpiezas en una sola transaccion SQLite.

    espera: segundos antes de que el trabajador en segundo plano las tome.
    Devuelve las filas como las lee _pendientes, para correrlas en el momento.
    """
    # (nombre, método, ruta en sesión, cuerpo) de cada limpieza.
    acciones = [
        (
            "eliminar_borrador",
            "POST",
            f"/sesiones/{anotador_id}",
            {"accion": "borrar_borrador", "tarea_id": tarea_id, "token": token},
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
    filas = [
        (tarea_id, token, nombre, metodo, ruta, json.dumps(cuerpo), 0)
        for nombre, metodo, ruta, cuerpo in acciones
    ]

    with eventos._conexion() as db:
        db.executemany(
            """
            INSERT OR IGNORE INTO limpiezas
            (tarea_id, token, accion, metodo, ruta, cuerpo, intentos, siguiente)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [fila + (time.time() + espera,) for fila in filas],
        )

    return filas


def _pendientes():
    """Hasta 50 limpiezas cuyo próximo intento ya llegó."""
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


async def registrar(tarea_id, anotador_id, token):
    """Solo anota las limpiezas (sin ejecutarlas); las toma el trabajador."""
    await asyncio.to_thread(
        _registrar,
        str(tarea_id),
        str(anotador_id),
        str(token),
    )


def _cerrar_varias(cerradas, reprogramadas):
    """Aplica el resultado de una pasada en una sola transacción SQLite."""
    ahora = time.time()

    with eventos._conexion() as db:
        db.executemany(
            """
            DELETE FROM limpiezas
            WHERE tarea_id = ? AND token = ? AND accion = ?
            """,
            cerradas,
        )
        # Espera exponencial: 2, 4, 8, ... segundos, con tope de 60 s.
        db.executemany(
            """
            UPDATE limpiezas
            SET intentos = ?, siguiente = ?
            WHERE tarea_id = ? AND token = ? AND accion = ?
            """,
            [
                (
                    intentos + 1,
                    ahora + min(60, 2 ** min(intentos + 1, 6)),
                    tarea_id,
                    token,
                    accion,
                )
                for tarea_id, token, accion, intentos in reprogramadas
            ],
        )


async def _procesar(filas):
    """Ejecuta cada limpieza contra sesión y luego cierra o reprograma las filas en SQLite."""
    cerradas, reprogramadas = [], []

    for tarea_id, token, accion, metodo, ruta, cuerpo, intentos in filas:
        try:
            respuesta = await solicitar(
                "sesion",
                metodo,
                ruta,
                json=json.loads(cuerpo),
            )

        except ServicioNoDisponible:
            # Sesión no responde: se reintenta más tarde.
            reprogramadas.append((tarea_id, token, accion, intentos))

            log.warning(
                "limpieza_reprogramada",
                extra={"datos": {
                    "tarea_id": tarea_id,
                    "accion": accion,
                }},
            )
            continue

        # Cualquier respuesta < 500 cierra la limpieza. 404/409: el borrador,
        # el lock o la sesión ya no son de este token (no existen, vencieron o
        # son de un dueño nuevo); reintentar no lo cambia y no debemos tocarlos.
        cerradas.append((tarea_id, token, accion))

        log.info(
            "limpieza_finalizada" if respuesta.status_code == 200
            else "limpieza_descartada",
            extra={"datos": {
                "tarea_id": tarea_id,
                "accion": accion,
                "status": respuesta.status_code,
            }},
        )

    if cerradas or reprogramadas:
        await asyncio.to_thread(_cerrar_varias, cerradas, reprogramadas)


async def procesar_pendientes():
    """Una pasada del trabajador: toma las limpiezas vencidas y las ejecuta."""
    filas = await asyncio.to_thread(_pendientes)
    await _procesar(filas)


async def ejecutar(tarea_id, anotador_id, token):
    """Registra las limpiezas y las corre en el momento.

    El lock se suelta antes de responder: si la tarea se rechaza y vuelve a
    la cola, el siguiente anotador ya puede tomarla. Lo que falle porque
    Sesión no responde queda en SQLite y lo reintenta el trabajador; la
    espera de 30 s evita que el trabajador las tome mientras corren aquí.
    """
    tarea_id, anotador_id, token = str(tarea_id), str(anotador_id), str(token)
    filas = await asyncio.to_thread(
        _registrar, tarea_id, anotador_id, token, 30
    )

    try:
        await _procesar(filas)
    except Exception:
        # MongoDB ya confirmó: no se cambia la respuesta. Lo que quedó en
        # SQLite lo termina el trabajador.
        log.exception(
            "error_limpieza_inmediata",
            extra={"datos": {"tarea_id": tarea_id}},
        )


async def _trabajar():
    """Bucle del trabajador: una pasada cada 2 s; un error no lo detiene."""
    while True:
        try:
            await procesar_pendientes()
        except Exception:
            log.exception("error_trabajador_limpiezas")

        await asyncio.sleep(2)


async def iniciar():
    """Crea la tabla y lanza el trabajador (al arrancar el gateway)."""
    global _trabajador

    await asyncio.to_thread(_inicializar)
    _trabajador = asyncio.create_task(_trabajar())


async def detener():
    """Cancela el trabajador y espera a que termine (al apagar el gateway)."""
    global _trabajador

    if _trabajador is not None:
        _trabajador.cancel()
        try:
            await _trabajador
        except asyncio.CancelledError:
            pass
        _trabajador = None
