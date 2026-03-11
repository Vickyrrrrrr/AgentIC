"""
AgentIC Stage Summary Generator
Generates human-readable stage completion summaries using the LLM.
"""
import json
import time
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Next stage mapping
STAGE_FLOW = [
    "INIT", "SPEC", "SPEC_VALIDATE", "HIERARCHY_EXPAND", "FEASIBILITY_CHECK", "CDC_ANALYZE", "VERIFICATION_PLAN", "RTL_GEN", "RTL_FIX", "VERIFICATION",
    "FORMAL_VERIFY", "COVERAGE_CHECK", "REGRESSION",
    "SDC_GEN", "FLOORPLAN", "HARDENING", "CONVERGENCE_REVIEW",
    "ECO_PATCH", "SIGNOFF", "SUCCESS",
]

STAGE_DESCRIPTIONS = {
    "INIT": "Initialize workspace, check tool availability, and prepare build directories",
    "SPEC": "Decompose natural language description into a structured architecture specification (SID JSON)",
    "SPEC_VALIDATE": "Run 6-stage hardware spec validation: classify design, check completeness, decompose modules, define interfaces, generate behavioral contract",
    "HIERARCHY_EXPAND": "Evaluate submodule complexity, recursively expand complex submodules into nested specs, and verify interface consistency across the full hierarchy",
    "FEASIBILITY_CHECK": "Evaluate Sky130/OpenLane physical design feasibility: frequency limits, memory sizing, arithmetic complexity, area budget, and PDK-specific rules",
    "CDC_ANALYZE": "Identify clock domain crossings, assign synchronization strategies (2-flop sync, pulse sync, async FIFO, handshake, reset sync), and generate CDC submodule specifications",
    "RTL_GEN": "Generate Verilog/SystemVerilog RTL code from the architecture specification",
    "RTL_FIX": "Run syntax checks and fix any Verilog syntax errors in the generated RTL",
    "VERIFICATION": "Generate a testbench and run functional simulation to verify RTL correctness",
    "FORMAL_VERIFY": "Write SystemVerilog Assertions and run formal property verification",
    "COVERAGE_CHECK": "Run simulation with coverage instrumentation and analyze line/branch/toggle coverage",
    "REGRESSION": "Run regression tests across multiple scenarios and corner cases",
    "SDC_GEN": "Generate Synopsys Design Constraints (SDC) for timing/clock definitions",
    "FLOORPLAN": "Generate floorplan configuration and run physical placement",
    "HARDENING": "Run OpenLane GDSII hardening flow (synthesis → PnR → signoff)",
    "CONVERGENCE_REVIEW": "Analyze timing/congestion/area convergence and decide on strategy pivots",
    "ECO_PATCH": "Apply Engineering Change Orders to fix post-layout violations",
    "SIGNOFF": "Run DRC, LVS, STA, and power signoff checks",
    "SUCCESS": "Build completed successfully — all quality gates passed",
    "FAIL": "Build failed — see error log for details",
}


def get_next_stage(current_stage: str) -> Optional[str]:
    """Get the next stage in the pipeline."""
    try:
        idx = STAGE_FLOW.index(current_stage)
        if idx + 1 < len(STAGE_FLOW):
            return STAGE_FLOW[idx + 1]
    except ValueError:
        pass
    return None


def collect_stage_artifacts(orchestrator, stage_name: str) -> List[Dict[str, str]]:
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
            ("feasibility_enrichment", "Feasibility verdict, GE estimate, floorplan recommendation, warnings"),
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
            path = value if isinstance(value, str) else json.dumps(value)[:200]
            artifacts.append({
                "name": key,
                "path": path[:500],
                "description": desc,
            })
    
    return artifacts


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
            if "fallback" in msg or "pivot" in msg or "strategy" in msg:
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
            if any(w in msg for w in ["warn", "near-fail", "degraded", "threshold", "exceeded", "timeout"]):
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
    artifacts = collect_stage_artifacts(orchestrator, stage_name)
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
STAGE_HUMAN_NAMES = {
    "INIT": "Initialization",
    "SPEC": "Architecture Specification",
    "SPEC_VALIDATE": "Specification Validation",
    "HIERARCHY_EXPAND": "Hierarchy Expansion",
    "FEASIBILITY_CHECK": "Feasibility Check",
    "CDC_ANALYZE": "CDC Analysis",
    "VERIFICATION_PLAN": "Verification Planning",
    "RTL_GEN": "RTL Generation",
    "RTL_FIX": "RTL Syntax Fixing",
    "VERIFICATION": "Verification",
    "FORMAL_VERIFY": "Formal Verification",
    "COVERAGE_CHECK": "Coverage Analysis",
    "REGRESSION": "Regression Testing",
    "SDC_GEN": "SDC Generation",
    "FLOORPLAN": "Floorplanning",
    "HARDENING": "GDSII Hardening",
    "CONVERGENCE_REVIEW": "Convergence Review",
    "ECO_PATCH": "ECO Patch",
    "SIGNOFF": "Signoff",
    "SUCCESS": "Build Complete",
    "FAIL": "Build Failed",
}


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
