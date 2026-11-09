
"""Pruebas B3.2 - cliente HTTP interno."""

import asyncio

import httpx

from app.cliente_interno import (
    ServicioNoDisponible,
    permite_reintento,
    solicitar,
)


async def pruebas():
    # 1. GET: un fallo temporal seguido de éxito.
    llamadas = []

    def get_temporal(request):
        llamadas.append(request)
        if len(llamadas) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(get_temporal)
    ) as cliente:
        respuesta = await solicitar(
            "registro", "GET", "/salud", cliente=cliente
        )

    assert respuesta.status_code == 200
    assert len(llamadas) == 2
    print("OK | GET reintentado ante HTTP 503")

    # 2. PUT borrador: mismo contenido y token.
    llamadas = []

    def put_temporal(request):
        llamadas.append(request)
        if len(llamadas) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"guardado": True})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(put_temporal)
    ) as cliente:
        respuesta = await solicitar(
            "sesion",
            "PUT",
            "/tareas/123/borrador",
            json={"token": "abc", "contenido": {"texto": "hola"}},
            cliente=cliente,
        )

    assert respuesta.status_code == 200
    assert len(llamadas) == 2
    assert llamadas[0].content == llamadas[1].content
    print("OK | PUT borrador reintentado sin alterar datos")

    # 3. POST enviar: jamás reintentar.
    llamadas = []

    def enviar_fallido(request):
        llamadas.append(request)
        return httpx.Response(503)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(enviar_fallido)
    ) as cliente:
        try:
            await solicitar(
                "registro",
                "POST",
                "/tareas/123/enviar",
                json={"token": "abc"},
                cliente=cliente,
            )
            raise AssertionError("Debió fallar")
        except ServicioNoDisponible:
            pass

    assert len(llamadas) == 1
    print("OK | POST enviar no se reintenta")

    # 4. Otros POST tampoco se reintentan.
    assert not permite_reintento("POST", "/proyectos")
    assert not permite_reintento("POST", "/tareas/123/enviar")
    assert not permite_reintento("PUT", "/proyectos/123")
    assert permite_reintento("GET", "/salud")
    assert permite_reintento("PUT", "/tareas/123/borrador")
    print("OK | Reintentos restringidos a operaciones permitidas")

    # 5. Error de conexión: GET reintenta, luego 503.
    llamadas = []

    def sin_conexion(request):
        llamadas.append(request)
        raise httpx.ConnectError("Sin conexión")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(sin_conexion)
    ) as cliente:
        try:
            await solicitar(
                "registro", "GET", "/salud", cliente=cliente
            )
            raise AssertionError("Debió fallar")
        except ServicioNoDisponible:
            pass

    assert len(llamadas) == 2
    print("OK | Error de conexión tras agotar reintentos")

    # 6. Los conflictos de negocio no se reintentan.
    llamadas = []

    def conflicto(request):
        llamadas.append(request)
        return httpx.Response(409, json={"error": "conflicto"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(conflicto)
    ) as cliente:
        respuesta = await solicitar(
            "sesion",
            "PUT",
            "/tareas/123/borrador",
            cliente=cliente,
        )

    assert respuesta.status_code == 409
    assert len(llamadas) == 1
    print("OK | HTTP 409 preservado sin reintento")

    # 7. Propagación de identificador.
    llamadas = []

    def verificar_header(request):
        llamadas.append(request)
        return httpx.Response(200)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(verificar_header)
    ) as cliente:
        await solicitar(
            "registro",
            "GET",
            "/salud",
            request_id="b32-prueba-123",
            cliente=cliente,
        )

    assert llamadas[0].headers["X-Request-Id"] == "b32-prueba-123"
    print("OK | X-Request-Id propagado al servicio interno")

    print("B3.2_CLIENTE_INTERNO_OK")


if __name__ == "__main__":
    asyncio.run(pruebas())
