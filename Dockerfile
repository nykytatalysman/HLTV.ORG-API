FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHROME_BINARY=/usr/bin/chromium \
    HLTV_DATABASE_PATH=/data/hltv-service.sqlite \
    HLTV_BROWSER=chrome \
    HLTV_HEADLESS=true \
    PORT=8000

RUN apt-get update \
    && apt-get install --no-install-recommends -y chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system hltv && useradd --system --gid hltv --home /app hltv

WORKDIR /app
COPY pyproject.toml README.md LICENSE.txt setup.cfg setup.py ./
COPY HLTV ./HLTV
COPY hltv_service ./hltv_service
RUN python -m pip install --no-cache-dir .

RUN mkdir -p /data && chown -R hltv:hltv /app /data
USER hltv

VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT','8000') + '/health', timeout=3)"

CMD ["sh", "-c", "uvicorn hltv_service.app:app --host 0.0.0.0 --port \"${PORT}\""]
