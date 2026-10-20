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

### GET /analitica/{q}?conjunto=principal|grande|prueba
→ `200 {"consulta": "q1", "conjunto": "principal", "filas": [...], "ms": 1234}`

### GET /proyectos/{id}/exportar?cursor=&limite=100
→ `200 {"tareas": [...], "siguiente_cursor": "uuid|null"}` (solo `aprobada`).

### GET /salud
Siempre 200 (el gateway no se cae aunque las capas sí).
```json
{ "estado": "operativo|degradado|caido",
  "capas": { "gateway": "operativo", "registro": "operativo", "sesion": "operativo",
             "historial": "caido", "analitica": "operativo", "mongodb": "operativo",
             "valkey": "operativo", "spark": "operativo" },
  "ts": "..." }
```
Regla: `caido` si registro/MongoDB o sesión/Valkey no están disponibles (no se pueden asignar
tareas); `degradado` si solo fallan historial, analítica o Spark; si no, `operativo`.
