FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system commerce \
    && adduser --system --ingroup commerce --home /app commerce

COPY pyproject.toml README.md alembic.ini ./
COPY src ./src
COPY migrations ./migrations

RUN python -m pip install --upgrade pip \
    && python -m pip install . \
    && chown -R commerce:commerce /app

USER commerce

EXPOSE 8010

CMD ["commerce-copilot"]
