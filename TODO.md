# TODO — Proyecto 2: Plataforma distribuida de anotación de datos

> Documento vivo y versionado. **Cada commit que avance una tarea debe actualizar este archivo**
> en el mismo commit (marcar la casilla, ajustar el resumen y la fecha).
> Leyenda: `[ ]` pendiente · `[~]` en progreso / a medias · `[x]` hecho · `[!]` bloqueado (decir por qué).
> Contratos entre servicios: [`endpoints/`](endpoints/README.md) · Decisiones técnicas: [`DECISIONES.md`](DECISIONES.md).

## Resumen de avance

| Persona | Rol principal | Hechas / Total | Notas |
|---|---|---|---|
| A | Registro (MongoDB) + dataset operacional + simulador + carga | 34 / 34 | Todas hechas; quedan las tareas de «Todos» (integración, ensamblar el informe, defensa) |
| B | Sesión (Valkey) + Gateway + salud | 19 / 29 | B1.1, B1.2, B2.1–B2.8, B3.1–B3.5, B3.7, B3.8, B4.1 y B4.2 completadas. B3.6 sigue parcialmente completada por la garantía de publicación durable de eventos. A corrigió seis errores del gateway en `integracion-a-b-c` (ver «Tareas nuevas descubiertas»). |
| C | Historial (Parquet) + Analítica (Spark) + dataset histórico | 23 / 31 | 23 hechas, 2 a medias (falta el gateway y /estado-capas de B), 1 bloqueada por B, 5 del informe y README al final |
| Todos | Integración, informe, defensa | 0 / 12 | |

_Última actualización: 2026-11-14 — B4.1 y B4.2 de B (borradores y latidos cada 2 s desde el simulador, interrupción y recuperación) integradas en `integracion-a-b-c` y traídas a `documentacion`; el simulador con borradores corre sin errores y la revisión da 200. En `documentacion` están el informe (`informe/`), el `README.md` y las preguntas de defensa de A, con lo de B y C marcado como pendiente. A: 34/34. B: 19/29._
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
- [x] A2.9 `GET /salud` (incluye si hay PRIMARY) + latido cada 5 s a `POST sesion:/estado-capas` (D4); nunca escribe directo en Valkey.
      Nota: el latido llega al /estado-capas real de sesion cada 5 s y pasa a `caido` sin primario. El /salud del registro decía `estado: operativo` aunque MongoDB estuviera caído y el gateway lee ese campo; ahora `estado` es el de MongoDB (evidence/A2.9_latido_registro_20261110-1447.txt).
- [x] A2.10 Timeouts cortos a Mongo (`serverSelectionTimeoutMS`) para que el gateway pueda devolver 503 rápido.

### A3. Índices
- [x] A3.1 Crear índices mínimos: `tareas(proyecto_id, estado, prioridad)`, `tareas(asignacion.anotador_id, estado)`, `tareas(batch_id)`, `proyectos(cliente_id, estado)`.
- [x] A3.2 `explain("executionStats")` de la consulta de asignación y de exportación → guardar en `evidence/`.
- [x] A3.3 Justificar (con explain/medición) cualquier índice extra (p. ej. para tareas `abierta` vencidas).
      Nota: extra tareas {estado:1, prioridad:-1} para la asignación sin proyecto (60 000 → 1 documento examinado); la exportación no lleva índice propio. Ver evidence/A3_explain_registro_20261022-2003.md.

### A4. Rutas del gateway (sobre el núcleo de B)
- [x] A4.1 `POST /proyectos`, `POST /proyectos/{id}/lotes`, `GET /proyectos/{id}/exportar` → registro.
      Nota: pasan directo a registro con el cliente interno de B; los errores de registro (404, 422) llegan tal cual. Ver evidence/A4_rutas_gateway_20261110-1446.txt.
- [x] A4.2 `POST /tareas/{id}/revision`: empezar revisión / aprobar / rechazar → registro; si 200, publicar evento a historial (función de B).
      Nota: `servicios/gateway/app/revision.py`. 422 sin motivo o con motivo fuera de la lista, 409 en estado inválido, 422 si no es revisor. En el rechazo Mongo queda `disponible` y el lago `rechazada` con su motivo (evidence/A4_rutas_gateway_20261110-1446.txt).

