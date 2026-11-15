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

Renueva el lock y actualiza la actividad de la sesión en Valkey.

- `200`: latido confirmado.
- `404`: sesión inexistente.
- `409`: token incorrecto o lock vencido.
- `503`: servicio Sesión no disponible.

### GET /proyectos/{id}/exportar?cursor=&limite=100
→ `200 {"tareas": [...], "siguiente_cursor": "uuid|null"}` (solo `aprobada`).


### GET /salud

Siempre responde HTTP 200, incluso cuando las capas internas
no están disponibles.

Consulta Registro, Sesión, Historial y Analítica en paralelo,
con timeout de aproximadamente 1 segundo por servicio.

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
- `degradado`: existe una combinación de estados.
- `caido`: las cuatro capas están caídas.

El Gateway conserva HTTP 200 para permitir consultar
el diagnóstico incluso durante fallos.


## Ejemplos de llamadas al Gateway desde PowerShell

Los siguientes ejemplos utilizan `curl.exe`. Los identificadores deben sustituirse por valores reales antes de ejecutar las operaciones. Las operaciones que crean o modifican datos deben realizarse sobre proyectos de prueba.

**Variables comunes**

```powershell
$base = "http://localhost:8000"
$anotador = "UUID_ANOTADOR"
$revisor = "UUID_REVISOR"
$cliente = "UUID_CLIENTE"
$proyecto = "UUID_PROYECTO"
$tarea = "UUID_TAREA"
$token = "UUID_TOKEN"
```

### 1. Crear proyecto — POST /proyectos

```powershell
$body = @{cliente_id=$cliente; nombre="LiDAR de prueba"; modalidad="lidar"; taxonomia=@(@{etiqueta="lidar#ciclista"; instruccion="Identificar ciclistas"}); sla_horas=48; prioridad=3} | ConvertTo-Json -Depth 8 -Compress
curl.exe -i -X POST "$base/proyectos" -H "Content-Type: application/json" --data-raw $body
```

Respuesta habitual: HTTP 201. Errores: 404 y 422.

### 2. Registrar lote — POST /proyectos/{id}/lotes

```powershell
$body = @{tareas=@(@{tipo_tarea="cuboide_3d"; prioridad=3; archivo=@{uri="s3://pruebas/escena.pcd"; sha256="f3d1d93aea8f4dd3ae59d438551e382ef3d1d93aea8f4dd3ae59d438551e382e"; bytes=1000; mime="application/x-pcd"; meta=@{min=0; max=100}}})} | ConvertTo-Json -Depth 8 -Compress
curl.exe -i -X POST "$base/proyectos/$proyecto/lotes" -H "Content-Type: application/json" --data-raw $body
```

Respuesta habitual: HTTP 201. Errores: 404 y 422.

### 3. Solicitar tarea — POST /tareas/siguiente

```powershell
$body = @{proyecto_id=$proyecto} | ConvertTo-Json -Compress
curl.exe -i -X POST "$base/tareas/siguiente" -H "X-Anotador-Id: $anotador" -H "Content-Type: application/json" --data-raw $body
```

Respuestas: 200 si se asigna una tarea, 204 si no hay tareas y 503 ante una falla de dependencias. Puede responder 422 por validación.

### 4. Guardar borrador — PUT /tareas/{id}/borrador

```powershell
$body = @{token=$token; contenido=@{etiquetas=@("lidar#ciclista")}} | ConvertTo-Json -Depth 8 -Compress
curl.exe -i -X PUT "$base/tareas/$tarea/borrador" -H "X-Anotador-Id: $anotador" -H "Content-Type: application/json" --data-raw $body
```

Respuestas principales: 200, 409 y 422.

### 5. Enviar anotación — POST /tareas/{id}/enviar

```powershell
$body = @{token=$token; anotacion=@{etiquetas=@("lidar#ciclista"); geometria=@{tipo="cuboide_3d"; centro=@(1.2,3.4,0.5); dimensiones=@(0.6,1.8,1.7); yaw=0.3}}} | ConvertTo-Json -Depth 8 -Compress
curl.exe -i -X POST "$base/tareas/$tarea/enviar" -H "X-Anotador-Id: $anotador" -H "Content-Type: application/json" --data-raw $body
```

Respuestas principales: 200, 409, 422 y 503.

### 6. Liberar tarea — POST /tareas/{id}/liberar

```powershell
$body = @{token=$token} | ConvertTo-Json -Compress
curl.exe -i -X POST "$base/tareas/$tarea/liberar" -H "X-Anotador-Id: $anotador" -H "Content-Type: application/json" --data-raw $body
```

Respuestas principales: 200, 409 y 422.

### 7. Revisar tarea — POST /tareas/{id}/revision

```powershell
$body = @{accion="empezar"} | ConvertTo-Json -Compress
curl.exe -i -X POST "$base/tareas/$tarea/revision" -H "X-Anotador-Id: $revisor" -H "Content-Type: application/json" --data-raw $body
```

Para aprobar: `@{accion="aprobar"}`. Para rechazar: `@{accion="rechazar"; motivo="ETIQUETA_INCORRECTA"}`.

Respuestas principales: 200, 409 y 422.

### 8. Recuperar sesión — POST /sesiones/recuperar

```powershell
curl.exe -i -X POST "$base/sesiones/recuperar" -H "X-Anotador-Id: $anotador"
```

Respuestas principales: 200 y 404.

### 9. Enviar latido — POST /sesiones/latido

```powershell
$body = @{token=$token} | ConvertTo-Json -Compress
curl.exe -i -X POST "$base/sesiones/latido" -H "X-Anotador-Id: $anotador" -H "Content-Type: application/json" --data-raw $body
```

Respuestas principales: 200, 404, 409, 422 y 503.

### 10. Exportar resultados — GET /proyectos/{id}/exportar

```powershell
curl.exe -i "$base/proyectos/$proyecto/exportar?limite=100"
```

Respuestas principales: 200 y 404.

### 11. Consultar salud — GET /salud

```powershell
curl.exe -i "$base/salud"
```

Respuesta HTTP 200 con estado `operativo`, `degradado` o `caido`, según la disponibilidad de las capas.

### Consideraciones

Los ejemplos usan identificadores ilustrativos. Para operar sobre tareas se necesita una asignación vigente y el token correspondiente. Los códigos HTTP 422 representan problemas de validación, mientras que los conflictos de estado o token pueden responder HTTP 409. Los servicios internos pueden provocar errores 503 cuando no están disponibles.