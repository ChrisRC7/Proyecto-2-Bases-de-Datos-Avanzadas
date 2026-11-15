"""P5 — Q1–Q5 sobre Parquet: validación con casos conocidos y tiempos (C5.2).

Lo llama pruebas/p5_analitica.sh, que reinicia la analítica (primera ejecución en frío) y cambia
entre 1 y 2 workers. Solo HTTP a la analítica desde la red interna:

    docker compose --profile datos run --rm -T -v ./pruebas:/srv/pruebas dataset \
        python /srv/pruebas/p5_analitica.py --validar --config 1worker --csv /srv/evidence/P5_...csv

--validar  compara Q1–Q5 de conjunto=prueba con dataset/historico/esperado_prueba.json
--planes   guarda explain("formatted") de Q1 y Q3 (conjunto principal) en <prefijo>_plan_q{1,3}.txt
--gateway  pasa por GET gateway:/analitica/q{n} (C4.9) en vez de ir directo a analitica:/q/{n}
Tiempos: por cada conjunto y consulta, --repeticiones ejecuciones seguidas (la 1ª es la primera
del proceso para esa consulta y conjunto); una fila por ejecución en el CSV.
"""
import argparse
import csv
import json
import os
import statistics
import time
import urllib.error
import urllib.request

URL = os.getenv("ANALITICA_URL", "http://analitica:8004")
RUTA_Q = "/q/{n}?conjunto={conjunto}"
ESPERADO = "/srv/dataset/historico/esperado_prueba.json"
TOLERANCIA = 1e-3


def get(ruta: str, timeout: float = 900):
    try:
        with urllib.request.urlopen(URL + ruta, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def igual(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return abs(float(a) - float(b)) <= TOLERANCIA
    return a == b


def validar() -> bool:
    esperado = json.load(open(ESPERADO))
    todo_bien = True
    print("\n## Validación contra conjunto=prueba (esperado calculado a mano)")
    for n in range(1, 6):
        q = f"q{n}"
        status, d = get(RUTA_Q.format(n=n, conjunto="prueba"))
        filas, esp = d["filas"], esperado[q]["filas"]
        if q == "q3":  # el orden (media desc) es parte del resultado
            ok = len(filas) == len(esp) and all(
                all(igual(f[k], e[k]) for k in e) for f, e in zip(filas, esp))
        else:
            clave = lambda f: tuple(str(f[k]) for k in list(esp[0])[:2])  # noqa: E731
            por_clave = {clave(f): f for f in filas}
            ok = len(filas) == len(esp) and all(
                clave(e) in por_clave and all(igual(por_clave[clave(e)][k], e[k]) for k in e) for e in esp)
        if "parametros" in esperado[q]:
            ok = ok and all(d["parametros"][k] == v for k, v in esperado[q]["parametros"].items())
        todo_bien &= ok
        print(f"{q}: {'OK' if ok else 'DIFERENTE'} · status {status} · {len(filas)} filas · {d['ms']} ms")
        print(f"  obtenido: {json.dumps(filas, ensure_ascii=False)}")
        if not ok:
            print(f"  esperado: {json.dumps(esp, ensure_ascii=False)}")
    return todo_bien


def planes(prefijo: str) -> None:
    for n in (1, 3):
        status, d = get(f"/plan/{n}?conjunto=principal")
        with open(f"{prefijo}_plan_q{n}.txt", "w") as f:
            f.write(f"# explain('formatted') de Q{n} · conjunto principal · parametros {d.get('parametros')}\n\n")
            f.write(d["plan"])
        print(f"plan q{n}: {prefijo}_plan_q{n}.txt")


def tiempos(config: str, conjuntos: list[str], reps: int, ruta_csv: str) -> None:
    nuevo = not os.path.exists(ruta_csv)
    with open(ruta_csv, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "config", "conjunto", "consulta", "rep", "ms", "ms_cliente",
                                          "primera_ejecucion", "ejecutores", "filas", "status"])
        if nuevo:
            w.writeheader()
        print(f"\n## Tiempos · {config} · {reps} ejecuciones por consulta (ms medidos por la analítica)")
        print("| conjunto | consulta | 1ª ejecución | siguientes: mediana | mín | máx | filas |")
        print("|---|---|---:|---:|---:|---:|---:|")
        for conjunto in conjuntos:
            for n in range(1, 6):
                medidos = []
                for rep in range(1, reps + 1):
                    t0 = time.perf_counter()
                    status, d = get(RUTA_Q.format(n=n, conjunto=conjunto))
                    ms_cliente = round((time.perf_counter() - t0) * 1000, 1)
                    if status != 200:
                        print(f"| {conjunto} | q{n} | error {status}: {d} |")
                        break
                    w.writerow({"ts": round(time.time(), 3), "config": config, "conjunto": conjunto,
                                "consulta": f"q{n}", "rep": rep, "ms": d["ms"], "ms_cliente": ms_cliente,
                                "primera_ejecucion": d["primera_ejecucion"], "ejecutores": d["ejecutores"],
                                "filas": len(d["filas"]), "status": status})
                    f.flush()
                    medidos.append((d["ms"], len(d["filas"])))
                if len(medidos) > 1:
                    resto = [m for m, _ in medidos[1:]]
                    print(f"| {conjunto} | q{n} | {medidos[0][0]:.0f} | {statistics.median(resto):.0f} | "
                          f"{min(resto):.0f} | {max(resto):.0f} | {medidos[0][1]} |")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="etiqueta, p. ej. 1worker / 2workers")
    ap.add_argument("--csv", help="sin --csv solo valida (y guarda planes)")
    ap.add_argument("--repeticiones", type=int, default=6)
    ap.add_argument("--conjuntos", default="principal,grande")
    ap.add_argument("--validar", action="store_true")
    ap.add_argument("--planes", help="prefijo de los archivos de plan")
    ap.add_argument("--gateway", action="store_true")
    args = ap.parse_args()

    global URL, RUTA_Q
    if args.gateway:
        URL = os.getenv("GATEWAY_URL", "http://gateway:8000")
        RUTA_Q = "/analitica/q{n}?conjunto={conjunto}"
        status, salud = get("/salud", timeout=10)
        salud = salud["capas"]["analitica"]["detalle"]
    else:
        status, salud = get("/salud", timeout=10)
    print(f"analitica: {json.dumps(salud['spark'], ensure_ascii=False)}")
    ok = validar() if args.validar else True
    if args.csv:
        tiempos(args.config, args.conjuntos.split(","), args.repeticiones, args.csv)
    if args.planes:
        planes(args.planes)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