### A5. Dataset operacional (`dataset/`)
- [x] A5.1 Generador reproducible (semilla fija) de 60 clientes, 120 proyectos activos, 600 batches, 60 000 tareas (30/30/20/20 % texto/imagen/video/LiDAR con geometría correcta), 300 anotadores en 30 equipos (10 % revisores). Incluir el proyecto LiDAR de ejemplo con su taxonomía.
- [x] A5.2 Exportar IDs de los 60 proyectos cerrados y de clientes/anotadores para que C los use en el histórico (archivo compartido `dataset/salida/ids.json`).
      Nota: dataset/salida/ está en .gitignore: el ids.json se obtiene corriendo la carga (semilla 2026, mismos ids en cualquier máquina).
- [x] A5.3 Script de carga desde cero (`make cargar` o `python -m dataset.cargar`) que borra y recarga; imprime conteos. Documentar en `datos/README.md`.
      Nota: docker compose --profile datos run --rm dataset · documentado en datos/README.md.
- [x] A5.4 Script "regenerar tareas disponibles" para cuando las pruebas de carga las agoten (anota fecha y motivo en evidence).

### A6. Simulador (`simulador/`) — solo HTTP al gateway
- [x] A6.1 Cliente HTTP base (`httpx`), configuración por CLI (URL, número de usuarios, duración).
- [x] A6.2 Flujo anotador: siguiente → (borradores, de B) → enviar; flujo revisor: revisión → aprobar/rechazar con motivo aleatorio.
      Nota: corrido contra el gateway real (10 usuarios, 30 s). Antes de corregir el gateway: 79 de 139 `/tareas/siguiente` con 503 por locks viejos (evidence/A6.2_simulador_gateway_20261110-1501.txt). Con las correcciones aparecieron esperas de 10 s por «database is locked» en el SQLite del gateway (evidence/A6.2_simulador_gateway_20261110-1721.txt); con SQLite en WAL: 0 errores, 179 asignaciones, 186 envíos (372 por minuto) y 91 revisiones (evidence/A6.2_simulador_gateway_20261110-1726.txt). Con B4.2 integrado (`--borradores`, pensar de 3 a 6 s): 113 borradores, 113 latidos, 70 envíos y 99 revisiones, todo con 200 (evidence/A6.2_simulador_borradores_20261114-2318.txt).
- [x] A6.3 Registro de latencias por endpoint (CSV crudo) para P6.
      Nota: CSV con ts, fase (calentamiento/medicion), usuario, rol, endpoint, status y ms; resumen p50/p95/p99 por endpoint y envíos/min.

### A7. Pruebas
- [x] A7.1 **P2**: estado del replica set + script con `ProcessPoolExecutor`: preparar exactamente 10 tareas libres, 50 solicitudes simultáneas → verificar 10 asignaciones, 40 sin tarea (204), ninguna tarea con 2 anotadores (consulta de verificación vía gateway/export), segundo envío → 409. Evidencia.
      Nota: vía gateway, desde el contenedor simulador (red borde): 10/40, cero dobles, 409 con token ajeno y en el reenvío, 10 aprobadas exportadas con su anotador. Repetida con el gateway corregido: mismo resultado y p50 del cliente de 3,4 s a 0,32 s (evidence/P2_concurrencia_gateway_20261110-1714.md; la de las 14:50 queda como el antes). La preliminar contra el registro queda como respaldo (evidence/P2_concurrencia_registro_20261022-2007.md).
