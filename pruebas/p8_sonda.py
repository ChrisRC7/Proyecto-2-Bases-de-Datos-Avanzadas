"""Sonda de escrituras para P8 (MongoDB): asigna y envía tareas sin parar a través del registro.

Cada vuelta hace dos escrituras: POST /tareas/asignar y, si dio 200, el envío de esa tarea.
Escribe una fila por petición (ts de la respuesta, op, tarea, status, error, ms) para calcular
después RTO y RPO con pruebas/p8_verificar.py. La orquesta pruebas/p8_mongo.sh.
"""
import argparse
import csv
import json
import os
import time
import urllib.error
import urllib.request
import uuid

URL = os.getenv("REGISTRO_URL", "http://registro:8001")
IDS = "/srv/dataset/salida/ids.json"
GEOMETRIAS = {
    "span": {"tipo": "span", "inicio": 3, "fin": 12},
    "bbox": {"tipo": "bbox", "x": 10, "y": 20, "w": 100, "h": 80},
    "poligono": {"tipo": "poligono", "puntos": [[0, 0], [50, 0], [25, 40]]},
    "bbox_video": {"tipo": "bbox_video", "cuadros": [{"t": 0, "x": 10, "y": 20, "w": 80, "h": 120}]},
    "cuboide_3d": {"tipo": "cuboide_3d", "centro": [1.2, 3.4, 0.5], "dimensiones": [0.6, 1.8, 1.7], "yaw": 0.3},
}


def http(metodo: str, ruta: str, cuerpo=None):
    peticion = urllib.request.Request(URL + ruta, method=metodo, headers={"Content-Type": "application/json"},
                                      data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
    try:
        with urllib.request.urlopen(peticion, timeout=15) as r:
            texto = r.read()
            return r.status, json.loads(texto) if texto else None
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
            return e.code, None
    except OSError as e:
        return 0, {"error": type(e).__name__}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--duracion", type=float, default=60)
    ap.add_argument("--pausa", type=float, default=0.2)
    ap.add_argument("--csv", required=True)
    args = ap.parse_args()

    ids = json.load(open(IDS))
    anotador = next(a["id"] for a in ids["anotadores"] if a["rol"] == "anotador")
    etiquetas = {p["id"]: p["etiquetas"][0] for p in ids["proyectos_activos"]}
    fin = time.time() + args.duracion
    ultimo = None

    with open(args.csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "op", "tarea_id", "version", "status", "error", "ms"])
        w.writeheader()

        def anotar(op, tarea_id, version, status, cuerpo, t0):
            nonlocal ultimo
            ts = time.time()
            error = (cuerpo or {}).get("error") if status not in (200, 204) else ""
            w.writerow({"ts": f"{ts:.3f}", "op": op, "tarea_id": tarea_id or "", "version": version, "status": status,
                        "error": error or "", "ms": round((ts - t0) * 1000, 1)})
            f.flush()
            estado = "ok" if status in (200, 204) else f"error {status} {error}"
            if estado != ultimo:
                print(f"{time.strftime('%H:%M:%S', time.localtime(ts))}.{int(ts % 1 * 1000):03d} {op}: {estado}", flush=True)
                ultimo = estado

        while time.time() < fin:
            token = str(uuid.uuid4())
            t0 = time.time()
            s, d = http("POST", "/tareas/asignar", {"anotador_id": anotador, "token": token})
            tarea = d["tarea"] if s == 200 else None
            anotar("asignar", tarea and tarea["tarea_id"], tarea and tarea["version"], s, d, t0)
            if tarea:
                t0 = time.time()
                s, d = http("POST", f"/tareas/{tarea['tarea_id']}/transicion", {
                    "accion": "enviar", "token": token, "version": tarea["version"], "actor_id": anotador,
                    "anotacion": {"etiquetas": [etiquetas[tarea["proyecto_id"]]],
                                  "geometria": GEOMETRIAS[tarea["tipo_tarea"]]}})
                anotar("enviar", tarea["tarea_id"], tarea["version"] + 1, s, d, t0)  # versión esperada
            time.sleep(args.pausa)


if __name__ == "__main__":
    main()
