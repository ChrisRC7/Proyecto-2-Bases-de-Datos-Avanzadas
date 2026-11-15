#!/usr/bin/env bash
# C4.9: GET /analitica/{q1..q5}?conjunto= por el gateway (puerto 8000).
#   1. Q1–Q5 de conjunto=prueba por el gateway, comparadas con dataset/historico/esperado_prueba.json.
#   2. Tiempos de principal por el gateway: ms dentro de la analítica contra ms que ve el cliente.
#   3. Errores: 422 (consulta o conjunto inválidos), 404 (conjunto sin Parquet), 503 (analítica caída)
#      y /salud del gateway respondiendo 200 "degradado" mientras la analítica no está.
# Uso: pruebas/c49_analitica_gateway.sh   → evidence/C4.9_analitica_gateway_<AAAAMMDD-HHMM>.txt (+ .csv)
set -u
cd "$(dirname "$0")/.."

cuerpo=$(mktemp)
base=evidence/C4.9_analitica_gateway_$(date +%Y%m%d-%H%M)
p5() {
  docker compose --profile datos run --rm -T -v ./pruebas:/srv/pruebas dataset \
    python /srv/pruebas/p5_analitica.py --gateway "$@" 2>&1 | grep -v '^ Container'
}
pedir() {  # código HTTP, segundos y los primeros 160 caracteres del cuerpo
  curl -s -o "$cuerpo" -w "%{http_code} %{time_total}s " "localhost:8000$1"
  head -c 160 "$cuerpo"; echo
}
spark_listo() {
  for _ in $(seq 1 90); do
    curl -s localhost:8000/salud | python3 -c "import json,sys; sys.exit(json.load(sys.stdin)['capas']['analitica']['estado'] != 'operativo')" && return 0
    sleep 2
  done
  return 1
}

{
  echo "# C4.9 analítica por el gateway · $(date -u +%FT%TZ) · commit $(git rev-parse --short HEAD)"
  spark_listo && echo "analítica operativa según GET /salud del gateway"

  echo; echo "## 1 y 2. Validación (conjunto=prueba) y tiempos (principal, 4 ejecuciones) por el gateway"
  p5 --validar --config gateway --conjuntos principal --repeticiones 4 --csv "/srv/${base}.csv"
  python3 - "${base}.csv" <<'EOF'
import csv, statistics, sys
filas = list(csv.DictReader(open(sys.argv[1])))
extra = [float(f["ms_cliente"]) - float(f["ms"]) for f in filas]
print(f"\nms_cliente - ms (red + gateway + serialización): n={len(extra)} "
      f"mediana {statistics.median(extra):.0f} ms, mín {min(extra):.0f}, máx {max(extra):.0f}")
EOF

  echo; echo "## 3. Errores"
  echo "GET /analitica/q9 → $(pedir /analitica/q9)"
  echo "GET /analitica/q1?conjunto=otro → $(pedir '/analitica/q1?conjunto=otro')"
  mv datalake/eventos_prueba datalake/.eventos_prueba_c49
  echo "conjunto=prueba sin Parquet (carpeta movida) → $(pedir '/analitica/q1?conjunto=prueba')"
  mv datalake/.eventos_prueba_c49 datalake/eventos_prueba
  echo "conjunto=prueba restaurado → $(pedir '/analitica/q4?conjunto=prueba')"
  docker compose stop analitica >/dev/null 2>&1
  echo "analítica detenida: GET /analitica/q1 → $(pedir /analitica/q1)"
  echo "analítica detenida: GET /salud → $(curl -s -w ' %{http_code}' localhost:8000/salud | python3 -c "
import json,sys; t=sys.stdin.read(); c=t.rsplit(' ',1); d=json.loads(c[0])
print(c[1], d['estado'], {k: v['estado'] for k, v in d['capas'].items()})")"
  docker compose start analitica >/dev/null 2>&1
  spark_listo && echo "analítica operativa otra vez: GET /analitica/q4?conjunto=prueba → $(pedir '/analitica/q4?conjunto=prueba')"
} | tee "$base.txt"