- [x] A7.2 **P6**: 10, 40, 100 usuarios virtuales; 30 s calentamiento + 3 corridas de 90 s por nivel; p50/p95/p99 por endpoint, envíos/min, errores, `docker stats` muestreado. Características de la máquina.
      Nota: desbloqueada: A corrigió en `integracion-a-b-c` los errores del gateway que daban 57 % de 503 en `/tareas/siguiente` (evidence/A_correcciones_gateway_20261110-1713.txt).
      Nota: primera corrida completa (evidence/P6_carga_20261110-1727.md): 0 errores con 10 y 40 usuarios; el gateway se satura cerca de 30 peticiones/s (81–88 % de un núcleo) y con 100 usuarios el envío p50 sube a 4,7 s. Encontró que el publicador de eventos se atrasa bajo carga; se repite P6 después de corregirlo.
      Nota: corrida final con el gateway corregido (evidence/P6_carga_20261110-1800.md): 0 errores con 10 y 40 usuarios (envío p50 118 ms y 1,3 s; 375 y 664 envíos/min); con 100 usuarios 98 peticiones pasan los 10 s del cliente pero terminan con 200 y todos los eventos llegan al lago (p50 3,7 s). Cuello de botella: el proceso único del gateway (~29 peticiones/s, 93–99 % de un núcleo); con 2 procesos, 38–53 % más envíos (experimento aparte).
- [x] A7.3 **P8 (Mongo)**: detener secundario (sigue escribiendo), detener primario (elección; lo que dio 200 sigue una sola vez), detener dos nodos (sin mayoría no escribe → 503). Medir RTO/RPO. Restaurar estado conocido y registrar acciones.
      Nota: vía gateway, la sonda en el simulador: secundario sin errores; primario RTO 10,53 s; sin mayoría RTO 30,93 s (7,98 s desde que vuelven los nodos); RPO 0 en los tres. Por el gateway hay más errores que sí se escribieron (1 y 9) porque corta a 1 s mientras el registro espera la mayoría (evidence/P8_mongo_gateway_20261110-1453.md). Repetida con el gateway corregido: RPO 0 en los tres; primario RTO 10,45 s; sin mayoría 10,24 s desde que vuelven los nodos; 122 envíos confirmados en 45 s en lugar de 67 (evidence/P8_mongo_gateway_20261110-1715.md). La corrida directa al registro queda como respaldo (evidence/P8_mongo_20261022-2028.md).
- [x] A7.4 Medición `w:"majority"` vs `w:1` (misma carga, mismo dataset) con la advertencia de que docker local no representa un clúster real.
      Nota: medido directo contra el registro (misma carga y dataset): +3 ms por escritura con majority sin cola; con 20 hilos manda la cola del registro. Ver evidence/A7.4_write_concern_20261022-2011.md.

### A8. Informe y README
- [x] A8.1 Informe §2: modelo de registro, índices, explain, prueba de no doble adquisición.
      Nota: `informe/informe.tex` §2: modelo y transiciones, datos, índices con explain, P2 vía gateway, `w: majority` frente a `w: 1` y límites.
- [x] A8.2 Informe §6: resultados de carga y cuellos de botella.
      Nota: §6 con la P6 final, los cuellos de botella corregidos antes de medir, el publicador de eventos y el experimento con 2 procesos.
- [x] A8.3 Informe §7 (parte Mongo): fallas, RTO, RPO, enlaces a evidence.
      Nota: §7.1 con la P8 vía gateway corregido; el Apéndice A lista la evidencia de A.
- [x] A8.4 README: sección de carga de datos y simulador.
      Nota: `README.md` con la estructura que pide el enunciado: carga de datos, simulador y las pruebas de A (A4, P2, P6, P8 de MongoDB). Requisitos, inicio y detención, y solución de problemas quedan marcados como pendientes de B6.4; consultas y regenerar el histórico, de C6.4.
- [x] A8.5 Revisar PRs de **B** y preparar 5 preguntas/respuestas sobre sesión y gateway para la defensa.
      Nota: B no abrió PRs; A revisó su código al integrarlo en `integracion-a-b-c` (seis correcciones, ver «Tareas nuevas descubiertas»). Las 5 preguntas con respuesta están en `defensa/preguntas.md`, con lugar para las de B (B6.6) y C (C6.5).

---

## Persona B — Sesión (Valkey) · Gateway · salud

### B1. Infraestructura Valkey, gateway y red
- [x] B1.1 Servicio `valkey` (`valkey/valkey:8`) con `--appendonly yes --appendfsync everysec`, volumen persistente, healthcheck. Sin puertos al host.
- [x] B1.2 Mantener que solo el gateway publique puerto (`8000:8000`); UIs de admin (si hay) en `127.0.0.1:...`. Variables de conexión de Valkey **solo** en `sesion`.

