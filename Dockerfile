FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends supervisor curl \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic ./alembic
COPY scripts ./scripts
COPY config ./config
COPY alembic.ini supervisord.conf ./

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .

RUN useradd -m appuser \
 && chown -R appuser:appuser /app /tmp
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["supervisord", "-c", "/app/supervisord.conf", "-n"]
