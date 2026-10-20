# Analítica — API interna (Spark sobre Parquet)

Base: `http://analitica:8004`. Lee **solo** el lago (`datalake/` montado en solo lectura).
No tiene conexión a MongoDB, Valkey ni historial.

| # | Método y ruta | Uso |
|---|---|---|
| 1 | `GET /q/{n}?conjunto=principal|grande|prueba` | Ejecuta Qn. `{"consulta", "conjunto", "filas", "ms", "primera_ejecucion"}` |
| 2 | `GET /plan/{n}?conjunto=` | `explain("formatted")` de Qn. |
| 3 | `GET /salud` | SparkSession activa y master alcanzable. |

Conjunto → ruta: `principal` → `datalake/eventos`, `grande` → `datalake/eventos_grande`,
`prueba` → `datalake/eventos_prueba`.

Base común de todas las consultas: leer el conjunto, `dropDuplicates(["event_id"])`, ignorar `liberada`.

## Definiciones

| Q | Definición |
|---|---|
| Q1 | Filas `aprobada` con `ts >= max(ts) - 90 días` (D6), agrupadas por `to_date(ts)` y `modalidad` → `count`. |
| Q2 | Por `equipo_id` y `mes`: `aprobadas / (aprobadas + rechazadas)`. |
| Q3 | Por cada fila `anotada`: `duracion_s = ts - tomada_ts`. Por `modalidad`, `tipo_tarea`: media y `percentile_approx(duracion_s, 0.95)`, ordenado por media desc. |
| Q4 | Por `cliente_id`, `proyecto_id`: `rechazadas / (aprobadas + rechazadas)`. |
| Q5 | Por cada fila `aprobada`: `horas = ts - tomada_ts`. Por `cliente_id`: p50, p95 y `% horas > sla_horas`. |

Notas: `revisadas = aprobada + rechazada`. Para Q2 el `equipo_id` es el del anotador de la tarea.
