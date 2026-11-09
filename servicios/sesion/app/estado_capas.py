
"""Mensajes de salud de las capas en Valkey (B2.7)."""

import json

from app.comun import ahora_iso
from app.locks import cliente

TTL_SALUD = 15


def publicar(servicio: str, estado: str, detalle) -> dict:
    """Guarda el ultimo estado de una capa durante 15 segundos."""
    instante = ahora_iso()
    llave = f"salud:{servicio}"

    datos = {
        "estado": estado,
        "detalle": json.dumps(detalle, ensure_ascii=False),
        "ts": instante,
    }

    # HSET y EXPIRE juntos en una transaccion de Valkey.
    with cliente.pipeline(transaction=True) as operaciones:
        operaciones.hset(llave, mapping=datos)
        operaciones.expire(llave, TTL_SALUD)
        operaciones.execute()

    return {
        "servicio": servicio,
        "estado": estado,
        "detalle": detalle,
        "ts": instante,
    }


def consultar() -> dict:
    """Consulta los mensajes vigentes de todas las capas."""
    capas = {}

    for llave in cliente.scan_iter(match="salud:*"):
        datos = cliente.hgetall(llave)

        if not datos:
            continue

        servicio = llave.removeprefix("salud:")

        capas[servicio] = {
            "estado": datos["estado"],
            "detalle": json.loads(datos["detalle"]),
            "ts": datos["ts"],
        }

    return {"capas": capas}
