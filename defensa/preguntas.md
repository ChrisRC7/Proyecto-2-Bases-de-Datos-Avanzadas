# Preguntas cruzadas para la defensa

La defensa pide responder sobre componentes que cada quien no implementó (enunciado §13). Cada persona
revisa el código de otra y deja aquí 5 preguntas con su respuesta (A revisa a B, B revisa a C, C revisa
a A; TODO A8.5, B6.6 y C6.5). Se usan en el ensayo (I.10).

## Sesión y gateway (código de B, preguntas de A)

**1. Si el lock de Valkey existe, ¿por qué se dice que la exclusividad la da MongoDB?**
Porque la tarea se asigna con un solo `findOneAndUpdate` cuyo filtro pide `estado = disponible` (o
`abierta` vencida). MongoDB aplica cada actualización de un documento de forma atómica: de dos peticiones
juntas, solo una encuentra la tarea libre. El lock (`SET lock:tarea:{id} <token> NX PX 120000`) se toma
después, con el mismo token que guardó el registro. Si el lock falla, el gateway compensa: cierra la
sesión, suelta el lock si es suyo y devuelve la tarea con `liberar`, y responde 503. La prueba P2 (50
contra 10) dio cero tareas con dos dueños (`evidence/P2_concurrencia_gateway_20261110-1714.md`).

**2. ¿Qué pasa si MongoDB no responde cuando alguien envía su anotación?**
El gateway primero renueva el lock con el token del usuario (si no coincide, 409 sin tocar MongoDB).
Después llama a la transición `enviar` del registro una sola vez: el cliente interno solo reintenta `GET` y
`PUT` de borrador, porque son idempotentes, y nunca un `POST`. Si el registro da 5xx o no contesta en 1 s,
el usuario recibe 503 `servicio_no_disponible`. El borrador y el lock se quedan, así que puede volver a
enviar cuando el registro responda. Ojo: error no siempre es «no escrito». Si MongoDB alcanzó a escribir
y la respuesta no llegó a tiempo, el reenvío da 409 porque la tarea ya está `anotada`. En P8 vía gateway
pasó con asignaciones (`evidence/P8_mongo_gateway_20261110-1453.md`).

**3. Si el historial está caído, ¿se pierde el evento de una tarea que ya respondió 200?**
No. Antes de responder, el gateway guarda el evento en una tabla SQLite (`pendientes`, en modo WAL con
`synchronous=FULL`) en un volumen propio. Un trabajador en segundo plano manda los pendientes a
`historial:/eventos` en lotes de hasta 50 en un solo POST y los borra solo cuando el historial responde 202;
si falla, reintenta con espera creciente. Si un evento llega dos veces (por ejemplo, el gateway se cayó
después de mandarlo y antes de borrarlo), el historial lo descarta por `event_id` (`SET visto:{event_id} NX`).
En la primera P6 se vio que, mandando de a uno, con 100 usuarios el publicador quedaba miles de eventos
atrás: no se perdían, pero llegaban tarde al lago (`evidence/P6_carga_20261110-1727/eventos.txt`). Lo que
falta: si MongoDB confirma y justo falla SQLite, el usuario recibe error aunque la transición quedó hecha
(pendiente en B3.6).

**4. ¿Qué limpia el gateway después de un envío y por qué no puede borrarle algo a otro dueño?**
Borra el borrador, suelta el lock y cierra la sesión. Las tres operaciones comparan el token o el anotador
dentro de un script Lua en Valkey (comparar y borrar), así que si la tarea ya es de otra persona no le
tocan nada y responden 409. Primero se anotan en SQLite y luego se ejecutan en el momento, antes de
responder. 404 o 409 las cierran, y solo se reintentan si la sesión no responde. Antes se dejaban a un
trabajador cada 2 s y los 404/409 se reintentaban para siempre. Una tarea rechazada antes de que se soltara
su lock viejo hacía que cada `/tareas/siguiente` respondiera 503: 79 de 139 pedidos con 10 usuarios
(`evidence/A6.2_simulador_gateway_20261110-1501.txt`, corregido en
`evidence/A_correcciones_gateway_20261110-1713.txt`).

