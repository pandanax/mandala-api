# Логи приложения в YC Logging (доставка с ВМ)

Как структурные `funnel …` логи Mandala попадают из stdout docker-контейнера на ВМ
в **YC Logging** (консоль → *Logging → группа → Логи*). Дашборд метрик и его ссылка на
логи — в [monitoring.md](monitoring.md); эмиссия логов в коде —
[`src/mandala/observability.py`](../src/mandala/observability.py).

## Зачем это нужно

Приложение пишет структурные логи в **stdout** контейнера (`op_format`, строки
`funnel …`). На текущем проде (**ВМ + docker**, см.
[deployment-yandex-cloud.md](deployment-yandex-cloud.md)) stdout docker-контейнера сам по
себе в YC Logging **не** попадает — нужна доставка. (У YC Serverless Container stdout шёл
бы автоматически, но прод сейчас — ВМ.) Доставку делает **Unified Agent** (рекомендуемый
YC путь) — аддитивно, не трогая контейнер бота, nginx, n8n и путь деплоя.

## Как устроено

```text
контейнер mandala-http (stdout: funnel …)
   │  docker logs -f --tail 0 mandala-http        ← systemd: mandala-logship.service
   ▼
/var/log/mandala/app.log   (сырые funnel-строки; logrotate copytruncate)
   │  file_input                                  ← systemd: mandala-unified-agent.service
   ▼
Unified Agent (cr.yandex/yc/unified-agent, config.yml → output yc_logs)
   │  gRPC + IAM-токен сервисного аккаунта ВМ (metadata service)
   ▼
YC Logging log-группа  (terraform: yandex_logging_group.mandala)
```

Все части — в [`scripts/deploy/unified-agent/`](../scripts/deploy/unified-agent/):

| Файл | Роль |
|---|---|
| `config.yml` | Конфиг Unified Agent: `file_input` (`/var/log/mandala/app.log`) → `yc_logs` (log-группа, IAM через metadata SA). |
| `mandala-logship.service` | systemd: `docker logs -f --tail 0 mandala-http >> /var/log/mandala/app.log`. |
| `mandala-unified-agent.service` | systemd: запуск контейнера Unified Agent с монтированием конфига/файла/буфера. |
| `logrotate-mandala` | Ротация `app.log` (`copytruncate`, чтобы не переоткрывать шиппер). |
| `install.sh` | Идемпотентный установщик всего перечисленного на ВМ. |

**Ключевые решения:**

- **Путь деплоя не тронут.** `restart_app.sh`/`deploy.sh` **пересоздают** контейнер
  `mandala-http` (id меняется), поэтому читать docker-json по пути нельзя. Шиппер
  цепляется по **имени** контейнера через `docker logs -f`; при пересоздании команда
  завершается, `systemd` (`Restart=always`) поднимает её снова и она переподключается к
  новому контейнеру. Так критический путь деплоя остаётся без изменений.
- **Только Mandala.** Шиппер читает stdout ровно контейнера `mandala-http` — логи n8n и
  прочих контейнеров ВМ в группу **не** попадают.
- **`docker logs` продолжает работать** (json-file драйвер контейнера не менялся) — для
  быстрого локального дебага.
- **Строки читаются как есть.** `docker logs` отдаёт уже распакованный текст, поэтому в
  YC Logging тело записи = сама строка `funnel …` (JSON-обёртку docker парсить не нужно).
- **Аутентификация — сервисный аккаунт ВМ** через metadata service (тот же путь, что у
  метрик в [`metrics.py`](../src/mandala/metrics.py)); SA нужна роль **`logging.writer`**.

## Как поднять

### 1. Создать log-группу (Terraform, аддитивно)

