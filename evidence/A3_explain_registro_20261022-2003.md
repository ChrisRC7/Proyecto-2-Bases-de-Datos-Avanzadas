# A3 — explain de la asignación y la exportación

| Campo | Valor |
|---|---|
| Fecha y hora (inicio – fin) | 2026-10-22 20:03 – 20:04 (UTC-6) |
| Responsable | Fabricio Mena (A) |
| Objetivo | Ver qué índice usan las dos consultas del registro y justificar cualquier índice extra con documentos examinados (A3.2 y A3.3). |
| Commit | `67d6852` (código del registro y dataset) |
| Configuración | Replica set `rs0` de 3 nodos `mongo:7.0` (7.0.45), `--wiredTigerCacheSizeGB 0.25`. |
| Conjunto de datos | Carga desde cero (semilla 2026): 60 000 tareas. Para que la exportación tenga qué devolver, se marcaron a mano 300 tareas del proyecto «LiDAR urbano 2026» como `aprobada` con `updateMany`; después se recargó el dataset. |
| Máquina | Laptop AMD Ryzen 7 5700U, 16 GB de RAM (4 CPU y 9,7 GiB para WSL 2), Windows 11 Home con Ubuntu 24.04 en WSL 2, Docker Desktop 29.5.2. |

## Comando(s) ejecutado(s)
```bash
URI="mongodb://mongo1,mongo2,mongo3/plataforma?replicaSet=rs0"
# 1) solo los índices obligatorios
docker compose exec -T mongo1 mongosh --quiet "$URI" --eval 'db.tareas.dropIndex("estado_1_prioridad_-1")'
docker compose exec -T mongo1 mongosh --quiet "$URI" --eval "$(cat pruebas/explain_registro.js)"
# 2) con el índice extra
docker compose exec -T mongo1 mongosh --quiet "$URI" --eval 'db.tareas.createIndex({estado: 1, prioridad: -1})'
docker compose exec -T mongo1 mongosh --quiet "$URI" --eval "$(cat pruebas/explain_registro.js)"
```
`findAndModify` en modo `explain` no modifica datos. El filtro es el mismo de `POST /tareas/asignar`:
`disponible`, o `abierta` con `asignacion.vence_en < ahora`, ordenado por `prioridad` descendente.

## Resultado observado

| Consulta | Índices | Plan | Claves | Documentos | Devueltos |
|---|---|---|---:|---:|---:|
| Asignación con `proyecto_id` | obligatorios | `SORT_MERGE` de dos `IXSCAN(proyecto_id_1_estado_1_prioridad_-1)` | 1 | 1 | 1 |
| Asignación sin `proyecto_id` | obligatorios | `SORT` ← `COLLSCAN` | 0 | 60 000 | 1 |
| Asignación sin `proyecto_id` | + `{estado:1, prioridad:-1}` | `SORT_MERGE` de dos `IXSCAN(estado_1_prioridad_-1)` | 1 | 1 | 1 |
| Exportación, primera página (límite 100) | obligatorios (igual con el extra) | `SORT` ← `FETCH` ← `IXSCAN(proyecto_id_1_estado_1_prioridad_-1)` | 300 | 300 | 100 |

Una sola ejecución por consulta; los milisegundos están en el archivo crudo pero no se usan para
decidir (dependen de la caché y de que el índice recién creado vacía la caché de planes).

## Interpretación
- La asignación por proyecto ya es óptima con el índice obligatorio: cada rama del `$or` es un
  rango del índice y `SORT_MERGE` une las dos ya ordenadas por `prioridad`, así que basta leer una clave.
- Sin `proyecto_id` (el contrato lo permite) ningún índice obligatorio empieza por `estado`, y cada
  asignación recorre las 60 000 tareas y las ordena. Con `{estado:1, prioridad:-1}` pasa a 1
  documento examinado. **Se agrega** (registro y carga lo crean).
- La exportación usa el prefijo `proyecto_id, estado` y ordena por `_id` en memoria: examina todas las
  aprobadas restantes del proyecto (aquí 300 para devolver 100). Como un proyecto tiene a lo sumo 500
  tareas, el costo está acotado, y un índice `{proyecto_id, estado, _id}` sumaría otra escritura de
  índice en cada transición (todas cambian `estado`). **No se agrega**; si un proyecto creciera a miles
  de aprobadas habría que revisarlo.
- Límite: 3 nodos en una sola máquina y una colección pequeña; lo que se compara son documentos
  examinados, no tiempos.

## Archivos de respaldo
- `evidence/A3_explain_registro_20261022-2003.txt` (salida completa de las dos corridas)
- `pruebas/explain_registro.js`

## Acciones de recuperación
`docker compose --profile datos run --rm dataset` para quitar las 300 aprobadas manuales y dejar el
dataset en su estado inicial, ya con el índice extra.
