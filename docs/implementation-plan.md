# Пошаговый план реализации (тикеты)

Каждый пункт — **отдельный тикет**: его можно передать ИИ-агенту как единое задание. Порядок учитывает зависимости; параллельно можно вести только тикеты без блокирующих зависимостей (указано в каждом пункте).

**Общие правила для исполнителя:** соблюдать [architecture.md](architecture.md), [data-model.md](data-model.md), [channels.md](channels.md), [billing.md](billing.md), [quotas-and-plans.md](quotas-and-plans.md), [agent.md](agent.md). Источник истины по схеме — [data-model.md](data-model.md); при расхождениях с кодом актуализировать документ отдельным PR. **Локальные контейнеры в этом репозитории** — через **Podman** (`podman compose`, `podman build`); в README и тикетах не опираться на Docker CLI.

**Продуктовый контекст (после тикета 1):** платформа для **нескольких агентов / вертикалей** (астролог, психолог, нутрициолог и т.д.): у каждого — своя KB, сценарий и форма «карточки клиента», при этом **ядро данных единое**. **Решение по БД (зафиксировано): гибрид PostgreSQL** — реляционные таблицы и строгие индексы для учётных сущностей, **квот, биллинга, связей пользователь–канал–вертикаль**; поля **JSONB** для **гибкого тела** карточки клиента, `scenario_state`, структурированных рекомендаций и метаданных артефактов (текст/картинки), чтобы не плодить сотни nullable-колонок под каждого агента. **Векторный поиск (RAG)** — **отдельное хранилище** (Qdrant, pgvector, Managed-сервис и т.д.), подключаемое **вторым шагом** после стабильного пайплайна (тикет 16). Локально — **Podman Compose** с **PostgreSQL**; выкладка в **Yandex Cloud** (Managed PostgreSQL + при необходимости отдельный векторный сервис) через **Terraform** — этап тикета 23 и далее.

---

## Тикет 1. Каркас репозитория и качество кода

**Цель:** воспроизводимая среда разработки и единый стиль.

**Объём:**
- Структура каталогов (`src/` или `app/`, `tests/`, `docs/` уже есть).
- Менеджер зависимостей: `pyproject.toml` (Poetry или uv/pip-tools) с Python 3.11+.
- Линтер и форматтер: `ruff` (или аналог), конфиг в репозитории.
- Типизация: `mypy` в режиме, согласованном с проектом (хотя бы для новых модулей).
- `.env.example` со всеми переменными окружения без секретов.
- `README` в корне репозитория (кратко): как установить зависимости, как запустить тесты.

**Критерии приёмки:**
- `pip install` / `uv sync` восстанавливает окружение по инструкции.
- Одна команда запускает линтер и тесты (пусть тестов пока мало).
- В `.env.example` перечислены ключи для БД, Telegram, LLM (плейсхолдеры).

**Зависимости:** нет.

---

## Тикет 2. Локальная инфраструктура: Podman Compose и PostgreSQL

**Цель:** одна команда поднимает **PostgreSQL** для разработки и интеграционных тестов.

**Объём:**
- Файл **`compose.yaml`** под **Podman Compose** (`podman compose`): сервис **PostgreSQL** (версия зафиксирована), volume, порт, учётные данные только через `.env`.
- В корневом `README`: поднятие стека **`podman compose up -d`**, **`DATABASE_URL`** (asyncpg/psycopg — зафиксировать в тикете).
- Обновить `.env.example`: `DATABASE_URL` с плейсхолдером, без реальных паролей в репозитории.

**Критерии приёмки:**
- `podman compose up -d` поднимает Postgres; приложение или скрипт проверки подключается по `DATABASE_URL`.
- Секреты не коммитятся.

**Зависимости:** тикет 1.

---

## Тикет 3. Миграции (Alembic): вертикали, пользователи, каналы, планы, лимиты

**Цель:** одно ядро — много агентов (**`vertical_id`**): учётные сущности в **нормализованных таблицах**; описание вертикали (пути KB, промпты, маппинг Telegram token → `vertical_id`) — **конфиг в репозитории** и/или таблица **`agent_verticals`** (slug, метаданные без тяжёлого JSON бизнес-данных клиента).

**Объём:**
- **Alembic**: таблицы **`users`** (`vertical_id`, FK на план, …), **`channel_links`** с уникальным **`(vertical_id, channel, external_user_id)`**, **`plans`**, **`plan_limits`**.
- Seed-миграция или скрипт: минимум два плана (`free`, `premium`) с разными лимитами для `text_reply` и `image_generation` (включая **0** картинок на одном из планов).
- Привязка пользователя к плану — одна согласованная схема (поле на `users` или подписки) + комментарий в миграции.

**Критерии приёмки:**
- `alembic upgrade head` с нуля без ошибок; после seed планы и лимиты читаются SQL-запросом.
- Два разных `vertical_id` не дают коллизии пользователя при одном и том же Telegram `external_user_id`.

**Зависимости:** тикеты 1–2.

---

## Тикет 4. Миграции: профиль клиента (JSONB), сообщения, артефакты, usage, платежи

**Цель:** **гибрид**: жёсткая «шапка» в колонках + **JSONB** для **тела карточки клиента**, сценария и произвольных рекомендаций под каждого агента; история диалога и сгенерированный контент — в Postgres.

