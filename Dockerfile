FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY pyproject.toml README.md ./
RUN mkdir -p src/crypto_smc src/smc_core \
    && touch src/crypto_smc/__init__.py src/smc_core/__init__.py
RUN pip install --no-cache-dir ".[dev]"
RUN rm -rf src

COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
COPY tests ./tests

RUN chown -R app:app /app

USER app

CMD ["uvicorn", "crypto_smc.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
