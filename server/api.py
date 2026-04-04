"""
AgentIC Backend API — Premium Chip Studio
Real-time SSE streaming, job management, human-in-the-loop approval, and chip result reporting.
"""
import asyncio
import json
import os
import re
import sys
import os

# Force API server mode globally so orchestrator.py respects HITL loops
os.environ["AGENTIC_API_SERVER"] = "1"

# Add src to Python path so 'agentic' module can be found when run locally or via docker
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
import time
import uuid
import glob
import io
import threading
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from server.approval import approval_manager
from server.auth import (
    AUTH_ENABLED,
    check_build_allowed,
    encrypt_api_key,
    get_current_user,
    get_llm_key_for_user,
    get_byok_config_for_user,
    record_build_failure,
    record_build_start,
    record_build_success,
)
from server.billing import router as billing_router
from server.lab import router as lab_router
from server.report_gen import (
    generate_stage_report_pdf,
    generate_stage_report_docx,
    generate_full_report_pdf,
    generate_full_report_docx,
)
from server.stage_summary import (
    build_stage_complete_payload,
    get_next_stage,
    STAGE_DESCRIPTIONS,
    STAGE_HUMAN_NAMES,
    generate_failure_explanation,
    get_stage_log_summary,
)

# ─── Python path ────────────────────────────────────────────────────
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# ─── App ─────────────────────────────────────────────────────────────
app = FastAPI(title="AgentIC Backend API", version="3.0.0")
app.include_router(billing_router)
app.include_router(lab_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Allow your Vercel frontend to proxy requests securely
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Job Store ───────────────────────────────────────────────────────
# Structure: { job_id: { status, design_name, events: [], result: {}, cancelled: bool } }
JOB_STORE: Dict[str, Dict[str, Any]] = {}

try:
    from .db import SessionLocal, Job as DBJob
except ImportError:
    SessionLocal = None
    pass

def _sync_job_to_db(job_id: str):
    """Safely mirrors JOB_STORE dictionary changes into the persistent Database."""
    if not SessionLocal:
        return
    try:
        job_data = JOB_STORE.get(job_id)
        if not job_data:
            return
            
        with SessionLocal() as db:
            db_job = db.query(DBJob).filter(DBJob.id == job_id).first()
            if not db_job:
                db_job = DBJob(id=job_id)
                db.add(db_job)
            
            db_job.design_name = job_data.get("design_name", "")
            db_job.status = job_data.get("status", "pending")
            db_job.build_status = job_data.get("build_status", "pending")
            db_job.human_in_loop = job_data.get("human_in_loop", False)
            db_job.waiting_approval = job_data.get("waiting_approval", False)
            db_job.waiting_stage = job_data.get("waiting_stage", "")
            
            # Using copy for JSON safely
            db_job.events = job_data.get("events", [])
            db_job.stages = job_data.get("stages", {})
            db_job.request_data = job_data.get("request_data", {})
            db_job.result = job_data.get("result", None)
            
            db.commit()
    except Exception as e:
        logger.error(f"Failed to sync {job_id} to DB: {e}")

def _pull_job_from_db(job_id: str):
    """Safely reads the background Worker's Database changes back into the API memory."""
    if not SessionLocal:
        return
    try:
        with SessionLocal() as db:
            db_job = db.query(DBJob).filter(DBJob.id == job_id).first()
            if db_job:
                if job_id not in JOB_STORE:
                    JOB_STORE[job_id] = {}
                JOB_STORE[job_id].update({
                    "design_name": db_job.design_name or JOB_STORE[job_id].get("design_name", ""),
                    "status": db_job.status or "pending",
                    "build_status": db_job.build_status or "pending",
                    "human_in_loop": db_job.human_in_loop,
                    "waiting_approval": db_job.waiting_approval,
                    "waiting_stage": db_job.waiting_stage,
                    "events": db_job.events or [],
                    "stages": db_job.stages or {},
                    "result": db_job.result or {},
                    "current_state": db_job.events[-1].get("state", "UNKNOWN") if db_job.events else "INIT",
                })
    except Exception as e:
        logger.error(f"Failed to pull {job_id} from DB: {e}")

# Registry for active orchestrator instances to support HITL interaction
RUNNING_ORCHESTRATORS: Dict[str, Any] = {}

# Training data output path
TRAINING_JSONL = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "training", "agentic_sft_data.jsonl"))

BUILD_STATES_ORDER = [
    "INIT", "SPEC", "SPEC_VALIDATE", "HIERARCHY_EXPAND", "FEASIBILITY_CHECK", "CDC_ANALYZE", "VERIFICATION_PLAN", "RTL_GEN", "RTL_FIX", "VERIFICATION",
    "FORMAL_VERIFY", "COVERAGE_CHECK", "REGRESSION",
    "SDC_GEN",
    "FLOORPLAN", "HARDENING", "CONVERGENCE_REVIEW",
    "ECO_PATCH", "SIGNOFF", "SUCCESS",
]
TOTAL_STEPS = len(BUILD_STATES_ORDER)

STAGE_META: Dict[str, Dict[str, str]] = {
    "INIT": {"label": "Initializing Workspace", "icon": "🔧"},
    "SPEC": {"label": "Architectural Planning", "icon": "📐"},
    "SPEC_VALIDATE": {"label": "Specification Validation", "icon": "🔍"},
    "HIERARCHY_EXPAND": {"label": "Hierarchy Expansion", "icon": "🌲"},
    "FEASIBILITY_CHECK": {"label": "Feasibility Check", "icon": "⚖️"},
    "CDC_ANALYZE": {"label": "CDC Analysis", "icon": "🔀"},
    "VERIFICATION_PLAN": {"label": "Verification Planning", "icon": "📋"},
    "RTL_GEN": {"label": "RTL Generation", "icon": "💻"},
    "RTL_FIX": {"label": "RTL Syntax Fixing", "icon": "🔨"},
    "VERIFICATION": {"label": "Verification & Testbench", "icon": "🧪"},
    "FORMAL_VERIFY": {"label": "Formal Verification", "icon": "📊"},
    "COVERAGE_CHECK": {"label": "Coverage Analysis", "icon": "📈"},
    "REGRESSION": {"label": "Regression Testing", "icon": "🔁"},
    "SDC_GEN": {"label": "SDC Generation", "icon": "🕒"},
    "FLOORPLAN": {"label": "Floorplanning", "icon": "🗺️"},
    "HARDENING": {"label": "GDSII Hardening", "icon": "🏗️"},
    "CONVERGENCE_REVIEW": {"label": "Convergence Review", "icon": "🎯"},
    "ECO_PATCH": {"label": "ECO Patch", "icon": "🩹"},
    "SIGNOFF": {"label": "DRC/LVS Signoff", "icon": "✅"},
    "SUCCESS": {"label": "Build Complete", "icon": "🎉"},
    "FAIL": {"label": "Build Failed", "icon": "❌"},
}


