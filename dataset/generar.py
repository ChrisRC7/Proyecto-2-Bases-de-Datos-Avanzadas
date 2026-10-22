"""Generador reproducible del dataset operacional (MongoDB, BD plataforma).

Con la misma semilla produce exactamente los mismos documentos e identificadores, así que
cargar desde cero o regenerar las tareas deja la base igual que la primera vez.

Volúmenes (enunciado §9): 60 clientes, 120 proyectos activos (+ 60 cerrados que solo existen
en el historial), 600 batches, 60 000 tareas (30 % texto, 30 % imagen, 20 % video, 20 % LiDAR),
300 anotadores en 30 equipos, 10 % revisores.
"""
import random
import uuid
from datetime import datetime, timedelta, timezone

SEMILLA = 2026
CLIENTES = 60
EQUIPOS = 30
ANOTADORES_POR_EQUIPO = 10  # el primero de cada equipo es revisor: 30 de 300 = 10 %
BATCHES_POR_PROYECTO = 5
TAREAS_POR_BATCH = 100
BASE = datetime(2026, 10, 1, tzinfo=timezone.utc)

# 120 proyectos activos y 60 cerrados con la proporción 30/30/20/20 de las tareas.
MODALIDADES_ACTIVOS = ["texto"] * 36 + ["imagen"] * 36 + ["video"] * 24 + ["lidar"] * 24
MODALIDADES_CERRADOS = ["texto"] * 18 + ["imagen"] * 18 + ["video"] * 12 + ["lidar"] * 12

TAXONOMIAS = {
    "texto": [
        ("texto#persona", "Nombre propio de una persona."),
        ("texto#lugar", "Ciudad, país o dirección."),
        ("texto#organizacion", "Empresa, institución o equipo."),
        ("texto#fecha", "Fecha u hora escrita en el texto."),
    ],
    "imagen": [
        ("imagen#peaton", "Persona de pie o caminando."),
        ("imagen#vehiculo", "Automóvil, bus, camión o motocicleta."),
        ("imagen#animal", "Cualquier animal visible."),
        ("imagen#senal", "Señal de tránsito vertical."),
    ],
    "video": [
        ("video#peaton", "Persona a pie durante todo el tramo."),
        ("video#vehiculo", "Automóvil, bus o camión en movimiento."),
        ("video#ciclista", "Persona en bicicleta o patineta."),
    ],
    # Taxonomía del proyecto LiDAR del enunciado.
    "lidar": [
        ("lidar#peaton", "Persona a pie."),
        ("lidar#vehiculo", "Automóvil, bus o camión."),
        ("lidar#ciclista", "Un skater se clasifica como ciclista, no como peatón."),
        ("lidar#signal", "Señal de tránsito vertical."),
    ],
}


