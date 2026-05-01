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
3. На ВМ: при необходимости миграции —  
   `docker run --rm --env-file /opt/mandala/env <образ> python -m alembic upgrade head`
4. Перезапуск:  
   `docker stop mandala-http && docker rm mandala-http`  
   затем снова **`docker run -d …`** с теми же флагами, что в **`scripts/deploy/README.md`** (**`HOST=0.0.0.0`**, **`-p 8000:8000`**, **`--env-file /opt/mandala/env`**).

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
