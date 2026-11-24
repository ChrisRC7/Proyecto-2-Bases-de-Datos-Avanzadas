"""Sonda de escrituras para P8 (MongoDB): asigna y envía tareas sin parar.

Modo gateway (prueba final, red borde): POST /tareas/siguiente y POST /tareas/{id}/enviar.
Modo registro (corrida del 22/10, red interna): POST /tareas/asignar y /tareas/{id}/transicion.
Cada vuelta hace dos escrituras: la asignación y, si dio 200, el envío de esa tarea.
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

# URL de cada modo y lugares donde buscar ids.json.
URLS = {
    "registro": os.getenv("REGISTRO_URL", "http://registro:8001"),
    "gateway": os.getenv("GATEWAY_URL", "http://gateway:8000"),
}
IDS = [os.getenv("IDS_JSON", ""), "/srv/ids/ids.json", "/srv/dataset/salida/ids.json"]
URL = URLS["registro"]
ANOTADOR = None  # en modo gateway va en el header X-Anotador-Id
# Una geometría válida fija por tipo de tarea (la sonda no necesita variedad).
GEOMETRIAS = {
    "span": {"tipo": "span", "inicio": 3, "fin": 12},
    "bbox": {"tipo": "bbox", "x": 10, "y": 20, "w": 100, "h": 80},
    "poligono": {"tipo": "poligono", "puntos": [[0, 0], [50, 0], [25, 40]]},
    "bbox_video": {"tipo": "bbox_video", "cuadros": [{"t": 0, "x": 10, "y": 20, "w": 80, "h": 120}]},
    "cuboide_3d": {"tipo": "cuboide_3d", "centro": [1.2, 3.4, 0.5], "dimensiones": [0.6, 1.8, 1.7], "yaw": 0.3},
}


def http(metodo: str, ruta: str, cuerpo=None):
    """Petición JSON a URL+ruta; devuelve (status, cuerpo). status 0 si no hubo respuesta."""
    cabeceras = {"Content-Type": "application/json"}
    if ANOTADOR:
        cabeceras["X-Anotador-Id"] = ANOTADOR
    peticion = urllib.request.Request(URL + ruta, method=metodo, headers=cabeceras,
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


def codigo_error(cuerpo) -> str:
    """El gateway responde 503 servicio_no_disponible y dice en el detalle qué servicio falló."""
    cuerpo = cuerpo or {}
    error = cuerpo.get("error") or ""
    detalle = cuerpo.get("detalle")
    if error == "servicio_no_disponible" and isinstance(detalle, dict) and detalle.get("servicio"):
        error += f"({detalle['servicio']})"
    return error


def main() -> None:
    """Bucle de asignar + enviar durante --duracion segundos, anotando cada petición en el CSV."""
    global URL, ANOTADOR
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--modo", choices=["gateway", "registro"], default="gateway")
    ap.add_argument("--duracion", type=float, default=60)
    ap.add_argument("--pausa", type=float, default=0.2)
    ap.add_argument("--csv", required=True)
    args = ap.parse_args()

    # La sonda trabaja como el primer anotador del dataset.
    ids = json.load(open(next(p for p in IDS if p and os.path.exists(p))))
    anotador = next(a["id"] for a in ids["anotadores"] if a["rol"] == "anotador")
    URL = URLS[args.modo]
    if args.modo == "gateway":
        ANOTADOR = anotador
    etiquetas = {p["id"]: p["etiquetas"][0] for p in ids["proyectos_activos"]}
    fin = time.time() + args.duracion
    ultimo = None

    with open(args.csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "op", "tarea_id", "version", "status", "error", "ms"])
        w.writeheader()

        def anotar(op, tarea_id, version, status, cuerpo, t0):
            """Escribe una fila en el CSV y avisa por pantalla solo cuando cambia el estado (ok/error)."""
            nonlocal ultimo
            ts = time.time()
            error = codigo_error(cuerpo) if status not in (200, 204) else ""
            w.writerow({"ts": f"{ts:.3f}", "op": op, "tarea_id": tarea_id or "", "version": version, "status": status,
                        "error": error or "", "ms": round((ts - t0) * 1000, 1)})
            f.flush()
            estado = "ok" if status in (200, 204) else f"error {status} {error}"
            if estado != ultimo:
                print(f"{time.strftime('%H:%M:%S', time.localtime(ts))}.{int(ts % 1 * 1000):03d} {op}: {estado}", flush=True)
                ultimo = estado

        while time.time() < fin:
            # 1. Asignación (por el gateway o directo al registro).
            t0 = time.time()
            if args.modo == "gateway":
                s, d = http("POST", "/tareas/siguiente", {})
                token = d["token"] if s == 200 else None
            else:
                token = str(uuid.uuid4())
                s, d = http("POST", "/tareas/asignar", {"anotador_id": anotador, "token": token})
            tarea = d["tarea"] if s == 200 else None
            anotar("asignar", tarea and tarea["tarea_id"], tarea and tarea["version"], s, d, t0)
            # 2. Si hubo tarea, se envía su anotación.
            if tarea:
                anotacion = {"etiquetas": [etiquetas[tarea["proyecto_id"]]],
                             "geometria": GEOMETRIAS[tarea["tipo_tarea"]]}
                t0 = time.time()
                if args.modo == "gateway":
                    s, d = http("POST", f"/tareas/{tarea['tarea_id']}/enviar", {"token": token, "anotacion": anotacion})
                else:
                    s, d = http("POST", f"/tareas/{tarea['tarea_id']}/transicion", {
                        "accion": "enviar", "token": token, "version": tarea["version"], "actor_id": anotador,
                        "anotacion": anotacion})
                anotar("enviar", tarea["tarea_id"], tarea["version"] + 1, s, d, t0)  # versión esperada
            time.sleep(args.pausa)


if __name__ == "__main__":
    main()
