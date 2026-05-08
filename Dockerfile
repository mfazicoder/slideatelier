FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml ./
COPY src/ ./src/

RUN uv sync --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Default: run the web server. CLI is also installed (`atelier`) and accessible
# via `docker compose run --rm slideatelier atelier <command>`.
CMD ["uvicorn", "slideatelier.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
