# Документация Mandala (MVP)

Несколько каналов (Telegram, HTTP Web API), профиль и память в **PostgreSQL**, опционально **RAG** (Qdrant), разные **LLM** для текста и картинок, тарифы с лимитами, платежи (**Telegram Stars** и задел под другие провайдеры).

**С чего начать после клонирования репозитория:** [getting-started.md](getting-started.md) (установка, **`.env`**, миграции, запуск, прод-checklist). **Что дальше по продукту:** [roadmap.md](roadmap.md).

## Оглавление

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
| [agent.md](agent.md) | Оркестрация (граф), роутинг моделей, RAG, память диалога |
| [deployment-yandex-cloud.md](deployment-yandex-cloud.md) | Деплой в Yandex Cloud: ресурсы, сеть, БД, Docker, Terraform, обновления |
| [implementation-plan.md](implementation-plan.md) | Исторический поэтапный план с тикетами (контекст для команды и агентов) |

## Принципы

1. **Ядро не знает про Telegram** — только внутренний `user_id`, **`vertical_id`** и доменные события.
2. **Профиль, квоты, биллинг** — источник истины в **PostgreSQL**; гибкая часть профиля и артефактов — **JSONB** (разные агенты — разная форма без смены СУБД).
3. **Лимиты** — конфигурируемые по плану, без магических чисел в коде.
4. **Платежи** — через интерфейс `BillingProvider`, первая реализация — Stars.
5. **RAG** — векторный слой отдельно от OLTP (см. [architecture.md](architecture.md)).
6. **Контейнеры локально** — **Podman** (`podman compose`, `podman build`), без инструкций под Docker Desktop в этом репозитории.
