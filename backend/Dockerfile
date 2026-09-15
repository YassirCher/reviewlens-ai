FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

RUN groupadd --gid 10001 reviewlens \
    && useradd --uid 10001 --gid reviewlens --create-home reviewlens \
    && mkdir -p /data/workspaces /data/quarantine \
    && chown -R reviewlens:reviewlens /data /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM base AS runtime
COPY --chown=reviewlens:reviewlens alembic.ini ./alembic.ini
COPY --chown=reviewlens:reviewlens migrations ./migrations
COPY --chown=reviewlens:reviewlens app ./app
USER reviewlens
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS test
COPY --chown=reviewlens:reviewlens tests ./tests
CMD ["python", "-m", "pytest", "tests", "-q"]
