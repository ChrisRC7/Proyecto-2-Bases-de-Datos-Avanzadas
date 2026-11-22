
"""B4.1: preparar y limpiar un entorno aislado de pruebas.

Por defecto solo muestra lo que haria.
No borra colecciones completas ni usa FLUSHDB.
"""

import argparse
import json
import os
import uuid

import httpx

from pathlib import Path


# URL del registro (este script corre dentro de la red interna) y archivo de manifiesto.
REGISTRO_URL = os.getenv(
    "REGISTRO_URL", "http://registro:8001"
)

MANIFIESTO = Path(__file__).resolve().parent / "casos_b41.json"


def guardar_manifiesto(datos):
    """Guarda en casos_b41.json qué proyecto y tareas creó este script."""
    MANIFIESTO.write_text(
        json.dumps(datos, indent=2),
        encoding="utf-8",
    )

# Dirección de Valkey (reservada para una futura limpieza segura).
VALKEY_URL = os.getenv(
    "VALKEY_URL", "redis://valkey:6379/0"
)


def crear_casos(numero, cliente_id):
    """Crea un proyecto nuevo con N tareas libres."""

    if MANIFIESTO.exists():
        raise RuntimeError(
            "Ya existe un manifiesto B4.1. "
            "No se crearan otros casos automaticamente."
        )

    with httpx.Client(timeout=10) as http:
        # 1. Crea el proyecto LiDAR de prueba.
        respuesta = http.post(
            f"{REGISTRO_URL}/proyectos",
            json={
                "cliente_id": cliente_id,
                "nombre": f"B4.1 pruebas {uuid.uuid4().hex[:8]}",
                "modalidad": "lidar",
                "sla_horas": 48,
                "taxonomia": [
                    {
                        "etiqueta": "lidar#ciclista",
                        "instruccion": "Caso controlado B4.1",
                    }
                ],
            },
        )
        respuesta.raise_for_status()
        proyecto_id = respuesta.json()["proyecto_id"]

        guardar_manifiesto({
            "proyecto_id": proyecto_id,
            "cliente_id": cliente_id,
            "total_tareas": numero,
            "estado": "proyecto_creado",
        })

        # 2. Crea un lote con `numero` tareas cuboide_3d.
        tareas = [
            {
                "tipo_tarea": "cuboide_3d",
                "archivo": {
                    "uri": f"s3://datos/lidar/b41/{uuid.uuid4().hex}.pcd",
                    "sha256": uuid.uuid4().hex * 2,
                    "bytes": 1000 + i,
                    "mime": "application/x-pcd",
                    "meta": {"min": 0.0, "max": 100.0},
                },
            }
            for i in range(numero)
        ]

        lote = http.post(
            f"{REGISTRO_URL}/proyectos/{proyecto_id}/lotes",
            json={"tareas": tareas},
        )
        lote.raise_for_status()

        guardar_manifiesto({
            "proyecto_id": proyecto_id,
            "cliente_id": cliente_id,
            "total_tareas": numero,
            "estado": "tareas_creadas",
            "lote": lote.json(),
        })

        return {
            "proyecto_id": proyecto_id,
            "total_tareas": numero,
            "lote": lote.json(),
        }

def main():
    """Sin --ejecutar solo muestra lo que haría; con --ejecutar crea los casos de prueba."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--cliente-id")
    parser.add_argument("--ejecutar", action="store_true")
    parser.add_argument("--limpiar", action="store_true")
    parser.add_argument("--tareas", nargs="*", default=[])
    parser.add_argument("--anotadores", nargs="*", default=[])
    args = parser.parse_args()

    if not args.ejecutar:
        print("SIMULACION: no se modificaron datos")
        print(f"Tareas solicitadas: {args.n}")
        print(f"Limpiar tareas: {args.tareas}")
        print(f"Limpiar anotadores: {args.anotadores}")
        print("Use --ejecutar para confirmar acciones")
        return

    if args.limpiar:
        parser.error(
            "La limpieza esta deshabilitada hasta implementar "
            "una operacion segura mediante Sesion"
        )

    if not args.cliente_id:
        parser.error(
            "Para crear casos se requiere --cliente-id"
        )

    if not 1 <= args.n <= 100:
        parser.error("--n debe estar entre 1 y 100")

    resultado = crear_casos(args.n, args.cliente_id)
    print(json.dumps(resultado, indent=2))


if __name__ == "__main__":
    main()
