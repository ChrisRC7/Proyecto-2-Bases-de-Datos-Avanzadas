
"""Pruebas B3.3: cola durable, reintentos y recuperación."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx

from app import eventos
from app.cliente_interno import ServicioNoDisponible


def evento_ejemplo():
    return {
        "event_id": str(uuid4()),
        "ts": "2026-11-09T20:00:00Z",
        "tarea_id": "tarea-b33-prueba",
        "proyecto_id": "proyecto-b33-prueba",
        "cliente_id": "cliente-b33-prueba",
        "batch_id": "batch-b33-prueba",
        "modalidad": "texto",
        "tipo_tarea": "span",
        "estado": "abierta",
        "version": 1,
        "anotador_id": "anotador-b33",
        "equipo_id": None,
        "revisor_id": None,
        "motivo_rechazo": None,
        "tomada_ts": None,
        "sla_horas": 24,
    }


async def pruebas():
    with tempfile.TemporaryDirectory() as carpeta:
        ruta_db = Path(carpeta) / "eventos.sqlite3"

        with patch.object(eventos, "DB_PATH", ruta_db):
            eventos._inicializar()

            evento = evento_ejemplo()

            # 1. El evento se almacena antes del envío.
            resultado = await eventos.publicar_evento(evento)
            assert resultado["event_id"] == evento["event_id"]
            assert await eventos.contar_pendientes() == 1
            print("OK | Evento guardado en SQLite")

            # 2. Publicar dos veces el mismo ID no duplica pendientes.
            await eventos.publicar_evento(evento)
            assert await eventos.contar_pendientes() == 1
            print("OK | event_id idempotente")

            # 3. Simular Historial caído.
            llamadas = []

            async def historial_caido(*args, **kwargs):
                llamadas.append(kwargs["json"]["event_id"])
                raise ServicioNoDisponible("historial", "Sin conexión")

            with patch.object(
                eventos, "solicitar", side_effect=historial_caido
            ):
                await eventos.despachar_pendientes()

            assert llamadas == [evento["event_id"]]
            assert await eventos.contar_pendientes() == 1
            print("OK | Historial caído: evento conservado")

            # 4. Simular reinicio: cerrar y volver a abrir SQLite.
            eventos._inicializar()
            assert await eventos.contar_pendientes() == 1
            print("OK | Pendiente recuperado desde SQLite")

            # 5. Historial responde 202: se elimina el pendiente.
            enviados = []

            async def historial_recuperado(*args, **kwargs):
                enviados.append(kwargs["json"])
                return httpx.Response(202, json={
                    "aceptados": 1,
                    "duplicados": 0,
                })

            # El reintento anterior programó una espera.
            # La adelantamos solamente dentro de esta prueba.
            with eventos._conexion() as db:
                db.execute("UPDATE pendientes SET siguiente = 0")

            with patch.object(
                eventos, "solicitar", side_effect=historial_recuperado
            ):
                await eventos.despachar_pendientes()

            assert await eventos.contar_pendientes() == 0
            assert enviados[0]["event_id"] == evento["event_id"]
            print("OK | Historial recuperado: evento entregado")

            # 6. Verificar que se conservó el contenido original.
            assert enviados[0] == evento
            print("OK | Contenido y event_id preservados")

    print("B3.3_EVENTOS_OK")


if __name__ == "__main__":
    asyncio.run(pruebas())
