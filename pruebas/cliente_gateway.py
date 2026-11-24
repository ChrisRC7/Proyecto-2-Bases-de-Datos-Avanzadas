"""Costo del cliente interno del gateway (antes y después de reusar un solo httpx.AsyncClient).

    docker compose exec -T -w /srv gateway python - < pruebas/cliente_gateway.py
"""
import asyncio, statistics, time
import httpx
from app import cliente_interno

async def main():
    """Mide el costo de crear clientes httpx y la latencia de `solicitar` en serie y en paralelo."""
    # 1. CPU que cuesta crear (y cerrar) un httpx.AsyncClient nuevo, 20 veces.
    crear = []
    for _ in range(20):
        t0 = time.process_time(); c = httpx.AsyncClient(); crear.append((time.process_time() - t0) * 1000); await c.aclose()
    print(f"crear httpx.AsyncClient: CPU media {statistics.mean(crear):.1f} ms, mediana {statistics.median(crear):.1f} ms (20 veces)")
    # 2. Latencia de 50 llamadas seguidas con el cliente compartido.
    lat = []
    for _ in range(50):
        t0 = time.perf_counter(); r = await cliente_interno.solicitar("registro", "GET", "/salud"); lat.append((time.perf_counter() - t0) * 1000)
    lat.sort()
    print(f"solicitar('registro', GET /salud) x50 seguidas: p50 {lat[25]:.1f} ms, max {lat[-1]:.1f} ms")
    # 3. 30 a la vez, como varias peticiones del gateway juntas
    async def una():
        """Una llamada a GET /salud del registro; devuelve su duración en ms."""
        t0 = time.perf_counter(); await cliente_interno.solicitar("registro", "GET", "/salud"); return (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter(); res = sorted(await asyncio.gather(*(una() for _ in range(30)))); total = (time.perf_counter() - t0) * 1000
    print(f"30 solicitar a la vez: p50 {res[15]:.1f} ms, max {res[-1]:.1f} ms, total {total:.0f} ms")

asyncio.run(main())
