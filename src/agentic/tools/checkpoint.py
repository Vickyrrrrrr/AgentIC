"""
Checkpoint & Resume - Build State Persistence
============================================
Production tapeout flows can take hours. This module provides checkpoint/resume
so interrupted builds can continue from where they left off rather than restart.

Checkpoint includes:
- Current build state
- All generated artifacts (RTL, TB, netlist, GDS)
- Stage retry counts
- Convergence history
- Error fingerprints

Usage:
    from agentic.tools.checkpoint import CheckpointManager

    cm = CheckpointManager("design_name", checkpoint_dir="./checkpoints")
    cm.save(orchestrator)          # Save checkpoint
    restored = cm.load()            # Load checkpoint
    cm.resume(restored)             # Continue from checkpoint
"""

import json
import os
import pickle
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class StageCheckpoint:
    """Checkpoint data for a single stage."""

    stage_name: str
    started_at: str
    completed_at: Optional[str] = None
    ok: bool = False
    retry_count: int = 0
    artifacts: Dict[str, str] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BuildCheckpoint:
    """Complete build checkpoint."""

    design_name: str
    design_desc: str
    created_at: str
    updated_at: str
    pdk: str
    current_state: str
    completed_stages: List[str]
    stage_data: Dict[str, StageCheckpoint]
    retry_counts: Dict[str, int]
    fingerprint_history: List[str]
    convergence_history: List[Dict[str, Any]]
    artifacts: Dict[str, Any]
    rtl_path: Optional[str] = None
    tb_path: Optional[str] = None
    netlist_path: Optional[str] = None
    gds_path: Optional[str] = None
    synth_path: Optional[str] = None
    scan_netlist_path: Optional[str] = None
    coverage_report: Optional[str] = None
    sta_report: Optional[str] = None
    power_report: Optional[str] = None
    drc_report: Optional[str] = None
    lvs_report: Optional[str] = None
    spec_data: Optional[str] = None


