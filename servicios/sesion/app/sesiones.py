"""Sesiones activas en Valkey (B2.3).

Llaves usadas:
- `sesion:{anotador_id}` (hash): tarea, proyecto, etapa, token y última actividad (ms)
  de la sesión abierta de cada anotador.
- `editando:{proyecto_id}` (sorted set): anotadores activos en un proyecto con el
  instante de su última actividad como puntaje; sirve para saber quién trabaja ahora.
"""

import time

from app import locks

from app.locks import cliente

# Una entrada sin actividad durante 120 s deja de figurar como activa.
VENTANA_ACTIVA_MS = 120_000


def ahora_ms() -> int:
    """Instante actual en milisegundos desde epoch."""
    return int(time.time() * 1000)


def limpiar_editando(proyecto_id: str) -> int:
    """Elimina del conjunto los anotadores con actividad vencida."""
    # Todo lo que tenga puntaje (última actividad) anterior a este límite está vencido.
    limite = ahora_ms() - VENTANA_ACTIVA_MS
    return cliente.zremrangebyscore(
        f"editando:{proyecto_id}",
        "-inf",
        limite,
    )


def abrir(
    anotador_id: str,
    tarea_id: str,
    proyecto_id: str,
    etapa: str,
    token: str,
) -> dict:
    """Guarda la sesión y registra al anotador como activo."""
    instante = ahora_ms()
    llave_sesion = f"sesion:{anotador_id}"
    # Sesión anterior del anotador (si tenía una) para saber de qué proyecto sacarlo.
    anterior = cliente.hgetall(llave_sesion)

    # Pipeline: todas las órdenes viajan juntas a Valkey en un solo viaje de red.
    with cliente.pipeline() as operaciones:
        # Si cambió de proyecto, deja de figurar como activo en el proyecto anterior.
        if anterior.get("proyecto") and anterior["proyecto"] != proyecto_id:
            operaciones.zrem(
                f"editando:{anterior['proyecto']}",
                anotador_id,
            )

        # Sobrescribe la sesión con la tarea y el token nuevos.
        operaciones.hset(
            llave_sesion,
            mapping={
                "tarea": tarea_id,
                "proyecto": proyecto_id,
                "etapa": etapa,
                "token": token,
                "actualizada_en": instante,
            },
        )

        # Marca al anotador como activo en el proyecto con el instante actual.
        operaciones.zadd(
            f"editando:{proyecto_id}",
            {anotador_id: instante},
        )

        operaciones.execute()

    # Aprovecha para sacar a los anotadores inactivos del proyecto.
    limpiar_editando(proyecto_id)

    return {
        "anotador_id": anotador_id,
        "tarea_id": tarea_id,
        "proyecto_id": proyecto_id,
        "etapa": etapa,
        "token": token,
        "actualizada_en": instante,
    }


def consultar(anotador_id: str) -> dict | None:
    """Devuelve la sesión almacenada, si existe."""
    datos = cliente.hgetall(f"sesion:{anotador_id}")
    # HGETALL devuelve {} cuando la llave no existe; se convierte en None.
    return datos or None


def cerrar(anotador_id: str, token: str) -> str:
    """Cierra una sesión solo cuando coincide el token.

    Devuelve "no_existe", "conflicto" (token distinto) u "ok".
    """
    datos = consultar(anotador_id)

    if datos is None:
        return "no_existe"

    if datos.get("token") != token:
        return "conflicto"

    # Borra la sesión y saca al anotador de los activos del proyecto.
    with cliente.pipeline() as operaciones:
        operaciones.delete(f"sesion:{anotador_id}")
        operaciones.zrem(
            f"editando:{datos['proyecto']}",
            anotador_id,
        )
        operaciones.execute()

    return "ok"


def latido(anotador_id: str, token: str) -> dict | str:
    """Renueva el lock y actualiza la actividad del anotador.

    Devuelve "no_existe", "conflicto" (token distinto o lock perdido) o un dict con
    la sesión actualizada.
    """

    datos = consultar(anotador_id)

    if datos is None:
        return "no_existe"

    if datos.get("token") != token:
        return "conflicto"

    tarea_id = datos["tarea"]
    proyecto_id = datos["proyecto"]

    # Renovar el lock solo si coincide su token.
    if not locks.renovar(tarea_id, token):
        return "conflicto"

    instante = ahora_ms()

    # Actualiza la última actividad en la sesión y en el conjunto de activos.
    with cliente.pipeline() as operaciones:
        operaciones.hset(
            f"sesion:{anotador_id}",
            "actualizada_en",
            instante,
        )
        operaciones.zadd(
            f"editando:{proyecto_id}",
            {anotador_id: instante},
        )
        operaciones.execute()

    limpiar_editando(proyecto_id)

    return {
        "anotador_id": anotador_id,
        "tarea_id": tarea_id,
        "proyecto_id": proyecto_id,
        "actualizada_en": instante,
        "lock_renovado": True,
    }


# Sesión cuyo lock ya no es suyo (venció o la tarea la tomó otro anotador): se borra para que
# `recuperar` no devuelva una tarea ajena. Todo en Lua para no borrar una sesión que se acaba de
# reabrir con otro token entre la lectura y el borrado.
# KEYS = [sesion, lock, editando]; ARGV = [token de la sesión leída, anotador_id].
LUA_DESCARTAR_VENCIDA = """
if redis.call('HGET', KEYS[1], 'token') ~= ARGV[1] then
    return 0
end
if redis.call('GET', KEYS[2]) == ARGV[1] then
    return 0
end
redis.call('DEL', KEYS[1])
redis.call('ZREM', KEYS[3], ARGV[2])
return 1
"""


def descartar_si_vencida(anotador_id: str, datos: dict) -> bool:
    """True si la sesión se borró porque su token ya no tiene el lock de la tarea."""
    return bool(cliente.eval(
        LUA_DESCARTAR_VENCIDA,
        3,
        f"sesion:{anotador_id}",
        f"lock:tarea:{datos['tarea']}",
        f"editando:{datos['proyecto']}",
        datos["token"],
        anotador_id,
    ))
