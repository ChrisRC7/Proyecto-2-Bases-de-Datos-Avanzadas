# Gateway — API pública

Base: `http://localhost:8000`. Único componente expuesto al host. El simulador solo habla con estas rutas.

Headers comunes: `X-Request-Id` (opcional en la petición, siempre en la respuesta).
Identidad del usuario simulado: header `X-Anotador-Id: <uuid>` (anotador o revisor).

| Método y ruta | Servicios internos | Respuestas |
|---|---|---|
| `POST /proyectos` | registro | 201, 404 (cliente), 422 |
| `POST /proyectos/{id}/lotes` | registro | 201, 404, 422 |
| `POST /tareas/siguiente` | sesion → registro → sesion (lock) → historial | 200, 204, 503 |
| `PUT /tareas/{id}/borrador` | sesion | 200, 409 |
| `POST /tareas/{id}/enviar` | sesion → registro → historial | 200, 409, 503 |
| `POST /tareas/{id}/liberar` | registro → sesion → historial | 200, 409 |
| `POST /tareas/{id}/revision` | registro → historial | 200, 409, 422 |
| `POST /sesiones/recuperar` | sesion | 200, 404 |
| `POST /sesiones/latido` | sesion | 200, 404, 409, 503 |
| `GET /analitica/{q1..q5}?conjunto=` | analitica | 200, 404, 503 |
| `GET /proyectos/{id}/exportar?cursor=&limite=` | registro | 200, 404 |
| `GET /salud` | todas (timeout corto) | 200 siempre |

## Cuerpos

### POST /proyectos
```json
{
  "cliente_id": "uuid",
  "nombre": "LiDAR urbano 2026",
  "modalidad": "lidar",
  "taxonomia": [
    {"etiqueta": "lidar#peaton",    "instruccion": "Persona a pie."},
    {"etiqueta": "lidar#vehiculo",  "instruccion": "Automóvil, bus o camión."},
    {"etiqueta": "lidar#ciclista",  "instruccion": "Un skater se clasifica como ciclista, no como peatón."},
    {"etiqueta": "lidar#signal",    "instruccion": "Señal de tránsito vertical."}
  ],
  "sla_horas": 48,
  "prioridad": 3
}
```
→ `201 {"proyecto_id": "uuid"}`

### POST /proyectos/{id}/lotes
```json
{
  "tareas": [
    {
      "tipo_tarea": "cuboide_3d",
      "prioridad": 3,
      "archivo": {
        "uri": "s3://datos/lidar/escena-0001.pcd",
        "sha256": "9f86d08...",
        "bytes": 4823112,
        "mime": "application/x-pcd",
        "meta": {"min": 0.0, "max": 120.5, "puntos": 120000}
      }
    }
  ]
}
```
→ `201 {"batch_id": "uuid", "tareas": 1}`

### POST /tareas/siguiente
Body: `{"proyecto_id": "uuid (opcional)"}` · Header `X-Anotador-Id`.
→ `200`
```json
{
  "tarea": { "tarea_id": "uuid", "proyecto_id": "uuid", "modalidad": "lidar",
             "tipo_tarea": "cuboide_3d", "archivo": {"uri": "...", "sha256": "...", "bytes": 1, "mime": "...", "meta": {}},
             "taxonomia": [], "version": 1 },
  "token": "uuid",
  "vence_en": "2026-10-20T21:11:09Z",
  "borrador": null
}
```
→ `204` sin tareas · `503` registro/sesión no disponible.

### PUT /tareas/{id}/borrador
Body: `{"token": "uuid", "contenido": {...}}` → `200 {"guardado_en": "...", "lock_vence_en": "..."}` · `409` token no coincide.

### POST /tareas/{id}/enviar
Body:
```json
{ "token": "uuid",
  "anotacion": { "etiquetas": ["lidar#ciclista"],
                 "geometria": {"tipo": "cuboide_3d", "centro": [1.2, 3.4, 0.5], "dimensiones": [0.6, 1.8, 1.7], "yaw": 0.3} } }
```
→ `200 {"estado": "anotada", "version": 2, "event_id": "uuid"}` · `409` token o estado · `503` MongoDB no responde (no se reintenta; el borrador sigue en Valkey).

Geometrías por modalidad: texto → `span` (`inicio`, `fin`); imagen → `bbox` (`x`,`y`,`w`,`h`) o
`poligono` (`puntos`); video → `bbox_video` (`cuadros: [{t, x, y, w, h}]`); lidar → `cuboide_3d`.

### POST /tareas/{id}/liberar
Body: `{"token": "uuid"}` → `200 {"estado": "disponible"}` · `409`.

