# Биллинг

> **Модель монетизации — предоплаченный кошелёк сообщений, БЕЗ подписки и месячных лимитов.**
> Авторитетный код: `services/message_packs.py`, `services/telegram_stars.py`,
> `services/billing.py`, `services/quota.py`, `repositories/wallet.py`.

## Принцип

У каждого пользователя есть простой целочисленный **баланс сообщений**. Каждый **LLM-ответ
текстом списывает 1**; мгновенные детерминированные рендеры (`/natal`, `/matrix`,
`/numerology`, `/profile`, `/help`) **не списывают** ничего. Баланс **не привязан к времени**,
**никогда не сгорает по времени** и **переживает `/reset`**.

- Новый пользователь получает разовый стартовый грант `MANDALA_MESSAGE_WALLET_START`
  (по умолчанию **20**).
- На балансе 0 бот показывает **три кнопки-пакета**; покупка пакета **добавляет** сообщения
  (навсегда, не сгорают).
- **Промо = вечный безлимит** — обходит любые списания и тоже переживает `/reset`.
- **Картинки не тарифицируются из кошелька** (решение капитана): `image_generation`
  разрешён только под промо, иначе отказ, кошелёк не трогается.

## Баланс: `users.message_balance`

Баланс живёт на колонке `users.message_balance` (миграция `t20_01_message_wallet`), ключ —
`(user, vertical)`: одна строка `users` на вертикаль, поэтому балансы разных ботов
изолированы. Репозиторий — `repositories/wallet.py` (`WalletRepository`).

- **Атомарное списание** — без гонок и без ухода в минус (двойное условие внутри `UPDATE`):
  ```sql
  UPDATE … SET message_balance = message_balance - 1
  WHERE … AND message_balance >= 1 RETURNING …
  ```
- Стартовый грант выдаётся в `user_identity.get_or_create_user` (server_default колонки = 0).
- `/reset` **не трогает** `users` — баланс сохраняется. `ProfileRepository.reset_session`
  дополнительно сохраняет единственный ключ `activated_promo` в `agent_card`, вычищая всё
  остальное (см. [quotas-and-plans.md](quotas-and-plans.md)).

## Квота = кошелёк (`services/quota.py`)

`can_consume`/`consume` учитывают ресурс: `text_reply` читает/декрементит кошелёк; **промо
обходит всё (безлимит)**; **картинки не списываются** (нейтральный путь). Точка списания —
после успешного ответа, в той же транзакции.

## Три пакета — единый источник истины (`services/message_packs.py`)

Связка `payload ↔ ⭐-цена ↔ +сообщения`:

| pack_id | payload            | цена  | +сообщений |
|---------|--------------------|-------|------------|
| 100     | `mandala_pack_100` | 1 ⭐  | 100        |
| 300     | `mandala_pack_300` | 2 ⭐  | 300        |
| 1000    | `mandala_pack_1000`| 5 ⭐  | 1000       |

Цены и гранты параметризуются env (`MANDALA_PACK_{100,300,1000}_{PRICE,MESSAGES}`). Числовой
`pack_id`/суффикс payload — **стабильный** ключ продукта в журнале покупок: не менять при
правке цены/гранта. Хелперы: `pack_by_id` / `pack_by_payload` / `all_packs`.

## Инвойсы и пикер (`services/telegram_stars.py`)

- `build_pack_invoice_message(pack_id)` → `OutboundMessage.invoice` (`StarsInvoice`, валюта
  `XTR`, пустой `provider_token`, терминальное сообщение).
- `build_packs_picker_message(text=…, balance=…, unlimited=…)` → одно сообщение с тремя
  кнопками пакетов (`mdl:pack:<id>`). Если явный `text` не задан, а переданы
  `balance`/`unlimited` — над предложением выбрать пакет добавляется строка текущего
  баланса («💬 У тебя сейчас: N сообщений» / «∞ (безлимит)» под промо).
