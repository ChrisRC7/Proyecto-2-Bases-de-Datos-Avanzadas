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
4. Escribe `datalake/_tmp/<micro_lote_id>__<AAAA-MM>.parquet` (pyarrow) → `fsync` → `os.rename` a
   `datalake/eventos/mes=AAAA-MM/part-<micro_lote_id>.parquet` (rename atómico en el mismo FS).
   Si el lote abarca dos meses, se escribe un archivo por mes con el mismo id.
5. `XACK` solo después del rename. Al arrancar: `XAUTOCLAIM` de pendientes y limpieza de `_tmp/`.

Spark nunca lee `_tmp/` (empieza con `_`, Spark lo ignora) → no hay lecturas a medias.

Recuperación al arrancar (o al volver la cola): borra los `_tmp/` huérfanos, `XAUTOCLAIM` de todo
lo pendiente del grupo, rehace con el mismo rango (mismo nombre de archivo) los micro-lotes que
quedaron en `microlotes:abiertos` y publica el resto de pendientes propios (`XREADGROUP ... 0`).

## Respuestas

| Ruta | Respuesta |
|---|---|
| `POST /eventos` | Body: un evento o una lista. `202 {"aceptados": n, "duplicados": m}` (un `event_id` ya visto no se vuelve a encolar). `422` esquema inválido (estado, modalidad/tipo, rechazada sin `motivo_rechazo`...). `503 cola_no_disponible` si cola-eventos no responde: el gateway reintenta (idempotente). |
| `POST /flush` | `200 {"micro_lotes": [{"micro_lote_id", "filas", "archivos": [{"archivo", "filas"}]}]}`. `503` si el consumidor está detenido. |
| `POST /reprocesar/{id}` | `200 {"micro_lote_id", "filas", "archivos", "reprocesado_en"}`. `404` id desconocido. `409 micro_lote_recortado` si el stream ya no tiene esas entradas. |
| `GET /micro-lotes?ultimos=n` | `200 {"micro_lotes": [{"micro_lote_id", "primero", "ultimo", "filas", "estado", "archivos", "publicado_en", "reprocesado_en"?, "reprocesos"?}]}` |
| `GET /salud` | Siempre `200 {"servicio", "estado": "operativo|degradado|caido", "cola": {"estado", "eventos_en_stream", "pendientes_sin_ack", "micro_lotes_abiertos"}, "lago": {"escribible"}, "consumidor": {"vivo", "error"}, "ultimo_micro_lote", "ts"}`. Cada 5 s se publica en `sesion:/estado-capas` (D4). |

## Llaves de cola-eventos (solo las usa el historial)

| Llave | Tipo | Uso |
|---|---|---|
| `eventos:tareas` | stream | Campo `evento` = JSON de la fila. Se recorta (`XTRIM MINID`) dejando lo necesario para reprocesar los últimos `RETENER_MICRO_LOTES` (2000). |
| `visto:{event_id}` | string | Marca de idempotencia de `POST /eventos` (`SET NX` + `XADD` en un script Lua). TTL 7 días. |
| `microlote:{id}` | hash | `primero`, `ultimo`, `filas`, `estado` (`escribiendo` → `publicado`), `archivos`, `publicado_en`. |
| `microlotes` | sorted set | Micro-lotes publicados, score = epoch ms. |
| `microlotes:abiertos` | set | Empezaron a escribirse y no confirmaron (se rehacen al arrancar). |

La confirmación (`estado=publicado`, `ZADD`, `SREM` y `XACK`) va en una transacción `MULTI` después del rename.

## Compactación (mantenimiento manual, no es una ruta)
`docker compose exec historial python -m app.compactar --mes AAAA-MM [--conjunto eventos] --ejecutar`:
lee los `part-*.parquet` del mes, quita duplicados de `event_id`, publica `part-compacto-<ts>.parquet`
con el mismo `_tmp` + rename y luego borra los archivos que leyó. Entre la publicación y el borrado
hay filas repetidas que Spark quita con `dropDuplicates`; una consulta que ya listó los archivos
viejos puede fallar al abrirlos, por eso se corre fuera de las mediciones.

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
