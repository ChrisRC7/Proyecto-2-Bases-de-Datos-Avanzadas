#!/usr/bin/env bash
# C2.6: compactación de un mes del lago principal. Cuenta archivos, filas y event_id distintos del mes antes
# y después, y compara Q1–Q5 (por el gateway) antes y después: deben dar lo mismo.
# Uso: pruebas/c26_compactacion.sh [AAAA-MM]   (por defecto el mes actual, donde escribe el historial)
#   → evidence/C2.6_compactacion_<AAAAMMDD-HHMM>.txt
set -u
cd "$(dirname "$0")/.."

mes=${1:-$(date -u +%Y-%m)}
base=evidence/C2.6_compactacion_$(date +%Y%m%d-%H%M)
dir=$(mktemp -d)

mes_lago() {  # archivos, filas y event_id distintos de la carpeta del mes (lo lee el historial)
  docker compose exec -T historial python -c "
import glob, os, pyarrow.parquet as pq
a = sorted(glob.glob('/datalake/eventos/mes=$mes/*.parquet'))
ids = [e for f in a for e in pq.read_table(f, columns=['event_id']).column('event_id').to_pylist()]
print(f'mes=$mes: {len(a)} archivos, {sum(os.path.getsize(f) for f in a) / 1e6:.2f} MB, {len(ids)} filas, {len(set(ids))} event_id distintos')
print('  ' + ', '.join(os.path.basename(f) for f in a[:3]) + (' ...' if len(a) > 3 else ''))"
}
consultas() {  # Q1–Q5 por el gateway, filas ordenadas, en $dir/$1.json
  python3 - "$dir/$1.json" <<'EOF'
import json, sys, urllib.request
r = {}
for n in range(1, 6):
    d = json.load(urllib.request.urlopen(f"http://localhost:8000/analitica/q{n}?conjunto=principal", timeout=300))
    r[f"q{n}"] = {"parametros": d["parametros"], "filas": sorted(json.dumps(f, sort_keys=True) for f in d["filas"])}
    print(f"q{n}: {len(d['filas'])} filas, {d['ms']} ms")
json.dump(r, open(sys.argv[1], "w"))
EOF
}

{
  echo "# C2.6 compactación de mes=$mes (conjunto principal) · $(date -u +%FT%TZ) · commit $(git rev-parse --short HEAD)"
  echo; echo "## Antes"
  docker compose exec -T historial python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://localhost:8003/flush', method='POST'), timeout=30)"
  mes_lago
  consultas antes
  echo; echo "## Simulación (sin --ejecutar)"
  docker compose exec -T historial python -m app.compactar --mes "$mes"
  echo; echo "## Compactación"
  /usr/bin/time -f "tiempo: %e s" docker compose exec -T historial python -m app.compactar --mes "$mes" --ejecutar
  echo; echo "## Después"
  mes_lago
  consultas despues
  python3 - "$dir" <<'EOF'
import json, sys
a, d = (json.load(open(f"{sys.argv[1]}/{x}.json")) for x in ("antes", "despues"))
for q in a:
    print(f"{q}: {'igual' if a[q] == d[q] else 'DIFERENTE'} antes y después")
EOF
} 2>&1 | tee "$base.txt"
rm -rf "$dir"