### POST /tareas/{id}/revision
Header `X-Anotador-Id` (revisor). Body:
- empezar: `{"accion": "empezar"}` → estado `en_revision`
- aprobar: `{"accion": "aprobar"}` → `aprobada`
- rechazar: `{"accion": "rechazar", "motivo": "ETIQUETA_INCORRECTA"}` → Mongo `disponible`, lago `rechazada`

Códigos de motivo: `ETIQUETA_INCORRECTA`, `GEOMETRIA_IMPRECISA`, `OBJETO_OMITIDO`, `INSTRUCCION_NO_SEGUIDA`, `OTRO`.
→ `200 {"estado": "...", "version": n, "event_id": "uuid"}` · `409` estado inválido · `422` rechazo sin motivo.

### POST /sesiones/recuperar
Header `X-Anotador-Id` → `200 {"tarea": {...}, "token": "uuid", "borrador": {"contenido": {}, "guardado_en": "..."}}` · `404` sin sesión.

### POST /sesiones/latido
Header: `X-Anotador-Id` (UUID).
Body: `{"token": "uuid"}`.

Renueva el lock y actualiza la actividad de la sesión en Valkey, y corre también el plazo de la
asignación en MongoDB (`registro:/tareas/{id}/transicion` con `accion: "renovar"`), para que el registro
no ofrezca a otro una tarea cuyo lock sigue vivo. El registro solo escribe cuando queda menos de la mitad
del plazo (60 s); la respuesta trae `sesion.registro_renovado` (`true` escribió, `false` no hacía falta,
`null` el registro no respondió: el lock ya se renovó y el siguiente latido lo vuelve a intentar).

- `200`: latido confirmado.
- `404`: sesión inexistente.
- `409`: token incorrecto o lock vencido (`conflicto_lock`), o la asignación ya venció en el registro
  (`asignacion_vencida`).
- `503`: servicio Sesión no disponible.

### GET /analitica/{q}?conjunto=principal|grande|prueba
→ `200 {"consulta": "q1", "conjunto": "principal", "filas": [...], "ms": 1234}`

Pasa tal cual la respuesta de `analitica:/q/{n}` (también trae `parametros`, `primera_ejecucion` y
`ejecutores`, ver `analitica.md`). Sin `conjunto` usa `principal`. `422 validacion` si `q` no es q1–q5 o el
conjunto no existe en la lista; `404 conjunto_no_existe` si la carpeta no tiene Parquet; `503
servicio_no_disponible` si la analítica o Spark no responden. Límite de 180 s y **sin reintento**: una
consulta de Spark tarda segundos y repetirla después de un timeout la lanzaría dos veces.

### GET /proyectos/{id}/exportar?cursor=&limite=100
→ `200 {"tareas": [...], "siguiente_cursor": "uuid|null"}` (solo `aprobada`).


### GET /salud

Siempre responde HTTP 200, incluso cuando las capas internas
no están disponibles.

Cada capa (registro, historial, analítica) publica cada 5 s su estado en Valkey por
`sesion:/estado-capas` (llave `salud:{servicio}`, TTL 15 s). El gateway lee esos mensajes
(`GET sesion:/estado-capas`) y, en paralelo, consulta el `/salud` de cada servicio con timeout de
aproximadamente 1 segundo. Para cada capa gana el peor de los dos: el mensaje trae lo que la capa sabe de
sí misma (primario de MongoDB, ejecutores de Spark, cola) y la consulta directa detecta en el momento una
capa caída cuyo último mensaje todavía no venció. Una capa sin mensaje vigente en Valkey cuenta como
`caido`. Cada capa trae `fuente` (`valkey+directa`, o `directa` para sesión y cuando Valkey no responde) y
`mensaje_valkey`; la raíz trae `mensajes_valkey: true|false`.

Ejemplo:

```json
{
  "servicio": "gateway",
  "estado": "degradado",
  "capas": {
    "registro": {
      "estado": "operativo",
      "detalle": {}
    },
    "sesion": {
      "estado": "operativo",
      "detalle": {}
    },
    "historial": {
      "estado": "caido",
      "detalle": "ConnectError"
    },
    "analitica": {
      "estado": "operativo",
      "detalle": {}
    }
  },
  "ts": "2026-11-10T03:29:20+00:00"
}
```

Estados agregados:
- `operativo`: las cuatro capas están operativas.
- `caido`: registro (MongoDB) o sesión (Valkey) está caído; sin ellos no se puede asignar ni enviar.
- `degradado`: cualquier otra combinación (por ejemplo historial o analítica caídos).

El Gateway conserva HTTP 200 para permitir consultar
el diagnóstico incluso durante fallos.
