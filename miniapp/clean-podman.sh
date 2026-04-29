#!/bin/bash

echo "🧹 Останавливаем и удаляем ВСЕ контейнеры..."
podman stop $(podman ps -aq) 2>/dev/null
podman rm -f $(podman ps -aq) 2>/dev/null

echo "🔥 Удаляем ВСЕ образы..."
podman rmi -f $(podman images -aq) 2>/dev/null

echo "♻️ Очищаем системный кэш..."
podman system prune -a --force

echo "✅ Podman полностью очищен!"
