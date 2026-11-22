# P1, P3 y P8 (Valkey) — sesión, borrador, lock y reinicio de Valkey (B5.1–B5.3)

Ficha de las pruebas de B con la plantilla (`_PLANTILLA.md`). Las corrió Alisson y sus archivos crudos
(`B5_1_*`, `B5_2_*`, `B5_3_P8.txt`) son la salida tal cual de cada script. Esta ficha se escribió después,
el 2026-11-21, solo con lo que está en esos archivos y en git: no agrega mediciones.

| Campo | Valor |
|---|---|
| Fecha y hora | 2026-11-15. B5.1: 17:42–17:56 UTC (encabezados `date:` de las respuestas HTTP). B5.2: borradores guardados a las 21:11 y 21:14 UTC (`guardado_en`). B5.3: subida a git a las 21:49 UTC. |
| Responsable | AlissonRM19 (B) |
| Objetivo | P1: rutas de sesión del gateway, sus errores, `/salud` y que solo el gateway publique un puerto. P3: recuperar el borrador tras una interrupción, que el borrador sobreviva a un reinicio de Valkey con AOF y que al vencer el lock la tarea vuelva a ofrecerse sin dos dueños. P8 (Valkey): RTO y RPO del borrador al reiniciar Valkey con una sesión activa. |
| Commit | Archivos agregados en `3456f27` (B5.1), `1a685f0` (B5.2) y `866f729` (B5.3). |
| Configuración | `docker compose up` sin perfiles. Valkey 8.0 con `appendonly yes` y `appendfsync everysec`, volumen `valkey_sesion`. Lock de 120 s. Cada prueba crea su propio proyecto con una tarea LiDAR y la libera al final. |
| Conjunto de datos | Dataset operacional de semilla 2026 cargado (`datos/README.md`); las pruebas no dependen de sus conteos. |
| Máquina | La de Alisson: Windows con Docker Desktop, scripts corridos desde PowerShell (se ve en la salida de `Tee-Object`). **El procesador y la memoria no quedaron anotados.** Las mismas pruebas se repitieron en la laptop Intel i7-6500U dentro de la corrida desde cero I5 (`I5_entorno_limpio_20261115-2205.log`). |

## Comando(s) ejecutado(s)
```bash
python pruebas/b51_sesiones_gateway.py          | Tee-Object evidence/B5_1_sesiones.txt
docker compose ps                                > evidence/B5_1_puertos.txt
curl.exe -i http://localhost:8000/salud          # normal, sin historial, sin Valkey → B5_1_salud_*
python pruebas/b52_interrupcion_gateway.py       | Tee-Object evidence/B5_2_interrupcion.txt
python pruebas/b52_persistencia_gateway.py       | Tee-Object evidence/B5_2_persistencia.txt
python pruebas/b52_vencimiento_gateway.py        | Tee-Object evidence/B5_2_vencimiento.txt
python pruebas/b53_fallo_valkey.py               | Tee-Object evidence/B5_3_P8.txt
```

## Resultado observado

| Prueba | Resultado | Muestras | Archivo |
|---|---|---|---|
| P1 rutas de sesión | Borrador y latido con token ajeno → 409; con el token correcto → 200; `recuperar` devuelve la misma tarea, token y borrador | 1 corrida | `B5_1_sesiones.txt` |
| P1 errores | Sin `X-Anotador-Id` → 422; id inválido o sin token → 422; `recuperar` sin sesión → 404 `sesion_no_existe` | 1 c/u | `B5_1_*_invalido.txt`, `B5_1_siguiente_sin_anotador.txt`, `B5_1_recuperar_sin_sesion.txt` |
| P1 `/salud` | Normal: `operativo`; historial parado: `degradado`; Valkey parado: `caido` (la sesión no responde) | 1 c/u | `B5_1_salud_*` |
| P1 puertos | Solo `gateway` publica un puerto (`0.0.0.0:8000->8000/tcp`) | 1 | `B5_1_puertos.txt` |
| P3 interrupción | `recuperar` → 200 con el mismo token y contenido; la llamada tardó **0,047 s** | 1 | `B5_2_interrupcion.txt` |
| P3 reinicio de Valkey | Tras `docker compose restart valkey`: token y contenido del borrador intactos | 1 | `B5_2_persistencia.txt` |
| P3 vencimiento | Con el lock vigente el otro anotador no recibe la tarea; tras 123 s sí, con otro token; el token viejo recibe 409 al guardar y el nuevo 200 | 1 | `B5_2_vencimiento.txt` |
| P8 Valkey | Latidos: 5 × 200 y 2 × 503 durante el reinicio. **RTO 1,016 s**; borrador confirmado recuperado igual (RPO 0); edad del borrador al recuperarlo 2,024 s | 1 | `B5_3_P8.txt` |

Repetición en la i7-6500U (I5): interrupción 0,027 s; persistencia y vencimiento iguales; P8 con RTO 0,930 s,
6 × 200 y 1 × 503, borrador de 1,946 s recuperado igual.

## Interpretación
El lock (`SET NX PX`, renovar y soltar en Lua comparando el token) impide editar con un token viejo, y la
tarea vuelve a la cola cuando el lock vence. El borrador confirmado sobrevive al reinicio de Valkey.
`docker compose restart` es un apagado limpio, así que Valkey sincroniza el AOF antes de salir: este RPO 0
no prueba la ventana de hasta ~1 s de `appendfsync everysec` ante una caída sin aviso, que no se midió. Una
sola muestra por escenario y máquina: no hay percentiles.

En la primera corrida de vencimiento la sesión del anotador vencido seguía en Valkey después de la
reasignación y se cerró a mano. Se corrigió después: la sesión se borra en Lua al recuperarla y
`recuperar` responde 404 (informe, sección 3.3).

## Archivos de respaldo
- `evidence/B5_1_*`, `evidence/B5_2_*`, `evidence/B5_3_P8.txt`
- `evidence/I5_entorno_limpio_20261115-2205.log` (secciones «P1», «P3» y «P8 Valkey»)

## Acciones de recuperación (pruebas de fallas)
Cada script libera su tarea al final (`LIMPIEZA_HTTP_200`). Valkey vuelve solo después de
`docker compose restart valkey`. Después de las pruebas de `/salud` con historial y Valkey parados, el
sistema volvió a `operativo` (`B5_1_salud_recuperada.json`, `B5_1_salud_post_valkey.json`).
