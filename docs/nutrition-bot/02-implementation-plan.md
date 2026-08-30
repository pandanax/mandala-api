# План реализации вертикали `nutrition`

## 1. Продуктовая рамка MVP

Бот помогает совершеннолетнему пользователю улучшать питание и устойчиво менять привычки:

- уточняет цель: общее качество питания, снижение/поддержание/набор веса, режим, идеи блюд;
- учитывает предпочтения, аллергию/непереносимость, бюджет, доступные продукты, готовку и режим;
- предлагает небольшие конкретные действия, меню-конструктор и список покупок;
- ведёт короткий check-in по соблюдаемости, голоду/сытости, энергии и препятствиям;
- объясняет принципы питания, не обещает быстрых результатов и не стыдит за вес или срывы.

Не входят в MVP: распознавание еды по фото, интеграции с трекерами, лабораторные анализы,
назначение БАДов/лекарств, лечебные диеты, работа с детьми и ведение беременности. Калорийность и
макронутриенты допускаются только как прозрачная ориентировочная оценка после safety-screening,
с показом исходных данных и без маскировки результата под медицинское назначение.

## 2. Safety-контур

Нутрициология — health-сценарий, поэтому одного disclaimer в промпте недостаточно. Safety должен
состоять из детерминированных правил до LLM, инструкций модели, проверенного KB-контента и тестов.

### До персонализации

В intake задать минимально необходимые вопросы:

1. имя или обращение;
2. подтверждение возраста 18+ (не полная дата рождения, если она не нужна продукту);
3. цель и желаемый горизонт;
4. рост и текущий вес — опционально, только если цель связана с весом;
5. ограничения: аллергии/непереносимости, религиозные и этические предпочтения;
6. состояния, требующие специалиста: беременность/лактация, диабет, заболевания ЖКТ/почек/
   печени, приём препаратов, послеоперационное состояние;
7. признаки/история РПП и текущая профессиональная помощь;
8. бытовой контекст: бюджет, время на готовку, число приёмов пищи, доступность продуктов.

Не собирать адрес, документы, результаты анализов и подробный диагноз. Не использовать один
универсальный `min_len`: добавить типизированные validators/options и нормализованное значение для
возраста, роста/веса, enum-выборов и `skip/не хочу отвечать`.

### Детерминированный triage

Создать `services/nutrition_safety.py`, вызываемый после подтверждения intake и перед каждым
nutrition LLM-turn. Он возвращает `allowed`, `limited` или `refer` плюс безопасный шаблон ответа.

- `refer`: несовершеннолетний; беременность/лактация; подозрение на РПП; тяжёлые симптомы;
  запрос на изменение назначений; лечебная диета при значимом заболевании.
- `limited`: аллергии, хронические состояния или лекарства — только общая образовательная
  информация, никаких персональных норм/дефицита; рекомендация обсудить план с врачом/диетологом.
- `allowed`: общий wellness взрослого без заявленных красных флагов.

Запросы на экстремально быстрое похудение, голодание, очищение, рвоту/слабительные, опасное
ограничение жидкости или самостоятельное применение рецептурных средств всегда перехватываются
до модели. Экстренные симптомы получают ясный совет обратиться за срочной медицинской помощью,
без попытки диагностировать.

