"""
Feasibility Checker — Phase 3 of the Spec Pipeline
===================================================

Receives a fully expanded hierarchical hardware specification and evaluates
whether the design is physically realizable on the selected PDK within the
OpenLane RTL-to-GDS flow — before a single line of RTL is written.

Pipeline Steps:
  1. FREQUENCY  — Clock target vs. PDK achievable limits
  2. MEMORY     — Storage structures vs. register / OpenRAM thresholds
  3. ARITHMETIC — Multiplier / divider / FPU gate-cost per PDK
  4. AREA       — Total gate-equivalent budget and floorplan sizing
  5. PDK_CHECK  — PDK-specific incompatibility scan
  6. OUTPUT     — Annotated spec with feasibility verdict
"""

import json
import logging
import math
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import get_pdk_flow_capabilities, get_pdk_tool_config
from .flow_capabilities import resolve_flow_profile

logger = logging.getLogger(__name__)


# ─── Area Estimation Constants (Gate Equivalents) ────────────────────
# 1 GE = one 2-input NAND gate on Sky130

GE_ESTIMATES: Dict[str, int] = {
    # Registers
    "1_bit_ff": 6,
    "8_bit_register": 48,
    "16_bit_register": 96,
    "32_bit_register": 192,
    # Register files
    "32x32_regfile": 6144,
    "32x64_regfile": 12288,
    "16x32_regfile": 3072,
    # Arithmetic
    "8_bit_adder": 40,
    "16_bit_adder": 80,
    "32_bit_adder": 160,
    "8x8_multiplier": 200,
    "16x16_multiplier": 1000,
    "32x32_multiplier": 4000,
    # Logic
    "4_state_fsm": 100,
    "8_state_fsm": 250,
    "16_state_fsm": 600,
    # Interfaces
    "uart_115200": 500,
    "spi_master": 800,
    "i2c_master": 1200,
    "apb_slave": 600,
    # Processors
    "riscv_5stage_no_cache": 20000,
}

# Floorplan size mapping (GE → recommended area)
FLOORPLAN_TIERS = [
    (5_000, "TinyTapeout tile (130×160 μm)", "130x160"),
    (50_000, "Chipignite medium (500×500 μm)", "500x500"),
    (200_000, "Chipignite large (1000×1000 μm)", "1000x1000"),
    (500_000, "Multi-tile (2000×2000 μm)", "2000x2000"),
]


# ─── Submodule-type GE heuristic keywords ───────────────────────────

_GE_KEYWORD_MAP: List[Tuple[List[str], int, str]] = [
    # (keywords, base_ge, description)
    (["riscv", "risc-v", "rv32", "rv64", "processor", "cpu"], 20000, "RISC-V / CPU core"),
    (["uart"], 500, "UART controller"),
    (["spi"], 800, "SPI controller"),
    (["i2c"], 1200, "I2C controller"),
    (["apb", "axi", "wishbone"], 600, "Bus interface"),
    (["alu"], 500, "ALU"),
    (["multiplier", "multiply"], 1000, "Multiplier"),
    (["divider", "divide"], 1500, "Divider"),
    (["fpu", "floating point"], 5000, "Floating-point unit"),
    (["register_file", "regfile", "register file"], 6144, "Register file"),
    (["fifo"], 400, "FIFO buffer"),
    (["cache"], 8000, "Cache"),
    (["dma"], 3000, "DMA controller"),
    (["arbiter", "arbitration"], 300, "Arbiter"),
    (["interrupt", "irq"], 400, "Interrupt controller"),
    (["program_counter", "pc"], 200, "Program counter"),
    (["instruction_fetch", "fetch"], 800, "Instruction fetch"),
    (["instruction_decode", "decode"], 1000, "Instruction decode"),
    (["writeback"], 400, "Writeback stage"),
    (["hazard"], 500, "Hazard unit"),
    (["branch_predict"], 1500, "Branch predictor"),
    (["pipeline_register", "pipe_reg"], 200, "Pipeline register"),
    (["control_unit", "control_logic"], 300, "Control unit"),
    (["state_machine", "fsm"], 100, "State machine"),
    (["shift_register", "barrel_shifter"], 200, "Shift register / barrel shifter"),
    (["counter"], 100, "Counter"),
    (["comparator"], 50, "Comparator"),
    (["mux", "multiplexer"], 30, "Multiplexer"),
    (["adder"], 160, "Adder"),
    (["memory_array", "sram", "ram", "rom"], 2000, "Memory array"),
    (["address_decoder", "decoder"], 100, "Address decoder"),
    (["output_register"], 48, "Output register"),
    (["data_buffer", "buffer"], 200, "Data buffer"),
    (["status_register"], 48, "Status register"),
    (["clock_divider"], 80, "Clock divider"),
]


# ─── Output Dataclasses ─────────────────────────────────────────────


@dataclass
class MacroRequirement:
    """OpenRAM macro specification for large memories."""

    submodule_name: str
    width_bits: int
    depth_words: int
    read_ports: int = 1
    write_ports: int = 1
    size_bits: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FeasibilityIssue:
    """Structured issue emitted by the feasibility checker."""

    category: str  # AUTO_REPAIRABLE | REQUIRES_MACRO | REQUIRES_USER_ASSET | UNSUPPORTED
    code: str
    message: str
    target: str = ""
    suggested_action: str = ""
    severity: str = "warning"  # info | warning | error

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SignoffRequirement:
    """One node-specific requirement that must have evidence before tapeout."""

    key: str
    description: str
    required: bool = True
    evidence_type: str = "report"
    tool_family: str = ""
    blocking: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NodeContract:
    """PDK/node contract used to keep autonomous loops signoff-governed."""

    node: str
    pdk: str
    std_cell_library: str = ""
    node_class: str = "generic"
    flow_status: str = "requires_pdk_collateral"
    fabrication_ready: bool = False
    collateral_ready: bool = False
    proprietary: bool = False
    custom: bool = False
    max_reliable_mhz: int = 150
    upper_limit_mhz: int = 200
    voltage_vdd: str = "1.8"
    memory_macro_threshold_bytes: int = 1024
    flow_profile: str = "sky130_oss_executable"
    readiness_ceiling: str = "OSS_LAYOUT_CANDIDATE"
    blocked_extensions: List[str] = field(default_factory=list)
    required_signoff: List[SignoffRequirement] = field(default_factory=list)
    missing_capabilities: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["required_signoff"] = [r.to_dict() for r in self.required_signoff]
        return data


