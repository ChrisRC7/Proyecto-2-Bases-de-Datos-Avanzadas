"""Proyecto LiDAR propio con una sola tarea, creado por el gateway, para las pruebas de sesión de B.

Las pruebas de B5.2, B5.3 y B4.2 necesitan saber qué tarea van a recibir. Con un proyecto de una sola
tarea, el primer `/tareas/siguiente` con ese `proyecto_id` la devuelve, sin depender de ids creados a
mano ni del estado de la cola global. Solo biblioteca estándar; corre en el anfitrión o en un contenedor.

    from proyecto_prueba import crear_proyecto
    PROYECTO = crear_proyecto("b52")
"""
import json
import os
import urllib.request
import uuid

# URL del gateway y lugares donde se busca ids.json (variable de entorno, repo o contenedor).
GATEWAY = os.getenv("GATEWAY_URL", "http://localhost:8000")
RUTAS_IDS = [os.getenv("IDS_JSON", ""), "dataset/salida/ids.json", "/srv/ids/ids.json"]


def _ids() -> dict:
    """Lee el primer ids.json que exista en RUTAS_IDS; si no hay ninguno, termina con un mensaje."""
    for ruta in RUTAS_IDS:
        if ruta and os.path.exists(ruta):
            with open(ruta, encoding="utf-8") as archivo:
                return json.load(archivo)
    raise SystemExit("No se encontró ids.json: cargar el dataset (README, «Carga de datos»)")


def _post(gateway: str, ruta: str, cuerpo: dict) -> dict:
    """POST JSON al gateway y devuelve la respuesta ya decodificada (lanza excepción si no es 2xx)."""
    peticion = urllib.request.Request(
        gateway + ruta, data=json.dumps(cuerpo).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(peticion, timeout=15) as respuesta:
        return json.loads(respuesta.read().decode("utf-8"))


def anotadores(n: int = 1) -> list[str]:
    """Los primeros `n` anotadores (rol anotador) del dataset."""
    return [a["id"] for a in _ids()["anotadores"] if a["rol"] == "anotador"][:n]


def crear_proyecto(prefijo: str, gateway: str | None = None) -> str:
    """Crea un proyecto con una tarea `cuboide_3d` y devuelve su id."""
    gateway = gateway or GATEWAY
    ids = _ids()
    # Sufijo aleatorio para que el nombre y la uri no se repitan entre corridas.
    sufijo = uuid.uuid4().hex[:6]
    proyecto_id = _post(gateway, "/proyectos", {
        "cliente_id": ids["clientes"][0], "nombre": f"LiDAR prueba {prefijo} {sufijo}", "modalidad": "lidar",
        "taxonomia": [{"etiqueta": "lidar#ciclista", "instruccion": "Un skater se clasifica como ciclista."}],
        "sla_horas": 48, "prioridad": 3,
    })["proyecto_id"]
    # Lote con una única tarea LiDAR.
    _post(gateway, f"/proyectos/{proyecto_id}/lotes", {"tareas": [{
        "tipo_tarea": "cuboide_3d", "prioridad": 5,
        "archivo": {"uri": f"s3://datos/lidar/{prefijo}-{sufijo}.pcd", "sha256": uuid.uuid4().hex * 2,
                    "bytes": 4823112, "mime": "application/x-pcd",
                    "meta": {"min": 0.0, "max": 120.5, "puntos": 120000}},
    }]})
    return proyecto_id
