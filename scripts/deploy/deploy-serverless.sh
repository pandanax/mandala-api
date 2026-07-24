#!/bin/bash
# Деплой в Yandex Serverless Container.
#
# Требования:
#   - yc CLI установлен и аутентифицирован (yc init / yc iam create-token)
#   - docker/podman доступен для сборки образа
#   - Переменные окружения заданы (см. ниже)
#
# Обязательные переменные:
#   REGISTRY_ID        — ID реестра в Yandex Container Registry (cr.yandex/<id>/...)
#   DATABASE_URL       — postgresql+psycopg://user:pass@host:5432/db
#   LLM_BASE_URL       — https://api.openai.com/v1 (или совместимый)
#   LLM_API_KEY        — ключ LLM-провайдера
#   LLM_MODEL          — например gpt-4o-mini
#   TELEGRAM_BOT_TOKEN — токен бота
#   TELEGRAM_VERTICAL_ID    — slug вертикали (astrology / therapy)
#   TELEGRAM_WEBHOOK_SECRET — секрет для X-Telegram-Bot-Api-Secret-Token
#
# Опциональные (имеют defaults):
#   YC_CONTAINER_NAME  — имя контейнера в Serverless (default: mandala-api)
#   YC_FOLDER_ID       — ID каталога YC (default: из yc config)
#   IMAGE_TAG_SUFFIX   — суффикс тега образа (default: latest)
#   LLM_EMBEDDING_MODEL — модель эмбеддингов (если используется RAG)
#   QDRANT_URL         — URL Qdrant (если используется RAG)

set -euo pipefail

REGISTRY_ID="${REGISTRY_ID:?'Укажите REGISTRY_ID (ID реестра Yandex Container Registry)'}"
YC_CONTAINER_NAME="${YC_CONTAINER_NAME:-mandala-api}"
IMAGE_TAG_SUFFIX="${IMAGE_TAG_SUFFIX:-latest}"
IMAGE_TAG="cr.yandex/${REGISTRY_ID}/mandala-api:${IMAGE_TAG_SUFFIX}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "==> Сборка образа: ${IMAGE_TAG}"
docker build -f "${REPO_ROOT}/Containerfile" -t "mandala:local" "${REPO_ROOT}"

echo "==> Тег образа для YC Container Registry"
docker tag mandala:local "${IMAGE_TAG}"

echo "==> Пуш в cr.yandex"
docker push "${IMAGE_TAG}"

echo "==> Деплой ревизии Serverless Container"

# Собираем аргументы env — только заданные переменные
ENV_ARGS=(
    "--environment" "DATABASE_URL=${DATABASE_URL:?'Укажите DATABASE_URL'}"
    "--environment" "LLM_BASE_URL=${LLM_BASE_URL:?'Укажите LLM_BASE_URL'}"
    "--environment" "LLM_API_KEY=${LLM_API_KEY:?'Укажите LLM_API_KEY'}"
    "--environment" "LLM_MODEL=${LLM_MODEL:?'Укажите LLM_MODEL'}"
    "--environment" "TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:?'Укажите TELEGRAM_BOT_TOKEN'}"
    "--environment" "TELEGRAM_VERTICAL_ID=${TELEGRAM_VERTICAL_ID:-astrology}"
    "--environment" "TELEGRAM_WEBHOOK_SECRET=${TELEGRAM_WEBHOOK_SECRET:?'Укажите TELEGRAM_WEBHOOK_SECRET'}"
)

if [[ -n "${LLM_EMBEDDING_MODEL:-}" ]]; then
    ENV_ARGS+=("--environment" "LLM_EMBEDDING_MODEL=${LLM_EMBEDDING_MODEL}")
fi
if [[ -n "${QDRANT_URL:-}" ]]; then
    ENV_ARGS+=("--environment" "QDRANT_URL=${QDRANT_URL}")
    ENV_ARGS+=("--environment" "MANDALA_RAG_BACKEND=qdrant")
fi

FOLDER_ARGS=()
if [[ -n "${YC_FOLDER_ID:-}" ]]; then
    FOLDER_ARGS=("--folder-id" "${YC_FOLDER_ID}")
fi

yc serverless container revision deploy \
    "${FOLDER_ARGS[@]}" \
    --container-name "${YC_CONTAINER_NAME}" \
    --image "${IMAGE_TAG}" \
    --cores 1 \
    --memory 512MB \
    --execution-timeout 60s \
    --concurrency 16 \
    "${ENV_ARGS[@]}"

echo ""
echo "✅ Задеплоено в Yandex Serverless Container: ${YC_CONTAINER_NAME}"
echo "   Образ: ${IMAGE_TAG}"
echo ""
echo "Следующий шаг — зарегистрировать webhook Telegram:"
echo "  curl -X POST 'https://api.telegram.org/bot\${TELEGRAM_BOT_TOKEN}/setWebhook' \\"
echo "    -d 'url=https://<YC_CONTAINER_URL>/webhooks/telegram/\${TELEGRAM_VERTICAL_ID}' \\"
echo "    -d 'secret_token=\${TELEGRAM_WEBHOOK_SECRET}'"
