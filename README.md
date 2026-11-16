# Proyecto 2 — Plataforma distribuida de anotación de datos

TI-4601 Bases de Datos Avanzadas · Grupo 3 · Fabricio Mena, Alisson Redondo y Christopher Rodriguez.

Gateway REST (único puerto expuesto, 8000) delante de cuatro servicios: registro (MongoDB, replica set de
3 nodos), sesión (Valkey con AOF), historial (lago Parquet) y analítica (Spark). Contratos en
[`endpoints/`](endpoints/README.md), decisiones en [`DECISIONES.md`](DECISIONES.md), avance en
[`TODO.md`](TODO.md) e informe en [`informe/`](informe/).

## Requisitos previos

- Git.
- Docker Desktop con Docker Compose v2.
- Docker Desktop iniciado y con recursos suficientes para MongoDB, Valkey y Spark.
- En Windows (PowerShell) usar `curl.exe` en lugar de `curl` en los ejemplos.
- Python 3.12 para las pruebas en `pruebas/` que corren desde el anfitrión.

Las dependencias internas de cada servicio se instalan mediante sus imágenes Docker.

## Iniciar el sistema

Desde la raíz del repositorio:

```bash
docker compose up -d --build
```

Comprobar el estado de los contenedores:

```bash
docker compose ps
```

Consultar la salud consolidada del sistema:

```bash
curl -sS http://localhost:8000/salud
```

El Gateway es el punto de entrada público mediante el puerto `8000`. Los servicios internos y sus motores de almacenamiento no requieren puertos publicados al equipo anfitrión.

Documentación interactiva del Gateway:

`http://localhost:8000/docs`

## Perfiles de Docker Compose

El archivo `docker-compose.yml` define tres perfiles opcionales.

| Perfil | Uso |
|---|---|
| `carga` | Habilitar el simulador de usuarios |
| `datos` | Habilitar los servicios o tareas de preparación de datos |
| `spark2` | Habilitar un segundo trabajador de Spark |

Ejecutar el sistema con el simulador:

```bash
docker compose --profile carga up -d --build
```

Ejecutar la configuración de preparación de datos:

```bash
docker compose --profile datos up -d --build
```

Habilitar un segundo trabajador de Spark:

```bash
docker compose --profile spark2 up -d --build
```

Los perfiles pueden combinarse:

```bash
docker compose --profile carga --profile spark2 up -d --build
```

La disponibilidad de cada componente depende de sus servicios y dependencias definidos en Docker Compose.

## Comandos de operación

Ver los servicios en ejecución:

```bash
docker compose ps
```

Consultar los últimos registros del Gateway:

```bash
docker compose logs --tail 100 gateway
```

Seguir los registros del servicio de Sesión:

```bash
docker compose logs -f sesion
```

Reiniciar únicamente Valkey:

```bash
docker compose restart valkey
```

Detener los servicios conservando los volúmenes:

```bash
docker compose down
```

Volver a iniciarlos:

```bash
docker compose up -d
```

**Precaución:** los volúmenes persistentes contienen información operacional, sesiones y datos de los motores. No deben eliminarse durante las pruebas habituales.

## Persistencia de datos

El sistema utiliza volúmenes Docker para sus componentes persistentes.

- MongoDB utiliza tres instancias organizadas en el conjunto de réplica `rs0`, en la red `mongo`, que solo
  comparte con el registro (y con la carga de datos).
- Valkey de Sesión utiliza el volumen `valkey_sesion`, con AOF activado y `appendfsync everysec`, en la red
  `valkey`, que solo comparte con el servicio de sesión.
- Historial emplea una instancia Valkey independiente, `cola-eventos`, para la mensajería mediante Streams.
- El lago de datos almacena archivos históricos Parquet y se regenera según el procedimiento del proyecto.

La separación entre los motores ayuda a mantener responsabilidades independientes.

## Principales operaciones del Gateway

