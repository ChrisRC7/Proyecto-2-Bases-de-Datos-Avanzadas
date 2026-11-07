"""P4 — historial: frescura, fila rechazada, idempotencia, reprocesar y caída del consumidor (C5.1).

Corrida preliminar sin gateway: hace lo que hará el gateway (B3.3 publicar_evento): llama al
registro y, solo si respondió 200, reenvía el `evento` que devolvió a POST historial:/eventos.
Lo llama pruebas/p4_historial.sh fase por fase:

    docker compose --profile datos run --rm -T -v ./pruebas:/srv/pruebas -v ./datalake:/datalake:ro \
        dataset python /srv/pruebas/p4_historial.py <fase> --prefijo /srv/evidence/P4_...

Fases: frescura · rechazo · reprocesar · carga · caida-antes · caida-despues.
Lee MongoDB y los Parquet solo para verificar (no es parte de la plataforma).
"""
import argparse
import csv
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.dataset as ds
from pymongo import MongoClient

REGISTRO = os.getenv("REGISTRO_URL", "http://registro:8001")
HISTORIAL = os.getenv("HISTORIAL_URL", "http://historial:8003")
ANALITICA = os.getenv("ANALITICA_URL", "http://analitica:8004")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo1:27017,mongo2:27017,mongo3:27017/?replicaSet=rs0")
IDS = "/srv/dataset/salida/ids.json"
LAGO = Path("/datalake")
GEOMETRIA = {"tipo": "cuboide_3d", "centro": [1.2, 3.4, 0.5], "dimensiones": [0.6, 1.8, 1.7], "yaw": 0.3}


