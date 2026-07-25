# Деплой Mandala — единый способ

> **Единый источник правды по деплою.** Один канонический способ выкатки:
>
> ```bash
> bash scripts/deploy/deploy.sh
> ```
>
> Всё остальное в этом каталоге — вспомогательное или устаревшее (см. ниже). Не деплой другими путями.

Целевая схема прода (ВМ, Nginx, Managed PostgreSQL, контейнер **`mandala-http`**) — **[docs/deployment-yandex-cloud.md](../../docs/deployment-yandex-cloud.md)**.

## Как деплоить

Из корня репозитория:

```bash
bash scripts/deploy/deploy.sh
```

Скрипт делает всё сам и **гарантированно** — с ретраями и авто-откатом:

1. **rsync** исходника на ВМ (только код: без `.git`, `.venv`, кэшей, `dist`, `.gnhf`);
2. **нативная сборка** образа `amd64` **прямо на ВМ** (`docker build`) — без эмуляции Rosetta и без перекачки многосотмегабайтного tar;
3. **`restart_app.sh`** на ВМ: пересоздать `mandala-http` с `--env-file /opt/mandala/env`, при `RUN_MIGRATIONS=1` — `alembic upgrade head`, дождаться `/health`;
4. **E2E на реальном проде**: `GET /health` и `POST /webhooks/web` (`/help`);
5. при провале рестарта или E2E — **авто-откат** на предыдущий образ и повторная проверка;
6. **prune** старых образов на ВМ (оставляет `KEEP_IMAGES` + запущенный).

Прод не трогается, пока сборка не готова: при сбое rsync/сборки контейнер остаётся на текущем образе.

### Параметры (env, с дефолтами)

- `SSH_HOST=ubuntu@api.mandala-app.online` — куда деплоим
- `BASE_URL=https://api.mandala-app.online` — для E2E
- `RUN_MIGRATIONS=1` — `alembic upgrade head` перед стартом (0 чтобы пропустить)
- `REMOTE_SRC=mandala-build` — каталог сборки в `$HOME` пользователя `ubuntu`
- `RETRIES=2` — повторов rsync/сборки при транзиентном сбое
- `KEEP_IMAGES=3` — сколько образов оставить на ВМ

```bash
RUN_MIGRATIONS=0 bash scripts/deploy/deploy.sh     # без миграций
SSH_HOST=ubuntu@staging bash scripts/deploy/deploy.sh
```

### Предпосылки (уже настроены на проде)

- **passwordless SSH** на ВМ (`ssh ubuntu@api.mandala-app.online true` проходит без пароля);
- на ВМ: **docker**, файл окружения **`/opt/mandala/env`** и скрипт **`/opt/mandala/restart_app.sh`** (копия [`restart_app.sh`](restart_app.sh); при правке — обновить и на ВМ, см. ниже);
- секреты (`DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `LLM_*`, `TELEGRAM_WEBHOOK_SECRET`) — только в `/opt/mandala/env`, в git не коммитятся.

### После деплоя нового кода с Telegram-фичами

Telegram-вебхук живёт на стороне Telegram и деплоем не меняется. Если бот перестал отвечать —
проверь, что вебхук указывает на ВМ (а не на старый serverless-контейнер):

```bash
ssh ubuntu@api.mandala-app.online 'set -a; . /opt/mandala/env; set +a; \
  curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"'
# при необходимости — setWebhook на https://api.mandala-app.online/webhooks/telegram/<vertical_id>
```

## Файлы каталога

| Файл | Назначение |
|------|------------|
| **`deploy.sh`** | **Единственный способ деплоя** (этот README). Удалённая сборка + E2E + авто-откат. |
| `restart_app.sh` | Вызывается `deploy.sh` **на ВМ**: пересоздать контейнер, миграции, ждать `/health`. Лежит на ВМ в `/opt/mandala/`. |
| `nginx-*.conf.example` | Пример vhost для Nginx на ВМ (reverse proxy на `127.0.0.1:8000`). |
| `unified-agent/` | Доставка логов приложения в **YC Logging** (Unified Agent + systemd). Аддитивно, не трогает деплой. Гайд — [docs/logging.md](../../docs/logging.md). |
| ~~`build_image.sh`~~ | Устаревшее: локальная сборка образа. `deploy.sh` собирает на ВМ — этот скрипт больше не нужен для деплоя. |
| ~~`deploy-serverless.sh`~~ | Устаревшее: путь Yandex Serverless Container. Прод сейчас — ВМ; **не использовать**. |

### Обновить `restart_app.sh` на ВМ (если правил в репо)

```bash
scp scripts/deploy/restart_app.sh ubuntu@api.mandala-app.online:/tmp/
ssh ubuntu@api.mandala-app.online 'sudo install -m 0755 -o root -g root /tmp/restart_app.sh /opt/mandala/restart_app.sh'
```

## Бэкапы БД

[Резервные копии и PITR — Yandex Managed PostgreSQL](https://yandex.cloud/ru/docs/managed-postgresql/concepts/backup).
