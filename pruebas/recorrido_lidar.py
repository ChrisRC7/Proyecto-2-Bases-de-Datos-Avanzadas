"""I.2 — recorrido completo de una tarea LiDAR por el gateway, de la cola hasta Q1.

siguiente → borrador (x2) → latido → recuperar → enviar → segundo envío 409 → revisión (empezar,
aprobar) → exportar → fila visible en Q1 (GET /analitica/q1). Usa un proyecto LiDAR propio con una sola
tarea (taxonomía del enunciado) y los actores de dataset/salida/ids.json. Solo biblioteca estándar.

    python3 pruebas/recorrido_lidar.py        (o pruebas/recorrido_lidar.sh para dejar la evidencia)
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from proyecto_prueba import GATEWAY, _ids, _post

# Geometría del cuboide, taxonomía del enunciado y espera máxima para ver la aprobación en Q1.
GEOMETRIA = {"tipo": "cuboide_3d", "centro": [12.4, -3.1, 0.9], "dimensiones": [1.8, 0.6, 1.7], "yaw": 1.2}
TAXONOMIA = [
    {"etiqueta": "lidar#peaton", "instruccion": "Persona a pie."},
    {"etiqueta": "lidar#vehiculo", "instruccion": "Automóvil, bus o camión."},
    {"etiqueta": "lidar#ciclista", "instruccion": "Un skater se clasifica como ciclista, no como peatón."},
    {"etiqueta": "lidar#signal", "instruccion": "Señal de tránsito vertical."},
]
ESPERA_Q1_S = 120


def http(metodo, ruta, cuerpo=None, actor=None):
    """Petición al gateway (con X-Anotador-Id si se da `actor`); devuelve (status, JSON, ms)."""
    datos = None if cuerpo is None else json.dumps(cuerpo).encode("utf-8")
    cabeceras = {"Content-Type": "application/json"}
    if actor:
        cabeceras["X-Anotador-Id"] = actor
    peticion = urllib.request.Request(GATEWAY + ruta, data=datos, headers=cabeceras, method=metodo)
    inicio = time.perf_counter()
    try:
        with urllib.request.urlopen(peticion, timeout=200) as r:
            texto = r.read().decode("utf-8")
            codigo = r.status
    except urllib.error.HTTPError as exc:
        texto, codigo = exc.read().decode("utf-8"), exc.code
    ms = (time.perf_counter() - inicio) * 1000
    return codigo, (json.loads(texto) if texto else None), ms


def paso(nombre, esperado, metodo, ruta, cuerpo=None, actor=None):
    """Ejecuta un paso del recorrido e imprime OK/FALLA; si el código no es el esperado, termina."""
    codigo, datos, ms = http(metodo, ruta, cuerpo, actor)
    marca = "OK " if codigo == esperado else "FALLA"
    print(f"{marca} {metodo:4} {ruta:52} {codigo} ({ms:6.0f} ms)  {nombre}", flush=True)
    if codigo != esperado:
        print("      ", json.dumps(datos, ensure_ascii=False)[:400])
        sys.exit(1)
    return datos


def aprobadas_hoy_lidar() -> int:
    """Aprobadas LiDAR de hoy según Q1 (0 si todavía no hay fila)."""
    _, d, _ = http("GET", "/analitica/q1?conjunto=principal")
    hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return next((f["aprobadas"] for f in d["filas"] if f["dia"] == hoy and f["modalidad"] == "lidar"), 0)


def main():
    """Ejecuta el recorrido completo; devuelve 0 si la aprobación aparece en Q1 a tiempo."""
    # Actores: dos anotadores y un revisor del dataset.
    ids = _ids()
    anotador, otro = [a["id"] for a in ids["anotadores"] if a["rol"] == "anotador"][:2]
    revisor = next(a["id"] for a in ids["anotadores"] if a["rol"] == "revisor")

    # Estado inicial: salud del sistema y aprobadas LiDAR de hoy en Q1.
    _, salud, _ = http("GET", "/salud")
    print(f"/salud: {salud['estado']} {({k: v['estado'] for k, v in salud['capas'].items()})}")
    antes = aprobadas_hoy_lidar()
    print(f"Q1 antes (hoy, lidar): {antes}")

    # Proyecto LiDAR propio con una sola tarea.
    proyecto = _post(GATEWAY, "/proyectos", {
        "cliente_id": ids["clientes"][0], "nombre": f"LiDAR urbano recorrido {int(time.time())}",
        "modalidad": "lidar", "taxonomia": TAXONOMIA, "sla_horas": 48, "prioridad": 3,
    })["proyecto_id"]
    _post(GATEWAY, f"/proyectos/{proyecto}/lotes", {"tareas": [{
        "tipo_tarea": "cuboide_3d", "prioridad": 5,
        "archivo": {"uri": "s3://datos/lidar/escena-recorrido.pcd", "sha256": "ab" * 32, "bytes": 4823112,
                    "mime": "application/x-pcd", "meta": {"min": 0.0, "max": 120.5, "puntos": 120000}},
    }]})
    print(f"proyecto {proyecto} (1 tarea cuboide_3d, taxonomía con 4 etiquetas)\n")

    a = paso("asignación exclusiva + lock + sesión", 200, "POST", "/tareas/siguiente",
             {"proyecto_id": proyecto}, anotador)
    tarea, token = a["tarea"]["tarea_id"], a["token"]
    print(f"      tarea {tarea} · vence_en {a['vence_en']} · etiquetas "
          f"{[t['etiqueta'] for t in a['tarea']['taxonomia']]}")
    paso("otro anotador: no hay más tareas en el proyecto", 204, "POST", "/tareas/siguiente",
         {"proyecto_id": proyecto}, otro)

    # Dos borradores (el segundo corrige la etiqueta), latido y recuperación de la sesión.
    for i, (etiquetas, nota) in enumerate([(["lidar#peaton"], "primera idea: peatón"),
                                           (["lidar#ciclista"], "corrige: un skater es ciclista")], 1):
        paso(f"borrador {i} ({nota})", 200, "PUT", f"/tareas/{tarea}/borrador",
             {"token": token, "contenido": {"etiquetas": etiquetas, "geometria": GEOMETRIA}}, anotador)
        time.sleep(2)
    paso("latido", 200, "POST", "/sesiones/latido", {"token": token}, anotador)
    r = paso("recuperar sesión interrumpida", 200, "POST", "/sesiones/recuperar", None, anotador)
    assert r["borrador"]["contenido"]["etiquetas"] == ["lidar#ciclista"], r
    print(f"      borrador recuperado: {r['borrador']['contenido']['etiquetas']} guardado {r['borrador']['guardado_en']}")

    # Envío, intento de doble envío (409), revisión y exportación.
    anotacion = {"etiquetas": ["lidar#ciclista"], "geometria": GEOMETRIA}
    paso("enviar (MongoDB confirma)", 200, "POST", f"/tareas/{tarea}/enviar",
         {"token": token, "anotacion": anotacion}, anotador)
    paso("segundo envío de la misma tarea", 409, "POST", f"/tareas/{tarea}/enviar",
         {"token": token, "anotacion": anotacion}, anotador)
    paso("revisión: empezar", 200, "POST", f"/tareas/{tarea}/revision", {"accion": "empezar"}, revisor)
    paso("revisión: aprobar", 200, "POST", f"/tareas/{tarea}/revision", {"accion": "aprobar"}, revisor)
    t_200 = time.time()
    e = paso("exportar aprobadas", 200, "GET", f"/proyectos/{proyecto}/exportar")
    assert [t["tarea_id"] for t in e["tareas"]] == [tarea], e
    print(f"      exportada con etiquetas {e['tareas'][0]['anotacion']['etiquetas']}")

    # Espera a que el evento llegue al lago y Q1 cuente una aprobada más.
    while time.time() - t_200 < ESPERA_Q1_S:
        despues = aprobadas_hoy_lidar()
        if despues >= antes + 1:
            print(f"\nQ1 después (hoy, lidar): {despues} · visible {time.time() - t_200:.1f} s después del 200 de aprobar")
            print("RECORRIDO_LIDAR_OK")
            return 0
        time.sleep(1)
    print(f"FALLA: Q1 no cambió en {ESPERA_Q1_S} s")
    return 1


if __name__ == "__main__":
    sys.exit(main())
