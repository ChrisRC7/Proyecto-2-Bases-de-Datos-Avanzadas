"""Carga directa contra el registro para comparar w:"majority" con w:1 (A7.4).

Cada hilo repite el ciclo de una tarea: asignar -> enviar -> empezar_revision -> aprobar (cuatro
escrituras en MongoDB). Se usa la misma carga y el mismo dataset en los dos modos; lo único que
cambia es MONGO_W del registro. Va directo al registro (red interna) para que la diferencia no se
mezcle con el gateway ni con Valkey.

    docker compose --profile datos run --rm -v ./pruebas:/srv/pruebas dataset \
        python /srv/pruebas/carga_registro.py --hilos 20 --calentamiento 10 --duracion 30 --csv /srv/evidence/...csv
"""
import argparse
import csv
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import defaultdict

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
        with urllib.request.urlopen(peticion, timeout=30) as r:
            texto = r.read()
            return r.status, json.loads(texto) if texto else None
    except urllib.error.HTTPError as e:
        return e.code, None
    except OSError:
        return 0, None


def percentil(valores: list[float], p: float) -> float:
    orden = sorted(valores)
    return orden[min(len(orden) - 1, max(0, round(p / 100 * len(orden) + 0.5) - 1))]  # nearest rank


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hilos", type=int, default=20)
    ap.add_argument("--calentamiento", type=float, default=10)
    ap.add_argument("--duracion", type=float, default=30)
    ap.add_argument("--csv")
    args = ap.parse_args()

    ids = json.load(open(IDS))
    anotadores = [a["id"] for a in ids["anotadores"] if a["rol"] == "anotador"][:args.hilos]
    revisor = next(a["id"] for a in ids["anotadores"] if a["rol"] == "revisor")
    etiquetas = {p["id"]: p["etiquetas"][0] for p in ids["proyectos_activos"]}
    _, salud = http("GET", "/salud")

    inicio = time.time()
    medir_desde, fin = inicio + args.calentamiento, inicio + args.calentamiento + args.duracion
    filas, candado = [], threading.Lock()

    def medir(op: str, metodo: str, ruta: str, cuerpo=None):
        t0 = time.perf_counter()
        status, datos = http(metodo, ruta, cuerpo)
        ahora = time.time()
        with candado:
            filas.append({"ts": round(ahora, 3), "fase": "medicion" if ahora >= medir_desde else "calentamiento",
                          "op": op, "status": status, "ms": round((time.perf_counter() - t0) * 1000, 2)})
        return status, datos

    def trabajar(anotador_id: str):
        while time.time() < fin:
            token = str(uuid.uuid4())
            s, d = medir("asignar", "POST", "/tareas/asignar", {"anotador_id": anotador_id, "token": token})
            if s != 200:
                time.sleep(0.5)
                continue
            t = d["tarea"]
            ruta = f"/tareas/{t['tarea_id']}/transicion"
            s, _ = medir("enviar", "POST", ruta, {"accion": "enviar", "token": token, "version": t["version"],
                                                    "actor_id": anotador_id, "anotacion": {
                                                        "etiquetas": [etiquetas[t["proyecto_id"]]],
                                                        "geometria": GEOMETRIAS[t["tipo_tarea"]]}})
            if s != 200:
                continue
            if medir("empezar_revision", "POST", ruta, {"accion": "empezar_revision", "actor_id": revisor})[0] == 200:
                medir("aprobar", "POST", ruta, {"accion": "aprobar", "actor_id": revisor})

    hilos = [threading.Thread(target=trabajar, args=(a,)) for a in anotadores]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["ts", "fase", "op", "status", "ms"])
            w.writeheader()
            w.writerows(filas)

    medidas = [f for f in filas if f["fase"] == "medicion"]
    print(f"registro {URL} · {len(anotadores)} hilos · {args.calentamiento:.0f} s de calentamiento + "
          f"{args.duracion:.0f} s medidos · mongodb: {salud and salud.get('mongodb')}")
    print(f"{'operación':<18} {'n':>6} {'p50':>8} {'p95':>8} {'p99':>8} {'ops/s':>8} {'errores':>8}")
    for op in ("asignar", "enviar", "empezar_revision", "aprobar"):
        fs = [f for f in medidas if f["op"] == op]
        ok = [f["ms"] for f in fs if f["status"] in (200, 204)]
        if ok:
            print(f"{op:<18} {len(fs):>6} {percentil(ok, 50):>8.2f} {percentil(ok, 95):>8.2f} "
                  f"{percentil(ok, 99):>8.2f} {len(ok) / args.duracion:>8.1f} {len(fs) - len(ok):>8}")
    ciclos = sum(1 for f in medidas if f["op"] == "aprobar" and f["status"] == 200)
    print(f"ciclos completos (aprobadas): {ciclos} = {ciclos / args.duracion:.1f} por segundo")


if __name__ == "__main__":
    main()
