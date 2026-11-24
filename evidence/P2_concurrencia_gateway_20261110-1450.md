# P2 — 50 solicitudes simultáneas contra 10 tareas libres (vía gateway)

| Campo | Valor |
|---|---|
| Fecha y hora (inicio – fin) | 2026-11-10 14:51 – 14:52 (UTC-6) |
| Responsable | Fabricio Mena (A) |
| Objetivo | Mostrar que la asignación es atómica pasando por el gateway, como el simulador: con 10 tareas libres y 50 solicitudes al mismo tiempo, 10 reciben tarea, 40 reciben 204 y ninguna tarea queda con dos anotadores. Un envío con token ajeno y un segundo envío responden 409. |
| Commit | `3a4430e` más el modo gateway de `pruebas/p2_concurrencia.py`, que se sube junto con esta evidencia. |
| Configuración | Replica set `rs0` (3 nodos `mongo:7.0`), registro con `MONGO_W=majority`. Gateway de B (B3.4 asignación con lock y sesión, B3.6 envío) y rutas de A (A4.1 proyectos y lotes, A4.2 revisión). La prueba corre en el contenedor `simulador`, que solo está en la red `borde` y solo ve al gateway. |
| Conjunto de datos | Carga desde cero (semilla 2026) y un proyecto nuevo, creado por el gateway, con un solo lote de exactamente 10 tareas libres; las 50 solicitudes piden tareas de ese proyecto. 50 anotadores distintos de `ids.json`. |
| Máquina | Laptop AMD Ryzen 7 5700U, 16 GB de RAM (4 CPU y 9,7 GiB para WSL 2), Windows 11 Home con Ubuntu 24.04 en WSL 2, Docker Desktop 29.5.2. |

## Comando(s) ejecutado(s)
```bash
docker compose --profile datos run --rm dataset          # carga desde cero
pruebas/estado_rs.sh                                     # estado del replica set
docker compose --profile carga run --rm -v ./pruebas:/srv/pruebas simulador \
  python /srv/pruebas/p2_concurrencia.py --modo gateway --csv /srv/evidence/P2_concurrencia_gateway_20261110-1450.csv
```
Las 50 solicitudes salen de 50 procesos (`ProcessPoolExecutor`) que esperan en una `Barrier` y la
cruzan juntos; salieron en una ventana de 710 ms. Cada ganador envía primero con un token inventado,
después con el suyo y después otra vez con el suyo; un revisor empieza la revisión y aprueba, y al final
se exporta el proyecto.

## Resultado observado

| Métrica | Valor | Muestras |
|---|---|---|
| Respuestas `200` (tarea asignada) | 10 | 50 solicitudes |
| Respuestas `204` (sin tarea) | 40 | 50 solicitudes |
| Tareas asignadas a más de un anotador | 0 | 10 tareas |
| Envío con un token ajeno → `409` | 10 de 10 | 10 |
| Primer envío con el token propio → `200` | 10 de 10 | 10 |
| Segundo envío de la tarea ya enviada → `409` | 10 de 10 | 10 |
| Exportación: aprobadas con el anotador que las tomó | 10 de 10 | 10 |

Estado previo del replica set: `mongo3` PRIMARY, `mongo1` y `mongo2` SECONDARY, `term` 11, los tres con
`health=1`.

**Latencias de las 50 solicitudes.** Medidas en el cliente: `200` p50 3 691 ms (máx. 4 013), `204` p50
3 366 ms (máx. 4 029). En los mismos minutos, el registro atendió las 50 `POST /tareas/asignar` con p50
52 ms y máximo 148 ms (log del registro), y el gateway midió sus propias `/tareas/siguiente` con p50
1 170 ms y máximo 3 327 ms (log del gateway, que no cuenta la espera antes de que la petición entre a la
aplicación). Casi todo el tiempo se va en el gateway, no en MongoDB.

## Interpretación
El resultado de exclusividad es el mismo que en la corrida preliminar contra el registro
(`P2_concurrencia_registro_20261022-2007.md`): la asignación es un solo `findOneAndUpdate` con filtro
`estado = disponible` (o `abierta` vencida), y MongoDB aplica cada actualización de un documento de forma
atómica. De dos solicitudes que apuntan a la misma tarea solo una la encuentra en `disponible`; la otra
pasa a la siguiente o recibe `204`. El gateway suma el lock de Valkey con el mismo token, así que el
envío con un token ajeno lo rechaza la sesión (`409`) antes de llegar a MongoDB, y el segundo envío lo
rechaza el registro (`409`) porque la tarea ya no está en `abierta`.

Sobre la latencia: el cliente HTTP interno del gateway (`servicios/gateway/app/cliente_interno.py`, B3.2)
crea un `httpx.AsyncClient` nuevo en cada llamada. Crear uno toma unos 35 ms de CPU en el contenedor del
gateway (promedio de 20, medido con `docker compose exec gateway`), y ese trabajo bloquea el único
proceso del gateway. `/tareas/siguiente` hace tres llamadas internas (asignar en registro, lock y sesión),
así que 50 solicitudes juntas hacen fila detrás de unos 150 clientes nuevos. Esto no cambia la conclusión
de P2, pero sí va a pesar en P6; se anotó en «Tareas nuevas descubiertas» del `TODO.md` para B.

Límites: los tres nodos de MongoDB y todos los servicios están en la misma máquina; las 50 solicitudes
compiten también por la CPU con el cliente de la prueba.

## Archivos de respaldo
- `evidence/P2_concurrencia_gateway_20261110-1450.txt` (estado del replica set y salida completa)
- `evidence/P2_concurrencia_gateway_20261110-1450.csv` (una fila por solicitud: anotador, token, status, inicio, ms, tarea, error)
- `pruebas/p2_concurrencia.py`, `pruebas/estado_rs.sh`

## Acciones de recuperación
`docker compose --profile datos run --rm dataset` para quitar el proyecto de la prueba y volver al dataset inicial.
