"""
Self-Reflect Pipeline — Autonomous Retry with Reflection
==========================================================

Implements the "Self-Reflect and Retry" pattern for the AgentIC pipeline.

When OpenLane synthesis/hardening fails, the system:
1. Captures the failure log and error category
2. Reflects on WHY it failed (structured root-cause analysis)
3. Generates a corrective action plan
4. Applies the fix and retries (up to 5 times)
5. Tracks a convergence history to avoid repeating failed approaches

The reflection uses gradient feedback from:
  - Synthesis timing (WNS/TNS)
  - Area utilization
  - DRC/LVS violations
  - Routing congestion
  - Formal property results
"""

import os
import re
import json
import time
import hashlib
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


# ─── Data Structures ─────────────────────────────────────────────────

class FailureCategory(Enum):
    """Categories of failures the self-reflect pipeline handles."""
    SYNTAX_ERROR = "syntax_error"
    SIMULATION_FAIL = "simulation_fail"
    FORMAL_PROPERTY_FAIL = "formal_property_fail"
    SYNTHESIS_ERROR = "synthesis_error"
    TIMING_VIOLATION = "timing_violation"
    ROUTING_CONGESTION = "routing_congestion"
    DRC_VIOLATION = "drc_violation"
    LVS_MISMATCH = "lvs_mismatch"
    AREA_OVERFLOW = "area_overflow"
    POWER_VIOLATION = "power_violation"
    UNKNOWN = "unknown"


@dataclass
class FailureAnalysis:
    """Structured analysis of a pipeline failure."""
    category: FailureCategory
    error_message: str
    root_cause: str            # Identified root cause
    impact: str                # What downstream effects this has
    similar_past_failures: int  # How many similar failures we've seen
    is_repeating: bool         # Are we stuck in a loop?


@dataclass
class CorrectionAction:
    """A fix action to attempt."""
    action_type: str           # "modify_rtl", "adjust_config", "relax_constraints", "pivot_strategy"
    description: str
    target_file: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReflectionEntry:
    """A single self-reflection cycle."""
    attempt: int
    failure: FailureAnalysis
    reflection: str            # The agent's reasoning about the failure
    proposed_actions: List[CorrectionAction]
    outcome: str = ""          # "fixed" | "partial" | "failed" | "worse"
    timestamp: float = field(default_factory=time.time)
    metrics_before: Dict[str, Any] = field(default_factory=dict)
    metrics_after: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConvergenceMetrics:
    """Metrics tracked across retry iterations for convergence analysis."""
    wns: float = 0.0           # Worst Negative Slack (ns)
    tns: float = 0.0           # Total Negative Slack (ns)
    area_um2: float = 0.0
    power_w: float = 0.0
    congestion_pct: float = 0.0
    drc_count: int = 0
    lvs_ok: bool = False
    formal_pass: bool = False
    sim_pass: bool = False

    def is_improving(self, previous: "ConvergenceMetrics") -> bool:
        """Check if metrics are trending in the right direction."""
        improvements = 0
        regressions = 0
        
        if self.wns > previous.wns:
            improvements += 1
        elif self.wns < previous.wns:
            regressions += 1
        
        if self.drc_count < previous.drc_count:
            improvements += 1
        elif self.drc_count > previous.drc_count:
            regressions += 1
        
        if self.congestion_pct < previous.congestion_pct:
            improvements += 1
        elif self.congestion_pct > previous.congestion_pct:
            regressions += 1
            
        if self.sim_pass and not previous.sim_pass:
            improvements += 1
        if self.formal_pass and not previous.formal_pass:
            improvements += 1

        return improvements > regressions

    def to_dict(self) -> dict:
        return asdict(self)


# ─── Reflection Prompt Templates ─────────────────────────────────────

SELF_REFLECT_PROMPT = """\
You are a Self-Reflecting VLSI Agent. A pipeline stage has FAILED.

Your job is to:
1. ANALYZE the failure — identify the root cause
2. REFLECT on whether this is a repeating pattern
3. PROPOSE concrete corrective actions
4. ASSESS the risk of each action

FAILURE CONTEXT:
  Category: {category}
  Error: {error_message}
  Attempt: {attempt}/{max_attempts}
  
CONVERGENCE HISTORY:
{convergence_history}

PREVIOUS REFLECTIONS (do NOT repeat the same fix):
{previous_reflections}

CURRENT RTL SUMMARY:
{rtl_summary}

Respond in this EXACT format:
ROOT_CAUSE: <one sentence>
REFLECTION: <2-3 sentences about what went wrong and why>
ACTION_1: <type>|<description>|<target_file>
ACTION_2: <type>|<description>|<target_file>
RISK_ASSESSMENT: <one sentence about what could go wrong with these fixes>
CONVERGENCE_TREND: IMPROVING | STAGNATING | DIVERGING
"""


