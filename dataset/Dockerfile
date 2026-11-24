# Imagen de carga de datos (perfil "datos"). El código se monta desde ./dataset, así que no hace
# falta reconstruir la imagen al cambiar el generador.
FROM python:3.12-slim

WORKDIR /srv
# Solo se instalan las dependencias (pymongo, pyarrow); el código llega por volumen.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Por defecto carga desde cero la base de MongoDB (dataset/cargar.py).
CMD ["python", "-m", "dataset.cargar"]
