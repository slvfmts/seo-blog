FROM python:3.12-slim

WORKDIR /app

# Mermaid CLI for diagram rendering (optional, graceful degradation)
# Requires Node.js - skip if apt repos are unreachable
RUN (apt-get update && apt-get install -y --no-install-recommends curl \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @mermaid-js/mermaid-cli \
    && rm -rf /var/lib/apt/lists/*) 2>/dev/null \
    || echo "Node.js + mmdc install skipped (apt repos unreachable)"

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
