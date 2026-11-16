
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


# Las capas publican cada 5 s su estado en Valkey (sesion:/estado-capas, llave `salud:{servicio}`
# con TTL 15 s). El gateway lee esos mensajes y además consulta el /salud de cada servicio en paralelo:
# el mensaje trae lo que la capa sabe de sí misma (primario de MongoDB, ejecutores de Spark, cola) y la
# consulta directa detecta en el momento una capa que se cayó y cuyo último mensaje sigue vivo hasta 15 s.
# Gana el peor de los dos. Si sesion (Valkey) no responde no hay mensajes y queda solo la consulta directa.
ORDEN = {"operativo": 0, "degradado": 1, "caido": 2}
CON_MENSAJE = ("registro", "historial", "analitica")


def normalizar(estado) -> str:
    estado = str(estado or "").lower()
    if estado in ("operativo", "ok", "healthy"):
        return "operativo"
    if estado in ("caido", "caído"):
        return "caido"
    return "degradado"


async def leer_mensajes(cliente) -> dict | None:
    """Mensajes vigentes de Valkey, o None si sesion no los pudo dar."""
    try:
        respuesta = await cliente.get(
            SERVICIOS["sesion"].rstrip("/") + "/estado-capas",
            timeout=TIMEOUT,
        )
        datos = respuesta.json() if respuesta.status_code == 200 else None
    except Exception:
        return None
    if not isinstance(datos, dict) or not isinstance(datos.get("capas"), dict):
        return None
    return datos["capas"]


def combinar(nombre, directa, mensajes):
    if mensajes is None or nombre not in CON_MENSAJE:
        return {**directa, "fuente": "directa"}
    mensaje = mensajes.get(nombre)
    if mensaje is None:
        valkey = {"estado": "caido", "detalle": "sin mensaje en Valkey en los últimos 15 s"}
    else:
        valkey = {"estado": normalizar(mensaje.get("estado")), "detalle": mensaje.get("detalle"),
                  "ts": mensaje.get("ts")}
    estado = max(valkey["estado"], directa["estado"], key=ORDEN.__getitem__)
    return {"estado": estado, "detalle": directa["detalle"], "mensaje_valkey": valkey,
            "fuente": "valkey+directa"}


async def consultar_salud(cliente=None):
    async def consultar(http):
        nombres = list(SERVICIOS)

        *resultados, mensajes = await asyncio.gather(
            *(
                revisar_servicio(
                    http, nombre, SERVICIOS[nombre]
                )
                for nombre in nombres
            ),
            leer_mensajes(http),
        )

        capas = {
            nombre: combinar(nombre, directa, mensajes)
            for nombre, directa in zip(nombres, resultados)
        }
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
            "mensajes_valkey": mensajes is not None,
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
