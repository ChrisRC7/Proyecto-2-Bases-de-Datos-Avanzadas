"""Carga desde cero de la BD plataforma: borra las colecciones y las vuelve a llenar.

    docker compose --profile datos run --rm dataset
    docker compose --profile datos run --rm dataset python -m dataset.cargar --semilla 2026

También escribe dataset/salida/ids.json para el histórico de C (no se versiona: se regenera).
"""
import argparse
import json
import time
from collections import Counter
from pathlib import Path

from dataset.generar import SEMILLA, generar, ids
from dataset.mongo import conectar, crear_indices, insertar_por_lotes

# Colecciones que se borran y se vuelven a llenar, y carpeta donde se deja ids.json.
COLECCIONES = ["clientes", "anotadores", "proyectos", "batches", "tareas"]
SALIDA = Path(__file__).resolve().parent / "salida"


def main() -> None:
    """Genera los datos con la semilla, recarga MongoDB, escribe ids.json e imprime un resumen."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--semilla", type=int, default=SEMILLA)
    args = ap.parse_args()

    # Genera en memoria, borra las colecciones e inserta todo de nuevo (mide el tiempo total).
    inicio = time.perf_counter()
    datos = generar(args.semilla)
    bd = conectar()
    for col in COLECCIONES:
        bd[col].drop()
    for col in COLECCIONES:
        insertar_por_lotes(bd[col], datos[col])
    crear_indices(bd)
    seg = time.perf_counter() - inicio

    # Ids que necesita el generador del histórico para usar los mismos clientes/anotadores.
    SALIDA.mkdir(exist_ok=True)
    (SALIDA / "ids.json").write_text(json.dumps(ids(datos, args.semilla), ensure_ascii=False, indent=1))

    # Resumen de lo cargado.
    print(f"Semilla {args.semilla} · carga con w=majority en {seg:.1f} s")
    for col in COLECCIONES:
        print(f"  {col:<11} {bd[col].count_documents({}):>7}")
    por_modalidad = Counter(t["modalidad"] for t in datos["tareas"])
    por_tipo = Counter(t["tipo_tarea"] for t in datos["tareas"])
    roles = Counter(a["rol"] for a in datos["anotadores"])
    print("  tareas por modalidad:", dict(sorted(por_modalidad.items())))
    print("  tareas por tipo:     ", dict(sorted(por_tipo.items())))
    print(f"  anotadores: {roles['anotador']} anotadores + {roles['revisor']} revisores en {len(datos['equipos'])} equipos")
    print(f"  proyectos cerrados (solo historial, en ids.json): {len(datos['cerrados'])}")
    print(f"  proyecto LiDAR de ejemplo: {datos['proyectos'][0]['_id']} ({datos['proyectos'][0]['nombre']})")
    print(f"  ids para el histórico: {SALIDA / 'ids.json'}")


if __name__ == "__main__":
    main()
