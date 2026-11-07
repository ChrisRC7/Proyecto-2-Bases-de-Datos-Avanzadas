"""Cola durable de eventos (D3): Valkey Stream en la instancia propia cola-eventos (AOF everysec).

Llaves (solo de esta instancia, ningún otro servicio la ve):
  eventos:tareas         stream; cada entrada tiene el campo "evento" (JSON de la fila)
  visto:{event_id}       marca de idempotencia de POST /eventos (TTL VISTO_TTL_S)
  microlote:{id}         hash: primero, ultimo, filas, estado (escribiendo|publicado), archivos, ts
  microlotes             sorted set de los micro-lotes publicados (score = epoch ms)
  microlotes:abiertos    set de micro-lotes que empezaron a escribirse y no confirmaron (recuperación)
"""
import json
import os
import socket

import valkey

STREAM = "eventos:tareas"
GRUPO = "historial"
CONSUMIDOR = os.getenv("CONSUMIDOR", socket.gethostname())
VISTO_TTL_S = int(os.getenv("VISTO_TTL_S", str(7 * 24 * 3600)))
# Micro-lotes que se pueden reprocesar: el stream se recorta hasta el primero de ellos.
RETENER_MICRO_LOTES = int(os.getenv("RETENER_MICRO_LOTES", "2000"))

cola = valkey.Valkey(
    host=os.getenv("COLA_HOST", "cola-eventos"),
    port=int(os.getenv("COLA_PUERTO", "6379")),
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=5,  # mayor que el BLOCK de XREADGROUP
    health_check_interval=10,
)

# SET NX y XADD en un solo paso atómico: un event_id repetido no entra dos veces a la cola.
_encolar = cola.register_script("""
if redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[2]) then
  return redis.call('XADD', KEYS[2], '*', 'evento', ARGV[1])
end
return false
""")


def encolar(eventos: list[dict]) -> tuple[int, int]:
    """Devuelve (aceptados, duplicados)."""
    tubo = cola.pipeline(transaction=False)
    for e in eventos:
        _encolar(keys=[f"visto:{e['event_id']}", STREAM], args=[json.dumps(e), VISTO_TTL_S], client=tubo)
    resultados = tubo.execute()
    aceptados = sum(1 for r in resultados if r)
    return aceptados, len(eventos) - aceptados


def crear_grupo() -> None:
    try:
        cola.xgroup_create(STREAM, GRUPO, id="0", mkstream=True)
    except valkey.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def reclamar_pendientes() -> int:
    """XAUTOCLAIM de todo lo pendiente del grupo (de este consumidor o de uno anterior con otro nombre)."""
    total, inicio = 0, "0-0"
    while True:
        siguiente, reclamadas, *_ = cola.xautoclaim(STREAM, GRUPO, CONSUMIDOR, min_idle_time=0,
                                                    start_id=inicio, count=500)
        total += len(reclamadas)
        if siguiente == "0-0":
            return total
        inicio = siguiente


def leer(desde: str, cantidad: int, bloquear_ms: int | None) -> list[tuple[str, dict]]:
    """XREADGROUP: desde=">" nuevos, desde="0" los pendientes propios."""
    resp = cola.xreadgroup(GRUPO, CONSUMIDOR, {STREAM: desde}, count=cantidad, block=bloquear_ms)
    return [(i, json.loads(campos["evento"])) for _, entradas in (resp or []) for i, campos in entradas
            if campos]  # una entrada pendiente ya recortada vuelve sin campos


def rango(primero: str, ultimo: str) -> list[tuple[str, dict]]:
    return [(i, json.loads(c["evento"])) for i, c in cola.xrange(STREAM, primero, ultimo)]


def abrir_micro_lote(mlid: str, primero: str, ultimo: str, filas: int) -> None:
    tubo = cola.pipeline(transaction=True)
    tubo.hset(f"microlote:{mlid}", mapping={"primero": primero, "ultimo": ultimo, "filas": filas,
                                            "estado": "escribiendo"})
    tubo.sadd("microlotes:abiertos", mlid)
    tubo.execute()


def confirmar_micro_lote(mlid: str, ids: list[str], archivos: list[dict], ts_ms: int) -> None:
    """Después del rename: marca publicado y hace XACK, todo en una transacción."""
    tubo = cola.pipeline(transaction=True)
    tubo.hset(f"microlote:{mlid}", mapping={"estado": "publicado", "archivos": json.dumps(archivos),
                                            "publicado_en": ts_ms})
    tubo.zadd("microlotes", {mlid: ts_ms})
    tubo.srem("microlotes:abiertos", mlid)
    tubo.xack(STREAM, GRUPO, *ids)
    tubo.execute()


def recortar() -> None:
    """Deja en el stream solo lo necesario para reprocesar los últimos RETENER_MICRO_LOTES."""
    viejos = cola.zrange("microlotes", 0, -RETENER_MICRO_LOTES - 1)
    if not viejos:
        return
    limite = cola.zrange("microlotes", -RETENER_MICRO_LOTES, -RETENER_MICRO_LOTES)
    primero = cola.hget(f"microlote:{limite[0]}", "primero") if limite else None
    tubo = cola.pipeline(transaction=True)
    if primero:
        tubo.xtrim(STREAM, minid=primero)
    tubo.zrem("microlotes", *viejos)
    tubo.delete(*[f"microlote:{m}" for m in viejos])
    tubo.execute()


def micro_lote(mlid: str) -> dict | None:
    datos = cola.hgetall(f"microlote:{mlid}")
    if not datos:
        return None
    datos["filas"] = int(datos["filas"])
    datos["archivos"] = json.loads(datos.get("archivos", "[]"))
    for campo in ("publicado_en", "reprocesado_en", "reprocesos"):
        if campo in datos:
            datos[campo] = int(datos[campo])
    return {"micro_lote_id": mlid, **datos}


def ultimos_micro_lotes(n: int) -> list[dict]:
    return [m for m in (micro_lote(i) for i in cola.zrevrange("microlotes", 0, n - 1)) if m]


def estado() -> dict:
    """Para /salud: longitud del stream, pendientes sin ACK y micro-lotes a medio escribir."""
    cola.ping()
    try:
        pendientes = cola.xpending(STREAM, GRUPO)["pending"]
    except valkey.ResponseError:  # el grupo todavía no existe
        pendientes = 0
    return {"estado": "operativo", "eventos_en_stream": cola.xlen(STREAM), "pendientes_sin_ack": pendientes,
            "micro_lotes_abiertos": cola.scard("microlotes:abiertos")}
