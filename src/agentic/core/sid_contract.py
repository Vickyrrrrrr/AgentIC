"""Executable SID contract gates for RTL generation.

The SID is useful only if downstream stages treat it as a contract, not as
prompt decoration. These helpers keep the checks deterministic and reusable:
SID shape, SID-vs-HardwareSpec consistency, and RTL-vs-SID interface matching.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class SIDContractReport:
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    contract: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "contract": dict(self.contract),
        }


def _load_sid(sid: Any) -> Dict[str, Any]:
    if sid is None:
        return {}
    if isinstance(sid, str):
        try:
            return json.loads(sid)
        except json.JSONDecodeError:
            return {}
    if isinstance(sid, dict):
        return dict(sid)
    if hasattr(sid, "to_json"):
        try:
            return json.loads(sid.to_json())
        except Exception:
            return {}
    return {}


def _ports_from_module(module: Dict[str, Any]) -> List[Dict[str, Any]]:
    ports = module.get("ports", [])
    return ports if isinstance(ports, list) else []


def _param_defaults(sid_dict: Dict[str, Any]) -> Dict[str, str]:
    defaults: Dict[str, str] = {}
    for item in sid_dict.get("parameters", []) or []:
        if isinstance(item, dict) and item.get("name"):
            defaults[str(item["name"])] = str(item.get("default", "")).strip()
    return defaults


def _canonical_width(width: str, params: Optional[Dict[str, str]] = None) -> str:
    value = str(width or "").strip()
    params = params or {}
    if value in {"", "1", "logic", "wire", "reg"}:
        return "1"
    if value in params:
        return params[value]
    if re.fullmatch(r"\d+", value):
        return value
    bracket = re.fullmatch(r"\[\s*(.+?)\s*:\s*(.+?)\s*\]", value)
    if bracket:
        hi, lo = bracket.groups()
        for name, default in params.items():
            hi = re.sub(rf"\b{re.escape(name)}\b", default, hi)
            lo = re.sub(rf"\b{re.escape(name)}\b", default, lo)
        if re.fullmatch(r"[\d\s+\-*/()]+", hi) and re.fullmatch(r"[\d\s+\-*/()]+", lo):
            try:
                return str(abs(int(eval(hi, {"__builtins__": {}}, {})) - int(eval(lo, {"__builtins__": {}}, {}))) + 1)
            except Exception:
                return value
        return value
    return value


def _top_module(sid_dict: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], bool]:
    top_name = sid_dict.get("top_module") or sid_dict.get("design_name")
    sub_modules = sid_dict.get("sub_modules", []) or sid_dict.get("submodules", []) or []
    explicit = False
    for module in sub_modules:
        if isinstance(module, dict) and module.get("name") == top_name:
            explicit = True
            return module, explicit
    return (sub_modules[0], False) if sub_modules else (None, False)


def _hw_spec_ports(hw_spec: Optional[Any]) -> Dict[str, Dict[str, str]]:
    if hw_spec is None:
        return {}
    if hasattr(hw_spec, "to_dict"):
        hw_spec = hw_spec.to_dict()
    if isinstance(hw_spec, str):
        try:
            hw_spec = json.loads(hw_spec)
        except json.JSONDecodeError:
            return {}
    if not isinstance(hw_spec, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for port in hw_spec.get("ports", []) or []:
        if not isinstance(port, dict) or not port.get("name"):
            continue
        data_type = str(port.get("data_type", "") or "")
        width_match = re.search(r"\[[^\]]+\]", data_type)
        out[str(port["name"])] = {
            "direction": str(port.get("direction", "")).lower(),
            "width": _canonical_width(width_match.group(0) if width_match else "1"),
        }
    return out


def validate_sid_executable_contract(
    sid: Any,
    *,
    design_name: str = "",
    spec_text: str = "",
    hw_spec: Optional[Any] = None,
) -> SIDContractReport:
    """Validate the SID as an implementation contract."""
    sid_dict = _load_sid(sid)
    errors: List[str] = []
    warnings: List[str] = []

    if not sid_dict:
        return SIDContractReport(False, ["SID is missing or is not valid JSON."], [], {})

    top_name = str(sid_dict.get("top_module") or sid_dict.get("design_name") or "").strip()
    expected_name = design_name or str(sid_dict.get("design_name") or "").strip()
    if expected_name and top_name != expected_name:
        errors.append(f"SID top_module '{top_name}' must equal requested design name '{expected_name}'.")

    top, explicit_top = _top_module(sid_dict)
    if top is None:
        errors.append("SID has no sub_modules and therefore no executable top module contract.")
        top_ports: List[Dict[str, Any]] = []
    else:
        if not explicit_top:
            errors.append(
                f"SID must explicitly include top module '{top_name}' in sub_modules; "
                "do not infer the top contract from the first child module."
            )
        top_ports = _ports_from_module(top)

    params = _param_defaults(sid_dict)
    port_names = [str(p.get("name", "")).strip() for p in top_ports if isinstance(p, dict)]
    port_set = {p for p in port_names if p}
    if len(port_names) != len(port_set):
        errors.append("SID top module has duplicate port names.")

    legal_dirs = {"input", "output", "inout"}
    top_contract_ports: Dict[str, Dict[str, str]] = {}
    for port in top_ports:
        if not isinstance(port, dict):
            errors.append("SID top module contains a non-object port entry.")
            continue
        name = str(port.get("name", "")).strip()
        direction = str(port.get("direction", "")).lower().strip()
        width = str(port.get("width", "")).strip()
        if not re.fullmatch(r"[A-Za-z_]\w*", name or ""):
            errors.append(f"SID top port '{name}' is not a legal Verilog identifier.")
        if direction not in legal_dirs:
            errors.append(f"SID top port '{name}' has illegal direction '{direction}'.")
        if width.lower() in {"", "unknown", "n/a", "na"}:
            errors.append(f"SID top port '{name}' has undefined width '{width}'.")
        top_contract_ports[name] = {
            "direction": direction,
            "width": _canonical_width(width, params),
            "raw_width": width,
        }

    clk = str(sid_dict.get("clock_name", "clk"))
    rst = str(sid_dict.get("reset_name", "rst_n"))
    for signal in (clk, rst):
        if signal not in port_set:
            errors.append(f"SID top module must include required signal '{signal}'.")
        elif top_contract_ports.get(signal, {}).get("direction") != "input":
            errors.append(f"SID required signal '{signal}' must be an input.")
        elif top_contract_ports.get(signal, {}).get("width") != "1":
            errors.append(f"SID required signal '{signal}' must be scalar width 1.")

    if str(sid_dict.get("reset_style", "")).lower() not in {"sync", "async", "synchronous", "asynchronous"}:
        errors.append("SID reset_style must be sync or async.")
    if str(sid_dict.get("reset_polarity", "")).lower() not in {"active_low", "active_high"}:
        errors.append("SID reset_polarity must be active_low or active_high.")

    sub_modules = sid_dict.get("sub_modules", []) or sid_dict.get("submodules", []) or []
    module_names = {m.get("name") for m in sub_modules if isinstance(m, dict)}
    for module in sub_modules:
        if not isinstance(module, dict):
            continue
        for child in module.get("instantiates", []) or []:
            if child not in module_names:
                errors.append(f"SID module '{module.get('name')}' instantiates unknown submodule '{child}'.")
        logic = str(module.get("functional_logic", "")).lower()
        if re.search(r"\b(register|counter|fsm|state|clock|reset|sequential|pipeline)\b", logic):
            child_ports = {p.get("name") for p in _ports_from_module(module) if isinstance(p, dict)}
            if clk not in child_ports or rst not in child_ports:
                errors.append(
                    f"Sequential SID module '{module.get('name')}' must include clock '{clk}' and reset '{rst}'."
                )
        if re.search(r"\b(as needed|etc\.?|appropriate|handle all|and so on)\b", logic):
            warnings.append(
                f"SID module '{module.get('name')}' has vague functional_logic; prefer explicit cycle behavior."
            )

    hw_ports = _hw_spec_ports(hw_spec)
    for name, hw_port in hw_ports.items():
        sid_port = top_contract_ports.get(name)
        if sid_port is None:
            errors.append(f"HardwareSpec port '{name}' is missing from SID top module.")
            continue
        if hw_port["direction"] and sid_port["direction"] != hw_port["direction"]:
            errors.append(
                f"Port '{name}' direction mismatch: SID={sid_port['direction']} HardwareSpec={hw_port['direction']}."
            )
        if hw_port["width"] and sid_port["width"] != hw_port["width"]:
            errors.append(
                f"Port '{name}' width mismatch: SID={sid_port['width']} HardwareSpec={hw_port['width']}."
            )

    if hw_spec is not None and hasattr(hw_spec, "to_dict"):
        hw_dict = hw_spec.to_dict()
    elif isinstance(hw_spec, dict):
        hw_dict = hw_spec
    else:
        hw_dict = {}
    if hw_dict and not hw_dict.get("behavioral_contract"):
        warnings.append("HardwareSpec has no behavioral_contract statements; verification will be weaker.")

    contract = {
        "top_module": top_name,
        "clock": clk,
        "reset": {
            "name": rst,
            "style": sid_dict.get("reset_style", ""),
            "polarity": sid_dict.get("reset_polarity", ""),
        },
        "parameters": params,
        "ports": top_contract_ports,
        "verification_hints": sid_dict.get("verification_hints", []) or [],
        "spec_fingerprint_basis": (spec_text or "")[:500],
    }
    return SIDContractReport(len(errors) == 0, errors, warnings, contract)


def _strip_comments(code: str) -> str:
    code = re.sub(r"/\*[\s\S]*?\*/", "", code or "")
    return re.sub(r"//.*", "", code)


def extract_rtl_top_ports(rtl_code: str, top_module: str) -> Dict[str, Dict[str, str]]:
    """Extract top-level RTL ports as direction/width records."""
    clean = _strip_comments(rtl_code)
    match = re.search(
        rf"\bmodule\s+{re.escape(top_module)}\s*(?:#\s*\([\s\S]*?\)\s*)?\(([\s\S]*?)\)\s*;",
        clean,
    )
    if not match:
        return {}
    header = match.group(1)
    params: Dict[str, str] = {}
    for pname, pval in re.findall(r"\bparameter\s+(?:\w+\s+)?([A-Za-z_]\w*)\s*=\s*([^,;\)\n]+)", clean):
        params[pname] = pval.strip()

    ports: Dict[str, Dict[str, str]] = {}
    current_dir = ""
    current_width = ""
    for raw in header.split(","):
        item = raw.strip()
        if not item:
            continue
        dir_match = re.search(r"\b(input|output|inout)\b", item, flags=re.I)
        if dir_match:
            current_dir = dir_match.group(1).lower()
            current_width = ""
        width_match = re.search(r"\[[^\]]+\]", item)
        if width_match:
            current_width = width_match.group(0)
        cleaned = re.sub(r"\b(input|output|inout|wire|reg|logic|signed|unsigned)\b", " ", item, flags=re.I)
        cleaned = re.sub(r"\[[^\]]+\]", " ", cleaned)
        name_match = re.search(r"\b([A-Za-z_]\w*)\b\s*(?:=.*)?$", cleaned.strip())
        if not name_match:
            continue
        name = name_match.group(1)
        ports[name] = {
            "direction": current_dir,
            "width": _canonical_width(current_width, params),
            "raw_width": current_width or "1",
        }
    return ports


def validate_rtl_against_sid_contract(sid: Any, rtl_code: str) -> SIDContractReport:
    sid_report = validate_sid_executable_contract(sid)
    if not sid_report.contract:
        return sid_report
    errors = list(sid_report.errors)
    warnings = list(sid_report.warnings)
    top = sid_report.contract["top_module"]
    sid_ports: Dict[str, Dict[str, str]] = sid_report.contract.get("ports", {})
    rtl_ports = extract_rtl_top_ports(rtl_code, top)
    if not rtl_ports:
        errors.append(f"RTL does not define top module '{top}' with an extractable ANSI port list.")
        return SIDContractReport(False, errors, warnings, sid_report.contract)

    sid_names = set(sid_ports)
    rtl_names = set(rtl_ports)
    for name in sorted(sid_names - rtl_names):
        errors.append(f"RTL top module is missing SID port '{name}'.")
    for name in sorted(rtl_names - sid_names):
        errors.append(f"RTL top module has extra port '{name}' not present in SID.")
    for name in sorted(sid_names & rtl_names):
        sid_port = sid_ports[name]
        rtl_port = rtl_ports[name]
        if sid_port.get("direction") != rtl_port.get("direction"):
            errors.append(
                f"RTL port '{name}' direction mismatch: RTL={rtl_port.get('direction')} SID={sid_port.get('direction')}."
            )
        if sid_port.get("width") != rtl_port.get("width"):
            errors.append(
                f"RTL port '{name}' width mismatch: RTL={rtl_port.get('width')} SID={sid_port.get('width')}."
            )

    return SIDContractReport(len(errors) == 0, errors, warnings, sid_report.contract)
