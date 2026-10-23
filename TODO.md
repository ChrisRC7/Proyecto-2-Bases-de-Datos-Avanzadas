# TODO — Proyecto 2: Plataforma distribuida de anotación de datos

> Documento vivo y versionado. **Cada commit que avance una tarea debe actualizar este archivo**
> en el mismo commit (marcar la casilla, ajustar el resumen y la fecha).
> Leyenda: `[ ]` pendiente · `[~]` en progreso / a medias · `[x]` hecho · `[!]` bloqueado (decir por qué).
> Contratos entre servicios: [`endpoints/`](endpoints/README.md) · Decisiones técnicas: [`DECISIONES.md`](DECISIONES.md).

## Resumen de avance

| Persona | Rol principal | Hechas / Total | Notas |
|---|---|---|---|
| A | Registro (MongoDB) + dataset operacional + simulador + carga | 22 / 34 | A1, A2, A3, A5 y A6 listos; P2 preliminar (faltan rutas del gateway de B) |
| B | Sesión (Valkey) + Gateway + salud | 0 / 29 | |
| C | Historial (Parquet) + Analítica (Spark) + dataset histórico | 0 / 31 | |
| Todos | Integración, informe, defensa | 0 / 12 | |

_Última actualización: 2026-10-22 — corrección del 500 sin mayoría en registro (Fabricio)._

---

## Cómo está dividido el trabajo

Cada persona es **dueña de un "carril vertical"** (su motor de datos + su servicio + sus pruebas +
su parte del dataset + su sección del informe + sus rutas en el gateway). Así:

- **Todos tocan todas las capas**: A, B y C escriben código de servicio, rutas del gateway,
  infraestructura docker, generación de datos, pruebas con evidencia y secciones del informe.
- **Nadie bloquea a nadie**: los contratos ya están definidos en `endpoints/`; cada quien trabaja contra **stubs**
  (servicios falsos que respetan el contrato) hasta que el servicio real esté listo.
- **Conocimiento cruzado** (exigido en la defensa): revisión rotativa de PRs
  (A revisa a B, B revisa a C, C revisa a A) + sesión de explicación cruzada en la semana 4.

| Área | A | B | C |
|---|---|---|---|
| Infra docker | MongoDB rs de 3 nodos | Valkey AOF de sesión | cola-eventos (Valkey del historial) + Spark + volúmenes del lago |
| Servicio | registro | sesion + gateway (núcleo) | historial + analitica |
| Rutas gateway | /proyectos, /lotes, /exportar, /revision | /tareas/siguiente, /borrador, /enviar, /liberar, /sesiones/recuperar, /salud | /analitica/{q} |
| Dataset | clientes, proyectos, batches, tareas, anotadores (Mongo) | casos controlados de sesión + script de "reset" | ~800k eventos históricos + eventos_grande + casos conocidos Q1–Q5 |
| Simulador | núcleo (anotador/revisor, concurrencia) | borradores cada 2 s, interrupción y recuperación | consumidor de analítica |
| Pruebas | P2, P6, P8-Mongo | P1, P3, P8-Valkey | P4, P5 |
| Informe | §2 registro, §6 carga, §7 fallas Mongo | §1 arquitectura, §3 sesión, §7 fallas Valkey | §4 lago, §5 Spark, §8 limitaciones |

Calendario sugerido (5 semanas): **S1** infra + stubs · **S2–S3** servicios y
dataset · **S3–S4** integración y pruebas · **S5** informe, README final, ensayo de defensa.

---

## Base del proyecto (hecho)

- Estructura: `servicios/{gateway,registro,sesion,historial,analitica}`, `endpoints/`, `dataset/`,
  `datos/`, `simulador/`, `pruebas/`, `evidence/`, `datalake/` (el repo es la carpeta `` del enunciado, ver D10).
- Contratos en `endpoints/`: convenciones comunes (`X-Request-Id`, formato de error, códigos HTTP,
  puertos), API pública del gateway, API interna de cada servicio (≤ 6 rutas), modelo MongoDB e
  índices, llaves Valkey, esquema del evento Parquet y layout del lago, definición de Q1–Q5.
- Decisiones D1–D12 en `DECISIONES.md`.
- Plantilla común de servicio (`app/comun.py`: logging JSON, `X-Request-Id`, formato de error;
  `Dockerfile`; `/salud`) copiada en los 5 servicios.
