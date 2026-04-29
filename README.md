# Mandala

Бэкенд и документация продукта «Mandala»: архитектура в `docs/`, пошаговый план — `docs/implementation-plan.md`.

По коду на сейчас: тикеты **5–19** закрыты — репозитории и сервисы (**`PlansRepository`**, **`QuotaService`**, **`UserIdentityService`**, **`BillingProvider`** / **`PostgresBillingProvider`**, **`apply_plan_change`**, **`PaymentTransactionsRepository`**, **`mandala.services.telegram_stars`**, обработка **pre_checkout** / **successful_payment** в **`process_telegram_billing_update`**), **`mandala.domain`** (**`handle_inbound`** — сначала анкета, затем роутер **текст / картинка**; ветка картинки: **`image_reply`**, квота **`image_generation`**, OpenAI-compatible **`/images/generations`** или заглушка, **`messages`** + **`generated_artifacts`**; текст: **`text_reply`** + опционально **RAG** в Qdrant + **память диалога** (последние **20** сообщений из **`messages`**, тикет 17), **`mandala.llm`**, **`mandala.verticals`**, **`mandala.rag`**), адаптер **`mandala.adapters.telegram`**, HTTP **`mandala.http`**, **`mandala.llm`**; следующий шаг плана — **тикет 20** (наблюдаемость).

## Зависимости

