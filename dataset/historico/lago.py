"""Escritura del lago para los generadores del histórico, con la misma convención que el historial.

Mismo esquema y mismo procedimiento que servicios/historial/app/lago.py (si se cambia uno,
cambiar los dos): cada archivo se escribe en datalake/_tmp/, fsync y rename atómico a
datalake/<conjunto>/mes=AAAA-MM/part-<micro_lote_id>.parquet.
"""
import os
import shutil
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

LAGO = Path(os.getenv("LAGO_DIR", "/datalake"))
TMP = LAGO / "_tmp"
CONJUNTOS = {"principal": "eventos", "grande": "eventos_grande", "prueba": "eventos_prueba"}

ESQUEMA = pa.schema([
    pa.field("event_id", pa.string(), nullable=False),
    pa.field("ts", pa.timestamp("ms", tz="UTC"), nullable=False),
    pa.field("tarea_id", pa.string(), nullable=False),
    pa.field("proyecto_id", pa.string(), nullable=False),
    pa.field("cliente_id", pa.string(), nullable=False),
    pa.field("batch_id", pa.string(), nullable=False),
    pa.field("modalidad", pa.string(), nullable=False),
    pa.field("tipo_tarea", pa.string(), nullable=False),
    pa.field("estado", pa.string(), nullable=False),
    pa.field("version", pa.int32(), nullable=False),
    pa.field("anotador_id", pa.string()),
    pa.field("equipo_id", pa.string()),
    pa.field("revisor_id", pa.string()),
    pa.field("motivo_rechazo", pa.string()),
    pa.field("tomada_ts", pa.timestamp("ms", tz="UTC")),
    pa.field("sla_horas", pa.int32(), nullable=False),
    pa.field("micro_lote_id", pa.string()),
])
CAMPOS = ESQUEMA.names


def vaciar(conjunto: str) -> Path:
    """Borra el conjunto completo (carga desde cero) y devuelve su carpeta."""
    base = LAGO / CONJUNTOS[conjunto]
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    return base


def escribir(columnas: dict, base: Path, mes: str, micro_lote_id: str) -> int:
    """columnas: dict campo -> lista; ts y tomada_ts en epoch ms (int o None)."""
    t = pa.table({c: pa.array(columnas[c], type=pa.int64() if c in ("ts", "tomada_ts") else ESQUEMA.field(c).type)
                  for c in CAMPOS})
    t = t.cast(ESQUEMA)  # int64 -> timestamp[ms, UTC]
    TMP.mkdir(parents=True, exist_ok=True)
    tmp = TMP / f"{micro_lote_id}__{mes}.parquet"
    with open(tmp, "wb") as f:
        pq.write_table(t, f, compression="snappy")
        f.flush()
        os.fsync(f.fileno())
    destino = base / f"mes={mes}" / f"part-{micro_lote_id}.parquet"
    destino.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, destino)
    return t.num_rows
