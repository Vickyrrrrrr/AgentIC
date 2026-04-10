#!/bin/sh
# Production entrypoint: ensure runtime directories exist before the
# main process starts. Runs as appuser (non-root uid 1000) so we MUST
# NOT attempt chown — /app is already owned by appuser from the Dockerfile.
set -e

for dir in /app/designs /app/artifacts /app/training; do
  mkdir -p "$dir"
done

# Hand off to the real command passed as arguments
exec "$@"
