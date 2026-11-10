
"""B3.8: salud agregada, fallos y consultas paralelas."""

import asyncio
import time
from unittest.mock import patch

import httpx

from app import main, salud


async def pruebas():
    original = salud.consultar_salud

    async def ejecutar(escenario):
        async def responder(request):
            await asyncio.sleep(0.2)

            nombre = request.url.host

            if escenario == "caidos":
                raise httpx.ConnectError(
                    "Servicio inaccesible",
                    request=request,
                )

            if escenario == "mixto" and nombre == "historial":
                return httpx.Response(503)

            if escenario == "mixto" and nombre == "analitica":
                return httpx.Response(
                    200,
                    json={"estado": "degradado"},
                )

            if escenario == "registro_caido" and nombre == "registro":
                # MongoDB sin primario: registro responde 200 con estado caido.
                return httpx.Response(200, json={"estado": "caido"})

            return httpx.Response(
                200,
                json={"estado": "operativo"},
            )

        transporte_interno = httpx.MockTransport(responder)

        async with httpx.AsyncClient(
            transport=transporte_interno
        ) as interno:

            async def salud_simulada():
                return await original(cliente=interno)

            with patch.object(
                main.salud_capas,
                "consultar_salud",
                side_effect=salud_simulada,
            ):
                transporte_gateway = httpx.ASGITransport(
                    app=main.app
                )

                async with httpx.AsyncClient(
                    transport=transporte_gateway,
                    base_url="http://gateway",
                ) as cliente:
                    inicio = time.perf_counter()
                    respuesta = await cliente.get("/salud")
                    duracion = time.perf_counter() - inicio

        assert respuesta.status_code == 200
        return respuesta.json(), duracion

    # Todos los servicios operativos.
    datos, duracion = await ejecutar("operativos")
    assert datos["estado"] == "operativo"
    assert len(datos["capas"]) == 4
    assert all(
        capa["estado"] == "operativo"
        for capa in datos["capas"].values()
    )
    print("OK | Cuatro servicios operativos")

    # Algunos servicios presentan fallos.
    datos, duracion = await ejecutar("mixto")
    assert datos["estado"] == "degradado"
    assert datos["capas"]["historial"]["estado"] == "caido"
    assert datos["capas"]["analitica"]["estado"] == "degradado"
    print("OK | Fallo parcial: gateway degradado")

    # Registro (MongoDB) caído: no se puede asignar ni enviar.
    datos, duracion = await ejecutar("registro_caido")
    assert datos["estado"] == "caido"
    assert datos["capas"]["registro"]["estado"] == "caido"
    assert datos["capas"]["sesion"]["estado"] == "operativo"
    print("OK | Registro caido: gateway caido aunque el resto responda")

    # Todos los servicios inaccesibles.
    datos, duracion = await ejecutar("caidos")
    assert datos["estado"] == "caido"
    assert len(datos["capas"]) == 4
    print("OK | Todos caidos: gateway responde HTTP 200")

    # Cada servicio tarda 0.2 segundos.
    # Cuatro llamadas secuenciales tardarian ~0.8 s.
    assert duracion < 0.65, (
        f"Las consultas parecen secuenciales: {duracion:.3f}s"
    )
    print("OK | Consultas paralelas verificadas")
    print(f"Duracion: {duracion:.3f} s")

    print("B3.8_SALUD_OK")


if __name__ == "__main__":
    asyncio.run(pruebas())
