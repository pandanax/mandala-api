# Mandala

Бэкенд и документация продукта «Mandala»: архитектура в `docs/`, пошаговый план — `docs/implementation-plan.md`.

По коду на сейчас: тикеты **5–10** закрыты — репозитории и сервисы (**`PlansRepository`**, **`QuotaService`**, **`UserIdentityService`**), **`mandala.domain`** (`handle_inbound(event, conn)`), адаптер **`mandala.adapters.telegram`** (long polling), HTTP приложение **`mandala.http`** (FastAPI: health и webhook); следующий шаг плана — **тикет 11** (абстракция LLM).

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

**Полная проверка** (базовые проверки + при наличии `DATABASE_URL` в окружении или в `.env`: Postgres, `alembic upgrade head`, только интеграционные тесты):

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

## Тесты отдельно

```bash
uv run pytest
```

### Интеграционные тесты (тикеты 5, 7, 8, 10)

Файлы в `tests/integration/` помечены `pytest.mark.integration` и **пропускаются**, если не задан **`DATABASE_URL`**, или если БД не накатана до `alembic upgrade head`. Среди них: репозитории, квоты, идентичность пользователя, **HTTP (`test_http_integration.py`)** — health и webhook против живой БД.

```bash
export $(grep -v '^#' .env | xargs)
podman compose up -d
python -m alembic upgrade head
pytest -v -m integration
```

## Telegram-бот (тикет 9, long polling)

Нужны **живой Postgres** (миграции до `head`), переменные **`DATABASE_URL`**, **`TELEGRAM_BOT_TOKEN`** (от [@BotFather](https://t.me/BotFather)) и **`TELEGRAM_VERTICAL_ID`** — slug из seed (`astrology` или `therapy`). В логах токен **маскируется**; не коммить реальные секреты.

```bash
export $(grep -v '^#' .env | xargs)   # или вручную export …
python -m alembic upgrade head
python -m mandala.adapters.telegram
```

Остановка: `Ctrl+C`. **Webhook** и приём апдейтов через HTTP — см. раздел ниже.

## HTTP-приложение (тикет 10, FastAPI + webhook)

Нужны **`DATABASE_URL`**, миграции до `head`, для ответов в Telegram — **`TELEGRAM_BOT_TOKEN`** и совпадение **`TELEGRAM_VERTICAL_ID`** с `{vertical_id}` в URL webhook.

```bash
export $(grep -v '^#' .env | xargs)   # или вручную export …
python -m alembic upgrade head
python -m mandala.http
```

Запуск через **uvicorn** внутри модуля (порт **`PORT`**, хост **`HOST`**, по умолчанию `8000` и `0.0.0.0`).

**Endpoints:**

- `GET /health` — проверка доступности приложения и PostgreSQL (`SELECT 1` через тот же пул, что и у домена)
- `POST /webhooks/telegram/{vertical_id}` — webhook: тело — JSON **Update** Telegram; **`vertical_id` в URL** должен совпадать с slug вертикали бота (в MVP — с **`TELEGRAM_VERTICAL_ID`** из `.env`, иначе ответ в Telegram не отправится: нет маппинга токена на другую вертикаль)

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

Юнит-тесты HTTP: `tests/test_http_app.py` (моки БД и Telegram). Интеграционные — `tests/integration/test_http_integration.py` при `DATABASE_URL`.

**Проверка health:**

```bash
curl http://localhost:8000/health
```

При работающем Postgres вернёт: `{"status": "ok", "database": "ok"}`
