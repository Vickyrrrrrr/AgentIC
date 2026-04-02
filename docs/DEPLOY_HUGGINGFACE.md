# AgentIC — HuggingFace Spaces Deployment Guide

> **This document covers:** what was cleaned from git history, what is safe and what is not, how AgentIC operates on HuggingFace (including HITL), how the CI/CD pipeline works, and how to maintain the setup.

---

## 1. What Was Removed From Git History — And Why It Is Safe

HuggingFace rejects pushes that contain any file >10 MiB **anywhere in the entire commit history**, not just at the latest commit. Three rounds of `git-filter-repo` were run to rewrite history and remove large binary files that had been committed in old commits.

### Files Removed

| Path | What it was | Impact on AgentIC |
|---|---|---|
| `.venv-agentic/` | Local Python virtual environment accidentally committed | **Zero impact.** Docker builds its own isolated venv via `pip install -r requirements.txt` during image build. |
| `designs/minicount/runs/` | OpenLane GDSII build output from a previous run | **Zero impact.** Run outputs are generated fresh per build job, written to the container's local filesystem. |
| `designs/simple_counter/runs/` | OpenLane GDSII build output | **Zero impact.** Same as above. |
| `artifacts/*.gds` | Generated GDS chip layout files | **Zero impact.** GDS files are outputs, not inputs. They are regenerated at runtime. |
| `artifacts/*.vcd` | Simulation waveform files | **Zero impact.** Generated fresh per simulation run. |
| `oss-cad-suite/` | Pre-built EDA tool binaries (~500 MB) | **Zero impact.** Docker installs `verilator`, `iverilog`, `yosys`, and `sby` via `apt` and builds from source inside the image. |

### What Was NOT Removed — Everything AgentIC Needs Is Intact

| Path | Purpose |
|---|---|
| `src/agentic/agents/` | Designer, testbench, verifier, SDC, doc agents |
| `src/agentic/orchestrator.py` | Core build state machine |
| `src/agentic/tools/vlsi_tools.py` | All EDA tool wrappers (iverilog, verilator, yosys, sby) |
| `src/agentic/config.py` | LLM + env config |
| `src/agentic/contracts.py` | AgentResult, StageResult, FailureClass definitions |
| `src/agentic/core/` | ArchitectModule, SelfReflect, ReAct, WaveformExpert, DeepDebugger |
| `src/agentic/golden_lib/templates/` | 8 golden RTL templates (counter, FIFO, FSM, UART, SPI, timer, shift reg, PWM) |
| `server/api.py` | FastAPI backend — all endpoints including HITL approve/reject |
| `server/approval.py` | Human-in-the-loop approval manager |
| `server/stage_summary.py` | Stage event and summary formatting |
| `web/src/` | Full React frontend — DesignStudio, HumanInLoopBuild, Dashboard, etc. |
| `designs/simple_counter/src/*.v` | Reference Verilog + testbench |
| `designs/simple_counter/config.tcl` | OpenLane config template |
| `requirements.txt` | All Python dependencies |
| `scripts/` | CI and setup scripts |

---

## 2. How AgentIC Operates on HuggingFace

### Architecture

```
Browser / Frontend (React)
         │  HTTP + SSE
         ▼
FastAPI  (server/api.py)  ← uvicorn on port 7860
         │
         ├── POST /build          → starts a background build job
         ├── GET  /build/stream/{job_id}  → Server-Sent Events stream
         ├── POST /approve        → HITL approval gate
         ├── POST /reject         → HITL rejection
         └── GET  /build/result/{job_id} → final artifacts
         │
         ▼
Orchestrator (src/agentic/orchestrator.py)
         │
         ├── CrewAI agents  (LLM calls to NVIDIA API)
         ├── EDA tools      (iverilog, verilator, yosys, sby — installed in Docker)
         └── File system    (writes Verilog, configs, logs to /app/designs/ inside container)
```

### What runs where

