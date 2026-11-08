
"""Sesiones activas en Valkey (B2.3)."""

import time

from app.locks import cliente

# Una entrada sin actividad durante 120 s deja de figurar como activa.
VENTANA_ACTIVA_MS = 120_000


def ahora_ms() -> int:
    return int(time.time() * 1000)


def limpiar_editando(proyecto_id: str) -> int:
    """Elimina del conjunto los anotadores con actividad vencida."""
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
    anterior = cliente.hgetall(llave_sesion)

    with cliente.pipeline() as operaciones:
        if anterior.get("proyecto") and anterior["proyecto"] != proyecto_id:
            operaciones.zrem(
                f"editando:{anterior['proyecto']}",
                anotador_id,
            )

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
        "etapa": etapa,
        "token": token,
        "actualizada_en": instante,
    }


def consultar(anotador_id: str) -> dict | None:
    """Devuelve la sesión almacenada, si existe."""
    datos = cliente.hgetall(f"sesion:{anotador_id}")
    return datos or None


def cerrar(anotador_id: str, token: str) -> str:
    """Cierra una sesión solo cuando coincide el token."""
    datos = consultar(anotador_id)

    if datos is None:
        return "no_existe"

    if datos.get("token") != token:
        return "conflicto"

    with cliente.pipeline() as operaciones:
        operaciones.delete(f"sesion:{anotador_id}")
        operaciones.zrem(
            f"editando:{datos['proyecto']}",
            anotador_id,
        )
        operaciones.execute()

    return "ok"
