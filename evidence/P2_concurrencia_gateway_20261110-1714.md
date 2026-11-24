# P2 — 50 solicitudes simultáneas contra 10 tareas libres (vía gateway, con el gateway corregido)

| Campo | Valor |
|---|---|
| Fecha y hora (inicio – fin) | 2026-11-10 17:14 – 17:15 (UTC-6) |
| Responsable | Fabricio Mena (A) |
| Objetivo | Repetir P2 por el gateway después de corregir el cliente HTTP interno y las limpiezas (`0c12001`): con 10 tareas libres y 50 solicitudes al mismo tiempo, 10 reciben tarea, 40 reciben 204 y ninguna tarea queda con dos anotadores. Un envío con token ajeno y un segundo envío responden 409. |
| Commit | `0c12001` (rama `integracion-a-b-c`). |
| Configuración | Replica set `rs0` (3 nodos `mongo:7.0`), registro con `MONGO_W=majority`. Gateway de B con las correcciones de A (un solo `httpx.AsyncClient`, limpieza de lock, borrador y sesión al enviar). Un solo proceso de uvicorn en el gateway. La prueba corre en el contenedor `simulador`, que solo está en la red `borde` y solo ve al gateway. |
| Conjunto de datos | Carga desde cero (semilla 2026) y un proyecto nuevo, creado por el gateway, con un solo lote de exactamente 10 tareas libres; las 50 solicitudes piden tareas de ese proyecto. 50 anotadores distintos de `ids.json`. |
| Máquina | Laptop AMD Ryzen 7 5700U, 16 GB de RAM (4 CPU y 9,7 GiB para WSL 2), Windows 11 Home con Ubuntu 24.04 en WSL 2, Docker Desktop 29.5.2. |

## Comando(s) ejecutado(s)
```bash
docker compose --profile datos run --rm dataset          # carga desde cero
pruebas/estado_rs.sh                                     # estado del replica set
docker compose --profile carga run --rm -v ./pruebas:/srv/pruebas simulador \
  python /srv/pruebas/p2_concurrencia.py --modo gateway --csv /srv/evidence/P2_concurrencia_gateway_20261110-1714.csv
```
Las 50 solicitudes salen de 50 procesos (`ProcessPoolExecutor`) que esperan en una `Barrier` y la
cruzan juntos; salieron en una ventana de 68 ms. Cada ganador envía primero con un token inventado,
después con el suyo y después otra vez con el suyo; un revisor empieza la revisión y aprueba, y al final
se exporta el proyecto.

## Resultado observado

| Métrica | Valor | Muestras |
|---|---|---|
| Respuestas `200` (tarea asignada) | 10 | 50 solicitudes |
| Respuestas `204` (sin tarea) | 40 | 50 solicitudes |
| Tareas asignadas a más de un anotador | 0 | 10 tareas |
| Envío con un token ajeno → `409` | 10 de 10 | 10 |
| Primer envío con el token propio → `200` | 10 de 10 | 10 |
| Segundo envío de la tarea ya enviada → `409` | 10 de 10 | 10 |
| Exportación: aprobadas con el anotador que las tomó | 10 de 10 | 10 |

Estado previo del replica set: `mongo3` PRIMARY, `mongo1` y `mongo2` SECONDARY, `term` 14, los tres con
`health=1`.

**Latencias de las 50 solicitudes** (ms, percentil por rango más cercano):

| Dónde se mide | n | p50 | p95 | p99 | máx. |
|---|---|---|---|---|---|
| Cliente, respuestas `200` | 10 | 473,3 | 509,8 | 509,8 | 509,8 |
| Cliente, respuestas `204` | 40 | 275,7 | 461,1 | 466,2 | 466,2 |
| Cliente, todas | 50 | 324,3 | 499,7 | 509,8 | 509,8 |
| Gateway, `POST /tareas/siguiente` (su log) | 50 | 183,4 | 481,0 | 483,8 | 483,8 |
| Registro, `POST /tareas/asignar` (su log) | 50 | 15,7 | 81,8 | 92,9 | 92,9 |
| Sesión, `POST /locks/{id}` (su log) | 50 | 2,6 | 27,0 | 34,7 | 34,7 |

Antes de las correcciones (`P2_concurrencia_gateway_20261110-1450.md`) el cliente vio p50 3 414 ms y
máximo 4 029 ms, con el registro en p50 52 ms.

## Interpretación
La exclusividad no cambia: la asignación es un solo `findOneAndUpdate` con filtro `estado = disponible`
(o `abierta` vencida) y MongoDB aplica cada actualización de un documento de forma atómica; de dos
solicitudes que apuntan a la misma tarea solo una la encuentra en `disponible`. El lock de Valkey usa el
mismo token, así que el envío con un token ajeno lo rechaza la sesión (`409`) antes de llegar a MongoDB,
y el segundo envío lo rechaza el registro (`409`) porque la tarea ya no está en `abierta`.

La latencia del cliente bajó unas 10 veces (p50 de 3,4 s a 0,32 s) solo por reusar un cliente HTTP en el
gateway: antes cada llamada interna creaba uno, y ese trabajo de CPU bloqueaba el único proceso del
gateway mientras las 50 solicitudes esperaban. Ahora el registro tarda p50 16 ms y la sesión p50 3 ms; lo
que queda (gateway p50 183 ms) es la fila de 50 peticiones en un solo proceso de uvicorn, que además
guarda cada evento en SQLite antes de responder. Eso se mide con más usuarios en P6.

Límites: los tres nodos de MongoDB y todos los servicios están en la misma máquina; las 50 solicitudes
compiten también por la CPU con el cliente de la prueba.

## Archivos de respaldo
- `evidence/P2_concurrencia_gateway_20261110-1714.txt` (estado del replica set y salida completa)
- `evidence/P2_concurrencia_gateway_20261110-1714.csv` (una fila por solicitud: anotador, token, status, inicio, ms, tarea, error)
- `evidence/P2_concurrencia_gateway_20261110-1450.md` (misma prueba antes de las correcciones)
- `pruebas/p2_concurrencia.py`, `pruebas/estado_rs.sh`

## Acciones de recuperación
`docker compose --profile datos run --rm dataset` para quitar el proyecto de la prueba y volver al dataset inicial.
