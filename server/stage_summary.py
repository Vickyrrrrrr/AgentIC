"""
AgentIC Stage Summary Generator
Generates human-readable stage completion summaries using the LLM.
"""
import json
import time
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SRC_PATH = Path(__file__).resolve().parents[1] / "src"
if str(_SRC_PATH) not in sys.path:
    sys.path.insert(0, str(_SRC_PATH))

from agentic.core.flow_capabilities import (
    get_stage_descriptions,
    get_stage_human_names,
    resolve_flow_profile,
)

# Next stage mapping - capability-gated by the selected flow profile.
DEFAULT_FLOW_PROFILE = resolve_flow_profile()
STAGE_FLOW = list(DEFAULT_FLOW_PROFILE.stages)

STAGE_DESCRIPTIONS = get_stage_descriptions()


def get_next_stage(current_stage: str) -> Optional[str]:
    """Get the next stage in the pipeline."""
    return DEFAULT_FLOW_PROFILE.next_stage(current_stage)


ARTIFACT_DESCRIPTIONS = {
    ".v": "Generated Verilog RTL or netlist",
    ".sv": "SystemVerilog source or assertions",
    ".sdc": "Timing constraints for synthesis and STA",
    ".sby": "Formal verification configuration",
    ".vcd": "Simulation waveform",
    ".json": "Structured pipeline data or tool configuration",
    ".tcl": "EDA tool script",
    ".gds": "GDSII physical layout",
    ".def": "DEF physical placement/routing data",
    ".lef": "LEF abstract/layout metadata",
    ".spef": "Parasitic extraction data",
    ".sdf": "Timing annotation data",
    ".rpt": "EDA report",
    ".log": "Tool execution log",
    ".pdf": "Build report PDF",
    ".docx": "Build report document",
}

