"""Resumen de P6 (A7.2) a partir de la carpeta que deja pruebas/p6_carga.sh.

    python3 pruebas/p6_resumen.py evidence/P6_carga_AAAAMMDD-HHMM

Por nivel junta las tres corridas (solo la fase de medición): n, p50, p95 y p99 por endpoint, códigos
2xx/4xx/5xx, envíos por minuto de cada corrida, y CPU y memoria de cada contenedor según `docker stats`
en la ventana medida. Solo biblioteca estándar.
"""
import csv
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

# Nombre del endpoint de envío tal como aparece en el CSV del simulador.
ENVIAR = "POST /tareas/{id}/enviar"


def percentil(valores, p):
    """Percentil p (0-100) por rango más cercano."""
    orden = sorted(valores)  # rango más cercano, igual que simulador/simular.py
    return orden[min(len(orden) - 1, max(0, round(p / 100 * len(orden) + 0.5) - 1))]


def mib(texto):
    """Convierte un tamaño de `docker stats` ("512MiB", "1.2GiB", "300kB"...) a MiB."""
    numero, unidad = re.match(r"([\d.]+)\s*([KMG]i?B|B)", texto.strip()).groups()
    return float(numero) * {"B": 1 / 2**20, "KiB": 1 / 1024, "kB": 1 / 1024, "MiB": 1, "MB": 1,
                            "GiB": 1024, "GB": 1024}[unidad]


def main():
    """Imprime, por nivel de usuarios, las tablas de latencias, envíos/min y uso de CPU/memoria."""
    # Agrupa los CSV por nivel: "40u_c1.csv" -> nivel 40, corrida 1.
    carpeta = Path(sys.argv[1])
    corridas = defaultdict(list)
    for archivo in sorted(carpeta.glob("*u_c[0-9].csv")):
        nivel = int(archivo.name.split("u_")[0])
        corridas[nivel].append(archivo)

    for nivel in sorted(corridas):
        print(f"\n=== {nivel} usuarios virtuales ({len(corridas[nivel])} corridas de 90 s medidos)")
        juntas = defaultdict(list)
        por_corrida = []
        ventanas = []
        # Junta las filas de la fase medida de todas las corridas del nivel.
        for archivo in corridas[nivel]:
            filas = [f for f in csv.DictReader(open(archivo)) if f["fase"] == "medicion"]
            ts = [float(f["ts"]) for f in filas]
            ventanas.append((archivo, min(ts), max(ts)))
            envios = sum(1 for f in filas if f["endpoint"] == ENVIAR and f["status"] == "200")
            errores = sum(1 for f in filas if f["status"] == "0" or int(f["status"]) >= 500)
            por_corrida.append((archivo.stem, len(filas), envios, envios / 90 * 60, errores))
            for f in filas:
                juntas[f["endpoint"]].append(f)

        # Tabla de latencias por endpoint (las tres corridas juntas).
        print(f"{'endpoint':<30} {'n':>6} {'p50':>8} {'p95':>8} {'p99':>8} {'2xx':>6} {'4xx':>5} {'5xx/0':>6}")
        for endpoint, fs in sorted(juntas.items()):
            ms = [float(f["ms"]) for f in fs]
            st = [int(f["status"]) for f in fs]
            print(f"{endpoint:<30} {len(fs):>6} {percentil(ms, 50):>8.1f} {percentil(ms, 95):>8.1f} "
                  f"{percentil(ms, 99):>8.1f} {sum(200 <= s < 300 for s in st):>6} "
                  f"{sum(400 <= s < 500 for s in st):>5} {sum(s >= 500 or s == 0 for s in st):>6}")

        print("por corrida: peticiones · envíos 200 · envíos/min · errores 5xx o sin respuesta")
        for nombre, n, envios, por_min, errores in por_corrida:
            print(f"  {nombre:<8} {n:>6} · {envios:>5} · {por_min:>7.1f} · {errores}")
        tasas = [c[3] for c in por_corrida]
        print(f"envíos/min: media {statistics.mean(tasas):.1f}, mín. {min(tasas):.1f}, máx. {max(tasas):.1f}")
        print("p50 / p95 / p99 por corrida (ms):")
        for archivo, _, _ in ventanas:
            filas = [f for f in csv.DictReader(open(archivo)) if f["fase"] == "medicion"]
            partes = []
            for endpoint in sorted(juntas):
                ms = [float(f["ms"]) for f in filas if f["endpoint"] == endpoint]
                if ms:
                    nombre = endpoint.split()[1].replace("/tareas/{id}/", "").replace("/tareas/", "")
                    partes.append(f"{nombre} {percentil(ms, 50):.0f}/{percentil(ms, 95):.0f}/{percentil(ms, 99):.0f}")
            print(f"  {archivo.stem:<8} " + " · ".join(partes))

        # CPU y memoria de docker stats, solo dentro de la ventana medida de cada corrida.
        cpu, mem = defaultdict(list), defaultdict(list)
        for archivo, desde, hasta in ventanas:
            stats = carpeta / f"{archivo.stem}_stats.csv"
            if not stats.exists():
                continue
            for f in csv.DictReader(open(stats)):
                if desde <= float(f["ts"]) <= hasta and f["cpu"].endswith("%"):
                    nombre = f["contenedor"].removeprefix("proyecto2-").removesuffix("-1")
                    nombre = re.sub(r"-run-[0-9a-f]+$", "", nombre)
                    cpu[nombre].append(float(f["cpu"].rstrip("%")))
                    mem[nombre].append(mib(f["mem"].split("/")[0]))
        if cpu:
            print("docker stats en la ventana medida (CPU en % de un núcleo; memoria en MiB):")
            print(f"  {'contenedor':<16} {'muestras':>8} {'CPU media':>10} {'CPU máx.':>9} {'mem máx.':>9}")
            for nombre in sorted(cpu, key=lambda k: -statistics.mean(cpu[k])):
                print(f"  {nombre:<16} {len(cpu[nombre]):>8} {statistics.mean(cpu[nombre]):>10.1f} "
                      f"{max(cpu[nombre]):>9.1f} {max(mem[nombre]):>9.0f}")


if __name__ == "__main__":
    main()
