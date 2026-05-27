"""
AgentIC Backend API — Premium Chip Studio
Real-time SSE streaming, job management, human-in-the-loop approval, and chip result reporting.
"""

import asyncio
import json
import logging
import os
import re
import sys

# Force API server mode globally so orchestrator.py respects HITL loops
os.environ["AGENTIC_API_SERVER"] = "1"

# Add src to Python path so 'agentic' module can be found when run locally or via docker
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
)
import time
import uuid
import io
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel, Field
from sqlalchemy import inspect, text

from server.approval import approval_manager
from server.auth import (
    AUTH_ENABLED,
    check_build_allowed,
    encrypt_api_key,
    get_current_user,
    get_llm_key_for_user,
    get_byok_config_for_user,
    save_byok_config_for_user,
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
from agentic.core.flow_capabilities import get_stage_meta, resolve_flow_profile

# ─── Python path ────────────────────────────────────────────────────
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# ─── App ─────────────────────────────────────────────────────────────
app = FastAPI(title="AgentIC Backend API", version="3.0.0")
app.include_router(billing_router)
app.include_router(lab_router)

from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

APP_ENV = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "development")).strip().lower()
IS_PRODUCTION = APP_ENV in {"prod", "production"}
_ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "")
CORS_ORIGINS = (
    [origin.strip() for origin in _ALLOWED_ORIGINS.split(",") if origin.strip()]
    if _ALLOWED_ORIGINS
    else ([] if IS_PRODUCTION else ["*"])
)
if IS_PRODUCTION and not CORS_ORIGINS:
    logging.getLogger("agentic.api").error(
        "APP_ENV=production requires ALLOWED_ORIGINS. Browser clients will be denied by CORS."
    )
if "*" in CORS_ORIGINS and IS_PRODUCTION:
    logging.getLogger("agentic.api").warning(
        "Wildcard CORS is unsafe for production. Set ALLOWED_ORIGINS to your deployed frontend URL."
    )

_ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "")
ALLOWED_HOSTS = [host.strip() for host in _ALLOWED_HOSTS.split(",") if host.strip()]
if ALLOWED_HOSTS:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials="*" not in CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("agentic.api")


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "Invalid integer for %s=%r; using default %s", name, raw, default
        )
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid float for %s=%r; using default %s", name, raw, default)
        return default


ALLOW_BACKEND_LLM_FALLBACK = _env_flag("ALLOW_BACKEND_LLM_FALLBACK", False)
DB_INIT_ATTEMPTS = max(1, _env_int("DB_INIT_ATTEMPTS", 12))
DB_INIT_RETRY_SECONDS = max(0.0, _env_float("DB_INIT_RETRY_SECONDS", 5.0))

HTTP_REQUESTS_TOTAL = Counter(
    "agentic_http_requests_total",
    "Total HTTP requests served by AgentIC.",
    ["method", "path", "status"],
)
HTTP_REQUEST_LATENCY_SECONDS = Histogram(
    "agentic_http_request_latency_seconds",
    "HTTP request latency for AgentIC routes.",
    ["method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)
HTTP_INFLIGHT_REQUESTS = Gauge(
    "agentic_http_inflight_requests",
    "Number of in-flight HTTP requests.",
)
BUILDS_STARTED_TOTAL = Counter(
    "agentic_builds_started_total",
    "Number of build requests accepted by AgentIC.",
)
BUILDS_COMPLETED_TOTAL = Counter(
    "agentic_builds_completed_total",
    "Number of builds that reached a terminal state.",
    ["outcome"],
)
JOBS_BY_STATUS = Gauge(
    "agentic_jobs_by_status",
    "Current in-memory job counts by status.",
    ["status"],
)


@app.middleware("http")
async def advanced_request_logging_middleware(request: Request, call_next):
    start = time.perf_counter()
    HTTP_INFLIGHT_REQUESTS.inc()
    response = None
    try:
        response = await call_next(request)
        duration = time.perf_counter() - start
        response.headers["X-Process-Time"] = str(duration)
        return response
    finally:
        route = request.scope.get("route")
        path_template = getattr(route, "path", request.url.path)
        status_code = str(getattr(response, "status_code", 500))
        duration = time.perf_counter() - start

        # Log the request details for Big Tech level observability
        client_ip = request.client.host if request.client else "unknown"
        logger.info(
            f"[{status_code}] {request.method} {request.url.path} - {client_ip} - {duration:.4f}s"
        )

        HTTP_REQUESTS_TOTAL.labels(request.method, path_template, status_code).inc()
        HTTP_REQUEST_LATENCY_SECONDS.labels(request.method, path_template).observe(
            duration
        )
        HTTP_INFLIGHT_REQUESTS.dec()


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    response.headers["X-Request-ID"] = request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), payment=()",
    )
    if IS_PRODUCTION and request.url.scheme == "https":
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    return response


# ─── Job Store ───────────────────────────────────────────────────────
# Structure: { job_id: { status, design_name, events: [], result: {}, cancelled: bool } }
JOB_STORE: Dict[str, Dict[str, Any]] = {}
DEFAULT_CELERY_TASK_TIME_LIMIT = 3660
STALE_JOB_GRACE_SECONDS = 120


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


CELERY_TASK_TIME_LIMIT_SECONDS = _env_int(
    "CELERY_TASK_TIME_LIMIT", DEFAULT_CELERY_TASK_TIME_LIMIT
)

try:
    from .db import (
        SessionLocal,
        Job as DBJob,
        engine as DB_ENGINE,
        ensure_database_ready,
        is_schema_ready,
    )
except ImportError:
    SessionLocal = None
    DBJob = None
    DB_ENGINE = None
    ensure_database_ready = None
    is_schema_ready = lambda: False


def _db_persistence_ready() -> bool:
    if not SessionLocal or DB_ENGINE is None or DBJob is None:
        return False
    if is_schema_ready():
        return True
    if ensure_database_ready is None:
        return False
    return ensure_database_ready(max_attempts=1, retry_interval=0)


@app.on_event("startup")
def initialize_persistence():
    if ensure_database_ready is None:
        logger.warning(
            "Database persistence module unavailable; startup is continuing without DB-backed job history."
        )
        return

    if ensure_database_ready(
        max_attempts=DB_INIT_ATTEMPTS, retry_interval=DB_INIT_RETRY_SECONDS
    ):
        _hydrate_job_store_from_db()
        logger.info("Database schema is ready for AgentIC startup.")
        return

    logger.error(
        "Database schema could not be initialized during startup. "
        "The API will stay live, but readiness checks and job persistence will remain degraded."
    )


def _sync_job_to_db(job_id: str):
    """Safely mirrors JOB_STORE dictionary changes into the persistent Database."""
    if not _db_persistence_ready():
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

            db_job.user_id = job_data.get("user_id")
            db_job.user_email = job_data.get("user_email")
            db_job.design_name = job_data.get("design_name", "")
            db_job.status = job_data.get("status", "pending")
            db_job.build_status = job_data.get("build_status", "pending")
            created_at = _normalize_epoch_seconds(job_data.get("created_at"))
            if created_at:
                db_job.created_at = datetime.fromtimestamp(created_at)
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
    if not _db_persistence_ready():
        return
    try:
        with SessionLocal() as db:
            db_job = db.query(DBJob).filter(DBJob.id == job_id).first()
            if db_job:
                if job_id not in JOB_STORE:
                    JOB_STORE[job_id] = {}
                JOB_STORE[job_id].update(
                    {
                        "user_id": getattr(db_job, "user_id", None),
                        "user_email": getattr(db_job, "user_email", None),
                        "design_name": db_job.design_name
                        or JOB_STORE[job_id].get("design_name", ""),
                        "status": db_job.status or "pending",
                        "build_status": db_job.build_status or "pending",
                        "human_in_loop": db_job.human_in_loop,
                        "waiting_approval": db_job.waiting_approval,
                        "waiting_stage": db_job.waiting_stage,
                        "events": db_job.events or [],
                        "stages": db_job.stages or {},
                        "result": db_job.result or {},
                        "created_at": int(db_job.created_at.timestamp())
                        if db_job.created_at
                        else JOB_STORE[job_id].get("created_at", int(time.time())),
                        "current_state": db_job.events[-1].get("state", "UNKNOWN")
                        if db_job.events
                        else "INIT",
                    }
                )
    except Exception as e:
        logger.error(f"Failed to pull {job_id} from DB: {e}")


