# Pn — <título corto de la prueba>

Copiar este archivo como `evidence/Pn_<nombre>_<AAAAMMDD-HHMM>.md` y dejar al lado los archivos crudos
(`.csv`, `.json`, `.log`, `.txt`) con el mismo prefijo.

| Campo | Valor |
|---|---|
| Fecha y hora (inicio – fin) | AAAA-MM-DD HH:MM – HH:MM (zona) |
| Responsable | |
| Objetivo | Qué se quiere demostrar / medir. |
| Commit | `git rev-parse --short HEAD` |
| Configuración | Perfil de compose, variables relevantes (`MONGO_W`, workers de Spark, nº usuarios, duración...). |
| Conjunto de datos | principal / grande / prueba — conteos al momento de la prueba. |
| Máquina | CPU (modelo, núcleos), RAM, disco, SO, versión de Docker. |

## Comando(s) ejecutado(s)
```bash
# exactamente lo que se ejecutó
```

## Resultado observado
Datos, no opiniones. Latencias como **p50 / p95 / p99** y **número de muestras**.

| Métrica | Valor | Muestras |
|---|---|---|
| | | |

## Interpretación
Qué significa el resultado y sus límites (no extrapolar más allá de las condiciones del experimento).

## Archivos de respaldo
- `evidence/Pn_<...>.csv`

## Acciones de recuperación (pruebas de fallas)
Qué se hizo para volver al estado conocido.
