# Первый MVP в проде: что сделать тебе (Telegram + LLM, в т.ч. DeepSeek)

Краткий чеклист: куда вписать **токен Telegram**, **ключ и URL DeepSeek** (или любого OpenAI-совместимого API), проверить **webhook** и **health**.

Подробности инфраструктуры YC — **[deployment-yandex-cloud.md](deployment-yandex-cloud.md)**. Переменные по смыслу совпадают с **`.env.example`** в корне репозитория.

---

## 0. Что уже должно быть сделано

- В YC: **ВМ**, **Managed PostgreSQL**, DNS **`api.<твой-домен>`**, **Nginx + HTTPS**, контейнер **`mandala-http`**.
- В файле на ВМ **`/opt/mandala/env`** уже есть рабочий **`DATABASE_URL`** к БД **`mandala`** (пользователь **`mandala_app`**, SSL).

Если контейнер ещё не поднимали — см. **[scripts/deploy/README.md](../scripts/deploy/README.md)**.

---

## 1. Редактируешь только файл на сервере

Подключись по SSH:

```bash
ssh ubuntu@<публичный-IP-ВМ>
sudo nano /opt/mandala/env
# или: sudo vim /opt/mandala/env
```

Файл **не** должен попадать в git. Права: **`chmod 600`**, владелец — тот, под кем запускаешь **`docker`**.

---

## 2. Telegram (бот + webhook)

Добавь / проверь строки:

```bash
TELEGRAM_BOT_TOKEN=<токен от @BotFather>
TELEGRAM_VERTICAL_ID=astrology
# или therapy — slug должен совпадать с seed в БД и с путём webhook
TELEGRAM_WEBHOOK_SECRET=<длинная случайная строка, та же что в setWebhook>
```

**Важно:** `TELEGRAM_VERTICAL_ID` должен совпадать с **`{vertical_id}`** в URL webhook (ниже).

После сохранения файла:

```bash
docker restart mandala-http
```

**Повесить webhook** (с компьютера, подставь свои значения):

```bash
curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{
    \"url\": \"https://api.mandala-app.online/webhooks/telegram/${TELEGRAM_VERTICAL_ID}\",
    \"secret_token\": \"${TELEGRAM_WEBHOOK_SECRET}\"
  }"
```

Если DNS у тебя другой — замени **`api.mandala-app.online`** на свой хост.

Проверка ответа Telegram: в JSON должно быть **`"ok":true`**. Ошибка **400** часто из‑за неверного токена или невалидного JSON.

---

## 3. DeepSeek (или другой OpenAI-compatible чат)

Клиент в коде ходит на **`POST {LLM_BASE_URL}/chat/completions`** с заголовком **`Authorization: Bearer …`**.

Для **DeepSeek** типично:

```bash
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=<ключ из консоли DeepSeek>
LLM_MODEL=deepseek-chat
```

Добавь эти три строки в **`/opt/mandala/env`**, затем снова:

```bash
docker restart mandala-http
```

Актуальные URL и имена моделей смотри в [документации DeepSeek API](https://api-docs.deepseek.com/) (они могут меняться).

Если используешь **OpenAI** / **совместимый прокси** — поменяй только **`LLM_BASE_URL`** и **`LLM_MODEL`**.

---

## 4. Быстрая проверка без Telegram

С любой машины, где резолвится твой DNS:

```bash
curl -sS "https://api.mandala-app.online/health"
```

Ожидаемо: **`{"status":"ok","database":"ok"}`**. Если **503** — БД не доступна из контейнера (проверь **`DATABASE_URL`**, security group кластера, что контейнер перезапущен после правки **`env`**).

---

## 5. Проверка сценария «сообщение в боте»

1. В Telegram напиши боту **`/start`** (пойдёт анкета по вертикали из seed).
2. Если ответа нет — на ВМ: **`docker logs mandala-http --tail 100`**.
3. Частые причины: неверный **`TELEGRAM_WEBHOOK_SECRET`**, webhook не на HTTPS, **`TELEGRAM_VERTICAL_ID`** не совпадает с URL, не заданы **`LLM_*`**, исчерпана квота **`text_reply`** на плане **free** (см. **`docs/quotas-and-plans.md`**).

---

## 6. Локальная отладка (без YC)

Скопируй **`.env.example`** → **`.env`**, подставь те же переменные, подними Postgres (**`podman compose`**) и запускай **`python -m mandala.http`** или **`bash scripts/verify_project.sh`** — см. корневой **README.md**.

---

## 7. Чеклист перед «считаю MVP готовым»

- [ ] **`/opt/mandala/env`**: **`DATABASE_URL`**, **`LLM_*`**, **`TELEGRAM_*`**, **`TELEGRAM_WEBHOOK_SECRET`**
- [ ] **`docker restart mandala-http`**, **`curl` /health** — OK
- [ ] **`setWebhook`** — **`ok: true`**
- [ ] Сообщение боту — ответ после анкеты (нужен рабочий LLM)
- [ ] Секреты **нигде** не закоммичены в git
