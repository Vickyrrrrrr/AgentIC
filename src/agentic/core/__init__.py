"""
AgentIC Multi-Agent Core Modules
=================================
State-of-the-art pipeline modules based on Spec2RTL-Agent, VerilogCoder, and FVDebug.

Modules:
    - architect: Spec2RTL Decomposer Agent (structured spec → JSON)
    - waveform_expert: AST-based Waveform Tracing (Pyverilog + VCD back-trace)
    - deep_debugger: FVDebug balanced analysis (SymbiYosys + causal graphs)
    - react_agent: ReAct (Reasoning + Acting) framework for all agent loops
    - self_reflect: Self-reflection retry pipeline with OpenLane convergence
"""

from .architect import ArchitectModule, StructuredSpecDict
from .waveform_expert import WaveformExpertModule
from .deep_debugger import DeepDebuggerModule
from .react_agent import ReActAgent, ReActStep
from .self_reflect import SelfReflectPipeline

__all__ = [
    "ArchitectModule",
    "StructuredSpecDict",
    "WaveformExpertModule",
    "DeepDebuggerModule",
    "ReActAgent",
    "ReActStep",
    "SelfReflectPipeline",
]
