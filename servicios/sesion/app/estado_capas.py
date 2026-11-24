"""Mensajes de salud de las capas en Valkey (B2.7).

Cada capa (registro, historial, analítica, ...) publica su último estado en la llave
`salud:{servicio}`. La llave expira a los 15 s: si una capa deja de publicar, su
mensaje desaparece solo y se considera sin información.
"""

import json

from app.comun import ahora_iso
from app.locks import cliente

# Segundos que dura vigente un mensaje de salud.
TTL_SALUD = 15


def publicar(servicio: str, estado: str, detalle) -> dict:
    """Guarda el ultimo estado de una capa durante 15 segundos."""
    instante = ahora_iso()
    llave = f"salud:{servicio}"

    # El detalle se guarda como texto JSON porque un hash de Valkey solo guarda strings.
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

    # Se devuelve lo publicado con el detalle ya como objeto.
    return {
        "servicio": servicio,
        "estado": estado,
        "detalle": detalle,
        "ts": instante,
    }


def consultar() -> dict:
    """Consulta los mensajes vigentes de todas las capas."""
    capas = {}

    # SCAN recorre las llaves salud:* sin bloquear Valkey (a diferencia de KEYS).
    for llave in cliente.scan_iter(match="salud:*"):
        datos = cliente.hgetall(llave)

        # La llave pudo expirar entre el SCAN y el HGETALL.
        if not datos:
            continue

        servicio = llave.removeprefix("salud:")

        capas[servicio] = {
            "estado": datos["estado"],
            "detalle": json.loads(datos["detalle"]),
            "ts": datos["ts"],
        }

    return {"capas": capas}
