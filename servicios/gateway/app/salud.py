
"""B3.8: salud agregada de los servicios internos."""

import asyncio

import httpx

from app.cliente_interno import SERVICIOS
from app.comun import ahora_iso

TIMEOUT = 1.0


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

        if all(e == "operativo" for e in estados):
            general = "operativo"
        elif all(e == "caido" for e in estados):
            general = "caido"
        else:
            general = "degradado"

        return {
            "servicio": "gateway",
            "estado": general,
            "capas": capas,
            "ts": ahora_iso(),
        }

    try:
        if cliente is not None:
            return await consultar(cliente)

        async with httpx.AsyncClient() as http:
            return await consultar(http)

    except Exception as exc:
        return {
            "servicio": "gateway",
            "estado": "degradado",
            "capas": {},
            "detalle": type(exc).__name__,
            "ts": ahora_iso(),
        }
