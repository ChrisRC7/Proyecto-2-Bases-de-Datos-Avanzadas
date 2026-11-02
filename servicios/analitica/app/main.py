"""Servicio analitica — Q1–Q5 con Spark sobre el lago Parquet (endpoints/analitica.md).

Rutas: GET /q/{n}?conjunto= · GET /plan/{n}?conjunto= · GET /salud.

Una sola SparkSession por proceso (D5), conectada al master standalone; se crea al arrancar en un
hilo aparte para que /salud responda aunque Spark tarde o esté caído. No se cachea ningún
DataFrame: la diferencia entre la primera ejecución y las siguientes es el costo de arranque
(JVM, ejecutores, listado de archivos, generación de código), no datos guardados en memoria.
Las consultas se atienden de a una (candado) para que los tiempos medidos sean comparables.
"""
import json
import os
import socket
import threading
import time
import urllib.request
from typing import Optional

from fastapi import Path as RutaParam, Query

from app.comun import SERVICIO, ahora_iso, crear_app, error, log
from app.consultas import CONSULTAS, ConjuntoVacio, a_json, explicar, leer

SPARK_MASTER = os.getenv("SPARK_MASTER", "spark://spark-master:7077")
SESION_URL = os.getenv("SESION_URL", "http://sesion:8002")
LATIDO_SEGUNDOS = 5
CONJUNTO = Query("principal", pattern="^(principal|grande|prueba)$")
NUMERO = RutaParam(..., ge=1, le=5)

app = crear_app(SERVICIO)


class Motor:
    def __init__(self):
        self.spark = None
        self.lista = threading.Event()
        self.candado = threading.Lock()
        self.ejecutadas = set()  # (n, conjunto) que ya corrieron en este proceso
        self.arranque_ms: Optional[float] = None
        self.ultimo_error: Optional[str] = None

    def crear(self) -> None:
        from pyspark.sql import SparkSession
        while self.spark is None:
            t0 = time.perf_counter()
            try:
                self.spark = (SparkSession.builder
                              .master(SPARK_MASTER)
                              .appName("analitica")
                              .config("spark.driver.host", socket.gethostname())
                              .config("spark.driver.bindAddress", "0.0.0.0")
                              .config("spark.driver.memory", os.getenv("SPARK_DRIVER_MEMORY", "1g"))
                              .config("spark.executor.memory", os.getenv("SPARK_EXECUTOR_MEMORY", "1536m"))
                              .config("spark.sql.session.timeZone", "UTC")
                              .config("spark.sql.shuffle.partitions", os.getenv("SPARK_SHUFFLE_PARTICIONES", "16"))
                              # "mes" queda como texto (2026-01), no se intenta convertir a fecha.
                              .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
                              # max(ts) de Q1 sale de las estadísticas de los footers, sin leer filas.
                              .config("spark.sql.parquet.aggregatePushdown", "true")
                              .getOrCreate())
                self.spark.sparkContext.setLogLevel("WARN")
                self.arranque_ms = round((time.perf_counter() - t0) * 1000, 1)
                self.ultimo_error = None
                self.lista.set()
                log.info("sparksession lista", extra={"datos": {"ms": self.arranque_ms, "master": SPARK_MASTER}})
            except Exception as exc:
                self.ultimo_error = str(exc).splitlines()[0]
                log.warning("spark no disponible", extra={"datos": {"error": self.ultimo_error}})
                time.sleep(5)

    def ejecutores(self) -> int:
        """Ejecutores registrados (sin contar el driver)."""
        return self.spark.sparkContext._jsc.sc().getExecutorMemoryStatus().size() - 1


motor = Motor()


def master_alcanzable() -> bool:
    host, puerto = SPARK_MASTER.replace("spark://", "").split(":")
    try:
        socket.create_connection((host, int(puerto)), timeout=1).close()
        return True
    except OSError:
        return False


def _preparar(n: int, conjunto: str):
    if not motor.lista.wait(timeout=60):
        return None, error(503, "spark_no_disponible", motor.ultimo_error)
    try:
        df = leer(motor.spark, conjunto)
    except ConjuntoVacio as exc:
        return None, error(404, "conjunto_no_existe", str(exc))
    return df, None


@app.get("/q/{n}")
def consulta(n: int = NUMERO, conjunto: str = CONJUNTO):
    df, problema = _preparar(n, conjunto)
    if problema:
        return problema
    with motor.candado:
        primera = (n, conjunto) not in motor.ejecutadas
        t0 = time.perf_counter()
        try:
            res, parametros = CONSULTAS[n](motor.spark, df)
            filas = a_json(res.collect())
        except Exception as exc:  # Py4J / ejecutores caídos
            log.warning("consulta fallida", extra={"datos": {"consulta": n, "error": str(exc)[:500]}})
            return error(503, "spark_error", str(exc).splitlines()[0][:500])
        ms = round((time.perf_counter() - t0) * 1000, 1)
        motor.ejecutadas.add((n, conjunto))
    log.info("consulta", extra={"datos": {"consulta": f"q{n}", "conjunto": conjunto, "ms": ms,
                                          "filas": len(filas), "primera_ejecucion": primera}})
    return {"consulta": f"q{n}", "conjunto": conjunto, "parametros": parametros, "filas": filas,
            "ms": ms, "primera_ejecucion": primera, "ejecutores": motor.ejecutores()}


@app.get("/plan/{n}")
def plan(n: int = NUMERO, conjunto: str = CONJUNTO):
    df, problema = _preparar(n, conjunto)
    if problema:
        return problema
    with motor.candado:
        res, parametros = CONSULTAS[n](motor.spark, df)
        texto = explicar(res)
    return {"consulta": f"q{n}", "conjunto": conjunto, "parametros": parametros, "plan": texto}


def estado_capa() -> dict:
    master = master_alcanzable()
    detalle = {"master": SPARK_MASTER, "master_alcanzable": master, "sesion_spark": motor.lista.is_set(),
               "arranque_ms": motor.arranque_ms, "error": motor.ultimo_error}
    if motor.lista.is_set():
        try:
            detalle["ejecutores"] = motor.ejecutores()
            detalle["nucleos"] = motor.spark.sparkContext.defaultParallelism
        except Exception as exc:
            detalle["error"] = str(exc).splitlines()[0]
    sano = master and motor.lista.is_set() and detalle.get("ejecutores", 0) > 0
    return {"estado": "operativo" if sano else "caido", "spark": detalle}


@app.get("/salud")
def salud():
    return {"servicio": SERVICIO, **estado_capa(), "ts": ahora_iso()}


def _latido():
    """Cada 5 s publica su estado en sesion:/estado-capas (D4)."""
    while True:
        capa = estado_capa()
        cuerpo = json.dumps({"servicio": SERVICIO, "estado": capa["estado"], "detalle": capa["spark"]})
        peticion = urllib.request.Request(f"{SESION_URL}/estado-capas", data=cuerpo.encode(),
                                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(peticion, timeout=1).close()
        except Exception as exc:  # sesion caída o todavía sin la ruta: se reintenta en 5 s
            log.debug("latido sin respuesta", extra={"datos": {"error": str(exc)}})
        time.sleep(LATIDO_SEGUNDOS)


@app.on_event("startup")
def iniciar():
    threading.Thread(target=motor.crear, name="spark", daemon=True).start()
    threading.Thread(target=_latido, name="latido", daemon=True).start()
