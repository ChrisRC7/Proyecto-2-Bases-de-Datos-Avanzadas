
"""Pruebas B3.3: cola durable, reintentos y recuperación.

Se ejecuta dentro del contenedor del gateway:
    docker compose exec -T gateway python -m app.prueba_eventos_b33
No necesita los demás servicios: las llamadas internas se reemplazan con mocks.
Imprime una línea OK por cada caso y una marca final si todo pasa.
"""

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
    """Devuelve un evento válido (fila del lago) con un event_id nuevo."""
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
    """Usa una base SQLite temporal y un historial simulado para probar el publicador."""
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
                """Historial simulado caído: anota los ids y lanza ServicioNoDisponible."""
                llamadas.extend(e["event_id"] for e in kwargs["json"])
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
                """Historial simulado sano: guarda lo recibido y responde 202."""
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
            assert enviados[0] == [evento]
            print("OK | Historial recuperado: evento entregado")

            # 6. Verificar que se conservó el contenido original.
            assert enviados[0][0] == evento
            print("OK | Contenido y event_id preservados")

            # 7. Varios pendientes van en un solo POST.
            otros = [evento_ejemplo() for _ in range(3)]
            for e in otros:
                await eventos.publicar_evento(e)
            enviados.clear()

            with patch.object(
                eventos, "solicitar", side_effect=historial_recuperado
            ):
                await eventos.despachar_pendientes()

            assert enviados == [otros]
            assert await eventos.contar_pendientes() == 0
            print("OK | Tres eventos en un solo POST")

            # 8. Si historial rechaza la lista (422), se prueban de a uno:
            # el evento malo queda pendiente y los buenos se entregan.
            malo, bueno = evento_ejemplo(), evento_ejemplo()
            await eventos.publicar_evento(malo)
            await eventos.publicar_evento(bueno)

            async def historial_valida(*args, **kwargs):
                """Rechaza listas y el evento malo (422); acepta los demás de a uno (202)."""
                cuerpo = kwargs["json"]
                if isinstance(cuerpo, list) or cuerpo["event_id"] == malo["event_id"]:
                    return httpx.Response(422, json={"error": "validacion"})
                return httpx.Response(202, json={"aceptados": 1, "duplicados": 0})

            with patch.object(
                eventos, "solicitar", side_effect=historial_valida
            ):
                await eventos.despachar_pendientes()

            with eventos._conexion() as db:
                quedan = [f[0] for f in db.execute("SELECT event_id FROM pendientes")]

            assert quedan == [malo["event_id"]]
            print("OK | Lista rechazada: de a uno, el malo queda pendiente")

    print("B3.3_EVENTOS_OK")


if __name__ == "__main__":
    asyncio.run(pruebas())
