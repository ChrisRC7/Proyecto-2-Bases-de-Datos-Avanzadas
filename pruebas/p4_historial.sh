#!/usr/bin/env bash
# P4 (C5.1): historial y lago Parquet. Corrida preliminar sin gateway (el script publica los eventos
# del registro en el historial, como hará publicar_evento de B3.3).
#   0. Estado conocido: MongoDB recargado, conjunto principal regenerado, cola-eventos vacía.
#   1. Frescura: segundos entre el 200 del registro (aprobar) y la fila visible en Q1 de Spark,
#      esperando el cierre del micro-lote (5 s) y con POST /flush.
#   2. Rechazo: fila "rechazada" en Parquet y "disponible" en MongoDB; reenvío del mismo evento.
#   3. Spark leyendo en bucle mientras se escriben 20 000 eventos; reprocesar un micro-lote.
#   4. docker kill del historial con un micro-lote abierto; al volver no se pierde ni se repite nada.
# Uso: pruebas/p4_historial.sh   → evidence/P4_historial_<AAAAMMDD-HHMM>.txt (+ _frescura.csv, _lector.csv...)
set -u
cd "$(dirname "$0")/.."

# Prefijo de la evidencia en el host y dentro de los contenedores.
base=evidence/P4_historial_$(date +%Y%m%d-%H%M)
prefijo=/srv/evidence/${base#evidence/}
fase() {  # corre una fase de pruebas/p4_historial.py en el contenedor dataset (lago en solo lectura)
  docker compose --profile datos run --rm -T -v ./pruebas:/srv/pruebas -v ./datalake:/datalake:ro dataset \
    python /srv/pruebas/p4_historial.py "$@" --prefijo "$prefijo" 2>&1 | grep -v '^ Container'
}
sano() {  # espera a que el contenedor del servicio esté healthy
  for _ in $(seq 1 60); do
    [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q "$1")" 2>/dev/null)" = healthy ] && return 0
    sleep 1
  done
  return 1
}
spark_listo() {  # espera hasta 180 s a que la analítica tenga su SparkSession operativa
  for _ in $(seq 1 90); do
    docker compose exec -T analitica python3 -c "import json,urllib.request,sys; sys.exit(json.load(urllib.request.urlopen('http://localhost:8004/salud', timeout=3))['estado'] != 'operativo')" 2>/dev/null && return 0
    sleep 2
  done
  return 1
}

{
  echo "# P4 historial · $(date -u +%FT%TZ) · commit $(git rev-parse --short HEAD)"
  echo; echo "## 0. Estado conocido"
  docker compose up -d >/dev/null 2>&1
  sano registro && sano historial
  docker compose --profile datos run --rm dataset 2>&1 | grep -E 'Semilla|tareas  '
  docker compose --profile datos run --rm dataset-historico 2>&1 | grep -E ' (eventos|archivos|duplicados_inyectados) '
  docker compose exec -T cola-eventos valkey-cli FLUSHALL
  docker compose restart historial analitica >/dev/null 2>&1
  sano historial && spark_listo && echo "historial y analítica listos"

  echo; echo "## 1. Frescura (HTTP 200 del registro -> fila visible en Q1 de Spark)"
  fase frescura --repeticiones 5

  echo; echo "## 2. Fila rechazada en Parquet vs disponible en MongoDB"
  fase rechazo

  echo; echo "## 3. Spark leyendo en bucle mientras el historial escribe; reprocesar un micro-lote"
  docker compose run --rm --no-deps -T -v ./pruebas:/srv/pruebas analitica \
    python3 /srv/pruebas/p4_lector.py --segundos 110 2>/dev/null > "${base}_lector.csv" &
  lector=$!  # PID del lector en segundo plano
  sleep 25  # vueltas de referencia antes de escribir
  echo "inicio de escritura: $(date +%s.%N | cut -c1-14)"
  fase carga --eventos 20000 --pausa 0.05
  echo "fin de escritura: $(date +%s.%N | cut -c1-14)"
  fase reprocesar
  wait "$lector"
  # Resume el CSV del lector: vueltas, errores, rango de filas y si alguna vez bajó la cuenta.
  awk -F, 'NR > 1 { n++; if ($7 != "") e++; else { if (min == "" || $3 < min) min = $3; if ($3 > max) max = $3;
                    if (prev != "" && $3 < prev) baja++; prev = $3; ms += $6 } }
           END { printf "lector: %d vueltas, %d con error, filas de %d a %d, %d veces bajó la cuenta, %.0f ms por vuelta\n",
                 n, e, min, max, baja, (n > e ? ms / (n - e) : 0) }' "${base}_lector.csv"

  echo; echo "## 4. Caída del historial con un micro-lote abierto"
  fase caida-antes --eventos 300
  echo "docker kill historial: $(date +%s.%N | cut -c1-14)"
  docker compose kill historial >/dev/null 2>&1
  docker compose start historial >/dev/null 2>&1
  sano historial && echo "historial healthy: $(date +%s.%N | cut -c1-14)"
  docker compose logs --no-log-prefix historial 2>/dev/null | grep '"recuperacion"' | tail -1
  fase caida-despues
} | tee "$base.txt"