### B2. Servicio `sesion` (≤ 6 rutas, solo Valkey, **no** decide el estado de la tarea)
- [x] B2.1 Stub inicial según contrato (S1).
- [x] B2.2 Locks: `SET lock:tarea:{id} <token> NX PX 120000`; renovar solo si el valor coincide (script Lua / `EVAL` compare-and-pexpire); soltar solo si coincide (compare-and-del).
- [x] B2.3 Sesión: `HSET sesion:{anotador}` (tarea, proyecto, etapa, token); `ZADD editando:{proyecto} <ts> <anotador>`; limpieza de entradas vencidas.
- [x] B2.4 Borradores: `HSET borrador:{tarea}` (contenido, anotador, token, guardado_en); se devuelven en `abrir`/`recuperar` de `/sesiones`. No generan eventos.
- [x] B2.5 Latidos: renovar lock + `editando` + timestamp de sesión.
- [x] B2.6 Almacenamiento temporal de envíos pendientes (si Mongo dio 503, conservar borrador; opcional `pendiente:{tarea}`).
- [x] B2.7 Mensajes de salud: `POST /estado-capas` y `GET /estado-capas`, llaves `salud:{servicio}` con TTL 15 s (D4).
- [x] B2.8 `GET /salud` (incluye `PING` a Valkey y estado de AOF vía `INFO persistence`).

### B3. Núcleo del gateway (`servicios/gateway`)
- [x] B3.1 Middleware: `X-Request-Id` (generar/propagar), logging, validación básica (pydantic), formato de error común.
- [x] B3.2 Cliente interno con timeouts cortos y **reintentos seguros** (solo idempotentes: GET, PUT borrador; nunca reintentar `/enviar` → 503 sin reintento).
- [x] B3.3 Función común `publicar_evento()` → `POST historial:/eventos` (usada por A y C también). Si historial falla, no se pierde el 200 (encolar/reintentar en background y documentarlo).
- [x] B3.4 `POST /tareas/siguiente`: abre/actualiza sesión → asigna en registro → toma lock con el token → si el lock falla o Mongo falla, revertir (liberar en registro / soltar lock) → 204 si no hay tareas; incluir borrador recuperable si existe → evento `abierta`.
- [x] B3.5 `PUT /tareas/{id}/borrador`: guarda borrador + renueva lock (409 si el token no coincide).
- [~] B3.6 `POST /tareas/{id}/enviar`: verifica lock/token → transición en registro → 200 / 409 / 503 (sin reintento, borrador queda) → evento `anotada` solo tras 200 → borra borrador y suelta lock.
Nota: implementación y 6 pruebas automatizadas aprobadas; validación HTTP 422 correcta. Pendiente recuperar eventos si SQLite falla después de confirmar MongoDB y reintentar limpiezas fallidas. Evidencia: evidence/B3_6_envio.txt.
Nota: envío implementado y probado; limpiezas posteriores al HTTP 200 almacenadas en SQLite con reintentos automáticos. Se verificó la recuperación ante fallos de Sesión y la conservación de pendientes ante HTTP 409. Evidencias: evidence/B3_6_envio.txt y evidence/B3_6_limpiezas.txt. Pendiente recuperar eventos si MongoDB confirma pero falla SQLite.
- [x] B3.7 `POST /tareas/{id}/liberar` y `POST /sesiones/recuperar` (tarea + último borrador).
      Nota: rutas implementadas; liberación con evento y limpiezas persistentes, recuperación de sesión y borrador con fecha ISO 8601. Siete pruebas simuladas aprobadas (200, 404, 409, 503). Pendiente prueba de interrupción real en B5.2. Evidencia: evidence/B3_7_pruebas.txt.
- [x] B3.8 `GET /salud`: consulta cada capa con timeout ~1 s en paralelo; `operativo` / `degradado` / `caído` + detalle por capa; nunca lanza excepción (el gateway sigue vivo aunque todo esté caído).

