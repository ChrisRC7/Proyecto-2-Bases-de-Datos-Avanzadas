
"""Pruebas de reasignacion segura de borradores en Valkey.

Comprueba preparar_para_asignacion: si la tarea pasa a otro anotador, el borrador anterior se
archiva (con TTL) y no se le muestra; si vuelve al mismo anotador, lo recupera con el token
nuevo; y con un token sin lock no se toca nada. Corre dentro del contenedor de sesión.
"""

from uuid import uuid4

from app import borradores, locks


# Conexión a Valkey y lista de tareas creadas (para limpiarlas al final).
cliente = locks.cliente
tareas = []


def nuevo_id():
    """UUID aleatorio como texto."""
    return str(uuid4())


def preparar_tarea():
    """Id de tarea nuevo, registrado para la limpieza final."""
    tarea = nuevo_id()
    tareas.append(tarea)
    return tarea


def prueba_otro_anotador():
    """El borrador de A se archiva cuando la tarea se asigna a B, y B empieza sin borrador."""
    tarea = preparar_tarea()
    anterior, nuevo = nuevo_id(), nuevo_id()
    token_anterior, token_nuevo = nuevo_id(), nuevo_id()

    assert locks.adquirir(tarea, token_anterior)
    assert borradores.guardar(
        tarea, anterior, token_anterior, {"avance": 70}
    )

    assert locks.soltar(tarea, token_anterior)
    assert locks.adquirir(tarea, token_nuevo)

    estado, recuperado = borradores.preparar_para_asignacion(
        tarea, nuevo, token_nuevo
    )

    assert estado == "ok"
    assert recuperado is None
    assert borradores.recuperar(tarea) is None

    archivos = list(cliente.scan_iter(
        match=f"borrador:archivado:{tarea}:*"
    ))
    assert len(archivos) == 1
    assert cliente.ttl(archivos[0]) > 0

    assert borradores.guardar(
        tarea, nuevo, token_nuevo, {"avance": 10}
    )
    assert borradores.eliminar(tarea, nuevo, token_nuevo)

    print("OK | Otro anotador puede trabajar")
    print("OK | Borrador anterior archivado")
    print("OK | Limpieza con token nuevo")


def prueba_mismo_anotador():
    """Si la tarea vuelve al mismo anotador, recupera su borrador con el token nuevo."""
    tarea = preparar_tarea()
    anotador = nuevo_id()
    token_anterior, token_nuevo = nuevo_id(), nuevo_id()

    assert locks.adquirir(tarea, token_anterior)
    assert borradores.guardar(
        tarea, anotador, token_anterior, {"avance": 80}
    )

    assert locks.soltar(tarea, token_anterior)
    assert locks.adquirir(tarea, token_nuevo)

    estado, recuperado = borradores.preparar_para_asignacion(
        tarea, anotador, token_nuevo
    )

    assert estado == "ok"
    assert recuperado["contenido"] == {"avance": 80}
    assert recuperado["token"] == token_nuevo
    assert borradores.eliminar(tarea, anotador, token_nuevo)

    print("OK | Mismo anotador conserva su borrador")
    print("OK | Token actualizado permite limpieza")


def prueba_token_invalido():
    """Un token sin el lock no puede modificar ni archivar el borrador."""
    tarea = preparar_tarea()
    anotador = nuevo_id()
    token_valido, token_falso = nuevo_id(), nuevo_id()

    assert locks.adquirir(tarea, token_valido)
    assert borradores.guardar(
        tarea, anotador, token_valido, {"avance": 50}
    )

    estado, recuperado = borradores.preparar_para_asignacion(
        tarea, anotador, token_falso
    )

    assert estado == "conflicto"
    assert recuperado is None
    assert borradores.recuperar(tarea)["token"] == token_valido

    print("OK | Token invalido no modifica el borrador")


def limpiar():
    """Borra locks, borradores y archivos de todas las tareas de la prueba."""
    for tarea in tareas:
        claves = [
            f"lock:tarea:{tarea}",
            f"borrador:{tarea}",
        ]
        claves.extend(cliente.scan_iter(
            match=f"borrador:archivado:{tarea}:*"
        ))
        cliente.delete(*claves)


if __name__ == "__main__":
    try:
        prueba_otro_anotador()
        prueba_mismo_anotador()
        prueba_token_invalido()
        print("REASIGNACION_BORRADORES_OK")
    finally:
        limpiar()
        print("OK | Claves temporales eliminadas")
