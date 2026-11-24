
"""Pruebas HTTP de mensajes de salud - B2.7.

Publica el estado de una capa ficticia y comprueba el hash `salud:{servicio}` en Valkey,
su TTL de 15 s, la consulta por HTTP, la renovación del TTL al republicar y que la capa
desaparece al expirar. Imprime B2.7_ESTADO_CAPAS_OK si todo pasa.
"""

import json
import time
import urllib.request
import uuid

from app import estado_capas

# Nombre de capa único para esta corrida y su llave en Valkey.
servicio = f"prueba-b27-{uuid.uuid4().hex}"
llave = f"salud:{servicio}"
base = "http://localhost:8002"


def consultar():
    """GET /estado-capas; exige HTTP 200 y devuelve el JSON."""
    with urllib.request.urlopen(
        f"{base}/estado-capas", timeout=5
    ) as respuesta:
        assert respuesta.status == 200
        return json.load(respuesta)


try:
    # 1. Publicar un mensaje.
    cuerpo = {
        "servicio": servicio,
        "estado": "operativo",
        "detalle": {"prueba": True},
    }

    peticion = urllib.request.Request(
        f"{base}/estado-capas",
        data=json.dumps(cuerpo).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(peticion, timeout=5) as respuesta:
        assert respuesta.status == 200

    print("OK | Publicar estado: HTTP 200")

    # 2. Comprobar el hash en Valkey.
    datos = estado_capas.cliente.hgetall(llave)
    assert datos["estado"] == "operativo"
    assert "ts" in datos
    print("OK | HSET salud:{servicio}")

    # 3. Comprobar que tiene TTL.
    ttl = estado_capas.cliente.ttl(llave)
    assert 0 < ttl <= 15
    print("OK | TTL de 15 segundos")

    # 4. Consultar desde la API.
    capas = consultar()["capas"]
    assert servicio in capas
    assert capas[servicio]["detalle"] == {"prueba": True}
    print("OK | Consultar estado: HTTP 200")

    # 5. Comprobar que una nueva publicacion renueva el TTL.
    estado_capas.cliente.expire(llave, 2)

    with urllib.request.urlopen(peticion, timeout=5) as respuesta:
        assert respuesta.status == 200

    assert estado_capas.cliente.ttl(llave) > 2
    print("OK | Nueva publicacion renueva TTL")

    # 6. Simular expiracion sin esperar 15 segundos.
    estado_capas.cliente.expire(llave, 1)
    time.sleep(1.2)

    assert servicio not in consultar()["capas"]
    print("OK | Capa vencida desaparece")

    print("B2.7_ESTADO_CAPAS_OK")

finally:
    # Limpieza: borra la llave de salud de la capa de prueba.
    estado_capas.cliente.delete(llave)
