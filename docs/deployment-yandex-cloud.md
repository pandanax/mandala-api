# Деплой Mandala в Yandex Cloud (фактическая схема MVP)

Документ фиксирует **текущее** состояние интеграции с Yandex Cloud: что где лежит, как устроено, как обновлять. Общая архитектура — в [architecture.md](architecture.md); установка, env, первый запуск и чеклист после инсталляции — в [getting-started.md](getting-started.md).

Секреты (токены, пароли БД) **не** описываются здесь в открытом виде — только **имена переменных** и **файлы**, куда их класть.

---

## 1. Облако и каталог

| Сущность | Как посмотреть | Заметки |
|----------|----------------|---------|
| **Облако** | `yc config get cloud-id` | Один облако-профиль в `yc`. |
| **Каталог (folder)** | `yc config get folder-id` | Для текущего MVP используется каталог **`n8n`** (имя в консоли; id — из `yc`). |
| **Зона** | `yc config get compute-default-zone` | Часто **`ru-central1-b`**. |

Все команды ниже предполагают **`yc init`** и выбранный нужный каталог (`yc config set folder-id …`).

---

## 2. Ресурсы в каталоге (карта)

### 2.1. Виртуальная машина (Compute)

- **Имя:** `n8n-server` (как в Terraform/истории n8n).
- **Назначение:** хост для **Docker Compose** (n8n + **Nginx** + **certbot**) и отдельного контейнера **`mandala-http`** (образ Mandala).
- **Сеть:** подсеть **`n8n-subnet`** (частный диапазон **`10.128.0.0/24`**), публичный **one-to-one NAT** (внешний IPv4 у ВМ — смотри `yc compute instance get --name n8n-server`).
- **SSH:** пользователь **`ubuntu`** (как принято в образе), ключ с машины разработчика:
  `ssh ubuntu@<публичный-IP>`
- **Security group на ВМ:** вход **22, 80, 443** (приложение Mandala **не** слушает публично **8000** — только через Nginx на **443**).

Проверка: `yc compute instance list`, `yc compute instance get --name n8n-server`.

### 2.2. Managed PostgreSQL

