"""Servicio historial — único que escribe el lago Parquet (endpoints/historial.md).

Rutas: POST /eventos · POST /flush · POST /reprocesar/{micro_lote_id} · GET /micro-lotes · GET /salud.

POST /eventos solo encola en cola-eventos (XADD) y responde 202; un hilo consumidor junta
micro-lotes (MICRO_LOTE_EVENTOS eventos o MICRO_LOTE_SEGUNDOS segundos) y los publica en el lago.
"""
import json
import os
import threading
import time
import urllib.request
from typing import Annotated

import valkey
from fastapi import Body, Query

from app import cola, lago
from app.comun import SERVICIO, ahora_iso, crear_app, error, log
from app.esquemas import Evento

TAM_LOTE = int(os.getenv("MICRO_LOTE_EVENTOS", "500"))
SEG_LOTE = float(os.getenv("MICRO_LOTE_SEGUNDOS", "5"))
SESION_URL = os.getenv("SESION_URL", "http://sesion:8002")
LATIDO_SEGUNDOS = 5

app = crear_app(SERVICIO)


@app.exception_handler(valkey.ValkeyError)
async def cola_caida(_request, exc: valkey.ValkeyError):
    # Sin cola no se acepta nada: el gateway reintenta (POST /eventos es idempotente por event_id).
    return error(503, "cola_no_disponible", str(exc))


class Consumidor(threading.Thread):
    """XREADGROUP -> micro-lote -> Parquet en _tmp/ -> rename -> XACK."""

    def __init__(self):
        super().__init__(name="consumidor", daemon=True)
        self.escritura = threading.Lock()  # el consumidor y /reprocesar no escriben a la vez
        self.pedir_flush = threading.Event()
        self.flush_listo = threading.Event()
        self.flush_resultado: list[dict] = []
        self.vivo = False
        self.ultimo_error: str | None = None

    def publicar(self, entradas: list[tuple[str, dict]]) -> dict:
        ids = [i for i, _ in entradas]
        # Determinista: el mismo rango del stream da el mismo nombre de archivo.
        mlid = f"{ids[0]}_{ids[-1]}"
        cola.abrir_micro_lote(mlid, ids[0], ids[-1], len(ids))
        with self.escritura:
            archivos = lago.publicar(mlid, [e for _, e in entradas])
        ts_ms = int(time.time() * 1000)
        cola.confirmar_micro_lote(mlid, ids, archivos, ts_ms)
        cola.recortar()
        log.info("micro-lote publicado", extra={"datos": {"micro_lote_id": mlid, "filas": len(ids),
                                                          "archivos": [a["archivo"] for a in archivos]}})
        return {"micro_lote_id": mlid, "filas": len(ids), "archivos": archivos}

    def recuperar(self) -> None:
        """Al arrancar (o al volver la cola): _tmp/ huérfanos, pendientes y micro-lotes a medias."""
        huerfanos = lago.limpiar_tmp()
        cola.crear_grupo()
        reclamados = cola.reclamar_pendientes()
        # Un micro-lote que abrió y no confirmó se rehace con el mismo rango (mismo nombre de archivo).
        rehechos = []
        for mlid in cola.cola.smembers("microlotes:abiertos"):
            datos = cola.micro_lote(mlid)
            entradas = cola.rango(datos["primero"], datos["ultimo"]) if datos else []
            if entradas:
                rehechos.append(self.publicar(entradas)["micro_lote_id"])
            else:
                cola.cola.srem("microlotes:abiertos", mlid)
        # El resto de pendientes propios (leídos y sin ACK) se publica en micro-lotes normales.
        pendientes = 0
        while entradas := cola.leer("0", TAM_LOTE, None):
            self.publicar(entradas)
            pendientes += len(entradas)
        log.info("recuperacion", extra={"datos": {"tmp_huerfanos": huerfanos, "reclamados": reclamados,
                                                  "micro_lotes_rehechos": rehechos,
                                                  "pendientes_publicados": pendientes}})

    def bucle(self) -> None:
        lote: list[tuple[str, dict]] = []
        inicio = 0.0
        while True:
            flush = self.pedir_flush.is_set()
            if flush:
                espera = None  # sin BLOCK: toma lo que ya está en el stream y cierra
            elif lote:
                espera = max(1, int((inicio + SEG_LOTE - time.monotonic()) * 1000))
            else:
                espera = 250
            nuevas = cola.leer(">", TAM_LOTE - len(lote), min(espera, 250) if espera else None)
            if nuevas and not lote:
                inicio = time.monotonic()
            lote += nuevas
            lleno = len(lote) >= TAM_LOTE
            vencido = lote and time.monotonic() - inicio >= SEG_LOTE
            if lote and (lleno or vencido or (flush and not nuevas)):
                resultado = self.publicar(lote)
                lote = []
                if flush:
                    self.flush_resultado.append(resultado)
                    continue  # puede quedar más en el stream
            if flush and not nuevas and not lote:
                self.pedir_flush.clear()
                self.flush_listo.set()

    def run(self) -> None:
        while True:
            try:
                self.recuperar()
                self.vivo, self.ultimo_error = True, None
                self.bucle()
            except Exception as exc:  # cola caída: se reintenta; lo leído sin ACK queda pendiente
                self.vivo, self.ultimo_error = False, str(exc)
                log.warning("consumidor detenido", extra={"datos": {"error": str(exc)}})
                time.sleep(1)

    def flush(self, timeout: float = 30) -> list[dict] | None:
        self.flush_resultado = []
        self.flush_listo.clear()
        self.pedir_flush.set()
        return self.flush_resultado if self.flush_listo.wait(timeout) else None