def _get_llm(byok_config: dict = None):
    try:
        from crewai import LLM
    except Exception as imp_err:
        raise RuntimeError(f"Cannot import crewai.LLM: {imp_err}")

    # If BYOK config is provided and has group1, try to use it directly
    if byok_config and "group1" in byok_config and byok_config["group1"].get("api_key"):
        g = byok_config["group1"]
        model = g.get("model", "")
        _KNOWN = ("openai/", "groq/", "ollama/", "anthropic/", "nvidia_nim/", "azure/", "huggingface/", "together_ai/", "mistral/")
        if g.get("base_url") and not any(model.startswith(p) for p in _KNOWN):
             model = f"openai/{model}"
        llm_kwargs = dict(model=model, api_key=g.get("api_key"), temperature=0.6, max_tokens=16384)
        if g.get("base_url"):
            llm_kwargs["base_url"] = g.get("base_url")
        try:
            return LLM(**llm_kwargs), f"{model} (BYOK)"
        except Exception as e:
            raise RuntimeError(f"BYOK Compute Engine Failed: {e}")

    # Fallback to existing cloud configs
    from agentic.config import CLOUD_CONFIG, GROQ_CONFIG, LOCAL_CONFIG, GLM_CONFIG

    configs = [
        ("ZhipuAI Compute Engine", GLM_CONFIG),
        ("NVIDIA Compute Engine",  CLOUD_CONFIG),
        ("Groq Compute Engine",    GROQ_CONFIG),
        ("Local Compute Engine",   LOCAL_CONFIG),
    ]

    backend_errors: list = []
    
    # If legacy BYOK key acts as fallback string
    byok_api_key = byok_config.get("group1", {}).get("api_key", "") if byok_config else ""
    
    for name, cfg in configs:
        is_local = "Local" in name
        key = byok_api_key if (byok_api_key and not is_local) else cfg.get("api_key", "")
        if not is_local and (not cfg.get("api_key", "") or cfg.get("api_key", "").strip() in ("", "mock-key", "NA")):
            if not (byok_api_key and ((byok_api_key.startswith("gsk_") and "Groq" in name) or (byok_api_key.startswith("nvapi-") and "NVIDIA" in name) or ("ZhipuAI" in name and not byok_api_key.startswith("gsk_") and not byok_api_key.startswith("nvapi-")))):
                backend_errors.append(f"{name}: skipped – no API key")
                continue
        try:
            model = cfg["model"]
            _KNOWN = ("openai/", "groq/", "ollama/", "anthropic/", "nvidia_nim/", "azure/", "huggingface/", "together_ai/", "mistral/")
            if cfg.get("base_url") and not any(model.startswith(p) for p in _KNOWN):
                model = f"openai/{model}"

            if byok_api_key and not is_local:
                if byok_api_key.startswith("gsk_") and "Groq" not in name:
                    pass
                elif byok_api_key.startswith("nvapi-") and "NVIDIA" not in name:
                    pass
                else:
                    key = byok_api_key

            llm_kwargs: dict = dict(model=model, api_key=key, temperature=0.6)
            if cfg.get("base_url"):
                llm_kwargs["base_url"] = cfg["base_url"]

            llm = LLM(**llm_kwargs)
            return llm, name
        except Exception as e:
            backend_errors.append(f"{name} ({cfg.get('model','?')}): {type(e).__name__}: {e}")
            continue

    raise RuntimeError("No valid LLM backend found. " + " | ".join(backend_errors))


def _get_role_llm_map(byok_config: dict = None) -> Dict[str, Any]:
    from agentic.config import get_role_llm_config
    from crewai import LLM
    
    roles = ["architect", "designer", "testbench_designer", "verifier", "fixer", "debugger", "manager", "physical"]
    role_map = {}
    
    for role in roles:
        if byok_config and "group1" in byok_config:
            if role in ("architect", "debugger", "manager"):
                g = byok_config.get("group1", {})
            elif role in ("designer", "testbench_designer", "verifier"):
                g = byok_config.get("group2", {})
            else:
                g = byok_config.get("group3", {})
                
            if g and g.get("api_key"):
                model = g.get("model", "")
                _KNOWN = ("openai/", "groq/", "ollama/", "anthropic/", "nvidia_nim/", "azure/", "huggingface/", "together_ai/", "mistral/")
                if g.get("base_url") and not any(model.startswith(p) for p in _KNOWN):
                     model = f"openai/{model}"
                llm_kwargs = dict(model=model, api_key=g.get("api_key"), temperature=0.6)
                if g.get("base_url"):
                    llm_kwargs["base_url"] = g.get("base_url")
                role_map[role] = LLM(**llm_kwargs)
                continue
                
        # fallback behavior
        cfg = get_role_llm_config(role)
        byok_key = byok_config.get("group1", {}).get("api_key", "") if byok_config else ""
        key = byok_key if byok_key else cfg.get("api_key", "")
        if not key or key in ("mock-key", "NA"):
            key = cfg.get("api_key", "")
            
        model = cfg["model"]
        llm_kwargs = dict(model=model, api_key=key, temperature=0.6)
        if cfg.get("base_url"):
            llm_kwargs["base_url"] = cfg["base_url"]
            
        role_map[role] = LLM(**llm_kwargs)
        
    return role_map


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
    _sync_job_to_db(job_id)
    # Also update current state
    JOB_STORE[job_id]["current_state"] = state


def _emit_agent_thought(job_id: str, agent_name: str, thought_type: str, content: str, state: str = ""):
    """Emit a real-time agent thought event for the activity feed."""
    if job_id not in JOB_STORE:
        return
    event = {
        "type": "agent_thought",
        "agent_name": agent_name,
        "thought_type": thought_type,
        "content": content,
        "state": state or JOB_STORE[job_id].get("current_state", "UNKNOWN"),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "step": 0,
        "total_steps": TOTAL_STEPS,
        "message": f"[{agent_name}] {content[:200]}",
    }
    JOB_STORE[job_id]["events"].append(event)
    _sync_job_to_db(job_id)


def _emit_agent_thinking(job_id: str, agent_name: str, message: str, state: str = ""):
    """Emit an agent_thinking event to show a pulsing thinking indicator in the frontend.
    
    This is emitted at the start of any long-running LLM call and automatically
    superseded when the next real log entry arrives.
    """
    if job_id not in JOB_STORE:
        return
    event = {
        "type": "agent_thinking",
        "agent_name": agent_name,
        "message": message,
        "content": message,
        "state": state or JOB_STORE[job_id].get("current_state", "UNKNOWN"),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "step": 0,
        "total_steps": TOTAL_STEPS,
    }
    JOB_STORE[job_id]["events"].append(event)
    _sync_job_to_db(job_id)


def _emit_stage_complete(job_id: str, payload: dict):
    """Emit a stage_complete event with full approval card data."""
    if job_id not in JOB_STORE:
        return
    event = {
        **payload,
        "type": "stage_complete",
        "step": BUILD_STATES_ORDER.index(payload.get("stage_name", "INIT")) + 1 if payload.get("stage_name") in BUILD_STATES_ORDER else 0,
        "total_steps": TOTAL_STEPS,
        "state": payload.get("stage_name", "UNKNOWN"),
        "message": f"✋ Stage {payload.get('stage_name', '')} complete — awaiting approval",
    }
    JOB_STORE[job_id]["events"].append(event)
    _sync_job_to_db(job_id)
    JOB_STORE[job_id]["current_state"] = payload.get("stage_name", "UNKNOWN")
    JOB_STORE[job_id]["waiting_approval"] = True
    JOB_STORE[job_id]["waiting_stage"] = payload.get("stage_name", "")
    # Store payload so report endpoints can access it
    stage_name = payload.get("stage_name", "UNKNOWN")
    JOB_STORE[job_id].setdefault("stages", {})[stage_name] = payload


# ─── Models ──────────────────────────────────────────────────────────
class BuildRequest(BaseModel):
    api_key: Optional[str] = None
    design_name: str
    description: str
    skip_openlane: bool = False
    skip_coverage: bool = False
    full_signoff: bool = False
    max_retries: int = 5
    show_thinking: bool = False
    min_coverage: float = 80.0
    strict_gates: bool = False
    pdk_profile: str = "sky130"
    max_pivots: int = 2
    congestion_threshold: float = 10.0
    hierarchical: str = "auto"
    tb_gate_mode: str = "strict"
    tb_max_retries: int = 3
    tb_fallback_template: str = "uvm_lite"
    coverage_backend: str = "auto"  # From SIM_BACKEND_DEFAULT
    coverage_fallback_policy: str = "fail_closed"  # From COVERAGE_FALLBACK_POLICY_DEFAULT
    coverage_profile: str = "balanced"  # From COVERAGE_PROFILE_DEFAULT
    human_in_loop: bool = False  # Enable human-in-the-loop approval (HITL Build page sends True)
    skip_stages: List[str] = []  # Stages to skip (from build mode selector UI)


