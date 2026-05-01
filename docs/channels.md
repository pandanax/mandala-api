# Каналы (мультиплатформенность)

## Цель

Ядро приложения и агент работают с **внутренним `user_id`**, доменными структурами и **`vertical_id`** — идентификатором **конкретного агента / продукта** (например астролог, психолог, нутрициолог): один код, разные конфиги, базы знаний и сценарии. Telegram, Web и CLI подключаются как **адаптеры** одинакового контракта.

## Вертикаль и откуда она берётся

**`vertical_id`** (строковый slug, например `astrology`, `therapy`) должен быть известен **до** вызова доменного `handle_inbound`. Адаптер или тонкий слой перед ним определяет его так:

- **Telegram:** по токену бота (один токен — одна вертикаль в MVP long polling: переменные **`TELEGRAM_BOT_TOKEN`** и **`TELEGRAM_VERTICAL_ID`**, см. `README`); по пути webhook **`POST /webhooks/telegram/{vertical_id}`** (тикет 10) — `vertical_id` берётся из URL и передаётся в маппинг; при **`setWebhook`** с `secret_token` сервер сверяет **`X-Telegram-Bot-Api-Secret-Token`** с **`TELEGRAM_WEBHOOK_SECRET`** (если задан). В **`setWebhook`** не ограничивайте **`allowed_updates`** только **`message`**, иначе не придут **`callback_query`** (inline‑кнопки). Таблица token→`vertical_id` / несколько ботов — при необходимости расширить позже.
- **Web:** из поля **`vertical_id`** в JSON-теле **`POST /webhooks/web`** или из заголовка **`X-Vertical-Id`** (приоритет: непустое значение в теле, иначе заголовок). Маппинг **`Authorization: Bearer`** → **`vertical_id`** — **TODO** до таблицы/конфига ключей (см. ответ 422 и код). Идентификатор пользователя в канале (**`external_user_id`** для **`channel_links`**) в MVP: заголовок **`X-External-User-Id`** (стабильная строка со стороны клиента/интеграции; JWT/сессия — следующие итерации).
- **CLI:** из аргумента или конфига запуска.

Квоты, профиль клиента и ссылки канал ↔ пользователь в БД всегда в разрезе **`vertical_id`** (см. [data-model.md](data-model.md)).

**Реализация в коде:** типизированные модели входа/выхода и валидация — пакет **`mandala.domain`** (тикет 6, по таблицам ниже). Доменный обработчик **`handle_inbound(event, conn, llm_client=None, image_client=None, kb_search=None)`** (тикеты 8, 12–17) принимает **активное соединение БД в транзакции** (`sqlalchemy.engine.Connection`): внутри резолвятся пользователь и профиль; при незавершённой анкете — **`mandala.services.scenario_intake`** (обновление **`scenario_state`** / **`agent_card`**); иначе при намерении «картинка» (см. **`mandala.services.intent_router`**) — квота **`image_generation`** и ветка **`mandala.services.image_reply`** (артефакт и при реальном провайдере — **`OutboundMessage.photo`**); иначе для **текста** — пайплайн **`text_reply`**: квота **`text_reply`** → при включённом env — **RAG (Qdrant)** в системный промпт → последние **N** сообщений из **`messages`** в контекст чата (тикет 17) → LLM → запись в **`messages`**. Адаптер канала открывает транзакцию и вызывает `handle_inbound`. HTTP Web: **`POST /webhooks/web`** (**`mandala.http.web_chat`**) — тот же **`handle_inbound`**, ответ JSON (**`OutboundMessage`**). При расхождении имён или семантики полей — обновить эту доку или код в одном изменении.

## Вход: InboundEvent

Нормализованное событие после парсинга канала:

