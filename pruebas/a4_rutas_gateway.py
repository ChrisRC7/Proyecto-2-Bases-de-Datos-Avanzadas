"""A4 — rutas de A en el gateway: proyectos, lotes, exportar y revisión.

Recorre por el gateway (puerto 8000) un proyecto LiDAR nuevo con dos tareas: una se aprueba y la
otra se rechaza. Revisa también los errores del contrato (404, 409, 422).

    python3 pruebas/a4_rutas_gateway.py

Solo usa la biblioteca estándar y los ids de dataset/salida/ids.json (archivo, no la base).
"""
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

# Gateway, ids del dataset y geometría válida para las tareas LiDAR.
URL = os.getenv("GATEWAY_URL", "http://localhost:8000")
IDS = os.getenv("IDS_JSON", "dataset/salida/ids.json")
GEOMETRIA = {"tipo": "cuboide_3d", "centro": [1.2, 3.4, 0.5], "dimensiones": [0.6, 1.8, 1.7], "yaw": 0.3}

# Contador global de casos que no dieron el código esperado.
fallas = 0


def llamar(metodo: str, ruta: str, cuerpo=None, anotador=None):
    """Petición JSON al gateway; devuelve (status, cuerpo)."""
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    cabeceras = {"Content-Type": "application/json"}
    if anotador:
        cabeceras["X-Anotador-Id"] = anotador
    peticion = urllib.request.Request(URL + ruta, data=datos, headers=cabeceras, method=metodo)
    try:
        with urllib.request.urlopen(peticion, timeout=10) as r:
            texto = r.read()
            return r.status, json.loads(texto) if texto else None
    except urllib.error.HTTPError as e:
        texto = e.read()
        return e.code, json.loads(texto) if texto else None


def caso(nombre: str, esperado: int, metodo: str, ruta: str, cuerpo=None, anotador=None):
    """Ejecuta un caso, imprime OK/FALLA según el código y devuelve el cuerpo de la respuesta."""
    global fallas
    status, datos = llamar(metodo, ruta, cuerpo, anotador)
    ok = status == esperado
    fallas += not ok
    resumen = json.dumps(datos, ensure_ascii=False)
    print(f"{'OK   ' if ok else 'FALLA'} {nombre:<44} esperado {esperado} obtenido {status}  {resumen[:150]}")
    return datos


