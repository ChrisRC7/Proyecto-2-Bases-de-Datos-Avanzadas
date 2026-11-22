# Guía de ejecución para la defensa

Cómo levantar todo desde cero, cargar los datos, mostrar el sistema funcionando y correr cada prueba
(P1–P8). Todos los comandos se corren **desde la raíz del repositorio**. Las preguntas cruzadas están en
[`preguntas.md`](preguntas.md); el detalle de cada servicio en [`../endpoints/`](../endpoints/README.md).

- En **Linux o WSL** los comandos van tal cual.
- En **Windows (PowerShell)**: usar `curl.exe` en vez de `curl`, y correr los `.sh` desde WSL o Git Bash.
- Los scripts `.sh` y los `.py` de `pruebas/` corren en el anfitrión con **Python 3.12** (solo biblioteca
  estándar; `p6_lago.py` necesita `pip install pyarrow`).

---

## 1. Antes de la defensa (el día anterior)

1. Correr la batería completa desde cero, que es justo lo que pide el enunciado (§13: «la solución no se
   considera reproducible si no puede iniciarse siguiendo el README en un entorno limpio»):

   ```bash
   bash pruebas/i5_todas.sh        # ~1 h 10 min; borra volúmenes, levanta, carga y corre todas las pruebas
   ```

   **No editar ningún archivo de `pruebas/` mientras corre**: bash lee el script a medida que lo ejecuta y
   un cambio a mitad de camino lo corta con «error de sintaxis» (pasó el 2026-11-21 al agregar comentarios).

   Al terminar, revisar el resumen `evidence/I5_entorno_limpio_<fecha>.txt`: **todas las líneas deben decir
   `exit=0`**. Si alguna no, la salida completa está en el `.log` del mismo nombre.

2. Dejar el sistema arriba y con datos frescos para la demo (sección 2, pasos 3 a 5).
3. Tener abiertos: este archivo, `informe/informe.pdf`, `defensa/preguntas.md` y una terminal en la raíz.

---

## 2. Levantar todo desde cero (≈ 5 min)

| Paso | Comando | Tiempo | Qué debe pasar |
|---|---|---|---|
| 1. (Opcional) borrar todo | `docker compose down -v` | 20 s | Se borran MongoDB, Valkey, la cola de eventos y la cola del gateway. |
| 2. Levantar | `docker compose up -d --build` | 1–3 min la primera vez | 12 contenedores; `mongo-init` arma el replica set y termina. |
| 3. Cargar MongoDB | `docker compose --profile datos run --rm dataset` | ~10 s | Imprime 60 clientes, 120 proyectos, 600 batches, 60 000 tareas, 300 anotadores. |
| 4. Generar el lago | `docker compose --profile datos run --rm dataset-historico` | ~20 s | ~808 703 eventos en `datalake/eventos`. |
| | `docker compose --profile datos run --rm dataset-historico python -m dataset.historico.prueba` | 1 s | 42 eventos con resultado conocido (`datalake/eventos_prueba`). |
| | (solo para P5) `docker compose --profile datos run --rm dataset-historico python -m dataset.historico.generar --conjunto grande` | ~3 min | ~10 M eventos, 553 MB. |
| 5. Que Spark vea los conjuntos nuevos | `docker compose restart analitica` | 10 s | |
| 6. Comprobar | `curl -s localhost:8000/salud` | | `"estado":"operativo"` y las cuatro capas `operativo` (Spark tarda ~20 s en arrancar). |

> **Cuidado:** el perfil `datos` **borra y recarga**. No usar `docker compose --profile datos up`; siempre
> `run --rm`.

Para ver el estado: `docker compose ps` (todo `healthy`, `mongo-init` en `Exited (0)`),
`pruebas/estado_rs.sh` (1 PRIMARY + 2 SECONDARY), `docker compose logs --tail 50 gateway`.

---

## 3. Demo en vivo (≈ 10 min)

Orden sugerido; cada bloque dice qué se está demostrando para poder explicarlo mientras corre.

### 3.1 Solo el gateway está expuesto y cada servicio solo ve su motor (aislamiento, 20 s)