class CheckpointManager:
    """Manages build checkpoints for resume capability."""

    def __init__(self, design_name: str, checkpoint_dir: str = "./checkpoints"):
        self.design_name = design_name
        self.checkpoint_dir = os.path.abspath(checkpoint_dir)
        self.ckpt_file = os.path.join(self.checkpoint_dir, f"{design_name}_ckpt.json")
        self.ckpt_binary = os.path.join(self.checkpoint_dir, f"{design_name}_ckpt.bin")
        self.artifacts_dir = os.path.join(
            self.checkpoint_dir, f"{design_name}_artifacts"
        )

    def save(
        self,
        orchestrator: Any,
        include_artifacts: bool = True,
    ) -> Tuple[bool, str]:
        """Save current build state as checkpoint.

        Args:
            orchestrator: BuildOrchestrator instance
            include_artifacts: Copy RTL/TB/netlist files to checkpoint dir

        Returns: (ok, checkpoint_path)
        """
        os.makedirs(self.checkpoint_dir, exist_ok=True)

        stage_data: Dict[str, StageCheckpoint] = {}
        for stage_name, stage_ckpt in getattr(
            orchestrator, "_stage_checkpoints", {}
        ).items():
            stage_data[stage_name] = StageCheckpoint(
                stage_name=stage_ckpt.get("stage_name", stage_name),
                started_at=stage_ckpt.get("started_at", ""),
                completed_at=stage_ckpt.get("completed_at"),
                ok=stage_ckpt.get("ok", False),
                retry_count=stage_ckpt.get("retry_count", 0),
                errors=stage_ckpt.get("errors", []),
                warnings=stage_ckpt.get("warnings", []),
                metrics=stage_ckpt.get("metrics", {}),
            )

        ckpt = BuildCheckpoint(
            design_name=orchestrator.name,
            design_desc=orchestrator.desc,
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
            pdk=getattr(orchestrator, "pdk_profile", "sky130"),
            current_state=orchestrator.state.name
            if hasattr(orchestrator, "state")
            else "UNKNOWN",
            completed_stages=getattr(orchestrator, "_completed_stages", []),
            stage_data=stage_data,
            retry_counts=dict(getattr(orchestrator, "state_retry_counts", {})),
            fingerprint_history=list(
                getattr(orchestrator, "_failure_fingerprints", [])
            ),
            convergence_history=list(getattr(orchestrator, "convergence_history", [])),
            artifacts=dict(orchestrator.artifacts),
        )

        if include_artifacts:
            self._save_artifacts(orchestrator, ckpt)

        ckpt_dict = {
            "design_name": ckpt.design_name,
            "design_desc": ckpt.design_desc,
            "created_at": ckpt.created_at,
            "updated_at": ckpt.updated_at,
            "pdk": ckpt.pdk,
            "current_state": ckpt.current_state,
            "completed_stages": ckpt.completed_stages,
            "stage_data": {
                k: {
                    "stage_name": v.stage_name,
                    "started_at": v.started_at,
                    "completed_at": v.completed_at,
                    "ok": v.ok,
                    "retry_count": v.retry_count,
                    "errors": v.errors,
                    "warnings": v.warnings,
                    "metrics": v.metrics,
                }
                for k, v in ckpt.stage_data.items()
            },
            "retry_counts": ckpt.retry_counts,
            "fingerprint_history": ckpt.fingerprint_history,
            "convergence_history": ckpt.convergence_history,
            "artifacts": ckpt.artifacts,
            "rtl_path": ckpt.rtl_path,
            "tb_path": ckpt.tb_path,
            "netlist_path": ckpt.netlist_path,
            "gds_path": ckpt.gds_path,
            "synth_path": ckpt.synth_path,
            "scan_netlist_path": ckpt.scan_netlist_path,
            "coverage_report": ckpt.coverage_report,
            "sta_report": ckpt.sta_report,
            "power_report": ckpt.power_report,
            "drc_report": ckpt.drc_report,
            "lvs_report": ckpt.lvs_report,
            "spec_data": ckpt.spec_data,
        }

        with open(self.ckpt_file, "w") as f:
            json.dump(ckpt_dict, f, indent=2)

        try:
            with open(self.ckpt_binary, "wb") as f:
                pickle.dump(orchestrator, f)
        except (pickle.PicklingError, OSError):
            pass

        return True, self.ckpt_file

    def _save_artifacts(self, orchestrator: Any, ckpt: BuildCheckpoint) -> None:
        """Copy build artifacts to checkpoint directory."""
        os.makedirs(self.artifacts_dir, exist_ok=True)

        artifact_map = {
            "rtl_path": "design.v",
            "tb_path": "design_tb.v",
            "netlist_path": "design_synth.v",
            "gds_path": "design.gds",
            "synth_path": "design_synth.v",
            "scan_netlist_path": "design_scan.v",
        }

        artifacts = getattr(orchestrator, "artifacts", {})
        for artifact_key, filename in artifact_map.items():
            src = artifacts.get(artifact_key)
            if src and os.path.exists(src):
                dst = os.path.join(self.artifacts_dir, filename)
                try:
                    shutil.copy2(src, dst)
                    setattr(ckpt, artifact_key, dst)
                except OSError:
                    pass

        spec_data = artifacts.get("spec_json")
        if spec_data and os.path.exists(spec_data):
            shutil.copy2(spec_data, os.path.join(self.artifacts_dir, "spec.json"))
            ckpt.spec_data = os.path.join(self.artifacts_dir, "spec.json")

    def load(self) -> Optional[BuildCheckpoint]:
        """Load build checkpoint from disk.

        Returns: BuildCheckpoint or None if no checkpoint exists
        """
        if not os.path.exists(self.ckpt_file):
            return None

        try:
            with open(self.ckpt_file) as f:
                data = json.load(f)

            stage_data: Dict[str, StageCheckpoint] = {}
            for k, v in data.get("stage_data", {}).items():
                stage_data[k] = StageCheckpoint(**v)

            return BuildCheckpoint(
                design_name=data["design_name"],
                design_desc=data["design_desc"],
                created_at=data["created_at"],
                updated_at=data["updated_at"],
                pdk=data["pdk"],
                current_state=data["current_state"],
                completed_stages=data["completed_stages"],
                stage_data=stage_data,
                retry_counts=data["retry_counts"],
                fingerprint_history=data["fingerprint_history"],
                convergence_history=data["convergence_history"],
                artifacts=data["artifacts"],
                rtl_path=data.get("rtl_path"),
                tb_path=data.get("tb_path"),
                netlist_path=data.get("netlist_path"),
                gds_path=data.get("gds_path"),
                synth_path=data.get("synth_path"),
                scan_netlist_path=data.get("scan_netlist_path"),
                coverage_report=data.get("coverage_report"),
                sta_report=data.get("sta_report"),
                power_report=data.get("power_report"),
                drc_report=data.get("drc_report"),
                lvs_report=data.get("lvs_report"),
                spec_data=data.get("spec_data"),
            )
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def load_orchestrator(self) -> Optional[Any]:
        """Load full orchestrator from binary checkpoint.

        Returns: BuildOrchestrator instance or None
        """
        if not os.path.exists(self.ckpt_binary):
            return None
        try:
            with open(self.ckpt_binary, "rb") as f:
                return pickle.load(f)
        except (pickle.UnpicklingError, OSError):
            return None

    def resume(
        self,
        from_stage: Optional[str] = None,
    ) -> Tuple[bool, str, BuildCheckpoint]:
        """Resume a build from checkpoint.

        Args:
            from_stage: Optional stage to resume from (default: current state)

        Returns: (ok, message, checkpoint)
        """
        ckpt = self.load()
        if ckpt is None:
            return False, f"No checkpoint found for {self.design_name}", None

        msg = f"Checkpoint found for {self.design_name}"
        msg += f"\n  Created: {ckpt.created_at}"
        msg += f"\n  Last state: {ckpt.current_state}"
        msg += f"\n  Completed stages: {len(ckpt.completed_stages)}"
        msg += f"\n  PDK: {ckpt.pdk}"

        if from_stage:
            msg += f"\n  Resuming from: {from_stage}"
        else:
            msg += f"\n  Resuming from: {ckpt.current_state}"

        return True, msg, ckpt

    def list_checkpoints(
        self, checkpoint_dir: str = "./checkpoints"
    ) -> List[Dict[str, Any]]:
        """List all available checkpoints in a directory.

        Returns: List of checkpoint metadata
        """
        checkpoints: List[Dict[str, Any]] = []
        if not os.path.exists(checkpoint_dir):
            return checkpoints

        for fname in os.listdir(checkpoint_dir):
            if fname.endswith("_ckpt.json"):
                fpath = os.path.join(checkpoint_dir, fname)
                try:
                    with open(fpath) as f:
                        data = json.load(f)
                    checkpoints.append(
                        {
                            "design": data.get("design_name", "unknown"),
                            "state": data.get("current_state", "unknown"),
                            "created": data.get("created_at", ""),
                            "updated": data.get("updated_at", ""),
                            "completed_stages": len(data.get("completed_stages", [])),
                            "pdk": data.get("pdk", ""),
                            "path": fpath,
                        }
                    )
                except (json.JSONDecodeError, OSError):
                    pass

        return sorted(checkpoints, key=lambda x: x["updated"], reverse=True)

    def delete(self) -> Tuple[bool, str]:
        """Delete checkpoint and artifacts.

        Returns: (ok, message)
        """
        deleted = []
        for path in [self.ckpt_file, self.ckpt_binary]:
            if os.path.exists(path):
                os.remove(path)
                deleted.append(path)
        if os.path.exists(self.artifacts_dir):
            shutil.rmtree(self.artifacts_dir)
            deleted.append(self.artifacts_dir)

        if deleted:
            return True, f"Deleted: {', '.join(deleted)}"
        return False, "No checkpoint to delete"

    def get_next_stage(self) -> Optional[str]:
        """Determine next stage to run based on checkpoint state.

        Returns: Next stage name or None if build is complete/failed
        """
        ckpt = self.load()
        if ckpt is None:
            return None

        COMPLETE_STATES = {"SUCCESS", "FAIL"}
        if ckpt.current_state in COMPLETE_STATES:
            return None

        ALL_STAGES = [
            "INIT",
            "SPEC",
            "ARCHITECT",
            "FEASIBILITY_CHECK",
            "CDC_ANALYZE",
            "VERIFICATION_PLAN",
            "RTL_GEN",
            "RTL_FIX",
            "VERIFICATION",
            "SIMULATION",
            "FORMAL_VERIFY",
            "DFT",
            "SYNTHESIS",
            "OPENLANE_FLOW",
            "SELF_REFLECT",
            "DEEP_DEBUG",
            "FINAL_VERIFY",
            "POWER_ANALYSIS",
            "TIMING_ANALYSIS",
            "PHYSICAL_VERIFY",
            "SIGNOFF",
        ]

        try:
            current_idx = ALL_STAGES.index(ckpt.current_state)
        except ValueError:
            return None

        for i in range(current_idx + 1, len(ALL_STAGES)):
            if ALL_STAGES[i] not in ckpt.completed_stages:
                return ALL_STAGES[i]

        return None


