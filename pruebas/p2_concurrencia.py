"""P2 — 50 solicitudes simultáneas contra exactamente 10 tareas libres (A7.1).

Modo registro (corrida preliminar, sin gateway): llama a POST /tareas/asignar del registro desde
la red interna, que es donde está el findOneAndUpdate atómico. Cuando estén las rutas del gateway
(B3.4 y B3.6) la prueba final se repite contra /tareas/siguiente y /tareas/{id}/enviar.

    docker compose --profile datos run --rm -v ./pruebas:/srv/pruebas dataset \
        python /srv/pruebas/p2_concurrencia.py --csv /srv/evidence/P2_...csv

Usa los ids de dataset/salida/ids.json (archivo, no la base) y solo HTTP.
"""
import argparse
import csv
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import Barrier

URL = os.getenv("REGISTRO_URL", "http://registro:8001")
IDS = "/srv/dataset/salida/ids.json"
GEOMETRIA = {"tipo": "cuboide_3d", "centro": [1.2, 3.4, 0.5], "dimensiones": [0.6, 1.8, 1.7], "yaw": 0.3}

barrera = None


def http(metodo: str, ruta: str, cuerpo=None):
    peticion = urllib.request.Request(URL + ruta, method=metodo, headers={"Content-Type": "application/json"},
                                      data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
    try:
        with urllib.request.urlopen(peticion, timeout=30) as r:
            texto = r.read()
            return r.status, json.loads(texto) if texto else None
    except urllib.error.HTTPError as e:
        texto = e.read()
        return e.code, json.loads(texto) if texto else None


def iniciar(b):
    global barrera
    barrera = b


def pedir(anotador_id: str, proyecto_id: str) -> dict:
    token = str(uuid.uuid4())
    barrera.wait(timeout=60)  # las 50 salen a la vez
    t0 = time.time()
    status, cuerpo = http("POST", "/tareas/asignar",
                          {"anotador_id": anotador_id, "proyecto_id": proyecto_id, "token": token})
    return {"anotador_id": anotador_id, "token": token, "status": status, "inicio": round(t0, 6),
            "ms": round((time.time() - t0) * 1000, 1),
            "tarea_id": cuerpo["tarea"]["tarea_id"] if status == 200 else None,
            "version": cuerpo["tarea"]["version"] if status == 200 else None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--solicitudes", type=int, default=50)
    ap.add_argument("--tareas", type=int, default=10)
    ap.add_argument("--csv", help="filas crudas por solicitud")
    args = ap.parse_args()

    ids = json.load(open(IDS))
    anotadores = [a["id"] for a in ids["anotadores"] if a["rol"] == "anotador"][:args.solicitudes]
    revisor = next(a["id"] for a in ids["anotadores"] if a["rol"] == "revisor")

    # 1) Exactamente N tareas libres: un proyecto nuevo con un solo lote de N tareas.
    s, p = http("POST", "/proyectos", {
        "cliente_id": ids["clientes"][0], "nombre": f"P2 concurrencia {time.strftime('%Y%m%d-%H%M%S')}",
        "modalidad": "lidar", "sla_horas": 48,
        "taxonomia": [{"etiqueta": "lidar#ciclista", "instruccion": "Un skater se clasifica como ciclista."}]})
    assert s == 201, (s, p)
    proyecto = p["proyecto_id"]
    tareas = [{"tipo_tarea": "cuboide_3d", "archivo": {
        "uri": f"s3://datos/lidar/p2/escena-{i:04d}.pcd", "sha256": f"{i:064x}", "bytes": 1000 + i,
        "mime": "application/x-pcd", "meta": {"min": 0.0, "max": 100.0}}} for i in range(args.tareas)]
    s, lote = http("POST", f"/proyectos/{proyecto}/lotes", {"tareas": tareas})
    assert s == 201 and lote["tareas"] == args.tareas, (s, lote)
    print(f"Proyecto {proyecto} con {lote['tareas']} tareas libres · {len(anotadores)} anotadores distintos")

    # 2) N solicitudes simultáneas, una por proceso.
    with ProcessPoolExecutor(max_workers=len(anotadores), initializer=iniciar,
                             initargs=(Barrier(len(anotadores)),)) as ex:
        resultados = list(ex.map(pedir, anotadores, [proyecto] * len(anotadores)))
    inicios = [r["inicio"] for r in resultados]
    print(f"Las {len(resultados)} salieron en una ventana de {(max(inicios) - min(inicios)) * 1000:.1f} ms")

    # 3) Verificación.
    estados = Counter(r["status"] for r in resultados)
    asignadas = [r for r in resultados if r["status"] == 200]
    por_tarea = Counter(r["tarea_id"] for r in asignadas)
    dobles = {t: n for t, n in por_tarea.items() if n > 1}

    envios, segundos, ajenos = Counter(), Counter(), Counter()
    otro_token = str(uuid.uuid4())
    for r in asignadas:
        cuerpo = {"accion": "enviar", "token": r["token"], "version": r["version"], "actor_id": r["anotador_id"],
                  "anotacion": {"etiquetas": ["lidar#ciclista"], "geometria": GEOMETRIA}}
        ajenos[http("POST", f"/tareas/{r['tarea_id']}/transicion", {**cuerpo, "token": otro_token})[0]] += 1
        envios[http("POST", f"/tareas/{r['tarea_id']}/transicion", cuerpo)[0]] += 1
        segundos[http("POST", f"/tareas/{r['tarea_id']}/transicion", cuerpo)[0]] += 1
        for accion in ("empezar_revision", "aprobar"):
            http("POST", f"/tareas/{r['tarea_id']}/transicion", {"accion": accion, "actor_id": revisor})

    s, exportado = http("GET", f"/proyectos/{proyecto}/exportar?limite=100")
    dueño = {r["tarea_id"]: r["anotador_id"] for r in asignadas}
    coinciden = sum(1 for t in exportado["tareas"] if dueño.get(t["tarea_id"]) == t["anotacion"]["anotador_id"])

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(resultados[0]))
            w.writeheader()
            w.writerows(resultados)

    lat = sorted(r["ms"] for r in resultados)
    print(f"Respuestas: {dict(sorted(estados.items()))} · latencia p50={lat[len(lat) // 2]} ms max={lat[-1]} ms")
    chequeos = [
        (f"{args.tareas} asignaciones (200)", estados[200] == args.tareas),
        (f"{len(anotadores) - args.tareas} sin tarea (204)", estados[204] == len(anotadores) - args.tareas),
        ("ninguna tarea con dos anotadores", not dobles and len(por_tarea) == estados[200]),
        ("enviar con token ajeno -> 409", ajenos == Counter({409: estados[200]})),
        ("primer envío -> 200", envios == Counter({200: estados[200]})),
        ("segundo envío de una tarea ya enviada -> 409", segundos == Counter({409: estados[200]})),
        (f"exportación: {len(exportado['tareas'])} aprobadas, cada una con el anotador que la tomó",
         len(exportado["tareas"]) == args.tareas and coinciden == args.tareas),
    ]
    for texto, bien in chequeos:
        print(("OK:   " if bien else "FALLÓ: ") + texto)
    return 0 if all(b for _, b in chequeos) else 1


if __name__ == "__main__":
    raise SystemExit(main())
