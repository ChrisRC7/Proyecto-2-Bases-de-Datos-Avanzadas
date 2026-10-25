"""Validación del evento que llega a POST /eventos (una fila del lago, endpoints/historial.md)."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

TIPOS_POR_MODALIDAD = {
    "texto": {"span"},
    "imagen": {"bbox", "poligono"},
    "video": {"bbox_video"},
    "lidar": {"cuboide_3d"},
}
MOTIVOS = {"ETIQUETA_INCORRECTA", "GEOMETRIA_IMPRECISA", "OBJETO_OMITIDO", "INSTRUCCION_NO_SEGUIDA", "OTRO"}


def _iso(t: datetime | None) -> str | None:
    if t is None:
        return None
    t = t.astimezone(timezone.utc)
    return f"{t:%Y-%m-%dT%H:%M:%S}.{t.microsecond // 1000:03d}Z"


class Evento(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    ts: datetime
    tarea_id: str
    proyecto_id: str
    cliente_id: str
    batch_id: str
    modalidad: Literal["texto", "imagen", "video", "lidar"]
    tipo_tarea: Literal["span", "bbox", "poligono", "bbox_video", "cuboide_3d"]
    estado: Literal["abierta", "anotada", "en_revision", "aprobada", "rechazada", "liberada"]
    version: int
    anotador_id: str | None = None
    equipo_id: str | None = None
    revisor_id: str | None = None
    motivo_rechazo: str | None = None
    tomada_ts: datetime | None = None
    sla_horas: int
    micro_lote_id: str | None = None  # lo llena el historial; si viene, se ignora

    @field_validator("ts", "tomada_ts")
    @classmethod
    def con_zona(cls, t: datetime | None) -> datetime | None:
        # Sin zona se toma como UTC (todas las fechas del proyecto son UTC).
        return t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t

    @model_validator(mode="after")
    def coherente(self):
        if self.tipo_tarea not in TIPOS_POR_MODALIDAD[self.modalidad]:
            raise ValueError(f"tipo_tarea '{self.tipo_tarea}' no corresponde a '{self.modalidad}'")
        if self.estado == "rechazada" and self.motivo_rechazo not in MOTIVOS:
            raise ValueError("una fila rechazada necesita un motivo_rechazo válido")
        if self.version < 1 or self.sla_horas < 1:
            raise ValueError("version y sla_horas deben ser positivos")
        return self

    def fila(self) -> dict:
        """Dict serializable a JSON con las fechas en ISO-8601 UTC (como las manda el registro)."""
        datos = self.model_dump()
        datos["event_id"] = str(self.event_id)
        datos["ts"] = _iso(self.ts)
        datos["tomada_ts"] = _iso(self.tomada_ts)
        datos["micro_lote_id"] = None
        return datos
