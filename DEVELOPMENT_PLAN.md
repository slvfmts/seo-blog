# Development Plan — Fully Automated SEO Blog

## Верхнеуровневый план

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DEVELOPMENT ROADMAP                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  PHASE 0: Foundation          PHASE 1: Core Pipeline                        │
│  ─────────────────            ──────────────────────                        │
│  [2 weeks]                    [4 weeks]                                     │
│                                                                              │
│  ┌──────────────┐             ┌──────────────┐                              │
│  │ Project Setup│────────────▶│ Brief → Draft│                              │
│  │ + Data Model │             │ → Validation │                              │
│  └──────────────┘             └──────┬───────┘                              │
│                                      │                                       │
│                                      ▼                                       │
│  PHASE 2: Discovery           PHASE 3: Automation                           │
│  ──────────────────           ───────────────────                           │
│  [4 weeks]                    [3 weeks]                                     │
│                                                                              │
│  ┌──────────────┐             ┌──────────────┐                              │
│  │ Research →   │────────────▶│ Orchestration│                              │
│  │ Keywords →   │             │ + Publishing │                              │
│  │ Clustering   │             │ + Scheduling │                              │
│  └──────────────┘             └──────┬───────┘                              │
│                                      │                                       │
│                                      ▼                                       │
│  PHASE 4: Monitoring          PHASE 5: Production                           │
│  ───────────────────          ───────────────────                           │
│  [3 weeks]                    [4 weeks]                                     │
│                                                                              │
│  ┌──────────────┐             ┌──────────────┐                              │
│  │ GSC/GA4 +    │────────────▶│ Multi-tenant │                              │
│  │ Decay +      │             │ + Security + │                              │
│  │ Iteration    │             │ + Scale      │                              │
│  └──────────────┘             └──────────────┘                              │
│                                                                              │
│  Total: ~20 weeks (5 months)                                                │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 0: Foundation (Weeks 1-2)

**Цель:** Базовая инфраструктура, на которой будет строиться всё остальное.

### Задачи

| # | Задача | Приоритет | Выход |
|---|--------|-----------|-------|
| 0.1 | Инициализация репозитория, структура проекта | P0 | Monorepo с workspace |
| 0.2 | Docker Compose для локальной разработки | P0 | postgres, redis, работают локально |
| 0.3 | Схема БД + миграции (Alembic) | P0 | Все 14 таблиц созданы |
| 0.4 | Базовый FastAPI с health check | P0 | `/health` отвечает 200 |
| 0.5 | Конфиг-система (Pydantic Settings) | P0 | Читает .env и YAML |
| 0.6 | Логирование (structlog) | P1 | JSON-логи с correlation_id |
| 0.7 | Базовые CRUD для sites, configs | P1 | REST endpoints работают |

### Структура проекта

```
/seo-blog
├── docker-compose.yml
├── alembic/
│   └── versions/
├── src/
│   ├── api/
│   │   ├── main.py
│   │   ├── routes/
│   │   └── dependencies.py
│   ├── db/
│   │   ├── models.py
│   │   ├── session.py
│   │   └── repositories/
│   ├── config/
│   │   ├── settings.py
│   │   └── loaders.py
│   └── common/
│       ├── logging.py
│       └── exceptions.py
├── configs/
│   └── example-site/
├── tests/
└── pyproject.toml
```

### Definition of Done
- [ ] `docker-compose up` поднимает всё окружение
- [ ] Миграции применяются без ошибок
- [ ] API отвечает на `/health` и `/api/v1/sites`
- [ ] Тесты проходят (pytest)

---

## Phase 1: Core Pipeline (Weeks 3-6)

**Цель:** Работающий pipeline "Brief → Draft → Validation" — ядро системы.

### 1.1 Brief Module (Week 3)

| Задача | Описание |
|--------|----------|
| Brief CRUD | Создание/редактирование ТЗ через API |
| Brief schema | JSON-структура для sections, requirements |
| Manual brief input | UI-форма или API для ручного ввода |

