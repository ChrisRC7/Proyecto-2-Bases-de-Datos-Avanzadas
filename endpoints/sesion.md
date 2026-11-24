# Sesión — API interna (Valkey)

Base: `http://sesion:8002`. Coordinación temporal. **No** decide el estado definitivo de una tarea.
Valkey independiente (`valkey`) con `appendonly yes` y `appendfsync everysec`. Solo este servicio
tiene la dirección/credenciales de esta instancia.

| # | Método y ruta | Uso |
|---|---|---|
| 1 | `POST /locks/{tarea_id}` | `{"accion": "adquirir|renovar|soltar", "token": "uuid"}` |
| 2 | `PUT /borradores/{tarea_id}` | Guarda el borrador `{"anotador_id", "token", "contenido"}` (requiere token vigente) y renueva el lock. |
| 3 | `POST /sesiones/{anotador_id}` | `{"accion": "abrir|latido|cerrar|recuperar|borrar_borrador", "tarea_id", "proyecto_id", "etapa", "token"}`. `abrir` y `recuperar` devuelven el borrador existente si lo hay. `abrir` exige que el token tenga el lock (`409 conflicto_lock` si no); si el borrador era de otro anotador lo archiva en `borrador:archivado:{tarea}:{uuid}` (7 días). `recuperar` responde `404 sesion_no_existe` y borra la sesión si su token ya no tiene el lock (venció o la tomó otro). `borrar_borrador` (`tarea_id`, `token`) borra el borrador solo si es de ese anotador y token: es la limpieza del gateway después de un envío con 200 (`409 borrador_no_coincide` si no). |
| 4 | `POST /estado-capas` | Una capa publica su mensaje de salud `{"servicio", "estado", "detalle"}`. |
| 5 | `GET /estado-capas` | Los mensajes vigentes de todas las capas (el gateway los usa en `/salud`). |
| 6 | `GET /salud` | `PING` + `INFO persistence` (AOF activo). |

Seis rutas contando método y ruta, el máximo del enunciado (D17). Las variantes van como `accion` en el
cuerpo para no agregar rutas.

Respuestas: `200`; `409` si el lock está tomado por otro token o el token no coincide; `404` sin datos.

## Llaves

| Llave | Tipo | Contenido | TTL |
|---|---|---|---|
| `lock:tarea:{id}` | string | token de asignación. `SET NX PX 120000`; renovar/soltar con script Lua que compara el token. | 120 s |
| `sesion:{anotador}` | hash | `tarea`, `proyecto`, `etapa` (anotando/revisando), `token`, `actualizada_en` | — (se borra al cerrar) |
| `borrador:{tarea}` | hash | `contenido` (JSON), `anotador`, `token`, `guardado_en` | — (se borra tras envío 200) |
| `editando:{proyecto}` | sorted set | miembro = anotador, score = epoch ms del último latido | — (se limpian scores viejos) |
| *(sin llave propia)* | — | Envío pendiente: si MongoDB responde 503 al enviar, el gateway no borra el borrador ni suelta el lock, así que el contenido sigue en `borrador:{tarea}` y el anotador reintenta (B2.6). No se usa una llave `pendiente:{tarea}` aparte. | — |
| `salud:{servicio}` | hash | `estado`, `detalle`, `ts` | 15 s |

## Mensajes de salud
Cada servicio interno (registro, historial, analitica) publica cada 5 s su estado con
`POST /estado-capas`. Como la llave tiene TTL 15 s, una capa que deja de publicar desaparece y el
gateway la reporta como `caido`. Ningún otro servicio escribe directamente en Valkey.
