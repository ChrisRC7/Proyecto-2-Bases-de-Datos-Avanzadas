"""Simulador de anotadores y revisores (A6). Solo habla HTTP con el gateway (D12, red borde).

    docker compose --profile carga run --rm simulador \
        python -m simulador.simular --usuarios 40 --calentamiento 30 --duracion 90 \
        --csv /srv/evidence/P6_40u_corrida1.csv

Cada usuario virtual es un hilo con un anotador de dataset/salida/ids.json (archivo, no la base):
- anotador: POST /tareas/siguiente -> anota (pensar) -> POST /tareas/{id}/enviar
- revisor (10 % de los usuarios): toma una tarea enviada -> revisión empezar -> aprobar o rechazar
Cada petición queda en el CSV crudo (fase, usuario, rol, endpoint, status, ms) para P6.
"""
import argparse
import csv
import json
import os
import queue
import random
import threading
import time
from collections import defaultdict

import httpx

IDS = os.getenv("IDS_JSON", "/srv/ids/ids.json")
MOTIVOS = ["ETIQUETA_INCORRECTA", "GEOMETRIA_IMPRECISA", "OBJETO_OMITIDO", "INSTRUCCION_NO_SEGUIDA", "OTRO"]


class Registro:
    """Junta las mediciones de todos los hilos."""

    def __init__(self, inicio_medicion: float):
        self.inicio_medicion = inicio_medicion
        self.filas: list[dict] = []
        self.candado = threading.Lock()

    def anotar(self, usuario: str, rol: str, endpoint: str, status: int, ms: float):
        ahora = time.time()
        fila = {"ts": round(ahora, 3), "fase": "medicion" if ahora >= self.inicio_medicion else "calentamiento",
                "usuario": usuario, "rol": rol, "endpoint": endpoint, "status": status, "ms": round(ms, 2)}
        with self.candado:
            self.filas.append(fila)


def peticion(cliente: httpx.Client, reg: Registro, usuario: str, rol: str, metodo: str, ruta: str,
             endpoint: str, cuerpo: dict | None = None):
    t0 = time.perf_counter()
    try:
        r = cliente.request(metodo, ruta, json=cuerpo, headers={"X-Anotador-Id": usuario})
        status = r.status_code
    except httpx.HTTPError:
        r, status = None, 0  # sin respuesta (timeout o conexión): cuenta como error
    reg.anotar(usuario, rol, endpoint, status, (time.perf_counter() - t0) * 1000)
    return r


def geometria(rng: random.Random, tipo: str) -> dict:
    if tipo == "span":
        inicio = rng.randint(0, 500)
        return {"tipo": "span", "inicio": inicio, "fin": inicio + rng.randint(1, 40)}
    if tipo == "bbox":
        return {"tipo": "bbox", "x": rng.randint(0, 1000), "y": rng.randint(0, 600), "w": rng.randint(10, 300), "h": rng.randint(10, 300)}
    if tipo == "poligono":
        return {"tipo": "poligono", "puntos": [[rng.randint(0, 1000), rng.randint(0, 600)] for _ in range(rng.randint(3, 8))]}
    if tipo == "bbox_video":
        return {"tipo": "bbox_video", "cuadros": [{"t": t, "x": rng.randint(0, 1000), "y": rng.randint(0, 600), "w": 80, "h": 120}
                                                    for t in range(rng.randint(1, 5))]}
    return {"tipo": "cuboide_3d", "centro": [round(rng.uniform(-50, 50), 2) for _ in range(3)],
            "dimensiones": [round(rng.uniform(0.3, 5), 2) for _ in range(3)], "yaw": round(rng.uniform(-3.14, 3.14), 2)}


def anotar(cliente: httpx.Client, reg: Registro, usuario: str, tarea: dict, token: str, segundos: float):
    """Tiempo de anotación. Aquí se engancha el envío de borradores cada 2 s (B4.2)."""
    time.sleep(segundos)


def anotador(args, url: str, usuario: str, reg: Registro, fin: float, enviadas: queue.Queue, semilla: int):
    rng = random.Random(semilla)
    with httpx.Client(base_url=url, timeout=args.timeout) as cliente:
        while time.time() < fin:
            r = peticion(cliente, reg, usuario, "anotador", "POST", "/tareas/siguiente", "POST /tareas/siguiente",
                         {"proyecto_id": args.proyecto})
            if r is None or r.status_code != 200:
                time.sleep(1)  # 204 sin tareas, 503 o sin respuesta: espera y vuelve a pedir
                continue
            datos = r.json()
            tarea, token = datos["tarea"], datos["token"]
            anotar(cliente, reg, usuario, tarea, token, rng.uniform(args.pensar_min, args.pensar_max))
            etiquetas = [e["etiqueta"] for e in tarea.get("taxonomia", [])] or ["sin_etiqueta"]
            cuerpo = {"token": token, "anotacion": {"etiquetas": [rng.choice(etiquetas)],
                                                     "geometria": geometria(rng, tarea["tipo_tarea"])}}
            r = peticion(cliente, reg, usuario, "anotador", "POST", f"/tareas/{tarea['tarea_id']}/enviar",
                         "POST /tareas/{id}/enviar", cuerpo)
            if r is not None and r.status_code == 200:
                enviadas.put(tarea["tarea_id"])


