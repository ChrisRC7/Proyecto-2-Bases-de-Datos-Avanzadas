"""Compactación de un mes del lago (C2.6): junta los part-*.parquet pequeños en uno solo.

    docker compose exec historial python -m app.compactar --mes 2026-10
    docker compose exec historial python -m app.compactar --mes 2026-10 --conjunto eventos_prueba --ejecutar

Sin --ejecutar solo muestra qué haría. Pasos:
1. Lista los archivos del mes y los lee (los que se publiquen después no se tocan).
2. Quita duplicados por event_id (se queda con la primera aparición).
3. Escribe part-compacto-<AAAAMMDDTHHMMSS>.parquet con el mismo procedimiento atómico (_tmp + rename).
4. Recién entonces borra los archivos que leyó.

Entre 3 y 4 hay un momento con filas repetidas en dos archivos: Spark las quita con
dropDuplicates(["event_id"]), así que el resultado de Q1–Q5 no cambia. Una consulta que ya listó
los archivos viejos y los lee después del paso 4 falla con "file not found": por eso es una tarea
de mantenimiento manual, fuera de las mediciones. Corre dentro del contenedor del historial porque
es el único con el lago en lectura/escritura.
"""
import argparse
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from app import lago


def compactar(mes: str, conjunto: str = "eventos", ejecutar: bool = False) -> dict:
    """Compacta los archivos de un mes en uno solo y devuelve un resumen.

    Con ejecutar=False solo calcula el resumen (simulación) sin escribir ni borrar nada.
    """
    carpeta = lago.LAGO / conjunto / f"mes={mes}"
    archivos = sorted(carpeta.glob("part-*.parquet"))
    if len(archivos) < 2:
        return {"mes": mes, "archivos": len(archivos), "accion": "nada que compactar"}

    # Lee todos los archivos del mes y los une en una sola tabla.
    t = pa.concat_tables([pq.read_table(a, schema=lago.ESQUEMA) for a in archivos])
    filas = t.num_rows
    # Primera aparición de cada event_id.
    indice = pa.table({"event_id": t["event_id"], "i": pa.array(range(filas))})
    primeros = indice.group_by("event_id", use_threads=False).aggregate([("i", "min")])["i_min"]
    t = t.take(pc.take(primeros, pc.sort_indices(primeros)))  # en el orden original
    # Nombre del archivo compacto con la fecha y hora UTC actual.
    nombre = f"part-compacto-{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}.parquet"
    resumen = {"mes": mes, "archivos": len(archivos), "filas": filas, "filas_sin_duplicados": t.num_rows,
               "duplicados_quitados": filas - t.num_rows, "destino": str(Path(conjunto) / f"mes={mes}" / nombre)}
    if not ejecutar:
        return {**resumen, "accion": "simulacion (usar --ejecutar)"}

    # Publica el archivo compacto y después borra los originales que se leyeron.
    lago.escribir_atomico(t, carpeta / nombre, f"compacto__{conjunto}__{mes}.parquet")
    for a in archivos:
        a.unlink()
    return {**resumen, "accion": "compactado"}


def main() -> None:
    """Lee los argumentos de la línea de comandos, compacta e imprime el resumen."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mes", required=True, help="AAAA-MM")
    ap.add_argument("--conjunto", default="eventos", choices=["eventos", "eventos_grande", "eventos_prueba"])
    ap.add_argument("--ejecutar", action="store_true")
    args = ap.parse_args()
    for clave, valor in compactar(args.mes, args.conjunto, args.ejecutar).items():
        print(f"{clave:<22} {valor}")


if __name__ == "__main__":
    main()
