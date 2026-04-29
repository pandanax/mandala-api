# Биллинг

## Принцип

Бизнес-логика не зависит от конкретного способа оплаты. Все провайдеры реализуют общий контракт; активация тарифа происходит в **одном месте** после подтверждённого платежа.

## Интерфейс BillingProvider

Реализовано в коде (тикет 18): Protocol **`mandala.services.billing.BillingProvider`** с методом **`activate_plan`** — идемпотентная запись в **`payment_transactions`** (уникальность **`(provider, external_id)`**) и **`UPDATE users.current_plan_id`**, `subscription_period_start` при новой вставке. Реализация по умолчанию: **`PostgresBillingProvider`** на **`sqlalchemy.engine.Connection`** (транзакция — ответственность вызывающего кода).

**Тикет 19 (Telegram Stars):** в **`mandala.services.telegram_stars`** — **`handle_pre_checkout_query`**, **`handle_successful_payment`**; маршрут в адаптере: **`process_telegram_billing_update`** (long polling и **``POST /webhooks/telegram/{vertical_id}``**; для оплаты в webhook нужен тот же `TELEGRAM_BOT_TOKEN` для `vertical_id`). Семантика после **первой** успешной оплаты: **`apply_plan_change`**.

- `create_payment_offer` (обёртка `sendInvoice` / UI «купить») — **TODO: тикет 21+**
- `refund`, `sync_subscription` (Stripe) — **TODO: за пределами MVP**

## Telegram Stars (первая реализация)

- Обработка **`pre_checkout_query`** — `answerPreCheckoutQuery` после проверки payload; обработка **`message.successful_payment`** — `activate_plan` + `apply_plan_change`.
- Связь: для платного плана (seed, ``premium``) в БД миграцией заданы **`billing_provider` = `telegram_stars`**, **`external_product_id` = `mandala_premium_stars`**; тот же строковый `invoice_payload` в **`sendInvoice`** / ссылке на товар.

Требования:

- **Идемпотентность**: повторный webhook с тем же платежом не продлевает подписку второй раз.
- **Логи без лишних PII** (тикет 20: **`funnel billing`**, **`activate_plan`** / **`apply_plan_change`** с **`reason`** и **`outcome`**, без сырого **`raw_payload`** на INFO); сырые payload в БД — ограниченный доступ.

## Другие провайдеры (задел)

| Провайдер | Особенности |
|-----------|-------------|
| Stripe | Подписки, webhooks, customer portal |
| ЮKassa | РФ, разные сценарии оплаты |

Общая таблица транзакций (`payment_transactions`) с полем `provider` и уникальностью по `(provider, external_id)`.

## Связь с планами

После успешной оплаты:

1. Запись в `payment_transactions` со статусом `completed` (и **`activate_plan`** + **`apply_plan_change`** для Stars).
2. Обновление `users.current_plan_id` и `subscription_period_start` / `subscription_period_end` (тикет 19).
3. **Usage:** при **Telegram Stars** — сброс за текущий календарный месяц в `apply_plan_change` (см. [quotas-and-plans.md](quotas-and-plans.md)).
