"""Generador reproducible del histórico de eventos del lago Parquet (C3.1, C3.2, C3.4, C3.5).

    docker compose --profile datos run --rm dataset-historico                 # principal, ~800k
    docker compose --profile datos run --rm dataset-historico \
        python -m dataset.historico.generar --conjunto grande --tareas 1250000  # ~10M

Borra y vuelve a escribir datalake/eventos (o eventos_grande). Con la misma semilla salen
exactamente los mismos eventos. Doce meses, del 2025-10-01 al 2026-09-30: termina justo cuando
empieza el dataset operacional de MongoDB (2026-10-01).

Las tareas son de los 60 proyectos cerrados (solo existen en el historial), con los clientes,
equipos y anotadores de dataset/salida/ids.json. Si ese archivo no está, los mismos ids se
calculan con dataset/generar.py (misma semilla, no hace falta MongoDB).

Cada tarea recorre las transiciones del registro (endpoints/registro.md):
  abierta -> [liberada -> abierta | (vence el plazo) abierta] -> anotada -> en_revision
          -> aprobada | rechazada -> abierta -> ...
Los campos de cada fila siguen las reglas del registro: anotador_id, equipo_id y tomada_ts son
los de la asignación vigente; revisor_id en en_revision/aprobada/rechazada; version +1 por fila.
Las tareas que no terminan antes del 2026-10-01 quedan a medias (como en la realidad).

Duplicados (C3.5): una fracción de los eventos se vuelve a escribir en otro archivo del mismo mes
(part-<id>-reentrega.parquet, como un micro-lote entregado dos veces), con el mismo event_id.
"""
import argparse
import json
import math
import random
import time
import uuid
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dataset.historico.lago import CAMPOS, CONJUNTOS, escribir, vaciar

SEMILLA = 2026
INICIO = datetime(2025, 10, 1, tzinfo=timezone.utc)
FIN = datetime(2026, 10, 1, tzinfo=timezone.utc)
IDS = Path(__file__).resolve().parents[1] / "salida" / "ids.json"
TAREAS_POR_BATCH = 100
MOTIVOS = ["ETIQUETA_INCORRECTA", "GEOMETRIA_IMPRECISA", "OBJETO_OMITIDO", "INSTRUCCION_NO_SEGUIDA", "OTRO"]
MAX_CICLOS = 6  # después de 5 rechazos se aprueba (evita tareas infinitas)

# Mediana (s) del tiempo de anotación por tipo de tarea; lognormal con sigma 0,5.
ANOTACION_S = {"span": 360, "bbox": 600, "poligono": 1200, "bbox_video": 2100, "cuboide_3d": 3000}
P_LIBERAR = 0.12        # el anotador suelta la tarea antes de enviarla
P_VENCER = 0.08         # el plazo de la asignación vence y otro la toma
ESPERA_REVISION_H = 5   # mediana anotada -> en_revision; lognormal sigma 1,1 (cola de revisores)
H = 3600 * 1000
S = 1000


def cargar_ids(semilla: int) -> tuple[dict, str]:
    if IDS.exists():
        return json.loads(IDS.read_text()), str(IDS)
    from dataset.generar import generar, ids  # mismos ids que la carga de MongoDB
    return ids(generar(semilla), semilla), "calculados con dataset/generar.py"


