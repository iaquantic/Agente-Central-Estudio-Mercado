# Imagen autónoma del Agente Central (sirve para Easypanel, Coolify, docker compose…).
# El Controlador de Mercado (Agente Externo) se instala desde su repositorio público de GitHub.
ARG IMAGEN_BASE=python:3.12-slim
FROM ${IMAGEN_BASE}
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DIRECTORIO_DATOS=/app/datos/empresa \
    CONTROLADOR_CACHE_DIR=/app/datos/historial_mercado \
    API_HOST=0.0.0.0 API_PORT=8090
ARG CONTROLADOR_MERCADO_URL=https://github.com/iaquantic/Agente-Controlador-de-Mercado/archive/refs/heads/main.tar.gz
RUN pip install --no-cache-dir "${CONTROLADOR_MERCADO_URL}"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agente_central ./agente_central
COPY prompts ./prompts
COPY config ./config
COPY demo ./demo
COPY docs/contratos ./docs/contratos
RUN useradd --create-home agente && mkdir -p datos && chown agente datos
USER agente
# Monta un volumen persistente en /app/datos: panel, caché e histórico de capturas (sin él no hay tendencias).
VOLUME ["/app/datos"]
EXPOSE 8090
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"API_PORT\",\"8090\")}/salud')"
CMD ["python", "-m", "agente_central", "servir"]
