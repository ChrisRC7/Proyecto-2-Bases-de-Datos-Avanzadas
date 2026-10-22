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