consumidor = Consumidor()


@app.post("/eventos", status_code=202)
def recibir_eventos(cuerpo: Annotated[Evento | list[Evento], Body()]):
    eventos = cuerpo if isinstance(cuerpo, list) else [cuerpo]
    if not eventos:
        return error(422, "validacion", "lista vacía")
    aceptados, duplicados = cola.encolar([e.fila() for e in eventos])
    return {"aceptados": aceptados, "duplicados": duplicados}


@app.post("/flush")
def flush():
    if not consumidor.vivo:
        return error(503, "consumidor_detenido", consumidor.ultimo_error)
    resultado = consumidor.flush()
    if resultado is None:
        return error(503, "flush_sin_respuesta", "el consumidor no cerró el micro-lote en 30 s")
    return {"micro_lotes": resultado}


@app.post("/reprocesar/{micro_lote_id}")
def reprocesar(micro_lote_id: str):
    """Vuelve a escribir el micro-lote desde el stream: mismo nombre de archivo, sin duplicar filas."""
    datos = cola.micro_lote(micro_lote_id)
    if datos is None:
        return error(404, "micro_lote_no_existe", micro_lote_id)
    entradas = cola.rango(datos["primero"], datos["ultimo"])
    if len(entradas) != datos["filas"]:
        return error(409, "micro_lote_recortado",
                     {"filas_esperadas": datos["filas"], "filas_en_stream": len(entradas)})
    with consumidor.escritura:
        archivos = lago.publicar(micro_lote_id, [e for _, e in entradas])
    ts_ms = int(time.time() * 1000)
    cola.cola.hset(f"microlote:{micro_lote_id}", "reprocesado_en", ts_ms)
    cola.cola.hincrby(f"microlote:{micro_lote_id}", "reprocesos", 1)
    log.info("micro-lote reprocesado", extra={"datos": {"micro_lote_id": micro_lote_id}})
    return {"micro_lote_id": micro_lote_id, "filas": len(entradas), "archivos": archivos,
            "reprocesado_en": ts_ms}


@app.get("/micro-lotes")
def micro_lotes(ultimos: int = Query(10, ge=1, le=500)):
    return {"micro_lotes": cola.ultimos_micro_lotes(ultimos)}


def estado_capa() -> dict:
    try:
        estado_cola = cola.estado()
    except valkey.ValkeyError as exc:
        estado_cola = {"estado": "caido", "error": str(exc)}
    escribible = lago.escribible()
    sano = estado_cola["estado"] == "operativo" and escribible
    return {
        "estado": "operativo" if sano and consumidor.vivo else ("degradado" if sano else "caido"),
        "cola": estado_cola,
        "lago": {"escribible": escribible},
        "consumidor": {"vivo": consumidor.vivo, "error": consumidor.ultimo_error},
    }


@app.get("/salud")
def salud():
    capa = estado_capa()
    try:
        ultimo = cola.ultimos_micro_lotes(1)
    except valkey.ValkeyError:
        ultimo = []
    return {"servicio": SERVICIO, **capa, "ultimo_micro_lote": ultimo[0] if ultimo else None, "ts": ahora_iso()}


def _latido():
    """Cada 5 s publica su estado en sesion:/estado-capas (D4). Nunca escribe en el Valkey de sesión."""
    while True:
        capa = estado_capa()
        cuerpo = json.dumps({"servicio": SERVICIO, "estado": capa["estado"], "detalle": capa})
        peticion = urllib.request.Request(f"{SESION_URL}/estado-capas", data=cuerpo.encode(),
                                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(peticion, timeout=1).close()
        except Exception as exc:  # sesion caída o todavía sin la ruta: se reintenta en 5 s
            log.debug("latido sin respuesta", extra={"datos": {"error": str(exc)}})
        time.sleep(LATIDO_SEGUNDOS)


@app.on_event("startup")
def iniciar():
    consumidor.start()
    threading.Thread(target=_latido, name="latido", daemon=True).start()
