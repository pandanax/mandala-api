# Тикет: полноценный UI с командами и inline-кнопками (Вариант C)

> **Статус**: открыт. Создаётся как продолжение быстрого UX-патча, который уже задеплоен
> (см. [`src/mandala/services/scenario_intake.py`](../../src/mandala/services/scenario_intake.py:1):
> функции [`_extract_command()`](../../src/mandala/services/scenario_intake.py:178)
> и [`_handle_command()`](../../src/mandala/services/scenario_intake.py:215)).
>
> Этот тикет — про полноценный UX: меню команд в Telegram, inline-кнопки,
> описание бота, индивидуальные приветствия по вертикали (из БД), паритет в Web-канале.

## 1. Цель

Сейчас (после быстрого патча):
- `/start` сбрасывает состояние и шлёт приветствие + первый шаг анкеты.
- Произвольный текст после `/start` парсится как ответ на текущий шаг анкеты.
- Inline-кнопок нет, callback-ов нет, меню команд в Telegram нет.

Хочется, чтобы:
- В Telegram был виден список команд (`setMyCommands`) и описание бота (`setMyDescription`).
- Поверх анкеты и в свободном диалоге работали inline-кнопки
  (быстрые действия: «Начать заново», «Что ты умеешь», «Тарифы», «Скачать карту»).
- Тексты приветствия/about/CTA жили в БД (`agent_verticals.config`) и редактировались без релиза.
- Web-канал поддерживал тот же протокол (команды + кнопки), чтобы UI на сайте/виджете строил
  кнопки из ответа бэкенда.

## 2. Что нужно сделать

### 2.1. Inbound: парсинг `callback_query` (Telegram)

Файл: [`src/mandala/adapters/telegram/inbound_map.py`](../../src/mandala/adapters/telegram/inbound_map.py:1).

- Добавить ветку для `update.callback_query`: вычитать `data`, `from`, `message`.
- Заполнить [`InboundEvent.callback_data`](../../src/mandala/domain/contracts.py:63)
  и `text=None`. Поле уже есть в контракте.
- Обновить тесты в [`tests/test_telegram_adapter.py`](../../tests/test_telegram_adapter.py:1).

Также сразу: в [`src/mandala/adapters/telegram/outbound_send.py`](../../src/mandala/adapters/telegram/outbound_send.py:1)
после отправки колбэка вызывать `answerCallbackQuery`, чтобы у пользователя пропадал «часики»-индикатор.

### 2.2. Domain: маршрутизация команд и колбэков

Файл: [`src/mandala/domain/handler.py`](../../src/mandala/domain/handler.py:1).

- До `handle_intake_before_llm` (или внутри него) обработать `event.callback_data`:
  парсить как `<action>:<arg>` и роутить в один из обработчиков (reset/help/about/plans/...).
- Команды (`/cmd`) уже распознаются в анкете; нужно расширить, чтобы они работали и
  **после** анкеты (сейчас часть веток требует пройденную анкету).
- Команды, которые имеют смысл после анкеты: `/help`, `/about`, `/plans`, `/start` (сброс).

### 2.3. Outbound: inline_keyboard в Telegram

Файл: [`src/mandala/adapters/telegram/outbound_send.py`](../../src/mandala/adapters/telegram/outbound_send.py:1).

- Контракт уже допускает [`OutboundMessage.buttons`](../../src/mandala/domain/contracts.py:91)
  как `list[list[dict[str, str]]]`. Нужно сериализовать в Telegram
  `reply_markup={"inline_keyboard": [[{"text": ..., "callback_data": ...}]]}`.
- В словаре кнопки: `text` (обязательно), `callback_data` (для action) **или** `url`.
- Покрыть в [`tests/test_telegram_adapter.py`](../../tests/test_telegram_adapter.py:1):
  - кнопка с `callback_data` сериализуется правильно;
  - кнопка с `url` сериализуется правильно;
  - сообщение без кнопок не передаёт `reply_markup`.

### 2.4. Per-vertical контент в БД

Сейчас приветствие зашито в коде ([`_vertical_greeting`](../../src/mandala/services/scenario_intake.py:148)).

- Расширить `agent_verticals.config` (JSONB) полями:
  ```json
  {
    "ui": {
      "welcome_text": "...",
      "about_text": "...",
      "post_intake_cta": "...",
      "default_buttons": [
        [{"text": "Что ты умеешь", "callback_data": "help"}],
        [{"text": "Тарифы",         "callback_data": "plans"}]
      ]
    }
  }
  ```
