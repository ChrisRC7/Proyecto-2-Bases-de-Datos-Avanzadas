#!/usr/bin/env bash
# P5 (C5.2): valida Q1–Q5 contra conjunto=prueba y mide tiempos con 1 y 2 workers de Spark.
#   1. Reinicia la analítica (SparkSession nueva: la 1ª ejecución de cada consulta es en frío).
#   2. Con 1 worker: validación + tiempos en principal y grande + planes de Q1 y Q3.
#   3. Levanta spark-worker-2 (perfil spark2), reinicia la analítica y repite los tiempos.
#   4. Apaga spark-worker-2 (vuelve al estado base).
# Uso: pruebas/p5_analitica.sh            (REPS=6 por defecto; CONJUNTOS=principal,grande)
# Deja evidence/P5_analitica_<AAAAMMDD-HHMM>.{txt,csv} y _plan_q1.txt, _plan_q3.txt.
set -u
cd "$(dirname "$0")/.."

# Parámetros (se pueden cambiar con variables de entorno) y prefijo de la evidencia.
reps=${REPS:-6}
conjuntos=${CONJUNTOS:-principal,grande}
base=evidence/P5_analitica_$(date +%Y%m%d-%H%M)

# Corre pruebas/p5_analitica.py dentro del contenedor dataset.
corre() { docker compose --profile datos run --rm -T -v ./pruebas:/srv/pruebas dataset python /srv/pruebas/p5_analitica.py "$@" 2>&1 | grep -v '^ Container'; }
ejecutores() {  # cuántos ejecutores de Spark tiene registrados la analítica (0 si no responde)
  docker compose exec -T analitica python3 -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://localhost:8004/salud', timeout=3))['spark'].get('ejecutores', 0))" 2>/dev/null || echo 0
}
espera_ejecutores() {  # hasta que la SparkSession nueva tenga n ejecutores registrados
  for _ in $(seq 1 90); do
    [ "$(ejecutores)" = "$1" ] && return 0
    sleep 2
  done
  echo "la analítica no llegó a $1 ejecutores" >&2
  return 1
}

{
  echo "# P5 analítica · $(date -u +%FT%TZ) · commit $(git rev-parse --short HEAD) · REPS=$reps"
  echo; echo "## Conjuntos"
  for c in eventos eventos_grande eventos_prueba; do
    [ -f "datalake/$c/_resumen.json" ] && echo "$c: $(tr -d '\n' < "datalake/$c/_resumen.json" | grep -o '"eventos": [0-9]*'), $(find "datalake/$c" -name '*.parquet' | wc -l) archivos, $(du -sh "datalake/$c" | cut -f1)"
    [ "$c" = eventos_prueba ] && echo "$c: $(find "datalake/$c" -name '*.parquet' | wc -l) archivos (42 eventos + 1 duplicado)"
  done

  echo; echo "## 1 worker"
  docker compose --profile spark2 rm -sf spark-worker-2 >/dev/null 2>&1
  docker compose restart analitica >/dev/null 2>&1
  espera_ejecutores 1 && corre --config 1worker --validar --repeticiones "$reps" --conjuntos "$conjuntos" \
    --csv "/srv/evidence/${base#evidence/}.csv" --planes "/srv/evidence/${base#evidence/}"

  echo; echo "## 2 workers"
  docker compose --profile spark2 up -d spark-worker-2 >/dev/null 2>&1
  docker compose restart analitica >/dev/null 2>&1
  espera_ejecutores 2 && corre --config 2workers --repeticiones "$reps" --conjuntos "$conjuntos" \
    --csv "/srv/evidence/${base#evidence/}.csv"

  echo; echo "## docker stats al terminar"
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' | grep -E 'NAME|spark|analitica'
  docker compose --profile spark2 rm -sf spark-worker-2 >/dev/null 2>&1
  echo; echo "spark-worker-2 apagado (estado base: 1 worker)"
} | tee "$base.txt"
