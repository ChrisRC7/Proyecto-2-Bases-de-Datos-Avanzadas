"""Almacenamiento de borradores en Valkey (B2.4).

El borrador de una tarea vive en el hash `borrador:{tarea_id}` con los campos
`contenido` (JSON), `anotador`, `token` y `guardado_en` (epoch en ms). Solo quien
tiene el lock vigente de la tarea puede guardarlo, y solo su dueño puede borrarlo.
"""

import json
import time
from uuid import uuid4

from app import locks

# Se reutiliza la misma conexión a Valkey que usan los locks.
cliente = locks.cliente


def guardar(
    tarea_id: str,
    anotador_id: str,
    token: str,
    contenido,
) -> dict | None:
    """Guarda el borrador únicamente si el token posee el lock.

    Devuelve:
    - None si el token ya no tiene el lock (expiró o es de otra asignación).
    - "otro_anotador" si ya hay un borrador de otro anotador para esa tarea.
    - Un dict con tarea, anotador e instante de guardado si se guardó.
    Guardar también renueva el lock (cuenta como actividad).
    """

    # Verifica que el token todavía posea el lock.
    if not locks.renovar(tarea_id, token):
        return None

    # Evita sobrescribir un borrador perteneciente a otro anotador.
    existente = cliente.hgetall(f"borrador:{tarea_id}")

    if existente and existente.get("anotador") != anotador_id:
        return "otro_anotador"


    # Instante del guardado en milisegundos desde epoch.
    instante = int(time.time() * 1000)


    # El contenido se serializa a JSON porque los campos de un hash son strings.
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
    """Recupera el borrador almacenado, si existe.

    Devuelve None si no hay borrador; si existe, devuelve el contenido ya
    deserializado junto con anotador, token e instante de guardado.
    """

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



# Borra el hash del borrador solo si token y anotador coinciden (atómico en Valkey).
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

# Script que se ejecuta al asignar una tarea. KEYS = [lock, borrador, archivo];
# ARGV = [anotador nuevo, token nuevo, TTL del archivo]. Códigos de retorno:
#   0 -> conflicto (el lock no es de este token o el archivo ya existe)
#   1 -> no había borrador
#   2 -> el borrador era del mismo anotador: se conserva y se le pone el token nuevo
#   3 -> el borrador era de otro anotador: se mueve a un archivo con expiración
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
    """Reconcilia el borrador solo con un lock valido.

    Devuelve ("conflicto", None) si el token no tiene el lock; ("ok", None) si no hay
    borrador recuperable para este anotador; o ("ok", borrador) si el anotador recupera
    su propio borrador anterior.
    """

    # Nombre único para archivar el borrador de otro anotador (no se sobrescriben).
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