def http(base: str, metodo: str, ruta: str, cuerpo=None, timeout: float = 600):
    peticion = urllib.request.Request(base + ruta, method=metodo, headers={"Content-Type": "application/json"},
                                      data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as r:
            texto = r.read()
            return r.status, json.loads(texto) if texto else None
    except urllib.error.HTTPError as e:
        texto = e.read()
        return e.code, json.loads(texto) if texto else None


def publicar(evento: dict) -> dict:
    s, r = http(HISTORIAL, "POST", "/eventos", evento)
    assert s == 202, (s, r)
    return r


def actores():
    ids = json.load(open(IDS))
    anotador = next(a["id"] for a in ids["anotadores"] if a["rol"] == "anotador")
    revisor = next(a["id"] for a in ids["anotadores"] if a["rol"] == "revisor")
    return ids["proyecto_lidar_ejemplo"], anotador, revisor


def recorrido(final: str) -> dict:
    """asignar -> enviar -> empezar_revision -> aprobar|rechazar, publicando cada evento tras el 200."""
    proyecto, anotador, revisor = actores()
    token = str(uuid.uuid4())
    s, a = http(REGISTRO, "POST", "/tareas/asignar", {"anotador_id": anotador, "proyecto_id": proyecto, "token": token})
    assert s == 200, (s, a)
    tarea = a["tarea"]
    eventos = [a["evento"]]
    publicar(a["evento"])
    etiqueta = tarea["taxonomia"][0]["etiqueta"]
    pasos = [
        {"accion": "enviar", "token": token, "version": tarea["version"], "actor_id": anotador,
         "anotacion": {"etiquetas": [etiqueta], "geometria": GEOMETRIA}},
        {"accion": "empezar_revision", "actor_id": revisor},
        {"accion": "aprobar", "actor_id": revisor} if final == "aprobar"
        else {"accion": "rechazar", "actor_id": revisor, "motivo": "GEOMETRIA_IMPRECISA"},
    ]
    for paso in pasos:
        s, r = http(REGISTRO, "POST", f"/tareas/{tarea['tarea_id']}/transicion", paso)
        assert s == 200, (paso["accion"], s, r)
        t_200 = time.time()
        publicar(r["evento"])
        eventos.append(r["evento"])
        respuesta = r
    return {"tarea": tarea, "eventos": eventos, "t_200": t_200, "respuesta_final": respuesta}


def aprobadas_hoy() -> tuple[int, float]:
    """Cuenta de Q1 (principal) para hoy y lidar, y lo que tardó la consulta."""
    s, d = http(ANALITICA, "GET", "/q/1?conjunto=principal")
    assert s == 200, (s, d)
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fila = next((f for f in d["filas"] if f["dia"] == hoy and f["modalidad"] == "lidar"), None)
    return (fila["aprobadas"] if fila else 0), d["ms"]


def filas_lago(event_ids: list[str], conjunto: str = "eventos"):
    t = ds.dataset(LAGO / conjunto, format="parquet", partitioning="hive").to_table(
        filter=ds.field("event_id").isin(event_ids),
        columns=["event_id", "ts", "tarea_id", "estado", "version", "motivo_rechazo", "anotador_id",
                 "revisor_id", "micro_lote_id", "mes"])
    return t.to_pylist()


def frescura(args) -> None:
    filas = []
    for modo in ("espera_micro_lote", "con_flush"):
        for rep in range(1, args.repeticiones + 1):
            antes, _ = aprobadas_hoy()
            r = recorrido("aprobar")
            if modo == "con_flush":
                http(HISTORIAL, "POST", "/flush")
            consultas, ms_q1 = 0, []
            while True:
                t_consulta = time.time()
                ahora, ms = aprobadas_hoy()
                consultas += 1
                ms_q1.append(ms)
                if ahora >= antes + 1:
                    t_visible = time.time()
                    break
            fila = {"modo": modo, "rep": rep, "tarea_id": r["tarea"]["tarea_id"],
                    "event_id": r["respuesta_final"]["evento"]["event_id"], "t_200": round(r["t_200"], 3),
                    "t_inicio_consulta_que_lo_vio": round(t_consulta, 3), "t_visible": round(t_visible, 3),
                    "segundos_hasta_visible": round(t_visible - r["t_200"], 2),
                    "segundos_hasta_consulta_que_lo_vio": round(t_consulta - r["t_200"], 2),
                    "consultas_q1": consultas, "ms_q1_media": round(sum(ms_q1) / len(ms_q1), 1)}
            filas.append(fila)
            print(json.dumps(fila))
    with open(f"{args.prefijo}_frescura.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)
    for modo in ("espera_micro_lote", "con_flush"):
        seg = sorted(f["segundos_hasta_visible"] for f in filas if f["modo"] == modo)
        print(f"{modo}: n={len(seg)} min={seg[0]} mediana={seg[len(seg) // 2]} max={seg[-1]} s")


def rechazo(args) -> None:
    r = recorrido("rechazar")
    evento = r["respuesta_final"]["evento"]
    print("registro respondió:", json.dumps(r["respuesta_final"]["tarea"]))
    s, f = http(HISTORIAL, "POST", "/flush")
    print("flush:", s, json.dumps(f))
    print("\nParquet (todas las filas de la tarea):")
    for fila in sorted(filas_lago([e["event_id"] for e in r["eventos"]]), key=lambda x: x["version"]):
        print("  ", json.dumps(fila, default=str))
    tarea = MongoClient(MONGO_URI, tz_aware=True).plataforma.tareas.find_one(
        {"_id": r["tarea"]["tarea_id"]}, {"estado": 1, "version": 1, "asignacion": 1, "ultima_revision": 1,
                                          "ultimo_evento_id": 1})
    print("\nMongoDB (registro):", json.dumps(tarea, default=str))
    print("ultimo_evento_id == event_id de la fila rechazada:", tarea["ultimo_evento_id"] == evento["event_id"])

    print("\nIdempotencia: se vuelve a mandar el mismo evento rechazada")
    print("  POST /eventos:", json.dumps(publicar(evento)))
    http(HISTORIAL, "POST", "/flush")
    print("  filas en el lago con ese event_id:", len(filas_lago([evento["event_id"]])))

    s, d = http(ANALITICA, "GET", "/q/4?conjunto=principal")
    fila = next(x for x in d["filas"] if x["proyecto_id"] == r["tarea"]["proyecto_id"])
    print("\nQ4 (principal) para el proyecto LiDAR de ejemplo:", json.dumps(fila))
    Path(f"{args.prefijo}_rechazo.json").write_text(json.dumps({"evento": evento, "mongo": tarea, "q4": fila},
                                                               default=str, indent=1))


def sha(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()[:16]


def reprocesar(args) -> None:
    s, d = http(HISTORIAL, "GET", "/micro-lotes?ultimos=1")
    ml = d["micro_lotes"][0]
    archivos = [LAGO / a["archivo"] for a in ml["archivos"]]
    total_antes = len(list((LAGO / "eventos").rglob("*.parquet")))
    antes = {str(a): (sha(a), a.stat().st_mtime_ns) for a in archivos}
    ids = ds.dataset(archivos, format="parquet").to_table(columns=["event_id"])["event_id"].to_pylist()
    filas_antes = len(filas_lago(ids))
    time.sleep(1)
    s, r = http(HISTORIAL, "POST", f"/reprocesar/{ml['micro_lote_id']}")
    print("POST /reprocesar:", s, json.dumps(r))
    despues = {str(a): (sha(a), a.stat().st_mtime_ns) for a in archivos}
    print(f"micro-lote {ml['micro_lote_id']} ({ml['filas']} eventos)")
    for a in antes:
        print(f"  {a}: sha256 {antes[a][0]} -> {despues[a][0]} · mtime cambió: {antes[a][1] != despues[a][1]}")
    print(f"archivos .parquet en eventos/: {total_antes} -> {len(list((LAGO / 'eventos').rglob('*.parquet')))}")
    print(f"filas en el lago con esos event_id: {filas_antes} -> {len(filas_lago(ids))} (eventos del micro-lote: {len(ids)})")


def sinteticos(n: int) -> list[dict]:
    """Eventos 'abierta' válidos (Q1–Q5 no usan abierta, así que no cambian los KPIs)."""
    proyecto, anotador, _ = actores()
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    base = {"ts": ahora, "proyecto_id": proyecto, "cliente_id": "p4-sintetico", "batch_id": "p4-sintetico",
            "modalidad": "lidar", "tipo_tarea": "cuboide_3d", "estado": "abierta", "version": 1,
            "anotador_id": anotador, "equipo_id": None, "tomada_ts": ahora, "sla_horas": 48}
    return [{**base, "event_id": str(uuid.uuid4()), "tarea_id": str(uuid.uuid4())} for _ in range(n)]


def carga(args) -> None:
    s, d = http(HISTORIAL, "GET", "/micro-lotes?ultimos=500")
    previos = {m["micro_lote_id"] for m in d["micro_lotes"]}
    t0 = time.time()
    for i in range(0, args.eventos, 250):
        publicar(sinteticos(min(250, args.eventos - i)))
        time.sleep(args.pausa)
    print(f"{args.eventos} eventos enviados en {time.time() - t0:.1f} s")
    http(HISTORIAL, "POST", "/flush")
    s, d = http(HISTORIAL, "GET", "/micro-lotes?ultimos=500")
    nuevos = [m for m in d["micro_lotes"] if m["micro_lote_id"] not in previos]
    print(f"micro-lotes publicados: {len(nuevos)} · filas: {sum(m['filas'] for m in nuevos)} · "
          f"archivos: {sum(len(m['archivos']) for m in nuevos)}")


def caida_antes(args) -> None:
    eventos = sinteticos(args.eventos)
    publicar(eventos)
    Path(f"{args.prefijo}_caida_ids.txt").write_text("\n".join(e["event_id"] for e in eventos))
    print(f"{len(eventos)} eventos aceptados (202) a las {time.time():.3f}; menos de un micro-lote, "
          "quedan en memoria del consumidor y pendientes sin ACK en el stream")


def caida_despues(args) -> None:
    ids = Path(f"{args.prefijo}_caida_ids.txt").read_text().split()
    s, f = http(HISTORIAL, "POST", "/flush")
    filas = filas_lago(ids)
    por_id = {}
    for fila in filas:
        por_id[fila["event_id"]] = por_id.get(fila["event_id"], 0) + 1
    print(f"eventos aceptados antes de la caída: {len(ids)}")
    print(f"en el lago después de reiniciar: {len(por_id)} event_id distintos, {len(filas)} filas")
    print(f"perdidos: {len(set(ids) - set(por_id))} · repetidos: {sum(1 for v in por_id.values() if v > 1)}")
    print(f"micro-lotes que los contienen: {sorted({x['micro_lote_id'] for x in filas})}")
    print(f"_tmp/ después de reiniciar: {sorted(p.name for p in (LAGO / '_tmp').glob('*'))}")
    s, salud = http(HISTORIAL, "GET", "/salud")
    print("historial /salud:", json.dumps(salud["cola"]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fase", choices=["frescura", "rechazo", "reprocesar", "carga", "caida-antes", "caida-despues"])
    ap.add_argument("--prefijo", required=True)
    ap.add_argument("--repeticiones", type=int, default=5)
    ap.add_argument("--eventos", type=int, default=300)
    ap.add_argument("--pausa", type=float, default=0.05)
    args = ap.parse_args()
    {"frescura": frescura, "rechazo": rechazo, "reprocesar": reprocesar, "carga": carga,
     "caida-antes": caida_antes, "caida-despues": caida_despues}[args.fase](args)


if __name__ == "__main__":
    main()
