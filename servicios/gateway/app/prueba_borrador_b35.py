
"""Pruebas B3.5: guardado de borradores desde gateway.

Se ejecuta dentro del contenedor del gateway:
    docker compose exec -T gateway python -m app.prueba_borrador_b35
No necesita los demás servicios: las llamadas internas se reemplazan con mocks.
Imprime una línea OK por cada caso y una marca final si todo pasa.
"""

import asyncio
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from app import main
from app.cliente_interno import permite_reintento


def respuesta(codigo, datos):
    """Construye una httpx.Response falsa con ese código y cuerpo JSON (para los mocks)."""
    return httpx.Response(
        codigo,
        json=datos,
        request=httpx.Request("PUT", "http://sesion:8002/borradores/prueba"),
    )


async def pruebas():
    """Llama a main.guardar_borrador directamente con `solicitar` simulado."""
    tarea_id = uuid4()
    anotador_id = uuid4()
    token = uuid4()

    cuerpo = main.SolicitudBorrador(
        token=token,
        contenido={"texto": "borrador de prueba"},
    )

    # Objeto mínimo que imita a fastapi.Request (solo se usa request.state.request_id).
    request = type("RequestPrueba", (), {})()
    request.state = type("EstadoPrueba", (), {})()
    request.state.request_id = "b35-request-id"

    # 1. Guardado correcto.
    solicitar = AsyncMock(return_value=respuesta(
        200,
        {"estado": "ok", "borrador": {"contenido": {"texto": "borrador de prueba"}}},
    ))

    with patch.object(main, "solicitar", solicitar):
        resultado = await main.guardar_borrador(
            request, tarea_id, cuerpo, anotador_id
        )

    assert resultado["estado"] == "ok"
    a_sesion, a_registro = solicitar.await_args_list
    assert a_sesion.args[0] == "sesion"
    assert a_sesion.kwargs["json"]["token"] == str(token)
    assert a_sesion.kwargs["json"]["anotador_id"] == str(anotador_id)
    assert a_sesion.kwargs["request_id"] == "b35-request-id"
    print("OK | Borrador enviado a Sesión con token y anotador")

    # El plazo de la asignación en registro se corre junto con el lock.
    assert a_registro.args[:3] == ("registro", "POST", f"/tareas/{tarea_id}/transicion")
    assert a_registro.kwargs["json"] == {
        "accion": "renovar", "token": str(token), "actor_id": str(anotador_id),
    }
    print("OK | Plazo de la asignación renovado en Registro")

    # Si el registro ya no tiene la asignación (venció), el borrador responde 409.
    solicitar = AsyncMock(side_effect=[
        respuesta(200, {"estado": "ok", "borrador": {}}),
        respuesta(409, {"error": "asignacion_vencida", "detalle": {"estado": "abierta"}}),
    ])
    with patch.object(main, "solicitar", solicitar):
        resultado = await main.guardar_borrador(
            request, tarea_id, cuerpo, anotador_id
        )
    assert resultado.status_code == 409
    print("OK | Asignación vencida en Registro: HTTP 409")

    # 2. Conflicto de lock.
    solicitar = AsyncMock(return_value=respuesta(
        409,
        {"error": "conflicto_lock", "detalle": "Token incorrecto"},
    ))

    with patch.object(main, "solicitar", solicitar):
        resultado = await main.guardar_borrador(
            request, tarea_id, cuerpo, anotador_id
        )

    assert resultado.status_code == 409
    print("OK | Token incorrecto: HTTP 409")

    # 3. Reintentos seguros en la ruta interna.
    assert permite_reintento("PUT", f"/borradores/{tarea_id}")
    assert permite_reintento("PUT", f"/tareas/{tarea_id}/borrador")
    assert not permite_reintento("POST", f"/tareas/{tarea_id}/enviar")
    assert not permite_reintento("PUT", f"/locks/{tarea_id}")
    print("OK | Reintentos permitidos solo para PUT borrador")

    # 4. Ruta pública registrada.
    rutas = {
        ruta.path
        for ruta in main.app.routes
        if hasattr(ruta, "path")
    }
    assert "/tareas/{tarea_id}/borrador" in rutas
    print("OK | Ruta pública de borradores registrada")

    print("B3.5_BORRADORES_OK")


if __name__ == "__main__":
    asyncio.run(pruebas())