def _normalize_epoch_seconds(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _event_epoch_seconds(event: Dict[str, Any]) -> int:
    value = event.get("timestamp")
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return 0
    return 0


def _last_job_activity_seconds(job: Dict[str, Any]) -> int:
    for event in reversed(job.get("events", []) or []):
        ts = _event_epoch_seconds(event)
        if ts:
            return ts
    return _normalize_epoch_seconds(job.get("created_at"))


def _mark_stale_running_job_failed(job_id: str, job: Dict[str, Any]) -> bool:
    if job.get("status") not in ("queued", "running"):
        return False

    last_activity = _last_job_activity_seconds(job)
    if not last_activity:
        return False

    stale_after = CELERY_TASK_TIME_LIMIT_SECONDS + STALE_JOB_GRACE_SECONDS
    if int(time.time()) - last_activity < stale_after:
        return False

    state = job.get("current_state") or _job_current_state(job)
    message = (
        "Build stopped after exceeding the worker time limit. "
        "The worker process was likely killed before it could write a final result."
    )
    job["status"] = "failed"
    job["build_status"] = "failed"
    job.setdefault("result", {})["error"] = "worker_time_limit_exceeded"
    job.setdefault("result", {})["failure_explanation"] = message
    job.setdefault("events", []).append(
        {
            "type": "error",
            "state": state,
            "message": message,
            "step": 0,
            "total_steps": TOTAL_STEPS,
            "timestamp": int(time.time()),
        }
    )
    _sync_job_to_db(job_id)
    return True


def _mark_orphaned_local_job_failed(job_id: str, job: Dict[str, Any]) -> bool:
    if os.getenv("RUN_CELERY", "false").lower() == "true":
        return False
    if job.get("status") not in ("queued", "running", "cancelling"):
        return False

    message = (
        "Local backend restarted before the in-process build completed. "
        "Start the build again from the UI."
    )
    state = job.get("current_state") or _job_current_state(job)
    job["status"] = "failed"
    job["build_status"] = "failed"
    job.setdefault("result", {})["error"] = "local_backend_restarted"
    job.setdefault("result", {})["failure_explanation"] = message
    job.setdefault("events", []).append(
        {
            "type": "error",
            "state": state,
            "message": message,
            "step": 0,
            "total_steps": TOTAL_STEPS,
            "timestamp": int(time.time()),
        }
    )
    _sync_job_to_db(job_id)
    return True


def _job_current_state(job: Any) -> str:
    events = (
        getattr(job, "events", None) if not isinstance(job, dict) else job.get("events")
    )
    if events:
        try:
            return events[-1].get("state", "INIT")
        except (AttributeError, IndexError):
            return "INIT"
    if isinstance(job, dict):
        return job.get("current_state", "INIT")
    return "INIT"


def _serialize_job_record(job_id: str, job: Any) -> Dict[str, Any]:
    if isinstance(job, dict):
        events = job.get("events", []) or []
        return {
            "job_id": job_id,
            "design_name": job.get("design_name", ""),
            "status": job.get("status", "pending"),
            "current_state": _job_current_state(job),
            "created_at": _normalize_epoch_seconds(job.get("created_at")),
            "event_count": len(events),
            "human_in_loop": bool(job.get("human_in_loop", False)),
        }

    events = job.events or []
    return {
        "job_id": job_id,
        "design_name": job.design_name or "",
        "status": job.status or "pending",
        "current_state": _job_current_state(job),
        "created_at": _normalize_epoch_seconds(job.created_at),
        "event_count": len(events),
        "human_in_loop": bool(getattr(job, "human_in_loop", False)),
    }


def _load_jobs_for_profile(profile: Optional[dict]) -> List[Dict[str, Any]]:
    if _db_persistence_ready():
        try:
            with SessionLocal() as db:
                query = db.query(DBJob)
                if (
                    AUTH_ENABLED
                    and profile is not None
                    and getattr(DBJob, "user_id", None) is not None
                ):
                    query = query.filter(DBJob.user_id == profile["id"])
                rows = query.order_by(DBJob.created_at.desc()).all()
                return [_serialize_job_record(row.id, row) for row in rows]
        except Exception as exc:
            logger.error("Failed to load persisted jobs: %s", exc)

    jobs: List[Dict[str, Any]] = []
    for jid, job in JOB_STORE.items():
        if (
            AUTH_ENABLED
            and profile is not None
            and job.get("user_id") != profile.get("id")
        ):
            continue
        jobs.append(_serialize_job_record(jid, job))
    jobs.sort(key=lambda item: item.get("created_at", 0), reverse=True)
    return jobs


def _ensure_job_access(job_id: str, profile: Optional[dict]) -> Dict[str, Any]:
    """Return a job only if the active user is allowed to access it."""
    _pull_job_from_db(job_id)
    job = JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if AUTH_ENABLED:
        if profile is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        owner_id = job.get("user_id")
        if not owner_id:
            raise HTTPException(status_code=404, detail="Job not found")
        if owner_id != profile.get("id"):
            raise HTTPException(status_code=404, detail="Job not found")

    _mark_stale_running_job_failed(job_id, job)
    return job


def _ensure_design_access(design_name: str, profile: Optional[dict]) -> None:
    """Authorize access to design-scoped files and reports."""
    _validate_design_name(design_name)
    if not AUTH_ENABLED:
        return
    if profile is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    for job in _load_jobs_for_profile(profile):
        if job.get("design_name") == design_name:
            return
    raise HTTPException(status_code=404, detail="Design not found")


def _hydrate_job_store_from_db(limit: int = 250) -> None:
    """Warm memory from persisted jobs so restarts keep recent workspace history available."""
    if not _db_persistence_ready():
        return
    try:
        with SessionLocal() as db:
            row_ids = [
                row.id
                for row in db.query(DBJob)
                .order_by(DBJob.created_at.desc())
                .limit(limit)
                .all()
            ]
        for row_id in row_ids:
            _pull_job_from_db(row_id)
            job = JOB_STORE.get(row_id)
            if job:
                _mark_orphaned_local_job_failed(row_id, job)
        logger.info(
            "Hydrated %s persisted jobs into memory on startup.", min(len(row_ids), limit)
        )
    except Exception as exc:
        logger.error("Failed to hydrate persisted jobs on startup: %s", exc)


def _summarize_jobs(jobs: List[Dict[str, Any]]) -> Dict[str, int]:
    running_statuses = {"queued", "running", "cancelling"}
    active_designs = {job["design_name"] for job in jobs if job.get("design_name")}
    return {
        "total_builds": len(jobs),
        "running_builds": sum(
            1 for job in jobs if job.get("status") in running_statuses
        ),
        "successful_builds": sum(1 for job in jobs if job.get("status") == "done"),
        "failed_builds": sum(1 for job in jobs if job.get("status") == "failed"),
        "active_designs": len(active_designs),
    }


def _recent_job_activity(
    jobs: List[Dict[str, Any]], limit: int = 8
) -> List[Dict[str, Any]]:
    return sorted(jobs, key=lambda item: item.get("created_at", 0), reverse=True)[
        :limit
    ]


def _design_has_gds(design_name: str) -> bool:
    if not design_name:
        return False

    workspace_dir = os.path.join(_repo_root(), "designs", design_name)
    if os.path.isdir(workspace_dir):
        for root_dir, _dirs, files in os.walk(workspace_dir):
            if any(file_name.lower().endswith(".gds") for file_name in files):
                return True

    try:
        from agentic.config import OPENLANE_ROOT

        openlane_dir = os.path.join(OPENLANE_ROOT, "designs", design_name)
        if os.path.isdir(openlane_dir):
            for root_dir, _dirs, files in os.walk(openlane_dir):
                if any(file_name.lower().endswith(".gds") for file_name in files):
                    return True
    except Exception:
        return False

    return False


def _job_status_counts() -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for job in JOB_STORE.values():
        status = job.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _check_database() -> Dict[str, Any]:
    if DB_ENGINE is None:
        return {"ok": False, "detail": "database engine unavailable"}
    try:
        with DB_ENGINE.connect() as conn:
            conn.execute(text("SELECT 1"))
        if not _db_persistence_ready():
            return {"ok": False, "detail": "database schema not initialized"}
        if not inspect(DB_ENGINE).has_table("jobs"):
            return {"ok": False, "detail": "jobs table missing"}
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def _check_redis() -> Dict[str, Any]:
    redis_url = os.getenv("REDIS_URL", "").strip()
    if not redis_url:
        return {"ok": False, "detail": "REDIS_URL not configured"}
    try:
        import redis as redis_lib

        client = redis_lib.Redis.from_url(
            redis_url, socket_timeout=1, socket_connect_timeout=1
        )
        client.ping()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def _check_object_storage() -> Dict[str, Any]:
    try:
        from server.storage import S3_BUCKET_NAME, S3_ENDPOINT_URL, get_s3_client

        client = get_s3_client()
        if not client:
            return {"ok": False, "enabled": False, "detail": "object storage disabled"}
        client.list_objects_v2(Bucket=S3_BUCKET_NAME, MaxKeys=1)
        return {
            "ok": True,
            "enabled": True,
            "bucket": S3_BUCKET_NAME,
            "endpoint": S3_ENDPOINT_URL,
        }
    except Exception as exc:
        return {"ok": False, "enabled": True, "detail": str(exc)}


def _collect_platform_status(include_llm: bool = False) -> Dict[str, Any]:
    db_status = _check_database()
    redis_status = _check_redis()
    storage_status = _check_object_storage()

    llm_ok = None
    llm_name = None
    llm_error = None
    if include_llm:
        try:
            _, llm_name = _get_llm(is_agentic_paid=True)
            llm_ok = True
        except Exception as exc:
            llm_ok = False
            llm_error = str(exc)

    ready = (
        db_status["ok"]
        and redis_status["ok"]
        and (storage_status["ok"] or not storage_status.get("enabled", False))
    )
    if include_llm and llm_ok is False:
        ready = False

    return {
        "status": "ok" if ready else "degraded",
        "database": db_status,
        "redis": redis_status,
        "object_storage": storage_status,
        "jobs": _job_status_counts(),
        "run_celery": os.getenv("RUN_CELERY", "false").lower() == "true",
        "llm_backend": llm_name,
        "llm_ok": llm_ok,
        "llm_error": llm_error,
        "version": "3.0.0",
    }


def _pdk_catalog_payload() -> Dict[str, Any]:
    from agentic.config import (
        DEFAULT_PDK_PROFILE,
        PDK_ROOT,
        detect_available_pdks,
        list_pdk_profiles,
    )

    profiles = list_pdk_profiles()
    available = detect_available_pdks()
    items: List[Dict[str, Any]] = []
    for key, profile in sorted(profiles.items()):
        detected = available.get(key, {})
        proprietary = bool(profile.get("proprietary", False))
        is_available = bool(detected.get("available", False))
        gds_ready = is_available and not proprietary
        if proprietary and not is_available:
            status = "requires_foundry_pdk"
            reason = "Proprietary node. Licensed PDK, timing libraries, LEF/tech files, and OpenLane/OpenROAD integration are required."
        elif gds_ready:
            status = "ready"
            reason = "Available for full GDSII runs in this workspace."
        else:
            status = "not_installed"
            reason = "Install this PDK under PDK_ROOT or OPENLANE_PDK_ROOT before enabling GDSII for users."

        items.append(
            {
                "key": key,
                "label": key,
                "pdk": profile.get("pdk", key),
                "std_cell_library": profile.get("std_cell_library", ""),
                "description": profile.get("description", ""),
                "maturity": profile.get("maturity", ""),
                "fabrication_ready": bool(profile.get("fabrication_ready", False)),
                "available": is_available,
                "gds_ready": gds_ready,
                "tech_ok": bool(detected.get("tech_ok", False)),
                "proprietary": proprietary,
                "root_path": detected.get("root_path", ""),
                "status": status,
                "reason": reason,
            }
        )

    for key, detected in sorted(available.items()):
        if key in profiles:
            continue
        items.append(
            {
                "key": key,
                "label": key,
                "pdk": detected.get("pdk", key),
                "std_cell_library": detected.get("std_cell_library", ""),
                "description": detected.get(
                    "description", "Custom user-provided PDK detected for this workspace"
                ),
                "maturity": detected.get("maturity", "custom"),
                "fabrication_ready": bool(detected.get("fabrication_ready", True)),
                "available": True,
                "gds_ready": True,
                "tech_ok": bool(detected.get("tech_ok", False)),
                "proprietary": False,
                "root_path": detected.get("root_path", ""),
                "status": "ready",
                "reason": "Custom PDK detected for this workspace.",
            }
        )

    ready = [item for item in items if item["gds_ready"]]
    return {
        "default": DEFAULT_PDK_PROFILE,
        "pdk_root": PDK_ROOT,
        "pdks": items,
        "gds_ready_pdks": ready,
    }


# Registry for active orchestrator instances to support HITL interaction
RUNNING_ORCHESTRATORS: Dict[str, Any] = {}

# Training data output path
TRAINING_JSONL = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "training", "agentic_sft_data.jsonl")
)

DEFAULT_FLOW_PROFILE = resolve_flow_profile()
BUILD_STATES_ORDER = list(DEFAULT_FLOW_PROFILE.stages)
TOTAL_STEPS = len(BUILD_STATES_ORDER)

STAGE_META: Dict[str, Dict[str, str]] = get_stage_meta()

_KNOWN_MODEL_PREFIXES = (
    "openai/",
    "azure/",
    "azure_ai/",
    "groq/",
    "ollama/",
    "anthropic/",
    "nvidia_nim/",
    "infinity/",
    "huggingface/",
    "together_ai/",
    "mistral/",
    "deepseek/",
    "openrouter/",
    "xai/",
    "gemini/",
    "cohere/",
    "perplexity/",
    "replicate/",
    "bedrock/",
    "vertex_ai/",
)

BYOK_DEFAULT_MODEL = (
    os.getenv("BYOK_DEFAULT_MODEL", "").strip()
    or os.getenv("LLM_MODEL", "").strip()
    or "infinity"
)
BYOK_DEFAULT_BASE_URL = (
    os.path.expandvars(
        os.getenv("BYOK_DEFAULT_BASE_URL", "").strip()
        or os.getenv("LLM_BASE_URL", "").strip()
    )
    or "https://api.openai.com/v1"
)


def _has_real_api_key(value: str) -> bool:
    return bool(value and value.strip() and value.strip() not in ("NA", "mock-key"))


def _has_any_byok_api_key(byok_config: Optional[dict]) -> bool:
    if not byok_config:
        return False
    for group_name in ("group1", "group2", "group3"):
        group = byok_config.get(group_name) or {}
        if _has_real_api_key(group.get("api_key", "")):
            return True
    return False


ROLE_GROUP_MAP: Dict[str, str] = {
    "fixer": "group1",
    "debugger": "group1",
    "reasoner": "group1",
    "architect": "group2",
    "designer": "group2",
    "testbench_designer": "group2",
    "verifier": "group2",
    "manager": "group2",
    "physical": "group2",
    "documenter": "group3",
    "reporter": "group3",
    "doc_gen": "group3",
}

PRIMARY_GROUP_FALLBACK_ROLE: Dict[str, str] = {
    "group1": "fixer",
    "group2": "architect",
    "group3": "documenter",
}


def _normalize_model_name(model: str, base_url: str) -> str:
    model = (model or "").strip()
    base_url = (base_url or "").strip()
    if (
        base_url
        and ".openai.infinity.com" in base_url.lower()
        and model
        and not model.startswith("infinity/")
    ):
        return f"infinity/{model}"
    if (
        base_url
        and model
        and not any(model.startswith(prefix) for prefix in _KNOWN_MODEL_PREFIXES)
    ):
        return f"openai/{model}"
    return model


def _load_request_byok_config(
    request: Request, profile: Optional[dict]
) -> tuple[Optional[dict], str]:
    header_key = request.headers.get("X-LLM-API-Key")
    if header_key:
        try:
            parsed = json.loads(header_key)
        except json.JSONDecodeError:
            parsed = {
                "group1": {"api_key": header_key},
                "group2": {"api_key": header_key},
                "group3": {"api_key": header_key},
            }
        return _normalize_byok_config(parsed), "request_header"

    profile_byok = get_byok_config_for_user(profile)
    if profile_byok:
        return _normalize_byok_config(profile_byok), "stored_profile"
    return None, "backend_env"


def _resolve_role_llm_details(
    role: str, byok_config: Optional[dict] = None
) -> Dict[str, Any]:
    from agentic.config import get_role_llm_config

    cfg = get_role_llm_config(role)
    target_group = ROLE_GROUP_MAP.get(role, "group2")
    group = (byok_config or {}).get(target_group, {}) if byok_config else {}

    if group.get("api_key"):
        model = _normalize_model_name(
            group.get("model") or cfg.get("model", ""),
            group.get("base_url") or cfg.get("base_url", ""),
        )
        result: Dict[str, Any] = {
            "role": role,
            "group": target_group,
            "model": model or cfg.get("model", ""),
            "api_key": group.get("api_key", ""),
            "base_url": (group.get("base_url") or cfg.get("base_url") or "").strip(),
            "source": f"BYOK {target_group}",
        }
        if "deepseek" in result["model"].lower():
            result["extra_body"] = {"chat_template_kwargs": {"thinking": True}}
        return result

    # Legacy behavior: if only a single BYOK key exists, keep it usable for all roles.
    legacy_byok_key = (
        (byok_config or {}).get("group1", {}).get("api_key", "") if byok_config else ""
    )
    result = {
        "role": role,
        "group": target_group,
        "model": cfg.get("model", ""),
        "api_key": legacy_byok_key if legacy_byok_key else cfg.get("api_key", ""),
        "base_url": cfg.get("base_url", ""),
        "source": "Stored Profile BYOK fallback"
        if legacy_byok_key
        else "Local .env config",
    }
    if "extra_body" in cfg:
        result["extra_body"] = cfg["extra_body"]
    return result


