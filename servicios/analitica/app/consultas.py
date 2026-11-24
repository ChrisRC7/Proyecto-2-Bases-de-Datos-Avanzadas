"""Q1–Q5 sobre el lago Parquet (endpoints/analitica.md). Solo lee archivos; nada de MongoDB ni historial.

Base común: leer el conjunto, filtrar los estados que usa la consulta y dropDuplicates(["event_id"]).
Se filtra antes de deduplicar porque las copias de un event_id son la misma fila (misma transición,
mismo estado y mismo ts; a lo sumo cambia micro_lote_id), así que el resultado es igual y el
shuffle de la deduplicación mueve solo las filas que la consulta necesita. Las filas liberada nunca
entran (D11): cada consulta pide estados explícitos.
"""
import os
from datetime import timedelta
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

# Raíz del lago (montada solo lectura) y carpeta de cada conjunto de datos que se puede consultar.
LAGO = Path(os.getenv("LAGO_DIR", "/datalake"))
CONJUNTOS = {"principal": "eventos", "grande": "eventos_grande", "prueba": "eventos_prueba"}


class ConjuntoVacio(Exception):
    """El conjunto pedido no tiene archivos Parquet (la API responde 404)."""
    pass


def leer(spark: SparkSession, conjunto: str) -> DataFrame:
    """Devuelve el DataFrame de todo el conjunto (lectura perezosa: Spark aún no lee filas)."""
    ruta = LAGO / CONJUNTOS[conjunto]
    if not any(ruta.glob("mes=*/*.parquet")):
        raise ConjuntoVacio(f"{ruta} no tiene archivos Parquet")
    # La columna "mes" sale del nombre de la carpeta (mes=AAAA-MM): permite partition pruning.
    return spark.read.option("basePath", f"file://{ruta}").parquet(f"file://{ruta}")


def _base(df: DataFrame, *estados: str) -> DataFrame:
    """Filtra las filas con alguno de los estados dados y quita duplicados por event_id."""
    return df.where(F.col("estado").isin(*estados)).dropDuplicates(["event_id"])


def _segundos(col_fin: str, col_inicio: str):
    """Expresión Spark: segundos entre dos columnas de fecha (fin - inicio)."""
    return F.col(col_fin).cast("double") - F.col(col_inicio).cast("double")


def q1(spark: SparkSession, df: DataFrame):
    """Aprobadas por día y modalidad en los últimos 90 días respecto de max(ts) del conjunto (D6)."""
    # La ventana se calcula desde el evento más reciente del conjunto, no desde "hoy".
    hasta = df.agg(F.max("ts")).first()[0]
    desde = hasta - timedelta(days=90)
    # Primero por partición (solo los meses de la ventana) y luego por ts exacto.
    ventana = df.where(F.col("mes") >= desde.strftime("%Y-%m")).where(F.col("ts") >= F.lit(desde))
    res = (_base(ventana, "aprobada")
           .groupBy(F.to_date("ts").alias("dia"), "modalidad")
           .agg(F.count("*").alias("aprobadas"))
           .orderBy("dia", "modalidad"))
    return res, {"desde": desde.strftime("%Y-%m-%dT%H:%M:%SZ"), "hasta": hasta.strftime("%Y-%m-%dT%H:%M:%SZ")}


def q2(spark: SparkSession, df: DataFrame):
    """Aprobadas / revisadas (aprobada + rechazada) por equipo del anotador y mes."""
    # 1 si la fila es aprobada, 0 si es rechazada: su suma cuenta las aprobadas.
    aprobada = F.when(F.col("estado") == "aprobada", 1).otherwise(0)
    res = (_base(df, "aprobada", "rechazada")
           .groupBy("equipo_id", "mes")
           .agg(F.sum(aprobada).alias("aprobadas"), F.count("*").alias("revisadas"))
           .withColumn("proporcion", F.round(F.col("aprobadas") / F.col("revisadas"), 4))
           .orderBy("equipo_id", "mes"))
    return res, {}


def q3(spark: SparkSession, df: DataFrame):
    """Duración (anotada - toma vigente) media y p95 por modalidad y tipo, ordenado por media desc (D7)."""
    res = (_base(df, "anotada")
           .where(F.col("tomada_ts").isNotNull())
           .withColumn("duracion_s", _segundos("ts", "tomada_ts"))
           .groupBy("modalidad", "tipo_tarea")
           .agg(F.count("*").alias("anotadas"),
                F.round(F.avg("duracion_s"), 2).alias("media_s"),
                F.percentile_approx("duracion_s", 0.95).alias("p95_s"))
           .orderBy(F.col("media_s").desc()))
    return res, {}


def q4(spark: SparkSession, df: DataFrame):
    """Rechazadas / revisadas por cliente y proyecto (cuenta filas rechazada del lago)."""
    # 1 si la fila es rechazada: su suma cuenta los rechazos.
    rechazada = F.when(F.col("estado") == "rechazada", 1).otherwise(0)
    res = (_base(df, "aprobada", "rechazada")
           .groupBy("cliente_id", "proyecto_id")
           .agg(F.sum(rechazada).alias("rechazadas"), F.count("*").alias("revisadas"))
           .withColumn("proporcion", F.round(F.col("rechazadas") / F.col("revisadas"), 4))
           .orderBy(F.col("proporcion").desc(), "cliente_id", "proyecto_id"))
    return res, {}


def q5(spark: SparkSession, df: DataFrame):
    """Horas toma -> aprobación: p50 y p95 por cliente y % que superó sla_horas (D7)."""
    horas = (_base(df, "aprobada")
             .where(F.col("tomada_ts").isNotNull())
             .withColumn("horas", _segundos("ts", "tomada_ts") / 3600))
    # 1 si la tarea tardó más que su SLA.
    incumple = F.when(F.col("horas") > F.col("sla_horas"), 1).otherwise(0)
    res = (horas.groupBy("cliente_id")
           .agg(F.count("*").alias("aprobadas"),
                F.percentile_approx("horas", 0.5).alias("p50_h"),
                F.percentile_approx("horas", 0.95).alias("p95_h"),
                F.sum(incumple).alias("incumplen_sla"))
           .withColumn("pct_incumple_sla", F.round(100 * F.col("incumplen_sla") / F.col("aprobadas"), 2))
           .orderBy("cliente_id"))
    return res, {}


# Número de consulta -> función que la arma.
CONSULTAS = {1: q1, 2: q2, 3: q3, 4: q4, 5: q5}


def explicar(df: DataFrame) -> str:
    """Lo mismo que df.explain("formatted"), pero como texto."""
    return df.sparkSession._jvm.PythonSQLUtils.explainString(df._jdf.queryExecution(), "formatted")


def a_json(filas) -> list:
    """Convierte las filas de Spark a dicts JSON: fechas a ISO y floats redondeados a 4 decimales."""
    salida = []
    for fila in filas:
        d = fila.asDict()
        for k, v in d.items():
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat()
            elif isinstance(v, float):
                d[k] = round(v, 4)
        salida.append(d)
    return salida
