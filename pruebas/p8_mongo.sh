#!/usr/bin/env bash
# P8 (MongoDB, A7.3): provoca una falla mientras la sonda escribe por el registro y verifica RTO y RPO.
#   secundario   cae un secundario: se debe seguir escribiendo (mayoría 2 de 3)
#   primario     cae el primario: elección; lo que dio 200 sigue una sola vez
#   sin-mayoria  caen los dos secundarios: sin mayoría no se escribe (503)
# Uso: pruebas/p8_mongo.sh secundario|primario|sin-mayoria
# Deja evidence/P8_mongo_<escenario>_<AAAAMMDD-HHMM>.{txt,csv} y vuelve a encender los nodos.
set -u
cd "$(dirname "$0")/.."

esc=${1:?uso: pruebas/p8_mongo.sh secundario|primario|sin-mayoria}
case $esc in
  secundario) duracion=45 ;;
  primario) duracion=60 ;;
  sin-mayoria) duracion=90 ;;
  *) echo "escenario desconocido: $esc" >&2; exit 2 ;;
esac
base=evidence/P8_mongo_${esc}_$(date +%Y%m%d-%H%M)

rol() {  # nodos con ese estado según el primer nodo que responda, p. ej. «rol PRIMARY»
  for n in mongo1 mongo2 mongo3; do
    docker compose exec -T "$n" mongosh --quiet --eval \
      "rs.status().members.filter(m => m.stateStr == '$1').forEach(m => print(m.name.split(':')[0]))" 2>/dev/null && return
  done
}
corre() { docker compose --profile datos run --rm -T -v ./pruebas:/srv/pruebas dataset python "$@" 2>&1 | grep -v '^ Container'; }
sano() { [ "$(rol PRIMARY | wc -l)" -eq 1 ] && [ "$(rol SECONDARY | wc -l)" -eq 2 ]; }

{
  echo "# P8 MongoDB · $esc · $(date -u +%FT%TZ) · commit $(git rev-parse --short HEAD)"
  echo; echo "## Precondición"
  docker compose --profile datos run --rm dataset 2>&1 | grep -E 'Semilla|tareas  '  # dataset limpio
  pruebas/estado_rs.sh
  primario=$(rol PRIMARY)
  read -r -a secundarios <<< "$(rol SECONDARY | tr '\n' ' ')"
  case $esc in
    secundario) objetivo=("${secundarios[0]}") ;;
    primario) objetivo=("$primario") ;;
    sin-mayoria) objetivo=("${secundarios[@]}") ;;
  esac
  echo "primario: $primario · secundarios: ${secundarios[*]} · se detiene: ${objetivo[*]}"

  echo; echo "## Sonda (cambios de estado)"
  corre /srv/pruebas/p8_sonda.py --duracion "$duracion" --csv "/srv/$base.csv" &
  sonda=$!
  sleep 8

  docker compose kill "${objetivo[@]}" > /dev/null 2>&1   # SIGKILL: caída abrupta
  marca=$(date +%s.%N)
  echo "-- falla: docker compose kill ${objetivo[*]} · marca $marca ($(date -u +%FT%T.%3NZ))"
  sleep 15
  echo "-- estado durante la falla:"
  pruebas/estado_rs.sh
  case $esc in primario) sleep 15 ;; sin-mayoria) sleep 5 ;; esac  # el viejo primario vuelve tarde
  docker compose start "${objetivo[@]}" > /dev/null 2>&1
  recuperacion=$(date +%s.%N)
  echo "-- recuperación: docker compose start ${objetivo[*]} · $recuperacion ($(date -u +%FT%T.%3NZ))"
  wait "$sonda"

  for _ in $(seq 1 30); do sano && break; sleep 2; done
  echo; echo "## Después (conjunto restaurado)"
  pruebas/estado_rs.sh
  echo; echo "## Verificación contra MongoDB"
  corre /srv/pruebas/p8_verificar.py --csv "/srv/$base.csv" --marca "$marca" --recuperacion "$recuperacion"
} 2>&1 | tee "$base.txt"
