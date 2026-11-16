#!/usr/bin/env bash
# I.2 — recorrido completo de una tarea LiDAR por el gateway (siguiente → borrador → enviar → revisión →
# aprobada → exportar → visible en Q1). Necesita el sistema arriba y el dataset cargado (README).
# Uso: pruebas/recorrido_lidar.sh   → evidence/I2_recorrido_lidar_<AAAAMMDD-HHMM>.txt
set -u
cd "$(dirname "$0")/.."
salida=evidence/I2_recorrido_lidar_$(date +%Y%m%d-%H%M).txt
{
  echo "# I.2 recorrido LiDAR · $(date -u +%FT%TZ) · commit $(git rev-parse --short HEAD)"
  echo "Máquina: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs) · $(nproc) CPU · $(free -g | awk '/Mem:/ {print $2}') GiB"
  echo
  python3 pruebas/recorrido_lidar.py
  echo "EXIT_CODE=$?"
} 2>&1 | tee "$salida"
