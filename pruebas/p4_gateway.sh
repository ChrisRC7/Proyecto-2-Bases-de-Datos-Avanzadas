#!/usr/bin/env bash
# P4 (C5.1) por el flujo real: simulador / script -> gateway -> registro -> cola del gateway (SQLite)
# -> historial -> cola-eventos -> micro-lote Parquet -> Spark (GET gateway:/analitica).
#   0. Estado conocido: MongoDB recargado, conjunto principal regenerado, colas vacías.
#   1. Frescura: segundos entre el 200 del gateway (aprobar) y la fila visible en Q1 pedida al gateway.
#   2. Rechazo: fila "rechazada" en Parquet, "disponible" en MongoDB, segundo envío 409 sin fila nueva
#      y Q4 la cuenta (I.3).
#   3. Spark leyendo en bucle mientras el simulador (10 usuarios, 60 s) genera eventos por el gateway;
#      reprocesar un micro-lote.
#   4. docker kill del historial en medio de la carga del simulador: el gateway guarda los eventos y los
#      entrega cuando el historial vuelve; se cuentan contra las respuestas 200 del simulador.
# Uso: pruebas/p4_gateway.sh   → evidence/P4_gateway_<AAAAMMDD-HHMM>.txt y carpeta del mismo nombre
set -u
cd "$(dirname "$0")/.."

base=evidence/P4_gateway_$(date +%Y%m%d-%H%M)
mkdir -p "$base"
prefijo=/srv/$base/p4
fase() {
  docker compose --profile datos run --rm -T -v ./pruebas:/srv/pruebas -v ./datalake:/datalake:ro dataset \
    python /srv/pruebas/p4_historial.py "$@" --prefijo "$prefijo" 2>&1 | grep -v '^ Container'
}
simulador() {  # $1 = archivo CSV dentro de $base; resto = argumentos del simulador
  local csv=$1; shift
  docker compose --profile carga run --rm -T simulador python -m simulador.simular \
    --usuarios 10 --calentamiento 0 --csv "/srv/$base/$csv" "$@" 2>&1 | grep -v '^ Container' | tail -6
}
sano() {
  for _ in $(seq 1 60); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q "$1")" 2>/dev/null)" = healthy ] && return 0
    sleep 1
  done
  return 1
}
spark_listo() {
  for _ in $(seq 1 90); do
    curl -s localhost:8000/salud | python3 -c "import json,sys; sys.exit(json.load(sys.stdin)['capas']['analitica']['estado'] != 'operativo')" 2>/dev/null && return 0
    sleep 2
  done
  return 1
}
pendientes_gateway() {
  docker compose exec -T gateway python -c "import sqlite3; print(sqlite3.connect('/srv/pendientes/eventos.sqlite3').execute('select count(*) from pendientes').fetchone()[0])"
}
limpiar_valkey() {  # no toca salud:* (mensajes de las capas)
  for patron in 'lock:tarea:*' 'sesion:*' 'borrador:*' 'editando:*' 'pendiente:*'; do
    docker compose exec -T valkey valkey-cli --scan --pattern "$patron" \
      | xargs -r -n 500 docker compose exec -T valkey valkey-cli DEL >/dev/null
  done
}

{
  echo "# P4 por el gateway · $(date -u +%FT%TZ) · commit $(git rev-parse --short HEAD)"
  echo "Máquina: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs) · $(nproc) CPU · $(free -g | awk '/Mem:/ {print $2}') GiB"
  echo; echo "## 0. Estado conocido"
  docker compose up -d >/dev/null 2>&1
  sano registro && sano historial && sano gateway
  docker compose --profile datos run --rm dataset 2>&1 | grep -E 'Semilla|tareas  '
  docker compose --profile datos run --rm dataset-historico 2>&1 | grep -E ' (eventos|archivos|duplicados_inyectados) '
  docker compose exec -T cola-eventos valkey-cli FLUSHALL
  limpiar_valkey
  docker compose restart historial analitica >/dev/null 2>&1
  sano historial && spark_listo && echo "historial y analítica listos; eventos pendientes en el gateway: $(pendientes_gateway)"

  echo; echo "## 1. Frescura (HTTP 200 del gateway -> fila visible en Q1 por GET /analitica/q1)"
  fase frescura --gateway --repeticiones 5

  echo; echo "## 2. Fila rechazada en Parquet vs disponible en MongoDB, segundo envío y Q4"
  fase rechazo --gateway

  echo; echo "## 3. Spark leyendo en bucle mientras el simulador escribe por el gateway; reprocesar un micro-lote"
  limpiar_valkey
  docker compose run --rm --no-deps -T -v ./pruebas:/srv/pruebas analitica \
    python3 /srv/pruebas/p4_lector.py --segundos 120 2>/dev/null > "$base/lector.csv" &
  lector=$!
  sleep 25  # vueltas de referencia antes de escribir
  echo "inicio del simulador: $(date +%s.%N | cut -c1-14)"
  simulador 10u_c1.csv --duracion 60
  echo "fin del simulador: $(date +%s.%N | cut -c1-14) · pendientes en el gateway: $(pendientes_gateway)"
  wait "$lector"
  awk -F, 'NR > 1 { n++; if ($7 != "") e++; else { if (min == "" || $3 < min) min = $3; if ($3 > max) max = $3;
                    if (prev != "" && $3 < prev) baja++; prev = $3; ms += $6 } }
           END { printf "lector: %d vueltas, %d con error, filas de %d a %d, %d veces bajó la cuenta, %.0f ms por vuelta\n",
                 n, e, min, max, baja, (n > e ? ms / (n - e) : 0) }' "$base/lector.csv"
  fase reprocesar

  echo; echo "## 4. Caída del historial en medio de la carga del simulador"
  limpiar_valkey
  simulador 10u_c2.csv --duracion 45 > "$base/sim_caida.txt" &
  sim=$!
  sleep 15
  echo "docker kill historial: $(date +%s.%N | cut -c1-14) · pendientes en el gateway: $(pendientes_gateway)"
  docker compose kill historial >/dev/null 2>&1
  sleep 15
  echo "pendientes en el gateway con el historial caído (15 s): $(pendientes_gateway)"
  curl -s localhost:8000/salud | python3 -c "import json,sys; d=json.load(sys.stdin); print('GET /salud del gateway:', d['estado'], {k: v['estado'] for k, v in d['capas'].items()})"
  docker compose start historial >/dev/null 2>&1
  sano historial && echo "historial healthy: $(date +%s.%N | cut -c1-14)"
  wait "$sim"
  cat "$base/sim_caida.txt"
  for _ in $(seq 1 60); do [ "$(pendientes_gateway)" = 0 ] && break; sleep 2; done
  echo "pendientes en el gateway al final: $(pendientes_gateway)"
  docker compose exec -T historial python -c "import urllib.request; urllib.request.urlopen(urllib.request.Request('http://localhost:8003/flush', method='POST'), timeout=30)"
  docker compose logs --no-log-prefix historial 2>/dev/null | grep '"recuperacion"' | tail -1
  echo; echo "Respuestas 200 del simulador contra eventos distintos en el lago (pruebas/p6_lago.py):"
  python3 pruebas/p6_lago.py "$base"
} | tee "$base.txt"
