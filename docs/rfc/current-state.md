# Текущее состояние (current state)

> Достоверное описание архитектуры и реально работающих фич на момент написания.
> Подтверждено кодом: `src/mandala/**`, тестами `tests/**`, миграциями `alembic/versions/`.
> Последнее обновление: 2026-07 (ветка `gnhf-night-2`).

## Продукт

**Mandala** — Telegram-бот + Web API для **серьёзного астрологического консультирования**.
Основная вертикаль — `astrology`: сбор данных рождения, математический расчёт натальной карты,
интерпретации через LLM, прогнозы/транзиты через кнопки меню, синастрия.
Вторая вертикаль (`therapy`) — в каркасе, не активна в проде.

Целевая аудитория: люди, **серьёзно увлечённые астрологией** — ищут настоящего астролога-бота,
а не развлекательный гороскоп. Детальнее — [docs/research/astro-power-users.md](../research/astro-power-users.md).

## Слои архитектуры

```
Каналы (Telegram / Web)
   │
Адаптеры (adapters/telegram, adapters/web)
   │   inbound_map → InboundEvent
   │   outbound_send ← OutboundMessage
   │
Домен/хендлер (domain/handler.py :: handle_inbound)
   │
Сервисы (services/)
   ├── scenario_intake.py   # анкета + команды
   ├── text_reply.py        # LLM + RAG + квоты
   ├── image_reply.py       # генерация изображений + квоты
   ├── intent_router.py     # text vs image routing
   ├── user_identity.py     # резолвинг пользователя
   ├── quota.py             # QuotaService
   ├── billing.py           # BillingProvider, apply_plan_change
   └── telegram_stars.py    # Stars pre_checkout, successful_payment
   │
Verticals (verticals/)
   ├── prompts.py           # системные промпты per vertical_id
   ├── quick_actions.py     # маппинг кнопок → LLM-промпты
   ├── intake_steps.json    # шаги анкеты по vertical_id
   ├── client_knowledge.py  # agent_card, ---mandala--- маркер
   └── post_intake_offers.py # кнопки после завершения анкеты
   │
Репозитории (repositories/)
   ├── users.py             # UsersRepository
   ├── user_channel.py      # channel_links (vertical_id, channel, external_user_id)
   ├── profiles.py          # client_profiles (agent_card JSONB, scenario_state JSONB)
   ├── messages.py          # MessageRepository (dialog history)
   ├── artifacts.py         # generated_artifacts (image URLs, структурированный payload)
   ├── usage.py             # usage_counters (атомарный инкремент)
   ├── plans.py             # plans, plan_limits
   └── payments.py          # payment_transactions
   │
DB (db/engine.py)           # SQLAlchemy Engine, asyncpg / psycopg2
RAG (rag/)                  # Qdrant + OpenAI-compatible embeddings
LLM (llm/)                  # OpenAI-compatible text + image clients
Observability (observability.py) # op_format, mask_api_key
```

## Реально работающие фичи

### Анкета (scenario_intake)
- Шаги опроса по `intake_steps.json`: `full_name`, `birth_date`, `birth_place`, `birth_time`
- Валидаторы: `full_name`, `birth_date`, `birth_place`, `birth_time`, `min_len`
- Команды работают в любой момент диалога: `/start`, `/restart` (soft reset), `/reset` (hard reset), `/help`, `/about`, `/info`, `/promo CODE`, `/topup` (тест)
- После анкеты — математический расчёт натальной карты (`astro/natal_chart.py`)
- Карта сохраняется в `agent_card[AGENT_CARD_NATAL_CHART_DATA]` (dict: sun_sign, moon_sign, ascendant, planets, aspects, chart_system)

