
"""B5.1: borradores, latidos y recuperacion desde Gateway real."""

import json
import os
import uuid
import urllib.error
import urllib.request
from pathlib import Path


GATEWAY = os.getenv("GATEWAY_URL", "http://localhost:8000")
RAIZ = Path(__file__).resolve().parents[1]
IDS = RAIZ / "dataset" / "salida" / "ids.json"


def llamar(metodo, ruta, cuerpo=None, anotador=None):
    datos = (
        json.dumps(cuerpo).encode("utf-8")
        if cuerpo is not None else None
    )
    headers = {"Content-Type": "application/json"}

    if anotador:
        headers["X-Anotador-Id"] = anotador

    solicitud = urllib.request.Request(
        GATEWAY + ruta,
        data=datos,
        headers=headers,
        method=metodo,
    )

    try:
        with urllib.request.urlopen(solicitud, timeout=15) as r:
            texto = r.read()
            return r.status, json.loads(texto) if texto else {}
    except urllib.error.HTTPError as e:
        texto = e.read()
        return e.code, json.loads(texto) if texto else {}


def comprobar(nombre, esperado, metodo, ruta, cuerpo=None, anotador=None):
    codigo, datos = llamar(metodo, ruta, cuerpo, anotador)

    if codigo != esperado:
        raise AssertionError(
            f"FALLA | {nombre}: esperado {esperado}, "
            f"obtenido {codigo}, respuesta={datos}"
        )

    print(f"OK | {nombre}: HTTP {codigo}", flush=True)
    return datos


def main():
    ids = json.loads(IDS.read_text(encoding="utf-8"))

    # No utilizar una sesion activa de otro caso.
    anotador = None
    for candidato in ids["anotadores"]:
        if candidato["rol"] != "anotador":
            continue

        codigo, _ = llamar(
            "POST", "/sesiones/recuperar",
            anotador=candidato["id"],
        )

        if codigo == 404:
            anotador = candidato["id"]
            break

        if codigo != 200:
            raise RuntimeError(
                f"Error al consultar sesion: HTTP {codigo}"
            )

    if anotador is None:
        raise RuntimeError(
            "No hay anotadores sin sesion activa. "
            "No se modificaron tareas."
        )

    print(f"ANOTADOR={anotador}", flush=True)

    proyecto = comprobar(
        "Crear proyecto aislado",
        201,
        "POST",
        "/proyectos",
        {
            "cliente_id": ids["clientes"][0],
            "nombre": f"B5.1 sesiones {uuid.uuid4().hex[:8]}",
            "modalidad": "lidar",
            "sla_horas": 48,
            "taxonomia": [
                {
                    "etiqueta": "lidar#ciclista",
                    "instruccion": "Prueba controlada B5.1",
                }
            ],
        },
    )["proyecto_id"]

    comprobar(
        "Crear tarea aislada",
        201,
        "POST",
        f"/proyectos/{proyecto}/lotes",
        {
            "tareas": [
                {
                    "tipo_tarea": "cuboide_3d",
                    "archivo": {
                        "uri": f"s3://pruebas/b51/{uuid.uuid4()}.pcd",
                        "sha256": uuid.uuid4().hex * 2,
                        "bytes": 1000,
                        "mime": "application/x-pcd",
                        "meta": {"min": 0, "max": 100},
                    },
                }
            ]
        },
    )

    asignacion = comprobar(
        "Asignar tarea",
        200,
        "POST",
        "/tareas/siguiente",
        {"proyecto_id": proyecto},
        anotador,
    )

    tarea = asignacion["tarea"]["tarea_id"]
    token = asignacion["token"]
    token_falso = str(uuid.uuid4())
    contenido = {"avance": 55, "origen": "B5.1"}

    print(f"PROYECTO={proyecto}", flush=True)
    print(f"TAREA={tarea}", flush=True)

    try:
        comprobar(
            "Borrador con token incorrecto",
            409,
            "PUT",
            f"/tareas/{tarea}/borrador",
            {"token": token_falso, "contenido": contenido},
            anotador,
        )

        comprobar(
            "Guardar borrador",
            200,
            "PUT",
            f"/tareas/{tarea}/borrador",
            {"token": token, "contenido": contenido},
            anotador,
        )

        comprobar(
            "Latido con token incorrecto",
            409,
            "POST",
            "/sesiones/latido",
            {"token": token_falso},
            anotador,
        )

        comprobar(
            "Latido valido",
            200,
            "POST",
            "/sesiones/latido",
            {"token": token},
            anotador,
        )

        recuperada = comprobar(
            "Recuperar sesion",
            200,
            "POST",
            "/sesiones/recuperar",
            anotador=anotador,
        )

        assert recuperada["tarea"]["tarea_id"] == tarea
        assert recuperada["token"] == token
        assert recuperada["borrador"]["contenido"] == contenido

        print("OK | Tarea, token y borrador coinciden", flush=True)
        print("B5_1_SESIONES_OK", flush=True)

    finally:
        codigo, datos = llamar(
            "POST",
            f"/tareas/{tarea}/liberar",
            {"token": token},
            anotador,
        )
        print(
            f"LIMPIEZA | Liberar tarea: HTTP {codigo} {datos}",
            flush=True,
        )
        if codigo != 200:
            raise RuntimeError(
                "No se pudo liberar la tarea; revisar estado."
            )


if __name__ == "__main__":
    main()
