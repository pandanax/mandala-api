# Установка, переменные окружения и первый запуск

После того как репозиторий склонирован и зависимости стоят — это **первый шаг**: локально или на сервере задать **env**, миграции, запуск HTTP/Telegram. Прод в YC — **[deployment-yandex-cloud.md](deployment-yandex-cloud.md)** и **[scripts/deploy/README.md](../scripts/deploy/README.md)**.

Полный список ключей — **`.env.example`** в корне репозитория (в git только заглушки, без секретов).

---

## 1. Установка (разработка)

Нужен **Python 3.11+**. Удобнее всего **[uv](https://docs.astral.sh/uv/)**:

```bash
uv sync --extra dev
```

Повторяемо с lockfile: `uv sync --extra dev --frozen`.

Через **pip**:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

---

## 2. Локальная база (Podman)

```bash
cp .env.example .env
podman compose up -d
```

Проверка БД: `make db-check` (нужен **`DATABASE_URL`** в окружении или в **`.env`**).

Миграции:

```bash
export $(grep -v '^#' .env | xargs)   # или вручную DATABASE_URL=…
python -m alembic upgrade head
```

Сквозная проверка: **`bash scripts/verify_project.sh`** (см. корневой **README** — раздел про проверки).

---

## 3. Запуск приложения

**HTTP (webhook + Web API):**

```bash
export $(grep -v '^#' .env | xargs)
python -m alembic upgrade head   # если ещё не накатывали
python -m mandala.http
```

**Telegram (long polling):**

```bash
python -m mandala.adapters.telegram
```

Нужны **`DATABASE_URL`**, **`TELEGRAM_BOT_TOKEN`**, **`TELEGRAM_VERTICAL_ID`**, **`LLM_*`** — см. **`.env.example`**. Webhook в проде — HTTPS и **`TELEGRAM_WEBHOOK_SECRET`** (см. ниже).

---

## 4. Прод: файл окружения на сервере

На ВМ (пример пути **`/opt/mandala/env`**) задаёшь те же переменные, что и в **`.env`**, плюс строка **`DATABASE_URL`** к Managed PostgreSQL (**`sslmode=require`**). Файл **не** в git, права **`chmod 600`**.

После правок:

```bash
docker restart mandala-http
```

(или эквивалент для твоего способа запуска контейнера — см. **[scripts/deploy/README.md](../scripts/deploy/README.md)**.)

---

## 5. Telegram и LLM (в т.ч. DeepSeek)

В **`/opt/mandala/env`** (или локальный **`.env`**):

```bash
TELEGRAM_BOT_TOKEN=<от @BotFather>
TELEGRAM_VERTICAL_ID=astrology
TELEGRAM_WEBHOOK_SECRET=<случайная строка; в проде обязательно>
```

**Webhook** (подставь свой хост вместо примера):

```bash
curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{
    \"url\": \"https://api.mandala-app.online/webhooks/telegram/${TELEGRAM_VERTICAL_ID}\",
    \"secret_token\": \"${TELEGRAM_WEBHOOK_SECRET}\"
  }"
```

**DeepSeek** (OpenAI-совместимый чат):

```bash
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=<ключ>
LLM_MODEL=deepseek-chat
```

Документация провайдера: [DeepSeek API](https://api-docs.deepseek.com/).

---

## 6. Проверки

```bash
curl -sS "https://<твой-api-хост>/health"
```

Ожидаемо: `{"status":"ok","database":"ok"}`. В Telegram — **`/start`** после настройки LLM; логи: **`docker logs mandala-http`** на сервере.

Квоты и планы — **[quotas-and-plans.md](quotas-and-plans.md)**.

---

## 7. Линтер и тесты (разработка)

```bash
bash scripts/check.sh
```

С живой БД: **`bash scripts/verify_project.sh`**.

**Интеграционные тесты** (маркер **`integration`**, нужны **`DATABASE_URL`** и **`alembic upgrade head`**):

```bash
export $(grep -v '^#' .env | xargs)
podman compose up -d
python -m alembic upgrade head
pytest -v -m integration
```

**RAG (Qdrant):** по умолчанию выключен. Включение, индексация **`python -m mandala.index_kb`**, переменные **`MANDALA_RAG_BACKEND`**, **`QDRANT_URL`** — в **[agent.md](agent.md)** и **[architecture.md](architecture.md)**.

Опционально один живой вызов к LLM в тестах: задать **`LLM_*`**, **`export LLM_LIVE_TEST=1`**, затем **`pytest tests/test_llm_openai_client.py -v -m llm_live`**.
