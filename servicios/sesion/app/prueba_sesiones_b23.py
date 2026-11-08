
"""Pruebas de sesiones activas en Valkey - B2.3."""

from app import sesiones

cliente = sesiones.cliente

anotador = "prueba-b23-anotador"
proyecto = "prueba-b23-proyecto"
tarea = "prueba-b23-tarea"
token = "token-b23"

llave_sesion = f"sesion:{anotador}"
llave_editando = f"editando:{proyecto}"

# Limpiar posibles datos de pruebas anteriores.
cliente.delete(llave_sesion)
cliente.zrem(llave_editando, anotador)

try:
    # 1. Abrir sesión.
    resultado = sesiones.abrir(
        anotador, tarea, proyecto, "anotando", token
    )
    assert resultado["anotador_id"] == anotador
    print("OK | Abrir sesion")

    # 2. Verificar hash.
    datos = cliente.hgetall(llave_sesion)
    assert datos["tarea"] == tarea
    assert datos["proyecto"] == proyecto
    assert datos["etapa"] == "anotando"
    assert datos["token"] == token
    assert "actualizada_en" in datos
    print("OK | HSET sesion")

    # 3. Verificar sorted set.
    assert cliente.zscore(llave_editando, anotador) is not None
    print("OK | ZADD editando")

    # 4. Rechazar cierre con token incorrecto.
    assert sesiones.cerrar(anotador, "token-incorrecto") == "conflicto"
    assert cliente.exists(llave_sesion) == 1
    print("OK | Token incorrecto no cierra sesion")

    # 5. Cerrar con token correcto.
    assert sesiones.cerrar(anotador, token) == "ok"
    assert cliente.exists(llave_sesion) == 0
    assert cliente.zscore(llave_editando, anotador) is None
    print("OK | Cerrar sesion y limpiar editando")

    # 6. Comprobar limpieza de entradas vencidas.
    cliente.zadd(
        llave_editando,
        {"prueba-b23-vencido": sesiones.ahora_ms() - 180_000},
    )
    eliminados = sesiones.limpiar_editando(proyecto)
    assert eliminados >= 1
    assert cliente.zscore(llave_editando, "prueba-b23-vencido") is None
    print("OK | Limpiar entradas vencidas")

    print("B2.3_VALKEY_OK")

finally:
    cliente.delete(llave_sesion)
    cliente.zrem(llave_editando, anotador, "prueba-b23-vencido")
