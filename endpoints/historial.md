# Historial — API interna (cola durable + lago Parquet)

Base: `http://historial:8003`. Único servicio que escribe en el lago.
Cola durable propia: instancia `cola-eventos` (Valkey Stream con AOF `everysec`), **separada** del
Valkey de sesión para respetar el aislamiento entre servicios.

| # | Método y ruta | Uso |
|---|---|---|
| 1 | `POST /eventos` | Recibe uno o varios eventos → `XADD eventos:tareas`. `202`. Idempotente por `event_id`. |
| 2 | `POST /flush` | Fuerza el cierre del micro-lote actual (pruebas P4). |
| 3 | `POST /reprocesar/{micro_lote_id}` | Vuelve a escribir un micro-lote (prueba de deduplicación). |
| 4 | `GET /micro-lotes?ultimos=n` | Últimos micro-lotes publicados (id, archivo, filas, ts). |
| 5 | `GET /salud` | Estado de la cola, pendientes sin ACK, último micro-lote. |

## Flujo del consumidor
1. `XREADGROUP` en `eventos:tareas` (grupo `historial`).
2. Junta un micro-lote: 500 eventos **o** 5 s, lo que ocurra primero.
3. `micro_lote_id = <primer stream id>_<último stream id>` (determinista → reprocesar sobrescribe el mismo archivo).
4. Escribe `datalake/_tmp/<micro_lote_id>.parquet` (pyarrow) → `fsync` → `os.rename` a
   `datalake/eventos/mes=AAAA-MM/part-<micro_lote_id>.parquet` (rename atómico en el mismo FS).
   Si el lote abarca dos meses, se escribe un archivo por mes con el mismo id.
5. `XACK` solo después del rename. Al arrancar: `XAUTOCLAIM` de pendientes y limpieza de `_tmp/`.

Spark nunca lee `_tmp/` (empieza con `_`, Spark lo ignora) → no hay lecturas a medias.

## Layout del lago
```
datalake/
  _tmp/
  eventos/mes=2026-01/part-<micro_lote_id>.parquet      # conjunto principal (~800k)
  eventos_grande/mes=.../part-*.parquet                 # conjunto ampliado (opcional)
  eventos_prueba/mes=.../part-*.parquet                 # casos conocidos para validar Q1–Q5
```

## Esquema del evento (una fila por transición)

| Columna | Tipo Arrow | Nulo | Nota |
|---|---|---|---|
| event_id | string | no | UUID; clave de deduplicación |
| ts | timestamp[ms, UTC] | no | momento de la transición |
| tarea_id | string | no | |
| proyecto_id | string | no | |
| cliente_id | string | no | |
| batch_id | string | no | |
| modalidad | string | no | texto · imagen · video · lidar |
| tipo_tarea | string | no | span · bbox · poligono · bbox_video · cuboide_3d |
| estado | string | no | abierta · anotada · en_revision · aprobada · rechazada · liberada |
| version | int32 | no | versión de la tarea tras la transición |
| anotador_id | string | sí | quien tiene/tuvo la asignación |
| equipo_id | string | sí | equipo del anotador (Q2) |
| revisor_id | string | sí | en en_revision / aprobada / rechazada |
| motivo_rechazo | string | sí | solo en rechazada |
| tomada_ts | timestamp[ms, UTC] | sí | toma de la asignación vigente (Q3, Q5) |
| sla_horas | int32 | no | del proyecto (Q5) |
| micro_lote_id | string | sí | lo llena el historial al escribir |

La partición `mes` se deriva de `ts` (no es columna del archivo).