def meses() -> list[tuple[int, str]]:
    inicio, salida = INICIO, []
    while inicio < FIN:
        salida.append((int(inicio.timestamp() * 1000), f"{inicio:%Y-%m}"))
        inicio = inicio.replace(year=inicio.year + inicio.month // 12, month=inicio.month % 12 + 1)
    return salida


def generar(conjunto: str, tareas: int, semilla: int, tareas_por_archivo: int, duplicados: float) -> dict:
    rng = random.Random(semilla)
    ids, origen = cargar_ids(semilla)

    def nuevo_id() -> str:
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))

    def lognormal(mediana: float, sigma: float) -> float:
        return mediana * math.exp(rng.gauss(0, sigma))

    anotadores = [a for a in ids["anotadores"] if a["rol"] == "anotador"]
    revisores = [a["id"] for a in ids["anotadores"] if a["rol"] == "revisor"]
    # Calidad por equipo: multiplica la probabilidad de rechazo del proyecto (Q2 varía por equipo).
    factor_equipo = {e: rng.uniform(0.6, 1.4) for e in ids["equipos"]}
    inicio_ms, fin_ms = int(INICIO.timestamp() * 1000), int(FIN.timestamp() * 1000)
    limites = meses()
    inicios_mes = [m for m, _ in limites]

    proyectos = []
    for p in ids["proyectos_cerrados"]:
        desde = inicio_ms + int(rng.uniform(0, 240) * 24 * H)
        hasta = min(desde + int(rng.uniform(90, 300) * 24 * H), fin_ms - 24 * H)
        proyectos.append({**p, "desde": desde, "hasta": hasta, "p_rechazo": rng.uniform(0.25, 0.6)})

    base = vaciar(conjunto)
    conteo, archivos, filas_dup = Counter(), 0, 0
    tareas_terminadas = 0
    cols = defaultdict(lambda: {c: [] for c in CAMPOS})       # mes -> columnas
    dups = defaultdict(lambda: {c: [] for c in CAMPOS})
    archivo_n = 0

    def volcar():
        nonlocal archivos, archivo_n, filas_dup
        mlid = f"hist-{conjunto}-s{semilla}-{archivo_n:03d}"
        for mes, c in sorted(cols.items()):
            c["micro_lote_id"] = [mlid] * len(c["event_id"])
            escribir(c, base, mes, mlid)
            archivos += 1
        for mes, c in sorted(dups.items()):
            c["micro_lote_id"] = [f"{mlid}-reentrega"] * len(c["event_id"])
            filas_dup += escribir(c, base, mes, f"{mlid}-reentrega")
            archivos += 1
        cols.clear()
        dups.clear()
        archivo_n += 1

    por_proyecto = [tareas // len(proyectos) + (1 if i < tareas % len(proyectos) else 0)
                    for i in range(len(proyectos))]
    n = 0
    for p, cuantas in zip(proyectos, por_proyecto):
        batch_id = None
        for k in range(cuantas):
            if k % TAREAS_POR_BATCH == 0:
                batch_id = nuevo_id()
            tipo = {"texto": "span", "video": "bbox_video", "lidar": "cuboide_3d"}.get(p["modalidad"]) \
                or ("bbox" if rng.random() < 0.7 else "poligono")
            fijo = {"tarea_id": nuevo_id(), "proyecto_id": p["id"], "cliente_id": p["cliente_id"],
                    "batch_id": batch_id, "modalidad": p["modalidad"], "tipo_tarea": tipo,
                    "sla_horas": p["sla_horas"]}
            estado_vivo = {"version": 0}

            def emitir(ts: int, estado: str, anotador: dict, tomada: int, revisor=None, motivo=None) -> bool:
                if ts >= fin_ms:
                    return False  # la tarea queda a medias al cierre del histórico
                estado_vivo["version"] += 1
                fila = {**fijo, "event_id": nuevo_id(), "ts": ts, "estado": estado,
                        "version": estado_vivo["version"], "anotador_id": anotador["id"],
                        "equipo_id": anotador["equipo_id"], "revisor_id": revisor, "motivo_rechazo": motivo,
                        "tomada_ts": tomada, "micro_lote_id": None}
                mes = limites[bisect_right(inicios_mes, ts) - 1][1]
                destino = cols[mes]
                for c in CAMPOS:
                    destino[c].append(fila[c])
                if rng.random() < duplicados:
                    for c in CAMPOS:
                        dups[mes][c].append(fila[c])
                conteo[estado] += 1
                return True

            t = p["desde"] + int(rng.random() * (p["hasta"] - p["desde"]))
            terminada = False
            for ciclo in range(MAX_CICLOS):
                # Toma (y retomas por liberación o plazo vencido) hasta que alguien la envía.
                la_envia = False
                while True:
                    anotador = rng.choice(anotadores)
                    tomada = t
                    if not emitir(t, "abierta", anotador, tomada):
                        break
                    azar = rng.random()
                    if azar < P_LIBERAR:
                        t += int(lognormal(300, 0.8) * S)
                        if not emitir(t, "liberada", anotador, tomada):
                            break
                        t += int(lognormal(900, 0.8) * S)
                    elif azar < P_LIBERAR + P_VENCER:
                        t += int((120 + lognormal(600, 0.8)) * S)  # vence el lock de 120 s
                    else:
                        la_envia = True
                        break
                if not la_envia:
                    break
                t += int(lognormal(ANOTACION_S[tipo], 0.5) * S)
                if not emitir(t, "anotada", anotador, tomada):
                    break
                t += int(lognormal(ESPERA_REVISION_H, 1.1) * H)
                revisor = rng.choice(revisores)
                if not emitir(t, "en_revision", anotador, tomada, revisor):
                    break
                t += int(lognormal(300, 0.5) * S)
                p_rechazo = min(0.9, p["p_rechazo"] * factor_equipo[anotador["equipo_id"]])
                if ciclo < MAX_CICLOS - 1 and rng.random() < p_rechazo:
                    if not emitir(t, "rechazada", anotador, tomada, revisor, rng.choice(MOTIVOS)):
                        break
                    t += int(lognormal(2, 1.0) * H)  # vuelve a la cola
                    continue
                terminada = emitir(t, "aprobada", anotador, tomada, revisor)
                break
            tareas_terminadas += terminada
            n += 1
            if n % tareas_por_archivo == 0:
                volcar()
    if cols:
        volcar()

    total = sum(conteo.values())
    return {"conjunto": conjunto, "carpeta": str(base), "semilla": semilla, "ids": origen,
            "desde": INICIO.isoformat(), "hasta": FIN.isoformat(), "meses": len(limites),
            "proyectos": len(proyectos), "tareas": tareas, "tareas_aprobadas": tareas_terminadas,
            "eventos": total, "eventos_por_estado": dict(sorted(conteo.items())),
            "duplicados_inyectados": filas_dup, "filas_en_archivos": total + filas_dup, "archivos": archivos}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--conjunto", choices=["principal", "grande"], default="principal")
    ap.add_argument("--tareas", type=int, help="100 000 en principal; ~1 250 000 para ~10M en grande")
    ap.add_argument("--semilla", type=int, default=SEMILLA)
    ap.add_argument("--duplicados", type=float, default=0.002, help="fracción de eventos repetidos (C3.5)")
    ap.add_argument("--tareas-por-archivo", type=int, help="tareas por micro-lote generado")
    args = ap.parse_args()
    tareas = args.tareas or (100_000 if args.conjunto == "principal" else 1_250_000)
    por_archivo = args.tareas_por_archivo or max(20_000, tareas // 12)

    inicio = time.perf_counter()
    resumen = generar(args.conjunto, tareas, args.semilla, por_archivo, args.duplicados)
    resumen["segundos"] = round(time.perf_counter() - inicio, 1)
    # Empieza con "_": Spark no lo lee como dato.
    (Path(resumen["carpeta"]) / "_resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=1))
    for clave, valor in resumen.items():
        print(f"  {clave:<22} {valor}")


if __name__ == "__main__":
    main()
