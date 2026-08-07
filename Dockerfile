FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG_PATH=/data/config.json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .
COPY app/ ./app/

# /data ist der Volume-Mount für config.json
RUN useradd --create-home --uid 1000 bot && mkdir -p /data && chown bot:bot /data
USER bot

CMD ["python", "bot.py"]
