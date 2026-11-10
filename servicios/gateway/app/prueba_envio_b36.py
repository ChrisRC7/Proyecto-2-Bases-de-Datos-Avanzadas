
"""Pruebas B3.6: envio, errores y limpieza segura."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from app import envio
from app.cliente_interno import ServicioNoDisponible


def respuesta(codigo, datos=None):
    return httpx.Response(
        codigo,
        json=datos,
        request=httpx.Request("POST", "http://prueba.local/"),
    )


async def pruebas():
    tarea_id = str(uuid4())
    anotador_id = str(uuid4())
    token = str(uuid4())
    anotacion = {
        "etiquetas": ["texto#prueba"],
        "geometria": {"tipo": "span", "inicio": 0, "fin": 5},
    }

    evento = {
        "event_id": str(uuid4()),
        "estado": "anotada",
    }

    datos_registro = {
        "tarea": {
            "tarea_id": tarea_id,
            "estado": "anotada",
            "version": 2,
        },
        "evento": evento,
    }

    argumentos = (tarea_id, anotador_id, token, anotacion)

    # 1. Envio correcto: Registro 200, evento y limpieza en el momento.
    solicitudes = AsyncMock(side_effect=[
        respuesta(200, {"estado": "ok"}),  # renovar lock
        respuesta(200, datos_registro),    # transicion Registro
    ])
    limpiar = AsyncMock()

    publicar = AsyncMock(return_value={"estado": "pendiente"})

    with patch.object(envio, "solicitar", solicitudes), \
         patch.object(envio.eventos, "publicar_evento", publicar), \
         patch.object(envio.limpiezas, "ejecutar", limpiar):
        codigo, datos = await envio.enviar(*argumentos)

    assert codigo == 200
    assert datos["estado"] == "anotada"
    assert datos["version"] == 2
    assert datos["event_id"] == evento["event_id"]
    assert solicitudes.await_count == 2
    publicar.assert_awaited_once_with(evento)
    limpiar.assert_awaited_once_with(tarea_id, anotador_id, token)

    rutas = [c.args[2] for c in solicitudes.await_args_list]
    assert rutas == [
        f"/locks/{tarea_id}",
        f"/tareas/{tarea_id}/transicion",
    ]
    print("OK | HTTP 200: evento y limpieza posterior")

    # 2. Conflicto de token: no llamar Registro.
    solicitudes = AsyncMock(return_value=respuesta(
        409, {"error": "conflicto_lock", "detalle": "Token incorrecto"}
    ))
    publicar = AsyncMock()

    with patch.object(envio, "solicitar", solicitudes), \
         patch.object(envio.eventos, "publicar_evento", publicar):
        codigo, _ = await envio.enviar(*argumentos)

    assert codigo == 409
    assert solicitudes.await_count == 1
    publicar.assert_not_awaited()
    print("OK | Lock 409: sin envio ni evento")

    # 3. Registro devuelve 409: conservar borrador.
    solicitudes = AsyncMock(side_effect=[
        respuesta(200, {"estado": "ok"}),
        respuesta(409, {"error": "transicion_invalida"}),
    ])
    publicar = AsyncMock()

    with patch.object(envio, "solicitar", solicitudes), \
         patch.object(envio.eventos, "publicar_evento", publicar):
        codigo, _ = await envio.enviar(*argumentos)

    assert codigo == 409
    assert solicitudes.await_count == 2
    publicar.assert_not_awaited()
    print("OK | Registro 409: borrador conservado")

    # 4. Registro devuelve 503: no reintento ni limpieza.
    solicitudes = AsyncMock(side_effect=[
        respuesta(200, {"estado": "ok"}),
        ServicioNoDisponible("registro", "HTTP 503"),
    ])
    publicar = AsyncMock()

    with patch.object(envio, "solicitar", solicitudes), \
         patch.object(envio.eventos, "publicar_evento", publicar):
        try:
            await envio.enviar(*argumentos)
            raise AssertionError("Debio fallar con 503")
        except ServicioNoDisponible:
            pass

    assert solicitudes.await_count == 2
    publicar.assert_not_awaited()
    print("OK | Registro 503: sin reintento ni limpieza")

    # 5. Si falla el registro del evento, no se limpia.
    solicitudes = AsyncMock(side_effect=[
        respuesta(200, {"estado": "ok"}),
        respuesta(200, datos_registro),
    ])
    publicar = AsyncMock(side_effect=OSError("SQLite no disponible"))

    with patch.object(envio, "solicitar", solicitudes), \
         patch.object(envio.eventos, "publicar_evento", publicar):
        try:
            await envio.enviar(*argumentos)
            raise AssertionError("Debio fallar SQLite")
        except OSError:
            pass

    assert solicitudes.await_count == 2
    print("OK | SQLite falla: sin limpiar borrador")

    # 6. Falla limpieza tras Registro 200: no repetir envio.
    # La limpieza que no se pudo hacer queda en SQLite para el trabajador.
    solicitudes = AsyncMock(side_effect=[
        respuesta(200, {"estado": "ok"}),
        respuesta(200, datos_registro),
    ])
    sesion = AsyncMock(side_effect=[
        ServicioNoDisponible("sesion", "Sin conexion"),
        respuesta(200, {"estado": "ok"}),
        respuesta(200, {"estado": "ok"}),
    ])
    publicar = AsyncMock(return_value={"estado": "pendiente"})

    with tempfile.TemporaryDirectory() as carpeta, \
         patch.object(envio.eventos, "DB_PATH", Path(carpeta) / "p.sqlite3"), \
         patch.object(envio, "solicitar", solicitudes), \
         patch.object(envio.limpiezas, "solicitar", sesion), \
         patch.object(envio.eventos, "publicar_evento", publicar):
        envio.limpiezas._inicializar()
        codigo, _ = await envio.enviar(*argumentos)

        with envio.eventos._conexion() as db:
            quedan = db.execute(
                "SELECT accion FROM limpiezas"
            ).fetchall()

    assert codigo == 200
    assert solicitudes.await_count == 2
    assert sesion.await_count == 3
    assert quedan == [("eliminar_borrador",)]
    publicar.assert_awaited_once_with(evento)
    print("OK | Fallo de limpieza: no repetir envio, queda en SQLite")

    print("B3.6_ENVIO_OK")


if __name__ == "__main__":
    asyncio.run(pruebas())