```bash
docker compose ps --format '{{.Service}}\t{{.Ports}}'     # solo gateway tiene 0.0.0.0:8000
bash pruebas/i4_aislamiento.sh                              # puertos, redes, variables, bibliotecas, lago ro
```
Mirar la tabla «A qué motores llega cada servicio»: registro solo resuelve `mongo1`, sesión solo
`valkey`, historial solo `cola-eventos`, analítica solo `spark-master`, el simulador solo `gateway`.

### 3.2 Recorrido de una tarea LiDAR a mano (2 min)

```bash
# Ids del dataset (semilla fija: son siempre los mismos)
eval "$(python3 - <<'PY'
import json; d = json.load(open("dataset/salida/ids.json"))
an = [a["id"] for a in d["anotadores"] if a["rol"] == "anotador"]
rv = [a["id"] for a in d["anotadores"] if a["rol"] == "revisor"]
print(f"ANOT={an[0]} OTRO={an[1]} REV={rv[0]} PROY={d['proyecto_lidar_ejemplo']}")
PY
)"

# 1. Pedir tarea (el proyecto LiDAR urbano 2026 del enunciado)
curl -s -X POST localhost:8000/tareas/siguiente -H "X-Anotador-Id: $ANOT" \
     -H 'Content-Type: application/json' -d "{\"proyecto_id\":\"$PROY\"}" | tee /tmp/t.json; echo
TAREA=$(python3 -c "import json;print(json.load(open('/tmp/t.json'))['tarea']['tarea_id'])")
TOKEN=$(python3 -c "import json;print(json.load(open('/tmp/t.json'))['token'])")

# 2. Borrador (primero peatón, después corrige: un skater es ciclista)
curl -s -X PUT localhost:8000/tareas/$TAREA/borrador -H "X-Anotador-Id: $ANOT" \
     -H 'Content-Type: application/json' -d "{\"token\":\"$TOKEN\",\"contenido\":{\"etiquetas\":[\"lidar#ciclista\"]}}"; echo

# 3. "Se cae el navegador": recuperar sesión y borrador
curl -s -X POST localhost:8000/sesiones/recuperar -H "X-Anotador-Id: $ANOT"; echo

# 4. Otro anotador no puede editar con un token inventado → 409
curl -s -o /dev/null -w '%{http_code}\n' -X PUT localhost:8000/tareas/$TAREA/borrador -H "X-Anotador-Id: $OTRO" \
     -H 'Content-Type: application/json' -d '{"token":"00000000-0000-4000-8000-000000000000","contenido":{}}'

# 5. Enviar → 200; enviar otra vez → 409 (y no se escribe fila en el lago)
ANOTACION='{"etiquetas":["lidar#ciclista"],"geometria":{"tipo":"cuboide_3d","centro":[1.2,3.4,0.5],"dimensiones":[0.6,1.8,1.7],"yaw":0.3}}'
curl -s -X POST localhost:8000/tareas/$TAREA/enviar -H "X-Anotador-Id: $ANOT" \
     -H 'Content-Type: application/json' -d "{\"token\":\"$TOKEN\",\"anotacion\":$ANOTACION}"; echo
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/tareas/$TAREA/enviar -H "X-Anotador-Id: $ANOT" \
     -H 'Content-Type: application/json' -d "{\"token\":\"$TOKEN\",\"anotacion\":$ANOTACION}"

# 6. Revisión: empezar y aprobar (o rechazar con motivo: {"accion":"rechazar","motivo":"ETIQUETA_INCORRECTA"})
for a in empezar aprobar; do
  curl -s -X POST localhost:8000/tareas/$TAREA/revision -H "X-Anotador-Id: $REV" \
       -H 'Content-Type: application/json' -d "{\"accion\":\"$a\"}"; echo
done

# 7. Exportar aprobadas y ver la fila en Spark (Q1) unos 10 s después
curl -s "localhost:8000/proyectos/$PROY/exportar?limite=5" | head -c 600; echo
sleep 12; curl -s "localhost:8000/analitica/q1?conjunto=principal" | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(d['parametros'], [f for f in d['filas'] if f['modalidad']=='lidar'][-2:])"
```

Lo mismo automatizado y con tiempos: `bash pruebas/recorrido_lidar.sh` (30 s; termina en
`RECORRIDO_LIDAR_OK` y dice a los cuántos segundos se vio en Q1).