### Натальная математика (astro/natal_chart.py)
- Геокодинг города: Nominatim (OpenStreetMap) + TimezoneFinder
- Расчёт карты: kerykeion + pyswisseph (Swiss Ephemeris)
- Системы: `western` (тропическая, по умолчанию) и `vedic` (сидерическая, Lahiri)
- Планеты: Солнце, Луна, Меркурий, Венера, Марс, Юпитер, Сатурн, Уран, Нептун, Плутон
- Аспекты: через `AspectsFactory.single_chart_aspects()` (kerykeion v4+)
- Асцендент: при известном времени рождения
- **Текущие транзиты**: `calculate_current_transits(year, month, day)` — позиции планет на заданную дату, инжектируются в system-промпт LLM через `current_transits_to_system_text()`
- **Geocoding failsafe**: `ValueError: City not found` / `Geocoding failed` → мягкое сообщение пользователю с предложением указать ближайший крупный город

### Кнопки и клавиатура (retention-петля)
- **Навигация только inline** (постоянная Reply-клавиатура `ASTROLOGY_REPLY_KEYBOARD`
  удалена). Каждый ответ заканчивается inline-клавиатурой, которую выбирает LLM через
  nav-блок; при отсутствии валидного nav контекстный fallback навешивает
  `services/nav_guarantee.py` (`ensure_nav`). `outbound_send` навешивает одноразовый
  `ReplyKeyboardRemove` на первое сообщение без кнопок, чтобы убрать залипшую клавиатуру
  у старых пользователей. Подробно — [../astrology/navigator-ux.md](../astrology/navigator-ux.md).
- Профиль / сброс / помощь — в бургер-меню (`setMyCommands`), не на клавиатуре.
- Inline-подменю прогноза: «📅 Сегодня», «📆 Неделя», «🗓️ Месяц», «🔭 Год» (callback_data: `mdl:fc_today` и т.д.)
- **Inline-кнопки сфер** после каждого LLM-ответа (`_with_sphere_followup`): 6 тем в 2 ряда — Личность, Отношения, Партнёр, Карьера, Финансы, Здоровье
- Callback routing в `domain/handler.py`: `expand_inbound_quick_action()` → LLM-промпт
- `answerCallbackQuery` вызывается после каждого колбэка (polling + webhook)
- Outbound: `OutboundMessage.buttons` → `inline_keyboard` (поддержка `callback_data` и `url`)
- Web-канал: `callback_data` принимается, `buttons` возвращаются в JSON

### Команды быстрых действий (quick_actions)
| Кнопка / callback_data | Действие |
|------------------------|---------|
| `mdl:natal` | Интерпретация натальной карты |
| `mdl:fc_today/week/month/year` | Прогноз на период |
| `mdl:syn` | Совместимость (инструкция) |
| `mdl:th_fin/rel/health` | Тематические разборы (финансы, отношения, здоровье) |
| `mdl:th_personality` | Личность и характер |
| `mdl:th_career` | Карьера и предназначение |
| `mdl:th_partner` | Партнёрство и брак |
| `mdl:switch_western/vedic` | Переключение системы + пересчёт |
| `mdl:forecast_menu` | Inline-подменю периодов |
| `mdl:profile` | Показ профиля пользователя |

