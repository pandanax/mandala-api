# Деплой образа Mandala

Целевая схема (ВМ, Nginx, Managed PostgreSQL, контейнер **`mandala-http`**) — **`docs/deployment-yandex-cloud.md`**. Ниже — сборка образа, env на ВМ, типовые команды Docker.

**GitHub Actions** в этот репозиторий **не** выкладывает в Yandex — только проверки кода. Выкладка — вручную или скриптами отсюда.

## 1. Образ приложения

Из корня репозитория (нужен **Podman** или **Docker**):

```bash
bash scripts/deploy/build_image.sh
# или: CONTAINER_ENGINE=docker bash scripts/deploy/build_image.sh
# тег: MANDALA_IMAGE=mandala:1.0.0 bash scripts/deploy/build_image.sh
# платформа по умолчанию linux/amd64 (типичная ВМ в YC); для локального Mac ARM: MANDALA_PLATFORM=linux/arm64
```

Сохранить образ на ВМ (пример; для **amd64**-ВМ образ должен быть собран с **`MANDALA_PLATFORM=linux/amd64`**):

```bash
podman save mandala:local | ssh ubuntu@<VM> 'docker load'
```

Либо registry (отдельная настройка), либо `podman pull` с вашего хранилища образов.

## 2. Переменные окружения на ВМ

Создайте файл (пример пути **`/opt/mandala/env`**, **не** в git):

