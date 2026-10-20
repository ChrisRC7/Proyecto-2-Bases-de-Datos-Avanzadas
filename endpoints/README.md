# Contratos de la plataforma

Contratos compartidos entre servicios. Cambiar un contrato = PR que actualiza este directorio
**antes** que el código, y avisar a las otras dos personas.

| Archivo | Contenido | Dueño |
|---|---|---|
| [gateway.md](gateway.md) | API pública (única expuesta al host) | B |
| [registro.md](registro.md) | API interna + modelo MongoDB + índices | A |
| [sesion.md](sesion.md) | API interna + llaves Valkey + mensajes de salud | B |
| [historial.md](historial.md) | API interna + esquema del evento Parquet + layout del lago | C |
| [analitica.md](analitica.md) | API interna + definición de Q1–Q5 | C |

## Convenciones comunes

- **Puertos (red `interna`)**: gateway 8000 · registro 8001 · sesion 8002 · historial 8003 ·
  analitica 8004 · mongo1-3 27017 · valkey 6379 · spark-master 7077.
  Solo el gateway publica puerto en el host (`8000:8000`).
- **Máximo 6 rutas** por servicio interno (incluida `/salud`).
- **Identificadores**: UUID v4 en texto (`"3f2c...-..."`).
- **Fechas**: ISO-8601 en UTC (`2026-10-20T21:09:09Z`).
- **Correlación**: header `X-Request-Id`. El gateway lo genera si no viene y lo propaga a todas
  las llamadas internas; todos los servicios lo devuelven en la respuesta y lo escriben en el log.
- **Formato de error** (todos los servicios):
  ```json
  { "error": "codigo_corto", "detalle": "texto u objeto opcional" }
  ```
- **Códigos HTTP**:

  | Código | Uso |
  |---|---|
  | 200 | OK con cuerpo |
  | 201 | Recurso creado |
  | 202 | Aceptado para procesamiento asíncrono (historial) |
  | 204 | Sin tareas disponibles |
  | 400 / 422 | Petición inválida / validación |
  | 404 | No existe |
  | 409 | Token de asignación no coincide, estado no válido para la transición o reenvío |
  | 503 | Dependencia caída (p. ej. MongoDB sin primario). **Sin reintento** en `/enviar` |

- **Reintentos seguros** (solo gateway → interno): se reintentan únicamente operaciones
  idempotentes (`GET`, `PUT .../borrador`, `POST /eventos` por ser idempotente por `event_id`).
- **Logs**: JSON por línea (`ts`, `nivel`, `servicio`, `msg`, `request_id`, ...).
- **Plantilla de servicio**: `servicios/<servicio>/app/comun.py` (idéntico en todos).
