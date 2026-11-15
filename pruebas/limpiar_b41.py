
"""B4.1: limpieza controlada de Valkey para tareas de prueba.

Ejecutar exclusivamente desde el contenedor sesion.
Por defecto es una simulacion: no elimina nada.
"""

import argparse
import json
from pathlib import Path
from uuid import UUID

from app.locks import cliente


PROYECTO_ESPERADO = "d2347c44-16a6-43cf-ad47-1c1e435b4b09"

CARPETA = Path("/srv/pruebas")
MANIFIESTO = CARPETA / "casos_b41.json"
ARCHIVO_TAREAS = CARPETA / "tareas_b41.json"


def cargar_ids():
    manifiesto = json.loads(
        MANIFIESTO.read_text(encoding="utf-8-sig")
    )
    tareas = json.loads(
        ARCHIVO_TAREAS.read_text(encoding="utf-8-sig")
    )

    if manifiesto.get("proyecto_id") != PROYECTO_ESPERADO:
        raise ValueError("El proyecto no coincide con B4.1")

    if manifiesto.get("estado") != "tareas_creadas":
        raise ValueError("El manifiesto no esta completo")

    if not isinstance(tareas, list) or len(tareas) != 10:
        raise ValueError("Se esperaban exactamente 10 tareas")

    tareas = [str(UUID(t)) for t in tareas]

    if len(set(tareas)) != 10:
        raise ValueError("Hay IDs repetidos")

    if manifiesto.get("total_tareas") != len(tareas):
        raise ValueError("Los conteos no coinciden")

    return tareas


def sesiones_relacionadas(tareas):
    """Detecta sesiones asociadas a las tareas controladas."""
    permitidas = set(tareas)
    relacionadas = []

    for llave in cliente.scan_iter(match="sesion:*"):
        sesion = cliente.hgetall(llave)

        if sesion.get("tarea") in permitidas:
            relacionadas.append(llave)

    return relacionadas


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ejecutar", action="store_true")
    args = parser.parse_args()

    tareas = cargar_ids()

    # No eliminar datos mientras alguien tiene una
    # sesion activa sobre las tareas controladas.
    sesiones = sesiones_relacionadas(tareas)

    if sesiones:
        print("LIMPIEZA BLOQUEADA: hay sesiones relacionadas.")
        for llave in sesiones:
            print(f"  {llave}")
        print("Revisar esas sesiones antes de continuar.")
        return

    llaves = []

    for tarea_id in tareas:
        llaves.extend([
            f"lock:tarea:{tarea_id}",
            f"borrador:{tarea_id}",
        ])

    existentes = [
        llave for llave in llaves
        if cliente.exists(llave)
    ]

    print(f"Proyecto: {PROYECTO_ESPERADO}")
    print(f"Tareas autorizadas: {len(tareas)}")
    print(f"Llaves existentes: {len(existentes)}")

    if not args.ejecutar:
        print("SIMULACION: no se elimino ninguna llave")
        for llave in existentes:
            print(f"  {llave}")
        return

    for llave in existentes:
        cliente.delete(llave)

    print(f"Limpieza completada: {len(existentes)} llaves")


if __name__ == "__main__":
    main()
