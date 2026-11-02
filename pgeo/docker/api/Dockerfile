# pgeo-api: FastAPI + asyncpg, run by uvicorn as a non-root user.
FROM ghcr.io/astral-sh/uv:0.9.17@sha256:5cb6b54d2bc3fe2eb9a8483db958a0b9eebf9edff68adedb369df8e7b98711a2 AS uv
FROM python:3.14-slim-trixie@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8
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