**Объём:**
- Таблица **`client_profiles`** (или расширение `users`): FK `user_id`, `vertical_id`; **JSONB** `agent_card` (или несколько полей JSONB: `profile_body`, `scenario_state`) — форма зависит от агента, валидируется в приложении (версия схемы в JSON при необходимости).
- **`messages`**: роль, тип контента, `content_text` при необходимости + **`content_meta` JSONB** (вложения, `file_id`, ссылки); индексы под последние N по `(user_id, vertical_id, created_at)`.
- **`generated_artifacts`**: тип (текст/изображение), **`payload` JSONB** (рекомендации, URL, telegram file_id и т.д.), FK на пользователя/вертикаль; либо денормализация части в `messages` — зафиксировать одну схему в миграции.
- **`usage_counters`**, **`payment_transactions`** — реляционно, с уникальностью и полями под идемпотентность **`(provider, external_id)`**; при необходимости **`raw_payload` JSONB** с маскированием в логах.

**Критерии приёмки:**
- Миграции накатываются после тикета 3; индексы под типовые запросы описаны в `data-model.md` или комментариях.
- Пример: сохранение ответа LLM как строка в `messages` + опционально структура в `generated_artifacts.payload` — задокументировано.

**Зависимости:** тикет 3.

---

## Тикет 5. Слой доступа к данным (репозитории / DAO)

**Цель:** ядро не пишет сырой SQL в хендлерах; квоты и биллинг идут через репозитории с **транзакциями PostgreSQL**.

**Объём:**
- Репозитории: пользователь по `channel_links` и **`vertical_id`**, CRUD профиля (в т.ч. merge JSONB для `scenario_state`), сообщения и артефакты, чтение лимитов плана.
- Транзакции и **`SELECT … FOR UPDATE`** / атомарный `UPDATE … WHERE count < limit` там, где нужна атомарность квот (см. тикет 7).

**Критерии приёмки:**
- Интеграционные тесты против Postgres из Podman Compose (или testcontainers).
- Логика периода биллинга и ключей не дублируется по модулям.

**Зависимости:** тикет 4.

---

## Тикет 6. Доменные контракты: InboundEvent и OutboundMessage

**Цель:** канало-независимый вход/выход из [channels.md](channels.md) + явный контекст **вертикали**.

**Объём:**
- Pydantic-модели: `InboundEvent`, `OutboundMessage` по документации каналов; поле **`vertical_id`** (или разрешение из токена бота до `vertical_id` на границе адаптера — зафиксировать одно место).
- Функция приложения: `handle_inbound(event: InboundEvent, conn: Connection) -> list[OutboundMessage]` — после тикета 8: резолвинг пользователя и профиль (см. тикет 8); до полного пайплайна агента ответ остаётся минимальным стубом.

**Критерии приёмки:**
- Тесты сериализации/валидации.
- Докстринги: назначение полей, в т.ч. `vertical_id`.

**Зависимости:** тикет 5.

**Примечания к реализации:** добавить **Pydantic v2** в основные `[project] dependencies` в `pyproject.toml`. Модели `InboundEvent` / `OutboundMessage` и заглушка `handle_inbound` размещаются под `src/mandala/` (подпакет на усмотрение исполнителя, например `mandala.domain` или `mandala.contracts` — зафиксировать импорты в рамках тикета). Резолвинг «токен бота → `vertical_id`» — зона адаптеров (**тикеты 9–10**); в **`InboundEvent` на входе `vertical_id` уже задан** до вызова `handle_inbound` (см. [channels.md](channels.md)).

**Состояние кода (закрыто):** пакет **`mandala.domain`** (`src/mandala/domain/`): `contracts.py` (`InboundEvent`, `OutboundMessage`, `InboundAttachment`), `handler.py` (`handle_inbound` — с тикета 8 принимает **`conn`** и обращается к БД, см. тикет 8), зависимость **`pydantic>=2`**, mypy-плагин `pydantic.mypy`, тесты **`tests/test_domain_contracts.py`**.

---

**Состояние кода (актуализация после тикета 20):** как после тикета 19, плюс операционные логи воронки **`inbound → quota → llm → outbound`** и биллинга: модуль **`mandala.observability`** (**`op_format`**, **`mask_api_key`**), префикс сообщений **`funnel`** в **`handle_inbound`**, **`QuotaService`**, **`text_reply`** / **`image_reply`**, **`deliver_outbound_messages`** (при переданном **`vertical_id`**), **`mandala.http`** (webhook), **`mandala.adapters.telegram.polling`**, **`process_telegram_billing_update`**; в **`apply_plan_change`** / **`PostgresBillingProvider.activate_plan`** — поля **`reason`** / **`outcome`** без сырого PII на INFO. Сквозная проверка: **`bash scripts/check.sh`**; с БД — **`bash scripts/verify_project.sh`** (см. README).

## Тикет 7. Сервис квот: проверка и атомарный инкремент usage

**Цель:** лимиты из **PostgreSQL**, без гонок (транзакции + блокировка строки или условный `UPDATE`).

**Объём:**
- Правило «текущего периода» (календарный месяц или биллинговый интервал) — один вариант, описан в коде и в комментарии.
- `can_consume` / `consume`: одна транзакция с **`SELECT … FOR UPDATE`** на строке usage/плана **или** атомарный `UPDATE usage_counters SET count = count + 1 WHERE … AND count < :limit`.
- Лимиты из `plan_limits` по текущему плану пользователя в рамках **`vertical_id`**.

**Критерии приёмки:**
- Тест на конкурентные `consume` не превышает лимит.
- Лимит `0` для `image_generation` — отказ без вызова image API.

**Зависимости:** тикеты 3–5.

**Состояние кода (закрыто):** модуль **`mandala.services.quota`**: `QuotaService` (`can_consume`, `consume`), константы ресурсов `RESOURCE_*`, результат `QuotaConsumeResult`; лимит из **`plan_limits`** (период **`month`**) по **`users.current_plan_id`** и **`vertical_id`** через **`UsersRepository`**; ключ периода счётчика — **`current_billing_period()`** (согласован с **`usage_counters.billing_period`**); атомарность расхода — **`UsageRepository.try_increment`**. Интеграционные тесты: **`tests/integration/test_quota_service.py`**.

