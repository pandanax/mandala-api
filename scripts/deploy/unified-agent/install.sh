#!/usr/bin/env bash
# Установка доставки логов Mandala в YC Logging на ВМ (идемпотентно).
#
# Ставит и запускает две systemd-службы + logrotate:
#   * mandala-logship        — docker logs -f mandala-http >> /var/log/mandala/app.log
#   * mandala-unified-agent  — Unified Agent: файл -> YC Logging (log-группа)
# НЕ трогает контейнер mandala-http, nginx, n8n и путь деплоя (restart_app.sh).
#
# Предпосылки (см. docs/logging.md):
#   1) создана log-группа (terraform apply → `terraform output logging_group_id`);
#   2) к ВМ привязан сервисный аккаунт с ролью `logging.writer`
#      (тот же SA, что для метрик, ему просто добавляется роль).
#
# Использование (на ВМ, из каталога с этими файлами):
#   sudo LOG_GROUP_ID=e23xxxxxxxxxxxxxxxx bash install.sh
# Повторный запуск безопасен (перекладывает файлы и перезапускает службы).

set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_GROUP_ID="${LOG_GROUP_ID:-}"

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: запускать под root (sudo)." >&2
  exit 1
fi
if [[ -z "$LOG_GROUP_ID" ]]; then
  echo "ERROR: задай LOG_GROUP_ID=<id log-группы> (terraform output logging_group_id)." >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker не найден на ВМ." >&2
  exit 1
fi

echo "[install] каталоги…"
install -d -m 0755 /var/log/mandala
install -d -m 0755 /var/lib/yandex/unified_agent
install -d -m 0755 /etc/mandala/unified-agent

echo "[install] конфиг Unified Agent…"
install -m 0644 "$SRC_DIR/config.yml" /etc/mandala/unified-agent/config.yml

echo "[install] env с LOG_GROUP_ID (права 600)…"
umask 077
printf 'LOG_GROUP_ID=%s\n' "$LOG_GROUP_ID" > /etc/mandala/unified-agent.env
chmod 600 /etc/mandala/unified-agent.env

echo "[install] logrotate…"
install -m 0644 "$SRC_DIR/logrotate-mandala" /etc/logrotate.d/mandala

echo "[install] systemd-юниты…"
install -m 0644 "$SRC_DIR/mandala-logship.service" /etc/systemd/system/mandala-logship.service
install -m 0644 "$SRC_DIR/mandala-unified-agent.service" /etc/systemd/system/mandala-unified-agent.service

echo "[install] подтягиваю образ Unified Agent…"
docker pull cr.yandex/yc/unified-agent >/dev/null

echo "[install] запуск служб…"
systemctl daemon-reload
systemctl enable --now mandala-logship.service
systemctl enable --now mandala-unified-agent.service

echo
echo "[install] готово. Проверка:"
echo "  systemctl status mandala-logship mandala-unified-agent --no-pager"
echo "  docker logs mandala-unified-agent --tail 30   # ошибки доставки/старта агента"
echo "  tail -n 20 /var/log/mandala/app.log           # что реально шлётся"
echo "  # логи в консоли: YC Logging → группа $LOG_GROUP_ID → Логи"