### 3.3 Cero dobles asignaciones: 50 contra 10 (P2, 10 s)

```bash
pruebas/estado_rs.sh
docker compose --profile carga run --rm -v ./pruebas:/srv/pruebas simulador \
  python /srv/pruebas/p2_concurrencia.py --modo gateway --csv /srv/evidence/P2_demo.csv
```
Mirar las líneas `OK:` del final: `10 asignaciones (200)`, `40 sin tarea (204)`, `ninguna tarea con dos
anotadores`, `enviar con token ajeno -> 409`, `segundo envío de una tarea ya enviada -> 409`.

### 3.4 Sesión: interrupción y reinicio de Valkey (P3 y P8 de Valkey, 10 s)

```bash
python3 pruebas/b52_interrupcion_gateway.py   # RECUPERACION_EN_0.0xx_SEGUNDOS · B5_2_INTERRUPCION_OK
python3 pruebas/b53_fallo_valkey.py           # latidos 200 → 503 → 200 · RTO_OBSERVADO_SEGUNDOS ≈ 1 · B5_3_P8_OK
```
Decir: el reinicio es un apagado limpio y Valkey sincroniza el AOF; la ventana de `everysec` (hasta ~1 s)
solo se pierde con una caída sin aviso, que no se midió (informe §7.2).

### 3.5 `/salud` normal, degradado y caído (1 min)

```bash
docker compose stop historial;  sleep 3; curl -s localhost:8000/salud | head -c 300; echo   # degradado
docker compose start historial
docker compose stop valkey;     sleep 3; curl -s localhost:8000/salud | head -c 300; echo   # caido (sin sesión)
docker compose start valkey;    sleep 8; curl -s localhost:8000/salud | head -c 120; echo   # operativo
```
`/salud` siempre responde 200. Cada capa trae `fuente` y `mensaje_valkey`: los mensajes que publican
registro, historial y analítica cada 5 s en Valkey (TTL 15 s) más una consulta directa; gana el peor.

### 3.6 Caída del primario de MongoDB (P8, ~1,5 min)

```bash
bash pruebas/p8_mongo.sh primario
```
Mirar: 503 durante unos 10 s (la elección, `electionTimeoutMillis`), luego 200; «confirmados en MongoDB,
versión esperada: N de N» → RPO 0. Al final vuelve a encender el nodo y espera 1 PRIMARY + 2 SECONDARY.

### 3.7 Analítica (1 min)

```bash
bash pruebas/c49_analitica_gateway.sh                     # q1..q5: OK = iguales a lo calculado a mano
docker compose exec analitica python3 -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://localhost:8004/plan/1?conjunto=principal'))['plan'])" | head -40
```
En el plan de Q1 mirar `PartitionFilters: [isnotnull(mes), (mes >= ...)]` (solo lee los meses de la ventana).

---

## 4. Cada prueba del enunciado

Todas dejan su archivo en `evidence/` con fecha y hora en el nombre. Las de Python imprimen en pantalla;
para guardarlas: `python3 pruebas/<x>.py | tee evidence/<x>_$(date +%Y%m%d-%H%M).txt`.

