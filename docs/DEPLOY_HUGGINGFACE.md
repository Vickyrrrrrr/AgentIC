# AgentIC — HuggingFace Spaces Deployment Guide

> **Goal:** Run AgentIC 24/7 on HuggingFace Spaces (Docker), with automatic deploys triggered by every `git push` to your private GitHub repo.

---

## Architecture

```
Your PC  ──push──►  GitHub (private repo)
                         │
              GitHub Actions (deploy.yml)
                         │
                    git push --force
                         │
                         ▼
         HuggingFace Spaces (Docker)
         https://vickynishad-AgentIC.hf.space
              uvicorn server.api:app :7860
```

---

## Prerequisites

| Tool | Version |
|---|---|
| Docker | ≥ 24 |
| Git | any |
| Python | 3.11 (inside container) |
| HuggingFace account | [huggingface.co](https://huggingface.co) |
| GitHub repo | Private, branch `main` |

---

## Step 1 — Create the HuggingFace Space

1. Go to https://huggingface.co/new-space
2. **Owner:** `vickynishad`
3. **Space name:** `AgentIC`
4. **SDK:** `Docker`
5. **Visibility:** Public or Private (your choice)
6. Click **Create Space**

The Space will sit empty — the deploy pipeline will populate it.

---

## Step 2 — Generate a HuggingFace Token

1. Go to https://huggingface.co/settings/tokens
2. Click **New token**
3. **Name:** `AgentIC-deploy`
4. **Role:** `write`
5. Copy the token — you'll need it in Steps 3 and 4

---

## Step 3 — Add GitHub Secret

In your GitHub repo:

**Settings → Secrets and variables → Actions → New repository secret**

| Name | Value |
|---|---|
| `HF_TOKEN` | The HuggingFace write token from Step 2 |

---

## Step 4 — Add HuggingFace Space Secrets

In your HF Space:

**Space → Settings → Variables and secrets → New secret**

Add every key from `.env.example` that you need at runtime:

| Secret name | Value |
|---|---|
| `NVIDIA_API_KEY` | Your NVIDIA API key |
| `GROQ_API_KEY` | Your Groq API key (optional) |
| `NVIDIA_MODEL` | e.g. `meta/llama-3.3-70b-instruct` |
| `NVIDIA_BASE_URL` | `https://integrate.api.nvidia.com/v1` |

> HuggingFace injects Space secrets as environment variables at container runtime — **never put API keys in the Dockerfile or commit them to git.**

---

## Step 5 — First Manual Push to HuggingFace

Run once from your machine to seed the Space:

```bash
cd ~/AgentIC

# Add the HF remote (one-time)
git remote add hf https://huggingface:YOUR_HF_TOKEN@huggingface.co/spaces/vickynishad/AgentIC

# Push
git push hf main --force
```

After ~2–5 minutes the Space will build the Docker image and start. Watch build logs at:
`https://huggingface.co/spaces/vickynishad/AgentIC` → **Logs** tab

---

## Step 6 — Automatic CI/CD via GitHub Actions

The file `.github/workflows/deploy.yml` is already configured. Every push to `main`:

1. GitHub Actions checks out your full repo history
2. Force-pushes to `huggingface.co/spaces/vickynishad/AgentIC`
3. HF detects the change and rebuilds the Docker image
4. The new container starts on port 7860

No manual steps needed after the first push.

---

## Local Development & Testing

### Test the Docker build locally

```bash
cd ~/AgentIC

# Build
docker build -t agentic:local .

# Run (uses your local .env file for API keys)
docker compose up

# Verify
curl http://localhost:7860/docs
```

### Stop the local container

```bash
docker compose down
```

### Rebuild after code changes

```bash
docker compose up --build
```

---

## .env File Safety

| File | Committed? | Purpose |
|---|---|---|
| `.env` | **NO** — in `.gitignore` | Your real local secrets |
| `.env.example` | **YES** | Template showing key names only, no values |
| `.dockerignore` | **YES** | Excludes `.env` from Docker build context |

**Rules:**
- Never `git add .env`
- Never paste API keys into `Dockerfile`, `docker-compose.yml`, or any committed file
- Use HF Space Secrets for production keys
- Use GitHub Secrets only for deployment tokens (`HF_TOKEN`)

To verify `.env` is not tracked:
```bash
git ls-files .env    # should print nothing
```

---

## Deployed URL

```
https://vickynishad-AgentIC.hf.space
```

API docs:
```
https://vickynishad-AgentIC.hf.space/docs
```

---

## Optional: Custom Domain (`agentic.buildstack.live`)

HF free tier does not support custom domains natively. Use Cloudflare:

1. In Cloudflare DNS for `buildstack.live`, add:
   ```
   Type:  CNAME
   Name:  agentic
   Target: vickynishad-agentIC.hf.space
   Proxy: ON (orange cloud)
   ```
2. In Cloudflare **Rules → Page Rules** (or a Worker), proxy requests to the HF Space URL

For native custom domain support, upgrade to HF Pro and use:
**Space Settings → Custom domains → Add domain → `agentic.buildstack.live`**
HF will give you a CNAME record to set in your DNS.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Space shows "Building" forever | Check HF Logs tab for apt/pip errors |
| `500` errors on API calls | Check HF Space Secrets — `NVIDIA_API_KEY` missing |
| GitHub Actions fails with `401` | `HF_TOKEN` secret expired or wrong — regenerate at huggingface.co/settings/tokens |
| `port 7860 refused` locally | Run `docker compose up` first; give it 5 seconds to start |
| Container crashes on start | Run `docker logs agentic_local` to see the Python traceback |
