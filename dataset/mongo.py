"""Conexión a la BD plataforma e índices, compartidos por la carga y la regeneración de tareas."""
import os

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

MONGO_URI = os.getenv("MONGO_URI", "mongodb://mongo1:27017,mongo2:27017,mongo3:27017/?replicaSet=rs0")
MONGO_DB = os.getenv("MONGO_DB", "plataforma")

# Mismos índices que crea el registro al arrancar (servicios/registro/app/bd.py y
# endpoints/registro.md). Si se cambia uno, cambiar los dos.
INDICES = {
    "tareas": [
        [("proyecto_id", ASCENDING), ("estado", ASCENDING), ("prioridad", DESCENDING)],
        [("asignacion.anotador_id", ASCENDING), ("estado", ASCENDING)],
        [("batch_id", ASCENDING)],
    ],
    "proyectos": [
        [("cliente_id", ASCENDING), ("estado", ASCENDING)],
    ],
}


def conectar() -> Database:
    return MongoClient(MONGO_URI, w="majority", tz_aware=True)[MONGO_DB]


def crear_indices(bd: Database) -> list[str]:
    return [bd[col].create_index(claves) for col, lista in INDICES.items() for claves in lista]


def insertar_por_lotes(coleccion, docs: list[dict], lote: int = 5000) -> int:
    total = 0
    for i in range(0, len(docs), lote):
        total += len(coleccion.insert_many(docs[i:i + lote], ordered=False).inserted_ids)
    return total
