#!/usr/bin/env bash
# Сборка образа Mandala (тикет 23). Из корня репозитория: bash scripts/deploy/build_image.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

IMAGE="${MANDALA_IMAGE:-mandala:local}"
ENGINE="${CONTAINER_ENGINE:-podman}"
# В Yandex Cloud ВМ обычно linux/amd64; образ с Mac ARM без --platform на AMD не запустится.
PLATFORM="${MANDALA_PLATFORM:-linux/amd64}"

if ! command -v "$ENGINE" >/dev/null 2>&1; then
  echo "Не найден $ENGINE. Установите Podman или задайте CONTAINER_ENGINE=docker." >&2
  exit 1
fi

echo "[build_image] $ENGINE build --platform $PLATFORM -f Containerfile -t $IMAGE"
"$ENGINE" build --platform "$PLATFORM" -f Containerfile -t "$IMAGE" "$ROOT"
echo "[build_image] Готово: $IMAGE"
