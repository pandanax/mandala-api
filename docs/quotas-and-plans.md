# Квоты: кошелёк сообщений

> **Актуальная модель — предоплаченный кошелёк сообщений, без планов и без периодов.**
> Полное описание монетизации — в [billing.md](billing.md). Здесь — механика квоты/списания.
> Авторитетный код: `services/quota.py`, `repositories/wallet.py`.

## Как это работает

- Квота = **баланс сообщений** на `users.message_balance` (ключ `(user, vertical)`), а не
  лимит-за-период. Числа не «сгорают» по времени.
- **Каждый LLM-ответ текстом списывает 1**; мгновенные детерминированные рендеры
  (`/natal`, `/matrix`, `/numerology`, `/profile`, `/help`) не списывают.
- Стартовый разовый грант новому пользователю — `MANDALA_MESSAGE_WALLET_START` (по умолчанию 20).
- На балансе 0 — три кнопки-пакета Stars (покупка **добавляет** сообщения; см. [billing.md](billing.md)).
- **Промо — вечный безлимит**, обходит списание; переживает `/reset`.
- **Картинки не тарифицируются** из кошелька: `image_generation` разрешён только под промо,
  иначе отказ, кошелёк не трогается.

## Списание (атомарно, без гонок)

Сервис `mandala.services.quota.QuotaService` ресурс-аware:

- `can_consume` / `consume` для `text_reply` читают/декрементят кошелёк
  (`WalletRepository`, `repositories/wallet.py`) единым атомарным
  `UPDATE … SET message_balance = message_balance - 1 WHERE … AND message_balance >= 1
  RETURNING …` — не уходит в минус, защищён от параллельных ходов.
- Точка списания — **после** успешного ответа, в той же транзакции.
- Промо обходит проверку целиком; картинки не списываются.

## `/reset` сохраняет баланс и промо

`ProfileRepository.reset_session` **не трогает** `users` (баланс сохраняется) и сохраняет
единственный ключ `activated_promo` в `agent_card`, вычищая анкету, историю и остальной
`agent_card`. Регрессия: `tests/integration/test_wallet_and_reset.py`.

## Наследие: таблицы планов не читаются

`plans` / `plan_limits` / `usage_counters` остаются в схеме (миграции целы), но рантайм их
**не читает**. `users.current_plan_id` по-прежнему указывает на `free` (NOT NULL FK). Историю
перехода на кошелёк см. в [billing.md](billing.md) (миграция `t20_01_message_wallet`).

## Тесты

Офлайн: `tests/test_quota_promo_bypass.py`. DB-gated:
`tests/integration/test_quota_service.py` (стартовый грант, декремент, параллельная
атомарность), `tests/integration/test_wallet_and_reset.py`.
