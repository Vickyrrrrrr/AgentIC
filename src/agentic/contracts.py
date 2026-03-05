import enum
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


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
        candidates.append(stripped[first:last + 1])
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
    *,
    producer: str,
    raw_output: str = "",
    diagnostics: Optional[List[str]] = None,
    tool_result: Optional[Dict[str, Any]] = None,
) -> FailureClass:
    diag_text = "\n".join(diagnostics or [])
    text = f"{raw_output}\n{diag_text}".lower()
    if tool_result:
        if tool_result.get("infra_failure"):
            return FailureClass.INFRASTRUCTURE_ERROR
        if tool_result.get("tool"):
            return FailureClass.EDA_TOOL_ERROR
    if "not valid json" in text or "missing required key" in text or "prose" in text:
        return FailureClass.LLM_FORMAT_ERROR
    if "timed out" in text or "tool missing" in text or "binary not found" in text:
        return FailureClass.INFRASTRUCTURE_ERROR
    if "cannot find" in text or "%error" in text or "warning" in text or "yosys" in text or "verilator" in text:
        return FailureClass.EDA_TOOL_ERROR
    if "handoff" in text or "missing artifact" in text or "routing" in text:
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
