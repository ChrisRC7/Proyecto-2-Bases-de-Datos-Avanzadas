"""B5.2 complemento: con latidos, la asignación no vence en el registro aunque pasen más de 120 s.

El lock de Valkey se renueva con cada latido o borrador; `asignacion.vence_en` en MongoDB también
(gateway → `registro:/tareas/{id}/transicion` con `accion: "renovar"`, que solo escribe cuando queda
menos de la mitad del plazo).
La prueba toma la única tarea de un proyecto propio, manda latidos cada 10 s durante 150 s y comprueba:
otro anotador recibe 204 en todo ese tiempo, el registro corrió `vence_en` y el envío final funciona.
Tarda unos 2,5 min.

    python3 pruebas/b52_renovacion_registro.py
"""
import json
import sys
import time
import urllib.error
import urllib.request

from proyecto_prueba import GATEWAY, anotadores, crear_proyecto

# Duración total de la prueba, intervalo entre latidos y geometría válida para enviar.
DURACION_S = 150
CADA_S = 10
GEOMETRIA = {"tipo": "cuboide_3d", "centro": [1.2, 3.4, 0.5], "dimensiones": [0.6, 1.8, 1.7], "yaw": 0.3}


def pedir(anotador, metodo, ruta, cuerpo=None):
    """Petición al gateway como `anotador`; devuelve (status, JSON)."""
    datos = None if cuerpo is None else json.dumps(cuerpo).encode("utf-8")
    peticion = urllib.request.Request(GATEWAY + ruta, data=datos, method=metodo,
                                      headers={"X-Anotador-Id": anotador, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(peticion, timeout=15) as respuesta:
            texto = respuesta.read().decode("utf-8")
            return respuesta.status, json.loads(texto) if texto else None
    except urllib.error.HTTPError as exc:
        texto = exc.read().decode("utf-8")
        return exc.code, json.loads(texto) if texto else None


def main():
    """A toma la tarea y manda latidos 150 s; B nunca la recibe; al final A la envía con éxito."""
    a, b = anotadores(2)
    proyecto = crear_proyecto("b52-renovacion")
    estado, datos = pedir(a, "POST", "/tareas/siguiente", {"proyecto_id": proyecto})
    assert estado == 200, (estado, datos)
    tarea, token, vence_inicial = datos["tarea"]["tarea_id"], datos["token"], datos["vence_en"]
    print(f"ASIGNADA tarea={tarea} vence_en={vence_inicial}", flush=True)

    renovaciones = 0
    try:
        inicio = time.monotonic()
        while time.monotonic() - inicio < DURACION_S:
            time.sleep(CADA_S)
            estado, datos = pedir(a, "POST", "/sesiones/latido", {"token": token})
            assert estado == 200, ("latido", estado, datos)
            renovaciones += bool(datos["sesion"].get("registro_renovado"))
            estado_b, datos_b = pedir(b, "POST", "/tareas/siguiente", {"proyecto_id": proyecto})
            assert estado_b == 204, ("la tarea se ofreció a otro", estado_b, datos_b)
            print(f"t={time.monotonic() - inicio:5.1f}s latido 200 · registro_renovado="
                  f"{datos['sesion'].get('registro_renovado')} · otro anotador 204", flush=True)

        # El registro debe haber extendido vence_en al menos dos veces en 150 s.
        assert renovaciones >= 2, f"vence_en se renovó solo {renovaciones} veces en {DURACION_S} s"
        print(f"RENOVACIONES_EN_REGISTRO={renovaciones} (escritura solo con menos de 60 s de plazo)")
        estado, datos = pedir(a, "POST", f"/tareas/{tarea}/enviar",
                              {"token": token, "anotacion": {"etiquetas": ["lidar#ciclista"], "geometria": GEOMETRIA}})
        assert estado == 200, ("envío tras 150 s", estado, datos)
        tarea = None
        print(f"ENVIO_TRAS_{DURACION_S}_S_HTTP_200")
        print("B5_2_RENOVACION_REGISTRO_OK")
    finally:
        if tarea:
            print("LIMPIEZA", pedir(a, "POST", f"/tareas/{tarea}/liberar", {"token": token}))


if __name__ == "__main__":
    sys.exit(main())
