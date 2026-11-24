
"""Pruebas de sesiones activas en Valkey - B2.3.

Llama directamente a app.sesiones (sin HTTP) y revisa las llaves en Valkey: el hash
`sesion:{anotador}`, el sorted set `editando:{proyecto}`, el cierre con token correcto e
incorrecto y la limpieza de anotadores inactivos. Imprime B2.3_VALKEY_OK si todo pasa.
"""

from app import sesiones

# Conexión directa a Valkey para revisar las llaves.
cliente = sesiones.cliente

# Datos fijos de la prueba y las llaves que se van a revisar.
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
    # Limpieza: borra la sesión y las entradas de prueba del conjunto.
    cliente.delete(llave_sesion)
    cliente.zrem(llave_editando, anotador, "prueba-b23-vencido")
