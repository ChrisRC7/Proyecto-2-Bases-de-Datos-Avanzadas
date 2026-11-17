#!/usr/bin/env bash
# I.4 — evidencia del aislamiento modular (enunciado §4): registro no consulta Valkey, sesión no decide el
# estado de una tarea, analítica solo lee el lago Parquet y el simulador solo habla con el gateway.
# Revisa en los contenedores levantados: puertos publicados, redes, variables de conexión, bibliotecas
# instaladas, a qué nombres llega cada uno por la red y el montaje del lago.
# Uso: pruebas/i4_aislamiento.sh   → evidence/I4_aislamiento_<AAAAMMDD-HHMM>.txt
set -u
cd "$(dirname "$0")/.."
salida=evidence/I4_aislamiento_$(date +%Y%m%d-%H%M).txt
SERVICIOS="gateway registro sesion historial analitica"

puede_importar() {  # $1 servicio, $2 módulo
  docker compose exec -T "$1" python -c "import $2" >/dev/null 2>&1 && echo "sí" || echo "no"
}
resuelve() {  # $1 servicio, $2 nombre de host
  docker compose exec -T "$1" python -c "import socket,sys; socket.gethostbyname(sys.argv[1])" "$2" >/dev/null 2>&1 \
    && echo "sí" || echo "no"
}

{
  echo "# I.4 aislamiento modular · $(date -u +%FT%TZ) · commit $(git rev-parse --short HEAD)"
  echo
  echo "## 1. Puertos publicados al anfitrión (solo el gateway, 8000)"
  docker compose ps --format '{{.Service}}\t{{.Ports}}' | awk -F'\t' '{p=$2; if (p ~ /->/) print $1 "\t" p; else print $1 "\t(ninguno)"}'
  echo
  echo "## 2. Redes de cada contenedor"
  for c in $SERVICIOS mongo1 mongo2 mongo3 valkey cola-eventos spark-master spark-worker; do
    printf '%-14s %s\n' "$c" "$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$(docker compose ps -q "$c")" | sed 's/proyecto2_//g')"
  done
  echo "simulador      $(docker compose --profile carga config --format json | python3 -c 'import json,sys; print(" ".join(json.load(sys.stdin)["services"]["simulador"]["networks"]))')"
  echo
  echo "## 3. Variables de conexión de cada servicio (qué motor conoce)"
  for s in $SERVICIOS; do
    echo "$s: $(docker compose exec -T "$s" env | grep -E '_(URL|URI|HOST)=' | sort | tr '\n' ' ')"
  done
  echo
  echo "## 4. Bibliotecas de cliente instaladas (pymongo = MongoDB, valkey = Valkey, pyarrow = Parquet)"
  printf '%-10s %-8s %-8s %-8s %-8s\n' servicio pymongo valkey pyarrow pyspark
  for s in $SERVICIOS; do
    printf '%-10s %-8s %-8s %-8s %-8s\n' "$s" "$(puede_importar "$s" pymongo)" "$(puede_importar "$s" valkey)" \
      "$(puede_importar "$s" pyarrow)" "$(puede_importar "$s" pyspark)"
  done
  echo
  echo "## 5. A qué motores llega cada servicio por la red (resolución DNS dentro de la red de Docker)"
  echo "(«no» = el nombre ni siquiera existe en sus redes: no hay camino para conectarse)"
  printf '%-10s %-7s %-7s %-13s %-13s %-8s\n' servicio mongo1 valkey cola-eventos spark-master gateway
  for s in $SERVICIOS; do
    printf '%-10s %-7s %-7s %-13s %-13s %-8s\n' "$s" "$(resuelve "$s" mongo1)" "$(resuelve "$s" valkey)" \
      "$(resuelve "$s" cola-eventos)" "$(resuelve "$s" spark-master)" "$(resuelve "$s" gateway)"
  done
  echo "simulador: $(docker compose --profile carga run --rm --no-deps -T simulador python -c '
import socket
for h in ("gateway", "mongo1", "valkey", "registro", "sesion", "historial", "analitica"):
    try:
        socket.gethostbyname(h); print(h, "sí", end=" · ")
    except OSError:
        print(h, "no", end=" · ")
' 2>/dev/null)"
  echo
  echo "## 6. Lago Parquet: quién lo monta y cómo"
  for s in historial analitica spark-worker; do
    echo "$s: $(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/datalake"}}{{.Destination}} rw={{.RW}}{{end}}{{end}}' "$(docker compose ps -q "$s")")"
  done
  echo "analitica escribiendo en /datalake: $(docker compose exec -T analitica sh -c 'touch /datalake/_prueba_i4 2>&1 && rm /datalake/_prueba_i4 && echo PUDO || echo "no puede (solo lectura)"' | tail -1)"
  echo
  echo "## 7. Menciones en el código de un servicio al motor de otro (deben ser solo comentarios)"
  echo "registro → Valkey:"; grep -rniE 'valkey|redis' servicios/registro/app/*.py | grep -v 'estado-capas' | sed 's/^/  /'
  echo "sesion → MongoDB:"; grep -rniE 'pymongo|mongo' servicios/sesion/app/*.py | sed 's/^/  /'
  echo "analitica → MongoDB / Valkey / historial: $(grep -rniE 'pymongo|valkey|historial:800' servicios/analitica/app/*.py | wc -l) líneas"
  echo "sesion → estados de una tarea (disponible/anotada/en_revision/aprobada), sin pruebas: $(grep -rnE '\"(disponible|anotada|en_revision|aprobada)\"' servicios/sesion/app/*.py | grep -v prueba | wc -l) líneas"
  echo "simulador → bibliotecas: $(tr '\n' ' ' < simulador/requirements.txt)"
} 2>&1 | tee "$salida"
