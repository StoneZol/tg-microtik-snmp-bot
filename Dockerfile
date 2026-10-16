FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BOT_LANG=en \
    CONFIG_DIR=/app/config/devices \
    STATE_DIR=/data/state \
    HOOK_HOST=0.0.0.0 \
    HOOK_PORT=8443

EXPOSE 8443

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY locales/ ./locales/
COPY config/ ./config/

RUN mkdir -p /data/state

ENV PYTHONPATH=/app/src

CMD ["python", "-m", "mikrotik_bot"]