- `build_packs_picker_with_balance(conn, user_id=…, vertical_id=…)` — **единый** способ
  открыть «Купить сообщения»: сам читает промо (`is_promo_active`) и баланс
  (`WalletRepository.get_balance`) и строит пикер с шапкой-балансом. Используется и из
  `/topup`, и из инлайн-кнопки `mdl:packs`.
- Доставка — `outbound_send.deliver_outbound_messages` → `bot_api.send_invoice`.
- Хелперы/парсинг callback — `verticals/quick_actions.py`
  (`PACKS_MENU_CALLBACK=mdl:packs`, `pack_callback`, `parse_pack_callback`, `is_packs_menu`;
  легаси `mdl:premium` тоже открывает пикер).

## Идемпотентное зачисление — деньги, обязательно (`services/billing.py`)

`PostgresBillingProvider.credit_pack`: `pre_checkout_query` → `successful_payment` резолвит
пакет по payload, затем в **одной** транзакции вставляет `payment_transactions` (идемпотентно
по `UNIQUE(provider, external_id)` = Telegram `telegram_payment_charge_id`) **и**
`WalletRepository.add_balance`. Повторный webhook того же платежа → `duplicate_external_id`,
баланс не меняется, двойного зачисления нет. `handle_successful_payment` возвращает
`SuccessfulPaymentOutcome` (credited / new balance / duplicate) — `billing_updates.py`
показывает подтверждение с актуальным балансом.

## Где показывается пикер пакетов

- `/topup` (`scenario_intake.py`) — команда бургер-меню называется **«Купить сообщения»**
  (бывш. «Тарифы», см. `bot_commands.py`); открывает пикер с шапкой-балансом.
- кнопка «💬 Купить сообщения» в `verticals/post_intake_offers.py` (`mdl:packs`);
- ветка исчерпанного баланса в `services/text_reply.py`.
- Ветка картинок (`image_reply.py`) показывает нейтральное «картинки недоступны» **без** CTA
  на покупку (пакеты — это текстовые кредиты, картинки ими не открываются).

Callback-и пакетов маршрутизируются в `domain/handler.py` (`_route_message_packs`, до
раскрытия quick-action).

**Где виден баланс.** Актуальный баланс показывается в шапке пикера «Купить сообщения»
(`/topup` / `mdl:packs`) и через `/promo` (для промо — «∞ безлимит»). Карточка `/profile`
баланс **больше не дублирует** — она содержит только данные анкеты (см.
[product.md](product.md) и `services/profile_view.py`: аргумент `message_balance` оставлен в
сигнатуре для совместимости, но в тело не рендерится).

## Наследие месячной модели (не удалено, но не используется)

Таблицы `plans`/`plan_limits`/`usage_counters` и `billing_period` остаются (цепочка миграций
и downgrade целы), но рантайм их **не читает**. `users.current_plan_id` по-прежнему указывает
на `free` (NOT NULL FK) — просто `plan_limits` больше не читается. Миграция `t20_01`
бэкфиллит существующих пользователей на стартовый грант (прежние `premium`-подписчики → 1000,
чтобы никого не понизить); промо-безлимит сохраняется.

## Другие провайдеры (задел)

| Провайдер | Особенности |
|-----------|-------------|
| Stripe | Подписки, webhooks, customer portal |
| ЮKassa | РФ, разные сценарии оплаты |

Общая таблица транзакций `payment_transactions` с полем `provider` и уникальностью по
`(provider, external_id)`.

## Тесты

Офлайн: `tests/test_message_packs.py`, `tests/test_telegram_stars_invoice.py`,
`tests/test_billing_provider.py` (идемпотентность `credit_pack`),
`tests/test_quota_promo_bypass.py` (списание кошелька + промо + картинка-не-списывается).
DB-gated: `tests/integration/test_quota_service.py` (стартовый грант, декремент,
**параллельная атомарность**), `test_telegram_stars_billing.py`, `test_billing_credit_pack.py`
(идемпотентное зачисление по пакету), `test_wallet_and_reset.py` (reset сохраняет
баланс+промо, чистит анкету/историю).
