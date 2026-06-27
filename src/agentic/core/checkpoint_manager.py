"""
Checkpoint Manager
================

Manages build state checkpoints for recovery from failures.
Features:
- Automatic checkpoint creation at key milestones
- State serialization and deserialization
- Recovery from checkpoints after failures
- Checkpoint pruning (keep last N)

Usage:
    checkpoint = CheckpointManager("my_design")

    # Save checkpoint
    checkpoint.save(orchestrator, reason="after_rtl_fix")

    # Load checkpoint
    state = checkpoint.load_latest()
"""

import os
import json
import shutil
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict, field
from pathlib import Path
from glob import glob

logger = logging.getLogger(__name__)


@dataclass
class CheckpointMetadata:
    """Metadata for a checkpoint."""

    timestamp: str
    reason: str
    state: str
    global_step: int
    pivot_count: int
    retry_count: int
    rtl_length: int
    coverage_pct: float
    file_path: str = ""


@dataclass
class BuildState:
    """Serialized build state for recovery."""

    timestamp: str
    reason: str

    # Core state
    design_name: str
    state_name: str
    state_value: int

    # Counters
    global_step_count: int
    global_retry_count: int
    pivot_count: int
    state_retry_counts: Dict[str, int]

    # Strategy
    strategy: str
    max_pivots: int
    max_retries: int

    # Key artifacts
    rtl_code: str = ""
    rtl_path: str = ""
    tb_path: str = ""
    spec: str = ""

    # Coverage
    coverage: Dict[str, float] = field(default_factory=dict)

    # Convergence
    convergence_history: List[Dict] = field(default_factory=list)

    # Errors
    last_error: str = ""
    error_history: List[str] = field(default_factory=list)

    # Files created
    generated_files: List[str] = field(default_factory=list)

    # Failure fingerprint history (CRITICAL for detecting repeated failures)
    failure_fingerprint_history: Dict[str, int] = field(default_factory=dict)
    failed_code_by_fingerprint: Dict[str, str] = field(default_factory=dict)

    # Custom metadata
    metadata: Dict[str, Any] = field(default_factory=dict)


