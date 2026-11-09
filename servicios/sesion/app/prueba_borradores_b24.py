
"""Pruebas HTTP de borradores recuperables - B2.4."""

import json
import urllib.error
import urllib.request
import uuid

from app import borradores, locks, sesiones

identificador = uuid.uuid4().hex
tarea = f"prueba-b24-{identificador}"
anotador = f"anotador-b24-{identificador}"
otro_anotador = f"otro-b24-{identificador}"
proyecto = f"proyecto-b24-{identificador}"
token = f"token-b24-{identificador}"

base = "http://localhost:8002"


def peticion(ruta, metodo, cuerpo):
    datos = json.dumps(cuerpo).encode("utf-8")
    solicitud = urllib.request.Request(
        f"{base}{ruta}",
        data=datos,
        headers={"Content-Type": "application/json"},
        method=metodo,
    )

    try:
        with urllib.request.urlopen(solicitud, timeout=5) as respuesta:
            return respuesta.status, json.load(respuesta)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def sesion(anotador_id, accion, token_sesion=token, **campos):
    return peticion(
        f"/sesiones/{anotador_id}",
        "POST",
        {"accion": accion, "token": token_sesion, **campos},
    )


try:
    # 1. Adquirir el lock.
    codigo, _ = peticion(
        f"/locks/{tarea}",
        "POST",
        {"accion": "adquirir", "token": token},
    )
    assert codigo == 200
    print("OK | Adquirir lock: HTTP 200")

    # 2. Guardar un borrador.
    contenido = {"etiqueta": "gato", "avance": 50}
    codigo, _ = peticion(
        f"/borradores/{tarea}",
        "PUT",
        {
            "anotador_id": anotador,
            "token": token,
            "contenido": contenido,
        },
    )
    assert codigo == 200
    print("OK | Guardar borrador: HTTP 200")

    # 3. Rechazar un token incorrecto.
    codigo, _ = peticion(
        f"/borradores/{tarea}",
        "PUT",
        {
            "anotador_id": anotador,
            "token": "token-incorrecto",
            "contenido": {"etiqueta": "perro"},
        },
    )
    assert codigo == 409
    print("OK | Token incorrecto: HTTP 409")

    # 4. Abrir y recuperar el borrador.
    codigo, cuerpo = sesion(
        anotador,
        "abrir",
        tarea_id=tarea,
        proyecto_id=proyecto,
        etapa="anotando",
    )
    assert codigo == 200, (codigo, cuerpo)
    assert cuerpo["borrador"]["contenido"] == contenido
    print("OK | Abrir devuelve borrador")

    # 5. Recuperar la sesion existente.
    codigo, cuerpo = sesion(anotador, "recuperar")
    assert codigo == 200, (codigo, cuerpo)
    assert cuerpo["borrador"]["contenido"] == contenido
    print("OK | Recuperar devuelve borrador")

    # 6. Otro anotador no puede leerlo.
    codigo, _ = sesion(
        otro_anotador,
        "abrir",
        tarea_id=tarea,
        proyecto_id=proyecto,
        etapa="anotando",
    )
    assert codigo == 409
    assert sesiones.consultar(otro_anotador) is None
    print("OK | Otro anotador rechazado: HTTP 409")

    # 7. Comprobar que el borrador permanece guardado.
    assert borradores.recuperar(tarea)["contenido"] == contenido
    print("OK | Borrador conservado en Valkey")

    print("B2.4_HTTP_OK")

finally:
    locks.cliente.delete(f"borrador:{tarea}", f"lock:tarea:{tarea}")
    for usuario in (anotador, otro_anotador):
        datos = sesiones.consultar(usuario)
        if datos:
            sesiones.cerrar(usuario, datos["token"])
