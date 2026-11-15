
"""Pruebas de B4.2 sin servicios reales."""

from unittest.mock import Mock

from b42_borradores import guardar_periodicamente, recuperar


def pruebas():
    cliente = Mock()
    cliente.put.return_value.status_code = 200
    cliente.post.return_value.status_code = 200

    resultado = guardar_periodicamente(
        cliente=cliente,
        tarea_id="tarea-prueba",
        anotador_id="anotador-prueba",
        token="token-prueba",
        contenido={"etiquetas": ["lidar#ciclista"]},
        duracion=0.16,
        intervalo=0.05,
        interrumpir=True,
    )

    assert resultado["estado"] == "interrumpido"
    assert resultado["borradores"] == 3
    assert cliente.put.call_count == 3
    assert cliente.post.call_count == 3
    print("OK | Tres latidos de sesion")

    print("OK | Tres borradores periodicos")
    print("OK | Interrupcion sin enviar anotacion")

    cliente.post.return_value.status_code = 200
    cliente.post.return_value.json.return_value = {
        "token": "token-prueba",
        "borrador": {
            "contenido": {"etiquetas": ["lidar#ciclista"]},
        },
    }

    codigo, datos = recuperar(cliente, "anotador-prueba")
    cliente.post.reset_mock()

    assert codigo == 200
    assert datos["token"] == "token-prueba"
    assert datos["borrador"]["contenido"]["etiquetas"] == [
        "lidar#ciclista"
    ]

    print("OK | Recuperacion del ultimo borrador")
    print("B4.2_BORRADORES_OK")


if __name__ == "__main__":
    pruebas()