| Component | Runs on HuggingFace | Notes |
|---|---|---|
| FastAPI server | Yes | `uvicorn server.api:app --host 0.0.0.0 --port 7860` |
| React web app | **No** — served separately | The `web/` frontend must be built and hosted on Vercel/Netlify/Cloudflare Pages, or served statically from FastAPI |
| LLM inference | No — remote API call | Agent calls `NVIDIA_API_KEY` → `integrate.api.nvidia.com` |
| `iverilog` simulation | Yes | Installed in Docker via apt |
| `verilator` coverage | Yes | Installed in Docker via apt |
| `yosys` synthesis | Yes | Installed in Docker via apt |
| `sby` formal verification | Yes | Built from source in Docker |
| OpenLane GDSII hardening | **Optional / skipped by default** | Set `skip_openlane: true` in build request |

### Persistent Storage

HuggingFace Spaces **do not have persistent disk by default**. This means:
- Build artifacts (Verilog files, simulation logs) are written to the container filesystem and **lost when the Space restarts**.
- For persistent storage, mount a HuggingFace Dataset as a volume (HF Pro feature) or use an external object store (S3, R2).
- For demo / RTL-only workflows this is fine — the agent regenerates outputs each run.

---

## 3. HITL (Human-in-the-Loop) Build — Does It Still Work?

**Yes, HITL works exactly as before.** Nothing in the approval flow was removed.

### How HITL Works

The HITL flow is controlled by the `server/approval.py` `ApprovalManager` and the `/approve` / `/reject` API endpoints.

```
Build job starts (POST /build, skip_openlane: false or true)
         │
         ▼
Stage: RTL_GEN → VERIFICATION → FORMAL_VERIFY → COVERAGE → REGRESSION → SDC_GEN
         │
         ▼ (if skip_openlane=false)
Stage: FLOORPLAN
         │  ← pauses here, emits SSE event type: "approval_required"
         ▼
Frontend (HumanInLoopBuild page) shows the stage summary
User clicks Approve or Reject
         │
POST /approve  or  POST /reject
         │
         ▼ (if approved)
Stage: HARDENING → CONVERGENCE_REVIEW → ECO_PATCH → SIGNOFF → SUCCESS
```

### Key endpoints

| Endpoint | Purpose |
|---|---|
| `POST /build` | Start a build. Body: `{ "design_name": "...", "description": "...", "skip_openlane": false }` |
| `GET /build/stream/{job_id}` | SSE stream — receive all stage events, agent thoughts, approval gates in real time |
| `GET /approval/status` | Check if any job is currently waiting for approval |
| `POST /approve` | Approve the pending stage and continue the build |
| `POST /reject` | Reject — build stops with failure record |
| `GET /build/result/{job_id}` | Fetch final Verilog, metrics, logs after completion |

### skip_openlane flag

If you only want RTL generation + verification (no physical implementation), send:
```json
{ "skip_openlane": true }
```
The orchestrator skips FLOORPLAN, HARDENING, SIGNOFF, and CONVERGENCE_REVIEW entirely. **This is the recommended setting on HuggingFace** since OpenLane requires Docker-in-Docker and PDK files which are not included.

---

## 4. CI/CD Pipeline — How deploy.yml Works

File: `.github/workflows/deploy.yml`

```yaml
on:
  push:
    branches:
      - main
```

**Trigger:** Any commit pushed to the `main` branch on GitHub.

### Step-by-step flow

```
Developer pushes to GitHub main
         │
GitHub Actions runner starts (ubuntu-latest)
         │
Step 1: actions/checkout@v4 (fetch-depth: 0)
         └── Clones the full git history with LFS support
         │
Step 2: git config user identity
         └── Sets bot email/name for git operations
         │
Step 3: git remote add hf
         └── Adds HuggingFace remote using HF_TOKEN secret
         │
Step 4: git push hf main --force
         └── Force-pushes to huggingface.co/spaces/vxkyyy/AgentIC
         │
         ▼ (on HuggingFace side)
HF detects new commit → reads Dockerfile → builds Docker image
         └── apt install: verilator, iverilog, yosys + deps
         └── git clone YosysHQ/sby → make install
         └── pip install -r requirements.txt
         └── Starts: uvicorn server.api:app --host 0.0.0.0 --port 7860
         │
Space goes live at https://huggingface.co/spaces/vxkyyy/AgentIC
```

