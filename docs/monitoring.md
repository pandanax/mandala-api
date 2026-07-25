# Наблюдаемость: дашборд метрик и логи (Yandex Cloud)

Дашборд метрик и доступ к логам для MVP на **публичном Yandex Cloud** (ВМ + Nginx,
контейнер на порту 8000). Показывает работу трёх подсистем — **Telegram**, **LLM** и
**приложения** — плюс ссылку на логи.

- **Дашборд-как-код:** [`terraform/monitoring.tf`](../terraform/monitoring.tf) —
  ресурс `yandex_monitoring_dashboard`.
- **Эмиссия метрик:** [`src/mandala/metrics.py`](../src/mandala/metrics.py) —
  лёгкая инструментация на границах подсистем.
- **Логи:** структурные `funnel …` строки из stdout
  ([`src/mandala/observability.py`](../src/mandala/observability.py)).

## Что собирается

Приложение эмитит **custom-метрики** в YC Monitoring (`service=custom`). Имена метрик —
единый источник правды в `metrics.py`; запросы дашборда в `monitoring.tf` их повторяют.

| Метрика | Тип | Метки | Подсистема / смысл |
|---|---|---|---|
| `mandala.http.requests` | COUNTER | `route`, `method`, `status` | Приложение: RPS, ошибки (4xx/5xx), health; **webhook Telegram** — по `route=/webhooks/telegram*` |
| `mandala.http.latency_ms` | DGAUGE | `route`, `stat=avg\|max` | Латентность ответа приложения и webhook (за окно отправки) |
| `mandala.llm.requests` | COUNTER | `outcome=ok\|error\|timeout` | LLM: количество запросов, ошибки, таймауты |
| `mandala.llm.latency_ms` | DGAUGE | `stat=avg\|max` | LLM: латентность запроса |
| `mandala.telegram.delivery` | COUNTER | `method`, `outcome=ok\|error` | Telegram: доставка через Bot API (sendMessage/sendPhoto/…) |
| `mandala.app.up` | IGAUGE | — | Liveness-heartbeat: `1` в каждом цикле отправки (приложение живо) |

**Точки инструментации** (обёртки на границах, без переписывания логики):

- **LLM** — `src/mandala/llm/openai_compatible.py` (`OpenAICompatibleTextClient.complete`):
  тайминг + классификация исхода `ok/error/timeout`.
- **Telegram-доставка** — `src/mandala/adapters/telegram/bot_api.py`
  (`TelegramBotApiClient.call`): счётчик по методу и исходу для всех вызовов Bot API.
- **Приложение и webhook** — HTTP-мидлварь в `src/mandala/http/app.py`: запрос,
  статус и латентность по нормализованному роуту (покрывает `/health`,
  `/webhooks/telegram/{vertical_id}`, `/webhooks/web`).

Счётчики — монотонные (кумулятивные); per-second на дашборде считается запросом
`non_negative_derivative(...)` — **в YC Monitoring нет функции `rate()`** (её
использование даёт «Ошибка построения графика»). Латентность — оконное avg/max,
сбрасывается на каждой отправке; берётся селектором как есть, без derivative
(«Нет данных» на этих виджетах = просто отсутствие трафика за окно, запрос валиден).
Метрики агрегируются в процессе и раз в интервал уходят фоновым потоком в write API.
Ошибка отправки логируется на DEBUG и **не** ломает приложение.

### Синтаксис query дашборда (язык YC Monitoring, не PromQL)

Запросы виджетов в `monitoring.tf` используют язык YC Monitoring:

- селектор — `"<имя.метрики>"{service="custom", label="value"}`: имя метрики **перед**
  фигурными скобками и в **двойных** кавычках, значения меток тоже в двойных
  (формы `{name='...'}` с одинарными кавычками не строятся);
- per-second из COUNTER — обёртка `non_negative_derivative(<селектор>)`
  (клампит отрицательные скачки при рестарте/сбросе счётчика в `NaN`);
- glob в значениях меток — `status="4*"`, `route="/webhooks/telegram*"`;
- DGAUGE-латентность (`stat=avg|max`) берётся селектором напрямую, без derivative.

Требуют **реального трафика**, чтобы появились данные (иначе «Нет данных», но без
ошибки): все латентности (`*.latency_ms`), RPS/ошибки HTTP и LLM, доставка Bot API,
webhook-апдейты. `mandala.app.up` появляется в каждом цикле отправки (heartbeat),
пока метрики включены.

## Включить эмиссию метрик (на ВМ)

По умолчанию метрики **выключены** (полный no-op: ни потока, ни трафика). Включаются
переменными окружения в `/opt/mandala/env` (права 600, в git не коммитится):