### B4. Dataset y simulador (parte de sesión)
- [x] B4.1 Casos controlados: script que deja N tareas libres conocidas y limpia llaves de Valkey (`reset_entorno.py`), usado por P2/P3/P8.
      Nota: creado proyecto aislado con 10 tareas disponibles y manifiesto local. Limpieza de Valkey implementada mediante limpiar_b41.py y validada: elimina la llave autorizada y conserva llaves ajenas. Uso restringido a entorno de pruebas sin concurrencia. Evidencia: evidence/B4_1_limpieza.txt.
- [x] B4.2 Simulador: hilo de borradores cada 2 s + latidos; modo "interrupción" (matar al usuario a mitad) y "recuperación" (`/sesiones/recuperar`).
Nota (2026-11-14): integración opcional mediante --borradores en simulador/simular.py. Prueba real: 6 borradores HTTP 200, 6 latidos HTTP 200 y 3 envíos HTTP 200. Interrupción real mediante docker kill y recuperación de la misma tarea, token y borrador. Liberación y limpieza posteriores verificadas. Evidencias: evidence/B4_2_borradores.txt, evidence/B4_2_integracion.txt, evidence/B4_2_interrupcion_real.txt y evidence/B4_2_simulador.csv. La interrupción se ejecuta desde un script independiente; no es una opción del CLI principal.

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
- [x] C1.0 Servicio `cola-eventos` (`valkey/valkey:8.0`, AOF everysec, volumen propio), solo accesible por `historial` (D3).
      Nota: está solo en la red `cola`, que comparte únicamente con historial (D13); ni el gateway ni sesion lo resuelven por nombre.
- [x] C1.1 Servicios `spark-master` y `spark-worker` (perfil `spark2` con 2 workers), volumen compartido `datalake` montado **solo lectura** en Spark/analítica.
      Nota: `apache/spark:3.5.1-python3` en la red `spark` (solo analitica la ve). Worker de 2 núcleos y 2 GB (`SPARK_WORKER_CORES`, `SPARK_WORKER_MEMORY`); `spark-worker-2` en el perfil `spark2`. El lago es `./datalake` montado en `/datalake:ro`, la misma ruta que en el driver.
- [x] C1.2 Volumen del lago montado lectura/escritura solo en `historial`.
      Nota: historial corre con el UID del host para que los Parquet no queden a nombre de root.

### C2. Servicio `historial` (≤ 6 rutas)
- [x] C2.1 Stub `POST /eventos` que acepta y responde 202 (S1) para que el gateway pueda integrar.
      Nota: no hizo falta stub: el servicio real ya responde 202 según el contrato (C2.2).
- [x] C2.2 Ingesta durable (D3): `POST /eventos` → `XADD eventos:tareas` en `cola-eventos`. Validar esquema del evento (`endpoints/historial.md`). Idempotente por `event_id`.
      Nota: `SET visto:{event_id} NX` + `XADD` en un solo script Lua; responde `{aceptados, duplicados}`. Sin cola responde 503.
- [x] C2.3 Consumidor con consumer group: junta micro-lote (N eventos o T segundos) → escribe Parquet con `pyarrow` en `datalake/_tmp/` → `os.rename` atómico a `datalake/eventos/mes=AAAA-MM/part-{micro_lote_id}.parquet` → `XACK`. Nombre determinista por micro-lote (reprocesar = sobrescribir el mismo archivo, no duplicar).
      Nota: 500 eventos o 5 s (`MICRO_LOTE_EVENTOS`, `MICRO_LOTE_SEGUNDOS`); id = `<primer stream id>_<último>`; fsync del archivo y de la carpeta antes del XACK.
- [x] C2.4 Recuperación: al arrancar, reclamar pendientes (`XAUTOCLAIM`) y limpiar `_tmp` huérfanos.
      Nota: además rehace con el mismo nombre los micro-lotes que abrieron y no confirmaron (`microlotes:abiertos`).
