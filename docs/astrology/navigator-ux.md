# Бот-навигатор: короткие ответы, кликабельные термины, бургер-меню

> Как устроен UX астрологического бота: не «собеседник», а **навигатор**.
> Авторитетный код: `src/mandala/services/nav_protocol.py`, `services/text_reply.py`,
> `domain/handler.py`, `adapters/telegram/text_format.py`.

## Идея

Астробот отвечает **коротким сообщением** и сразу предлагает навигацию — набор кнопок
и кликабельных терминов, которые ведут к следующему шагу разговора. Пользователь не
формулирует запросы вручную: он ходит по маршруту, который на каждом шаге генерирует
сама модель.

## Протокол навигации (LLM → приложение)

Модель дописывает машинный блок в самый конец ответа — после опционального блока
агент-карты `---mandala---`:

```
<короткое сообщение>
---mandala-nav---
{"buttons":[{"label":"…","q":"…"}],"terms":[{"term":"…","q":"…"}]}
```

- `label` — надпись на кнопке; `term` — слово в тексте, которое станет ссылкой.
- `q` — **полный follow-up запрос**, который выполнится при клике (как обычный ход диалога).
- Парсинг, присвоение id и разбор клика: `src/mandala/services/nav_protocol.py`
  (`split_llm_nav_suffix`, `assign_ids`, `resolve_nav_action`). Невалидный или
  отсутствующий JSON деградирует до обычного текста — **никогда не бросает исключение**.

## Почему `q` не кладётся в кнопку напрямую

Полный текст `q` не влезает ни в `callback_data` Telegram (64 байта), ни в start-payload
deep-link (64 символа). Поэтому `assign_ids` сохраняет карту `id -> q` в
`agent_card["nav_map"]` (персист через `ProfileRepository.merge_agent_card`; карта
**перезаписывается каждый навигационный ход** = «текущий шаг маршрута»).

- Inline-кнопки несут только `mdl:nav:<id>`.
- Кликабельные термины несут `mdlnav_<id>` (start-payload deep-link).

## Маршрутизация клика

Клики разбираются в `domain/handler.py` **до** intake:
`resolve_nav_action` превращает `mdl:nav:*` (inline-кнопка) или `/start mdlnav_*`
(deep-link термина) обратно в `q` и запускает обычный ход LLM. Привязку кнопок и
term-ссылок к ответу делает `services/text_reply.py`.

## Рендер терминов (Telegram)

Термины отображаются как inline-ссылки `t.me/<bot>?start=<payload>` в
`adapters/telegram/text_format.py` (`format_llm_text_for_telegram_html`). Имя бота
берётся из env `TELEGRAM_BOT_USERNAME` или из кэшированного `getMe` в `outbound_send.py`.
Если имени бота нет — термины остаются **обычным текстом** (безопасная деградация).

Канало-независимое поле `OutboundMessage.term_links` несёт пары `{term, payload}`.

## Меню и клавиатура — только inline (без постоянной reply-клавиатуры)

- **Профиль / сброс / помощь** живут в бургер-меню (`setMyCommands` в `bot_commands.py`;
  `/profile` обрабатывается в `scenario_intake.py`).
- **Навигация всегда inline.** Каждый ответ бота заканчивается inline-клавиатурой: её
  выбирает LLM через nav-блок выше. Если модель не выдала валидный nav (битый JSON,
  неастрологическая вертикаль), контекстный fallback навешивает `services/nav_guarantee.py`
  (`ensure_nav`) на терминальное (последнее не-invoice) сообщение — ответ **никогда** не
  остаётся без навигации. Каждый путь возврата из домена, кроме «голого» приглашения
  intake-визарда, проходит через `ensure_nav` (`domain/handler.py`,
  `services/scenario_intake.py`).
- **Постоянная reply-клавиатура удалена** (`ASTROLOGY_REPLY_KEYBOARD` больше нет).
  Чтобы убрать залипшую клавиатуру у старых пользователей,
  `outbound_send.deliver_outbound_messages` навешивает одноразовый `ReplyKeyboardRemove`
  на первое сообщение батча без кнопок (stateless, без лишнего «пузыря»). Старые нажатия
  по залипшей клавиатуре всё ещё разбираются через `_KEYBOARD_TEXT_TO_CODE` в
  `quick_actions.py`.

## Связанные документы

- Расчётная модель двух школ — [natal-chart.md](natal-chart.md).
- Прогнозы/транзиты, кнопки прогнозов — [forecasting-transits.md](forecasting-transits.md).
- Полный перечень команд и кнопок — [../tickets/full-commands-and-buttons.md](../tickets/full-commands-and-buttons.md).
