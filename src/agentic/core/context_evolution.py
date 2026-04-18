"""
Context Evolution
=================

Deterministic multi-agent context evolution inspired by ACE-style iteration.
It lets independent "reviewer" perspectives update the prompt context before
an LLM call, without requiring extra API calls.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ContextEvolution:
    """Structured context digest from multiple deterministic reviewers."""

    stage: str
    constraints: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    retry_directives: List[str] = field(default_factory=list)
    verification_focus: List[str] = field(default_factory=list)
    physical_focus: List[str] = field(default_factory=list)

    def to_prompt(self, max_items: int = 6) -> str:
        sections = []
        for label, values in (
            ("CONSTRAINTS", self.constraints),
            ("RISKS", self.risks),
            ("RETRY DIRECTIVES", self.retry_directives),
            ("VERIFICATION FOCUS", self.verification_focus),
            ("PHYSICAL FOCUS", self.physical_focus),
        ):
            cleaned = [v for v in values if v]
            if cleaned:
                sections.append(label + ":\n" + "\n".join(f"- {v}" for v in cleaned[:max_items]))
        if not sections:
            return ""
        return "CONTEXT EVOLUTION DIGEST:\n" + "\n\n".join(sections)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "constraints": self.constraints,
            "risks": self.risks,
            "retry_directives": self.retry_directives,
            "verification_focus": self.verification_focus,
            "physical_focus": self.physical_focus,
        }


class MultiAgentContextEvolver:
    """Combines architect, verifier, fixer, and physical-review perspectives."""

    def evolve(
        self,
        *,
        stage: str,
        spec: Any = "",
        rtl: str = "",
        error: str = "",
        history: List[str] = None,
        target_pdk: str = "",
        strategy: str = "",
    ) -> ContextEvolution:
        history = history or []
        spec_text = str(spec or "")
        stage_text = (stage or "").lower()
        rtl_text = rtl or ""
        error_text = error or ""
        digest = ContextEvolution(stage=stage)

        digest.constraints.extend(self._architect_constraints(spec_text, target_pdk, strategy))
        digest.risks.extend(self._risk_review(stage_text, spec_text, rtl_text, error_text))
        digest.retry_directives.extend(self._retry_directives(error_text, history))
        digest.verification_focus.extend(self._verification_focus(spec_text, rtl_text, error_text))
        digest.physical_focus.extend(self._physical_focus(stage_text, target_pdk, error_text))

        return self._dedupe(digest)

    @staticmethod
    def _architect_constraints(spec: str, target_pdk: str, strategy: str) -> List[str]:
        constraints = []
        if target_pdk:
            constraints.append(f"Preserve target PDK assumptions for {target_pdk}; do not silently switch libraries or voltage domains.")
        if strategy:
            constraints.append(f"Keep implementation consistent with current strategy: {strategy}.")
        if re.search(r"\bfifo|queue|buffer\b", spec, re.I):
            constraints.append("FIFO-like behavior requires exact full/empty, ordering, and reset semantics.")
        if re.search(r"\buart|spi|i2c|axi|apb|wishbone\b", spec, re.I):
            constraints.append("Protocol designs must preserve handshake timing and backpressure behavior.")
        if re.search(r"\b(cpu|pipeline|alu|cache|branch)\b", spec, re.I):
            constraints.append("Datapath/control split should be explicit; avoid long unregistered decode-to-execute paths.")
        return constraints

    @staticmethod
    def _risk_review(stage: str, spec: str, rtl: str, error: str) -> List[str]:
        risks = []
        combined = " ".join([stage, spec, rtl[:4000], error])
        if re.search(r"width|trunc|extend|signed|unsigned", combined, re.I):
            risks.append("Width/sign fixes can change arithmetic semantics; size operands explicitly and preserve wrap/saturation intent.")
        if re.search(r"latch|always_comb|not assigned", combined, re.I):
            risks.append("Combinational fixes must assign defaults before branches to avoid latch inference.")
        if re.search(r"multiple driver|driven.*multiple|conflict", combined, re.I):
            risks.append("Driver conflicts require one ownership point per signal, not masking with extra wires.")
        if re.search(r"clock|cdc|domain|async", combined, re.I):
            risks.append("Clock-domain fixes must not independently synchronize multi-bit payloads.")
        if "physical" in stage or "timing" in stage:
            risks.append("Physical/timing fixes must preserve interface latency unless downstream contracts are updated.")
        return risks

    @staticmethod
    def _retry_directives(error: str, history: List[str]) -> List[str]:
        directives = []
        repeated = {}
        for item in history[-12:]:
            key = re.sub(r"\d+", "#", item[:160])
            repeated[key] = repeated.get(key, 0) + 1
        if any(count >= 2 for count in repeated.values()):
            directives.append("A similar failure repeated; choose a materially different fix path and state what changed.")
        if error:
            directives.append("Use the latest error as ground truth; avoid broad rewrites unless the local fix cannot preserve behavior.")
        if re.search(r"None|empty|no result|format", error, re.I):
            directives.append("If the issue is LLM output format, demand fenced code or strict JSON and reject prose.")
        return directives

    @staticmethod
    def _verification_focus(spec: str, rtl: str, error: str) -> List[str]:
        focus = []
        combined = " ".join([spec, rtl[:3000], error])
        if re.search(r"\breset|rst_n\b", combined, re.I):
            focus.append("Check reset convergence and all registered outputs after reset release.")
        if re.search(r"\bvalid|ready|req|ack|grant\b", combined, re.I):
            focus.append("Cover and assert handshake progress, no dropped transactions, and no duplicate acknowledgements.")
        if re.search(r"\bfifo|queue\b", combined, re.I):
            focus.append("Assert no underflow/overflow and preserve first-in-first-out ordering.")
        if re.search(r"\bstate|fsm\b", combined, re.I):
            focus.append("Assert legal FSM states and defined transitions for unexpected inputs.")
        return focus

    @staticmethod
    def _physical_focus(stage: str, target_pdk: str, error: str) -> List[str]:
        focus = []
        if target_pdk:
            focus.append(f"Use {target_pdk} timing/area limits when judging feasibility.")
        if re.search(r"slack|wns|tns|timing", " ".join([stage, error]), re.I):
            focus.append("Prefer pipelining, fanout reduction, and simpler critical paths over constraint relaxation.")
        if re.search(r"congestion|overflow|route", " ".join([stage, error]), re.I):
            focus.append("Reduce placement pressure by lowering utilization, simplifying high-fanout nets, or preserving hierarchy.")
        if re.search(r"drc|lvs", " ".join([stage, error]), re.I):
            focus.append("Treat DRC/LVS as signoff blockers; fixes should be reproducible in generated scripts/config.")
        return focus

    @staticmethod
    def _dedupe(digest: ContextEvolution) -> ContextEvolution:
        for attr in ("constraints", "risks", "retry_directives", "verification_focus", "physical_focus"):
            seen = set()
            values = []
            for item in getattr(digest, attr):
                key = item.lower()
                if key not in seen:
                    seen.add(key)
                    values.append(item)
            setattr(digest, attr, values)
        return digest