---

## Тикет 8. Резолвинг пользователя по каналу и вертикали

**Цель:** стабильный внутренний `user_id` в границах вертикали.

**Объём:**
- `get_or_create_user(vertical_id, channel, external_user_id)`; план по умолчанию `free` для этой вертикали.
- В `handle_inbound`: после резолва — загрузка профиля клиента.

**Критерии приёмки:**
- Повторные вызовы не создают дубликатов в паре `(vertical_id, channel, external_user_id)`.
- Тест: первый и повторный заход.

**Зависимости:** тикеты 5–6.

**Состояние кода (закрыто):** **`UserIdentityService.get_or_create_user`** (`src/mandala/services/user_identity.py`): идемпотентность по уникальному индексу **`channel_links (vertical_id, channel, external_user_id)`**, план **`free`** из **`plans`** (seed); **`handle_inbound(event, conn)`** — транзакционное соединение SQLAlchemy, после резолва **`ProfileRepository.ensure_row`** + **`get_by_user_id`**. Интеграционные тесты: **`tests/integration/test_user_identity.py`**.

---

## Тикет 9. Адаптер Telegram: приём сообщений и отправка ответов

**Цель:** работающий бот без полной бизнес-логики агента; поддержка текста и **вложений/картинок** на входе (метаданные в `InboundEvent`).

**Объём:**
- Токен из env **или** таблица соответствия token→`vertical_id` (как выбрано в тикете 3) — зафиксировать.
- Webhook **или** long polling для MVP.
- Маппинг update → `OutboundMessage` / отправка текста и фото.
- Ретраи и маскирование секретов в логах.

**Критерии приёмки:**
- Локально (со стеком из тикета 2) можно написать боту и получить ответ стуба `handle_inbound`.

**Зависимости:** тикеты 6, 8.

**Состояние кода (закрыто):** пакет **`mandala.adapters.telegram`**: long polling (**``python -m mandala.adapters.telegram``**), ``TELEGRAM_BOT_TOKEN`` + ``TELEGRAM_VERTICAL_ID`` из env (slug вертикали из seed); маппинг ``Update`` → ``InboundEvent`` (текст, caption, фото, документ, ``callback_query``), доставка ``OutboundMessage`` (**``sendMessage``** / **``sendPhoto``**, опционально inline-кнопки); HTTP через **httpx**, ретраи на 429/5xx, в логах **маскирование токена**. Юнит-тесты: **`tests/test_telegram_adapter.py`**. Приём webhook через HTTP — **тикет 10** (**`mandala.http`**). Несколько токенов бота → несколько ``vertical_id`` (таблица/конфиг) — **TODO в коде**, вне scope тикета 10.

---

## Тикет 10. HTTP-приложение (FastAPI): health и webhook

**Цель:** единая точка для Telegram и будущих каналов.

**Объём:**
- `GET /health` — проверка доступности **PostgreSQL** (простой запрос `SELECT 1` или проверка пула).
- `POST /webhooks/telegram/{vertical_id}` или один секретный путь + определение вертикали из заголовка/токена — согласовать и описать.
- Порт, секрет webhook.

**Критерии приёмки:**
- Health 200 при живой БД; webhook закрывает цикл ответа пользователю.

**Зависимости:** тикет 9.

**Состояние кода (закрыто):** пакет **`mandala.http`**: **`python -m mandala.http`** (uvicorn, по умолчанию **``HOST=0.0.0.0``**, **``PORT=8000``**); **`GET /health`** — ``SELECT 1`` через **`create_engine_from_env`** / пул SQLAlchemy; **`POST /webhooks/telegram/{vertical_id}`** — тело как ``Update`` Telegram, ``vertical_id`` из пути; при заданном **``TELEGRAM_WEBHOOK_SECRET``** сверка заголовка **``X-Telegram-Bot-Api-Secret-Token``** (как у ``setWebhook`` с ``secret_token``); цепочка **``telegram_update_to_inbound_event``** → **``handle_inbound(event, conn)``** → **``TelegramBotApiClient``** + **``deliver_outbound_messages``** (токен из env, MVP: один бот — ``TELEGRAM_VERTICAL_ID`` должен совпадать с ``{vertical_id}`` в пути). Зависимости в **`pyproject.toml`**: **fastapi**, **uvicorn**. Тесты: **`tests/test_http_app.py`**, интеграция: **``tests/integration/test_http_integration.py``**. Подробности — **README**, **docs/channels.md**.

---

## Тикет 11. Абстракция LLM: текстовый клиент и конфиг провайдеров

**Цель:** смена модели без переписывания агента; опционально разные дефолты per `vertical_id` из конфига.

**Объём:**
- Интерфейс `TextCompletionClient`, реализация под OpenAI-compatible API.
- Env: URL, ключ, модель по умолчанию; переопределения из конфига вертикали — по возможности.

**Критерии приёмки:**
- Интеграционный тест с моком HTTP или opt-in с реальным ключом.
- Ошибки API → доменное исключение.

**Зависимости:** тикет 1 (можно параллельно с 9–10 при разделении работ).

