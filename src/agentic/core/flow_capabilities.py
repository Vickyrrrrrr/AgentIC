"""
Capability-gated VLSI flow profiles.

This module is the source of truth for which stages AgentIC may present as
executable for a selected PDK/tool environment. Stages that require commercial
DFT/signoff tooling are still known to the system, but they are not part of the
default Sky130/OpenLane executable path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class StageInfo:
    state: str
    label: str
    icon: str
    description: str
    capability: str = "oss_executable"
    default_executable: bool = True

    def to_schema(self) -> Dict[str, str]:
        return {
            "state": self.state,
            "label": self.label,
            "icon": self.icon,
            "description": self.description,
            "capability": self.capability,
        }


@dataclass(frozen=True)
class FlowProfile:
    name: str
    label: str
    description: str
    stages: List[str]
    readiness_ceiling: str
    optional_stages: List[str] = field(default_factory=list)
    blocked_extensions: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def has_stage(self, stage: str) -> bool:
        return stage in self.stages

    def next_stage(self, current_stage: str) -> Optional[str]:
        if current_stage == "CONVERGENCE_REVIEW" and "ECO_PATCH" in self.stages:
            return "POWER_ANALYSIS"
        if current_stage == "ECO_PATCH":
            return "HARDENING" if "HARDENING" in self.stages else "SIGNOFF"
        try:
            idx = self.stages.index(current_stage)
        except ValueError:
            return None
        if idx + 1 < len(self.stages):
            return self.stages[idx + 1]
        return None

    def to_schema(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "readiness_ceiling": self.readiness_ceiling,
            "optional_stages": list(self.optional_stages),
            "blocked_extensions": list(self.blocked_extensions),
            "notes": list(self.notes),
        }


STAGE_REGISTRY: Dict[str, StageInfo] = {
    "INIT": StageInfo("INIT", "Initializing Workspace", "01", "Initialize workspace, check tool availability, and prepare build directories"),
    "SPEC": StageInfo("SPEC", "Architectural Planning", "02", "Decompose natural language into a structured architecture specification"),
    "SPEC_VALIDATE": StageInfo("SPEC_VALIDATE", "Specification Validation", "03", "Check completeness, interfaces, clocks, resets, and implementation assumptions"),
    "HIERARCHY_EXPAND": StageInfo("HIERARCHY_EXPAND", "Hierarchy Expansion", "04", "Expand complex submodules and verify interface consistency"),
    "FEASIBILITY_CHECK": StageInfo("FEASIBILITY_CHECK", "Feasibility Check", "05", "Check PDK/tool collateral, macro assumptions, area, frequency, and node contract"),
    "VERIFICATION_PLAN": StageInfo("VERIFICATION_PLAN", "Verification Planning", "06", "Define tests, assertions, coverage goals, and signoff evidence"),
    "RTL_GEN": StageInfo("RTL_GEN", "RTL Generation", "07", "Generate synthesizable Verilog/SystemVerilog RTL"),
    "RTL_FIX": StageInfo("RTL_FIX", "RTL Syntax Fixing", "08", "Run parser/lint/elaboration checks and fix RTL issues before verification"),
    "CDC_ANALYZE": StageInfo("CDC_ANALYZE", "CDC Analysis", "09", "Analyze clock-domain crossings and reset release hazards after RTL exists"),
    "VERIFICATION": StageInfo("VERIFICATION", "Verification & Testbench", "10", "Generate and run self-checking functional simulation"),
    "FORMAL_VERIFY": StageInfo("FORMAL_VERIFY", "Formal Verification", "11", "Run formal property checks where supported by the design"),
    "COVERAGE_CHECK": StageInfo("COVERAGE_CHECK", "Coverage Analysis", "12", "Measure coverage and tie low coverage back to requirements, not metric gaming"),
    "REGRESSION": StageInfo("REGRESSION", "Regression Testing", "13", "Re-run scenarios after fixes to guard against regressions"),
    "SDC_GEN": StageInfo("SDC_GEN", "SDC Generation", "14", "Generate timing constraints for synthesis and physical implementation"),
    "SYNTHESIS": StageInfo("SYNTHESIS", "RTL Synthesis", "15", "Map RTL to a gate-level netlist with Yosys/OpenROAD-compatible collateral"),
    "FLOORPLAN": StageInfo("FLOORPLAN", "Floorplanning", "16", "Generate die/core geometry, utilization, placement, and macro assumptions"),
    "HARDENING": StageInfo("HARDENING", "GDSII Hardening", "17", "Run OpenLane/OpenROAD placement, routing, extraction, and GDS generation"),
    "CONVERGENCE_REVIEW": StageInfo("CONVERGENCE_REVIEW", "Convergence Review", "18", "Review timing, congestion, DRC/LVS, area, and power evidence before continuing"),
    "ECO_PATCH": StageInfo("ECO_PATCH", "ECO Patch", "19", "Apply bounded implementation fixes only when convergence evidence requires it"),
    "POWER_ANALYSIS": StageInfo("POWER_ANALYSIS", "Power Analysis", "20", "Estimate dynamic/static power from available activity and extracted collateral"),
    "TIMING_ANALYSIS": StageInfo("TIMING_ANALYSIS", "Static Timing Analysis", "21", "Run OpenSTA timing checks using available libraries and constraints"),
    "PHYSICAL_VERIFY": StageInfo("PHYSICAL_VERIFY", "Physical Verification", "22", "Run Magic DRC and Netgen LVS where layout collateral exists"),
    "SIGNOFF": StageInfo("SIGNOFF", "OSS Signoff Review", "23", "Produce an honest readiness statement and list any commercial signoff blockers"),
    "IP_PACKAGE": StageInfo("IP_PACKAGE", "IP Packaging", "24", "Package RTL, constraints, reports, layout outputs, and readiness documentation"),
    "SUCCESS": StageInfo("SUCCESS", "Build Complete", "25", "Build completed with the selected flow evidence"),
    "FAIL": StageInfo("FAIL", "Build Failed", "XX", "Build failed; inspect logs and artifacts"),
    "DFT_SCAN": StageInfo("DFT_SCAN", "DFT Scan Insertion", "C1", "Commercial scan insertion extension; not provided by the default OSS Sky130 flow", "commercial_dft", False),
    "DFT_ATPG": StageInfo("DFT_ATPG", "DFT ATPG Patterns", "C2", "Commercial ATPG extension; requires Tessent/Modus/TetraMAX-style tooling", "commercial_dft", False),
    "MBIST": StageInfo("MBIST", "Memory BIST", "C3", "Commercial/technology-specific MBIST compiler extension", "commercial_dft", False),
    "GLS_SIMULATION": StageInfo("GLS_SIMULATION", "SDF Gate-Level Simulation", "O1", "Optional gate-level simulation only when netlist, SDF, cell simulation models, and GLS testbench are available", "optional_oss", False),
    "POST_LAYOUT_SPICE": StageInfo("POST_LAYOUT_SPICE", "Scoped Post-Layout SPICE", "O2", "Optional tiny-block or critical-path SPICE; full-chip automatic SPICE is not a default flow stage", "optional_oss", False),
}

SKY130_OSS_STAGES = [
    "INIT",
    "SPEC",
    "SPEC_VALIDATE",
    "HIERARCHY_EXPAND",
    "FEASIBILITY_CHECK",
    "VERIFICATION_PLAN",
    "RTL_GEN",
    "RTL_FIX",
    "CDC_ANALYZE",
    "VERIFICATION",
    "FORMAL_VERIFY",
    "COVERAGE_CHECK",
    "REGRESSION",
    "SDC_GEN",
    "SYNTHESIS",
    "FLOORPLAN",
    "HARDENING",
    "CONVERGENCE_REVIEW",
    "ECO_PATCH",
    "POWER_ANALYSIS",
    "TIMING_ANALYSIS",
    "PHYSICAL_VERIFY",
    "SIGNOFF",
    "IP_PACKAGE",
    "SUCCESS",
]

OPTIONAL_GLS_STAGES = [
    *SKY130_OSS_STAGES[: SKY130_OSS_STAGES.index("FLOORPLAN")],
    "GLS_SIMULATION",
    *SKY130_OSS_STAGES[SKY130_OSS_STAGES.index("FLOORPLAN") :],
]

COMMERCIAL_SIGNOFF_STAGES = [
    *SKY130_OSS_STAGES[: SKY130_OSS_STAGES.index("FLOORPLAN")],
    "DFT_SCAN",
    "DFT_ATPG",
    "MBIST",
    "GLS_SIMULATION",
    *SKY130_OSS_STAGES[SKY130_OSS_STAGES.index("FLOORPLAN") : SKY130_OSS_STAGES.index("SIGNOFF")],
    "POST_LAYOUT_SPICE",
    "SIGNOFF",
    "IP_PACKAGE",
    "SUCCESS",
]

FLOW_PROFILES: Dict[str, FlowProfile] = {
    "sky130_oss_executable": FlowProfile(
        name="sky130_oss_executable",
        label="Sky130 OSS Executable",
        description="Default OpenLane/OpenROAD/Yosys/Magic/Netgen flow. Excludes commercial DFT, ATPG, MBIST, full-chip SPICE, and unproven SDF GLS.",
        stages=SKY130_OSS_STAGES,
        readiness_ceiling="OSS_LAYOUT_CANDIDATE",
        optional_stages=["REGRESSION", "ECO_PATCH"],
        blocked_extensions=["DFT_SCAN", "DFT_ATPG", "MBIST", "GLS_SIMULATION", "POST_LAYOUT_SPICE"],
        notes=[
            "DFT/ATPG/MBIST require commercial or technology-specific tools.",
            "Full-chip post-layout SPICE is not a default automated Sky130 OSS stage.",
            "SDF GLS is only executable when SDF, cell simulation models, and a gate-level testbench are present.",
        ],
    ),
    "oss_with_optional_gls": FlowProfile(
        name="oss_with_optional_gls",
        label="OSS With Proven GLS",
        description="OSS flow with SDF gate-level simulation enabled only when required GLS collateral exists.",
        stages=OPTIONAL_GLS_STAGES,
        readiness_ceiling="OSS_LAYOUT_CANDIDATE",
        optional_stages=["REGRESSION", "ECO_PATCH", "GLS_SIMULATION"],
        blocked_extensions=["DFT_SCAN", "DFT_ATPG", "MBIST", "POST_LAYOUT_SPICE"],
    ),
    "commercial_signoff": FlowProfile(
        name="commercial_signoff",
        label="Commercial Signoff Extension",
        description="Extended flow shape for environments with real DFT/ATPG/MBIST/post-layout signoff adapters configured.",
        stages=COMMERCIAL_SIGNOFF_STAGES,
        readiness_ceiling="COMMERCIAL_SIGNOFF_REQUIRED",
        optional_stages=["REGRESSION", "ECO_PATCH", "GLS_SIMULATION", "POST_LAYOUT_SPICE"],
        blocked_extensions=[],
        notes=["Requires external commercial tool adapters; AgentIC does not claim these are available by default."],
    ),
}


def resolve_flow_profile(profile: str = "", pdk: str = "", tool_config: Optional[Dict[str, object]] = None) -> FlowProfile:
    requested = (profile or os.getenv("AGENTIC_FLOW_PROFILE", "")).strip().lower()
    if not requested:
        requested = "oss_with_optional_gls" if os.getenv("AGENTIC_ENABLE_EXPERIMENTAL_GLS", "").lower() in {"1", "true", "yes", "on"} else "sky130_oss_executable"
    if requested in {"sky130", "sky130_oss", "oss", "default"}:
        requested = "sky130_oss_executable"
    if requested in {"gls", "oss_gls", "optional_gls"}:
        requested = "oss_with_optional_gls"
    if requested in {"commercial", "commercial_dft", "advanced_node_commercial"}:
        requested = "commercial_signoff"

    flow = FLOW_PROFILES.get(requested, FLOW_PROFILES["sky130_oss_executable"])

    if flow.name == "commercial_signoff":
        tool_config = tool_config or {}
        has_commercial = bool(tool_config.get("commercial_signoff")) or os.getenv(
            "AGENTIC_COMMERCIAL_SIGNOFF", ""
        ).lower() in {"1", "true", "yes", "on"}
        if not has_commercial:
            return FLOW_PROFILES["sky130_oss_executable"]
    return flow


def get_stage_info(stage: str) -> StageInfo:
    return STAGE_REGISTRY.get(stage, StageInfo(stage, stage, "•", stage))


def get_stage_descriptions() -> Dict[str, str]:
    return {state: info.description for state, info in STAGE_REGISTRY.items()}


def get_stage_human_names() -> Dict[str, str]:
    return {state: info.label for state, info in STAGE_REGISTRY.items()}


def get_stage_meta() -> Dict[str, Dict[str, str]]:
    return {state: {"label": info.label, "icon": info.icon, "description": info.description, "capability": info.capability} for state, info in STAGE_REGISTRY.items()}