### 1.2 Writer Agent (Week 4)

| Задача | Описание |
|--------|----------|
| Claude API integration | Базовый клиент с retry, rate limiting |
| Writer prompt engineering | System prompt + few-shot examples |
| Source citation | Формат `[1]`, парсинг источников |
| Draft generation | Brief → Draft с сохранением в БД |

### 1.3 Validation Pipeline (Weeks 5-6)

| Задача | Описание |
|--------|----------|
| Validation framework | Базовый интерфейс для checkers |
| SEO Lint checker | Title, meta, H1, keyword density |
| Plagiarism integration | Copyscape или Copyleaks API |
| Fact-check (basic) | LLM проверка claims + source verification |
| Validation orchestrator | Запуск всех проверок, агрегация результатов |
| Revision loop | Failed → feedback → rewrite (max 3) |

### Ключевой deliverable

```python
# Минимальный рабочий пример
brief = await brief_service.create(site_id, brief_data)
draft = await writer_agent.generate(brief)
report = await validation_pipeline.run(draft)

if report.passed:
    draft.status = "approved"
else:
    draft.status = "revision_needed"
    draft.feedback = report.revision_instructions
```

### Definition of Done
- [ ] Brief создаётся через API
- [ ] Writer генерирует draft по brief
- [ ] Validation возвращает pass/fail с деталями
- [ ] Retry loop работает (до 3 попыток)
- [ ] End-to-end тест проходит

---

## Phase 2: Discovery & Strategy (Weeks 7-10)

**Цель:** Автоматический сбор keywords и построение стратегии.

### 2.1 Researcher Agent (Week 7)

| Задача | Описание |
|--------|----------|
| Competitor discovery | По seed keywords найти топ-10 конкурентов |
| Web scraping setup | Playwright/Puppeteer для анализа страниц |
| Content gap analysis | Что есть у конкурентов, чего нет у нас |

### 2.2 Keyword Module (Week 8)

| Задача | Описание |
|--------|----------|
| SEO API integration | DataForSEO или Ahrefs API |
| Keyword collection | Volume, difficulty, CPC, intent |
| SERP analysis | Какие фичи в выдаче, кто в топе |
| Caching layer | Redis кэш для SERP данных (TTL 24h) |

### 2.3 Clustering Agent (Week 9)

| Задача | Описание |
|--------|----------|
| Embedding generation | OpenAI/Voyage embeddings для keywords |
| Clustering algorithm | HDBSCAN или k-means по embeddings |
| Intent classification | LLM классификация по 4 типам intent |
| Topic hierarchy | Pillar → Cluster → Supporting |

### 2.4 Strategy Agent (Week 10)

| Задача | Описание |
|--------|----------|
| Priority scoring | Volume × (1/difficulty) × intent_weight |
| Roadmap generation | Распределение по неделям |
| Quick wins detection | Low difficulty + decent volume |
| Auto brief creation | Cluster → Brief автоматически |

### Definition of Done
- [ ] По теме находит 50+ релевантных keywords
- [ ] Keywords группируются в кластеры (accuracy >80%)
- [ ] Стратегия генерируется с приоритетами
- [ ] Briefs создаются автоматически из кластеров

---

## Phase 3: Automation & Publishing (Weeks 11-13)

**Цель:** Полная автоматизация от темы до публикации.

### 3.1 Task Queue (Week 11)

| Задача | Описание |
|--------|----------|
| BullMQ setup | Redis-based queue |
| Job types | research, write, validate, publish |
| Priority queues | Urgent, normal, background |
| Dead letter queue | Для failed jobs |
| Retry policies | Exponential backoff |

### 3.2 WordPress Adapter (Week 12)

| Задача | Описание |
|--------|----------|
| WP REST API client | Posts, media, categories |
| Image handling | Upload featured image |
| SEO plugin support | Yoast/RankMath meta fields |
| Draft → Publish | Статусы, scheduling |

