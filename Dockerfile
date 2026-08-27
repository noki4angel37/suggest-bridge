FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BRIDGE_DB_PATH=/data/bridge.db

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system suggest && useradd --system --gid suggest suggest

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot/ ./bot/
COPY scripts/ ./scripts/

RUN mkdir -p /data && chown -R suggest:suggest /data /app

USER suggest

VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; p=os.environ.get('HEALTH_PORT','8080'); urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz', timeout=3)" || exit 1

CMD ["python", "-m", "bot.main"]