class CheckpointManager:
    """
    Manages build state checkpoints for recovery.

    Features:
    - Saves complete state at key points
    - Loads and restores state
    - Automatic pruning (keeps last N checkpoints)
    - Checkpoint compression
    """

    def __init__(
        self,
        design_name: str,
        checkpoint_dir: Optional[str] = None,
        max_checkpoints: int = 20,
        compress: bool = False,
    ):
        """
        Initialize checkpoint manager.

        Args:
            design_name: Name of the design being built
            checkpoint_dir: Directory for checkpoints (auto-created)
            max_checkpoints: Maximum checkpoints to keep
            compress: Whether to compress checkpoints
        """
        self.design_name = design_name

        if checkpoint_dir:
            self.checkpoint_dir = Path(checkpoint_dir)
        else:
            # Default to workspace/checkpoints/design_name
            from ..config import WORKSPACE_ROOT

            self.checkpoint_dir = Path(WORKSPACE_ROOT) / "checkpoints" / design_name

        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.max_checkpoints = max_checkpoints
        self.compress = compress

        logger.info(f"CheckpointManager initialized at {self.checkpoint_dir}")

    def save(
        self,
        orchestrator,
        reason: str = "",
        force: bool = False,
    ) -> Optional[str]:
        """
        Save a checkpoint from orchestrator state.

        Args:
            orchestrator: The orchestrator instance
            reason: Why this checkpoint is being saved
            force: Force save even if recent

        Returns:
            Path to saved checkpoint file
        """
        try:
            state = self._extract_state(orchestrator, reason)
            return self._save_checkpoint(state)
        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")
            return None

    def _extract_state(self, orchestrator, reason: str) -> BuildState:
        """Extract serializable state from orchestrator."""
        state = BuildState(
            timestamp=datetime.now().isoformat(),
            reason=reason,
            design_name=orchestrator.name,
            state_name=orchestrator.state.name,
            state_value=orchestrator.state.value,
            global_step_count=orchestrator.global_step_count,
            global_retry_count=orchestrator.global_retry_count,
            pivot_count=orchestrator.pivot_count,
            state_retry_counts=dict(orchestrator.state_retry_counts or {}),
            strategy=orchestrator.strategy.value
            if hasattr(orchestrator.strategy, "value")
            else str(orchestrator.strategy),
            max_pivots=orchestrator.max_pivots,
            max_retries=orchestrator.max_retries,
            rtl_code=orchestrator.artifacts.get("rtl_code", ""),
            rtl_path=orchestrator.artifacts.get("rtl_path", ""),
            tb_path=orchestrator.artifacts.get("tb_path", ""),
            spec=orchestrator.artifacts.get("spec", ""),
            coverage=orchestrator.artifacts.get("coverage", {}),
            convergence_history=[asdict(s) for s in (orchestrator.convergence_history or [])[-10:]],
            last_error=orchestrator.artifacts.get("last_error", ""),
            error_history=list(getattr(orchestrator, "errors", []) or [])[-20:],
            generated_files=list(getattr(orchestrator, "generated_files", []) or [])[-50:],
            failure_fingerprint_history=dict(
                getattr(orchestrator, "failure_fingerprint_history", {}) or {}
            ),
            failed_code_by_fingerprint=dict(
                getattr(orchestrator, "failed_code_by_fingerprint", {}) or {}
            ),
        )

        return state

    def _save_checkpoint(self, state: BuildState) -> str:
        """Save state to checkpoint file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"checkpoint_{timestamp}.json"
        filepath = self.checkpoint_dir / filename

        with open(filepath, "w") as f:
            json.dump(asdict(state), f, indent=2)

        # Update latest
        latest_path = self.checkpoint_dir / "latest.json"
        shutil.copy(filepath, latest_path)

        # Create metadata
        metadata = CheckpointMetadata(
            timestamp=state.timestamp,
            reason=state.reason,
            state=state.state_name,
            global_step=state.global_step_count,
            pivot_count=state.pivot_count,
            retry_count=state.global_retry_count,
            rtl_length=len(state.rtl_code),
            coverage_pct=state.coverage.get("line_pct", 0.0),
            file_path=str(filepath),
        )

        # Save metadata index
        self._update_metadata_index(metadata)

        # Prune old checkpoints
        self._prune_checkpoints()

        logger.info(f"Checkpoint saved: {filepath}")

        return str(filepath)

    def _update_metadata_index(self, metadata: CheckpointMetadata):
        """Update the metadata index file."""
        index_path = self.checkpoint_dir / "metadata_index.json"

        # Load existing
        if index_path.exists():
            with open(index_path) as f:
                index = json.load(f)
        else:
            index = []

        # Add new metadata
        index.append(asdict(metadata))

        # Keep last 100
        index = index[-100:]

        with open(index_path, "w") as f:
            json.dump(index, f, indent=2)

    def load_latest(self) -> Optional[BuildState]:
        """Load the most recent checkpoint."""
        latest_path = self.checkpoint_dir / "latest.json"

        if not latest_path.exists():
            return None

        try:
            with open(latest_path) as f:
                data = json.load(f)

            return BuildState(**data)
        except Exception as e:
            logger.error(f"Failed to load checkpoint: {e}")
            return None

    def load(self, timestamp: str) -> Optional[BuildState]:
        """Load a specific checkpoint by timestamp."""
        pattern = f"checkpoint_{timestamp}*.json"
        matches = glob(str(self.checkpoint_dir / pattern))

        if not matches:
            return None

        try:
            with open(matches[0]) as f:
                data = json.load(f)

            return BuildState(**data)
        except Exception as e:
            logger.error(f"Failed to load checkpoint {timestamp}: {e}")
            return None

    def list_checkpoints(self) -> List[CheckpointMetadata]:
        """List all available checkpoints."""
        index_path = self.checkpoint_dir / "metadata_index.json"

        if not index_path.exists():
            return []

        try:
            with open(index_path) as f:
                data = json.load(f)

            return [CheckpointMetadata(**m) for m in data[-self.max_checkpoints :]]
        except Exception as e:
            logger.error(f"Failed to list checkpoints: {e}")
            return []

    def get_best_checkpoint(self, criteria: str = "coverage") -> Optional[BuildState]:
        """
        Get the best checkpoint by criteria.

        Args:
            criteria: 'coverage', 'step' (least steps), 'latest'
        """
        checkpoints = self.list_checkpoints()

        if not checkpoints:
            return None

        if criteria == "coverage":
            best = max(checkpoints, key=lambda c: c.coverage_pct)
        elif criteria == "step":
            best = min(checkpoints, key=lambda c: c.global_step)
        else:  # latest
            best = checkpoints[-1]

        return self.load(best.timestamp[:15])  # Match by date pattern

    def restore(self, checkpoint: BuildState, orchestrator) -> bool:
        """
        Restore orchestrator state from checkpoint.

        Args:
            checkpoint: The checkpoint to restore
            orchestrator: The orchestrator instance

        Returns:
            True if successful
        """
        try:
            # Restore state
            orchestrator.state = type(orchestrator.state)(checkpoint.state_value)

            # Restore counters
            orchestrator.global_step_count = checkpoint.global_step_count
            orchestrator.global_retry_count = checkpoint.global_retry_count
            orchestrator.pivot_count = checkpoint.pivot_count
            orchestrator.state_retry_counts = checkpoint.state_retry_counts

            # CRITICAL: Restore strategy — without this, a post-pivot checkpoint
            # resumes with the wrong strategy (default SV_MODULAR instead of VERILOG_CLASSIC)
            from ..orchestrator import BuildStrategy
            if checkpoint.strategy:
                for bs in BuildStrategy:
                    if bs.value == checkpoint.strategy:
                        orchestrator.strategy = bs
                        break

            if checkpoint.max_pivots:
                orchestrator.max_pivots = checkpoint.max_pivots
            if checkpoint.max_retries:
                orchestrator.max_retries = checkpoint.max_retries

            # Restore artifacts
            if checkpoint.rtl_code:
                orchestrator.artifacts["rtl_code"] = checkpoint.rtl_code
            if checkpoint.rtl_path:
                orchestrator.artifacts["rtl_path"] = checkpoint.rtl_path
            if checkpoint.tb_path:
                orchestrator.artifacts["tb_path"] = checkpoint.tb_path
            if checkpoint.spec:
                orchestrator.artifacts["spec"] = checkpoint.spec
            if checkpoint.coverage:
                orchestrator.artifacts["coverage"] = checkpoint.coverage
            if checkpoint.last_error:
                orchestrator.artifacts["last_error"] = checkpoint.last_error

            # Restore convergence history (needed for floorplan iteration decisions)
            if checkpoint.convergence_history:
                orchestrator.convergence_history = list(checkpoint.convergence_history)

            # Restore error history (for LLM context on what went wrong before)
            if checkpoint.error_history:
                orchestrator.errors = list(checkpoint.error_history)

            # CRITICAL: Restore failure fingerprint history.
            # Without this, the build cannot detect repeated failures after resume
            # and will retry the same failing approach indefinitely.
            if checkpoint.failure_fingerprint_history:
                orchestrator.failure_fingerprint_history = dict(
                    checkpoint.failure_fingerprint_history
                )
            if checkpoint.failed_code_by_fingerprint:
                orchestrator.failed_code_by_fingerprint = dict(
                    checkpoint.failed_code_by_fingerprint
                )

            logger.info(
                f"Restored checkpoint from {checkpoint.timestamp} | "
                f"Stage: {checkpoint.state_name} | Strategy: {checkpoint.strategy}"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to restore checkpoint: {e}")
            return False

    def _prune_checkpoints(self):
        """Remove old checkpoints beyond max_checkpoints."""
        checkpoints = sorted(self.checkpoint_dir.glob("checkpoint_*.json"), key=os.path.getmtime)

        # Keep all but oldest if over limit
        to_remove = (
            checkpoints[: -self.max_checkpoints] if len(checkpoints) > self.max_checkpoints else []
        )

        for old in to_remove:
            if old.name != "latest.json":
                old.unlink()
                logger.debug(f"Pruned checkpoint: {old}")

    def prune_by_reason(self, reason: str) -> int:
        """Remove checkpoints by reason."""
        removed = 0
        for cp in self.list_checkpoints():
            if reason in cp.reason:
                path = self.checkpoint_dir / f"checkpoint_{cp.timestamp[:15]}*.json"
                for p in glob(str(path)):
                    if p != str(self.checkpoint_dir / "latest.json"):
                        Path(p).unlink()
                        removed += 1
        return removed

    def clear(self) -> int:
        """Clear all checkpoints."""
        count = 0
        for f in self.checkpoint_dir.glob("checkpoint_*.json"):
            if f.name != "latest.json":
                f.unlink()
                count += 1

        latest = self.checkpoint_dir / "latest.json"
        if latest.exists():
            latest.unlink()

        logger.info(f"Cleared {count} checkpoints")
        return count

    def get_stats(self) -> Dict[str, Any]:
        """Get checkpoint statistics."""
        checkpoints = self.list_checkpoints()

        if not checkpoints:
            return {
                "total": 0,
                "by_reason": {},
                "oldest": None,
                "newest": None,
            }

        # Count by reason
        by_reason = {}
        for cp in checkpoints:
            by_reason[cp.reason] = by_reason.get(cp.reason, 0) + 1

        return {
            "total": len(checkpoints),
            "by_reason": by_reason,
            "oldest": checkpoints[0].timestamp if checkpoints else None,
            "newest": checkpoints[-1].timestamp if checkpoints else None,
            "max_coverage": max(cp.coverage_pct for cp in checkpoints) if checkpoints else 0,
            "directory": str(self.checkpoint_dir),
        }


class AutomaticCheckpointTrigger:
    """
    Triggers automatic checkpoints at key milestones.
    """

    TRIGGERS = {
        "periodic": 300,  # Every 5 minutes
        "after_rtl_gen": True,
        "after_verification": True,
        "after_synth": True,
        "after_error": True,
        "before_pivot": True,
    }

    def __init__(
        self, checkpoint_manager: CheckpointManager, triggers: Optional[Dict[str, Any]] = None
    ):
        self.manager = checkpoint_manager
        self.triggers = triggers or self.TRIGGERS.copy()
        self.last_checkpoint_time = 0.0

    def should_checkpoint(
        self,
        trigger: str,
        orchestrator,
    ) -> bool:
        """Check if checkpoint should be triggered."""
        if trigger not in self.triggers:
            return False

        if trigger == "periodic":
            import time

            if time.time() - self.last_checkpoint_time >= self.triggers[trigger]:
                self.last_checkpoint_time = time.time()
                return True
            return False

        if isinstance(self.triggers[trigger], bool):
            return self.triggers[trigger]

        return bool(self.triggers[trigger])

    def checkpoint_if_needed(
        self, trigger: str, orchestrator, force: bool = False
    ) -> Optional[str]:
        """Checkpoint if trigger condition met."""
        if force or self.should_checkpoint(trigger, orchestrator):
            return self.manager.save(orchestrator, reason=trigger)
        return None


def create_checkpoint_manager(design_name: str, **kwargs) -> CheckpointManager:
    """Factory function for creating checkpoint manager."""
    return CheckpointManager(design_name, **kwargs)
