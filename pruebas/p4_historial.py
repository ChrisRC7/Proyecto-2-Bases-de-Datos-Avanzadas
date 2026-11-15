"""P4 — historial: frescura, fila rechazada, idempotencia, reprocesar y caída del consumidor (C5.1).

Dos modos:
- Directo (pruebas/p4_historial.sh, corrida preliminar sin gateway): hace lo que hace el gateway
  (B3.3 publicar_evento): llama al registro y, solo si respondió 200, reenvía el `evento` que
  devolvió a POST historial:/eventos.
- --gateway (pruebas/p4_gateway.sh, flujo real): el recorrido va por el gateway (siguiente, enviar,
  revisión) y es el gateway quien publica los eventos; Q1 y Q4 se piden a GET gateway:/analitica.
Fase por fase:

    docker compose --profile datos run --rm -T -v ./pruebas:/srv/pruebas -v ./datalake:/datalake:ro \
        dataset python /srv/pruebas/p4_historial.py <fase> --prefijo /srv/evidence/P4_...

Fases: frescura · rechazo · reprocesar · carga · caida-antes · caida-despues (las dos primeras aceptan --gateway).
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
GATEWAY = os.getenv("GATEWAY_URL", "http://gateway:8000")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo1:27017,mongo2:27017,mongo3:27017/?replicaSet=rs0")
IDS = "/srv/dataset/salida/ids.json"
LAGO = Path("/datalake")
GEOMETRIA = {"tipo": "cuboide_3d", "centro": [1.2, 3.4, 0.5], "dimensiones": [0.6, 1.8, 1.7], "yaw": 0.3}


def http(base: str, metodo: str, ruta: str, cuerpo=None, timeout: float = 600, anotador: str | None = None):
    cabeceras = {"Content-Type": "application/json"}
    if anotador:
        cabeceras["X-Anotador-Id"] = anotador
    peticion = urllib.request.Request(base + ruta, method=metodo, headers=cabeceras,
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


def recorrido_gateway(final: str) -> dict:
    """Lo mismo por el gateway: siguiente -> enviar -> revisión empezar -> aprobar|rechazar.

    El gateway guarda cada evento en su cola local (SQLite) y lo manda al historial en segundo plano.
    """
    proyecto, anotador, revisor = actores()
    s, a = http(GATEWAY, "POST", "/tareas/siguiente", {"proyecto_id": proyecto}, anotador=anotador)
    assert s == 200, (s, a)
    tarea, token = a["tarea"], a["token"]
    pasos = [
        (anotador, f"/tareas/{tarea['tarea_id']}/enviar",
         {"token": token, "anotacion": {"etiquetas": [tarea["taxonomia"][0]["etiqueta"]], "geometria": GEOMETRIA}}),
        (revisor, f"/tareas/{tarea['tarea_id']}/revision", {"accion": "empezar"}),
        (revisor, f"/tareas/{tarea['tarea_id']}/revision", {"accion": "aprobar"} if final == "aprobar"
         else {"accion": "rechazar", "motivo": "GEOMETRIA_IMPRECISA"}),
    ]
    for actor, ruta, cuerpo in pasos:
        s, r = http(GATEWAY, "POST", ruta, cuerpo, anotador=actor)
        assert s == 200, (ruta, cuerpo, s, r)
        t_200 = time.time()
    return {"tarea": tarea, "token": token, "anotador": anotador, "t_200": t_200, "respuesta_final": r}


def event_id_final(r: dict) -> str:
    """El registro devuelve el evento completo; el gateway solo su event_id."""
    final = r["respuesta_final"]
    return final["event_id"] if "event_id" in final else final["evento"]["event_id"]


def consulta(n: int, gateway: bool) -> dict:
    s, d = (http(GATEWAY, "GET", f"/analitica/q{n}?conjunto=principal") if gateway
            else http(ANALITICA, "GET", f"/q/{n}?conjunto=principal"))
    assert s == 200, (s, d)
    return d


def aprobadas_hoy(gateway: bool = False) -> tuple[int, float]:
    """Cuenta de Q1 (principal) para hoy y lidar, y lo que tardó la consulta."""
    d = consulta(1, gateway)
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fila = next((f for f in d["filas"] if f["dia"] == hoy and f["modalidad"] == "lidar"), None)
    return (fila["aprobadas"] if fila else 0), d["ms"]


def filas_lago(event_ids: list[str], conjunto: str = "eventos"):
    t = ds.dataset(LAGO / conjunto, format="parquet", partitioning="hive").to_table(
        filter=ds.field("event_id").isin(event_ids),
        columns=["event_id", "ts", "tarea_id", "estado", "version", "motivo_rechazo", "anotador_id",
                 "revisor_id", "micro_lote_id", "mes"])
    return t.to_pylist()


def filas_tarea(tarea_id: str, desde: float) -> list[dict]:
    """Filas del lago de una tarea escritas desde `desde` (epoch s), ordenadas por versión."""
    t = ds.dataset(LAGO / "eventos", format="parquet", partitioning="hive").to_table(
        filter=ds.field("tarea_id") == tarea_id,
        columns=["event_id", "ts", "tarea_id", "estado", "version", "motivo_rechazo", "anotador_id",
                 "revisor_id", "micro_lote_id", "mes"])
    filas = [f for f in t.to_pylist() if f["ts"].timestamp() >= desde - 1]
    return sorted(filas, key=lambda f: (f["version"], f["event_id"]))


def frescura(args) -> None:
    filas = []
    # Por el gateway no hay /flush: el evento pasa por la cola del gateway y espera el cierre del micro-lote.
    modos = ("via_gateway",) if args.gateway else ("espera_micro_lote", "con_flush")
    for modo in modos:
        for rep in range(1, args.repeticiones + 1):
            antes, _ = aprobadas_hoy(args.gateway)
            r = recorrido_gateway("aprobar") if args.gateway else recorrido("aprobar")
            if modo == "con_flush":
                http(HISTORIAL, "POST", "/flush")
            consultas, ms_q1 = 0, []
            while True:
                t_consulta = time.time()
                ahora, ms = aprobadas_hoy(args.gateway)
                consultas += 1
                ms_q1.append(ms)
                if ahora >= antes + 1:
                    t_visible = time.time()
                    break
            fila = {"modo": modo, "rep": rep, "tarea_id": r["tarea"]["tarea_id"],
                    "event_id": event_id_final(r), "t_200": round(r["t_200"], 3),
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
    for modo in modos:
        seg = sorted(f["segundos_hasta_visible"] for f in filas if f["modo"] == modo)
        print(f"{modo}: n={len(seg)} min={seg[0]} mediana={seg[len(seg) // 2]} max={seg[-1]} s")


def fila_q4(proyecto: str) -> dict | None:
    return next((x for x in consulta(4, True)["filas"] if x["proyecto_id"] == proyecto), None)


def rechazo_gateway(args) -> None:
    """Fila rechazada en Parquet y disponible en MongoDB, con Q4 contándola (I.3), todo por el gateway."""
    proyecto, anotador, _ = actores()
    q4_antes = fila_q4(proyecto)
    print("Q4 antes (proyecto LiDAR de ejemplo):", json.dumps(q4_antes))
    t0 = time.time()
    r = recorrido_gateway("rechazar")
    tarea_id = r["tarea"]["tarea_id"]
    print("gateway respondió al rechazo:", json.dumps(r["respuesta_final"]))

    s, d = http(GATEWAY, "POST", f"/tareas/{tarea_id}/enviar",
                {"token": r["token"], "anotacion": {"etiquetas": ["lidar#peaton"], "geometria": GEOMETRIA}},
                anotador=anotador)
    print(f"segundo envío con el token viejo: {s} {json.dumps(d, ensure_ascii=False)}")

    filas = []
    for _ in range(120):  # el evento va gateway -> historial -> micro-lote de 5 s
        filas = filas_tarea(tarea_id, t0)
        if any(f["estado"] == "rechazada" for f in filas):
            break
        time.sleep(0.5)
    print(f"\nParquet (filas de la tarea desde el inicio de la prueba, {time.time() - r['t_200']:.1f} s después del 200):")
    for fila in filas:
        print("  ", json.dumps(fila, default=str))
    repetidos = len(filas) - len({f["event_id"] for f in filas})
    print(f"filas: {len(filas)} · estados: {[f['estado'] for f in filas]} · event_id repetidos: {repetidos}")
    tarea = MongoClient(MONGO_URI, tz_aware=True).plataforma.tareas.find_one(
        {"_id": tarea_id}, {"estado": 1, "version": 1, "asignacion": 1, "ultima_revision": 1, "ultimo_evento_id": 1})
    print("\nMongoDB (registro):", json.dumps(tarea, default=str))
    print("ultimo_evento_id == event_id de la fila rechazada:", tarea["ultimo_evento_id"] == r["respuesta_final"]["event_id"])

    q4 = fila_q4(proyecto)
    print("\nQ4 después:", json.dumps(q4))
    antes = q4_antes["rechazadas"] if q4_antes else 0
    print(f"rechazadas en Q4: {antes} -> {q4['rechazadas'] if q4 else 0} (se esperaba {antes + 1})")
    Path(f"{args.prefijo}_rechazo.json").write_text(json.dumps(
        {"respuesta_gateway": r["respuesta_final"], "segundo_envio": [s, d], "filas_lago": filas, "mongo": tarea,
         "q4_antes": q4_antes, "q4_despues": q4}, default=str, indent=1))


def rechazo(args) -> None:
    if args.gateway:
        return rechazo_gateway(args)
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
    ap.add_argument("--gateway", action="store_true", help="frescura y rechazo por el gateway (flujo real)")
    args = ap.parse_args()
    {"frescura": frescura, "rechazo": rechazo, "reprocesar": reprocesar, "carga": carga,
     "caida-antes": caida_antes, "caida-despues": caida_despues}[args.fase](args)


if __name__ == "__main__":
    main()