| Prueba | Qué demuestra | Comando | Tiempo | Señal de éxito | Evidencia citada en el informe |
|---|---|---|---|---|---|
| **P1** rutas | Rutas del gateway y sus errores 404/409/422 | `python3 pruebas/a4_rutas_gateway.py` y `python3 pruebas/b51_sesiones_gateway.py` | 5 s | `FALLAS: 0` · `B5_1_SESIONES_OK` | `A4_rutas_gateway_20261110-1446.txt`, `B5_1_*` |
| **P1** salud y puertos | `/salud` normal, degradado y caído; solo el 8000 publicado | `bash pruebas/p1_salud.sh` | ~3 min | El archivo muestra los tres estados y vuelve a `operativo` | `P1_salud_20261115-2307.txt` |
| **P2** concurrencia | 50 solicitudes contra 10 tareas, 0 dobles, 409 | ver 3.3 | 10 s | Líneas `OK:` (10 × 200, 40 × 204, ninguna con dos anotadores, 409) | `P2_concurrencia_gateway_20261110-1714.md` |
| **P3** interrupción | Recuperar el borrador de una sesión interrumpida | `python3 pruebas/b52_interrupcion_gateway.py` | 1 s | `B5_2_INTERRUPCION_OK` | `B5_2_interrupcion.txt` |
| **P3** reinicio Valkey | El borrador sobrevive a `docker compose restart valkey` (AOF) | `python3 pruebas/b52_persistencia_gateway.py` | 5 s | `B5_2_PERSISTENCIA_AOF_OK` | `B5_2_persistencia.txt` |
| **P3** vencimiento | Al vencer el lock la tarea vuelve a ofrecerse; el token viejo → 409 | `python3 pruebas/b52_vencimiento_gateway.py` | **2 min** (espera 123 s) | `B5_2_VENCIMIENTO_EXCLUSIVIDAD_OK` | `B5_2_vencimiento.txt` |
| P3 plazo en MongoDB | Con latidos la asignación no vence aunque pasen 150 s (D16) | `python3 pruebas/b52_renovacion_registro.py` | 2,5 min | `B5_2_RENOVACION_REGISTRO_OK` | `I5_entorno_limpio_20261115-2205.log` |
| P3 borrador de otro | Un borrador viejo no se le entrega al nuevo dueño | `docker compose cp pruebas/prueba_borradores_huerfanos.py sesion:/tmp/h.py && docker compose exec -T -e PYTHONPATH=/srv sesion python /tmp/h.py` | 1 s | `REASIGNACION_BORRADORES_OK` | `I5_entorno_limpio_20261115-2205.txt` |
| B4.2 simulador real | Usuario virtual con borradores y latidos cada 2 s, matado y recuperado | `docker compose --profile carga run --rm -v ./pruebas:/srv/pruebas -w /srv/pruebas simulador python prueba_b42_integracion.py` | 30 s | `B4.2_INTEGRACION_OK` | `B4_2_*` |
| **P4** lago | Frescura, fila `rechazada` + MongoDB `disponible` + Q4, lector Spark en bucle sin lecturas a medias, reprocesar, caída del historial sin pérdidas | `bash pruebas/p4_gateway.sh` | ~8 min | `via_gateway: n=5 min=… mediana=… max=… s`; rechazo `disponible` + segundo envío 409; sin pérdidas tras la caída del historial | `P4_gateway_20261115-1102.md` |
| P4 directo | Reenvío del mismo `event_id` (deduplicación), sin gateway | `bash pruebas/p4_historial.sh` | ~6 min | | `P4_historial_20261030-1431.md` |
| Compactación | Juntar un mes: mismos Q1–Q5 antes y después | `bash pruebas/c26_compactacion.sh` | 30 s | `qN: igual antes y después` en las cinco | `C2.6_compactacion_20261115-1113.txt` |
| **P5** analítica | Validación, 1ª vs siguientes, 1 y 2 workers, principal y grande, planes de Q1 y Q3 | `bash pruebas/p5_analitica.sh` (necesita el conjunto grande) | ~15 min | Bajo «Validación contra conjunto=prueba», `qN: OK` en las cinco | `P5_analitica_20261030-1410.md` |
| Q1–Q5 por gateway | Las consultas y sus errores 422/404/503 | `bash pruebas/c49_analitica_gateway.sh` | 2 min | `qN: OK` en las cinco; 422, 404 y 503 donde corresponde | `C4.9_analitica_gateway_20261115-1057.txt` |
| **P6** carga | 10, 40 y 100 usuarios, 30 s + 3 × 90 s; p50/p95/p99, envíos/min, errores, `docker stats` | `bash pruebas/p6_carga.sh` (y después `python3 pruebas/p6_lago.py evidence/P6_carga_<fecha>`) | ~20 min | `resumen.txt` con las tres tablas | `P6_carga_20261110-1800.md` |
| **P8** MongoDB | Cae un secundario, el primario y dos nodos; RTO y RPO | `bash pruebas/p8_mongo.sh secundario`, `primario`, `sin-mayoria` | ~1,5 min c/u | RPO `0` en los tres; con 1 nodo caído sigue escribiendo, sin mayoría 503 | `P8_mongo_gateway_20261110-1715.md` |
| **P8** Valkey | Reinicio de Valkey con sesión activa; RTO y RPO del borrador | `python3 pruebas/b53_fallo_valkey.py` | 5 s | `B5_3_P8_OK` | `B5_3_P8.txt` |
| I2 recorrido | Una tarea LiDAR de punta a punta hasta Q1 | `bash pruebas/recorrido_lidar.sh` | 30 s | `RECORRIDO_LIDAR_OK` | `I2_recorrido_lidar_20261115-2217.txt` |
| I4 aislamiento | Reglas de aislamiento del enunciado §4 | `bash pruebas/i4_aislamiento.sh` | 20 s | Tablas del archivo | `I4_aislamiento_20261115-2218.txt` |
| Todas | Todo lo anterior desde `down -v` | `bash pruebas/i5_todas.sh` | ~1 h 10 min | Todas `exit=0` | `I5_entorno_limpio_20261115-2205.txt` y `I5_entorno_limpio_20261121-2215.txt` |
| Unitarias | Lógica del gateway (10) y de la sesión (8) | `for f in servicios/gateway/app/prueba_*.py; do docker compose exec -T gateway python -m app.$(basename $f .py) \| tail -1; done` (igual con `sesion`) | 1 min | Cada una termina en `..._OK` | — |