@dataclass
class FeasibilityResult:
    """Output of the FeasibilityChecker."""

    feasibility_status: str  # "PASS" | "WARN" | "REJECT"
    estimated_gate_equivalents: int = 0
    recommended_floorplan_size_um: str = ""
    target_frequency_mhz: int = 50
    recommended_frequency_mhz: int = 50  # Auto-adjusted frequency if needed
    frequency_was_adjusted: bool = False
    memory_macros_required: List[MacroRequirement] = field(default_factory=list)
    feasibility_warnings: List[str] = field(default_factory=list)
    feasibility_rejections: List[str] = field(default_factory=list)
    feasibility_issues: List[FeasibilityIssue] = field(default_factory=list)
    node_contract: Optional[NodeContract] = None
    readiness_level: str = "RTL_CANDIDATE"
    signoff_blockers: List[str] = field(default_factory=list)
    # Detailed breakdown
    area_breakdown: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feasibility_status": self.feasibility_status,
            "estimated_gate_equivalents": self.estimated_gate_equivalents,
            "recommended_floorplan_size_um": self.recommended_floorplan_size_um,
            "target_frequency_mhz": self.target_frequency_mhz,
            "recommended_frequency_mhz": self.recommended_frequency_mhz,
            "frequency_was_adjusted": self.frequency_was_adjusted,
            "memory_macros_required": [m.to_dict() for m in self.memory_macros_required],
            "feasibility_warnings": list(self.feasibility_warnings),
            "feasibility_rejections": list(self.feasibility_rejections),
            "feasibility_issues": [i.to_dict() for i in self.feasibility_issues],
            "node_contract": self.node_contract.to_dict() if self.node_contract else {},
            "readiness_level": self.readiness_level,
            "signoff_blockers": list(self.signoff_blockers),
            "area_breakdown": dict(self.area_breakdown),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# ─── Main Class ──────────────────────────────────────────────────────


