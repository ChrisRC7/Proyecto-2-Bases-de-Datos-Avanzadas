
"""Pruebas de B4.2 sin servicios reales.

Usa un cliente HTTP falso (Mock) para comprobar que guardar_periodicamente hace un borrador y
un latido por intervalo, y que recuperar devuelve el último borrador.

    cd pruebas && python3 prueba_b42.py
"""

from unittest.mock import Mock

from b42_borradores import guardar_periodicamente, recuperar


def pruebas():
    """Ejecuta los casos e imprime B4.2_BORRADORES_OK si todo pasa."""
    # Cliente falso: todos los PUT y POST responden 200.
    cliente = Mock()
    cliente.put.return_value.status_code = 200
    cliente.post.return_value.status_code = 200

    # 0.16 s con intervalo de 0.05 s: deben salir 3 borradores y 3 latidos.
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

    # Respuesta simulada de /sesiones/recuperar con el borrador.
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
