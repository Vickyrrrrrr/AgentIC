"""
AgentIC Human-in-the-Loop Approval Manager
Manages per-design, per-stage asyncio Events for pause/resume orchestration.
"""
import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class StageApproval:
    """Tracks the approval state for a single stage in a single design build."""
    stage: str
    design_name: str
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False
    rejected: bool = False
    feedback: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


class ApprovalManager:
    """Per-design, per-stage approval gate manager.
    
    Uses threading.Events since the orchestrator runs in a background thread
    (not asyncio). The web API (FastAPI/async) signals the events from
    async handlers via thread-safe Event.set().
    """

    def __init__(self):
        # Key: (design_name, stage_name) → StageApproval
        self._gates: Dict[tuple, StageApproval] = {}
        self._lock = threading.Lock()
        # Stores user feedback for next retry, keyed by design_name
        self._pending_feedback: Dict[str, str] = {}
        # Stage completion data keyed by (design_name, stage_name)
        self._stage_data: Dict[tuple, dict] = {}

    def create_gate(self, design_name: str, stage: str) -> StageApproval:
        """Create an approval gate for a specific design+stage."""
        key = (design_name, stage)
        with self._lock:
            gate = StageApproval(stage=stage, design_name=design_name)
            self._gates[key] = gate
            return gate

    def wait_for_approval(self, design_name: str, stage: str, timeout: float = 7200.0) -> StageApproval:
        """Block the calling thread until approval or rejection is signalled.
        
        This is called from the build thread (not async). It blocks until
        the FastAPI async handler calls approve() or reject().
        
        Args:
            design_name: The design being built
            stage: The current stage name
            timeout: Max seconds to wait (default 2 hours)
            
        Returns:
            The StageApproval with approved/rejected/feedback set
        """
        key = (design_name, stage)
        with self._lock:
            gate = self._gates.get(key)
            if gate is None:
                gate = self.create_gate(design_name, stage)

        # Block until event is set (from approve/reject endpoint)
        gate.event.wait(timeout=timeout)
        return gate

    def approve(self, design_name: str, stage: str) -> bool:
        """Signal approval for a waiting stage. Called from async endpoint."""
        key = (design_name, stage)
        with self._lock:
            gate = self._gates.get(key)
            if gate is None:
                return False
            gate.approved = True
            gate.rejected = False
            gate.event.set()
            return True

    def reject(self, design_name: str, stage: str, feedback: Optional[str] = None) -> bool:
        """Signal rejection for a waiting stage. Called from async endpoint."""
        key = (design_name, stage)
        with self._lock:
            gate = self._gates.get(key)
            if gate is None:
                return False
            gate.approved = False
            gate.rejected = True
            gate.feedback = feedback
            gate.event.set()
            # Store feedback for the stage retry
            if feedback:
                self._pending_feedback[design_name] = feedback
            return True

    def get_pending_feedback(self, design_name: str) -> Optional[str]:
        """Retrieve and clear pending user feedback for a design."""
        with self._lock:
            return self._pending_feedback.pop(design_name, None)

    def store_stage_data(self, design_name: str, stage: str, data: dict):
        """Store stage completion data for retrieval by frontend."""
        key = (design_name, stage)
        with self._lock:
            self._stage_data[key] = data

    def get_stage_data(self, design_name: str, stage: str) -> Optional[dict]:
        """Retrieve stage completion data."""
        key = (design_name, stage)
        with self._lock:
            return self._stage_data.get(key)

    def cleanup(self, design_name: str):
        """Remove all gates for a completed/failed design."""
        with self._lock:
            keys_to_remove = [k for k in self._gates if k[0] == design_name]
            for k in keys_to_remove:
                del self._gates[k]
            keys_to_remove = [k for k in self._stage_data if k[0] == design_name]
            for k in keys_to_remove:
                del self._stage_data[k]
            self._pending_feedback.pop(design_name, None)

    def get_waiting_stages(self) -> List[dict]:
        """List all stages currently waiting for approval."""
        with self._lock:
            waiting = []
            for key, gate in self._gates.items():
                if not gate.event.is_set():
                    waiting.append({
                        "design_name": gate.design_name,
                        "stage": gate.stage,
                        "waiting_since": gate.timestamp,
                    })
            return waiting


# Singleton instance
approval_manager = ApprovalManager()
