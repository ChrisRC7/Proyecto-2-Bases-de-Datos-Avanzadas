
"""B3.8: salud agregada de los servicios internos."""

import asyncio

from app.cliente_interno import SERVICIOS, cliente_compartido
from app.comun import ahora_iso

TIMEOUT = 1.0

# Sin registro (MongoDB) o sin sesión (Valkey) no se puede asignar ni enviar: el sistema está caído
# aunque historial y analítica respondan (regla de endpoints/gateway.md).
CRITICAS = ("registro", "sesion")


async def revisar_servicio(cliente, nombre, url):
    try:
        respuesta = await cliente.get(
            url.rstrip("/") + "/salud",
            timeout=TIMEOUT,
        )

        if respuesta.status_code != 200:
            return {
                "estado": "caido",
                "detalle": f"HTTP {respuesta.status_code}",
            }

        datos = respuesta.json()

        if not isinstance(datos, dict):
            return {
                "estado": "degradado",
                "detalle": "Respuesta no valida",
            }

        estado = str(datos.get("estado", "")).lower()

        if estado in ("operativo", "ok", "healthy"):
            estado = "operativo"
        elif estado in ("caido", "caído"):
            estado = "caido"
        else:
            estado = "degradado"

        return {
            "estado": estado,
            "detalle": datos,
        }

    except Exception as exc:
        return {
            "estado": "caido",
            "detalle": type(exc).__name__,
        }


async def consultar_salud(cliente=None):
    async def consultar(http):
        nombres = list(SERVICIOS)

        resultados = await asyncio.gather(
            *(
                revisar_servicio(
                    http, nombre, SERVICIOS[nombre]
                )
                for nombre in nombres
            )
        )

        capas = dict(zip(nombres, resultados))
        estados = [capa["estado"] for capa in capas.values()]

        if any(capas[n]["estado"] == "caido" for n in CRITICAS if n in capas):
            general = "caido"
        elif all(e == "operativo" for e in estados):
            general = "operativo"
        else:
            general = "degradado"

        return {
            "servicio": "gateway",
            "estado": general,
            "capas": capas,
            "ts": ahora_iso(),
        }

    try:
        return await consultar(cliente or cliente_compartido())

    except Exception as exc:
        return {
            "servicio": "gateway",
            "estado": "degradado",
            "capas": {},
            "detalle": type(exc).__name__,
            "ts": ahora_iso(),
        }