class ApproveRequest(BaseModel):
    stage: str
    design_name: str


class RejectRequest(BaseModel):
    stage: str
    design_name: str
    feedback: Optional[str] = None


class BuildElaborateRequest(BaseModel):
    job_id: str
    choice: str  # "1", "2", "3" or custom text


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


_SAFE_DESIGN_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")


def _validate_design_name(design_name: str) -> None:
    """Raise 400 if design_name contains path-traversal characters or unsafe patterns."""
    if not design_name or not _SAFE_DESIGN_NAME_RE.match(design_name) or ".." in design_name:
        raise HTTPException(status_code=400, detail="Invalid design name")


def _docs_index() -> Dict[str, Dict[str, str]]:
    root = _repo_root()
    return {
        "getting_started": {
            "title": "Getting Started",
            "section": "Product",
            "path": os.path.join(root, "docs", "USER_GUIDE.md"),
            "summary": "Quick-start guide — build your first chip in minutes.",
        },
        "web_guide": {
            "title": "Web App Guide",
            "section": "Web",
            "path": os.path.join(root, "WEB_APP_GUIDE.md"),
            "summary": "Web app architecture and usage guide.",
        },
        "install": {
            "title": "Installation",
            "section": "Setup",
            "path": os.path.join(root, "docs", "INSTALL.md"),
            "summary": "Installation and environment setup steps.",
        },
        "cloud_deploy": {
            "title": "Cloud Deployment",
            "section": "Setup",
            "path": os.path.join(root, "docs", "CLOUD_DEPLOY.md"),
            "summary": "Deploy AgentIC on HuggingFace Spaces or any cloud.",
        },
    }

