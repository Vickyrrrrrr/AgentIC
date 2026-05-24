"""
Pipeline Error Recovery & Self-Healing
======================================
Unified error recovery system that handles ANY error at ANY stage
in the VLSI pipeline: detection → classification → root-cause → fix → retry.

Supports:
- RTL errors (syntax, lint, logic)          → ReAct loop + IncrementalFixEngine
- Synthesis errors (Yosys)                  → SDC adjustment + RTL fix
- Timing violations (STA)                   → Clock relaxation + area expansion
- Physical errors (OpenLane placement/CTS/routing) → Area/utilization/constraint tuning
- DRC violations (Magic)                    → Floorplan adjustment
- LVS mismatches (Netgen)                   → Port preservation + re-synthesis
- Tool/infrastructure errors                → Retry with fallback
"""

import re
import os
import time
import json
import logging
from typing import Dict, List, Optional, Tuple, Any, Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class RecoveryAction(Enum):
    """Actions that the recovery system can take."""
    RETRY_SAME = "retry_same"           # Re-run the identical operation
    RELAX_CLOCK = "relax_clock"         # Increase clock period (+10%-30%)
    EXPAND_AREA = "expand_area"         # Increase die area (+10%-30%)
    REDUCE_UTIL = "reduce_util"         # Reduce core utilization (-5% to -15%)
    FIX_RTL = "fix_rtl"                # Reroute to RTL_GEN with error context
    REGEN_SDC = "regen_sdc"            # Regenerate SDC constraints
    REGEN_CONFIG = "regen_config"      # Regenerate OpenLane config
    PIPELINE_CRITICAL = "pipeline_critical"  # Split critical path
    SWITCH_STRATEGY = "switch_strategy" # Switch synth strategy (AREA→DELAY etc.)
    SKIP_STAGE = "skip_stage"          # Skip this stage and continue
    FAIL = "fail"                      # Unrecoverable — fail the build


@dataclass
class RecoveryResult:
    action: RecoveryAction
    description: str
    params: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    needs_rtl_fix: bool = False
    needs_sdc_regen: bool = False
    needs_config_regen: bool = True  # Most fixes need config regeneration


