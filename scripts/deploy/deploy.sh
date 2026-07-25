#!/usr/bin/env bash
# ЕДИНЫЙ канонический деплой Mandala. Один способ выкатки — этот скрипт.
# См. scripts/deploy/README.md (единый источник правды по деплою).
#
#   Использование (из корня репозитория):
#     bash scripts/deploy/deploy.sh
#
#   Что делает (надёжно, с ретраями и авто-откатом):
#     1) rsync исходника на ВМ (без .git/.venv/кэшей — только код);
#     2) НАТИВНАЯ сборка образа amd64 прямо на ВМ (docker build) — без эмуляции и tar;
#     3) restart_app.sh: пересоздать контейнер + alembic upgrade head + ждать /health;
#     4) E2E на реальном проде (health + web /help);
#     5) при любом провале после переключения — АВТО-ОТКАТ на предыдущий образ;
#     6) prune старых образов на ВМ.
#
#   Переменные (с дефолтами):
#     SSH_HOST=ubuntu@api.mandala-app.online   куда деплоим
#     BASE_URL=https://api.mandala-app.online   для e2e
#     RUN_MIGRATIONS=1                          alembic upgrade head перед стартом
#     REMOTE_SRC=mandala-build                  каталог сборки в $HOME ubuntu на ВМ
#     RETRIES=2                                 повторов rsync/сборки при сбое
#     KEEP_IMAGES=3                             сколько образов оставить на ВМ
#     TAG=<дата-время>                          тег образа
#
#   Требования: passwordless SSH на ВМ; на ВМ — docker и /opt/mandala/{env,restart_app.sh}.
set -uo pipefail

# Предотвращаем сон на время сборки (только macOS и только если ещё не под caffeinate).
if command -v caffeinate >/dev/null 2>&1 && [[ -z "${MANDALA_DEPLOY_CAFF:-}" ]]; then
  exec caffeinate -ims env MANDALA_DEPLOY_CAFF=1 bash "$0" "$@"
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SSH_HOST="${SSH_HOST:-ubuntu@api.mandala-app.online}"
BASE_URL="${BASE_URL:-https://api.mandala-app.online}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-1}"
REMOTE_SRC="${REMOTE_SRC:-mandala-build}"
RETRIES="${RETRIES:-2}"
KEEP_IMAGES="${KEEP_IMAGES:-3}"
TAG="${TAG:-$(date +%Y%m%d-%H%M%S)}"
IMAGE="localhost/mandala:${TAG}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=12 "$SSH_HOST")

log() { echo "[deploy] $*"; }

# Повторяем команду до (RETRIES+1) раз с нарастающей паузой.
retry() {
  local n=0
  until "$@"; do
    n=$((n + 1))
    if (( n > RETRIES )); then return 1; fi
    log "повтор ($n/$RETRIES) через $((n * 5))с"
    sleep $((n * 5))
  done
}

echo "==================== mandala deploy (${TAG}) ===================="
cd "$REPO" || { log "нет каталога репозитория"; exit 1; }
log "repo=$REPO  ssh=$SSH_HOST  image=$IMAGE"
log "ветка: $(git rev-parse --abbrev-ref HEAD 2>/dev/null) @ $(git rev-parse --short HEAD 2>/dev/null)"

# Текущий прод-образ — цель отката.
OLD_IMG="$("${SSH[@]}" 'sudo docker inspect -f "{{.Config.Image}}" mandala-http 2>/dev/null' | tr -d "[:space:]")"
log "текущий прод-образ (для отката): ${OLD_IMG:-<неизвестен>}"

rollback() {
  log "-------- ROLLBACK на ${OLD_IMG:-<нет>} --------"
  if [[ -z "$OLD_IMG" ]]; then
    log "‼️ откат невозможен (старый образ неизвестен). Диагностика:"
    log "   ${SSH[*]} 'sudo docker ps -a; sudo docker logs mandala-http --tail 80'"
    return 1
  fi
  "${SSH[@]}" "sudo MANDALA_IMAGE='$OLD_IMG' bash /opt/mandala/restart_app.sh" \
    && log "↩️ откат выполнен на $OLD_IMG" \
    || log "‼️ откат ТОЖЕ упал — нужно ручное вмешательство"
}