def _generate_and_save_build_reports(job_id: str, design_name: str):
    """Automatically generate PDF and DOCX reports summarizing the build at the end of the pipeline,
    and save them into the design's workspace directory as artifacts.
    """
    job = JOB_STORE.get(job_id)
    if not job: return
    
    stages = job.get("stages", {})
    events = job.get("events", [])
    build_status = job.get("build_status", "unknown")
    
    workspace_dir = os.path.join(_repo_root(), "designs", design_name)
    os.makedirs(workspace_dir, exist_ok=True)
    
    try:
        pdf_bytes = generate_full_report_pdf(stages, design_name, build_status, events)
        pdf_path = os.path.join(workspace_dir, f"{design_name}_Build_Report.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
    except Exception as e:
        print(f"Failed to generate auto PDF report: {e}")
        
    try:
        docx_bytes = generate_full_report_docx(stages, design_name, build_status, events)
        docx_path = os.path.join(workspace_dir, f"{design_name}_Build_Report.docx")
        with open(docx_path, "wb") as f:
            f.write(docx_bytes)
    except Exception as e:
        print(f"Failed to generate auto DOCX report: {e}")


# ─── Build Runner ────────────────────────────────────────────────────
def _run_agentic_build(job_id: str, req: BuildRequest):
    """Runs the full AgentIC build in a background thread, emitting events.
    
    When human_in_loop is enabled, the orchestrator pauses after each stage
    and waits for user approval via the /approve or /reject endpoints.
    """
    try:
        import src.agentic.config as _cfg
        if req.api_key:
            import json
            try:
                byok = json.loads(req.api_key)
            except:
                byok = {
                    "group1": {"api_key": req.api_key},
                    "group2": {"api_key": req.api_key},
                    "group3": {"api_key": req.api_key}
                }
            
            def safe_set(config_obj, grp):
                if grp.get("api_key"): config_obj["api_key"] = grp["api_key"]
                if grp.get("model"): config_obj["model"] = grp["model"]
                if grp.get("base_url"): config_obj["base_url"] = grp["base_url"]

            safe_set(_cfg.GLM_CONFIG, byok.get("group1", {}))
            safe_set(_cfg.CLOUD_CONFIG, byok.get("group2", {}))
            safe_set(_cfg.NVIDIA_CONFIG, byok.get("group2", {}))
            safe_set(_cfg.GROQ_CONFIG, byok.get("group3", {}))
            safe_set(_cfg.LOCAL_CONFIG, byok.get("group1", {}))

        from agentic.orchestrator import BuildOrchestrator, BuildState

        JOB_STORE[job_id]["status"] = "running"
        JOB_STORE[job_id]["human_in_loop"] = req.human_in_loop
        JOB_STORE[job_id]["waiting_approval"] = False
        JOB_STORE[job_id]["waiting_stage"] = ""
        JOB_STORE[job_id]["skip_stages"] = req.skip_stages or []
        _emit_event(job_id, "checkpoint", "INIT", "🚀 Build started — initializing workspace", step=1)

        # Current agent tracker for thought events
        current_agent_state = {"name": "Orchestrator", "stage": "INIT"}

        def event_sink(event: dict):
            """Hook called by orchestrator on every log/transition."""
            state = event.get("state", "UNKNOWN")
            message = event.get("message", "")
            event_type = event.get("type", "log")
            step = BUILD_STATES_ORDER.index(state) + 1 if state in BUILD_STATES_ORDER else 0
            _emit_event(job_id, event_type, state, message, step=step)

            # Also emit as agent_thought for the live activity feed
            if message and event_type in ("log", "checkpoint"):
                # Infer agent name from state
                agent_name = _infer_agent_name(state, message)
                thought_type = _infer_thought_type(message)
                _emit_agent_thought(job_id, agent_name, thought_type, message, state)

        # Use smart LLM selection: Cloud first (NVIDIA → Groq) → Local fallback
        byok_key = JOB_STORE[job_id].get("byok_key")
        llm, llm_name = _get_llm(byok_config=byok_key)
        role_llms = _get_role_llm_map(byok_config=byok_key)
        _emit_event(job_id, "checkpoint", "INIT", f"🤖 Compute engine ready", step=1)

        IS_HUGGINGFACE = os.environ.get("SPACE_ID") is not None
        forced_skip_openlane = True if IS_HUGGINGFACE else req.skip_openlane

        orchestrator = BuildOrchestrator(
            name=req.design_name,
            desc=req.description,
            llm=llm,
            max_retries=req.max_retries,
            verbose=req.show_thinking,
            skip_openlane=forced_skip_openlane, # TEMPORARY HF MAINTENANCE OVERRIDE
            skip_coverage=req.skip_coverage,
            full_signoff=req.full_signoff,
            min_coverage=req.min_coverage,
            strict_gates=req.strict_gates,
            pdk_profile=req.pdk_profile,
            max_pivots=req.max_pivots,
            congestion_threshold=req.congestion_threshold,
            hierarchical_mode=req.hierarchical,
            tb_gate_mode=req.tb_gate_mode,
            tb_max_retries=req.tb_max_retries,
            tb_fallback_template=req.tb_fallback_template,
            coverage_backend=req.coverage_backend,
            coverage_fallback_policy=req.coverage_fallback_policy,
            coverage_profile=req.coverage_profile,
            event_sink=event_sink,
            role_llms=role_llms,
            human_in_loop=req.human_in_loop,
        )

        RUNNING_ORCHESTRATORS[job_id] = orchestrator

        if req.human_in_loop:
            # Run with human-in-the-loop approval gates
            _run_with_approval_gates(job_id, orchestrator, req, llm)
        else:
            # Original autonomous flow
            orchestrator.run()

        # Check if cancelled mid-build
        if JOB_STORE.get(job_id, {}).get("cancelled"):
            JOB_STORE[job_id]["status"] = "cancelled"
            _emit_event(job_id, "error", "FAIL", "🛑 Build cancelled by user.", step=0)
            if job_id in RUNNING_ORCHESTRATORS:
                del RUNNING_ORCHESTRATORS[job_id]
            return

        # Gather result
        if job_id in RUNNING_ORCHESTRATORS:
            del RUNNING_ORCHESTRATORS[job_id]
            
        success = orchestrator.state.name == "SUCCESS"
        result = _build_result_summary(orchestrator, req.design_name, success)
        
        # Generate LLM failure explanation if build failed
        if not success:
            try:
                failed_state = orchestrator.state.name
                # Find the last non-terminal state from build history
                last_stage = "UNKNOWN"
                for entry in reversed(orchestrator.build_history):
                    if entry.state not in ("SUCCESS", "FAIL", "UNKNOWN"):
                        last_stage = entry.state
                        break
                error_log = get_stage_log_summary(orchestrator, last_stage)
                explanation = generate_failure_explanation(llm, last_stage, req.design_name, error_log)
                result["failure_explanation"] = explanation.get("explanation", "")
                result["failure_suggestion"] = explanation.get("suggestion", "")
                result["failed_stage"] = last_stage
                result["failed_stage_human"] = STAGE_HUMAN_NAMES.get(last_stage, last_stage.replace("_", " ").title())
            except Exception:
                result["failure_explanation"] = ""
                result["failure_suggestion"] = ""
        
        JOB_STORE[job_id]["result"] = result
        JOB_STORE[job_id]["status"] = "done" if success else "failed"
        JOB_STORE[job_id]["build_status"] = "success" if success else "failed"

        # ── Record build outcome in Supabase ───────────────────────
        user_profile = JOB_STORE[job_id].get("user_profile")
        if success:
            record_build_success(user_profile, job_id)
        else:
            record_build_failure(job_id)

        final_type = "done" if success else "error"
        final_msg = "✅ Chip build completed successfully!" if success else "❌ Build failed. See logs for details."
        _emit_event(job_id, final_type, orchestrator.state.name, final_msg, step=TOTAL_STEPS)

        # ── Auto-export to training JSONL ──────────────────────────
        _export_training_record(job_id, req.design_name, req.description, result, orchestrator)

    except Exception as e:
        import traceback
        err = traceback.format_exc()
        JOB_STORE[job_id]["status"] = "failed"
        JOB_STORE[job_id]["build_status"] = "failed"
        JOB_STORE[job_id]["result"] = {"error": str(e), "traceback": err}
        _emit_event(job_id, "error", "FAIL", f"💥 Critical error: {str(e)}", step=0)
        record_build_failure(job_id)
    finally:
        # Cleanup approval gates
        design_name = JOB_STORE.get(job_id, {}).get("design_name", "")
        if design_name:
            approval_manager.cleanup(design_name)
        
        # ── Auto-generate final build reports as artifacts ──
        _generate_and_save_build_reports(job_id, req.design_name)


def _infer_agent_name(state: str, message: str) -> str:
    """Infer which agent is active from the state and message content."""
    msg_lower = message.lower()
    
    if "architect" in msg_lower or "sid" in msg_lower or "decompos" in msg_lower:
        return "ArchitectModule"
    elif "self-reflect" in msg_lower or "selfreflect" in msg_lower:
        return "SelfReflectPipeline"
    elif "waveform" in msg_lower or "vcd" in msg_lower:
        return "WaveformExpertModule"
    elif "debug" in msg_lower and "deep" in msg_lower:
        return "DeepDebuggerModule"
    elif "testbench" in msg_lower or "tb " in msg_lower or "tb_" in msg_lower:
        return "Testbench Designer"
    elif "formal" in msg_lower or "sva" in msg_lower or "sby" in msg_lower:
        return "Verification Engineer"
    elif "regression" in msg_lower:
        return "Regression Architect"
    elif "error" in msg_lower or "fix" in msg_lower or "syntax" in msg_lower:
        return "Error Analyst"
    elif "rtl" in msg_lower or "verilog" in msg_lower or "module" in msg_lower:
        return "RTL Designer"
    elif "coverage" in msg_lower:
        return "Verification Engineer"
    elif "openlane" in msg_lower or "gds" in msg_lower or "harden" in msg_lower:
        return "Physical Design"
    elif "floorplan" in msg_lower or "placement" in msg_lower:
        return "Physical Design"
    elif "drc" in msg_lower or "lvs" in msg_lower or "signoff" in msg_lower:
        return "Signoff Engineer"
    elif "sdc" in msg_lower or "timing" in msg_lower or "clock" in msg_lower:
        return "SDC Agent"
    elif "convergence" in msg_lower or "eco" in msg_lower:
        return "Convergence Reviewer"
    
    # Fallback by state
    state_agents = {
        "INIT": "Orchestrator",
        "SPEC": "ArchitectModule",
        "SPEC_VALIDATE": "Spec Validator",
        "HIERARCHY_EXPAND": "Hierarchy Expander",
        "FEASIBILITY_CHECK": "Feasibility Checker",
        "CDC_ANALYZE": "CDC Analyzer",
        "VERIFICATION_PLAN": "Verification Planner",
        "RTL_GEN": "RTL Designer",
        "RTL_FIX": "Error Analyst",
        "VERIFICATION": "Testbench Designer",
        "FORMAL_VERIFY": "Verification Engineer",
        "COVERAGE_CHECK": "Verification Engineer",
        "REGRESSION": "Regression Architect",
        "SDC_GEN": "SDC Agent",
        "FLOORPLAN": "Physical Design",
        "HARDENING": "Physical Design",
        "CONVERGENCE_REVIEW": "Convergence Reviewer",
        "ECO_PATCH": "Convergence Reviewer",
        "SIGNOFF": "Signoff Engineer",
    }
    return state_agents.get(state, "Orchestrator")


def _infer_thought_type(message: str) -> str:
    """Infer the thought type from message content."""
    msg_lower = message.lower()
    
    if any(kw in msg_lower for kw in ["running", "executing", "calling", "invoking", "checking"]):
        return "tool_call"
    elif any(kw in msg_lower for kw in ["result:", "output:", "passed", "completed", "success"]):
        return "tool_result"
    elif any(kw in msg_lower for kw in ["decided", "choosing", "strategy", "pivot", "fallback"]):
        return "decision"
    elif any(kw in msg_lower for kw in ["found", "detected", "observed", "noticed"]):
        return "observation"
    else:
        return "thought"


def _get_thinking_message(state_name: str, design_name: str) -> str:
    """Generate a human-readable thinking message for a given stage."""
    messages = {
        "INIT": f"Setting up workspace for {design_name}...",
        "SPEC": f"Decomposing architecture for {design_name}...",
        "SPEC_VALIDATE": f"Validating hardware spec for {design_name}...",
        "HIERARCHY_EXPAND": f"Expanding submodule hierarchy for {design_name}...",
        "FEASIBILITY_CHECK": f"Checking Sky130 feasibility for {design_name}...",
        "CDC_ANALYZE": f"Analyzing clock domain crossings for {design_name}...",
        "VERIFICATION_PLAN": f"Generating verification plan for {design_name}...",
        "RTL_GEN": f"Generating Verilog RTL for {design_name}...",
        "RTL_FIX": f"Running syntax checks and applying fixes...",
        "VERIFICATION": f"Generating testbench and running simulation...",
        "FORMAL_VERIFY": f"Writing assertions and running formal verification...",
        "COVERAGE_CHECK": f"Analyzing code coverage metrics...",
        "REGRESSION": f"Running regression test suite...",
        "SDC_GEN": f"Generating timing constraints...",
        "FLOORPLAN": f"Creating floorplan configuration...",
        "HARDENING": f"Running GDSII hardening flow...",
        "CONVERGENCE_REVIEW": f"Analyzing timing and area convergence...",
        "ECO_PATCH": f"Applying engineering change orders...",
        "SIGNOFF": f"Running DRC, LVS, and STA checks...",
    }
    return messages.get(state_name, f"Processing {state_name}...")


def _run_with_approval_gates(job_id: str, orchestrator, req, llm):
    """Run the orchestrator with approval gates after every stage.
    
    This replaces orchestrator.run() when human_in_loop is enabled.
    After each stage completes, it generates a summary, emits stage_complete,
    and blocks until the user approves or rejects.
    """
    from agentic.orchestrator import BuildState
    
    design_name = req.design_name
    skip_stages = set(req.skip_stages or [])
    orchestrator.log(f"Build started for '{orchestrator.name}'", refined=True)
    
    try:
        while orchestrator.state != BuildState.SUCCESS and orchestrator.state != BuildState.FAIL:
            orchestrator.global_step_count += 1
            if orchestrator.global_step_count > orchestrator.global_step_budget:
                orchestrator.log(f"Global step budget exceeded ({orchestrator.global_step_budget}). Failing closed.", refined=True)
                orchestrator.state = BuildState.FAIL
                break
            
            # Check for cancellation
            if JOB_STORE.get(job_id, {}).get("cancelled"):
                orchestrator.state = BuildState.FAIL
                break
            
            current_state_name = orchestrator.state.name
            
            # Auto-skip stages that the user opted out of
            if current_state_name in skip_stages:
                _emit_event(job_id, "log", current_state_name, 
                    f"Skipping {current_state_name.replace('_', ' ').title()} (user preference)", 
                    step=BUILD_STATES_ORDER.index(current_state_name) + 1 if current_state_name in BUILD_STATES_ORDER else 0)
                next_st = get_next_stage(current_state_name)
                if next_st and hasattr(BuildState, next_st):
                    orchestrator.transition(getattr(BuildState, next_st))
                else:
                    orchestrator.state = BuildState.SUCCESS
                continue
            
            # Check for user feedback from previous rejection
            feedback = approval_manager.get_pending_feedback(design_name)
            if feedback:
                _emit_agent_thought(job_id, "Orchestrator", "observation", 
                    f"User feedback from review: {feedback}. Taking this into account before proceeding.", 
                    current_state_name)
                # Inject feedback into the orchestrator's context
                orchestrator.log(f"User feedback from review: {feedback}. Take this into account before proceeding.", refined=True)
            
            # Emit thinking indicator before stage execution
            agent_name = _infer_agent_name(current_state_name, "")
            _emit_agent_thinking(job_id, agent_name, 
                _get_thinking_message(current_state_name, orchestrator.name), 
                current_state_name)
            
            # Execute the current stage
            prev_state = orchestrator.state
            _execute_stage(orchestrator, current_state_name)
            new_state = orchestrator.state

            # ── Spec elaboration options event ──
            # If spec_generator produced 3 design options (short description), emit them
            # so the web UI can surface an interactive option picker card.
            if orchestrator.artifacts.get("spec_elaboration_needed"):
                options = orchestrator.artifacts.get("spec_elaboration_options", [])
                _emit_event(
                    job_id,
                    event_type="design_options",
                    state="SPEC_VALIDATE",
                    message="Your description was brief — here are 3 expert design interpretations:",
                    extra={
                        "options": options,
                        "auto_selected": orchestrator.artifacts.get("elaborated_desc", ""),
                    }
                )
                # Clear the flag so we don't re-emit on the retry
                orchestrator.artifacts.pop("spec_elaboration_needed", None)

            # If the stage transitioned to a new state, the stage completed successfully
            # Generate approval card and wait
            if new_state != prev_state or new_state in (BuildState.SUCCESS, BuildState.FAIL):
                completed_stage = current_state_name
                
                # Don't wait for approval on terminal states
                if new_state in (BuildState.SUCCESS, BuildState.FAIL):
                    # Still emit stage_complete for the last stage before terminal
                    if completed_stage not in ("SUCCESS", "FAIL"):
                        _emit_stage_summary(job_id, orchestrator, completed_stage, design_name, llm, wait=False)
                    break
                
                # Generate and emit stage_complete, then wait for approval
                approved = _emit_stage_summary(job_id, orchestrator, completed_stage, design_name, llm, wait=True)
                
                if not approved:
                    # User rejected — loop back to retry the CURRENT state
                    # Reset state back to the completed stage so the next loop iteration
                    # actually reruns it with the stored rejection feedback.
                    _emit_agent_thought(job_id, "Orchestrator", "decision", 
                        f"Stage {completed_stage} rejected by user. Retrying...", 
                        new_state.name)
                    orchestrator.state = prev_state
                    continue
            else:
                # State didn't change — this can happen for retry loops within a stage
                # Don't emit approval for internal retries
                continue
                
    except Exception as e:
        orchestrator.log(f"CRITICAL ERROR: {str(e)}", refined=False)
        import traceback
        from rich.console import Console
        Console().print(traceback.format_exc())
        orchestrator.state = BuildState.FAIL

    if orchestrator.state == BuildState.SUCCESS:
        try:
            orchestrator._save_industry_benchmark_metrics()
        except Exception as e:
            orchestrator.log(f"Benchmark metrics export warning: {e}", refined=True)
        from rich.console import Console
        from rich.panel import Panel
        summary = {k: v for k, v in orchestrator.artifacts.items() if 'code' not in k and 'spec' not in k}
        Console().print(Panel(
            f"[bold green]BUILD SUCCESSFUL[/]\n\n" + 
            "\n".join([f"[bold]{k.upper()}:[/] {v}" for k, v in summary.items()]),
            title="Done"
        ))
    else:
        from rich.console import Console
        from rich.panel import Panel
        Console().print(Panel(f"[bold red]BUILD FAILED[/]", title="Failed"))


def _execute_stage(orchestrator, state_name: str):
    """Execute a single orchestrator stage by name."""
    from agentic.orchestrator import BuildState
    
    stage_handlers = {
        "INIT": orchestrator.do_init,
        "SPEC": orchestrator.do_spec,
        "SPEC_VALIDATE": orchestrator.do_spec_validate,
        "HIERARCHY_EXPAND": orchestrator.do_hierarchy_expand,
        "FEASIBILITY_CHECK": orchestrator.do_feasibility_check,
        "CDC_ANALYZE": orchestrator.do_cdc_analyze,
        "VERIFICATION_PLAN": orchestrator.do_verification_plan,
        "RTL_GEN": orchestrator.do_rtl_gen,
        "RTL_FIX": orchestrator.do_rtl_fix,
        "VERIFICATION": orchestrator.do_verification,
        "FORMAL_VERIFY": orchestrator.do_formal_verify,
        "COVERAGE_CHECK": orchestrator.do_coverage_check,
        "REGRESSION": orchestrator.do_regression,
        "SDC_GEN": orchestrator.do_sdc_gen,
        "FLOORPLAN": orchestrator.do_floorplan,
        "HARDENING": orchestrator.do_hardening,
        "CONVERGENCE_REVIEW": orchestrator.do_convergence_review,
        "ECO_PATCH": orchestrator.do_eco_patch,
        "SIGNOFF": orchestrator.do_signoff,
    }
    
    handler = stage_handlers.get(state_name)
    if handler:
        handler()
    else:
        orchestrator.log(f"Unknown state {state_name}", refined=False)
        orchestrator.state = BuildState.FAIL


def _emit_stage_summary(job_id: str, orchestrator, stage_name: str, design_name: str, llm, wait: bool = True) -> bool:
    """Generate stage summary, emit stage_complete event, and optionally wait for approval.
    
    Returns True if approved (or not waiting), False if rejected.
    """
    # Emit thinking indicator while generating summary
    _emit_agent_thinking(job_id, "Orchestrator", "Preparing stage summary...", stage_name)
    
    # Build the stage_complete payload with LLM summary
    try:
        payload = build_stage_complete_payload(orchestrator, stage_name, design_name, llm)
    except Exception as e:
        payload = {
            "type": "stage_complete",
            "stage_name": stage_name,
            "summary": f"Stage {stage_name} completed. (Summary generation error: {str(e)[:100]})",
            "artifacts": [],
            "decisions": [],
            "warnings": [],
            "next_stage_name": get_next_stage(stage_name) or "DONE",
            "next_stage_preview": STAGE_DESCRIPTIONS.get(get_next_stage(stage_name) or "", ""),
            "timestamp": time.time(),
        }
    
    if wait:
        # Create approval gate FIRST so that stream API `is_live_waiting` evaluates true immediately
        approval_manager.create_gate(design_name, stage_name)
        
    # Emit the stage_complete event
    _emit_stage_complete(job_id, payload)
    
    if not wait:
        return True
    
    # Wait for frontend approval signal via the gate
    gate = approval_manager.wait_for_approval(design_name, stage_name, timeout=7200.0)
    
    JOB_STORE[job_id]["waiting_approval"] = False
    JOB_STORE[job_id]["waiting_stage"] = ""
    
    if gate.approved:
        return True
    elif gate.rejected:
        return False
    else:
        # Timeout — treat as approved to not block indefinitely
        _emit_agent_thought(job_id, "Orchestrator", "observation", 
            f"⏰ Approval timeout for {stage_name}. Auto-proceeding.", stage_name)
        return True


def _build_result_summary(orchestrator, design_name: str, success: bool) -> dict:
    """Collect all artifacts and metrics into a summary dict."""
    artifacts = orchestrator.artifacts or {}
    history = orchestrator.build_history or []

    # Self-healing telemetry (derived from build history + artifacts)
    lower_msgs = [h.message.lower() for h in history]
    self_heal_stats = {
        "stage_exception_count": sum("stage " in m and "exception" in m for m in lower_msgs),
        "formal_regen_count": int(artifacts.get("formal_regen_count", 0) or 0),
        "coverage_best_restore_count": sum("restoring best testbench" in m for m in lower_msgs),
        "coverage_regression_reject_count": sum("tb regressed coverage" in m for m in lower_msgs),
        "deterministic_tb_fallback_count": sum("deterministic tb fallback" in m for m in lower_msgs),
    }

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
        "self_heal": self_heal_stats,
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


@app.get("/ping")
def ping():
    """Zero-processing liveness probe for uptime monitors."""
    return {"status": "ok"}


@app.get("/health")
def health_check():
    """Health probe — verifies the LLM backend is reachable."""
    import traceback
    from agentic.config import CLOUD_CONFIG, GROQ_CONFIG, LOCAL_CONFIG
    llm_ok = False
    llm_name = "none"
    llm_error = None
    try:
        _, llm_name = _get_llm()
        llm_ok = True
    except Exception as e:
        llm_error = traceback.format_exc()
    return {
        "status": "ok" if llm_ok else "degraded",
        "llm_backend": llm_name,
        "llm_ok": llm_ok,
        "cloud_key_set": bool(CLOUD_CONFIG.get("api_key", "").strip()),
        "cloud_model": CLOUD_CONFIG.get("model", ""),
        "groq_key_set": bool(GROQ_CONFIG.get("api_key", "").strip()),
        "groq_model": GROQ_CONFIG.get("model", ""),
        "llm_error": llm_error,
        "version": "3.0.0",
    }


@app.get("/pipeline/schema")
def get_pipeline_schema():
    """Canonical pipeline schema for frontend timeline rendering."""
    stages = [{"state": s, **STAGE_META.get(s, {"label": s, "icon": "•"})} for s in BUILD_STATES_ORDER]
    return {
        "stages": stages,
        "terminal_states": ["SUCCESS", "FAIL"],
        "optional_stages": ["REGRESSION", "ECO_PATCH"],
        "total_steps": TOTAL_STEPS,
    }


@app.get("/build/options")
def get_build_options_contract():
    """Metadata contract for web build-option UI and docs sync."""
    return {
        "groups": [
            {
                "name": "Core",
                "options": [
                    {"key": "strict_gates", "type": "boolean", "default": True, "description": "Enable strict gate enforcement with bounded self-healing."},
                    {"key": "full_signoff", "type": "boolean", "default": False, "description": "Run full physical signoff checks when available."},
                    {"key": "skip_openlane", "type": "boolean", "default": False, "description": "Skip physical implementation stages for faster RTL-only iteration."},
                    {"key": "skip_coverage", "type": "boolean", "default": False, "description": "Skip the coverage stage and continue from formal verification to regression."},
                    {"key": "max_retries", "type": "int", "default": 5, "min": 1, "max": 12, "description": "Max repair retries per stage."},
                ],
            },
            {
                "name": "Coverage",
                "options": [
                    {"key": "min_coverage", "type": "float", "default": 80.0, "min": 0.0, "max": 100.0, "description": "Minimum line coverage threshold."},
                    {"key": "coverage_profile", "type": "enum", "default": "balanced", "values": ["balanced", "aggressive", "relaxed"], "description": "Profile-based line/branch/toggle/function thresholds."},
                    {"key": "coverage_backend", "type": "enum", "default": "auto", "values": ["auto", "verilator", "iverilog"], "description": "Coverage simulator backend selection."},
                    {"key": "coverage_fallback_policy", "type": "enum", "default": "fail_closed", "values": ["fail_closed", "fallback_oss", "skip"], "description": "Behavior when coverage infra fails."},
                ],
            },
            {
                "name": "Verification",
                "options": [
                    {"key": "tb_gate_mode", "type": "enum", "default": "strict", "values": ["strict", "relaxed"], "description": "TB compile/static gate mode."},
                    {"key": "tb_max_retries", "type": "int", "default": 3, "min": 1, "max": 10, "description": "TB-specific retry budget."},
                    {"key": "tb_fallback_template", "type": "enum", "default": "uvm_lite", "values": ["uvm_lite", "classic"], "description": "Deterministic fallback testbench template."},
                ],
            },
            {
                "name": "Physical",
                "options": [
                    {"key": "pdk_profile", "type": "enum", "default": "sky130", "values": ["sky130", "gf180"], "description": "OSS PDK profile."},
                    {"key": "max_pivots", "type": "int", "default": 2, "min": 0, "max": 6, "description": "Convergence strategy pivot budget."},
                    {"key": "congestion_threshold", "type": "float", "default": 10.0, "min": 0.0, "max": 100.0, "description": "Congestion threshold for convergence review."},
                    {"key": "hierarchical", "type": "enum", "default": "auto", "values": ["auto", "on", "off"], "description": "Hierarchy planner mode."},
                ],
            },
        ]
    }


@app.get("/docs/index")
def get_docs_index():
    """List in-app documentation documents."""
    docs = _docs_index()
    items = []
    for doc_id, meta in docs.items():
        path = meta.get("path", "")
        if os.path.exists(path):
            items.append({
                "id": doc_id,
                "title": meta.get("title", doc_id),
                "section": meta.get("section", "General"),
                "summary": meta.get("summary", ""),
            })
    return {"docs": items}


@app.get("/docs/content/{doc_id}")
def get_doc_content(doc_id: str):
    """Return markdown content for one document by id."""
    docs = _docs_index()
    meta = docs.get(doc_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Document not found")

    path = meta.get("path", "")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Document file missing")

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to read document: {e}")

    return {
        "id": doc_id,
        "title": meta.get("title", doc_id),
        "section": meta.get("section", "General"),
        "content": content,
    }


@app.post("/build")
async def trigger_build(req: BuildRequest, request: Request, profile: dict = Depends(get_current_user)):
    """Start a new chip build. Returns job_id immediately.

    When auth is enabled, checks plan quota and uses BYOK key if applicable.
    """
    header_key = request.headers.get("X-LLM-API-Key")
    if header_key:
        req.api_key = header_key

    # ── Auth guard: check plan + build count ──
    check_build_allowed(profile)
    byok_key = get_byok_config_for_user(profile)

    # Allow header explicitly over backend profile key for public clouds mapped correctly
    if req.api_key:
        try:
            import json
            byok_key = json.loads(req.api_key)
        except json.JSONDecodeError:
            byok_key = {
                "group1": {"api_key": req.api_key},
                "group2": {"api_key": req.api_key},
                "group3": {"api_key": req.api_key}
            }

    # ── LLM pre-flight: fail fast with a clear message ──
    try:
        _get_llm(byok_config=byok_key)
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail=str(e),
        )

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
        "user_profile": profile,
        "byok_key": byok_key,
        "stages": {},          # stage_name -> stage_complete payload
        "build_status": "running",
    }

    req.design_name = design_name

    # Record build start in Supabase
    record_build_start(profile, job_id, design_name)

    use_celery = os.getenv("RUN_CELERY", "false").lower() == "true"
    if use_celery:
        try:
            from .tasks import run_agentic_build_task
            logger.info(f"Sending job {job_id} to distributed Celery worker queue.")
            # We dump the pydantic model to a dict so Celery/Redis can serialize it
            run_agentic_build_task.apply_async(args=[job_id, req.dict()])
        except ImportError:
            use_celery = False
            
    if not use_celery:
        thread = threading.Thread(
            target=_run_agentic_build,
            args=(job_id, req),
            daemon=True,
        )
        thread.start()

    return {"job_id": job_id, "design_name": design_name, "status": "queued"}


