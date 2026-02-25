"""
AgentIC Backend API — Premium Chip Studio
Real-time SSE streaming, job management, and chip result reporting.
"""
import asyncio
import json
import os
import sys
import time
import uuid
import glob
import threading
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ─── Python path ────────────────────────────────────────────────────
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# ─── App ─────────────────────────────────────────────────────────────
app = FastAPI(title="AgentIC Backend API", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Job Store ───────────────────────────────────────────────────────
# Structure: { job_id: { status, design_name, events: [], result: {}, cancelled: bool } }
JOB_STORE: Dict[str, Dict[str, Any]] = {}

# Training data output path
TRAINING_JSONL = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "training", "agentic_sft_data.jsonl"))

BUILD_STATES_ORDER = [
    "INIT", "SPEC", "RTL_GEN", "RTL_FIX", "VERIFICATION",
    "FORMAL_VERIFY", "COVERAGE_CHECK", "REGRESSION",
    "FLOORPLAN", "HARDENING", "CONVERGENCE_REVIEW",
    "ECO_PATCH", "SIGNOFF", "SUCCESS",
]
TOTAL_STEPS = len(BUILD_STATES_ORDER)


def _get_llm():
    """Mirrors CLI's get_llm() — tries cloud first, falls back to local.
    Priority: NVIDIA Nemotron → GLM5 Cloud → VeriReason Local
    """
    from agentic.config import NEMOTRON_CONFIG, GLM5_CONFIG, LOCAL_CONFIG
    from crewai import LLM

    configs = [
        ("NVIDIA Nemotron Cloud", NEMOTRON_CONFIG),
        ("Backup GLM5 Cloud",     GLM5_CONFIG),
        ("VeriReason Local",      LOCAL_CONFIG),
    ]

    for name, cfg in configs:
        key = cfg.get("api_key", "")
        # Skip cloud configs with no valid key
        if "Cloud" in name and (not key or key.strip() in ("", "mock-key", "NA")):
            continue
        try:
            extra = {}
            if "nemotron" in cfg["model"].lower():
                extra = {"reasoning_budget": 16384,
                         "chat_template_kwargs": {"enable_thinking": True}}
            elif "glm5" in cfg["model"].lower():
                extra = {"chat_template_kwargs": {"enable_thinking": True, "clear_thinking": False}}

            llm = LLM(
                model=cfg["model"],
                base_url=cfg["base_url"],
                api_key=key if key and key not in ("NA", "") else "mock-key",
                temperature=1.0,
                top_p=1.0,
                max_completion_tokens=16384,
                max_tokens=16384,
                timeout=300,
                extra_body=extra,
            )
            return llm, name
        except Exception:
            continue

    raise RuntimeError("No valid LLM backend found. Check NVIDIA_API_KEY or local Ollama.")


def _emit_event(job_id: str, event_type: str, state: str, message: str, step: int = 0, extra: dict = None):
    """Push a structured event into the job store."""
    if job_id not in JOB_STORE:
        return
    event = {
        "type": event_type,
        "state": state,
        "message": message,
        "step": step,
        "total_steps": TOTAL_STEPS,
        "timestamp": int(time.time()),
        **(extra or {}),
    }
    JOB_STORE[job_id]["events"].append(event)
    # Also update current state
    JOB_STORE[job_id]["current_state"] = state


# ─── Models ──────────────────────────────────────────────────────────
class BuildRequest(BaseModel):
    design_name: str
    description: str
    skip_openlane: bool = False
    full_signoff: bool = False