def _resolve_primary_llm_details(
    byok_config: Optional[dict] = None,
) -> Optional[Dict[str, Any]]:
    from agentic.config import get_role_llm_config

    for group_name in ("group2", "group1", "group3"):
        group = (byok_config or {}).get(group_name, {}) if byok_config else {}
        if not group.get("api_key"):
            continue
        fallback_role = PRIMARY_GROUP_FALLBACK_ROLE[group_name]
        cfg = get_role_llm_config(fallback_role)
        model = _normalize_model_name(
            group.get("model") or cfg.get("model", ""),
            group.get("base_url") or cfg.get("base_url", ""),
        )
        result: Dict[str, Any] = {
            "group": group_name,
            "model": model or cfg.get("model", ""),
            "api_key": group.get("api_key", ""),
            "base_url": (group.get("base_url") or cfg.get("base_url") or "").strip(),
            "source": f"BYOK {group_name}",
        }
        if "deepseek" in result["model"].lower():
            result["extra_body"] = {"chat_template_kwargs": {"thinking": True}}
        return result
    return None


def _get_llm(byok_config: Optional[dict] = None, is_agentic_paid: bool = False):
    """Get an LLM instance for chip builds.

    Two paths:
    - is_agentic_paid=True + no byok_config → use server's VERILOG_CODEGEN_CONFIG
    - byok_config provided → use user's BYOK keys (single key for all roles)
    """
    try:
        from crewai import LLM
    except Exception as imp_err:
        raise RuntimeError(f"Cannot import crewai.LLM: {imp_err}")

    # Path 1: AgentIC-paid — use server's VERILOG_CODEGEN model
    if is_agentic_paid and not byok_config:
        from agentic.config import VERILOG_CODEGEN_CONFIG, VERILOG_CODEGEN_ENABLED

        if not VERILOG_CODEGEN_ENABLED:
            raise RuntimeError(
                "Managed AgentIC model access is not enabled for this workspace."
            )
        cfg = VERILOG_CODEGEN_CONFIG
        model = cfg.get("model", "").strip() or "infinity"
        api_key = cfg.get("api_key", "").strip()
        base_url = cfg.get("base_url", "").strip()
        model = _normalize_model_name(model, base_url)
        if not api_key:
            raise RuntimeError(
                "Managed AgentIC model access is not fully configured for this workspace."
            )
        try:
            llm_kwargs = dict(
                model=model, api_key=api_key, temperature=0.6, max_tokens=16384
            )
            if base_url:
                llm_kwargs["base_url"] = base_url
            if "infinity/" in model.lower():
                api_version = os.getenv("INFINITY_API_VERSION") or os.getenv("VERILOG_CODEGEN_API_VERSION")
                if api_version:
                    llm_kwargs["api_version"] = api_version
            llm = LLM(**llm_kwargs)
            return llm, f"AgentIC ({model})"
        except Exception as e:
            raise RuntimeError(f"Managed model connection failed: {e}")

    # Path 2: BYOK — use user's provided keys
    if not byok_config:
        raise RuntimeError(
            "No API key configured. Select 'AgentIC Model' to use the built-in model, "
            "or configure 'Bring Your Own Key' to use your own API keys."
        )

    # Find first available BYOK key from any group
    byok_api_key = ""
    byok_model = ""
    byok_base_url = ""
    for group_name in ("group1", "group2", "group3"):
        group = byok_config.get(group_name, {})
        key = group.get("api_key", "").strip()
        if key and key not in ("NA", "mock-key", ""):
            byok_api_key = key
            byok_model = group.get("model", "").strip() or byok_model
            byok_base_url = group.get("base_url", "").strip() or byok_base_url
            break

    if not byok_api_key:
        raise RuntimeError(
            "No valid BYOK API key found. Please configure your API keys in Workspace Settings."
        )

    model = byok_model or "infinity"
    base_url = byok_base_url.strip()
    model = _normalize_model_name(model, base_url)

    try:
        llm_kwargs = dict(
            model=model, api_key=byok_api_key, temperature=0.6, max_tokens=16384
        )
        if base_url:
            llm_kwargs["base_url"] = base_url
        if "infinity/" in model.lower():
            api_version = os.getenv("INFINITY_API_VERSION")
            if api_version:
                llm_kwargs["api_version"] = api_version
        if "deepseek" in model.lower():
            llm_kwargs["extra_body"] = {"chat_template_kwargs": {"thinking": True}}
        llm = LLM(**llm_kwargs)
        return llm, f"BYOK ({model})"
    except Exception as e:
        raise RuntimeError(f"Model provider connection failed: {e}")


def _get_role_llm_map(
    byok_config: Optional[dict] = None, is_agentic_paid: bool = False
) -> Dict[str, Any]:
    from crewai import LLM

    # When agentic-paid, use VERILOG_CODEGEN for all roles
    if is_agentic_paid:
        from agentic.config import VERILOG_CODEGEN_CONFIG

        cfg = VERILOG_CODEGEN_CONFIG
        model = cfg.get("model", "").strip() or "infinity"
        api_key = cfg.get("api_key", "").strip()
        base_url = cfg.get("base_url", "").strip()
        model = _normalize_model_name(model, base_url)
        llm_kwargs = dict(
            model=model, api_key=api_key, temperature=0.6, max_tokens=16384
        )
        if base_url:
            llm_kwargs["base_url"] = base_url
        if "infinity/" in model.lower():
            api_version = os.getenv("INFINITY_API_VERSION") or os.getenv("VERILOG_CODEGEN_API_VERSION")
            if api_version:
                llm_kwargs["api_version"] = api_version
        shared_llm = LLM(**llm_kwargs)
        logger.info("AgentIC Compute Routing Map: ALL ROLES -> AgentIC (%s)", model)
        return {
            role: shared_llm
            for role in [
                "architect",
                "designer",
                "testbench_designer",
                "verifier",
                "fixer",
                "debugger",
                "manager",
                "physical",
                "documenter",
                "reporter",
            ]
        }

    roles = [
        "architect",
        "designer",
        "testbench_designer",
        "verifier",
        "fixer",
        "debugger",
        "manager",
        "physical",
        "documenter",
        "reporter",
    ]
    role_map = {}
    debug_log_map = {}

    for role in roles:
        resolved = _resolve_role_llm_details(role, byok_config=byok_config)
        llm_kwargs = dict(
            model=resolved["model"],
            api_key=resolved.get("api_key", ""),
            temperature=0.6,
        )
        if resolved.get("base_url"):
            llm_kwargs["base_url"] = resolved["base_url"]
        if "infinity/" in resolved["model"].lower():
            api_version = os.getenv("INFINITY_API_VERSION")
            if api_version:
                llm_kwargs["api_version"] = api_version
        if "extra_body" in resolved:
            llm_kwargs["extra_body"] = resolved["extra_body"]

        role_map[role] = LLM(**llm_kwargs)
        debug_log_map[role] = f"{resolved['model']} [{resolved['source']}]"

    # Print the assignments so they appear in Docker logs
    for r, m in debug_log_map.items():
        logger.debug("LLM Routing: %s -> %s", r.upper(), m)

    return role_map


@app.get("/debug/llm-routing")
async def debug_llm_routing(
    request: Request, profile: dict = Depends(get_current_user)
):
    if os.getenv("AGENTIC_ENABLE_DEBUG_ROUTES", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=404, detail="Not found")

    byok_config, byok_source = _load_request_byok_config(request, profile)
    roles = [
        "architect",
        "designer",
        "testbench_designer",
        "verifier",
        "fixer",
        "debugger",
        "manager",
        "physical",
        "documenter",
        "reporter",
    ]
    resolved_roles = {}
    for role in roles:
        resolved = _resolve_role_llm_details(role, byok_config=byok_config)
        resolved_roles[role] = {
            "group": resolved["group"],
            "source": resolved["source"],
            "model": resolved["model"],
            "base_url": resolved.get("base_url", ""),
            "has_api_key": bool(
                (resolved.get("api_key") or "").strip()
                and (resolved.get("api_key") or "").strip() not in ("NA", "mock-key")
            ),
        }

    return {
        "byok_source": byok_source,
        "managed_fallback_allowed": ALLOW_BACKEND_LLM_FALLBACK,
        "primary_llm": (
            {
                "group": primary["group"],
                "source": primary["source"],
                "model": primary["model"],
                "base_url": primary.get("base_url", ""),
                "has_api_key": bool((primary.get("api_key") or "").strip()),
            }
            if (primary := _resolve_primary_llm_details(byok_config))
            else None
        ),
        "groups_present": {
            "group1": bool((byok_config or {}).get("group1", {}).get("api_key")),
            "group2": bool((byok_config or {}).get("group2", {}).get("api_key")),
            "group3": bool((byok_config or {}).get("group3", {}).get("api_key")),
        },
        "roles": resolved_roles,
    }


def _emit_event(
    job_id: str,
    event_type: str,
    state: str,
    message: str,
    step: int = 0,
    extra: Optional[dict] = None,
):
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
    # Also update current state
    JOB_STORE[job_id]["current_state"] = state
    JOB_STORE[job_id]["events"].append(event)
    _sync_job_to_db(job_id)


def _emit_agent_thought(
    job_id: str, agent_name: str, thought_type: str, content: str, state: str = ""
):
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
        "step": BUILD_STATES_ORDER.index(payload.get("stage_name", "INIT")) + 1
        if payload.get("stage_name") in BUILD_STATES_ORDER
        else 0,
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
    skip_spice: bool = True
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
    coverage_backend: str = "auto"
    coverage_fallback_policy: str = "fail_closed"
    coverage_profile: str = "balanced"
    human_in_loop: bool = False
    skip_stages: List[str] = []
    flow_profile: str = ""
    plan_type: str = "byok"


class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    plan_type: str = "byok"
    api_key: Optional[str] = None
    pdk_profile: Optional[str] = None
    pdk_options: Optional[List[Dict[str, Any]]] = None

def _looks_like_chip_build_request(description: str) -> bool:
    text = re.sub(r"\s+", " ", (description or "").strip().lower())
    if not text:
        return False
    if re.fullmatch(r"(hi+|hello+|hey+|yo+|sup|thanks|thank you|ok|okay|test)", text):
        return False
    hardware = re.search(
        r"\b(chip|rtl|verilog|systemverilog|vlsi|asic|fpga|pdk|sky130|gf180|gds|layout|"
        r"synthesis|synthesize|timer|uart|spi|i2c|axi|apb|wishbone|fifo|ram|rom|sram|"
        r"cpu|risc|risc-v|mcu|microcontroller|alu|dma|pwm|watchdog|aes|sha|trng|gpio|"
        r"counter|fsm|pll|adc|dac|register|bus|peripheral|accelerator|core)\b",
        text,
    )
    if not hardware:
        return False
    words = [w for w in text.split(" ") if w]
    action = re.search(r"\b(build|create|design|generate|make|implement|synthesize|harden|layout|verify)\b", text)
    detail = re.search(
        r"\b(with|using|include|support|clock|reset|register|interrupt|memory[- ]mapped|"
        r"bit|width|mhz|khz|formal|testbench|coverage|gdsii|openlane|fifo|divider|interface)\b",
        text,
    )
    return bool((detail and len(words) >= 4) or (action and len(words) >= 6) or len(words) >= 10)

class RagQueryRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=4000)
    domain: Optional[str] = None
    pdk: Optional[str] = None
    stage: str = ""
    top_k: int = Field(6, ge=1, le=12)
    answer: bool = True


