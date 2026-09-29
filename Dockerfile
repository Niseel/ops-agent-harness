# One image: the API and the built UI on :8000 (ADR 0016). Run it with `docker compose --profile app up --build`.

# 1. Build the UI.
FROM node:24-slim AS ui
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# 2. The API. The layout under /app mirrors the repo, so `app.config.ROOT` is /app and every path resolves as on the host.
FROM python:3.12-slim-trixie
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1 UV_PYTHON_DOWNLOADS=0
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --locked
COPY backend/app ./app
COPY backend/prompts ./prompts
COPY config.yaml /app/
COPY data/kb /app/data/kb
COPY data/services.json /app/data/
COPY evals/kb_golden.jsonl /app/evals/
COPY --from=ui /src/frontend/dist/frontend/browser /app/frontend/dist/frontend/browser

# The store creates the database's directory only when it can: /app/state exists and belongs to the app user.
RUN useradd --create-home --uid 10001 app && mkdir /app/state && chown app /app/state
USER app
ENV PATH="/app/backend/.venv/bin:$PATH" DB_PATH=/app/state/harness.db
EXPOSE 8000
# Exec form, so uvicorn gets SIGTERM; the graceful timeout keeps an open event stream from holding the stop.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "5"]
