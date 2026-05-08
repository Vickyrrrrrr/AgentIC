import enum
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class FailureClass(str, enum.Enum):
    EDA_TOOL_ERROR = "EDA_TOOL_ERROR"
    LLM_FORMAT_ERROR = "LLM_FORMAT_ERROR"
    LLM_SEMANTIC_ERROR = "LLM_SEMANTIC_ERROR"
    ORCHESTRATOR_ROUTING_ERROR = "ORCHESTRATOR_ROUTING_ERROR"
    RETRY_BUDGET_ERROR = "RETRY_BUDGET_ERROR"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"
    UNKNOWN = "UNKNOWN"


class StageStatus(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    RETRY = "RETRY"
    SKIP = "SKIP"
    ERROR = "ERROR"


@dataclass
class ArtifactRef:
    key: str
    producer: str
    consumer: str = ""
    required: bool = False
    blocking: bool = False
    value: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FailureRecord:
    failure_class: FailureClass
    producer: str
    message: str
    diagnostics: List[str] = field(default_factory=list)
    raw_excerpt: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["failure_class"] = self.failure_class.value
        return data


@dataclass
class AgentResult:
    agent: str
    ok: bool
    producer: str
    payload: Dict[str, Any] = field(default_factory=dict)
    diagnostics: List[str] = field(default_factory=list)
    failure_class: FailureClass = FailureClass.UNKNOWN
    raw_output: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["failure_class"] = self.failure_class.value
        return data


@dataclass
class StageResult:
    stage: str
    status: StageStatus
    producer: str
    failure_class: FailureClass = FailureClass.UNKNOWN
    consumable_payload: Dict[str, Any] = field(default_factory=dict)
    diagnostics: List[str] = field(default_factory=list)
    artifacts_written: List[str] = field(default_factory=list)
    next_action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["failure_class"] = self.failure_class.value
        return data


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# Global debug directory for failed JSON parses
_DEBUG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "agentic-workspace", "debug", "json_fails"
)
_PARSE_STATS = {"success": 0, "failure": 0}


def _ensure_debug_dir() -> str:
    """Ensure debug directory exists."""
    try:
        os.makedirs(_DEBUG_DIR, exist_ok=True)
    except OSError:
        pass
    return _DEBUG_DIR


def _log_json_failure(raw: str, context: str, reason: str) -> None:
    """Log failed JSON parse attempt to debug file."""
    try:
        debug_dir = _ensure_debug_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{context}.json"
        filepath = os.path.join(debug_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "timestamp": datetime.now().isoformat(),
                    "context": context,
                    "failure_reason": reason,
                    "raw_output": raw[:10000],  # Limit size
                },
                f,
                indent=2,
            )

        logger.debug(f"JSON parse failure logged to {filepath}")
    except Exception:
        pass  # Never let logging crash the build

    _PARSE_STATS["failure"] += 1


def _deep_clean_keys(obj: Any) -> Any:
    """Recursively strip literal quotes and whitespace from dictionary keys."""
    if isinstance(obj, dict):
        new_dict = {}
        for k, v in obj.items():
            new_key = k
            if isinstance(k, str):
                # Strip whitespace and literal double quotes from both ends
                new_key = k.strip().strip('"').strip("'")
            new_dict[new_key] = _deep_clean_keys(v)
        return new_dict
    elif isinstance(obj, list):
        return [_deep_clean_keys(i) for i in obj]
    return obj


