"""P4 — Spark leyendo el lago en bucle mientras el historial escribe (C5.1, sin lecturas a medias).

Corre en la imagen de la analítica (Spark 3.5.1, lago montado en solo lectura) con Spark local,
para no competir por los núcleos del cluster con la SparkSession de la analítica:

    docker compose run --rm --no-deps -T -v ./pruebas:/srv/pruebas analitica \
        python3 /srv/pruebas/p4_lector.py --segundos 120 > evidence/P4_..._lector.csv

Cada vuelta lista de nuevo datalake/eventos y cuenta filas, event_id distintos y archivos. Si Spark
llegara a abrir un Parquet a medio escribir, la vuelta fallaría ("not a Parquet file", footer
inválido...) y el error queda en la columna error. Salida: CSV por stdout.
"""
import argparse
import sys
import time

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--segundos", type=float, default=120)
    ap.add_argument("--ruta", default="file:///datalake/eventos")
    args = ap.parse_args()

    spark = (SparkSession.builder.master("local[2]").appName("p4-lector")
             .config("spark.ui.enabled", "false")
             .config("spark.sql.session.timeZone", "UTC")
             .config("spark.sql.shuffle.partitions", "4")
             .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
             .getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    print("ts,vuelta,filas,event_id_distintos,archivos,ms,error", flush=True)
    fin, vuelta = time.time() + args.segundos, 0
    while time.time() < fin:
        vuelta += 1
        t0 = time.time()
        try:
            df = spark.read.parquet(args.ruta)
            r = df.agg(F.count("*"), F.countDistinct("event_id")).first()
            fila, problema = (r[0], r[1], len(df.inputFiles())), ""
        except Exception as exc:  # una lectura a medias terminaría aquí
            fila, problema = ("", "", ""), str(exc).splitlines()[0].replace(",", ";")[:300]
        print(f"{t0:.3f},{vuelta},{fila[0]},{fila[1]},{fila[2]},{(time.time() - t0) * 1000:.0f},{problema}",
              flush=True)
    spark.stop()


if __name__ == "__main__":
    sys.exit(main())