class OpenLaneErrorFixer:
    """Maps OpenLane/EDA tool errors to concrete fix actions.
    
    Unlike the SelfReflectPipeline (which proposes actions via LLM),
    this class provides DETERMINISTIC fixes based on error pattern matching.
    """

    # Patterns for detecting specific failure modes in OpenLane output
    PATTERNS = {
        "timing_setup": [
            r"(setup|hold)\s+timing.*(?:violat|fail|not met)",
            r"wns:\s*-?\d+\.\d+\s*(?:ns|ps)?.*(?:FAIL|VIOLAT)",
            r"check_setup.*slack.*?(-[\d.]+)",
            r"check_hold.*slack.*?(-[\d.]+)",
        ],
        "routing_congestion": [
            r"congestion.*(?:(\d+(?:\.\d+)?)\s*%)",
            r"global\s+routing.*(?:fail|overflow|capacity)",
            r"GRT.*overflow",
            r"routing.*(?:fail|error).*capacity",
        ],
        "global_route_uncovered": [
            r"GRT-0076",
            r"net\s+\S+\s+not\s+properly\s+covered",
            r"not\s+properly\s+covered",
        ],
        "detail_route_resource": [
            r"child\s+killed:\s+kill\s+signal",
            r"detailed\s+routing.*(?:killed|out\s+of\s+memory|oom)",
            r"TritonRoute.*(?:killed|out\s+of\s+memory|oom)",
        ],
        "detail_route_short": [
            r"violations\s+in\s+the\s+design\s+after\s+detailed\s+routing",
            r"Viol/Layer\s+.*Short",
            r"violation\s+type:\s+Short",
        ],
        "antenna_overrepair": [
            r"DPL-0036[\s\S]{0,3000}ANTENNA_",
            r"ANTENNA_[A-Za-z0-9_]+[\s\S]{0,3000}DPL-0036",
            r"Inserted\s+(?:[1-9]\d{4,}|[\d,]{6,})\s+diodes",
            r"diode.*(?:legaliz|placement|dpl).*fail",
        ],
        "detailed_placement_failure": [
            r"DPL-0036",
            r"Detailed\s+placement\s+failed",
            r"detailed\s+placement.*(?:fail|error)",
        ],
        "drc_violation": [
            r"total\s+violations?:\s*(\d+)",
            r"DRC\s+(?:violation|error)s?:\s*(\d+)",
            r"(\d+)\s+violations?\s*(?:found|detected)",
        ],
        "lvs_mismatch": [
            r"(?:layout.*schematic|LVS).*mismatch",
            r"net\s+mismatch",
            r"device\s+mismatch",
            r"property\s+error",
        ],
        "synthesis_error": [
            r"(?:yosys|synthesis).*error",
            r"ERROR.*(?:synthesi|module|cell)",
            r"no\s+matching\s+module",
        ],
        "placement_failure": [
            r"(?:placement|place).*fail",
            r"unable\s+to\s+place",
            r"utilization.*exceed",
        ],
        "antenna_violation": [
            r"antenna.*violation",
            r"(?:gate|oxide)\s+(?:area|ratio).*exceed",
            r"(?:pin|net)\s+antenna\s+violations?:\s*[1-9]\d*",
            r"pin\s+violations?:\s*[1-9]\d*.*net\s+violations?:\s*[1-9]\d*",
        ],
        "max_transition": [
            r"max\s+transition.*violat",
            r"trans.*slew.*exceed",
        ],
        "max_capacitance": [
            r"max\s+capacitance.*violat",
            r"cap.*load.*exceed",
        ],
    }

    # Action sequence when multiple fixes are needed (in priority order)
    ACTION_ESCALATION = [
        RecoveryAction.RELAX_CLOCK,
        RecoveryAction.REDUCE_UTIL,
        RecoveryAction.EXPAND_AREA,
        RecoveryAction.PIPELINE_CRITICAL,
        RecoveryAction.SWITCH_STRATEGY,
        RecoveryAction.FIX_RTL,
        RecoveryAction.FAIL,
    ]

    def __init__(self):
        self.compiled_patterns = {
            key: [re.compile(p, re.IGNORECASE) for p in patterns]
            for key, patterns in self.PATTERNS.items()
        }

    def classify(self, error_output: str) -> List[str]:
        """Classify error output into one or more failure categories."""
        categories = []
        for category, patterns in self.compiled_patterns.items():
            for pattern in patterns:
                if pattern.search(error_output):
                    categories.append(category)
                    break
        return categories or ["unknown"]

    def get_fix(
        self,
        categories: List[str],
        current_params: Dict[str, Any],
        attempt: int,
    ) -> RecoveryResult:
        """Generate a fix action for the given failure categories.
        
        Args:
            categories: List of failure category strings
            current_params: Current physical design parameters
            attempt: Which recovery attempt this is (0-indexed)
        
        Returns:
            RecoveryResult with the fix action and adjusted parameters
        """
        if not categories or "unknown" in categories:
            if attempt >= 2:
                return RecoveryResult(RecoveryAction.FAIL, "Unclassified error after 2 attempts")
            return RecoveryResult(RecoveryAction.RETRY_SAME, "Retrying unclassified failure")

        # Cross-category failures first: antenna repair can over-insert diodes
        # until detailed placement fails, which should not be treated as a
        # plain area expansion problem.
        category_set = set(categories)
        if "antenna_overrepair" in category_set:
            return self._fix_antenna_overrepair(current_params, attempt)
        if (
            "detailed_placement_failure" in category_set
            and "antenna_violation" in category_set
        ):
            return self._fix_antenna_overrepair(current_params, attempt)

        # Determine the dominant issue and apply fix
        primary = categories[0]
        
        if primary == "timing_setup":
            return self._fix_timing(current_params, attempt)
        elif primary == "routing_congestion":
            return self._fix_congestion(current_params, attempt)
        elif primary == "global_route_uncovered":
            return self._fix_macro_pin_access(current_params, attempt)
        elif primary == "detail_route_resource":
            return self._fix_detail_route_resource(current_params, attempt)
        elif primary == "detail_route_short":
            return self._fix_detail_route_short(current_params, attempt)
        elif primary == "drc_violation":
            return self._fix_drc(current_params, attempt)
        elif primary == "lvs_mismatch":
            return self._fix_lvs(current_params, attempt)
        elif primary == "synthesis_error":
            return self._fix_synthesis(current_params, attempt)
        elif primary == "placement_failure":
            return self._fix_placement(current_params, attempt)
        elif primary == "detailed_placement_failure":
            return self._fix_placement(current_params, attempt)
        elif primary == "antenna_violation":
            return self._fix_antenna(current_params, attempt)
        elif primary in ("max_transition", "max_capacitance"):
            return self._fix_physical(current_params, attempt, primary)
        
        # Escalate through the action hierarchy
        if attempt >= len(self.ACTION_ESCALATION):
            return RecoveryResult(RecoveryAction.FAIL, "All recovery actions exhausted")
        
        action = self.ACTION_ESCALATION[attempt]
        return RecoveryResult(action, f"Escalated recovery: {action.value}")

    # ── Individual fix generators ──

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _current_die_dimensions(self, params: Dict[str, Any], default: int = 800) -> Tuple[int, int]:
        """Return the failing design's current die dimensions without assuming a chip size."""
        die = self._to_int(params.get("die_area"), default)
        width = self._to_int(params.get("die_width"), die)
        height = self._to_int(params.get("die_height"), die)
        return max(1, width), max(1, height)

    def _route_recovery_dimensions(
        self,
        params: Dict[str, Any],
        *,
        compact_large_floorplan: bool,
    ) -> Tuple[int, int]:
        """Scale routing-recovery dimensions from the current design.

        Oversized sparse floorplans can make routing failures worse, but forcing
        every design into one known-good die size would overfit to a single chip.
        This keeps small designs unchanged and proportionally compacts only large
        floorplans.
        """
        width, height = self._current_die_dimensions(params)
        if compact_large_floorplan and max(width, height) >= 2400:
            scale = float(params.get("route_recovery_compact_scale", 0.67) or 0.67)
            width = max(400, int(width * scale))
            height = max(400, int(height * scale))
        if abs(width - height) <= 1 and max(width, height) >= 1600:
            height = max(400, int(width * 0.90))
        return width, height

    def _macro_repair_knobs(self, width: int, height: int, util: int) -> Dict[str, Any]:
        span = max(width, height)
        halo = max(20, min(80, int(round(span * 0.02))))
        channel = max(40, min(160, halo * 2))
        density = max(0.20, min(0.45, util / 100.0))
        return {
            "target_density": density,
            "macro_halo": [halo, halo],
            "macro_channel": [channel, channel],
            "macro_blockages_layer": "li1 met1 met2 met3 met4 met5",
        }

    @staticmethod
    def _macro_nudge(width: int, attempt: int) -> int:
        if attempt <= 0:
            return 0
        return max(80, min(600, int(width * 0.30)))

    def _wirelength_limit(self, width: int, height: int) -> int:
        span = max(width, height)
        return max(250, min(800, int(span * 0.30)))

    def _antenna_config_overrides(
        self,
        width: int,
        height: int,
        *,
        margin: int,
        max_iters: int,
        heuristic: bool,
        threshold: int = 60,
    ) -> Dict[str, Any]:
        """Return bounded OpenLane antenna-repair knobs.

        These are intentionally conservative. Unbounded heuristic diode
        insertion can fix antenna ratios while destroying detailed placement.
        """
        wire_limit = self._wirelength_limit(width, height)
        overrides: Dict[str, Any] = {
            "GRT_REPAIR_ANTENNAS": 1,
            "GRT_ANT_ITERS": 30,
            "GRT_ANT_MARGIN": margin,
            "GRT_MAX_DIODE_INS_ITERS": max_iters,
            "RUN_HEURISTIC_DIODE_INSERTION": 1 if heuristic else 0,
            "DIODE_ON_PORTS": "in",
            "DIODE_PADDING": 2,
            "GLB_RESIZER_DESIGN_OPTIMIZATIONS": 1,
            "GLB_RESIZER_TIMING_OPTIMIZATIONS": 0,
            "GLB_RESIZER_MAX_WIRE_LENGTH": wire_limit,
            "PL_RESIZER_MAX_WIRE_LENGTH": wire_limit,
        }
        if heuristic:
            overrides["HEURISTIC_ANTENNA_THRESHOLD"] = threshold
            overrides["HEURISTIC_ANTENNA_INSERTION_MODE"] = "balanced"
        return overrides

    def _fix_timing(self, params: Dict[str, Any], attempt: int) -> RecoveryResult:
        current_clk = params.get("clock_period", 10.0)
        if attempt == 0:
            new_clk = round(current_clk * 1.15, 2)  # +15%
            return RecoveryResult(
                RecoveryAction.RELAX_CLOCK,
                f"Relax clock period from {current_clk}ns to {new_clk}ns (+15%)",
                {"clock_period": new_clk, "old_clock_period": current_clk},
                confidence=0.85,
            )
        elif attempt == 1:
            new_clk = round(current_clk * 1.30, 2)  # +30% total
            return RecoveryResult(
                RecoveryAction.EXPAND_AREA,
                f"Timing still failing after clock relax. Expanding area + reducing utilization.",
                {"clock_period": new_clk, "die_area_scale": 1.20, "core_util_reduce": -10},
                confidence=0.70,
            )
        else:
            return RecoveryResult(
                RecoveryAction.PIPELINE_CRITICAL,
                "Timing not converging — pipeline the critical path or decouple logic.",
                {"action_hint": "Insert pipeline registers in critical path"},
                confidence=0.60,
            )

    def _fix_congestion(self, params: Dict[str, Any], attempt: int) -> RecoveryResult:
        current_util = params.get("core_util", 40)
        current_area = params.get("die_area", 500)
        if attempt == 0:
            new_util = max(25, current_util - 8)
            return RecoveryResult(
                RecoveryAction.REDUCE_UTIL,
                f"Reduce core utilization from {current_util}% to {new_util}% (-8pp)",
                {"core_util": new_util, "old_core_util": current_util},
                confidence=0.80,
            )
        elif attempt == 1:
            new_area = int(current_area * 1.20)
            new_util = max(25, current_util - 5)
            return RecoveryResult(
                RecoveryAction.EXPAND_AREA,
                f"Expand die area to {new_area}um and reduce util to {new_util}%",
                {"die_area": new_area, "core_util": new_util},
                confidence=0.75,
            )
        else:
            new_util = max(20, current_util - 5)
            new_area = int(current_area * 1.35)
            return RecoveryResult(
                RecoveryAction.EXPAND_AREA,
                f"Aggressive: area={new_area}um, util={new_util}%",
                {"die_area": new_area, "core_util": new_util},
                confidence=0.60,
            )

    def _fix_macro_pin_access(self, params: Dict[str, Any], attempt: int) -> RecoveryResult:
        """Repair GRT-0076/net-not-covered failures, commonly macro pin access."""
        width, height = self._route_recovery_dimensions(params, compact_large_floorplan=True)
        util = max(20, min(35, self._to_int(params.get("core_util"), 35)))
        nudge = self._macro_nudge(width, attempt)
        return RecoveryResult(
            RecoveryAction.REGEN_CONFIG,
            "Global route left a net/pin uncovered; enable macro-aware placement, halo, and routing blockages.",
            {
                "macro_floorplan_repair": True,
                "macro_placement_nudge_x": nudge,
                "die_width": width,
                "die_height": height,
                "core_util": util,
                **self._macro_repair_knobs(width, height, util),
            },
            confidence=0.82,
        )

    def _fix_detail_route_resource(self, params: Dict[str, Any], attempt: int) -> RecoveryResult:
        """Repair TritonRoute kill/OOM caused by oversized sparse floorplans."""
        width, height = self._route_recovery_dimensions(params, compact_large_floorplan=True)
        util = max(20, min(35, self._to_int(params.get("core_util"), 35)))
        return RecoveryResult(
            RecoveryAction.REGEN_CONFIG,
            "Detailed routing was killed; compact the floorplan and reduce filler/router load.",
            {
                "die_width": width,
                "die_height": height,
                "core_util": util,
                "macro_floorplan_repair": True,
                **self._macro_repair_knobs(width, height, util),
            },
            confidence=0.78,
        )

    def _fix_detail_route_short(self, params: Dict[str, Any], attempt: int) -> RecoveryResult:
        """Repair detailed-route shorts, especially macro top-metal/power shorts."""
        width, height = self._route_recovery_dimensions(params, compact_large_floorplan=False)
        util = max(20, self._to_int(params.get("core_util"), 35) - 5)
        nudge = max(80, min(600, int(width * (0.30 if attempt == 0 else 0.15))))
        return RecoveryResult(
            RecoveryAction.REGEN_CONFIG,
            "Detailed routing produced shorts; move hard macros out of congested corridors and preserve macro blockages.",
            {
                "macro_floorplan_repair": True,
                "macro_placement_nudge_x": nudge,
                "die_width": width,
                "die_height": height,
                "core_util": util,
                **self._macro_repair_knobs(width, height, util),
            },
            confidence=0.72,
        )

    def _fix_drc(self, params: Dict[str, Any], attempt: int) -> RecoveryResult:
        current_util = params.get("core_util", 40)
        current_area = params.get("die_area", 500)
        new_util = max(25, current_util - 10)
        new_area = int(current_area * (1.10 + attempt * 0.10))
        return RecoveryResult(
            RecoveryAction.REDUCE_UTIL if attempt == 0 else RecoveryAction.EXPAND_AREA,
            f"Relax floorplan to resolve DRC: util={new_util}%, area={new_area}um",
            {"core_util": new_util, "die_area": new_area},
            confidence=0.70,
        )

    def _fix_lvs(self, params: Dict[str, Any], attempt: int) -> RecoveryResult:
        if attempt == 0:
            return RecoveryResult(
                RecoveryAction.FIX_RTL,
                "LVS mismatch — RTL ports may have changed during optimization. Re-generate with preserved interface.",
                {"lvs_repair": True},
                confidence=0.55,
            )
        else:
            return RecoveryResult(
                RecoveryAction.REGEN_CONFIG,
                "LVS persists — regenerate config with preserved netlist options.",
                {"preserve_netlist": True},
                confidence=0.45,
            )

    def _fix_synthesis(self, params: Dict[str, Any], attempt: int) -> RecoveryResult:
        if attempt == 0:
            return RecoveryResult(
                RecoveryAction.SWITCH_STRATEGY,
                "Switch synthesis strategy from AREA to DELAY and retry.",
                {"synth_strategy": "DELAY 0"},
                confidence=0.75,
            )
        return RecoveryResult(
            RecoveryAction.FIX_RTL,
            "Synthesis still failing — likely an RTL issue. Reroute to RTL fix.",
            {},
            confidence=0.65,
        )

    def _fix_placement(self, params: Dict[str, Any], attempt: int) -> RecoveryResult:
        current_area = params.get("die_area", 500)
        new_area = int(current_area * (1.15 + attempt * 0.10))
        return RecoveryResult(
            RecoveryAction.EXPAND_AREA,
            f"Placement failed — expand die to {new_area}um",
            {"die_area": new_area},
            confidence=0.70,
        )

    def _fix_antenna(self, params: Dict[str, Any], attempt: int) -> RecoveryResult:
        """Repair signoff antenna violations without overfitting to one layout."""
        width, height = self._route_recovery_dimensions(params, compact_large_floorplan=False)
        current_util = self._to_int(params.get("core_util"), 40)
        util = max(20, min(35, current_util - (5 if attempt else 0)))

        if attempt <= 0:
            margin = 20
            max_iters = 4
            heuristic = False
        elif attempt == 1:
            margin = 30
            max_iters = 4
            heuristic = True
        else:
            # Persistent antenna usually means long nets or sparse placement.
            # Compact very large floorplans proportionally, keep diode insertion bounded.
            width, height = self._route_recovery_dimensions(params, compact_large_floorplan=True)
            margin = 30
            max_iters = 6
            heuristic = True
            util = max(20, min(32, util))

        return RecoveryResult(
            RecoveryAction.REGEN_CONFIG,
            "Antenna violations detected; enable bounded antenna repair and limit long wires.",
            {
                "die_width": width,
                "die_height": height,
                "core_util": util,
                "target_density": max(0.20, min(0.42, util / 100.0)),
                "openlane_config_overrides": self._antenna_config_overrides(
                    width,
                    height,
                    margin=margin,
                    max_iters=max_iters,
                    heuristic=heuristic,
                ),
            },
            confidence=0.74,
        )

    def _fix_antenna_overrepair(
        self, params: Dict[str, Any], attempt: int
    ) -> RecoveryResult:
        """Back off when antenna diode insertion causes placement collapse."""
        width, height = self._route_recovery_dimensions(params, compact_large_floorplan=False)
        if attempt > 0:
            width = int(width * 1.08)
            height = int(height * 1.08)
        current_util = self._to_int(params.get("core_util"), 40)
        util = max(20, min(32, current_util - 8))

        return RecoveryResult(
            RecoveryAction.REGEN_CONFIG,
            "Antenna repair over-inserted diodes; disable heuristic insertion and reopen placement density.",
            {
                "die_width": width,
                "die_height": height,
                "core_util": util,
                "target_density": max(0.20, min(0.36, util / 100.0)),
                "antenna_overrepair": True,
                "openlane_config_overrides": self._antenna_config_overrides(
                    width,
                    height,
                    margin=10 if attempt == 0 else 15,
                    max_iters=2 if attempt == 0 else 3,
                    heuristic=False,
                ),
            },
            confidence=0.80,
        )

    def _fix_physical(
        self, params: Dict[str, Any], attempt: int, category: str
    ) -> RecoveryResult:
        current_util = params.get("core_util", 40)
        new_util = max(30, current_util - 5)
        return RecoveryResult(
            RecoveryAction.REDUCE_UTIL,
            f"Physical error ({category}): reduce util to {new_util}%",
            {"core_util": new_util},
            confidence=0.60,
        )


