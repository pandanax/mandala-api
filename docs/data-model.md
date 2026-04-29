# Модель данных

Источник истины для **операционных данных** — **PostgreSQL**. Ниже — логическая схема; имена таблиц и полей можно уточнить при миграциях.

## Принцип: гибрид реляция + JSONB

| Слой | Где хранить | Зачем |
|------|-------------|--------|
| Учётные сущности, связи, квоты, платежи, «шапки» сообщений | Обычные колонки, FK, уникальные индексы | ACID, отчёты, целостность, простые запросы |
| Карточка клиента под конкретного агента, `scenario_state`, структурированные рекомендации, произвольные поля анкеты | **JSONB** (`agent_card`, `scenario_state`, `payload` артефактов) | Разные агенты — разная форма без сотен nullable-колонок |
| Эмбеддинги и ANN-поиск (RAG) | **Отдельное векторное хранилище** (см. [architecture.md](architecture.md)) | Не смешивать OLTP и поиск; связь по id и `vertical_id` |

В JSONB по договорённости можно хранить **`schema_version`** (целое) для эволюции формата под одного агента.

## Вертикаль (агент / продукт)

### `agent_verticals` (опционально, если не только файлы конфига)

- `id` / `slug` (`vertical_id`) — например `astrology`, `therapy`
- `display_name`, флаги активности
- опционально: `config` JSONB (пути к KB, дефолтные model ids) — только **метаданные продукта**, не вся карточка клиента

Привязка Telegram **bot token** → `vertical_id` — в конфиге деплоя и/или в этой таблице / секретах. В текущем MVP (тикеты 9–10): long polling и webhook используют **`TELEGRAM_BOT_TOKEN`** + **`TELEGRAM_VERTICAL_ID`** из env; в webhook **`vertical_id`** дополнительно задаётся **сегментом пути** (`/webhooks/telegram/{vertical_id}`) и должен совпадать с настроенной вертикалью бота.

## Пользователь и каналы

### `users`

- `id` (UUID, PK)
- **`vertical_id`** (FK или текстовый slug — зафиксировать в миграции)
- `created_at`, `updated_at`
- `locale` (опционально)
- `current_plan_id` → `plans` (или подписка в отдельной таблице)
- `subscription_period_start`, `subscription_period_end` (если биллинг по периодам)

### `channel_links`

Связь «один человек — несколько каналов» **в рамках одной вертикали**.

- `id`
- `user_id` → `users`
- **`vertical_id`** (согласован с `users.vertical_id`)
- `channel` (enum: `telegram`, `web`, `cli`, …)
- `external_user_id` (строка)
- `metadata` (JSONB, опционально)

Уникальный индекс: **`(vertical_id, channel, external_user_id)`**.

Идемпотентное создание пользователя и строки связи — **`mandala.services.user_identity.UserIdentityService`** (тикет 8): план по умолчанию **`free`** из таблицы **`plans`** (seed).

## Профиль клиента (гибкая часть)

### `client_profiles` (рекомендуемое имя; можно объединить с `users` при простой модели)

- `user_id` (PK или FK), **`vertical_id`**
- **`agent_card` JSONB** — всё агент-специфичное: анкета, слоты, ответы пользователя в свободной форме
- **`scenario_state` JSONB** — текущий шаг графа, флаги, машина состояний
- при необходимости денормализованные колонки для частых фильтров (например `display_name` text)

Персональные данные хранить осознанно (политика удаления, минимизация логов).

## Диалог и сгенерированный контент

### `conversations` (опционально)

- `id`, `user_id`, **`vertical_id`**, `channel`, `created_at`

### `messages`

- `id`, `user_id`, **`vertical_id`**, `role` (`user` / `assistant` / `system`)
- опционально **`content_kind`** — грубый тип контента (текст, изображение, файл, …) для фильтров и аналитики
- `content_text` (TEXT, опционально) — плоский текст для простых кейсов
- **`content_meta` JSONB** — вложения, `telegram_file_id`, ссылки на объектное хранилище, mime-type
- `created_at`
- при больших объёмах — **summary** в отдельном поле JSONB или таблице `conversation_summaries`

**Индекс под типовой запрос «последние N сообщений»:** btree **`(user_id, vertical_id, created_at DESC)`** — ускоряет `ORDER BY created_at DESC LIMIT N` в разрезе пользователя и вертикали (см. миграцию `t4_01_dialog_oltp`). При равных `created_at` в приложении задайте детерминированный порядок (например вторично **`id DESC`** в репозитории сообщений, тикет 5).

### `generated_artifacts` (рекомендуется для аудита рекомендаций и медиа)

- `id`, `user_id`, **`vertical_id`**
- `kind` (`text_recommendation`, `image`, …)
- **`payload` JSONB** — текст рекомендаций, URL, id файла, произвольная структура под агента
- `source_message_id` → `messages` (опционально)
- `created_at`

Дублирование с `messages` допустимо по политике: короткий ответ в `messages`, развёрнутый отчёт в `generated_artifacts`.

## Планы и лимиты

См. [quotas-and-plans.md](quotas-and-plans.md). Таблицы: `plans`, `plan_limits` (реляционно; при необходимости расширение `metadata` JSONB на уровне плана).

## Usage

### `usage_counters`

Учёт расхода по периоду (атомарные инкременты в транзакции):

- `user_id`, **`vertical_id`** — квоты **в разрезе вертикали** (не глобально на всех агентов платформы)
- **`billing_period`** (TEXT) — в миграции зафиксирован **календарный месяц в виде строки `YYYY-MM`** (например `2026-04`). Поля `period_start` / `period_end` в таблице не используются; при смене правила периода (биллинговый месяц vs календарный) — отдельная миграция и тикет 7
- `resource` (`text_reply`, `image_generation`, …)
- `count`

Уникальность: **`(user_id, vertical_id, billing_period, resource)`** — одна строка счётчика на пару «период + ресурс» в границах пользователя и вертикали (см. миграцию `t4_01_dialog_oltp`).

Прикладная логика проверки лимита и инкремента в одном стиле с периодом биллинга — **`mandala.services.quota.QuotaService`** (тикет 7): строки **`plan_limits`** с **`period = month`** соответствуют месячному ключу **`YYYY-MM`**.

## Платежи

### `payment_transactions` (или `billing_events`)

- `id`, `user_id`, **`vertical_id`** (если платёж привязан к боту-вертикали)
- `provider` (`telegram_stars`, `stripe`, …)
- `external_id` (id у провайдера)
- `amount`, `currency`
- `plan_id` или товар
- `status` (`pending`, `completed`, `failed`)
- `raw_payload` (JSONB, для отладки, с маскированием PII)
- `created_at`

Идемпотентность: повтор webhook с тем же `external_id` не должен дважды активировать план.

## База знаний (метаданные)

Документы для RAG могут храниться как файлы + метаданные в БД:

### `kb_documents`

- `id`, **`vertical_id`**, `title`, `source`, `chunking_version`
- при необходимости связь с объектами в object storage

Чанки и векторы — **во внешнем векторном хранилище** с ссылкой на `document_id` и `chunk_index` / `chunk_id`.
