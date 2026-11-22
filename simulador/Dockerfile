# Imagen del simulador (perfil "carga"). Solo necesita httpx; el código se monta desde ./simulador.
FROM python:3.12-slim

WORKDIR /srv
# Instala las dependencias del simulador.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Sin argumentos muestra la ayuda; el comando real se pasa con `docker compose run`.
CMD ["python", "-m", "simulador.simular", "--help"]
