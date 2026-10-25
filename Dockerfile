# syntax=docker/dockerfile:1.7
# CatalogPay Agent: one image serving the FastAPI API *and* the built React Command Center.

# ---- Stage 1: build the React 19 + AG Grid frontend ------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python runtime ------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ENVIRONMENT=production \
    DATABASE_URL=sqlite:////data/catalogpay.db \
    STATIC_DIST_DIR=/app/frontend/dist \
    PORT=8000

WORKDIR /app/backend

# Dependencies first (their own layer): read the pinned list straight from pyproject.toml.
COPY backend/pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY backend/app ./app
COPY --from=frontend /app/frontend/dist /app/frontend/dist
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app \
    && mkdir -p /data && chown app:app /data \
    && chmod 0755 /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/api/v1/health', timeout=4)" || exit 1

# The entrypoint fixes ownership of a freshly mounted /data disk, then drops to the
# unprivileged "app" user before starting uvicorn.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