STAGE_ARTIFACT_SUFFIXES = {
    "SPEC": {".json", ".md", ".txt"},
    "SPEC_VALIDATE": {".json", ".md", ".txt"},
    "HIERARCHY_EXPAND": {".json", ".md", ".txt"},
    "FEASIBILITY_CHECK": {".json", ".rpt", ".log"},
    "VERIFICATION_PLAN": {".json", ".sv", ".sva", ".md", ".txt"},
    "RTL_GEN": {".v", ".sv"},
    "RTL_FIX": {".v", ".sv", ".log"},
    "CDC_ANALYZE": {".json", ".rpt", ".log"},
    "VERIFICATION": {".v", ".sv", ".vcd", ".log", ".txt"},
    "FORMAL_VERIFY": {".sby", ".sv", ".v", ".log", ".txt"},
    "COVERAGE_CHECK": {".json", ".dat", ".info", ".log", ".rpt", ".html"},
    "REGRESSION": {".json", ".log", ".rpt", ".txt"},
    "SDC_GEN": {".sdc", ".tcl", ".log"},
    "SYNTHESIS": {".v", ".json", ".blif", ".rpt", ".log"},
    "GLS_SIMULATION": {".v", ".sv", ".vcd", ".log", ".sdf", ".rpt", ".txt"},
    "FLOORPLAN": {".tcl", ".json", ".def", ".odb", ".log"},
    "HARDENING": {".gds", ".def", ".lef", ".odb", ".spef", ".sdf", ".rpt", ".log"},
    "TIMING_ANALYSIS": {".rpt", ".spef", ".sdf", ".log"},
    "CONVERGENCE_REVIEW": {".json", ".rpt", ".log"},
    "ECO_PATCH": {".v", ".def", ".tcl", ".rpt", ".log"},
    "POWER_ANALYSIS": {".rpt", ".json", ".log"},
    "PHYSICAL_VERIFY": {".rpt", ".log", ".gds", ".def"},
    "POST_LAYOUT_SPICE": {".sp", ".spice", ".raw", ".log", ".tcl", ".json"},
    "SIGNOFF": {".rpt", ".log", ".json", ".gds", ".def", ".lef"},
    "IP_PACKAGE": {".zip", ".tar", ".gz", ".json", ".pdf", ".docx", ".v", ".sv", ".sdc", ".gds"},
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _artifact_description(path: Path) -> str:
    return ARTIFACT_DESCRIPTIONS.get(path.suffix.lower(), "Generated VLSI pipeline artifact")


def _append_file_artifact(artifacts: List[Dict[str, str]], path: Path, description: str = "") -> None:
    try:
        if not path.is_file():
            return
        name = path.name
        if any(existing.get("name") == name for existing in artifacts):
            return
        artifacts.append({
            "name": name,
            "path": str(path),
            "description": description or _artifact_description(path),
        })
    except OSError:
        return


def _materialize_logical_artifact(design_name: str, stage_name: str, key: str, value: Any) -> Optional[Path]:
    if not design_name or value in (None, ""):
        return None
    out_dir = _repo_root() / "designs" / design_name / "_checkpoint_artifacts" / stage_name.lower()
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = ".json" if isinstance(value, (dict, list)) else ".txt"
    if key in {"rtl_code"}:
        ext = ".v"
    elif key in {"spec", "sim_result", "formal_result", "signoff_result"}:
        ext = ".txt"
    filename = f"{stage_name.lower()}_{key}{ext}"
    path = out_dir / filename
    try:
        if isinstance(value, (dict, list)):
            path.write_text(json.dumps(value, indent=2), encoding="utf-8")
        else:
            path.write_text(str(value), encoding="utf-8")
        return path
    except OSError as exc:
        logger.warning("Failed to materialize checkpoint artifact %s: %s", filename, exc)
        return None


def _collect_filesystem_artifacts(design_name: str, stage_name: str, limit: int = 10) -> List[Dict[str, str]]:
    suffixes = STAGE_ARTIFACT_SUFFIXES.get(stage_name, {".v", ".sv", ".json", ".log", ".rpt"})
    roots = [_repo_root() / "designs" / design_name]

    try:
        from agentic.config import OPENLANE_ROOT

        if OPENLANE_ROOT:
            roots.append(Path(OPENLANE_ROOT) / "designs" / design_name)
    except Exception:
        pass

    candidates: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        try:
            for path in root.rglob("*"):
                if path.is_file() and path.suffix.lower() in suffixes:
                    candidates.append(path)
        except OSError:
            continue

    file_infos = []
    for path in candidates:
        try:
            mtime = path.stat().st_mtime
            file_infos.append((path, mtime))
        except OSError:
            continue

    file_infos.sort(key=lambda x: (0 if x[0].suffix.lower() in {".gds", ".v", ".sv", ".sdc", ".sby", ".vcd", ".def"} else 1, -x[1]))
    
    artifacts: List[Dict[str, str]] = []
    for path, _ in file_infos[:limit]:
        _append_file_artifact(artifacts, path)
    return artifacts


def collect_stage_artifacts(orchestrator, stage_name: str, design_name: str = "") -> List[Dict[str, str]]:
    """Collect artifacts produced in a given stage."""
    artifacts = []
    art = orchestrator.artifacts or {}
    
    artifact_map = {
        "INIT": [
            ("root", "Build workspace root directory"),
            ("startup_check", "Startup diagnostics report"),
        ],
        "SPEC": [
            ("sid", "Structured Interface Document (SID JSON)"),
            ("spec", "Detailed RTL generation prompt from SID"),
        ],
        "SPEC_VALIDATE": [
            ("hardware_spec", "Validated hardware specification (JSON)"),
            ("spec_enrichment", "Behavioral contract and verification hints from spec validation"),
        ],
        "HIERARCHY_EXPAND": [
            ("hierarchy_result", "Expanded hierarchy specification (JSON)"),
            ("hierarchy_enrichment", "Hierarchy depth, expansion count, and consistency fixes"),
        ],
        "FEASIBILITY_CHECK": [
            ("feasibility_result", "Physical design feasibility analysis (JSON)"),
            ("feasibility_enrichment", "Feasibility verdict, node contract, readiness level, GE estimate, floorplan recommendation, warnings"),
            ("reconciled_spec", "PDK-feasible reconciled hardware specification (JSON)"),
            ("spec_change_log", "Automatic spec changes made to satisfy PDK/tool constraints"),
        ],
        "CDC_ANALYZE": [
            ("cdc_result", "Clock domain crossing analysis (JSON)"),
            ("cdc_enrichment", "CDC status, domain count, crossing signals, synchronization submodules"),
        ],
        "VERIFICATION_PLAN": [
            ("verification_plan", "Structured verification plan (JSON)"),
            ("verification_enrichment", "Test counts, SVA count, coverage points, warnings"),
        ],
        "RTL_GEN": [
            ("rtl_path", "Generated Verilog RTL file"),
            ("rtl_code", "RTL source code content"),
        ],
        "RTL_FIX": [
            ("rtl_path", "Syntax-fixed Verilog RTL file"),
        ],
        "VERIFICATION": [
            ("tb_path", "Testbench file"),
            ("sim_result", "Simulation result output"),
            ("vcd_path", "Value Change Dump (VCD) waveform"),
        ],
        "FORMAL_VERIFY": [
            ("formal_result", "Formal verification result"),
            ("sby_path", "SymbiYosys configuration file"),
        ],
        "COVERAGE_CHECK": [
            ("coverage", "Coverage analysis results"),
        ],
        "REGRESSION": [
            ("regression_result", "Regression test results"),
        ],
        "SDC_GEN": [
            ("sdc_path", "SDC timing constraints file"),
        ],
        "FLOORPLAN": [
            ("floorplan_tcl", "Floorplan TCL script"),
            ("openlane_config", "OpenLane configuration JSON"),
        ],
        "HARDENING": [
            ("gds_path", "GDSII layout file"),
            ("def_path", "DEF placement file"),
        ],
        "CONVERGENCE_REVIEW": [
            ("convergence_snapshot", "Timing/area/congestion convergence data"),
        ],
        "ECO_PATCH": [
            ("eco_patch", "ECO patch applied"),
        ],
        "SIGNOFF": [
            ("signoff_result", "DRC/LVS/STA signoff report"),
        ],
    }

    stage_artifacts = artifact_map.get(stage_name, [])
    for key, desc in stage_artifacts:
        value = art.get(key)
        if value is not None:
            if isinstance(value, str):
                path = Path(value)
                if not path.is_absolute() and design_name:
                    path = _repo_root() / "designs" / design_name / value
                if path.is_file():
                    _append_file_artifact(artifacts, path, desc)
                    continue
            materialized = _materialize_logical_artifact(design_name, stage_name, key, value)
            if materialized is not None:
                _append_file_artifact(artifacts, materialized, desc)

    if design_name:
        for item in _collect_filesystem_artifacts(design_name, stage_name):
            if not any(existing.get("name") == item["name"] for existing in artifacts):
                artifacts.append(item)
    
    return artifacts[:12]


def collect_stage_decisions(orchestrator, stage_name: str) -> List[str]:
    """Collect decisions made during a stage from build history."""
    decisions = []
    
    # Check strategy pivots
    if orchestrator.pivot_count > 0:
        decisions.append(f"Strategy pivot #{orchestrator.pivot_count} applied (now using {orchestrator.strategy.value})")
    
    # Check retry counts
    retries = orchestrator.state_retry_counts.get(stage_name, 0)
    if retries > 0:
        decisions.append(f"Stage was retried {retries} time(s)")
    
    # Check specific decisions from history
    for entry in orchestrator.build_history:
        if entry.state == stage_name:
            msg = entry.message.lower()
            if "fallback" in msg or "pivot" in msg or "strategy" in msg or "spec change" in msg:
                decisions.append(entry.message[:200])
            elif "gate" in msg and ("pass" in msg or "fail" in msg):
                decisions.append(entry.message[:200])
    
    return decisions[:10]  # Cap at 10


def collect_stage_warnings(orchestrator, stage_name: str) -> List[str]:
    """Collect warnings from a stage."""
    warnings = []
    
    for entry in orchestrator.build_history:
        if entry.state == stage_name:
            msg = entry.message.lower()
            if any(w in msg for w in ["warn", "near-fail", "degraded", "threshold", "exceeded", "timeout", "reconciled"]):
                warnings.append(entry.message[:200])
    
    return warnings[:10]


def get_stage_log_summary(orchestrator, stage_name: str) -> str:
    """Get a condensed log of what happened in a stage."""
    lines = []
    for entry in orchestrator.build_history:
        if entry.state == stage_name:
            lines.append(entry.message)
    return "\n".join(lines[-30:])  # Last 30 log lines


def generate_stage_summary_llm(llm, stage_name: str, design_name: str,
                                stage_log: str, artifacts: List[dict],
                                decisions: List[str], next_stage: Optional[str]) -> dict:
    """Call the LLM to generate a human-readable stage summary.
    
    Returns: {"summary": str, "next_stage_preview": str}
    """
    artifact_list = "\n".join(
        f"- {a['name']}: {a['description']} (path: {a['path'][:100]})"
        for a in artifacts
    ) or "No artifacts produced."
    
    decisions_list = "\n".join(f"- {d}" for d in decisions) or "No autonomous decisions."
    
    next_stage_desc = STAGE_DESCRIPTIONS.get(next_stage, "Unknown") if next_stage else "Build complete."
    
    next_stage_label = next_stage or "N/A"
    next_stage_desc_text = STAGE_DESCRIPTIONS.get(next_stage, "Unknown") if next_stage else "Build complete."
    
    prompt = (
        f"You just completed the {stage_name} stage of an autonomous chip design pipeline "
        f"for the design '{design_name}'.\n\n"
        f"Stage log (last events):\n{stage_log[:2000]}\n\n"
        f"Respond in exactly 2 sentences. No more.\n"
        f"Sentence 1: What just completed in the {stage_name} stage in plain simple language — "
        f"one specific thing that was done.\n"
        f"Sentence 2: What the next stage {next_stage_label} will do.\n\n"
        f"Do not mention artifacts. Do not mention approvals. Do not use phrases like "
        f"'the user should'. Do not pad with filler sentences. Just 2 clean sentences.\n\n"
        f"Respond in this exact JSON format:\n"
        f'{{"summary": "...", "next_stage_preview": "..."}}'
    )
    
    try:
        from crewai import LLM
        result = llm.call(messages=[{"role": "user", "content": prompt}])
        
        # Parse the response — try to extract JSON
        text = str(result) if result else ""
        
        # Try to find JSON in the response
        import re
        json_match = re.search(r'\{[^{}]*"summary"[^{}]*"next_stage_preview"[^{}]*\}', text, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                return parsed
            except json.JSONDecodeError:
                pass
        
        # Fallback: use the text as summary
        return {
            "summary": text[:500] if text else f"Completed {stage_name} stage for {design_name}.",
            "next_stage_preview": f"Next: {next_stage} — {next_stage_desc}" if next_stage else "Build complete."
        }
        
    except Exception as e:
        logger.warning(f"LLM summary generation failed: {e}")
        # Deterministic fallback — keep it to 2 sentences max
        next_desc_short = STAGE_DESCRIPTIONS.get(next_stage, "") if next_stage else ""
        return {
            "summary": (
                f"{stage_name.replace('_', ' ').title()} completed for {design_name}. "
                f"{'Next up: ' + next_stage.replace('_', ' ').title() + '.' if next_stage else 'Build complete.'}"
            ),
            "next_stage_preview": (
                f"{next_desc_short}" if next_stage 
                else "Build complete — all stages finished."
            )
        }


def build_stage_complete_payload(orchestrator, stage_name: str, design_name: str, llm) -> dict:
    """Build the complete stage_complete event payload."""
    artifacts = collect_stage_artifacts(orchestrator, stage_name, design_name)
    decisions = collect_stage_decisions(orchestrator, stage_name)
    warnings = collect_stage_warnings(orchestrator, stage_name)
    stage_log = get_stage_log_summary(orchestrator, stage_name)
    next_stage = get_next_stage(stage_name)
    
    # Generate LLM summary
    llm_result = generate_stage_summary_llm(
        llm=llm,
        stage_name=stage_name,
        design_name=design_name,
        stage_log=stage_log,
        artifacts=artifacts,
        decisions=decisions,
        next_stage=next_stage,
    )
    
    return {
        "type": "stage_complete",
        "stage_name": stage_name,
        "summary": llm_result.get("summary", ""),
        "artifacts": artifacts,
        "decisions": decisions,
        "warnings": warnings,
        "next_stage_name": next_stage or "DONE",
        "next_stage_preview": llm_result.get("next_stage_preview", ""),
        "timestamp": time.time(),
    }


# ─── Human-readable stage name mapping ───────────────────────────────
STAGE_HUMAN_NAMES = get_stage_human_names()


def generate_failure_explanation(llm, stage_name: str, design_name: str,
                                  error_log: str) -> dict:
    """Generate a calm, human-readable explanation of what went wrong.
    
    Returns: {"explanation": str, "suggestion": str}
    """
    human_stage = STAGE_HUMAN_NAMES.get(stage_name, stage_name.replace("_", " ").title())
    
    prompt = (
        f"A chip design build for '{design_name}' stopped at the {human_stage} stage.\n\n"
        f"Error log (last entries):\n{error_log[:2000]}\n\n"
        f"In 1-2 sentences, explain what went wrong in plain language a hardware engineer "
        f"would understand. Do not be alarmist. Be specific about the actual error.\n\n"
        f"Then in one sentence, suggest one specific thing the user could try differently "
        f"in their chip description to avoid this issue.\n\n"
        f"Respond in this exact JSON format:\n"
        f'{{"explanation": "...", "suggestion": "..."}}'
    )
    
    try:
        result = llm.call(messages=[{"role": "user", "content": prompt}])
        text = str(result) if result else ""
        
        import re
        json_match = re.search(r'\{[^{}]*"explanation"[^{}]*"suggestion"[^{}]*\}', text, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                return parsed
            except json.JSONDecodeError:
                pass
        
        return {
            "explanation": text[:300] if text else f"The build stopped during {human_stage}.",
            "suggestion": "Try simplifying the design or checking the error log for details."
        }
        
    except Exception as e:
        logger.warning(f"Failure explanation generation failed: {e}")
        return {
            "explanation": f"The build stopped during {human_stage}. Check the log for specific errors.",
            "suggestion": "Try simplifying the design description or reducing complexity."
        }
