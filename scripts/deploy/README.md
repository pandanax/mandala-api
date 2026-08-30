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

Production использует Yandex Cloud OS Login, поэтому локально сначала проверьте эффективного
пользователя и передавайте его явно. На текущем owner-workstation рабочая команда:

```bash
ssh -o BatchMode=yes pandanaxya@api.mandala-app.online true
SSH_HOST=pandanaxya@api.mandala-app.online bash scripts/deploy/deploy.sh
```

Устаревший default `ubuntu@…` может вернуть `Permission denied (publickey)`. Это не повод менять
ключи или пользователей ВМ: остановите попытку и укажите корректный `SSH_HOST`.

### Предпосылки (уже настроены на проде)

- **passwordless SSH** на ВМ для эффективного OS Login пользователя
  (`ssh -o BatchMode=yes pandanaxya@api.mandala-app.online true` проходит без пароля);
- на ВМ: **docker**, файл окружения **`/opt/mandala/env`** и скрипт **`/opt/mandala/restart_app.sh`** (копия [`restart_app.sh`](restart_app.sh); при правке — обновить и на ВМ, см. ниже);
- секреты (`DATABASE_URL`, `TELEGRAM_BOT_TOKEN`, `LLM_*`, `TELEGRAM_WEBHOOK_SECRET`) — только в `/opt/mandala/env`, в git не коммитятся.

### Telegram delivery: webhook и polling взаимоисключающие

Для одного bot token должен работать ровно один способ доставки. Production nutrition и
astrology сейчас используют общий multi-token polling-контейнер. Нельзя одновременно оставлять
старый webhook: Telegram вернёт polling-потоку `409 Conflict`.

Чек-лист переноса токена с webhook (например, n8n) на polling:

1. Записать старый URL через `getWebhookInfo`, чтобы rollback был возможен.
2. Вызвать `deleteWebhook` без `drop_pending_updates=true`; не удалять старый workflow или его
   инфраструктуру без отдельного разрешения.
3. Убедиться, что `getWebhookInfo.result.url == ""`.
4. Перезапустить `mandala-telegram-polling`. Код обязан передавать явный `allowed_updates` со
   значениями `message`, `edited_message`, `callback_query`, `pre_checkout_query`: Telegram
   запоминает старый фильтр, и без этого тексты могут работать, а inline-кнопки — бесконечно
   крутиться.
5. Проверить в логах старт нужной vertical, `getUpdates 200 OK`, отсутствие `409`, затем вручную
   пройти `текст → inline-кнопка → следующий шаг`. Проверка только текста недостаточна.

Если выбран webhook, наоборот остановите polling для этого токена, задайте per-vertical secret и
явный список update types при `setWebhook`. Никогда не переключайте оба механизма одновременно.

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
DEPLOY_TARGET=pandanaxya@api.mandala-app.online
scp scripts/deploy/restart_app.sh "$DEPLOY_TARGET:/tmp/"
ssh "$DEPLOY_TARGET" 'sudo install -m 0755 -o root -g root /tmp/restart_app.sh /opt/mandala/restart_app.sh'
```

## Бэкапы БД

[Резервные копии и PITR — Yandex Managed PostgreSQL](https://yandex.cloud/ru/docs/managed-postgresql/concepts/backup).
