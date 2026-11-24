"""Conjunto de prueba (C3.3): 7 tareas escritas a mano para validar Q1–Q5.

    docker compose --profile datos run --rm dataset-historico python -m dataset.historico.prueba

Escribe datalake/eventos_prueba. Los resultados esperados están en esperado_prueba.json,
calculados a mano a partir de la tabla de abajo (no con Spark ni con este código).

Dos clientes, tres proyectos, dos equipos, dos anotadores y un revisor:
  C1: P1 texto/span (SLA 24 h) · P2 lidar/cuboide_3d (SLA 48 h)
  C2: P3 imagen/bbox (SLA 24 h)
  A1 en el equipo E1 · A2 en el equipo E2 · R1 revisa todo

Tarea  Ciclo  Anotador  toma               anotada  en_revision        cierre
T1     1      A1        2026-09-01 08:00   08:10    2026-09-01 09:00   aprobada 09-01 10:00
T2     1      A2        2026-09-02 08:00   08:20    2026-09-02 12:00   rechazada 09-02 12:30
T2     2      A1        2026-09-03 08:00   08:05    2026-09-04 10:00   aprobada 09-04 12:00
T3     -      A1        2026-09-10 10:00   (liberada 10:30)
T3     1      A2        2026-09-10 11:00   12:00    2026-09-11 09:00   aprobada 09-11 11:00
T4     1      A2        2026-06-15 10:00   10:15    2026-06-15 11:00   rechazada 06-15 11:30
T4     2      A2        2026-06-16 09:00   09:30    2026-06-16 10:00   aprobada 06-16 13:00
T5     1      A1        2026-09-30 09:00   09:12    2026-09-30 10:00   aprobada 09-30 12:00
T6     1      A1        2026-07-01 08:00   08:30    2026-07-01 09:00   rechazada 07-01 09:30
T6     2      A1        2026-07-02 08:00   08:30    2026-07-02 09:00   aprobada 07-02 11:00
T7     1      A2        2026-09-01 09:00   09:30    2026-09-01 10:00   aprobada 09-01 14:00
(T1, T2, T7 en P1 · T3 en P2 · T4, T5, T6 en P3)

Trampas a propósito: la aprobada de T1 está repetida en otro archivo (mismo event_id, debe contar
una vez); T3 tiene una fila liberada (Q1–Q5 la ignoran); T6 se aprueba a las 11:00 del 2026-07-02,
una hora antes del inicio de la ventana de 90 días de Q1 (max(ts) = 2026-09-30 12:00 -> desde
2026-07-02 12:00), y T4 es de junio, fuera de la ventana.
"""
from collections import defaultdict
from datetime import datetime, timezone

from dataset.historico.lago import CAMPOS, escribir, vaciar


def u(prefijo: str, n: int) -> str:
    """UUID fijo y legible: el prefijo indica el tipo de entidad y n el número."""
    return f"{prefijo}-0000-4000-8000-{n:012d}"


# Clientes, proyectos, equipos, anotadores (A1, A2) y revisor (R1) del conjunto de prueba.
C1, C2 = u("c0000000", 1), u("c0000000", 2)
P1, P2, P3 = u("90000000", 1), u("90000000", 2), u("90000000", 3)
E1, E2 = u("e0000000", 1), u("e0000000", 2)
A1, A2, R1 = u("a0000000", 1), u("a0000000", 2), u("a0000000", 99)
EQUIPO = {A1: E1, A2: E2}
PROYECTO = {  # proyecto -> cliente, modalidad, tipo, sla
    P1: (C1, "texto", "span", 24),
    P2: (C1, "lidar", "cuboide_3d", 48),
    P3: (C2, "imagen", "bbox", 24),
}
# Tareas y el proyecto al que pertenece cada una.
TAREAS = {u("70000000", i): p for i, p in enumerate([P1, P1, P2, P3, P3, P3, P1], start=1)}
T1, T2, T3, T4, T5, T6, T7 = TAREAS

