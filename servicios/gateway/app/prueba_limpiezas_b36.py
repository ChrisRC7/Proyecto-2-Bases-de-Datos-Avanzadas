
"""B3.6: pruebas de limpiezas persistentes y reintentos.

Se ejecuta dentro del contenedor del gateway:
    docker compose exec -T gateway python -m app.prueba_limpiezas_b36
No necesita los demás servicios: las llamadas internas se reemplazan con mocks.
Imprime una línea OK por cada caso y una marca final si todo pasa.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx

from app import eventos, limpiezas
from app.cliente_interno import ServicioNoDisponible


async def pruebas():
    """Usa una base SQLite temporal y un servicio de sesión simulado."""
    with tempfile.TemporaryDirectory() as carpeta:
        with patch.object(
            eventos, "DB_PATH", Path(carpeta) / "prueba.sqlite3"
        ):
            limpiezas._inicializar()

            tarea = str(uuid4())
            anotador = str(uuid4())
            token = str(uuid4())

            # 1. Guardar las tres acciones.
            await limpiezas.registrar(tarea, anotador, token)

            with eventos._conexion() as db:
                cantidad = db.execute(
                    "SELECT COUNT(*) FROM limpiezas"
                ).fetchone()[0]

            assert cantidad == 3
            print("OK | Tres limpiezas guardadas en SQLite")

            # 2. Simular Sesión caída.
            async def sesion_caida(*args, **kwargs):
                """Sesión simulada caída."""
                raise ServicioNoDisponible("sesion", "Sin conexión")

            with patch.object(
                limpiezas, "solicitar", side_effect=sesion_caida
            ):
                await limpiezas.procesar_pendientes()

            with eventos._conexion() as db:
                cantidad = db.execute(
                    "SELECT COUNT(*) FROM limpiezas"
                ).fetchone()[0]

            assert cantidad == 3
            print("OK | Sesión caída: limpiezas conservadas")

            # 3. Reinicializar SQLite: los pendientes siguen ahí.
            limpiezas._inicializar()

            with eventos._conexion() as db:
                cantidad = db.execute(
                    "SELECT COUNT(*) FROM limpiezas"
                ).fetchone()[0]
                db.execute(
                    "UPDATE limpiezas SET siguiente = 0"
                )

            assert cantidad == 3
            print("OK | Limpiezas recuperadas tras reinicializar")

            # 4. Simular recuperación de Sesión.
            llamadas = []

            async def sesion_recuperada(*args, **kwargs):
                """Sesión simulada sana: anota la ruta llamada y responde 200."""
                llamadas.append(args[2])
                return httpx.Response(
                    200,
                    json={"estado": "ok"},
                    request=httpx.Request(
                        "POST", "http://sesion:8002/prueba"
                    ),
                )

            with patch.object(
                limpiezas, "solicitar",
                side_effect=sesion_recuperada
            ):
                await limpiezas.procesar_pendientes()

            assert len(llamadas) == 3

            with eventos._conexion() as db:
                cantidad = db.execute(
                    "SELECT COUNT(*) FROM limpiezas"
                ).fetchone()[0]

            assert cantidad == 0
            print("OK | Recuperación: tres limpiezas ejecutadas")

            # 5. Registrar dos veces no duplica acciones.
            await limpiezas.registrar(tarea, anotador, token)
            await limpiezas.registrar(tarea, anotador, token)

            with eventos._conexion() as db:
                cantidad = db.execute(
                    "SELECT COUNT(*) FROM limpiezas"
                ).fetchone()[0]

            assert cantidad == 3
            print("OK | Registro idempotente de limpiezas")


            # 6. Un conflicto HTTP 409 no confirma la limpieza.
            with eventos._conexion() as db:
                db.execute("UPDATE limpiezas SET siguiente = 0")

            async def sesion_conflicto(*args, **kwargs):
                """Sesión simulada que responde 409 (el recurso ya no es de este token)."""
                return httpx.Response(
                    409,
                    json={"error": "conflicto_lock"},
                    request=httpx.Request(
                        "POST", "http://sesion:8002/prueba"
                    ),
                )

            with patch.object(
                limpiezas, "solicitar",
                side_effect=sesion_conflicto
            ):
                await limpiezas.procesar_pendientes()

            with eventos._conexion() as db:
                cantidad = db.execute(
                    "SELECT COUNT(*) FROM limpiezas"
                ).fetchone()[0]

            assert cantidad == 0
            print("OK | HTTP 409: limpiezas descartadas, no se reintentan")

            # 7. ejecutar: registra y corre las tres en el momento.
            llamadas = []

            with patch.object(
                limpiezas, "solicitar",
                side_effect=sesion_recuperada
            ):
                await limpiezas.ejecutar(tarea, anotador, token)

            with eventos._conexion() as db:
                cantidad = db.execute(
                    "SELECT COUNT(*) FROM limpiezas"
                ).fetchone()[0]

            assert llamadas == [
                f"/sesiones/{anotador}",
                f"/locks/{tarea}",
                f"/sesiones/{anotador}",
            ]
            assert cantidad == 0
            print("OK | ejecutar: borrador, lock y sesión en el momento")


    print("B3.6_LIMPIEZAS_OK")


if __name__ == "__main__":
    asyncio.run(pruebas())
