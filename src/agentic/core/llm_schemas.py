import logging
from typing import Any, Dict, Iterable, Optional

from ..contracts import robust_json_extract

logger = logging.getLogger(__name__)


PHYSICAL_ANALYST_REQUIRED_KEYS = ("class", "root_cause", "fix_hint")
PHYSICAL_ANALYST_OPTIONAL_KEYS = {
    "eda_tool": "unknown",
    "failure_type": "unknown",
    "affected_stage": "unknown",
    "suspected_rtl_file": "",
    "suspected_rtl_line": "",
    "suspected_rtl_signal": "",
    "pdk_context": "",
    "recommended_next_action": "",
    "confidence": "low",
}


class OutputParser:
    """Robust parsers for structured VLSI-aware LLM outputs.

    These helpers keep agent responses machine-consumable without removing the
    hardware context needed by downstream recovery logic.
    """

    @staticmethod
    def parse_json(
        raw: str,
        *,
        context: str = "llm_schema",
        required_keys: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """Extract a JSON object from an LLM response string."""
        keys = list(required_keys) if required_keys else None
        data, success = robust_json_extract(raw, context=context, required_keys=keys)
        if success and isinstance(data, dict):
            return data

        logger.warning(
            "[OutputParser] JSON extraction failed for context=%s; returning fallback.",
            context,
        )
        return {
            "class": "C",
            "root_cause": (
                "Failed to parse LLM response as JSON structure. "
                f"Raw start: {str(raw)[:180]}"
            ),
            "fix_hint": "Regenerate the analysis with JSON-only output.",
        }

    @staticmethod
    def parse_physical_analysis(raw: str) -> Dict[str, Any]:
        """Parse and normalize a physical-design failure diagnosis.

        The output schema is intentionally VLSI-aware so the pipeline can decide
        whether to repair RTL, constraints, OpenLane configuration, or simply
        surface a tool/infrastructure issue.
        """
        parsed = OutputParser.parse_json(
            raw,
            context="physical_analyst",
            required_keys=PHYSICAL_ANALYST_REQUIRED_KEYS,
        )
        return OutputParser._normalize_physical_analysis(parsed, raw)

    @staticmethod
    def _normalize_physical_analysis(
        payload: Dict[str, Any], raw: str
    ) -> Dict[str, Any]:
        diagnosis = dict(payload)

        diagnosis_class = str(diagnosis.get("class", "C")).strip().upper()
        if diagnosis_class not in {"A", "B", "C"}:
            diagnosis_class = "C"

        diagnosis["class"] = diagnosis_class
        diagnosis["root_cause"] = str(
            diagnosis.get("root_cause")
            or "Physical analysis could not identify a concrete VLSI root cause."
        ).strip()
        diagnosis["fix_hint"] = str(
            diagnosis.get("fix_hint")
            or "Inspect synthesis, placement/routing, DRC/LVS, and timing logs before retrying."
        ).strip()

        for key, default in PHYSICAL_ANALYST_OPTIONAL_KEYS.items():
            value = diagnosis.get(key, default)
            diagnosis[key] = str(value).strip() if value is not None else default

        diagnosis["vlsi_awareness"] = {
            "schema": "physical_design_failure_v1",
            "class_meaning": {
                "A": "RTL or microarchitecture defect likely caused the EDA failure.",
                "B": "Constraint, PDK, floorplan, or tool-configuration issue likely.",
                "C": "Insufficient evidence, infrastructure issue, or generic tool failure.",
            },
            "expected_domains": [
                "synthesis",
                "sta",
                "floorplan",
                "placement",
                "routing",
                "drc",
                "lvs",
                "pdn",
                "pdk",
                "verilog",
            ],
        }

        diagnosis.setdefault("raw_excerpt", str(raw)[:500])
        return diagnosis
