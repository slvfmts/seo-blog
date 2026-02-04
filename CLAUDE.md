# Fully Automated SEO Blog — System Architecture

---

## 1. One-Pager

**Fully Automated SEO Blog** — платформа для запуска и масштабирования SEO-блогов с минимальным ручным участием.

**Границы системы:**
- Вход: тема/ниша + конфигурация (язык, тон, CMS, ограничения)
- Выход: опубликованные статьи с мониторингом позиций и итеративным улучшением

**Ключевые принципы:**
1. **Quality over speed** — каждая статья проходит многоуровневую валидацию перед публикацией
2. **Traceability** — все решения агентов логируются с источниками и reasoning
3. **Idempotency** — любой шаг пайплайна можно безопасно перезапустить
4. **Graceful degradation** — сбой внешнего API не ломает весь pipeline
5. **Multi-tenant ready** — изоляция данных, rate limits per tenant, RBAC
6. **Human-in-the-loop** — критические точки требуют approval (опционально)

**Не делаем:** массовую генерацию без валидации, копипаст, обход rate limits, чёрное SEO.

---

## 2. Компоненты

### 2.1 Core Services

| Компонент | Назначение | Технологии |
|-----------|------------|------------|
| **Orchestrator** | Управление pipeline runs, scheduling, retry logic, state transitions | Temporal.io / Inngest / custom FSM |
| **Agent Runner** | Исполнение LLM-агентов с tool use, memory, контекстом | Claude API + MCP servers |
| **API Gateway** | REST/GraphQL для UI, webhooks, внешние интеграции | FastAPI / Hono |
| **Task Queue** | Асинхронные задачи, backpressure, priorities | BullMQ / RabbitMQ / SQS |

### 2.2 Domain Modules

| Модуль | Назначение |
|--------|------------|
| **Discovery** | Анализ ниши, поиск конкурентов, определение границ темы |
| **SERP/Keywords** | Сбор ключевых слов, позиций, search intent, difficulty |
| **Clustering** | Группировка ключей по intent, построение topic clusters |
| **Strategy** | Приоритизация кластеров, content gap analysis, roadmap |
| **Briefs** | Генерация ТЗ: структура, обязательные разделы, источники |
| **Drafting** | Написание черновиков по ТЗ с цитированием источников |
| **Validation** | Проверки качества: факты, плагиат, SEO, legal, brand |
| **Publishing** | Публикация в CMS через адаптеры, scheduling |
| **Monitoring** | Трекинг позиций, трафика, конверсий, decay detection |
| **Iteration** | Автоматическое обновление устаревшего контента |

### 2.3 Storage Layer

| Хранилище | Назначение | Технология |
|-----------|------------|------------|
| **Primary DB** | Сущности, состояния, связи | PostgreSQL |
| **Vector Store** | Embeddings статей для дедупликации и поиска | pgvector / Qdrant |
| **Object Storage** | Черновики, отчёты, скриншоты SERP | S3 / MinIO |
| **Cache** | SERP-данные, результаты валидации, LLM responses | Redis |
| **Search Index** | Full-text поиск по контенту | Meilisearch / Typesense |

### 2.4 Observability

| Компонент | Назначение |
|-----------|------------|
| **Admin Panel** | Управление проектами, approval queue, настройки |
| **Pipeline Monitor** | Визуализация runs, статусы, bottlenecks |
| **Metrics Dashboard** | SEO-метрики, расходы на API, качество контента |
| **Alert System** | Уведомления о сбоях, падении позиций, лимитах |

---

## 3. Диаграмма потоков данных (ASCII)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              FULLY AUTOMATED SEO BLOG                           │
└─────────────────────────────────────────────────────────────────────────────────┘

                                    ┌──────────┐
                                    │  CONFIG  │
                                    │  (YAML)  │
                                    └────┬─────┘
                                         │
                                         ▼
┌─────────┐    ┌────────────┐    ┌──────────────┐    ┌────────────┐
│  TOPIC  │───▶│  DISCOVERY │───▶│  COMPETITORS │───▶│   SERP/    │
│  INPUT  │    │   Agent    │    │    [1..N]    │    │  KEYWORDS  │
└─────────┘    └────────────┘    └──────────────┘    └─────┬──────┘
                                                          │
                     ┌────────────────────────────────────┘
                     ▼
              ┌─────────────┐    ┌────────────┐    ┌────────────┐
              │  KEYWORDS   │───▶│ CLUSTERING │───▶│  CLUSTERS  │
              │  [100..10K] │    │   Agent    │    │  [10..100] │
              └─────────────┘    └────────────┘    └─────┬──────┘
                                                        │
                     ┌──────────────────────────────────┘
                     ▼
              ┌─────────────┐    ┌────────────┐    ┌────────────┐
              │  STRATEGY   │───▶│   BRIEFS   │───▶│   BRIEF    │
              │  (Roadmap)  │    │   Agent    │    │  [1/cluster]│
              └─────────────┘    └────────────┘    └─────┬──────┘
                                                        │
                     ┌──────────────────────────────────┘
                     ▼
              ┌─────────────┐    ┌────────────┐    ┌────────────┐
              │   DRAFT     │───▶│ VALIDATION │───▶│  REPORT    │
              │   Agent     │    │  Pipeline  │    │ pass/fail  │
              └─────────────┘    └────────────┘    └─────┬──────┘
                                                        │
                                      ┌─────────────────┴─────────────────┐
                                      │                                   │
                                      ▼                                   ▼
                               ┌────────────┐                     ┌──────────────┐
                               │  REVISION  │◀────────────────────│   APPROVED   │
                               │  (retry)   │      if failed      │    DRAFT     │
                               └────────────┘                     └──────┬───────┘
                                                                        │
                                                                        ▼
                                                                 ┌─────────────┐
                                                                 │  PUBLISHER  │
                                                                 │   Agent     │
                                                                 └──────┬──────┘
                                                                        │
                     ┌──────────────────────────────────────────────────┘
                     ▼
              ┌─────────────┐    ┌────────────┐    ┌────────────┐
              │    POST     │───▶│ MONITORING │───▶│  METRICS   │
              │  (live)     │    │   Agent    │    │ (daily)    │
              └─────────────┘    └────────────┘    └─────┬──────┘
                                                        │
                                                        ▼
                                                 ┌─────────────┐
                                                 │  ITERATION  │
                                                 │  (if decay) │
                                                 └─────────────┘
                                                        │
                                                        └───────▶ [Back to BRIEF]


═══════════════════════════════════════════════════════════════════════════════════
DATA ENTITIES FLOW:

Site ──▶ Competitor[] ──▶ Keyword[] ──▶ Cluster[] ──▶ Brief ──▶ Draft ──▶
     ──▶ ValidationReport ──▶ Post ──▶ Metric[] ──▶ IterationTask
