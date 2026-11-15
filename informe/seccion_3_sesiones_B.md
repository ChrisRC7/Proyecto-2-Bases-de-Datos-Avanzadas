# 3. Gestión de sesiones y recuperación con Valkey

## 3.1 Descripción del servicio de Sesión

El servicio de Sesión administra el estado temporal de los anotadores mediante Valkey 8.0. Sus responsabilidades incluyen la apertura y recuperación de sesiones, la adquisición y renovación de locks, el almacenamiento de borradores y el registro de actividad de los usuarios.

La plataforma separa esta información de los datos operacionales almacenados en MongoDB. El Gateway coordina las operaciones mediante las interfaces HTTP de los servicios, evitando que los clientes accedan directamente a Valkey.

La instancia de Valkey utilizada para sesiones está configurada con persistencia AOF:

```text
appendonly yes
appendfsync everysec
```

Además, utiliza el volumen persistente `valkey_sesion`, montado en `/data` dentro del contenedor.

## 3.2 Control de concurrencia mediante locks

Cuando un anotador solicita una tarea, el Gateway genera un token UUID. Registro almacena ese token dentro de la asignación operacional en MongoDB y Sesión lo utiliza como valor del lock correspondiente en Valkey.

Los locks tienen una duración inicial de 120 segundos. Para adquirirlos se utiliza una operación equivalente a `SET ... NX PX`, que impide sobrescribir un lock existente.

La renovación y liberación de los locks se realizan mediante scripts Lua que comprueban que el token recibido coincida con el almacenado. Esto evita que un anotador renueve o libere el lock de otro.

Los latidos enviados a `POST /sesiones/latido` permiten renovar el lock y actualizar la última actividad del anotador.

## 3.3 Almacenamiento y recuperación de borradores

Durante la edición de una tarea, el anotador puede guardar avances mediante `PUT /tareas/{id}/borrador`. Los borradores se almacenan temporalmente en Valkey y se asocian con la tarea y la sesión autorizada.

Si el usuario interrumpe su trabajo sin enviar la anotación, puede volver a solicitar su sesión mediante `POST /sesiones/recuperar`.

La respuesta de recuperación contiene la información de la tarea, el token y, cuando existe, el contenido del borrador con su fecha de guardado.

También se implementó una corrección para el manejo de borradores huérfanos: cuando una tarea se reasigna, el borrador de un anotador anterior no se entrega al nuevo propietario. El sistema conserva el contenido antiguo bajo una clave de archivo temporal y permite que el nuevo anotador empiece con su propio borrador.

## 3.4 Prueba P3: interrupción y recuperación

Para verificar la recuperación se utilizó el script `pruebas/b52_interrupcion_gateway.py`.

La prueba realizó una asignación mediante el Gateway, guardó un borrador, simuló una interrupción de edición y recuperó inmediatamente la sesión.

| Indicador | Resultado |
|---|---|
| Asignación de tarea | Correcta |
| Guardado de borrador | HTTP 200 |
| Recuperación de sesión | HTTP 200 |
| Tiempo medido de recuperación | **0.047 segundos** |
| Contenido del borrador | Recuperado |
| Liberación final | HTTP 200 |

El tiempo observado estuvo por debajo de los cinco segundos establecidos como referencia para la recuperación.

La prueba mide el intervalo entre el inicio de la operación de guardado y la respuesta de recuperación. El borrador recuperado también incluye una marca temporal `guardado_en`, que permite conocer cuándo se almacenó.

**Evidencia:** `evidence/B5_2_interrupcion.txt`.

## 3.5 Persistencia de borradores después de reiniciar Valkey

La prueba `pruebas/b52_persistencia_gateway.py` verificó que un borrador confirmado sobreviva a un reinicio controlado del servicio.

La secuencia fue: asignar una tarea, guardar el borrador, permitir la sincronización AOF, ejecutar `docker compose restart valkey` y recuperar la sesión.