### 3.3 Orchestrator (Week 13)

| Задача | Описание |
|--------|----------|
| Pipeline definition | Topic → Published post workflow |
| State management | Durable state между шагами |
| Error handling | Compensation, rollback |
| Rate limiting | Per-tenant, per-API limits |
| Scheduling | Публикация по расписанию |

### Definition of Done
- [ ] Полный pipeline работает автоматически
- [ ] Статья публикуется в WordPress
- [ ] Rate limits соблюдаются
- [ ] Errors обрабатываются gracefully

---

## Phase 4: Monitoring & Iteration (Weeks 14-16)

**Цель:** Отслеживание результатов и автоматическое улучшение.

### 4.1 Analytics Integration (Week 14)

| Задача | Описание |
|--------|----------|
| GSC API | Позиции, impressions, clicks |
| GA4 API | Traffic, behavior metrics |
| Data sync | Daily cron для сбора метрик |
| Metrics storage | post_metrics таблица |

### 4.2 Analyst Agent (Week 15)

| Задача | Описание |
|--------|----------|
| Performance tracking | Динамика позиций по дням |
| Decay detection | Падение >5 позиций за неделю |
| Opportunity finder | Keywords близко к топ-10 |
| Report generation | Weekly/monthly summaries |

### 4.3 Iteration Pipeline (Week 16)

| Задача | Описание |
|--------|----------|
| Update triggers | Decay, freshness, manual |
| Delta brief | Что обновить в существующей статье |
| Content merge | Обновление без потери структуры |
| Re-validation | Проверка обновлённого контента |

### Definition of Done
- [ ] Метрики собираются автоматически
- [ ] Decay детектируется в течение 48h
- [ ] Обновление статьи работает end-to-end
- [ ] Dashboard показывает ключевые метрики

---

## Phase 5: Production Ready (Weeks 17-20)

**Цель:** Готовность к multi-tenant SaaS.

### 5.1 Multi-tenancy (Week 17)

| Задача | Описание |
|--------|----------|
| Tenant isolation | Row-level security в PostgreSQL |
| Tenant context | Middleware для tenant_id |
| Per-tenant configs | Отдельные лимиты, API keys |
| Data encryption | Encrypt at rest (credentials) |

### 5.2 Security & Auth (Week 18)

| Задача | Описание |
|--------|----------|
| Authentication | JWT + refresh tokens |
| RBAC | Admin, editor, viewer roles |
| API keys | Для внешних интеграций |
| Audit logging | Все действия в audit_log |
| Secrets management | Vault или AWS Secrets |

### 5.3 Observability (Week 19)

| Задача | Описание |
|--------|----------|
| Prometheus metrics | Latency, throughput, errors |
| Grafana dashboards | Бизнес + техн. метрики |
| Alerting | PagerDuty/Slack интеграция |
| Distributed tracing | OpenTelemetry |
| Log aggregation | Loki или ELK |

### 5.4 Scale & Reliability (Week 20)

| Задача | Описание |
|--------|----------|
| Load testing | k6 на 100 concurrent pipelines |
| Performance tuning | Query optimization, indexes |
| Horizontal scaling | Stateless workers |
| Backup & recovery | Automated backups, tested restore |
| Runbooks | Процедуры для on-call |

### Definition of Done
- [ ] 10 tenants работают изолированно
- [ ] Auth + RBAC работает
- [ ] Dashboards и alerting настроены
- [ ] Load test пройден
- [ ] Security audit пройден

---

## С чего начать: Critical Path

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CRITICAL PATH (первые 6 недель)                      │
└─────────────────────────────────────────────────────────────────────────────┘

