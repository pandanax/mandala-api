# Утренняя рассылка: девиз-мотиватор (proactive daily)

> Бот сам, без запроса, шлёт каждое утро **короткий девиз-мотиватор** (1–2 строки, «девиз
> дня», можно emoji — **без** разбора планет). По умолчанию **ВКЛ в 10:00 МСК** для всех;
> время/выключение — командой `/morning`. **Бесплатно** — никогда не списывает кошелёк/квоту
> (подарок, как мгновенные рендеры); промо и баланс не трогаются.
>
> Авторитетный код: `services/daily_forecast.py`,
> `adapters/telegram/daily_forecast_scheduler.py`, `repositories/daily_forecast.py`,
> `services/daily_forecast_settings.py`. Настройки в `agent_card` — ключи
> `AGENT_CARD_DAILY_FORECAST_*` (`verticals/client_knowledge.py`).

## Настройки (в `agent_card`, без миграции)

Всё состояние живёт в `agent_card` — миграции не нужно. Фиксированный пояс **`Europe/Moscow`**
для всех (НЕ пояс места рождения).

| Ключ | Смысл |
|------|-------|
| `daily_forecast_enabled` | вкл/выкл; **отсутствие ключа = True** (по умолчанию включено) |
| `daily_forecast_time` | `"HH:MM"` МСК, по умолчанию `"10:00"` |
| `daily_forecast_last_sent` | `"YYYY-MM-DD"` МСК — идемпотентность, переживает рестарт |

## Решение и контент (`services/daily_forecast.py`)

Чистые функции (без сети/БД):

- `should_send_daily_forecast(agent_card, now)` — инъекция `now` (по умолчанию `now_msk()`);
  проверяет: включено / не отправлено сегодня / время наступило / окно догона
  (`CATCHUP_WINDOW_MINUTES = 180` — после простоя не выстрелит в 3 ночи).
- `build_daily_slogan(...)` — зовёт LLM вертикали с крошечным standalone-промптом (малый
  `max_tokens`), **никогда** `QuotaService`. Деградирует до общего девиза без карты/транзитов;
  при сбое LLM возвращает `None` (тогда не шлём и не помечаем отправленным). Транзиты
  переиспользуют школу натала (`calculate_current_transits`), позиции — внутренняя «подсказка
  настроения» модели, пользователю не показываются.

## Планировщик (фоновая asyncio-задача в HTTP-lifespan)

`http/app.py` → `adapters/telegram/daily_forecast_scheduler.py`
(`start/stop_daily_forecast_scheduler`). Цикл просыпается ~раз в 60с, считает МСК-`now` и
**отгружает** синхронный tick в рабочий поток (`anyio.to_thread.run_sync` — не блокирует
event-loop).

`run_daily_forecast_tick` идёт по каждой вертикали с bot-токеном (`load_bot_token_map`),
читает получателей (`repositories/daily_forecast.py`: join `client_profiles`+`channel_links`,
только строки с `birth_date`), шлёт на `external_user_id` (= chat_id в приватном чате)
токеном вертикали через `deliver_outbound_messages`, затем помечает `last_sent` в отдельной
транзакции. Per-user try/except: сбой **не** помечает отправленным (ретрай в пределах окна).

- **Глобальный рубильник** `MANDALA_DAILY_FORECAST_ENABLED` (по умолчанию on). Off = задача
  не стартует вовсе.

## Команда `/morning` (детерминированная, без LLM)

`services/daily_forecast_settings.py`, роутится в `domain/handler.py` **до** анкеты
(`is_daily_forecast_action`) — работает в любом состоянии профиля. Показывает текущее
состояние + переключатель + пресеты времени (07:00–12:00); поддерживает `/morning on|off` и
`/morning HH:MM`; callback-и `mdl:morning` / `:on` / `:off` / `:set:HH:MM`. В бургер-меню
(`bot_commands.py`). Само утреннее сообщение несёт кнопки «📊 Подробнее» (`mdl:fc_today`) и
«⚙️ Настроить» (`mdl:morning`).

## Тесты (офлайн, детерминированные)

- `tests/test_daily_forecast.py` — ветки should-send, парсинг настроек, обработчик `/morning`,
  билдер контента + деградация/сбой.
- `tests/test_daily_forecast_scheduler.py` — обвязка tick: правильные chat_id+токен,
  идемпотентность, кошелёк не тронут, LLM-сбой не помечает, start/stop.
- `tests/test_keyboard_and_commands.py` — `mdl:morning` роутится, не трогая LLM.

## Связанные документы

- Прогнозы по запросу и транзиты — [forecasting-transits.md](forecasting-transits.md).
- UX-навигация и команды — [navigator-ux.md](navigator-ux.md).
- Кошелёк/квота (почему рассылка бесплатна) — [../billing.md](../billing.md).