| Método | Ruta | Finalidad |
|---|---|---|
| `GET` | `/salud` | Estado consolidado de los componentes |
| `POST` | `/proyectos` | Crear un proyecto |
| `POST` | `/proyectos/{id}/lotes` | Registrar un lote |
| `POST` | `/tareas/siguiente` | Solicitar una tarea |
| `PUT` | `/tareas/{id}/borrador` | Guardar un borrador |
| `POST` | `/sesiones/recuperar` | Recuperar sesión y borrador |
| `POST` | `/sesiones/latido` | Renovar actividad y lock |
| `POST` | `/tareas/{id}/enviar` | Enviar una anotación |
| `POST` | `/tareas/{id}/liberar` | Liberar una asignación |
| `POST` | `/tareas/{id}/revision` | Revisar una tarea |
| `GET` | `/proyectos/{id}/exportar` | Exportar resultados |
| `GET` | `/analitica/{q1..q5}?conjunto=` | Consultas analíticas con Spark |

Los cuerpos JSON, encabezados y códigos HTTP se documentan en `endpoints/gateway.md`.

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
| P1 rutas y sesiones | `python3 pruebas/b51_sesiones_gateway.py` (y `a4_rutas_gateway.py`) | `B5_1_*` |
| P1 `/salud` normal, degradado y caído, y puertos | `pruebas/p1_salud.sh` (para y vuelve a encender historial, analítica, Valkey y dos nodos de MongoDB; unos 3 min) | `P1_salud_*` |
| P2 50 solicitudes contra 10 tareas | `docker compose --profile carga run --rm -v ./pruebas:/srv/pruebas simulador python /srv/pruebas/p2_concurrencia.py --modo gateway --csv /srv/evidence/P2_<fecha>.csv` | `P2_concurrencia_gateway_*` |
| P3 sesión y borrador (interrupción, reinicio de Valkey, vencimiento del lock) | `python3 pruebas/b52_interrupcion_gateway.py`, `b52_persistencia_gateway.py`, `b52_vencimiento_gateway.py` (este espera 120 s). Cada una crea su proyecto con una sola tarea | `B5_2_*` |
| B4.2 borradores y latidos del simulador, interrupción y recuperación | `docker compose --profile carga run --rm -v ./pruebas:/srv/pruebas -w /srv/pruebas simulador python prueba_b42_integracion.py` | |
| P3 con latidos la asignación no vence en MongoDB (más de 120 s) | `python3 pruebas/b52_renovacion_registro.py` (unos 2,5 min) | |
| Borradores huérfanos al reasignar una tarea | `docker compose cp pruebas/prueba_borradores_huerfanos.py sesion:/tmp/h.py && docker compose exec -T -e PYTHONPATH=/srv sesion python /tmp/h.py` | |
| Recorrido completo de una tarea LiDAR (siguiente → borrador → enviar → revisión → exportar → Q1) | `pruebas/recorrido_lidar.sh` | `I2_recorrido_lidar_*` |
| Aislamiento modular (puertos, redes, variables, bibliotecas, lago en solo lectura) | `pruebas/i4_aislamiento.sh` | `I4_aislamiento_*` |
| Todas las anteriores, en orden, desde un entorno limpio (`down -v`) | `pruebas/i5_todas.sh` (hora y media aprox.) | `I5_entorno_limpio_*` |
| P4 historial por el gateway (frescura, rechazo, lectura en bucle, reprocesar, caída) | `pruebas/p4_gateway.sh` (unos 8 min; recarga MongoDB y el conjunto principal) | `P4_gateway_*` |
| P4 historial directo, sin gateway (incluye reenvío del mismo evento) | `pruebas/p4_historial.sh` | `P4_historial_*` |
| P5 analítica (validación, 1ª vs siguientes, 1 y 2 workers, principal y grande) | `pruebas/p5_analitica.sh` (necesita el conjunto grande y prueba generados) | `P5_analitica_*` |
| Q1–Q5 por el gateway y sus errores | `pruebas/c49_analitica_gateway.sh` | `C4.9_analitica_gateway_*` |
| Compactación de un mes del lago | `pruebas/c26_compactacion.sh <AAAA-MM>` | `C2.6_compactacion_*` |
| P6 carga (10, 40 y 100 usuarios, 3 corridas de 90 s) | `pruebas/p6_carga.sh` (unos 20 min); después `python3 pruebas/p6_lago.py evidence/P6_carga_<fecha>` para ver si los eventos llegaron al lago | `P6_carga_<fecha>/resumen.txt` |
| P8 MongoDB (secundario, primario, sin mayoría) | `pruebas/p8_mongo.sh secundario`, `primario`, `sin-mayoria` | `P8_mongo_gateway_*` |
| P8 Valkey y sesión (RTO/RPO) | `python3 pruebas/b53_fallo_valkey.py` | `B5_3_P8.txt` |
| Índices (explain) | ver `evidence/A3_explain_registro_20261022-2003.md` | `A3_explain_*` |
| `w: majority` frente a `w: 1` | ver `evidence/A7.4_write_concern_20261022-2011.md` | `A7.4_write_concern_*` |