prod_e2e() {
  log "-------- E2E на проде --------"
  local h code r rcode i
  # health (несколько попыток — прод мог только что стартовать)
  for i in 1 2 3 4 5; do
    h="$(curl -sS -m 12 -w $'\n%{http_code}' "$BASE_URL/health" 2>&1)"; code="${h##*$'\n'}"
    [[ "$code" == 200 && "$h" == *'"status":"ok"'* ]] && break
    sleep 3
  done
  log "[health] http=$code ${h%$'\n'*}"
  [[ "$code" == 200 && "$h" == *'"status":"ok"'* ]] || { log "E2E FAIL: health"; return 1; }
  # web /help — реальный пайплайн (vertical -> handle_inbound -> команда -> меню)
  r="$(curl -sS -m 25 -X POST "$BASE_URL/webhooks/web" \
        -H 'Content-Type: application/json' -H "X-External-User-Id: deploy-e2e-$TAG" \
        -d '{"text":"/help","vertical_id":"astrology"}' -w $'\n%{http_code}' 2>&1)"
  rcode="${r##*$'\n'}"
  log "[web /help] http=$rcode"
  [[ "$rcode" == 200 && "$r" == *'"messages"'* ]] || { log "E2E FAIL: web /help ($rcode)"; return 1; }
  log "E2E OK"
  return 0
}

# 1) rsync исходника (ретраи)
log "-------- rsync исходника -> $SSH_HOST:~/$REMOTE_SRC --------"
retry rsync -az --delete -e 'ssh -o BatchMode=yes -o ConnectTimeout=12' \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
  --exclude '.mypy_cache' --exclude '.pytest_cache' --exclude '.ruff_cache' \
  --exclude '__pycache__' --exclude 'dist' --exclude 'terraform/.terraform' \
  --exclude 'cache' --exclude '*.tar' --exclude '.DS_Store' --exclude '.gnhf' \
  "$REPO/" "$SSH_HOST:$REMOTE_SRC/" || { log "❌ rsync упал (прод не тронут)"; exit 1; }

# 2) нативная сборка на ВМ (ретраи)
log "-------- docker build на ВМ (нативный amd64) --------"
retry "${SSH[@]}" "cd ~/$REMOTE_SRC && sudo docker build -f Containerfile -t '$IMAGE' ." \
  || { log "❌ сборка на ВМ упала (прод не тронут, остаётся ${OLD_IMG})"; exit 1; }

# 3) переключение на новый образ (restart_app.sh сам ждёт /health)
log "-------- restart_app.sh -> $IMAGE (RUN_MIGRATIONS=$RUN_MIGRATIONS) --------"
if ! "${SSH[@]}" "sudo MANDALA_IMAGE='$IMAGE' RUN_MIGRATIONS='$RUN_MIGRATIONS' bash /opt/mandala/restart_app.sh"; then
  log "❌ рестарт/health нового образа не прошёл"
  rollback
  exit 1
fi

# 4) E2E на проде
if ! prod_e2e; then
  log "❌ E2E не прошёл — откатываюсь"
  rollback
  prod_e2e && log "после отката прод отвечает" || log "‼️ после отката E2E тоже не ок — вмешаться вручную"
  exit 1
fi

# 5) prune старых образов
log "-------- prune (оставляю $KEEP_IMAGES + запущенный) --------"
"${SSH[@]}" "
  RUNNING=\$(sudo docker inspect -f '{{.Config.Image}}' mandala-http 2>/dev/null || true)
  sudo docker images --format '{{.CreatedAt}}\t{{.Repository}}:{{.Tag}}' \
    | grep -E 'localhost/mandala:' | sort -r | tail -n +$((KEEP_IMAGES + 1)) | cut -f2 \
    | grep -v \"^\${RUNNING}\$\" | xargs -r -n1 sudo docker rmi >/dev/null 2>&1 || true
" || true

echo
log "✅ ДЕПЛОЙ УСПЕШЕН: $IMAGE на $BASE_URL — health + web e2e зелёные."
