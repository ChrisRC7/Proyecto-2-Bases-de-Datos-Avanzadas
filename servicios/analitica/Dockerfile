# Imagen oficial de Spark (Java 11 + Spark 3.5.1 + Python 3.8): el driver de la analítica usa la
# misma versión de Spark que el master y los workers. pyspark viene en /opt/spark/python.
FROM apache/spark:3.5.1-python3

# Se necesita root para instalar dependencias y crear el enlace a python.
USER root
WORKDIR /srv
COPY requirements.txt .
# Instala FastAPI/uvicorn y deja disponible el comando `python` (la imagen solo trae python3).
RUN pip install --no-cache-dir -r requirements.txt && ln -sf /usr/bin/python3 /usr/local/bin/python
# Código del servicio de analítica.
COPY app ./app

# PYTHONPATH permite importar pyspark y py4j que vienen dentro de la imagen de Spark.
ENV PYTHONPATH=/opt/spark/python/lib/pyspark.zip:/opt/spark/python/lib/py4j-0.10.9.7-src.zip \
    PUERTO=8000
# El servicio corre con el usuario sin privilegios de la imagen de Spark.
USER spark
# Arranca la API de analítica con uvicorn.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PUERTO}"]
