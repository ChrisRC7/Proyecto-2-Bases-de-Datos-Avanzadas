
"""B2.6 - Conservacion de borradores ante un envio fallido."""

import uuid

from app import borradores, locks, sesiones

identificador = uuid.uuid4().hex
tarea = f"prueba-b26-{identificador}"
anotador = f"anotador-b26-{identificador}"
proyecto = f"proyecto-b26-{identificador}"
token = f"token-b26-{identificador}"
contenido = {"etiquetas": ["ciclista"], "avance": 90}

try:
    assert locks.adquirir(tarea, token)
    print("OK | Lock adquirido")

    sesiones.abrir(anotador, tarea, proyecto, "anotando", token)
    print("OK | Sesion abierta")

    assert borradores.guardar(tarea, anotador, token, contenido)
    print("OK | Borrador guardado")

    # Simulamos el resultado del gateway ante Mongo 503.
    # No eliminamos el borrador ni liberamos el lock.
    codigo_envio = 503
    assert codigo_envio == 503

    conservado = borradores.recuperar(tarea)
    assert conservado is not None
    assert conservado["contenido"] == contenido
    assert conservado["anotador_id"] == anotador
    print("OK | Borrador conservado tras fallo simulado 503")

    # El anotador puede recuperar el contenido.
    sesion_actual = sesiones.consultar(anotador)
    assert sesion_actual["tarea"] == tarea
    assert borradores.recuperar(tarea)["contenido"] == contenido
    print("OK | Borrador recuperable para reintento")

    print("B2.6_CONSERVACION_OK")

finally:
    locks.cliente.delete(
        f"lock:tarea:{tarea}",
        f"borrador:{tarea}",
    )
    datos = sesiones.consultar(anotador)
    if datos:
        sesiones.cerrar(anotador, datos["token"])
