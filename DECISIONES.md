# Decisiones de diseño

Registro de decisiones técnicas. Para cambiar una: PR que modifica esta tabla con la justificación.

| # | Decisión | Justificación |
|---|---|---|
| D1 | **Python 3.12 + FastAPI + uvicorn** en todos los servicios. `httpx` (gateway, simulador), `pymongo` (registro), `valkey-py` (sesión, historial), `pyarrow` (historial), `pyspark` (analítica). | Un solo lenguaje para los 3 integrantes; el enunciado sugiere `concurrent.futures` de Python para P2. |
| D2 | Imágenes: `mongo:7.0` ×3 (replica set `rs0`), `valkey/valkey:8.0` con `--appendonly yes --appendfsync everysec`, Spark `apache/spark:3.5.1` (master + worker; segundo worker en perfil `spark2`). | Versiones estables y oficiales. |
| D3 | **Mensajería durable de eventos**: el gateway, tras el 200 de MongoDB, llama a `POST historial:/eventos`; el historial lo encola en **su propia** instancia Valkey (`cola-eventos`, Stream + AOF) y un consumidor arma micro-lotes Parquet. | El Valkey de sesión pertenece al servicio de sesión; usar otra instancia evita que un servicio acceda a la base de otro. Streams dan consumer groups, ACK y reclamación de pendientes. |
| D4 | **Salud**: cada servicio publica cada 5 s `POST sesion:/estado-capas` (llave `salud:{servicio}`, TTL 15 s). El gateway arma `/salud` con esos mensajes + un `GET /salud` directo a cada servicio con timeout de 1 s, en paralelo y sin propagar excepciones. | Cumple "use Valkey para almacenar los mensajes" sin que otros servicios toquen Valkey, y `/salud` funciona aunque Valkey esté caído. |
| D5 | Analítica: FastAPI con una `SparkSession` persistente conectada al master; `conjunto=principal|grande|prueba`. | Permite medir 1ª ejecución vs siguientes. |
| D6 | Q1 "últimos 90 días" se calcula respecto de `max(ts)` del conjunto. | Los datos son sintéticos; con `now()` el resultado cambiaría según el día. |
| D7 | Q3 duración = `ts(anotada) − tomada_ts`; Q5 = `ts(aprobada) − tomada_ts` de la asignación vigente; SLA incumplido si supera `sla_horas`. | Definición explícita y reproducible. |
| D8 | Token de asignación = UUID generado por el gateway; se guarda en MongoDB (`asignacion.token`) y como valor del lock en Valkey. | Un único token valida ambas capas. |
| D9 | Los datos del lago no se versionan (`datalake/*` en `.gitignore`); se regeneran con el generador. | Tamaño. |
| D10 | El repositorio **es** la carpeta `proyecto2/` del enunciado (archivos en la raíz del repo). | Estructura equivalente sin anidar `proyecto2/proyecto2/`; al clonar el repo `proyecto2` queda igual al enunciado. |
| D11 | `liberar` genera evento `liberada` (no está en la tabla del enunciado); Q1–Q5 lo ignoran. | Trazabilidad sin alterar los KPIs. |
| D12 | Simulador en la red `borde` (solo ve al gateway); servicios y motores en la red `interna`. | Evidencia de aislamiento a nivel de red. |
| D13 | Redes propias para los motores de C: `cola` (solo historial ↔ cola-eventos) y `spark` (solo analitica ↔ spark-master/workers). | El aislamiento queda en la red, no solo en la configuración: ningún otro servicio puede resolver ni abrir conexión a esos motores. |
| D14 | Analítica: cada consulta filtra los estados que usa **antes** de `dropDuplicates(["event_id"])`; no se cachean DataFrames y las consultas se atienden de a una. Imagen `apache/spark:3.5.1-python3` (Python 3.8, por eso uvicorn 0.33). | Las copias de un `event_id` son la misma fila (mismo estado y `ts`), así que el resultado es igual y el shuffle de la deduplicación mueve solo lo necesario. Sin caché ni concurrencia, la diferencia 1ª vs siguientes es arranque y no datos en memoria, y los tiempos son comparables. |
