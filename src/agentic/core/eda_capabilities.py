"""
EDA Capability Detection
========================

Detects local commercial EDA tools and GPU availability so AgentIC can guide
users toward accelerated or proprietary flows when those tools are present.
"""

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Dict, List


@dataclass
class EDACapabilities:
    """Detected accelerator and EDA tool capabilities."""

    gpu_available: bool = False
    gpu_backend: str = ""
    gpu_summary: str = ""
    commercial_tools: Dict[str, str] = field(default_factory=dict)
    open_source_tools: Dict[str, str] = field(default_factory=dict)
    recommended_flow: str = "openroad_cpu"
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)

    def to_prompt(self) -> str:
        lines = ["EDA CAPABILITY MAP:"]
        lines.append(f"- Recommended flow: {self.recommended_flow}")
        lines.append(f"- GPU: {self.gpu_summary or 'not detected'}")
        if self.commercial_tools:
            lines.append("- Commercial tools: " + ", ".join(sorted(self.commercial_tools)))
        if self.open_source_tools:
            lines.append("- Open-source tools: " + ", ".join(sorted(self.open_source_tools)))
        for note in self.notes[:4]:
            lines.append(f"- {note}")
        return "\n".join(lines)


def _run_version(cmd: List[str], timeout: int = 5) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        text = (result.stdout or result.stderr or "").strip().splitlines()
        return text[0][:160] if text else "found"
    except Exception:
        return "found"


def detect_eda_capabilities() -> EDACapabilities:
    caps = EDACapabilities()

    if shutil.which("nvidia-smi"):
        caps.gpu_available = True
        caps.gpu_backend = "cuda"
        caps.gpu_summary = _run_version(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    elif shutil.which("rocm-smi"):
        caps.gpu_available = True
        caps.gpu_backend = "rocm"
        caps.gpu_summary = _run_version(["rocm-smi", "--showproductname"])

    commercial = {
        "aprisa": "aprisa",
        "innovus": "innovus",
        "genus": "genus",
        "dc_shell": "dc_shell",
        "pt_shell": "pt_shell",
        "calibre": "calibre",
        "vcs": "vcs",
        "questa": "vsim",
        "xcelium": "xrun",
    }
    for name, binary in commercial.items():
        path = shutil.which(binary)
        if path:
            caps.commercial_tools[name] = path

    open_source = {
        "yosys": "yosys",
        "openroad": "openroad",
        "opensta": "sta",
        "magic": "magic",
        "netgen": "netgen",
        "verilator": "verilator",
        "iverilog": "iverilog",
    }
    for name, binary in open_source.items():
        path = shutil.which(binary)
        if path:
            caps.open_source_tools[name] = path

    if caps.commercial_tools:
        caps.recommended_flow = "commercial_available"
        caps.notes.append("Commercial tools were detected; AgentIC can preserve hooks for manual proprietary signoff.")
    if caps.gpu_available:
        caps.notes.append("GPU detected; use GPU-enabled STA/PnR tools when available. OpenROAD itself remains CPU-oriented.")
    if not caps.commercial_tools and not caps.gpu_available:
        caps.notes.append("Using open-source CPU flow. This is expected for portable AgentIC builds.")
    if os.environ.get("AGENTIC_ENABLE_COMMERCIAL_EDA", "").lower() in {"1", "true", "yes"}:
        caps.notes.append("Commercial EDA integration flag is enabled.")

    return caps