**Состояние кода (закрыто):** пакет **`mandala.llm`**: **`TextCompletionClient`** (Protocol), **`OpenAICompatibleTextClient`** (**httpx**, **`POST …/chat/completions`**), **`ChatMessage`** / **`ChatRole`**, **`LlmEnvSettings.from_env()`** (`LLM_BASE_URL`, **`LLM_API_KEY`**, **`LLM_MODEL`**), **`LlmConfigProvider`** + **`load_vertical_overrides`** (JSON per **`vertical_id`**, опционально **`LLM_VERTICAL_OVERRIDES_PATH`**, иначе **`mandala/llm/vertical_overrides.json`** в пакете), доменное исключение **`LlmProviderError`**. Тесты: **`tests/test_llm_openai_client.py`** (**`httpx.MockTransport`**), маркер **`llm_live`** + **`LLM_LIVE_TEST=1`** для опционального живого вызова — см. **README**.

---

## Тикет 12. Минимальный «агент»: один запрос — один ответ без графа

**Цель:** «сообщение → LLM → ответ» с **записью в Postgres**: вход пользователя и ответ ассистента в **`messages`**, при необходимости структура рекомендаций в **`generated_artifacts.payload` (JSONB)**.

**Объём:**
- Сохранить вход пользователя; квота `text_reply`; вызов LLM; сохранить ответ ассистента; `consume`.
- Системный промпт из конфига **вертикали** (заглушки разные для двух тестовых вертикалей — опционально).
- Исчерпание квоты → понятный `OutboundMessage`.

**Критерии приёмки:**
- E2E в Telegram: ответы до лимита free; в БД видны пары user/assistant и совпадение счётчиков usage.
- Сгенерированный текст читается из БД запросом (smoke).

**Зависимости:** тикеты 7, 8, 9, 10, 11.

**Состояние кода (закрыто):** **`mandala.services.text_reply.handle_inbound_text_llm`**: запись **user** в **`messages`**, **`QuotaService`** (**`can_consume`** / **`consume`**, ресурс **`text_reply`**), системный промпт **`mandala.verticals.get_vertical_system_prompt`** (разные строки для **`astrology`** и **`therapy`**), вызов **`mandala.llm.factory.create_text_client_for_vertical`** или переданного **`llm_client`**; ответ **assistant** в **`messages`**; **`handle_inbound(event, conn, llm_client=None)`** — опциональная подмена клиента для тестов. Интеграционные тесты: **`tests/integration/test_text_reply_quota.py`** (лист без LLM при исчерпании квоты); **`tests/integration/test_http_integration.py`** и **`tests/integration/test_user_identity.py`** обновлены под пайплайн. Локальный запуск бота/webhook требует **`LLM_*`** в окружении (см. **README**, `.env.example`). Полный E2E Telegram до лимита free — ручная проверка / смоук по **`messages`** и **`usage_counters`**.

---

## Тикет 13. Граф сценария: сбор полей профиля **по конфигу вертикали**

**Цель:** шаги сценария не захардкожены только под мандалу — задаются конфигом/реестром для вертикали (разные поля для астролога и психолога).

**Объём:**
- Состояние графа в `scenario_state`; список шагов и валидаторы из конфига `vertical_id`.
- Сохранение после каждого значимого шага в **`client_profiles`** (обновление **`scenario_state` / `agent_card` в JSONB**).

**Критерии приёмки:**
- Для двух тестовых вертикалей — разные цепочки вопросов (минимально: разный порядок или набор полей).
- Невалидный ввод не ломает состояние.

**Зависимости:** тикет 12.

**Состояние кода (закрыто):** цепочки шагов в JSON **`mandala/verticals/intake_steps.json`** (переопределение: **`MANDALA_INTAKE_STEPS_PATH`**); загрузка **`mandala.verticals.intake_loader`**, публичный API **`mandala.verticals.intake_config`** (`intake_steps_for_vertical`, кэш, **`clear_intake_steps_cache`**); валидаторы по полю **`validator.kind`** — **`mandala.verticals.intake_validators`**. Для **`astrology`** — `birth_place` → `birth_time`, для **`therapy`** — `main_concern` → `mood`. Сервис **`mandala.services.scenario_intake.handle_intake_before_llm`** (ключи **`scenario_state`**: `intake_step_index`, `intake_complete`, `intake_schema_version`); в **`handle_inbound`** сначала анкета, затем тикет 14+ (**`intent_router`** / **`text_reply`** / **`image_reply`**). Тесты: **`tests/integration/test_scenario_intake.py`**, **`tests/test_scenario_intake_logic.py`**, **`tests/test_intake_loader.py`**.

---

## Тикет 14. Роутер задач: текст vs изображение

**Цель:** как в исходном плане, с учётом лимитов и вертикали.

**Объём:**
- Явные команды и/или классификация; `ImageGenerationClient` с заглушкой.

**Критерии приёмки:**
- Лимит 0 → нет вызова image API; текстовая ветка не ломается.

**Зависимости:** тикеты 7, 11, 13.

**Состояние кода (закрыто):** **`mandala.services.intent_router`** (`post_intake_intent`, **`image_prompt_from_user_text`**): команды **`/image`**, **`/picture`**, префиксы **`нарисуй `**, **`draw `** → **`mandala.services.image_reply.handle_inbound_image_generation`** (квота **`RESOURCE_IMAGE_GENERATION`**, **`can_consume`** до вызова клиента; при лимите **0** или исчерпании — **без** вызова **`ImageGenerationClient.generate`**); при успехе — заглушка **`StubImageGenerationClient`**, запись в **`messages`** (**`content_kind=image`**), **`consume`**. Protocol **`ImageGenerationClient`**, **`ImageGenerationResult`**, фабрика **`create_stub_image_client_for_vertical`** в **`mandala.llm`**. **`handle_inbound(..., image_client=None)`** для тестов. Юнит-тесты: **`tests/test_intent_router.py`**, **`tests/test_image_stub.py`**; интеграция: **`tests/integration/test_image_router_quota.py`**.

---

## Тикет 15. Реальная генерация изображений и асинхронная доставка

