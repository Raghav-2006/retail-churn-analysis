# API image: FastAPI service on the committed demo database (demo/demo.sqlite).
#   docker build -t retail-api .
#   docker run -p 8000:8000 retail-api        ->  http://localhost:8000/health
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so code changes don't invalidate this layer.
# An optional build secret "ca" lets builds behind a TLS-inspecting proxy trust its CA
# (docker build --secret id=ca,src=ca.crt ...); normal builds don't need it.
COPY requirements-api.txt .
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi && \
    pip install -r requirements-api.txt

# Only what the service imports, plus the demo database and the eval set.
COPY service/ service/
COPY tiering/__init__.py tiering/score.py tiering/features.py tiering/
COPY analyst/ analyst/
COPY pipeline/__init__.py pipeline/db.py pipeline/load.py pipeline/transform.py pipeline/
COPY src/__init__.py src/metrics.py src/
COPY sql/schema.sql sql/
COPY demo/demo.sqlite demo/

RUN useradd --create-home --uid 10001 app && mkdir -p logs && chown -R app /app
USER 10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["uvicorn", "service.api:app", "--host", "0.0.0.0", "--port", "8000"]
