# syntax=docker/dockerfile:1
# pigtail web app image (M1-T12, D1 preview): builds the UI (ui/, pnpm) and serves it together with
# the read-only API from one process (`pigtail ui serve`). Same Python layout as the root
# Dockerfile (uv, locked deps, non-root, read-only root filesystem).
#   docker build -f infra/ui/Dockerfile -t pigtail-ui .
FROM node:22-alpine AS ui
WORKDIR /ui
RUN corepack enable
COPY ui/package.json ui/pnpm-lock.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile
COPY ui/ ./
RUN pnpm build

FROM ghcr.io/astral-sh/uv:0.12.10 AS uv

FROM python:3.12-slim AS app
ARG PIGTAIL_CODE_COMMIT=""
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable \
    && rm /usr/local/bin/uv
COPY migrations ./migrations
COPY --from=ui /ui/dist ./ui/dist

RUN useradd --system --uid 10001 --user-group --home-dir /nonexistent --shell /usr/sbin/nologin pigtail

ENV PATH=/app/.venv/bin:$PATH \
    PIGTAIL_CODE_COMMIT=${PIGTAIL_CODE_COMMIT} \
    PIGTAIL_DATA_DIR=/data \
    PIGTAIL_MIGRATIONS_DIR=/app/migrations \
    PIGTAIL_UI_DIST=/app/ui/dist
USER pigtail:pigtail
EXPOSE 8080
# 0.0.0.0 inside the container only; compose publishes it on the host's 127.0.0.1.
CMD ["pigtail", "ui", "serve", "--host", "0.0.0.0", "--port", "8080"]
