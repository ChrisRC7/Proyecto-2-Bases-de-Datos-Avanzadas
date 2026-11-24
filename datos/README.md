# Datos: generación y carga desde cero

El dataset operacional (MongoDB, BD `plataforma`) lo genera `dataset/generar.py` con la semilla
fija **2026**: con la misma semilla salen exactamente los mismos documentos e identificadores, en
cualquier máquina. El histórico de eventos (Parquet) lo genera C en `dataset/historico/`.

## Conteos

| Colección | Documentos | Detalle |
| --- | ---: | --- |
| `clientes` | 60 | |
| `anotadores` | 300 | 30 equipos de 10; el primero de cada equipo es revisor (30 revisores, 10 %). |
| `proyectos` | 120 | Activos. Los 60 cerrados solo existen en el historial (van en `ids.json`). |
| `batches` | 600 | 5 por proyecto, 100 tareas cada uno. |
| `tareas` | 60 000 | 18 000 texto (`span`), 18 000 imagen (`bbox` 70 % / `poligono` 30 %), 12 000 video (`bbox_video`), 12 000 LiDAR (`cuboide_3d`). Todas en `disponible`, versión 0. |

El primer proyecto es el del enunciado: **LiDAR urbano 2026**, SLA de 48 h, con la taxonomía
`lidar#peaton`, `lidar#vehiculo`, `lidar#ciclista` (un skater es ciclista) y `lidar#signal`.

Cada tarea guarda la referencia al archivo (`uri`, `sha256`, `bytes`, `mime`, `meta`), no el
archivo. Los `sha256` son valores aleatorios con el formato correcto, porque los archivos no existen.

## Cargar todo desde cero

Con el replica set arriba (`docker compose up -d`, que también corre `mongo-init`):

```bash
docker compose --profile datos run --rm dataset
```

Borra las cinco colecciones, las vuelve a llenar con `w: "majority"` en lotes de 5 000
(`insert_many` con `ordered=False`), crea los índices de `endpoints/registro.md` e imprime los
conteos. Tarda unos 5 s. También escribe `dataset/salida/ids.json` (clientes, equipos, anotadores
con su equipo y rol, proyectos activos y los 60 cerrados) para el generador del histórico; esa
carpeta no se versiona porque se regenera igual.

Otra semilla: `docker compose --profile datos run --rm dataset python -m dataset.cargar --semilla 7`.

## Cuando las pruebas de carga agotan las tareas

```bash
docker compose --profile datos run --rm dataset \
  python -m dataset.regenerar_tareas --motivo "P6: se agotaron las tareas disponibles"
```

Vuelve a crear batches y tareas con la misma semilla (mismos ids, todas en `disponible`) sin tocar
clientes, anotadores ni proyectos, y anota en `evidence/regeneraciones.md` la fecha, el motivo y
los conteos por estado antes y después. Las corridas de antes y de después no son comparables si
dependían de tareas ya consumidas; por eso queda registrado.

## Histórico de eventos (lago Parquet)

Lo genera `dataset/historico/generar.py` con la misma semilla **2026**: doce meses de eventos, del
2025-10-01 al 2026-09-30 (termina justo donde empieza el dataset de MongoDB). Las tareas son de los
60 proyectos cerrados, que solo existen en el historial, y usan los clientes, equipos y anotadores
de `dataset/salida/ids.json`; si ese archivo no está, los mismos ids se calculan con
`dataset/generar.py` (no hace falta MongoDB). Cada tarea recorre las transiciones del registro y
cada fila sigue el esquema de `endpoints/historial.md`. Se escribe con la misma convención que el
historial (`_tmp/` + rename a `<conjunto>/mes=AAAA-MM/part-<micro_lote_id>.parquet`).

```bash
docker compose --profile datos run --rm dataset-historico                       # principal
docker compose --profile datos run --rm dataset-historico \
  python -m dataset.historico.generar --conjunto grande                          # ~10M
docker compose --profile datos run --rm dataset-historico python -m dataset.historico.prueba
```

Cada uno borra y vuelve a escribir su carpeta (`datalake/eventos` también tiene los eventos que
escribió el historial en operación: se pierden). El resumen queda en `datalake/<conjunto>/_resumen.json`
(empieza con `_`, Spark no lo lee).

| Conjunto | Carpeta | Tareas | Eventos | Duplicados de `event_id` | Archivos | Tamaño | Tiempo |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| principal | `datalake/eventos` | 100 000 | 808 703 | 1 634 | 115 | 47 MB | 19 s |
| grande | `datalake/eventos_grande` | 1 250 000 | 10 107 232 | 20 447 | 271 | 553 MB | 157 s |
| prueba | `datalake/eventos_prueba` | 7 | 42 | 1 | 4 | — | — |

Principal por estado: 229 726 `abierta`, 183 779 `anotada`, 183 727 `en_revision`, 99 942
`aprobada`, 83 785 `rechazada` y 27 744 `liberada` (58 tareas quedan sin aprobar al cierre).
Los duplicados son copias de eventos ya escritos en otro archivo del mismo mes
(`part-<id>-reentrega.parquet`), como un micro-lote entregado dos veces; Spark los quita con
`dropDuplicates(["event_id"])`. Se controla con `--duplicados` (0,002 por defecto).

`prueba` son 7 tareas escritas a mano en `dataset/historico/prueba.py` (con una aprobada repetida, una
liberada y aprobadas justo fuera de la ventana de Q1). Los resultados esperados de Q1–Q5, calculados
a mano, están en `dataset/historico/esperado_prueba.json`.