P7 no existe en el enunciado (salta de P6 a P8).

**Qué pruebas cambian el sistema:** `p1_salud.sh`, `p8_mongo.sh`, `b52_persistencia`, `b53_fallo_valkey` y
`p4_gateway.sh` paran o reinician contenedores (todas los vuelven a encender solas). `p4_*`, `p6_carga.sh`
y `p2` en modo registro recargan MongoDB; `p4_*` regenera el lago principal. No correrlas al mismo tiempo
que otra prueba ni con el simulador andando.

---

## 5. Cifras clave (para responder sin buscar)

| Qué | Valor | Archivo |
|---|---|---|
| P2: 50 contra 10 | 10 × 200, 40 × 204, 0 dobles; p50/p95/p99 cliente 324 / 500 / 510 ms | `P2_concurrencia_gateway_20261110-1714.md` |
| `w: majority` vs `w: 1` (1 hilo) | `enviar` p50 11,8 vs 8,8 ms; 24 % menos ciclos/s | `A7.4_write_concern_20261022-2011.md` |
| P8 MongoDB | RPO 0 en los tres; RTO primario 10,45 s; sin mayoría 10,24 s desde que vuelven los nodos | `P8_mongo_gateway_20261110-1715.md` |
| P3 / P8 Valkey | Recuperar 0,047 s; RTO 1,016 s; borrador sin pérdida (edad 2,0 s) | `B5_2_interrupcion.txt`, `B5_3_P8.txt` |
| Frescura | Un evento se ve en Spark entre 6 y 12 s después del 200 | `P4_gateway_20261115-1102.md` |
| P5 principal (1 worker) | 1ª ejecución 1,8–3,2 s; siguientes ~1,4–1,8 s | `P5_analitica_20261030-1410.md` |
| P6 | Máximo ~665 envíos/min con 40 usuarios; el cuello es el gateway (un proceso, ~100 % de un núcleo) | `P6_carga_20261110-1800.md` |

---

## 6. Si algo falla

| Síntoma | Qué hacer |
|---|---|
| `/salud` dice `analitica: caido` al arrancar | Spark tarda ~20 s; esperar. Si sigue: `docker compose restart analitica`. |
| `/tareas/siguiente` siempre 204 | Se agotaron las tareas: `docker compose --profile datos run --rm dataset`. |
| `404 conjunto_no_existe` en la analítica | Falta generar ese conjunto (sección 2, paso 4) y `docker compose restart analitica`. |
| Una prueba de P8 se cortó y quedó un nodo apagado | `docker compose start mongo1 mongo2 mongo3` y `pruebas/estado_rs.sh` hasta ver un PRIMARY. |
| Valkey quedó parado | `docker compose start valkey`. |
| `No se encontró ids.json` | Correr la carga de MongoDB (sección 2, paso 3): ese comando lo genera. |
| Un `.sh` no corre en Windows | Correrlo desde WSL o Git Bash. |
| Todo raro | `docker compose down -v && docker compose up -d --build` y volver a la sección 2. |