@app.post("/build/elaborate")
async def elaborate_build(req: BuildElaborateRequest, user: dict = Depends(get_current_user)):
    """Inject a user choice into a waiting orchestrator (HITL Elaboration)."""
    job_id = req.job_id
    if job_id not in RUNNING_ORCHESTRATORS:
        raise HTTPException(status_code=404, detail="Active build not found or not in elaboration state")
    
    orch = RUNNING_ORCHESTRATORS[job_id]
    orch.artifacts['spec_elaboration_choice'] = req.choice
    
    return {"status": "success", "message": f"Choice '{req.choice}' injected"}


@app.get("/build/status/{job_id}")
def get_build_status(job_id: str):
    """Poll current build status and all events so far."""
    _pull_job_from_db(job_id) # Ensure API stays synced with Worker
    if job_id not in JOB_STORE:
        raise HTTPException(status_code=404, detail="Job not found")
    job = JOB_STORE[job_id]
    resp = {
        "job_id": job_id,
        "status": job["status"],
        "design_name": job["design_name"],
        "current_state": job["current_state"],
        "events": job["events"],
        "event_count": len(job["events"]),
    }
    
    # If the build is actively waiting for an elaboration choice, attach the options
    if job_id in RUNNING_ORCHESTRATORS:
        orch = RUNNING_ORCHESTRATORS[job_id]
        if orch.artifacts.get('waiting_for_elaboration') and 'spec_elaboration_options' in orch.artifacts:
            resp["waiting_for_elaboration"] = True
            resp["elaboration_options"] = orch.artifacts['spec_elaboration_options']
            
    return resp