- [x] C2.5 `POST /flush`, `POST /reprocesar/{micro_lote_id}` y `GET /micro-lotes` (para P4).
- [x] C2.6 Compactación opcional de archivos pequeños por mes (documentar estrategia en el informe aunque sea manual).
      Nota: manual, `docker compose exec historial python -m app.compactar --mes AAAA-MM --ejecutar` (sin `--ejecutar` solo simula). Junta el mes en un `part-compacto-*.parquet` sin duplicados de event_id, lo publica con rename y después borra los viejos. Falta explicarla en el informe (C6.1).
- [~] C2.7 `GET /salud` + latido cada 5 s a `POST sesion:/estado-capas` (D4).
      Nota: salud y latido listos; falta verlo llegar al /estado-capas real de sesion (B2.7).

### C3. Dataset histórico (`dataset/historico/`)
- [x] C3.1 Generador de ~800 000 eventos / 12 meses / 100 000 tareas, coherentes con las transiciones (abierta → anotada → en_revision → aprobada | rechazada → abierta ...), usando IDs de `ids.json` de A (o IDs propios si A aún no termina: el generador acepta ambos). Incluye los 60 proyectos cerrados.
      Nota: 808 703 eventos de 100 000 tareas de los 60 proyectos cerrados, 2025-10 a 2026-09, en 19 s. Sin `ids.json` calcula los mismos ids con `dataset/generar.py`. Conteos en datos/README.md.
- [x] C3.2 Escribirlo con la misma convención de archivos/particiones que el historial.
- [x] C3.3 Casos conocidos pequeños (`conjunto=prueba`) con resultados esperados calculados a mano para validar Q1–Q5.
      Nota: 7 tareas, 42 eventos + 1 duplicado (`dataset/historico/prueba.py`); esperado en `dataset/historico/esperado_prueba.json`.
- [x] C3.4 `eventos_grande` (~10M filas) si la máquina lo permite; si no, documentar la limitación.
      Nota: 10 107 232 eventos (1 250 000 tareas), 553 MB, 157 s. Se mide en P5.
- [x] C3.5 Inyectar duplicados de `event_id` a propósito para probar la deduplicación.
      Nota: 0,2 % de los eventos (1 634 en principal, 20 447 en grande) repetidos en archivos `-reentrega`.

### C4. Servicio `analitica` (solo lee Parquet)
- [x] C4.1 Cambiar la imagen de `analitica` a una con Java + `pyspark` (p. ej. base `apache/spark:3.5.1-python3`); `SparkSession` reutilizable; `conjunto=principal|grande|prueba`; latido de salud (D4).
      Nota: la SparkSession se crea al arrancar en un hilo (reintenta si el master no está) y /salud responde siempre. El latido a /estado-capas está listo; falta verlo llegar a sesion (B2.7).
- [x] C4.2 Lectura con `dropDuplicates(["event_id"])` y partition pruning por `mes`.
      Nota: filtro de estados antes de deduplicar (D14). Q1 poda por `mes >= mes de inicio de la ventana` (`PartitionFilters` en el plan).
- [x] C4.3 Q1 aprobadas por día y modalidad (últimos 90 días, D6).
      Nota: `max(ts)` sale de los footers (`spark.sql.parquet.aggregatePushdown`).
- [x] C4.4 Q2 aprobadas / revisadas por equipo y mes (revisadas = aprobada + rechazada).
- [x] C4.5 Q3 duración media y p95 (`percentile_approx`) por modalidad y tipo de tarea (D7).
- [x] C4.6 Q4 rechazadas / revisadas por cliente y proyecto.
- [x] C4.7 Q5 toma→aprobación p50/p95 por cliente + % incumplimiento SLA.
- [x] C4.8 `GET /plan/{n}` → `explain("formatted")`; guardar planes de Q1 (agrupación por mes) y Q3 (ordenamiento).
      Nota: evidence/P5_analitica_20261030-1410_plan_q1.txt (`PartitionFilters: mes >= 2026-07`) y _plan_q3.txt (`rangepartitioning(media_s DESC)` + Sort).
- [!] C4.9 Ruta del gateway `GET /analitica/{q1..q5}?conjunto=` (sobre el núcleo de B).
      Nota: bloqueada: va sobre el núcleo del gateway (B3.1 middleware, B3.2 cliente interno). La analítica ya responde `GET /q/{n}`.

