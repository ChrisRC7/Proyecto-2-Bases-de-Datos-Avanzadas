"""Regenera las tareas cuando las pruebas de carga agotan las disponibles (enunciado §9).

    docker compose --profile datos run --rm dataset \
        python -m dataset.regenerar_tareas --motivo "P6: se agotaron las tareas disponibles"

Vuelve a crear batches y tareas con la misma semilla (mismos ids, todas en "disponible", versión 0)
y deja clientes, anotadores y proyectos como están. Anota fecha, motivo y conteos en
evidence/regeneraciones.md, porque las corridas de antes y de después no son comparables si
dependían de tareas ya consumidas.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

from dataset.generar import SEMILLA, generar
from dataset.mongo import conectar, crear_indices, insertar_por_lotes

REGISTRO = Path(__file__).resolve().parents[1] / "evidence" / "regeneraciones.md"
ENCABEZADO = (
    "# Regeneraciones de tareas (A5.4)\n\n"
    "| Fecha (UTC) | Motivo | Antes, por estado | Después |\n"
    "| --- | --- | --- | --- |\n"
)


def por_estado(bd) -> dict:
    return {d["_id"]: d["n"] for d in bd.tareas.aggregate([{"$group": {"_id": "$estado", "n": {"$sum": 1}}}])}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--motivo", required=True)
    ap.add_argument("--semilla", type=int, default=SEMILLA)
    args = ap.parse_args()

    bd = conectar()
    antes = por_estado(bd)
    datos = generar(args.semilla)
    # delete_many y no drop: los índices se conservan mientras el registro sigue atendiendo.
    bd.tareas.delete_many({})
    bd.batches.delete_many({})
    insertar_por_lotes(bd.batches, datos["batches"])
    insertar_por_lotes(bd.tareas, datos["tareas"])
    crear_indices(bd)
    despues = por_estado(bd)

    if not REGISTRO.exists():
        REGISTRO.write_text(ENCABEZADO, encoding="utf-8")
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    with REGISTRO.open("a", encoding="utf-8") as f:
        f.write(f"| {fecha} | {args.motivo} | {dict(sorted(antes.items()))} | {despues} |\n")
    print(f"Antes:   {dict(sorted(antes.items()))}")
    print(f"Después: {despues}")
    print(f"Anotado en {REGISTRO}")


if __name__ == "__main__":
    main()