@app.get("/build/stream/{job_id}")
async def stream_build_events(job_id: str):
    """SSE endpoint — streams live build events as they are emitted."""
    _pull_job_from_db(job_id) # Prime memory
    if job_id not in JOB_STORE:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        sent_index = 0
        last_event_sent_at = time.time()
        stall_warned = False
        elaboration_sent = False
        STALL_TIMEOUT = 300  # 5 minutes of silence → stall warning
        # Send a ping immediately so the browser knows the connection is alive
        yield "data: {\"type\": \"ping\", \"message\": \"connected\"}\n\n"

        while True:
            _pull_job_from_db(job_id)
            job = JOB_STORE.get(job_id)
            if job is None:
                break

            events = job["events"]
            waiting_stages = approval_manager.get_waiting_stages()
            while sent_index < len(events):
                event = events[sent_index]
                event_copy = dict(event)
                
                # If this stage_complete event happens to be the one we are CURRENTLY waiting on, 
                # flag it so the frontend doesn't pop up ghost approvals for past stages.
                if event_copy.get("type") == "stage_complete":
                    is_live_waiting = any(
                        w["design_name"] == job["design_name"] and w["stage"] == event_copy.get("state") 
                        for w in waiting_stages
                    )
                    event_copy["is_live_waiting"] = is_live_waiting

                yield f"data: {json.dumps(event_copy)}\n\n"
                sent_index += 1
                last_event_sent_at = time.time()
                stall_warned = False  # new event arrived — reset warning

            # Stop streaming when done, failed, or cancelled
            if job["status"] in ("done", "failed", "cancelled") and sent_index >= len(events):
                yield f"data: {json.dumps({'type': 'stream_end', 'status': job['status']})}\n\n"
                break

            # If the orchestrator is waiting for elaboration, emit a special status event periodically
            # We track this locally so we don't spam the stream every 0.4s
            if job_id in RUNNING_ORCHESTRATORS:
                orch = RUNNING_ORCHESTRATORS[job_id]
                if orch.artifacts.get('waiting_for_elaboration') and 'spec_elaboration_options' in orch.artifacts:
                    if not elaboration_sent:
                        waiting_event = {
                            "type": "elaboration_waiting",
                            "options": orch.artifacts['spec_elaboration_options'],
                            "message": "Waiting for architectural choice...",
                            "timestamp": int(time.time()),
                        }
                        yield f"data: {json.dumps(waiting_event)}\n\n"
                        elaboration_sent = True

            # Emit a stall warning if no events have arrived for STALL_TIMEOUT seconds
            if (
                not stall_warned
                and job["status"] == "running"
                and (time.time() - last_event_sent_at) >= STALL_TIMEOUT
            ):
                stage = job.get("current_state", "UNKNOWN")
                stall_event = {
                    "type": "stall_warning",
                    "state": stage,
                    "message": (
                        f"⚠️ No activity for 5 minutes at stage {stage} — "
                        "the LLM may be stuck or unresponsive. "
                        "You can cancel and retry."
                    ),
                    "step": 0,
                    "total_steps": TOTAL_STEPS,
                    "timestamp": int(time.time()),
                }
                yield f"data: {json.dumps(stall_event)}\n\n"
                stall_warned = True

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
    """List chip designs built in this session (job store only).

    NOTE: Listing raw filesystem paths is disabled unconditionally on the public
    deployment — the previous Origin/Host-header check was spoofable and leaked
    internal directory structure. Jobs are tracked via JOB_STORE instead.
    """
    return {"designs": []}


