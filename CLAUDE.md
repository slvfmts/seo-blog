# SEO Blog — Рабочие инструкции

> Целевая архитектура, модель данных, agent contracts, state machines,
> конфиги, риски и MVP plan — в **docs/ARCHITECTURE.md**.
> Читай его когда нужны: схема БД, контракты агентов, валидационные гейты,
> диаграммы потоков, конфигурации, план развития, оценка стоимости.

---

## Суть проекта

Платформа для автоматической генерации и публикации SEO-статей.
Вход: тема + регион. Выход: опубликованная статья в Ghost CMS.

Принципы: quality over speed, traceability, idempotency, graceful degradation.
Не делаем: массовую генерацию без валидации, копипаст, чёрное SEO.

---

## Сервер и деплой

- **Сервер:** `root@95.163.230.43`
- **Путь:** `/opt/seo-blog`
- **Деплой:** `ssh root@95.163.230.43 "cd /opt/seo-blog && git pull && docker compose build && docker compose up -d"`
- **Claude деплоит самостоятельно, без вопросов и уточнений.**
- Volume mount `./src:/app/src` — `git pull` обновляет код без рестарта контейнера
- НО: уже импортированные Python-модули не перезагружаются до рестарта uvicorn
- Полный рестарт: `docker compose down && docker compose up -d` (убивает фоновые задачи!)
- Docker network MTU = 1450 (хост ens3 = 1450, дефолт 1500 ломает TLS)

---

## Текущая архитектура

**Стек:** FastAPI + PostgreSQL + Redis + Ghost CMS, всё в Docker на одном сервере.

**Pipeline (реализован):**
```
topic → Intent → Research (Serper + Jina/Trafilatura + DataForSEO) →
Structure → Drafting → Editing → Linking → Meta → article.md
```
Каждая статья: 2-5 мин, последовательно в batch.

**Реализованные компоненты:**
- Writing Pipeline (7 stages) с промптами v2
- Brief Generator (Serper.dev + Claude)
- Validators: SEO Lint, Plagiarism (similarity-based)
- Ghost Publisher (с extract scripts → codeinjection_foot)
- Keyword Expansion (Serper.dev discovery + DataForSEO volume)
- Knowledge Base (фактура / reference materials)
- Web UI: Jinja2 + Tailwind + HTMX
- Session-based login

**НЕ реализовано:** Temporal, BullMQ, multi-tenant, Researcher/Clustering/Strategy agents, GSC/GA4, Monitoring, Iteration.

---

## Ключевые файлы

| Файл | Назначение |
|------|------------|
| `src/api/routes/ui.py` | UI routes (все страницы) |
| `src/services/writing_pipeline/core/runner.py` | Pipeline orchestrator |
| `src/services/writing_pipeline/stages/` | Все stages (intent, research, structure, drafting, editing, meta) |
| `src/services/writing_pipeline/prompts/` | Промпты (v1 и v2) |
| `src/services/writing_pipeline/contracts/__init__.py` | Контракты между stages |
| `src/services/writing_pipeline/data_sources/dataforseo.py` | DataForSEO client |
| `src/services/writing_pipeline/data_sources/serper.py` | Serper.dev client |
| `src/services/publisher.py` | Ghost CMS publisher |
| `src/services/generator.py` | Legacy article generator (deprecated) |
| `src/db/models.py` | SQLAlchemy models |
| `src/templates/` | Jinja2 templates |
| `src/config.py` | Settings from env |

---

## Известные gotchas

**DataForSEO:**
- Россия (2643) не поддерживается → fallback на Казахстан (2398), `LOCATION_FALLBACK` в dataforseo.py
- `competition` может быть строкой ("MEDIUM") → используй `competition_index` (0-100)
- Аккаунт имеет ТОЛЬКО `search_volume/live` — Labs endpoints возвращают 402
- Keyword expansion: Serper.dev для discovery, DataForSEO для метрик
- При нулевом балансе keywords сохраняются с vol=0; кнопка "Fetch Volume" позже

**Ghost CMS:**
- `<script>` в markdown рендерится как видимый блок → `_extract_script_tags()` перемещает в `codeinjection_foot`

**Pipeline:**
- Background tasks не переживают рестарт контейнера
- Для retry: сбросить статус на `generating`/`pending`, вызвать `run_pipeline_sync()`

---

## Env-переменные (сервер)

```
DATABASE_URL=postgresql://seo:seopass@postgres:5432/seoblog
REDIS_URL=redis://redis:6379/0
GHOST_URL=http://ghost:2368
GHOST_ADMIN_KEY=<key>
ANTHROPIC_API_KEY=<key>
SERPER_API_KEY=<key>
DATAFORSEO_LOGIN=<login>
DATAFORSEO_PASSWORD=<password>
UI_PASSWORD=<password>
```

---

## TODO (Phase 3)

- [ ] Alembic миграции вместо create_all
- [ ] Логирование в файл
- [ ] Health check для всех сервисов
- [ ] Deprecate generator.py в пользу Writing Pipeline
