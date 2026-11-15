# Plataforma distribuida de anotación de datos LiDAR

Proyecto 2 — Bases de Datos Avanzadas

## Descripción

Plataforma distribuida para administrar proyectos y tareas de anotación, sesiones de usuarios, historial de eventos y consultas analíticas.

El sistema utiliza una arquitectura de servicios independientes coordinados mediante un Gateway HTTP.

| Componente | Tecnología | Responsabilidad |
|---|---|---|
| Gateway | Python, FastAPI | Entrada pública y coordinación |
| Registro | MongoDB 7.0 | Proyectos, tareas y asignaciones |
| Sesión | Valkey 8.0 | Sesiones, locks y borradores |
| Historial | Valkey Streams y Parquet | Eventos y almacenamiento histórico |
| Analítica | Apache Spark | Consultas e indicadores |
| Simulador | Python | Simulación de actividad y carga |

## Requisitos previos

- Git.
- Docker Desktop con Docker Compose v2.
- Docker Desktop iniciado y con recursos suficientes para MongoDB, Valkey y Spark.
- PowerShell para ejecutar los ejemplos de Windows.
- Python 3.12 para ejecutar pruebas locales que requieran Python.

Las dependencias internas de cada servicio se instalan mediante sus imágenes Docker.

## Obtener el proyecto

Clonar el repositorio y entrar en su directorio:

```powershell
git clone https://github.com/ChrisRC7/Proyecto-2-Bases-de-Datos-Avanzadas.git
cd Proyecto-2-Bases-de-Datos-Avanzadas
```

## Iniciar el sistema

Desde la raíz del repositorio:

```powershell
docker compose up -d --build
```

Comprobar el estado de los contenedores:

```powershell
docker compose ps
```

Consultar la salud consolidada del sistema:

```powershell
curl.exe -sS http://localhost:8000/salud
```

El Gateway es el punto de entrada público mediante el puerto `8000`. Los servicios internos y sus motores de almacenamiento no requieren puertos publicados al equipo anfitrión.

Documentación interactiva del Gateway:

`http://localhost:8000/docs`

## Perfiles de Docker Compose

El archivo `docker-compose.yml` define tres perfiles opcionales.

| Perfil | Uso |
|---|---|
| `carga` | Habilitar el simulador de usuarios |
| `datos` | Habilitar los servicios o tareas de preparación de datos |
| `spark2` | Habilitar un segundo trabajador de Spark |

Ejecutar el sistema con el simulador:

```powershell
docker compose --profile carga up -d --build
```

Ejecutar la configuración de preparación de datos:

```powershell
docker compose --profile datos up -d --build
```

Habilitar un segundo trabajador de Spark:

```powershell
docker compose --profile spark2 up -d --build
```

Los perfiles pueden combinarse:

```powershell
docker compose --profile carga --profile spark2 up -d --build
```

La disponibilidad de cada componente depende de sus servicios y dependencias definidos en Docker Compose.

## Comandos de operación

Ver los servicios en ejecución:

```powershell
docker compose ps
```

Consultar los últimos registros del Gateway:

```powershell
docker compose logs --tail 100 gateway
```

Seguir los registros del servicio de Sesión:

```powershell
docker compose logs -f sesion
```

Reiniciar únicamente Valkey:

```powershell
docker compose restart valkey
```

Detener los servicios conservando los volúmenes:

```powershell
docker compose down
```

Volver a iniciarlos:

```powershell
docker compose up -d
```

**Precaución:** los volúmenes persistentes contienen información operacional, sesiones y datos de los motores. No deben eliminarse durante las pruebas habituales.

## Persistencia de datos

El sistema utiliza volúmenes Docker para sus componentes persistentes.

- MongoDB utiliza tres instancias organizadas en el conjunto de réplica `rs0`.
- Valkey de Sesión utiliza el volumen `valkey_sesion`, con AOF activado y `appendfsync everysec`.
- Historial emplea una instancia Valkey independiente, `cola-eventos`, para la mensajería mediante Streams.
- El lago de datos almacena archivos históricos Parquet y se regenera según el procedimiento del proyecto.

