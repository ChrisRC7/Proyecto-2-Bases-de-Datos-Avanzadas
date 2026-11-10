
"""Cliente HTTP interno del gateway - B3.2."""

import asyncio
import os
import re

import httpx


SERVICIOS = {
    "registro": os.getenv("REGISTRO_URL", "http://registro:8001"),
    "sesion": os.getenv("SESION_URL", "http://sesion:8002"),
    "historial": os.getenv("HISTORIAL_URL", "http://historial:8003"),
    "analitica": os.getenv("ANALITICA_URL", "http://analitica:8004"),
}

TIMEOUT_SEGUNDOS = 1.0
MAX_INTENTOS = 2
ESPERA_REINTENTO = 0.1

ERRORES_TEMPORALES = {502, 503, 504}


class ServicioNoDisponible(Exception):
    """Fallo de comunicación con un servicio interno."""

    def __init__(self, servicio: str, detalle: str):
        self.servicio = servicio
        self.detalle = detalle
        super().__init__(f"{servicio}: {detalle}")



def permite_reintento(metodo: str, ruta: str) -> bool:
    metodo = metodo.upper()

    if metodo == "GET":
        return True

    if metodo == "PUT":
        return bool(
            re.fullmatch(
                r"/(?:tareas/[^/]+/borrador|borradores/[^/]+)/?",
                ruta,
            )
        )

    return False



async def solicitar(
    servicio: str,
    metodo: str,
    ruta: str,
    *,
    json=None,
    params=None,
    request_id=None,
    cliente: httpx.AsyncClient | None = None,
) -> httpx.Response:
    """Solicita una operación interna con reintentos seguros.

    Devuelve la respuesta para que cada ruta del gateway
    gestione sus códigos 2xx y 4xx.

    Los fallos de red y respuestas 5xx generan
    ServicioNoDisponible, que el gateway convierte en HTTP 503.
    """

    if servicio not in SERVICIOS:
        raise ValueError(f"Servicio interno desconocido: {servicio}")

    if not ruta.startswith("/") or ruta.startswith("//"):
        raise ValueError("La ruta interna debe comenzar con una sola /")

    metodo = metodo.upper()
    intentos = MAX_INTENTOS if permite_reintento(metodo, ruta) else 1

    url = SERVICIOS[servicio].rstrip("/") + ruta
    headers = {}

    if request_id:
        headers["X-Request-Id"] = request_id

    async def ejecutar(http: httpx.AsyncClient) -> httpx.Response:
        for intento in range(intentos):
            try:
                respuesta = await http.request(
                    metodo,
                    url,
                    json=json,
                    params=params,
                    headers=headers,
                    timeout=TIMEOUT_SEGUNDOS,
                )

                if respuesta.status_code < 500:
                    return respuesta

                if (
                    respuesta.status_code not in ERRORES_TEMPORALES
                    or intento == intentos - 1
                ):
                    raise ServicioNoDisponible(
                        servicio,
                        f"HTTP {respuesta.status_code} del servicio interno",
                    )

            except httpx.TransportError as exc:
                if intento == intentos - 1:
                    raise ServicioNoDisponible(
                        servicio,
                        f"Error de comunicación: {type(exc).__name__}",
                    ) from exc

            await asyncio.sleep(ESPERA_REINTENTO)

        raise ServicioNoDisponible(servicio, "Intentos agotados")

    if cliente is not None:
        return await ejecutar(cliente)

    async with httpx.AsyncClient() as http:
        return await ejecutar(http)
