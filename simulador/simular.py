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

# Archivo con los ids de anotadores/revisores y motivos de rechazo posibles.
IDS = os.getenv("IDS_JSON", "/srv/ids/ids.json")
MOTIVOS = ["ETIQUETA_INCORRECTA", "GEOMETRIA_IMPRECISA", "OBJETO_OMITIDO", "INSTRUCCION_NO_SEGUIDA", "OTRO"]


class Registro:
    """Junta las mediciones de todos los hilos."""

    def __init__(self, inicio_medicion: float):
        """`inicio_medicion`: instante (epoch) en que termina el calentamiento."""
        self.inicio_medicion = inicio_medicion
        self.filas: list[dict] = []
        self.candado = threading.Lock()

    def anotar(self, usuario: str, rol: str, endpoint: str, status: int, ms: float):
        """Agrega una medición (protegida con candado porque escriben varios hilos)."""
        ahora = time.time()
        fila = {"ts": round(ahora, 3), "fase": "medicion" if ahora >= self.inicio_medicion else "calentamiento",
                "usuario": usuario, "rol": rol, "endpoint": endpoint, "status": status, "ms": round(ms, 2)}
        with self.candado:
            self.filas.append(fila)


def peticion(cliente: httpx.Client, reg: Registro, usuario: str, rol: str, metodo: str, ruta: str,
             endpoint: str, cuerpo: dict | None = None):
    """Hace una petición al gateway como `usuario`, mide su duración y la anota en `reg`.

    Devuelve la respuesta, o None si no hubo respuesta (timeout o error de conexión).
    """
    t0 = time.perf_counter()
    try:
        r = cliente.request(metodo, ruta, json=cuerpo, headers={"X-Anotador-Id": usuario})
        status = r.status_code
    except httpx.HTTPError:
        r, status = None, 0  # sin respuesta (timeout o conexión): cuenta como error
    reg.anotar(usuario, rol, endpoint, status, (time.perf_counter() - t0) * 1000)
    return r


def geometria(rng: random.Random, tipo: str) -> dict:
    """Geometría aleatoria pero válida para el tipo de tarea (span, bbox, polígono, video o cuboide)."""
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



def anotar(cliente, reg, usuario, tarea, token, contenido, segundos, borradores=False):
    """Simula la anotacion y, opcionalmente, guarda borradores en otro hilo."""

    # Sin borradores: solo "piensa" el tiempo indicado.
    if not borradores:
        time.sleep(segundos)
        return True

    from simulador.b42 import guardar_periodicamente

    resultado = {}

    def enviar(metodo, ruta, endpoint, cuerpo):
        """Usa `peticion` para que los borradores y latidos también queden medidos."""
        return peticion(
            cliente, reg, usuario, "anotador",
            metodo, ruta, endpoint, cuerpo
        )

    def trabajo_borradores():
        """Hilo secundario: guarda borrador + latido cada 2 s mientras dura la anotación."""
        resultado.update(
            guardar_periodicamente(
                cliente=cliente,
                tarea_id=tarea["tarea_id"],
                anotador_id=usuario,
                token=token,
                contenido=contenido,
                duracion=segundos,
                intervalo=2.0,
                enviar=enviar,
            )
        )

    hilo = threading.Thread(
        target=trabajo_borradores,
        name=f"b42-{usuario[:8]}",
        daemon=True,
    )

    hilo.start()
    time.sleep(segundos)
    hilo.join()

    # Si un borrador o latido falló, la tarea ya no es de este anotador: no se envía.
    if resultado.get("estado") != "completado":
        print(
            f"B4.2 | No se enviara tarea {tarea['tarea_id']}: {resultado}",
            flush=True,
        )
        return False

    return True