```bash
# /opt/mandala/env
MANDALA_METRICS_ENABLED=1
YC_MONITORING_FOLDER_ID=b1gmrr5e6bncvoin732o   # yc config get folder-id
# MANDALA_METRICS_PUSH_INTERVAL=30             # секунды, минимум 5 (по умолчанию 30)
```

Токен для записи метрик берётся автоматически из **сервисного аккаунта ВМ** через
metadata service. Требования:

1. К ВМ **привязан сервисный аккаунт** с ролью **`monitoring.editor`** (запись
   custom-метрик). Проверить/выдать:
   ```bash
   yc compute instance get --name <VM> --format json | jq .service_account_id
   yc resource-manager folder add-access-binding <folder> \
     --role monitoring.editor --subject serviceAccount:<sa-id>
   ```
2. Если SA к ВМ не привязан — можно задать краткоживущий токен явно:
   `YC_IAM_TOKEN=$(yc iam create-token)` (истекает ~12 ч, для постоянной работы
   предпочтителен SA на ВМ).

После правки `/opt/mandala/env` контейнер нужно **пересоздать** (не `docker restart`):

```bash
sudo bash /opt/mandala/restart_app.sh
```

(см. [deployment-yandex-cloud.md](deployment-yandex-cloud.md) §11.2 и
[`scripts/deploy/restart_app.sh`](../scripts/deploy/restart_app.sh)).

### Переменные окружения

| Переменная | Обяз. | По умолчанию | Назначение |
|---|---|---|---|
| `MANDALA_METRICS_ENABLED` | да | `` (выкл.) | `1/true/yes/on` — включить эмиссию |
| `YC_MONITORING_FOLDER_ID` | да | `YC_FOLDER_ID` | Каталог для write API (`service=custom`) |
| `MANDALA_METRICS_PUSH_INTERVAL` | нет | `30` | Период отправки, сек (мин. 5) |
| `YC_IAM_TOKEN` | нет | — | Явный IAM-токен вместо metadata SA |
| `MANDALA_METRICS_ENDPOINT` | нет | YC v2 write | Переопределение endpoint (тесты) |
| `HOSTNAME` | нет | — | Пишется в общую метку `host` |

## Поднять дашборд (`terraform apply`)

Дашборд аддитивен: **не трогает** VM, Managed PostgreSQL и DNS (`Plan: 1 to add`).
Из каталога [`terraform/`](../terraform/) (подготовка — [terraform/README.md](../terraform/README.md)):

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars   # заполнить folder_id, dns_*, vm_public_ip, log_group_id
export YC_TOKEN=$(yc iam create-token)
terraform init
terraform plan     # ожидается создание yandex_monitoring_dashboard.mandala
terraform apply
```

Переменные дашборда (`terraform/variables.tf`):

- `folder_id` — каталог YC (общий с DNS-записью);
- `dashboard_name` — имя дашборда (по умолчанию `mandala-observability`);
- `log_group_id` — ID log-группы YC Logging для ссылки на логи в шапке дашборда
  (`yc logging group list`; пусто → ссылка на список групп).

После apply ссылка на дашборд — в выводе `terraform output dashboard_url`.

> Только дашборд, не трогая DNS:
> `terraform apply -target=yandex_monitoring_dashboard.mandala`.

## Смотреть логи

Приложение пишет структурные логи в **stdout** контейнера. Варианты доступа:

- **Локально на ВМ:** `docker logs mandala-http --tail 200 -f` (быстрый дебаг).
- **YC Logging** (если stdout контейнера пишется в log-группу): консоль →
  *Logging → группа → Логи*. Прямая ссылка — в шапке дашборда (виджет `text`,
  строится из `folder_id` + `log_group_id`).

Формат строк — `funnel <stage> vertical_id=… stage=… outcome=… …` (поля собирает
`op_format`; на INFO нет текста переписки, промптов, токенов). Полезные фильтры YC
Logging по подстроке: `funnel webhook`, `funnel outbound`, `stage=received`.

## Проверка и границы

- `scripts/check.sh` включает юнит-тесты инструментации
  ([`tests/test_metrics.py`](../tests/test_metrics.py)): реестр, конфиг, payload,
  no-op при выключенных метриках, обёртки LLM/Telegram/HTTP.
- `terraform validate` и `terraform plan` подтверждают корректность дашборда
  (провайдер `yandex-cloud/yandex`).
- Это **публичный** Yandex Cloud (`monitoring.api.cloud.yandex.net`), не внутренний
  Яндекс-мониторинг.
