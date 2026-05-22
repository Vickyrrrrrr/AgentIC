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
from .spec_generator import HardwareSpecGenerator, HardwareSpec
from .hierarchy_expander import HierarchyExpander, HierarchyResult
from .feasibility_checker import (
    FeasibilityChecker,
    FeasibilityIssue,
    FeasibilityResult,
    NodeContract,
    SignoffRequirement,
)
from .design_intent_reconciler import DesignIntentReconciler, ReconciliationResult
from .cdc_analyzer import CDCAnalyzer, CDCResult
from .verification_planner import VerificationPlanner, VerificationPlan
from .waveform_expert import WaveformExpertModule
from .deep_debugger import DeepDebuggerModule
from .react_agent import ReActAgent, ReActStep
from .self_reflect import SelfReflectPipeline
from .hardware_knowledge import HardwareKnowledgeBase, build_hardware_context
from .vlsi_rag import VLSIKnowledgeBase, build_vlsi_context, smart_chunk, classify_domain, classify_node
from .context_evolution import MultiAgentContextEvolver, ContextEvolution
from .rtl_corpus import RTLCorpusBuilder
from .eda_capabilities import EDACapabilities, detect_eda_capabilities
from .flow_capabilities import (
    FlowProfile,
    StageInfo,
    FLOW_PROFILES,
    STAGE_REGISTRY,
    resolve_flow_profile,
    get_stage_info,
)

__all__ = [
    "ArchitectModule",
    "StructuredSpecDict",
    "HardwareSpecGenerator",
    "HardwareSpec",
    "HierarchyExpander",
    "HierarchyResult",
    "FeasibilityChecker",
    "FeasibilityIssue",
    "FeasibilityResult",
    "NodeContract",
    "SignoffRequirement",
    "DesignIntentReconciler",
    "ReconciliationResult",
    "CDCAnalyzer",
    "CDCResult",
    "VerificationPlanner",
    "VerificationPlan",
    "WaveformExpertModule",
    "DeepDebuggerModule",
    "ReActAgent",
    "ReActStep",
    "SelfReflectPipeline",
    "HardwareKnowledgeBase",
    "build_hardware_context",
    "VLSIKnowledgeBase",
    "build_vlsi_context",
    "smart_chunk",
    "classify_domain",
    "classify_node",
    "MultiAgentContextEvolver",
    "ContextEvolution",
    "RTLCorpusBuilder",
    "EDACapabilities",
    "detect_eda_capabilities",
    "FlowProfile",
    "StageInfo",
    "FLOW_PROFILES",
    "STAGE_REGISTRY",
    "resolve_flow_profile",
    "get_stage_info",
]
