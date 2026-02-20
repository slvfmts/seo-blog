FROM python:3.12-slim

WORKDIR /app

# System deps for Playwright and Mermaid
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Mermaid CLI for diagram rendering (optional, graceful degradation)
RUN npm install -g @mermaid-js/mermaid-cli 2>/dev/null || echo "mmdc install skipped"

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
