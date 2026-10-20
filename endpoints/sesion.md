# Sesión — API interna (Valkey)

Base: `http://sesion:8002`. Coordinación temporal. **No** decide el estado definitivo de una tarea.
Valkey independiente (`valkey`) con `appendonly yes` y `appendfsync everysec`. Solo este servicio
tiene la dirección/credenciales de esta instancia.

| # | Método y ruta | Uso |
|---|---|---|
| 1 | `POST /locks/{tarea_id}` | `{"accion": "adquirir|renovar|soltar", "token": "uuid"}` |
| 2 | `PUT /borradores/{tarea_id}` | Guarda borrador (requiere token vigente) y renueva el lock. |
| 3 | `POST /sesiones/{anotador_id}` | `{"accion": "abrir|latido|cerrar|recuperar", "tarea_id", "proyecto_id", "etapa", "token"}`. `abrir` y `recuperar` devuelven el borrador existente si lo hay. |
| 4 | `POST /estado-capas` | Una capa publica su mensaje de salud `{"servicio", "estado", "detalle"}`. |
| 5 | `GET /estado-capas` | Lee los últimos mensajes de salud de todas las capas. |
| 6 | `GET /salud` | `PING` + `INFO persistence` (AOF activo). |

Respuestas: `200`; `409` si el lock está tomado por otro token o el token no coincide; `404` sin datos.

## Llaves

| Llave | Tipo | Contenido | TTL |
|---|---|---|---|
| `lock:tarea:{id}` | string | token de asignación. `SET NX PX 120000`; renovar/soltar con script Lua que compara el token. | 120 s |
| `sesion:{anotador}` | hash | `tarea`, `proyecto`, `etapa` (anotando/revisando), `token`, `actualizada_en` | — (se borra al cerrar) |
| `borrador:{tarea}` | hash | `contenido` (JSON), `anotador`, `token`, `guardado_en` | — (se borra tras envío 200) |
| `editando:{proyecto}` | sorted set | miembro = anotador, score = epoch ms del último latido | — (se limpian scores viejos) |
| `pendiente:{tarea}` | hash | envío que recibió 503 (para que el usuario reintente) | — |
| `salud:{servicio}` | hash | `estado`, `detalle`, `ts` | 15 s |

## Mensajes de salud
Cada servicio (registro, historial, analitica, gateway) publica cada 5 s su estado con
`POST /estado-capas`. Como la llave tiene TTL 15 s, una capa que deja de publicar desaparece y el
gateway la reporta como `caido`. Ningún otro servicio escribe directamente en Valkey.
