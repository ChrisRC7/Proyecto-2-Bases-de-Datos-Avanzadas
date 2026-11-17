#!/usr/bin/env bash
# P1 — /salud en estado normal, degradado (historial o analítica parados) y caído (Valkey o MongoDB sin
# mayoría), y puertos publicados. Deja el sistema como estaba (vuelve a encender lo que paró).
# Uso: pruebas/p1_salud.sh   → evidence/P1_salud_<AAAAMMDD-HHMM>.txt
set -u
cd "$(dirname "$0")/.."
salida=evidence/P1_salud_$(date +%Y%m%d-%H%M).txt

FORMATO=$(cat <<'PY'
import json, sys
d = json.load(sys.stdin)
partes = []
for nombre, capa in d["capas"].items():
    valkey = (capa.get("mensaje_valkey") or {}).get("estado", "-")
    partes.append(f"{nombre} {capa['estado']} ({capa.get('fuente')}; valkey {valkey})")
print(f"HTTP 200 · {d['estado']} · mensajes_valkey={d.get('mensajes_valkey')} · " + " · ".join(partes))
PY
)
salud() {  # estado general, estado y fuente de cada capa, y el mensaje de Valkey
  curl -s -m 5 localhost:8000/salud | python3 -c "$FORMATO"
}
operativo() {  # espera hasta 120 s a que /salud vuelva a operativo
  for _ in $(seq 1 60); do
    curl -s -m 5 localhost:8000/salud | grep -q '^{"servicio":"gateway","estado":"operativo"' && return 0
    sleep 2
  done
  return 1
}
escenario() {  # $1 título, $2 servicios a parar
  echo; echo "## $1 (docker compose stop $2)"
  docker compose stop $2 >/dev/null 2>&1
  echo "inmediato: $(salud)"
  sleep 16
  echo "a los 16 s (mensajes de Valkey vencidos, TTL 15 s): $(salud)"
  docker compose start $2 >/dev/null 2>&1
  operativo && echo "recuperado: $(salud)" || echo "NO volvió a operativo en 120 s: $(salud)"
}

{
  echo "# P1 /salud y puertos · $(date -u +%FT%TZ) · commit $(git rev-parse --short HEAD)"
  echo; echo "## Normal"
  operativo; echo "$(salud)"
  escenario "Degradado: historial parado" historial
  escenario "Degradado: analítica parada" analitica
  escenario "Caído: Valkey de sesión parado" valkey
  escenario "Caído: MongoDB sin mayoría (dos nodos parados)" "mongo2 mongo3"
  echo; echo "## Puertos publicados al anfitrión"
  docker compose ps --format '{{.Service}}\t{{.Ports}}' | awk -F'\t' '{p=$2; if (p ~ /->/) print $1 "\t" p; else print $1 "\t(ninguno)"}'
  echo; echo "Escuchando en el anfitrión (ss): $(ss -tln | awk 'NR>1 {print $4}' | grep -E ':(8000|8001|8002|8003|8004|27017|6379|7077)$' | sort -u | tr '\n' ' ')"
} 2>&1 | tee "$salida"
