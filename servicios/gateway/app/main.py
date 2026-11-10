
"""Servicio gateway - API de entrada."""

from fastapi import Request,Header, Response

from app.cliente_interno import ServicioNoDisponible
from app.comun import SERVICIO, ahora_iso, crear_app, error
from app import eventos

from uuid import UUID

from pydantic import BaseModel, Field

from app import asignacion

from app.cliente_interno import solicitar

from app import envio

from app import limpiezas

from app import liberacion

from datetime import datetime, timezone

app = crear_app(SERVICIO)


class SolicitudSiguiente(BaseModel):
    proyecto_id: UUID | None = None


@app.post("/tareas/siguiente")
async def tareas_siguiente(
    request: Request,
    cuerpo: SolicitudSiguiente,
    anotador_id: UUID = Header(alias="X-Anotador-Id"),
):
    codigo, datos = await asignacion.siguiente(
        anotador_id=anotador_id,
        proyecto_id=cuerpo.proyecto_id,
        request_id=request.state.request_id,
    )

    if codigo == 204:
        return Response(status_code=204)

    if codigo != 200:
        return error(
            codigo,
            datos.get("error", "error_registro"),
            datos.get("detalle"),
        )

    return datos



@app.on_event("startup")
async def startup():
    await eventos.iniciar_publicador()
    await limpiezas.iniciar()


@app.on_event("shutdown")
async def shutdown():
    await limpiezas.detener()
    await eventos.detener_publicador()



@app.exception_handler(ServicioNoDisponible)
async def servicio_no_disponible(_request: Request, exc: ServicioNoDisponible):
    return error(
        503,
        "servicio_no_disponible",
        {
            "servicio": exc.servicio,
            "mensaje": exc.detalle,
        },
    )


@app.get("/salud")
async def salud():
    return {
        "servicio": SERVICIO,
        "estado": "operativo",
        "ts": ahora_iso(),
    }



class SolicitudBorrador(BaseModel):
    token: UUID
    contenido: dict = Field(...)


@app.put("/tareas/{tarea_id}/borrador")
async def guardar_borrador(
    request: Request,
    tarea_id: UUID,
    cuerpo: SolicitudBorrador,
    anotador_id: UUID = Header(alias="X-Anotador-Id"),
):
    respuesta = await solicitar(
        "sesion",
        "PUT",
        f"/borradores/{tarea_id}",
        json={
            "anotador_id": str(anotador_id),
            "token": str(cuerpo.token),
            "contenido": cuerpo.contenido,
        },
        request_id=request.state.request_id,
    )

    datos = respuesta.json()

    if respuesta.status_code != 200:
        return error(
            respuesta.status_code,
            datos.get("error", "error_borrador"),
            datos.get("detalle"),
        )

    return datos



class SolicitudEnviar(BaseModel):
    token: UUID
    anotacion: dict


@app.post("/tareas/{tarea_id}/enviar")
async def enviar_anotacion(
    request: Request,
    tarea_id: UUID,
    cuerpo: SolicitudEnviar,
    anotador_id: UUID = Header(alias="X-Anotador-Id"),
):
    codigo, datos = await envio.enviar(
        tarea_id=tarea_id,
        anotador_id=anotador_id,
        token=cuerpo.token,
        anotacion=cuerpo.anotacion,
        request_id=request.state.request_id,
    )

    if codigo != 200:
        return error(
            codigo,
            datos.get("error", "error_envio"),
            datos.get("detalle"),
        )

    return datos

class SolicitudLiberar(BaseModel):
    token: UUID


@app.post("/tareas/{tarea_id}/liberar")
async def liberar_tarea(
    request: Request,
    tarea_id: UUID,
    cuerpo: SolicitudLiberar,
    anotador_id: UUID = Header(alias="X-Anotador-Id"),
):
    codigo, datos = await liberacion.liberar(
        tarea_id=tarea_id,
        anotador_id=anotador_id,
        token=cuerpo.token,
        request_id=request.state.request_id,
    )

    if codigo != 200:
        return error(
            codigo,
            datos.get("error", "error_liberacion"),
            datos.get("detalle"),
        )

    return datos


@app.post("/sesiones/recuperar")
async def recuperar_sesion(
    request: Request,
    anotador_id: UUID = Header(alias="X-Anotador-Id"),
):
    respuesta = await solicitar(
        "sesion",
        "POST",
        f"/sesiones/{anotador_id}",
        json={"accion": "recuperar"},
        request_id=request.state.request_id,
    )

    datos = respuesta.json()

    if respuesta.status_code != 200:
        return error(
            respuesta.status_code,
            datos.get("error", "error_recuperacion"),
            datos.get("detalle"),
        )

    sesion = datos["sesion"]
    borrador = datos.get("borrador")

    return {
        "tarea": {
            "tarea_id": sesion["tarea"],
            "proyecto_id": sesion["proyecto"],
            "etapa": sesion["etapa"],
        },
        "token": sesion["token"],
        "borrador": (
            {
                "contenido": borrador["contenido"],
                "guardado_en": datetime.fromtimestamp(
                    borrador["guardado_en"] / 1000,
                    tz=timezone.utc,
                ).isoformat(),
            }
            if borrador is not None
            else None
        ),
    }
