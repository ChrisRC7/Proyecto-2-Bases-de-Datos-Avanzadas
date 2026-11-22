
"""B3.8: salud agregada, fallos y consultas paralelas.

Se ejecuta dentro del contenedor del gateway:
    docker compose exec -T gateway python -m app.prueba_salud_b38
No necesita los demás servicios: las llamadas internas se reemplazan con mocks.
Imprime una línea OK por cada caso y una marca final si todo pasa.
"""

import asyncio
import time
from unittest.mock import patch

import httpx

from app import main, salud


async def pruebas():
    """Simula las respuestas /salud de cada capa y los mensajes de Valkey y revisa el estado agregado."""
    # Se guarda la función real para restaurarla al final.
    original = salud.consultar_salud

    async def ejecutar(escenario):
        """Corre consultar_salud con todas las capas respondiendo según el escenario dado."""
        async def responder(request):
            """Respuesta simulada de cada servicio (con 0.2 s de demora para medir el paralelismo)."""
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

            if escenario.startswith("valkey") and request.url.path == "/estado-capas":
                # Mensajes que las capas dejaron en Valkey (llaves salud:{servicio}, TTL 15 s).
                capas = {n: {"estado": "operativo", "detalle": {}, "ts": "t"}
                         for n in ("registro", "historial", "analitica")}
                if escenario == "valkey_sin_analitica":
                    del capas["analitica"]  # dejó de publicar: su llave venció
                if escenario == "valkey_registro_caido":
                    capas["registro"]["estado"] = "caido"
                return httpx.Response(200, json={"capas": capas})

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
                """consultar_salud real usando el cliente HTTP simulado."""
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

    # Sin mensajes de Valkey (respuesta sin "capas"): solo la consulta directa.
    datos, _ = await ejecutar("operativos")
    assert datos["mensajes_valkey"] is False
    assert datos["capas"]["registro"]["fuente"] == "directa"

    # Con mensajes en Valkey: se combinan con la consulta directa.
    datos, _ = await ejecutar("valkey_ok")
    assert datos["estado"] == "operativo" and datos["mensajes_valkey"] is True
    assert datos["capas"]["historial"]["fuente"] == "valkey+directa"
    assert datos["capas"]["sesion"]["fuente"] == "directa"
    print("OK | Mensajes de Valkey leidos para registro, historial y analitica")

    # Una capa que dejó de publicar en Valkey cuenta como caída aunque su /salud responda.
    datos, _ = await ejecutar("valkey_sin_analitica")
    assert datos["capas"]["analitica"]["estado"] == "caido"
    assert datos["estado"] == "degradado"
    print("OK | Capa sin mensaje en Valkey: caido")

    # El mensaje de Valkey dice que el registro está caído (MongoDB sin primario): gana el peor.
    datos, _ = await ejecutar("valkey_registro_caido")
    assert datos["capas"]["registro"]["estado"] == "caido"
    assert datos["estado"] == "caido"
    print("OK | Registro caido segun Valkey: gateway caido")

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