class RagIngestRequest(BaseModel):
    path: str = Field(..., min_length=1)
    recursive: bool = True


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
    if (
        not design_name
        or not _SAFE_DESIGN_NAME_RE.match(design_name)
        or ".." in design_name
    ):
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

    This function is called in the finally block so it runs on BOTH success AND failure.
    """
    job = JOB_STORE.get(job_id)
    if not job:
        logger.warning(f"Job {job_id} not found for report generation")
        return

    stages = job.get("stages", {})
    events = job.get("events", [])
    build_status = job.get("build_status", "unknown")
    
    logger.info(f"Generating build reports for {design_name}, status={build_status}, stages_completed={len(stages)}")

    workspace_dir = os.path.join(_repo_root(), "designs", design_name)
    os.makedirs(workspace_dir, exist_ok=True)
    manifest = {
        "job_id": job_id,
        "design_name": design_name,
        "build_status": build_status,
        "generated_at": int(time.time()),
        "workspace_dir": workspace_dir,
        "artifacts": [],
    }

    upload_artifact_to_cloud: Optional[Callable[[str, str], str]] = None
    storage_bucket = ""
    try:
        from server.storage import (
            S3_BUCKET_NAME,
            upload_artifact_to_cloud as upload_to_cloud,
        )

        storage_bucket = S3_BUCKET_NAME
        upload_artifact_to_cloud = upload_to_cloud
    except Exception:
        pass

    def _register_artifact(local_path: str) -> None:
        if not os.path.exists(local_path):
            return
        entry = {
            "name": os.path.basename(local_path),
            "local_path": local_path,
            "size": os.path.getsize(local_path),
            "type": _classify_artifact(local_path),
            "cloud_url": "",
        }
        if upload_artifact_to_cloud is not None:
            cloud_url = upload_artifact_to_cloud(
                local_path, f"{design_name}/{os.path.basename(local_path)}"
            )
            if cloud_url:
                entry["cloud_url"] = cloud_url
        manifest["artifacts"].append(entry)

    # Collect all generated files from workspace directory as fallback artifacts
    # This ensures we have artifacts even if stage summaries are missing
    all_generated_files = []
    try:
        if os.path.exists(workspace_dir):
            for f in os.listdir(workspace_dir):
                fpath = os.path.join(workspace_dir, f)
                if os.path.isfile(fpath) and not f.endswith('_Build_Report.pdf') and not f.endswith('_Build_Report.docx') and not f.endswith('_artifact_manifest.json'):
                    all_generated_files.append({
                        "name": f,
                        "local_path": fpath,
                        "size": os.path.getsize(fpath),
                        "type": _classify_artifact(fpath),
                        "cloud_url": "",
                    })
        logger.info(f"Found {len(all_generated_files)} generated files in workspace for {design_name}")
    except Exception as e:
        logger.warning(f"Failed to scan workspace for artifacts: {e}")

    # Add all generated files to manifest
    for f in all_generated_files:
        manifest["artifacts"].append(f)

    try:
        pdf_bytes = generate_full_report_pdf(stages, design_name, build_status, events, all_generated_files)
        pdf_path = os.path.join(workspace_dir, f"{design_name}_Build_Report.pdf")
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
        _register_artifact(pdf_path)
        logger.info(f"Successfully generated PDF report for {design_name}")
    except Exception as e:
        logger.warning("Failed to generate auto PDF report for %s: %s", design_name, e)

    try:
        docx_bytes = generate_full_report_docx(
            stages, design_name, build_status, events, all_generated_files
        )
        docx_path = os.path.join(workspace_dir, f"{design_name}_Build_Report.docx")
        with open(docx_path, "wb") as f:
            f.write(docx_bytes)
        _register_artifact(docx_path)
        logger.info(f"Successfully generated DOCX report for {design_name}")
    except Exception as e:
        logger.warning("Failed to generate auto DOCX report for %s: %s", design_name, e)

    manifest_path = os.path.join(workspace_dir, f"{design_name}_artifact_manifest.json")
    try:
        with open(manifest_path, "w", encoding="utf-8") as manifest_fp:
            json.dump(manifest, manifest_fp, indent=2)
        if upload_artifact_to_cloud is not None:
            manifest_cloud_url = upload_artifact_to_cloud(
                manifest_path, f"{design_name}/{os.path.basename(manifest_path)}"
            )
            if manifest_cloud_url:
                manifest["manifest_cloud_url"] = manifest_cloud_url
                with open(manifest_path, "w", encoding="utf-8") as manifest_fp:
                    json.dump(manifest, manifest_fp, indent=2)
    except Exception as e:
        logger.warning("Failed to persist artifact manifest for %s: %s", design_name, e)

    result = job.setdefault("result", {})
    result["generated_reports"] = manifest["artifacts"]
    result["artifact_manifest"] = manifest_path
    result["object_storage_bucket"] = storage_bucket
    _sync_job_to_db(job_id)


# ─── Build Runner ────────────────────────────────────────────────────
def _run_agentic_build(job_id: str, req: BuildRequest):
    """Runs the full AgentIC build in a background thread, emitting events.

    When human_in_loop is enabled, the orchestrator pauses after each stage
    and waits for user approval via the /approve or /reject endpoints.
    """
    try:
        from agentic.orchestrator import BuildOrchestrator, BuildState

        JOB_STORE[job_id]["status"] = "running"
        JOB_STORE[job_id]["human_in_loop"] = req.human_in_loop
        JOB_STORE[job_id]["flow_profile"] = req.flow_profile or DEFAULT_FLOW_PROFILE.name
        JOB_STORE[job_id]["waiting_approval"] = False
        JOB_STORE[job_id]["waiting_stage"] = ""
        JOB_STORE[job_id]["skip_stages"] = req.skip_stages or []
        _emit_event(
            job_id,
            "checkpoint",
            "INIT",
            "🚀 Build started — initializing workspace",
            step=1,
        )

        # Current agent tracker for thought events
        current_agent_state = {"name": "Orchestrator", "stage": "INIT"}

        def event_sink(event: dict):
            """Hook called by orchestrator on every log/transition."""
            state = event.get("state", "UNKNOWN")
            message = event.get("message", "")
            event_type = event.get("type", "log")
            extra = {
                k: v
                for k, v in event.items()
                if k not in {"type", "state", "message"}
            }
            step = (
                BUILD_STATES_ORDER.index(state) + 1
                if state in BUILD_STATES_ORDER
                else 0
            )
            _emit_event(job_id, event_type, state, message, step=step, extra=extra)

            # Also emit as agent_thought for the live activity feed
            if message and event_type in ("log", "checkpoint", "design_decision"):
                # Infer agent name from state
                agent_name = _infer_agent_name(state, message)
                thought_type = _infer_thought_type(message)
                if event_type == "design_decision":
                    thought_type = "decision"
                _emit_agent_thought(job_id, agent_name, thought_type, message, state)

        # Resolve LLM: AgentIC-paid uses server VERILOG_CODEGEN, BYOK uses user's keys
        byok_key = JOB_STORE[job_id].get("byok_key")
        is_agentic_paid = JOB_STORE[job_id].get("plan_type") == "agentic_paid"
        llm, llm_name = _get_llm(byok_config=byok_key, is_agentic_paid=is_agentic_paid)
        role_llms = _get_role_llm_map(
            byok_config=byok_key, is_agentic_paid=is_agentic_paid
        )
        _emit_event(job_id, "checkpoint", "INIT", f"🤖 Compute engine ready", step=1)

        IS_HUGGINGFACE = os.environ.get("SPACE_ID") is not None
        forced_skip_openlane = True if IS_HUGGINGFACE else req.skip_openlane

        orchestrator = BuildOrchestrator(
            name=req.design_name,
            desc=req.description,
            llm=llm,
            max_retries=req.max_retries,
            verbose=req.show_thinking,
            skip_openlane=forced_skip_openlane,  # TEMPORARY HF MAINTENANCE OVERRIDE
            skip_spice=req.skip_spice,
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
            flow_profile=req.flow_profile,
            event_sink=event_sink,
            role_llms=_get_role_llm_map(byok_config=byok_key),
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
            # First: check if the orchestrator itself crashed (e.g. LLM auth error)
            crash_error = orchestrator.artifacts.get("crash_error", "")
            crash_tb = orchestrator.artifacts.get("crash_traceback", "")
            if crash_error:
                result["failure_explanation"] = f"Orchestrator crashed: {crash_error}"
                result["failure_suggestion"] = (
                    "Check your BYOK API key, model name, and base_url in Workspace Settings. "
                    "The LLM call likely failed due to authentication or connectivity issues."
                )
                result["failed_stage"] = "INIT"
                result["failed_stage_human"] = "Initialization"
                result["crash_traceback"] = crash_tb
                logger.error("[job:%s] Orchestrator crash: %s", job_id, crash_error)
            else:
                try:
                    failed_state = orchestrator.state.name
                    # Find the last non-terminal state from build history
                    last_stage = "UNKNOWN"
                    for entry in reversed(orchestrator.build_history):
                        if entry.state not in ("SUCCESS", "FAIL", "UNKNOWN"):
                            last_stage = entry.state
                            break
                    error_log = get_stage_log_summary(orchestrator, last_stage)
                    explanation = generate_failure_explanation(
                        llm, last_stage, req.design_name, error_log
                    )
                    result["failure_explanation"] = explanation.get("explanation", "")
                    result["failure_suggestion"] = explanation.get("suggestion", "")
                    result["failed_stage"] = last_stage
                    result["failed_stage_human"] = STAGE_HUMAN_NAMES.get(
                        last_stage, last_stage.replace("_", " ").title()
                    )
                except Exception:
                    result["failure_explanation"] = ""
                    result["failure_suggestion"] = ""


        JOB_STORE[job_id]["result"] = result
        JOB_STORE[job_id]["status"] = "done" if success else "failed"
        JOB_STORE[job_id]["build_status"] = "success" if success else "failed"
        BUILDS_COMPLETED_TOTAL.labels(outcome="success" if success else "failed").inc()

        # ── Record build outcome in Supabase ───────────────────────
        user_profile = JOB_STORE[job_id].get("user_profile")
        if success:
            record_build_success(user_profile, job_id)
        else:
            record_build_failure(job_id)

        final_type = "done" if success else "error"
        final_msg = (
            "✅ Chip build completed successfully!"
            if success
            else "❌ Build failed. See logs for details."
        )
        _emit_event(
            job_id, final_type, orchestrator.state.name, final_msg, step=TOTAL_STEPS
        )

        # ── Auto-export to training JSONL ──────────────────────────
        _export_training_record(
            job_id, req.design_name, req.description, result, orchestrator
        )

    except Exception as e:
        import traceback

        err = traceback.format_exc()
        JOB_STORE[job_id]["status"] = "failed"
        JOB_STORE[job_id]["build_status"] = "failed"
        JOB_STORE[job_id]["result"] = {"error": str(e), "traceback": err}
        BUILDS_COMPLETED_TOTAL.labels(outcome="failed").inc()
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
        "POST_LAYOUT_SPICE": "Scoped SPICE Extension",
        "SIGNOFF": "Signoff Engineer",
    }
    return state_agents.get(state, "Orchestrator")


def _infer_thought_type(message: str) -> str:
    """Infer the thought type from message content."""
    msg_lower = message.lower()

    if any(
        kw in msg_lower
        for kw in ["running", "executing", "calling", "invoking", "checking"]
    ):
        return "tool_call"
    elif any(
        kw in msg_lower
        for kw in ["result:", "output:", "passed", "completed", "success"]
    ):
        return "tool_result"
    elif any(
        kw in msg_lower
        for kw in ["decided", "choosing", "strategy", "pivot", "fallback"]
    ):
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
        "FEASIBILITY_CHECK": f"Checking PDK feasibility for {design_name}...",
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
        "POST_LAYOUT_SPICE": f"Running scoped post-layout SPICE extension...",
        "SIGNOFF": f"Reviewing OSS DRC, LVS, STA, power, and commercial blockers...",
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
        while (
            orchestrator.state != BuildState.SUCCESS
            and orchestrator.state != BuildState.FAIL
        ):
            orchestrator.global_step_count += 1
            if orchestrator.global_step_count > orchestrator.global_step_budget:
                orchestrator.log(
                    f"Global step budget exceeded ({orchestrator.global_step_budget}). Failing closed.",
                    refined=True,
                )
                orchestrator.state = BuildState.FAIL
                break

            # Check for cancellation
            if JOB_STORE.get(job_id, {}).get("cancelled"):
                orchestrator.state = BuildState.FAIL
                break

            current_state_name = orchestrator.state.name

            # Auto-skip stages that the user opted out of
            if current_state_name in skip_stages:
                _emit_event(
                    job_id,
                    "log",
                    current_state_name,
                    f"Skipping {current_state_name.replace('_', ' ').title()} (user preference)",
                    step=BUILD_STATES_ORDER.index(current_state_name) + 1
                    if current_state_name in BUILD_STATES_ORDER
                    else 0,
                )
                next_st = get_next_stage(current_state_name)
                if next_st and hasattr(BuildState, next_st):
                    orchestrator.transition(getattr(BuildState, next_st))
                else:
                    orchestrator.state = BuildState.SUCCESS
                continue

            # Check for user feedback from previous rejection
            feedback = approval_manager.get_pending_feedback(design_name)
            if feedback:
                _emit_agent_thought(
                    job_id,
                    "Orchestrator",
                    "observation",
                    f"User feedback from review: {feedback}. Taking this into account before proceeding.",
                    current_state_name,
                )
                # Inject feedback into the orchestrator's context
                orchestrator.log(
                    f"User feedback from review: {feedback}. Take this into account before proceeding.",
                    refined=True,
                )

            # Emit thinking indicator before stage execution
            agent_name = _infer_agent_name(current_state_name, "")
            _emit_agent_thinking(
                job_id,
                agent_name,
                _get_thinking_message(current_state_name, orchestrator.name),
                current_state_name,
            )

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
                        "auto_selected": orchestrator.artifacts.get(
                            "elaborated_desc", ""
                        ),
                    },
                )
                # Clear the flag so we don't re-emit on the retry
                orchestrator.artifacts.pop("spec_elaboration_needed", None)

            # If the stage transitioned to a new state, the stage completed successfully
            # Generate approval card and wait
            if new_state != prev_state or new_state in (
                BuildState.SUCCESS,
                BuildState.FAIL,
            ):
                completed_stage = current_state_name

                # Don't wait for approval on terminal states
                if new_state in (BuildState.SUCCESS, BuildState.FAIL):
                    # Still emit stage_complete for the last stage before terminal
                    if completed_stage not in ("SUCCESS", "FAIL"):
                        _emit_stage_summary(
                            job_id,
                            orchestrator,
                            completed_stage,
                            design_name,
                            llm,
                            wait=False,
                        )
                    break

                # Generate and emit stage_complete, then wait for approval
                approved = _emit_stage_summary(
                    job_id, orchestrator, completed_stage, design_name, llm, wait=True
                )

                if not approved:
                    # User rejected — loop back to retry the CURRENT state
                    # Reset state back to the completed stage so the next loop iteration
                    # actually reruns it with the stored rejection feedback.
                    _emit_agent_thought(
                        job_id,
                        "Orchestrator",
                        "decision",
                        f"Stage {completed_stage} rejected by user. Retrying...",
                        new_state.name,
                    )
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

        summary = {
            k: v
            for k, v in orchestrator.artifacts.items()
            if "code" not in k and "spec" not in k
        }
        Console().print(
            Panel(
                f"[bold green]BUILD SUCCESSFUL[/]\n\n"
                + "\n".join([f"[bold]{k.upper()}:[/] {v}" for k, v in summary.items()]),
                title="Done",
            )
        )
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
        "VERIFICATION_PLAN": orchestrator.do_verification_plan,
        "RTL_GEN": orchestrator.do_rtl_gen,
        "RTL_FIX": orchestrator.do_rtl_fix,
        "LINT_CHECK": orchestrator.do_lint_check,
        "CDC_ANALYZE": orchestrator.do_cdc_analyze,
        "VERIFICATION": orchestrator.do_verification,
        "FORMAL_VERIFY": orchestrator.do_formal_verify,
        "COVERAGE_CHECK": orchestrator.do_coverage_check,
        "REGRESSION": orchestrator.do_regression,
        "SDC_GEN": orchestrator.do_sdc_gen,
        "SYNTHESIS": orchestrator.do_synthesis,
        "DFT_SCAN": orchestrator.do_dft_scan,
        "DFT_ATPG": orchestrator.do_dft_atpg,
        "MBIST": orchestrator.do_mbist,
        "GLS_SIMULATION": orchestrator.do_gls_simulation,
        "GLS_SIM": orchestrator.do_gls_simulation,
        "FLOORPLAN": orchestrator.do_floorplan,
        "HARDENING": orchestrator.do_hardening,
        "TIMING_ANALYSIS": orchestrator.do_timing_analysis,
        "CONVERGENCE_REVIEW": orchestrator.do_convergence_review,
        "ECO_PATCH": orchestrator.do_eco_patch,
        "POWER_ANALYSIS": orchestrator.do_power_analysis,
        "PHYSICAL_VERIFY": orchestrator.do_physical_verify,
        "POST_LAYOUT_SPICE": orchestrator.do_post_layout_spice,
        "SIGNOFF": orchestrator.do_signoff,
        "IP_PACKAGE": orchestrator.do_ip_package,
    }

    handler = stage_handlers.get(state_name)
    if handler:
        handler()
    else:
        orchestrator.log(f"Unknown state {state_name}", refined=False)
        orchestrator.state = BuildState.FAIL


def _emit_stage_summary(
    job_id: str, orchestrator, stage_name: str, design_name: str, llm, wait: bool = True
) -> bool:
    """Generate stage summary, emit stage_complete event, and optionally wait for approval.

    Returns True if approved (or not waiting), False if rejected.
    """
    # Emit thinking indicator while generating summary
    _emit_agent_thinking(
        job_id, "Orchestrator", "Preparing stage summary...", stage_name
    )

    # Build the stage_complete payload with LLM summary
    try:
        payload = build_stage_complete_payload(
            orchestrator, stage_name, design_name, llm
        )
    except Exception as e:
        payload = {
            "type": "stage_complete",
            "stage_name": stage_name,
            "summary": f"Stage {stage_name} completed. (Summary generation error: {str(e)[:100]})",
            "artifacts": [],
            "decisions": [],
            "warnings": [],
            "next_stage_name": get_next_stage(stage_name) or "DONE",
            "next_stage_preview": STAGE_DESCRIPTIONS.get(
                get_next_stage(stage_name) or "", ""
            ),
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
        _emit_agent_thought(
            job_id,
            "Orchestrator",
            "observation",
            f"⏰ Approval timeout for {stage_name}. Auto-proceeding.",
            stage_name,
        )
        return True


def _build_result_summary(orchestrator, design_name: str, success: bool) -> dict:
    """Collect all artifacts and metrics into a summary dict."""
    artifacts = orchestrator.artifacts or {}
    history = orchestrator.build_history or []

    # Self-healing telemetry (derived from build history + artifacts)
    lower_msgs = [h.message.lower() for h in history]
    self_heal_stats = {
        "stage_exception_count": sum(
            "stage " in m and "exception" in m for m in lower_msgs
        ),
        "formal_regen_count": int(artifacts.get("formal_regen_count", 0) or 0),
        "coverage_best_restore_count": sum(
            "restoring best testbench" in m for m in lower_msgs
        ),
        "coverage_regression_reject_count": sum(
            "tb regressed coverage" in m for m in lower_msgs
        ),
        "deterministic_tb_fallback_count": sum(
            "deterministic tb fallback" in m for m in lower_msgs
        ),
    }

    summary = {
        "success": success,
        "design_name": design_name,
        "spec": (artifacts.get("spec") or "")[:2000],
        "rtl_snippet": (artifacts.get("rtl_code") or "")[:1500],
        "paths": {
            k: v
            for k, v in artifacts.items()
            if isinstance(v, str) and os.path.exists(v)
        },
        "coverage": artifacts.get("coverage", {}),
        "formal_result": artifacts.get("formal_result", ""),
        "signoff_result": artifacts.get("signoff_result", ""),
        "convergence_history": [
            {
                "iteration": s.iteration,
                "wns": s.wns,
                "tns": s.tns,
                "congestion": s.congestion,
                "area_um2": s.area_um2,
                "power_w": s.power_w,
            }
            for s in (orchestrator.convergence_history or [])
        ],
        "self_heal": self_heal_stats,
        "total_steps": len(history),
        "strategy": orchestrator.strategy.value if orchestrator.strategy else "",
        "build_time_s": int(time.time())
        - (history[0].timestamp if history else int(time.time())),
    }

    # Try to read OpenLane metrics using the resolved runtime workspace root.
    from agentic.config import OPENLANE_ROOT

    openlane_root = OPENLANE_ROOT
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
        pw = (
            float(row.get("power_typical_internal_uW", 0))
            + float(row.get("power_typical_switching_uW", 0))
            + float(row.get("power_typical_leakage_uW", 0))
        )
        return f"{pw / 1000:.3f} mW"
    except Exception:
        return "N/A"


def _export_training_record(
    job_id: str, design_name: str, description: str, result: dict, orchestrator
):
    """Append a completed build as a JSONL record for local model training.

    Format is SFT-compatible: one JSON object per line with
    'instruction', 'input', 'output', and metadata fields.
    This feeds directly into training/generate_reasoning.py workflow.
    """
    try:
        os.makedirs(os.path.dirname(TRAINING_JSONL), exist_ok=True)
        history = orchestrator.build_history or []
        log_text = "\n".join(f"[{h.state}] {h.message}" for h in history)[:8000]

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


@app.get("/livez")
def livez():
    """Container/process liveness probe."""
    return {"status": "ok", "version": "3.0.0"}


@app.get("/readyz")
def readyz():
    """Infrastructure readiness probe for API, DB, Redis, and object storage."""
    status = _collect_platform_status(include_llm=False)
    return status


@app.get("/health")
def health_check():
    """Deep health probe including infrastructure and LLM reachability."""
    from agentic.config import VERILOG_CODEGEN_CONFIG, VERILOG_CODEGEN_ENABLED

    status = _collect_platform_status(include_llm=True)
    status.update(
        {
            "verilog_codegen_enabled": VERILOG_CODEGEN_ENABLED,
            "verilog_codegen_key_set": bool(
                VERILOG_CODEGEN_CONFIG.get("api_key", "").strip()
            ),
            "verilog_codegen_model": VERILOG_CODEGEN_CONFIG.get("model", ""),
            "verilog_codegen_base_url": VERILOG_CODEGEN_CONFIG.get("base_url", ""),
        }
    )
    return status


@app.get("/ops/metrics")
def ops_metrics():
    """Prometheus-compatible metrics endpoint for free observability tooling."""
    counts = _job_status_counts()
    known_statuses = {"queued", "running", "done", "failed", "cancelled", "cancelling"}
    for status in known_statuses:
        JOBS_BY_STATUS.labels(status=status).set(counts.get(status, 0))
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/ops/summary")
def ops_summary(profile: dict = Depends(get_current_user)):
    """Compact operator summary for health, usage, and recent activity."""
    jobs = _load_jobs_for_profile(profile)
    summary = _summarize_jobs(jobs)
    platform = _collect_platform_status(include_llm=False)
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "auth_enabled": AUTH_ENABLED,
        "workspace_scope": "user" if profile is not None else "local",
        "version": "3.0.0",
        "platform": platform,
        "usage": summary,
        "recent_jobs": _recent_job_activity(jobs, limit=8),
    }


@app.get("/ops/jobs/export")
def export_jobs_backup(profile: dict = Depends(get_current_user)):
    """Download workspace job history as JSON for backup and migration."""
    jobs = _load_jobs_for_profile(profile)
    payload = {
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "version": "3.0.0",
        "auth_enabled": AUTH_ENABLED,
        "workspace_scope": "user" if profile is not None else "local",
        "profile": {
            "id": profile.get("id") if profile else None,
            "email": profile.get("email") if profile else None,
            "plan": profile.get("plan") if profile else None,
        },
        "usage": _summarize_jobs(jobs),
        "jobs": jobs,
    }
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    filename = f"agentic-jobs-backup-{stamp}.json"
    return Response(
        content=json.dumps(payload, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/pipeline/schema")
def get_pipeline_schema(flow_profile: str = "", pdk_profile: str = ""):
    """Canonical pipeline schema for frontend timeline rendering."""
    profile = resolve_flow_profile(flow_profile, pdk=pdk_profile)
    stages = [
        {"state": s, **STAGE_META.get(s, {"label": s, "icon": "•"})}
        for s in profile.stages
    ]
    return {
        "stages": stages,
        "flow_profile": profile.to_schema(),
        "terminal_states": ["SUCCESS", "FAIL"],
        "optional_stages": profile.optional_stages,
        "blocked_extensions": [
            {"state": s, **STAGE_META.get(s, {"label": s, "icon": "•"})}
            for s in profile.blocked_extensions
        ],
        "total_steps": len(profile.stages),
    }


@app.get("/pdks")
def list_available_pdks():
    """Return known and installed PDKs so the web UI only offers real GDSII targets."""
    return _pdk_catalog_payload()


@app.get("/rag/health")
def get_rag_health():
    """Report Qdrant-backed VLSI RAG readiness for VPS deployments."""
    try:
        from agentic.core.vlsi_rag import VLSIKnowledgeBase

        kb = VLSIKnowledgeBase()
        stats = kb.stats()
        if "error" in stats:
            return {
                "ok": False,
                "qdrant_url": os.getenv("VLSI_RAG_QDRANT_URL", "local"),
                "error": stats["error"],
            }
        return {
            "ok": True,
            "qdrant_url": os.getenv("VLSI_RAG_QDRANT_URL", "local"),
            **stats,
        }
    except Exception as exc:
        return {
            "ok": False,
            "qdrant_url": os.getenv("VLSI_RAG_QDRANT_URL", "local"),
            "error": str(exc),
        }


@app.post("/rag/query")
def query_vlsi_rag(req: RagQueryRequest):
    """Query the VLSI knowledge base from the web/backend runtime."""
    try:
        from agentic.core.vlsi_rag import VLSIKnowledgeBase

        kb = VLSIKnowledgeBase()
        if req.answer:
            result = kb.answer(
                req.query,
                domain=req.domain,
                pdk=req.pdk,
                top_k=req.top_k,
            )
            return result
        return {
            "context": kb.build_context(
                query=req.query,
                stage=req.stage,
                target_pdk=req.pdk or "",
                top_k=req.top_k,
            )
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"RAG query failed: {exc}")


@app.post("/rag/ingest")
def ingest_vlsi_rag(req: RagIngestRequest):
    """Ingest a mounted knowledge file or directory into Qdrant."""
    try:
        from agentic.core.vlsi_rag import VLSIKnowledgeBase

        root = os.path.abspath(os.path.expanduser(req.path))
        knowledge_root = os.path.abspath(
            os.path.expanduser(os.getenv("AGENTIC_KNOWLEDGE_DIR", "/app/knowledge/hardware"))
        )
        if not os.path.exists(root):
            raise HTTPException(status_code=404, detail=f"Path not found: {req.path}")
        allowed_roots = [knowledge_root, "/tmp"]
        if not any(os.path.commonpath([root, allowed]) == allowed for allowed in allowed_roots):
            raise HTTPException(
                status_code=400,
                detail=f"Ingest path must be under {knowledge_root} or /tmp",
            )

        kb = VLSIKnowledgeBase()
        if os.path.isfile(root):
            kb.ingest_file(root)
            return {"ingested": 1, "path": root}

        allowed_suffixes = {".pdf", ".md", ".txt", ".sv", ".v", ".lib", ".sp", ".spice", ".sdc", ".tcl", ".lef"}
        pattern = "**/*" if req.recursive else "*"
        count = 0
        for path in sorted(Path(root).glob(pattern)):
            if path.is_file() and path.suffix.lower() in allowed_suffixes:
                kb.ingest_file(str(path))
                count += 1
        return {"ingested": count, "path": root}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"RAG ingest failed: {exc}")


@app.get("/build/options")
def get_build_options_contract():
    """Metadata contract for web build-option UI and docs sync."""
    pdk_catalog = _pdk_catalog_payload()
    pdk_values = [item["key"] for item in pdk_catalog["pdks"]]
    pdk_ready_values = [item["key"] for item in pdk_catalog["gds_ready_pdks"]]
    return {
        "groups": [
            {
                "name": "Core",
                "options": [
                    {
                        "key": "strict_gates",
                        "type": "boolean",
                        "default": True,
                        "description": "Enable strict gate enforcement with bounded self-healing.",
                    },
                    {
                        "key": "full_signoff",
                        "type": "boolean",
                        "default": False,
                        "description": "Run full physical signoff checks when available.",
                    },
                    {
                        "key": "skip_openlane",
                        "type": "boolean",
                        "default": False,
                        "description": "Skip physical implementation stages for faster RTL-only iteration.",
                    },
                    {
                        "key": "skip_coverage",
                        "type": "boolean",
                        "default": False,
                        "description": "Skip the coverage stage and continue from formal verification to regression.",
                    },
                    {
                        "key": "skip_spice",
                        "type": "boolean",
                        "default": False,
                        "description": "Skip post-layout ngspice simulation for faster physical iterations.",
                    },
                    {
                        "key": "max_retries",
                        "type": "int",
                        "default": 5,
                        "min": 1,
                        "max": 12,
                        "description": "Max repair retries per stage.",
                    },
                ],
            },
            {
                "name": "Coverage",
                "options": [
                    {
                        "key": "min_coverage",
                        "type": "float",
                        "default": 80.0,
                        "min": 0.0,
                        "max": 100.0,
                        "description": "Minimum line coverage threshold.",
                    },
                    {
                        "key": "coverage_profile",
                        "type": "enum",
                        "default": "balanced",
                        "values": ["balanced", "aggressive", "relaxed"],
                        "description": "Profile-based line/branch/toggle/function thresholds.",
                    },
                    {
                        "key": "coverage_backend",
                        "type": "enum",
                        "default": "auto",
                        "values": ["auto", "verilator", "iverilog"],
                        "description": "Coverage simulator backend selection.",
                    },
                    {
                        "key": "coverage_fallback_policy",
                        "type": "enum",
                        "default": "fail_closed",
                        "values": ["fail_closed", "fallback_oss", "skip"],
                        "description": "Behavior when coverage infra fails.",
                    },
                ],
            },
            {
                "name": "Verification",
                "options": [
                    {
                        "key": "tb_gate_mode",
                        "type": "enum",
                        "default": "strict",
                        "values": ["strict", "relaxed"],
                        "description": "TB compile/static gate mode.",
                    },
                    {
                        "key": "tb_max_retries",
                        "type": "int",
                        "default": 3,
                        "min": 1,
                        "max": 10,
                        "description": "TB-specific retry budget.",
                    },
                    {
                        "key": "tb_fallback_template",
                        "type": "enum",
                        "default": "uvm_lite",
                        "values": ["uvm_lite", "classic"],
                        "description": "Deterministic fallback testbench template.",
                    },
                ],
            },
            {
                "name": "Physical",
                "options": [
                    {
                        "key": "pdk_profile",
                        "type": "enum",
                        "default": pdk_catalog["default"],
                        "values": pdk_values,
                        "gds_ready_values": pdk_ready_values,
                        "description": "PDK profile. Full GDSII runs require the selected PDK to be available in this workspace.",
                    },
                    {
                        "key": "max_pivots",
                        "type": "int",
                        "default": 2,
                        "min": 0,
                        "max": 6,
                        "description": "Convergence strategy pivot budget.",
                    },
                    {
                        "key": "congestion_threshold",
                        "type": "float",
                        "default": 10.0,
                        "min": 0.0,
                        "max": 100.0,
                        "description": "Congestion threshold for convergence review.",
                    },
                    {
                        "key": "hierarchical",
                        "type": "enum",
                        "default": "auto",
                        "values": ["auto", "on", "off"],
                        "description": "Hierarchy planner mode.",
                    },
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
            items.append(
                {
                    "id": doc_id,
                    "title": meta.get("title", doc_id),
                    "section": meta.get("section", "General"),
                    "summary": meta.get("summary", ""),
                }
            )
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


@app.post("/chat/converse")
@limiter.limit("20/minute")
async def chat_converse(
    req: ChatRequest, request: Request, profile: dict = Depends(get_current_user)
):
    """Stateless chat conversation with the VLSI Expert Copilot.
    Uses the model configured under your plan (Infinity serverless credits or BYOK).
    """
    header_key = request.headers.get("X-LLM-API-Key")
    if header_key:
        req.api_key = header_key

    is_agentic_paid = req.plan_type == "agentic_paid"

    byok_key = None
    if req.api_key:
        try:
            byok_key = _normalize_byok_config(json.loads(req.api_key))
        except json.JSONDecodeError:
            byok_key = _normalize_byok_config({
                "group1": {"api_key": req.api_key},
                "group2": {"api_key": req.api_key},
                "group3": {"api_key": req.api_key},
            })
    elif not is_agentic_paid:
        byok_key = _normalize_byok_config(get_byok_config_for_user(profile))

    try:
        llm, model_info = _get_llm(byok_config=byok_key, is_agentic_paid=is_agentic_paid)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # Add context about current PDK and options if provided
    pdk_context = ""
    if req.pdk_profile and req.pdk_options:
        available_pdks = [p["key"] for p in req.pdk_options if p.get("gds_ready")]
        pdk_context = (
            f"\nThe user's current selected PDK is '{req.pdk_profile}'. "
            f"The PDKs currently available that can yield a fabrication-ready chip (gds_ready) are: {', '.join(available_pdks)}.\n"
        )

    system_prompt = (
        "You are AgentIC Infinite, a calm, state-of-the-art silicon copilot for autonomous chip creation.\n"
        "You help the user turn plain-English hardware intent into a buildable digital chip specification, "
        "then the AgentIC build pipeline can execute that spec into RTL, testbench, formal collateral, "
        "synthesis/layout artifacts, reports, and GDSII when physical flow is enabled.\n\n"
        "How to answer:\n"
        "- Sound like a premium coding assistant: concise, direct, warm, technically sharp.\n"
        "- Prefer structured Markdown with short sections only when structure helps.\n"
        "- BE VLSI AWARE AND CURIOUS: If the user provides a vague idea about a chip, proactively ask clarifying questions about their intentions (e.g. 'Do you want to fabricate this chip? What are your power/area constraints? What clock speed do you need?').\n"
        "- ASK ABOUT PDK: Make sure you understand which PDK the user wants to target. "
        f"{pdk_context}"
        "- If the user sends a greeting or non-chip question, answer conversationally and guide them toward building a chip.\n"
        "- Explain what is feasible as synthesizable digital RTL and what needs a hard macro, wrapper, or user-supplied asset.\n"
        "- You may propose safe spec substitutions for PDK/tool limits.\n"
        "- Never pretend a build has run from chat alone. Chat refines the spec; the Run pipeline action executes AgentIC.\n"
        "- When the spec is good enough, say it is ready to run and summarize what AgentIC will generate.\n"
    )

    messages_payload = [{"role": "system", "content": system_prompt}]
    for msg in req.messages:
        messages_payload.append({"role": msg.role, "content": msg.content})

    # Direct extraction of LLM properties to prevent attribute errors on wrapper objects
    model_name = "infinity"
    api_key = None
    base_url = None
    api_version = None

    if is_agentic_paid:
        from agentic.config import VERILOG_CODEGEN_CONFIG
        cfg = VERILOG_CODEGEN_CONFIG
        model_name = cfg.get("model", "").strip() or "infinity"
        api_key = cfg.get("api_key", "").strip()
        base_url = cfg.get("base_url", "").strip()
        if "infinity/" in model_name.lower():
            api_version = os.getenv("INFINITY_API_VERSION") or os.getenv("VERILOG_CODEGEN_API_VERSION")
    elif byok_key:
        for group_name in ("group1", "group2", "group3"):
            group = byok_key.get(group_name, {})
            key = group.get("api_key", "").strip()
            if key and key not in ("NA", "mock-key", ""):
                api_key = key
                model_name = group.get("model", "").strip() or model_name
                base_url = group.get("base_url", "").strip() or base_url
                if "infinity/" in model_name.lower():
                    api_version = os.getenv("INFINITY_API_VERSION")
                elif model_name.lower().startswith("azure/"):
                    api_version = os.getenv("AZURE_API_VERSION") or os.getenv("VERILOG_CODEGEN_API_VERSION")
                break

    # Normalize model name for litellm
    model_name = _normalize_model_name(model_name, base_url)

    try:
        from litellm import completion
        
        llm_kwargs = {
            "model": model_name,
            "messages": messages_payload,
            "temperature": 0.5,
        }
        if api_key:
            llm_kwargs["api_key"] = api_key
        if base_url:
            llm_kwargs["base_url"] = base_url
        if api_version:
            llm_kwargs["api_version"] = api_version
            
        response = completion(**llm_kwargs)
        reply = response.choices[0].message.content
        return {"reply": reply, "model": model_name}
    except Exception as e:
        logger.error(f"Chat completion failed: {e}")
        raise HTTPException(status_code=500, detail=f"Copilot logic failed: {e}")

@app.post("/build")
@limiter.limit("5/minute")
async def trigger_build(
    req: BuildRequest, request: Request, profile: dict = Depends(get_current_user)
):
    """Start a new chip build. Returns job_id immediately.

    Routing:
    - plan_type="agentic_paid" → use server's VERILOG_CODEGEN model
    - plan_type="byok" → use user's BYOK keys from request body or Supabase profile
    """
    header_key = request.headers.get("X-LLM-API-Key")
    if header_key:
        req.api_key = header_key

    if not _looks_like_chip_build_request(req.description):
        raise HTTPException(
            status_code=400,
            detail={
                "error": "not_chip_build_request",
                "message": (
                    "This looks like chat or an underspecified chip idea, not a runnable silicon build. "
                    "Ask AgentIC to refine it into a VLSI-aware prompt first, then approve the build."
                ),
                "hint": (
                    "Include the block type, interface, clock/reset, registers or data widths, "
                    "verification expectations, target PDK, and whether GDSII is required."
                ),
            },
        )

    # ── Auth guard: check plan + build count ──
    check_build_allowed(profile)

    if not req.skip_openlane:
        from agentic.config import validate_pdk_installation

        pdk_ok, pdk_messages = validate_pdk_installation(req.pdk_profile)
        if not pdk_ok:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "pdk_not_ready",
                    "message": (
                        f"PDK '{req.pdk_profile}' is not available for GDSII in this workspace."
                    ),
                    "messages": pdk_messages,
                    "pdks": _pdk_catalog_payload(),
                },
            )

    # ── Resolve BYOK key and plan type ──
    byok_key = None
    is_agentic_paid = req.plan_type == "agentic_paid"

    # Priority: req.api_key (JSON or plain string) > Supabase stored BYOK > None
    if req.api_key:
        try:
            byok_key = _normalize_byok_config(json.loads(req.api_key))
        except json.JSONDecodeError:
            byok_key = _normalize_byok_config({
                "group1": {"api_key": req.api_key},
                "group2": {"api_key": req.api_key},
                "group3": {"api_key": req.api_key},
            })
    elif not is_agentic_paid:
        # Fall back to Supabase-stored BYOK for BYOK plan users
        byok_key = _normalize_byok_config(get_byok_config_for_user(profile))

    # ── LLM pre-flight: fail fast with a clear message ──
    try:
        _get_llm(byok_config=byok_key, is_agentic_paid=is_agentic_paid)
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail=str(e),
        )

    # Sanitize design name — Verilog identifiers cannot start with a digit
    import re as _re

    design_name = req.design_name.strip().lower()
    design_name = _re.sub(r"[^a-z0-9_]", "_", design_name)  # keep only safe chars
    design_name = design_name.strip("_")
    design_name = _re.sub(r"_+", "_", design_name)  # collapse doubles
    if design_name and design_name[0].isdigit():
        design_name = "chip_" + design_name  # e.g. chip_8bit_risc_cpu
    if not design_name or ".." in design_name or "/" in design_name:
        raise HTTPException(status_code=400, detail="Invalid design name")

    job_id = str(uuid.uuid4())
    JOB_STORE[job_id] = {
        "status": "queued",
        "user_id": profile.get("id") if profile else None,
        "user_email": profile.get("email") if profile else None,
        "design_name": design_name,
        "description": req.description,
        "current_state": "INIT",
        "events": [],
        "result": {},
        "created_at": int(time.time()),
        "user_profile": profile,
        "byok_key": byok_key,
        "plan_type": req.plan_type,
        "human_in_loop": req.human_in_loop,
        "flow_profile": req.flow_profile or DEFAULT_FLOW_PROFILE.name,
        "stages": {},  # stage_name -> stage_complete payload
        "build_status": "running",
    }
    BUILDS_STARTED_TOTAL.inc()

    req.design_name = design_name

    # Record build start in Supabase
    record_build_start(profile, job_id, design_name)
    _sync_job_to_db(job_id)

    use_celery = os.getenv("RUN_CELERY", "false").lower() == "true"
    if use_celery:
        try:
            from .tasks import run_agentic_build_task

            logger.info(f"Sending job {job_id} to distributed Celery worker queue.")
            # We dump the pydantic model to a dict so Celery/Redis can serialize it
            run_agentic_build_task.apply_async(args=[job_id, req.model_dump()])
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
@limiter.limit("10/minute")
async def elaborate_build(
    req: BuildElaborateRequest, request: Request, user: dict = Depends(get_current_user)
):
    """Inject a user choice into a waiting orchestrator (HITL Elaboration)."""
    job_id = req.job_id
    _ensure_job_access(job_id, user)
    if job_id not in RUNNING_ORCHESTRATORS:
        raise HTTPException(
            status_code=404, detail="Active build not found or not in elaboration state"
        )

    orch = RUNNING_ORCHESTRATORS[job_id]
    orch.artifacts["spec_elaboration_choice"] = req.choice

    return {"status": "success", "message": f"Choice '{req.choice}' injected"}


@app.get("/build/status/{job_id}")
def get_build_status(job_id: str, profile: dict = Depends(get_current_user)):
    """Poll current build status and all events so far."""
    job = _ensure_job_access(job_id, profile)
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
        if (
            orch.artifacts.get("waiting_for_elaboration")
            and "spec_elaboration_options" in orch.artifacts
        ):
            resp["waiting_for_elaboration"] = True
            resp["elaboration_options"] = orch.artifacts["spec_elaboration_options"]

    return resp


@app.get("/build/stream/{job_id}")
async def stream_build_events(
    job_id: str,
    request: Request,
    profile: dict = Depends(get_current_user),
):
    """SSE endpoint — streams live build events as they are emitted."""
    _ensure_job_access(job_id, profile)

    async def event_generator():
        sent_index = 0
        last_event_sent_at = time.time()
        last_ping_sent_at = time.time()
        stall_warned = False
        elaboration_sent = False
        STALL_TIMEOUT = 300  # 5 minutes of silence → stall warning
        HEARTBEAT_INTERVAL = 15
        # Send a ping immediately so the browser knows the connection is alive
        yield 'event: ping\ndata: {"type": "ping", "message": "connected"}\n\n'

        while True:
            if await request.is_disconnected():
                break

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
                        w["design_name"] == job["design_name"]
                        and w["stage"] == event_copy.get("state")
                        for w in waiting_stages
                    )
                    event_copy["is_live_waiting"] = is_live_waiting

                yield f"data: {json.dumps(event_copy)}\n\n"
                sent_index += 1
                last_event_sent_at = time.time()
                stall_warned = False  # new event arrived — reset warning
                last_ping_sent_at = time.time()

            # Stop streaming when done, failed, or cancelled
            if job["status"] in ("done", "failed", "cancelled") and sent_index >= len(
                events
            ):
                yield f"data: {json.dumps({'type': 'stream_end', 'status': job['status']})}\n\n"
                break
                
            # Orphan detection: if stuck in 'cancelling' for ~15 seconds without the worker acknowledging it,
            # assume the worker is dead and force the cancellation.
            if job["status"] == "cancelling":
                if not hasattr(stream_build_events, "cancel_ticks"):
                    stream_build_events.cancel_ticks = {}
                stream_build_events.cancel_ticks[job_id] = stream_build_events.cancel_ticks.get(job_id, 0) + 1
                if stream_build_events.cancel_ticks[job_id] > 30:
                    job["status"] = "cancelled"
                    job["build_status"] = "cancelled"
                    job.setdefault("result", {})["failure_explanation"] = "Build unexpectedly orphaned (Worker crashed or restarted). Forced cancellation."
                    _sync_job_to_db(job_id)
                    yield f"data: {json.dumps({'type': 'stream_end', 'status': 'cancelled'})}\n\n"
                    break

            # If the orchestrator is waiting for elaboration, emit a special status event periodically
            # We track this locally so we don't spam the stream every 0.4s
            if job_id in RUNNING_ORCHESTRATORS:
                orch = RUNNING_ORCHESTRATORS[job_id]
                if (
                    orch.artifacts.get("waiting_for_elaboration")
                    and "spec_elaboration_options" in orch.artifacts
                ):
                    if not elaboration_sent:
                        waiting_event = {
                            "type": "elaboration_waiting",
                            "options": orch.artifacts["spec_elaboration_options"],
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
                last_ping_sent_at = time.time()

            if (time.time() - last_ping_sent_at) >= HEARTBEAT_INTERVAL:
                ping_event = {
                    "type": "ping",
                    "status": job.get("status", "unknown"),
                    "timestamp": int(time.time()),
                }
                yield f"event: ping\ndata: {json.dumps(ping_event)}\n\n"
                last_ping_sent_at = time.time()

            await asyncio.sleep(0.4)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/build/result/{job_id}")
def get_build_result(job_id: str, profile: dict = Depends(get_current_user)):
    """Return the final chip summary after build completes."""
    job = _ensure_job_access(job_id, profile)
    if job["status"] not in ("done", "failed", "cancelled"):
        raise HTTPException(status_code=202, detail="Build still in progress")
    return {"job_id": job_id, "status": job["status"], "result": job["result"]}


@app.get("/jobs")
def list_jobs(profile: dict = Depends(get_current_user)):
    """List persisted jobs for the active workspace or authenticated user."""
    return {"jobs": _load_jobs_for_profile(profile)}


@app.post("/build/cancel/{job_id}")
def cancel_build(job_id: str, profile: dict = Depends(get_current_user)):
    """Request cancellation of a running build.
    Sets a flag that the build thread checks — the thread exits gracefully
    after its current step completes (cannot hard-kill Python threads).
    """
    job = _ensure_job_access(job_id, profile)
    if job["status"] not in ("queued", "running"):
        return {
            "ok": False,
            "message": f"Job already in terminal state: {job['status']}",
        }
    JOB_STORE[job_id]["cancelled"] = True
    JOB_STORE[job_id]["status"] = "cancelling"
    _emit_event(
        job_id,
        "log",
        job["current_state"],
        "🛑 Cancellation requested — stopping after current step…",
        step=0,
    )
    return {"ok": True, "message": "Cancellation requested"}


@app.get("/designs")
def list_designs(profile: dict = Depends(get_current_user)):
    """List persisted designs for the active workspace with GDS availability."""
    jobs = _load_jobs_for_profile(profile)
    design_map: Dict[str, Dict[str, Any]] = {}
    for job in jobs:
        design_name = (job.get("design_name") or "").strip()
        if not design_name:
            continue
        if design_name not in design_map:
            design_map[design_name] = {
                "name": design_name,
                "has_gds": _design_has_gds(design_name),
                "last_build_at": job.get("created_at", 0),
                "build_count": 0,
            }
        design_map[design_name]["build_count"] += 1
        design_map[design_name]["last_build_at"] = max(
            design_map[design_name]["last_build_at"],
            job.get("created_at", 0),
        )

    designs = sorted(
        design_map.values(),
        key=lambda item: (
            item["has_gds"],
            item["last_build_at"],
            item["build_count"],
            item["name"],
        ),
        reverse=True,
    )
    return {"designs": designs}


@app.get("/metrics/{design_name}")
def get_metrics(design_name: str, profile: dict = Depends(get_current_user)):
    """Return latest OpenLane metrics for a design."""
    _ensure_design_access(design_name, profile)
    from agentic.config import OPENLANE_ROOT

    des_dir = os.path.join(OPENLANE_ROOT, "designs", design_name)
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
def get_signoff_report(design_name: str, profile: dict = Depends(get_current_user)):
    _ensure_design_access(design_name, profile)
    try:
        from agentic.tools.vlsi_tools import check_physical_metrics

        metrics, report = check_physical_metrics(design_name)
        return {"success": metrics is not None, "report": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Human-in-the-Loop Approval Endpoints ───────────────────────────


@app.post("/approve")
@limiter.limit("15/minute")
def approve_stage(
    req: ApproveRequest,
    request: Request,
    profile: dict = Depends(get_current_user),
):
    """Approve the current stage and allow the pipeline to proceed."""
    _ensure_design_access(req.design_name, profile)
    ok = approval_manager.approve(req.design_name, req.stage)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"No pending approval for design '{req.design_name}' at stage '{req.stage}'",
        )
    return {
        "ok": True,
        "message": f"Stage '{req.stage}' approved for '{req.design_name}'",
    }


@app.post("/reject")
@limiter.limit("15/minute")
def reject_stage(
    req: RejectRequest,
    request: Request,
    profile: dict = Depends(get_current_user),
):
    """Reject the current stage, optionally providing feedback for retry."""
    _ensure_design_access(req.design_name, profile)
    ok = approval_manager.reject(req.design_name, req.stage, req.feedback)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"No pending approval for design '{req.design_name}' at stage '{req.stage}'",
        )
    return {
        "ok": True,
        "message": f"Stage '{req.stage}' rejected for '{req.design_name}'"
        + (f" with feedback" if req.feedback else ""),
        "will_retry": True,
    }


@app.get("/approval/status")
def get_approval_status(profile: dict = Depends(get_current_user)):
    """List all stages currently waiting for user approval."""
    waiting = approval_manager.get_waiting_stages()
    if AUTH_ENABLED and profile is not None:
        allowed_designs = {
            job.get("design_name")
            for job in _load_jobs_for_profile(profile)
            if job.get("design_name")
        }
        waiting = [item for item in waiting if item.get("design_name") in allowed_designs]
    return {"waiting": waiting, "count": len(waiting)}


@app.get("/build/artifacts/{design_name}")
def get_partial_artifacts(
    design_name: str,
    profile: dict = Depends(get_current_user),
):
    """Scan the design's output directory for any partial artifacts produced during a build.
    Used by the failure summary card to show what was generated before the build failed.
    """
    _ensure_design_access(design_name, profile)
    artifacts = []
    manifest_cloud_urls: Dict[str, str] = {}

    # Check designs/ workspace directory
    workspace_dir = os.path.join(_repo_root(), "designs", design_name)
    manifest_path = os.path.join(workspace_dir, f"{design_name}_artifact_manifest.json")
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as manifest_fp:
                manifest = json.load(manifest_fp)
            for item in manifest.get("artifacts", []):
                if item.get("name"):
                    manifest_cloud_urls[item["name"]] = item.get("cloud_url", "")
        except Exception:
            pass
    if os.path.isdir(workspace_dir):
        for root_dir, _dirs, files in os.walk(workspace_dir):
            for file_name in files:
                fpath = os.path.join(root_dir, file_name)
                size = os.path.getsize(fpath)
                artifacts.append(
                    {
                        "name": file_name,
                        "path": fpath,
                        "size": size,
                        "type": _classify_artifact(file_name),
                        "cloud_url": manifest_cloud_urls.get(file_name, ""),
                    }
                )

    # Check OpenLane designs directory
    from agentic.config import OPENLANE_ROOT

    ol_design_dir = os.path.join(OPENLANE_ROOT, "designs", design_name)
    if os.path.isdir(ol_design_dir):
        for root_dir, _dirs, files in os.walk(ol_design_dir):
            for file_name in files:
                if file_name.endswith(
                    (
                        ".v",
                        ".sv",
                        ".vcd",
                        ".gds",
                        ".def",
                        ".lef",
                        ".spef",
                        ".sdf",
                        ".sdc",
                        ".json",
                        ".tcl",
                        ".sby",
                        ".log",
                        ".csv",
                        ".rpt",
                        ".pdf",
                        ".docx",
                    )
                ):
                    fpath = os.path.join(root_dir, file_name)
                    size = os.path.getsize(fpath)
                    artifacts.append(
                        {
                            "name": file_name,
                            "path": fpath,
                            "size": size,
                            "type": _classify_artifact(file_name),
                            "cloud_url": manifest_cloud_urls.get(file_name, ""),
                        }
                    )

    return {"design_name": design_name, "artifacts": artifacts[:50]}  # Cap at 50


@app.get("/build/artifacts/{design_name}/{filename}")
def download_artifact(
    design_name: str,
    filename: str,
    profile: dict = Depends(get_current_user),
):
    """Download an individual artifact file from a design's output directory."""
    _ensure_design_access(design_name, profile)
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
        ".v": "rtl",
        ".sv": "rtl",
        ".vcd": "waveform",
        ".gds": "layout",
        ".def": "layout",
        ".lef": "layout",
        ".spef": "timing",
        ".sdf": "timing",
        ".sdc": "constraints",
        ".json": "config",
        ".tcl": "script",
        ".sby": "formal",
        ".log": "log",
        ".csv": "report",
        ".rpt": "report",
        ".pdf": "report",
        ".docx": "report",
    }
    return classifications.get(ext, "other")


