# How to Release AgentIC

## Overview

AgentIC has two distribution channels:

| Channel | Command | Audience |
|---------|---------|----------|
| **PyPI** | `pip install agentic-ic` | Developers |
| **GitHub Releases** | Download binary from agentic-model repo | End users |

The source code lives in the **private** `AgentIC` repo.
Built packages are published to the **public** `agentic-model` repo.

---

## Prerequisites

1. GitHub Personal Access Token with `repo` and `write:packages` scopes
2. PyPI API token (stored in `~/.pypirc`)
3. Python 3.11+ with `build` and `twine` installed

---

## Step 1: Bump Version

Update the version number in these 3 files to the same value:

```
pyproject.toml              → version = "0.2.0"
src/agentic/__init__.py     → __version__ = "0.2.0"
npm/package.json            → "version": "0.2.0"
```

---

## Step 2: Build the Wheel

```bash
cd AgentIC
python3 -m build --wheel --no-isolation
```

This creates `dist/agentic_ic-0.2.0-py3-none-any.whl`

---

## Step 3: Publish to PyPI

```bash
python3 -m twine upload dist/agentic_ic-0.2.0-py3-none-any.whl
```

Verify: https://pypi.org/project/agentic-ic/0.2.0/

---

## Step 4: Create GitHub Release on Public Repo

### Option A: Via GitHub Web UI

1. Go to https://github.com/Vickyrrrrrr/agentic-model/releases
2. Click "Draft a new release"
3. Tag: `v0.2.0`
4. Title: `AgentIC v0.2.0`
5. Attach the `.whl` file
6. Publish

### Option B: Via API (script)

Replace `YOUR_GITHUB_TOKEN` with your PAT:

```bash
TOKEN="ghp_xxxxxxxxxxxx"

# Create release
curl -X POST \
  -H "Authorization: token $TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/Vickyrrrrrr/agentic-model/releases \
  -d '{
    "tag_name": "v0.2.0",
    "name": "AgentIC v0.2.0",
    "body": "Release notes here...",
    "draft": false,
    "prerelease": false
  }'

# Get the release ID from the response, then upload the wheel:
RELEASE_ID="..."  # from the response above

curl -X POST \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @dist/agentic_ic-0.2.0-py3-none-any.whl \
  "https://uploads.github.com/repos/Vickyrrrrrr/agentic-model/releases/$RELEASE_ID/assets?name=agentic_ic-0.2.0-py3-none-any.whl"
```

---

## Step 5: Tag the Private Repo (optional)

```bash
git tag v0.2.0
git push --tags
```

This keeps your private repo's tags in sync with the public releases.

---

## User's View

After publishing:

```bash
# From PyPI
pip install agentic-ic

# From GitHub Releases
# Download the .whl file, then:
pip install agentic_ic-0.2.0-py3-none-any.whl
```
