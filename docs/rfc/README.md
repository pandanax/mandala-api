# RFC / Актуальная документация Mandala

> Единая точка входа во все актуальные артефакты проекта.
> Историческое поэтапное описание реализации — [docs/implementation-plan.md](../implementation-plan.md).

## Быстрая навигация

| Документ | Что внутри |
|----------|-----------|
| **[current-state.md](current-state.md)** | Реальная архитектура и список работающих фич (подтверждено кодом) |
| **[../plan/README.md](../plan/README.md)** | Единый приоритизированный план развития |
| **[../astrology/](../astrology/)** | Астрологическое KB: западная vs ведическая, расчёты, источники |
| **[../research/astro-power-users.md](../research/astro-power-users.md)** | Исследование: аудитория продвинутых астрологов и их ожидания |

## Корневые документы (не устарели)

| Документ | Зачем |
|----------|-------|
| [README.md](../../README.md) | Обзор, запуск, CI |
| [docs/architecture.md](../architecture.md) | Слои, потоки данных (подтверди по current-state.md) |
| [docs/channels.md](../channels.md) | InboundEvent / OutboundMessage, форматы каналов |
| [docs/data-model.md](../data-model.md) | Таблицы PostgreSQL + JSONB (подтверди миграциями) |
| [docs/agent.md](../agent.md) | LLM, RAG, память диалога |
| [docs/billing.md](../billing.md) | Telegram Stars, BillingProvider |
| [docs/quotas-and-plans.md](../quotas-and-plans.md) | Планы, лимиты, usage |
| [docs/getting-started.md](../getting-started.md) | Установка, env, миграции, запуск |
| [docs/deployment-yandex-cloud.md](../deployment-yandex-cloud.md) | Прод в Yandex Cloud |
| [docs/tickets/full-commands-and-buttons.md](../tickets/full-commands-and-buttons.md) | Тикет на UI с командами и кнопками |

## Расхождения docs со кодом (задокументированы в current-state.md)

- `docs/product.md` описывает продукт как «мандала/нумерология» — в реальности продукт — **астролог** (Telegram-бот, вертикаль `astrology`)
- `docs/architecture.md` упоминает LangGraph — в коде его нет, оркестрация собственная (пайплайн в `domain/handler.py`)
- В `docs/agent.md` и `docs/architecture.md` говорится об опциональном Redis — в MVP Redis не используется
