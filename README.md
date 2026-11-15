# Proyecto 2 — Plataforma distribuida de anotación de datos

TI-4601 Bases de Datos Avanzadas · Grupo 3 · Fabricio Mena, Alisson Redondo y Christopher Rodriguez.

Gateway REST (único puerto expuesto, 8000) delante de cuatro servicios: registro (MongoDB, replica set de
3 nodos), sesión (Valkey con AOF), historial (lago Parquet) y analítica (Spark). Contratos en
[`endpoints/`](endpoints/README.md), decisiones en [`DECISIONES.md`](DECISIONES.md), avance en
[`TODO.md`](TODO.md) e informe en [`informe/`](informe/).

## Requisitos previos

> Pendiente (B6.4).

## Inicio y detención

> Pendiente (B6.4): `docker compose up -d --build`, `docker compose down` (y `-v` para borrar los
> volúmenes), perfiles `datos`, `carga` y `spark2`.

## Carga de datos

Con el sistema arriba, el dataset operacional se carga desde cero con:

```bash
docker compose --profile datos run --rm dataset
```

Borra y vuelve a llenar las cinco colecciones de MongoDB (semilla 2026: 60 clientes, 120 proyectos, 600
batches, 60 000 tareas y 300 anotadores), crea los índices e imprime los conteos. Tarda unos 5 s y deja
`dataset/salida/ids.json`, que usan el simulador, las pruebas y el generador del histórico. Si las pruebas
de carga agotan las tareas:

```bash
docker compose --profile datos run --rm dataset \
  python -m dataset.regenerar_tareas --motivo "P6: se agotaron las tareas disponibles"
```

El detalle (conteos, otra semilla, qué se anota en `evidence/regeneraciones.md`) está en
[`datos/README.md`](datos/README.md).

> Pendiente (C6.4): histórico de eventos (`dataset-historico`, conjuntos principal, grande y prueba).

## Simulador

Usuarios virtuales que solo hablan HTTP con el gateway: corre en el contenedor `simulador`, que solo está
en la red `borde` y no ve ninguna base de datos. El 90 % son anotadores (`/tareas/siguiente`, pensar,
`/tareas/{id}/enviar`) y el 10 % revisores (empezar revisión, aprobar, o rechazar con un motivo al azar el
20 % de las veces).

```bash
docker compose --profile carga run --rm simulador python -m simulador.simular \
  --usuarios 40 --calentamiento 30 --duracion 90 --csv /srv/evidence/mi_corrida.csv
```

Opciones útiles: `--proyecto <uuid>` (por defecto la cola global), `--pensar-min/--pensar-max` (segundos
que tarda en anotar), `--rechazo 0.2`, `--semilla`. Al final imprime p50/p95/p99 por endpoint y envíos por
minuto; el CSV tiene una fila por petición (`ts`, `fase`, `usuario`, `rol`, `endpoint`, `status`, `ms`).
`--borradores` activa el hilo de B4.2: mientras el anotador piensa, cada 2 s guarda un borrador
(`PUT /tareas/{id}/borrador`) y manda un latido (`POST /sesiones/latido`). Conviene subir
`--pensar-min/--pensar-max` (por ejemplo 3 y 6) para que cada tarea alcance a guardar borradores.

## Consultas analíticas

> Pendiente (C6.4): `GET /analitica/{q1..q5}?conjunto=principal|grande|prueba` y cómo ver los planes.

## Pruebas

Cada prueba deja sus archivos en `evidence/`. Antes de cada una conviene recargar el dataset (arriba).

| Prueba | Comando | Evidencia |
|---|---|---|
| Rutas de A en el gateway | `python3 pruebas/a4_rutas_gateway.py` | `A4_rutas_gateway_*.txt` |
| P1 rutas, salud y puertos | Pendiente (B5.1) | |
| P2 50 solicitudes contra 10 tareas | `docker compose --profile carga run --rm -v ./pruebas:/srv/pruebas simulador python /srv/pruebas/p2_concurrencia.py --modo gateway --csv /srv/evidence/P2_<fecha>.csv` | `P2_concurrencia_gateway_*` |
| P3 sesión y borrador | Pendiente (B5.2) | |
| P4 historial | `pruebas/p4_historial.sh` (ver C5.1) | `P4_historial_*` |
| P5 analítica | `pruebas/p5_analitica.sh` (ver C5.2) | `P5_analitica_*` |
| P6 carga (10, 40 y 100 usuarios, 3 corridas de 90 s) | `pruebas/p6_carga.sh` (unos 20 min); después `python3 pruebas/p6_lago.py evidence/P6_carga_<fecha>` para ver si los eventos llegaron al lago | `P6_carga_<fecha>/resumen.txt` |
| P8 MongoDB (secundario, primario, sin mayoría) | `pruebas/p8_mongo.sh secundario`, `primario`, `sin-mayoria` | `P8_mongo_gateway_*` |
| P8 Valkey y sesión | Pendiente (B5.3) | |
| Índices (explain) | ver `evidence/A3_explain_registro_20261022-2003.md` | `A3_explain_*` |
| `w: majority` frente a `w: 1` | ver `evidence/A7.4_write_concern_20261022-2011.md` | `A7.4_write_concern_*` |

`pruebas/p8_mongo.sh` mata nodos con `docker compose kill`, vuelve a encenderlos y espera a 1 PRIMARY + 2
SECONDARY antes de terminar. `pruebas/estado_rs.sh` imprime el estado del replica set en cualquier momento.
`pruebas/p6_carga.sh` recarga el dataset y borra las llaves de sesión de Valkey antes de cada corrida.

## Solución de problemas

> Pendiente (B6.4). De lo que encontró A:
>
> - `pruebas/p8_mongo.sh` se cortó a la mitad y quedó un nodo apagado: `docker compose start mongo1 mongo2
>   mongo3` y esperar a que `pruebas/estado_rs.sh` muestre un PRIMARY.
> - `/tareas/siguiente` responde 204 en todo: se agotaron las tareas; recargar el dataset o usar
>   `dataset.regenerar_tareas`.