- `docker-compose.yml` con redes `interna` y `borde`, solo el gateway publica `8000:8000`, y
  comentarios donde cada quien agrega su bloque. Verificado: `docker compose up -d --build`
  levanta los 5 servicios `healthy`.
- Plantilla de evidencia `evidence/_PLANTILLA.md`.

---

## Persona A — Registro (MongoDB) · dataset operacional · simulador · carga

### A1. Infraestructura MongoDB
- [x] A1.1 Servicios `mongo1`, `mongo2`, `mongo3` (`mongo:7`) con `--replSet rs0`, volúmenes, healthcheck. Sin puertos al host.
- [x] A1.2 Contenedor/script `mongo-init` que ejecuta `rs.initiate()` idempotente y espera PRIMARY.
- [x] A1.3 Script que imprime `rs.status()` resumido (para P2 y P8).

### A2. Servicio `registro` (≤ 6 rutas, solo MongoDB, **nunca** Valkey)
- [x] A2.1 Esqueleto FastAPI + conexión con `w="majority"` configurable por variable de entorno (`MONGO_W=majority|1`).
- [x] A2.2 Stub inicial que responde según contrato (para que B y C no esperen). Publicarlo en S1.
      Nota: no hizo falta stub: el servicio real ya responde según el contrato (A2.3 a A2.8).
- [x] A2.3 `POST /proyectos`: valida cliente, taxonomía (etiqueta + instrucción) y `sla_horas`.
- [x] A2.4 `POST /proyectos/{id}/lotes`: inserta batch + tareas (`insert_many` ordenado=false), valida sha256/uri/mime/modalidad/geometría.
- [x] A2.5 `POST /tareas/asignar`: `findOneAndUpdate` atómico (`disponible` **o** `abierta` con `vence_en < now`) ordenado por prioridad → `abierta`, `version+1`, `asignacion{anotador_id, token, tomada_en, vence_en}`. Devuelve la tarea o 204.
- [x] A2.6 `POST /tareas/{id}/transicion`: enviar / empezar revisión / aprobar / rechazar (exige motivo) / liberar. Filtro por `estado`, `asignacion.token` y `version` → si no matchea, 409. Rechazo deja `disponible` y limpia asignación.
- [x] A2.7 Cada transición devuelve los datos necesarios para que el gateway arme el evento (`event_id`, estado del lago — `rechazada` en rechazo —, `tomada_ts`, `sla_horas`, `equipo_id`...) y guarda `ultimo_evento_id`.
- [x] A2.8 `GET /proyectos/{id}/exportar?pagina=&tamano=` paginado por `_id` (cursor/keyset, no `skip` grande).
      Nota: se usa ?cursor=&limite= como en endpoints/gateway.md y registro.md.
- [~] A2.9 `GET /salud` (incluye si hay PRIMARY) + latido cada 5 s a `POST sesion:/estado-capas` (D4); nunca escribe directo en Valkey.
      Nota: salud y latido listos; falta verlo llegar al /estado-capas real de sesion (B2.7).
- [x] A2.10 Timeouts cortos a Mongo (`serverSelectionTimeoutMS`) para que el gateway pueda devolver 503 rápido.

### A3. Índices
- [x] A3.1 Crear índices mínimos: `tareas(proyecto_id, estado, prioridad)`, `tareas(asignacion.anotador_id, estado)`, `tareas(batch_id)`, `proyectos(cliente_id, estado)`.
- [x] A3.2 `explain("executionStats")` de la consulta de asignación y de exportación → guardar en `evidence/`.
- [x] A3.3 Justificar (con explain/medición) cualquier índice extra (p. ej. para tareas `abierta` vencidas).
      Nota: extra tareas {estado:1, prioridad:-1} para la asignación sin proyecto (60 000 → 1 documento examinado); la exportación no lleva índice propio. Ver evidence/A3_explain_registro_20261022-2003.md.

### A4. Rutas del gateway (sobre el núcleo de B)
- [ ] A4.1 `POST /proyectos`, `POST /proyectos/{id}/lotes`, `GET /proyectos/{id}/exportar` → registro.
- [ ] A4.2 `POST /tareas/{id}/revision`: empezar revisión / aprobar / rechazar → registro; si 200, publicar evento a historial (función de B).