- Добавить `VerticalConfigRepository` (или метод в существующий репозиторий) с кэшем
  на процесс.
- Обновить миграцию-сид [`alembic/versions/t3_seed_02_plans_and_verticals.py`](../../alembic/versions/t3_seed_02_plans_and_verticals.py:1)
  или сделать новый файл миграции, чтобы прокинуть UI-тексты для `astrology` и `therapy`.
- В `_handle_command` / `handle_intake_before_llm` подставлять тексты из конфига,
  с фоллбэком на текущие зашитые в коде строки.

### 2.5. Регистрация команд и описания бота в Telegram

Команды и описание ставятся **один раз** на стороне Telegram через Bot API:
- `POST /bot<TOKEN>/setMyCommands` — список команд для меню «/».
- `POST /bot<TOKEN>/setMyDescription` — текст в карточке бота.
- `POST /bot<TOKEN>/setMyShortDescription` — короткая строка.

Реализация:
- Скрипт [`scripts/deploy/register_telegram_ui.sh`](../../scripts/deploy/register_telegram_ui.sh:1) (новый),
  принимающий `TELEGRAM_BOT_TOKEN` и `TELEGRAM_VERTICAL_ID` из env, делающий 3 запроса.
- Команды и тексты — те же, что в `agent_verticals.config.ui`.
- Документация: добавить шаг в `docs/deployment-yandex-cloud.md` §12 (первый запуск).

### 2.6. Web-канал: паритет

Файл: [`src/mandala/adapters/web/inbound_map.py`](../../src/mandala/adapters/web/inbound_map.py:1).

- Принимать `callback_data` из тела JSON.
- В ответе `/webhooks/web` уже возвращаем `OutboundMessage`-ы; нужно убедиться, что `buttons`
  попадают в JSON ответа и фронт может их рендерить.
- Тест в [`tests/test_web_inbound_map.py`](../../tests/test_web_inbound_map.py:1): кнопка с
  `callback_data` приходит обратно в ответе.

## 3. Тесты

- `tests/test_scenario_intake_logic.py` — добавить кейс на роутинг callback_data.
- `tests/test_telegram_adapter.py` — inbound (callback_query) и outbound (inline_keyboard,
  answerCallbackQuery).
- `tests/test_web_inbound_map.py` — callback_data round-trip.
- `tests/integration/test_scenario_intake.py` — `/start` после завершения анкеты сбрасывает
  состояние; `/help` после анкеты не сбрасывает; кнопка «Начать заново» эквивалентна `/start`.

## 4. Риски / открытые вопросы

- Совместимость со старым forward-компонентом: `OutboundMessage.buttons` уже есть в контракте,
  но в коде ещё нигде не задействован — нужно проверить, что в адаптере Telegram отправка
  сейчас не падает, если поле `None`.
- В Web-канале ответ уже сериализуется через Pydantic — `buttons` там автоматически попадёт,
  но клиент должен уметь его парсить (отдельный фронт-тикет).
- Список команд должен совпадать в трёх местах: `_RESET_COMMANDS`/`_INFO_COMMANDS` в коде,
  `setMyCommands` в Telegram и `default_buttons` в `agent_verticals.config`. Нужно зафиксировать
  единый источник правды (предлагаю — БД, остальные читают из неё).

## 5. Оценка

- Inbound `callback_query` + тесты: 30–45 мин.
- Outbound `inline_keyboard` + `answerCallbackQuery` + тесты: 45–60 мин.
- `agent_verticals.config.ui` + миграция + репозиторий: 1–1.5 часа.
- Routing команд/колбэков в `domain/handler.py` + тесты: 1–1.5 часа.
- Скрипт `setMyCommands`/описания + докуменация: 30 мин.
- Web-канал паритет + тест: 30 мин.

**Итого:** 4–6 часов работы + smoke-test на проде (через `restart_app.sh`).

## 6. Definition of Done

- В Telegram у бота видно меню команд и описание.
- `/start`, `/help`, `/about` работают в любой момент диалога (до и после анкеты).
- Под сообщением бота появляются inline-кнопки (минимум: «Начать заново», «Что ты умеешь»).
- Нажатие на кнопку обрабатывается и отвечает в том же чате.
- Тексты приветствия/about можно сменить без релиза, через UPDATE в `agent_verticals.config`.
- Web-канал отдаёт `buttons` в JSON, принимает `callback_data` в запросе.
- `bash scripts/check.sh` зелёный.
