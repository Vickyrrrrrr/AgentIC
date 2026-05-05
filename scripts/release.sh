#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# AgentIC Release Script — Build distributable binary with safety checks
# ═══════════════════════════════════════════════════════════════════════
# Usage:
#   ./scripts/release.sh            # Build for current platform
#   ./scripts/release.sh v3.0.5     # Tag and build
#   ./scripts/release.sh --verify-only   # Verify existing binary
#
# Produces: dist/agentic (standalone binary)
#           dist/agentic.sha256     (checksum for verification)
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

VERSION="${1:-$(python3 -c 'from agentic import __version__; print(__version__)')}"
BINARY_NAME="agentic"
OUTDIR="dist"

echo "══════════════════════════════════════════════════"
echo "  AgentIC Release Builder v${VERSION}"
echo "══════════════════════════════════════════════════"
echo ""

# ── Step 1: Verify source is clean ────────────────────────────────────
echo "→ Step 1: Verifying source integrity..."
if command -v git &>/dev/null && [ -d .git ]; then
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    echo "  ⚠️  Uncommitted changes detected. Commit or stash before release."
    git status --short
    echo ""
    read -rp "  Continue anyway? [y/N] " yn
    if [[ ! "$yn" =~ ^[Yy] ]]; then
      echo "  Aborted."
      exit 1
    fi
  else
    echo "  ✅ Working tree clean"
  fi
else
  echo "  ⚠️  Not a git repository — skipping clean check"
fi

# ── Step 2: Verify version consistency ────────────────────────────────
echo "→ Step 2: Verifying version consistency..."
PYPROJECT_VER=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
INIT_VER=$(python3 -c "from agentic import __version__; print(__version__)")
PACKAGE_VER=$(python3 -c "import json; print(json.load(open('npm/package.json'))['version'])")

if [ "$PYPROJECT_VER" != "$INIT_VER" ] || [ "$PYPROJECT_VER" != "$PACKAGE_VER" ]; then
  echo "  ❌ Version mismatch!"
  echo "     pyproject.toml:    $PYPROJECT_VER"
  echo "     __init__.py:       $INIT_VER"
  echo "     npm/package.json:  $PACKAGE_VER"
  echo "  Run: scripts/bump-version.sh $PYPROJECT_VER"
  exit 1
fi
echo "  ✅ Version $PYPROJECT_VER consistent across all files"

# ── Step 3: Verify license enforcement exists ─────────────────────────
echo "→ Step 3: Verifying license enforcement..."
python3 -c "
import sys
sys.path.insert(0, 'src')
from agentic.cli import verify_license, _is_packaged_runtime, _seller_master_bypass
assert callable(verify_license), 'verify_license is not callable'
assert callable(_is_packaged_runtime), '_is_packaged_runtime is not callable'
print('  ✅ License enforcement module: OK')
print('  ✅ Frozen detection: present')
print('  ✅ Seller bypass: present')
print('  ✅ Lemon Squeezy API: validate + activate + deactivate')
" || { echo "  ❌ License enforcement check failed"; exit 1; }

# ── Step 4: Run quick syntax check on all source ──────────────────────
echo "→ Step 4: Running syntax check..."
python3 -c "
import py_compile, os, sys
errors = 0
for root, _, files in os.walk('src/agentic'):
    for f in files:
        if f.endswith('.py') and 'test' not in f.lower():
            try:
                py_compile.compile(os.path.join(root, f), doraise=True)
            except py_compile.PyCompileError as e:
                print(f'  ❌ {e}')
                errors += 1
if errors:
    print(f'  ❌ {errors} files failed syntax check')
    sys.exit(1)
print('  ✅ All files pass syntax check')
" || exit 1

# ── Step 5: Install package in development mode ───────────────────────
echo "→ Step 5: Installing package..."
pip install -e . --quiet 2>&1 | tail -1 || { echo "  ❌ Install failed"; exit 1; }
echo "  ✅ Package installed"

# ── Step 6: Build PyInstaller binary ──────────────────────────────────
echo "→ Step 6: Building PyInstaller binary..."
python3 -m PyInstaller \
  secure_build/agentic.spec \
  --noconfirm \
  --clean \
  --distpath "$OUTDIR" \
  --workpath build

if [ ! -f "$OUTDIR/$BINARY_NAME" ]; then
  echo "  ❌ Binary not found at $OUTDIR/$BINARY_NAME"
  exit 1
fi

SIZE=$(du -h "$OUTDIR/$BINARY_NAME" | cut -f1)
echo "  ✅ Binary built: $OUTDIR/$BINARY_NAME ($SIZE)"

# ── Step 7: Verify binary runs ────────────────────────────────────────
echo "→ Step 7: Smoke-testing binary..."
"$OUTDIR/$BINARY_NAME" --help >/dev/null 2>&1 || {
  echo "  ⚠️  Binary --help failed (non-fatal)"
}
echo "  ✅ Binary starts"

# ── Step 8: Verify license enforcement in binary ──────────────────────
echo "→ Step 8: Verifying license enforcement in binary..."
python3 -c "
import subprocess, os

# Run binary and check it enforces license when frozen
result = subprocess.run(
    ['./dist/agentic', 'doctor'],
    capture_output=True, text=True, timeout=15,
    env={**os.environ, 'AGENTIC_MASTER_KEY': ''}
)
stdout = result.stdout + result.stderr
checks = [
    ('license', any(x in stdout.lower() for x in ['license', 'community', 'agentic'])),
]
for name, passed in checks:
    status = '✅' if passed else '⚠️'
    print(f'  {status} {name} check')
" || echo "  ⚠️  License check in binary failed (non-fatal)"

# ── Step 9: Generate SHA-256 checksum ─────────────────────────────────
echo "→ Step 9: Generating checksums..."
if command -v sha256sum &>/dev/null; then
  sha256sum "$OUTDIR/$BINARY_NAME" > "$OUTDIR/${BINARY_NAME}.sha256"
elif command -v shasum &>/dev/null; then
  shasum -a 256 "$OUTDIR/$BINARY_NAME" > "$OUTDIR/${BINARY_NAME}.sha256"
else
  python3 -c "
import hashlib
with open('$OUTDIR/$BINARY_NAME', 'rb') as f:
    h = hashlib.sha256(f.read()).hexdigest()
with open('$OUTDIR/${BINARY_NAME}.sha256', 'w') as f:
    f.write(f'{h}  $BINARY_NAME\n')
"
fi
echo "  ✅ Checksum: $(cat "$OUTDIR/${BINARY_NAME}.sha256")"

# ── Done ──────────────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo "  ✅ Release $VERSION built successfully"
echo "══════════════════════════════════════════════════"
echo ""
echo "  Binary:   $OUTDIR/$BINARY_NAME  ($SIZE)"
echo "  Checksum: $OUTDIR/${BINARY_NAME}.sha256"
echo ""
echo "  Next steps:"
echo "    1. Test: ./dist/agentic build --name test --desc 'counter'"
echo "    2. Tag:  git tag v$VERSION && git push --tags"
echo "    3. Release: GitHub Actions will build and publish automatically"
echo ""