class FeasibilityChecker:
    """
    Evaluates whether a hardware specification is physically realizable
    on a given PDK within the OpenLane automated flow.

    Checks: frequency, memory sizing, arithmetic complexity, total area,
    and PDK-specific incompatibilities.  Produces a PASS / WARN / REJECT
    verdict with detailed justification.

    Supports auto-adjust mode: adjusts frequency to PDK limits instead of rejecting.
    """

    def __init__(self, pdk: str = "sky130", auto_adjust: bool = True):
        self.pdk = pdk
        self.auto_adjust = auto_adjust
        self.node_contract = self._build_node_contract()

    # ── Public API ───────────────────────────────────────────────────

    def check(
        self,
        hw_spec_dict: Dict[str, Any],
        hierarchy_result_dict: Optional[Dict[str, Any]] = None,
    ) -> FeasibilityResult:
        """
        Run all feasibility checks against the spec.

        Args:
            hw_spec_dict: HardwareSpec.to_dict() output.
            hierarchy_result_dict: Optional HierarchyResult.to_dict() for
                expanded submodule analysis.

        Returns:
            FeasibilityResult with verdict and details.
        """
        warnings: List[str] = []
        rejections: List[str] = []
        issues: List[FeasibilityIssue] = []
        area_breakdown: Dict[str, int] = {}
        frequency_was_adjusted = False
        node_contract = self.node_contract
        signoff_blockers = list(node_contract.missing_capabilities)
        if not node_contract.collateral_ready:
            signoff_blockers.append(
                f"PDK collateral for {node_contract.node} is not fully validated."
            )
        if node_contract.proprietary:
            signoff_blockers.append(
                "Authorized foundry/commercial signoff collateral must be confirmed."
            )

        # Resolve target frequency
        target_freq = hw_spec_dict.get("target_frequency_mhz", 0)
        if not target_freq or target_freq <= 0:
            target_freq = 50
            warnings.append(
                "INFERRED: target_frequency_mhz was 0 or unspecified — defaulting to 50 MHz."
            )

        # Get PDK frequency limits
        tool_config = get_pdk_tool_config(self.pdk)
        max_reliable_mhz = tool_config.get("max_reliable_mhz", 150)
        upper_limit_mhz = tool_config.get("upper_limit_mhz", 200)

        # Auto-adjust frequency if needed
        if target_freq > upper_limit_mhz:
            if self.auto_adjust:
                original_freq = target_freq
                target_freq = max_reliable_mhz
                frequency_was_adjusted = True
                warnings.append(
                    f"AUTO_ADJUSTED: Target frequency {original_freq} MHz exceeds {self.pdk} limit "
                    f"of {upper_limit_mhz} MHz. Adjusted to {target_freq} MHz (max reliable)."
                )
                issues.append(
                    FeasibilityIssue(
                        category="AUTO_REPAIRABLE",
                        code="FREQUENCY_ADJUSTED",
                        target="target_frequency_mhz",
                        message=(
                            f"Target frequency {original_freq} MHz exceeds the configured "
                            f"{self.pdk} upper limit of {upper_limit_mhz} MHz."
                        ),
                        suggested_action=f"Use {target_freq} MHz for this PDK profile.",
                    )
                )
            else:
                msg = (
                    f"FEASIBILITY_REJECTED: {self.pdk} cannot reliably achieve "
                    f"{target_freq} MHz. Maximum reliable: {max_reliable_mhz} MHz."
                )
                rejections.append(msg)
                issues.append(
                    FeasibilityIssue(
                        category="AUTO_REPAIRABLE",
                        code="FREQUENCY_TOO_HIGH",
                        target="target_frequency_mhz",
                        message=msg,
                        suggested_action=f"Lower target_frequency_mhz to {max_reliable_mhz}.",
                        severity="error",
                    )
                )

        # Collect all submodule specs (top-level + nested from hierarchy)
        all_submodules = self._collect_all_submodules(hw_spec_dict, hierarchy_result_dict)
        all_ports = hw_spec_dict.get("ports", [])
        all_contracts = hw_spec_dict.get("behavioral_contract", [])
        design_category = hw_spec_dict.get("design_category", "CONTROL")
        design_desc = hw_spec_dict.get("design_description", "")

        # Step 1: Frequency feasibility
        freq_warnings, freq_rejections = self._check_frequency(
            target_freq, all_submodules, design_category
        )
        warnings.extend(freq_warnings)
        rejections.extend(freq_rejections)

        # Step 2: Memory feasibility
        mem_warnings, mem_rejections, macros, mem_issues = self._check_memory(all_submodules)
        warnings.extend(mem_warnings)
        rejections.extend(mem_rejections)
        issues.extend(mem_issues)

        # Step 3: Arithmetic feasibility
        arith_warnings = self._check_arithmetic(all_submodules, all_contracts, design_desc)
        warnings.extend(arith_warnings)

        # Step 4: Area estimation
        total_ge, area_breakdown = self._estimate_area(all_submodules)
        area_warnings = self._check_area_budget(total_ge)
        warnings.extend(area_warnings)

        # Step 5: PDK-specific / ASIC-flow rules
        pdk_warnings, pdk_rejections, pdk_issues = self._check_pdk_rules(
            all_ports, all_submodules, all_contracts, design_desc, hw_spec_dict
        )
        warnings.extend(pdk_warnings)
        rejections.extend(pdk_rejections)
        issues.extend(pdk_issues)

        # Determine floorplan recommendation
        floorplan = self._recommend_floorplan(total_ge)

        # Determine overall status
        if rejections:
            status = "REJECT"
        elif warnings:
            status = "WARN"
        else:
            status = "PASS"

        readiness_level = self._infer_readiness_level(status, node_contract, signoff_blockers)

        return FeasibilityResult(
            feasibility_status=status,
            estimated_gate_equivalents=total_ge,
            recommended_floorplan_size_um=floorplan,
            target_frequency_mhz=target_freq,
            recommended_frequency_mhz=target_freq,
            frequency_was_adjusted=frequency_was_adjusted,
            memory_macros_required=macros,
            feasibility_warnings=warnings,
            feasibility_rejections=rejections,
            feasibility_issues=issues,
            node_contract=node_contract,
            readiness_level=readiness_level,
            signoff_blockers=signoff_blockers,
            area_breakdown=area_breakdown,
        )

    # ── Node Contract / Signoff Governance ───────────────────────────

    def _build_node_contract(self) -> NodeContract:
        """Build a conservative signoff contract from PDK/tool capabilities.

        A real foundry node should provide this explicitly. If no manifest is
        available, AgentIC derives a minimum digital-signoff contract and marks
        the collateral state honestly.
        """
        caps = get_pdk_flow_capabilities(self.pdk)
        tool = get_pdk_tool_config(self.pdk)
        manifest = self._load_node_contract_manifest(caps)

        requirements = self._default_signoff_requirements(caps)
        notes: List[str] = []
        missing: List[str] = []

        if manifest:
            notes.append("Loaded external node contract manifest.")
            requirements = self._requirements_from_manifest(manifest, requirements)
            missing.extend(str(x) for x in manifest.get("missing_capabilities", []) if x)

        if not caps.get("collateral_ready"):
            missing.append("pdk_collateral_ready")
        if not caps.get("fabrication_ready"):
            notes.append(
                "PDK is not marked fabrication-ready; treat output as a tapeout candidate only."
            )
        if caps.get("flow_status") in {
            "requires_authorized_foundry_collateral",
            "requires_custom_pdk_validation",
            "requires_pdk_collateral",
        }:
            notes.append(f"Flow status: {caps.get('flow_status')}")

        if caps.get("advanced_node"):
            notes.append(
                "Advanced node: require tighter MMMC, EM/IR, DFM, reliability, and power-intent evidence."
            )

        node = str(manifest.get("node") if manifest else caps.get("profile") or self.pdk)
        pdk = str(manifest.get("pdk") if manifest else caps.get("pdk") or self.pdk)
        flow = resolve_flow_profile(pdk=self.pdk, tool_config=tool)
        if flow.blocked_extensions:
            notes.append(
                "Capability-gated extensions are not part of the default executable flow: "
                + ", ".join(flow.blocked_extensions)
            )

        return NodeContract(
            node=node,
            pdk=pdk,
            std_cell_library=str(
                manifest.get("std_cell_library")
                if manifest
                else caps.get("std_cell_library") or tool.get("std_cell_library", "")
            ),
            node_class=str(caps.get("node_class", "generic")),
            flow_status=str(manifest.get("flow_status") if manifest else caps.get("flow_status")),
            fabrication_ready=bool(
                manifest.get("fabrication_ready")
                if manifest
                else caps.get("fabrication_ready", False)
            ),
            collateral_ready=bool(
                manifest.get("collateral_ready")
                if manifest
                else caps.get("collateral_ready", False)
            ),
            proprietary=bool(caps.get("proprietary", False)),
            custom=bool(caps.get("custom", False)),
            max_reliable_mhz=int(tool.get("max_reliable_mhz", 150) or 150),
            upper_limit_mhz=int(tool.get("upper_limit_mhz", 200) or 200),
            voltage_vdd=str(tool.get("voltage_vdd", "1.8")),
            memory_macro_threshold_bytes=int(
                caps.get("memory_macro_threshold_bytes", 1024) or 1024
            ),
            flow_profile=flow.name,
            readiness_ceiling=flow.readiness_ceiling,
            blocked_extensions=list(flow.blocked_extensions),
            required_signoff=requirements,
            missing_capabilities=sorted(set(missing)),
            notes=notes,
        )

    def _load_node_contract_manifest(self, caps: Dict[str, Any]) -> Dict[str, Any]:
        """Load optional JSON node contract from env or PDK directory."""
        candidates: List[str] = []
        explicit = os.getenv("AGENTIC_NODE_CONTRACT", "").strip()
        if explicit:
            candidates.append(os.path.expanduser(explicit))

        tool = caps.get("tool_config", {}) if isinstance(caps, dict) else {}
        pdk_dir = str(tool.get("pdk_dir", "") or caps.get("pdk", "") or "")
        pdk_root = os.getenv("PDK_ROOT", "").strip()
        if pdk_root and pdk_dir:
            base = os.path.join(pdk_root, pdk_dir)
            candidates.extend(
                [
                    os.path.join(base, "agentic_node_contract.json"),
                    os.path.join(base, "node_contract.json"),
                ]
            )

        for path in candidates:
            if not path or not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data["_source_path"] = path
                    return data
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Failed to read node contract %s: %s", path, exc)
        return {}

    def _default_signoff_requirements(
        self, caps: Dict[str, Any]
    ) -> List[SignoffRequirement]:
        req = [
            SignoffRequirement("rtl_lint_clean", "RTL syntax/lint clean", tool_family="verilator"),
            SignoffRequirement("simulation_pass", "Self-checking simulation passed", tool_family="verilator/iverilog"),
            SignoffRequirement("formal_reviewed", "Formal/SVA pass or explicit waiver", tool_family="sby"),
            SignoffRequirement("coverage_met", "Coverage thresholds met or waived", tool_family="verilator/iverilog"),
            SignoffRequirement("sdc_valid", "Timing constraints reviewed and consumed", tool_family="sdc"),
            SignoffRequirement("synthesis_clean", "Synthesis completed with no unmapped logic", tool_family="yosys"),
            SignoffRequirement("lec_pass", "RTL-to-gate logic equivalence passed or waived with OSS limitation noted", tool_family="eqy", required=False, blocking=False),
            SignoffRequirement("dft_complete", "Scan/ATPG/MBIST complete only when a commercial DFT flow is configured", required=False, tool_family="commercial_dft", blocking=False),
            SignoffRequirement("gds_generated", "GDS/OASIS generated by physical flow", tool_family="openlane/openroad"),
            SignoffRequirement("sta_all_corners_pass", "All required setup/hold corners pass", tool_family="opensta"),
            SignoffRequirement("drc_clean", "DRC clean with required deck", tool_family="magic/calibre/pegasus"),
            SignoffRequirement("lvs_clean", "LVS clean with required deck", tool_family="netgen/calibre/pegasus"),
            SignoffRequirement("antenna_clean", "Antenna checks clean or repaired", tool_family="openroad/magic"),
            SignoffRequirement("power_reviewed", "Power report reviewed against target", tool_family="opensta/openroad"),
            SignoffRequirement("package_complete", "Tapeout/IP package contains required collateral", tool_family="agentic"),
        ]
        if caps.get("advanced_node"):
            req.extend(
                [
                    SignoffRequirement("mmmc_defined", "MMMC views/modes/corners explicitly defined", tool_family="sta"),
                    SignoffRequirement("emir_pass", "EM/IR signoff passed", tool_family="redhawk/voltus"),
                    SignoffRequirement("dfm_density_pass", "DFM, density, and fill checks passed", tool_family="foundry"),
                    SignoffRequirement("reliability_pass", "Reliability/aging checks passed where required", tool_family="foundry"),
                    SignoffRequirement("power_intent_verified", "UPF/CPF power intent verified if multi-domain", tool_family="cpf/upf"),
                ]
            )
        return req

    def _requirements_from_manifest(
        self,
        manifest: Dict[str, Any],
        fallback: List[SignoffRequirement],
    ) -> List[SignoffRequirement]:
        raw = manifest.get("required_signoff")
        if not isinstance(raw, list) or not raw:
            return fallback
        parsed: List[SignoffRequirement] = []
        for item in raw:
            if isinstance(item, str):
                parsed.append(SignoffRequirement(key=item, description=item.replace("_", " ")))
            elif isinstance(item, dict):
                key = str(item.get("key") or item.get("name") or "").strip()
                if not key:
                    continue
                parsed.append(
                    SignoffRequirement(
                        key=key,
                        description=str(item.get("description") or key.replace("_", " ")),
                        required=bool(item.get("required", True)),
                        evidence_type=str(item.get("evidence_type", "report")),
                        tool_family=str(item.get("tool_family", "")),
                        blocking=bool(item.get("blocking", True)),
                    )
                )
        return parsed or fallback

    def _infer_readiness_level(
        self,
        status: str,
        contract: NodeContract,
        signoff_blockers: List[str],
    ) -> str:
        if status == "REJECT":
            return "NOT_FEASIBLE"
        if not contract.collateral_ready:
            return "RTL_CANDIDATE"
        if contract.fabrication_ready:
            return "FAB_READY" if not signoff_blockers else "COMMERCIAL_SIGNOFF_REQUIRED"
        return contract.readiness_ceiling or "OSS_LAYOUT_CANDIDATE"

    # ── Step 1: Frequency Feasibility ────────────────────────────────

    def _check_frequency(
        self,
        target_mhz: int,
        submodules: List[Dict[str, Any]],
        design_category: str,
    ) -> Tuple[List[str], List[str]]:
        warnings: List[str] = []
        rejections: List[str] = []

        tool_config = get_pdk_tool_config(self.pdk)
        max_reliable_mhz = tool_config.get("max_reliable_mhz", 150)
        upper_limit_mhz = tool_config.get("upper_limit_mhz", 200)

        # Note: Rejections for frequency > upper_limit are now handled in check()
        # with auto-adjust capability. This method only adds warnings for high-risk frequencies.

        if target_mhz > max_reliable_mhz:
            warnings.append(
                f"HIGH_RISK: Target frequency {target_mhz} MHz is at the upper "
                f"limit of {self.pdk}. Only feasible for highly pipelined datapaths "
                f"with no combinational paths longer than 3 logic levels."
            )
            # Flag submodules with likely deep logic
            for sm in submodules:
                combined = f"{sm.get('name', '')} {sm.get('description', '')}".lower()
                if any(
                    kw in combined
                    for kw in [
                        "alu",
                        "multiplier",
                        "divider",
                        "decode",
                        "arbiter",
                        "cache",
                        "branch_predict",
                    ]
                ):
                    warnings.append(
                        f"HIGH_RISK: Submodule '{sm.get('name')}' likely has deep "
                        f"combinational paths incompatible with {target_mhz} MHz."
                    )

        elif target_mhz > 100:
            warnings.append(
                f"MARGINAL: Target frequency {target_mhz} MHz requires careful "
                f"constraint tuning in OpenLane. Critical path budget: "
                f"{1000.0 / target_mhz:.1f} ns."
            )
            # Flag submodules whose critical path likely > 6ns
            for sm in submodules:
                combined = f"{sm.get('name', '')} {sm.get('description', '')}".lower()
                deep_logic_keywords = [
                    "multiplier",
                    "multiply",
                    "divider",
                    "divide",
                    "alu",
                    "decode",
                    "cache",
                    "arbiter",
                    "out-of-order",
                    "branch_predict",
                ]
                if any(kw in combined for kw in deep_logic_keywords):
                    warnings.append(
                        f"MARGINAL: Submodule '{sm.get('name')}' critical path "
                        f"likely exceeds 6 ns ({1000.0 / target_mhz:.1f} ns budget)."
                    )

        elif target_mhz > 50:
            # Check for designs with known timing pressure at 51-100 MHz
            for sm in submodules:
                combined = f"{sm.get('name', '')} {sm.get('description', '')}".lower()
                has_wide_mult = (
                    "multiplier" in combined or "multiply" in combined
                ) and self._extract_bit_width(combined) > 8
                has_deep_pipeline = (
                    "pipeline" in combined and self._count_pipeline_stages(combined) > 4
                )
                has_deep_logic = any(
                    kw in combined for kw in ["deep logic", "long combinational", "barrel"]
                )
                if has_wide_mult:
                    warnings.append(
                        f"TIMING_WARN: Submodule '{sm.get('name')}' contains a "
                        f"multiplier wider than 8 bits at {target_mhz} MHz."
                    )
                if has_deep_pipeline:
                    warnings.append(
                        f"TIMING_WARN: Submodule '{sm.get('name')}' has more than "
                        f"4 pipeline stages at {target_mhz} MHz."
                    )
                if has_deep_logic:
                    warnings.append(
                        f"TIMING_WARN: Submodule '{sm.get('name')}' has deep logic "
                        f"cones at {target_mhz} MHz."
                    )

        # 0-50 MHz: FEASIBLE for any complexity — no warnings needed

        return warnings, rejections

    # ── Step 2: Memory Feasibility ───────────────────────────────────

    def _check_memory(
        self, submodules: List[Dict[str, Any]]
    ) -> Tuple[List[str], List[str], List[MacroRequirement], List[FeasibilityIssue]]:
        warnings: List[str] = []
        rejections: List[str] = []
        issues: List[FeasibilityIssue] = []
        macros: List[MacroRequirement] = []

        for sm in submodules:
            name = sm.get("name", "unknown")
            combined = f"{name} {sm.get('description', '')}".lower()

            # Skip non-memory submodules
            mem_keywords = [
                "memory",
                "ram",
                "sram",
                "rom",
                "fifo",
                "cache",
                "register_file",
                "regfile",
                "register file",
                "buffer",
                "stack",
                "queue",
            ]
            if not any(kw in combined for kw in mem_keywords):
                continue

            # Try to extract width × depth
            width, depth = self._extract_memory_dimensions(combined)
            if width == 0 or depth == 0:
                # Try to infer from port widths
                ports = sm.get("ports", [])
                width, depth = self._infer_memory_from_ports(ports)

            if width == 0 or depth == 0:
                continue

            size_bits = width * depth

            if size_bits > 16384:  # > 2KB
                msg = (
                    f"MEMORY_WARNING: '{name}' requires {size_bits} bits "
                    f"({size_bits // 8} bytes) of storage. This must be "
                    f"implemented as an OpenRAM macro, not synthesized registers. "
                    f"(width={width}, depth={depth})"
                )
                warnings.append(msg)
                # Infer port counts from description
                rports = 1
                wports = 1
                if "dual" in combined or "2-port" in combined:
                    rports = 2
                if "dual write" in combined or "2 write" in combined:
                    wports = 2
                macros.append(
                    MacroRequirement(
                        submodule_name=name,
                        width_bits=width,
                        depth_words=depth,
                        read_ports=rports,
                        write_ports=wports,
                        size_bits=size_bits,
                    )
                )
                issues.append(
                    FeasibilityIssue(
                        category="REQUIRES_MACRO",
                        code="MEMORY_MACRO_REQUIRED",
                        target=name,
                        message=msg,
                        suggested_action=(
                            "Use a memory macro wrapper/black box and keep only the "
                            "digital control/interface logic in synthesized RTL."
                        ),
                    )
                )

            elif size_bits > 2048:  # 256B–2KB
                ge_estimate = width * depth * 6  # rough: each bit ≈ 6 GE
                warnings.append(
                    f"MEMORY_WARN: '{name}' requires {size_bits} bits "
                    f"({size_bits // 8} bytes). Will synthesize as registers but "
                    f"consumes ~{ge_estimate} gate equivalents. "
                    f"(width={width}, depth={depth})"
                )
            # Below 2048 bits: FEASIBLE, no action

        return warnings, rejections, macros, issues

    # ── Step 3: Arithmetic Feasibility ───────────────────────────────

    def _check_arithmetic(
        self,
        submodules: List[Dict[str, Any]],
        contracts: List[Dict[str, Any]],
        design_desc: str,
    ) -> List[str]:
        warnings: List[str] = []
        combined_text = design_desc.lower()

        # Collect all text: submodule descriptions + behavioral contracts
        for sm in submodules:
            combined_text += f" {sm.get('name', '')} {sm.get('description', '')}".lower()
        for c in contracts:
            combined_text += (
                f" {c.get('given', '')} {c.get('when', '')} {c.get('then', '')}".lower()
            )

        # Check for multiplication
        mult_patterns = [
            (r"(\d+)\s*[x×]\s*(\d+)\s*(?:bit|-)?\s*mult", "explicit multiplier"),
            (r"(\d+)\s*-?\s*bit\s+mult", "bit-width multiplier"),
            (r"mult\w*\s+(\d+)\s*(?:bit|-bit)", "multiplier width"),
        ]
        found_mult = False
        for pat, desc in mult_patterns:
            m = re.search(pat, combined_text, re.IGNORECASE)
            if m:
                found_mult = True
                groups = m.groups()
                try:
                    if len(groups) == 2:
                        w1, w2 = int(groups[0]), int(groups[1])
                    else:
                        w1 = w2 = int(groups[0])
                except (ValueError, TypeError):
                    w1, w2 = 0, 0

                if w1 > 16 or w2 > 16:
                    warnings.append(
                        f"ARITHMETIC_WARN: {w1}×{w2}-bit multiplier is expensive on "
                        f"{self.pdk} (~{w1 * w2 * 4} GE, no DSP blocks). Consider "
                        f"pipelining or shift-and-add over multiple cycles."
                    )
                elif w1 > 8 or w2 > 8:
                    warnings.append(
                        f"ARITHMETIC_WARN: {w1}×{w2}-bit multiplier will consume "
                        f"~1000 GE on {self.pdk} and may impact timing."
                    )
                # ≤ 8×8: feasible (~200 GE)

        # Check for multiplier keywords even without explicit dimensions
        if not found_mult:
            for sm in submodules:
                combined = f"{sm.get('name', '')} {sm.get('description', '')}".lower()
                if "multiplier" in combined or "multiply" in combined or "mac" in combined:
                    width = self._extract_bit_width(combined)
                    if width > 16:
                        warnings.append(
                            f"ARITHMETIC_WARN: Submodule '{sm.get('name')}' contains "
                            f"multiplication ({width}-bit). Very expensive on {self.pdk}. "
                            f"Consider pipelining."
                        )
                    elif width > 8:
                        warnings.append(
                            f"ARITHMETIC_WARN: Submodule '{sm.get('name')}' contains "
                            f"multiplication ({width}-bit). ~1000 GE, may impact timing."
                        )

        # Check for division
        if "divider" in combined_text or "divide" in combined_text or "division" in combined_text:
            warnings.append(
                f"ARITHMETIC_WARN: Division is extremely expensive on {self.pdk} "
                "(no hardware divider). Flag for manual review. Consider "
                "iterative shift-subtract implementation."
            )

        # Check for floating point
        if "float" in combined_text or "fpu" in combined_text or "ieee 754" in combined_text:
            warnings.append(
                "ARITHMETIC_WARN: Floating-point operations are extremely expensive "
                f"on {self.pdk}. A minimal FPU can consume >5000 GE. Flag for manual review."
            )

        return warnings

    # ── Step 4: Area Estimation ──────────────────────────────────────

    def _estimate_area(self, submodules: List[Dict[str, Any]]) -> Tuple[int, Dict[str, int]]:
        total_ge = 0
        breakdown: Dict[str, int] = {}

        for sm in submodules:
            name = sm.get("name", "unknown")
            combined = f"{name} {sm.get('description', '')}".lower()

            ge = self._estimate_submodule_ge(combined, sm)
            breakdown[name] = ge
            total_ge += ge

        # Add overhead for top-level IO pads, clock tree, etc. (~5%)
        # Dynamic calculation instead of a hard 100 GE floor to allow simpler modules
        overhead = max(10, int(total_ge * 0.05)) if total_ge > 0 else 0
        breakdown["_interconnect_overhead"] = overhead
        total_ge += overhead

        return total_ge, breakdown

    def _estimate_submodule_ge(self, combined_text: str, sm: Dict[str, Any]) -> int:
        """Estimate gate equivalents for a single submodule."""
        best_ge = 0
        matched = False

        for keywords, base_ge, _desc in _GE_KEYWORD_MAP:
            for kw in keywords:
                if kw in combined_text:
                    # Scale by apparent data width if detectable
                    width = self._extract_bit_width(combined_text)
                    if width > 0 and kw in (
                        "adder",
                        "counter",
                        "comparator",
                        "shift_register",
                        "barrel_shifter",
                        "register",
                    ):
                        scaled = int(base_ge * (width / 32.0)) if width != 32 else base_ge
                        best_ge = max(best_ge, max(scaled, base_ge // 4))
                    else:
                        best_ge = max(best_ge, base_ge)
                    matched = True

        if not matched:
            # Fallback: estimate from port count
            port_count = len(sm.get("ports", []))
            best_ge = max(50, port_count * 20)

        return best_ge

    def _check_area_budget(self, total_ge: int) -> List[str]:
        warnings: List[str] = []

        if total_ge > 200_000:
            warnings.append(
                f"AREA_WARN: Estimated {total_ge} GE exceeds the comfortable "
                f"OpenLane limit. OpenLane may time out or fail placement. "
                f"Consider splitting into multiple tiles or simplifying."
            )
        elif total_ge > 50_000:
            warnings.append(
                f"AREA_INFO: Large design ({total_ge} GE). OpenLane run will "
                f"take 30–60 minutes. Ensure adequate compute resources."
            )

        return warnings

    def _recommend_floorplan(self, total_ge: int) -> str:
        for threshold, description, _size in FLOORPLAN_TIERS:
            if total_ge <= threshold:
                return description
        return f"Very large design ({total_ge} GE) — manual floorplan required"

    # ── Step 5: PDK / ASIC Flow Rules ────────────────────────────────

    def _check_pdk_rules(
        self,
        top_ports: List[Dict[str, Any]],
        submodules: List[Dict[str, Any]],
        contracts: List[Dict[str, Any]],
        design_desc: str,
        spec: Dict[str, Any],
    ) -> Tuple[List[str], List[str], List[FeasibilityIssue]]:
        warnings: List[str] = []
        rejections: List[str] = []
        issues: List[FeasibilityIssue] = []
        caps = get_pdk_flow_capabilities(self.pdk)
        pdk_label = caps.get("profile") or self.pdk

        combined_text = design_desc.lower()
        for sm in submodules:
            combined_text += f" {sm.get('name', '')} {sm.get('description', '')}".lower()
        for c in contracts:
            combined_text += (
                f" {c.get('given', '')} {c.get('when', '')} {c.get('then', '')}".lower()
            )

        # Rule 1: Internal tri-state buses
        top_level_port_names = {p.get("name", "") for p in top_ports}
        for sm in submodules:
            for p in sm.get("ports", []):
                if p.get("direction", "") == "inout":
                    pname = p.get("name", "")
                    if pname not in top_level_port_names:
                        msg = (
                            f"FEASIBILITY_REJECTED: Internal tri-state/inout port "
                            f"'{pname}' in submodule '{sm.get('name')}'. Standard-cell "
                            f"ASIC flows for {pdk_label} cannot use internal tri-states. "
                            f"Replace with explicit input/output/output-enable signals."
                        )
                        rejections.append(msg)
                        issues.append(
                            FeasibilityIssue(
                                category="AUTO_REPAIRABLE",
                                code="INTERNAL_TRISTATE",
                                target=f"{sm.get('name')}.{pname}",
                                message=msg,
                                suggested_action="Split internal inout into *_i, *_o, and *_oe signals.",
                                severity="error",
                            )
                        )

        # Rule 2: Async reset with > 2 clock domains
        clock_domain_keywords = [
            "clock domain",
            "clk_domain",
            "cdc",
            "multi-clock",
            "clock crossing",
            "dual clock",
        ]
        has_multi_clock = any(kw in combined_text for kw in clock_domain_keywords)

        async_reset_keywords = ["async", "asynchronous reset", "async_reset"]
        has_async_reset = any(kw in combined_text for kw in async_reset_keywords)

        # Count distinct clock-like ports
        clock_ports: set = set()
        for p in top_ports:
            pname = p.get("name", "").lower()
            if "clk" in pname or "clock" in pname:
                clock_ports.add(pname)
        for sm in submodules:
            for p in sm.get("ports", []):
                pname = p.get("name", "").lower()
                if "clk" in pname or "clock" in pname:
                    clock_ports.add(pname)

        if has_async_reset and (has_multi_clock or len(clock_ports) > 2):
            warnings.append(
                f"PDK_WARN: Asynchronous reset with more than 2 clock domains on {pdk_label} "
                "detected. Cross-domain async reset de-assertion needs "
                "synchronizers. Add reset synchronizer module per domain."
            )

        # Rule 3: PLL or analog blocks
        analog_keywords = [
            "pll",
            "phase-locked loop",
            "dac",
            "adc",
            "analog",
            "voltage reference",
            "bandgap",
            "ldo",
            "oscillator",
            "trng",
        ]
        for kw in analog_keywords:
            if kw in combined_text:
                msg = (
                    f"ANALOG_BLOCK_DETECTED: '{kw.upper()}' requires analog collateral "
                    f"and cannot be synthesized directly in the {pdk_label} RTL-to-GDS flow. "
                    f"Auto-reconcile by implementing it as a digital wrapper/interface."
                )
                warnings.append(msg)
                issues.append(
                    FeasibilityIssue(
                        category="AUTO_REPAIRABLE",
                        code="ANALOG_OR_CUSTOM_BLOCK",
                        target=kw,
                        message=msg,
                        suggested_action=(
                            "Replace with a digital control/status wrapper or a hard-macro "
                            "black-box interface."
                        ),
                        severity="warning",
                    )
                )

        # Rule 4: Negative-edge triggered flip-flops
        negedge_keywords = [
            "negedge",
            "negative edge",
            "falling edge triggered",
            "neg-edge",
            "negative-edge",
        ]
        for kw in negedge_keywords:
            if kw in combined_text:
                warnings.append(
                    f"PDK_WARN: Negative-edge triggered flip-flops detected for {pdk_label}. "
                    "The selected standard-cell library may have limited negedge cells. "
                    "Prefer posedge-triggered always_ff."
                )
                break

        # Rule 5: Latches
        latch_keywords = ["latch", "level-sensitive", "transparent latch"]
        for kw in latch_keywords:
            if kw in combined_text:
                warnings.append(
                    f"PDK_WARN: Latch-based storage detected for {pdk_label}. OpenLane synthesis "
                    "may not handle latch inference correctly. Prefer always_ff "
                    "with flip-flops."
                )
                break

        return warnings, rejections, issues

    # ── Utility: Collect All Submodules ──────────────────────────────

    def _collect_all_submodules(
        self,
        hw_spec_dict: Dict[str, Any],
        hierarchy_result_dict: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Flatten all submodules including nested specs from hierarchy."""
        all_subs: List[Dict[str, Any]] = []

        # Top-level submodules from hw_spec
        for sm in hw_spec_dict.get("submodules", []):
            all_subs.append(sm)

        # If hierarchy result exists, also scan expanded nested specs
        if hierarchy_result_dict:
            for sm in hierarchy_result_dict.get("submodules", []):
                # Don't re-add duplicates already in hw_spec
                nested = sm.get("nested_spec")
                if nested and isinstance(nested, dict):
                    self._collect_nested_subs(nested, all_subs)

        return all_subs

    def _collect_nested_subs(self, spec_dict: Dict[str, Any], out: List[Dict[str, Any]]) -> None:
        """Recursively collect submodules from nested specs."""
        for sm in spec_dict.get("submodules", []):
            out.append(sm)
            nested = sm.get("nested_spec")
            if nested and isinstance(nested, dict):
                self._collect_nested_subs(nested, out)

    # ── Utility: Extract Dimensions ──────────────────────────────────

    def _extract_bit_width(self, text: str) -> int:
        """Extract the most likely data width (in bits) from text."""
        patterns = [
            r"(\d+)\s*-?\s*bit",
            r"data_width\s*[=:]\s*(\d+)",
            r"width\s*[=:]\s*(\d+)",
            r"\[(\d+):0\]",
        ]
        best = 0
        for pat in patterns:
            for m in re.finditer(pat, text, re.IGNORECASE):
                try:
                    val = int(m.group(1))
                    if pat == r"\[(\d+):0\]":
                        val += 1  # [N:0] means N+1 bits
                    best = max(best, val)
                except (ValueError, IndexError):
                    pass
        return best

    def _extract_memory_dimensions(self, text: str) -> Tuple[int, int]:
        """Extract width × depth from memory description text."""
        # Patterns: "32x1024", "32-bit × 256-deep", "width 32 depth 256"
        patterns = [
            r"(\d+)\s*[x×]\s*(\d+)",
            r"width\s*[=:]\s*(\d+).*?depth\s*[=:]\s*(\d+)",
            r"(\d+)\s*-?\s*bit\s*.*?(\d+)\s*-?\s*(?:deep|entries|words|locations)",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                try:
                    a, b = int(m.group(1)), int(m.group(2))
                    # Convention: smaller number is width, larger is depth
                    width = min(a, b)
                    depth = max(a, b)
                    # But if first number looks like a width (8,16,32,64,128)
                    if a in (8, 16, 32, 64, 128, 256):
                        width, depth = a, b
                    return width, depth
                except (ValueError, IndexError):
                    pass

        # Try single dimension: "1024-bit memory" → assume 8-bit wide × 128 deep
        m = re.search(r"(\d+)\s*-?\s*bit\s+(?:memory|ram|sram|rom)", text, re.IGNORECASE)
        if m:
            total = int(m.group(1))
            if total > 256:
                # Assume 8-bit width
                return 8, total // 8

        return 0, 0

    def _infer_memory_from_ports(self, ports: List[Dict[str, Any]]) -> Tuple[int, int]:
        """Infer memory width/depth from port data types."""
        data_width = 0
        addr_width = 0

        for p in ports:
            pname = p.get("name", "").lower()
            dtype = p.get("data_type", "")

            # Extract bus width from data_type like "logic [31:0]"
            m = re.search(r"\[(\d+):0\]", dtype)
            bus_width = (int(m.group(1)) + 1) if m else 1

            if any(kw in pname for kw in ["data", "din", "dout", "q", "rdata", "wdata"]):
                data_width = max(data_width, bus_width)
            if any(kw in pname for kw in ["addr", "address"]):
                addr_width = max(addr_width, bus_width)

        if data_width > 0 and addr_width > 0:
            depth = 2**addr_width
            return data_width, depth

        return 0, 0

    def _count_pipeline_stages(self, text: str) -> int:
        """Try to extract pipeline stage count from text."""
        m = re.search(r"(\d+)\s*-?\s*stage", text, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return 0

    # ── Enrichment for Downstream Stages ─────────────────────────────

    def to_feasibility_enrichment(self, result: FeasibilityResult) -> Dict[str, Any]:
        """Convert FeasibilityResult to enrichment dict for the spec artifact."""
        return {
            "feasibility_status": result.feasibility_status,
            "estimated_gate_equivalents": result.estimated_gate_equivalents,
            "recommended_floorplan": result.recommended_floorplan_size_um,
            "target_frequency_mhz": result.target_frequency_mhz,
            "readiness_level": result.readiness_level,
            "signoff_blockers": list(result.signoff_blockers),
            "node_contract": result.node_contract.to_dict() if result.node_contract else {},
            "memory_macros": [m.to_dict() for m in result.memory_macros_required],
            "warnings_count": len(result.feasibility_warnings),
            "rejections_count": len(result.feasibility_rejections),
            "issues": [i.to_dict() for i in result.feasibility_issues],
            "area_breakdown": result.area_breakdown,
        }