@app.get("/metrics/{design_name}")
def get_metrics(design_name: str):
    """Return latest OpenLane metrics for a design."""
    _validate_design_name(design_name)
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
    _validate_design_name(design_name)
    try:
        from agentic.tools.vlsi_tools import check_physical_metrics
        metrics, report = check_physical_metrics(design_name)
        return {"success": metrics is not None, "report": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Human-in-the-Loop Approval Endpoints ───────────────────────────

@app.post("/approve")
def approve_stage(req: ApproveRequest):
    """Approve the current stage and allow the pipeline to proceed."""
    ok = approval_manager.approve(req.design_name, req.stage)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No pending approval for design '{req.design_name}' at stage '{req.stage}'")
    return {"ok": True, "message": f"Stage '{req.stage}' approved for '{req.design_name}'"}


@app.post("/reject")
def reject_stage(req: RejectRequest):
    """Reject the current stage, optionally providing feedback for retry."""
    ok = approval_manager.reject(req.design_name, req.stage, req.feedback)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No pending approval for design '{req.design_name}' at stage '{req.stage}'")
    return {
        "ok": True,
        "message": f"Stage '{req.stage}' rejected for '{req.design_name}'" + (f" with feedback" if req.feedback else ""),
        "will_retry": True
    }


@app.get("/approval/status")
def get_approval_status():
    """List all stages currently waiting for user approval."""
    waiting = approval_manager.get_waiting_stages()
    return {"waiting": waiting, "count": len(waiting)}


@app.get("/build/artifacts/{design_name}")
def get_partial_artifacts(design_name: str):
    """Scan the design's output directory for any partial artifacts produced during a build.
    Used by the failure summary card to show what was generated before the build failed.
    """
    _validate_design_name(design_name)
    artifacts = []
    
    # Check designs/ workspace directory
    workspace_dir = os.path.join(_repo_root(), "designs", design_name)
    if os.path.isdir(workspace_dir):
        for f in os.listdir(workspace_dir):
            fpath = os.path.join(workspace_dir, f)
            if os.path.isfile(fpath):
                size = os.path.getsize(fpath)
                artifacts.append({
                    "name": f,
                    "path": fpath,
                    "size": size,
                    "type": _classify_artifact(f),
                })
    
    # Check OpenLane designs directory
    from agentic.config import OPENLANE_ROOT
    ol_design_dir = os.path.join(OPENLANE_ROOT, "designs", design_name)
    if os.path.isdir(ol_design_dir):
        for root_dir, _dirs, files in os.walk(ol_design_dir):
            for f in files:
                if f.endswith(('.v', '.sv', '.vcd', '.gds', '.def', '.sdc', '.json', '.tcl', '.sby', '.log', '.csv')):
                    fpath = os.path.join(root_dir, f)
                    size = os.path.getsize(fpath)
                    artifacts.append({
                        "name": f,
                        "path": fpath,
                        "size": size,
                        "type": _classify_artifact(f),
                    })
    
    return {"design_name": design_name, "artifacts": artifacts[:50]}  # Cap at 50


@app.get("/build/artifacts/{design_name}/{filename}")
def download_artifact(design_name: str, filename: str):
    """Download an individual artifact file from a design's output directory."""
    _validate_design_name(design_name)
    # Sanitize filename to prevent path traversal
    safe_name = os.path.basename(filename)
    if safe_name != filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Search workspace designs/ first, then OpenLane designs/
    from agentic.config import OPENLANE_ROOT
    search_dirs = [os.path.join(_repo_root(), "designs", design_name)]
    search_dirs.append(os.path.join(OPENLANE_ROOT, "designs", design_name))

    for base_dir in search_dirs:
        if not os.path.isdir(base_dir):
            continue
        for root_dir, _dirs, files in os.walk(base_dir):
            if safe_name in files:
                fpath = os.path.join(root_dir, safe_name)
                return FileResponse(fpath, filename=safe_name)

    raise HTTPException(status_code=404, detail="Artifact not found")


def _classify_artifact(filename: str) -> str:
    """Classify a file by its extension."""
    ext = os.path.splitext(filename)[1].lower()
    classifications = {
        '.v': 'rtl', '.sv': 'rtl',
        '.vcd': 'waveform',
        '.gds': 'layout', '.def': 'layout',
        '.sdc': 'constraints',
        '.json': 'config',
        '.tcl': 'script',
        '.sby': 'formal',
        '.log': 'log',
        '.csv': 'report',
        '.pdf': 'report',
        '.docx': 'report',
    }
    return classifications.get(ext, 'other')


# ─── Auth & Profile Routes ──────────────────────────────────────────
class SetApiKeyRequest(BaseModel):
    api_key: str


@app.get("/profile")
async def get_profile(profile: dict = Depends(get_current_user)):
    """Return the authenticated user's profile (plan, build count, etc.)."""
    if profile is None:
        return {"auth_enabled": False}
    return {
        "auth_enabled": True,
        "id": profile["id"],
        "email": profile.get("email"),
        "full_name": profile.get("full_name"),
        "plan": profile.get("plan", "free"),
        "successful_builds": profile.get("successful_builds", 0),
        "has_byok_key": bool(profile.get("llm_api_key")),
    }


@app.post("/profile/api-key")
async def set_byok_key(req: SetApiKeyRequest, profile: dict = Depends(get_current_user)):
    """Store an encrypted LLM API key for BYOK plan users."""
    if profile is None:
        raise HTTPException(status_code=403, detail="Auth not enabled")
    if profile.get("plan") != "byok":
        raise HTTPException(status_code=400, detail="Only BYOK plan users can set an API key")

    from server.auth import _supabase_update
    encrypted = encrypt_api_key(req.api_key)
    _supabase_update("profiles", f"id=eq.{profile['id']}", {"llm_api_key": encrypted})
    return {"success": True, "message": "API key stored securely"}


# ─── Report Download Endpoints ────────────────────────────────────────
# Single-stage reports (HITL flow) and full-build reports (both flows).

def _get_job_or_404(job_id: str) -> dict:
    if not re.match(r"^[0-9a-f-]{36}$", job_id):
        raise HTTPException(status_code=400, detail="Invalid job ID")
    job = JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/report/{job_id}/full.pdf",
         summary="Download full build report as PDF")