### C5. Pruebas
- [~] C5.1 **P4**: escribir lago vía flujo real, reprocesar un micro-lote, deduplicar por `event_id`, mostrar que no hay lecturas a medias (Spark leyendo en bucle mientras se escribe), medir segundos entre HTTP 200 y evento visible en Spark. Mostrar la fila `rechazada` en Parquet vs `disponible` en Mongo.
      Nota: corrida preliminar contra registro + historial, sin gateway: 39 vueltas de Spark sin error durante 40 micro-lotes; reprocesar deja el mismo archivo y sha256; reenvío → duplicados 1; frescura mediana 7,42 s (1,32 s con flush); rechazada en Parquet y disponible en Mongo; kill del historial con 300 pendientes → 0 perdidos, 0 repetidos (evidence/P4_historial_20261030-1431.md). Falta repetirla vía gateway (B3.3-B3.6, I.5).
- [x] C5.2 **P5**: Q1–Q5 validadas contra `conjunto=prueba`; tiempos 1ª ejecución vs siguientes (≥ 5 repeticiones); 1 vs 2 workers; principal vs grande.
      Nota: las 5 iguales a lo calculado a mano; 6 ejecuciones por consulta, principal y grande, 1 y 2 workers (con 2 núcleos físicos, 2 workers no acelera principal). Directo contra la analítica, sin gateway (C4.9). Ver evidence/P5_analitica_20261030-1410.md.

### C6. Informe y README
- [ ] C6.1 Informe §4: lago Parquet, compactación, duplicados, fila rechazada.
- [ ] C6.2 Informe §5: definición de Q1–Q5, resultados, planes, comparación de tiempos, limitaciones de Spark en el entorno.
- [ ] C6.3 Informe §8: limitaciones de la solución (consolidar las de todos).
- [x] C6.4 README: ejecución de consultas analíticas y regeneración del histórico.
      Nota: también la compactación y las filas de P4, P5, C4.9 y C2.6 en la tabla de pruebas.
- [x] C6.5 Revisar PRs de **A** y preparar 5 preguntas/respuestas sobre registro, índices y fallas de Mongo para la defensa.
      Nota: en defensa/preguntas.md (índice extra, paginación por cursor, caída del primario y retryWrites, error ≠ no escrito sin mayoría, fila rechazada y ReturnDocument.BEFORE).

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
- [~] I.8 (S5) Ensamblar informe PDF con apéndices y enlace al repo; verificar que cada cifra apunta a un archivo de `evidence/`.
      Nota: base en `informe/informe.tex` (LaTeX como los labs; el enunciado solo pide PDF con apéndices y el enlace al repo). Las secciones de B y C están marcadas con `\falta{}` y salen en rojo en `informe/informe.pdf`.
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
- [x] (B, 2026-11-10) gateway: después de un envío el lock se suelta en segundo plano (cada 2 s). Si la tarea se rechaza y se pide otra vez antes, `/tareas/siguiente` responde 503 porque el lock viejo sigue en Valkey. Lo encontró la prueba de A4 (`pruebas/a4_rutas_gateway.py`).
      Nota: corregido por A en `integracion-a-b-c`: `limpiezas.ejecutar` limpia borrador, lock y sesión antes de responder (envío y liberar); siguen guardadas en SQLite por si sesion no responde (evidence/A_correcciones_gateway_20261110-1713.txt).
- [x] (B, 2026-11-10) gateway `limpiezas.py`: con 404/409 la limpieza se reprograma para siempre, aunque el comentario dice que ya no se puede completar. Cada envío sin borrador deja un `eliminar_borrador` que se reintenta cada 60 s (en la prueba de A4 llegaron a 6 intentos). Va a pesar en P6.
      Nota: corregido por A: cualquier respuesta < 500 cierra la limpieza; solo se reintenta si sesion no responde. Las 637 que estaban atascadas se cerraron en la primera pasada.
- [x] (B, 2026-11-10) gateway `/salud`: con el registro caído (MongoDB sin primario) responde `degradado`; `endpoints/gateway.md` dice `caido` si registro/MongoDB o sesión/Valkey no están, y `degradado` solo si fallan historial, analítica o Spark (evidence/A2.9_latido_registro_20261110-1447.txt).
      Nota: corregido por A: `caido` si registro o sesion están caídos. Probado matando dos nodos de MongoDB y con un caso nuevo en `prueba_salud_b38`.
