import os
from celery import Celery
import asyncio
from typing import Dict, Any

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
    It will run the Heavy API logic for Orhestration.
    Due to the `_run_agentic_build` function relying on asyncio, 
    we must wrap it nicely.
    """
    from server.api import _run_agentic_build
    from agentic.orchestrator import BuildRequest
    
    # Reconstruct request Pydantic Model from Dictionary
    req = BuildRequest(**request_data)
    
    # We will trigger the actual build pipeline 
    # but the API logic tracks everything safely via DB or events.
    _run_agentic_build(job_id, req)
    
    return {"job_id": job_id, "status": "Finished Execution Sequence"}