`pruebas/p8_mongo.sh` mata nodos con `docker compose kill`, vuelve a encenderlos y espera a 1 PRIMARY + 2
SECONDARY antes de terminar. `pruebas/estado_rs.sh` imprime el estado del replica set en cualquier momento.
`pruebas/p6_carga.sh` recarga el dataset y borra las llaves de sesión de Valkey antes de cada corrida.

Las pruebas que reinician Valkey o paran servicios interrumpen las sesiones en curso: correrlas sin otros
usuarios ni otras pruebas al mismo tiempo.

## Solución de problemas

### El Gateway no responde

Comprobar los contenedores y revisar los registros:

```bash
docker compose ps
docker compose logs --tail 100 gateway
```

Si el Gateway está levantado, consultar:

```bash
curl -i http://localhost:8000/salud
```

### El estado de salud aparece degradado o caído

La ruta `/salud` muestra el estado de los componentes. Revisar los registros del servicio indicado y sus dependencias.

```bash
docker compose logs --tail 100 sesion
docker compose logs --tail 100 registro
```

### No se pueden guardar borradores

Comprobar que el anotador tenga una sesión activa y utilice el token correcto. Los locks vencen después de 120 segundos si no se renuevan.

Un error HTTP 409 puede indicar que el lock venció o que el token no coincide.

### Una sesión no aparece al recuperarla

La respuesta HTTP 404 `sesion_no_existe` indica que no hay una sesión registrada para el anotador consultado.

Comprobar que se envíe correctamente el encabezado `X-Anotador-Id`.

### MongoDB no está disponible

Revisar los contenedores del conjunto de réplica:

```bash
docker compose ps
docker compose logs --tail 100 mongo1
```

### Spark no responde

Comprobar los servicios de Analítica y Spark:

```bash
docker compose ps
docker compose logs --tail 100 analitica
docker compose logs --tail 100 spark-master
```

### Un archivo de evidencia aparece como binario en Git

Algunas versiones de PowerShell pueden guardar la salida de `Tee-Object` con una codificación distinta de UTF-8. Conviene convertir los archivos de evidencia a UTF-8 antes de agregarlos al repositorio.

### `pruebas/p8_mongo.sh` se cortó y quedó un nodo apagado

```bash
docker compose start mongo1 mongo2 mongo3
pruebas/estado_rs.sh   # esperar a ver un PRIMARY
```

### `/tareas/siguiente` responde 204 siempre

Se agotaron las tareas: recargar el dataset o usar `dataset.regenerar_tareas` (ver «Carga de datos»).

## Documentación adicional

- `DECISIONES.md`: decisiones técnicas y justificaciones.
- `TODO.md`: distribución de responsabilidades y avance del proyecto.
- `endpoints/`: documentación de las interfaces HTTP.
- `informe/`: secciones preparadas para el informe final.
- `evidence/`: resultados de las pruebas.

## Observaciones de integración

Las dos observaciones que dejaron las pruebas de B quedaron resueltas:

- Una sesión cuyo lock venció (o cuya tarea ya tomó otro anotador) se descarta al recuperarla:
  `POST /sesiones/recuperar` responde `404 sesion_no_existe` en lugar de devolver una tarea ajena.
- El latido y el borrador renuevan también `asignacion.vence_en` en MongoDB, así que el registro no ofrece a
  otro una tarea cuyo lock sigue vivo (`pruebas/b52_renovacion_registro.py`).

Las limitaciones que siguen abiertas están en la sección de limitaciones del informe.