Нужен **Python 3.11+**. Рекомендуется [uv](https://docs.astral.sh/uv/).

### Через uv

```bash
uv sync --extra dev
```

Повторяемая установка по зафиксированным версиям: `uv sync --extra dev --frozen` (нужен `uv.lock` в репозитории).

### Через pip (editable)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Переменные окружения — из `.env.example` (скопируйте в `.env`; значения-заглушки, без секретов).

## Локальный PostgreSQL (тикет 2)

Поднять БД (**только Podman**):

```bash
cp .env.example .env   # при необходимости поправьте пароль и порт
podman compose up -d
# Опционально RAG (тикет 16): поднять и Qdrant — podman compose up -d postgres qdrant
```

Образ: **PostgreSQL 16.6** (`postgres:16.6-alpine`), данные в volume `mandala_pg_data`. Параметры `POSTGRES_*` в `.env` должны совпадать с `DATABASE_URL` (пользователь, пароль, имя БД, хост `localhost`, порт).

Остановка:

```bash
podman compose down
```

Требуется **Podman** в PATH (`podman compose`). Образы тянутся из OCI-регистров (в `compose.yaml` указан `docker.io/...` — это имя хоста реестра, не зависимость от Docker Desktop).

Проверка подключения (нужны dev-зависимости и **запущенный** Postgres):

```bash
export $(grep -v '^#' .env | xargs)   # или вручную: export DATABASE_URL=...
make db-check
```

**Одним скриптом** (копирует `.env` из примера при отсутствии, поднимает compose, ждёт БД, делает `SELECT 1`):

```bash
pip install -e ".[dev]"   # или uv sync --extra dev — если ещё не ставили
bash scripts/verify_local_postgres.sh
```

`Makefile` вызывает только **`podman compose`** (`COMPOSE` менять не предполагается).

## Миграции БД (Alembic: тикеты 3–4)

Нужны dev-зависимости и переменная **`DATABASE_URL`** (как для Postgres выше). Схема (вертикали, пользователи, планы, профили, сообщения, usage, платежи) накатывается одной командой:

```bash
export $(grep -v '^#' .env | xargs)   # или вручную export DATABASE_URL=...
python -m alembic upgrade head
```

Сквозная проверка Compose + `alembic upgrade head` + базовые проверки схемы:
`bash scripts/verify_migrations.sh`.

Проверка данных (планы и лимиты после seed):

```bash
python -c "
import os, psycopg
u = os.environ['DATABASE_URL']
with psycopg.connect(u) as c, c.cursor() as cur:
    cur.execute('SELECT name FROM plans ORDER BY name')
    print('plans:', [r[0] for r in cur.fetchall()])
    cur.execute('''SELECT p.name, l.resource, l.limit_per_period
      FROM plan_limits l JOIN plans p ON p.id = l.plan_id ORDER BY p.name, l.resource''')
    print('limits:', cur.fetchall())
"
```

Эквивалент через Make: `make db-migrate` (ожидает `DATABASE_URL` в окружении).

**Драйверы для приложения (зафиксировано в тикете 2):** строка подключения — **`DATABASE_URL`** (в коде приложения SQLAlchemy + **psycopg v3**, синхронный движок: см. **`mandala.db.engine.create_engine_from_env`**). Скрипт проверки Postgres тоже использует **`psycopg`**. Формат **`postgresql+asyncpg://`** при необходимости можно ввести позже отдельным изменением.

## Проверки (линтер, типы, тесты)

Одна команда из корня репозитория (сначала зависимости — см. выше; для pip активируйте `.venv`):

```bash
make check
```

То же через bash-скрипт (при наличии `.venv` обновит `pip install -e ".[dev]"`):

```bash
bash scripts/check.sh
```

**Полная проверка** (при **`DATABASE_URL`** в окружении или в **`.env`**: сначала Postgres и **`alembic upgrade head`**, затем те же шаги, что в **`check.sh`** — в том числе **`pytest`** с интеграционными тестами; без **`DATABASE_URL`** выполняется только **`check.sh`**):

```bash
bash scripts/verify_project.sh
```

Эквивалент: `make verify` (вызывает тот же скрипт).

С явным интерпретатором: `PY=.venv/bin/python make check`.

Если установлен **uv**:

```bash
uv sync --extra dev && uv run make check
```

Без Make (эквивалент цели `check`):

```bash
uv sync --extra dev && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts && uv run mypy src/mandala tests scripts && uv run pytest
```

## CI (GitHub Actions, тикет 22)

В репозитории настроен workflow **`.github/workflows/ci.yml`**: при **`push`** в ветки **`main`** / **`master`** и при **`pull_request`** выполняются **`uv sync --extra dev --frozen`**, **ruff**, **mypy**, контейнер **PostgreSQL 16.6** (образ **`postgres:16.6-alpine`**, учётные данные как в **`.env.example`**), проверка подключения, **`alembic upgrade head`**, полный **`pytest`** (включая маркер **`integration`**). Отдельные секреты **`DATABASE_URL`** в репозитории не нужны: URL задан в workflow и совпадает с сервисом. В **форках** первый запуск Actions может потребовать разрешения у владельца upstream-репозитория; при отключённых Actions проверку повторяют локально: **`bash scripts/check.sh`** и при поднятом Postgres — **`bash scripts/verify_project.sh`**.

## Тесты отдельно

```bash
uv run pytest
```

### Интеграционные тесты (тикеты 5, 7, 8, 10, 12–17)

Файлы в `tests/integration/` помечены `pytest.mark.integration` и **пропускаются**, если не задан **`DATABASE_URL`**, или если БД не накатана до `alembic upgrade head`. Среди них: репозитории, квоты, идентичность пользователя, **HTTP (`test_http_integration.py`)** — health и webhook против живой БД; **`test_text_reply_quota.py`** (тикет 12: отказ по квоте без вызова LLM; анкета в профиле помечена завершённой); **`test_text_reply_dialog_memory.py`** (тикет 17: в контекст LLM попадают предыдущие реплики из **`messages`** и опционально **`dialog_summary`**); **`test_scenario_intake.py`** (тикет 13: анкета и JSONB); **`test_image_router_quota.py`** (тикет 14–15: **`image_generation`=0** без вызова клиента; успех premium и запись **`generated_artifacts`**). Юнит-тесты загрузки JSON анкеты — **`tests/test_intake_loader.py`**. Изоляция RAG по **`vertical_id`** — **`tests/test_rag_vertical_isolation.py`** (без Postgres). Сборка истории для чата — **`tests/test_text_reply_dialog_context.py`**.

```bash
export $(grep -v '^#' .env | xargs)
podman compose up -d
python -m alembic upgrade head
pytest -v -m integration
```

### Абстракция LLM (тикет 11)

Пакет **`mandala.llm`**: клиент **`OpenAICompatibleTextClient`** шлёт **`POST {LLM_BASE_URL}/chat/completions`** с заголовком **`Authorization: Bearer`**. Глобальные параметры — **`LLM_BASE_URL`**, **`LLM_API_KEY`**, **`LLM_MODEL`**. Переопределения для slug вертикали (как в seed: `astrology`, `therapy`) — JSON, путь в **`LLM_VERTICAL_OVERRIDES_PATH`** или встроенный пример **`mandala/llm/vertical_overrides.json`**.

**Тикет 12–15:** до завершения анкеты цепочка шагов из JSON (**`mandala/verticals/intake_steps.json`**, опционально **`MANDALA_INTAKE_STEPS_PATH`**) и **`mandala.services.scenario_intake`**; затем при **`/image`**, **`/picture`**, префиксах **`нарисуй `** / **`draw `** — **`mandala.services.image_reply`**: квота **`image_generation`**, при успехе — запись в **`messages`** (assistant, `content_kind=image`) и **`generated_artifacts`** (`kind=image`, JSONB: `image_url`, `stub_ref`, `provider`), **`consume`** после успешного ответа провайдера; в Telegram — **`sendPhoto`** с URL, если провайдер вернул **`image_url`** (режим по умолчанию без **`IMAGE_GENERATION_PROVIDER`** — заглушка без фото). Иначе **`mandala.services.text_reply`** + **`mandala.verticals`** + **`messages`** и **`QuotaService`** — см. **`handle_inbound`** и **`docs/implementation-plan.md`**.

**Генерация изображений (тикет 15):** переменные **`IMAGE_GENERATION_PROVIDER`** (`stub` или `openai_compatible`), **`IMAGE_BASE_URL`** и **`IMAGE_API_KEY`** (если не заданы — подставляются **`LLM_BASE_URL`** и **`LLM_API_KEY`**), **`IMAGE_MODEL`** (например `dall-e-3`). Запрос — **`POST …/images/generations`** в формате OpenAI (`response_format=url`). При ошибке API пользователь получает короткое сообщение об ошибке, **`consume`** не выполняется. **MVP без очереди:** генерация синхронна в обработчике webhook/polling (ответ Telegram приходит после завершения HTTP к провайдеру; таймаут чтения ~180 с).

### Опциональный live-тест LLM (тикет 11)

Юнит-тесты LLM используют **`httpx.MockTransport`** (без сети). Чтобы прогнать **один** реальный вызов к провайдеру, задайте в окружении валидные **`LLM_BASE_URL`**, **`LLM_API_KEY`**, **`LLM_MODEL`** и включите флаг:

```bash
export LLM_LIVE_TEST=1
pytest tests/test_llm_openai_client.py -v -m llm_live
```

Без **`LLM_LIVE_TEST=1`** тест с маркером **`llm_live`** пропускается.

## RAG и Qdrant (тикет 16)

**По умолчанию RAG выключен** (`MANDALA_RAG_BACKEND` не задан или `none`): текстовый ответ идёт только с системным промптом вертикали, без обращения к векторному хранилищу.

Чтобы включить retrieval в **`text_reply`**:

1. Поднять **Qdrant** (сервис в **`compose.yaml`**, порт **`QDRANT_PORT`**, по умолчанию **6333**): `podman compose up -d qdrant`.
2. В `.env`: **`MANDALA_RAG_BACKEND=qdrant`**, **`QDRANT_URL=http://localhost:6333`** (и при необходимости **`QDRANT_API_KEY`** для managed).
3. Положить сырьё KB в **`src/mandala/verticals/kb/{vertical_id}/`** (`*.md`, `*.txt`) или задать **`MANDALA_KB_ROOT`** с подкаталогами по slug.
4. Проиндексировать (нужны **`LLM_BASE_URL`**, **`LLM_API_KEY`** — те же, что для чата; модель эмбеддингов — **`LLM_EMBEDDING_MODEL`**, по умолчанию `text-embedding-3-small`; размер вектора **`RAG_VECTOR_SIZE`**, по умолчанию **1536**):

```bash
export $(grep -v '^#' .env | xargs)
python -m mandala.index_kb --vertical astrology
# Полная пересборка коллекции: добавьте --recreate-collection
```

Перед вызовом чат-модели в системный промпт добавляются top-**`RAG_TOP_K`** фрагментов (по умолчанию 5), суммарно не длиннее **`RAG_MAX_CONTEXT_CHARS`** символов (по умолчанию 8000). Это **не** лимит токенов всего запроса: бюджет токенов задаёт провайдер и параметр **`max_tokens`** в коде (**1024** для текстового ответа). История диалога (тикет 17) ограничивается **числом последних сообщений** из Postgres (**`TEXT_REPLY_CONTEXT_MESSAGES`** = **20** в **`mandala.services.text_reply`**), без отдельной нарезки по токенам; при длинных репликах суммарный контекст может приблизиться к лимитам модели — см. **`docs/agent.md`**.

Изоляция вертикалей: в Qdrant в payload каждой точки записан **`vertical_id`**, при поиске задаётся фильтр; смоук-тест — **`tests/test_rag_vertical_isolation.py`**.

## Telegram-бот (тикет 9, long polling)

Нужны **живой Postgres** (миграции до `head`), переменные **`DATABASE_URL`**, **`TELEGRAM_BOT_TOKEN`** (от [@BotFather](https://t.me/BotFather)), **`TELEGRAM_VERTICAL_ID`** — slug из seed (`astrology` или `therapy`), а также **`LLM_BASE_URL`**, **`LLM_API_KEY`**, **`LLM_MODEL`** — для ответа через LLM **после** короткой анкеты (тикеты 12–13). Картинки: план **premium** (в seed — ненулевой лимит **`image_generation`**) и при необходимости **`IMAGE_GENERATION_PROVIDER=openai_compatible`** + ключи/URL (см. выше). В логах токен **маскируется**; не коммить реальные секреты.

```bash
export $(grep -v '^#' .env | xargs)   # или вручную export …
python -m alembic upgrade head
python -m mandala.adapters.telegram
```

Остановка: `Ctrl+C`. **Webhook** и приём апдейтов через HTTP — см. раздел ниже.

## HTTP-приложение (тикеты 10, 21: FastAPI + webhook + Web API)

Нужны **`DATABASE_URL`**, миграции до `head`, **`LLM_BASE_URL`**, **`LLM_API_KEY`**, **`LLM_MODEL`**, для ответов в Telegram — **`TELEGRAM_BOT_TOKEN`** и совпадение **`TELEGRAM_VERTICAL_ID`** с `{vertical_id}` в URL webhook. Для RAG (тикет 16) дополнительно: **`MANDALA_RAG_BACKEND=qdrant`**, **`QDRANT_URL`**, проиндексированная коллекция (**`python -m mandala.index_kb`**).

```bash
export $(grep -v '^#' .env | xargs)   # или вручную export …
python -m alembic upgrade head
python -m mandala.http
```

Запуск через **uvicorn** внутри модуля (порт **`PORT`**, хост **`HOST`**, по умолчанию `8000` и `0.0.0.0`).

**Endpoints:**

- `GET /health` — проверка доступности приложения и PostgreSQL (`SELECT 1` через тот же пул, что и у домена)
- `POST /webhooks/telegram/{vertical_id}` — webhook: тело — JSON **Update** Telegram; **`vertical_id` в URL** должен совпадать с slug вертикали бота (в MVP — с **`TELEGRAM_VERTICAL_ID`** из `.env`, иначе ответ в Telegram не отправится: нет маппинга токена на другую вертикаль)
- `POST /webhooks/web` — канал **Web** без UI: тот же **`handle_inbound`**, что и у Telegram после идентификации. JSON-тело: опционально **`text`**, **`vertical_id`**, **`locale`**, **`callback_data`**; **`vertical_id`** можно передать заголовком **`X-Vertical-Id`** (если в теле пусто — берётся заголовок). Обязателен заголовок **`X-External-User-Id`** — стабильный внешний id пользователя в канале `web` (MVP). Ответ: **`{ "messages": [ … ] }`** — список **`OutboundMessage`** (OpenAPI: `/docs`). Маппинг **`Authorization: Bearer`** → вертикаль — пока не реализован (**TODO** в коде).

Пример вызова Web-API:

```bash
curl -sS -X POST "http://localhost:8000/webhooks/web" \
  -H "Content-Type: application/json" \
  -H "X-External-User-Id: demo-user-1" \
  -H "X-Vertical-Id: astrology" \
  -d '{"text": "/start"}' | jq .
```

**Настройка webhook в Telegram:**

URL в **`setWebhook`** и slug в пути должны совпадать: например вертикаль `astrology` → `…/webhooks/telegram/astrology`.

Для настройки webhook используйте Telegram Bot API:

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{
    \"url\": \"https://your-domain.com/webhooks/telegram/${TELEGRAM_VERTICAL_ID}\",
    \"secret_token\": \"${TELEGRAM_WEBHOOK_SECRET}\"
  }"
```

**Переменные окружения (тикет 10):**

- `PORT` — порт для HTTP сервера (по умолчанию 8000)
- `HOST` — адрес для привязки (по умолчанию 0.0.0.0)
- `TELEGRAM_WEBHOOK_SECRET` — строка, которую вы передаёте в **`setWebhook`** как `secret_token`; сервер сравнивает её с заголовком **`X-Telegram-Bot-Api-Secret-Token`**. Если переменная **не задана**, проверка заголовка **отключена** (удобно для локальной отладки; **в продакшене секрет задавайте обязательно**).

Юнит-тесты HTTP: `tests/test_http_app.py` (моки БД и Telegram), маппинг Web — `tests/test_web_inbound_map.py`. Интеграционные — `tests/integration/test_http_integration.py` при `DATABASE_URL`.

**Проверка health:**

```bash
curl http://localhost:8000/health
```

При работающем Postgres вернёт: `{"status": "ok", "database": "ok"}`
