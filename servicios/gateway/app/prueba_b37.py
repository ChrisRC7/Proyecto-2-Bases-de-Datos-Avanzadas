
"""B3.7: pruebas simuladas de liberar y recuperar sesion."""

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from app import liberacion, main
from app.cliente_interno import ServicioNoDisponible


def respuesta(codigo, datos):
    return httpx.Response(
        codigo,
        json=datos,
        request=httpx.Request("POST", "http://interno/prueba"),
    )


async def pruebas():
    tarea = str(uuid4())
    anotador = str(uuid4())
    token = str(uuid4())

    transporte = httpx.ASGITransport(app=main.app)

    async with httpx.AsyncClient(
        transport=transporte,
        base_url="http://gateway",
    ) as cliente:

        headers = {"X-Anotador-Id": anotador}

        # 1. Liberar correctamente.
        datos_registro = {
            "tarea": {
                "estado": "disponible",
                "version": 2,
            },
            "evento": {"event_id": str(uuid4())},
        }

        with (
            patch.object(
                liberacion,
                "solicitar",
                new_callable=AsyncMock,
                return_value=respuesta(200, datos_registro),
            ) as registro,
            patch.object(
                liberacion.eventos,
                "publicar_evento",
                new_callable=AsyncMock,
            ) as evento,
            patch.object(
                liberacion.limpiezas,
                "registrar",
                new_callable=AsyncMock,
            ) as limpieza,
        ):
            r = await cliente.post(
                f"/tareas/{tarea}/liberar",
                headers=headers,
                json={"token": token},
            )

            assert r.status_code == 200, r.text
            assert r.json()["estado"] == "disponible"
            registro.assert_awaited_once()
            evento.assert_awaited_once()
            limpieza.assert_awaited_once()

        print("OK | Liberar 200: evento y limpieza registrados")

        # 2. Conflicto de Registro: no generar evento ni limpieza.
        with (
            patch.object(
                liberacion,
                "solicitar",
                new_callable=AsyncMock,
                return_value=respuesta(
                    409,
                    {"error": "transicion_invalida"},
                ),
            ),
            patch.object(
                liberacion.eventos,
                "publicar_evento",
                new_callable=AsyncMock,
            ) as evento,
            patch.object(
                liberacion.limpiezas,
                "registrar",
                new_callable=AsyncMock,
            ) as limpieza,
        ):
            r = await cliente.post(
                f"/tareas/{tarea}/liberar",
                headers=headers,
                json={"token": token},
            )

            assert r.status_code == 409, r.text
            evento.assert_not_awaited()
            limpieza.assert_not_awaited()

        print("OK | Liberar 409: no limpia ni publica")

        # 3. Registro no disponible.
        with patch.object(
            liberacion,
            "solicitar",
            new_callable=AsyncMock,
            side_effect=ServicioNoDisponible(
                "registro", "Sin conexion"
            ),
        ) as registro:
            r = await cliente.post(
                f"/tareas/{tarea}/liberar",
                headers=headers,
                json={"token": token},
            )

            assert r.status_code == 503, r.text
            assert registro.await_count == 1

        print("OK | Liberar 503: sin reintento en gateway")

        # 4. Recuperar sesion y borrador.
        datos_sesion = {
            "sesion": {
                "tarea": tarea,
                "proyecto": str(uuid4()),
                "etapa": "anotando",
                "token": token,
            },
            "borrador": {
                "contenido": {"etiquetas": ["carro"]},
                "guardado_en": 1794279600000,
            },
        }

        with patch.object(
            main,
            "solicitar",
            new_callable=AsyncMock,
            return_value=respuesta(200, datos_sesion),
        ):
            r = await cliente.post(
                "/sesiones/recuperar",
                headers=headers,
            )

            assert r.status_code == 200, r.text
            assert r.json()["token"] == token
            assert r.json()["tarea"]["tarea_id"] == tarea
            assert r.json()["borrador"]["contenido"] == {
                "etiquetas": ["carro"]
            }

        print("OK | Recuperar 200: sesion y borrador")

        # 5. No hay sesion.
        with patch.object(
            main,
            "solicitar",
            new_callable=AsyncMock,
            return_value=respuesta(
                404,
                {"error": "sesion_no_existe"},
            ),
        ):
            r = await cliente.post(
                "/sesiones/recuperar",
                headers=headers,
            )
            assert r.status_code == 404, r.text

        print("OK | Recuperar 404: sin sesion")

        # 6. Conflicto de sesion.
        with patch.object(
            main,
            "solicitar",
            new_callable=AsyncMock,
            return_value=respuesta(
                409,
                {"error": "borrador_de_otro_anotador"},
            ),
        ):
            r = await cliente.post(
                "/sesiones/recuperar",
                headers=headers,
            )
            assert r.status_code == 409, r.text

        print("OK | Recuperar 409: conflicto")

        # 7. No hay borrador, pero la sesion existe.
        sin_borrador = {
            "sesion": datos_sesion["sesion"],
            "borrador": None,
        }

        with patch.object(
            main,
            "solicitar",
            new_callable=AsyncMock,
            return_value=respuesta(200, sin_borrador),
        ):
            r = await cliente.post(
                "/sesiones/recuperar",
                headers=headers,
            )

            assert r.status_code == 200, r.text
            assert r.json()["borrador"] is None

        print("OK | Recuperar 200: sesion sin borrador")

    print("B3.7_PRUEBAS_OK")


if __name__ == "__main__":
    asyncio.run(pruebas())