# (tarea, fecha hora, estado, anotador, toma vigente, motivo)
FILAS = [
    (T1, "2026-09-01 08:00", "abierta", A1, "2026-09-01 08:00", None),
    (T1, "2026-09-01 08:10", "anotada", A1, "2026-09-01 08:00", None),
    (T1, "2026-09-01 09:00", "en_revision", A1, "2026-09-01 08:00", None),
    (T1, "2026-09-01 10:00", "aprobada", A1, "2026-09-01 08:00", None),
    (T2, "2026-09-02 08:00", "abierta", A2, "2026-09-02 08:00", None),
    (T2, "2026-09-02 08:20", "anotada", A2, "2026-09-02 08:00", None),
    (T2, "2026-09-02 12:00", "en_revision", A2, "2026-09-02 08:00", None),
    (T2, "2026-09-02 12:30", "rechazada", A2, "2026-09-02 08:00", "ETIQUETA_INCORRECTA"),
    (T2, "2026-09-03 08:00", "abierta", A1, "2026-09-03 08:00", None),
    (T2, "2026-09-03 08:05", "anotada", A1, "2026-09-03 08:00", None),
    (T2, "2026-09-04 10:00", "en_revision", A1, "2026-09-03 08:00", None),
    (T2, "2026-09-04 12:00", "aprobada", A1, "2026-09-03 08:00", None),
    (T3, "2026-09-10 10:00", "abierta", A1, "2026-09-10 10:00", None),
    (T3, "2026-09-10 10:30", "liberada", A1, "2026-09-10 10:00", None),
    (T3, "2026-09-10 11:00", "abierta", A2, "2026-09-10 11:00", None),
    (T3, "2026-09-10 12:00", "anotada", A2, "2026-09-10 11:00", None),
    (T3, "2026-09-11 09:00", "en_revision", A2, "2026-09-10 11:00", None),
    (T3, "2026-09-11 11:00", "aprobada", A2, "2026-09-10 11:00", None),
    (T4, "2026-06-15 10:00", "abierta", A2, "2026-06-15 10:00", None),
    (T4, "2026-06-15 10:15", "anotada", A2, "2026-06-15 10:00", None),
    (T4, "2026-06-15 11:00", "en_revision", A2, "2026-06-15 10:00", None),
    (T4, "2026-06-15 11:30", "rechazada", A2, "2026-06-15 10:00", "GEOMETRIA_IMPRECISA"),
    (T4, "2026-06-16 09:00", "abierta", A2, "2026-06-16 09:00", None),
    (T4, "2026-06-16 09:30", "anotada", A2, "2026-06-16 09:00", None),
    (T4, "2026-06-16 10:00", "en_revision", A2, "2026-06-16 09:00", None),
    (T4, "2026-06-16 13:00", "aprobada", A2, "2026-06-16 09:00", None),
    (T5, "2026-09-30 09:00", "abierta", A1, "2026-09-30 09:00", None),
    (T5, "2026-09-30 09:12", "anotada", A1, "2026-09-30 09:00", None),
    (T5, "2026-09-30 10:00", "en_revision", A1, "2026-09-30 09:00", None),
    (T5, "2026-09-30 12:00", "aprobada", A1, "2026-09-30 09:00", None),
    (T6, "2026-07-01 08:00", "abierta", A1, "2026-07-01 08:00", None),
    (T6, "2026-07-01 08:30", "anotada", A1, "2026-07-01 08:00", None),
    (T6, "2026-07-01 09:00", "en_revision", A1, "2026-07-01 08:00", None),
    (T6, "2026-07-01 09:30", "rechazada", A1, "2026-07-01 08:00", "OBJETO_OMITIDO"),
    (T6, "2026-07-02 08:00", "abierta", A1, "2026-07-02 08:00", None),
    (T6, "2026-07-02 08:30", "anotada", A1, "2026-07-02 08:00", None),
    (T6, "2026-07-02 09:00", "en_revision", A1, "2026-07-02 08:00", None),
    (T6, "2026-07-02 11:00", "aprobada", A1, "2026-07-02 08:00", None),
    (T7, "2026-09-01 09:00", "abierta", A2, "2026-09-01 09:00", None),
    (T7, "2026-09-01 09:30", "anotada", A2, "2026-09-01 09:00", None),
    (T7, "2026-09-01 10:00", "en_revision", A2, "2026-09-01 09:00", None),
    (T7, "2026-09-01 14:00", "aprobada", A2, "2026-09-01 09:00", None),
]


def ms(texto: str) -> int:
    """"AAAA-MM-DD HH:MM" (UTC) a epoch en milisegundos."""
    return int(datetime.strptime(texto, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp() * 1000)


def filas() -> list[dict]:
    """Convierte FILAS en filas completas del lago (versión creciente por tarea)."""
    salida, version = [], defaultdict(int)
    for n, (tarea, ts, estado, anotador, toma, motivo) in enumerate(FILAS, start=1):
        cliente, modalidad, tipo, sla = PROYECTO[TAREAS[tarea]]
        version[tarea] += 1
        salida.append({
            "event_id": u("ee000000", n), "ts": ms(ts), "tarea_id": tarea, "proyecto_id": TAREAS[tarea],
            "cliente_id": cliente, "batch_id": u("b0000000", 1), "modalidad": modalidad, "tipo_tarea": tipo,
            "estado": estado, "version": version[tarea], "anotador_id": anotador, "equipo_id": EQUIPO[anotador],
            "revisor_id": R1 if estado in ("en_revision", "aprobada", "rechazada") else None,
            "motivo_rechazo": motivo, "tomada_ts": ms(toma), "sla_horas": sla, "micro_lote_id": None,
        })
    return salida


def main() -> None:
    """Escribe el conjunto "prueba" y un archivo extra con un event_id duplicado (reentrega)."""
    base = vaciar("prueba")
    todas = filas()
    reentrega = [f for f in todas if f["tarea_id"] == T1 and f["estado"] == "aprobada"]
    for nombre, lote in (("prueba-001", todas), ("prueba-001-reentrega", reentrega)):
        por_mes = defaultdict(lambda: {c: [] for c in CAMPOS})
        for f in lote:
            mes = datetime.fromtimestamp(f["ts"] / 1000, timezone.utc).strftime("%Y-%m")
            for c in CAMPOS:
                por_mes[mes][c].append(nombre if c == "micro_lote_id" else f[c])
        for mes, columnas in sorted(por_mes.items()):
            print(f"  {base.name}/mes={mes}/part-{nombre}.parquet  {escribir(columnas, base, mes, nombre)} filas")
    print(f"  total: {len(todas)} eventos + {len(reentrega)} duplicado(s) de event_id")


if __name__ == "__main__":
    main()
