# Imagen de carga de datos (perfil "datos"). El código se monta desde ./dataset, así que no hace
# falta reconstruir la imagen al cambiar el generador.
FROM python:3.12-slim

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "-m", "dataset.cargar"]
