# syntax=docker/dockerfile:1
# pigtail app image (M1-T21): the scheduler service runs from it; other app services may reuse it.
# python:3.12-slim + uv, locked dependencies, no dev tools, non-root user, works with a
# read-only root filesystem (writable: PIGTAIL_DATA_DIR volume at /data, tmpfs /tmp).
#   docker build --build-arg PIGTAIL_CODE_COMMIT=$(git rev-parse HEAD) -t pigtail-app .
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

# Dependencies first (cached layer), then the project itself, non-editable.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable \
    && rm /usr/local/bin/uv
COPY migrations ./migrations
COPY infra/schedule.toml ./infra/schedule.toml

RUN useradd --system --uid 10001 --user-group --home-dir /nonexistent --shell /usr/sbin/nologin pigtail \
    && mkdir -p /data && chown pigtail:pigtail /data && chmod 0700 /data

ENV PATH=/app/.venv/bin:$PATH \
    PIGTAIL_CODE_COMMIT=${PIGTAIL_CODE_COMMIT} \
    PIGTAIL_DATA_DIR=/data \
    PIGTAIL_MIGRATIONS_DIR=/app/migrations \
    PIGTAIL_SCHEDULE=/app/infra/schedule.toml \
    PIGTAIL_HEALTH_PORT=8787
USER pigtail:pigtail
VOLUME ["/data"]
EXPOSE 8787
CMD ["pigtail", "scheduler", "run"]
