# syntax=docker/dockerfile:1.7
# GridCast runtime images. One Python codebase, three dependency profiles:
#   base      FastAPI services (vendors, ingestion, feature-service, planning-api, operator)
#   ml        + scikit-learn / pandas / boto3   (forecast-service)
#   pipeline  + ml + Prefect                    (forecast pipeline worker, training jobs)
# Per-service release images are layered on top by deploy/docker/Dockerfile.release.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ghcr.io/astral-sh/uv:0.11 AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
ARG EXTRAS=""
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project ${EXTRAS}
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable ${EXTRAS}

FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --gid 10001 gridcast && useradd --uid 10001 --gid gridcast --no-create-home gridcast \
 && mkdir -p /app /tmp/prefect && chown -R gridcast:gridcast /app /tmp/prefect
COPY --from=build /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp PREFECT_HOME=/tmp/prefect
WORKDIR /app
USER 10001
ENTRYPOINT ["gridcast"]
