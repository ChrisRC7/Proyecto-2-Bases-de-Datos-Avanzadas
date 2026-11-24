"""Conexión a MongoDB (replica set rs0) e índices del registro.

Define el cliente de MongoDB compartido por el servicio, la base `bd`, los índices que
necesita la colección de tareas y una función para conocer la topología del replica set
(quién es primario) sin hacer consultas extra.
"""
import os

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

# Dirección del replica set de 3 nodos y nombre de la base de datos.
MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo1:27017,mongo2:27017,mongo3:27017/?replicaSet=rs0")
MONGO_DB = os.getenv("MONGO_DB", "plataforma")
# "majority" en operación normal; "1" solo para la comparación de A7.4.
MONGO_W = os.getenv("MONGO_W", "majority")

# Timeouts cortos (A2.10): sin primario el registro responde 503 en unos 2 s en vez de
# quedarse esperando, y el gateway puede contestar rápido. wtimeout acota la espera de la
# mayoría cuando el primario todavía no se dio cuenta de que la perdió.
# Cliente único del servicio. w = write concern (cuántos nodos deben confirmar cada escritura).
cliente = MongoClient(
    MONGO_URI,
    w=int(MONGO_W) if MONGO_W.isdigit() else MONGO_W,
    wtimeoutMS=int(os.getenv("MONGO_WTIMEOUT_MS", "5000")),
    serverSelectionTimeoutMS=int(os.getenv("MONGO_SELECCION_MS", "2000")),
    connectTimeoutMS=2000,
    socketTimeoutMS=10000,
    heartbeatFrequencyMS=2000,
    retryWrites=True,  # reintenta una vez las escrituras si cambia el primario
    tz_aware=True,  # las fechas leídas de Mongo vuelven con zona horaria (UTC)
)
# Base de datos del registro (colecciones clientes, proyectos, batches, tareas, anotadores).
bd: Database = cliente[MONGO_DB]

# Índices mínimos del enunciado + el extra justificado con explain (endpoints/registro.md).
INDICES = {
    "tareas": [
        [("proyecto_id", ASCENDING), ("estado", ASCENDING), ("prioridad", DESCENDING)],
        [("asignacion.anotador_id", ASCENDING), ("estado", ASCENDING)],
        [("batch_id", ASCENDING)],
        # Extra (A3.3): asignación sin proyecto_id. Sin él es un COLLSCAN de las 60 000 tareas.
        [("estado", ASCENDING), ("prioridad", DESCENDING)],
    ],
    "proyectos": [
        [("cliente_id", ASCENDING), ("estado", ASCENDING)],
    ],
}


def crear_indices() -> list[str]:
    """Crea (si no existen) todos los índices de INDICES y devuelve sus nombres.

    create_index es idempotente: si el índice ya existe no hace nada.
    """
    return [bd[col].create_index(claves) for col, lista in INDICES.items() for claves in lista]


def estado_mongo() -> dict:
    """Topología que el driver ya conoce por sus latidos; no hace una consulta nueva."""
    # Descripción de cada nodo según el último latido del driver.
    servidores = cliente.topology_description.server_descriptions().values()
    primario = next(
        (f"{s.address[0]}:{s.address[1]}" for s in servidores if s.server_type_name == "RSPrimary"),
        None,
    )
    # Sin primario no se puede escribir: el registro se considera caído.
    return {
        "estado": "operativo" if primario else "caido",
        "primario": primario,
        "secundarios": sum(1 for s in servidores if s.server_type_name == "RSSecondary"),
    }