def checkpoint_tool(
    design_name: str,
    action: str = "save",
    checkpoint_dir: str = "./checkpoints",
) -> Tuple[bool, str]:
    """CrewAI tool wrapper for checkpoint management.

    Usage:
        checkpoint_tool("counter", "save")   # Save checkpoint
        checkpoint_tool("counter", "load")  # Load checkpoint info
        checkpoint_tool("counter", "list")  # List all checkpoints

    Returns: (ok, message)
    """
    cm = CheckpointManager(design_name, checkpoint_dir)

    if action == "save":
        return False, "Checkpoint save requires orchestrator reference"
    elif action == "load":
        ok, msg, ckpt = cm.resume()
        if ckpt:
            msg += f"\n  RTL: {ckpt.rtl_path or 'N/A'}"
            msg += f"\n  GDS: {ckpt.gds_path or 'N/A'}"
        return ok, msg
    elif action == "list":
        checkpoints = cm.list_checkpoints(checkpoint_dir)
        if not checkpoints:
            return True, "No checkpoints found"
        msg = f"Found {len(checkpoints)} checkpoint(s):\n"
        for c in checkpoints:
            msg += f"  [{c['design']}] state={c['state']} updated={c['updated'][:19]}\n"
        return True, msg
    elif action == "delete":
        ok, msg = cm.delete()
        return ok, msg
    else:
        return False, f"Unknown action: {action}"
