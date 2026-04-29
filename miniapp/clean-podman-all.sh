#!/bin/bash

echo "🧹 Starting full Podman cleanup..."

# Stop and remove all containers
podman stop -a 2>/dev/null
podman rm -af 2>/dev/null

# Remove all images
podman rmi -f $(podman images -aq) 2>/dev/null

# Remove all volumes
podman volume rm -f $(podman volume ls -q) 2>/dev/null

# System cleanup
podman system prune -a --volumes -f
podman builder prune -f

echo "✅ Podman fully cleaned!"
