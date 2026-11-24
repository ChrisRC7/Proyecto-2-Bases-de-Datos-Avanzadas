# Imagen base liviana con Python 3.12.
FROM python:3.12-slim

# Carpeta de trabajo dentro del contenedor.
WORKDIR /srv
# Primero se instalan las dependencias (capa que Docker reutiliza si no cambian).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Después se copia el código del servicio.
COPY app ./app

# Puerto interno en el que escucha el servicio (se puede cambiar desde docker-compose).
ENV PUERTO=8000
# Arranca la aplicación FastAPI (app/main.py) con uvicorn en todas las interfaces.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PUERTO}"]
