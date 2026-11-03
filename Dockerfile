FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.13-slim@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1 AS python-base

FROM python-base AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-cache

FROM python-base AS runtime
# Installers are build tools. Remove pip and its bundled recovery copy so their
# vendored dependencies are not shipped or restored in the running service.
RUN rm -rf /usr/local/lib/python3.13/site-packages/pip \
    /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
    /usr/local/lib/python3.13/ensurepip \
    /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13
WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY --from=build /app/.venv ./.venv
COPY app ./app
USER 10001
EXPOSE 8080
CMD [".venv/bin/uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