Los resultados confirmaron que el token y el contenido del borrador permanecieron intactos después del reinicio. Al finalizar, la tarea se liberó correctamente.

**Evidencia:** `evidence/B5_2_persistencia.txt`.

## 3.6 Vencimiento y reasignación de tareas

La prueba `pruebas/b52_vencimiento_gateway.py` comprobó el comportamiento ante el vencimiento del lock.

Inicialmente, el anotador A recibió una tarea. Mientras su asignación estaba vigente, el anotador B no pudo obtenerla.

Después de aproximadamente 123 segundos, B recibió la misma tarea con un token diferente. El intento de A de guardar un borrador con su token anterior fue rechazado con HTTP 409, mientras que B pudo guardar correctamente.

La prueba demostró exclusividad en el escenario de vencimiento normal, aunque no constituye una garantía universal para todos los casos de concurrencia.

Se detectó una sesión residual de A en Valkey después de la reasignación. Esta sesión se cerró mediante la API interna de Sesión y se verificó que ambas sesiones de prueba desaparecieran.

**Evidencia:** `evidence/B5_2_vencimiento.txt`.

## 3.7 Prueba P8: recuperación ante fallos y RTO/RPO

Para evaluar la recuperación ante fallos se utilizó `pruebas/b53_fallo_valkey.py`.

La prueba creó una sesión activa, guardó un borrador y generó latidos periódicos mientras se reiniciaba la instancia de Valkey.

Se observaron respuestas HTTP 200 antes del reinicio, HTTP 503 durante la interrupción y HTTP 200 tras la recuperación.

| Indicador | Resultado observado |
|---|---|
| RTO | **1.016 segundos** |
| RPO | Sin pérdida del borrador confirmado |
| Antigüedad del borrador recuperado | **2.024 segundos** |
| Latidos registrados | 7 |
| Sesión y token | Recuperados correctamente |
| Liberación final | HTTP 200 |

El RTO observado corresponde al intervalo desde el inicio del reinicio hasta la recuperación exitosa de la sesión y su borrador.

El RPO observado indica que no se perdió el borrador confirmado durante esta ejecución. No demuestra pérdida cero en todos los escenarios: la configuración AOF `everysec` mantiene una ventana potencial de pérdida cercana a un segundo.

**Evidencia:** `evidence/B5_3_P8.txt`.

## 3.8 Limitaciones y observaciones de integración

Las pruebas identificaron dos situaciones que requieren seguimiento:

1. **Sesiones residuales:** cuando vence un lock, la sesión anterior puede permanecer almacenada en Valkey, aunque su token ya no permita editar la tarea. En las pruebas se realizó una limpieza controlada mediante la API interna.

2. **Desfase entre MongoDB y Valkey:** los latidos renuevan el lock en Valkey, pero actualmente no prolongan `asignacion.vence_en` en MongoDB. Durante una sesión larga, esta diferencia podría ocasionar que Registro considere vencida una asignación cuyo lock continúa activo. Es un aspecto pendiente de coordinación con el responsable del servicio Registro.

Estas observaciones deben distinguirse de los resultados funcionales ya aprobados: las pruebas de recuperación y persistencia fueron exitosas, pero no eliminan las limitaciones de integración descritas.

## 3.9 Conclusión

Valkey permite mantener sesiones, borradores y locks temporales con operaciones eficientes y persistencia AOF.

Las pruebas P3 y P8 demostraron recuperación de borradores, conservación de datos tras reinicios y rechazo de tokens vencidos. El reinicio durante actividad produjo una interrupción observable, seguida de recuperación con un RTO de 1.016 segundos y sin pérdida del borrador confirmado en la ejecución realizada.

La solución cumple los escenarios funcionales probados y deja identificadas las mejoras necesarias para reforzar la limpieza automática de sesiones y la consistencia de vencimientos entre MongoDB y Valkey.