**Цель:** генерация + **строки в Postgres**: `generated_artifacts` и/или `messages` с типом изображения и **JSONB-метаданными** (URL, `file_id`).

**Объём:**
- Подключение image API; `consume` при успехе; политика при ошибке — в коде и README.
- Очередь (Redis + worker) или «генерирую» + дозагрузка — выбрать MVP-вариант.

**Критерии приёмки:**
- Пользователь получает фото при ненулевом лимите; в БД есть запись об артефакте.

**Зависимости:** тикет 14.

**Состояние кода (закрыто):** **`mandala.llm`**: **`ImageEnvSettings`** (`IMAGE_GENERATION_PROVIDER` **`stub`** | **`openai_compatible`**, **`IMAGE_BASE_URL`** / **`IMAGE_API_KEY`** / **`IMAGE_MODEL`**, fallback на **`LLM_*`**), **`OpenAICompatibleImageClient`** (`POST …/images/generations`, **`response_format=url`**), **`create_image_client_for_vertical`**, **`StubImageGenerationClient`**; **`mandala.services.image_reply.handle_inbound_image_generation`**: **`can_consume`** до вызова API; **`consume`** и запись в **`messages`** + **`generated_artifacts`** (`kind=image`, JSONB **`payload`**: `image_url`, `provider`, `stub_ref`, `prompt_echo`) только после успеха; при **`LlmProviderError`** — сообщение пользователю без **`consume`**; при наличии **`image_url`** — **`OutboundMessage(photo=url)`** → Telegram **`sendPhoto`**. **MVP:** синхронная генерация в webhook/polling (без Redis/worker); таймаут чтения HTTP ~180 с — см. **README**, **`docs/architecture.md`**. Юнит-тест: **`tests/test_openai_image_client.py`**; интеграция: **`tests/integration/test_image_router_quota.py`**.

---

## Тикет 16. RAG: база знаний **per vertical** и поиск в промпте (**отдельное векторное хранилище**)

**Цель:** KB привязана к `vertical_id`; **векторный индекс не обязан жить в Postgres** — второй шаг после текстового пайплайна.

**Объём:**
- Сырьё: каталог в репо **на вертикаль** или объектное хранилище.
- **Эмбеддинги и поиск**: отдельный сервис — **Qdrant**, **pgvector** в том же Postgres, **Chroma** и т.д. — выбрать один для MVP, зафиксировать в README и `architecture.md`.
- CLI: `python -m mandala.index_kb --vertical …` (или согласованный модуль): чтение файлов → чанки → эмбеддинги → запись в **векторное хранилище** с тегом `vertical_id`.
- Перед вызовом LLM: retrieval + top-k в промпт; **изоляция**: чанки вертикали A не доступны запросу для B.

**Критерии приёмки:**
- Smoke-тест на изоляцию по `vertical_id`.
- Ограничение контекста документировано.

**Зависимости:** тикет 12 или 13.

**Состояние кода (закрыто):** MVP-векторное хранилище — **Qdrant** (`qdrant-client`), не в OLTP Postgres. Пакет **`mandala.rag`**: **`RagEnvSettings`**, **`OpenAICompatibleEmbeddingClient`** (`POST …/embeddings`), **`QdrantVerticalKbStore`** (payload **`vertical_id`**, фильтр при **`query_points`**), **`create_kb_search_from_env`**, **`KbSearchPort`**, чанкинг и **`build_kb_context_block`** (лимит **`RAG_MAX_CONTEXT_CHARS`**). Сырьё по умолчанию: **`src/mandala/verticals/kb/{vertical_id}/`** (`*.md`, `*.txt`), корень переопределяется **`MANDALA_KB_ROOT`**. CLI: **`python -m mandala.index_kb --vertical <slug>`** (нужны **`MANDALA_RAG_BACKEND=qdrant`**, **`QDRANT_URL`**, **`LLM_*`** для эмбеддингов). В **`mandala.services.text_reply.handle_inbound_text_llm`** перед LLM — retrieval и дополнение **системного** промпта; **`handle_inbound(..., kb_search=None)`** прокидывает опциональную подмену для тестов. Юнит-тест изоляции: **`tests/test_rag_vertical_isolation.py`**. Локально Qdrant — сервис в **`compose.yaml`** (порт **`QDRANT_PORT`**, по умолчанию **6333**). Таблица **`kb_documents`** в Postgres — **не** введена в этом тикете (метаданные в payload Qdrant достаточно для MVP).

---

## Тикет 17. Память диалога: история и summary (опционально для MVP)

**Цель:** последние N строк из **`messages`** (+ опционально summary в JSONB профиля).

**Объём:** как в исходной идее, на таблицах Postgres.

**Критерии приёмки:** предсказуемая обрезка по N; значение N в доке.

**Зависимости:** тикет 12.

