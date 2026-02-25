# AgentIC Web App — Complete Guide

> **How the premium chip design web interface was built and how every piece fits together.**

---

## Table of Contents

1. [What Was Built](#what-was-built)
2. [Architecture Overview](#architecture-overview)
3. [Backend — `server/api.py`](#backend--serverapipy)
4. [Orchestrator Hook — `src/agentic/orchestrator.py`](#orchestrator-hook--srcagenticorchestratorpy)
5. [Frontend — `web/src/`](#frontend--websrc)
6. [LLM Selection](#llm-selection)
7. [Where Data Is Saved](#where-data-is-saved)
8. [Training Data Auto-Export](#training-data-auto-export)
9. [Cancel a Build](#cancel-a-build)
10. [Running the App](#running-the-app)
11. [API Reference](#api-reference)

---

## What Was Built

The original AgentIC was a **CLI-only tool**: you typed a command and watched logs scroll by. We transformed it into a full-stack web application with:

| Feature | Description |
|---|---|
| **NLP Prompt UI** | Type a chip description in plain English, hit Launch |
| **Live Build Monitor** | See every stage of the build pipeline in real-time as it runs |
| **Cancel Button** | Stop a running build from the browser |
| **Chip Summary** | After completion, view die area, WNS, power, gate count, RTL preview |
| **Browser Notifications** | Get a popup when the build finishes, even if you've switched tabs |
| **Training Auto-Export** | Every build is appended to a JSONL file for fine-tuning VeriReason |
| **Claude-style Theme** | Warm off-white (#F7F4EF), terracotta accent (#C96442), zero neon |

---

## Architecture Overview

```
┌─────────────────────┐         HTTP / SSE         ┌──────────────────────┐
│  Browser (Vite UI)  │ ◄────────────────────────► │  FastAPI Backend      │
│  localhost:5173     │                             │  localhost:8000       │
│                     │                             │                       │
│  DesignStudio.tsx   │  POST /build                │  _run_agentic_build() │
│  BuildMonitor.tsx   │  GET  /build/stream/{id}    │  (background thread)  │
│  ChipSummary.tsx    │  POST /build/cancel/{id}    │                       │
│                     │  GET  /build/result/{id}    │  BuildOrchestrator    │
└─────────────────────┘                             │  (orchestrator.py)    │
                                                    └──────────────────────┘
                                                             │
                                                             ▼
                                                    ~/OpenLane/designs/<name>/
                                                    training/agentic_sft_data.jsonl
```

**Key concept: Server-Sent Events (SSE)**  
Instead of the frontend constantly asking "are we done yet?", the backend *pushes* updates to the browser the moment they happen. This is called SSE (Server-Sent Events) — it's like a one-way live stream from server → browser over a normal HTTP connection.

---

## Backend — `server/api.py`

### The Job Store

```python
JOB_STORE: Dict[str, Dict[str, Any]] = {}
```

This is an **in-memory dictionary** that holds the state of every build job. Each entry looks like:

```python
{
  "status": "running",          # queued | running | cancelling | done | failed | cancelled
  "design_name": "my_counter",
  "current_state": "RTL_GEN",  # current orchestrator stage
  "events": [...],              # list of all events emitted so far
  "result": {...},              # filled in at the end
  "cancelled": False,           # cancel flag checked by the build thread
  "created_at": 1709123456,
}
```

> ⚠️ **The Job Store is in RAM only.** It resets when the server restarts. The actual chip files (RTL, GDSII, logs) are on disk in `~/OpenLane/designs/` and persist permanently.

---

### `_get_llm()` — Smart LLM Selection

```python
def _get_llm():
    configs = [
        ("NVIDIA Nemotron Cloud", NEMOTRON_CONFIG),  # tries first
        ("Backup GLM5 Cloud",     GLM5_CONFIG),       # second
        ("VeriReason Local",      LOCAL_CONFIG),       # last resort
    ]
    for name, cfg in configs:
        # skip cloud if no API key
        # try to instantiate LLM
        # return first one that works
```

This mirrors the exact same logic as the CLI's `get_llm()`. So when you click **Launch** in the browser, it uses **the same LLM priority** as running `python main.py build` from the terminal.

With your `.env` file having `NVIDIA_API_KEY` set → it always picks **Nemotron Cloud first**.

---

### `_emit_event()` — Pushing Events

```python
def _emit_event(job_id, event_type, state, message, step=0):
    event = {"type": ..., "state": ..., "message": ..., "timestamp": ...}
    JOB_STORE[job_id]["events"].append(event)
```

Every time something happens in the build (a state change, a log message, an error), this function is called. It appends a structured event to the job's event list. The SSE stream then picks these up and sends them to the browser.

**Event types:**
- `checkpoint` — an important milestone (e.g. "Starting RTL generation")
- `transition` — the orchestrator moved to a new state (e.g. `RTL_GEN → VERIFICATION`)  
- `log` — a regular log message
- `error` — something went wrong
- `done` / `stream_end` — build finished

---

### `_run_agentic_build()` — Background Thread

```python
def _run_agentic_build(job_id, design_name, description, ...):
    llm, llm_name = _get_llm()

    def event_sink(event: dict):
        # receives events from the orchestrator
        _emit_event(job_id, ...)

    orchestrator = BuildOrchestrator(
        ...,
        event_sink=event_sink,   # the live hook
    )
    orchestrator.run()

    # after build finishes:
    result = _build_result_summary(orchestrator, ...)
    _export_training_record(...)  # save to JSONL
```

This runs in a **separate Python thread** (via `threading.Thread`) so the API doesn't block. When you call `POST /build`, it starts this thread immediately and returns `job_id` to the browser — the browser then connects to the SSE stream to receive live updates.

---

### SSE Stream — `GET /build/stream/{job_id}`

```python
async def event_generator():
    sent_index = 0
    while True:
        events = JOB_STORE[job_id]["events"]
        while sent_index < len(events):
            yield f"data: {json.dumps(events[sent_index])}\n\n"
            sent_index += 1
        if job["status"] in ("done", "failed", "cancelled"):
            yield f"data: {json.dumps({'type': 'stream_end', ...})}\n\n"
            break
        await asyncio.sleep(0.4)   # poll every 400ms
```

The browser holds this connection open. Every 400ms the server checks if there are new events and sends them. When the build finishes, it sends a `stream_end` event and closes the connection.

---

### Training Export — `_export_training_record()`

```python
record = {
    "instruction": "Design a digital chip: <your prompt>",
    "input": "<architecture spec>",        # chip spec written by the LLM
    "output": "<RTL verilog code>",        # generated RTL
    "success": True/False,
    "metrics": {"wns": ..., "area": ...},
    "build_log_excerpt": "...",            # first 8000 chars of build log
    "source": "agentic_web_build",
}
# appended to: training/agentic_sft_data.jsonl
```

Every completed build (success or failure) is saved as one line in `training/agentic_sft_data.jsonl`. This is **SFT (Supervised Fine-Tuning) format** — exactly what `training/generate_reasoning.py` expects. You can use it to fine-tune VeriReason to get better at the kinds of chips you build.

---

## Orchestrator Hook — `src/agentic/orchestrator.py`

### What Changed

Only **3 things** were added (all backward-compatible — the CLI is unaffected):

**1. New `event_sink` parameter in `__init__`:**
```python
def __init__(self, ..., event_sink=None):
    self.event_sink = event_sink  # None when called from CLI
```

**2. In `log()` — fires on every log message:**
```python
def log(self, message, refined=False):
    # ... existing logging ...
    if self.event_sink is not None:
        self.event_sink({"type": "checkpoint" if refined else "log",
                         "state": self.state.name, "message": message})
```

**3. In `transition()` — fires on every state change:**
```python
def transition(self, new_state):
    # ... existing logic ...
    if self.event_sink is not None:
        self.event_sink({"type": "transition",
                         "state": new_state.name, "message": f"▶ {new_state.value}"})
```

**Why this approach?** The orchestrator has 3000+ lines. Instead of rewriting it, we added a single "callback hook". When the orchestrator calls `self.log(...)` or `self.transition(...)`, the hook fires and sends the event to the web backend. The CLI never passes `event_sink`, so it gets `None` and nothing changes for existing CLI users.

---

## Frontend — `web/src/`

### Three-Phase Flow

The UI works as a **state machine with 3 phases**:

```
Phase 1: prompt  ──►  Phase 2: building  ──►  Phase 3: done
  (type desc)           (watch build)           (see results)
```

All managed in `DesignStudio.tsx` with a single `phase` state variable.

---

### `DesignStudio.tsx` — The Controller

This is the **parent component** that owns all state and coordinates the 3 phases.

**Key state variables:**
```typescript
const [phase, setPhase] = useState('prompt');    // which screen to show
const [jobId, setJobId] = useState('');          // the server-side job ID
const [events, setEvents] = useState([]);        // all SSE events received
const [jobStatus, setJobStatus] = useState('queued');
const [result, setResult] = useState(null);      // final chip data
```

**How the SSE connection works:**
```typescript
const es = new EventSource(`http://localhost:8000/build/stream/${jobId}`);
es.onmessage = (evt) => {
    const data = JSON.parse(evt.data);
    if (data.type === 'stream_end') {
        // build finished — fetch full result
        fetchResult(jobId, data.status);
    } else {
        setEvents(prev => [...prev, data]);  // add to the timeline
    }
};
```

`EventSource` is a **built-in browser API** — no extra library needed. It automatically reconnects if the connection drops.

---

### `BuildMonitor.tsx` — The Live View

Shows the **checkpoint timeline** (pipeline stages) and the **live terminal log**.

**Checkpoint timeline logic:**
- Loops through all 14 pipeline stages (INIT → SUCCESS)
- If `state < currentState` → show green checkmark (done)
- If `state === currentState` → show spinning ring (active)
- If `state > currentState` → show empty circle (pending)

**Cancel button:**
```typescript
const handleCancel = async () => {
    await axios.post(`http://localhost:8000/build/cancel/${jobId}`);
};
```
Sends a POST request to the backend, which sets `JOB_STORE[job_id]["cancelled"] = True`. The build thread checks this flag between orchestrator steps and exits gracefully.

> **Note:** Python threads can't be killed mid-operation. The cancel takes effect **after the current step finishes**. If the LLM is in the middle of generating RTL, it will finish that generation, then stop before the next step.

---

### `ChipSummary.tsx` — The Results Page

Displays after the build completes:
- **Silicon metrics** (WNS, die area, power, gate count) from `~/OpenLane/designs/<name>/runs/.../metrics.csv`
- **Architecture spec** — the text spec the LLM wrote for your chip
- **RTL preview** — first 1200 chars of the generated Verilog
- **Convergence table** — WNS/TNS/congestion across OpenLane iterations
- **Error details** — if the build failed

---

### `index.css` — Claude-Inspired Theme

The design tokens mirror Claude.ai's visual language:

```css
:root {
  --bg:      #F7F4EF;   /* warm off-white parchment */
  --accent:  #C96442;   /* Claude's terracotta orange */
  --text:    #1A1817;   /* near-black ink */
  --border:  #E2DDD7;   /* warm light grey */
}
```

No neon, no glassmorphism, no glows. Cards have a 1px warm border and a soft `box-shadow`. Typography is Inter (same as Claude). The terminal inside the Build Monitor uses the dark theme (`var(--text)` background with `#D4CFC8` text) as a deliberate contrast island.

---

## LLM Selection

```
Browser clicks "Launch"
       │
       ▼
POST /build
       │
       ▼
_get_llm() tries in order:
  1. NVIDIA Nemotron  (if NVIDIA_API_KEY is set in .env)  ← your setup uses this
  2. GLM5 Cloud       (if above fails)
  3. VeriReason Local (last resort — Ollama at localhost:11434)
       │
       ▼
BuildOrchestrator(llm=<chosen llm>, event_sink=...)
```

The first log line in the Build Monitor terminal will always say which LLM was selected:
```
🤖 LLM selected: NVIDIA Nemotron Cloud
```

---

## Where Data Is Saved

| Data | Location | Persists after restart? |
|---|---|---|
| Job status & events | RAM (`JOB_STORE`) | ❌ No |
| Chip RTL code | `~/OpenLane/designs/<name>/src/<name>.v` | ✅ Yes |
| Testbench | `~/OpenLane/designs/<name>/src/<name>_tb.v` | ✅ Yes |
| Build log | `~/OpenLane/designs/<name>/<name>.log` | ✅ Yes |
| GDSII layout | `~/OpenLane/designs/<name>/runs/<run>/results/final/` | ✅ Yes |
| Metrics CSV | `~/OpenLane/designs/<name>/runs/<run>/reports/metrics.csv` | ✅ Yes |
| **Training JSONL** | `AgentIC/training/agentic_sft_data.jsonl` | ✅ Yes, appended |

---

## Training Data Auto-Export

After every build (success **or** failure), this record is appended to `training/agentic_sft_data.jsonl`:

```jsonl
{
  "instruction": "Design a digital chip: 8-bit counter with synchronous reset",
  "input": "# Architecture Spec\n## Module: counter\n...",
  "output": "module counter (\n  input clk, rst_n, en,\n...",
  "success": true,
  "metrics": {"wns": "0.12", "area": "0.003 mm²", ...},
  "build_log_excerpt": "[INIT] Initializing workspace\n[SPEC] ...",
  "source": "agentic_web_build"
}
```

**How to use it for fine-tuning VeriReason:**
```bash
# Existing script — reads training/agentic_sft_data.jsonl automatically
python training/generate_reasoning.py

# Then fine-tune
python training/train_verireason.py
```

The format is **Alpaca-style SFT**: `instruction` + `input` + `output`. This is the standard format used by Unsloth, LLaMA-Factory, and most fine-tuning frameworks.

---

## Cancel a Build

**From the browser:** Click the **✕ Cancel** button in the Build Monitor header.

**From the terminal:**
```bash
curl -X POST http://localhost:8000/build/cancel/<job_id>
```

**What happens:**
1. Backend sets `JOB_STORE[job_id]["cancelled"] = True`
2. The build thread checks this flag after each orchestrator step
3. The thread exits, logs `"🛑 Build cancelled by user."`
4. The SSE stream closes, the UI transitions to Phase 3 (results page) showing cancellation

---

## Running the App

**Backend** (port 8000):
```bash
source .venv-agentic/bin/activate
python -m uvicorn server.api:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend** (port 5173):
```bash
cd web
node node_modules/vite/bin/vite.js --host 0.0.0.0 --port 5173
```

Open **http://localhost:5173** → you land directly on the Design Studio.

---

## API Reference

| Method | Endpoint | What it does |
|---|---|---|
| `POST` | `/build` | Start a build. Body: `{design_name, description, skip_openlane, full_signoff}`. Returns `{job_id}` |
| `GET` | `/build/stream/{job_id}` | SSE stream of live build events |
| `GET` | `/build/status/{job_id}` | Poll current status + all events |
| `GET` | `/build/result/{job_id}` | Get final chip summary (only when done/failed) |
| `POST` | `/build/cancel/{job_id}` | Request graceful cancellation |
| `GET` | `/jobs` | List all jobs in the current session |
| `GET` | `/designs` | List all chip designs on disk |
| `GET` | `/signoff/{name}` | Run signoff check on an existing design |
| `GET` | `/metrics/{name}` | Get OpenLane metrics for a design |
| `GET` | `/` | Health check |
