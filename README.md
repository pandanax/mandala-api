# Mandala

Бэкенд **MVP** продукта «Mandala»: несколько вертикалей (астрология, терапия и др.), каналы **Telegram** и **HTTP** (webhook + Web API), **PostgreSQL**, текст и изображения через **OpenAI-совместимые** API, **квоты и планы**, оплата через **Telegram Stars**. Ядро не привязано к Telegram — доменные события и один пайплайн обработки.

## С чего начать

| Документ | Зачем |
|----------|--------|
| **[docs/getting-started.md](docs/getting-started.md)** | Установка, **`.env`**, Podman + Postgres, миграции, запуск HTTP/бота, прод-env, webhook, проверки |
| **[docs/deployment-yandex-cloud.md](docs/deployment-yandex-cloud.md)** | Прод в YC: ресурсы, SSH, рестарт после правки env, первый запуск (§11–§12) |
| **[docs/roadmap.md](docs/roadmap.md)** | План развития после MVP |
| **[docs/README.md](docs/README.md)** | Оглавление доменной документации |

## Документация (все страницы)

| Файл | Содержание |
|------|------------|
| [docs/product.md](docs/product.md) | Продукт, сценарий, границы |
| [docs/architecture.md](docs/architecture.md) | Слои, потоки данных, расширения |
| [docs/data-model.md](docs/data-model.md) | Сущности и таблицы БД |
| [docs/channels.md](docs/channels.md) | События, адаптеры, `OutboundMessage` |
| [docs/billing.md](docs/billing.md) | Биллинг, Stars, провайдеры |
| [docs/quotas-and-plans.md](docs/quotas-and-plans.md) | Планы, лимиты, usage |
| [docs/agent.md](docs/agent.md) | Оркестрация, LLM, RAG, память диалога |
| [docs/deployment-yandex-cloud.md](docs/deployment-yandex-cloud.md) | Деплой в Yandex Cloud |
| [docs/implementation-plan.md](docs/implementation-plan.md) | Исторический поэтапный план (тикеты) — для контекста агентов и команды |

Деплой скриптами и образ: **[scripts/deploy/README.md](scripts/deploy/README.md)** · Terraform (DNS): **[terraform/README.md](terraform/README.md)**.

## Разработка: проверки

Из корня (зависимости — см. [getting-started](docs/getting-started.md)):

```bash
bash scripts/check.sh
```

Полный прогон с БД и интеграционными тестами: **`bash scripts/verify_project.sh`** (нужен **`DATABASE_URL`** в окружении или в **`.env`**).

В CI: **`.github/workflows/ci.yml`** — **ruff**, **mypy**, Postgres в сервисе, **alembic**, **pytest** (включая **`integration`**).

## Образ и локальные контейнеры

Сборка: **`Containerfile`**, **`bash scripts/deploy/build_image.sh`**. Локально БД и опционально Qdrant — **`podman compose`** (см. **`.env.example`** и **getting-started**).