def main():
    """Recorre los casos de proyectos, lotes, revisión y exportación; sale con 1 si algo falló."""
    ids = json.load(open(IDS))
    cliente = ids["clientes"][0]
    anotador = next(a["id"] for a in ids["anotadores"] if a["rol"] == "anotador")
    revisor = next(a["id"] for a in ids["anotadores"] if a["rol"] == "revisor")

    print("== A4.1 proyectos y lotes")
    proyecto = {
        "cliente_id": cliente, "nombre": f"LiDAR prueba A4 {uuid.uuid4().hex[:6]}", "modalidad": "lidar",
        "taxonomia": [
            {"etiqueta": "lidar#peaton", "instruccion": "Persona a pie."},
            {"etiqueta": "lidar#ciclista", "instruccion": "Un skater se clasifica como ciclista, no como peatón."},
        ],
        "sla_horas": 48, "prioridad": 3,
    }
    caso("POST /proyectos cliente inexistente", 404, "POST", "/proyectos",
         {**proyecto, "cliente_id": str(uuid.uuid4())})
    caso("POST /proyectos sin taxonomía", 422, "POST", "/proyectos", {**proyecto, "taxonomia": []})
    proyecto_id = caso("POST /proyectos", 201, "POST", "/proyectos", proyecto)["proyecto_id"]

    def tarea(n: int, mime: str = "application/x-pcd") -> dict:
        """Tarea LiDAR de ejemplo número n (el mime se puede cambiar para provocar un 422)."""
        return {"tipo_tarea": "cuboide_3d", "prioridad": 5,
                "archivo": {"uri": f"s3://datos/lidar/a4-{n}.pcd", "sha256": f"{n:064x}", "bytes": 4823112,
                            "mime": mime, "meta": {"min": 0.0, "max": 120.5, "puntos": 120000}}}

    caso("POST /proyectos/{id}/lotes mime de imagen", 422, "POST", f"/proyectos/{proyecto_id}/lotes",
         {"tareas": [tarea(1, "image/png")]})
    caso("POST /proyectos/{id}/lotes proyecto inexistente", 404, "POST", f"/proyectos/{uuid.uuid4()}/lotes",
         {"tareas": [tarea(1)]})
    caso("POST /proyectos/{id}/lotes", 201, "POST", f"/proyectos/{proyecto_id}/lotes",
         {"tareas": [tarea(1), tarea(2)]})

    def anotar_y_enviar() -> str:
        """Toma la siguiente tarea del proyecto, la envía y devuelve su id."""
        datos = caso("POST /tareas/siguiente", 200, "POST", "/tareas/siguiente",
                     {"proyecto_id": proyecto_id}, anotador)
        tarea_id, token = datos["tarea"]["tarea_id"], datos["token"]
        caso("POST /tareas/{id}/enviar", 200, "POST", f"/tareas/{tarea_id}/enviar",
             {"token": token, "anotacion": {"etiquetas": ["lidar#ciclista"], "geometria": GEOMETRIA}}, anotador)
        return tarea_id

    print("== A4.2 revisión: aprobar")
    t1 = anotar_y_enviar()
    caso("revision aprobar sin empezar", 409, "POST", f"/tareas/{t1}/revision", {"accion": "aprobar"}, revisor)
    caso("revision empezar por un anotador", 422, "POST", f"/tareas/{t1}/revision", {"accion": "empezar"}, anotador)
    caso("revision empezar", 200, "POST", f"/tareas/{t1}/revision", {"accion": "empezar"}, revisor)
    caso("revision empezar otra vez", 409, "POST", f"/tareas/{t1}/revision", {"accion": "empezar"}, revisor)
    caso("revision aprobar", 200, "POST", f"/tareas/{t1}/revision", {"accion": "aprobar"}, revisor)

    print("== A4.2 revisión: rechazar")
    t2 = anotar_y_enviar()
    caso("revision empezar", 200, "POST", f"/tareas/{t2}/revision", {"accion": "empezar"}, revisor)
    caso("revision rechazar sin motivo", 422, "POST", f"/tareas/{t2}/revision", {"accion": "rechazar"}, revisor)
    caso("revision rechazar motivo inventado", 422, "POST", f"/tareas/{t2}/revision",
         {"accion": "rechazar", "motivo": "NO_ME_GUSTA"}, revisor)
    caso("revision rechazar", 200, "POST", f"/tareas/{t2}/revision",
         {"accion": "rechazar", "motivo": "ETIQUETA_INCORRECTA"}, revisor)
    caso("revision rechazar otra vez", 409, "POST", f"/tareas/{t2}/revision",
         {"accion": "rechazar", "motivo": "ETIQUETA_INCORRECTA"}, revisor)
    caso("revision tarea inexistente", 404, "POST", f"/tareas/{uuid.uuid4()}/revision", {"accion": "empezar"}, revisor)
    # Sin esperar: el gateway suelta el lock al enviar, así que la rechazada se puede tomar en seguida.
    datos = caso("la rechazada se vuelve a ofrecer", 200, "POST", "/tareas/siguiente",
                 {"proyecto_id": proyecto_id}, anotador)
    if datos["tarea"]["tarea_id"] != t2:
        print("FALLA la tarea ofrecida no es la rechazada")
        sys.exit(1)
    caso("POST /tareas/{id}/liberar", 200, "POST", f"/tareas/{t2}/liberar", {"token": datos["token"]}, anotador)
    datos = caso("la liberada se vuelve a ofrecer", 200, "POST", "/tareas/siguiente",
                 {"proyecto_id": proyecto_id}, anotador)
    if datos["tarea"]["tarea_id"] != t2:
        print("FALLA la tarea ofrecida no es la liberada")
        sys.exit(1)
    caso("POST /tareas/{id}/liberar otra vez", 200, "POST", f"/tareas/{t2}/liberar", {"token": datos["token"]}, anotador)

    print("== A4.1 exportar")
    datos = caso("GET /proyectos/{id}/exportar", 200, "GET", f"/proyectos/{proyecto_id}/exportar?limite=10")
    exportadas = [t["tarea_id"] for t in datos["tareas"]]
    print(f"      exportadas {exportadas} (se esperaba solo {t1}), siguiente_cursor {datos['siguiente_cursor']}")
    caso("GET /proyectos/{id}/exportar inexistente", 404, "GET", f"/proyectos/{uuid.uuid4()}/exportar")

    print(f"\nproyecto {proyecto_id} · aprobada {t1} · rechazada {t2}")
    print("FALLAS:", fallas)
    sys.exit(1 if fallas or exportadas != [t1] else 0)


if __name__ == "__main__":
    main()