**5. ¿Cómo decide `/salud` si el sistema está operativo, degradado o caído?**
Consulta en paralelo el `/salud` de registro, sesión, historial y analítica, con 1 s de límite cada uno,
y nunca lanza excepción: siempre responde 200 aunque todo esté caído. Si registro o sesión están caídos el
sistema está `caido`, porque no se puede asignar ni enviar. Si solo fallan historial o analítica, está
`degradado`. El registro se reporta `caido` cuando MongoDB no tiene primario. Además, cada capa publica su
estado cada 5 s en `sesion:/estado-capas` (llaves `salud:{servicio}` con TTL 15 s), y el gateway lee esos
mensajes en la misma pasada. Para cada capa gana el peor de los dos: el mensaje trae lo que la capa sabe de sí
misma (si MongoDB tiene primario, si Spark tiene ejecutores) y la consulta directa ve en el momento una capa
que se cayó y cuyo último mensaje todavía no vence. Una capa que deja de publicar sale `caido` a los 15 s
aunque su `/salud` conteste (`evidence/P1_salud_*.txt`).

## Lago y Spark (código de C, preguntas de B)

**1. ¿Cómo se asegura que Spark nunca lea un archivo Parquet a medio escribir?**
El historial junta 500 eventos o 5 s en un micro-lote y lo escribe completo en `datalake/_tmp/`, hace `fsync`
del archivo, lo mueve con `os.replace` a `eventos/mes=AAAA-MM/part-<rango del stream>.parquet` y hace
`fsync` de la carpeta. El rename es atómico en el mismo sistema de archivos: Spark ve el archivo entero o no lo
ve. Recién después hace `XACK` en la cola. Se probó con un lector de Spark que lista y cuenta el lago sin pausa
durante 120 s mientras el simulador escribe: 39 vueltas, 0 errores y la cuenta nunca bajó
(`evidence/P4_gateway_20261115-1102.md`).

**2. Si un evento llega dos veces, ¿dónde se descarta y por qué en más de un lugar?**
En la entrada, `POST historial:/eventos` hace `SET visto:{event_id} NX` y solo encola si es nuevo (un reenvío
del gateway responde `duplicados: 1` y no agrega fila). Reprocesar un micro-lote reescribe el mismo archivo
con el mismo nombre, porque el nombre es el rango del stream, así que no duplica. Y lo que pudiera quedar
repetido igual (por ejemplo, durante la compactación) lo quita Spark con `dropDuplicates(["event_id"])` en
cada consulta. El histórico trae 0,2 % de duplicados a propósito para probarlo.

**3. ¿Por qué una tarea rechazada está `disponible` en MongoDB y `rechazada` en Parquet, y quién cuenta Q4?**
MongoDB guarda el estado vigente: la tarea vuelve a la cola sin asignación para que otro la retrabaje. El lago
guarda el hecho: la fila `rechazada` con motivo, revisor y el equipo de la asignación que se acaba de borrar
(el registro arma el evento con el documento de antes del cambio, `ReturnDocument.BEFORE`). Q4 cuenta las filas
`rechazada` del lago, por eso ve rechazos que MongoDB ya no muestra. Probado en I.3: Q4 del proyecto pasó de
0 a 1 rechazadas.

**4. ¿Por qué la primera ejecución de una consulta tarda más, y por qué dos workers no siempre ayudan?**
No se cachea ningún DataFrame: la primera paga el arranque de los ejecutores, la carga de clases, la
generación de código y el listado de archivos (de 1,1 a 2,1 veces la mediana en `principal`, 3,7 veces si
arranca los ejecutores). Con dos workers, en `principal` es más lento (Q1 de 1,6 a 3,8 s) porque el trabajo cabe
en 2 núcleos y el segundo solo agrega shuffle entre contenedores; en `grande` mejoran Q3 y Q4. La laptop tiene
2 núcleos físicos: no es una medición de escalabilidad horizontal (`evidence/P5_analitica_20261030-1410.md`).

**5. ¿Qué muestra el plan de Q1 sobre la partición por mes, y por qué Q1 casi no crece con el conjunto grande?**
El `Scan parquet` de Q1 lleva `PartitionFilters: mes >= 2026-07`: solo lista y lee las carpetas de los 90 días
de la ventana, y `PushedFilters` baja `estado = aprobada` al lector de Parquet. `max(ts)` (el fin de la
ventana) sale de las estadísticas de los pies de archivo sin leer filas. Por eso Q1 pasa de 1,6 a 3,1 s con 12,5
veces más filas, mientras Q2–Q5 leen los 12 meses y deduplican millones de filas. La partición decide qué
archivos se leen; la agrupación por día no la aprovecha más allá de eso.

## Registro, índices y fallas de MongoDB (código de A, preguntas de C)

