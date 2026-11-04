# KalPakka backend image. Build context = repo root:
#   docker build -f infra/backend/Dockerfile -t kalpakka-backend .
# Keeps the repo layout (/app/backend, /app/content) because Settings resolves content and data
# from REPO_ROOT = parents[3] of kalpakka/config.py; the project is installed editable (uv sync).
# No secret is baked in: every key arrives as an environment variable at run time.
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/backend/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app/backend

# Dependencies first (cached layer), then the project itself.
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
COPY content/ /app/content/
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 app && mkdir -p /app/data && chown app:app /app/data
USER app

# SQLite and the pre-rendered fixed lines live on the mounted volume.
ENV DB_PATH=/app/data/kalpakka.db \
    PRERENDER_DIR=/app/data/prerender \
    TRUST_FORWARDED_FOR=true

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status != 200)"]

# One worker: calls hold in-process state (admission, sessions). Pings keep idle sockets alive
# through Caddy and mobile networks; keep-alive above Caddy's idle so Caddy closes first.
CMD ["/app/backend/.venv/bin/uvicorn", "kalpakka.server:create_production_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--ws-ping-interval", "20", "--ws-ping-timeout", "20", "--timeout-keep-alive", "75"]
