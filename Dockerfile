# Prod image (amd64). Built and pushed by scripts/deploy-reconcile-from-arm64.sh; for a local build:
#   docker buildx build --platform linux/amd64 -t reconcile:latest --load .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    RECONCILE_DB_PATH=/data/reconcile.db

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY templates ./templates
COPY static ./static

RUN useradd --uid 1000 --create-home reconcile \
    && mkdir -p /data && chown reconcile:reconcile /data
USER reconcile
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/offline')" || exit 1

# Behind Cloudflare: trust X-Forwarded-* so redirects keep https.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
