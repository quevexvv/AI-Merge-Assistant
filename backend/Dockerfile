FROM python:3.13-slim

# Установка системных зависимостей для libpq (PostgreSQL) и сборки
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем и устанавливаем зависимости
COPY requirements.txt requirements-input.txt requirements-llm.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt -r requirements-input.txt -r requirements-llm.txt

# Копируем код приложения
COPY app/ ./app/
COPY alembic.ini .
COPY alembic/ ./alembic/

# Устанавливаем переменные окружения по умолчанию
ENV PYTHONUNBUFFERED=1
ENV REDIS_URL=redis://redis:6379/0
ENV OFFLINE_DEMO_MODE=false

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
