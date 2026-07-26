# Документация Mandala (MVP)

Несколько каналов (Telegram, HTTP Web API), профиль и память в **PostgreSQL**, опционально **RAG** (Qdrant), разные **LLM** для текста и картинок, тарифы с лимитами, платежи (**Telegram Stars** и задел под другие провайдеры).

**С чего начать после клонирования репозитория:** [getting-started.md](getting-started.md) (установка, **`.env`**, миграции, запуск, прод-checklist). **Что дальше по продукту:** [roadmap.md](roadmap.md).

## Как смотреть документацию (просмотрщик)

Всю папку `docs/` можно открыть как сайт с оглавлением и поиском — одной командой:

```bash
bash scripts/docs-serve.sh          # http://127.0.0.1:8001 (mkdocs serve)
bash scripts/docs-serve.sh build    # статическая сборка в ./site
```

Просмотрщик (**mkdocs + Material**) полностью изолирован от рантайма бота: зависимости —
в `docs/requirements.txt`, они **не** входят в `pyproject.toml`/`uv.lock` и в прод-образ
(`Containerfile` ставит только `--extra deploy`), а окружение вьюера (`.venv-docs`) не
трогает `.venv` приложения. Конфиг и навигация — `mkdocs.yml` в корне репозитория.

## Оглавление

### Платформа

| Файл | Содержание |
|------|------------|
| [getting-started.md](getting-started.md) | Установка, переменные окружения, первый запуск |
| [roadmap.md](roadmap.md) | План развития после MVP |
| [product.md](product.md) | Продукт, пользовательский сценарий, границы ответственности |
| [architecture.md](architecture.md) | Слои системы, потоки данных, MVP vs расширения |
| [data-model.md](data-model.md) | Сущности, таблицы БД, связь каналов с пользователем |
| [channels.md](channels.md) | Нормализованные события, адаптеры, `OutboundMessage` |
| [billing.md](billing.md) | Абстракция биллинга, Telegram Stars, будущие провайдеры |
| [quotas-and-plans.md](quotas-and-plans.md) | Планы, лимиты (в т.ч. 0 картинок), учёт usage |
| [agent.md](agent.md) | Оркестрация (граф), единый выбор LLM-модели вертикали, RAG, память |
| [deployment-yandex-cloud.md](deployment-yandex-cloud.md) | Деплой в Yandex Cloud: ресурсы, сеть, БД, Docker, контракт PORT/EXPOSE/health |
| [monitoring.md](monitoring.md) | Наблюдаемость: дашборд метрик YC Monitoring (Terraform), эмиссия метрик, логи |
| [logging.md](logging.md) | Доставка логов приложения с ВМ в YC Logging (log-группа + Unified Agent) |
| [implementation-plan.md](implementation-plan.md) | Исторический поэтапный план с тикетами (контекст для команды и агентов) |

### Астрология

| Файл | Содержание |
|------|------------|
| [astrology/foundations.md](astrology/foundations.md) | Западная vs ведическая: зодиак, дома, айанамша |
| [astrology/natal-chart.md](astrology/natal-chart.md) | Расчёт натальной карты; две школы, которые не смешиваются |
| [astrology/forecasting-transits.md](astrology/forecasting-transits.md) | Прогнозы и транзиты (уважают школу натала) |
| [astrology/compatibility.md](astrology/compatibility.md) | Совместимость / синастрия |
| [astrology/navigator-ux.md](astrology/navigator-ux.md) | Бот-навигатор: короткие ответы, контекстные inline-кнопки «куда дальше», кликабельные термины, бургер-меню |
| [astrology/sources.md](astrology/sources.md) | Источники и справочные материалы |

### История, планы, исследования

| Файл | Содержание |
|------|------------|
| [plan/README.md](plan/README.md) | Черновики планов |
| [rfc/README.md](rfc/README.md) · [rfc/current-state.md](rfc/current-state.md) | RFC и снимок текущего состояния |
| [tickets/full-commands-and-buttons.md](tickets/full-commands-and-buttons.md) | Полный перечень команд и кнопок |
| [research/astro-power-users.md](research/astro-power-users.md) | Исследование: astro power users |

## Принципы

1. **Ядро не знает про Telegram** — только внутренний `user_id`, **`vertical_id`** и доменные события.
2. **Профиль, квоты, биллинг** — источник истины в **PostgreSQL**; гибкая часть профиля и артефактов — **JSONB** (разные агенты — разная форма без смены СУБД).
3. **Лимиты** — конфигурируемые по плану, без магических чисел в коде.
4. **Платежи** — через интерфейс `BillingProvider`, первая реализация — Stars.
5. **RAG** — векторный слой отдельно от OLTP (см. [architecture.md](architecture.md)).
6. **Контейнеры локально** — **Podman** (`podman compose`, `podman build`), без инструкций под Docker Desktop в этом репозитории.