# ─── Build Runner ────────────────────────────────────────────────────
def _run_agentic_build(job_id: str, design_name: str, description: str, skip_openlane: bool, full_signoff: bool):
    """Runs the full AgentIC build in a background thread, emitting events."""
    try:
        from agentic.orchestrator import BuildOrchestrator

        JOB_STORE[job_id]["status"] = "running"
        _emit_event(job_id, "checkpoint", "INIT", "🚀 Build started — initializing workspace", step=1)

        def event_sink(event: dict):
            """Hook called by orchestrator on every log/transition."""
            state = event.get("state", "UNKNOWN")
            message = event.get("message", "")
            event_type = event.get("type", "log")
            step = BUILD_STATES_ORDER.index(state) + 1 if state in BUILD_STATES_ORDER else 0
            _emit_event(job_id, event_type, state, message, step=step)

        # Use smart LLM selection: Cloud first (Nemotron → GLM5) → Local fallback
        llm, llm_name = _get_llm()
        _emit_event(job_id, "checkpoint", "INIT", f"🤖 LLM selected: {llm_name}", step=1)

        orchestrator = BuildOrchestrator(
            name=design_name,
            desc=description,
            llm=llm,
            skip_openlane=skip_openlane,
            full_signoff=full_signoff,
            event_sink=event_sink,
        )
        orchestrator.run()

        # Check if cancelled mid-build
        if JOB_STORE.get(job_id, {}).get("cancelled"):
            JOB_STORE[job_id]["status"] = "cancelled"
            _emit_event(job_id, "error", "FAIL", "🛑 Build cancelled by user.", step=0)
            return

        # Gather result
        success = orchestrator.state.name == "SUCCESS"
        result = _build_result_summary(orchestrator, design_name, success)
        JOB_STORE[job_id]["result"] = result
        JOB_STORE[job_id]["status"] = "done" if success else "failed"

        final_type = "done" if success else "error"
        final_msg = "✅ Chip build completed successfully!" if success else "❌ Build failed. See logs for details."
        _emit_event(job_id, final_type, orchestrator.state.name, final_msg, step=TOTAL_STEPS)

        # ── Auto-export to training JSONL ──────────────────────────
        _export_training_record(job_id, design_name, description, result, orchestrator)

    except Exception as e:
        import traceback
        err = traceback.format_exc()
        JOB_STORE[job_id]["status"] = "failed"
        JOB_STORE[job_id]["result"] = {"error": str(e), "traceback": err}
        _emit_event(job_id, "error", "FAIL", f"💥 Critical error: {str(e)}", step=0)


def _build_result_summary(orchestrator, design_name: str, success: bool) -> dict:
    """Collect all artifacts and metrics into a summary dict."""
    artifacts = orchestrator.artifacts or {}
    history = orchestrator.build_history or []

    summary = {
        "success": success,
        "design_name": design_name,
        "spec": (artifacts.get("spec") or "")[:2000],
        "rtl_snippet": (artifacts.get("rtl_code") or "")[:1500],
        "paths": {k: v for k, v in artifacts.items() if isinstance(v, str) and os.path.exists(v)},
        "coverage": artifacts.get("coverage", {}),
        "formal_result": artifacts.get("formal_result", ""),
        "signoff_result": artifacts.get("signoff_result", ""),
        "convergence_history": [
            {"iteration": s.iteration, "wns": s.wns, "tns": s.tns,
             "congestion": s.congestion, "area_um2": s.area_um2, "power_w": s.power_w}
            for s in (orchestrator.convergence_history or [])
        ],
        "total_steps": len(history),
        "strategy": orchestrator.strategy.value if orchestrator.strategy else "",
        "build_time_s": int(time.time()) - (history[0].timestamp if history else int(time.time())),
    }

    # Try to read OpenLane metrics
    openlane_root = os.environ.get("OPENLANE_ROOT", os.path.expanduser("~/OpenLane"))
    runs_dir = os.path.join(openlane_root, "designs", design_name, "runs")
    if os.path.exists(runs_dir):
        runs = sorted(os.listdir(runs_dir), reverse=True)
        if runs:
            import csv
            metrics_file = os.path.join(runs_dir, runs[0], "reports", "metrics.csv")
            if os.path.exists(metrics_file):
                try:
                    with open(metrics_file) as f:
                        rows = list(csv.DictReader(f))
                    if rows:
                        last = rows[-1]
                        summary["metrics"] = {
                            "wns": last.get("wns", "N/A"),
                            "area": last.get("DIEAREA_mm^2", "N/A"),
                            "gate_count": last.get("synth_cell_count", "N/A"),
                            "power": _calc_power(last),
                        }
                except Exception:
                    pass

    return summary