def robust_json_extract(
    raw: str,
    context: str = "unknown",
    required_keys: Optional[List[str]] = None,
    log_failures: bool = True,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """
    Extract JSON from LLM output with multiple fallback strategies.

    Tries strategies in order:
    1. extract_json_object() - most robust
    2. Direct JSON parse
    3. Markdown code block extraction
    4. Brace-based extraction with depth tracking
    5. LLM-assisted repair hint

    Returns:
        Tuple of (parsed_dict or None, success_bool)

    The parsed dict may be empty {} even if success=True but no JSON found.
    Use success_bool to determine if parsing actually worked.
    """
    if not raw or not raw.strip():
        if log_failures:
            _log_json_failure(raw, context, "empty_input")
        return None, False

    raw = raw.strip()
    candidates: List[str] = []
    strategies_tried: List[str] = []

    # Strategy 1: extract_json_object (most robust)
    strategy = "extract_json_object"
    strategies_tried.append(strategy)
    extracted = extract_json_object(raw)
    if extracted is not None:
        # Validate required keys if specified
        extracted = _deep_clean_keys(extracted)
        if required_keys and not all(k in extracted for k in required_keys):
            missing = [k for k in required_keys if k not in extracted]
            if log_failures:
                _log_json_failure(raw, context, f"missing_keys: {missing}")
            return None, False
        return extracted, True

    # Strategy 2: Direct parse
    strategy = "direct_parse"
    strategies_tried.append(strategy)
    if raw.startswith("{") and raw.endswith("}"):
        try:
            parsed = json.loads(raw)
            _PARSE_STATS["success"] += 1
            return parsed, True
        except json.JSONDecodeError:
            pass

    # Strategy 3: Markdown fence extraction
    strategy = "markdown_fence"
    strategies_tried.append(strategy)
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", raw)
    if fence_match:
        candidates.append(fence_match.group(1))

    # Strategy 4: Try to find JSON-like structure
    strategy = "brace_depth"
    strategies_tried.append(strategy)
    depth = 0
    start_idx = -1
    for i, ch in enumerate(raw):
        if ch == "{" and depth == 0:
            start_idx = i
            depth = 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start_idx >= 0:
                candidates.append(raw[start_idx : i + 1])

    # Try all candidates
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                parsed = _deep_clean_keys(parsed)
                _PARSE_STATS["success"] += 1
                # Validate required keys
                if required_keys and not all(k in parsed for k in required_keys):
                    missing = [k for k in required_keys if k not in parsed]
                    if log_failures:
                        _log_json_failure(raw, context, f"missing_keys: {missing}")
                    return None, False
                return parsed, True
        except json.JSONDecodeError:
            continue

    # All strategies failed
    if log_failures:
        _log_json_failure(raw, context, f"strategies_exhausted: {strategies_tried}")

    return None, False


def get_parse_stats() -> Dict[str, int]:
    """Get JSON parse statistics."""
    return dict(_PARSE_STATS)


def reset_parse_stats() -> None:
    """Reset parse statistics."""
    _PARSE_STATS["success"] = 0
    _PARSE_STATS["failure"] = 0


def extract_json_object(raw_text: str) -> Optional[Dict[str, Any]]:
    if not raw_text:
        return None
    match = _JSON_FENCE_RE.search(raw_text)
    candidates = [match.group(1)] if match else []
    stripped = raw_text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)
    first = stripped.find("{")
    last = stripped.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(stripped[first : last + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def validate_agent_payload(payload: Dict[str, Any], required_keys: List[str]) -> List[str]:
    errors: List[str] = []
    if not isinstance(payload, dict):
        return ["Payload is not a JSON object."]
    for key in required_keys:
        if key not in payload:
            errors.append(f"Missing required key '{key}'.")
    return errors


def infer_failure_class(
    raw_output: str,
    diagnostics: Optional[List[str]] = None,
    producer: str = "",
    tool_result: Optional[Dict[str, Any]] = None,
) -> FailureClass:
    """Intelligently map a failure into a canonical Recovery class."""
    if tool_result and isinstance(tool_result.get("structured_errors"), list):
        if any("synth" in e.get("type", "").lower() for e in tool_result["structured_errors"]):
            return FailureClass.EDA_TOOL_ERROR

    diag_text = "\\n".join(diagnostics or [])
    text = f"{raw_output}\\n{diag_text}".lower()
    
    if tool_result:
        if tool_result.get("infra_failure"):
            return FailureClass.INFRASTRUCTURE_ERROR
        if tool_result.get("tool"):
            return FailureClass.EDA_TOOL_ERROR

    if "syntax error" in text or "drc error" in text or "yosys" in text or "verilator" in text:
        return FailureClass.EDA_TOOL_ERROR
    if "not valid json" in text or "missing required key" in text or "prose" in text:
        return FailureClass.LLM_FORMAT_ERROR
    if "timed out" in text or "binary not found" in text or "no logic found" in text:
        return FailureClass.INFRASTRUCTURE_ERROR
    if "handoff" in text or "missing artifact" in text:
        return FailureClass.ORCHESTRATOR_ROUTING_ERROR
    if "retry" in text and "budget" in text:
        return FailureClass.RETRY_BUDGET_ERROR
    
    if producer.startswith("llm") or producer.startswith("agent"):
        return FailureClass.LLM_SEMANTIC_ERROR
        
    return FailureClass.UNKNOWN


def materially_changed(before: str, after: str) -> bool:
    if before == after:
        return False
    if not before or not after:
        return True
    before_norm = "\n".join(line.rstrip() for line in before.splitlines()).strip()
    after_norm = "\n".join(line.rstrip() for line in after.splitlines()).strip()
    return before_norm != after_norm
