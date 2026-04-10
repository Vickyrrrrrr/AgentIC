import os
import time
import logging
from celery import Celery
from typing import Dict, Any

logger = logging.getLogger("agentic.tasks")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./agentic_jobs.db")

# Initialize Celery explicitly pointing to Redis for message passing
celery_app = Celery(
    "agentic_tasks",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

# Configure Celery globally
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Make sure we don't block the backend with extremely long VLSI builds endlessly
    task_soft_time_limit=3600,   # 1 hour soft limit
    task_time_limit=3660,        # 1 hour and 1 min hard kill
)


@celery_app.task(bind=True, name="tasks.run_agentic_build")
def run_agentic_build_task(self, job_id: str, request_data: Dict[str, Any]):
    """
    Background Task executed by Celery Workers.
    The Celery worker is a SEPARATE PROCESS from the API server — it has its own
    empty JOB_STORE. We must:
      1. Bootstrap a JOB_STORE entry for this job (or pull it from DB if available).
      2. Inject the byok_key from request_data so _run_agentic_build can find it.
      3. Always mark job as failed in DB if any exception occurs (including
         permission errors) so the job never gets stuck as 'running'.
    """
    import json
    from server.api import JOB_STORE, _pull_job_from_db, _sync_job_to_db
    from server.api import BuildRequest as APIBuildRequest

    # Reconstruct Pydantic model from the serialised dict Celery received
    req = APIBuildRequest(**request_data)

    # ── 1. Ensure this worker process has a JOB_STORE entry ──────────────────
    # Try to pull the existing record that the API server wrote to the DB.
    _pull_job_from_db(job_id)

    if job_id not in JOB_STORE:
        # Fallback: create a minimal stub so _run_agentic_build won't KeyError
        JOB_STORE[job_id] = {
            "status": "queued",
            "design_name": req.design_name,
            "description": req.description,
            "current_state": "INIT",
            "events": [],
            "result": {},
            "created_at": int(time.time()),
            "human_in_loop": bool(req.human_in_loop),
            "stages": {},
            "build_status": "running",
            "cancelled": False,
        }

    # ── 2. Inject byok_key so _run_agentic_build can route LLM calls ─────────
    if req.api_key and not JOB_STORE[job_id].get("byok_key"):
        try:
            byok_key = json.loads(req.api_key)
        except (json.JSONDecodeError, TypeError):
            byok_key = {
                "group1": {"api_key": req.api_key},
                "group2": {"api_key": req.api_key},
                "group3": {"api_key": req.api_key},
            }
        JOB_STORE[job_id]["byok_key"] = byok_key

    # Persist the bootstrapped entry so the API server can stream events from DB
    _sync_job_to_db(job_id)

    # ── 3. Run the actual build pipeline ─────────────────────────────────────
    # Wrapped in try/except/finally so ANY crash (including Errno 13 permission
    # denied on /app/designs) is caught and the job is always marked as failed
    # in the DB. This prevents ghost 'running' jobs in the Jobs & History page.
    try:
        from server.api import _run_agentic_build
        _run_agentic_build(job_id, req)
    except Exception as exc:
        import traceback
        err_trace = traceback.format_exc()
        logger.error(
            "[task:%s] Unhandled exception in _run_agentic_build: %s\n%s",
            job_id, exc, err_trace
        )
        # Always mark as failed so the job is never stuck as 'running'
        if job_id in JOB_STORE:
            JOB_STORE[job_id]["status"] = "failed"
            JOB_STORE[job_id]["build_status"] = "failed"
            JOB_STORE[job_id].setdefault("result", {})["error"] = str(exc)
            JOB_STORE[job_id].setdefault("result", {})["traceback"] = err_trace
        _sync_job_to_db(job_id)
        # Re-raise so Celery also marks the task as FAILURE in its backend
        raise
    finally:
        # Always do a final DB sync regardless of success or failure
        # so the API server sees the terminal state immediately
        try:
            _sync_job_to_db(job_id)
        except Exception as sync_err:
            logger.warning("[task:%s] Final DB sync failed: %s", job_id, sync_err)

    return {"job_id": job_id, "status": "Finished Execution Sequence"}
