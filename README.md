# Proyecto 2 — Plataforma distribuida de anotación de datos

TI-4601 Bases de Datos Avanzadas · Grupo 3 · Fabricio Mena, Alisson Redondo y Christopher Rodriguez.

Gateway REST (único puerto expuesto, 8000) delante de cuatro servicios: registro (MongoDB, replica set de
3 nodos), sesión (Valkey con AOF), historial (lago Parquet) y analítica (Spark). Contratos en
[`endpoints/`](endpoints/README.md), decisiones en [`DECISIONES.md`](DECISIONES.md), avance en
[`TODO.md`](TODO.md) e informe en [`informe/`](informe/).

## Requisitos previos

> Pendiente (B6.4).

## Inicio y detención

> Pendiente (B6.4): `docker compose up -d --build`, `docker compose down` (y `-v` para borrar los
> volúmenes), perfiles `datos`, `carga` y `spark2`.

## Carga de datos

Con el sistema arriba, el dataset operacional se carga desde cero con:

```bash
docker compose --profile datos run --rm dataset
```

Borra y vuelve a llenar las cinco colecciones de MongoDB (semilla 2026: 60 clientes, 120 proyectos, 600
batches, 60 000 tareas y 300 anotadores), crea los índices e imprime los conteos. Tarda unos 5 s y deja
`dataset/salida/ids.json`, que usan el simulador, las pruebas y el generador del histórico. Si las pruebas
de carga agotan las tareas:

```bash
docker compose --profile datos run --rm dataset \
  python -m dataset.regenerar_tareas --motivo "P6: se agotaron las tareas disponibles"
```

El detalle (conteos, otra semilla, qué se anota en `evidence/regeneraciones.md`) está en
[`datos/README.md`](datos/README.md).

### Histórico de eventos (lago Parquet)

El lago se llena desde cero con el generador del histórico (semilla 2026, doce meses del 2025-10 al
2026-09, tareas de los 60 proyectos cerrados). No necesita MongoDB ni ningún servicio: corre sin red y
solo escribe archivos en `datalake/`.

```bash
docker compose --profile datos run --rm dataset-historico                                   # principal, ~800 000 eventos, ~20 s
docker compose --profile datos run --rm dataset-historico \
  python -m dataset.historico.generar --conjunto grande                                      # ~10 M eventos, ~3 min, 553 MB
docker compose --profile datos run --rm dataset-historico python -m dataset.historico.prueba  # 42 eventos con resultado conocido
```

Cada comando borra y vuelve a escribir su carpeta (`datalake/eventos`, `eventos_grande` o
`eventos_prueba`), así que también se pierden los eventos que el historial escribió en operación. Los
conteos quedan en `datalake/<conjunto>/_resumen.json`. Los tres conjuntos tienen duplicados de `event_id`
a propósito (0,2 %), que Spark quita al leer. El detalle está en [`datos/README.md`](datos/README.md).

En operación, el lago solo lo escribe el servicio historial: el gateway le manda cada evento después del
200 de MongoDB y el historial lo publica en `datalake/eventos/mes=AAAA-MM/` en micro-lotes de 500 eventos
o 5 s. Para juntar los archivos pequeños de un mes (mantenimiento manual, fuera de las mediciones):

```bash
docker compose exec historial python -m app.compactar --mes 2026-11              # solo muestra qué haría
docker compose exec historial python -m app.compactar --mes 2026-11 --ejecutar   # compacta
```

## Simulador

Usuarios virtuales que solo hablan HTTP con el gateway: corre en el contenedor `simulador`, que solo está
en la red `borde` y no ve ninguna base de datos. El 90 % son anotadores (`/tareas/siguiente`, pensar,
`/tareas/{id}/enviar`) y el 10 % revisores (empezar revisión, aprobar, o rechazar con un motivo al azar el
20 % de las veces).

```bash
docker compose --profile carga run --rm simulador python -m simulador.simular \
  --usuarios 40 --calentamiento 30 --duracion 90 --csv /srv/evidence/mi_corrida.csv
```

Opciones útiles: `--proyecto <uuid>` (por defecto la cola global), `--pensar-min/--pensar-max` (segundos
que tarda en anotar), `--rechazo 0.2`, `--semilla`. Al final imprime p50/p95/p99 por endpoint y envíos por
minuto; el CSV tiene una fila por petición (`ts`, `fase`, `usuario`, `rol`, `endpoint`, `status`, `ms`).
`--borradores` activa el hilo de B4.2: mientras el anotador piensa, cada 2 s guarda un borrador
(`PUT /tareas/{id}/borrador`) y manda un latido (`POST /sesiones/latido`). Conviene subir
`--pensar-min/--pensar-max` (por ejemplo 3 y 6) para que cada tarea alcance a guardar borradores.

## Consultas analíticas

Spark lee solo el lago Parquet (montado en solo lectura). Por el gateway:

```bash
curl "localhost:8000/analitica/q1?conjunto=principal"   # principal (por defecto), grande o prueba
```

