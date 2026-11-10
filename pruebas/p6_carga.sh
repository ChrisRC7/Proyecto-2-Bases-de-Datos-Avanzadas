#!/usr/bin/env bash
# P6 (A7.2): carga con 10, 40 y 100 usuarios virtuales; 30 s de calentamiento y tres corridas de 90 s
# medidos por nivel. Antes de cada corrida recarga el dataset y borra las llaves de sesión que dejó la
# corrida anterior en Valkey (locks, sesiones, borradores), para que todas empiecen igual. Mientras
# corre el simulador se muestrea `docker stats`.
# Uso: pruebas/p6_carga.sh [niveles...]        (por defecto 10 40 100)
#      CORRIDAS=1 ETIQUETA=gateway_2procesos pruebas/p6_carga.sh 40 100   (experimentos aparte)
# Deja evidence/P6_<ETIQUETA, por defecto carga>_<AAAAMMDD-HHMM>/ con, por corrida, <n>u_c<k>.csv (una fila por petición),
# <n>u_c<k>.txt (salida del simulador) y <n>u_c<k>_stats.csv; al final resumen.txt (pruebas/p6_resumen.py).
set -u
cd "$(dirname "$0")/.."

niveles=${*:-10 40 100}
corridas=${CORRIDAS:-3}
dir=evidence/P6_${ETIQUETA:-carga}_$(date +%Y%m%d-%H%M)
mkdir -p "$dir"

limpiar_valkey() {  # no toca salud:* (mensajes de las capas)
  for patron in 'lock:tarea:*' 'sesion:*' 'borrador:*' 'editando:*' 'pendiente:*'; do
    docker compose exec -T valkey valkey-cli --scan --pattern "$patron" \
      | xargs -r -n 500 docker compose exec -T valkey valkey-cli DEL >/dev/null
  done
}

muestrear() {  # docker stats cada ~2 s hasta que aparezca $1.fin
  echo "ts,contenedor,cpu,mem" > "$1"
  while [ ! -e "$1.fin" ]; do
    docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' | sed "s/^/$(date +%s),/" >> "$1"
  done
  rm -f "$1.fin"
}

{
  echo "P6 · niveles: $niveles · $(date '+%Y-%m-%d %H:%M:%S %Z') · commit $(git rev-parse --short HEAD)"
  echo "Gateway: $(docker compose exec -T gateway cat /proc/1/cmdline | tr '\0' ' ')"
  echo "Máquina: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs) · $(nproc) CPU y $(free -g | awk '/Mem:/ {print $2}') GiB visibles en WSL 2"
} | tee "$dir/maquina.txt"

for n in $niveles; do
  for c in $(seq 1 "$corridas"); do
    base=$dir/${n}u_c$c
    echo "== $n usuarios, corrida $c ($(date +%H:%M:%S))"
    docker compose --profile datos run --rm -T dataset 2>&1 | grep '^Semilla'
    limpiar_valkey
    muestrear "${base}_stats.csv" &
    sp=$!
    docker compose --profile carga run --rm -T simulador python -m simulador.simular \
      --usuarios "$n" --calentamiento 30 --duracion 90 --csv "/srv/${base}.csv" 2>&1 \
      | grep -v '^ Container' | tee "$base.txt"
    touch "${base}_stats.csv.fin"
    wait $sp
  done
done

python3 pruebas/p6_resumen.py "$dir" | tee "$dir/resumen.txt"