# ─── Auth & Profile Routes ──────────────────────────────────────────
class SetApiKeyRequest(BaseModel):
    api_key: str


class ByokGroupRequest(BaseModel):
    model: str = ""
    api_key: str = ""
    base_url: str = ""


class SetByokConfigRequest(BaseModel):
    group1: Optional[ByokGroupRequest] = None
    group2: Optional[ByokGroupRequest] = None
    group3: Optional[ByokGroupRequest] = None


class ByokConnectionTestRequest(SetByokConfigRequest):
    group: str = "group2"


def _normalize_byok_config(raw: Optional[dict]) -> dict:
    source = raw if isinstance(raw, dict) else {}
    first_api_key = ""
    first_model = ""
    first_base_url = ""
    if isinstance(raw, str):
        first_api_key = raw.strip()
    for group_name in ("group1", "group2", "group3"):
        group = source.get(group_name)
        if not isinstance(group, dict):
            continue
        first_api_key = first_api_key or str(group.get("api_key", "") or "").strip()
        first_model = first_model or str(group.get("model", "") or "").strip()
        first_base_url = first_base_url or str(group.get("base_url", "") or "").strip()

    first_model = first_model or BYOK_DEFAULT_MODEL
    first_base_url = first_base_url or BYOK_DEFAULT_BASE_URL
    normalized: Dict[str, Dict[str, str]] = {}
    for group_name in ("group1", "group2", "group3"):
        group = source.get(group_name)
        if not isinstance(group, dict):
            group = {}
        normalized[group_name] = {
            "model": str(group.get("model", "") or "").strip() or first_model,
            "api_key": str(group.get("api_key", "") or "").strip() or first_api_key,
            "base_url": str(group.get("base_url", "") or "").strip() or first_base_url,
        }
    return normalized