La separación entre los motores ayuda a mantener responsabilidades independientes.

## Principales operaciones del Gateway

| Método | Ruta | Finalidad |
|---|---|---|
| `GET` | `/salud` | Estado consolidado de los componentes |
| `POST` | `/proyectos` | Crear un proyecto |
| `POST` | `/proyectos/{id}/lotes` | Registrar un lote |
| `POST` | `/tareas/siguiente` | Solicitar una tarea |
| `PUT` | `/tareas/{id}/borrador` | Guardar un borrador |
| `POST` | `/sesiones/recuperar` | Recuperar sesión y borrador |
| `POST` | `/sesiones/latido` | Renovar actividad y lock |
| `POST` | `/tareas/{id}/enviar` | Enviar una anotación |
| `POST` | `/tareas/{id}/liberar` | Liberar una asignación |
| `POST` | `/tareas/{id}/revision` | Revisar una tarea |
| `GET` | `/proyectos/{id}/exportar` | Exportar resultados |

Los cuerpos JSON, encabezados y códigos HTTP se documentan en `endpoints/gateway.md`.

## Pruebas de integración

Las pruebas de la Persona B se encuentran en `pruebas/` y sus resultados en `evidence/`.

Entre las pruebas disponibles se incluyen:

- B5.1: rutas del Gateway, manejo de errores, estados de salud y puertos expuestos.
- B5.2: recuperación de borradores, persistencia tras reinicio y vencimiento de locks.
- B5.3: recuperación ante fallos de Valkey y medición de RTO/RPO.

Las pruebas que reinician Valkey pueden interrumpir temporalmente otras sesiones. Deben ejecutarse en un entorno de pruebas, sin usuarios trabajando simultáneamente.

## Solución de problemas

### El Gateway no responde

Comprobar los contenedores y revisar los registros:

```powershell
docker compose ps
docker compose logs --tail 100 gateway
```

Si el Gateway está levantado, consultar:

```powershell
curl.exe -i http://localhost:8000/salud
```

### El estado de salud aparece degradado o caído

La ruta `/salud` muestra el estado de los componentes. Revisar los registros del servicio indicado y sus dependencias.

```powershell
docker compose logs --tail 100 sesion
docker compose logs --tail 100 registro
```

### No se pueden guardar borradores

Comprobar que el anotador tenga una sesión activa y utilice el token correcto. Los locks vencen después de 120 segundos si no se renuevan.

Un error HTTP 409 puede indicar que el lock venció o que el token no coincide.

### Una sesión no aparece al recuperarla

La respuesta HTTP 404 `sesion_no_existe` indica que no hay una sesión registrada para el anotador consultado.

Comprobar que se envíe correctamente el encabezado `X-Anotador-Id`.

### MongoDB no está disponible

Revisar los contenedores del conjunto de réplica:

```powershell
docker compose ps
docker compose logs --tail 100 mongo1
```

### Spark no responde

Comprobar los servicios de Analítica y Spark:

```powershell
docker compose ps
docker compose logs --tail 100 analitica
docker compose logs --tail 100 spark-master
```

### Un archivo de evidencia aparece como binario en Git

Algunas versiones de PowerShell pueden guardar la salida de `Tee-Object` con una codificación distinta de UTF-8. Conviene convertir los archivos de evidencia a UTF-8 antes de agregarlos al repositorio.

## Documentación adicional

- `DECISIONES.md`: decisiones técnicas y justificaciones.
- `TODO.md`: distribución de responsabilidades y avance del proyecto.
- `endpoints/`: documentación de las interfaces HTTP.
- `informe/`: secciones preparadas para el informe final.
- `evidence/`: resultados de las pruebas.

## Observaciones de integración

Las pruebas identificaron dos puntos que requieren seguimiento: la posible permanencia de sesiones antiguas después del vencimiento de sus locks y la diferencia entre el vencimiento de una asignación en MongoDB y la renovación del lock correspondiente en Valkey.

La garantía integral de publicación durable de eventos también continúa bajo revisión.

Estas limitaciones se documentan para facilitar la integración y validación final del sistema.