class PipelineErrorRecovery:
    """Unified error recovery for any VLSI pipeline stage.
    
    Routes errors to the appropriate recovery mechanism:
    - RTL errors → IncrementalFixEngine + ReAct
    - Synthesis/timing/physical → OpenLaneErrorFixer
    - Tool/infra → GracefulDegradation retry
    
    Maintains recovery state across stages to prevent infinite loops.
    """

    def __init__(self, orchestrator_ref):
        """Initialize with a reference to the BuildOrchestrator."""
        self.orch = orchestrator_ref
        self.ol_fixer = OpenLaneErrorFixer()
        self.recovery_history: List[Dict[str, Any]] = []
        self.global_recovery_count = 0

    def handle_error(
        self,
        stage_name: str,
        error_output: str,
        *,
        rtl_code: Optional[str] = None,
        stage_params: Optional[Dict[str, Any]] = None,
        allow_rtl_fix: bool = True,
    ) -> Optional[RecoveryResult]:
        """Handle an error at any pipeline stage.
        
        Args:
            stage_name: Name of the failing stage (e.g., 'HARDENING', 'SYNTHESIS')
            error_output: Full error output from the tool
            rtl_code: Current RTL code (for RTL-level fixes)
            stage_params: Current stage parameters
            allow_rtl_fix: Whether to allow RTL fix as a recovery path
        
        Returns:
            RecoveryResult with fix action, or None if error is unclassified/ignorable
        """
        self.global_recovery_count += 1
        if self.global_recovery_count > 20:
            self.orch.log("Global recovery budget (20) exhausted. Failing.", refined=True)
            return RecoveryResult(RecoveryAction.FAIL, "Global recovery budget exhausted")

        # Step 1: Classify using IncrementalFixEngine
        rtl_code = rtl_code or self.orch.artifacts.get("rtl_code", "")
        analysis = self.orch.incremental_fixer.analyze_error(
            error_text=error_output[:8000],
            rtl_code=rtl_code,
        )

        # Step 2: Categorize using OpenLane fixer patterns
        ol_categories = self.ol_fixer.classify(error_output)

        # Step 3: Record recovery attempt
        recovery_entry = {
            "stage": stage_name,
            "error_type": analysis.error_type.value if hasattr(analysis.error_type, "value") else str(analysis.error_type),
            "ol_categories": ol_categories,
            "fix_confidence": analysis.fix_confidence,
            "timestamp": time.time(),
        }
        self.recovery_history.append(recovery_entry)

        # Step 4: Detect repeated failures (fingerprint dedup)
        if len(self.recovery_history) >= 3:
            last_three = self.recovery_history[-3:]
            if all(e["ol_categories"] == last_three[0]["ol_categories"] for e in last_three):
                self.orch.log(
                    f"Repeated failure pattern ({ol_categories}). Escalating.",
                    refined=True,
                )
                return self.ol_fixer.get_fix(
                    ol_categories,
                    stage_params or {},
                    attempt=3,  # Force escalation
                )

        # Step 5: Route to appropriate recovery mechanism
        recovery = self._route_recovery(
            stage_name=stage_name,
            analysis=analysis,
            ol_categories=ol_categories,
            stage_params=stage_params or {},
            allow_rtl_fix=allow_rtl_fix,
        )

        # Step 6: Log the recovery action
        self.orch.artifacts["last_recovery"] = {
            "stage": stage_name,
            "action": recovery.action.value,
            "description": recovery.description,
            "params": recovery.params,
            "confidence": recovery.confidence,
        }

        self.orch.log(
            f"[Recovery] {stage_name}: {recovery.action.value} ({recovery.description[:100]})",
            refined=True,
        )

        return recovery

    def _route_recovery(
        self,
        stage_name: str,
        analysis: Any,
        ol_categories: List[str],
        stage_params: Dict[str, Any],
        allow_rtl_fix: bool,
    ) -> RecoveryResult:
        """Route error to the appropriate recovery mechanism."""
        
        # Physical design errors → OpenLaneFixer
        physical_stages = {"HARDENING", "FLOORPLAN", "SYNTHESIS", "TIMING_ANALYSIS"}
        if stage_name in physical_stages and ol_categories:
            attempt = sum(1 for h in self.recovery_history if h["stage"] == stage_name)
            return self.ol_fixer.get_fix(ol_categories, stage_params, attempt)

        # Synthesis errors with structured output
        if stage_name == "SYNTHESIS" and analysis.error_type:
            err_name = analysis.error_type.value if hasattr(analysis.error_type, "value") else str(analysis.error_type)
            if "synthesis" in str(err_name).lower():
                return self.ol_fixer.get_fix(["synthesis_error"], stage_params, 0)

        # RTL-level errors → route to RTL fix
        if allow_rtl_fix and analysis.fix_confidence >= 0.45:
            return RecoveryResult(
                RecoveryAction.FIX_RTL,
                f"RTL-level error ({analysis.error_type}) — routing to RTL fix",
                {"error_analysis": True, "error_type": str(analysis.error_type)},
                confidence=analysis.fix_confidence,
            )

        # Tool errors → retry
        if ol_categories == ["unknown"]:
            return RecoveryResult(
                RecoveryAction.RETRY_SAME,
                "Unclassified error — retrying with same parameters",
                {},
                confidence=0.40,
            )

        # Last resort
        return RecoveryResult(
            RecoveryAction.FAIL,
            f"No viable recovery path for {stage_name} failure",
            {},
            confidence=0.10,
        )


