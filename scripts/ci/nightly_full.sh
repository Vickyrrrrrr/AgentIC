#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# Always run smoke first.
"$REPO_ROOT/scripts/ci/smoke.sh"

# Full flow requires local EDA/runtime environment and an available LLM backend.
for bin in docker verilator iverilog vvp; do
  if ! command -v "$bin" >/dev/null 2>&1; then
    echo "[nightly] missing required binary: $bin (skipping full flow)"
    exit 0
  fi
done

if [[ ! -d "${OPENLANE_ROOT:-$HOME/OpenLane}" ]]; then
  echo "[nightly] OPENLANE_ROOT not present (skipping full flow)"
  exit 0
fi

if [[ -z "${NVIDIA_API_KEY:-}" && -z "${LLM_BASE_URL:-}" ]]; then
  echo "[nightly] no LLM backend configured (skipping full flow)"
  exit 0
fi

# End-to-end strict run on reference design.
python3 main.py build \
  --name ci_nightly_counter \
  --desc "8-bit counter with enable and async reset" \
  --full-signoff \
  --strict-gates \
  --pdk-profile sky130 \
  --max-retries 2 \
  --min-coverage 80