def anotador(args, url: str, usuario: str, reg: Registro, fin: float, enviadas: queue.Queue, semilla: int):
    """Hilo de un anotador: pide tarea, la anota y la envía, en bucle hasta `fin`.

    Cada tarea enviada con éxito se pone en la cola `enviadas` para los revisores.
    """
    rng = random.Random(semilla)
    with httpx.Client(base_url=url, timeout=args.timeout) as cliente:
        while time.time() < fin:
            r = peticion(cliente, reg, usuario, "anotador", "POST", "/tareas/siguiente", "POST /tareas/siguiente",
                         {"proyecto_id": args.proyecto})
            if r is None or r.status_code != 200:
                time.sleep(1)  # 204 sin tareas, 503 o sin respuesta: espera y vuelve a pedir
                continue

            # Arma una anotación válida con una etiqueta de la taxonomía del proyecto.
            datos = r.json()
            tarea, token = datos["tarea"], datos["token"]

            etiquetas = [
                e["etiqueta"] for e in tarea.get("taxonomia", [])
            ] or ["sin_etiqueta"]

            anotacion = {
                "etiquetas": [rng.choice(etiquetas)],
                "geometria": geometria(rng, tarea["tipo_tarea"]),
            }

            completada = anotar(
                cliente,
                reg,
                usuario,
                tarea,
                token,
                anotacion,
                rng.uniform(args.pensar_min, args.pensar_max),
                borradores=args.borradores,
            )

            if not completada:
                continue

            # Envía la anotación final; si se acepta, queda disponible para revisión.
            cuerpo = {
                "token": token,
                "anotacion": anotacion,
            }

            r = peticion(cliente, reg, usuario, "anotador", "POST", f"/tareas/{tarea['tarea_id']}/enviar",
                         "POST /tareas/{id}/enviar", cuerpo)
            if r is not None and r.status_code == 200:
                enviadas.put(tarea["tarea_id"])


def revisor(args, url: str, usuario: str, reg: Registro, fin: float, enviadas: queue.Queue, semilla: int):
    """Hilo de un revisor: toma tareas enviadas, empieza la revisión y aprueba o rechaza."""
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
            # Tiempo de revisión y decisión (rechaza con probabilidad args.rechazo).
            time.sleep(rng.uniform(args.revisar_min, args.revisar_max))
            if rng.random() < args.rechazo:
                cuerpo = {"accion": "rechazar", "motivo": rng.choice(MOTIVOS)}
            else:
                cuerpo = {"accion": "aprobar"}
            peticion(cliente, reg, usuario, "revisor", "POST", ruta, "POST /tareas/{id}/revision", cuerpo)


def percentil(valores: list[float], p: float) -> float:
    """Percentil p (0-100) por el método de rango más cercano."""
    orden = sorted(valores)
    return orden[min(len(orden) - 1, max(0, round(p / 100 * len(orden) + 0.5) - 1))]  # nearest rank


def resumen(filas: list[dict], segundos: float) -> list[str]:
    """Tabla de texto con n, p50/p95/p99 y conteo de códigos por endpoint (solo fase de medición)."""
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
    """Lee los argumentos, lanza los hilos de usuarios, espera a que terminen y reporta."""
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
    ap.add_argument("--borradores", action="store_true", help="Activa el hilo de borradores y latidos cada 2 segundos")
    ap.add_argument("--semilla", type=int, default=2026)
    ap.add_argument("--csv", help="archivo con una fila por petición")
    args = ap.parse_args()

    # Elige los anotadores y revisores (10 % de los usuarios) de ids.json.
    ids = json.load(open(IDS))
    n_revisores = max(1, round(args.usuarios * 0.1))
    n_anotadores = args.usuarios - n_revisores
    anotadores = [a["id"] for a in ids["anotadores"] if a["rol"] == "anotador"][:n_anotadores]
    revisores = [a["id"] for a in ids["anotadores"] if a["rol"] == "revisor"][:n_revisores]
    if len(anotadores) < n_anotadores or len(revisores) < n_revisores:
        raise SystemExit(f"ids.json no tiene suficientes anotadores/revisores para {args.usuarios} usuarios")

    # Tiempos: calentamiento (no se mide) y luego `duracion` segundos medidos.
    inicio = time.time()
    reg = Registro(inicio + args.calentamiento)
    fin = inicio + args.calentamiento + args.duracion
    enviadas: queue.Queue = queue.Queue()
    # Un hilo por usuario virtual, cada uno con su propia semilla.
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

    # Guarda las mediciones crudas (opcional) e imprime el resumen.
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["ts", "fase", "usuario", "rol", "endpoint", "status", "ms"])
            w.writeheader()
            w.writerows(reg.filas)
    for linea in resumen(reg.filas, args.duracion):
        print(linea)


if __name__ == "__main__":
    main()
