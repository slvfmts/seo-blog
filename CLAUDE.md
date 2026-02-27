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

## Git workflow и деплой

**Сервер:** `root@95.163.230.43`

**Серверная схема:**
- `/opt/seo-blog` — **prod** (ветка `main`)
- `/opt/seo-blog-staging` — **staging** (ветка `dev`)

**Git workflow:**
- **По умолчанию работаю на `dev`**, не на `main`
- Коммичу и пушу в `dev` (или feature branch → merge в `dev`)
- В `main` — только merge из `dev` по команде пользователя ("кати в прод", "деплой прод")
- Claude деплоит самостоятельно, без вопросов и уточнений

**Команды деплоя:**
- `./deploy.sh staging` — деплой staging (dev → /opt/seo-blog-staging)
- `./deploy.sh prod` — деплой прод (main → /opt/seo-blog)
- `./deploy-theme.sh` — деплой темы Ghost

**Runtime:**
- Volume mount `./src:/app/src` — `git pull` обновляет код без рестарта контейнера
- НО: уже импортированные Python-модули не перезагружаются до рестарта uvicorn
- Полный рестарт: `docker compose down && docker compose up -d` (убивает фоновые задачи!)
- Docker network MTU = 1450 (хост ens3 = 1450, дефолт 1500 ломает TLS)

---

## Текущая архитектура

**Стек:** FastAPI + PostgreSQL + Redis + Ghost CMS, всё в Docker на одном сервере.

**Pipeline (реализован, 10 stages):**
```
topic → Intent → Research (Serper + Jina/Trafilatura + Wordstat/Rush) →
Structure → Drafting → Editing → Linking → SEO Polish → Quality Gate →
Meta → Formatting (DALL-E cover + Mermaid diagrams via kroki.io) → article.md
```
Каждая статья: 2-5 мин, последовательно в batch.

**Реализованные компоненты:**
- Writing Pipeline (10 stages) с промптами v2/v3
- Brief Generator (Serper.dev + Claude)
- Validators: SEO Lint, Plagiarism (similarity-based)
- Ghost Publisher (с extract scripts → codeinjection_foot, image upload, feature_image)
- Keyword Expansion (Serper.dev discovery + Yandex Wordstat/Rush Analytics volume)
- Knowledge Base (фактура / reference materials)
- Internal Linker (forward + backward cross-linking при публикации)
- Formatting: DALL-E 3 covers (через OpenAI proxy) + Mermaid diagrams (через kroki.io)
- Position Monitoring (Serper.dev SERP tracking + decay detection)
- Web UI: Jinja2 + Tailwind + HTMX
- Session-based login (bcrypt)
- LLM retry with backoff (overloaded, 429, 5xx)

**НЕ реализовано:** Temporal, BullMQ, multi-tenant, Researcher/Clustering/Strategy agents, GSC/GA4, Iteration agent.

---

## Ключевые файлы

| Файл | Назначение |
|------|------------|
| `src/api/routes/ui.py` | UI routes (все страницы) |
| `src/services/writing_pipeline/core/runner.py` | Pipeline orchestrator |
| `src/services/writing_pipeline/stages/` | Все stages (intent, research, structure, drafting, editing, linking, seo_polish, quality_gate, meta, formatting) |
| `src/services/writing_pipeline/prompts/` | Промпты (v1, v2, v3) |
| `src/services/writing_pipeline/contracts/__init__.py` | Контракты между stages |
| `src/services/writing_pipeline/data_sources/volume_provider.py` | VolumeProvider interface + routing |
| `src/services/writing_pipeline/data_sources/composite_provider.py` | CompositeVolumeProvider (Wordstat + Rush) |
| `src/services/writing_pipeline/data_sources/wordstat.py` | Yandex Wordstat provider |
| `src/services/writing_pipeline/data_sources/rush_provider.py` | Rush Analytics provider |
| `src/services/writing_pipeline/data_sources/serper.py` | Serper.dev client |
| `src/services/publisher.py` | Ghost CMS publisher |
| `src/services/generator.py` | Legacy article generator (deprecated) |
| `src/db/models.py` | SQLAlchemy models |
| `src/templates/` | Jinja2 templates |
| `src/config/settings.py` | Settings from env |
| `src/services/internal_linker.py` | Internal cross-linking engine |
| `src/services/monitoring/position_tracker.py` | SERP position monitoring (Serper.dev) |
| `src/services/monitoring/serper_serp.py` | Serper SERP client for position checks |

---

## Известные gotchas

**Keyword Volumes:**
- RU: Yandex Wordstat (broad match) + Rush Analytics (exact match) через CompositeVolumeProvider
- Non-RU: NullProvider (нет источника volume для EN/DE/etc.)
- Keyword expansion: Serper.dev для discovery, Wordstat+Rush для метрик
- При отсутствии ключей keywords сохраняются с vol=0; кнопка "Fetch Volume" позже

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
ANTHROPIC_PROXY_URL=<url>          # Cloudflare Worker proxy (optional)
ANTHROPIC_PROXY_SECRET=<secret>    # Proxy auth token (optional)
OPENAI_API_KEY=<key>               # For DALL-E cover generation
OPENAI_PROXY_URL=<url>             # OpenAI proxy for geo-blocked regions (optional)
SERPER_API_KEY=<key>               # Search + SERP position tracking
JINA_API_KEY=<key>                 # For web page extraction (optional, fallback to trafilatura)
YANDEX_WORDSTAT_API_KEY=<key>      # Yandex Wordstat (RU keyword volumes)
YANDEX_CLOUD_FOLDER_ID=<id>       # Yandex Cloud folder ID
RUSH_ANALYTICS_API_KEY=<key>       # Rush Analytics (RU keyword volumes, exact match)
AUTH_EMAIL=<email>                 # Login email
AUTH_PASSWORD_HASH=<bcrypt_hash>   # bcrypt password hash
SECRET_KEY=<key>                   # Session secret
```

---

## TODO

- [ ] Alembic миграции вместо create_all (ручные ALTER TABLE пока)
- [ ] Логирование в файл
- [ ] Health check для всех сервисов
- [ ] Deprecate generator.py в пользу Writing Pipeline
- [ ] Улучшить обложки: формат 16:9, качество генерации (EDI-89)
- [ ] Баг: [[LINK:...]] плейсхолдеры не резолвятся в HTML (EDI-90)
- [ ] Оптимизация расхода токенов (EDI-88)