### A5. Dataset operacional (`dataset/`)
- [x] A5.1 Generador reproducible (semilla fija) de 60 clientes, 120 proyectos activos, 600 batches, 60 000 tareas (30/30/20/20 % texto/imagen/video/LiDAR con geometría correcta), 300 anotadores en 30 equipos (10 % revisores). Incluir el proyecto LiDAR de ejemplo con su taxonomía.
- [x] A5.2 Exportar IDs de los 60 proyectos cerrados y de clientes/anotadores para que C los use en el histórico (archivo compartido `dataset/salida/ids.json`).
      Nota: dataset/salida/ está en .gitignore: el ids.json se obtiene corriendo la carga (semilla 2026, mismos ids en cualquier máquina).
- [x] A5.3 Script de carga desde cero (`make cargar` o `python -m dataset.cargar`) que borra y recarga; imprime conteos. Documentar en `datos/README.md`.
      Nota: docker compose --profile datos run --rm dataset · documentado en datos/README.md.
- [x] A5.4 Script "regenerar tareas disponibles" para cuando las pruebas de carga las agoten (anota fecha y motivo en evidence).

### A6. Simulador (`simulador/`) — solo HTTP al gateway
- [x] A6.1 Cliente HTTP base (`httpx`), configuración por CLI (URL, número de usuarios, duración).
- [~] A6.2 Flujo anotador: siguiente → (borradores, de B) → enviar; flujo revisor: revisión → aprobar/rechazar con motivo aleatorio.
      Nota: anotador y revisor listos y probados con un gateway de prueba que reenvía al registro real (10 usuarios, 113 envíos en 15 s, sin errores). Falta correrlo contra el gateway de B (B3.4-B3.7); los borradores cada 2 s se enganchan en anotar() (B4.2).
- [x] A6.3 Registro de latencias por endpoint (CSV crudo) para P6.
      Nota: CSV con ts, fase (calentamiento/medicion), usuario, rol, endpoint, status y ms; resumen p50/p95/p99 por endpoint y envíos/min.

### A7. Pruebas
- [~] A7.1 **P2**: estado del replica set + script con `ProcessPoolExecutor`: preparar exactamente 10 tareas libres, 50 solicitudes simultáneas → verificar 10 asignaciones, 40 sin tarea (204), ninguna tarea con 2 anotadores (consulta de verificación vía gateway/export), segundo envío → 409. Evidencia.
      Nota: corrida preliminar contra el registro, sin gateway: 10/40, cero dobles, 409 en el reenvío (evidence/P2_concurrencia_registro_20261022-2007.md). Falta repetirla vía gateway (B3.4, B3.6).
- [ ] A7.2 **P6**: 10, 40, 100 usuarios virtuales; 30 s calentamiento + 3 corridas de 90 s por nivel; p50/p95/p99 por endpoint, envíos/min, errores, `docker stats` muestreado. Características de la máquina.
- [ ] A7.3 **P8 (Mongo)**: detener secundario (sigue escribiendo), detener primario (elección; lo que dio 200 sigue una sola vez), detener dos nodos (sin mayoría no escribe → 503). Medir RTO/RPO. Restaurar estado conocido y registrar acciones.
- [x] A7.4 Medición `w:"majority"` vs `w:1` (misma carga, mismo dataset) con la advertencia de que docker local no representa un clúster real.
      Nota: medido directo contra el registro (misma carga y dataset): +3 ms por escritura con majority sin cola; con 20 hilos manda la cola del registro. Ver evidence/A7.4_write_concern_20261022-2011.md.

### A8. Informe y README
- [ ] A8.1 Informe §2: modelo de registro, índices, explain, prueba de no doble adquisición.
- [ ] A8.2 Informe §6: resultados de carga y cuellos de botella.
- [ ] A8.3 Informe §7 (parte Mongo): fallas, RTO, RPO, enlaces a evidence.
- [ ] A8.4 README: sección de carga de datos y simulador.
- [ ] A8.5 Revisar PRs de **B** y preparar 5 preguntas/respuestas sobre sesión y gateway para la defensa.

---

## Persona B — Sesión (Valkey) · Gateway · salud

### B1. Infraestructura Valkey, gateway y red
- [ ] B1.1 Servicio `valkey` (`valkey/valkey:8`) con `--appendonly yes --appendfsync everysec`, volumen persistente, healthcheck. Sin puertos al host.
- [ ] B1.2 Mantener que solo el gateway publique puerto (`8000:8000`); UIs de admin (si hay) en `127.0.0.1:...`. Variables de conexión de Valkey **solo** en `sesion`.

