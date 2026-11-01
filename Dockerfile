FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY .env.example ./.env.example

RUN pip install --upgrade pip && pip install .

RUN useradd --create-home --uid 10001 fetv3 \
    && mkdir -p /data /out \
    && chown -R fetv3:fetv3 /app /data /out
USER fetv3

ENV FETV3_DB=/data/schedule.db \
    FETV3_OUT=/out

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "fetv3.web.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