**Состояние кода (закрыто):** **`mandala.services.text_reply`**: константа **`TEXT_REPLY_CONTEXT_MESSAGES`** (= **20**), выборка **`MessageRepository.list_recent`** после записи входа пользователя, сбор **`ChatMessage`** (роли **`user`**/**`assistant`**, пустой **`content_text`** пропускается); порядок контекста: системный промпт вертикали → KB (RAG) → опционально **`dialog_summary`** из **`scenario_state`** → история; **`handle_inbound`** передаёт сводку в **`handle_inbound_text_llm`**. Юнит-тест: **`tests/test_text_reply_dialog_context.py`**; интеграция: **`tests/integration/test_text_reply_dialog_memory.py`**. Связка **`RAG_MAX_CONTEXT_CHARS`** / **`max_tokens`** / история — **`docs/agent.md`**, **README**.

---

## Тикет 18. Биллинг: интерфейс BillingProvider

**Цель:** изоляция платежей; активация плана — **UPDATE строк** `users` / `payment_transactions` в Postgres.

**Объём:**
- `BillingProvider`, `activate_plan` с идемпотентностью по `external_id`.

**Критерии приёмки:**
- Юнит-тесты с фейком; повтор активации не дублирует эффект.

**Зависимости:** тикеты 3–5 (можно параллельно с 9 при разделении).

**Состояние кода (закрыто):** Protocol **`mandala.services.billing.BillingProvider`**, реализация **`PostgresBillingProvider.activate_plan`**, **`ActivatePlanResult`**; репозиторий **`mandala.repositories.payments.PaymentTransactionsRepository`** (вставка со **`ON CONFLICT DO NOTHING RETURNING`** по **``uq_payment_provider_external_id``**); **`UsersRepository.update_current_plan`**. Юнит-тесты: **`tests/test_billing_provider.py`**; интеграция: **`tests/integration/test_billing_activate_plan.py`** (при **`DATABASE_URL`**). Единая политика **``apply_plan_change``** (сброс usage, период подписки, Telegram Stars) — **тикет 19**; при **``user_mismatch``** после вставки платежа вызывающий код должен откатить транзакцию.

---

## Тикет 19. Telegram Stars и планы

**Цель:** как в исходном плане; товары Stars ↔ **`plans.external_product_id`** в **Postgres**.

**Критерии приёмки:** смена плана и лимитов в БД; идемпотентность платежа.

**Зависимости:** тикеты 9, 18.

**Состояние кода (закрыто):** миграция **`t19_01_telegram_stars`**: у плана **``premium``** — **`billing_provider=telegram_stars`**, **`external_product_id=mandala_premium_stars`** (тот же **``invoice_payload``** в **``sendInvoice``** / ссылке на товар). **`PlansRepository.fetch_id_by_billing_product`**. **`mandala.services.billing`**: **`BILLING_PROVIDER_TELEGRAM_STARS`**, **`STARS_PLAN_SUBSCRIPTION_DAYS`**, **`apply_plan_change`** (сброс **``usage``** за текущий месяц UTC, **``subscription_period_end``**). **`mandala.services.telegram_stars`**: **`handle_pre_checkout_query`**, **`handle_successful_payment`** (идемпотентность по **``telegram_payment_charge_id``**). **`mandala.adapters.telegram.billing_updates.process_telegram_billing_update`**; **``TelegramBotApiClient.answer_pre_checkout_query``**; long polling и **`mandala.http`**: сначала ветка оплаты; для **оплаты** в webhook требуется токен бота. **`UsageRepository.reset_counts_for_user_vertical_period`**, **`UsersRepository.set_subscription_period_end`**. Интеграция: **`tests/integration/test_telegram_stars_billing.py`**. **TODO: тикет 21+** — продуктовый UI / **`create_payment_offer`**.

---

## Тикет 20. Наблюдаемость и операционные логи

**Цель:** воронка `inbound → quota → llm → outbound` с **`vertical_id`** в полях лога; без сырого PII в info.

**Зависимости:** тикеты 10, 12.

**Состояние кода (закрыто):** модуль **`mandala.observability`**: **`op_format`** (единый порядок полей: **`vertical_id`**, **`user_id`**, **`channel`**, **`stage`**, **`intent`**, **`resource`**, **`outcome`**, **`reason`**, **`update_id`**, счётчики доставки и т.д.), **`mask_api_key`** для ключей провайдеров; токен бота по-прежнему **`mask_bot_token`** в **`mandala.adapters.telegram.secrets`**. Логи с префиксом **`funnel`** на **INFO**: **`handle_inbound`** (identity, intake, route), **`QuotaService`** (**`can_consume`** / **`consume`** — отказы и **DEBUG** при успешном consume), **`text_reply`** / **`image_reply`** (старт/успех LLM, длина ответа в символах без текста), **`deliver_outbound_messages`** (при **`vertical_id`**), **`mandala.http`** (webhook received / ignored / delivered), **`process_telegram_billing_update`** (pre_checkout / successful_payment), **`polling.process_telegram_update`**. Биллинг: **`PostgresBillingProvider.activate_plan`**, **`apply_plan_change`** с прокидыванием **`reason`** (сценарии Stars и далее). Юнит-тесты: **`tests/test_observability.py`**. OpenTelemetry / метрики — **TODO: за пределами тикета 20**.

---

## Тикет 21. Канал Web (без UI): тот же `handle_inbound`

**Цель:** мультиканальность; в запросе передаётся **`vertical_id`** (или API-ключ, мапящийся на неё).

**Критерии приёмки:** та же логика, что Telegram; OpenAPI или `docs/`.

**Зависимости:** тикеты 6, 8, 12.

**Состояние кода (закрыто):** **`POST /webhooks/web`** — **`mandala.http.web_chat`** (`APIRouter`), общий engine — **`mandala.http.engine_access.get_engine`** (без циклического импорта с **`mandala.http.app`**). Маппинг HTTP → **`InboundEvent`** (**`channel=web`**) — **`mandala.adapters.web.inbound_map`** (**`resolve_web_vertical_id`**, **`inbound_event_from_web`**): **`vertical_id`** из JSON-тела или **`X-Vertical-Id`**; **`external_user_id`** — заголовок **`X-External-User-Id`** (MVP). Заголовок **`Authorization`** зарезервирован под будущий маппинг ключа → вертикаль (**TODO** в коде). Цепочка: **`handle_inbound(event, conn)`** → JSON **`WebChatResponse.messages`**: список **`OutboundMessage`**. Логи: **`funnel web_inbound`**, **`op_format`**, без текста сообщения на INFO. Юнит-тесты: **`tests/test_web_inbound_map.py`**, **`tests/test_http_app.py`** (web); интеграция с БД: **`tests/integration/test_http_integration.py`** (**`test_web_chat_with_real_database`**). Документация: **`docs/channels.md`**, **`docs/architecture.md`**, **README**.

---

## Тикет 22. CI: линт, тесты, Postgres и Alembic

**Цель:** зелёный pipeline на чистой ветке.

**Объём:**
- GitHub Actions: установка зависимостей, линт, тесты, **service container PostgreSQL**, **`alembic upgrade head`** (или эквивалент прогона миграций).

**Критерии приёмки:**
- Падающий линт, тест или миграция блокирует merge.

**Зависимости:** тикеты 1–4 минимум.

**Состояние кода (закрыто):** workflow **`.github/workflows/ci.yml`**: **`uv sync --extra dev --frozen`**, **`UV_PYTHON=3.11`**, **`ruff check`** / **`ruff format --check`**, **`mypy`**, сервис **PostgreSQL** **`docker.io/library/postgres:16.6-alpine`** (пользователь **`mandala`**, пароль **`changeme`**, БД **`mandala`**, порт **5432** на раннере), переменная **`DATABASE_URL=postgresql://mandala:changeme@localhost:5432/mandala`** (как в **`.env.example`**), **`scripts/check_postgres.py`**, **`alembic upgrade head`**, **`pytest`**. Триггеры: **`push`** в **`main`** / **`master`**, все **`pull_request`**. **`scripts/verify_project.sh`**: при **`DATABASE_URL`** сначала **Postgres + Alembic**, затем **`check.sh`** (интеграционные тесты не гоняются до миграций). Миграция **`t19_01_telegram_stars_plan_link`**: вызов **`op.execute`** через **`sa.text(...).bindparams(...)`** (совместимость с Alembic 1.18+). Подробности и форки — **README**.

---

## Тикет 23. Деплой MVP и задел под Yandex Cloud (Terraform)

**Цель:** контейнер приложения + понятный выклад; облако — воспроизводимо инфраструктурой как код.

**Целевая схема MVP (YC, согласовано):** опора на уже существующий контур **n8n** в каталоге облака (VM **`n8n-server`**, Managed PostgreSQL **`n8n-postgres`**, VPC **`n8n-network`**, публичная DNS-зона **`mandala-app.online`**, security group только **22 / 80 / 443**). **Приложение Mandala** — отдельный контейнер из **Containerfile** на **той же VM**, каталог на диске (например **`/opt/mandala`**), запуск через **systemd** или отдельный compose-стек; **`mandala.http`** слушает **127.0.0.1:8000** (или эквивалент), **порт приложения наружу не открывать**. **HTTPS и webhook Telegram:** новый поддомен в **`mandala-app.online`** (например **`api.mandala-app.online`**) → на существующем **Nginx** на VM — **новый `server`**, reverse proxy на **`http://127.0.0.1:8000`** (пути **`/health`**, **`/webhooks/…`**), TLS по тому же принципу, что у n8n (Let’s Encrypt / certbot). **БД:** в кластере **`n8n-postgres`** создать **отдельную БД и пользователя** только для Mandala; отдельный кластер под Mandala в MVP **не** обязателен. **Terraform в репозитории Mandala:** свой root (**`terraform/`** или **`infra/terraform/`**) и **свой `terraform.tfstate`**, без смешения с репозиторием n8n; существующие ресурсы n8n **не пересоздавать** — в коде **`data`** по id / подсети / сети или при необходимости **`import`**; первый **`apply`** по смыслу **аддитивный** (например **DNS** через **`yandex_dns_recordset`** на публичный IP VM). **Запрещено в MVP без осознанного плана:** общий **`destroy`**, слепое изменение сети/кластера без импорта, открытие лишних портов в SG вместо прокси через **443**.

**Политика CI vs деплой (уточнение к тикету 22):** **GitHub Actions** остаётся **только** контролем качества (линт, типы, тесты, миграции в CI — тикет 22). **Автоматической выкладки из GitHub в Yandex Cloud в MVP не делаем** — деплой выполняет человек **скриптами из репозитория** (обёртки над **`terraform`** / **`yc`**), секреты и локальные tfvars — **вне git** (см. `.gitignore`, в репо — только **`.example`**).

**Два контура обновления:**
- **Инфра — редко:** `terraform plan` / `apply` (сеть, БД, блокировки, новые ресурсы); допускается **bring-your-own** уже существующие ВМ и Managed PostgreSQL — описать в доке импорт / `data` sources, чтобы не пересоздавать прод случайно.
- **Образ / сервис приложения — часто:** отдельный сценарий (сборка **Containerfile**, доставка на ВМ, перезапуск **`mandala.http`** / uvicorn, при необходимости **`alembic upgrade head`**); без полного `apply` инфры на каждый релиз.

**Terraform state (MVP vs будущее):** в MVP **state у одного оператора локально** (файл или согласованное хранилище вне репозитория; **`terraform.tfstate` и чувствительные артефакты — не коммитить**). **В плане зафиксировано на будущее:** перенести backend на **удалённый state в Yandex Object Storage** (S3-совместимый бэкенд + блокировки по документации Yandex / Terraform provider) — как только появится второй человек с `apply` или нужна воспроизводимость с другой машины.

**Объём:**
- **Containerfile** (или Dockerfile) и сборка **`podman build`** для приложения; prod-env переменные.
- Инструкция: webhook Telegram на HTTPS.
- **`terraform/`** (или **`infra/terraform/`** — одно согласованное имя в репо) + **скрипты деплоя** в **`scripts/`** (или под **`infra/scripts/`**): первичная выкладка, обновление инфры, обновление приложения (см. два контура выше).
- Минимальный скелет Terraform под **Yandex Cloud** (VPC, **Managed PostgreSQL** при необходимости нового окружения, секреты **Lockbox** / аналог; при появлении RAG в проде — отдельный хост или managed векторный сервис); в README — что **«полный» автоматический прод через Terraform** (все сервисы, политики, мониторинг) — **следующий этап** после MVP-скелета.
- Бэкапы: ссылка на **Yandex Managed Service for PostgreSQL** (резервные копии / PITR).

**Критерии приёмки:**
- Локально/на одном хосте по инструкции поднимается бот с реальным токеном.
- Секреты только через env / Lockbox / аналог (задокументировать целевой путь для Yandex); в git не попадают.

**Зависимости:** тикеты 10, 22.

**Состояние кода (закрыто):** **`Containerfile`** (в builder: **`uv sync … --no-editable`** — пакет в **`.venv`** без монтирования **`src`** в runtime); **`.dockerignore`**; **`pyproject.toml`** — extra **`deploy`**; **`uv.lock`**; **`terraform/`** (DNS **A**, **`terraform.tfvars.example`**, **`versions.tf`** `>= 1.5`, **`README.md`** с **`YC_TOKEN`**); **`terraform/.terraform.lock.hcl`**; **`scripts/deploy/`** (**`build_image.sh`** с **`MANDALA_PLATFORM`**, **`README.md`**, **`nginx-mandala-api.conf.example`**). **Прод (выполнено):** в кластере **`n8n-postgres`** — пользователь **`mandala_app`**, БД **`mandala`**, **`DATABASE_URL`** с **`sslmode=require`** на ВМ в **`/opt/mandala/env`** (локально — **`.local/`**, в git не коммитить); **`terraform apply`** — **A `api` → IP ВМ**; **Nginx** + **certbot** для **`api.mandala-app.online`**; контейнер **`mandala-http`** (**`docker run`** с **`-p 8000:8000`**, миграции **`alembic upgrade head`**). **`bash scripts/check.sh`** — зелёный.

**Связь с тикетом 24:** перенос **Terraform state** в **Object Storage** и доведение секретов до **Lockbox** / ролей сервисных аккаунтов — **тикет 24** (после MVP-скелета из этого тикета).

---

## Тикет 24. Команда и прод-гигиена деплоя: remote state, Lockbox, доступы

**Цель:** несколько людей или несколько рабочих машин могут **безопасно и предсказуемо** работать с инфраструктурой в Yandex Cloud; секреты не «вечным файлом на диске», state не теряется и не коммитится.

**Объём:**
- **Terraform remote backend** в **Yandex Object Storage** (S3-совместимый backend + **блокировки state** по официальной схеме для провайдера **`yandex`** / Terraform): создание бакета, сервисный аккаунт с **минимальными** правами на бакет, **`backend.tf`** / фрагмент конфигурации, инструкция **миграции** с локального `terraform.tfstate` из тикета 23.
- **Yandex Lockbox** (или согласованный аналог): хранение значений **`DATABASE_URL`**, **`TELEGRAM_BOT_TOKEN`**, **`LLM_API_KEY`** и др.; **документированный** путь выдачи на ВМ при старте (например **`yc lockbox`**, агент, **systemd** с подстановкой из смонтированного секрета — выбрать один MVP-поток и описать в README / `docs/`).
- **Сервисные аккаунты и роли:** кто читает state, кто деплоит образ, кто читает Lockbox — таблица в доке; запрет широких **`editor`** на прод без необходимости.
- Обновить **`.gitignore`** / чеклист в README: **`*.tfstate`**, **`*.tfstate.*`**, локальные **`.tfvars`** с секретами, ключи **`yc`**, артефакты `terraform apply` — не в git; при необходимости пример **`terraform.tfvars.example`** без секретов.

**Критерии приёмки:**
- По документации второй участник (или вторая машина) выполняет **`terraform init`** с remote backend и получает согласованный **`plan`** без ручного копирования state.
- Секреты приложения в репозитории не появляются; путь ротации ключа / секрета описан (хотя бы чеклист).
- В README или `docs/` есть ссылка на документацию YC по **Lockbox** и по **хранению Terraform state** в Object Storage (полные URL).

**Зависимости:** тикет 23 (скелет Terraform и скрипты деплоя из MVP).

**Вне scope:** автоматический deploy из GitHub Actions в YC (по-прежнему не цель); полный мониторинг и алерты — **TODO: за пределами тикета 24** или отдельный тикет позже.

---

## Параллельность и порядок крупными блоками

1. **Фундамент:** 1 → 2 → 3 → 4 → 5.  
2. **Контракты и квоты:** 6 → 7 → 8.  
3. **Telegram + HTTP:** 9 → 10.  
4. **LLM и сквозной сценарий:** 11 → 12 → 13 → 17 (17 после 12).  
5. **Картинки:** 14 → 15.  
6. **Знания per vertical + вектор (отдельно от ядра Postgres):** 16 (после стабильного LLM-пайплайна).  
7. **Деньги:** 18 → 19.  
8. **Качество, Web, CI, облако:** 20 → 21 → 22 → 23 → 24.

Тикеты **11** и **18** можно параллелить с **9** при разделении команды; **16** логично после **13**.

---

## Как отдавать тикет ИИ-агенту

В начале промпта вставлять:

- номер и название тикета;
- этот файл с одним выделенным разделом (скопировать блок тикета целиком);
- ссылку на соответствующие `docs/*.md`;
- ограничение: «не менять объём тикета; вне scope — только TODO-комментарии».

Так исполнитель не разъезжает по всему продукту и закрывает один вертикальный срез.
