"""C4.9: GET /analitica/{q1..q5} del gateway con la analítica simulada (sin Spark).

    docker compose exec -T gateway python -m app.prueba_analitica_c49

Revisa que la ruta llame a analitica:/q/{n} con el conjunto, con un timeout largo y sin reintentar
(repetir una consulta de Spark después de un timeout la lanzaría dos veces), y que pase tal cual el
200 y el 404 de la analítica y convierta sus 5xx y la falta de respuesta en 503.
"""
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import cliente_interno
from app.main import TIMEOUT_ANALITICA, app


def llamar(ruta: str, manejador):
    """Hace la petición al gateway con la analítica reemplazada por `manejador`."""
    llamadas = []

    def transporte(request: httpx.Request) -> httpx.Response:
        llamadas.append(request)
        return manejador(request)

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(transporte))
    with patch.object(cliente_interno, "cliente_compartido", return_value=cliente):
        respuesta = TestClient(app).get(ruta)
    return respuesta, llamadas


def main():
    # 1. 200: pasa la respuesta de la analítica tal cual.
    cuerpo = {"consulta": "q3", "conjunto": "grande", "parametros": {}, "filas": [{"modalidad": "lidar"}],
              "ms": 9490.0, "primera_ejecucion": False, "ejecutores": 1}
    r, llamadas = llamar("/analitica/q3?conjunto=grande", lambda req: httpx.Response(200, json=cuerpo))
    assert r.status_code == 200 and r.json() == cuerpo, r.text
    assert len(llamadas) == 1
    assert llamadas[0].url.host == "analitica" and llamadas[0].url.path == "/q/3", llamadas[0].url
    assert llamadas[0].url.params["conjunto"] == "grande"
    assert llamadas[0].extensions["timeout"]["read"] == TIMEOUT_ANALITICA
    assert llamadas[0].headers.get("X-Request-Id") == r.headers.get("X-Request-Id")
    print(f"OK | q3 grande -> analitica:/q/3?conjunto=grande, 200 igual, timeout {TIMEOUT_ANALITICA} s, X-Request-Id propagado")

    # 2. Sin conjunto: principal.
    r, llamadas = llamar("/analitica/q1", lambda req: httpx.Response(200, json={**cuerpo, "conjunto": "principal"}))
    assert r.status_code == 200 and llamadas[0].url.params["conjunto"] == "principal"
    print("OK | sin conjunto -> principal")

    # 3. 404 de la analítica: pasa igual.
    error = {"error": "conjunto_no_existe", "detalle": "/datalake/eventos_grande no tiene archivos Parquet"}
    r, llamadas = llamar("/analitica/q2?conjunto=grande", lambda req: httpx.Response(404, json=error))
    assert r.status_code == 404 and r.json() == error and len(llamadas) == 1
    print("OK | 404 conjunto_no_existe pasa igual")

    # 4. 503 de Spark: 503 del gateway y sin reintento (un GET normal sí se reintenta una vez).
    r, llamadas = llamar("/analitica/q4", lambda req: httpx.Response(503, json={"error": "spark_error"}))
    assert r.status_code == 503 and r.json()["error"] == "servicio_no_disponible", r.text
    assert len(llamadas) == 1, len(llamadas)
    print("OK | 503 de la analítica -> 503 servicio_no_disponible, 1 sola llamada")

    # 5. La analítica no responde a tiempo: 503 y sin reintento.
    def lenta(req):
        raise httpx.ReadTimeout("sin respuesta", request=req)

    r, llamadas = llamar("/analitica/q5", lenta)
    assert r.status_code == 503 and r.json()["detalle"]["servicio"] == "analitica", r.text
    assert len(llamadas) == 1, len(llamadas)
    print("OK | timeout -> 503, 1 sola llamada")

    # 6. Consulta o conjunto inválidos: 422 sin llamar a la analítica.
    for ruta in ("/analitica/q6", "/analitica/q1?conjunto=otro"):
        r, llamadas = llamar(ruta, lambda req: httpx.Response(200, json=cuerpo))
        assert r.status_code == 422 and r.json()["error"] == "validacion" and not llamadas, (ruta, r.text)
    print("OK | q6 y conjunto=otro -> 422 sin llamar a la analítica")

    print("C4.9_PRUEBAS_OK")


if __name__ == "__main__":
    main()
