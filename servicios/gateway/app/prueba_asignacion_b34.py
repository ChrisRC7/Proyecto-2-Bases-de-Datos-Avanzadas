
"""Pruebas B3.4: asignación y compensación.

Se ejecuta dentro del contenedor del gateway:
    docker compose exec -T gateway python -m app.prueba_asignacion_b34
No necesita los demás servicios: las llamadas internas se reemplazan con mocks.
Imprime una línea OK por cada caso y una marca final si todo pasa.
"""

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from app import asignacion
from app.cliente_interno import ServicioNoDisponible


def respuesta(codigo, datos=None):
    """Construye una httpx.Response falsa con ese código y cuerpo JSON (para los mocks)."""
    return httpx.Response(
        codigo,
        json=datos,
        request=httpx.Request("POST", "http://prueba.local/"),
    ) if datos is not None else httpx.Response(
        codigo,
        request=httpx.Request("POST", "http://prueba.local/"),
    )


async def pruebas():
    """Casos: sin tareas (204), asignación completa, fallo del lock con compensación y registro caído."""
    # Identificadores y datos falsos que devuelven los mocks.
    anotador = str(uuid4())
    tarea_id = str(uuid4())
    proyecto_id = str(uuid4())

    tarea = {
        "tarea_id": tarea_id,
        "proyecto_id": proyecto_id,
        "vence_en": "2026-11-09T22:00:00Z",
        "version": 1,
    }

    evento = {"event_id": str(uuid4()), "estado": "abierta"}

    # 1. Sin tareas disponibles: 204 sin consultar Sesión.
    llamadas = AsyncMock(return_value=respuesta(204))

    with patch.object(asignacion, "solicitar", llamadas):
        codigo, datos = await asignacion.siguiente(anotador)

    assert codigo == 204
    assert datos is None
    assert llamadas.await_count == 1
    print("OK | Sin tareas disponibles: 204")

    # 2. Asignación completa y borrador recuperado.
    llamadas = AsyncMock(side_effect=[
        respuesta(200, {"tarea": tarea, "evento": evento}),
        respuesta(200, {"estado": "ok"}),
        respuesta(200, {
            "estado": "ok",
            "borrador": {"contenido": {"texto": "recuperado"}},
        }),
    ])

    publicar = AsyncMock(return_value={"estado": "pendiente"})

    with patch.object(asignacion, "solicitar", llamadas), \
         patch.object(asignacion.eventos, "publicar_evento", publicar):
        codigo, datos = await asignacion.siguiente(anotador)

    assert codigo == 200
    assert datos["tarea"]["tarea_id"] == tarea_id
    assert datos["borrador"]["contenido"]["texto"] == "recuperado"
    assert datos["token"]
    assert llamadas.await_count == 3
    publicar.assert_awaited_once_with(evento)

    token_asignado = llamadas.await_args_list[0].kwargs["json"]["token"]
    token_lock = llamadas.await_args_list[1].kwargs["json"]["token"]
    token_sesion = llamadas.await_args_list[2].kwargs["json"]["token"]

    assert token_asignado == token_lock == token_sesion
    print("OK | Asignación, lock, sesión y borrador")

    # 3. Si falla el lock, se intenta revertir.
    llamadas = AsyncMock(side_effect=[
        respuesta(200, {"tarea": tarea, "evento": evento}),
        respuesta(409, {"error": "conflicto_lock"}),
        respuesta(404, {"error": "sesion_no_existe"}),
        respuesta(409, {"error": "conflicto_lock"}),
        respuesta(200, {
            "tarea": {"estado": "disponible"},
            "evento": {"event_id": str(uuid4()), "estado": "liberada"},
        }),
    ])

    publicar = AsyncMock(return_value={"estado": "pendiente"})

    with patch.object(asignacion, "solicitar", llamadas), \
         patch.object(asignacion.eventos, "publicar_evento", publicar):
        try:
            await asignacion.siguiente(anotador)
            raise AssertionError("Se esperaba un fallo")
        except ServicioNoDisponible:
            pass

    assert llamadas.await_count == 5
    assert llamadas.await_args_list[4].args[2] == f"/tareas/{tarea_id}/transicion"
    assert llamadas.await_args_list[4].kwargs["json"]["accion"] == "liberar"
    publicar.assert_awaited_once()
    print("OK | Fallo de lock: compensación en Registro")

    # 4. Error en Registro: no intentar abrir sesión.
    llamadas = AsyncMock(
        side_effect=ServicioNoDisponible("registro", "Sin conexión")
    )

    with patch.object(asignacion, "solicitar", llamadas):
        try:
            await asignacion.siguiente(anotador)
            raise AssertionError("Se esperaba 503")
        except ServicioNoDisponible:
            pass

    assert llamadas.await_count == 1
    print("OK | Registro caído: sin reintento automático")

    print("B3.4_ASIGNACION_OK")


if __name__ == "__main__":
    asyncio.run(pruebas())
