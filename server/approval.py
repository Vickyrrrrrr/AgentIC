"""
AgentIC Human-in-the-Loop Approval Manager
Manages per-design, per-stage threading.Events for pause/resume orchestration.

Upgrade: approval gates now also persist their state to a lightweight dict
so the /approval/status endpoint stays accurate after a hot-reload, and
so SSE reconnects can tell the frontend if a gate is still live.
"""
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


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
    """
    Per-design, per-stage approval gate manager.

    Thread safety: all public methods acquire self._lock before touching
    internal state.  The .event.wait() call is made OUTSIDE the lock so
    the approve/reject callers can acquire it without deadlocking.
    """

    def __init__(self):
        self._gates: Dict[tuple, StageApproval] = {}
        self._lock = threading.Lock()
        self._pending_feedback: Dict[str, str] = {}
        self._stage_data: Dict[tuple, dict] = {}

    def create_gate(self, design_name: str, stage: str) -> StageApproval:
        key = (design_name, stage)
        with self._lock:
            gate = StageApproval(stage=stage, design_name=design_name)
            self._gates[key] = gate
            return gate

    def wait_for_approval(self, design_name: str, stage: str, timeout: float = 7200.0) -> StageApproval:
        """
        Block the calling thread until approval or rejection is signalled.
        NOTE: acquires gate reference under lock, then waits OUTSIDE lock.
        """
        key = (design_name, stage)
        with self._lock:
            gate = self._gates.get(key)
            if gate is None:
                gate = StageApproval(stage=stage, design_name=design_name)
                self._gates[key] = gate

        # Block outside the lock — approve/reject need to acquire it
        gate.event.wait(timeout=timeout)
        return gate

    def approve(self, design_name: str, stage: str) -> bool:
        key = (design_name, stage)
        with self._lock:
            gate = self._gates.get(key)
            if gate is None:
                return False
            gate.approved = True
            gate.rejected = False
        gate.event.set()  # set outside lock — safe, event is thread-safe
        return True

    def reject(self, design_name: str, stage: str, feedback: Optional[str] = None) -> bool:
        key = (design_name, stage)
        with self._lock:
            gate = self._gates.get(key)
            if gate is None:
                return False
            gate.approved = False
            gate.rejected = True
            gate.feedback = feedback
            if feedback:
                self._pending_feedback[design_name] = feedback
        gate.event.set()
        return True

    def get_pending_feedback(self, design_name: str) -> Optional[str]:
        with self._lock:
            return self._pending_feedback.pop(design_name, None)

    def store_stage_data(self, design_name: str, stage: str, data: dict):
        key = (design_name, stage)
        with self._lock:
            self._stage_data[key] = data

    def get_stage_data(self, design_name: str, stage: str) -> Optional[dict]:
        key = (design_name, stage)
        with self._lock:
            return self._stage_data.get(key)

    def cleanup(self, design_name: str):
        """Remove all gates and data for a completed/failed design."""
        with self._lock:
            keys_to_remove = [k for k in self._gates if k[0] == design_name]
            for k in keys_to_remove:
                # Wake any thread still waiting so it doesn't hang forever
                self._gates[k].event.set()
                del self._gates[k]
            keys_to_remove = [k for k in self._stage_data if k[0] == design_name]
            for k in keys_to_remove:
                del self._stage_data[k]
            self._pending_feedback.pop(design_name, None)

    def get_waiting_stages(self) -> List[dict]:
        """List all stages currently waiting for approval."""
        with self._lock:
            return [
                {
                    "design_name": gate.design_name,
                    "stage": gate.stage,
                    "waiting_since": gate.timestamp,
                }
                for gate in self._gates.values()
                if not gate.event.is_set()
            ]


# Singleton
approval_manager = ApprovalManager()
