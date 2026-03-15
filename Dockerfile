FROM python:3.12-slim

WORKDIR /app

# Cairo + fonts for SVG → PNG rendering via cairosvg
# Graceful: if apt repos unreachable, skip — cairosvg will be unavailable,
# pipeline degrades (charts skipped, covers still work)
RUN for i in 1 2 3; do apt-get update && break || sleep 5; done \
    && apt-get install -y --no-install-recommends \
    libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
    libffi-dev shared-mime-info fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    || echo "WARNING: Cairo libs install failed — SVG charts will be disabled"

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright + Chromium (optional, requires system deps)
# Skipped if apt repos are unreachable; pipeline degrades gracefully
RUN playwright install chromium 2>/dev/null || echo "Playwright Chromium install skipped (deps unavailable)"

# Копируем код
COPY src/ ./src/
COPY tests/ ./tests/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY pytest.ini .

# Порт
EXPOSE 8000

# Запуск
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
