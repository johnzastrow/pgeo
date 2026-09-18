# pgeo-api: FastAPI + asyncpg, run by uvicorn as a non-root user.
FROM ghcr.io/astral-sh/uv:0.9.17 AS uv
FROM python:3.13-slim-trixie@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
RUN useradd --system --uid 10001 --home /nonexistent --shell /usr/sbin/nologin pgeo
USER 10001
EXPOSE 4500
CMD ["sh", "-c", "exec uvicorn pgeo.api.app:app --host 0.0.0.0 --port 4500 --workers ${PGEO_WORKERS:-2} --no-access-log"]