**1. El enunciado pide cuatro índices. ¿Por qué hay un quinto, `tareas {estado: 1, prioridad: -1}`?**
Porque `/tareas/siguiente` sin `proyecto_id` (la cola global, que es lo que usa el simulador) filtra por
`estado` y ordena por `prioridad`, y ninguno de los obligatorios empieza por `estado`: el de
`{proyecto_id, estado, prioridad}` no sirve si no viene el proyecto. El `explain("executionStats")` mostró
`COLLSCAN` + `SORT` con 60 000 documentos examinados para devolver 1; con el índice nuevo, 1 clave y 1
documento (`evidence/A3_explain_registro_20261022-2003.md`). Con proyecto basta el obligatorio: cada rama
del `$or` (`disponible`, o `abierta` vencida) es un rango del índice y `SORT_MERGE` las une ya ordenadas.
El costo de un índice más es que todas las transiciones cambian `estado`, así que cada una lo actualiza; por
eso la exportación no lleva índice propio.

**2. ¿Cómo pagina la exportación de aprobadas y por qué no usa `skip`?**
Por llave: `GET /proyectos/{id}/exportar?cursor=&limite=` filtra `proyecto_id`, `estado: aprobada` y, si hay
cursor, `_id > cursor`; ordena por `_id` y devuelve como `siguiente_cursor` el último `_id` de la página
(o `null` si la página vino incompleta). Con `skip` MongoDB tiene que recorrer y descartar todo lo anterior en
cada página, y si una tarea se aprueba entre dos páginas los desplazamientos cambian y se repiten o saltan
filas. Con cursor por `_id` cada página empieza donde terminó la anterior. Límite conocido: el orden por
`_id` se hace en memoria sobre las aprobadas del proyecto (a lo sumo 500 en el dataset; con miles convendría
un índice `{proyecto_id, estado, _id}`).

**3. Cae el primario de MongoDB a mitad de una transición. ¿Se puede aplicar dos veces o perder?**
Ninguna de las dos para lo que respondió 200. El registro escribe con `w: "majority"`: el 200 sale cuando 2
de 3 nodos tienen la escritura, así que la elección no la puede deshacer (no hay *rollback* de lo
confirmado por mayoría). El driver tiene `retryWrites=True`: si el primario cae durante el
`findOneAndUpdate`, lo reintenta una vez contra el nuevo primario con el mismo identificador de
transacción y el servidor no lo aplica dos veces. Si no hay primario en 2 s (`serverSelectionTimeoutMS`) el
registro responde 503 `mongodb_no_disponible`, y el gateway no reintenta los POST. En P8 cayendo el primario:
RTO 10,45 s (lo manda el `electionTimeoutMillis` de 10 s) y RPO 0, 132 de 132 envíos confirmados una sola
vez (`evidence/P8_mongo_gateway_20261110-1715.md`).

**4. En P8 sin mayoría, 10 asignaciones que respondieron error sí quedaron escritas. ¿No es un error de
consistencia?**
No: «error» no significa «no escrito». El primario, antes de darse cuenta de que perdió la mayoría, aplica la
asignación en su copia y espera a la mayoría hasta el `wtimeout` (5 s); el gateway corta a 1 s y responde
503. Cuando la mayoría vuelve, ese nodo sigue de primario y conserva las escrituras. No hay doble dueño
porque la tarea queda `abierta` con un token que nadie tiene; su plazo (`vence_en`, 120 s) vence y el
filtro de la asignación la vuelve a ofrecer. Por eso el RPO se mide solo sobre lo que respondió 200. Con
`w: 1` el riesgo sería el contrario: un 200 que solo estaba en el primario caído puede perderse en la
elección.

**5. En un rechazo MongoDB deja la tarea `disponible` y el lago guarda `rechazada`. ¿Cómo se relacionan y
de dónde saca Q2 el equipo si la asignación se borró?**
Son dos cosas distintas: MongoDB guarda el estado vigente (la tarea vuelve a la cola sin asignación) y el
lago el hecho que pasó. La transición se hace con `find_one_and_update(..., ReturnDocument.BEFORE)`: el
registro recibe el documento como estaba antes del cambio y arma el evento con la asignación vieja
(anotador, equipo, `tomada_en`), aunque en MongoDB ya se haya borrado. Así la fila `rechazada` lleva el
`equipo_id` que usa Q2 y el `tomada_ts` que usan Q3 y Q5. Las dos quedan unidas por el `event_id`: el mismo
`findOneAndUpdate` guarda `ultimo_evento_id` en la tarea. Si el filtro no encuentra la tarea (otro ya la
cambió), responde 409 y no hay evento, así que tampoco fila
(`evidence/P4_historial_20261030-1431.md`, sección 2).
