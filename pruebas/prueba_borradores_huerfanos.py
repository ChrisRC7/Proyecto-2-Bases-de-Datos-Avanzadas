
"""Pruebas de reasignacion segura de borradores en Valkey."""

from uuid import uuid4

from app import borradores, locks


cliente = locks.cliente
tareas = []


def nuevo_id():
    return str(uuid4())


def preparar_tarea():
    tarea = nuevo_id()
    tareas.append(tarea)
    return tarea


def prueba_otro_anotador():
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