| Consulta | Qué responde | Columnas |
|---|---|---|
| `q1` | Aprobadas por día y modalidad en los últimos 90 días (contados desde el evento más reciente del conjunto) | `dia, modalidad, aprobadas` |
| `q2` | Aprobadas / revisadas por equipo y mes | `equipo_id, mes, aprobadas, revisadas, proporcion` |
| `q3` | Duración media y p95 de la anotación por modalidad y tipo de tarea | `modalidad, tipo_tarea, anotadas, media_s, p95_s` |
| `q4` | Rechazadas / revisadas por cliente y proyecto | `cliente_id, proyecto_id, rechazadas, revisadas, proporcion` |
| `q5` | Horas de la toma a la aprobación (p50, p95) por cliente y % que incumplió el SLA | `cliente_id, aprobadas, p50_h, p95_h, incumplen_sla, pct_incumple_sla` |

La respuesta trae `filas`, `ms` (lo que tardó Spark), `primera_ejecucion` y `ejecutores`. La primera
consulta después de arrancar tarda más (la SparkSession y los ejecutores se crean al iniciar la analítica,
y la primera ejecución carga clases y genera código). Errores: `404 conjunto_no_existe` si el conjunto no
se ha generado (por ejemplo `grande`), `422` si la consulta o el conjunto no existen, `503` si la analítica
o Spark no están.

Los planes de ejecución (`explain("formatted")`) solo se piden por dentro, a la analítica:

```bash
docker compose exec analitica python3 -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://localhost:8004/plan/1?conjunto=principal'))['plan'])"
```

Con dos workers de Spark (comparación de P5):

```bash
docker compose --profile spark2 up -d spark-worker-2 && docker compose restart analitica
docker compose --profile spark2 rm -sf spark-worker-2 && docker compose restart analitica   # volver a 1
```

`conjunto=prueba` tiene 7 tareas con el resultado de Q1–Q5 calculado a mano en
`dataset/historico/esperado_prueba.json`; `pruebas/c49_analitica_gateway.sh` lo compara por el gateway.

## Pruebas

Cada prueba deja sus archivos en `evidence/`. Antes de cada una conviene recargar el dataset (arriba).

| Prueba | Comando | Evidencia |
|---|---|---|
| Rutas de A en el gateway | `python3 pruebas/a4_rutas_gateway.py` | `A4_rutas_gateway_*.txt` |
| P1 rutas, salud y puertos | Pendiente (B5.1) | |
| P2 50 solicitudes contra 10 tareas | `docker compose --profile carga run --rm -v ./pruebas:/srv/pruebas simulador python /srv/pruebas/p2_concurrencia.py --modo gateway --csv /srv/evidence/P2_<fecha>.csv` | `P2_concurrencia_gateway_*` |
| P3 sesión y borrador | Pendiente (B5.2) | |
| P4 historial por el gateway (frescura, rechazo, lectura en bucle, reprocesar, caída) | `pruebas/p4_gateway.sh` (unos 8 min; recarga MongoDB y el conjunto principal) | `P4_gateway_*` |
| P4 historial directo, sin gateway (incluye reenvío del mismo evento) | `pruebas/p4_historial.sh` | `P4_historial_*` |
| P5 analítica (validación, 1ª vs siguientes, 1 y 2 workers, principal y grande) | `pruebas/p5_analitica.sh` (necesita el conjunto grande y prueba generados) | `P5_analitica_*` |
| Q1–Q5 por el gateway y sus errores | `pruebas/c49_analitica_gateway.sh` | `C4.9_analitica_gateway_*` |
| Compactación de un mes del lago | `pruebas/c26_compactacion.sh <AAAA-MM>` | `C2.6_compactacion_*` |
| P6 carga (10, 40 y 100 usuarios, 3 corridas de 90 s) | `pruebas/p6_carga.sh` (unos 20 min); después `python3 pruebas/p6_lago.py evidence/P6_carga_<fecha>` para ver si los eventos llegaron al lago | `P6_carga_<fecha>/resumen.txt` |
| P8 MongoDB (secundario, primario, sin mayoría) | `pruebas/p8_mongo.sh secundario`, `primario`, `sin-mayoria` | `P8_mongo_gateway_*` |
| P8 Valkey y sesión | Pendiente (B5.3) | |
| Índices (explain) | ver `evidence/A3_explain_registro_20261022-2003.md` | `A3_explain_*` |
| `w: majority` frente a `w: 1` | ver `evidence/A7.4_write_concern_20261022-2011.md` | `A7.4_write_concern_*` |

`pruebas/p8_mongo.sh` mata nodos con `docker compose kill`, vuelve a encenderlos y espera a 1 PRIMARY + 2
SECONDARY antes de terminar. `pruebas/estado_rs.sh` imprime el estado del replica set en cualquier momento.
`pruebas/p6_carga.sh` recarga el dataset y borra las llaves de sesión de Valkey antes de cada corrida.

## Solución de problemas

> Pendiente (B6.4). De lo que encontró A:
>
> - `pruebas/p8_mongo.sh` se cortó a la mitad y quedó un nodo apagado: `docker compose start mongo1 mongo2
>   mongo3` y esperar a que `pruebas/estado_rs.sh` muestre un PRIMARY.
> - `/tareas/siguiente` responde 204 en todo: se agotaron las tareas; recargar el dataset o usar
>   `dataset.regenerar_tareas`.
