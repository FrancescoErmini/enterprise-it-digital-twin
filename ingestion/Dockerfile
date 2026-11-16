FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
# Optional extra CA (only for builds behind a TLS-intercepting proxy; see README). Never baked into the image.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install -r requirements-dev.txt
COPY graph_ingestion ./graph_ingestion
COPY providers ./providers
COPY tests ./tests
COPY run_ingestion.py pytest.ini ./
RUN useradd --create-home --uid 10001 twin && mkdir -p /data/manifest && chown -R twin /data
USER twin
CMD ["python", "run_ingestion.py"]
