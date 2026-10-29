# Imagen oficial de Spark (Java 11 + Spark 3.5.1 + Python 3.8): el driver de la analítica usa la
# misma versión de Spark que el master y los workers. pyspark viene en /opt/spark/python.
FROM apache/spark:3.5.1-python3

USER root
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && ln -sf /usr/bin/python3 /usr/local/bin/python
COPY app ./app

ENV PYTHONPATH=/opt/spark/python/lib/pyspark.zip:/opt/spark/python/lib/py4j-0.10.9.7-src.zip \
    PUERTO=8000
USER spark
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PUERTO}"]