# ─── Self-Reflect Pipeline ───────────────────────────────────────────

class SelfReflectPipeline:
    """
    Self-reflection retry pipeline for OpenLane synthesis convergence.
    
    When any stage fails, the pipeline:
    1. Categorizes the failure
    2. Reflects on root cause (using LLM)
    3. Proposes and applies corrective actions
    4. Retries up to max_retries times
    5. Tracks convergence to detect stagnation
    
    The reflection history prevents the agent from repeating the same
    failed approach — each retry must try something different.
    """

    def __init__(
        self,
        llm,
        max_retries: int = 5,
        verbose: bool = False,
        on_reflection: Optional[Callable] = None,  # Callback for UI events
    ):
        self.llm = llm
        self.max_retries = max_retries
        self.verbose = verbose
        self.on_reflection = on_reflection  # Optional event sink

        self.reflections: List[ReflectionEntry] = []
        self.convergence_history: List[ConvergenceMetrics] = []
        self.failure_fingerprints: Dict[str, int] = {}

    def run_with_retry(
        self,
        stage_name: str,
        action_fn: Callable[[], Tuple[bool, str, Dict[str, Any]]],
        fix_fn: Callable[[CorrectionAction], bool],
        rtl_summary: str = "",
    ) -> Tuple[bool, str, List[ReflectionEntry]]:
        """
        Execute a pipeline stage with self-reflective retry.
        
        Args:
            stage_name:   Human-readable stage name (e.g., "OpenLane Hardening")
            action_fn:    The stage function. Returns (success, error_msg, metrics_dict)
            fix_fn:       Function that applies a CorrectionAction. Returns True if applied
            rtl_summary:  Current RTL code summary for context
            
        Returns:
            (success, final_message, reflection_history)
        """
        logger.info(f"[SelfReflect] Starting {stage_name} with up to {self.max_retries} retries")

        for attempt in range(1, self.max_retries + 1):
            logger.info(f"[SelfReflect] {stage_name} attempt {attempt}/{self.max_retries}")

            # Execute the stage
            try:
                success, error_msg, metrics = action_fn()
            except Exception as e:
                success = False
                error_msg = f"Stage exception: {str(e)}"
                metrics = {}

            # Track metrics
            cm = self._parse_metrics(metrics)
            self.convergence_history.append(cm)

            if success:
                logger.info(f"[SelfReflect] {stage_name} PASSED on attempt {attempt}")
                return True, f"Passed on attempt {attempt}", self.reflections

            # Check for repeating failure
            fp = self._fingerprint(error_msg)
            self.failure_fingerprints[fp] = self.failure_fingerprints.get(fp, 0) + 1
            is_repeating = self.failure_fingerprints[fp] >= 2

            # Categorize failure
            category = self._categorize_failure(error_msg)
            analysis = FailureAnalysis(
                category=category,
                error_message=error_msg[:2000],
                root_cause="",
                impact="",
                similar_past_failures=self.failure_fingerprints[fp],
                is_repeating=is_repeating,
            )

            # Self-reflect
            reflection_entry = self._reflect(
                analysis, attempt, rtl_summary
            )
            self.reflections.append(reflection_entry)

            # Emit event for UI
            if self.on_reflection:
                try:
                    self.on_reflection({
                        "type": "self_reflection",
                        "stage": stage_name,
                        "attempt": attempt,
                        "category": category.value,
                        "reflection": reflection_entry.reflection,
                        "actions": [a.description for a in reflection_entry.proposed_actions],
                    })
                except Exception:
                    pass

            # Check convergence — if diverging after 3+ attempts, abort early
            if attempt >= 3 and self._is_diverging():
                logger.warning(f"[SelfReflect] Convergence diverging after {attempt} attempts — aborting")
                return False, f"Diverging after {attempt} attempts — aborting", self.reflections

            # Apply corrective actions
            applied_any = False
            for action in reflection_entry.proposed_actions:
                try:
                    if fix_fn(action):
                        applied_any = True
                        logger.info(f"[SelfReflect] Applied fix: {action.description}")
                except Exception as e:
                    logger.warning(f"[SelfReflect] Fix failed: {action.description}: {e}")

            if not applied_any:
                logger.warning(f"[SelfReflect] No fixes could be applied on attempt {attempt}")

        return False, f"Failed after {self.max_retries} attempts", self.reflections

    def _categorize_failure(self, error_msg: str) -> FailureCategory:
        """Categorize a failure based on error message patterns."""
        msg = error_msg.lower()

        patterns = [
            (r"syntax error|parse error|unexpected token", FailureCategory.SYNTAX_ERROR),
            (r"test failed|simulation.*fail|mismatch", FailureCategory.SIMULATION_FAIL),
            (r"assert.*fail|property.*fail|formal.*fail", FailureCategory.FORMAL_PROPERTY_FAIL),
            (r"synthesis.*error|synth.*fail|yosys.*error", FailureCategory.SYNTHESIS_ERROR),
            (r"timing|slack|wns|tns|setup.*violation|hold.*violation", FailureCategory.TIMING_VIOLATION),
            (r"congestion|overflow|routing.*fail", FailureCategory.ROUTING_CONGESTION),
            (r"drc.*violation|design rule", FailureCategory.DRC_VIOLATION),
            (r"lvs.*mismatch|layout.*vs.*schematic", FailureCategory.LVS_MISMATCH),
            (r"area.*overflow|die.*area|utilization.*exceed", FailureCategory.AREA_OVERFLOW),
            (r"power.*violation|power.*exceed|ir.*drop", FailureCategory.POWER_VIOLATION),
        ]

        for pattern, category in patterns:
            if re.search(pattern, msg):
                return category

        return FailureCategory.UNKNOWN

    def _reflect(
        self,
        analysis: FailureAnalysis,
        attempt: int,
        rtl_summary: str,
    ) -> ReflectionEntry:
        """Run LLM self-reflection on the failure."""
        # Build convergence history string
        conv_lines = []
        for i, cm in enumerate(self.convergence_history):
            conv_lines.append(
                f"  [{i+1}] WNS={cm.wns:.3f}ns DRC={cm.drc_count} "
                f"cong={cm.congestion_pct:.1f}% sim={'PASS' if cm.sim_pass else 'FAIL'}"
            )
        conv_str = "\n".join(conv_lines[-5:]) or "  No history yet"

        # Build previous reflections string
        prev_lines = []
        for r in self.reflections[-3:]:
            prev_lines.append(
                f"  [Attempt {r.attempt}] {r.failure.category.value}: "
                f"{r.reflection[:100]}... → {r.outcome}"
            )
        prev_str = "\n".join(prev_lines) or "  No previous reflections"

        prompt = SELF_REFLECT_PROMPT.format(
            category=analysis.category.value,
            error_message=analysis.error_message[:1500],
            attempt=attempt,
            max_attempts=self.max_retries,
            convergence_history=conv_str,
            previous_reflections=prev_str,
            rtl_summary=rtl_summary[:3000],
        )

        # Call LLM for reflection
        try:
            from crewai import Agent, Task, Crew

            agent = Agent(
                role="Self-Reflecting VLSI Agent",
                goal="Analyze the failure and propose corrective actions",
                backstory=(
                    "You are an expert at diagnosing ASIC design failures. "
                    "You analyze error patterns, identify root causes, and propose "
                    "targeted fixes. You never repeat a fix that already failed."
                ),
                llm=self.llm,
                verbose=self.verbose,
            )

            task = Task(
                description=prompt,
                expected_output="ROOT_CAUSE, REFLECTION, ACTION_1, ACTION_2, RISK_ASSESSMENT, CONVERGENCE_TREND",
                agent=agent,
            )

            raw = str(Crew(agents=[agent], tasks=[task]).kickoff())
            return self._parse_reflection(raw, analysis, attempt)

        except Exception as e:
            logger.warning(f"[SelfReflect] LLM reflection failed: {e}")
            return self._fallback_reflection(analysis, attempt)

    def _parse_reflection(
        self, raw: str, analysis: FailureAnalysis, attempt: int
    ) -> ReflectionEntry:
        """Parse LLM reflection response."""
        # Extract root cause
        m = re.search(r'ROOT_CAUSE\s*:\s*(.+?)(?:\n|$)', raw, re.IGNORECASE)
        root_cause = m.group(1).strip() if m else "Unknown"
        analysis.root_cause = root_cause

        # Extract reflection
        m = re.search(r'REFLECTION\s*:\s*(.+?)(?=ACTION|\Z)', raw,
                       re.DOTALL | re.IGNORECASE)
        reflection = m.group(1).strip() if m else "Analysis inconclusive"

        # Extract actions
        actions: List[CorrectionAction] = []
        for i in (1, 2, 3):
            m = re.search(rf'ACTION_{i}\s*:\s*(.+?)(?:\n|$)', raw, re.IGNORECASE)
            if m:
                parts = m.group(1).strip().split("|")
                action_type = parts[0].strip() if len(parts) > 0 else "modify_rtl"
                desc = parts[1].strip() if len(parts) > 1 else parts[0].strip()
                target = parts[2].strip() if len(parts) > 2 else ""
                actions.append(CorrectionAction(
                    action_type=action_type,
                    description=desc,
                    target_file=target,
                ))

        if not actions:
            # Fallback: generate a default action based on category
            actions = self._default_actions(analysis.category)

        return ReflectionEntry(
            attempt=attempt,
            failure=analysis,
            reflection=reflection,
            proposed_actions=actions,
        )

    def _fallback_reflection(
        self, analysis: FailureAnalysis, attempt: int
    ) -> ReflectionEntry:
        """Generate fallback reflection when LLM is unavailable."""
        actions = self._default_actions(analysis.category)
        return ReflectionEntry(
            attempt=attempt,
            failure=analysis,
            reflection=f"Fallback reflection: {analysis.category.value} detected",
            proposed_actions=actions,
        )

    def _default_actions(self, category: FailureCategory) -> List[CorrectionAction]:
        """Generate default corrective actions based on failure category."""
        defaults = {
            FailureCategory.SYNTAX_ERROR: [
                CorrectionAction("modify_rtl", "Fix Verilog syntax errors"),
            ],
            FailureCategory.SIMULATION_FAIL: [
                CorrectionAction("modify_rtl", "Fix RTL logic to match expected behavior"),
                CorrectionAction("modify_rtl", "Adjust testbench timing and reset sequence"),
            ],
            FailureCategory.TIMING_VIOLATION: [
                CorrectionAction("adjust_config", "Increase clock period"),
                CorrectionAction("modify_rtl", "Pipeline critical path"),
            ],
            FailureCategory.ROUTING_CONGESTION: [
                CorrectionAction("adjust_config", "Reduce utilization target"),
                CorrectionAction("adjust_config", "Increase die area by 20%"),
            ],
            FailureCategory.DRC_VIOLATION: [
                CorrectionAction("adjust_config", "Reduce placement density"),
                CorrectionAction("adjust_config", "Enable DRC repair scripts"),
            ],
            FailureCategory.AREA_OVERFLOW: [
                CorrectionAction("adjust_config", "Increase die area"),
                CorrectionAction("modify_rtl", "Reduce design complexity"),
            ],
        }
        return defaults.get(category, [
            CorrectionAction("modify_rtl", "General RTL fix based on error log"),
        ])

    def _parse_metrics(self, metrics: Dict[str, Any]) -> ConvergenceMetrics:
        """Parse raw metrics dict into ConvergenceMetrics."""
        return ConvergenceMetrics(
            wns=float(metrics.get("wns", 0)),
            tns=float(metrics.get("tns", 0)),
            area_um2=float(metrics.get("area_um2", 0)),
            power_w=float(metrics.get("power_w", 0)),
            congestion_pct=float(metrics.get("congestion_pct", 0)),
            drc_count=int(metrics.get("drc_count", 0)),
            lvs_ok=bool(metrics.get("lvs_ok", False)),
            formal_pass=bool(metrics.get("formal_pass", False)),
            sim_pass=bool(metrics.get("sim_pass", False)),
        )

    def _fingerprint(self, error_msg: str) -> str:
        """Generate a fingerprint for deduplicating errors."""
        # Normalize: remove numbers, paths, timestamps
        normalized = re.sub(r'\d+', 'N', error_msg[:500])
        normalized = re.sub(r'/[\w/]+\.', 'FILE.', normalized)
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _is_diverging(self) -> bool:
        """Check if the convergence history shows divergence (getting worse)."""
        if len(self.convergence_history) < 3:
            return False
        
        recent = self.convergence_history[-3:]
        
        # Check if DRC count is increasing
        if all(recent[i].drc_count >= recent[i-1].drc_count 
               for i in range(1, len(recent))) and recent[-1].drc_count > 0:
            return True
        
        # Check if WNS is getting worse
        if all(recent[i].wns <= recent[i-1].wns 
               for i in range(1, len(recent))) and recent[-1].wns < -1.0:
            return True

        return False

    def get_summary(self) -> str:
        """Get a human-readable summary of the reflection history."""
        if not self.reflections:
            return "No reflections recorded."

        lines = [f"Self-Reflection Summary ({len(self.reflections)} attempts):"]
        for r in self.reflections:
            lines.append(
                f"  [{r.attempt}] {r.failure.category.value}: {r.reflection[:80]}... "
                f"→ {r.outcome or 'pending'}"
            )
        
        if self.convergence_history:
            last = self.convergence_history[-1]
            lines.append(
                f"  Latest metrics: WNS={last.wns:.3f} DRC={last.drc_count} "
                f"cong={last.congestion_pct:.1f}%"
            )

        return "\n".join(lines)