### LLM и RAG
- Клиент: OpenAI-compatible API (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`)
- По умолчанию модель: deepseek-v4-flash (через `LLM_VERTICAL_OVERRIDES_PATH`)
- Контекст: последние 20 сообщений из `messages` + опциональный `dialog_summary`
- RAG: Qdrant (`MANDALA_RAG_BACKEND=qdrant`, `QDRANT_URL`), изоляция по `vertical_id`
- KB: `src/mandala/verticals/kb/{vertical_id}/*.md`
- Парсинг `---mandala---` маркера из ответа LLM → сохранение в `agent_card`

### Изображения
- Провайдер: `stub` (по умолчанию) или `openai_compatible`
- Команды: `/image`, `/picture`, `нарисуй`, `draw`
- Запись в `messages` (kind=image) + `generated_artifacts` (image_url, provider)
- `can_consume` до вызова API; `consume` только после успеха

### Квоты и планы
- Планы: `free` (seed), `premium` (Telegram Stars)
- `QuotaService`: `can_consume` / `consume`, атомарный `UPDATE ... WHERE count < limit`
- Ресурсы: `text_reply`, `image_generation`
- Период: `billing_period.py` (calendar month UTC)
- Промокоды: `/promo CODE` → `activated_promo` в `agent_card`, сброс лимитов в `scenario_state`
- **P0 fix**: `consume` теперь проверяет `is_promo_active` и сразу возвращает `allowed=True` при активном промо (раньше логировался WARNING `limit_exceeded` после promo-пользователей)

### Telegram Stars (биллинг)
- `pre_checkout_query` → `answerPreCheckoutQuery`
- `successful_payment` → `PostgresBillingProvider.activate_plan` → `apply_plan_change`
- Идемпотентность по `telegram_payment_charge_id`
- Сброс usage за текущий период, установка `subscription_period_end`

### Каналы
- **Telegram**: long polling (`python -m mandala.adapters.telegram`) + webhook (`POST /webhooks/telegram/{vertical_id}`)
- **Web**: `POST /webhooks/web` (vertical_id из тела или заголовка `X-Vertical-Id`)
- **Health**: `GET /health` (SELECT 1)

### Деплой
- **Единый способ выкатки:** `bash scripts/deploy/deploy.sh` — удалённая сборка образа на ВМ + миграции + E2E на проде + авто-откат. Единый источник правды: [`scripts/deploy/README.md`](../../scripts/deploy/README.md). Устаревшее (не использовать): `deploy-serverless.sh`, `build_image.sh`.
- Containerfile + `scripts/deploy/`
- Yandex Cloud: VM `n8n-server`, Managed PostgreSQL `n8n-postgres`, Nginx, certbot
- Домен: `api.mandala-app.online`
- Алembic: `alembic upgrade head` при старте
- Terraform: `terraform/` (DNS A-запись), state — локальный (MVP)

## Расхождения между кодом и старыми доками

| Документ | Что говорит | Реальность в коде |
|----------|------------|-------------------|
| `docs/product.md` | «мандала/нумерология» | Продукт — **астролог-бот**, вертикаль `astrology` |
| `docs/architecture.md` | LangGraph для оркестрации | Нет LangGraph, пайплайн в `domain/handler.py` |
| `docs/architecture.md` | Redis (опционально) | В MVP Redis не используется |
| `docs/agent.md` | Граф агента | В коде: линейный пайплайн `intake → intent_router → text_reply/image_reply` |
| `docs/product.md` | «нумерология» как методология | В коде: Swiss Ephemeris + кerykeion, западная и ведическая астрология |

## Открытые TODO в коде

- `TODO: тикет 21+` — продуктовый UI оплаты (`create_payment_offer`)
- `TODO: несколько токенов → несколько vertical_id` — резолвинг в webhook
- `TODO: Authorization: Bearer → vertical_id` — для Web API
- Паритет Web-канала: фронт должен поддержать `buttons` из JSON-ответа
- RAG в проде: Qdrant не запущен на VM (требует отдельного сервиса)
- Синастрия: разбор совместимости двух карт (1.4 в план)

## Что сделано в gnhf-night-2 (2026-07)

- **P0.1**: fix `QuotaService.consume` — при активном промо не логирует ложный WARNING `limit_exceeded`
- **P0.2**: fix `City not found` — graceful сообщение пользователю с предложением указать ближайший крупный город
- **P0.3**: текущие транзиты планет (`calculate_current_transits`) инжектируются в каждый LLM-запрос; промпт запрещает апологии «нет данных о транзитах»
- **P1**: системный промпт astrology обновлён — обязательный блок «Итог:» (2–4 предложения) после каждого содержательного ответа
- **P2**: inline-кнопки сфер жизни после LLM-ответов: Личность, Отношения, Партнёр, Карьера, Финансы, Здоровье
- **P3**: кнопки периодов прогноза (Сегодня/Неделя/Месяц/Год) — уже работали, подтверждено тестами
