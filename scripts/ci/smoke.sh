#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

python3 -m compileall -q src/agentic
python3 -m unittest discover -s tests -p 'test_tier1_upgrade.py'