Week 1-2: Foundation
━━━━━━━━━━━━━━━━━━━━
   WHY: Без инфраструктуры нельзя ничего тестировать

   DO FIRST:
   ├── 1. Docker Compose (postgres + redis)
   ├── 2. Alembic + базовые миграции (sites, briefs, drafts)
   ├── 3. FastAPI skeleton + health check
   └── 4. Pydantic Settings для конфигов

   SKIP FOR NOW:
   ├── Admin UI (делаем в Phase 2)
   ├── Полная схема БД (добавим по мере надобности)
   └── CI/CD (пока локально)


Week 3-4: Writer Agent (CORE VALUE)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   WHY: Это главная ценность продукта — качественный контент

   DO FIRST:
   ├── 1. Claude API client с retry
   ├── 2. Writer system prompt (итеративно улучшать)
   ├── 3. Brief → Draft generation
   └── 4. Source citation parsing

   MEASURE:
   ├── Quality score (manual review 10 статей)
   ├── Hallucination rate
   └── Source accuracy


Week 5-6: Validation Pipeline (QUALITY GATE)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   WHY: Без валидации нельзя публиковать — риск репутации

   DO FIRST:
   ├── 1. SEO Lint (title, meta, H1, density) — простое, своё
   ├── 2. Plagiarism check (Copyscape API)
   ├── 3. Basic fact-check (LLM + web search)
   └── 4. Validation orchestrator + retry loop

   SKIP FOR NOW:
   ├── Legal check (добавим позже для YMYL)
   ├── Brand check (нужен когда будут guidelines)
   └── Cannibalization (нужен когда много статей)
```

---

## Рекомендация: Начать с Proof of Concept

**Перед Phase 0** рекомендую сделать однодневный PoC:

```python
# poc.py — запустить локально, проверить core hypothesis

import anthropic

def generate_article(topic: str, keywords: list[str]) -> str:
    """Проверяем: может ли Claude писать качественные SEO-статьи?"""

    client = anthropic.Anthropic()

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        system="""You are an expert SEO content writer.
        Write factual, well-researched articles with proper citations.
        Every claim must have a source.""",
        messages=[{
            "role": "user",
            "content": f"""Write an SEO-optimized article about: {topic}

            Target keywords: {', '.join(keywords)}

            Requirements:
            - 1500-2000 words
            - Include H2 and H3 headings
            - Cite sources for all statistics
            - Natural keyword placement
            """
        }]
    )

    return response.content[0].text

# Test
article = generate_article(
    topic="best practices for remote team management",
    keywords=["remote team management", "virtual team collaboration", "remote work tips"]
)
print(article)
```

**Что проверить в PoC:**
1. Качество текста (читаемость, структура)
2. Наличие реальных источников (не выдуманных)
3. SEO-оптимизация (keywords в заголовках, плотность)
4. Отсутствие явных галлюцинаций

**Если PoC успешен** → переходим к Phase 0.

---

## Приоритеты по бизнес-ценности

```
HIGH VALUE + LOW EFFORT (делать первым)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
├── Writer Agent — core product value
├── SEO Lint — простая автоматизация
├── WordPress publish — замыкает цикл

HIGH VALUE + HIGH EFFORT (делать вторым)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
├── Keyword research automation
├── Clustering + Strategy
├── Full validation pipeline

LOW VALUE + LOW EFFORT (делать когда время есть)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
├── Admin UI polish
├── Additional CMS adapters
├── Advanced reporting

LOW VALUE + HIGH EFFORT (делать последним или никогда)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
├── Custom ML models for clustering
├── Real-time SERP tracking
├── Complex multi-language support
```

---

## Immediate Next Steps

1. **Сегодня:** Запустить PoC скрипт, оценить качество генерации
2. **Завтра:** Инициализировать репозиторий, настроить Docker Compose
3. **Неделя 1:** Базовая схема БД + FastAPI skeleton
4. **Неделя 2:** Claude API client + первый Writer Agent
5. **Неделя 3:** Сгенерировать 10 тестовых статей, manual review

---

*Last updated: 2026-02-02*