def _archivo(rng: random.Random, modalidad: str, tipo: str, carpeta: str, n: int) -> dict:
    sha256 = f"{rng.getrandbits(256):064x}"
    if modalidad == "texto":
        b = rng.randint(2_000, 60_000)
        return {"uri": f"s3://datos/texto/{carpeta}/{n:06d}.txt", "sha256": sha256, "bytes": b,
                "mime": "text/plain", "meta": {"idioma": "es", "caracteres": b}}
    if modalidad == "imagen":
        png = rng.random() < 0.2
        ancho = rng.choice([1280, 1920, 3840])
        return {"uri": f"s3://datos/imagen/{carpeta}/{n:06d}.{'png' if png else 'jpg'}", "sha256": sha256,
                "bytes": rng.randint(200_000, 5_000_000), "mime": "image/png" if png else "image/jpeg",
                "meta": {"ancho": ancho, "alto": ancho * 9 // 16}}
    if modalidad == "video":
        return {"uri": f"s3://datos/video/{carpeta}/{n:06d}.mp4", "sha256": sha256,
                "bytes": rng.randint(5_000_000, 200_000_000), "mime": "video/mp4",
                "meta": {"duracion_s": rng.randint(10, 120), "fps": rng.choice([24, 30, 60])}}
    return {"uri": f"s3://datos/lidar/{carpeta}/escena-{n:06d}.pcd", "sha256": sha256,
            "bytes": rng.randint(1_000_000, 15_000_000), "mime": "application/x-pcd",
            "meta": {"min": 0.0, "max": round(rng.uniform(60, 150), 1), "puntos": rng.randint(50_000, 300_000)}}


def _tipo(rng: random.Random, modalidad: str) -> str:
    if modalidad == "imagen":
        return "bbox" if rng.random() < 0.7 else "poligono"
    return {"texto": "span", "video": "bbox_video", "lidar": "cuboide_3d"}[modalidad]


def generar(semilla: int = SEMILLA) -> dict:
    """Devuelve todas las colecciones y los proyectos cerrados (estos no van a MongoDB)."""
    rng = random.Random(semilla)

    def nuevo_id() -> str:
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))

    clientes = [{"_id": nuevo_id(), "nombre": f"Cliente {i + 1:02d}", "creado_en": BASE - timedelta(days=400 - i)}
                for i in range(CLIENTES)]

    equipos = [nuevo_id() for _ in range(EQUIPOS)]
    anotadores = []
    for e, equipo_id in enumerate(equipos):
        for k in range(ANOTADORES_POR_EQUIPO):
            n = e * ANOTADORES_POR_EQUIPO + k + 1
            anotadores.append({"_id": nuevo_id(), "nombre": f"Anotador {n:03d}", "equipo_id": equipo_id,
                               "rol": "revisor" if k == 0 else "anotador"})

    def proyecto(i: int, modalidad: str, estado: str, cliente_id: str) -> dict:
        ejemplo = estado == "activo" and i == 0  # el proyecto LiDAR del enunciado
        return {
            "_id": nuevo_id(),
            "cliente_id": cliente_id,
            "nombre": "LiDAR urbano 2026" if ejemplo else f"Proyecto {modalidad} {estado[0].upper()}{i + 1:03d}",
            "estado": estado,
            "modalidad": modalidad,
            "taxonomia": [{"etiqueta": e, "instruccion": t} for e, t in TAXONOMIAS[modalidad]],
            "sla_horas": 48 if ejemplo else rng.choice([24, 48, 72]),
            "prioridad": 3 if ejemplo else rng.randint(1, 5),
            "creado_en": BASE - timedelta(days=rng.randint(1, 30) if estado == "activo" else rng.randint(60, 360)),
        }

    # El ejemplo LiDAR va primero; el resto de modalidades se mezcla con la semilla.
    resto = MODALIDADES_ACTIVOS.copy()
    resto.remove("lidar")
    rng.shuffle(resto)
    activos = [proyecto(i, m, "activo", clientes[i % CLIENTES]["_id"]) for i, m in enumerate(["lidar"] + resto)]
    cerrados_mod = MODALIDADES_CERRADOS.copy()
    rng.shuffle(cerrados_mod)
    cerrados = [proyecto(i, m, "cerrado", clientes[i]["_id"]) for i, m in enumerate(cerrados_mod)]

    batches, tareas = [], []
    for p in activos:
        carpeta = p["_id"][:8]
        for b in range(BATCHES_POR_PROYECTO):
            batch = {"_id": nuevo_id(), "proyecto_id": p["_id"], "total_tareas": TAREAS_POR_BATCH,
                     "creado_en": p["creado_en"] + timedelta(hours=b)}
            batches.append(batch)
            for t in range(TAREAS_POR_BATCH):
                tipo = _tipo(rng, p["modalidad"])
                tareas.append({
                    "_id": nuevo_id(),
                    "proyecto_id": p["_id"],
                    "cliente_id": p["cliente_id"],
                    "batch_id": batch["_id"],
                    "modalidad": p["modalidad"],
                    "tipo_tarea": tipo,
                    "prioridad": p["prioridad"],
                    "sla_horas": p["sla_horas"],
                    "archivo": _archivo(rng, p["modalidad"], tipo, carpeta, b * TAREAS_POR_BATCH + t + 1),
                    "estado": "disponible",
                    "version": 0,
                    "asignacion": None,
                    "anotacion": None,
                    "ultima_revision": None,
                    "ultimo_evento_id": None,
                })

    return {"clientes": clientes, "anotadores": anotadores, "proyectos": activos, "batches": batches,
            "tareas": tareas, "cerrados": cerrados, "equipos": equipos}


def ids(datos: dict, semilla: int = SEMILLA) -> dict:
    """IDs que el histórico de C necesita (dataset/salida/ids.json)."""
    def resumen(p: dict) -> dict:
        return {"id": p["_id"], "cliente_id": p["cliente_id"], "nombre": p["nombre"],
                "modalidad": p["modalidad"], "sla_horas": p["sla_horas"],
                "etiquetas": [e["etiqueta"] for e in p["taxonomia"]]}
    return {
        "semilla": semilla,
        "clientes": [c["_id"] for c in datos["clientes"]],
        "equipos": datos["equipos"],
        "anotadores": [{"id": a["_id"], "equipo_id": a["equipo_id"], "rol": a["rol"]} for a in datos["anotadores"]],
        "proyecto_lidar_ejemplo": datos["proyectos"][0]["_id"],
        "proyectos_activos": [resumen(p) for p in datos["proyectos"]],
        "proyectos_cerrados": [resumen(p) for p in datos["cerrados"]],
    }