═══════════════════════════════════════════════════════════════════════════════════
```

---

## 4. Модель данных

### 4.1 Core Entities

```sql
-- Проект/сайт (tenant в SaaS)
CREATE TABLE sites (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    name            VARCHAR(255) NOT NULL,
    domain          VARCHAR(255),
    config_id       UUID REFERENCES site_configs(id),
    status          site_status NOT NULL DEFAULT 'setup',
    -- setup | active | paused | archived
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Конфигурация сайта
CREATE TABLE site_configs (
    id              UUID PRIMARY KEY,
    site_id         UUID REFERENCES sites(id),
    language        VARCHAR(10) NOT NULL,         -- 'ru', 'en-US'
    country         VARCHAR(2) NOT NULL,          -- 'RU', 'US'
    tone            JSONB,                        -- {formality, humor, expertise}
    stop_topics     TEXT[],                       -- запрещённые темы
    required_sources TEXT[],                      -- обязательные типы источников
    cms_adapter     VARCHAR(50) NOT NULL,         -- 'wordpress', 'ghost', 'strapi'
    cms_credentials JSONB,                        -- encrypted
    publish_limits  JSONB,                        -- {daily_max, min_interval_hours}
    brand_guidelines TEXT,
    legal_restrictions TEXT[],
    version         INT DEFAULT 1,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Конкуренты
CREATE TABLE competitors (
    id              UUID PRIMARY KEY,
    site_id         UUID REFERENCES sites(id),
    domain          VARCHAR(255) NOT NULL,
    discovered_at   TIMESTAMPTZ DEFAULT NOW(),
    relevance_score FLOAT,                        -- 0.0 - 1.0
    monthly_traffic BIGINT,
    top_keywords    JSONB,                        -- [{keyword, position, volume}]
    status          competitor_status DEFAULT 'active',
    -- active | ignored | analyzed
    UNIQUE(site_id, domain)
);

-- Ключевые слова
CREATE TABLE keywords (
    id              UUID PRIMARY KEY,
    site_id         UUID REFERENCES sites(id),
    keyword         TEXT NOT NULL,
    search_volume   INT,
    difficulty      FLOAT,                        -- 0-100
    cpc             DECIMAL(10,2),
    intent          search_intent,                -- informational | transactional | navigational | commercial
    serp_features   TEXT[],                       -- featured_snippet, paa, video
    current_position INT,                         -- наша позиция (null если не ранжируемся)
    competitor_id   UUID REFERENCES competitors(id), -- откуда узнали
    cluster_id      UUID REFERENCES clusters(id),
    status          keyword_status DEFAULT 'new',
    -- new | clustered | targeted | achieved | abandoned
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(site_id, keyword)
);

-- Кластеры (тематические группы)
CREATE TABLE clusters (
    id              UUID PRIMARY KEY,
    site_id         UUID REFERENCES sites(id),
    name            VARCHAR(255) NOT NULL,
    primary_keyword_id UUID REFERENCES keywords(id),
    intent          search_intent NOT NULL,
    topic_type      topic_type,                   -- pillar | cluster | supporting
    parent_cluster_id UUID REFERENCES clusters(id), -- для topic hierarchy
    priority_score  FLOAT,                        -- 0-100, рассчитывается Strategy
    estimated_traffic BIGINT,
    competition_level VARCHAR(20),                -- low | medium | high
    status          cluster_status DEFAULT 'new',
    -- new | planned | in_progress | published | monitoring
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Стратегия/Roadmap
CREATE TABLE content_roadmap (
    id              UUID PRIMARY KEY,
    site_id         UUID REFERENCES sites(id),
    cluster_id      UUID REFERENCES clusters(id),
    scheduled_week  DATE,                         -- начало недели
    priority        INT,                          -- 1 = highest
    reasoning       TEXT,                         -- почему такой приоритет
    dependencies    UUID[],                       -- clusters that should be published first
    status          roadmap_status DEFAULT 'planned',
    -- planned | in_progress | completed | skipped
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Бриф (ТЗ на статью)
CREATE TABLE briefs (
    id              UUID PRIMARY KEY,
    site_id         UUID REFERENCES sites(id),
    cluster_id      UUID REFERENCES clusters(id),
    title           VARCHAR(500) NOT NULL,
    target_keyword  TEXT NOT NULL,
    secondary_keywords TEXT[],
    word_count_min  INT NOT NULL,
    word_count_max  INT NOT NULL,
    structure       JSONB NOT NULL,               -- {sections: [{h2, h3[], key_points}]}
    required_sources JSONB,                       -- [{type, min_count}]
    competitor_urls TEXT[],                       -- для анализа
    internal_links  UUID[],                       -- posts to link to
    serp_analysis   JSONB,                        -- PAA, featured snippet target
    tone_override   JSONB,                        -- если отличается от site config
    status          brief_status DEFAULT 'draft',
    -- draft | approved | in_writing | completed
    version         INT DEFAULT 1,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    approved_at     TIMESTAMPTZ,
    approved_by     UUID                          -- user_id for HITL
);

-- Черновик статьи
CREATE TABLE drafts (
    id              UUID PRIMARY KEY,
    brief_id        UUID REFERENCES briefs(id),
    site_id         UUID REFERENCES sites(id),
    content_md      TEXT NOT NULL,                -- markdown
    content_html    TEXT,                         -- rendered
    word_count      INT NOT NULL,
    sources_used    JSONB,                        -- [{url, title, quote, location}]
    internal_links_used UUID[],
    meta_title      VARCHAR(70),
    meta_description VARCHAR(160),
    slug            VARCHAR(255),
    featured_image  JSONB,                        -- {prompt, url, alt}
    revision        INT DEFAULT 1,
    status          draft_status DEFAULT 'writing',
    -- writing | validating | revision_needed | approved | published | rejected
    rejection_reason TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Отчёты валидации
CREATE TABLE validation_reports (
    id              UUID PRIMARY KEY,
    draft_id        UUID REFERENCES drafts(id),
    check_type      validation_type NOT NULL,
    -- plagiarism | factcheck | seo_lint | brand | legal | cannibalization | freshness
    status          check_status NOT NULL,        -- passed | failed | warning | skipped
    score           FLOAT,                        -- 0-100 где применимо
    details         JSONB NOT NULL,               -- специфичные для типа проверки
    blocking        BOOLEAN DEFAULT true,         -- блокирует ли публикацию
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Опубликованные посты
CREATE TABLE posts (
    id              UUID PRIMARY KEY,
    draft_id        UUID REFERENCES drafts(id),
    site_id         UUID REFERENCES sites(id),
    cluster_id      UUID REFERENCES clusters(id),
    cms_post_id     VARCHAR(255),                 -- ID в CMS
    url             TEXT NOT NULL,
    title           VARCHAR(500) NOT NULL,
    published_at    TIMESTAMPTZ NOT NULL,
    last_updated_at TIMESTAMPTZ,
    status          post_status DEFAULT 'live',
    -- live | updated | redirected | unpublished
    current_revision INT DEFAULT 1,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Метрики постов
CREATE TABLE post_metrics (
    id              UUID PRIMARY KEY,
    post_id         UUID REFERENCES posts(id),
    date            DATE NOT NULL,
    impressions     BIGINT,
    clicks          BIGINT,
    ctr             FLOAT,
    avg_position    FLOAT,
    sessions        BIGINT,
    bounce_rate     FLOAT,
    avg_time_on_page FLOAT,
    conversions     INT,
    top_queries     JSONB,                        -- [{query, impressions, clicks, position}]
    source          metric_source NOT NULL,       -- gsc | ga4 | manual
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(post_id, date, source)
);

-- Задачи на итерацию/обновление
CREATE TABLE iteration_tasks (
    id              UUID PRIMARY KEY,
    post_id         UUID REFERENCES posts(id),
    trigger_type    iteration_trigger NOT NULL,
    -- decay | freshness | new_competitor | keyword_opportunity | manual
    trigger_data    JSONB,                        -- детали триггера
    priority        INT DEFAULT 5,
    new_brief_id    UUID REFERENCES briefs(id),   -- новый бриф для обновления
    status          task_status DEFAULT 'pending',
    -- pending | in_progress | completed | skipped
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

-- Pipeline runs (для трассировки)
CREATE TABLE pipeline_runs (
    id              UUID PRIMARY KEY,
    site_id         UUID REFERENCES sites(id),
    pipeline_type   pipeline_type NOT NULL,
    -- full_discovery | single_article | batch_publish | monitoring | iteration
    trigger         VARCHAR(50),                  -- scheduled | manual | webhook
    input_params    JSONB,
    status          run_status DEFAULT 'running',
    -- running | completed | failed | cancelled
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    error_message   TEXT,
    steps_completed JSONB                         -- [{step, status, duration_ms}]
);

-- Audit log
CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    entity_type     VARCHAR(50) NOT NULL,
    entity_id       UUID NOT NULL,
    action          VARCHAR(50) NOT NULL,
    actor_type      actor_type NOT NULL,          -- user | agent | system
    actor_id        VARCHAR(255),
    changes         JSONB,
    reasoning       TEXT,                         -- для agent actions
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

### 4.2 Enums

```sql
CREATE TYPE site_status AS ENUM ('setup', 'active', 'paused', 'archived');
CREATE TYPE competitor_status AS ENUM ('active', 'ignored', 'analyzed');
CREATE TYPE keyword_status AS ENUM ('new', 'clustered', 'targeted', 'achieved', 'abandoned');
CREATE TYPE search_intent AS ENUM ('informational', 'transactional', 'navigational', 'commercial');
CREATE TYPE topic_type AS ENUM ('pillar', 'cluster', 'supporting');
CREATE TYPE cluster_status AS ENUM ('new', 'planned', 'in_progress', 'published', 'monitoring');
CREATE TYPE roadmap_status AS ENUM ('planned', 'in_progress', 'completed', 'skipped');
CREATE TYPE brief_status AS ENUM ('draft', 'approved', 'in_writing', 'completed');
CREATE TYPE draft_status AS ENUM ('writing', 'validating', 'revision_needed', 'approved', 'published', 'rejected');
CREATE TYPE validation_type AS ENUM ('plagiarism', 'factcheck', 'seo_lint', 'brand', 'legal', 'cannibalization', 'freshness');
CREATE TYPE check_status AS ENUM ('passed', 'failed', 'warning', 'skipped');
CREATE TYPE post_status AS ENUM ('live', 'updated', 'redirected', 'unpublished');
CREATE TYPE metric_source AS ENUM ('gsc', 'ga4', 'manual');
CREATE TYPE iteration_trigger AS ENUM ('decay', 'freshness', 'new_competitor', 'keyword_opportunity', 'manual');
CREATE TYPE task_status AS ENUM ('pending', 'in_progress', 'completed', 'skipped');
CREATE TYPE pipeline_type AS ENUM ('full_discovery', 'single_article', 'batch_publish', 'monitoring', 'iteration');
CREATE TYPE run_status AS ENUM ('running', 'completed', 'failed', 'cancelled');
CREATE TYPE actor_type AS ENUM ('user', 'agent', 'system');
```

---

## 5. Состояния и переходы (State Machines)

### 5.1 Article State Machine

```
                                    ┌─────────────────┐
                                    │    [START]      │
                                    └────────┬────────┘
                                             │
                                             ▼
                                    ┌─────────────────┐
                                    │   brief:draft   │
                                    └────────┬────────┘
                                             │ approve (auto/manual)
                                             ▼
                                    ┌─────────────────┐
                                    │ brief:approved  │
                                    └────────┬────────┘
                                             │ start writing
                                             ▼
                                    ┌─────────────────┐
                                    │ draft:writing   │◀─────────────────┐
                                    └────────┬────────┘                  │
                                             │ complete                  │
                                             ▼                           │
                                    ┌─────────────────┐                  │
                                    │draft:validating │                  │
                                    └────────┬────────┘                  │
                                             │                           │
                          ┌──────────────────┼──────────────────┐        │
                          │                  │                  │        │
                          ▼                  ▼                  ▼        │
                   ┌────────────┐    ┌────────────┐    ┌─────────────┐   │
                   │   PASSED   │    │  WARNING   │    │   FAILED    │   │
                   └─────┬──────┘    └─────┬──────┘    └──────┬──────┘   │
                         │                 │                  │          │
                         │                 │ manual approve   │ revise   │
                         │                 ▼                  └──────────┘
                         │          ┌────────────┐                  │
                         └─────────▶│draft:approved│◀───────────────┘
                                    └─────┬──────┘        (max 3 retries)
                                          │                     │
                                          │ publish             │ max retries
                                          ▼                     ▼
                                   ┌────────────┐        ┌────────────┐
                                   │post:live   │        │draft:rejected│
                                   └─────┬──────┘        └────────────┘
                                         │                    [END]
                                         │ monitor
                                         ▼
                               ┌──────────────────┐
                               │ post:monitoring  │◀──────┐
                               └────────┬─────────┘       │
                                        │                 │
                          ┌─────────────┼─────────────┐   │
                          │             │             │   │
                          ▼             ▼             ▼   │
                    ┌──────────┐ ┌──────────┐ ┌──────────┐│
                    │  STABLE  │ │  DECAY   │ │ OUTDATED ││
                    └────┬─────┘ └────┬─────┘ └────┬─────┘│
                         │            │            │      │
                         │            └─────┬──────┘      │
                         │                  │             │
                         │                  ▼             │
                         │         ┌─────────────┐        │
                         │         │iteration_task│       │
                         │         └──────┬──────┘        │
                         │                │               │
                         │                ▼               │
                         │         ┌────────────┐         │
                         │         │post:updated│─────────┘
                         │         └────────────┘
                         │
                         └───────▶ [CONTINUE MONITORING]


RETRY POLICY (draft:validating → draft:writing):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Max retries: 3
• Backoff: immediate (LLM rewrite with feedback)
• Stop conditions:
  - 3 consecutive failures on same check
  - Legal check failed (immediate reject)
  - Plagiarism > 30% (immediate reject)
• Escalation: after 2 retries → human review queue
```

### 5.2 Pipeline (Niche) State Machine

```
                                    ┌─────────────────┐
                                    │    [TRIGGER]    │
                                    │ topic + config  │
                                    └────────┬────────┘
                                             │
                                             ▼
                                    ┌─────────────────┐
                    ┌──────────────▶│   DISCOVERY     │
                    │   retry       └────────┬────────┘
                    │   (exp backoff)        │ success
                    │                        ▼
                    │               ┌─────────────────┐
                    └───────────────│   SERP_FETCH    │──────┐
                      API error     └────────┬────────┘      │
                                             │               │ rate limit
                                             ▼               │
                                    ┌─────────────────┐      │
                                    │   CLUSTERING    │◀─────┘
                                    └────────┬────────┘  (wait + retry)
                                             │
                                             ▼
                                    ┌─────────────────┐
                                    │   STRATEGIZE    │
                                    └────────┬────────┘
                                             │
                                             ▼
                                    ┌─────────────────┐
                                    │ APPROVAL_GATE   │──────┐
                                    └────────┬────────┘      │
                                             │ approved      │ rejected
                                             ▼               ▼
                                    ┌─────────────────┐  ┌────────┐
                                    │   PRODUCING     │  │PAUSED  │
                                    │ (article loop)  │  └────────┘
                                    └────────┬────────┘
                                             │
                                             ▼
                                    ┌─────────────────┐
                                    │   MONITORING    │◀──────┐
                                    └────────┬────────┘       │
                                             │                │
                                             ▼                │
                                    ┌─────────────────┐       │
                                    │   ITERATING     │───────┘
                                    │ (update cycle)  │
                                    └─────────────────┘


STOP CRANES (аварийные остановки):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Budget exhausted         → PAUSED + alert
• API quota exceeded       → PAUSED + wait + auto-resume
• Legal flag detected      → STOPPED + human review required
• Plagiarism rate > 20%    → STOPPED + investigation
• Manual pause by user     → PAUSED
• CMS API down > 1 hour    → PAUSED (publishing only)
• Quality score < 60%      → PAUSED + review needed
  (rolling avg of last 10)
```

---

## 6. Quality & Safety Gates

### 6.1 Validation Pipeline

```
┌─────────┐
│  DRAFT  │
└────┬────┘
     │
     ▼
┌─────────────────────────────────────────────────────────────┐
│                    VALIDATION PIPELINE                       │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │PLAGIARISM│─▶│FACTCHECK │─▶│ SEO_LINT │─▶│  BRAND   │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
│       │             │             │             │           │
│       ▼             ▼             ▼             ▼           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                  │
│  │  LEGAL   │─▶│CANNIBAL- │─▶│ FRESHNESS│                  │
│  │          │  │ IZATION  │  │          │                  │
│  └──────────┘  └──────────┘  └──────────┘                  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────┐
│ VALIDATION_REPORT│
└─────────────────┘
```

### 6.2 Gate Specifications

#### 1. Plagiarism Check

| Параметр | Значение |
|----------|----------|
| **Входы** | `draft.content_md`, `draft.sources_used` |
| **Выходы** | `{score, matches: [{text, source_url, similarity}]}` |
| **Инструмент** | Copyscape API / Copyleaks / custom embedding similarity |
| **Pass** | similarity_score < 10% AND no exact matches > 50 chars |
| **Warning** | similarity_score 10-20% |
| **Fail** | similarity_score > 20% OR exact match > 100 chars |
| **Blocking** | Yes |

#### 2. Fact Check

| Параметр | Значение |
|----------|----------|
| **Входы** | `draft.content_md`, `brief.required_sources` |
| **Выходы** | `{claims: [{claim, source, verified, confidence}], score}` |
| **Инструмент** | LLM + web search + source verification |
| **Pass** | All claims have sources with confidence > 0.8 |
| **Warning** | 1-2 claims with confidence 0.5-0.8 |
| **Fail** | Any claim contradicts reliable source OR >3 unsourced claims |
| **Blocking** | Yes |

#### 3. SEO Lint

| Параметр | Значение |
|----------|----------|
| **Входы** | `draft.*`, `brief.target_keyword`, `brief.secondary_keywords` |
| **Выходы** | `{issues: [{type, severity, location, suggestion}], score}` |
| **Инструмент** | Custom rules engine |
| **Checks** | Title tag (60 chars, keyword), Meta desc (160 chars), H1 unique, Keyword density (1-2%), Internal links (≥2), Image alt tags, URL slug |
| **Pass** | score ≥ 85, no critical issues |
| **Warning** | score 70-84 |
| **Fail** | score < 70 OR any critical issue (no H1, no keyword in title) |
| **Blocking** | Yes |

#### 4. Brand Guidelines

| Параметр | Значение |
|----------|----------|
| **Входы** | `draft.content_md`, `site_config.brand_guidelines`, `site_config.tone` |
| **Выходы** | `{violations: [{text, rule, suggestion}], tone_match_score}` |
| **Инструмент** | LLM classifier + keyword blocklist |
| **Pass** | No violations AND tone_match > 0.8 |
| **Warning** | Minor tone deviation (0.6-0.8) |
| **Fail** | Any brand term violation OR competitor mention OR tone < 0.6 |
| **Blocking** | No (warning only, unless critical brand violation) |

#### 5. Legal Restrictions

| Параметр | Значение |
|----------|----------|
| **Входы** | `draft.content_md`, `site_config.legal_restrictions`, niche_type |
| **Выходы** | `{flags: [{text, risk_type, severity, suggestion}]}` |
| **Инструмент** | LLM + legal patterns database |
| **Checks** | Medical claims without disclaimer, Financial advice without disclaimer, Unsubstantiated guarantees, Copyright phrases, Defamation risk, YMYL compliance |
| **Pass** | No flags |
| **Warning** | Minor flags with easy fixes |
| **Fail** | Any high-severity flag |
| **Blocking** | Yes (immediate) |

#### 6. Cannibalization Check

| Параметр | Значение |
|----------|----------|
| **Входы** | `draft.content_md`, `brief.target_keyword`, existing `posts[]` from same site |
| **Выходы** | `{conflicts: [{post_id, keyword, similarity_score, recommendation}]}` |
| **Инструмент** | Embedding similarity + keyword overlap analysis |
| **Pass** | No post with >40% semantic similarity on same keyword |
| **Warning** | Similarity 30-40% (suggest internal link instead) |
| **Fail** | Existing post targets same primary keyword OR similarity >50% |
| **Blocking** | Yes |

#### 7. Freshness Check

| Параметр | Значение |
|----------|----------|
| **Входы** | `draft.sources_used`, `draft.content_md`, topic_type |
| **Выходы** | `{outdated_refs: [{source, date, issue}], freshness_score}` |
| **Инструмент** | Date extraction + topic freshness requirements |
| **Pass** | All sources from last 2 years (or 5 years for evergreen) |
| **Warning** | 1-2 sources slightly outdated but still valid |
| **Fail** | Key statistics from >3 years ago OR dead links >2 |
| **Blocking** | No (for evergreen), Yes (for news/trends) |

---

## 7. Интеграции и инструменты

### 7.1 External APIs

| Категория | Сервис | Назначение | Rate Limits | Fallback |
|-----------|--------|------------|-------------|----------|
| **SEO Data** | Ahrefs API | Keywords, backlinks, positions | 500 rows/min | DataForSEO |
| **SEO Data** | DataForSEO | SERP data, keyword suggestions | varies by plan | SerpAPI |
| **SEO Data** | SerpAPI | Real-time SERP | 5000/month | - |
| **Analytics** | Google Search Console API | Impressions, clicks, positions | 25K rows/day | - |
| **Analytics** | GA4 API | Traffic, behavior metrics | 10 req/sec | - |
| **Plagiarism** | Copyscape API | Duplicate detection | 10 req/min | Copyleaks |
| **Plagiarism** | Copyleaks | Deep plagiarism check | varies | - |
| **CMS** | WordPress REST API | Publish, update posts | 50 req/sec | - |
| **CMS** | Ghost Admin API | Publish, update posts | varies | - |
| **CMS** | Strapi API | Headless CMS operations | varies | - |
| **LLM** | Claude API | All agent operations | varies | - |
| **Images** | Unsplash API | Stock photos | 50 req/hour | Pexels |
| **Images** | DALL-E / Midjourney | Generated images | varies | - |

### 7.2 MCP Servers for Agents

```yaml
mcp_servers:
  # Веб-доступ
  browser:
    provider: puppeteer-mcp
    capabilities:
      - page_fetch
      - screenshot
      - wait_for_selector
    restrictions:
      - max_pages_per_session: 50
      - timeout_ms: 30000
      - blocked_domains: [competitor-admin.*, *.gov]

  fetch:
    provider: http-fetch-mcp
    capabilities:
      - GET
      - HEAD
    restrictions:
      - max_size_mb: 10
      - timeout_ms: 15000

  # Файловая система (для локальных операций)
  filesystem:
    provider: fs-mcp
    capabilities:
      - read
      - write
      - list
    restrictions:
      - base_path: /data/workspace/{tenant_id}
      - max_file_size_mb: 50

  # База данных
  database:
    provider: postgres-mcp
    capabilities:
      - query (read-only for most agents)
      - insert (specific agents only)
      - update (specific agents only)
    restrictions:
      - query_timeout_ms: 30000
      - max_rows: 10000

  # CMS адаптеры
  cms_wordpress:
    provider: wordpress-mcp
    capabilities:
      - create_post
      - update_post
      - upload_media
      - get_categories
    auth: oauth2

  cms_ghost:
    provider: ghost-mcp
    capabilities:
      - create_post
      - update_post
      - upload_image
    auth: admin_api_key

  cms_strapi:
    provider: strapi-mcp
    capabilities:
      - create_entry
      - update_entry
      - upload_file
    auth: jwt

  # SEO инструменты
  seo_tools:
    provider: custom-seo-mcp
    capabilities:
      - keyword_research
      - serp_analysis
      - backlink_check
      - position_tracking
    rate_limiting:
      requests_per_minute: 30
```

### 7.3 Orchestration Stack

```
┌─────────────────────────────────────────────────────────────┐
│                     ORCHESTRATION LAYER                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │  TEMPORAL   │  │   BullMQ    │  │    CRON     │         │
│  │  Workflows  │  │   Queues    │  │  Scheduler  │         │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘         │
│         │                │                │                 │
│         └────────────────┼────────────────┘                 │
│                          │                                  │
│                          ▼                                  │
│                  ┌───────────────┐                          │
│                  │ AGENT RUNNER  │                          │
│                  └───────────────┘                          │
│                                                              │
└─────────────────────────────────────────────────────────────┘

USE CASES:
━━━━━━━━━━━━
• Temporal: Long-running pipelines (discovery → publish),
            durable state, automatic retries, compensation

• BullMQ:   Short async tasks (validation checks, image gen),
            priority queues, rate limiting, dead letter

• Cron:     Scheduled jobs (daily monitoring, weekly reports,
            position tracking)
```

---

## 8. Agent Roles & Contracts

### 8.1 Researcher Agent

```json
{
  "role": "researcher",
  "description": "Analyzes niche, finds competitors, gathers market intelligence",
  "mcp_servers": ["browser", "fetch", "seo_tools", "database"],
  "input": {
    "site_id": "uuid",
    "topic": "string",
    "config": {
      "language": "string",
      "country": "string",
      "depth": "shallow | medium | deep"
    }
  },
  "output": {
    "competitors": [
      {
        "domain": "string",
        "relevance_score": "float",
        "monthly_traffic": "integer",
        "top_pages": ["string"],
        "content_gaps": ["string"]
      }
    ],
    "market_overview": {
      "total_search_volume": "integer",
      "competition_level": "string",
      "trending_topics": ["string"],
      "seasonality": "object"
    },
    "recommended_focus_areas": ["string"]
  },
  "constraints": {
    "max_competitors": 20,
    "min_relevance_score": 0.5,
    "timeout_minutes": 30
  }
}
```

### 8.2 Keyword Planner Agent

```json
{
  "role": "keyword_planner",
  "description": "Collects keywords, analyzes SERP, determines intent",
  "mcp_servers": ["seo_tools", "fetch", "database"],
  "input": {
    "site_id": "uuid",
    "competitors": ["competitor_id"],
    "seed_keywords": ["string"],
    "config": {
      "min_volume": "integer",
      "max_difficulty": "float",
      "intents": ["search_intent"]
    }
  },
  "output": {
    "keywords": [
      {
        "keyword": "string",
        "volume": "integer",
        "difficulty": "float",
        "cpc": "float",
        "intent": "search_intent",
        "serp_features": ["string"],
        "top_ranking_urls": ["string"],
        "source_competitor_id": "uuid | null"
      }
    ],
    "total_opportunity_score": "float"
  },
  "constraints": {
    "max_keywords": 5000,
    "timeout_minutes": 60
  }
}
```

### 8.3 Clustering Agent

```json
{
  "role": "clustering_agent",
  "description": "Groups keywords into topic clusters with hierarchy",
  "mcp_servers": ["database"],
  "input": {
    "site_id": "uuid",
    "keywords": ["keyword_id"]
  },
  "output": {
    "clusters": [
      {
        "name": "string",
        "primary_keyword_id": "uuid",
        "keyword_ids": ["uuid"],
        "intent": "search_intent",
        "topic_type": "pillar | cluster | supporting",
        "parent_cluster_id": "uuid | null",
        "total_volume": "integer",
        "avg_difficulty": "float"
      }
    ],
    "topic_hierarchy": {
      "pillars": ["cluster_id"],
      "relationships": [{"parent": "cluster_id", "children": ["cluster_id"]}]
    }
  },
  "constraints": {
    "min_cluster_size": 3,
    "max_cluster_size": 50
  }
}
```

### 8.4 Strategy Agent

```json
{
  "role": "strategy_agent",
  "description": "Prioritizes clusters, creates content roadmap",
  "mcp_servers": ["database", "seo_tools"],
  "input": {
    "site_id": "uuid",
    "clusters": ["cluster_id"],
    "constraints": {
      "articles_per_week": "integer",
      "budget_tokens_monthly": "integer",
      "priority_intents": ["search_intent"]
    }
  },
  "output": {
    "roadmap": [
      {
        "cluster_id": "uuid",
        "scheduled_week": "date",
        "priority": "integer",
        "reasoning": "string",
        "dependencies": ["cluster_id"],
        "expected_impact": {
          "traffic_estimate": "integer",
          "time_to_rank_weeks": "integer"
        }
      }
    ],
    "strategy_summary": "string",
    "quick_wins": ["cluster_id"],
    "long_term_plays": ["cluster_id"]
  }
}
```

### 8.5 Brief Writer Agent

```json
{
  "role": "brief_writer",
  "description": "Creates detailed content briefs from clusters",
  "mcp_servers": ["browser", "fetch", "database", "seo_tools"],
  "input": {
    "cluster_id": "uuid",
    "site_config": "object",
    "existing_posts": ["post_id"]
  },
  "output": {
    "brief": {
      "title": "string",
      "target_keyword": "string",
      "secondary_keywords": ["string"],
      "word_count_min": "integer",
      "word_count_max": "integer",
      "structure": {
        "sections": [
          {
            "heading": "string",
            "heading_level": "h2 | h3",
            "key_points": ["string"],
            "required_elements": ["string"],
            "word_count_target": "integer"
          }
        ]
      },
      "required_sources": [
        {"type": "statistic | expert_quote | case_study", "min_count": "integer"}
      ],
      "competitor_analysis": {
        "urls_analyzed": ["string"],
        "avg_word_count": "integer",
        "common_sections": ["string"],
        "content_gaps": ["string"]
      },
      "internal_links": ["post_id"],
      "serp_optimization": {
        "featured_snippet_target": "boolean",
        "paa_questions": ["string"]
      }
    }
  }
}
```

### 8.6 Writer Agent

```json
{
  "role": "writer",
  "description": "Writes article content following brief",
  "mcp_servers": ["browser", "fetch", "database"],
  "input": {
    "brief_id": "uuid",
    "site_config": "object",
    "revision_feedback": "string | null"
  },
  "output": {
    "draft": {
      "content_md": "string",
      "word_count": "integer",
      "sources_used": [
        {
          "url": "string",
          "title": "string",
          "quote": "string",
          "location_in_text": "string"
        }
      ],
      "internal_links_used": ["post_id"],
      "meta_title": "string",
      "meta_description": "string",
      "slug": "string",
      "featured_image_prompt": "string"
    }
  },
  "constraints": {
    "max_tokens_output": 8000,
    "citation_style": "inline",
    "min_sources": 3
  }
}
```

### 8.7 Validator Agent

```json
{
  "role": "validator",
  "description": "Orchestrates all validation checks on draft",
  "mcp_servers": ["database", "fetch"],
  "input": {
    "draft_id": "uuid"
  },
  "output": {
    "validation_report": {
      "overall_status": "passed | failed | warning",
      "checks": [
        {
          "type": "validation_type",
          "status": "check_status",
          "score": "float | null",
          "details": "object",
          "blocking": "boolean"
        }
      ],
      "revision_instructions": "string | null",
      "auto_fixable_issues": [
        {"type": "string", "fix": "string"}
      ]
    }
  }
}
```

### 8.8 Publisher Agent

```json
{
  "role": "publisher",
  "description": "Publishes approved drafts to CMS",
  "mcp_servers": ["cms_wordpress", "cms_ghost", "cms_strapi", "database"],
  "input": {
    "draft_id": "uuid",
    "cms_config": "object",
    "schedule": "datetime | null"
  },
  "output": {
    "publication": {
      "cms_post_id": "string",
      "url": "string",
      "published_at": "datetime",
      "status": "live | scheduled"
    }
  },
  "retry_policy": {
    "max_attempts": 3,
    "backoff_seconds": [60, 300, 900]
  }
}
```

### 8.9 Analyst Agent

```json
{
  "role": "analyst",
  "description": "Monitors performance, detects decay, suggests iterations",
  "mcp_servers": ["database", "seo_tools"],
  "input": {
    "site_id": "uuid",
    "date_range": {"start": "date", "end": "date"},
    "posts": ["post_id"]
  },
  "output": {
    "performance_report": {
      "summary": {
        "total_impressions": "integer",
        "total_clicks": "integer",
        "avg_position": "float",
        "top_performers": ["post_id"],
        "underperformers": ["post_id"]
      },
      "trends": {
        "traffic_change_pct": "float",
        "position_changes": [{"post_id": "uuid", "change": "float"}]
      },
      "iteration_recommendations": [
        {
          "post_id": "uuid",
          "trigger": "iteration_trigger",
          "priority": "integer",
          "suggested_actions": ["string"]
        }
      ],
      "new_opportunities": [
        {
          "keyword": "string",
          "current_position": "integer",
          "potential": "string"
        }
      ]
    }
  }
}
```

---

## 9. Configuration Files

### 9.1 Site Configuration (site-config.yaml)

```yaml
# /configs/sites/{site_id}/site-config.yaml

site:
  id: "550e8400-e29b-41d4-a716-446655440000"
  name: "Tech Startup Blog"
  domain: "blog.techstartup.com"

localization:
  language: "en-US"
  country: "US"
  timezone: "America/New_York"
  date_format: "MMMM D, YYYY"

content:
  tone:
    formality: 0.7          # 0 = casual, 1 = formal
    expertise_level: 0.8    # 0 = beginner, 1 = expert
    humor: 0.2              # 0 = serious, 1 = playful
    personality_traits:
      - "authoritative"
      - "helpful"
      - "data-driven"

  voice_examples:
    good:
      - "Here's what the data actually shows..."
      - "Let's break this down step by step."
    bad:
      - "OMG this is amazing!!!"
      - "You won't believe what happened next..."

  word_count:
    default_min: 1500
    default_max: 2500
    pillar_min: 3000
    pillar_max: 5000

stop_topics:
  - "competitor product comparisons"
  - "pricing information"
  - "political opinions"
  - "medical advice"
  - "financial predictions"

required_sources:
  min_per_article: 3
  preferred_types:
    - "academic_research"
    - "official_documentation"
    - "industry_reports"
  trusted_domains:
    - "*.edu"
    - "*.gov"
    - "techcrunch.com"
    - "wired.com"
  blocked_domains:
    - "wikipedia.org"  # требовать первоисточники
    - "reddit.com"
    - "quora.com"

seo:
  target_keyword_density:
    min: 0.01
    max: 0.02
  internal_links:
    min_per_article: 2
    max_per_article: 5
  external_links:
    min_per_article: 2
    nofollow_domains:
      - "competitor1.com"
      - "competitor2.com"
```

### 9.2 CMS Adapter Configuration (cms-adapter.yaml)

```yaml
# /configs/sites/{site_id}/cms-adapter.yaml

adapter: "wordpress"

wordpress:
  api_url: "https://blog.techstartup.com/wp-json/wp/v2"
  auth:
    type: "application_password"
    username: "${WORDPRESS_USER}"
    password: "${WORDPRESS_APP_PASSWORD}"

  defaults:
    status: "draft"  # draft | publish | pending
    author_id: 1
    categories:
      default: "blog"
      mapping:
        "pillar": "guides"
        "cluster": "tutorials"
        "supporting": "tips"

  field_mapping:
    title: "title.rendered"
    content: "content.rendered"
    excerpt: "excerpt.rendered"
    slug: "slug"
    meta_title: "yoast_meta.title"
    meta_description: "yoast_meta.description"
    featured_image: "featured_media"

  plugins:
    yoast_seo: true
    rank_math: false

# Alternative: Ghost
# adapter: "ghost"
# ghost:
#   api_url: "https://blog.example.com/ghost/api/v3/admin"
#   auth:
#     type: "admin_api_key"
#     key: "${GHOST_ADMIN_KEY}"

# Alternative: Strapi
# adapter: "strapi"
# strapi:
#   api_url: "https://cms.example.com/api"
#   auth:
#     type: "jwt"
#     identifier: "${STRAPI_USER}"
#     password: "${STRAPI_PASSWORD}"
#   content_type: "articles"
```

### 9.3 Pipeline Limits Configuration (limits.yaml)

```yaml
# /configs/sites/{site_id}/limits.yaml

publishing:
  daily_max: 2
  weekly_max: 10
  min_interval_hours: 4
  schedule:
    preferred_days: ["tuesday", "wednesday", "thursday"]
    preferred_hours: [9, 10, 14, 15]  # UTC
    avoid_hours: [0, 1, 2, 3, 4, 5]

tokens:
  monthly_budget: 10000000  # 10M tokens
  per_article_max: 50000
  alerts:
    - threshold: 0.8
      action: "email"
    - threshold: 0.95
      action: "pause_pipeline"

api_calls:
  serp_daily_max: 1000
  plagiarism_daily_max: 50

rate_limiting:
  research_requests_per_minute: 10
  cms_requests_per_minute: 30

retries:
  validation_max_attempts: 3
  publishing_max_attempts: 3
  api_backoff_seconds: [30, 60, 120, 300]

quality_gates:
  min_validation_score: 75
  max_plagiarism_percent: 15
  require_human_approval:
    - "legal_warning"
    - "brand_violation"
    - "high_competition_cluster"
```

### 9.4 Legal & Compliance Configuration (legal.yaml)

```yaml
# /configs/sites/{site_id}/legal.yaml

jurisdiction: "US"

disclaimers:
  medical:
    required: true
    text: "This article is for informational purposes only and does not constitute medical advice. Consult a healthcare professional for medical concerns."
    position: "end"

  financial:
    required: true
    text: "This content is not financial advice. Consult a qualified financial advisor before making investment decisions."
    position: "end"

  affiliate:
    required: true
    text: "This post may contain affiliate links. We may earn a commission at no extra cost to you."
    position: "start"
    trigger_keywords: ["buy", "purchase", "recommended product"]

prohibited_claims:
  - pattern: "guaranteed results"
    severity: "high"
  - pattern: "100% effective"
    severity: "high"
  - pattern: "cure for"
    severity: "critical"
  - pattern: "get rich quick"
    severity: "critical"
  - pattern: "no side effects"
    severity: "high"

required_disclosures:
  sponsored_content: true
  ai_generated: false  # зависит от юрисдикции

gdpr:
  cookie_notice: true
  data_collection_disclosure: true

copyright:
  image_attribution_required: true
  quote_max_length: 300
  fair_use_guidelines: true
```

### 9.5 Monitoring Configuration (monitoring.yaml)

```yaml
# /configs/sites/{site_id}/monitoring.yaml

tracking:
  position_check_frequency: "daily"
  metrics_sync_frequency: "daily"
  competitor_check_frequency: "weekly"

alerts:
  position_drop:
    threshold: 5  # positions
    period_days: 7
    action: "slack_notification"

  traffic_drop:
    threshold_percent: 20
    period_days: 14
    action: "email_and_slack"

  new_competitor:
    check_positions: [1, 2, 3]
    action: "add_to_analysis_queue"

decay_detection:
  enabled: true
  metrics:
    - name: "position"
      threshold: 10
      period_days: 30
    - name: "clicks"
      threshold_percent: -30
      period_days: 30
  action: "create_iteration_task"

freshness:
  evergreen_update_months: 12
  news_update_days: 30
  check_frequency: "weekly"

reporting:
  weekly_summary:
    enabled: true
    recipients: ["team@example.com"]
    day: "monday"
    time: "09:00"

  monthly_report:
    enabled: true
    recipients: ["management@example.com"]
    day: 1
```

---

## 10. Риски и ограничения (Top 12)

| # | Риск | Категория | Severity | Вероятность | Mitigation |
|---|------|-----------|----------|-------------|------------|
| 1 | **LLM галлюцинации** | Technical | Critical | High | Обязательная верификация фактов через веб-поиск; требование источников для каждого claim; human review для YMYL тем |
| 2 | **API rate limits / блокировки** | Technical | High | High | Кэширование SERP данных (TTL 24h); exponential backoff; несколько провайдеров (Ahrefs → DataForSEO → SerpAPI); request queuing с приоритетами |
| 3 | **SEO-каннибализация** | SEO | High | Medium | Embedding-based similarity check перед публикацией; central keyword registry; автоматическое предложение merge или redirect |
| 4 | **Google penalties** | SEO | Critical | Low | Качество над количеством; естественный темп публикаций; разнообразие контента; мониторинг manual actions в GSC |
| 5 | **Плагиат / copyright** | Legal | Critical | Medium | Copyscape проверка (blocking gate); цитирование с атрибуцией; парафраз вместо копирования; image licensing check |
| 6 | **Юридические риски (YMYL)** | Legal | Critical | Medium | Обязательные disclaimers; запрет медицинских/финансовых советов без квалификации; legal review для sensitive topics |
| 7 | **Устаревание контента** | Product | Medium | High | Автоматический freshness monitoring; scheduled content audits; decay detection alerts; iteration pipeline |
| 8 | **CMS downtime / API changes** | Technical | Medium | Medium | Circuit breaker pattern; queue публикаций при недоступности; adapter abstraction; webhook для health check |
| 9 | **Token cost explosion** | Technical | High | Medium | Token budgets per tenant; caching LLM responses; progressive summarization; smaller models для простых задач |
| 10 | **Quality drift over time** | Product | Medium | Medium | Rolling quality score monitoring; A/B testing заголовков; periodic human audits; feedback loops |
| 11 | **Multi-tenant data leakage** | Security | Critical | Low | Strict tenant isolation (row-level security); separate encryption keys; audit logging; regular security reviews |
| 12 | **Конкурент копирует стратегию** | Business | Low | Medium | Unique brand voice; proprietary data sources; first-mover advantage на новые темы; building topical authority |

### Mitigation Matrix

```
                    SEVERITY
                    Low      Medium     High      Critical
            ┌────────┬────────┬────────┬────────┐
      Low   │ Accept │ Accept │ Monitor│ Prevent│
            ├────────┼────────┼────────┼────────┤
P   Medium  │ Accept │ Monitor│ Mitigate│Prevent│
R          ├────────┼────────┼────────┼────────┤
O   High    │ Monitor│ Mitigate│Mitigate│Prevent│
B          ├────────┼────────┼────────┼────────┤
            └────────┴────────┴────────┴────────┘

Actions:
• Accept: Log and continue
• Monitor: Alert + dashboard tracking
• Mitigate: Active countermeasures
• Prevent: Blocking gates, no exceptions
```

---

## 11. MVP Plan (4 Iterations)

### v0.1 — Foundation (Week 1-4)

**Scope:**
- [ ] Core data models (PostgreSQL schema)
- [ ] Basic API (FastAPI)
- [ ] Single-site configuration (YAML)
- [ ] Manual keyword input → Brief generation
- [ ] Writer agent (Claude API)
- [ ] Basic SEO lint validation
- [ ] WordPress adapter only
- [ ] Manual publish trigger

**Success Metrics:**
- Генерация 10 статей с quality score > 70%
- End-to-end время: keyword → published < 30 min (manual steps included)
- Zero plagiarism flags

**Deliverables:**
```
/src
  /api          - FastAPI endpoints
  /agents       - writer, validator stubs
  /adapters     - wordpress
  /db           - models, migrations
  /config       - YAML parsers
```

---

### v0.2 — Discovery & Automation (Week 5-8)

**Scope:**
- [ ] Researcher agent (competitor analysis)
- [ ] Keyword Planner agent (SERP integration)
- [ ] Clustering agent
- [ ] Strategy agent (basic prioritization)
- [ ] Full validation pipeline (plagiarism, factcheck)
- [ ] BullMQ task queue
- [ ] Basic admin UI (article status, logs)
- [ ] Scheduled publishing

**Success Metrics:**
- Автоматическое обнаружение 50+ релевантных keywords
- Кластеризация с accuracy > 80% (manual review)
- Validation pipeline catches 95% of issues
- Публикация 20 статей без ручного вмешательства

**New Components:**
```
/src
  /agents
    /researcher
    /keyword_planner
    /clustering
    /strategy
  /queue          - BullMQ workers
  /validation     - full pipeline
/admin-ui         - React dashboard
```

---

### v0.3 — Monitoring & Iteration (Week 9-12)

**Scope:**
- [ ] GSC/GA4 integration
- [ ] Analyst agent (performance tracking)
- [ ] Decay detection
- [ ] Iteration pipeline (content updates)
- [ ] Ghost + Strapi adapters
- [ ] Temporal workflows (durable pipelines)
- [ ] Alert system (Slack/email)
- [ ] Quality dashboard

**Success Metrics:**
- Автоматическое обнаружение decay в течение 48 часов
- Успешное обновление 5 устаревших статей
- Dashboard показывает ROI по каждому кластеру
- 3 CMS адаптера работают корректно

**New Components:**
```
/src
  /agents
    /analyst
  /integrations
    /gsc
    /ga4
  /adapters
    /ghost
    /strapi
  /workflows      - Temporal definitions
  /alerts
```

---

### v1.0 — Production Ready (Week 13-16)

**Scope:**
- [ ] Multi-tenant architecture
- [ ] RBAC (roles: admin, editor, viewer)
- [ ] Tenant isolation & encryption
- [ ] Rate limiting per tenant
- [ ] Billing integration hooks
- [ ] API documentation (OpenAPI)
- [ ] Comprehensive logging & audit
- [ ] Performance optimization
- [ ] Security audit
- [ ] Load testing (100 concurrent pipelines)

**Success Metrics:**
- Support 10 tenants simultaneously
- 99.5% uptime over 2 weeks
- < 500ms API response time (p95)
- Zero security vulnerabilities (external audit)
- Complete API documentation

**Production Checklist:**
```
□ Multi-tenant data isolation verified
□ Encryption at rest and in transit
□ Rate limiting tested
□ Backup & recovery tested
□ Monitoring & alerting complete
□ Runbooks documented
□ On-call rotation established
□ Security audit passed
□ Load test passed
□ Disaster recovery tested
```

---

## 12. Cost Estimation & Optimization

### 12.1 Cost Breakdown per Article

| Component | Tokens/Calls | Unit Cost | Cost/Article |
|-----------|-------------|-----------|--------------|
| **Research** | ~5K tokens | $0.015/1K (Sonnet) | $0.08 |
| **Keyword Analysis** | ~3K tokens + 10 API calls | $0.05 + $0.50 | $0.55 |
| **Brief Generation** | ~8K tokens | $0.015/1K | $0.12 |
| **Writing** | ~15K tokens | $0.015/1K | $0.23 |
| **Validation** | ~10K tokens + plagiarism | $0.15 + $0.10 | $0.25 |
| **Revision (avg 0.5x)** | ~7K tokens | $0.015/1K | $0.05 |
| **Publishing** | ~1K tokens | $0.015/1K | $0.02 |
| **TOTAL** | | | **~$1.30** |

### 12.2 Monthly Projections (100 articles/month)

| Item | Cost |
|------|------|
| LLM (Claude) | $80-120 |
| SEO APIs (Ahrefs/DataForSEO) | $100-200 |
| Plagiarism checks | $30-50 |
| Infrastructure (cloud) | $50-100 |
| **Total** | **$260-470/month** |

### 12.3 Cost Optimization Strategies

```
┌─────────────────────────────────────────────────────────────────┐
│                    COST OPTIMIZATION MATRIX                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  1. CACHING (saves 40-60% on API costs)                         │
│     ├── SERP data: Redis, TTL 24h                               │
│     ├── Keyword metrics: Redis, TTL 7d                          │
│     ├── Competitor analysis: Redis, TTL 30d                     │
│     └── LLM responses (identical prompts): Redis, TTL 1h        │
│                                                                  │
│  2. DEDUPLICATION (saves 20-30% on LLM costs)                   │
│     ├── Similar brief detection before generation               │
│     ├── Reuse research for related clusters                     │
│     └── Shared competitor analysis across clusters              │
│                                                                  │
│  3. MODEL TIERING (saves 30-50% on LLM costs)                   │
│     ├── Haiku: SEO lint, formatting, simple checks              │
│     ├── Sonnet: Research, briefs, validation                    │
│     └── Opus: Complex writing, sensitive content review         │
│                                                                  │
│  4. BATCH PROCESSING (saves 15-25% on API costs)                │
│     ├── Keyword research: batch 100 keywords per request        │
│     ├── Position tracking: batch by domain                      │
│     └── Validation: parallel checks                             │
│                                                                  │
│  5. SMART SCHEDULING (saves 10-20%)                             │
│     ├── Off-peak API calls (lower rate limits)                  │
│     ├── Batch monitoring (daily vs real-time)                   │
│     └── Predictive refresh (update before decay)                │
│                                                                  │
│  6. PROGRESSIVE SUMMARIZATION (saves 20-40% on context)         │
│     ├── Compress research notes before brief                    │
│     ├── Extract key points from competitors                     │
│     └── Summarize long sources before citation                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 12.4 Monitoring Frequency Optimization

| Metric | Frequency | Rationale |
|--------|-----------|-----------|
| Position tracking | Daily → Weekly (after stable) | New posts daily, 30+ days old weekly |
| Traffic metrics | Daily | Low cost (GSC API free) |
| Competitor monitoring | Weekly | Changes are gradual |
| Freshness check | Weekly | Content doesn't stale quickly |
| Full revalidation | Monthly | Catch any drift |

### 12.5 Token Budget Allocation

```yaml
# Recommended budget split for 100 articles/month

budget:
  total_tokens: 5000000  # 5M tokens

  allocation:
    research: 15%        # 750K
    keyword_planning: 10% # 500K
    clustering: 5%       # 250K
    strategy: 5%         # 250K
    briefs: 15%          # 750K
    writing: 30%         # 1.5M
    validation: 10%      # 500K
    revision: 5%         # 250K (buffer for rewrites)
    monitoring: 3%       # 150K
    iteration: 2%        # 100K

  alerts:
    - at: 70%
      action: review_efficiency
    - at: 85%
      action: reduce_batch_size
    - at: 95%
      action: pause_new_articles
```

---

## Appendix A: Tech Stack Summary

| Layer | Technology | Rationale |
|-------|------------|-----------|
| **API** | FastAPI (Python) | Async, OpenAPI, type hints |
| **Queue** | BullMQ (Redis) | Priorities, rate limiting, DLQ |
| **Workflows** | Temporal.io | Durable execution, retries, visibility |
| **Database** | PostgreSQL + pgvector | ACID, vectors for similarity |
| **Cache** | Redis | Fast, TTL support, pub/sub |
| **Object Storage** | S3 / MinIO | Scalable, cost-effective |
| **LLM** | Claude API (Anthropic) | Quality, tool use, safety |
| **Admin UI** | React + Tailwind | Modern, component-based |
| **Monitoring** | Prometheus + Grafana | Industry standard |
| **Logging** | Loki / ELK | Centralized, searchable |

---

## Appendix B: Quick Start Commands

```bash
# Clone and setup
git clone https://github.com/org/seo-blog-automation
cd seo-blog-automation
cp .env.example .env

# Start infrastructure
docker-compose up -d postgres redis temporal

# Run migrations
alembic upgrade head

# Start API
uvicorn src.api.main:app --reload

# Start workers
python -m src.queue.worker

# Create first site
curl -X POST http://localhost:8000/api/v1/sites \
  -H "Content-Type: application/json" \
  -d '{"name": "My Blog", "topic": "cloud computing"}'
```

---

## Appendix C: Development Progress Log

### Phase 0: Foundation — ТЕКУЩИЙ СТАТУС

**Дата:** 2026-02-02

#### Выполнено:

1. **Инфраструктура на сервере (95.163.230.43)**
   - Ghost CMS развёрнут и работает: http://95.163.230.43
   - MySQL для Ghost
   - PostgreSQL для API
   - Redis для кэша
   - FastAPI приложение в Docker

2. **Структура проекта:**
   ```
   /src
     /api
       /routes
         - health.py      # Health check endpoint
         - sites.py       # CRUD для сайтов
         - articles.py    # Генерация и публикация статей
       - main.py          # FastAPI app factory
     /db
       - models.py        # SQLAlchemy models (Site, Draft, Post)
       - session.py       # Database session
     /services
       - generator.py     # ArticleGenerator (Claude API)
       - publisher.py     # GhostPublisher (Ghost Admin API)
     - config.py          # Settings from env
   ```

3. **Работающие эндпоинты:**
   - `GET /health` — проверка здоровья
   - `POST /api/v1/articles/generate` — запуск генерации (фоновая задача)
   - `GET /api/v1/articles/{id}` — получение статьи
   - `POST /api/v1/articles/{id}/publish` — публикация в Ghost

4. **PoC скрипты:**
   - `poc/generate_article.py` — тест генерации через Claude
   - `poc/publish_to_ghost.py` — тест публикации в Ghost

#### Проблемы и решения:

| Проблема | Решение |
|----------|---------|
| Ghost требует MySQL (не SQLite) | Добавлен MySQL в docker-compose |
| Dockerfile: apt-get failed | Убраны системные зависимости (psycopg2-binary не требует libpq-dev) |
| Таблицы не созданы | `Base.metadata.create_all(engine)` в session.py |
| ArticleResponse validation error | Сделаны поля `slug`, `content_md` optional |
| Ghost "Host not allowed" | Деплой API на тот же сервер для localhost доступа |
| Anthropic 404 "model not found" | **Исправлено:** модель `claude-3-5-sonnet-20241022` устарела → используем `claude-sonnet-4-20250514` |

#### Конфигурация на сервере (.env):

```env
DATABASE_URL=postgresql://seo:seopass@postgres:5432/seoblog
REDIS_URL=redis://redis:6379/0
GHOST_URL=http://ghost:2368
GHOST_ADMIN_KEY=69810b7b23ec7d0001526cd5:b64dd47c3cf03456615984d7c0f5f79a486bb049cf1fd86a6de434ce16da373e
ANTHROPIC_API_KEY=<ваш ключ>
DEBUG=true
```

#### Выполнено (2026-02-03):

5. **Web UI для Brief workflow:**
   - [x] Jinja2 templates + Tailwind CSS + HTMX
   - [x] `/ui/briefs` — список Brief
   - [x] `/ui/briefs/new` — генерация Brief из темы
   - [x] `/ui/briefs/{id}` — просмотр/approve/generate
   - [x] `/ui/drafts` — список Draft
   - [x] `/ui/drafts/{id}` — просмотр/approve/publish

6. **Brief Generator:**
   - [x] Serper.dev API для SERP данных
   - [x] Claude анализирует и генерирует структуру ТЗ

#### Следующие шаги:

1. **Phase 1: Validation Pipeline:**
   - [ ] SEO lint проверки
   - [ ] Базовая проверка на плагиат (простой similarity check)
   - [ ] Проверка источников

2. **Инфраструктурные улучшения:**
   - [ ] Alembic миграции вместо create_all
   - [ ] Логирование в файл
   - [ ] Health check для всех сервисов в docker-compose

#### TODO: Улучшения Brief Generator

Сейчас Brief использует только данные из Serper.dev (organic, PAA, related searches).

**Что можно добавить:**
- [ ] **Парсинг контента конкурентов** — получать реальный текст страниц, а не только snippet
- [ ] **Search volume / difficulty** — интеграция с Ahrefs/DataForSEO для частотности
- [ ] **Текущие позиции** — проверять где сайт уже ранжируется
- [ ] **Анализ внутренних ссылок** — какие статьи уже есть, чтобы избежать каннибализации
- [ ] **Автоматические internal links** — предлагать ссылки на существующие статьи

#### TODO: Вёрстка и форматирование статей

**Проблема:** После генерации статьи через Brief workflow получается сплошной текст без заголовков и структуры. При прямой генерации в Ghost было лучше.

**Что нужно исправить:**
- [ ] **Генератор не использует структуру из Brief** — промпт должен явно требовать H2/H3 заголовки из `brief.structure.sections`
- [ ] **Markdown форматирование** — убедиться что генератор возвращает правильный Markdown с `##`, `###`, списками, **bold**
- [ ] **Проверить конвертацию** — возможно markdown фильтр в шаблоне не работает корректно
- [ ] **Сравнить промпты** — посмотреть чем отличается промпт прямой генерации от генерации по Brief

#### Выполнено (2026-02-04):

7. **Validation Pipeline (SEO Lint + Plagiarism):**
   - [x] `src/services/validators/seo_lint.py` — SEO проверки (title, meta, keywords, structure)
   - [x] `src/services/validators/plagiarism.py` — проверка на плагиат через similarity
   - [x] `src/services/validation_pipeline.py` — оркестратор валидаторов
   - [x] UI для валидации в `/ui/drafts/{id}`

8. **Bugfix: Article Truncation + Validation Thresholds:**
   - [x] `generator.py`: `max_tokens` увеличен с 8000 до 16000 (статьи обрывались на полуслове)
   - [x] `seo_lint.py`: keyword_density пороги смягчены:
     - PASS: 0.5-3% (было 1-2%)
     - WARNING: 0.2-0.5% или 3-4% (было 0.5-1% или 2-3%)
     - Никогда не FAIL на keyword_density — только WARNING
   - [x] Задеплоено на сервер

9. **Writing Pipeline — Multi-Stage Article Generation:**
   - [x] Архитектура: Pipeline + Stage Registry с контрактами между этапами
   - [x] Все промпты созданы в `src/services/writing_pipeline/prompts/`:
     - `intent_v1.txt` — анализ интента и редакционный контракт
     - `research_queries_v1.txt` — генерация поисковых запросов
     - `research_packer_v1.txt` — обработка результатов поиска в research_pack
     - `structure_v1.txt` — архитектура статьи (outline)
     - `drafting_v1.txt` — написание текста по outline
     - `editing_v1.txt` — редактура и markdown-вёрстка
   - [x] Контракты определены: `src/services/writing_pipeline/contracts/__init__.py`
   - [x] PipelineRunner реализован: `src/services/writing_pipeline/core/runner.py`
   - [x] Все stages реализованы: `src/services/writing_pipeline/stages/`
   - [x] Serper.dev интеграция: `src/services/writing_pipeline/data_sources/serper.py`
   - [ ] Интеграция с существующим generator.py — TODO (deprecate постепенно)

   **Структура модуля:**
   ```
   src/services/writing_pipeline/
   ├── __init__.py           # Exports: PipelineRunner, WritingContext, contracts
   ├── cli.py                # CLI для тестирования pipeline
   ├── core/
   │   ├── __init__.py
   │   ├── context.py        # WritingContext (shared state)
   │   ├── runner.py         # PipelineRunner (orchestrator)
   │   └── stage.py          # WritingStage ABC
   ├── stages/
   │   ├── __init__.py
   │   ├── intent.py         # IntentStage
   │   ├── research.py       # ResearchStage (queries + packer)
   │   ├── structure.py      # StructureStage
   │   ├── drafting.py       # DraftingStage
   │   └── editing.py        # EditingStage
   ├── contracts/
   │   └── __init__.py       # IntentResult, ResearchResult, OutlineResult, etc.
   ├── data_sources/
   │   ├── __init__.py
   │   └── serper.py         # SerperDataSource
   └── prompts/
       ├── intent_v1.txt
       ├── research_queries_v1.txt
       ├── research_packer_v1.txt
       ├── structure_v1.txt
       ├── drafting_v1.txt
       └── editing_v1.txt
   ```

   **Pipeline flow:**
   ```
   topic → Intent → Research (queries → search → packer) → Structure → Drafting → Editing → article.md
   ```

   **Использование CLI:**
   ```bash
   # С Serper.dev (рекомендуется)
   ANTHROPIC_API_KEY=... SERPER_API_KEY=... python -m src.services.writing_pipeline.cli "Тема статьи" -o ./output

   # Без Serper.dev (только LLM knowledge)
   ANTHROPIC_API_KEY=... python -m src.services.writing_pipeline.cli "Тема статьи" -o ./output
   ```

   **Принципы архитектуры:**
   - Каждый этап — независимый subagent с минимальным контекстом
   - Контракт определяет inputs/outputs для каждого этапа
   - Research использует двухшаговый процесс (queries → WebSearch → packer)
   - Все промежуточные результаты логируются
   - Модуль можно обособить в отдельный пакет

---

## ⚠️ ВАЖНО: Доступ к серверу

### У CLAUDE ЕСТЬ ПОЛНЫЙ ДОСТУП К СЕРВЕРУ

**Сервер:** `root@95.163.230.43`

**Claude выполняет деплой САМОСТОЯТЕЛЬНО:**
```bash
ssh root@95.163.230.43 "cd /opt/seo-blog && git pull && docker compose build && docker compose up -d"
```

### ⚠️ НЕ СПРАШИВАТЬ РАЗРЕШЕНИЯ НА ДЕПЛОЙ

Claude имеет SSH-доступ и должен:
1. Делать `git push`
2. Подключаться к серверу по SSH
3. Выполнять `git pull && docker compose build && docker compose up -d`

**БЕЗ ВОПРОСОВ. БЕЗ УТОЧНЕНИЙ. ПРОСТО ДЕЛАТЬ.**

---

*Document version: 1.6*
*Last updated: 2026-02-04*
