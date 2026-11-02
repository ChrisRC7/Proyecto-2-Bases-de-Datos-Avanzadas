# Analítica — API interna (Spark sobre Parquet)

Base: `http://analitica:8004`. Lee **solo** el lago (`datalake/` montado en solo lectura).
No tiene conexión a MongoDB, Valkey ni historial.

| # | Método y ruta | Uso |
|---|---|---|
| 1 | `GET /q/{n}?conjunto=principal|grande|prueba` | Ejecuta Qn. `{"consulta", "conjunto", "parametros", "filas", "ms", "primera_ejecucion", "ejecutores"}` |
| 2 | `GET /plan/{n}?conjunto=` | `explain("formatted")` de Qn: `{"consulta", "conjunto", "parametros", "plan"}` (texto). |
| 3 | `GET /salud` | Siempre `200 {"servicio", "estado", "spark": {"master_alcanzable", "sesion_spark", "arranque_ms", "ejecutores", "nucleos", "error"}, "ts"}`. Cada 5 s se publica en `sesion:/estado-capas` (D4). |

Conjunto → ruta: `principal` → `datalake/eventos`, `grande` → `datalake/eventos_grande`,
`prueba` → `datalake/eventos_prueba`.

Base común de todas las consultas: leer el conjunto, `dropDuplicates(["event_id"])`, ignorar `liberada`.
Cada consulta filtra primero los estados que usa y después deduplica (D14). `ms` es lo que tarda
la consulta dentro de la analítica (sin la red); `primera_ejecucion` es la primera vez que esa
consulta corre sobre ese conjunto en el proceso actual; `ejecutores` son los ejecutores de Spark
registrados (1 por worker). `parametros` solo lo usa Q1: `{"desde", "hasta"}` de la ventana.

Errores: `404 conjunto_no_existe` (la carpeta no tiene Parquet, p. ej. `grande` sin generar) ·
`422 validacion` (`n` fuera de 1–5 o conjunto inválido) · `503 spark_no_disponible` /
`503 spark_error` (master o ejecutores caídos).

Columnas: Q1 `dia, modalidad, aprobadas` · Q2 `equipo_id, mes, aprobadas, revisadas, proporcion` ·
Q3 `modalidad, tipo_tarea, anotadas, media_s, p95_s` · Q4 `cliente_id, proyecto_id, rechazadas,
revisadas, proporcion` · Q5 `cliente_id, aprobadas, p50_h, p95_h, incumplen_sla, pct_incumple_sla`.

## Definiciones

| Q | Definición |
|---|---|
| Q1 | Filas `aprobada` con `ts >= max(ts) - 90 días` (D6), agrupadas por `to_date(ts)` y `modalidad` → `count`. |
| Q2 | Por `equipo_id` y `mes`: `aprobadas / (aprobadas + rechazadas)`. |
| Q3 | Por cada fila `anotada`: `duracion_s = ts - tomada_ts`. Por `modalidad`, `tipo_tarea`: media y `percentile_approx(duracion_s, 0.95)`, ordenado por media desc. |
| Q4 | Por `cliente_id`, `proyecto_id`: `rechazadas / (aprobadas + rechazadas)`. |
| Q5 | Por cada fila `aprobada`: `horas = ts - tomada_ts`. Por `cliente_id`: p50, p95 y `% horas > sla_horas`. |

Notas: `revisadas = aprobada + rechazada`. Para Q2 el `equipo_id` es el del anotador de la tarea.