Опорные продуктовые принципы: WHO описывает здоровый рацион через достаточность, баланс,
умеренность и разнообразие; NIDDK рекомендует научно обоснованный, реалистичный и адаптированный
к здоровью и предпочтениям план и отдельно предупреждает о программах с чрезмерными обещаниями.
NHS отмечает, что очень низкокалорийные диеты подходят не всем и требуют профессиональной оценки.
Источники: [WHO Healthy diet](https://www.who.int/news-room/fact-sheets/detail/healthy-diet),
[NIDDK Safe & Successful Weight-loss Program](https://www.niddk.nih.gov/health-information/weight-management/choosing-a-safe-successful-weight-loss-program),
[NHS Overweight and obesity](https://www.nhs.uk/conditions/overweight-and-obesity/).

До production тексты triage, intake и 20–30 эталонных ответов должен просмотреть практикующий
диетолог/врач. В коде и интерфейсе нельзя называть бота врачом или заменой консультации.

## 3. Целевая архитектура vertical-конфигурации

Добавить типизированный реестр, например `verticals/registry.py`:

```python
@dataclass(frozen=True)
class VerticalDefinition:
    slug: str
    system_prompt: str
    commands: tuple[BotCommand, ...]
    capabilities: frozenset[str]
    greeting: str
    help_text: str
    fallback_nav: ButtonRows
    profile_renderer: ProfileRenderer
    completion_builder: CompletionBuilder
    allowed_llm_profile_keys: frozenset[str]
```

Реестр содержит `astrology`, `therapy`, `nutrition`. Сначала перенести текущее поведение
astrology без изменения текстов и callback-кодов, закрепив characterization-тестами. После этого
подключить nutrition. Хранить safety-логику и валидаторы в Python; JSON оставлять для декларативной
последовательности полей. Не переносить исполняемые правила в `agent_verticals.config`.

Capabilities первого релиза:

- `text_chat`, `rag`, `profile`, `message_wallet`, `telegram_stars` — включены;
- `astrology_tools`, `daily_forecast`, `image_generation` — выключены;
- будущие `meal_plan`, `check_in`, `food_photo` заводятся отдельными флагами.

Production webhook принимает только active vertical, присутствующий одновременно в registry,
`agent_verticals` и token map. Несовпадение — startup warning/error либо HTTP 404 до создания
пользователя.

## 4. Контент вертикали

### System prompt

Добавить отдельный prompt, который:

- отвечает по-русски, конкретно и доброжелательно, без морализаторства;
- различает образование, wellness-рекомендацию и медицинское назначение;
- никогда не придумывает аллергию, диагноз, показатели или съеденную пищу;
- явно маркирует расчётные оценки и задаёт уточнение при недостатке данных;
- не рекомендует рецептурные препараты, дозы БАДов и отмену назначений;
- не продвигает продукты/бренды и не обещает локальное или быстрое похудение;
- выдаёт короткий ответ плюс 2–4 контекстные nav-кнопки и 0–3 действительно полезных термина;
- при конфликте с детерминированным safety-контекстом следует более строгому ограничению.

### KB

Создать `verticals/kb/nutrition/` с файлами по темам, у каждого — дата пересмотра, автор/ревьюер,
первичные или официальные источники и применимость:

- основы сбалансированного рациона и пищевой безопасности;
- планирование порций/приёмов пищи без жёсткого меню;
- белки, жиры, углеводы, клетчатка и вода;
- снижение/поддержание/набор веса и поддержание результата;
- бюджет, закупка, приготовление и замены продуктов;
- вегетарианские и культурные паттерны;
- аллергия/непереносимость: только границы и направление к специалисту;
- РПП, беременность, хронические болезни, препараты/БАДы: safety-only документы;
- словарь терминов и список запрещённых/сомнительных обещаний.

Не копировать коммерческие статьи. Индексация выполняется с `--vertical nutrition`; smoke-тест
доказывает, что запрос nutrition не получает chunks astrology и наоборот.

### Детерминированный UX

Команды MVP:

- `/start` — старт/меню без сброса заполненного профиля;
- `/profile` — просмотр и редактирование nutrition-профиля;
- `/plan` — план следующего небольшого шага или дня;
- `/checkin` — короткая отметка прогресса и препятствий;
- `/help`, `/reset`, `/promo`, `/topup` — общие сервисные команды с nutrition-текстами.

Не регистрировать `/natal`, `/matrix`, `/numerology`, `/forecast`, `/morning`. Callback namespace
сделать `mdl_nut:*` либо ввести общий структурированный `mdl:<vertical>:<action>` с поддержкой
legacy astrology-кодов; callback должен проверяться против текущего vertical.

## 5. План реализации по этапам

### Этап 0 — зафиксировать baseline астролога

- Запустить полный `bash scripts/check.sh` на `main` и сохранить результат.
- Добавить characterization-тесты текущих команд, greeting/help/profile/completion, nav и
  daily forecast astrology.
- Снять безопасный production smoke baseline: `/health`, `/help`, `/natal` с тестовым аккаунтом,
  callback, баланс и факт утренней рассылки; секреты и PII не сохранять в Git.

Критерий: тесты воспроизводят текущее поведение до рефакторинга.

### Этап 1 — закрыть multi-tenant протечки без изменения astrology UX

- Ввести `VerticalDefinition` и перенести существующие astrology/therapy определения.
- Сделать command extraction, special handlers, nav fallback, profile, completion, image intent,
  daily forecast settings и LLM profile patch capability-aware.
- Переписать `setMyCommands`: пройти по `load_bot_token_map()` и поставить свой список каждому
  токену. Telegram поддерживает отдельный вызов `setMyCommands` для каждого bot token:
  [Bot API](https://core.telegram.org/bots/api#setmycommands).
- Добавить per-vertical webhook secret resolver с legacy fallback.
- Валидировать token → registry → active DB vertical на startup/request boundary.

Критерий: все старые astrology-тесты и baseline проходят без изменения пользовательских текстов;
новые negative tests доказывают, что therapy/unknown vertical не запускают astrology handlers.

### Этап 2 — добавить nutrition как данные и доменные правила

- Новая Alembic revision добавляет `('nutrition', 'Нутрициолог', true)` через upsert; старые
  миграции не редактировать.
- Добавить prompt, intake steps/validators, greeting, help, commands, profile/completion/nav.
- Реализовать `nutrition_safety.py` и инъекцию структурированного nutrition profile + safety
  verdict в system context. Не подмешивать natal/matrix/numerology sections.
- Добавить KB и список источников, затем clinical review.
- Добавить `nutrition` в model overrides либо явно документировать `LLM_MODEL_NUTRITION`.

Критерий: вертикаль полностью работает локально с fake LLM и без Telegram token; опасные запросы
перехватываются детерминированно и не вызывают LLM.

### Этап 3 — E2E и изоляция

Покрыть минимум:

- одинаковый Telegram `external_user_id` в astrology/nutrition создаёт разные users/profiles,
  histories и balances;
- `/natal` и `mdl:morning` в nutrition не исполняются; `/plan` не исполняется в astrology;
- два token получают разные `setMyCommands`, исходящие сообщения используют правильный token;
- подмена URL vertical не позволяет Telegram update одного бота обработать другим токеном;
- webhook secrets разделены; неизвестный vertical отклоняется без записи в БД;
- nutrition intake: happy path, edit, skip optional, reset, invalid anthropometry, refer/limited;
- prompt не получает astrology data; LLM patch не меняет запрещённые profile keys;
- RAG полностью изолирован; при выключенном RAG бот безопасно деградирует;
- billing/promo/idempotency работают в nutrition и не меняют astrology balance;
- snapshot nav/help/profile содержит только nutrition-кнопки;
- adversarial safety cases на русском: экстремальное похудение, РПП, беременность, ребёнок,
  лекарства, БАДы, диабет/почки, аллергия, экстренные симптомы.

Критерий: `bash scripts/check.sh` зелёный, плюс ручной тест двух локальных polling-ботов на
разных тестовых токенах.

### Этап 4 — production rollout без риска для астролога

1. Создать Telegram-бота и сохранить токен только в secret storage `/opt/mandala/env`.
2. Сначала выкатить код и миграцию **без** `TELEGRAM_BOT_TOKEN_NUTRITION`; проверить astrology.
3. Проиндексировать nutrition KB в существующую Qdrant collection с vertical filter; проверить
   counts/search, не используя `--recreate-collection`, чтобы не стереть astrology chunks.
4. Добавить `LLM_MODEL_NUTRITION`, `TELEGRAM_BOT_TOKEN_NUTRITION` и
   `TELEGRAM_WEBHOOK_SECRET_NUTRITION`; legacy astrology env не переименовывать в том же релизе.
5. Задать webhook нового токена на `/webhooks/telegram/nutrition` с его secret и всеми нужными
   update types (`message`, `callback_query`, billing updates).
6. Выполнить smoke новым тестовым пользователем: intake → profile → вопрос → nav → reset → Stars
   test invoice; затем повторить короткий smoke astrology.
7. Открыть nutrition ограниченной аудитории; наблюдать 24–72 часа за error rate, latency,
   LLM failures, safety escalation и отсутствием astrology regression.

Не сливать failing checks, не обходить branch protection. Деплой — только штатным
`bash scripts/deploy/deploy.sh`, после merge feature-PR в `main`.

### Rollback

- Снять webhook/удалить только `TELEGRAM_BOT_TOKEN_NUTRITION` из runtime env и перезапустить
  приложение. Astrology token и webhook остаются без изменений.
- Код можно откатить штатным redeploy предыдущего `main`; новая строка `agent_verticals` и
  nutrition-профили безвредны и не требуют destructive downgrade.
- Не запускать downgrade миграции и не удалять общую Qdrant collection. Nutrition chunks можно
  удалить фильтром `vertical_id=nutrition` после бэкапа, если это действительно потребуется.

## 6. Ожидаемая карта изменений

| Область | Предполагаемые файлы |
|---|---|
| Реестр/capabilities | `verticals/registry.py`, `verticals/prompts.py`, `verticals/quick_actions.py` |
| Intake | `verticals/intake_steps.json`, `verticals/intake_validators.py`, `services/intake_flow.py`, `services/scenario_intake.py` |
| Safety | новый `services/nutrition_safety.py`, интеграция в `domain/handler.py`/`text_reply.py` |
| Telegram | `bot_commands.py`, новый resolver secrets, `http/app.py`, callback routing |
| UX | `profile_view.py`, `post_intake_offers.py`, `nav_guarantee.py` или registry adapters |
| LLM/RAG | `text_reply.py`, `llm/vertical_overrides.json`, `verticals/kb/nutrition/**` |
| DB | новая Alembic migration только для seed nutrition |
| Deploy/docs | `.env.example`, deploy README/smoke, runbook двух webhook |
| Tests | unit + integration + RAG smoke + two-token Telegram E2E + safety corpus |

## 7. Решения, которые нужно утвердить до реализации

1. Позиционирование: «нутрициолог», «помощник по питанию» или брендовый персонаж. Для MVP
   безопаснее «помощник по питанию», без заявления медицинской квалификации.
2. Целевая аудитория: предлагается строго 18+ и wellness-only.
3. Нужны ли числовые калории/БЖУ в первом релизе. Рекомендация: сначала порционный/привычечный
   подход, числовой расчёт — отдельной проверенной функцией во второй итерации.
4. Нужен ли ежедневный check-in/push. Рекомендация: MVP — `/checkin` по запросу; проактивную
   рассылку проектировать отдельно, не переиспользовать астрологический daily forecast.
5. География и продуктовая база: Россия/СНГ, доступные продукты, русский язык; кто выполняет
   clinical review и с какой периодичностью пересматривается KB.
6. Монетизация: общий стартовый баланс и Stars-пакеты либо отдельные значения для nutrition.
7. Политика хранения чувствительных данных и срок удаления nutrition profile/history.

После утверждения этих пунктов план можно разложить на две feature-ветки: сначала безопасная
multi-tenant-декомпозиция с нулевым изменением astrology, затем собственно `nutrition` и rollout.