@app.get("/profile")
async def get_profile(profile: dict = Depends(get_current_user)):
    """Return the authenticated user's profile (plan, build count, etc.)."""
    job_summary = _summarize_jobs(_load_jobs_for_profile(profile))
    if profile is None:
        return {"auth_enabled": False, **job_summary, "has_byok_key": False}
    return {
        "auth_enabled": True,
        "id": profile["id"],
        "email": profile.get("email"),
        "full_name": profile.get("full_name"),
        "plan": profile.get("plan", "free"),
        "successful_builds": profile.get("successful_builds", 0),
        "workspace_successful_builds": job_summary["successful_builds"],
        "total_builds": job_summary["total_builds"],
        "running_builds": job_summary["running_builds"],
        "failed_builds": job_summary["failed_builds"],
        "active_designs": job_summary["active_designs"],
        "has_byok_key": bool(profile.get("llm_api_key")),
    }


@app.get("/profile/byok")
async def get_profile_byok(profile: dict = Depends(get_current_user)):
    """Return normalized BYOK config stored in the authenticated profile."""
    if profile is None:
        raise HTTPException(status_code=403, detail="Auth not enabled")

    byok_config = get_byok_config_for_user(profile)
    if not byok_config:
        return {}

    return _normalize_byok_config(byok_config)