def revisor(args, url: str, usuario: str, reg: Registro, fin: float, enviadas: queue.Queue, semilla: int):
    rng = random.Random(semilla)
    with httpx.Client(base_url=url, timeout=args.timeout) as cliente:
        while time.time() < fin:
            try:
                tarea_id = enviadas.get(timeout=1)
            except queue.Empty:
                continue
            ruta = f"/tareas/{tarea_id}/revision"
            r = peticion(cliente, reg, usuario, "revisor", "POST", ruta, "POST /tareas/{id}/revision",
                         {"accion": "empezar"})
            if r is None or r.status_code != 200:
                continue
            time.sleep(rng.uniform(args.revisar_min, args.revisar_max))
            if rng.random() < args.rechazo:
                cuerpo = {"accion": "rechazar", "motivo": rng.choice(MOTIVOS)}
            else:
                cuerpo = {"accion": "aprobar"}
            peticion(cliente, reg, usuario, "revisor", "POST", ruta, "POST /tareas/{id}/revision", cuerpo)


def percentil(valores: list[float], p: float) -> float:
    orden = sorted(valores)
    return orden[min(len(orden) - 1, max(0, round(p / 100 * len(orden) + 0.5) - 1))]  # nearest rank


def resumen(filas: list[dict], segundos: float) -> list[str]:
    medidas = [f for f in filas if f["fase"] == "medicion"]
    por_endpoint = defaultdict(list)
    for f in medidas:
        por_endpoint[f["endpoint"]].append(f)
    lineas = [f"{'endpoint':<30} {'n':>6} {'p50':>8} {'p95':>8} {'p99':>8} {'2xx':>6} {'4xx':>5} {'5xx/0':>6}"]
    for endpoint, fs in sorted(por_endpoint.items()):
        ms = [f["ms"] for f in fs]
        c2 = sum(1 for f in fs if 200 <= f["status"] < 300)
        c4 = sum(1 for f in fs if 400 <= f["status"] < 500)
        c5 = sum(1 for f in fs if f["status"] >= 500 or f["status"] == 0)
        lineas.append(f"{endpoint:<30} {len(fs):>6} {percentil(ms, 50):>8.1f} {percentil(ms, 95):>8.1f} "
                      f"{percentil(ms, 99):>8.1f} {c2:>6} {c4:>5} {c5:>6}")
    envios = sum(1 for f in medidas if f["endpoint"] == "POST /tareas/{id}/enviar" and f["status"] == 200)
    lineas.append(f"envíos confirmados: {envios} en {segundos:.0f} s = {envios / segundos * 60:.1f} envíos/min")
    return lineas


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.getenv("GATEWAY_URL", "http://gateway:8000"))
    ap.add_argument("--usuarios", type=int, default=10, help="total de usuarios virtuales (10 %% revisores)")
    ap.add_argument("--calentamiento", type=float, default=30)
    ap.add_argument("--duracion", type=float, default=90, help="segundos medidos, después del calentamiento")
    ap.add_argument("--pensar-min", type=float, default=0.5)
    ap.add_argument("--pensar-max", type=float, default=2.0)
    ap.add_argument("--revisar-min", type=float, default=0.2)
    ap.add_argument("--revisar-max", type=float, default=1.0)
    ap.add_argument("--rechazo", type=float, default=0.2, help="probabilidad de rechazar en la revisión")
    ap.add_argument("--proyecto", default=None, help="proyecto_id fijo (por defecto la cola global)")
    ap.add_argument("--timeout", type=float, default=10)
    ap.add_argument("--semilla", type=int, default=2026)
    ap.add_argument("--csv", help="archivo con una fila por petición")
    args = ap.parse_args()

    ids = json.load(open(IDS))
    n_revisores = max(1, round(args.usuarios * 0.1))
    n_anotadores = args.usuarios - n_revisores
    anotadores = [a["id"] for a in ids["anotadores"] if a["rol"] == "anotador"][:n_anotadores]
    revisores = [a["id"] for a in ids["anotadores"] if a["rol"] == "revisor"][:n_revisores]
    if len(anotadores) < n_anotadores or len(revisores) < n_revisores:
        raise SystemExit(f"ids.json no tiene suficientes anotadores/revisores para {args.usuarios} usuarios")

    inicio = time.time()
    reg = Registro(inicio + args.calentamiento)
    fin = inicio + args.calentamiento + args.duracion
    enviadas: queue.Queue = queue.Queue()
    hilos = [threading.Thread(target=anotador, args=(args, args.url, u, reg, fin, enviadas, args.semilla + i))
             for i, u in enumerate(anotadores)]
    hilos += [threading.Thread(target=revisor, args=(args, args.url, u, reg, fin, enviadas, args.semilla + 1000 + i))
              for i, u in enumerate(revisores)]
    print(f"{args.url} · {n_anotadores} anotadores + {n_revisores} revisores · "
          f"{args.calentamiento:.0f} s de calentamiento + {args.duracion:.0f} s medidos")
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["ts", "fase", "usuario", "rol", "endpoint", "status", "ms"])
            w.writeheader()
            w.writerows(reg.filas)
    for linea in resumen(reg.filas, args.duracion):
        print(linea)


if __name__ == "__main__":
    main()
