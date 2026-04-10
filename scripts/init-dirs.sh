#!/bin/sh
# Production-grade entrypoint: create all required runtime directories
# with correct ownership BEFORE the main process starts.
# This fixes 'Errno 13 Permission denied: /app/designs/...' on fresh
# Linux VMs where Docker bind-mount target dirs don't pre-exist.
set -e

DIRS="/app/designs /app/artifacts /app/training"

for dir in $DIRS; do
  mkdir -p "$dir"
  # Only chown if we are running as root (which is the default in Docker)
  if [ "$(id -u)" = "0" ]; then
    chown -R nobody:nogroup "$dir" 2>/dev/null || true
  fi
  chmod -R 755 "$dir"
done

# Hand off to the real command passed as arguments
exec "$@"