- **Кластер:** `n8n-postgres`, PostgreSQL **15**, сеть та же, что у ВМ (доступ из подсети к кластеру настраивает **security group кластера** — отдельно от SG ВМ).
- **Хост подключения:** выдаётся в **`yc managed-postgresql hosts list --cluster-id <id>`** — FQDN вида **`<имя>.mdb.yandexcloud.net`**.
- **Пуулер (рекомендуется для приложений):** порт **6432**; прямое подключение к PostgreSQL — **5432** (см. [документацию YC](https://yandex.cloud/ru/docs/managed-postgresql/operations/connect)).
- **SSL:** в строке подключения обязательно **`sslmode=require`** (или эквивалент в драйвере).

**Отдельно под Mandala (не трогая БД n8n):**

- Пользователь БД: **`mandala_app`**.
- База данных: **`mandala`** (владелец — **`mandala_app`**).
- Создание через CLI:
  `yc managed-postgresql user create …`,
  `yc managed-postgresql database create mandala --owner mandala_app …`
  (точные флаги — `yc managed-postgresql user create --help`).

Строка **`DATABASE_URL`** для приложения (формат как в коде — `postgresql://…`, движок сам переводит в `postgresql+psycopg://`):
пользователь, пароль, хост, порт **6432**, имя БД **`mandala`**, query **`sslmode=require`**.

### 2.3. DNS

- Публичная зона **`mandala-app.online`** (id зоны — `yc dns zone list`).
- Запись **`api`** (A на публичный IP ВМ) создаётся **Terraform** из репозитория Mandala (`terraform/`, ресурс **`yandex_dns_recordset`**).
  Локально перед `apply`: **`export YC_TOKEN=$(yc iam create-token)`** (краткоживущий токен).

### 2.4. TLS

- Сертификаты Let’s Encrypt лежат в **Docker volume** **`certbot_conf`**, внутри контейнера Nginx монтируются в **`/etc/letsencrypt/live/<имя>/`**.
- Для **`api.mandala-app.online`** сертификат выпускается **certbot** в режиме **webroot** (каталог **`/var/www/certbot`** в том же compose, что n8n).

---

## 3. Архитектура трафика (MVP)

```text
Интернет
   → DNS api.mandala-app.online (A → публичный IP ВМ)
   → :443 на ВМ (security group)
   → контейнер n8n-nginx (Docker)
         → reverse_proxy http://<gateway-docker-bridge>:8000
   → хост или контейнер mandala-http (FastAPI / mandala.http)
         → TLS к Managed PostgreSQL (DATABASE_URL, порт 6432, sslmode=require)
```

**Gateway Docker → Linux-хост:** у контейнера **`n8n-nginx`** смотреть поле **Gateway** в `docker inspect` (в одном из развёртываний это было **`172.18.0.1`**). В конфиге Nginx для Mandala **`proxy_pass`** должен указывать на **хост:порт**, где реально слушает **uvicorn** (контейнер **`mandala-http`** с **`-p 8000:8000`**).

Готовый пример фрагмента vhost: **`scripts/deploy/nginx-mandala-api.conf.example`** в репозитории Mandala.

---

## 4. Что лежит на ВМ (пути)

| Путь / объект | Назначение |
|---------------|------------|
| **`/opt/n8n/`** | Проект n8n: **`docker-compose.yml`**, каталог **`nginx/`** (конфиги, в т.ч. **`conf.d/mandala-api.conf`**). |
| **`/opt/mandala/env`** | Файл с переменными окружения для **Mandala** (**`DATABASE_URL`**, **`LLM_*`**, **`TELEGRAM_*`**, …). Права **600**, владелец процесса/пользователь, от которого запускается `docker`. |
| **Docker: `mandala-http`** | Контейнер приложения Mandala; образ собирается из **`Containerfile`** в репозитории (см. **`scripts/deploy/README.md`**). |
| **Docker: `n8n-nginx`**, **`n8n-certbot`**, … | Стек n8n; **не** переименовывать и не ломать при обновлении Mandala. |

---

## 5. Как деплоится Mandala (два контура)

Типовой контур (инфра редко / образ и сервис чаще):

1. **Инфра редко** — Terraform в **`terraform/`** (сейчас по сути только DNS; state локально, в git не коммитить **`terraform.tfstate`**, **`terraform.tfvars`**).
2. **Образ / сервис часто** — сборка образа (**`podman`/`docker` build** с **`MANDALA_PLATFORM=linux/amd64`** для типичной ВМ YC), передача на ВМ (**`docker load`** или registry), **`docker restart mandala-http`** или пересоздание контейнера, при смене схемы — **`docker run … python -m alembic upgrade head`** (см. **`scripts/deploy/README.md`**).

**CI (GitHub Actions)** в этот репозиторий **не** выкладывает в YC — только проверки кода.

---

## 6. Как «стучаться» до БД

- **С ВМ** (или из контейнера с тем же доступом в VPC): **`psql`** / **`python scripts/check_postgres.py`** с **`DATABASE_URL`** на хост кластера **`.mdb.yandexcloud.net`**, порт **6432**, SSL.
- **С домашнего ноутбука:** только если в **security group кластера** разрешён вход с вашего IP (обычно для прод **не** открывают); удобнее **SSH-туннель** или работа через ВМ.
- **Пароль пользователя БД:** хранится только у вас (Lockbox / файл на ВМ / **не** в git).

Бэкапы кластера: [концепция бэкапов Managed PostgreSQL](https://yandex.cloud/ru/docs/managed-postgresql/concepts/backup).

---

## 7. Обновление ВМ, Docker, Mandala

### 7.1. Обновить только Mandala (часто)

1. Собрать образ локально: **`bash scripts/deploy/build_image.sh`** (при необходимости **`MANDALA_IMAGE`**, **`MANDALA_PLATFORM`**).
2. **`podman save … | ssh ubuntu@<IP> docker load`** (или свой registry + **`docker pull`**).
3. На ВМ: рестарт скриптом, при необходимости с миграциями —
   `sudo RUN_MIGRATIONS=1 bash /opt/mandala/restart_app.sh`
   (скрипт — **[scripts/deploy/restart_app.sh](../scripts/deploy/restart_app.sh)**, см. также §11 ниже).

⚠️ **Не использовать `docker restart mandala-http`** — он **не** перечитывает `--env-file`. После правки `/opt/mandala/env` контейнер обязательно пересоздавать (`stop` + `rm` + `run`). Скрипт делает это сам.

### 7.2. Обновить Nginx / TLS

- Правки только в **`/opt/n8n/nginx/conf.d/`** на ВМ, затем:
  `docker exec n8n-nginx nginx -t && docker exec n8n-nginx nginx -s reload`
- Новый домен — отдельный **`certbot certonly`** (как в **`scripts/deploy/README.md`**, с **`--entrypoint ''`** если в compose переопределён entrypoint).

### 7.3. Обновить n8n / всю ВМ

- Вне зоны репозитория Mandala; следуйте документации проекта n8n и снимайте бэкапы перед крупными изменениями.

---

## 8. Terraform из репозитория Mandala

- Каталог **`terraform/`**: **`main.tf`**, **`variables.tf`**, **`outputs.tf`**, **`versions.tf`**.
- **`terraform.tfvars.example`** — образец; реальный **`terraform.tfvars`** — только локально (**`.gitignore`**).
- Провайдер: **`yandex-cloud/yandex`**, версия зафиксирована в **`.terraform.lock.hcl`** (его **нужно** коммитить в git).
- **Remote state в Object Storage** — в планах развития ([roadmap.md](roadmap.md)).

---

## 9. Полезные команды `yc` (шпаргалка)

```bash
yc config list
yc resource-manager folder list --cloud-id "$(yc config get cloud-id)"
yc compute instance list
yc compute instance get --name n8n-server
yc managed-postgresql cluster list
yc managed-postgresql hosts list --cluster-id <id>
yc managed-postgresql database list --cluster-id <id>
yc managed-postgresql user list --cluster-id <id>
yc dns zone list
```

---

## 10. Ссылки на Yandex Cloud

- [Консоль](https://console.cloud.yandex.ru)
- [CLI](https://yandex.cloud/ru/docs/cli/quickstart)
- [Подключение к Managed PostgreSQL](https://yandex.cloud/ru/docs/managed-postgresql/operations/connect)
- [Бэкапы Managed PostgreSQL](https://yandex.cloud/ru/docs/managed-postgresql/concepts/backup)

---

## 11. SSH и рестарт приложения

### 11.1. Подключение по SSH

Самый простой путь — через DNS, который уже подведён Terraform’ом к публичному IP ВМ:

```bash
ssh ubuntu@api.mandala-app.online
```

Альтернативы:

```bash
# через явный IP, полученный из YC CLI
ssh ubuntu@$(yc compute instance get --name n8n-server --format json \
  | jq -r '.network_interfaces[0].primary_v4_address.one_to_one_nat.address')

# или вручную
yc compute instance list   # столбец EXTERNAL IP
ssh ubuntu@<IP-из-вывода>

# с явным ключом
ssh -i ~/.ssh/<твой-ключ> ubuntu@api.mandala-app.online
```

При первом подключении к новому хосту — `-o StrictHostKeyChecking=accept-new`.
SSH-проброс порта (если хочешь дернуть `:8000` локально, минуя Nginx):

```bash
ssh -L 8000:127.0.0.1:8000 ubuntu@api.mandala-app.online
```

### 11.2. Правка `/opt/mandala/env`

Файл с переменными окружения (см. **[getting-started.md §5](getting-started.md)** и **`.env.example`**) лежит на ВМ:

```bash
sudo nano /opt/mandala/env       # права 600, в git не коммитится
```

Минимальный набор для **MVP**: `DATABASE_URL`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_VERTICAL_ID`, `TELEGRAM_WEBHOOK_SECRET`. Полный список — в **[`.env.example`](../.env.example)**.

### 11.3. Рестарт после правки env

⚠️ **`docker restart mandala-http` НЕ перечитывает `--env-file`**.
После любого изменения `/opt/mandala/env` контейнер нужно **пересоздать**:

```bash
# на ВМ — одной командой:
sudo bash /opt/mandala/restart_app.sh

# с миграциями (если деплоится новая схема БД):
sudo RUN_MIGRATIONS=1 bash /opt/mandala/restart_app.sh
```

Скрипт делает: `docker stop` + `docker rm` + `docker run -d … --env-file /opt/mandala/env -p 8000:8000 …` и ждёт `/health`. Источник — **[scripts/deploy/restart_app.sh](../scripts/deploy/restart_app.sh)**. Если правишь скрипт в репо — обнови и копию на ВМ:

```bash
scp scripts/deploy/restart_app.sh ubuntu@api.mandala-app.online:/tmp/
ssh ubuntu@api.mandala-app.online \
  'sudo install -m 0755 -o root -g root /tmp/restart_app.sh /opt/mandala/restart_app.sh'
```

### 11.4. Полезные оперативные команды

```bash
# логи приложения
sudo docker logs mandala-http --tail 100 -f

# health
curl -sS https://api.mandala-app.online/health

# что реально передано в процесс (имена переменных без значений)
sudo docker exec mandala-http sh -c 'tr "\0" "\n" < /proc/1/environ' \
  | sed -E 's/=.*/=***/'

# логи nginx по нужному пути
sudo docker logs n8n-nginx --tail 200 | grep webhooks/telegram
```

---

## 12. Первый запуск MVP в проде (чеклист)

После того как ВМ, Managed PostgreSQL, DNS, Nginx, TLS и контейнер `mandala-http` уже подняты (см. §1–§5 и **[scripts/deploy/README.md](../scripts/deploy/README.md)**), для запуска бота нужно только:

1. **`ssh ubuntu@api.mandala-app.online`** (см. §11.1).
2. Прописать в **`/opt/mandala/env`** переменные:
   - **Telegram:** `TELEGRAM_BOT_TOKEN` (от `@BotFather`), `TELEGRAM_VERTICAL_ID` (slug из таблицы `agent_verticals`, по умолчанию `astrology` или `therapy`), `TELEGRAM_WEBHOOK_SECRET` (`openssl rand -hex 32`).
   - **LLM:** `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` (для DeepSeek: `https://api.deepseek.com/v1`, ключ, `deepseek-chat`).
3. **Пересоздать контейнер:** `sudo bash /opt/mandala/restart_app.sh` (см. §11.3).
4. **Health-check:** `curl -sS https://api.mandala-app.online/health` → `{"status":"ok","database":"ok"}`.
5. **Зарегистрировать webhook** (с любой машины):

   ```bash
   curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
     -H "Content-Type: application/json" \
     -d "{
       \"url\": \"https://api.mandala-app.online/webhooks/telegram/${TELEGRAM_VERTICAL_ID}\",
       \"secret_token\": \"${TELEGRAM_WEBHOOK_SECRET}\",
       \"drop_pending_updates\": true
     }"
   ```

   Ожидаемо: `{"ok":true,"result":true,"description":"Webhook was set"}`.
6. **Smoke-test:** в Telegram открыть бота и написать любое сообщение (или `/start`) — должна пойти анкета вертикали (см. **[agent.md](agent.md)** про intake).

Типичные причины отказов и как смотреть — §11.4 и **[quotas-and-plans.md](quotas-and-plans.md)** (квота `text_reply` на free-плане).