- **`DATABASE_URL`** — Managed PostgreSQL (отдельная БД и пользователь для Mandala; SSL по [доке YC](https://yandex.cloud/ru/docs/managed-postgresql/operations/connect)).
- **`LLM_BASE_URL`**, **`LLM_API_KEY`**, **`LLM_MODEL`**
- **`TELEGRAM_BOT_TOKEN`**, **`TELEGRAM_VERTICAL_ID`**
- **`HOST`**, **`PORT`**: внутри контейнера за Nginx из Docker-сети удобнее **`HOST=0.0.0.0`** и публикация **`-p 8000:8000`** на хост; тогда **`proxy_pass`** в Nginx указывает на **шлюз bridge → хост** (часто **`172.18.0.1:8000`** — проверьте `Gateway` в `docker inspect` для контейнера **nginx**). Пример готового конфига: **`scripts/deploy/nginx-mandala-api.conf.example`**.
- при webhook: **`TELEGRAM_WEBHOOK_SECRET`**
- опционально RAG: **`MANDALA_RAG_BACKEND`**, **`QDRANT_URL`**, … — см. **`.env.example`**

## 3. Миграции

На ВМ (или с машины с доступом к БД), с тем же **`DATABASE_URL`**:

```bash
docker run --rm --env-file /opt/mandala/env mandala:local python -m alembic upgrade head
```

(Команда **`alembic`** в PATH внутри образа.)

## 3.1. Полный деплой одной командой

Для типового случая (правка кода → собрать → закатить на ВМ → перезапустить → smoke-check) есть скрипт-обёртка [`deploy.sh`](deploy.sh):

```bash
# с локальной машины из корня репозитория:
bash scripts/deploy/deploy.sh                # тег по дате-времени
bash scripts/deploy/deploy.sh ux-start3      # явный тег
RUN_MIGRATIONS=1 bash scripts/deploy/deploy.sh   # с миграциями
SSH_HOST=ubuntu@<VM> bash scripts/deploy/deploy.sh
```

Что делает: `build_image.sh` под `linux/amd64` → `podman save` → `scp` → на ВМ `docker load` + `restart_app.sh` → ждёт `/health` → удаляет старые `localhost/mandala:*` образы на ВМ, оставляя `KEEP_REMOTE_IMAGES` самых свежих (по умолчанию 2) плюс защиту запущенного. Tar-файлы убирает сам и локально, и на ВМ.

Требования:
- на ВМ уже лежат `/opt/mandala/restart_app.sh` и `/opt/mandala/env`;
- ssh-ключ настроен (без пароля).

### 3.1.1. Если что-то пошло не так с диском

Если на ВМ закончилось место (типичные симптомы: `podman build` висит часами, `scp` падает с `No space left`), сначала почистите старые образы вручную:

```bash
ssh ubuntu@api.mandala-app.online '
  RUNNING_IMG=$(sudo docker inspect -f "{{.Config.Image}}" mandala-http 2>/dev/null || true)
  sudo docker images --format "{{.Repository}}:{{.Tag}}" \
    | grep "^localhost/mandala:" \
    | grep -v "^${RUNNING_IMG}$" \
    | xargs -r -n1 sudo docker rmi || true
  sudo docker image prune -f
  rm -f /tmp/mandala-*.tar
  df -h /
'
```

Локально аналогично: `podman image prune -f`, `rm -f /tmp/mandala-*.tar`. После обновления `deploy.sh` (с шагом prune) такие ситуации возникают редко — но если кто-то деплоил руками или прерывал сборку, мусор накапливается.

## 4. Запуск контейнера (пример)

```bash
docker run -d --name mandala-http --restart unless-stopped \
  --env-file /opt/mandala/env \
  -e HOST=0.0.0.0 -e PORT=8000 \
  -p 8000:8000 \
  mandala:local
```

Если Nginx в Docker проксирует на **`172.18.0.1:8000`**, на хосте должен слушать порт **8000** (не только **127.0.0.1**), иначе с bridge не достучаться.

### 4.1. Рестарт после правки `/opt/mandala/env`

⚠️ `docker restart mandala-http` **не** перечитывает `--env-file`. Используй скрипт **[`restart_app.sh`](restart_app.sh)** — он делает `stop` + `rm` + `run` с актуальным env-file и ждёт `/health`:

```bash
# на ВМ (скрипт уже скопирован в /opt/mandala/):
sudo bash /opt/mandala/restart_app.sh

# с миграциями (если деплоится новая схема БД):
sudo RUN_MIGRATIONS=1 bash /opt/mandala/restart_app.sh
```

Скопировать обновлённый скрипт на ВМ:

```bash
scp scripts/deploy/restart_app.sh ubuntu@<VM>:/tmp/
ssh ubuntu@<VM> 'sudo install -m 0755 -o root -g root /tmp/restart_app.sh /opt/mandala/restart_app.sh'
```

## 5. Nginx на той же ВМ (n8n + Docker)

Готовый пример vhost: **`scripts/deploy/nginx-mandala-api.conf.example`** — скопируйте на ВМ в каталог, смонтированный в **`n8n-nginx`** (часто **`/opt/n8n/nginx/conf.d/`**), под именем вроде **`mandala-api.conf`**, затем **`docker exec n8n-nginx nginx -t`** и **`nginx -s reload`**.

**Let's Encrypt** для нового имени (один раз), если у сервиса **certbot** в compose переопределён `entrypoint`:

```bash
cd /opt/n8n && docker compose run --rm --entrypoint '' certbot certbot certonly \
  --webroot -w /var/www/certbot -d api.mandala-app.online \
  --agree-tos --non-interactive --email admin@mandala-app.online
```

**БД и пользователь** в существующем кластере Managed PostgreSQL (пример **`yc`**):

```bash
yc managed-postgresql user create mandala_app --cluster-name n8n-postgres --password '<сгенерируйте>'
yc managed-postgresql database create mandala --cluster-name n8n-postgres --owner=mandala_app
```

Строка **`DATABASE_URL`**: хост вида **`<имя-хоста>.mdb.yandexcloud.net`**, пулер **`6432`**, **`?sslmode=require`** — см. [подключение к MDB](https://yandex.cloud/ru/docs/managed-postgresql/operations/connect).

Проверка: `curl -sS https://api.mandala-app.online/health`

## 6. Telegram webhook (HTTPS)

```bash
curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{
    \"url\": \"https://api.mandala-app.online/webhooks/telegram/${TELEGRAM_VERTICAL_ID}\",
    \"secret_token\": \"${TELEGRAM_WEBHOOK_SECRET}\"
  }"
```

**`vertical_id`** в URL должен совпадать с **`TELEGRAM_VERTICAL_ID`** (см. README корня репозитория).

## 7. DNS (Terraform)

Каталог **`terraform/`**: только **A-запись** на публичный IP ВМ. Инструкция — **`terraform/README.md`**.

## 8. systemd (опционально)

Вместо ручного `podman run` можно **Quadlet** или unit-файл с **`ExecStart=podman run ...`** и **`EnvironmentFile=/opt/mandala/env`**. Полный unit — по политике вашей ВМ (TODO при стабилизации).

## Бэкапы БД

[Резервные копии и PITR — Yandex Managed PostgreSQL](https://yandex.cloud/ru/docs/managed-postgresql/concepts/backup).
