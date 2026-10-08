# Xvimo — container for Nebius Serverless AI endpoints (CPU)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000 XVIMO_DATA_DIR=/data XVIMO_RESULTS_DIR=/app/results
WORKDIR /app

COPY requirements-deploy.txt .
RUN pip install --no-cache-dir -r requirements-deploy.txt

COPY *.py *.html ./
COPY assets/ ./assets/

RUN mkdir -p /data
EXPOSE 8000
CMD ["sh", "-c", "uvicorn dialog360_bot:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
