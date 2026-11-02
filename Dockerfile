FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.13-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-cache
COPY app ./app
USER 10001
EXPOSE 8080
CMD [".venv/bin/uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080"]
