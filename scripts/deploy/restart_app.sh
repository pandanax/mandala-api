#!/usr/bin/env bash
# Перезапуск контейнера mandala-http на ВМ с подхватом /opt/mandala/env.
#
# Зачем нужен: docker restart НЕ перечитывает --env-file.
# Поэтому после правки /opt/mandala/env (новые/изменённые TELEGRAM_*, LLM_*, ...)
# контейнер нужно ПЕРЕСОЗДАВАТЬ (stop + rm + run), а не просто перезапускать.
#
# Скрипт идемпотентный: можно запускать многократно.
#
# Использование (на ВМ):
#   sudo bash /opt/mandala/restart_app.sh
#   sudo MANDALA_IMAGE=localhost/mandala:test-amd64 bash /opt/mandala/restart_app.sh
#
# С локальной машины:
#   scp scripts/deploy/restart_app.sh ubuntu@api.mandala-app.online:/tmp/
#   ssh ubuntu@api.mandala-app.online 'sudo bash /tmp/restart_app.sh'

set -euo pipefail

# --- настройки (можно переопределить через env) ---
CONTAINER_NAME="${CONTAINER_NAME:-mandala-http}"
ENV_FILE="${ENV_FILE:-/opt/mandala/env}"
HOST_PORT="${HOST_PORT:-8000}"
CONTAINER_PORT="${CONTAINER_PORT:-8000}"
RESTART_POLICY="${RESTART_POLICY:-unless-stopped}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-0}"   # 1 = выполнить alembic upgrade head перед стартом

# --- проверки ---
if [[ ! -r "$ENV_FILE" ]]; then
  echo "ERROR: env-файл $ENV_FILE не найден или нет прав на чтение" >&2
  exit 1
fi

# Образ берётся из аргумента/переменной MANDALA_IMAGE, иначе — из текущего контейнера,
# иначе — пытаемся найти последний локальный mandala:*.
detect_image() {
  if [[ -n "${MANDALA_IMAGE:-}" ]]; then
    echo "$MANDALA_IMAGE"
    return
  fi
  if docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    docker inspect "$CONTAINER_NAME" --format '{{.Config.Image}}'
    return
  fi
  # последний по дате локальный образ mandala
  local img
  img=$(docker images --format '{{.Repository}}:{{.Tag}}' \
        | grep -E '(^|/)mandala:' | head -n1 || true)
  if [[ -z "$img" ]]; then
    echo "ERROR: не задан MANDALA_IMAGE и не нашёл локальный образ mandala:*" >&2
    exit 1
  fi
  echo "$img"
}

IMAGE="$(detect_image)"
echo "[restart_app] image:           $IMAGE"
echo "[restart_app] container:       $CONTAINER_NAME"
echo "[restart_app] env-file:        $ENV_FILE"
echo "[restart_app] port:            ${HOST_PORT}:${CONTAINER_PORT}"
echo "[restart_app] restart-policy:  $RESTART_POLICY"

# --- миграции (опционально) ---
if [[ "$RUN_MIGRATIONS" == "1" ]]; then
  echo "[restart_app] applying alembic migrations…"
  docker run --rm --env-file "$ENV_FILE" "$IMAGE" python -m alembic upgrade head
fi

# --- стоп и удаление старого контейнера ---
if docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
  echo "[restart_app] stopping old container…"
  docker stop "$CONTAINER_NAME" >/dev/null
  docker rm   "$CONTAINER_NAME" >/dev/null
fi

# --- запуск нового ---
echo "[restart_app] starting new container…"
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart "$RESTART_POLICY" \
  --env-file "$ENV_FILE" \
  -e HOST=0.0.0.0 -e PORT="$CONTAINER_PORT" \
  -p "${HOST_PORT}:${CONTAINER_PORT}" \
  "$IMAGE" >/dev/null

# --- ждём готовности и health-check ---
echo "[restart_app] waiting for /health…"
for i in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${HOST_PORT}/health" >/dev/null 2>&1; then
    echo "[restart_app] healthy after ${i}s"
    curl -fsS "http://127.0.0.1:${HOST_PORT}/health"; echo
    exit 0
  fi
  sleep 1
done

echo "[restart_app] ERROR: /health не отвечает за 20с — смотри логи: docker logs $CONTAINER_NAME --tail 50" >&2
docker logs "$CONTAINER_NAME" --tail 50 || true
exit 1