- [x] (B, 2026-11-10) gateway `cliente_interno.py`: crea un `httpx.AsyncClient` nuevo en cada llamada y crear uno cuesta unos 35 ms de CPU que bloquean al gateway. En P2 vía gateway el registro respondió con p50 52 ms, pero el cliente vio p50 3,4 s (evidence/P2_concurrencia_gateway_20261110-1450.md). Reusar un solo cliente creado al arrancar. Conviene corregirlo antes de P6.
      Nota: corregido por A: un solo cliente compartido (también en `/salud`). 30 llamadas juntas: p50 de 238-390 ms a 72-129 ms.
- [x] (B, 2026-11-10) gateway: una tarea rechazada con el lock viejo todavía en Valkey bloquea la cola. Cada `/tareas/siguiente` la toma (prioridad alta), falla el lock, la devuelve con liberar y responde 503; con 10 usuarios, 5 tareas así causaron 79 de 139 errores (evidence/A6.2_simulador_gateway_20261110-1501.txt). Sale de soltar el lock en segundo plano después del envío, atrasado por las limpiezas que se reintentan para siempre. Bloquea P6 (A7.2).
      Nota: se resuelve con las dos correcciones de limpiezas; la prueba de A4 ya toma la rechazada y la liberada sin esperar.
- [x] (B, 2026-11-10) gateway SQLite (`eventos.py`): con varias peticiones escribiendo a la vez aparecen esperas de 10 s y `database is locked`; las conexiones no se cerraban y el journal por defecto hace que leer bloquee a quien confirma. Salió al probar la corrección de las limpiezas, que suma escrituras en cada envío.
      Nota: corregido por A: WAL (un fsync por commit, ~10 ms contra ~22 ms), conexión que siempre se cierra y un commit por pasada del publicador. Con 10 usuarios, 0 errores (evidence/A6.2_simulador_gateway_20261110-1721.txt y -1726.txt).
- [x] (B, 2026-11-10) gateway `eventos.py`: el publicador manda un evento por POST, toma 50 por pasada y espera hasta 2 s entre pasadas. Con 100 usuarios en P6 se generan 27–29 eventos/s y llegan 8–11 al historial; quedaron 6 133 pendientes y sin carga se vaciaban a ~20/s (evidence/P6_carga_20261110-1727/eventos.txt). No se pierden, pero llegan al lago minutos tarde.
      Nota: corregido por A: los eventos de la pasada van en un solo POST (historial acepta una lista; si la rechaza con 4xx se prueban de a uno) y no espera mientras queden pendientes. 3 333 pendientes se vaciaron en unos 5 s. Caso nuevo en `prueba_eventos_b33`.
- [ ] (B, 2026-11-10) sesion `abrir`: si una tarea vuelve a la cola con el borrador de otro anotador todavía en Valkey (lock y plazo vencidos sin enviar), `abrir` responde 409 `borrador_de_otro_anotador`, el gateway compensa y `/tareas/siguiente` da 503; como la tarea sigue primera en la cola, se repite y solo el anotador original la puede tomar (visto en el código, sin prueba todavía). Toca a P3 («dejar vencer el candado y comprobar que la tarea vuelve a ofrecerse»). No afecta P6 porque el simulador todavía no guarda borradores (B4.2).
- [ ] (B, 2026-11-10) gateway `/salud` consulta el `/salud` de cada servicio y no lee los mensajes que las capas dejan en Valkey (`sesion:/estado-capas`); el enunciado pide usar Valkey para esos mensajes (§7, `GET /salud`).
- [ ] (A y B, 2026-11-10) el lock se renueva con latidos y borradores, pero `asignacion.vence_en` en MongoDB queda fijo en 120 s. Si alguien trabaja más de 120 s guardando borradores, el registro puede ofrecer la tarea a otro mientras el lock sigue tomado. Decidir si el gateway renueva también el plazo en el registro o si se documenta como limitación.
