FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_HTTP_TIMEOUT=180 \
    UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH" \
    SIM_HOST=0.0.0.0 SIM_PORT=8100
WORKDIR /app/simulator
COPY simulator/pyproject.toml simulator/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY simulator/ ./
# world.py reads scenarios from ../demo/scenarios
COPY demo/scenarios /app/demo/scenarios
EXPOSE 8100
CMD ["python", "server.py"]
