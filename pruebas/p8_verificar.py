"""Verifica una corrida de P8 contra MongoDB: RPO sobre lo confirmado, RTO y «error ≠ no escrito».

- Confirmado: un envío que respondió 200. RPO = confirmados que ya no están en `anotada` (o después).
- «Una sola vez»: cada envío confirmado dejó la tarea en la versión esperada (la de la asignación + 1);
  si se hubiera aplicado dos veces, la versión sería mayor.
- Desconocido: un envío con error (503 o sin respuesta). Se mira en la base si quedó escrito.
- Asignación con error que sí quedó escrita: tarea `abierta` a nombre de la sonda que nunca recibió
  respuesta 200. Queda así hasta que vence su plazo (120 s) y vuelve a ofrecerse.
- RTO: desde la marca de la falla hasta el primer envío 200 después del último error (filas del CSV).

Lee la base solo para verificar; no es parte de la plataforma.
"""
import argparse
import csv
import json
import os

from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo1:27017,mongo2:27017,mongo3:27017/?replicaSet=rs0")
IDS = "/srv/dataset/salida/ids.json"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", required=True)
    ap.add_argument("--marca", type=float, help="epoch de la falla (docker kill)")
    ap.add_argument("--recuperacion", type=float, help="epoch en que se volvieron a encender los nodos")
    args = ap.parse_args()

    filas = list(csv.DictReader(open(args.csv)))
    envios = [f for f in filas if f["op"] == "enviar"]
    confirmados = {f["tarea_id"] for f in envios if f["status"] == "200"}
    desconocidos = {f["tarea_id"] for f in envios if f["status"] != "200"} - confirmados
    asignadas_ok = {f["tarea_id"] for f in filas if f["op"] == "asignar" and f["status"] == "200"}
    asignadas_error = [f for f in filas if f["op"] == "asignar" and f["status"] not in ("200", "204")]
    sonda = next(a["id"] for a in json.load(open(IDS))["anotadores"] if a["rol"] == "anotador")

    bd = MongoClient(MONGO_URI, readPreference="primary")["plataforma"]
    docs = {d["_id"]: d for d in bd.tareas.find({"_id": {"$in": list(confirmados | desconocidos)}},
                                                 {"estado": 1, "version": 1, "anotacion": 1})}
    escrita = {t for t, d in docs.items() if d["estado"] in ("anotada", "en_revision", "aprobada") and d.get("anotacion")}
    perdidos = confirmados - escrita
    esperada = {f["tarea_id"]: int(f["version"]) for f in envios if f["status"] == "200"}
    dobles = [t for t in confirmados & escrita if docs[t]["version"] != esperada[t]]
    asignadas_sin_respuesta = bd.tareas.count_documents(
        {"asignacion.anotador_id": sonda, "estado": "abierta", "_id": {"$nin": list(asignadas_ok)}})

    print(f"filas: {len(filas)} · envíos: {len(envios)} · asignaciones con error: {len(asignadas_error)}")
    print(f"envíos confirmados (200): {len(confirmados)} · presentes en MongoDB: {len(confirmados & escrita)}")
    print(f"RPO (confirmados perdidos): {len(perdidos)}")
    print(f"confirmados con la versión esperada (aplicados una sola vez): {len(confirmados & escrita) - len(dobles)}"
          f" de {len(confirmados & escrita)}")
    print(f"envíos con error: {len(desconocidos)} · de esos SÍ quedaron escritos: {len(desconocidos & escrita)}"
          f" · no escritos: {len(desconocidos - escrita)}")
    print(f"asignaciones con error que SÍ quedaron escritas (tarea abierta a nombre de la sonda): {asignadas_sin_respuesta}")

    if not args.marca:
        return
    antes = [f for f in envios if float(f["ts"]) < args.marca and f["status"] == "200"]
    despues = [f for f in filas if float(f["ts"]) >= args.marca]
    errores = [f for f in despues if f["status"] not in ("200", "204")]
    print(f"\nmarca de la falla: {args.marca:.3f}")
    if antes:
        print(f"último envío 200 antes de la falla: {antes[-1]}")
    if not errores:
        primero = next((f for f in despues if f["op"] == "enviar" and f["status"] == "200"), None)
        print("sin errores después de la marca: la escritura no se interrumpió (no aplica RTO)")
        if primero:
            print(f"primer envío 200 después de la marca: {primero}")
    else:
        print(f"primer error: {errores[0]}")
        print(f"último error: {errores[-1]}")
        print(f"errores después de la marca: {len(errores)} "
              f"({', '.join(sorted({f['status'] + ' ' + f['error'] for f in errores}))})")
        ok = next((f for f in despues if f["op"] == "enviar" and f["status"] == "200"
                   and float(f["ts"]) > float(errores[-1]["ts"])), None)
        if ok:
            ts = float(ok["ts"])
            print(f"primer envío 200 después del último error: {ok}")
            print(f"RTO = {ts:.3f} - {args.marca:.3f} = {ts - args.marca:.3f} s")
            if args.recuperacion and ts > args.recuperacion:  # solo si hubo que esperar a los nodos
                print(f"desde que se encendieron los nodos ({args.recuperacion:.3f}) hasta ese envío: "
                      f"{ts - args.recuperacion:.3f} s")
    if args.recuperacion:
        print(f"nodos encendidos de nuevo {args.recuperacion - args.marca:.1f} s después de la falla")


if __name__ == "__main__":
    main()
