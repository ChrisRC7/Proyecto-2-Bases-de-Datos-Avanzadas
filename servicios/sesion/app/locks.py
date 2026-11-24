"""Locks de tareas en Valkey (B2.2).

Cada tarea asignada tiene un lock `lock:tarea:{tarea_id}` cuyo valor es el token de la
asignación. El lock expira solo (PX) si nadie lo renueva, así una tarea abandonada no
queda bloqueada para siempre. Renovar y soltar usan scripts Lua para comprobar el token
y modificar la llave en un solo paso atómico.
"""

import os

import valkey

# Dirección de Valkey (la define docker-compose).
VALKEY_URL = os.getenv("VALKEY_URL", "redis://valkey:6379/0")
# Duración del lock: 2 minutos; cada latido o guardado lo vuelve a extender.
DURACION_LOCK_MS = 120_000

# Cliente compartido por todos los módulos del servicio (devuelve str en lugar de bytes).
cliente = valkey.Valkey.from_url(VALKEY_URL, decode_responses=True)

# Extiende la expiración solo si el lock sigue teniendo el token de quien lo pide.
LUA_RENOVAR = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
else
    return 0
end
"""

# Borra el lock solo si sigue teniendo el token de quien lo pide.
LUA_SOLTAR = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


def adquirir(tarea_id: str, token: str) -> bool:
    """Toma el lock de la tarea con SET NX PX.

    Devuelve True si el lock estaba libre y ahora pertenece a `token`; False si otra
    asignación ya lo tiene.
    """
    return bool(
        cliente.set(
            f"lock:tarea:{tarea_id}",
            token,
            nx=True,
            px=DURACION_LOCK_MS,
        )
    )


def renovar(tarea_id: str, token: str) -> bool:
    """Extiende el lock otros DURACION_LOCK_MS si `token` todavía es su dueño.

    Devuelve False si el lock expiró o pertenece a otro token.
    """
    return bool(
        cliente.eval(
            LUA_RENOVAR,
            1,
            f"lock:tarea:{tarea_id}",
            token,
            DURACION_LOCK_MS,
        )
    )


def soltar(tarea_id: str, token: str) -> bool:
    """Libera el lock si `token` es su dueño. Devuelve True si se borró."""
    return bool(
        cliente.eval(
            LUA_SOLTAR,
            1,
            f"lock:tarea:{tarea_id}",
            token,
        )
    )
