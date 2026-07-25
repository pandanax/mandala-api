# Terraform (Yandex Cloud)

Аддитивный модуль, **не** трогает VM, Managed PostgreSQL и сеть:

- **DNS** (`main.tf`) — **одна A-запись** в существующей публичной зоне (например **`api`** → публичный IP ВМ с Nginx).
- **Дашборд наблюдаемости** (`monitoring.tf`) — `yandex_monitoring_dashboard` с виджетами Telegram / LLM / приложение + ссылка на логи. Метрики эмитит приложение (`src/mandala/metrics.py`); как включить и что собирается — **[docs/monitoring.md](../docs/monitoring.md)**.
- **Log-группа YC Logging** (`logging.tf`) — `yandex_logging_group` для структурных логов приложения; доставка stdout контейнера в группу (Unified Agent на ВМ) — **[docs/logging.md](../docs/logging.md)**. Аддитивно (`Plan: 1 to add`), id группы — `terraform output logging_group_id`.

Фактическая схема деплоя — **`docs/deployment-yandex-cloud.md`**, архитектура — **`docs/architecture.md`**. Исторический поэтапный план — **`docs/implementation-plan.md`**.

## Подготовка

1. [Yandex Cloud CLI](https://yandex.cloud/ru/docs/cli/quickstart) — **`yc init`**, профиль с нужным **`folder-id`**.
2. [Terraform](https://developer.hashicorp.com/terraform/install) ≥ 1.5.
3. Скопировать **`terraform.tfvars.example`** → **`terraform.tfvars`**, подставить **`folder_id`**, **`dns_zone_id`**, **`vm_public_ip`** (например `yc compute instance list`, `yc dns zone list`) и опционально **`log_group_id`** (`yc logging group list`) для ссылки на логи в дашборде.

## Команды

Перед **`terraform plan`** / **`apply`** с локальной машины задайте краткоживущий токен (или сервисный ключ):

```bash
export YC_TOKEN=$(yc iam create-token)
```

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

Только дашборд, не трогая DNS: `terraform apply -target=yandex_monitoring_dashboard.mandala`.
Ссылка на дашборд после apply — `terraform output dashboard_url`.

**State** по умолчанию локальный (`terraform.tfstate` — в **`.gitignore`**). Удалённый backend в Object Storage — в **[docs/roadmap.md](../docs/roadmap.md)**.

## Бэкапы Managed PostgreSQL

Резервные копии и PITR: [документация Yandex Managed Service for PostgreSQL](https://yandex.cloud/ru/docs/managed-postgresql/concepts/backup).
