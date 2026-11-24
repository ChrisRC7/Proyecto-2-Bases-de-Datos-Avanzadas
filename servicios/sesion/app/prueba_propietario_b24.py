
"""Prueba de protección de propietario de borradores - B2.4.

Comprueba directamente sobre Valkey (sin HTTP) que, si la tarea pasa a otro token y
otro anotador, este no puede sobrescribir el borrador del primero.
Imprime B2.4_PROPIETARIO_OK si todo pasa.
"""

import uuid

from app import borradores, locks

# Tarea y tokens únicos de la corrida.
identificador = uuid.uuid4().hex
tarea = f"prueba-propietario-{identificador}"
token_a = f"token-a-{identificador}"
token_b = f"token-b-{identificador}"

try:
    # El primer anotador adquiere el lock y guarda su borrador.
    assert locks.adquirir(tarea, token_a)
    assert borradores.guardar(
        tarea, "anotador-a", token_a, {"etiqueta": "gato"}
    )
    print("OK | Primer anotador guarda borrador")

    # Se libera el lock, pero se conserva el borrador.
    assert locks.soltar(tarea, token_a)
    assert locks.adquirir(tarea, token_b)
    print("OK | Tarea reasignada con otro token")

    # El segundo anotador no debe sobrescribir el borrador.
    resultado = borradores.guardar(
        tarea, "anotador-b", token_b, {"etiqueta": "perro"}
    )
    assert resultado == "otro_anotador"
    print("OK | Sobrescritura de otro anotador rechazada")

    # Comprobar que el contenido original permanece intacto.
    original = borradores.recuperar(tarea)
    assert original["anotador_id"] == "anotador-a"
    assert original["contenido"] == {"etiqueta": "gato"}
    print("OK | Borrador original conservado")

    print("B2.4_PROPIETARIO_OK")

finally:
    # Limpieza: borra lock y borrador de prueba.
    locks.cliente.delete(
        f"lock:tarea:{tarea}",
        f"borrador:{tarea}",
    )
