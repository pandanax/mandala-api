#!/usr/bin/env bash
# Полный цикл деплоя Mandala с локальной машины на ВМ:
#   1) собрать образ под linux/amd64,
#   2) сохранить в tar,
#   3) перекинуть на ВМ,
#   4) загрузить в Docker и перезапустить контейнер через restart_app.sh,
#   5) почистить tar-ы локально и на ВМ + удалить старые mandala-образы на ВМ
#      (оставляем только текущий и предыдущий тег).
#
# Использование:
#   bash scripts/deploy/deploy.sh                 # тег по дате-времени
#   bash scripts/deploy/deploy.sh my-tag          # явный тег
#
# Параметры окружения (с дефолтами):
#   SSH_HOST   — куда деплоим                  (по умолчанию ubuntu@api.mandala-app.online)
#   PLATFORM   — платформа сборки              (по умолчанию linux/amd64)
#   ENGINE     — локальный движок              (по умолчанию podman, можно docker)
#   RUN_MIGRATIONS=1 — выполнить alembic upgrade head перед стартом нового контейнера
#   KEEP_REMOTE_IMAGES=N — сколько последних образов mandala оставить на ВМ (по умолчанию 2)
#
# Перед запуском убедитесь, что:
#   - на ВМ лежит /opt/mandala/restart_app.sh и /opt/mandala/env;
#   - SSH-ключ настроен (ssh "$SSH_HOST" 'true' проходит без пароля).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

TAG="${1:-$(date +%Y%m%d-%H%M%S)}"
SSH_HOST="${SSH_HOST:-ubuntu@api.mandala-app.online}"
PLATFORM="${PLATFORM:-linux/amd64}"
ENGINE="${ENGINE:-podman}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-0}"
KEEP_REMOTE_IMAGES="${KEEP_REMOTE_IMAGES:-2}"

LOCAL_IMAGE="mandala:${TAG}"
REMOTE_IMAGE="localhost/mandala:${TAG}"
TAR_PATH="/tmp/mandala-${TAG}.tar"

echo "[deploy] tag:        $TAG"
echo "[deploy] platform:   $PLATFORM"
echo "[deploy] engine:     $ENGINE"
echo "[deploy] ssh host:   $SSH_HOST"
echo "[deploy] tar path:   $TAR_PATH"
echo

# 1) build
echo "[deploy] step 1/4: build image"
MANDALA_IMAGE="$LOCAL_IMAGE" \
MANDALA_PLATFORM="$PLATFORM" \
CONTAINER_ENGINE="$ENGINE" \
  bash "$ROOT/scripts/deploy/build_image.sh"

# 2) save → 3) scp → 4) load + restart
cleanup_local() {
  rm -f "$TAR_PATH" 2>/dev/null || true
}
trap cleanup_local EXIT

echo
echo "[deploy] step 2/4: save image to $TAR_PATH"
"$ENGINE" save "$REMOTE_IMAGE" -o "$TAR_PATH"
ls -lh "$TAR_PATH"

echo
echo "[deploy] step 3/4: scp to $SSH_HOST:$TAR_PATH"
scp "$TAR_PATH" "${SSH_HOST}:${TAR_PATH}"

echo
echo "[deploy] step 4/4: load + restart + prune on remote"
# shellcheck disable=SC2029
ssh "$SSH_HOST" "
  set -e
  sudo docker load -i '$TAR_PATH'
  sudo MANDALA_IMAGE='$REMOTE_IMAGE' RUN_MIGRATIONS='$RUN_MIGRATIONS' bash /opt/mandala/restart_app.sh
  rm -f '$TAR_PATH'
  # Удаляем старые mandala-образы, оставляя только KEEP_REMOTE_IMAGES самых свежих,
  # плюс защищаем образ запущенного контейнера от удаления.
  RUNNING_IMG=\$(sudo docker inspect -f '{{.Config.Image}}' mandala-http 2>/dev/null || true)
  sudo docker images --format '{{.Repository}}:{{.Tag}} {{.CreatedAt}}' \
    | awk '\$1 ~ /^localhost\/mandala:/ {print}' \
    | sort -k2,99 -r \
    | tail -n +$((KEEP_REMOTE_IMAGES + 1)) \
    | awk '{print \$1}' \
    | while read -r img; do
        if [ \"\$img\" = \"\$RUNNING_IMG\" ]; then continue; fi
        echo \"[deploy] prune old image: \$img\"
        sudo docker rmi \"\$img\" >/dev/null 2>&1 || true
      done
"

echo
echo "[deploy] done. image=$REMOTE_IMAGE"
