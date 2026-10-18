# syntax=docker/dockerfile:1
#
# StoreOps store image: the API, the website and the scheduled jobs for ONE
# store, in one process. Every store runs the same image with its own volume
# (/data: SQLite database, uploads, flyers) and its own environment (.env via
# docker-compose.yml). The image holds no secrets and no store data.
#
#   docker build -t storeops .
#   docker run --rm -p 127.0.0.1:8000:8000 -v storeops-data:/data --env-file .env storeops

# --- 1. Website build ------------------------------------------------------------------------
FROM node:22-bookworm-slim AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx vite build

# --- 2. Runtime ------------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract: the OCR text layer on invoice PDFs (app/services/toast_pdf.py).
# tini: forwards SIGTERM so the scheduler shuts down cleanly on `docker stop`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tini \
    && rm -rf /var/lib/apt/lists/*

# A fixed non-root user; it owns /data (the store's volume) and nothing else.
RUN groupadd --system --gid 10001 storeops \
    && useradd --system --uid 10001 --gid storeops --home-dir /data --shell /usr/sbin/nologin storeops \
    && mkdir -p /data \
    && chown storeops:storeops /data

WORKDIR /app/backend
COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/ ./
COPY --from=web /src/frontend/dist /app/frontend/dist
COPY docker/entrypoint.sh /usr/local/bin/storeops-entrypoint
RUN chmod 0755 /usr/local/bin/storeops-entrypoint \
    && python -m compileall -q app \
    && test ! -e .env

# Container paths. docker-compose.yml sets the same values so a store's .env
# cannot point them somewhere outside the volume by accident.
ENV DATABASE_URL=sqlite:////data/storeops.db \
    UPLOADS_ROOT=/data/uploads \
    INVOICE_UPLOADS_DIR=/data/uploads/invoices \
    TEAM_UPLOADS_DIR=/data/uploads/team \
    FLYERS_DIR=/data/uploads/flyers \
    INSIGHTS_HMAC_KEY_FILE=/data/insights_hmac.key \
    FRONTEND_DIST_DIR=/app/frontend/dist \
    PORT=8000

USER storeops
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os, sys, urllib.request; r = urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', '8000'), timeout=4); sys.exit(0 if r.status == 200 else 1)"]

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/storeops-entrypoint"]
