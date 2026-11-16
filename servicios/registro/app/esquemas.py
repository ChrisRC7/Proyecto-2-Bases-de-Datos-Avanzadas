"""Cuerpos de las peticiones del registro y reglas de validación (endpoints/registro.md)."""
import re
import uuid
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

Modalidad = Literal["texto", "imagen", "video", "lidar"]
TipoTarea = Literal["span", "bbox", "poligono", "bbox_video", "cuboide_3d"]
# "renovar" no cambia el estado: corre asignacion.vence_en junto con el lock de Valkey (latido/borrador).
Accion = Literal["enviar", "liberar", "empezar_revision", "aprobar", "rechazar", "renovar"]

MOTIVOS = {"ETIQUETA_INCORRECTA", "GEOMETRIA_IMPRECISA", "OBJETO_OMITIDO", "INSTRUCCION_NO_SEGUIDA", "OTRO"}

# Geometrías admitidas por modalidad (enunciado §6.1).
TIPOS_POR_MODALIDAD = {
    "texto": {"span"},
    "imagen": {"bbox", "poligono"},
    "video": {"bbox_video"},
    "lidar": {"cuboide_3d"},
}

# MIME aceptado por modalidad: un prefijo que termina en "/" o un valor exacto.
MIME_POR_MODALIDAD = {
    "texto": ("text/", "application/json"),
    "imagen": ("image/",),
    "video": ("video/",),
    "lidar": ("application/x-pcd", "application/vnd.las", "application/octet-stream"),
}

RE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
RE_URI = re.compile(r"^[a-z][a-z0-9+.-]*://\S+$")


def _uuid(valor: str) -> str:
    uuid.UUID(valor)
    return valor


Uuid = Annotated[str, AfterValidator(_uuid)]


def mime_valido(modalidad: str, mime: str) -> bool:
    return any(mime.startswith(p) if p.endswith("/") else mime == p for p in MIME_POR_MODALIDAD[modalidad])


class Etiqueta(BaseModel):
    etiqueta: str = Field(min_length=1)
    instruccion: str = Field(min_length=1)


class NuevoProyecto(BaseModel):
    cliente_id: Uuid
    nombre: str = Field(min_length=1)
    modalidad: Modalidad
    taxonomia: list[Etiqueta] = Field(min_length=1)
    sla_horas: int = Field(gt=0, le=720)
    prioridad: int = Field(default=3, ge=1, le=5)

    @field_validator("taxonomia")
    @classmethod
    def etiquetas_unicas(cls, v: list[Etiqueta]) -> list[Etiqueta]:
        nombres = [e.etiqueta for e in v]
        if len(set(nombres)) != len(nombres):
            raise ValueError("hay etiquetas repetidas en la taxonomía")
        return v


class Archivo(BaseModel):
    uri: str
    sha256: str
    bytes: int = Field(gt=0)
    mime: str = Field(min_length=3)
    meta: dict = Field(default_factory=dict)

    @field_validator("uri")
    @classmethod
    def uri_valida(cls, v: str) -> str:
        if not RE_URI.match(v):
            raise ValueError("la uri debe tener esquema, por ejemplo s3://...")
        return v

    @field_validator("sha256")
    @classmethod
    def sha256_valido(cls, v: str) -> str:
        if not RE_SHA256.match(v):
            raise ValueError("sha256 debe tener 64 caracteres hexadecimales en minúscula")
        return v


class NuevaTarea(BaseModel):
    tipo_tarea: TipoTarea
    prioridad: int | None = Field(default=None, ge=1, le=5)
    archivo: Archivo


class NuevoLote(BaseModel):
    tareas: list[NuevaTarea] = Field(min_length=1, max_length=5000)


class Asignar(BaseModel):
    anotador_id: Uuid
    proyecto_id: Uuid | None = None
    token: Uuid


class Anotacion(BaseModel):
    etiquetas: list[str] = Field(min_length=1)
    geometria: dict


class Transicion(BaseModel):
    accion: Accion
    token: Uuid | None = None
    version: int | None = None
    actor_id: Uuid
    anotacion: Anotacion | None = None
    motivo: str | None = None

    @model_validator(mode="after")
    def reglas_por_accion(self) -> "Transicion":
        if self.accion in ("enviar", "liberar", "renovar") and not self.token:
            raise ValueError(f"'{self.accion}' requiere el token de asignación")
        if self.accion == "enviar" and self.anotacion is None:
            raise ValueError("'enviar' requiere la anotación")
        if self.accion == "rechazar" and self.motivo not in MOTIVOS:
            raise ValueError(f"'rechazar' requiere un motivo válido: {sorted(MOTIVOS)}")
        return self


def _numeros(v, n: int | None = None) -> bool:
    return isinstance(v, list) and (n is None or len(v) == n) and all(isinstance(x, (int, float)) for x in v)


def error_geometria(tipo: str, g: dict) -> str | None:
    """Devuelve el problema de la geometría o None si es válida para el tipo de tarea."""
    if g.get("tipo") != tipo:
        return f"geometria.tipo debe ser '{tipo}'"
    if tipo == "span":
        ok = isinstance(g.get("inicio"), int) and isinstance(g.get("fin"), int) and 0 <= g["inicio"] < g["fin"]
        return None if ok else "span requiere enteros 0 <= inicio < fin"
    if tipo == "bbox":
        ok = all(isinstance(g.get(k), (int, float)) for k in "xywh") and g["w"] > 0 and g["h"] > 0
        return None if ok else "bbox requiere x, y, w > 0 y h > 0"
    if tipo == "poligono":
        puntos = g.get("puntos")
        ok = isinstance(puntos, list) and len(puntos) >= 3 and all(_numeros(p, 2) for p in puntos)
        return None if ok else "poligono requiere al menos 3 puntos [x, y]"
    if tipo == "bbox_video":
        cuadros = g.get("cuadros")
        ok = isinstance(cuadros, list) and len(cuadros) > 0 and all(
            isinstance(c, dict) and all(isinstance(c.get(k), (int, float)) for k in ("t", "x", "y", "w", "h"))
            for c in cuadros
        )
        return None if ok else "bbox_video requiere cuadros [{t, x, y, w, h}]"
    if tipo == "cuboide_3d":
        ok = (_numeros(g.get("centro"), 3) and _numeros(g.get("dimensiones"), 3)
              and all(d > 0 for d in g["dimensiones"]) and isinstance(g.get("yaw"), (int, float)))
        return None if ok else "cuboide_3d requiere centro [3], dimensiones [3] > 0 y yaw"
    return f"tipo de geometría desconocido: {tipo}"
