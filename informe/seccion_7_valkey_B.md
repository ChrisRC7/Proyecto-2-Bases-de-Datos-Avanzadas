# 7. Evaluación y resultados — Componente Valkey

## 7.1 Objetivo de la evaluación

La evaluación del componente Valkey busca comprobar el comportamiento del sistema ante interrupciones de sesiones, vencimiento de locks y fallos temporales del almacenamiento de datos de sesión.

Se analizaron tres aspectos principales: la recuperación de borradores, la exclusividad de las asignaciones y la recuperación del servicio ante reinicios. Las pruebas se realizaron a través del Gateway, utilizando una tarea LiDAR aislada y anotadores de prueba.

## 7.2 Configuración evaluada

La plataforma utiliza Valkey 8.0 como almacenamiento de sesiones, locks y borradores. El servicio se ejecuta en Docker Compose con las siguientes opciones:

```text
appendonly yes
appendfsync everysec
```

La persistencia utiliza el volumen Docker `valkey_sesion`, montado en `/data`.

Los locks tienen una duración de 120 segundos y se adquieren mediante una operación atómica `SET NX PX`. Las renovaciones y liberaciones verifican el token mediante scripts Lua.

El componente de mensajería histórica utiliza otra instancia independiente, `cola-eventos`, cuyo comportamiento no forma parte de estas mediciones.

## 7.3 Resultados de las pruebas

| Prueba | Indicador | Resultado observado |
|---|---|---|
| B5.2 — Interrupción | Tiempo de recuperación de borrador | **0.047 s** |
| B5.2 — Persistencia | Recuperación tras `docker compose restart valkey` | Exitosa |
| B5.2 — Exclusividad | Asignación simultánea antes del vencimiento | Rechazada |
| B5.2 — Vencimiento | Reasignación después de 120 s | Exitosa |
| B5.2 — Token antiguo | Intento de guardar tras la reasignación | **HTTP 409** |
| B5.3 (P8) — Recuperación | RTO observado | **1.016 s** |
| B5.3 (P8) — Persistencia | RPO observado | Sin pérdida del borrador confirmado |
| B5.3 (P8) — Disponibilidad | Respuestas durante el reinicio | **HTTP 503**, seguidas de **HTTP 200** |

Los resultados representan ejecuciones concretas de prueba y no deben interpretarse como garantías universales de latencia o disponibilidad.

## 7.4 Recuperación de borradores y persistencia

La prueba de interrupción B5.2 guardó un borrador y simuló la interrupción de la edición. La sesión se recuperó mediante el Gateway en 0.047 segundos, conservando el token y el contenido guardado.

Posteriormente, una prueba independiente reinició Valkey después de confirmar el almacenamiento del borrador. La recuperación posterior mostró que tanto el token como el contenido sobrevivieron al reinicio.

La evidencia corresponde a `evidence/B5_2_interrupcion.txt` y `evidence/B5_2_persistencia.txt`.

## 7.5 Exclusividad y vencimiento de locks

La prueba B5.2 asignó una tarea al anotador A y verificó que el anotador B no pudiera obtenerla mientras la asignación de A permanecía vigente.

Después de esperar aproximadamente 123 segundos, B obtuvo la tarea con un token nuevo. El token anterior de A recibió HTTP 409 al intentar guardar un borrador, mientras que B pudo guardar correctamente.

Esto demuestra exclusividad en el escenario evaluado de vencimiento normal.

Se observó que una sesión antigua de A permanecía almacenada en Valkey después del vencimiento. La sesión residual se cerró mediante la API interna de Sesión y se comprobó la eliminación de las sesiones de prueba.

**Evidencia:** `evidence/B5_2_vencimiento.txt`.

## 7.6 Recuperación ante fallos: RTO y RPO

La prueba P8 reinició Valkey mientras se enviaban latidos desde una sesión activa.

Durante el reinicio se observaron respuestas HTTP 503. Una vez recuperado el servicio, los latidos volvieron a responder HTTP 200 y fue posible recuperar la sesión y el borrador.

El **RTO observado fue de 1.016 segundos**, medido desde el inicio del reinicio hasta la recuperación exitosa.

El **RPO observado fue sin pérdida del borrador confirmado**: el contenido recuperado coincidió con el previamente guardado. La antigüedad del borrador al momento de la recuperación fue de 2.024 segundos; esta antigüedad no debe confundirse con pérdida de datos.

La configuración `appendfsync everysec` permite una ventana potencial de pérdida cercana a un segundo ante determinados fallos. Por ello, la recuperación completa de una ejecución no demuestra que siempre exista pérdida cero.

**Evidencia:** `evidence/B5_3_P8.txt`.

## 7.7 Limitaciones identificadas

Las pruebas permitieron reconocer dos limitaciones relevantes:

**Sesiones residuales después del vencimiento.** Cuando expira el lock de una tarea, puede permanecer almacenada la sesión del anotador anterior. Aunque el token vencido fue rechazado al intentar editar, la sesión residual requiere un mecanismo de limpieza automática o una política de expiración explícita.

**Diferencia entre vencimientos de MongoDB y Valkey.** El latido renueva el lock en Valkey, pero no actualiza `asignacion.vence_en` en MongoDB. Esto puede generar inconsistencias durante sesiones de larga duración y requiere coordinación con el servicio Registro.

También permanece parcialmente pendiente la garantía integral de publicación durable de eventos correspondiente a B3.6. Esta limitación se refiere a la coordinación de eventos entre Gateway e Historial y no invalida las pruebas de persistencia del Valkey de Sesión.

## 7.8 Conclusión

En los escenarios evaluados, Valkey permitió recuperar borradores, conservar datos después de reinicios y rechazar tokens antiguos tras el vencimiento de los locks.

La prueba de recuperación ante fallos registró un RTO de 1.016 segundos y ninguna pérdida del borrador confirmado. Durante el reinicio se observó una indisponibilidad temporal, seguida de recuperación satisfactoria.

Los resultados respaldan el funcionamiento del componente de Sesión en los casos probados, mientras que la limpieza de sesiones residuales y la coordinación de vencimientos entre MongoDB y Valkey permanecen como oportunidades de mejora antes de la integración final.