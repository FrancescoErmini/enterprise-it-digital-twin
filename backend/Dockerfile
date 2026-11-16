FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
# Optional extra CA (only for builds behind a TLS-intercepting proxy; see README). Never baked into the image.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install -r requirements-dev.txt
COPY app ./app
COPY tests ./tests
COPY pytest.ini ./
COPY scripts/twin-rules /usr/local/bin/twin-rules
RUN chmod +x /usr/local/bin/twin-rules && useradd --create-home --uid 10001 twin && mkdir -p /data/audit && chown -R twin /data
USER twin
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=12 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
