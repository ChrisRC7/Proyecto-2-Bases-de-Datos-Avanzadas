#!/usr/bin/env bash
# I.5 — todas las pruebas en orden desde un entorno limpio, con los comandos del README.
#   0. docker compose down -v (borra MongoDB, Valkey, la cola de eventos y la cola del gateway; el lago es
#      una carpeta del repo y se regenera en el paso 1) y docker compose up -d --build.
#   1. Carga de datos: MongoDB, lago principal y conjunto de prueba (el conjunto grande se regenera solo si
#      falta, porque tarda ~3 min y pesa 553 MB).
#   2. Las pruebas de la tabla del README, una detrás de otra; cada una deja su archivo en evidence/.
# Tarda en total una hora y media aproximadamente (P6 ~20 min, P5 ~15 min, P4 ~8 min, P8 ~10 min).
# Uso: pruebas/i5_todas.sh   → evidence/I5_entorno_limpio_<AAAAMMDD-HHMM>.txt (resumen con el código de
#      salida y la duración de cada prueba)
set -u
cd "$(dirname "$0")/.."
resumen=evidence/I5_entorno_limpio_$(date +%Y%m%d-%H%M).txt
log=${resumen%.txt}.log

operativo() {
  for _ in $(seq 1 90); do
    curl -s -m 5 localhost:8000/salud | grep -q '^{"servicio":"gateway","estado":"operativo"' && return 0
    sleep 2
  done
  return 1
}
paso() {  # $1 nombre; resto: comando
  local nombre=$1; shift
  local inicio=$SECONDS
  echo "===== $nombre · $(date -u +%T) · $*" >> "$log"
  "$@" >> "$log" 2>&1
  local codigo=$?
  printf '%-58s exit=%-3s %5d s\n' "$nombre" "$codigo" $((SECONDS - inicio)) | tee -a "$resumen"
  operativo || echo "  (el sistema no volvió a operativo después de $nombre)" | tee -a "$resumen"
}
dataset() { docker compose --profile datos run --rm dataset; }
historico() { docker compose --profile datos run --rm dataset-historico "$@"; }
huerfanos() {
  docker compose cp pruebas/prueba_borradores_huerfanos.py sesion:/tmp/h.py && docker compose exec -T -e PYTHONPATH=/srv sesion python /tmp/h.py
}
p2() {
  docker compose --profile carga run --rm -v ./pruebas:/srv/pruebas simulador \
    python /srv/pruebas/p2_concurrencia.py --modo gateway --csv "/srv/evidence/P2_concurrencia_gateway_$(date +%Y%m%d-%H%M).csv"
}

{
  echo "# I.5 entorno limpio · $(date -u +%FT%TZ) · commit $(git rev-parse --short HEAD)"
  echo "Máquina: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs) · $(nproc) CPU · $(free -g | awk '/Mem:/ {print $2}') GiB"
  echo "Salida completa de cada prueba: $log"
  echo
} > "$resumen"
cat "$resumen"

paso "0a docker compose down -v"                          docker compose down -v
paso "0b docker compose up -d --build"                    docker compose up -d --build
paso "1a carga de MongoDB (dataset)"                      dataset
paso "1b lago principal (dataset-historico)"              historico
paso "1c conjunto de prueba"                              historico python -m dataset.historico.prueba
if [ ! -f datalake/eventos_grande/_resumen.json ]; then
  paso "1d conjunto grande"                               historico python -m dataset.historico.generar --conjunto grande
fi
paso "1e reinicio de la analítica (lee los conjuntos nuevos)" docker compose restart analitica

paso "A4 rutas de A"                                      python3 pruebas/a4_rutas_gateway.py
paso "P1 rutas y sesiones (B5.1)"                         python3 pruebas/b51_sesiones_gateway.py
paso "P1 /salud y puertos"                                bash pruebas/p1_salud.sh
paso "P2 50 contra 10"                                    p2
paso "P3 interrupción"                                    python3 pruebas/b52_interrupcion_gateway.py
paso "P3 persistencia con reinicio de Valkey"             python3 pruebas/b52_persistencia_gateway.py
paso "P3 vencimiento del lock"                            python3 pruebas/b52_vencimiento_gateway.py
paso "P3 renovación del plazo en MongoDB"                 python3 pruebas/b52_renovacion_registro.py
paso "P3 borradores huérfanos"                            huerfanos
paso "P8 Valkey y sesión (B5.3)"                          python3 pruebas/b53_fallo_valkey.py
paso "I.2 recorrido LiDAR"                                bash pruebas/recorrido_lidar.sh
paso "I.4 aislamiento modular"                            bash pruebas/i4_aislamiento.sh
paso "C4.9 Q1–Q5 por el gateway"                          bash pruebas/c49_analitica_gateway.sh
paso "P4 historial por el gateway"                        bash pruebas/p4_gateway.sh
paso "C2.6 compactación del mes actual"                   bash pruebas/c26_compactacion.sh
paso "P5 analítica (1 y 2 workers, principal y grande)"   bash pruebas/p5_analitica.sh
paso "P8 MongoDB: secundario"                             bash pruebas/p8_mongo.sh secundario
paso "P8 MongoDB: primario"                               bash pruebas/p8_mongo.sh primario
paso "P8 MongoDB: sin mayoría"                            bash pruebas/p8_mongo.sh sin-mayoria
paso "P6 carga (10, 40, 100 usuarios)"                    bash pruebas/p6_carga.sh

echo; echo "Fin: $(date -u +%FT%TZ)" | tee -a "$resumen"
