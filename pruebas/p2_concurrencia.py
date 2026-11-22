"""P2 — 50 solicitudes simultáneas contra exactamente 10 tareas libres (A7.1).

Modo gateway (prueba final): todo pasa por el gateway, como el simulador. Pide con
POST /tareas/siguiente, envía con /tareas/{id}/enviar, revisa con /tareas/{id}/revision y verifica
con la exportación. Corre en la red `borde`, que solo ve al gateway:

    docker compose --profile carga run --rm -v ./pruebas:/srv/pruebas simulador \
        python /srv/pruebas/p2_concurrencia.py --modo gateway --csv /srv/evidence/P2_...csv

Modo registro (corrida preliminar del 22/10, sin gateway): llama a POST /tareas/asignar y
/tareas/{id}/transicion del registro desde la red interna:

    docker compose --profile datos run --rm -v ./pruebas:/srv/pruebas dataset \
        python /srv/pruebas/p2_concurrencia.py --modo registro --csv /srv/evidence/P2_...csv

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

# Destino de cada modo, lugares donde buscar ids.json y anotación válida que se envía.
URLS = {
    "registro": os.getenv("REGISTRO_URL", "http://registro:8001"),
    "gateway": os.getenv("GATEWAY_URL", "http://gateway:8000"),
}
IDS = [os.getenv("IDS_JSON", ""), "/srv/ids/ids.json", "/srv/dataset/salida/ids.json", "dataset/salida/ids.json"]
GEOMETRIA = {"tipo": "cuboide_3d", "centro": [1.2, 3.4, 0.5], "dimensiones": [0.6, 1.8, 1.7], "yaw": 0.3}
ANOTACION = {"etiquetas": ["lidar#ciclista"], "geometria": GEOMETRIA}

# Variables de cada proceso hijo (las fija `iniciar`): barrera compartida y modo.
barrera = None
modo = "gateway"


def http(metodo: str, ruta: str, cuerpo=None, anotador: str | None = None):
    """Petición JSON al gateway o al registro según `modo`; devuelve (status, cuerpo)."""
    cabeceras = {"Content-Type": "application/json"}
    if anotador:
        cabeceras["X-Anotador-Id"] = anotador
    peticion = urllib.request.Request(URLS[modo] + ruta, method=metodo, headers=cabeceras,
                                      data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
    try:
        with urllib.request.urlopen(peticion, timeout=30) as r:
            texto = r.read()
            return r.status, json.loads(texto) if texto else None
    except urllib.error.HTTPError as e:
        texto = e.read()
        return e.code, json.loads(texto) if texto else None


def iniciar(b, m):
    """Inicializador de cada proceso del pool: recibe la barrera y el modo."""
    global barrera, modo
    barrera, modo = b, m


def pedir(anotador_id: str, proyecto_id: str) -> dict:
    """Un proceso: espera en la barrera (todos salen juntos), pide una tarea y devuelve el resultado."""
    if modo == "gateway":
        barrera.wait(timeout=60)  # las 50 salen a la vez
        t0 = time.time()
        status, cuerpo = http("POST", "/tareas/siguiente", {"proyecto_id": proyecto_id}, anotador_id)
        token = cuerpo["token"] if status == 200 else None
    else:
        token = str(uuid.uuid4())
        barrera.wait(timeout=60)
        t0 = time.time()
        status, cuerpo = http("POST", "/tareas/asignar",
                              {"anotador_id": anotador_id, "proyecto_id": proyecto_id, "token": token})
    return {"anotador_id": anotador_id, "token": token, "status": status, "inicio": round(t0, 6),
            "ms": round((time.time() - t0) * 1000, 1),
            "tarea_id": cuerpo["tarea"]["tarea_id"] if status == 200 else None,
            "version": cuerpo["tarea"]["version"] if status == 200 else None,
            "error": cuerpo.get("error") if isinstance(cuerpo, dict) and status >= 400 else None}


def enviar(r: dict, token: str) -> int:
    """Envía la anotación de la tarea asignada con el token dado; devuelve el código HTTP."""
    if modo == "gateway":
        return http("POST", f"/tareas/{r['tarea_id']}/enviar", {"token": token, "anotacion": ANOTACION},
                    r["anotador_id"])[0]
    return http("POST", f"/tareas/{r['tarea_id']}/transicion",
                {"accion": "enviar", "token": token, "version": r["version"], "actor_id": r["anotador_id"],
                 "anotacion": ANOTACION})[0]


def revisar(tarea_id: str, revisor: str):
    """Empieza la revisión y aprueba la tarea (para que salga en la exportación)."""
    if modo == "gateway":
        for accion in ("empezar", "aprobar"):
            http("POST", f"/tareas/{tarea_id}/revision", {"accion": accion}, revisor)
    else:
        for accion in ("empezar_revision", "aprobar"):
            http("POST", f"/tareas/{tarea_id}/transicion", {"accion": accion, "actor_id": revisor})


def main() -> int:
    """Prepara N tareas, lanza las solicitudes simultáneas y verifica la exclusividad; 0 si todo OK."""
    global modo
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--modo", choices=["gateway", "registro"], default="gateway")
    ap.add_argument("--solicitudes", type=int, default=50)
    ap.add_argument("--tareas", type=int, default=10)
    ap.add_argument("--csv", help="filas crudas por solicitud")
    args = ap.parse_args()
    modo = args.modo

    ids = json.load(open(next(p for p in IDS if p and os.path.exists(p))))
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
    print(f"Modo {modo} ({URLS[modo]}) · proyecto {proyecto} con {lote['tareas']} tareas libres · "
          f"{len(anotadores)} anotadores distintos")

    # 2) N solicitudes simultáneas, una por proceso.
    with ProcessPoolExecutor(max_workers=len(anotadores), initializer=iniciar,
                             initargs=(Barrier(len(anotadores)), modo)) as ex:
        resultados = list(ex.map(pedir, anotadores, [proyecto] * len(anotadores)))
    inicios = [r["inicio"] for r in resultados]
    print(f"Las {len(resultados)} salieron en una ventana de {(max(inicios) - min(inicios)) * 1000:.1f} ms")

    # 3) Verificación.
    estados = Counter(r["status"] for r in resultados)
    errores = Counter(r["error"] for r in resultados if r["error"])
    asignadas = [r for r in resultados if r["status"] == 200]
    por_tarea = Counter(r["tarea_id"] for r in asignadas)
    dobles = {t: n for t, n in por_tarea.items() if n > 1}

    # Para cada asignada: envío con token ajeno (409), envío bueno (200), repetido (409) y revisión.
    envios, segundos, ajenos = Counter(), Counter(), Counter()
    for r in asignadas:
        ajenos[enviar(r, str(uuid.uuid4()))] += 1
        envios[enviar(r, r["token"])] += 1
        segundos[enviar(r, r["token"])] += 1
        revisar(r["tarea_id"], revisor)

    # La exportación debe tener cada tarea con el anotador que realmente la tomó.
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
    if errores:
        print(f"Errores: {dict(errores)}")
    print(f"Enviar con token ajeno: {dict(ajenos)} · primer envío: {dict(envios)} · segundo envío: {dict(segundos)}")
    # Lista de comprobaciones finales (texto, resultado).
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
