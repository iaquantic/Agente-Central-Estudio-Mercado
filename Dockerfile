# Imagen del Agente Central. El Controlador de Mercado (Agente Externo) se toma del contexto adicional "mercado"
# (repositorio vecino, ver docker-compose.yml), así no hacen falta credenciales de GitHub dentro de la imagen.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=mercado . /opt/controlador-mercado
RUN pip install --no-cache-dir /opt/controlador-mercado
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agente_central ./agente_central
COPY prompts ./prompts
COPY config ./config
COPY demo ./demo
COPY docs/contratos ./docs/contratos
RUN useradd --create-home agente && mkdir -p datos && chown agente datos
USER agente
EXPOSE 8090
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"API_PORT\",\"8090\")}/salud')"
CMD ["python", "-m", "agente_central", "servir"]