@app.post("/profile/byok")
@limiter.limit("10/minute")
async def set_profile_byok(
    req: SetByokConfigRequest, request: Request, profile: dict = Depends(get_current_user)
):
    """Persist encrypted multi-group BYOK config for cross-device sync."""
    if profile is None:
        raise HTTPException(status_code=403, detail="Auth not enabled")

    payload = _normalize_byok_config(req.model_dump())

    try:
        await save_byok_config_for_user(profile, payload)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {"success": True, "message": "BYOK configuration saved"}


@app.post("/profile/byok/test")
@limiter.limit("10/minute")
async def test_profile_byok(
    req: ByokConnectionTestRequest, request: Request, profile: dict = Depends(get_current_user)
):
    """Validate a BYOK model connection with a tiny provider call."""
    requested_group = req.group if req.group in {"group1", "group2", "group3"} else "group2"
    payload = _normalize_byok_config(req.model_dump(exclude={"group"}))

    group_order = [requested_group, "group2", "group1", "group3"]
    seen: set[str] = set()
    for group_name in group_order:
        if group_name in seen:
            continue
        seen.add(group_name)
        group = payload.get(group_name, {})
        api_key = (group.get("api_key") or "").strip()
        if not _has_real_api_key(api_key):
            continue

        model = _normalize_model_name(group.get("model", ""), group.get("base_url", ""))
        base_url = (group.get("base_url") or "").strip()
        try:
            import litellm

            kwargs: Dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "api_key": api_key,
                "max_tokens": 8,
                "timeout": 20,
            }
            if base_url:
                kwargs["api_base"] = base_url
            if "deepseek" in model.lower():
                kwargs["extra_body"] = {"chat_template_kwargs": {"thinking": True}}

            response = litellm.completion(**kwargs)
            text = ""
            try:
                text = response.choices[0].message.content or ""
            except Exception:
                text = ""
            return {
                "success": True,
                "group": group_name,
                "model": model,
                "base_url": base_url,
                "message": "Model connection verified.",
                "preview": text[:40],
            }
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "model_connection_failed",
                    "message": "Model connection failed. Check the model name, base URL, and API key.",
                    "group": group_name,
                    "model": model,
                    "base_url": base_url,
                    "provider_error": str(exc)[:500],
                },
            ) from exc

    raise HTTPException(
        status_code=400,
        detail={
            "error": "missing_model_key",
            "message": "Add a valid model API key before testing the connection.",
        },
    )