### B2. Servicio `sesion` (≤ 6 rutas, solo Valkey, **no** decide el estado de la tarea)
- [ ] B2.1 Stub inicial según contrato (S1).
- [ ] B2.2 Locks: `SET lock:tarea:{id} <token> NX PX 120000`; renovar solo si el valor coincide (script Lua / `EVAL` compare-and-pexpire); soltar solo si coincide (compare-and-del).
- [ ] B2.3 Sesión: `HSET sesion:{anotador}` (tarea, proyecto, etapa, token); `ZADD editando:{proyecto} <ts> <anotador>`; limpieza de entradas vencidas.
- [ ] B2.4 Borradores: `HSET borrador:{tarea}` (contenido, anotador, token, guardado_en); se devuelven en `abrir`/`recuperar` de `/sesiones`. No generan eventos.
- [ ] B2.5 Latidos: renovar lock + `editando` + timestamp de sesión.
- [ ] B2.6 Almacenamiento temporal de envíos pendientes (si Mongo dio 503, conservar borrador; opcional `pendiente:{tarea}`).
- [ ] B2.7 Mensajes de salud: `POST /estado-capas` y `GET /estado-capas`, llaves `salud:{servicio}` con TTL 15 s (D4).
- [ ] B2.8 `GET /salud` (incluye `PING` a Valkey y estado de AOF vía `INFO persistence`).

### B3. Núcleo del gateway (`servicios/gateway`)
- [ ] B3.1 Middleware: `X-Request-Id` (generar/propagar), logging, validación básica (pydantic), formato de error común.
- [ ] B3.2 Cliente interno con timeouts cortos y **reintentos seguros** (solo idempotentes: GET, PUT borrador; nunca reintentar `/enviar` → 503 sin reintento).
- [ ] B3.3 Función común `publicar_evento()` → `POST historial:/eventos` (usada por A y C también). Si historial falla, no se pierde el 200 (encolar/reintentar en background y documentarlo).
- [ ] B3.4 `POST /tareas/siguiente`: abre/actualiza sesión → asigna en registro → toma lock con el token → si el lock falla o Mongo falla, revertir (liberar en registro / soltar lock) → 204 si no hay tareas; incluir borrador recuperable si existe → evento `abierta`.
- [ ] B3.5 `PUT /tareas/{id}/borrador`: guarda borrador + renueva lock (409 si el token no coincide).
- [ ] B3.6 `POST /tareas/{id}/enviar`: verifica lock/token → transición en registro → 200 / 409 / 503 (sin reintento, borrador queda) → evento `anotada` solo tras 200 → borra borrador y suelta lock.
- [ ] B3.7 `POST /tareas/{id}/liberar` y `POST /sesiones/recuperar` (tarea + último borrador).
- [ ] B3.8 `GET /salud`: consulta cada capa con timeout ~1 s en paralelo; `operativo` / `degradado` / `caído` + detalle por capa; nunca lanza excepción (el gateway sigue vivo aunque todo esté caído).

### B4. Dataset y simulador (parte de sesión)
- [ ] B4.1 Casos controlados: script que deja N tareas libres conocidas y limpia llaves de Valkey (`reset_entorno.py`), usado por P2/P3/P8.
- [ ] B4.2 Simulador: hilo de borradores cada 2 s + latidos; modo "interrupción" (matar al usuario a mitad) y "recuperación" (`/sesiones/recuperar`).

### B5. Pruebas
- [ ] B5.1 **P1**: probar todas las rutas (casos felices y de error) + `/salud` en normal, degradado (parar historial o analítica) y caído (parar mongo/valkey). `docker compose ps` / `ss -tlnp` mostrando que solo 8000 está expuesto. Evidencia.
- [ ] B5.2 **P3**: interrumpir usuario, recuperar borrador (antigüedad ≤ 5 s); `docker compose restart valkey` → borrador sigue; dejar vencer lock (120 s o PX reducido por variable para la prueba, documentarlo) → la tarea se vuelve a ofrecer; ninguna tarea con dos dueños.
- [ ] B5.3 **P8 (Valkey + sesión)**: reiniciar Valkey durante carga, interrumpir sesión; RTO/RPO de borradores (AOF everysec → hasta ~1 s).

