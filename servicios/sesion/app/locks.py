
"""Locks de tareas en Valkey (B2.2)."""

import os

import valkey

VALKEY_URL = os.getenv("VALKEY_URL", "redis://valkey:6379/0")
DURACION_LOCK_MS = 120_000

cliente = valkey.Valkey.from_url(VALKEY_URL, decode_responses=True)

LUA_RENOVAR = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
else
    return 0
end
"""

LUA_SOLTAR = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


def adquirir(tarea_id: str, token: str) -> bool:
    return bool(
        cliente.set(
            f"lock:tarea:{tarea_id}",
            token,
            nx=True,
            px=DURACION_LOCK_MS,
        )
    )


def renovar(tarea_id: str, token: str) -> bool:
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
    return bool(
        cliente.eval(
            LUA_SOLTAR,
            1,
            f"lock:tarea:{tarea_id}",
            token,
        )
    )
