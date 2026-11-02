"""Lago Parquet: esquema del evento, layout por mes y publicación atómica (endpoints/historial.md).

Cada archivo se escribe completo en datalake/_tmp/, se hace fsync y se publica con os.replace en
datalake/eventos/mes=AAAA-MM/part-<micro_lote_id>.parquet. El rename es atómico dentro del mismo
sistema de archivos, así que Spark ve el archivo entero o no lo ve; _tmp/ empieza con "_" y además
está fuera de eventos/, por eso Spark nunca lo lista.
"""
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

LAGO = Path(os.getenv("LAGO_DIR", "/datalake"))
TMP = LAGO / "_tmp"
EVENTOS = LAGO / "eventos"

# Mismo esquema que dataset/historico/lago.py; si se cambia uno, cambiar los dos.
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


def a_fecha(valor) -> datetime | None:
    """ISO-8601 (con Z o con offset) a datetime en UTC; sin zona se toma como UTC."""
    if valor is None or isinstance(valor, datetime):
        t = valor
    else:
        t = datetime.fromisoformat(valor)
    if t is not None and t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t


def tabla(eventos: list[dict], micro_lote_id: str) -> pa.Table:
    columnas = {c: [] for c in CAMPOS}
    for e in eventos:
        for c in CAMPOS:
            columnas[c].append(e.get(c))
    columnas["ts"] = [a_fecha(v) for v in columnas["ts"]]
    columnas["tomada_ts"] = [a_fecha(v) for v in columnas["tomada_ts"]]
    columnas["micro_lote_id"] = [micro_lote_id] * len(eventos)
    return pa.Table.from_pydict(columnas, schema=ESQUEMA)


def _fsync_dir(carpeta: Path) -> None:
    fd = os.open(carpeta, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def escribir_atomico(t: pa.Table, destino: Path, nombre_tmp: str) -> None:
    """Escribe en _tmp/, fsync y rename al destino (sobrescribe si ya existía: reprocesar)."""
    TMP.mkdir(parents=True, exist_ok=True)
    tmp = TMP / nombre_tmp
    with open(tmp, "wb") as f:
        pq.write_table(t, f, compression="snappy")
        f.flush()
        os.fsync(f.fileno())
    destino.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, destino)
    _fsync_dir(destino.parent)


def publicar(micro_lote_id: str, eventos: list[dict], base: Path = EVENTOS) -> list[dict]:
    """Un archivo por mes del micro-lote, todos con el mismo id. Devuelve [{archivo, filas}]."""
    por_mes = defaultdict(list)
    for e in eventos:
        por_mes[f"{a_fecha(e['ts']):%Y-%m}"].append(e)
    publicados = []
    for mes, filas in sorted(por_mes.items()):
        destino = base / f"mes={mes}" / f"part-{micro_lote_id}.parquet"
        escribir_atomico(tabla(filas, micro_lote_id), destino, f"{micro_lote_id}__{mes}.parquet")
        publicados.append({"archivo": str(destino.relative_to(LAGO)), "filas": len(filas)})
    return publicados


def limpiar_tmp() -> list[str]:
    """Al arrancar: lo que quedó en _tmp/ es de una escritura que no llegó al rename."""
    if not TMP.exists():
        return []
    huerfanos = [p.name for p in TMP.iterdir() if p.is_file()]
    for nombre in huerfanos:
        (TMP / nombre).unlink(missing_ok=True)
    return huerfanos


def escribible() -> bool:
    return LAGO.is_dir() and os.access(LAGO, os.W_OK)