### B6. Informe y README
- [ ] B6.1 Informe §1: arquitectura, límites de responsabilidad, diagrama, recorrido completo con ejemplo LiDAR, evidencia de aislamiento modular (p. ej. variables de entorno/redes: registro no tiene credenciales de Valkey, etc.).
- [ ] B6.2 Informe §3: sesión, recuperación de borrador, reinicio de Valkey.
- [ ] B6.3 Informe §7 (parte Valkey).
- [ ] B6.4 README: requisitos previos, `docker compose up/down`, perfiles, solución de problemas.
- [ ] B6.5 `endpoints/gateway.md` completo (todas las rutas, ejemplos curl, códigos).
- [ ] B6.6 Revisar PRs de **C** y preparar 5 preguntas/respuestas sobre lago y Spark para la defensa.

---

## Persona C — Historial (Parquet) · Analítica (Spark) · dataset histórico

### C1. Infraestructura Spark y lago
- [ ] C1.0 Servicio `cola-eventos` (`valkey/valkey:8.0`, AOF everysec, volumen propio), solo accesible por `historial` (D3).
- [ ] C1.1 Servicios `spark-master` y `spark-worker` (perfil `spark2` con 2 workers), volumen compartido `datalake` montado **solo lectura** en Spark/analítica.
- [ ] C1.2 Volumen del lago montado lectura/escritura solo en `historial`.

### C2. Servicio `historial` (≤ 6 rutas)
- [ ] C2.1 Stub `POST /eventos` que acepta y responde 202 (S1) para que el gateway pueda integrar.
- [ ] C2.2 Ingesta durable (D3): `POST /eventos` → `XADD eventos:tareas` en `cola-eventos`. Validar esquema del evento (`endpoints/historial.md`). Idempotente por `event_id`.
- [ ] C2.3 Consumidor con consumer group: junta micro-lote (N eventos o T segundos) → escribe Parquet con `pyarrow` en `datalake/_tmp/` → `os.rename` atómico a `datalake/eventos/mes=AAAA-MM/part-{micro_lote_id}.parquet` → `XACK`. Nombre determinista por micro-lote (reprocesar = sobrescribir el mismo archivo, no duplicar).
- [ ] C2.4 Recuperación: al arrancar, reclamar pendientes (`XAUTOCLAIM`) y limpiar `_tmp` huérfanos.
- [ ] C2.5 `POST /flush`, `POST /reprocesar/{micro_lote_id}` y `GET /micro-lotes` (para P4).
- [ ] C2.6 Compactación opcional de archivos pequeños por mes (documentar estrategia en el informe aunque sea manual).
- [ ] C2.7 `GET /salud` + latido cada 5 s a `POST sesion:/estado-capas` (D4).

### C3. Dataset histórico (`dataset/historico/`)
- [ ] C3.1 Generador de ~800 000 eventos / 12 meses / 100 000 tareas, coherentes con las transiciones (abierta → anotada → en_revision → aprobada | rechazada → abierta ...), usando IDs de `ids.json` de A (o IDs propios si A aún no termina: el generador acepta ambos). Incluye los 60 proyectos cerrados.
- [ ] C3.2 Escribirlo con la misma convención de archivos/particiones que el historial.
- [ ] C3.3 Casos conocidos pequeños (`conjunto=prueba`) con resultados esperados calculados a mano para validar Q1–Q5.
- [ ] C3.4 `eventos_grande` (~10M filas) si la máquina lo permite; si no, documentar la limitación.
- [ ] C3.5 Inyectar duplicados de `event_id` a propósito para probar la deduplicación.

### C4. Servicio `analitica` (solo lee Parquet)
- [ ] C4.1 Cambiar la imagen de `analitica` a una con Java + `pyspark` (p. ej. base `apache/spark:3.5.1-python3`); `SparkSession` reutilizable; `conjunto=principal|grande|prueba`; latido de salud (D4).
- [ ] C4.2 Lectura con `dropDuplicates(["event_id"])` y partition pruning por `mes`.
- [ ] C4.3 Q1 aprobadas por día y modalidad (últimos 90 días, D6).
- [ ] C4.4 Q2 aprobadas / revisadas por equipo y mes (revisadas = aprobada + rechazada).
- [ ] C4.5 Q3 duración media y p95 (`percentile_approx`) por modalidad y tipo de tarea (D7).
- [ ] C4.6 Q4 rechazadas / revisadas por cliente y proyecto.
- [ ] C4.7 Q5 toma→aprobación p50/p95 por cliente + % incumplimiento SLA.
- [ ] C4.8 `GET /plan/{n}` → `explain("formatted")`; guardar planes de Q1 (agrupación por mes) y Q3 (ordenamiento).
- [ ] C4.9 Ruta del gateway `GET /analitica/{q1..q5}?conjunto=` (sobre el núcleo de B).

