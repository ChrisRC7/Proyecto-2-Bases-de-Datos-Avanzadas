
"""Almacenamiento de borradores en Valkey (B2.4)."""

import json
import time

from app import locks

cliente = locks.cliente


def guardar(
    tarea_id: str,
    anotador_id: str,
    token: str,
    contenido,
) -> dict | None:
    """Guarda el borrador únicamente si el token posee el lock."""

    # Verifica que el token todavía posea el lock.
    if not locks.renovar(tarea_id, token):
        return None

    # Evita sobrescribir un borrador perteneciente a otro anotador.
    existente = cliente.hgetall(f"borrador:{tarea_id}")

    if existente and existente.get("anotador") != anotador_id:
        return "otro_anotador"


    instante = int(time.time() * 1000)


    datos = {
        "contenido": json.dumps(contenido, ensure_ascii=False),
        "anotador": anotador_id,
        "token": token,
        "guardado_en": instante,
    }

    cliente.hset(f"borrador:{tarea_id}", mapping=datos)

    return {
        "tarea_id": tarea_id,
        "anotador_id": anotador_id,
        "guardado_en": instante,
    }


def recuperar(tarea_id: str) -> dict | None:
    """Recupera el borrador almacenado, si existe."""

    datos = cliente.hgetall(f"borrador:{tarea_id}")

    if not datos:
        return None

    return {
        "tarea_id": tarea_id,
        "contenido": json.loads(datos["contenido"]),
        "anotador_id": datos["anotador"],
        "token": datos["token"],
        "guardado_en": int(datos["guardado_en"]),
    }



LUA_ELIMINAR = """
if redis.call('HGET', KEYS[1], 'token') == ARGV[1]
and redis.call('HGET', KEYS[1], 'anotador') == ARGV[2]
then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


def eliminar(tarea_id: str, anotador_id: str, token: str) -> bool:
    """Elimina el borrador solo si pertenece al anotador y token."""
    return bool(cliente.eval(
        LUA_ELIMINAR,
        1,
        f"borrador:{tarea_id}",
        token,
        anotador_id,
    ))