def _calc_power(row: dict) -> str:
    try:
        pw = (float(row.get("power_typical_internal_uW", 0)) +
              float(row.get("power_typical_switching_uW", 0)) +
              float(row.get("power_typical_leakage_uW", 0)))
        return f"{pw / 1000:.3f} mW"
    except Exception:
        return "N/A"


def _export_training_record(job_id: str, design_name: str, description: str, result: dict, orchestrator):
    """Append a completed build as a JSONL record for local model training.

    Format is SFT-compatible: one JSON object per line with
    'instruction', 'input', 'output', and metadata fields.
    This feeds directly into training/generate_reasoning.py workflow.
    """
    try:
        os.makedirs(os.path.dirname(TRAINING_JSONL), exist_ok=True)
        history = orchestrator.build_history or []
        log_text = "\n".join(
            f"[{h.state}] {h.message}" for h in history
        )[:8000]

        record = {
            "job_id": job_id,
            "timestamp": int(time.time()),
            "design_name": design_name,
            "instruction": f"Design a digital chip: {description}",
            "input": result.get("spec", "")[:3000],
            "output": result.get("rtl_snippet", "")[:4000],
            "success": result.get("success", False),
            "strategy": result.get("strategy", ""),
            "metrics": result.get("metrics", {}),
            "coverage": result.get("coverage", {}),
            "build_log_excerpt": log_text,
            "source": "agentic_web_build",
        }
        with open(TRAINING_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Never let export errors affect the build result




# ─── Routes ──────────────────────────────────────────────────────────

@app.get("/")
def read_root():
    return {"message": "AgentIC API is online", "version": "3.0.0"}


@app.post("/build")
def trigger_build(req: BuildRequest):
    """Start a new chip build. Returns job_id immediately."""
    # Sanitize design name — Verilog identifiers cannot start with a digit
    import re as _re
    design_name = req.design_name.strip().lower()
    design_name = _re.sub(r'[^a-z0-9_]', '_', design_name)  # keep only safe chars
    design_name = design_name.strip('_')
    design_name = _re.sub(r'_+', '_', design_name)           # collapse doubles
    if design_name and design_name[0].isdigit():
        design_name = 'chip_' + design_name                  # e.g. chip_8bit_risc_cpu
    if not design_name or '..' in design_name or '/' in design_name:
        raise HTTPException(status_code=400, detail="Invalid design name")

    job_id = str(uuid.uuid4())
    JOB_STORE[job_id] = {
        "status": "queued",
        "design_name": design_name,
        "description": req.description,
        "current_state": "INIT",
        "events": [],
        "result": {},
        "created_at": int(time.time()),
    }

    thread = threading.Thread(
        target=_run_agentic_build,
        args=(job_id, design_name, req.description, req.skip_openlane, req.full_signoff),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id, "design_name": design_name, "status": "queued"}


@app.get("/build/status/{job_id}")
def get_build_status(job_id: str):
    """Poll current build status and all events so far."""
    if job_id not in JOB_STORE:
        raise HTTPException(status_code=404, detail="Job not found")
    job = JOB_STORE[job_id]
    return {
        "job_id": job_id,
        "status": job["status"],
        "design_name": job["design_name"],
        "current_state": job["current_state"],
        "events": job["events"],
        "event_count": len(job["events"]),
    }


@app.get("/build/stream/{job_id}")
async def stream_build_events(job_id: str):
    """SSE endpoint — streams live build events as they are emitted."""
    if job_id not in JOB_STORE:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        sent_index = 0
        # Send a ping immediately so the browser knows the connection is alive
        yield "data: {\"type\": \"ping\", \"message\": \"connected\"}\n\n"

        while True:
            job = JOB_STORE.get(job_id)
            if job is None:
                break

            events = job["events"]
            while sent_index < len(events):
                event = events[sent_index]
                yield f"data: {json.dumps(event)}\n\n"
                sent_index += 1

            # Stop streaming when done, failed, or cancelled
            if job["status"] in ("done", "failed", "cancelled") and sent_index >= len(events):
                yield f"data: {json.dumps({'type': 'stream_end', 'status': job['status']})}\n\n"
                break

            await asyncio.sleep(0.4)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/build/result/{job_id}")
def get_build_result(job_id: str):
    """Return the final chip summary after build completes."""
    if job_id not in JOB_STORE:
        raise HTTPException(status_code=404, detail="Job not found")
    job = JOB_STORE[job_id]
    if job["status"] not in ("done", "failed"):
        raise HTTPException(status_code=202, detail="Build still in progress")
    return {"job_id": job_id, "status": job["status"], "result": job["result"]}


@app.get("/jobs")
def list_jobs():
    """List all jobs (for debugging / history)."""
    return {
        "jobs": [
            {
                "job_id": jid,
                "design_name": j["design_name"],
                "status": j["status"],
                "current_state": j["current_state"],
                "created_at": j["created_at"],
                "event_count": len(j["events"]),
            }
            for jid, j in JOB_STORE.items()
        ]
    }


@app.post("/build/cancel/{job_id}")
def cancel_build(job_id: str):
    """Request cancellation of a running build.
    Sets a flag that the build thread checks — the thread exits gracefully
    after its current step completes (cannot hard-kill Python threads).
    """
    if job_id not in JOB_STORE:
        raise HTTPException(status_code=404, detail="Job not found")
    job = JOB_STORE[job_id]
    if job["status"] not in ("queued", "running"):
        return {"ok": False, "message": f"Job already in terminal state: {job['status']}"}
    JOB_STORE[job_id]["cancelled"] = True
    JOB_STORE[job_id]["status"] = "cancelling"
    _emit_event(job_id, "log", job["current_state"], "🛑 Cancellation requested — stopping after current step…", step=0)
    return {"ok": True, "message": "Cancellation requested"}


@app.get("/designs")
def list_designs():
    """List all chip designs on disk."""
    des_dir = os.path.join(os.environ.get("OPENLANE_ROOT", os.path.expanduser("~/OpenLane")), "designs")
    if not os.path.exists(des_dir):
        return {"designs": []}

    designs_info = []
    for d in os.listdir(des_dir):
        d_path = os.path.join(des_dir, d)
        if os.path.isdir(d_path):
            has_gds = False
            runs_dir = os.path.join(d_path, "runs")
            if os.path.exists(runs_dir):
                for run in os.listdir(runs_dir):
                    gds_path = os.path.join(runs_dir, run, "results", "signoff", f"{d}.gds")
                    if os.path.exists(gds_path):
                        has_gds = True
                        break
            designs_info.append({"name": d, "has_gds": has_gds})

    return {"designs": designs_info}


@app.get("/metrics/{design_name}")
def get_metrics(design_name: str):
    """Return latest OpenLane metrics for a design."""
    des_dir = os.path.join(os.environ.get("OPENLANE_ROOT", os.path.expanduser("~/OpenLane")), "designs", design_name)
    runs_dir = os.path.join(des_dir, "runs")

    if not os.path.exists(runs_dir):
        raise HTTPException(status_code=404, detail="No runs found for this design")

    runs = sorted(os.listdir(runs_dir), reverse=True)
    if not runs:
        raise HTTPException(status_code=404, detail="No runs found")

    metrics_file = os.path.join(runs_dir, runs[0], "reports", "metrics.csv")
    if not os.path.exists(metrics_file):
        raise HTTPException(status_code=404, detail="Metrics file not found")

    try:
        import csv
        with open(metrics_file) as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return {"metrics": {}}
        last = rows[-1]
        return {
            "metrics": {
                "wns": last.get("wns", "N/A"),
                "power": _calc_power(last),
                "area": f"{last.get('DIEAREA_mm^2', 'N/A')} mm²",
                "gate_count": last.get("synth_cell_count", "N/A"),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/signoff/{design_name}")
def get_signoff_report(design_name: str):
    try:
        from agentic.tools.vlsi_tools import check_physical_metrics
        metrics, report = check_physical_metrics(design_name)
        return {"success": metrics is not None, "report": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
