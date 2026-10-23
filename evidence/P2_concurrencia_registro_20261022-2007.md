# P2 — 50 solicitudes simultáneas contra 10 tareas libres (corrida preliminar contra el registro)

| Campo | Valor |
|---|---|
| Fecha y hora (inicio – fin) | 2026-10-22 20:07 – 20:07 (UTC-6) |
| Responsable | Fabricio Mena (A) |
| Objetivo | Mostrar que la asignación es atómica: con 10 tareas libres y 50 solicitudes al mismo tiempo, 10 reciben tarea, 40 reciben 204 y ninguna tarea queda con dos anotadores. Un segundo envío responde 409. |
| Commit | `889dd3a` |
| Configuración | Replica set `rs0` (3 nodos `mongo:7.0`), registro con `MONGO_W=majority`. **Sin gateway**: las rutas del gateway (B3.4, B3.6) todavía no existen, así que la prueba llama directo a `POST /tareas/asignar` y `/tareas/{id}/transicion` del registro desde la red `interna`. Se repetirá vía gateway. |
| Conjunto de datos | Carga desde cero (semilla 2026) y un proyecto nuevo con un solo lote de exactamente 10 tareas libres; las 50 solicitudes piden tareas de ese proyecto. 50 anotadores distintos de `ids.json`. |
| Máquina | Laptop AMD Ryzen 7 5700U, 16 GB de RAM (4 CPU y 9,7 GiB para WSL 2), Windows 11 Home con Ubuntu 24.04 en WSL 2, Docker Desktop 29.5.2. |

## Comando(s) ejecutado(s)
```bash
docker compose --profile datos run --rm dataset          # carga desde cero
pruebas/estado_rs.sh                                     # estado del replica set
docker compose --profile datos run --rm -v ./pruebas:/srv/pruebas dataset \
  python /srv/pruebas/p2_concurrencia.py --csv /srv/evidence/P2_concurrencia_registro_20261022-2007.csv
```
Las 50 solicitudes salen de 50 procesos (`ProcessPoolExecutor`) que esperan en una `Barrier` y la
cruzan juntos; salieron en una ventana de 59,7 ms.

## Resultado observado

| Métrica | Valor | Muestras |
|---|---|---|
| Respuestas `200` (tarea asignada) | 10 | 50 solicitudes |
| Respuestas `204` (sin tarea) | 40 | 50 solicitudes |
| Tareas asignadas a más de un anotador | 0 | 10 tareas |
| Envío con el token de otro → `409` | 10 de 10 | 10 |
| Primer envío con el token propio → `200` | 10 de 10 | 10 |
| Segundo envío de la tarea ya enviada → `409` | 10 de 10 | 10 |
| Exportación: aprobadas con el anotador que las tomó | 10 de 10 | 10 |

Estado previo del replica set: `mongo2` PRIMARY, `mongo1` y `mongo3` SECONDARY, `term` 3, los tres con
`health=1`. Las latencias de las 50 asignaciones (p50 132,4 ms, máximo 176,3 ms) están en el CSV, pero
no son una medición de rendimiento: son 50 peticiones peleando a la vez por el mismo servicio.

## Interpretación
La asignación es un solo `findOneAndUpdate` sobre un documento con filtro `estado = disponible` (o
`abierta` vencida). MongoDB aplica cada actualización de un documento de forma atómica, así que de dos
solicitudes que apuntan a la misma tarea solo una la encuentra todavía en `disponible`; la otra pasa a
la siguiente tarea o, cuando ya no quedan, recibe `204`. Por eso hubo exactamente 10 asignaciones y
cada tarea quedó con un solo token. El segundo envío responde `409` porque la tarea ya no está en
`abierta`, y el token ajeno también, porque el filtro incluye `asignacion.token`.

Límite: esta corrida prueba la capa de MongoDB. La prueba final debe pasar por el gateway, que además
toma el lock de Valkey; la conclusión sobre la doble adquisición no depende del lock, porque el
enunciado pone la exclusividad en el `findOneAndUpdate`.

## Archivos de respaldo
- `evidence/P2_concurrencia_registro_20261022-2007.txt` (estado del replica set y salida completa)
- `evidence/P2_concurrencia_registro_20261022-2007.csv` (una fila por solicitud: anotador, token, status, inicio, ms, tarea)
- `pruebas/p2_concurrencia.py`, `pruebas/estado_rs.sh`

## Acciones de recuperación
`docker compose --profile datos run --rm dataset` para quitar el proyecto de la prueba y volver al dataset inicial.