### Secrets used

| Secret | Where set | Used by |
|---|---|---|
| `HF_TOKEN` | GitHub repo → Settings → Secrets | `deploy.yml` to authenticate git push to HF |
| `NVIDIA_API_KEY` | HuggingFace Space → Settings → Secrets | Injected as env var at container runtime |

### What deploy.yml does NOT do

- It does not run tests before deploying (the existing `ci.yml` handles that separately)
- It does not build the Docker image on GitHub — HuggingFace builds it
- It does not push Docker Hub — the image is built inside HF's infrastructure

---

## 5. Setup Checklist

### One-time setup

- [ ] Create HF Space at https://huggingface.co/new-space — SDK: Docker, name: AgentIC
- [ ] Generate HF write token at https://huggingface.co/settings/tokens
- [ ] Add `HF_TOKEN` to GitHub repo Secrets
- [ ] Add `NVIDIA_API_KEY` to HF Space Secrets
- [ ] Add `NVIDIA_MODEL` = `meta/llama-3.3-70b-instruct` to HF Space Secrets
- [ ] Add `NVIDIA_BASE_URL` = `https://integrate.api.nvidia.com/v1` to HF Space Secrets
- [ ] First push: `git push hf main --force`

### Every deploy after that

```bash
git add .
git commit -m "feat: ..."
git push origin main   # GitHub Actions auto-deploys to HF
```

---

## 6. Local Testing

```bash
cd ~/AgentIC

# Build
docker build -t agentic:local .

# Run (reads your .env file)
docker compose up

# API docs
curl http://localhost:7860/docs

# Stop
docker compose down

# Rebuild after code change
docker compose up --build
```

---

## 7. .env File Safety

| File | Committed to git | Purpose |
|---|---|---|
| `.env` | **NO** (in `.gitignore`) | Your real local API keys |
| `.env.example` | **YES** (no values) | Documents which keys are needed |
| `.dockerignore` | **YES** | Prevents `.env` entering Docker build context |

To verify `.env` is safe:
```bash
git ls-files .env     # must print nothing
```

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| HF Space shows "Building" for >10 min | apt/pip install failure | Check HF Logs tab |
| `ModuleNotFoundError: server` | Wrong uvicorn module path | CMD in Dockerfile must be `server.api:app` |
| `500` on all API calls | `NVIDIA_API_KEY` missing | Add it to HF Space Secrets |
| GitHub Actions `401` | `HF_TOKEN` expired or wrong user | Regenerate token, update GitHub Secret |
| HITL approval never triggers | `skip_openlane: true` in request | Physical stages are skipped — HITL only fires before FLOORPLAN |
| Build artifacts gone after restart | Container filesystem is ephemeral | Expected on free HF tier — use HF Datasets volume for persistence |
| `port 7860 refused` locally | Container not started | Run `docker compose up` first |

## Important Note on OpenLane (Physical Design) in Hugging Face
Hugging Face Spaces strictly **prohibits Docker-in-Docker** (spawning new containers inside a container) for security reasons. Because the standard AgentIC flow runs OpenLane by spinning up the an `efabless/openlane` Docker container on the fly, **the final Physical Design/Layout (GDS) stage will fail on standard Hugging Face Spaces.**

### What WORKS on Hugging Face Backend:
- Spec Generation & Hierarchy Planning
- AI RTL Generation & Syntax Fixing
- UVM/Testbench Verification & Iterative Bug Fixing
- Verilator & Icarus Verilog Simulation
- Waveform Generation (`.vcd` & GTKWave integration)
- Yosys Logic Synthesis & Equivalency Checks

### What DOES NOT WORK on Hugging Face Backend:
- OpenLane Hardening (Floorplanning, Placement, Routing, GDS generation).

*If you specifically require OpenLane routing on Hugging Face, you must build a custom monolithic image using `FROM efabless/openlane:current` and install Python natively, effectively bypassing the Docker-in-Docker requirement.*
