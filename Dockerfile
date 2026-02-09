FROM python:3.12-slim

WORKDIR /app

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright + Chromium (optional, requires system deps)
# Skipped if apt repos are unreachable; pipeline degrades gracefully
RUN playwright install chromium 2>/dev/null || echo "Playwright Chromium install skipped (deps unavailable)"

# Копируем код
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini .

# Порт
EXPOSE 8000

# Запуск
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