@app.post("/profile/api-key")
@limiter.limit("10/minute")
async def set_byok_key(
    req: SetApiKeyRequest, request: Request, profile: dict = Depends(get_current_user)
):
    """Store an encrypted LLM API key for BYOK plan users."""
    if profile is None:
        raise HTTPException(status_code=403, detail="Auth not enabled")

    from server.auth import _supabase_update

    encrypted = encrypt_api_key(req.api_key)
    await _supabase_update(
        "profiles", f"id=eq.{profile['id']}", {"llm_api_key": encrypted}
    )
    return {"success": True, "message": "API key stored securely"}


# ─── Report Download Endpoints ────────────────────────────────────────
# Single-stage reports (HITL flow) and full-build reports (both flows).


def _get_job_or_404(job_id: str, profile: Optional[dict] = None) -> dict:
    if not re.match(r"^[0-9a-f-]{36}$", job_id):
        raise HTTPException(status_code=400, detail="Invalid job ID")
    return _ensure_job_access(job_id, profile)


@app.get("/report/{job_id}/full.pdf", summary="Download full build report as PDF")
def download_full_report_pdf(
    job_id: str,
    profile: dict = Depends(get_current_user),
):
    job = _get_job_or_404(job_id, profile)
    design_name = job.get("design_name", "design")
    build_status = job.get("build_status", "unknown")
    stages = job.get("stages", {})
    events = job.get("events", [])
    
    all_generated_files = []
    workspace_dir = os.path.join(_repo_root(), "designs", design_name)
    try:
        if os.path.exists(workspace_dir):
            for f in os.listdir(workspace_dir):
                fpath = os.path.join(workspace_dir, f)
                if os.path.isfile(fpath) and not f.endswith('_Build_Report.pdf') and not f.endswith('_Build_Report.docx') and not f.endswith('_artifact_manifest.json'):
                    all_generated_files.append({
                        "name": f,
                        "path": fpath,
                        "size": os.path.getsize(fpath),
                    })
    except Exception:
        pass
    
    pdf_bytes = generate_full_report_pdf(stages, design_name, build_status, events, all_generated_files)
    safe_name = re.sub(r"[^a-z0-9_]", "_", design_name.lower())
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}_full_report.pdf"'
        },
    )


@app.get("/report/{job_id}/full.docx", summary="Download full build report as DOCX")
def download_full_report_docx(
    job_id: str,
    profile: dict = Depends(get_current_user),
):
    job = _get_job_or_404(job_id, profile)
    design_name = job.get("design_name", "design")
    build_status = job.get("build_status", "unknown")
    stages = job.get("stages", {})
    events = job.get("events", [])
    
    all_generated_files = []
    workspace_dir = os.path.join(_repo_root(), "designs", design_name)
    try:
        if os.path.exists(workspace_dir):
            for f in os.listdir(workspace_dir):
                fpath = os.path.join(workspace_dir, f)
                if os.path.isfile(fpath) and not f.endswith('_Build_Report.pdf') and not f.endswith('_Build_Report.docx') and not f.endswith('_artifact_manifest.json'):
                    all_generated_files.append({
                        "name": f,
                        "path": fpath,
                        "size": os.path.getsize(fpath),
                    })
    except Exception:
        pass
    
    docx_bytes = generate_full_report_docx(stages, design_name, build_status, events, all_generated_files)
    safe_name = re.sub(r"[^a-z0-9_]", "_", design_name.lower())
    return StreamingResponse(
        io.BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}_full_report.docx"'
        },
    )


@app.get(
    "/report/{job_id}/stage/{stage_name}.pdf",
    summary="Download a single-stage report as PDF",
)
def download_stage_report_pdf(
    job_id: str,
    stage_name: str,
    profile: dict = Depends(get_current_user),
):
    if not re.match(r"^[A-Z_]{2,30}$", stage_name):
        raise HTTPException(status_code=400, detail="Invalid stage name")
    job = _get_job_or_404(job_id, profile)
    stages = job.get("stages", {})
    if stage_name not in stages:
        raise HTTPException(
            status_code=404, detail=f"Stage '{stage_name}' not found in this job"
        )
    design_name = job.get("design_name", "design")
    pdf_bytes = generate_stage_report_pdf(stages[stage_name], design_name)
    safe_name = re.sub(r"[^a-z0-9_]", "_", design_name.lower())
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}_{stage_name}_report.pdf"'
        },
    )


@app.get(
    "/report/{job_id}/stage/{stage_name}.docx",
    summary="Download a single-stage report as DOCX",
)
def download_stage_report_docx(
    job_id: str,
    stage_name: str,
    profile: dict = Depends(get_current_user),
):
    if not re.match(r"^[A-Z_]{2,30}$", stage_name):
        raise HTTPException(status_code=400, detail="Invalid stage name")
    job = _get_job_or_404(job_id, profile)
    stages = job.get("stages", {})
    if stage_name not in stages:
        raise HTTPException(
            status_code=404, detail=f"Stage '{stage_name}' not found in this job"
        )
    design_name = job.get("design_name", "design")
    docx_bytes = generate_stage_report_docx(stages[stage_name], design_name)
    safe_name = re.sub(r"[^a-z0-9_]", "_", design_name.lower())
    return StreamingResponse(
        io.BytesIO(docx_bytes),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}_{stage_name}_report.docx"'
        },
    )


import os
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Mount the static Vite React app (for HuggingFace Spaces/Docker)
frontend_dist = os.path.join(os.path.dirname(__file__), "..", "web", "dist")
if os.path.exists(frontend_dist):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(frontend_dist, "assets")),
        name="assets",
    )
    app.mount(
        "/vcdrom",
        StaticFiles(directory=os.path.join(frontend_dist, "vcdrom")),
        name="vcdrom",
    )

    @app.get("/{catchall:path}")
    def serve_frontend_app(catchall: str):
        full_path = os.path.join(frontend_dist, catchall)
        if os.path.isfile(full_path):
            return FileResponse(full_path)
        return FileResponse(os.path.join(frontend_dist, "index.html"))