def apply_recovery_result(
    orch: Any,
    result: RecoveryResult,
    stage_params: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply a recovery result to stage parameters and orchestrator state.
    
    Modifies orchestrator artifacts and returns updated stage_params.
    The caller is responsible for state transitions.
    """
    params = dict(stage_params)

    if result.action == RecoveryAction.RELAX_CLOCK:
        new_clk = result.params.get("clock_period")
        if new_clk:
            params["clock_period"] = new_clk
            orch.artifacts["clock_period_override"] = new_clk
            old = result.params.get("old_clock_period", "?")
            orch.log(f"Clock period adjusted: {old}ns → {new_clk}ns", refined=True)

    elif result.action == RecoveryAction.EXPAND_AREA:
        new_area = result.params.get("die_area")
        if new_area:
            params["die_area"] = new_area
            orch.artifacts["die_area_override"] = new_area
            orch.log(f"Die area expanded to {new_area}um", refined=True)
        new_util = result.params.get("core_util")
        if new_util:
            params["core_util"] = new_util
            orch.artifacts["core_util_override"] = new_util

    elif result.action == RecoveryAction.REDUCE_UTIL:
        new_util = result.params.get("core_util")
        if new_util:
            params["core_util"] = new_util
            orch.artifacts["core_util_override"] = new_util
            orch.log(f"Core utilization reduced to {new_util}%", refined=True)

    elif result.action == RecoveryAction.FIX_RTL:
        orch.artifacts["backend_error_stage"] = "pipeline_recovery"
        orch.artifacts["backend_error_analysis"] = result.params
        orch.artifacts["logic_decoupling_hint"] = result.params.get("action_hint", "")
        orch.log("Routing back to RTL_GEN for error-driven fix", refined=True)

    elif result.action == RecoveryAction.SWITCH_STRATEGY:
        new_strat = result.params.get("synth_strategy")
        if new_strat:
            params["synth_strategy"] = new_strat
            orch.log(f"Synthesis strategy switched to {new_strat}", refined=True)

    elif result.action == RecoveryAction.PIPELINE_CRITICAL:
        orch.artifacts["logic_decoupling_hint"] = result.params.get("action_hint", "")
        orch.log("Critical path pipelining requested", refined=True)

    elif result.action == RecoveryAction.REGEN_SDC:
        orch.artifacts["sdc_regen_requested"] = True
        orch.log("SDC regeneration requested", refined=True)

    elif result.action == RecoveryAction.REGEN_CONFIG:
        if result.params.get("die_width") and result.params.get("die_height"):
            width = int(result.params["die_width"])
            height = int(result.params["die_height"])
            params["die_width"] = width
            params["die_height"] = height
            params["die_area"] = max(width, height)
            orch.artifacts["die_area_override"] = (width, height)
            orch.log(f"Die area adjusted to {width}x{height}um", refined=True)
        if result.params.get("core_util"):
            new_util = int(result.params["core_util"])
            params["core_util"] = new_util
            orch.artifacts["core_util_override"] = new_util
        if result.params.get("target_density"):
            orch.artifacts["target_density_override"] = float(result.params["target_density"])
        config_overrides = result.params.get("openlane_config_overrides")
        if isinstance(config_overrides, dict) and config_overrides:
            merged = dict(orch.artifacts.get("openlane_config_overrides") or {})
            merged.update(config_overrides)
            orch.artifacts["openlane_config_overrides"] = merged
            orch.log(
                "OpenLane config overrides staged: "
                + ", ".join(sorted(str(k) for k in config_overrides.keys())),
                refined=True,
            )
        if result.params.get("macro_floorplan_repair"):
            orch.artifacts["macro_floorplan_repair"] = True
            orch.artifacts["macro_halo_override"] = result.params.get("macro_halo", [40, 40])
            orch.artifacts["macro_channel_override"] = result.params.get("macro_channel", [80, 80])
            orch.artifacts["macro_blockages_layer_override"] = result.params.get(
                "macro_blockages_layer",
                "li1 met1 met2 met3 met4 met5",
            )
        nudge_x = result.params.get("macro_placement_nudge_x")
        if nudge_x and hasattr(orch, "_nudge_macro_placements"):
            try:
                orch._nudge_macro_placements(float(nudge_x))
            except Exception as exc:
                orch.log(f"Macro placement nudge skipped: {exc}", refined=True)
        orch.log("Config regeneration requested", refined=True)

    elif result.action == RecoveryAction.RETRY_SAME:
        pass  # Retry with same params

    elif result.action == RecoveryAction.FAIL:
        pass  # Caller sets FAIL state

    return params
