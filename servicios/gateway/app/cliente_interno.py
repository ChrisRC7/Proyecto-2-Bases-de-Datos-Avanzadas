
"""Cliente HTTP interno del gateway - B3.2.

Función `solicitar` para hablar con los servicios internos: arma la URL, propaga el
X-Request-Id, aplica timeout y reintenta una sola vez solo las operaciones seguras de
repetir (GET y PUT de borrador). Los errores de red y los 5xx se convierten en
ServicioNoDisponible, que el gateway responde como 503.
"""

import asyncio
import os
import re

import httpx


# URL base de cada servicio interno (nombres de la red de docker-compose).
SERVICIOS = {
    "registro": os.getenv("REGISTRO_URL", "http://registro:8001"),
    "sesion": os.getenv("SESION_URL", "http://sesion:8002"),
    "historial": os.getenv("HISTORIAL_URL", "http://historial:8003"),
    "analitica": os.getenv("ANALITICA_URL", "http://analitica:8004"),
}

# Timeout por intento, intentos máximos (1 + 1 reintento) y pausa entre intentos.
TIMEOUT_SEGUNDOS = 1.0
MAX_INTENTOS = 2
ESPERA_REINTENTO = 0.1

# Códigos 5xx que pueden ser pasajeros y justifican reintentar.
ERRORES_TEMPORALES = {502, 503, 504}

# Un solo cliente para todo el gateway. Crear un httpx.AsyncClient cuesta CPU (arma el contexto TLS)
# y bloquea el event loop; con uno por llamada, 50 peticiones juntas hacían fila detrás de ~150
# clientes nuevos (evidence/P2_concurrencia_gateway_20261110-1450.md). Se crea al primer uso y se
# cierra al apagar. keepalive_expiry < 5 s (keep-alive de uvicorn) para no reusar una conexión que
# el servicio ya cerró: un POST que falla así no se reintenta.
_compartido: httpx.AsyncClient | None = None


def cliente_compartido() -> httpx.AsyncClient:
    """Devuelve el cliente HTTP compartido, creándolo si no existe o si ya se cerró."""
    global _compartido
    if _compartido is None or _compartido.is_closed:
        _compartido = httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=200,
                max_keepalive_connections=100,
                keepalive_expiry=2.0,
            ),
        )
    return _compartido


async def cerrar_cliente():
    """Cierra el cliente compartido (se llama al apagar el gateway)."""
    global _compartido
    if _compartido is not None:
        await _compartido.aclose()
        _compartido = None


class ServicioNoDisponible(Exception):
    """Fallo de comunicación con un servicio interno."""

    def __init__(self, servicio: str, detalle: str):
        """Guarda qué servicio falló y una descripción corta del problema."""
        self.servicio = servicio
        self.detalle = detalle
        super().__init__(f"{servicio}: {detalle}")



def permite_reintento(metodo: str, ruta: str) -> bool:
    """True si repetir la petición no tiene efectos dobles (idempotente).

    GET siempre; PUT solo para borradores (guardar dos veces lo mismo da igual). Los POST
    (asignar, enviar, transiciones) nunca se reintentan.
    """
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
    timeout: float = TIMEOUT_SEGUNDOS,
    reintentar: bool = True,
) -> httpx.Response:
    """Solicita una operación interna con reintentos seguros.

    Devuelve la respuesta para que cada ruta del gateway
    gestione sus códigos 2xx y 4xx.

    Los fallos de red y respuestas 5xx generan
    ServicioNoDisponible, que el gateway convierte en HTTP 503.

    `timeout` y `reintentar` solo los cambia la analítica (C4.9): una consulta de Spark
    tarda segundos y repetirla después de un timeout duplicaría el trabajo en el clúster.
    """

    # Validaciones defensivas: servicio conocido y ruta relativa (evita armar URLs a otro host).
    if servicio not in SERVICIOS:
        raise ValueError(f"Servicio interno desconocido: {servicio}")

    if not ruta.startswith("/") or ruta.startswith("//"):
        raise ValueError("La ruta interna debe comenzar con una sola /")

    metodo = metodo.upper()
    intentos = MAX_INTENTOS if reintentar and permite_reintento(metodo, ruta) else 1

    url = SERVICIOS[servicio].rstrip("/") + ruta
    headers = {}

    if request_id:
        headers["X-Request-Id"] = request_id

    async def ejecutar(http: httpx.AsyncClient) -> httpx.Response:
        """Hace hasta `intentos` llamadas; devuelve la primera respuesta < 500."""
        for intento in range(intentos):
            try:
                respuesta = await http.request(
                    metodo,
                    url,
                    json=json,
                    params=params,
                    headers=headers,
                    timeout=timeout,
                )

                # 2xx/4xx: respuesta válida del servicio; la ruta decide qué hacer.
                if respuesta.status_code < 500:
                    return respuesta

                # 5xx no temporal o último intento: se da por caído.

                if (
                    respuesta.status_code not in ERRORES_TEMPORALES
                    or intento == intentos - 1
                ):
                    raise ServicioNoDisponible(
                        servicio,
                        f"HTTP {respuesta.status_code} del servicio interno",
                    )

            # Error de red (conexión rechazada, timeout...): se reintenta si quedan intentos.
            except httpx.TransportError as exc:
                if intento == intentos - 1:
                    raise ServicioNoDisponible(
                        servicio,
                        f"Error de comunicación: {type(exc).__name__}",
                    ) from exc

            await asyncio.sleep(ESPERA_REINTENTO)

        raise ServicioNoDisponible(servicio, "Intentos agotados")

    # Las pruebas pueden pasar su propio cliente; si no, se usa el compartido.
    return await ejecutar(cliente or cliente_compartido())