### C5. Pruebas
- [ ] C5.1 **P4**: escribir lago vía flujo real, reprocesar un micro-lote, deduplicar por `event_id`, mostrar que no hay lecturas a medias (Spark leyendo en bucle mientras se escribe), medir segundos entre HTTP 200 y evento visible en Spark. Mostrar la fila `rechazada` en Parquet vs `disponible` en Mongo.
- [ ] C5.2 **P5**: Q1–Q5 validadas contra `conjunto=prueba`; tiempos 1ª ejecución vs siguientes (≥ 5 repeticiones); 1 vs 2 workers; principal vs grande.

### C6. Informe y README
- [ ] C6.1 Informe §4: lago Parquet, compactación, duplicados, fila rechazada.
- [ ] C6.2 Informe §5: definición de Q1–Q5, resultados, planes, comparación de tiempos, limitaciones de Spark en el entorno.
- [ ] C6.3 Informe §8: limitaciones de la solución (consolidar las de todos).
- [ ] C6.4 README: ejecución de consultas analíticas y regeneración del histórico.
- [ ] C6.5 Revisar PRs de **A** y preparar 5 preguntas/respuestas sobre registro, índices y fallas de Mongo para la defensa.

---

## Integración y cierre — Todos

- [ ] I.0 (S1) Crear el repo `proyecto2` en GitHub, subir el commit inicial, proteger `main` (PR obligatorio), agregar a las 3 personas. Ramas por persona: `a/<tarea>`, `b/<tarea>`, `c/<tarea>`.
- [ ] I.1 (S2) Integración con stubs: el gateway levanta y enruta a los 4 stubs con `docker compose up`.
- [ ] I.2 (S3) Recorrido completo real de una tarea LiDAR (siguiente → borrador → enviar → revisión → aprobada → visible en Q1) — script `pruebas/recorrido_lidar.sh`.
- [ ] I.3 (S3) Prueba de rechazo completa: Mongo `disponible`, Parquet `rechazada`, Q4 la cuenta.
- [ ] I.4 (S3) Evidencia de aislamiento modular (grep de dependencias por servicio, variables de entorno, redes docker).
- [ ] I.5 (S4) Correr TODAS las pruebas en orden desde un entorno limpio (`docker compose down -v`) siguiendo solo el README.
- [ ] I.6 (S4) Sesión de explicación cruzada (cada persona explica su carril a las otras dos, 30 min c/u).
- [ ] I.7 (S4) Preguntar al profesor por la prueba **P7** que falta en el enunciado.
- [ ] I.8 (S5) Ensamblar informe PDF con apéndices y enlace al repo; verificar que cada cifra apunta a un archivo de `evidence/`.
- [ ] I.9 (S5) README final revisado por alguien que no lo escribió.
- [ ] I.10 (S5) Ensayo de defensa de 15 min con preguntas cruzadas (usar las de A8.5, B6.6, C6.5).
- [ ] I.11 (S5) Entrega en TEC Digital.

---

## Dependencias (para no bloquearse)

| Si necesitas… | …y aún no existe | Usa mientras tanto |
|---|---|---|
| registro (B, C) | A2 | Stub A2.2 |
| sesion / gateway (A, C) | B2, B3 | Stub B2.1 / llamar al servicio interno directo con `docker compose exec <servicio> python -c ...` |
| historial (B, A) | C2 | Stub C2.1 |
| datos de Mongo (B, C) | A5 | `ids.json` de ejemplo con pocos IDs; C genera IDs propios |
| eventos reales (C) | flujo completo | dataset histórico sintético C3 |
| simulador (A7.2, C5.1) | A6 | scripts curl / `httpx` sencillos |

## Tareas nuevas descubiertas
_(agregar aquí con persona sugerida y fecha)_
- [x] (A, 2026-10-22) `comun.py`: el 422 de un validador propio terminaba en 500 (la excepción en `ctx` no se podía serializar). Corregido y copiado a los 5 servicios.
- [x] (A, 2026-10-22) registro: si el primario se degradaba mientras esperaba la mayoría, `WriteConcernError` (11602 `InterruptedDueToReplStateChange`) salía como 500. Ahora responde 503 `mongodb_sin_mayoria`. Lo encontró P8 sin mayoría (`evidence/P8_mongo_sin-mayoria_20261022-2024.txt`).
