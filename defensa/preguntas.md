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
estado cada 5 s en `sesion:/estado-capas` (llaves `salud:{servicio}` con TTL 15 s). Pendiente: el gateway
todavía no lee esos mensajes para armar su respuesta.

## Lago y Spark (código de C, preguntas de B)

Pendiente (B6.6).

## Registro, índices y fallas de MongoDB (código de A, preguntas de C)

Pendiente (C6.5).
