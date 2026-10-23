# Imagen del simulador (perfil "carga"). Solo necesita httpx; el código se monta desde ./simulador.
FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "-m", "simulador.simular", "--help"]
