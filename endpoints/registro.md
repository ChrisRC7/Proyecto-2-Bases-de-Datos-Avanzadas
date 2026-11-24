# Registro — API interna (MongoDB)

Base: `http://registro:8001`. Única fuente de verdad del estado de las tareas. **No** usa Valkey.
Escritura con `w: "majority"` (configurable con `MONGO_W` solo para la comparación con `w: 1`).

| # | Método y ruta | Uso |
|---|---|---|
| 1 | `POST /proyectos` | Crear proyecto (valida cliente). |
| 2 | `POST /proyectos/{id}/lotes` | Crear batch + tareas. |
| 3 | `POST /tareas/asignar` | Asignación atómica (`findOneAndUpdate`). |
| 4 | `POST /tareas/{id}/transicion` | enviar · empezar_revision · aprobar · rechazar · liberar · renovar (plazo, sin cambio de estado). |
| 5 | `GET /proyectos/{id}/exportar` | Aprobadas, paginación por cursor (`_id > cursor`). |
| 6 | `GET /salud` | Estado del servicio y del replica set (PRIMARY visible o no). |

### POST /tareas/asignar
Body `{"anotador_id": "uuid", "proyecto_id": "uuid|null", "token": "uuid"}` (el token lo genera el gateway).
Filtro: `estado = "disponible"` **o** (`estado = "abierta"` y `asignacion.vence_en < ahora`), ordenado por `prioridad` desc.
Update: `estado="abierta"`, `version += 1`, `asignacion = {anotador_id, token, tomada_en, vence_en}`.
→ `200 {tarea, evento}` · `204` · `503`.

### POST /tareas/{id}/transicion
Body `{"accion": "...", "token": "uuid|null", "version": n, "actor_id": "uuid", "anotacion": {...}|null, "motivo": "...|null"}`.

| accion | Estado requerido | Requiere token | Nuevo estado Mongo | Estado en el evento |
|---|---|---|---|---|
| enviar | abierta | sí | anotada | anotada |
| liberar | abierta | sí | disponible | liberada* |
| empezar_revision | anotada | no | en_revision | en_revision |
| aprobar | en_revision | no | aprobada | aprobada |
| rechazar | en_revision | no | disponible | **rechazada** |
| renovar | abierta y plazo vigente | sí | (sin cambio) | (sin evento) |

`renovar` corre `asignacion.vence_en` otros 120 s cuando el gateway recibe un latido o un borrador, igual
que el lock de Valkey. Solo escribe si quedan menos de 60 s (si no, `200 {"renovado": false, "vence_en"}`);
no cambia la versión ni genera evento. Si la asignación ya venció o no es de ese token → `409
asignacion_vencida`. Va como acción de esta ruta para no pasar de seis rutas por servicio.

\* `liberar` no aparece en la tabla de transiciones del enunciado; se registra como evento `liberada`
para trazabilidad y Spark lo ignora en Q1–Q5 (documentarlo en el informe).

Si el filtro (`_id`, estado, token, version) no coincide → `409`. Cada transición válida: `version += 1`,
genera `event_id` (UUID) y lo guarda en `ultimo_evento_id`.
Respuesta `200 {"tarea": {...}, "evento": {<fila según historial.md>}}` — el gateway reenvía `evento` al historial.
`version` es opcional y el gateway hoy no la manda: en `enviar` y `liberar` el filtro por estado y token ya
deja pasar una sola petición, y en la revisión basta el filtro por estado. Queda para un cliente que quiera
control optimista explícito.

### Errores

