
"""Almacenamiento de borradores en Valkey (B2.4)."""

import json
import time
from uuid import uuid4

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


# Reasignacion segura de borradores antiguos.
# El archivo temporal se conserva durante 7 dias.
ARCHIVO_TTL_SEGUNDOS = 7 * 24 * 60 * 60

LUA_PREPARAR_ASIGNACION = """
-- Solo el propietario del lock actual puede reasignar.
if redis.call('GET', KEYS[1]) ~= ARGV[2] then
    return 0
end

-- No existe borrador anterior.
if redis.call('EXISTS', KEYS[2]) == 0 then
    return 1
end

local propietario = redis.call('HGET', KEYS[2], 'anotador')

-- Mismo anotador: conservar contenido y actualizar token.
if propietario == ARGV[1] then
    redis.call('HSET', KEYS[2], 'token', ARGV[2])
    return 2
end

-- Otro anotador: archivar sin exponer su contenido.
if redis.call('EXISTS', KEYS[3]) ~= 0 then
    return 0
end

redis.call('RENAME', KEYS[2], KEYS[3])
redis.call('EXPIRE', KEYS[3], tonumber(ARGV[3]))

return 3
"""


def preparar_para_asignacion(
    tarea_id: str,
    anotador_id: str,
    token: str,
) -> tuple[str, dict | None]:
    """Reconcilia el borrador solo con un lock valido."""

    archivo = (
        f"borrador:archivado:{tarea_id}:{uuid4()}"
    )

    resultado = int(cliente.eval(
        LUA_PREPARAR_ASIGNACION,
        3,
        f"lock:tarea:{tarea_id}",
        f"borrador:{tarea_id}",
        archivo,
        anotador_id,
        token,
        ARCHIVO_TTL_SEGUNDOS,
    ))

    if resultado == 0:
        return "conflicto", None

    if resultado == 3:
        # El borrador anterior queda archivado.
        return "ok", None

    # Sin borrador o mismo anotador con token actualizado.
    return "ok", recuperar(tarea_id)