Ресурс `yandex_logging_group.mandala` — [`terraform/logging.tf`](../terraform/logging.tf),
недеструктивен (VM/БД/DNS/дашборд не трогает). Инкрементальный apply при уже накатанном
состоянии = **`Plan: 1 to add`**.

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # заполнить folder_id, dns_*, vm_public_ip
export YC_TOKEN=$(yc iam create-token)
terraform init
terraform apply -target=yandex_logging_group.mandala   # только группа
terraform output logging_group_id                      # ← понадобится ниже
```

Переменные группы (`terraform/variables.tf`): `log_group_name` (по умолчанию
`mandala-logs`), `log_group_retention_period` (по умолчанию `72h`).

> Чтобы шапка дашборда (monitoring.tf) давала прямую ссылку на группу — положи её id в
> `terraform.tfvars` как `log_group_id` и `terraform apply` (иначе ссылка ведёт на список
> групп каталога).

### 2. Выдать сервисному аккаунту ВМ роль `logging.writer`

К ВМ уже привязан сервисный аккаунт (используется для метрик). Добавь ему роль записи
логов:

```bash
SA_ID=$(yc compute instance get --name n8n-server --format json | jq -r .service_account_id)
yc resource-manager folder add-access-binding <folder> \
  --role logging.writer --subject serviceAccount:${SA_ID}
```

Если SA к ВМ не привязан — привязать (как для метрик, см. monitoring.md §«Включить
эмиссию метрик»).

### 3. Установить доставку на ВМ

Скопировать каталог и запустить установщик с id группы:

```bash
scp -r scripts/deploy/unified-agent ubuntu@api.mandala-app.online:/tmp/
ssh ubuntu@api.mandala-app.online \
  'sudo LOG_GROUP_ID=<terraform output logging_group_id> bash /tmp/unified-agent/install.sh'
```

`install.sh` идемпотентен: раскладывает `config.yml` в `/etc/mandala/unified-agent/`,
пишет `LOG_GROUP_ID` в `/etc/mandala/unified-agent.env` (права 600), ставит logrotate и
две systemd-службы, тянет образ агента и запускает всё. Повторный запуск безопасен.

## Смотреть логи

- **Консоль:** YC Logging → нужная группа → **Логи**. Прямая ссылка также в шапке
  дашборда (если `log_group_id` задан в tfvars).
- **CLI:** `yc logging read --group-id <id> --follow`.
- **Локально на ВМ (дебаг):** `docker logs mandala-http --tail 200 -f` (json-file драйвер
  не менялся) или `tail -f /var/log/mandala/app.log` (то, что реально уходит в агент).

Формат строк — `funnel <stage> vertical_id=… stage=… outcome=… …` (собирает `op_format`;
на INFO нет текста переписки, промптов, токенов). Полезные фильтры по подстроке:
`funnel webhook`, `funnel outbound`, `stage=received`.

## Проверка и диагностика

```bash
# службы живы
systemctl status mandala-logship mandala-unified-agent --no-pager

# что реально шлётся в агент
tail -n 20 /var/log/mandala/app.log

# ошибки старта/доставки самого агента
docker logs mandala-unified-agent --tail 50
```

Типовые причины пустой группы:

- **Нет роли `logging.writer`** у SA ВМ → в логах агента ошибки `PermissionDenied`.
- **Неверный `LOG_GROUP_ID`** в `/etc/mandala/unified-agent.env` → `NotFound`.
- **`mandala-logship` не запущен / контейнер `mandala-http` отсутствует** →
  `/var/log/mandala/app.log` не растёт (шиппер ретраит, пока контейнера нет).
- **Нет трафика** — приложение просто ещё ничего не залогировало (напиши боту `/start`).

## Границы

- Аддитивно и недеструктивно: не меняет контейнер `mandala-http`, nginx, n8n и путь
  деплоя (`restart_app.sh`/`deploy.sh`).
- Это **публичный** Yandex Cloud (`logging-ingester.api.cloud.yandex.net`), не внутренний
  Яндекс.
- `terraform validate` подтверждает корректность ресурса группы; `apply` (создание
  группы) и установка агента на ВМ — outward-шаги (см. выше).