| Поле | Описание |
|------|----------|
| **`vertical_id`** | Какой агент обрабатывает событие; обязателен для маршрутизации к конфигу, KB и `channel_links` |
| `channel` | Идентификатор канала (`telegram`, `web`, …) |
| `external_user_id` | Стабильный id пользователя **в этом канале** (внутри этой вертикали) |
| `text` | Текст сообщения (если есть) |
| `attachments` | Список вложений (фото, документы) при поддержке |
| `callback_data` | Для inline-кнопок и аналогов |
| `locale` | Если доступно |
| `raw_ref` | Опционально: ссылка на сырой объект для ответа (напр. `chat_id` в Telegram) |

**Реализация (тикет 19):** long polling и **`POST /webhooks/telegram/{vertical_id}`** сначала обрабатывают **``pre_checkout_query``** / **``message.successful_payment``** (см. **`process_telegram_billing_update`**), без вызова **`handle_inbound`**. Обычные сообщения — как раньше. В webhook для **оплаты** нужен настроенный **``TELEGRAM_BOT_TOKEN``** для той же **``vertical_id``**, иначе ответ **``answerPreCheckoutQuery``** / уведомление о покупке невозможен (500).

Адаптер **не** решает квоты и бизнес-логику — только маппинг и заполнение **`vertical_id`**.

## Выход: OutboundMessage

Универсальное представление ответа пользователю:

| Поле | Описание |
|------|----------|
| `text` | Текст (markdown/HTML по соглашению с каналом) |
| `buttons` | Опционально: клавиатура / inline |
| `photo` | URL или file_id / байты — по возможностям канала |
| `requires_payment` | Флаг или структура для Stars-only UI в Telegram |
| `defer` | «Ответ позже» для долгих задач (картинка в worker) |

В **`OutboundMessage`** поле **`vertical_id`** не обязательно: ответ относится к тому же диалогу, что и входящее событие; вертикаль уже зафиксирована в контексте обработчика и в логах (тикет 20: при доставке в Telegram в **`deliver_outbound_messages`** передаётся **`vertical_id`** для операционных логов). При необходимости (мультибот в одном процессе, отладка) допускается расширение модели метаданными — без изменения контракта для адаптеров.

Адаптер Telegram переводит это в `sendMessage`, `sendPhoto`, invoice и т.д. Web-адаптер — в JSON/WebSocket событие.

## Идентификация пользователя

1. По **`(vertical_id, channel, external_user_id)`** ищется запись в `channel_links`.
2. Если нет — создаются **`users`** + **`channel_links`** в границах этой вертикали; план по умолчанию — строка **`free`** в таблице **`plans`** (общий seed, см. миграции), при этом **`users.vertical_id`** задаёт продукт.

В коде: **`mandala.services.user_identity.UserIdentityService.get_or_create_user`** (идемпотентность по уникальному индексу на `channel_links`).

Один и тот же человек в Telegram может быть **разными** `user_id` в разных вертикалях — это нормально: разные боты и разные «карточки клиента».

Слияние аккаунтов между вертикалями или каналами (Telegram + Web): отдельный сценарий «привязать канал» с верификацией, явно вне базового `InboundEvent`.

## CLI (будущее)

CLI передаёт тот же `InboundEvent` с `channel=cli`, **`vertical_id`** из флага/конфига и, например, `external_user_id` из конфига или логина.

## Web (тикет 21, без UI)

**`POST /webhooks/web`** (FastAPI, см. **`mandala.http.web_chat`**): тело JSON — опционально **`text`**, **`vertical_id`**, **`locale`**, **`callback_data`**; обязателен заголовок **`X-External-User-Id`** (MVP: внешний id пользователя в канале `web`). После сборки **`InboundEvent`** с **`channel=web`** вызывается **`handle_inbound`** в транзакции БД; ответ **`{ "messages": [ … ] }`** — элементы в форме **`OutboundMessage`** (текст, **`photo`**, **`buttons`**, флаги **`requires_payment`** / **`defer`**). Внутренний UUID пользователя в JSON **не** отдаётся.

Операционные логи: префикс **`funnel`**, **`mandala.observability.op_format`**, на **INFO** не пишется текст пользовательского сообщения (только признаки вроде **`has_text`**).

Сессия, JWT и маппинг на единый аккаунт между Telegram и Web — отдельные сценарии (вне базового тикета 21).