def download_full_report_pdf(job_id: str):
    job = _get_job_or_404(job_id)
    design_name  = job.get("design_name", "design")
    build_status = job.get("build_status", "unknown")
    stages       = job.get("stages", {})
    events       = job.get("events", [])
    pdf_bytes = generate_full_report_pdf(stages, design_name, build_status, events)
    safe_name = re.sub(r"[^a-z0-9_]", "_", design_name.lower())
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="{safe_name}_full_report.pdf"'},
    )


@app.get("/report/{job_id}/full.docx",
         summary="Download full build report as DOCX")
def download_full_report_docx(job_id: str):
    job = _get_job_or_404(job_id)
    design_name  = job.get("design_name", "design")
    build_status = job.get("build_status", "unknown")
    stages       = job.get("stages", {})
    events       = job.get("events", [])
    docx_bytes = generate_full_report_docx(stages, design_name, build_status, events)
    safe_name = re.sub(r"[^a-z0-9_]", "_", design_name.lower())
    return StreamingResponse(
        io.BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition":
                 f'attachment; filename="{safe_name}_full_report.docx"'},
    )


@app.get("/report/{job_id}/stage/{stage_name}.pdf",
         summary="Download a single-stage report as PDF")
def download_stage_report_pdf(job_id: str, stage_name: str):
    if not re.match(r"^[A-Z_]{2,30}$", stage_name):
        raise HTTPException(status_code=400, detail="Invalid stage name")
    job = _get_job_or_404(job_id)
    stages = job.get("stages", {})
    if stage_name not in stages:
        raise HTTPException(status_code=404,
                            detail=f"Stage '{stage_name}' not found in this job")
    design_name = job.get("design_name", "design")
    pdf_bytes = generate_stage_report_pdf(stages[stage_name], design_name)
    safe_name = re.sub(r"[^a-z0-9_]", "_", design_name.lower())
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="{safe_name}_{stage_name}_report.pdf"'},
    )


@app.get("/report/{job_id}/stage/{stage_name}.docx",
         summary="Download a single-stage report as DOCX")
def download_stage_report_docx(job_id: str, stage_name: str):
    if not re.match(r"^[A-Z_]{2,30}$", stage_name):
        raise HTTPException(status_code=400, detail="Invalid stage name")
    job = _get_job_or_404(job_id)
    stages = job.get("stages", {})
    if stage_name not in stages:
        raise HTTPException(status_code=404,
                            detail=f"Stage '{stage_name}' not found in this job")
    design_name = job.get("design_name", "design")
    docx_bytes = generate_stage_report_docx(stages[stage_name], design_name)
    safe_name = re.sub(r"[^a-z0-9_]", "_", design_name.lower())
    return StreamingResponse(
        io.BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition":
                 f'attachment; filename="{safe_name}_{stage_name}_report.docx"'},
    )


import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Mount the static Vite React app (for HuggingFace Spaces/Docker)
frontend_dist = os.path.join(os.path.dirname(__file__), "..", "web", "dist")
if os.path.exists(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")
    app.mount("/vcdrom", StaticFiles(directory=os.path.join(frontend_dist, "vcdrom")), name="vcdrom")
    @app.get("/{catchall:path}")
    def serve_frontend_app(catchall: str):
        full_path = os.path.join(frontend_dist, catchall)
        if os.path.isfile(full_path):
            return FileResponse(full_path)
        return FileResponse(os.path.join(frontend_dist, "index.html"))
