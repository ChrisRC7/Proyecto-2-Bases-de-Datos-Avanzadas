"""P6: ¿llegaron al lago los eventos de cada corrida, y con cuánto atraso?

    python3 pruebas/p6_lago.py evidence/P6_carga_AAAAMMDD-HHMM [otra carpeta ...]

Corre en el host: saca de los CSV del simulador la ventana de cada corrida y le pide al contenedor
historial (que monta el lago en /datalake y tiene pyarrow) que lea datalake/eventos/mes=*/.
- Conteo: eventos distintos (event_id) con ts entre la salida de la primera petición y 12 s después de la
  última respuesta, contra las respuestas 200 del simulador y las que se quedaron sin respuesta en 10 s.
- Atraso: mtime del archivo Parquet publicado menos el ts del evento (hora de la transición), para los
  eventos de la fase medida.
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

# Script que se ejecuta dentro del contenedor historial (recibe las ventanas por stdin):
# lee ts, event_id y mtime de cada Parquet del lago e imprime por corrida cuántos eventos
# llegaron y el atraso (mtime del archivo - ts del evento) en p50/p95/máx.
EN_HISTORIAL = r'''
import glob, json, os, sys
import pyarrow.parquet as pq
ventanas = json.loads(sys.stdin.read())
filas = []
for f in glob.glob("/datalake/eventos/mes=*/*.parquet"):
    m = os.stat(f).st_mtime
    t = pq.read_table(f, columns=["ts", "event_id"])
    filas += [(ts.timestamp(), m, e) for ts, e in zip(t.column("ts").to_pylist(), t.column("event_id").to_pylist())]
def pct(v, p):
    v = sorted(v); return v[min(len(v) - 1, max(0, round(p / 100 * len(v) + 0.5) - 1))]
print("corrida                             respuestas 200  sin respuesta  eventos en el lago  atraso p50 / p95 / máx. (s)")
for k, (ini, fin, a, b, ok, cero) in ventanas.items():
    ids = {e for ts, _, e in filas if ini <= ts <= fin}
    at = [m - ts for ts, m, _ in filas if a <= ts <= b]
    print(f"{k:<36} {ok:>8} {cero:>12} {len(ids):>14}      {pct(at, 50):>6.1f} / {pct(at, 95):>5.1f} / {max(at):>6.1f}")
'''


def main():
    """Calcula la ventana de cada corrida a partir de sus CSV y le pide el conteo al historial."""
    ventanas = {}
    for carpeta in map(Path, sys.argv[1:]):
        for archivo in sorted(carpeta.glob("*u_c[0-9].csv"), key=lambda p: (int(p.name.split("u_")[0]), p.name)):
            # [inicio, fin, inicio medición, fin medición, respuestas 200, sin respuesta]
            filas = list(csv.DictReader(open(archivo)))
            medidas = [float(f["ts"]) for f in filas if f["fase"] == "medicion"]
            ventanas[f"{carpeta.name}/{archivo.stem}"] = [
                min(float(f["ts"]) - float(f["ms"]) / 1000 for f in filas),  # sale la primera petición
                max(float(f["ts"]) for f in filas) + 12,                      # lo que terminó tarde
                min(medidas), max(medidas),
                sum(f["status"] == "200" for f in filas),
                sum(f["status"] == "0" for f in filas),
            ]
    subprocess.run(["docker", "compose", "exec", "-T", "historial", "python", "-c", EN_HISTORIAL],
                   input=json.dumps(ventanas), text=True, check=True)


if __name__ == "__main__":
    main()