| Código | `error` | Cuándo |
|---|---|---|
| 404 | `cliente_no_existe`, `proyecto_no_existe`, `anotador_no_existe`, `tarea_no_existe` | El id no está en la base. |
| 409 | `transicion_invalida` | No coincide estado, token o versión. `detalle` trae el estado y la versión actuales. |
| 422 | `validacion` | Cuerpo inválido (uuid, sha256, uri, taxonomía repetida, rechazo sin motivo...). |
| 422 | `lote_invalido` | `tipo_tarea` o `mime` no corresponden a la modalidad del proyecto. |
| 422 | `anotacion_invalida` | Geometría que no corresponde al tipo de tarea o etiquetas fuera de la taxonomía. |
| 422 | `actor_no_es_revisor`, `proyecto_cerrado` | Revisión hecha por un anotador; lote para un proyecto cerrado. |
| 503 | `mongodb_no_disponible` | No hay primario (responde en unos 2 s, `serverSelectionTimeoutMS`). |
| 503 | `mongodb_sin_mayoria` | `w: majority` no se confirmó en `wtimeout`; la escritura pudo quedar en el primario. |

`GET /salud` responde siempre 200: `{"servicio", "estado", "mongodb": {"estado": "operativo|caido",
"primario", "secundarios"}, "ts"}`; `estado` es el de MongoDB (`caido` si no hay primario, porque sin él
no se puede asignar ni guardar). Cada 5 s el registro publica ese estado en `sesion:/estado-capas` (D4).

## Modelo (BD `plataforma`)

```jsonc
// clientes
{ "_id": "uuid", "nombre": "str", "creado_en": "date" }
// proyectos
{ "_id": "uuid", "cliente_id": "uuid", "nombre": "str", "estado": "activo|cerrado",
  "modalidad": "texto|imagen|video|lidar",
  "taxonomia": [{ "etiqueta": "lidar#ciclista", "instruccion": "..." }],
  "sla_horas": 48, "prioridad": 3, "creado_en": "date" }
// batches
{ "_id": "uuid", "proyecto_id": "uuid", "total_tareas": 100, "creado_en": "date" }
// anotadores
{ "_id": "uuid", "nombre": "str", "equipo_id": "uuid", "rol": "anotador|revisor" }
// tareas
{ "_id": "uuid", "proyecto_id": "uuid", "cliente_id": "uuid", "batch_id": "uuid",
  "modalidad": "lidar", "tipo_tarea": "cuboide_3d", "prioridad": 3, "sla_horas": 48,
  "archivo": { "uri": "...", "sha256": "...", "bytes": 1, "mime": "...", "meta": {} },
  "estado": "disponible|abierta|anotada|en_revision|aprobada",
  "version": 0,
  "asignacion": { "anotador_id": "uuid", "equipo_id": "uuid", "token": "uuid",
                  "tomada_en": "date", "vence_en": "date" },          // null si no hay
  "anotacion": { "etiquetas": [], "geometria": {}, "anotador_id": "uuid", "enviada_en": "date" },
  "ultima_revision": { "revisor_id": "uuid", "resultado": "aprobada|rechazada", "motivo": null, "en": "date" },
  "ultimo_evento_id": "uuid" }
```

`cliente_id` y `sla_horas` se desnormalizan en la tarea para no hacer joins al generar eventos.
`vence_en = tomada_en + 120 s` (igual que el lock de Valkey).

## Índices

| Índice | Justificación |
|---|---|
| `tareas {proyecto_id:1, estado:1, prioridad:-1}` | Obligatorio. Asignación por proyecto. |
| `tareas {"asignacion.anotador_id":1, estado:1}` | Obligatorio. Tareas de un anotador / recuperación. |
| `tareas {batch_id:1}` | Obligatorio. |
| `proyectos {cliente_id:1, estado:1}` | Obligatorio. |
| `tareas {estado:1, prioridad:-1}` | Extra. Asignación sin `proyecto_id` (cola global): sin él, `COLLSCAN` de 60 000 tareas; con él, 1 documento examinado (`evidence/A3_explain_registro_20261022-2003.md`). |
| (otros extras) | Solo con `explain` en `evidence/` que lo justifique. La exportación no lleva índice propio: ordena en memoria como máximo las aprobadas de un proyecto (≤ 500). |
