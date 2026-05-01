# Terraform (DNS в Yandex Cloud)

Минимальный модуль: **одна A-запись** в существующей публичной DNS-зоне (например **`api`** → публичный IP ВМ с Nginx). Сеть, ВМ и Managed PostgreSQL **не** создаются здесь — фактическая схема в **`docs/deployment-yandex-cloud.md`**, архитектура — **`docs/architecture.md`**. Исторический поэтапный план — **`docs/implementation-plan.md`**.

## Подготовка

1. [Yandex Cloud CLI](https://yandex.cloud/ru/docs/cli/quickstart) — **`yc init`**, профиль с нужным **`folder-id`**.
2. [Terraform](https://developer.hashicorp.com/terraform/install) ≥ 1.5.
3. Скопировать **`terraform.tfvars.example`** → **`terraform.tfvars`**, подставить **`folder_id`**, **`dns_zone_id`**, **`vm_public_ip`** (например `yc compute instance list`, `yc dns zone list`).

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

**State** по умолчанию локальный (`terraform.tfstate` — в **`.gitignore`**). Удалённый backend в Object Storage — в **[docs/roadmap.md](../docs/roadmap.md)**.

## Бэкапы Managed PostgreSQL

Резервные копии и PITR: [документация Yandex Managed Service for PostgreSQL](https://yandex.cloud/ru/docs/managed-postgresql/concepts/backup).
