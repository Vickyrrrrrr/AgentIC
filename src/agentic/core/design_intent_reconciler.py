"""
Design Intent Reconciler
========================

Converts feasibility blockers into the closest PDK-feasible implementation
intent.  This is deterministic by design: the agent may change specs when
the requested chip cannot be implemented directly in the selected RTL-to-GDS
flow, but each change is recorded for the CLI/API/UI.
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass
class IntentChange:
    original_request: str
    constraint: str
    chosen_substitute: str
    user_explanation: str
    target: str = ""
    category: str = "AUTO_REPAIRABLE"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReconciliationResult:
    spec: Dict[str, Any]
    hierarchy: Optional[Dict[str, Any]]
    changes: List[IntentChange] = field(default_factory=list)
    unresolved_blockers: List[str] = field(default_factory=list)
    changed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spec": self.spec,
            "hierarchy": self.hierarchy,
            "changes": [c.to_dict() for c in self.changes],
            "unresolved_blockers": list(self.unresolved_blockers),
            "changed": self.changed,
        }


_ANALOG_REPLACEMENTS = {
    "adc": "sampled_data_macro",
    "dac": "drive_data_macro",
    "pll": "clock_macro",
    "phase-locked loop": "clock_macro",
    "analog": "hard_macro",
    "voltage reference": "reference_macro",
    "bandgap": "reference_macro",
    "ldo": "power_macro",
    "oscillator": "clock_macro",
    "trng": "entropy_macro",
}

_ANALOG_PATTERN = re.compile(
    r"\b(adc|dac|pll|phase-locked loop|analog|voltage reference|bandgap|ldo|oscillator|trng)\b",
    re.IGNORECASE,
)


class DesignIntentReconciler:
    """Repair a spec to stay as close as possible to user intent."""

    def __init__(self, pdk_profile: Dict[str, Any]):
        self.pdk_profile = pdk_profile or {}
        self.pdk_name = str(
            self.pdk_profile.get("profile")
            or self.pdk_profile.get("pdk")
            or "selected PDK"
        )

    def reconcile(
        self,
        *,
        original_prompt: str,
        sid: Any,
        hw_spec_dict: Dict[str, Any],
        hierarchy_result_dict: Optional[Dict[str, Any]],
        feasibility_result: Any,
        macro_manifest_path: str = "",
    ) -> ReconciliationResult:
        spec = copy.deepcopy(hw_spec_dict or {})
        hierarchy = copy.deepcopy(hierarchy_result_dict) if hierarchy_result_dict else None
        changes: List[IntentChange] = []
        unresolved: List[str] = []
        feasibility = self._as_dict(feasibility_result)
        issues = feasibility.get("feasibility_issues") or []

        self._apply_frequency_repair(spec, feasibility, changes)
        self._apply_memory_macro_repair(spec, hierarchy, feasibility, changes)
        self._apply_tristate_repair(spec, hierarchy, issues, changes)
        self._apply_analog_macro_repair(spec, hierarchy, issues, changes)

        for issue in issues:
            category = str(issue.get("category", ""))
            message = str(issue.get("message", ""))
            if category == "UNSUPPORTED" and message:
                unresolved.append(message)

        result = ReconciliationResult(
            spec=spec,
            hierarchy=hierarchy,
            changes=changes,
            unresolved_blockers=unresolved,
            changed=bool(changes),
        )
        return result

    @staticmethod
    def _as_dict(value: Any) -> Dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if hasattr(value, "to_dict"):
            return value.to_dict()
        return {}

    def _apply_frequency_repair(
        self, spec: Dict[str, Any], feasibility: Dict[str, Any], changes: List[IntentChange]
    ) -> None:
        if not feasibility.get("frequency_was_adjusted"):
            return
        current = spec.get("target_frequency_mhz")
        recommended = feasibility.get("recommended_frequency_mhz") or feasibility.get(
            "target_frequency_mhz"
        )
        if not recommended or current == recommended:
            return
        spec["target_frequency_mhz"] = recommended
        changes.append(
            IntentChange(
                target="target_frequency_mhz",
                original_request=f"{current} MHz",
                constraint=f"{self.pdk_name} frequency limit",
                chosen_substitute=f"{recommended} MHz",
                user_explanation=(
                    f"The requested clock target was above this PDK profile's reliable "
                    f"range, so AgentIC lowered it to {recommended} MHz."
                ),
            )
        )

    def _apply_memory_macro_repair(
        self,
        spec: Dict[str, Any],
        hierarchy: Optional[Dict[str, Any]],
        feasibility: Dict[str, Any],
        changes: List[IntentChange],
    ) -> None:
        for macro in feasibility.get("memory_macros_required") or []:
            name = str(macro.get("submodule_name", "memory_macro"))
            width = int(macro.get("width_bits") or 0)
            depth = int(macro.get("depth_words") or 0)
            replacement = (
                f"external memory macro wrapper with {width} bit data and {depth} words"
                if width and depth
                else "external memory macro wrapper"
            )
            changed = self._rewrite_named_submodule(
                spec,
                hierarchy,
                name,
                lambda sm: self._mark_memory_macro(sm, width, depth),
            )
            if changed:
                changes.append(
                    IntentChange(
                        target=name,
                        category="REQUIRES_MACRO",
                        original_request=f"synthesized memory/register array in {name}",
                        constraint="Large memories should not be implemented as flip-flop arrays.",
                        chosen_substitute=replacement,
                        user_explanation=(
                            f"AgentIC kept the memory behavior but changed {name} into a "
                            f"macro-facing wrapper so the synthesized RTL stays feasible."
                        ),
                    )
                )

    def _mark_memory_macro(self, sm: Dict[str, Any], width: int, depth: int) -> None:
        sm["description"] = (
            f"External memory macro wrapper. Keep synthesized RTL to address/control, "
            f"chip-enable, write-enable, and read/write data interface logic only. "
            f"Macro dimensions: width={width}, depth={depth}. Do not synthesize storage bits."
        )
        sm["requires_macro"] = True
        sm["macro_kind"] = "memory"
        if width:
            sm["macro_width_bits"] = width
        if depth:
            sm["macro_depth_words"] = depth

    def _apply_tristate_repair(
        self,
        spec: Dict[str, Any],
        hierarchy: Optional[Dict[str, Any]],
        issues: Iterable[Dict[str, Any]],
        changes: List[IntentChange],
    ) -> None:
        top_ports = {str(p.get("name", "")) for p in spec.get("ports", [])}
        targets = {
            str(i.get("target", ""))
            for i in issues
            if i.get("code") == "INTERNAL_TRISTATE"
        }
        changed_targets: List[str] = []
        for sm in self._all_submodules(spec, hierarchy):
            ports = sm.get("ports") or []
            new_ports = []
            changed = False
            for port in ports:
                if str(port.get("direction", "")).lower() != "inout":
                    new_ports.append(port)
                    continue
                pname = str(port.get("name", "bus"))
                full_target = f"{sm.get('name')}.{pname}"
                if pname in top_ports and full_target not in targets:
                    new_ports.append(port)
                    continue
                base = self._clean_identifier(pname)
                dtype = port.get("data_type") or self._width_to_dtype(port.get("width"))
                desc = port.get("description", "Split internal bidirectional signal")
                new_ports.extend(
                    [
                        {
                            "name": f"{base}_i",
                            "direction": "input",
                            "data_type": dtype,
                            "description": f"{desc} input side",
                        },
                        {
                            "name": f"{base}_o",
                            "direction": "output",
                            "data_type": dtype,
                            "description": f"{desc} output side",
                        },
                        {
                            "name": f"{base}_oe",
                            "direction": "output",
                            "data_type": "logic",
                            "description": f"{desc} output-enable side",
                        },
                    ]
                )
                changed = True
                changed_targets.append(full_target)
            if changed:
                sm["ports"] = new_ports
                sm["description"] = (
                    f"{sm.get('description', '')} Internal bidirectional buses were "
                    f"rewritten as explicit mux/demux signals for ASIC synthesis."
                ).strip()

        for target in sorted(set(changed_targets)):
            changes.append(
                IntentChange(
                    target=target,
                    original_request="internal bidirectional/inout bus",
                    constraint="Standard-cell ASIC synthesis does not support internal tri-states.",
                    chosen_substitute="separate *_i, *_o, and *_oe signals with mux/demux logic",
                    user_explanation=(
                        f"AgentIC split {target} into explicit input, output, and "
                        f"output-enable signals."
                    ),
                )
            )

    def _apply_analog_macro_repair(
        self,
        spec: Dict[str, Any],
        hierarchy: Optional[Dict[str, Any]],
        issues: Iterable[Dict[str, Any]],
        changes: List[IntentChange],
    ) -> None:
        if not any(i.get("code") == "ANALOG_OR_CUSTOM_BLOCK" for i in issues):
            return

        spec["design_description"] = self._rewrite_analog_text(
            str(spec.get("design_description", ""))
        )
        top_name = str(spec.get("top_module_name", ""))
        changed_names: List[Tuple[str, str]] = []
        for sm in self._all_submodules(spec, hierarchy):
            text = f"{sm.get('name', '')} {sm.get('description', '')}"
            if not _ANALOG_PATTERN.search(text):
                continue
            old_name = str(sm.get("name", "hard_macro_if"))
            if old_name == top_name:
                sm["description"] = self._rewrite_analog_text(str(sm.get("description", "")))
                continue
            new_name = self._rewrite_analog_name(old_name)
            sm["name"] = new_name
            sm["description"] = (
                "Digital hard-macro interface wrapper. Expose only control, status, "
                "sample/data, enable, ready, interrupt, and health signals in RTL. "
                "Do not implement transistor-level or custom-layout internals."
            )
            sm["requires_macro"] = True
            sm["macro_kind"] = "custom_or_mixed_signal"
            changed_names.append((old_name, new_name))

        for old_name, new_name in changed_names:
            changes.append(
                IntentChange(
                    target=old_name,
                    original_request=f"direct implementation of {old_name}",
                    constraint=(
                        f"{old_name} needs custom or hard-macro collateral and cannot "
                        f"be synthesized directly in the {self.pdk_name} RTL-to-GDS flow."
                    ),
                    chosen_substitute=f"{new_name} digital hard-macro wrapper",
                    user_explanation=(
                        f"AgentIC kept the user-visible function but changed {old_name} "
                        f"to a digital wrapper that can be integrated with a supplied macro."
                    ),
                )
            )

    def _rewrite_named_submodule(
        self,
        spec: Dict[str, Any],
        hierarchy: Optional[Dict[str, Any]],
        name: str,
        update_fn,
    ) -> bool:
        changed = False
        for sm in self._all_submodules(spec, hierarchy):
            if str(sm.get("name", "")) == name:
                update_fn(sm)
                changed = True
        return changed

    def _all_submodules(
        self, spec: Dict[str, Any], hierarchy: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for key in ("submodules", "sub_modules"):
            for sm in spec.get(key, []) or []:
                if isinstance(sm, dict):
                    out.append(sm)
        if hierarchy:
            for sm in hierarchy.get("submodules", []) or []:
                self._collect_hierarchy_submodules(sm, out)
        return out

    def _collect_hierarchy_submodules(self, sm: Dict[str, Any], out: List[Dict[str, Any]]) -> None:
        if not isinstance(sm, dict):
            return
        out.append(sm)
        nested = sm.get("nested_spec")
        if isinstance(nested, dict):
            for child in nested.get("submodules", []) or []:
                self._collect_hierarchy_submodules(child, out)

    @staticmethod
    def _width_to_dtype(width: Any) -> str:
        try:
            width_i = int(str(width))
        except (TypeError, ValueError):
            width_i = 1
        if width_i <= 1:
            return "logic"
        return f"logic [{width_i - 1}:0]"

    @staticmethod
    def _clean_identifier(name: str) -> str:
        cleaned = re.sub(r"\W+", "_", name).strip("_")
        return cleaned or "bus"

    def _rewrite_analog_text(self, text: str) -> str:
        if not text:
            return text
        return _ANALOG_PATTERN.sub(lambda m: _ANALOG_REPLACEMENTS[m.group(1).lower()], text)

    def _rewrite_analog_name(self, name: str) -> str:
        rewritten = name.lower()
        for keyword, replacement in _ANALOG_REPLACEMENTS.items():
            rewritten = rewritten.replace(keyword.replace(" ", "_"), replacement)
            rewritten = rewritten.replace(keyword, replacement)
        rewritten = self._clean_identifier(rewritten)
        if not rewritten.endswith("_if") and not rewritten.endswith("_wrapper"):
            rewritten = f"{rewritten}_if"
        return rewritten


def write_reconciliation_artifacts(
    root_dir: str,
    result: ReconciliationResult,
) -> Dict[str, str]:
    os.makedirs(root_dir, exist_ok=True)
    spec_path = os.path.join(root_dir, "reconciled_spec.json")
    change_path = os.path.join(root_dir, "spec_change_log.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(result.spec, f, indent=2)
    with open(change_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "changes": [c.to_dict() for c in result.changes],
                "unresolved_blockers": result.unresolved_blockers,
            },
            f,
            indent=2,
        )
    return {
        "reconciled_spec_path": spec_path,
        "spec_change_log_path": change_path,
    }
