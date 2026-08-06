FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    REDIS_URL=redis://redis:6379/0 \
    TZ=Pacific/Noumea

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates/ templates/

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER 10001

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn app:asgi_app --host 0.0.0.0 --port ${PORT}"]
