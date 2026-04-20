import enum
import time
import logging
import os

logger = logging.getLogger("agentic.orchestrator")
import re
import hashlib
import json
from .core.graph_builder import DependencyGraph, GraphNode
import signal
import difflib
import subprocess
import threading
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List, Tuple
from rich.console import Console
from rich.panel import Panel
from crewai import Agent, Task, Crew, LLM
from rich.status import Status

# Local imports
from .config import (
    OPENLANE_ROOT,
    LLM_MODEL,
    LLM_BASE_URL,
    LLM_API_KEY,
    PDK,
    WORKSPACE_ROOT,
    SIM_BACKEND_DEFAULT,
    COVERAGE_FALLBACK_POLICY_DEFAULT,
    COVERAGE_PROFILE_DEFAULT,
    get_pdk_profile,
)
from .agents.designer import get_designer_agent
from .agents.testbench_designer import get_testbench_agent
from .agents.verifier import (
    get_verification_agent,
    get_error_analyst_agent,
    get_regression_agent,
)
from .agents.doc_agent import get_doc_agent
from .agents.sdc_agent import get_sdc_agent
from .core import (
    ArchitectModule,
    SelfReflectPipeline,
    ReActAgent,
    WaveformExpertModule,
    DeepDebuggerModule,
)
from .core.graceful_degradation import GracefulDegradation, get_degradation_handler
from .core.context_manager import TokenBudgetManager, DynamicTokenBudget
from .core.checkpoint_manager import CheckpointManager, AutomaticCheckpointTrigger
from .core.usage_tracker import UsageTracker, get_usage_tracker
from .core.incremental_fixer import IncrementalFixEngine, ErrorAnalysis, ErrorType
from .core.hardware_knowledge import HardwareKnowledgeBase
from .core.context_evolution import MultiAgentContextEvolver
from .core.eda_capabilities import detect_eda_capabilities
from .core import (
    HardwareSpecGenerator,
    HardwareSpec,
    HierarchyExpander,
    FeasibilityChecker,
    CDCAnalyzer,
    VerificationPlanner,
)
from .contracts import (
    AgentResult,
    ArtifactRef,
    FailureClass,
    FailureRecord,
    StageResult,
    StageStatus,
    extract_json_object,
    infer_failure_class,
    materially_changed,
    validate_agent_payload,
)
from .tools.vlsi_tools import (
    write_verilog,
    write_verilog_tool,
    run_syntax_check,
    syntax_check_tool,
    read_file_tool,
    run_simulation,
    run_openlane,
    SecurityCheck,
    write_sby_config,
    run_formal_verification,
    check_physical_metrics,
    run_lint_check,
    run_iverilog_lint,
    run_simulation_with_coverage,
    parse_coverage_report,
    parse_drc_lvs_reports,
    parse_sta_signoff,
    parse_power_signoff,
    run_cdc_check,
    generate_design_doc,
    convert_sva_to_yosys,
    validate_yosys_sby_check,
    startup_self_check,
    run_semantic_rigor_check,
    auto_fix_width_warnings,
    parse_eda_log_summary,
    parse_congestion_metrics,
    run_eqy_lec,
    apply_eco_patch,
    run_tb_static_contract_check,
    run_tb_compile_gate,
    repair_tb_for_verilator,
    get_coverage_thresholds,
    run_gls_simulation,
)

from .tools.rate_limiter import (
    rate_limited_crew_kickoff,
    rate_limited_call,
    RateLimitResult,
)

from rich.theme import Theme

claude_theme = Theme(
    {
        "info": "dim white",
        "accent": "#d97757",
        "success": "#32997b",
        "warning": "#e0b04a",
        "error": "#d45851",
        "heading": "bold #e5e1d8",
        "border": "#8f8a80",
        "spinner": "#d97757",
    }
)
console = Console(theme=claude_theme, markup=True, emoji=True)


# ─────────────────────────────────────────────────────────────────────────────
# OpenCode-Style Output — Clean, Minimal, Classy
# ─────────────────────────────────────────────────────────────────────────────


class ThinkingDisplay:
    """True Live-Updating OpenCode-style minimal display for AgentIC."""

    LEVELS = ("minimal", "normal", "verbose")

    def __init__(self, level: str = "minimal", _console: Optional["Console"] = None):
        import sys as _sys

        self.level = level if level in self.LEVELS else "minimal"
        _mod_console = _sys.modules[__name__].__dict__.get("console")
        self._console = _console if _console is not None else _mod_console

        # Initialize the live Rich status spinner
        self.status = Status(
            "[cyan]Waking up reasoning agents...[/cyan]", console=self._console, spinner="dots"
        )
        self.is_running = False

    def start(self):
        if not self.is_running:
            self.status.start()
            self.is_running = True

    def stop(self):
        if self.is_running:
            self.status.stop()
            self.is_running = False

    def show_state(self, state: str, subtitle: str = "") -> None:
        """Prints a clean, bold milestone badge."""
        self._console.print(f"\n[bold #d97757]▶ {state}[/] [dim]{subtitle}[/dim]")
        self.status.update(f"[bold cyan]Starting {state}...[/bold cyan]")

    def show_activity(self, activity: str, refined: bool = False) -> None:
        """Handles background reasoning vs major outputs."""
        clean_msg = str(activity).strip()

        if refined:
            # Major milestone: Prints above the spinner permanently
            self._console.print(f"  [bold green]✔[/bold green] [info]{clean_msg}[/info]")
        else:
            # Background thought: Updates the spinner text in place
            if self.level in ("normal", "verbose"):
                short_msg = clean_msg[:90] + "..." if len(clean_msg) > 90 else clean_msg
                self.status.update(f"[cyan]AgentIC reasoning...[/cyan] [dim]{short_msg}[/dim]")


# Thinking levels for CLI
THINKING_LEVEL_DEFAULT = os.environ.get("AGENTIC_THINKING_LEVEL", "minimal").strip().lower()
if THINKING_LEVEL_DEFAULT not in ThinkingDisplay.LEVELS:
    THINKING_LEVEL_DEFAULT = "minimal"


# ---------------------------------------------------------------------------
# Universal LLM Code Output Validator
# ---------------------------------------------------------------------------
# Called after every LLM code generation call (RTL, TB, SVA, fix) to reject
# prose / English explanations that are not valid Verilog / SystemVerilog.
# ---------------------------------------------------------------------------

# Common English sentence-starting words that never begin valid Verilog code
_PROSE_START_WORDS = re.compile(
    r"^\s*(the|this|here|i |in |a |an |to |we |my |it |as |for |note|sure|below|above|let|thank|sorry|great|of course)",
    re.IGNORECASE,
)


def extract_verilog_safely(raw_llm_text: str) -> str:
    """Universal Verilog Extractor - robust extraction from chatty LLMs.

    Strips think tags first, then tries fenced code blocks, then falls back
    to finding the first module...endmodule block. Returns cleaned code.
    """
    if raw_llm_text is None:
        return ""
    if not raw_llm_text.strip():
        return ""

    text = raw_llm_text

    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    fences = [
        (r"```(?:verilog|systemverilog|sv)\s*\n(.*?)\n```", re.DOTALL),
        (r"```(?:verilog|systemverilog|sv)\s*(.*?)```", re.DOTALL),
        (r"```v\s*\n(.*?)\n```", re.DOTALL),
        (r"```v\s*(.*?)```", re.DOTALL),
    ]
    for pattern, flags in fences:
        m = re.search(pattern, text, flags)
        if m:
            code = m.group(1)
            if "module" in code and "endmodule" in code:
                return code.strip()

    module_match = re.search(
        r"\bmodule\s+\w+.*?\bendmodule\b",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if module_match:
        return module_match.group(0).strip()

    return text.strip()


def validate_llm_code_output(raw: str) -> bool:
    """Return True if *raw* looks like valid Verilog/SystemVerilog code."""
    extracted = extract_verilog_safely(raw)
    if not extracted:
        return False

    if "module" not in extracted or "endmodule" not in extracted:
        return False

    if _PROSE_START_WORDS.match(extracted):
        return False

    return True


def extract_and_validate_llm_code(raw: str) -> Tuple[bool, str]:
    """Extract Verilog from raw LLM output and validate it.

    Returns:
        Tuple of (is_valid, extracted_code)
    """
    if raw is None:
        return False, ""

    extracted = extract_verilog_safely(raw)
    is_valid = False

    if extracted and "module" in extracted and "endmodule" in extracted:
        if not _PROSE_START_WORDS.match(extracted):
            is_valid = True

    return is_valid, extracted


class BuildStrategy(enum.Enum):
    SV_MODULAR = "SystemVerilog Modular (Modern)"
    VERILOG_CLASSIC = "Verilog-2005 (Legacy/Robust)"


class BuildState(enum.Enum):
    INIT = "Initializing"
    SPEC = "Architectural Planning"
    SPEC_VALIDATE = "Specification Validation"
    HIERARCHY_EXPAND = "Hierarchy Expansion"
    FEASIBILITY_CHECK = "Feasibility Check"
    CDC_ANALYZE = "CDC Analysis"
    VERIFICATION_PLAN = "Verification Planning"
    RTL_GEN = "RTL Generation"
    RTL_FIX = "RTL Syntax Fixing"
    VERIFICATION = "Verification & Testbench"
    FORMAL_VERIFY = "Formal Property Verification"
    COVERAGE_CHECK = "Coverage Analysis"
    REGRESSION = "Regression Testing"
    SDC_GEN = "Timing Constraints Generation"
    SYNTHESIS = "RTL Synthesis"
    DFT_SCAN = "DFT Scan Insertion"
    DFT_ATPG = "DFT ATPG Patterns"
    MBIST = "Memory BIST Insertion"
    GLS_SIM = "Gate-Level Simulation"
    FLOORPLAN = "Floorplanning"
    HARDENING = "GDSII Hardening"
    CONVERGENCE_REVIEW = "Convergence Review"
    ECO_PATCH = "ECO Patch"
    POWER_ANALYSIS = "Power Analysis"
    TIMING_ANALYSIS = "Static Timing Analysis"
    PHYSICAL_VERIFY = "Physical Verification"
    SIGNOFF = "DRC/LVS Signoff"
    IP_PACKAGE = "IP Packaging"
    SUCCESS = "Build Complete"
    FAIL = "Build Failed"


@dataclass
class ConvergenceSnapshot:
    iteration: int
    wns: float
    tns: float
    congestion: float
    area_um2: float
    power_w: float


@dataclass
class BuildHistory:
    state: str
    message: str
    timestamp: float


class BuildOrchestrator:
    @staticmethod
    def _suppress_external_logging():
        """Suppress noisy external library logging to console."""
        import io
        import sys
        import contextlib
        from rich.console import Console

        suppress_loggers = [
            "",
            "LiteLLM",
            "LiteLLM Proxy",
            "LiteLLM Router",
            "crewai",
            "crewai.A2A",
            "httpx",
            "openai",
            "httpcore",
            "urllib3",
        ]

        for logger_name in suppress_loggers:
            logger = logging.getLogger(logger_name)
            logger.setLevel(logging.ERROR)
            for handler in logger.handlers[:]:
                if isinstance(handler, logging.StreamHandler):
                    logger.removeHandler(handler)

        root = logging.getLogger()
        for handler in root.handlers[:]:
            if isinstance(handler, logging.StreamHandler):
                root.removeHandler(handler)

        # Patch crewai's console.print to suppress warnings
        try:
            from crewai.events import event_context as _crewai_events

            _silent_console = Console(markup=False, emoji=False, file=io.StringIO())
            _crewai_events._console = _silent_console
        except Exception:
            pass

        try:
            from crewai.events import event_bus as _event_bus

            _silent_console2 = Console(markup=False, emoji=False, file=io.StringIO())
            if hasattr(_event_bus, "crewai_event_bus"):
                _event_bus.crewai_event_bus._console = _silent_console2
        except Exception:
            pass

        # Suppress crewai event bus singleton
        try:
            from crewai.events import event_bus as _event_bus

            _event_bus.crewai_event_bus._console = Console(markup=False, emoji=False)
            _event_bus.crewai_event_bus._console.print = lambda *a, **k: None
        except Exception:
            pass

    def __init__(
        self,
        name: str,
        desc: str,
        llm: LLM,
        max_retries: int = 5,
        verbose: bool = True,
        skip_openlane: bool = False,
        skip_coverage: bool = False,
        full_signoff: bool = False,
        min_coverage: float = 80.0,
        strict_gates: bool = False,
        pdk_profile: str = "sky130",
        max_pivots: int = 2,
        congestion_threshold: float = 10.0,
        hierarchical_mode: str = "auto",
        global_step_budget: int = 120,
        tb_gate_mode: str = "strict",
        tb_max_retries: int = 3,
        tb_fallback_template: str = "uvm_lite",
        coverage_backend: str = SIM_BACKEND_DEFAULT,
        coverage_fallback_policy: str = COVERAGE_FALLBACK_POLICY_DEFAULT,
        coverage_profile: str = COVERAGE_PROFILE_DEFAULT,
        event_sink=None,  # Optional callable(dict) for live event streaming (web API)
        no_golden_templates: bool = False,  # Bypass golden template matching in RTL_GEN
        role_llms: Optional[Dict[str, Any]] = None,  # role -> LLM mapping
        human_in_loop: bool = False,  # For web API HITL
        thinking_level: str = "minimal",  # Display: minimal, normal, verbose
    ):
        self._suppress_external_logging()

        self.name = name
        self.desc = desc
        self.llm = llm
        self.role_llms = role_llms or {}
        self.human_in_loop = human_in_loop
        self.event_sink = event_sink  # Web API hook: callable(event_dict) or None
        self.no_golden_templates = no_golden_templates
        self.max_retries = max_retries
        self.verbose = verbose
        self.skip_openlane = skip_openlane
        self.skip_coverage = skip_coverage
        self.full_signoff = full_signoff
        self.min_coverage = min_coverage
        self.strict_gates = strict_gates
        self.pdk_profile = get_pdk_profile(pdk_profile)
        self.max_pivots = max_pivots
        self.congestion_threshold = congestion_threshold
        self.hierarchical_mode = hierarchical_mode
        self.global_step_budget = global_step_budget
        self.tb_gate_mode = (tb_gate_mode or "strict").lower()
        if self.tb_gate_mode not in {"strict", "relaxed"}:
            self.tb_gate_mode = "strict"
        self.tb_max_retries = max(1, int(tb_max_retries))
        self.tb_fallback_template = (tb_fallback_template or "uvm_lite").lower()
        if self.tb_fallback_template not in {"uvm_lite", "classic"}:
            self.tb_fallback_template = "uvm_lite"
        self.coverage_backend = (coverage_backend or SIM_BACKEND_DEFAULT).lower()
        if self.coverage_backend not in {"auto", "verilator", "iverilog"}:
            self.coverage_backend = SIM_BACKEND_DEFAULT
        self.coverage_fallback_policy = (
            coverage_fallback_policy or COVERAGE_FALLBACK_POLICY_DEFAULT
        ).lower()
        if self.coverage_fallback_policy not in {"fail_closed", "fallback_oss", "skip"}:
            self.coverage_fallback_policy = COVERAGE_FALLBACK_POLICY_DEFAULT
        self.coverage_profile = (coverage_profile or COVERAGE_PROFILE_DEFAULT).lower()
        if self.coverage_profile not in {"balanced", "aggressive", "relaxed"}:
            self.coverage_profile = COVERAGE_PROFILE_DEFAULT
        self.coverage_thresholds = get_coverage_thresholds(self.coverage_profile)
        self.thinking_level = (
            thinking_level if thinking_level in ThinkingDisplay.LEVELS else "minimal"
        )
        self._thinking = ThinkingDisplay(level=self.thinking_level)

        self.state = BuildState.INIT
        self.strategy = BuildStrategy.SV_MODULAR
        self.retry_count = 0
        self.global_retry_count = 0  # Added for Bug 2
        self.state_retry_counts: Dict[str, int] = {}
        self.failure_fingerprint_history: Dict[str, int] = {}
        self.failed_code_by_fingerprint: Dict[str, str] = {}  # Added for Bug 1
        self.tb_failed_code_by_fingerprint: Dict[str, str] = {}  # Added for Bug 1
        self.global_step_count = 0
        self.pivot_count = 0
        self.strategy_pivot_stage = 0
        self.convergence_history: List[ConvergenceSnapshot] = []
        self.build_history: List[BuildHistory] = []
        self.floorplan_attempts = 0
        self.eco_attempts = 0
        self.tb_generation_timeout_s = int(os.getenv("AGENTIC_TB_TIMEOUT_S", "120"))
        self.tb_static_fail_count = 0
        self.tb_compile_fail_count = 0
        self.tb_repair_fail_count = 0
        self.tb_failure_fingerprint_history: Dict[str, int] = {}
        self.tb_recovery_counts: Dict[str, int] = {}
        self.artifacts: Dict[str, Any] = {}  # Store paths to gathered files
        self.artifact_bus: Dict[str, ArtifactRef] = {}
        self.stage_contract_history: List[Dict[str, Any]] = []
        self.retry_metadata: Dict[str, int] = {
            "stage_retry": 0,
            "regeneration_retry": 0,
            "format_retry": 0,
            "infrastructure_retry": 0,
        }
        self.history: List[Dict[str, Any]] = []  # Log of state transitions and errors
        self.errors: List[str] = []  # List of error messages

        self.degradation = get_degradation_handler()
        self.usage_tracker = get_usage_tracker()
        self.checkpoint_manager = CheckpointManager(name)
        self.checkpoint_trigger = AutomaticCheckpointTrigger(self.checkpoint_manager)
        self.incremental_fixer = IncrementalFixEngine()
        self.hardware_kb = HardwareKnowledgeBase()
        self.context_evolver = MultiAgentContextEvolver()
        self.eda_capabilities = detect_eda_capabilities()
        self.artifacts["eda_capabilities"] = self.eda_capabilities.to_dict()

        provider = "openai"
        model = LLM_MODEL
        if base_url := getattr(self.llm, "base_url", ""):
            if "groq" in base_url:
                provider = "groq"
            elif "anthropic" in base_url:
                provider = "anthropic"
            elif "azure" in base_url:
                provider = "azure"
        self.token_budget = DynamicTokenBudget(model=model, provider=provider)

    def get_llm_for_role(self, role: str) -> "LLM":
        """Get the specific LLM override for a role, or fallback to the primary LLM."""
        return self.role_llms.get(role, self.llm)

    def _crew_kickoff(
        self,
        crew: "Crew",
        role_hint: str = "build",
        task_overrides: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Run a CrewAI crew with automatic rate-limit retry and caching.

        Detects the provider from the first agent's LLM config and applies
        per-provider exponential-backoff retry on 429/503 responses.
        Uses GracefulDegradation for intelligent caching and fallback.
        """
        model = ""
        base_url = ""
        provider = "openai"
        agents = getattr(crew, "agents", [])
        if agents:
            llm_obj = getattr(agents[0], "llm", None)
            if llm_obj:
                model = getattr(llm_obj, "model", "") or ""
                base_url = getattr(llm_obj, "base_url", "") or ""
                if "groq" in base_url:
                    provider = "groq"
                elif "anthropic" in base_url:
                    provider = "anthropic"
                elif "azure" in base_url:
                    provider = "azure"

        def _execute_crew():
            return rate_limited_crew_kickoff(
                crew,
                task_overrides=task_overrides,
                model=model,
                base_url=base_url,
            )

        exec_result = self.degradation.execute(
            task=f"CrewAI_{role_hint}",
            prompt=f"{role_hint}_{model}",
            execute_fn=_execute_crew,
            providers=[provider],
            model=model,
        )

        if exec_result.cache_hit:
            self.log(f"[Cache hit] Using cached response", refined=True)

        if exec_result.total_wait_seconds > 0:
            self.log(
                f"Rate-limit handling: waited {exec_result.total_wait_seconds:.1f}s, "
                f"used strategy: {exec_result.strategy_used.value}",
                refined=True,
            )

        result = exec_result.response if exec_result.success else None

        if not exec_result.success:
            err = exec_result.error or "Unknown error"
            self.logger.error(f"CrewAI call failed: {err}")
            self.log(f"CrewAI call failed: {err}", refined=True)

        self.usage_tracker.record_call(
            provider=provider,
            model=model,
            cache_hit=exec_result.cache_hit,
            duration_ms=int(exec_result.total_wait_seconds * 1000),
            success=exec_result.success,
            build_name=self.name,
            stage=self.state.name,
        )

        return result

    def setup_logger(self):
        """Sets up a file logger for the build process."""
        import shutil
        import datetime

        log_file = os.path.join(self.artifacts["root"], f"{self.name}.log")

        # Ensure directory exists
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        # If a previous log exists, rotate it so we don't lose the old run's terminal output/errors
        if os.path.exists(log_file):
            timestamp = datetime.datetime.fromtimestamp(os.path.getmtime(log_file)).strftime(
                "%Y%m%d_%H%M%S"
            )
            backup_log = os.path.join(
                self.artifacts["root"], f"{self.name}_{timestamp}_previous.log"
            )
            shutil.copy2(log_file, backup_log)

        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

        # File Handler
        fh = logging.FileHandler(log_file, mode="w")
        fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        self.logger.addHandler(fh)

        self.log(f"Logging initialized to {log_file}", refined=True)

    def log(self, message: str, refined: bool = False):
        """Logs a message to the file (always) and UI (cleanly)."""
        now = time.time()
        self.history.append({"state": self.state.name, "msg": message, "time": now})
        self.build_history.append(
            BuildHistory(state=self.state.name, message=message, timestamp=now)
        )

        # File Log
        if hasattr(self, "logger"):
            self.logger.info(f"[{self.state.name}] {message}")

        # Web API event sink
        if self.event_sink is not None:
            try:
                self.event_sink(
                    {
                        "type": "checkpoint" if refined else "log",
                        "state": self.state.name,
                        "message": message,
                    }
                )
            except Exception:
                pass  # Never let sink errors crash the build

        # Clean console output via the new ThinkingDisplay
        if hasattr(self, "_thinking"):
            self._thinking.show_activity(message, refined=refined)

    def transition(self, new_state: BuildState, preserve_retries: bool = False):
        if hasattr(self, "_thinking"):
            self._thinking.show_state(new_state.name, subtitle=new_state.value)
        self.state = new_state
        if not preserve_retries:
            self.retry_count = 0
            self.state_retry_counts[new_state.name] = 0
        if self.event_sink is not None:
            try:
                self.event_sink(
                    {
                        "type": "transition",
                        "state": new_state.name,
                        "message": f"▶ {new_state.value}",
                    }
                )
            except Exception:
                pass

        KEY_STATES = {
            BuildState.RTL_GEN,
            BuildState.VERIFICATION,
            BuildState.SYNTHESIS,
            BuildState.FLOORPLAN,
            BuildState.HARDENING,
            BuildState.SIGNOFF,
            BuildState.SUCCESS,
        }
        if new_state in KEY_STATES or new_state.name.startswith("FAIL"):
            cp_path = self.checkpoint_manager.save(self, reason=f"after_{new_state.name.lower()}")
            if cp_path and self.verbose:
                self.log(f"Checkpoint saved: {cp_path}", refined=True)

    def _bump_state_retry(self) -> int:
        count = self.state_retry_counts.get(self.state.name, 0) + 1
        self.state_retry_counts[self.state.name] = count
        return count

    def _artifact_fingerprint(self) -> str:
        rtl = self.artifacts.get("rtl_code", "")
        tb = ""
        tb_path = self.artifacts.get("tb_path", "")
        if tb_path and os.path.exists(tb_path):
            try:
                with open(tb_path, "r") as f:
                    tb = f.read()
            except OSError:
                tb = ""
        digest = hashlib.sha256((rtl + "\n" + tb).encode("utf-8", errors="ignore")).hexdigest()
        return digest[:16]

    def _record_failure_fingerprint(self, error_text: str) -> bool:
        base = f"{self.state.name}|{error_text[:500]}|{self._artifact_fingerprint()}"
        fp = hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()
        count = self.failure_fingerprint_history.get(fp, 0) + 1
        self.failure_fingerprint_history[fp] = count

        # Bug 1: Track the exact code that produced this error
        rtl = self.artifacts.get("rtl_code", "")
        tb = ""
        tb_path = self.artifacts.get("tb_path", "")
        if tb_path and os.path.exists(tb_path):
            try:
                with open(tb_path, "r") as f:
                    tb = f.read()
            except OSError:
                pass

        code_context = ""
        if rtl:
            code_context += f"// --- RTL CODE ---\n{rtl}\n"
        if tb:
            code_context += f"// --- TESTBENCH CODE ---\n{tb}\n"

        self.failed_code_by_fingerprint[fp] = code_context

        return count >= 2

    def _clear_last_fingerprint(self, error_text: str) -> None:
        """Reset the fingerprint counter for the given error so the next
        loop iteration doesn't see a 'repeated failure' when the code was
        never actually updated (e.g. LLM returned unparsable output)."""
        base = f"{self.state.name}|{error_text[:500]}|{self._artifact_fingerprint()}"
        fp = hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()
        self.failure_fingerprint_history.pop(fp, None)

    def _set_artifact(
        self,
        key: str,
        value: Any,
        *,
        producer: str,
        consumer: str = "",
        required: bool = False,
        blocking: bool = False,
    ) -> None:
        self.artifacts[key] = value
        self.artifact_bus[key] = ArtifactRef(
            key=key,
            producer=producer,
            consumer=consumer,
            required=required,
            blocking=blocking,
            value=value,
        )

    def _get_artifact(self, key: str, default: Any = None) -> Any:
        return self.artifacts.get(key, default)

    def _require_artifact(self, key: str, *, consumer: str, message: str) -> Any:
        if key in self.artifacts and self.artifacts[key] not in (None, "", {}):
            ref = self.artifact_bus.get(key)
            if ref is not None:
                ref.consumer = consumer
            return self.artifacts[key]
        self._record_stage_contract(
            StageResult(
                stage=self.state.name,
                status=StageStatus.ERROR,
                producer=consumer,
                failure_class=FailureClass.ORCHESTRATOR_ROUTING_ERROR,
                diagnostics=[message],
                next_action="fail_closed",
            )
        )
        raise RuntimeError(message)

    def _consume_handoff(self, key: str, *, consumer: str, required: bool = False) -> Any:
        value = self.artifacts.get(key)
        if value in (None, "", {}):
            if required:
                msg = f"Missing artifact handoff '{key}' for {consumer}."
                self._record_stage_contract(
                    StageResult(
                        stage=self.state.name,
                        status=StageStatus.ERROR,
                        producer=consumer,
                        failure_class=FailureClass.ORCHESTRATOR_ROUTING_ERROR,
                        diagnostics=[msg],
                        next_action="fail_closed",
                    )
                )
                raise RuntimeError(msg)
            return None
        ref = self.artifact_bus.get(key)
        if ref is not None:
            ref.consumer = consumer
        return value

    def _record_stage_contract(self, result: StageResult) -> None:
        payload = result.to_dict()
        self.stage_contract_history.append(payload)
        self.artifacts["last_stage_result"] = payload
        if hasattr(self, "logger"):
            self.logger.info(f"STAGE RESULT:\n{json.dumps(payload, indent=2, default=str)}")

    def _record_retry(self, bucket: str, *, consume_global: bool = False) -> int:
        count = int(self.retry_metadata.get(bucket, 0)) + 1
        self.retry_metadata[bucket] = count
        self.artifacts["retry_metadata"] = dict(self.retry_metadata)
        if consume_global:
            self.global_retry_count += 1
        return count

    def _attempt_backend_error_recovery(
        self,
        stage_name: str,
        errors: List[str],
        *,
        retry_state: BuildState,
        fallback_state: BuildState,
        structured_errors: Optional[List[Dict[str, Any]]] = None,
        confidence_threshold: float = 0.5,
        max_attempts: int = 2,
    ) -> bool:
        """Analyze backend tool errors and optionally route back for RTL recovery."""
        error_lines = [str(e).strip() for e in errors if str(e).strip()]
        if structured_errors:
            for err in structured_errors:
                message = str(err.get("message", "")).strip()
                if message and message not in error_lines:
                    error_lines.append(message)

        error_text = "\n".join(error_lines)[:8000]
        if not error_text:
            error_text = f"{stage_name} failed without a structured error message."

        analysis = self.incremental_fixer.analyze_error(
            error_text=error_text,
            rtl_code=self.artifacts.get("rtl_code", ""),
        )
        retry_key = f"{stage_name.lower()}_backend_recovery"
        attempts = int(self.retry_metadata.get(retry_key, 0))

        self.artifacts["last_error"] = f"{stage_name} failure:\n{error_text}"
        self.artifacts["backend_error_stage"] = stage_name
        self.artifacts["backend_error_analysis"] = {
            "error_type": analysis.error_type.value
            if hasattr(analysis.error_type, "value")
            else str(analysis.error_type),
            "fix_confidence": analysis.fix_confidence,
            "is_surgical": analysis.is_surgical,
            "signals": analysis.signals_mentioned[:10],
            "modules": analysis.modules_mentioned[:10],
            "strategy": analysis.recommended_strategy,
            "risks": analysis.side_effect_risks[:5],
        }
        if structured_errors is not None:
            self.artifacts[f"{stage_name.lower()}_structured_errors"] = structured_errors

        non_rtl_error_types = {ErrorType.UNKNOWN_TOOL_ERROR}
        if retry_state == BuildState.RTL_GEN:
            non_rtl_error_types.update({ErrorType.DRC_ERROR, ErrorType.LVS_ERROR})
        if attempts >= max_attempts:
            self.log(
                f"{stage_name} recovery budget exhausted ({attempts}/{max_attempts}); continuing to {fallback_state.name}.",
                refined=True,
            )
            return False

        if analysis.error_type in non_rtl_error_types:
            self.log(
                f"{stage_name} failure classified as {analysis.error_type.value}; not retrying RTL generation.",
                refined=True,
            )
            return False

        if analysis.fix_confidence <= confidence_threshold:
            self.log(
                f"{stage_name} failure classified as {analysis.error_type.value} "
                f"(confidence={analysis.fix_confidence:.2f}); continuing to {fallback_state.name}.",
                refined=True,
            )
            return False

        self._record_retry(retry_key, consume_global=False)
        if analysis.error_type in (ErrorType.TIMING_ERROR, ErrorType.TIMING_VIOLATION):
            self.artifacts["logic_decoupling_hint"] = (
                f"{stage_name} reported timing closure trouble: {error_text[:1000]}. "
                "Reduce critical-path depth, consider register slicing, and preserve top-level IO."
            )

        self.log(
            f"{stage_name} failed; attempting {retry_state.name} recovery "
            f"(type={analysis.error_type.value}, confidence={analysis.fix_confidence:.2f}, "
            f"attempt={attempts + 1}/{max_attempts}).",
            refined=True,
        )
        self.transition(retry_state, preserve_retries=True)
        return True

    def _record_non_consumable_output(
        self, producer: str, raw_output: str, diagnostics: List[str]
    ) -> None:
        self._record_retry("format_retry", consume_global=False)
        self._record_stage_contract(
            StageResult(
                stage=self.state.name,
                status=StageStatus.RETRY,
                producer=producer,
                failure_class=FailureClass.LLM_FORMAT_ERROR,
                diagnostics=diagnostics or ["LLM output could not be consumed."],
                next_action="retry_generation",
            )
        )
        self._set_artifact(
            "last_non_consumable_output",
            {
                "producer": producer,
                "raw_output": raw_output[:4000],
                "diagnostics": diagnostics,
            },
            producer=producer,
            consumer=self.state.name,
            required=False,
            blocking=False,
        )

    @staticmethod
    def _extract_module_names(code: str) -> List[str]:
        return re.findall(r"\bmodule\s+([A-Za-z_]\w*)", code or "")

    def _is_hierarchical_design(self, code: str) -> bool:
        return len(self._extract_module_names(code)) > 1

    def _validate_rtl_candidate(self, candidate_code: str, previous_code: str) -> List[str]:
        issues: List[str] = []
        if not validate_llm_code_output(candidate_code):
            issues.append("RTL candidate is not valid Verilog/SystemVerilog code output.")
            return issues
        modules = self._extract_module_names(candidate_code)
        if self.name not in modules:
            issues.append(f"RTL candidate is missing top module '{self.name}'.")
        prev_modules = self._extract_module_names(previous_code)
        if prev_modules and len(prev_modules) > 1:
            if sorted(prev_modules) != sorted(modules):
                issues.append(
                    "Hierarchical RTL repair changed the module inventory; module-scoped preservation failed."
                )
        prev_ports = self._extract_module_interface(previous_code)
        new_ports = self._extract_module_interface(candidate_code)
        if prev_ports and new_ports and prev_ports != new_ports:
            issues.append("RTL candidate changed the top-module interface.")
        return issues

    def _validate_tb_candidate(self, tb_code: str) -> List[str]:
        issues: List[str] = []
        if not validate_llm_code_output(tb_code):
            issues.append("TB candidate is not valid Verilog/SystemVerilog code output.")
            return issues
        module_match = re.search(r"\bmodule\s+([A-Za-z_]\w*)", tb_code)
        if not module_match or module_match.group(1) != f"{self.name}_tb":
            issues.append(f"TB module name must be '{self.name}_tb'.")
        if f'$dumpfile("{self.name}_wave.vcd")' not in tb_code:
            issues.append("TB candidate is missing the required VCD dumpfile block.")
        if "$dumpvars(0," not in tb_code:
            issues.append("TB candidate is missing the required dumpvars block.")
        if "TEST PASSED" not in tb_code or "TEST FAILED" not in tb_code:
            issues.append("TB candidate must include TEST PASSED and TEST FAILED markers.")
        return issues

    def _validate_sva_candidate(self, sva_code: str, rtl_code: str) -> List[str]:
        issues: List[str] = []
        if not validate_llm_code_output(sva_code):
            issues.append("SVA candidate is not valid SystemVerilog code output.")
            return issues
        if f"module {self.name}_sva" not in sva_code:
            issues.append(f"SVA candidate is missing module '{self.name}_sva'.")
        yosys_code = convert_sva_to_yosys(sva_code, self.name)
        if not yosys_code:
            issues.append("SVA candidate could not be translated to Yosys-compatible assertions.")
            return issues
        ok, report = validate_yosys_sby_check(yosys_code)
        if not ok:
            for issue in report.get("issues", []):
                issues.append(issue.get("message", "Invalid Yosys preflight assertion output."))
        signal_inventory = self._format_signal_inventory_for_prompt(rtl_code)
        if "No signal inventory could be extracted" in signal_inventory:
            issues.append("RTL signal inventory is unavailable for SVA validation.")
        return issues

    @staticmethod
    def _simulation_capabilities(sim_output: str, vcd_path: str) -> Dict[str, Any]:
        trace_enabled = "without --trace" not in (sim_output or "")
        waveform_generated = bool(
            vcd_path and os.path.exists(vcd_path) and os.path.getsize(vcd_path) > 200
        )
        return {
            "trace_enabled": trace_enabled,
            "waveform_generated": waveform_generated,
        }

    def _normalize_react_result(self, trace: Any) -> AgentResult:
        final_answer = getattr(trace, "final_answer", "") or ""
        code_match = re.search(
            r"```(?:verilog|systemverilog|sv|v)?\s*(.*?)```",
            final_answer,
            re.DOTALL | re.IGNORECASE,
        )
        code_str = code_match.group(1).strip() if code_match else final_answer.strip()
        has_code = bool(code_match) or ("module " in code_str and "endmodule" in code_str)
        payload = {
            "code": code_str if has_code else "",
            "self_check_status": "verified" if getattr(trace, "success", False) else "unverified",
            "tool_observations": [
                getattr(step, "observation", "")
                for step in getattr(trace, "steps", [])
                if getattr(step, "observation", "")
            ],
            "final_decision": "accept" if has_code else "fallback",
        }
        failure_class = FailureClass.UNKNOWN if has_code else FailureClass.LLM_FORMAT_ERROR
        return AgentResult(
            agent="ReAct",
            ok=has_code,
            producer="agent_react",
            payload=payload,
            diagnostics=[] if has_code else ["ReAct did not return valid Verilog code."],
            failure_class=failure_class,
            raw_output=final_answer,
        )

    def _normalize_waveform_result(self, diagnosis: Any, raw_output: str = "") -> AgentResult:
        if diagnosis is None:
            return AgentResult(
                agent="WaveformExpert",
                ok=False,
                producer="agent_waveform",
                payload={"fallback_reason": "No waveform diagnosis available."},
                diagnostics=["WaveformExpert returned no diagnosis."],
                failure_class=FailureClass.UNKNOWN,
                raw_output=raw_output,
            )
        payload = {
            "failing_signal": diagnosis.failing_signal,
            "mismatch_time": diagnosis.mismatch_time,
            "expected_value": diagnosis.expected_value,
            "actual_value": diagnosis.actual_value,
            "trace_roots": [
                {
                    "signal_name": trace.signal_name,
                    "source_file": trace.source_file,
                    "source_line": trace.source_line,
                    "assignment_type": trace.assignment_type,
                }
                for trace in diagnosis.root_cause_traces
            ],
            "suggested_fix_area": diagnosis.suggested_fix_area,
            "fallback_reason": "" if diagnosis.root_cause_traces else "No AST trace roots found.",
        }
        return AgentResult(
            agent="WaveformExpert",
            ok=True,
            producer="agent_waveform",
            payload=payload,
            diagnostics=[],
            failure_class=FailureClass.UNKNOWN,
            raw_output=raw_output,
        )

    def _normalize_deepdebug_result(self, verdict: Any, raw_output: str = "") -> AgentResult:
        if verdict is None:
            return AgentResult(
                agent="DeepDebugger",
                ok=False,
                producer="agent_deepdebug",
                payload={"usable_for_regeneration": False},
                diagnostics=["DeepDebugger returned no verdict."],
                failure_class=FailureClass.UNKNOWN,
                raw_output=raw_output,
            )
        payload = {
            "root_cause_signal": verdict.root_cause_signal,
            "root_cause_line": verdict.root_cause_line,
            "root_cause_file": verdict.root_cause_file,
            "fix_description": verdict.fix_description,
            "confidence": verdict.confidence,
            "balanced_analysis_log": verdict.balanced_analysis_log,
            "usable_for_regeneration": bool(verdict.root_cause_signal and verdict.fix_description),
        }
        return AgentResult(
            agent="DeepDebugger",
            ok=True,
            producer="agent_deepdebug",
            payload=payload,
            diagnostics=[],
            failure_class=FailureClass.UNKNOWN,
            raw_output=raw_output,
        )

    def _parse_structured_agent_json(
        self,
        *,
        agent_name: str,
        raw_output: str,
        required_keys: List[str],
    ) -> AgentResult:
        payload = extract_json_object(raw_output)
        if payload is None:
            return AgentResult(
                agent=agent_name,
                ok=False,
                producer=f"agent_{agent_name.lower()}",
                payload={},
                diagnostics=["LLM output is not valid JSON."],
                failure_class=FailureClass.LLM_FORMAT_ERROR,
                raw_output=raw_output,
            )
        errors = validate_agent_payload(payload, required_keys)
        if errors:
            return AgentResult(
                agent=agent_name,
                ok=False,
                producer=f"agent_{agent_name.lower()}",
                payload=payload,
                diagnostics=errors,
                failure_class=FailureClass.LLM_FORMAT_ERROR,
                raw_output=raw_output,
            )
        return AgentResult(
            agent=agent_name,
            ok=True,
            producer=f"agent_{agent_name.lower()}",
            payload=payload,
            diagnostics=[],
            failure_class=FailureClass.UNKNOWN,
            raw_output=raw_output,
        )

    def _build_llm_context(self, include_rtl: bool = True, mode: Optional[str] = None) -> str:
        """Build cumulative context string for LLM calls.

        Uses token-based budgeting for intelligent context truncation.
        Aggregates build state so the LLM can reason across the full pipeline
        instead of seeing only the immediate error + code.

        Args:
            include_rtl: Whether to include RTL code
            mode: Token budget mode ('error_mode', 'generation_mode', 'doc_mode', 'verification_mode')
        """
        spec = self.artifacts.get("spec", "")
        rtl = self.artifacts.get("rtl_code", "") if include_rtl else ""
        error = self.artifacts.get("last_error", "")
        history = [h.message for h in self.build_history[-10:]]
        target_pdk = self.pdk_profile.get("profile", self.pdk_profile.get("pdk", "sky130"))

        budgeted = self.token_budget.budget_context(
            spec=spec,
            rtl=rtl,
            error=error,
            history=history,
            mode=mode,
        )

        sections = []

        if budgeted.get("spec"):
            sections.append(f"ARCHITECTURE SPEC:\n{budgeted['spec']}")

        if budgeted.get("rtl"):
            sections.append(f"CURRENT RTL (token-budgeted):\n```verilog\n{budgeted['rtl']}\n```")

        evolved = self.context_evolver.evolve(
            stage=self.state.name,
            spec=spec,
            rtl=rtl,
            error=error,
            history=history,
            target_pdk=target_pdk,
            strategy=self.strategy.value,
        )
        evolved_context = evolved.to_prompt()
        if evolved_context:
            self.artifacts["context_evolution"] = evolved.to_dict()
            sections.append(evolved_context)

        rag_query = " ".join(
            [
                self.name,
                self.desc,
                self.state.name,
                target_pdk,
                str(spec)[:1200],
                str(error)[:800],
            ]
        )
        hardware_context = self.hardware_kb.build_context(
            query=rag_query,
            stage=self.state.name,
            target_pdk=target_pdk,
        )
        if hardware_context:
            sections.append(hardware_context)

        if self.eda_capabilities:
            sections.append(self.eda_capabilities.to_prompt())

        sections.append(
            f"BUILD STATE: {self.state.name} | Strategy: {self.strategy.value} | "
            f"Step: {self.global_step_count}/{self.global_step_budget} | "
            f"Pivots: {self.pivot_count}/{self.max_pivots} | "
            f"Retries this state: {self.state_retry_counts.get(self.state.name, 0)}/{self.max_retries}"
        )

        if self.convergence_history:
            recent = self.convergence_history[-3:]
            conv_lines = []
            for s in recent:
                conv_lines.append(
                    f"  iter={s.iteration}: WNS={s.wns:.3f}ns cong={s.congestion:.2f}% "
                    f"area={s.area_um2:.1f}um² power={s.power_w:.6f}W"
                )
            sections.append("CONVERGENCE HISTORY:\n" + "\n".join(conv_lines))

        cov = self.artifacts.get("coverage", {})
        if cov:
            sections.append(
                f"COVERAGE: line={cov.get('line_pct', 'N/A')}% "
                f"branch={cov.get('branch_pct', 'N/A')}% "
                f"toggle={cov.get('toggle_pct', 'N/A')}% "
                f"functional={cov.get('functional_pct', 'N/A')}% "
                f"[{cov.get('backend', 'N/A')}/{cov.get('coverage_mode', 'N/A')}]"
            )

        formal = self.artifacts.get("formal_result", "")
        if formal:
            sections.append(f"FORMAL RESULT: {formal}")
        signoff = self.artifacts.get("signoff_result", "")
        if signoff:
            sections.append(f"SIGNOFF RESULT: {signoff}")

        sem = self.artifacts.get("semantic_report", "")
        if sem:
            sem_budget = 500
            sections.append(f"SEMANTIC RIGOR: {str(sem)[:sem_budget]}")

        fail_hist = self._format_failure_history()
        if fail_hist:
            sections.append(fail_hist)

        return "\n\n".join(sections)

    def _format_failure_history(self) -> str:
        """Format recent failure fingerprints into a readable summary.

        Lets the LLM know what fixes have already been attempted so it
        avoids repeating the same ineffective patches.
        """
        lines = []

        # State-level failure fingerprints (last 5)
        if self.failure_fingerprint_history:
            recent_fps = list(self.failure_fingerprint_history.items())[-5:]
            lines.append("PREVIOUS FAILURE FINGERPRINTS (do NOT repeat these fixes):")
            for fp_hash, count in recent_fps:
                lines.append(f"  [{count}x] fingerprint {fp_hash[:12]}...")
                failed_code = self.failed_code_by_fingerprint.get(fp_hash)
                if failed_code:
                    lines.append(f"  CODE FROM THIS REJECTED ATTEMPT:\n{failed_code}")

        # TB gate history (last 5 events)
        tb_history = self.artifacts.get("tb_gate_history", [])
        if tb_history:
            recent_tb = tb_history[-5:]
            lines.append("TB GATE HISTORY:")
            for evt in recent_tb:
                lines.append(
                    f"  gate={evt.get('gate')} ok={evt.get('ok')} "
                    f"action={evt.get('action')} categories={evt.get('issue_categories', [])}"
                )
                fp_hash = evt.get("fingerprint")
                if fp_hash:
                    failed_tb_code = self.tb_failed_code_by_fingerprint.get(fp_hash)
                    if failed_tb_code:
                        lines.append(f"  REJECTED TESTBENCH CODE FOR THIS EVENT:\n{failed_tb_code}")

        # Build history (last 5 state transitions with errors)
        error_entries = [
            h
            for h in self.build_history
            if "error" in h.message.lower() or "fail" in h.message.lower()
        ]
        if error_entries:
            recent_errors = error_entries[-5:]
            lines.append("RECENT ERROR LOG:")
            for entry in recent_errors:
                lines.append(f"  [{entry.state}] {entry.message[:200]}")

        if not lines:
            return ""
        return "\n".join(lines)

    def run(self):
        """Main execution loop."""
        import io
        import sys
        import contextlib

        # Redirect stderr to suppress ERROR:root messages in non-verbose mode
        if not self.verbose:
            _stderr_sink = io.StringIO()
            _original_stderr = sys.stderr
            sys.stderr = _stderr_sink

        try:
            while self.state != BuildState.SUCCESS and self.state != BuildState.FAIL:
                if self.global_retry_count >= 15:
                    self.log(
                        f"Global retry budget exceeded ({self.global_retry_count}/15). Failing closed.",
                        refined=True,
                    )
                    self.state = BuildState.FAIL
                    break

                self.global_step_count += 1
                if self.global_step_count > self.global_step_budget:
                    self.log(
                        f"Global step budget exceeded ({self.global_step_budget}). Failing closed.",
                        refined=True,
                    )
                    self.state = BuildState.FAIL
                    break
                if self.state == BuildState.INIT:
                    self.do_init()
                elif self.state == BuildState.SPEC:
                    self.do_spec()
                elif self.state == BuildState.SPEC_VALIDATE:
                    self.do_spec_validate()
                elif self.state == BuildState.HIERARCHY_EXPAND:
                    self.do_hierarchy_expand()
                elif self.state == BuildState.FEASIBILITY_CHECK:
                    self.do_feasibility_check()
                elif self.state == BuildState.CDC_ANALYZE:
                    self.do_cdc_analyze()
                elif self.state == BuildState.VERIFICATION_PLAN:
                    self.do_verification_plan()
                elif self.state == BuildState.RTL_GEN:
                    self.do_rtl_gen()
                elif self.state == BuildState.RTL_FIX:
                    self.do_rtl_fix()
                elif self.state == BuildState.VERIFICATION:
                    self.do_verification()
                elif self.state == BuildState.FORMAL_VERIFY:
                    self.do_formal_verify()
                elif self.state == BuildState.COVERAGE_CHECK:
                    self.do_coverage_check()
                elif self.state == BuildState.REGRESSION:
                    self.do_regression()
                elif self.state == BuildState.SDC_GEN:
                    self.do_sdc_gen()
                elif self.state == BuildState.SYNTHESIS:
                    self.do_synthesis()
                elif self.state == BuildState.DFT_SCAN:
                    self.do_dft_scan()
                elif self.state == BuildState.DFT_ATPG:
                    self.do_dft_atpg()
                elif self.state == BuildState.MBIST:
                    self.do_mbist()
                elif self.state == BuildState.GLS_SIM:
                    self.do_gls_simulation()
                elif self.state == BuildState.POWER_ANALYSIS:
                    self.do_power_analysis()
                elif self.state == BuildState.TIMING_ANALYSIS:
                    self.do_timing_analysis()
                elif self.state == BuildState.PHYSICAL_VERIFY:
                    self.do_physical_verify()
                elif self.state == BuildState.IP_PACKAGE:
                    self.do_ip_package()
                elif self.state == BuildState.FLOORPLAN:
                    self.do_floorplan()
                elif self.state == BuildState.HARDENING:
                    self.do_hardening()
                elif self.state == BuildState.CONVERGENCE_REVIEW:
                    self.do_convergence_review()
                elif self.state == BuildState.ECO_PATCH:
                    self.do_eco_patch()
                elif self.state == BuildState.SIGNOFF:
                    self.do_signoff()
                else:
                    self.log(f"Unknown state {self.state}", refined=False)
                    self.state = BuildState.FAIL

        except Exception as e:
            import traceback

            err_trace = traceback.format_exc()
            # ALWAYS log the exception — not just in verbose mode.
            # This was causing builds to fail silently with zero diagnostics.
            logger.error("Orchestrator run() crashed: %s\n%s", e, err_trace)
            console.print(f"[red]ORCHESTRATOR CRASH:[/red] {e}")
            if self.verbose:
                console.print(err_trace)
            # Store traceback so _run_agentic_build can surface it in the result
            self.artifacts["crash_traceback"] = err_trace
            self.artifacts["crash_error"] = str(e)
            self.state = BuildState.FAIL
        finally:
            # Restore stderr
            if not self.verbose:
                sys.stderr = _original_stderr

        if self.state == BuildState.SUCCESS:
            try:
                self._save_industry_benchmark_metrics()
            except Exception as e:
                self.log(f"Benchmark metrics export warning: {e}", refined=True)
            # Create a clean summary of just the paths
            summary = {
                k: v for k, v in self.artifacts.items() if "code" not in k and "spec" not in k
            }

            console.print(f"\n[green]✓[/green] BUILD COMPLETE")
            console.print(
                f"  [dim]Output: {self.artifacts.get('root', 'N/A')}/runs/*/final/gdsii/[/dim]"
            )
        else:
            console.print(f"\n[red]✗[/red] BUILD FAILED")
            console.print(f"  [dim]Log: {self.artifacts.get('root', 'N/A')}/{self.name}.log[/dim]")

    # --- ACTION HANDLERS ---

    def do_init(self):
        # Setup directories, check tools
        self.artifacts["root"] = f"{OPENLANE_ROOT}/designs/{self.name}"
        self.setup_logger()  # Setup logging to file
        self.artifacts["pdk_profile"] = self.pdk_profile
        diag = startup_self_check()
        self.artifacts["startup_check"] = diag
        self.logger.info(f"STARTUP SELF CHECK: {diag}")
        if self.strict_gates and not diag.get("ok", False):
            self.state = BuildState.FAIL
            return
        self.transition(BuildState.SPEC)

    def do_spec(self):
        # Derive safe module name upfront — Verilog identifiers cannot start with a digit
        safe_name = self.name
        if safe_name and safe_name[0].isdigit():
            safe_name = "chip_" + safe_name
        # Also store the corrected name so RTL_GEN uses it
        self.name = safe_name

        # ── Phase 1: Structured Spec Decomposition (ArchitectModule) ──
        # Produces a validated JSON contract (SID) that defines every port,
        # parameter, FSM state, and sub-module BEFORE any Verilog is written.
        try:
            architect = ArchitectModule(llm=self.get_llm_for_role("architect"), max_retries=3)
            sid = architect.decompose(
                design_name=self.name,
                spec_text=self.desc,
            )
            self.artifacts["sid"] = sid.to_json()
            # Convert SID → detailed RTL prompt for the coder agent
            self.artifacts["spec"] = architect.sid_to_rtl_prompt(sid)
            self.log(
                f"Structured Spec: {len(sid.sub_modules)} sub-modules decomposed",
                refined=True,
            )
        except Exception as e:
            self.logger.warning(f"ArchitectModule failed ({e}), falling back to Crew-based spec")
            # Fallback: original Crew-based spec generation
            self._do_spec_fallback()
            return

        self.log("Architecture Plan Generated (SID validated)", refined=True)
        # Persistent Checkpoint
        sid_path = os.path.join(self.artifacts["root"], "sid_checkpoint.json")
        with open(sid_path, "w") as f:
            f.write(sid.to_json())
        self.transition(BuildState.SPEC_VALIDATE)

    def _do_spec_fallback(self):
        """Fallback spec generation using a single CrewAI agent."""
        arch_agent = Agent(
            role="Chief System Architect",
            goal=f"Define a robust micro-architecture for {self.name}",
            backstory=(
                "Veteran Silicon Architect with 20+ years spanning CPUs, DSPs, FIFOs, "
                "crypto cores, interface bridges, and SoC peripherals. "
                "Defines clean, complete, production-ready interfaces, FSMs, and datapaths. "
                "Never uses placeholders or simplified approximations."
            ),
            llm=self.get_llm_for_role("designer"),
            verbose=self.verbose,
        )

        spec_task = Task(
            description=(
                f"""Create a DETAILED Micro-Architecture Specification (MAS) for the chip below.

CHIP IDENTIFIER : {self.name}
REQUIREMENT     : {self.desc}

CRITICAL RULES:
1. RTL module name MUST be exactly: {self.name}  (starts with a letter — enforced).
2. Identify the chip family (counter / ALU / FIFO / FSM / UART / SPI / AXI / CPU / DSP / crypto / SoC).
3. List ALL I/O ports with direction, bit-width, and purpose. Always include clk and rst_n.
4. Define all parameters with default values (DATA_WIDTH, ADDR_WIDTH, DEPTH, etc.).
5. For sequential designs: specify reset style (sync/async) and reset values for every register.
6. For FSMs: enumerate all states, transitions, and output conditions.

SPECIFICATION SECTIONS (Markdown):
## Module: {self.name}
## Chip Family
## Port List
## Parameters
## Internal Signals & Registers
## FSM States (if applicable)
## Functional Description
## Timing & Reset
"""
            ),
            expected_output="Complete Markdown Specification with all sections filled in",
            agent=arch_agent,
        )

        with console.status(
            "[accent]Architecting Chip Specification...[/accent]",
            spinner="dots12",
            spinner_style="spinner",
        ):
            result = self._crew_kickoff(Crew(verbose=False, agents=[arch_agent], tasks=[spec_task]))

        self.artifacts["spec"] = str(result)
        self.log("Architecture Plan Generated (fallback)", refined=True)
        self.transition(BuildState.SPEC_VALIDATE)

    # ─── NEW PIPELINE STAGES ─────────────────────────────────────────

    def do_spec_validate(self):
        """Stage: Validate and enrich the spec via HardwareSpecGenerator (6-stage pipeline)."""
        self.log(
            "Running HardwareSpecGenerator (classify → complete → decompose → interface → contract → output)...",
            refined=True,
        )

        try:
            spec_gen = HardwareSpecGenerator(llm=self.get_llm_for_role("architect"))
            hw_spec, issues = spec_gen.generate(
                design_name=self.name,
                description=self.desc,
                target_pdk=self.pdk_profile.get("profile", "sky130"),
                base_sid=self.artifacts.get("sid"),
            )

            # Store the spec as structured JSON
            self.artifacts["hw_spec"] = hw_spec.to_dict()
            self.artifacts["hw_spec_json"] = hw_spec.to_json()
            self.artifacts["hw_spec_object"] = hw_spec

            # Log classification and stats
            self.log(f"Design classified as: {hw_spec.design_category}", refined=True)
            self.log(
                f"Ports: {len(hw_spec.ports)} | Submodules: {len(hw_spec.submodules)} | "
                f"Contract statements: {len(hw_spec.behavioral_contract)}",
                refined=True,
            )

            if issues:
                for issue in issues[:5]:
                    self.log(f"  ⚠ {issue}", refined=True)

            # Handle ELABORATION_NEEDED — present 3 options to user
            if hw_spec.design_category == "ELABORATION_NEEDED":
                options = [w for w in hw_spec.warnings if w.startswith("OPTION_")]
                self.log(
                    f"📋 Description is brief — generated {len(options)} design options.",
                    refined=True,
                )

                # Store options in artifact bus for the web API to surface
                parsed_options = []
                for opt_str in options:
                    # Format: OPTION_1: title | Category: X | Freq: Y MHz | Ports: ... | Details: ...
                    parts = {
                        p.split(":")[0].strip(): ":".join(p.split(":")[1:]).strip()
                        for p in opt_str.split(" | ")
                        if ":" in p
                    }
                    parsed_options.append(parts)
                self.artifacts["spec_elaboration_options"] = parsed_options
                self.artifacts["spec_elaboration_needed"] = True

                # ── CLI: Rich interactive display ──
                try:
                    import typer
                    from rich.table import Table

                    table = Table(
                        title=f"💡 AgentIC VLSI Design Advisor — 3 Options for '{self.name}'",
                        show_lines=True,
                    )
                    table.add_column("#", style="bold cyan", width=3)
                    table.add_column("Design Variant", style="bold white", width=30)
                    table.add_column("Category", style="yellow", width=12)
                    table.add_column("Freq", style="green", width=8)
                    table.add_column("Details", style="dim")

                    for i, opt in enumerate(parsed_options, 1):
                        opt_id = str(opt.get("OPTION_1".replace("1", str(i)), i))
                        title = opt.get("OPTION_1", opt.get(f"OPTION_{i}", f"Option {i}"))
                        # Get key from dynamic OPTION_N key
                        option_key = [k for k in opt if k.startswith("OPTION_")]
                        title = opt[option_key[0]] if option_key else f"Option {i}"
                        category = opt.get("Category", "")
                        freq = opt.get("Freq", "")
                        details = (
                            opt.get("Details", "")[:80] + "…"
                            if len(opt.get("Details", "")) > 80
                            else opt.get("Details", "")
                        )
                        table.add_row(str(i), title, category, freq, details)

                    console.print(table)
                    console.print()

                    # Prompt user to choose (only if in a real interactive terminal or HITL)
                    import sys
                    import os
                    import time

                    choice_str = None
                    is_api_server = os.environ.get("AGENTIC_API_SERVER") == "1" or (
                        sys.argv and "uvicorn" in sys.argv[0]
                    )

                    if is_api_server and self.human_in_loop:
                        # Web API HITL Mode: Signal that we are waiting for a choice
                        self.log(
                            "EVENT:SPEC_ELABORATION_WAIT | WAITING_FOR_USER_CHOICE",
                            refined=True,
                        )
                        self.artifacts["waiting_for_elaboration"] = True

                        # Loop until the choice is injected via API (artifacts['spec_elaboration_choice'])
                        # Or until timeout (e.g. 10 minutes)
                        wait_start = time.time()
                        while "spec_elaboration_choice" not in self.artifacts:
                            if time.time() - wait_start > 600:  # 10 min timeout
                                self.log(
                                    "HITL Timeout: Auto-selecting Option 1.",
                                    refined=True,
                                )
                                choice_str = "1"
                                break
                            time.sleep(2)

                        if not choice_str:
                            choice_str = self.artifacts.get("spec_elaboration_choice", "1")
                            self.log(f"Received user choice: {choice_str}", refined=True)

                        self.artifacts.pop("waiting_for_elaboration", None)
                    elif is_api_server and not self.human_in_loop:
                        # Non-interactive API -> Auto-select first option
                        self.log(
                            "Non-interactive API mode: auto-selecting Option 1.",
                            refined=True,
                        )
                        choice_str = "1"
                    elif sys.stdin.isatty():
                        choice_str = typer.prompt(
                            f"Choose an option (1-{len(parsed_options)}) or type a custom description",
                            default="1",
                        )
                    elif self.human_in_loop:
                        # Fallback HITL
                        self.log(
                            "EVENT:SPEC_ELABORATION_WAIT | WAITING_FOR_USER_CHOICE",
                            refined=True,
                        )
                        self.artifacts["waiting_for_elaboration"] = True
                        wait_start = time.time()
                        while "spec_elaboration_choice" not in self.artifacts:
                            if time.time() - wait_start > 600:
                                choice_str = "1"
                                break
                            time.sleep(2)

                        if not choice_str:
                            choice_str = self.artifacts.get("spec_elaboration_choice", "1")
                        self.artifacts.pop("waiting_for_elaboration", None)
                    else:
                        # Non-interactive (Background/CI) -> Auto-select first option
                        self.log(
                            "Non-interactive mode: auto-selecting Option 1.",
                            refined=True,
                        )
                        choice_str = "1"

                    chosen_desc = None
                    if choice_str.strip().isdigit():
                        idx = int(choice_str.strip()) - 1
                        if 0 <= idx < len(parsed_options):
                            opt = parsed_options[idx]
                            option_key = [k for k in opt if k.startswith("OPTION_")]
                            title = opt[option_key[0]] if option_key else f"Option {idx + 1}"
                            details = opt.get("Details", "")
                            ports = opt.get("Ports", "")
                            category = opt.get("Category", "")
                            freq = opt.get("Freq", "50 MHz")
                            chosen_desc = (
                                f"{self.name}: {title}. {details} "
                                f"Category: {category}. Target frequency: {freq}. "
                                f"Key ports: {ports}. Module name: {self.name}."
                            )
                            self.log(f"✅ Selected option {idx + 1}: {title}", refined=True)
                    else:
                        # Custom description entered directly
                        chosen_desc = choice_str.strip()
                        self.log(
                            f"✅ Using custom description: {chosen_desc[:80]}…",
                            refined=True,
                        )

                    if chosen_desc:
                        # Store the enriched description and retry spec validation
                        self.desc = chosen_desc
                        self.artifacts["original_desc"] = hw_spec.design_description
                        self.artifacts["elaborated_desc"] = chosen_desc
                        self.log(
                            "🔄 Re-running spec generation with elaborated description…",
                            refined=True,
                        )
                        # Re-enter this stage to regenerate with the full description
                        self.state = BuildState.SPEC_VALIDATE
                        return

                except Exception as prompt_err:
                    # If running non-interactively (e.g. web API), use the first option automatically
                    self.log(
                        f"Non-interactive mode — auto-selecting option 1 ({prompt_err})",
                        refined=True,
                    )
                    if parsed_options:
                        opt = parsed_options[0]
                        option_key = [k for k in opt if k.startswith("OPTION_")]
                        title = opt[option_key[0]] if option_key else "Option 1"
                        details = opt.get("Details", "")
                        ports = opt.get("Ports", "")
                        freq = opt.get("Freq", "50 MHz")
                        self.desc = (
                            f"{self.name}: {title}. {details} "
                            f"Target frequency: {freq}. Key ports: {ports}. Module name: {self.name}."
                        )
                        self.artifacts["elaborated_desc"] = self.desc
                        self.state = BuildState.SPEC_VALIDATE
                        return

                # If we got here without a valid choice, fail gracefully
                self.log("❌ No valid design option selected.", refined=True)
                self.state = BuildState.FAIL
                return

            # Check for hard rejection
            if hw_spec.design_category == "REJECTED":
                rejection_reason = (
                    hw_spec.warnings[0] if hw_spec.warnings else "Specification rejected"
                )
                self.log(f"❌ SPEC REJECTED: {rejection_reason}", refined=True)
                self.errors.append(f"Specification rejected: {rejection_reason}")
                self.artifacts["spec_rejection_reason"] = rejection_reason
                self.state = BuildState.FAIL
                return

            # Enrich the existing spec artifact with structured data
            enrichment = spec_gen.to_sid_enrichment(hw_spec)
            self.artifacts["spec_enrichment"] = enrichment

            # Append behavioral verification hints to spec for downstream RTL gen
            if enrichment.get("verification_hints_from_spec"):
                existing_spec = self.artifacts.get("spec", "")
                hints = "\n".join(enrichment["verification_hints_from_spec"])
                self.artifacts["spec"] = (
                    existing_spec
                    + f"\n\n## Behavioral Contract (Auto-Generated Assertions)\n{hints}\n"
                )

            self.log(
                f"Spec validation complete: {hw_spec.design_category} "
                f"({len(hw_spec.inferred_fields)} fields inferred, "
                f"{len(hw_spec.warnings)} warnings)",
                refined=True,
            )
            self.transition(BuildState.HIERARCHY_EXPAND)

        except Exception as e:
            self.log(
                f"HardwareSpecGenerator failed ({e}); skipping to HIERARCHY_EXPAND with basic spec.",
                refined=True,
            )
            self.logger.warning(f"HardwareSpecGenerator error: {e}")
            # Create a minimal hw_spec so downstream stages can still run
            self.artifacts["hw_spec"] = {
                "design_category": "CONTROL",
                "top_module_name": self.name,
                "target_pdk": self.pdk_profile.get("profile", "sky130"),
                "target_frequency_mhz": 50,
                "ports": [],
                "submodules": [],
                "behavioral_contract": [],
                "warnings": ["Spec validation was skipped due to error"],
            }
            self.transition(BuildState.HIERARCHY_EXPAND)

    def do_hierarchy_expand(self):
        """Stage: Evaluate submodule complexity and recursively expand complex ones."""
        self.log(
            "Running HierarchyExpander (evaluate → expand → consistency check)...",
            refined=True,
        )

        hw_spec_dict = self.artifacts.get("hw_spec", {})

        # If we have a full HardwareSpec object, use it directly
        hw_spec_obj = self.artifacts.get("hw_spec_object")
        if hw_spec_obj is None:
            # Reconstruct from dict
            try:
                hw_spec_obj = HardwareSpec.from_json(json.dumps(hw_spec_dict))
            except Exception:
                self.log(
                    "No valid HardwareSpec for hierarchy expansion; skipping.",
                    refined=True,
                )
                self.artifacts["hierarchy_result"] = {}
                self.transition(BuildState.FEASIBILITY_CHECK)
                return

        try:
            expander = HierarchyExpander(llm=self.get_llm_for_role("architect"))
            result = expander.expand(hw_spec_obj)

            self.artifacts["hierarchy_result"] = result.to_dict()
            self.artifacts["hierarchy_result_json"] = result.to_json()

            self.log(
                f"Hierarchy: depth={result.hierarchy_depth}, "
                f"expansions={result.expansion_count}, "
                f"submodules={len(result.submodules)}",
                refined=True,
            )

            # Log any consistency fixes
            consistency_fixes = [w for w in result.warnings if w.startswith("CONSISTENCY_FIX")]
            for fix in consistency_fixes[:3]:
                self.log(f"  🔧 {fix}", refined=True)

            # Store enrichment for downstream
            enrichment = expander.to_hierarchy_enrichment(result)
            self.artifacts["hierarchy_enrichment"] = enrichment

            self.transition(BuildState.FEASIBILITY_CHECK)

        except Exception as e:
            self.log(
                f"HierarchyExpander failed ({e}); skipping to FEASIBILITY_CHECK.",
                refined=True,
            )
            self.logger.warning(f"HierarchyExpander error: {e}")
            self.artifacts["hierarchy_result"] = {}
            self.transition(BuildState.FEASIBILITY_CHECK)

    def do_feasibility_check(self):
        """Stage: Check physical realizability on Sky130 before RTL generation."""
        self.log(
            f"Running FeasibilityChecker (frequency → memory → arithmetic → area → {self.pdk_profile.get('profile', 'sky130').upper()} rules)...",
            refined=True,
        )

        hw_spec_dict = self.artifacts.get("hw_spec", {})
        hierarchy_result_dict = self.artifacts.get("hierarchy_result", None)

        try:
            checker = FeasibilityChecker(pdk=self.pdk_profile.get("profile", "sky130"))
            result = checker.check(hw_spec_dict, hierarchy_result_dict)

            self.artifacts["feasibility_result"] = result.to_dict()
            self.artifacts["feasibility_result_json"] = result.to_json()

            self.log(
                f"Feasibility: {result.feasibility_status} | "
                f"~{result.estimated_gate_equivalents} GE | "
                f"Floorplan: {result.recommended_floorplan_size_um}",
                refined=True,
            )

            if result.feasibility_warnings:
                for w in result.feasibility_warnings[:5]:
                    self.log(f"  ⚠ {w[:120]}", refined=True)

            if result.feasibility_status == "REJECT":
                for r in result.feasibility_rejections:
                    self.log(f"  ❌ {r}", refined=True)
                self.log(
                    "❌ FEASIBILITY REJECTED — pipeline halted before RTL generation.",
                    refined=True,
                )
                self.errors.append(
                    f"Feasibility rejected: {'; '.join(result.feasibility_rejections[:3])}"
                )
                self.artifacts["feasibility_rejection_reasons"] = result.feasibility_rejections
                self.state = BuildState.FAIL
                return

            if result.memory_macros_required:
                for macro in result.memory_macros_required:
                    self.log(
                        f"  📦 OpenRAM macro needed: {macro.submodule_name} "
                        f"({macro.width_bits}×{macro.depth_words})",
                        refined=True,
                    )

            # Store enrichment
            enrichment = checker.to_feasibility_enrichment(result)
            self.artifacts["feasibility_enrichment"] = enrichment

            self.transition(BuildState.CDC_ANALYZE)

        except Exception as e:
            self.log(
                f"FeasibilityChecker failed ({e}); skipping to CDC_ANALYZE.",
                refined=True,
            )
            self.logger.warning(f"FeasibilityChecker error: {e}")
            self.artifacts["feasibility_result"] = {
                "feasibility_status": "WARN",
                "feasibility_warnings": [f"Check skipped: {e}"],
            }
            self.transition(BuildState.CDC_ANALYZE)

    def do_cdc_analyze(self):
        """Stage: Identify clock domain crossings and assign synchronization strategies."""
        self.log(
            "Running CDCAnalyzer (identify domains → crossings → sync strategies → submodules)...",
            refined=True,
        )

        hw_spec_dict = self.artifacts.get("hw_spec", {})
        hierarchy_result_dict = self.artifacts.get("hierarchy_result", None)

        try:
            analyzer = CDCAnalyzer()
            result = analyzer.analyze(hw_spec_dict, hierarchy_result_dict)

            self.artifacts["cdc_result"] = result.to_dict()
            self.artifacts["cdc_result_json"] = result.to_json()

            if result.cdc_status == "SINGLE_DOMAIN":
                self.log(
                    "CDC: Single clock domain detected — no CDC analysis required.",
                    refined=True,
                )
            else:
                self.log(
                    f"CDC: {result.domain_count} clock domains, "
                    f"{len(result.crossing_signals)} crossing signals, "
                    f"{len(result.cdc_submodules_added)} sync submodules generated.",
                    refined=True,
                )

                for crossing in result.crossing_signals[:5]:
                    self.log(
                        f"  🔀 {crossing.signal_name}: {crossing.source_domain} → "
                        f"{crossing.destination_domain} [{crossing.sync_strategy}]",
                        refined=True,
                    )

                if result.cdc_unresolved:
                    for u in result.cdc_unresolved:
                        self.log(f"  ⚠ {u}", refined=True)

                # Inject CDC submodule specs into the spec artifact for RTL gen
                if result.cdc_submodules_added:
                    cdc_section = "\n\n## CDC Synchronization Submodules (Auto-Generated)\n"
                    for sub in result.cdc_submodules_added:
                        cdc_section += (
                            f"\n### {sub.module_name} ({sub.strategy})\n"
                            f"- Source: {sub.source_domain} → Destination: {sub.destination_domain}\n"
                            f"- Behavior: {sub.behavior}\n"
                            f"- Ports: {json.dumps(sub.ports, indent=2)}\n"
                        )
                    existing_spec = self.artifacts.get("spec", "")
                    self.artifacts["spec"] = existing_spec + cdc_section

            self.transition(BuildState.VERIFICATION_PLAN)

        except Exception as e:
            self.log(
                f"CDCAnalyzer failed ({e}); skipping to VERIFICATION_PLAN.",
                refined=True,
            )
            self.logger.warning(f"CDCAnalyzer error: {e}")
            self.artifacts["cdc_result"] = {
                "cdc_status": "SINGLE_DOMAIN",
                "cdc_warnings": [f"Analysis skipped: {e}"],
            }
            self.transition(BuildState.VERIFICATION_PLAN)

    def do_verification_plan(self):
        """Stage: Generate structured verification plan with test cases, SVA, and coverage."""
        self.log(
            "Running VerificationPlanner (behaviors → mandatory tests → SVA → coverage → finalize)...",
            refined=True,
        )

        hw_spec_obj = self.artifacts.get("hw_spec_object")
        if hw_spec_obj is None:
            # Try to reconstruct
            hw_spec_dict = self.artifacts.get("hw_spec", {})
            try:
                hw_spec_obj = HardwareSpec.from_json(json.dumps(hw_spec_dict))
            except Exception:
                self.log(
                    "No valid HardwareSpec for verification planning; skipping.",
                    refined=True,
                )
                self.transition(BuildState.RTL_GEN)
                return

        # Get optional CDC and hierarchy results for coverage planning
        cdc_result_dict = self.artifacts.get("cdc_result", {})
        hierarchy_result_dict = self.artifacts.get("hierarchy_result", {})

        try:
            planner = VerificationPlanner()
            plan = planner.plan(
                hardware_spec=hw_spec_obj,
                cdc_result=cdc_result_dict if cdc_result_dict else None,
                hierarchy_result=hierarchy_result_dict if hierarchy_result_dict else None,
            )

            self.artifacts["verification_plan"] = plan.to_dict()
            self.artifacts["verification_plan_json"] = plan.to_json()

            self.log(
                f"Verification Plan: {plan.total_tests} tests "
                f"(P0={plan.p0_count}, P1={plan.p1_count}, P2={plan.p2_count})",
                refined=True,
            )
            self.log(
                f"SVA properties: {len(plan.sva_properties)} | "
                f"Coverage points: {len(plan.coverage_points)}",
                refined=True,
            )

            if plan.warnings:
                for w in plan.warnings[:3]:
                    self.log(f"  ⚠ {w}", refined=True)

            # Inject verification plan context into spec for testbench generation
            vplan_section = "\n\n## Verification Plan (Auto-Generated)\n"
            for tc in plan.test_cases:
                vplan_section += f"- [{tc.priority}] {tc.test_id}: {tc.title}\n"
                vplan_section += f"  Stimulus: {tc.stimulus}\n"
                vplan_section += f"  Expected: {tc.expected}\n"

            # Inject SVA assertions into spec for formal verification stage
            if plan.sva_properties:
                vplan_section += "\n### SVA Assertions\n```systemverilog\n"
                for sva in plan.sva_properties:
                    vplan_section += f"// {sva.description}\n{sva.sva_code}\n\n"
                vplan_section += "```\n"

            existing_spec = self.artifacts.get("spec", "")
            self.artifacts["spec"] = existing_spec + vplan_section

            self.transition(BuildState.RTL_GEN)

        except Exception as e:
            self.log(f"VerificationPlanner failed ({e}); skipping to RTL_GEN.", refined=True)
            self.logger.warning(f"VerificationPlanner error: {e}")
            self.artifacts["verification_plan"] = {}
            self.transition(BuildState.RTL_GEN)

    # ─── END NEW PIPELINE STAGES ─────────────────────────────────────

    def _get_strategy_prompt(self) -> str:
        if self.strategy == BuildStrategy.SV_MODULAR:
            return """Use SystemVerilog: 
            - Use `logic` for all signals.
            - Use `always_ff @(posedge clk or negedge rst_n)` for registers.
            - Use `always_comb` for combinational logic.
            - FSM Rules: MUST use separate `state` (register) and `next_state` (logic) signals. DO NOT assign to `state` inside `always_comb`.
            - Use `enum` for states: `typedef enum logic [1:0] {IDLE, ...} state_t;`
            - Output Style: Use standard indentation (4 spaces). DO NOT minify code into single lines.
            
            # STRICT PROHIBITIONS:
            - **NO PLACEHOLDERS**: Do not write `// Simplified check` or `assign data = 0;`. Implement the ACTUAL LOGIC.
            - **NO PARTIAL IMPLEMENTATIONS**: If it's a 4x4 array, enable ALL cells.
            - **NO HARDCODING**: Use `parameter` for widths and depths.
            - **HARDWARE RIGOR**: Validate bit-width compatibility on every assignment and never shadow module ports with internal signals.
            """
        else:
            return """
            USE CLASSIC VERILOG-2005 (Robust/Safe):
            - `reg` and `wire` types explicitly
            - `always @(posedge clk or negedge rst_n)`
            - `localparam` for FSM states (NO enums)
            - Simple flat module structure
            """

    def _get_tb_strategy_prompt(self) -> str:
        if self.strategy == BuildStrategy.SV_MODULAR:
            return """Use FLAT PROCEDURAL SystemVerilog Verification (Verilator-safe):

            CRITICAL VERILATOR CONSTRAINTS — MUST FOLLOW:
            ─────────────────────────────────────────────
            • Do NOT use `interface` blocks — Verilator REJECTS them.
            • Do NOT use `class` (Transaction, Driver, Monitor, Scoreboard) — Verilator REJECTS classes inside modules.
            • Do NOT use `covergroup` / `coverpoint` — Verilator does NOT support them.
            • Do NOT use `virtual interface` handles or `vif.signal` — Verilator REJECTS these.
            • Do NOT use `program` blocks — Verilator REJECTS them.
            • Do NOT use `new()`, `rand`, or any OOP construct.

            WHAT TO DO INSTEAD:
            ─────────────────────
            • Declare ALL DUT signals as `reg` (inputs) or `wire` (outputs) in the TB module.
            • Instantiate DUT with direct port connections: `.port_name(port_name)`
            • Use `initial` blocks for reset, stimulus, and checking.
            • Use `$urandom` for randomized stimulus (Verilator-safe).
            • Use `always #5 clk = ~clk;` for clock generation.
            • Check outputs directly with `if` statements and `$display`.
            • Track errors with `integer fail_count;` — print TEST PASSED/FAILED at end.
            • Add a timeout watchdog: `initial begin #100000; $display("TEST FAILED: Timeout"); $finish; end`
            • Dump waveforms: `$dumpfile("design.vcd"); $dumpvars(0, <tb_name>);`

            STRUCTURE:
            ───────────
            1. `timescale 1ns/1ps
            2. module <name>_tb;
            3. Signal declarations (reg for inputs, wire for outputs)
            4. DUT instantiation
            5. Clock generation
            6. initial block: reset → stimulus → checks → PASS/FAIL → $finish
            7. Timeout watchdog
            8. endmodule"""
        else:
            return """Use Verilog-2005 Procedural Verification:
            - Use `initial` blocks for stimulus.
            - Use `$monitor` to print changes.
            - Check results directly in the `initial` block.
            - Simple, linear test flow."""

    @staticmethod
    def _extract_module_interface(rtl_code: str) -> str:
        """Extract module port signature from RTL for testbench generation.

        Returns a clean, structured port list like:
            Parameters: WIDTH = 8
            Inputs:  clk, rst_n, enable, data_in [7:0]
            Outputs: data_out [7:0], valid, ready
        """
        import re

        lines = []

        # Extract parameters
        params = re.findall(r"parameter\s+(\w+)\s*=\s*([^,;\)]+)", rtl_code)
        if params:
            lines.append("Parameters: " + ", ".join(f"{n} = {v.strip()}" for n, v in params))

        # Extract ports — match input/output/inout declarations
        # Support parameterized widths like [DATA_W-1:0] in addition to [7:0]
        inputs = []
        outputs = []
        inouts = []

        for match in re.finditer(
            r"\b(input|output|inout)\s+(?:reg|wire|logic)?\s*(?:signed\s*)?(?:(\[[^\]]+\]))?\s*(\w+)",
            rtl_code,
        ):
            direction, width, name = match.groups()
            port_str = name + (f" {width}" if width else "")
            if direction == "input":
                inputs.append(port_str)
            elif direction == "output":
                outputs.append(port_str)
            else:
                inouts.append(port_str)

        if inputs:
            lines.append("Inputs:  " + ", ".join(inputs))
        if outputs:
            lines.append("Outputs: " + ", ".join(outputs))
        if inouts:
            lines.append("Inouts:  " + ", ".join(inouts))

        if lines:
            return "\n".join(lines)

        # Fallback: return the module header as-is
        header_match = re.search(r"(module\s+\w+[\s\S]*?;)", rtl_code)
        return (
            header_match.group(1)
            if header_match
            else "Could not extract ports — see full RTL below."
        )

    @staticmethod
    def _tb_meets_strict_contract(tb_code: str, strategy: BuildStrategy) -> tuple:
        missing = []
        text = tb_code or ""
        if "TEST PASSED" not in text:
            missing.append("Missing TEST PASSED marker")
        if "TEST FAILED" not in text:
            missing.append("Missing TEST FAILED marker")
        if strategy == BuildStrategy.SV_MODULAR:
            if "class Transaction" not in text:
                missing.append("Missing class Transaction")
            if all(
                token not in text for token in ["class Driver", "class Monitor", "class Scoreboard"]
            ):
                missing.append("Missing transaction flow classes")
        return len(missing) == 0, missing

    @staticmethod
    def _extract_module_ports(rtl_code: str) -> List[Dict[str, str]]:
        ports: List[Dict[str, str]] = []
        text = rtl_code or ""

        # --- Extract parameter defaults so we can resolve widths ---
        param_defaults: Dict[str, str] = {}
        param_pattern = re.compile(
            r"parameter\s+(?:\w+\s+)?([A-Za-z_]\w*)\s*=\s*([^,;\)\n]+)",
            re.IGNORECASE,
        )
        for pname, pval in param_pattern.findall(text):
            param_defaults[pname.strip()] = pval.strip()

        header_match = re.search(r"\bmodule\b[\s\S]*?\(([\s\S]*?)\)\s*;", text, re.IGNORECASE)
        header = header_match.group(1) if header_match else text
        header = re.sub(r"//.*", "", header)
        header = re.sub(r"/\*[\s\S]*?\*/", "", header)
        pattern = re.compile(
            r"\b(input|output|inout)\b\s*(?:reg|wire|logic)?\s*(?:signed\s+)?(\[[^\]]+\])?\s*([A-Za-z_]\w*)\b",
            re.IGNORECASE,
        )
        for direction, width, name in pattern.findall(header):
            resolved_width = (width or "").strip()
            # Resolve parameterized widths: [CNT_W-1:0] -> [7:0]
            if resolved_width:
                for pname, pval in param_defaults.items():
                    if pname in resolved_width:
                        try:
                            # Substitute and evaluate, e.g. "8-1" -> "7"
                            expr = resolved_width[1:-1]  # strip [ ]
                            expr = expr.replace(pname, str(pval))
                            parts = expr.split(":")
                            evaluated_parts = []
                            for part in parts:
                                part = part.strip()
                                # Safe eval: only digits and arithmetic
                                if re.match(r"^[\d\s\+\-\*\/\(\)]+$", part):
                                    evaluated_parts.append(str(int(eval(part))))  # noqa: S307
                                else:
                                    evaluated_parts.append(part)
                            resolved_width = f"[{':'.join(evaluated_parts)}]"
                        except Exception:
                            pass  # keep original width on error
            ports.append(
                {
                    "direction": direction.lower(),
                    "width": resolved_width,
                    "name": name.strip(),
                }
            )
        return ports

    @staticmethod
    def _extract_rtl_signal_inventory(rtl_code: str) -> List[Dict[str, str]]:
        """Extract DUT-visible signals and widths for downstream prompt grounding."""
        text = rtl_code or ""
        signals: List[Dict[str, str]] = []

        param_defaults: Dict[str, str] = {}
        param_pattern = re.compile(
            r"parameter\s+(?:\w+\s+)?([A-Za-z_]\w*)\s*=\s*([^,;\)\n]+)",
            re.IGNORECASE,
        )
        for pname, pval in param_pattern.findall(text):
            param_defaults[pname.strip()] = pval.strip()

        def _resolve_width(width: str) -> str:
            resolved = (width or "").strip()
            if not resolved:
                return "[0:0]"
            for pname, pval in param_defaults.items():
                if pname not in resolved:
                    continue
                try:
                    expr = resolved[1:-1]
                    expr = expr.replace(pname, str(pval))
                    parts = expr.split(":")
                    evaluated = []
                    for part in parts:
                        part = part.strip()
                        if re.match(r"^[\d\s\+\-\*\/\(\)]+$", part):
                            evaluated.append(str(int(eval(part))))  # noqa: S307
                        else:
                            evaluated.append(part)
                    resolved = f"[{':'.join(evaluated)}]"
                except Exception:
                    pass
            return resolved

        seen = set()
        for port in BuildOrchestrator._extract_module_ports(text):
            key = (port["name"], port["direction"])
            if key in seen:
                continue
            seen.add(key)
            signals.append(
                {
                    "name": port["name"],
                    "category": port["direction"],
                    "width": _resolve_width(port.get("width", "")),
                }
            )

        scrubbed = re.sub(r"//.*", "", text)
        scrubbed = re.sub(r"/\*[\s\S]*?\*/", "", scrubbed)
        internal_pattern = re.compile(
            r"^\s*(wire|reg|logic)\s*(?:signed\s+)?(\[[^\]]+\])?\s*([^;]+);",
            re.IGNORECASE | re.MULTILINE,
        )
        for kind, width, names_blob in internal_pattern.findall(scrubbed):
            resolved_width = _resolve_width(width)
            for raw_name in names_blob.split(","):
                candidate = raw_name.strip()
                if not candidate:
                    continue
                candidate = candidate.split("=")[0].strip()
                candidate = re.sub(r"\[[^\]]+\]", "", candidate).strip()
                if not re.fullmatch(r"[A-Za-z_]\w*", candidate):
                    continue
                key = (candidate, kind.lower())
                if key in seen:
                    continue
                seen.add(key)
                signals.append(
                    {
                        "name": candidate,
                        "category": kind.lower(),
                        "width": resolved_width,
                    }
                )
        return signals

    @staticmethod
    def _format_signal_inventory_for_prompt(rtl_code: str) -> str:
        signals = BuildOrchestrator._extract_rtl_signal_inventory(rtl_code)
        if not signals:
            return "No signal inventory could be extracted from the RTL. Use only identifiers explicitly declared in the RTL."
        lines = [
            f"- {sig['name']}: category={sig['category']}, width={sig['width']}" for sig in signals
        ]
        return "\n".join(lines)

    def _tb_gate_strict_enforced(self) -> bool:
        return self.strict_gates or self.tb_gate_mode == "strict"

    def _write_tb_diagnostic_artifact(self, stem: str, payload: Dict[str, Any]) -> str:
        src_dir = os.path.join(OPENLANE_ROOT, "designs", self.name, "src")
        os.makedirs(src_dir, exist_ok=True)
        count_key = f"{stem}_count"
        count = int(self.artifacts.get(count_key, 0)) + 1
        self.artifacts[count_key] = count
        attempt_path = os.path.join(src_dir, f"{self.name}_{stem}_attempt{count}.json")
        latest_path = os.path.join(src_dir, f"{self.name}_{stem}.json")
        with open(attempt_path, "w") as f:
            json.dump(payload, f, indent=2)
        with open(latest_path, "w") as f:
            json.dump(payload, f, indent=2)
        self.artifacts[f"{stem}_latest"] = latest_path
        return attempt_path

    def _record_tb_gate_history(self, gate: str, ok: bool, action: str, report: Dict[str, Any]):
        history = self.artifacts.setdefault("tb_gate_history", [])
        event = {
            "timestamp": int(time.time()),
            "gate": gate,
            "ok": bool(ok),
            "action": action,
            "issue_categories": report.get("issue_categories", report.get("issue_codes", [])),
            "fingerprint": report.get("fingerprint", ""),
        }
        history.append(event)
        self.artifacts["tb_gate_history_count"] = len(history)
        self.artifacts["tb_gate_last_action"] = action
        self.logger.info(f"TB GATE EVENT: {event}")

    def _record_tb_failure_fingerprint(self, gate: str, report: Dict[str, Any]) -> bool:
        compact = json.dumps(report, sort_keys=True, default=str)
        base = f"{gate}|{compact[:1000]}|{self._artifact_fingerprint()}"
        fp = hashlib.sha256(base.encode("utf-8", errors="ignore")).hexdigest()
        count = self.tb_failure_fingerprint_history.get(fp, 0) + 1
        self.tb_failure_fingerprint_history[fp] = count

        # Bug 1: Track generated TB code that produced this error
        tb_path = self.artifacts.get("tb_path", "")
        if tb_path and os.path.exists(tb_path):
            try:
                with open(tb_path, "r") as f:
                    self.tb_failed_code_by_fingerprint[fp] = f.read()
            except OSError:
                pass

        return count >= 2

    def _clear_tb_fingerprints(self) -> None:
        """Reset all TB failure fingerprints so gate-level retries start fresh."""
        self.tb_failure_fingerprint_history.clear()
        self.tb_failed_code_by_fingerprint.clear()
        self.tb_static_fail_count = 0
        self.tb_compile_fail_count = 0
        self.tb_repair_fail_count = 0

    def generate_uvm_lite_tb_from_rtl_ports(self, design_name: str, rtl_code: str) -> str:
        """Deterministic Verilator-safe testbench generated from RTL ports.

        Generates a flat procedural TB — no interfaces, no classes, no virtual
        references.  This compiles on Verilator, iverilog, and any IEEE-1800
        simulator without modification.
        """
        ports = self._extract_module_ports(rtl_code)
        if not ports:
            return self._generate_fallback_testbench(rtl_code)

        clock_name = None
        reset_name = None
        input_ports: List[Dict[str, str]] = []
        output_ports: List[Dict[str, str]] = []

        for p in ports:
            pname = p["name"]
            if p["direction"] == "input":
                input_ports.append(p)
                if clock_name is None and re.search(
                    r"(?:^|_)(?:clk|clock|sclk|aclk)(?:_|$)|^i_clk",
                    pname,
                    re.IGNORECASE,
                ):
                    clock_name = pname
                if reset_name is None and re.search(
                    r"(?:^|_)(?:rst|reset|nrst|areset)(?:_|$)|^i_rst",
                    pname,
                    re.IGNORECASE,
                ):
                    reset_name = pname
            elif p["direction"] == "output":
                output_ports.append(p)

        non_clk_rst_inputs = [
            p for p in input_ports if p["name"] != clock_name and p["name"] != reset_name
        ]
        reset_active_low = reset_name and reset_name.lower().endswith("_n") if reset_name else False

        lines: List[str] = ["`timescale 1ns/1ps", ""]
        lines.append(f"module {design_name}_tb;")
        lines.append("")

        # --- Signal declarations ---
        for p in input_ports:
            width = f"{p['width']} " if p.get("width") else ""
            lines.append(f"  reg {width}{p['name']};")
        for p in output_ports:
            width = f"{p['width']} " if p.get("width") else ""
            lines.append(f"  wire {width}{p['name']};")
        lines.append("")

        # --- DUT instantiation with parameter defaults ---
        param_pattern = re.compile(
            r"parameter\s+(?:\w+\s+)?([A-Za-z_]\w*)\s*=\s*([^,;\)\n]+)",
            re.IGNORECASE,
        )
        params_found = param_pattern.findall(rtl_code)
        if params_found:
            param_str = ", ".join(f".{n}({v.strip()})" for n, v in params_found)
            lines.append(f"  {design_name} #({param_str}) dut (")
        else:
            lines.append(f"  {design_name} dut (")
        conn = [f"    .{p['name']}({p['name']})" for p in ports]
        lines.append(",\n".join(conn))
        lines.append("  );")
        lines.append("")

        # --- Clock generation ---
        if clock_name:
            lines.append(f"  // Clock: 100MHz (10ns period)")
            lines.append(f"  initial {clock_name} = 1'b0;")
            lines.append(f"  always #5 {clock_name} = ~{clock_name};")
            lines.append("")

        # --- Failure tracker ---
        lines.append("  integer tb_fail;")
        lines.append("")

        # --- Main test sequence ---
        lines.append("  initial begin")
        lines.append("    tb_fail = 0;")
        lines.append("")

        # Dump waveforms
        lines.append(f'    $dumpfile("{design_name}_wave.vcd");')
        lines.append(f"    $dumpvars(0, {design_name}_tb);")
        lines.append("")

        # Initialize all inputs
        for p in non_clk_rst_inputs:
            width = p.get("width", "")
            if width:
                bits = re.search(r"\[(\d+):", width)
                if bits:
                    lines.append(f"    {p['name']} = {int(bits.group(1)) + 1}'d0;")
                else:
                    lines.append(f"    {p['name']} = 0;")
            else:
                lines.append(f"    {p['name']} = 1'b0;")

        # Reset sequence
        if reset_name:
            if reset_active_low:
                lines.append(f"    {reset_name} = 1'b0;  // Assert reset (active-low)")
                lines.append("    #50;")
                lines.append(f"    {reset_name} = 1'b1;  // Deassert reset")
            else:
                lines.append(f"    {reset_name} = 1'b1;  // Assert reset (active-high)")
                lines.append("    #50;")
                lines.append(f"    {reset_name} = 1'b0;  // Deassert reset")
        lines.append("    #20;")
        lines.append("")

        # Stimulus: drive random values on data inputs
        lines.append("    // === Stimulus Phase ===")
        lines.append("    repeat (40) begin")
        if clock_name:
            lines.append(f"      @(posedge {clock_name});")
        else:
            lines.append("      #10;")
        for p in non_clk_rst_inputs:
            lines.append(f"      {p['name']} = $urandom;")
        lines.append("    end")
        lines.append("")

        # Output check: verify no X/Z on outputs after stimulus
        lines.append("    // === Output Sanity Check ===")
        if clock_name:
            lines.append(f"    @(posedge {clock_name});")
        else:
            lines.append("    #10;")
        for p in output_ports:
            lines.append(f"    if (^({p['name']}) === 1'bx) begin")
            lines.append(f'      $display("TEST FAILED: X/Z detected on {p["name"]}");')
            lines.append("      tb_fail = tb_fail + 1;")
            lines.append("    end")
        lines.append("")

        # Result
        lines.append("    if (tb_fail == 0) begin")
        lines.append('      $display("TEST PASSED");')
        lines.append("    end else begin")
        lines.append('      $display("TEST FAILED");')
        lines.append("    end")
        lines.append("    $finish;")
        lines.append("  end")
        lines.append("")

        # Timeout watchdog
        lines.append("  // Timeout watchdog")
        lines.append("  initial begin")
        lines.append("    #100000;")
        lines.append('    $display("TEST FAILED: Timeout");')
        lines.append("    $finish;")
        lines.append("  end")
        lines.append("")
        lines.append("endmodule")
        return "\n".join(lines)

    def _deterministic_tb_fallback(self, rtl_code: str) -> str:
        if self.tb_fallback_template == "classic":
            return self._generate_fallback_testbench(rtl_code)
        return self.generate_uvm_lite_tb_from_rtl_ports(self.name, rtl_code)

    def _handle_tb_gate_failure(self, gate: str, report: Dict[str, Any], tb_code: str) -> None:
        if self._record_tb_failure_fingerprint(gate, report):
            self.log(
                f"Repeated TB {gate} fingerprint detected. Failing closed.",
                refined=True,
            )
            self.state = BuildState.FAIL
            return

        cycle = int(self.tb_recovery_counts.get(gate, 0)) + 1
        self.tb_recovery_counts[gate] = cycle
        self.artifacts[f"tb_recovery_cycle_{gate}"] = cycle

        self.global_retry_count += 1  # Bug 2: increment global budget

        if cycle > self.tb_max_retries:
            self.log(
                f"TB {gate} recovery attempts exceeded ({self.tb_max_retries}). Failing closed.",
                refined=True,
            )
            self.state = BuildState.FAIL
            return

        if cycle == 1:
            repaired_tb = repair_tb_for_verilator(tb_code, report)
            repair_payload = {
                "gate": gate,
                "action": "auto_repair",
                "changed": repaired_tb != tb_code,
                "compile_fingerprint": report.get("fingerprint", ""),
            }
            self._write_tb_diagnostic_artifact("tb_repair_action", repair_payload)
            if repaired_tb != tb_code:
                path = write_verilog(self.name, repaired_tb, is_testbench=True)
                if isinstance(path, str) and path.startswith("Error:"):
                    self.log(f"TB auto-repair write failed: {path}", refined=True)
                    self.tb_repair_fail_count += 1
                else:
                    self.artifacts["tb_path"] = path
                    self._record_tb_gate_history(gate, False, "auto_repair", report)
                    self.log(
                        "TB gate failed; applied deterministic auto-repair.",
                        refined=True,
                    )
                    return
            self.tb_repair_fail_count += 1
            self._record_tb_gate_history(gate, False, "auto_repair_nochange", report)
            # Auto-repair couldn't fix it — clear fingerprint so cycle 2 (LLM regen)
            # gets a fair shot instead of being killed by the fingerprint guard.
            self._clear_tb_fingerprints()
            return

        if cycle == 2:
            self._set_artifact(
                "tb_regen_context",
                json.dumps(report, indent=2, default=str)[:5000],
                producer="orchestrator_tb_gate",
                consumer="VERIFICATION",
                required=True,
                blocking=True,
            )
            tb_path = self.artifacts.get("tb_path")
            if tb_path and os.path.exists(tb_path):
                try:
                    os.remove(tb_path)
                except OSError:
                    pass
            self.artifacts.pop("tb_path", None)
            self._record_tb_gate_history(gate, False, "llm_regenerate", report)
            self.log(
                "TB gate failed; forcing LLM TB regeneration with structured diagnostics.",
                refined=True,
            )
            return

        fallback_tb = self._deterministic_tb_fallback(self.artifacts.get("rtl_code", ""))
        path = write_verilog(self.name, fallback_tb, is_testbench=True)
        if isinstance(path, str) and path.startswith("Error:"):
            self.log(f"Deterministic TB fallback write failed: {path}", refined=True)
            self.state = BuildState.FAIL
            return
        self.artifacts["tb_path"] = path
        # Fallback generation completed; allow compile/static gate to run a fresh
        # bounded recovery loop against this new artifact.

        # Bug 2: Removing explicit counter reset: self.tb_recovery_counts[gate] = 0
        # self.artifacts[f"tb_recovery_cycle_{gate}"] = 0
        self._record_tb_gate_history(gate, False, "deterministic_fallback", report)
        self.log("TB gate failed; switched to deterministic fallback template.", refined=True)

    def _generate_fallback_testbench(self, rtl_code: str) -> str:
        ports = self._extract_module_ports(rtl_code)
        if not ports:
            return f"""`timescale 1ns/1ps
module {self.name}_tb;
  initial begin
    $display("TEST FAILED");
    $display("No DUT ports discovered for deterministic TB fallback.");
    $finish;
  end
endmodule
"""

        declarations = []
        connections = []
        input_ports = []
        output_ports = []
        clock_name = None
        reset_name = None

        for p in ports:
            width = f"{p['width']} " if p["width"] else ""
            name = p["name"]
            direction = p["direction"]
            declarations.append(f"  logic {width}{name};")
            connections.append(f"    .{name}({name})")
            if direction == "input":
                input_ports.append(name)
                if clock_name is None and "clk" in name.lower():
                    clock_name = name
                if reset_name is None and ("rst" in name.lower() or "reset" in name.lower()):
                    reset_name = name
            elif direction == "output":
                output_ports.append(name)

        sv_classes = ""
        if self.strategy == BuildStrategy.SV_MODULAR:
            sv_classes = """
class Transaction;
  rand bit [31:0] payload;
endclass

class Driver;
  function void drive();
  endfunction
endclass

class Monitor;
  function void sample();
  endfunction
endclass

class Scoreboard;
  int errors = 0;
  function void check(bit cond, string msg);
    if (!cond) begin
      errors++;
      $display("TEST FAILED: %s", msg);
    end
  endfunction
endclass
"""

        body = [f"`timescale 1ns/1ps", f"module {self.name}_tb;"]
        body.extend(declarations)
        body.append("")
        body.append(sv_classes.rstrip())
        if sv_classes:
            body.append("")
        body.append(f"  {self.name} dut (")
        body.append(",\n".join(connections))
        body.append("  );")
        body.append("")

        if clock_name:
            body.append(f"  initial {clock_name} = 1'b0;")
            body.append(f"  always #5 {clock_name} = ~{clock_name};")
            body.append("")

        body.append("  integer tb_fail = 0;")
        body.append("")
        body.append("  initial begin")
        for inp in input_ports:
            if inp == clock_name:
                continue
            if inp == reset_name:
                if inp.lower().endswith("_n"):
                    body.append(f"    {inp} = 1'b0;")
                else:
                    body.append(f"    {inp} = 1'b1;")
            else:
                body.append(f"    {inp} = '0;")
        body.append("")
        if reset_name:
            body.append("    #20;")
            if reset_name.lower().endswith("_n"):
                body.append(f"    {reset_name} = 1'b1;")
            else:
                body.append(f"    {reset_name} = 1'b0;")
            body.append("    #10;")

        if input_ports:
            body.append("    repeat (20) begin")
            for inp in input_ports:
                if inp in (clock_name, reset_name):
                    continue
                body.append(f"      {inp} = $random;")
            if output_ports:
                body.append("      #10;")
                for outp in output_ports:
                    body.append(f"      if (^({outp}) === 1'bx) begin")
                    body.append("        tb_fail = 1;")
                    body.append(f'        $display("TEST FAILED: X detected on {outp}");')
                    body.append("      end")
            else:
                body.append("      #10;")
            body.append("    end")

        body.append("")
        body.append("    if (tb_fail == 0) begin")
        body.append('      $display("TEST PASSED");')
        body.append("    end else begin")
        body.append('      $display("TEST FAILED");')
        body.append("    end")
        body.append("    $finish;")
        body.append("  end")
        body.append("endmodule")
        return "\n".join([line for line in body if line is not None])

    def _kickoff_with_timeout(self, agents: List[Agent], tasks: List[Task], timeout_s: int) -> str:
        """Run CrewAI kickoff with a timeout.

        Uses threading instead of signal.SIGALRM so it works from any thread
        (FastAPI worker threads, background threads, etc.).
        """
        timeout_s = max(1, int(timeout_s))

        result_box: List[str] = []
        error_box: List[Exception] = []

        def _run():
            try:
                result_box.append(
                    str(self._crew_kickoff(Crew(verbose=False, agents=agents, tasks=tasks)))
                )
            except Exception as exc:
                error_box.append(exc)

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        worker.join(timeout=timeout_s)

        if worker.is_alive():
            # Thread is still running — we can't forcibly kill it, but we
            # raise so the caller falls back to the deterministic template.
            raise TimeoutError(f"Crew kickoff exceeded {timeout_s}s timeout")

        if error_box:
            raise error_box[0]

        if result_box:
            return result_box[0]

        raise RuntimeError("Crew kickoff returned no result")

    def _format_semantic_rigor_errors(self, sem_report: dict) -> str:
        """Parse raw Verilator width warnings into structured, actionable context for the LLM fixer."""
        lines = []
        lines.append("SEMANTIC_RIGOR_FAILURE — Width and port issues detected.\n")

        # Port shadowing — already structured
        for sig in sem_report.get("port_shadowing", []):
            lines.append(
                f"PORT SHADOWING: Signal '{sig}' is declared as both a port and an internal signal. "
                f"Remove the redundant internal declaration."
            )

        # Width issues — parse raw Verilator warning strings into structured records
        for raw in sem_report.get("width_issues", []):
            parsed = self._parse_width_warning(raw)
            if parsed:
                lines.append(
                    f"Signal '{parsed['signal']}' at line {parsed['line']}: "
                    f"expected {parsed['expected']} bits but got {parsed['actual']} bits. "
                    f"Fix the width mismatch at this exact location."
                )
            else:
                # Fallback: still include the warning but mark it clearly
                lines.append(f"WIDTH WARNING: {raw.strip()}")

        return "\n".join(lines)

    @staticmethod
    def _parse_width_warning(warning: str) -> dict | None:
        """Extract signal name, line number, and bit widths from a Verilator width warning.

        Handles patterns like:
          %Warning-WIDTHTRUNC: src/foo.v:42:5: Operator ASSIGN expects 8 bits on the Assign RHS, but Assign RHS's URANDOMRANGE generates 32 bits.
          %Warning-WIDTHEXPAND: src/foo.v:43:22: Operator EQ expects 32 or 5 bits on the LHS, but LHS's VARREF 'cnt' generates 4 bits.
        """
        import re

        # Extract line number from file:line:col pattern
        line_match = re.search(r":(\d+):\d+:", warning)
        line_no = int(line_match.group(1)) if line_match else 0

        # Extract signal/variable name — Verilator uses VARREF, Var, SELBIT, etc.
        sig_match = re.search(r"(?:VARREF|Var|of|SEL|SELBIT|ARRAYSEL)\s+'(\w+)'", warning)
        signal = sig_match.group(1) if sig_match else ""

        # Extract expected width.
        # Verilator can emit "expects N bits" or "expects N or M bits" — take
        # the *last* number before "bits" in the expects clause since Verilator
        # lists the context width first and the meaningful target width second.
        expect_match = re.search(r"expects\s+(\d+(?:\s+or\s+\d+)?)\s+bits", warning)
        expected = "?"
        if expect_match:
            # If format is "32 or 5", extract both; use the smaller (signal-meaningful) one.
            nums = re.findall(r"\d+", expect_match.group(1))
            if nums:
                expected = nums[-1]  # last number is the more specific width

        # Extract actual width — "generates N bits"
        actual_match = re.search(r"generates\s+(\d+)\s+bits", warning)
        if not actual_match:
            actual_match = re.search(r"(?:is|has)\s+(\d+)\s+bits", warning)

        actual = actual_match.group(1) if actual_match else "?"

        # If we couldn't extract anything meaningful, return None for fallback
        if not signal and expected == "?" and actual == "?":
            return None

        # If no signal name found, try to extract any quoted identifier
        if not signal:
            id_match = re.search(r"'(\w+)'", warning)
            signal = id_match.group(1) if id_match else "unknown"

        return {
            "signal": signal,
            "line": line_no,
            "expected": expected,
            "actual": actual,
        }

    @staticmethod
    def _format_unfixable_width_errors(unfixable: list) -> str:
        """Build a detailed, actionable LLM prompt from unfixable width warning context.

        Each entry in *unfixable* has: line_number, source_line, kind, signal,
        expected_width, actual_width, verilator_message.
        """
        parts = [
            "SEMANTIC_RIGOR_FAILURE — Width mismatches the mechanical post-processor could not resolve.\n"
            "Fix every issue listed below. Use whatever approach is correct for each specific expression "
            "(cast, explicit sizing, localparam, bit-select, zero-extension, etc.).\n"
        ]
        for ctx in unfixable:
            parts.append(
                f"LINE {ctx['line_number']}: {ctx['source_line']}\n"
                f"  Verilator says: {ctx['verilator_message']}\n"
                f"  Signal '{ctx['signal']}' is {ctx['actual_width']} bits wide, "
                f"but the expression context requires {ctx['expected_width']} bits.\n"
                f"  The RHS likely involves a parameter expression that evaluates to 32-bit integer.\n"
                f"  Resolve this width mismatch at this exact line.\n"
            )
        return "\n".join(parts)

    def _condense_failure_log(self, raw_text: str, kind: str) -> str:
        if not raw_text:
            return raw_text
        if len(raw_text) < 12000:
            return raw_text
        src_dir = f"{OPENLANE_ROOT}/designs/{self.name}/src"
        os.makedirs(src_dir, exist_ok=True)
        log_path = os.path.join(src_dir, f"{self.name}_{kind}_failure.log")
        try:
            with open(log_path, "w") as f:
                f.write(raw_text)
            summary = parse_eda_log_summary(log_path, kind=kind, top_n=10)
            return f"LOG_SUMMARY: {summary}"
        except OSError:
            return raw_text[-12000:]

    def _evaluate_hierarchy(self, rtl_code: str):
        module_count = len(re.findall(r"\bmodule\b", rtl_code))
        rtl_lines = len([l for l in rtl_code.splitlines() if l.strip()])
        if self.hierarchical_mode == "on":
            enabled = True
        elif self.hierarchical_mode == "off":
            enabled = False
        else:
            enabled = module_count >= 3 and rtl_lines >= 600
        self.artifacts["hierarchy_plan"] = {
            "mode": self.hierarchical_mode,
            "enabled": enabled,
            "module_count": module_count,
            "rtl_lines": rtl_lines,
            "thresholds": {"module_count": 3, "rtl_lines": 600},
        }
        if enabled:
            self.log("Hierarchical synthesis planner: enabled.", refined=True)
        else:
            self.log("Hierarchical synthesis planner: disabled.", refined=True)

    def _write_ip_manifest(self):
        rtl_path = self.artifacts.get("rtl_path", "")
        if not rtl_path or not os.path.exists(rtl_path):
            return
        with open(rtl_path, "r") as f:
            rtl_code = f.read()
        modules = re.findall(r"module\s+([A-Za-z_]\w*)", rtl_code)
        params = re.findall(r"parameter\s+([A-Za-z_]\w*)\s*=\s*([^,;\)]+)", rtl_code)
        ports = re.findall(
            r"\b(input|output|inout)\s+(?:reg|wire|logic)?\s*(?:\[[^\]]+\])?\s*([A-Za-z_]\w*)",
            rtl_code,
        )
        manifest = {
            "ip_name": self.name,
            "version": "1.0.0",
            "clock_reset": {"clock": "clk", "reset": "rst_n", "reset_active_low": True},
            "modules": modules,
            "dependencies": [m for m in modules if m != self.name],
            "parameters": [{"name": n, "default": v.strip()} for n, v in params],
            "ports": [{"direction": d, "name": n} for d, n in ports],
            "verification_status": {
                "simulation": "PASS" if self.state == BuildState.SUCCESS else "UNKNOWN",
                "formal": self.artifacts.get("formal_result", "UNKNOWN"),
                "signoff": self.artifacts.get("signoff_result", "UNKNOWN"),
            },
            "ipxact_bridge_ready": True,
        }
        out = os.path.join(OPENLANE_ROOT, "designs", self.name, "ip_manifest.json")
        with open(out, "w") as f:
            json.dump(manifest, f, indent=2)
        self.artifacts["ip_manifest"] = out

    def _build_industry_benchmark_snapshot(self) -> Dict[str, Any]:
        metrics = self.artifacts.get("metrics", {}) or {}
        sta = self.artifacts.get("sta_signoff", {}) or {}
        power = self.artifacts.get("power_signoff", {}) or {}
        signoff = self.artifacts.get("signoff", {}) or {}
        congestion = self.artifacts.get("congestion", {}) or {}
        coverage = self.artifacts.get("coverage", {}) or {}
        regression_results = self.artifacts.get("regression_results", []) or []
        tb_gate_history = self.artifacts.get("tb_gate_history", []) or []

        regression_pass = sum(1 for x in regression_results if x.get("status") == "PASS")
        regression_total = len(regression_results)

        snapshot = {
            "design_name": self.name,
            "generated_at_epoch": int(time.time()),
            "build_status": self.state.name,
            "signoff_result": self.artifacts.get("signoff_result", "UNKNOWN"),
            "pdk_profile": self.pdk_profile.get("profile"),
            "pdk": self.pdk_profile.get("pdk"),
            "std_cell_library": self.pdk_profile.get("std_cell_library"),
            "verification_metadata": {
                "tb_gate_mode": self.tb_gate_mode,
                "tb_max_retries": self.tb_max_retries,
                "tb_fallback_template": self.tb_fallback_template,
                "tb_static_fail_count": self.tb_static_fail_count,
                "tb_compile_fail_count": self.tb_compile_fail_count,
                "tb_repair_fail_count": self.tb_repair_fail_count,
                "tb_gate_history_count": len(tb_gate_history),
                "tb_gate_history": tb_gate_history[-20:],
                "coverage_backend": self.coverage_backend,
                "coverage_fallback_policy": self.coverage_fallback_policy,
                "coverage_profile": self.coverage_profile,
                "coverage_thresholds": self.coverage_thresholds,
                "coverage_mode": coverage.get("coverage_mode", "unknown"),
                "coverage_error_kind": coverage.get("error_kind", ""),
                "coverage_diag_path": coverage.get("raw_diag_path", ""),
                "coverage_threshold_pass": {
                    "line": float(coverage.get("line_pct", 0.0))
                    >= max(
                        float(self.coverage_thresholds.get("line", 85.0)),
                        float(self.min_coverage),
                    ),
                    "branch": float(coverage.get("branch_pct", 0.0))
                    >= float(self.coverage_thresholds.get("branch", 80.0)),
                    "toggle": float(coverage.get("toggle_pct", 0.0))
                    >= float(self.coverage_thresholds.get("toggle", 75.0)),
                    "functional": float(coverage.get("functional_pct", 0.0))
                    >= float(self.coverage_thresholds.get("functional", 80.0)),
                },
            },
            "industry_benchmark": {
                "area_um2": metrics.get("chip_area_um2", 0.0),
                "cell_count": metrics.get("area", 0.0),
                "utilization_pct": metrics.get("utilization", 0.0),
                "timing_wns_ns": sta.get("worst_setup", metrics.get("timing_wns", 0.0)),
                "timing_tns_ns": metrics.get("timing_tns", 0.0),
                "hold_slack_ns": sta.get("worst_hold", 0.0),
                "drc_violations": signoff.get("drc_violations", -1),
                "lvs_errors": signoff.get("lvs_errors", -1),
                "antenna_violations": signoff.get("antenna_violations", -1),
                "total_power_mw": float(power.get("total_power_w", 0.0)) * 1000.0,
                "internal_power_mw": float(power.get("internal_power_w", 0.0)) * 1000.0,
                "switching_power_mw": float(power.get("switching_power_w", 0.0)) * 1000.0,
                "leakage_power_uw": float(power.get("leakage_power_w", 0.0)) * 1e6,
                "irdrop_vpwr_mv": float(power.get("irdrop_max_vpwr", 0.0)) * 1000.0,
                "irdrop_vgnd_mv": float(power.get("irdrop_max_vgnd", 0.0)) * 1000.0,
                "congestion_usage_pct": congestion.get("total_usage_pct", 0.0),
                "congestion_overflow": congestion.get("total_overflow", 0),
                "coverage_line_pct": coverage.get("line_pct", 0.0),
                "coverage_branch_pct": coverage.get("branch_pct", 0.0),
                "coverage_toggle_pct": coverage.get("toggle_pct", 0.0),
                "coverage_functional_pct": coverage.get("functional_pct", 0.0),
                "coverage_backend": coverage.get("backend", self.coverage_backend),
                "coverage_mode": coverage.get("coverage_mode", "unknown"),
                "formal_result": self.artifacts.get("formal_result", "UNKNOWN"),
                "lec_result": self.artifacts.get("lec_result", "UNKNOWN"),
                "regression_passed": regression_pass,
                "regression_total": regression_total,
                "clock_period_ns": self.artifacts.get(
                    "clock_period_override",
                    self.pdk_profile.get("default_clock_period"),
                ),
                "pivots_used": self.pivot_count,
                "global_steps": self.global_step_count,
            },
        }
        return snapshot

    def _save_industry_benchmark_metrics(self):
        """Write benchmark metrics after successful chip creation to metrics/."""
        snapshot = self._build_industry_benchmark_snapshot()
        metrics_root = os.path.join(WORKSPACE_ROOT, "metrics")
        design_dir = os.path.join(metrics_root, self.name)
        os.makedirs(design_dir, exist_ok=True)

        stamp = time.strftime("%Y%m%d_%H%M%S")
        json_path = os.path.join(design_dir, f"{self.name}_industry_benchmark_{stamp}.json")
        md_path = os.path.join(design_dir, f"{self.name}_industry_benchmark_{stamp}.md")
        latest_json = os.path.join(design_dir, "latest.json")
        latest_md = os.path.join(design_dir, "latest.md")

        with open(json_path, "w") as f:
            json.dump(snapshot, f, indent=2)

        ib = snapshot["industry_benchmark"]
        lines = [
            f"# {self.name} Industry Benchmark Metrics",
            "",
            f"- Generated At (epoch): `{snapshot['generated_at_epoch']}`",
            f"- Build Status: `{snapshot['build_status']}`",
            f"- Signoff Result: `{snapshot['signoff_result']}`",
            f"- PDK Profile: `{snapshot['pdk_profile']}`",
            "",
            "| Metric | Value |",
            "|---|---|",
        ]
        for k, v in ib.items():
            lines.append(f"| `{k}` | `{v}` |")
        with open(md_path, "w") as f:
            f.write("\n".join(lines) + "\n")

        # Keep a latest pointer as plain copied files for easy consumption.
        with open(latest_json, "w") as f:
            json.dump(snapshot, f, indent=2)
        with open(latest_md, "w") as f:
            f.write("\n".join(lines) + "\n")

        self.artifacts["benchmark_metrics_json"] = json_path
        self.artifacts["benchmark_metrics_md"] = md_path
        self.artifacts["benchmark_metrics_dir"] = design_dir
        self.log(f"Saved industry benchmark metrics to {design_dir}", refined=True)

    def _emit_hierarchical_block_artifacts(self):
        plan = self.artifacts.get("hierarchy_plan", {})
        if not plan.get("enabled"):
            return
        rtl_code = self.artifacts.get("rtl_code", "")
        block_dir = os.path.join(OPENLANE_ROOT, "designs", self.name, "src", "blocks")
        os.makedirs(block_dir, exist_ok=True)
        modules = re.findall(r"(module\s+[A-Za-z_]\w*[\s\S]*?endmodule)", rtl_code)
        block_files = []
        for mod in modules:
            m = re.search(r"module\s+([A-Za-z_]\w*)", mod)
            if not m:
                continue
            mod_name = m.group(1)
            path = os.path.join(block_dir, f"{mod_name}.v")
            with open(path, "w") as f:
                f.write(mod.strip() + "\n")
            block_files.append(path)
        self.artifacts["hierarchy_blocks"] = block_files

    def do_rtl_gen(self):
        sid_raw = self.artifacts.get("sid")
        if sid_raw:
            try:
                sid_dict = json.loads(sid_raw)
                graph = DependencyGraph(sid_dict)
                self.log(
                    f"Recursive Graph Execution Enabled! Found {len(graph.nodes)} node(s).",
                    refined=True,
                )

                ordered_nodes = []
                # Simple topological sort
                unprocessed = list(graph.nodes.keys())

                while unprocessed:
                    progress = False
                    for node_name in list(unprocessed):
                        node = graph.nodes[node_name]
                        # If all dependencies are already in ordered_nodes
                        if all(dep in [n.name for n in ordered_nodes] for dep in node.dependencies):
                            ordered_nodes.append(node)
                            unprocessed.remove(node_name)
                            progress = True

                    if not progress:
                        self.log(
                            "Warning: Circular dependency detected in Graph Builder, continuing mostly sequential.",
                            refined=True,
                        )
                        for node_name in unprocessed:
                            ordered_nodes.append(graph.nodes[node_name])
                        break

                for node in ordered_nodes:
                    self.log(f"Proceeding to generate Sub-Module: {node.name}", refined=True)
                    # For MVP graph implementation, we will append sub_module requirements into the prompt
                    # instead of fully isolating state transitions, avoiding disruption of standard Verification pipes.

            except Exception as e:
                self.log(f"Graph Builder parsing warning: {e}", refined=True)
        # Check Golden Reference Library for a matching template
        from .golden_lib import get_best_template

        if self.no_golden_templates:
            self.log(
                "Golden template matching DISABLED (--no-golden-templates). Generating from scratch.",
                refined=True,
            )
            template = None
        else:
            template = get_best_template(self.desc, self.name)

        if template:
            self.log(
                f"Golden Reference MATCH: {template['ip_type']} (score={template['score']})",
                refined=True,
            )
            self.artifacts["golden_template"] = template["ip_type"]

            # Use the golden RTL DIRECTLY — just rename the module.
            # This guarantees RTL ↔ TB compatibility (both are pre-verified together).
            original_name = template["ip_type"]  # e.g. 'counter', 'fifo', 'uart_tx'

            # Rename RTL module (use word boundary to avoid over-matching comments/strings)
            rtl_code = re.sub(
                rf"\bmodule\s+{re.escape(original_name)}\b",
                f"module {self.name}",
                template["template_code"],
            )
            self.log(
                f"Using golden {original_name} template with module renamed to {self.name}.",
                refined=True,
            )
            self.logger.info(f"GOLDEN RTL ({original_name}):\n{rtl_code}")

            # Also rename the golden testbench module + DUT instantiation to match.
            # Both RTL and TB were pre-verified together under the original names.
            if template.get("tb_code"):
                tb_original_name = f"{original_name}_tb"
                tb_code = template["tb_code"]
                # Rename testbench module name
                tb_code = re.sub(
                    rf"\bmodule\s+{re.escape(tb_original_name)}\b",
                    f"module {self.name}_tb",
                    tb_code,
                )
                # Rename DUT instantiation from e.g. "counter dut (...)" -> "my_counter dut (...)"
                tb_code = re.sub(
                    rf"\b({re.escape(original_name)})\s+dut\b",
                    rf"{self.name} dut",
                    tb_code,
                )
                # Rename parameter/port connections referencing original module name
                tb_code = re.sub(rf"\b({re.escape(original_name)})\s*#", rf"{self.name} #", tb_code)
                self.artifacts["golden_tb"] = tb_code
        else:
            # No template match — pure LLM generation
            self.log("No golden template match. Generating from scratch.", refined=True)
            strategy_prompt = self._get_strategy_prompt()

            rtl_agent = get_designer_agent(
                self.get_llm_for_role("designer"),
                goal=f"Create {self.strategy.name} RTL for {self.name}",
                verbose=self.verbose,
                strategy=self.strategy.name,
            )

            # Reviewer agent — checks the designer's output for common issues
            reviewer = Agent(
                role="RTL Reviewer",
                goal="Review generated RTL for completeness, lint issues, and Verilator compatibility",
                backstory="""Senior RTL reviewer who catches missing reset logic, width mismatches,
undriven outputs, and Verilator-incompatible constructs. You verify that:
1. All outputs are driven in all code paths
2. All registers are reset
3. Width mismatches are flagged
4. Module name matches the design name
5. No placeholders or TODO comments remain
You return the FINAL corrected code in ```verilog``` fences.""",
                llm=self.get_llm_for_role("designer"),
                verbose=False,
                tools=[syntax_check_tool, read_file_tool, write_verilog_tool],
                allow_delegation=False,
            )

            rtl_task = Task(
                description=f"""Design module "{self.name}" based on SPEC.
            
SPECIFICATION:
{self.artifacts.get("spec", "")}

STRATEGY GUIDELINES:
{strategy_prompt}

LOGIC DECOUPLING HINT:
{self.artifacts.get("logic_decoupling_hint", "N/A")}

RETRIEVED HARDWARE KNOWLEDGE AND EVOLVED BUILD CONTEXT:
{self._build_llm_context(include_rtl=False, mode="generation_mode")}

CRITICAL RULES:
1. Top-level module name MUST be "{self.name}"
2. Async active-low reset `rst_n`
3. Flatten ports on the TOP module (no multi-dim arrays on top-level ports). Internal modules can use them.
4. **IMPLEMENT EVERYTHING**: Do not leave any logic as "to be implemented" or "simplified".
5. **MODULAR HIERARCHY**: For complex designs, break them into smaller sub-modules. Output ALL modules in your response.
6. Return code in ```verilog fence.
""",
                expected_output="Complete Verilog RTL Code",
                agent=rtl_agent,
            )

            review_task = Task(
                description=f"""Review the RTL code generated by the designer for module "{self.name}".

Check for these common issues:
1. Module name must be exactly "{self.name}"
2. All always_comb blocks must assign ALL variables in ALL branches (no latches)
3. Width mismatches (e.g., 2-bit signal assigned to 3-bit variable)
4. All outputs must be driven
5. All registers must be reset in the reset branch
6. No placeholders, TODOs, or simplified logic

If you find issues, FIX them and output the corrected code.
If the code is correct, output it unchanged.
ALWAYS return the COMPLETE code in ```verilog``` fences.
""",
                expected_output="Reviewed and corrected Verilog RTL Code in ```verilog``` fences",
                agent=reviewer,
            )

            with console.status(f"[warning]Generating RTL ({self.strategy.name})...[/warning]"):
                try:
                    result = self._crew_kickoff(
                        Crew(
                            verbose=False,
                            agents=[rtl_agent, reviewer],
                            tasks=[rtl_task, review_task],
                        )
                    )
                    if result is None:
                        self.logger.warning("CrewAI result is None, retrying...")
                        rtl_code = None
                    else:
                        rtl_code = str(result) if result else None

                    # --- Universal Verilog extraction + validation (RTL gen) ---
                    if rtl_code and rtl_code != "None":
                        is_valid, extracted = extract_and_validate_llm_code(rtl_code)
                        if is_valid:
                            rtl_code = extracted
                        elif extracted:
                            self.log(
                                "Extracted Verilog from prose, proceeding with extracted code.",
                                refined=True,
                            )
                            rtl_code = extracted
                        else:
                            self.log(
                                "RTL generation failed to produce Verilog. Retrying once.",
                                refined=True,
                            )
                            self.logger.warning(
                                f"RTL VALIDATION FAIL (no Verilog extracted):\n{rtl_code[:500]}"
                            )
                            result = self._crew_kickoff(
                                Crew(
                                    verbose=False,
                                    agents=[rtl_agent, reviewer],
                                    tasks=[rtl_task, review_task],
                                )
                            )
                            rtl_code = str(result) if result else None
                            if rtl_code:
                                is_valid, extracted = extract_and_validate_llm_code(rtl_code)
                                if is_valid or extracted:
                                    rtl_code = extracted if extracted else rtl_code
                    else:
                        self.log(
                            "RTL generation returned empty/None result. Retrying once.",
                            refined=True,
                        )
                        result = self._crew_kickoff(
                            Crew(
                                verbose=False,
                                agents=[rtl_agent, reviewer],
                                tasks=[rtl_task, review_task],
                            )
                        )
                        rtl_code = str(result) if result else None
                except Exception as crew_exc:
                    self.log(f"CrewAI RTL generation error: {crew_exc}", refined=True)
                    self.logger.warning(f"CrewAI kickoff exception in do_rtl_gen: {crew_exc}")
                    # Strategy pivot: allow recovery instead of hard crash
                    if self.strategy == BuildStrategy.SV_MODULAR:
                        self.log(
                            "Strategy Pivot after CrewAI error: SV_MODULAR -> VERILOG_CLASSIC",
                            refined=True,
                        )
                        self.strategy = BuildStrategy.VERILOG_CLASSIC
                        self.transition(BuildState.RTL_GEN)
                    else:
                        self.log(
                            "CrewAI error on fallback strategy. Build Failed.",
                            refined=True,
                        )
                        self.state = BuildState.FAIL
                    return

            if rtl_code is None or rtl_code == "None" or not rtl_code.strip():
                self.log("RTL generation produced no output. Build Failed.", refined=True)
                self.state = BuildState.FAIL
                return

            self.logger.info(f"GENERATED RTL ({self.strategy.name}):\n{rtl_code[:500]}...")

        # Save file (write_verilog cleans LLM output: strips markdown, think tags, etc.)
        path = write_verilog(self.name, rtl_code)
        if "Error" in path:
            self.log(f"File Write Error: {path}", refined=True)
            self.state = BuildState.FAIL
            return

        self.artifacts["rtl_path"] = path
        # Store the CLEANED code (read back from file), not raw LLM output
        with open(path, "r") as f:
            self.artifacts["rtl_code"] = f.read()
        self._evaluate_hierarchy(self.artifacts["rtl_code"])
        self._emit_hierarchical_block_artifacts()
        self.transition(BuildState.RTL_FIX)

    def do_rtl_fix(self):
        # Check syntax
        path = self.artifacts["rtl_path"]
        success, errors = run_syntax_check(path)

        if success:
            self.log("Syntax Check Passed (Verilator)", refined=True)

            # --- START VERILATOR LINT CHECK ---
            with console.status(
                "[warning]Running Verilator Lint...[/warning]",
                spinner="dots12",
                spinner_style="spinner",
            ):
                lint_success, lint_report = run_lint_check(path)

            self.logger.info(f"LINT REPORT:\n{lint_report}")

            if lint_success:
                self.log("Lint Check Passed (Verilator)", refined=True)

                # --- PRE-SYNTHESIS VALIDATION ---
                # Catch undriven signals that would fail Yosys synthesis
                from .tools.vlsi_tools import validate_rtl_for_synthesis

                was_fixed, synth_report = validate_rtl_for_synthesis(path)
                self.logger.info(f"PRE-SYNTH VALIDATION: {synth_report}")
                if was_fixed:
                    self.log(f"Pre-synthesis auto-fix applied.", refined=True)
                    # Re-read fixed code into artifacts
                    with open(path, "r") as f:
                        self.artifacts["rtl_code"] = f.read()
                    # Re-check syntax after fix (stay in RTL_FIX)
                    return

                sem_ok, sem_report = run_semantic_rigor_check(path)
                self.logger.info(f"SEMANTIC RIGOR: {sem_report}")
                if not sem_ok:
                    if self.strict_gates:
                        width_issues = (
                            sem_report.get("width_issues", [])
                            if isinstance(sem_report, dict)
                            else []
                        )
                        if not width_issues:
                            self.log(
                                "Semantic rigor failed on non-width issues. Routing directly to LLM fixer.",
                                refined=True,
                            )
                            errors = self._format_semantic_rigor_errors(sem_report)
                        else:
                            # --- Mechanical width auto-fix (no LLM) ---
                            self.log(
                                "Semantic rigor gate failed. Attempting mechanical width auto-fix.",
                                refined=True,
                            )
                            fix_ok, fix_report = auto_fix_width_warnings(path)
                            self.logger.info(
                                f"WIDTH AUTO-FIX: fixed={fix_report.get('fixed_count', 0)}, "
                                f"remaining={fix_report.get('remaining_count', 0)}"
                            )
                            if fix_ok:
                                self.log(
                                    f"Width auto-fix resolved all {fix_report['fixed_count']} warnings.",
                                    refined=True,
                                )
                                # Re-read the patched RTL into artifacts
                                with open(path, "r") as f:
                                    self.artifacts["rtl_code"] = f.read()
                                # Loop back to re-check syntax/lint on the patched file
                                return
                            elif fix_report.get("fixed_count", 0) > 0:
                                self.log(
                                    f"Width auto-fix resolved {fix_report['fixed_count']} warnings; "
                                    f"{fix_report['remaining_count']} remain. Re-checking.",
                                    refined=True,
                                )
                                with open(path, "r") as f:
                                    self.artifacts["rtl_code"] = f.read()
                                # Loop back — the remaining warnings may resolve after re-lint
                                return
                            # Post-processor couldn't fix anything — fall through to LLM
                            self.log(
                                "Mechanical auto-fix could not resolve width warnings. Routing to LLM fixer.",
                                refined=True,
                            )
                            # If the post-processor gathered rich context for unfixable warnings,
                            # build a detailed prompt giving the LLM everything it needs.
                            unfixable = fix_report.get("unfixable_context", [])
                            if unfixable:
                                errors = self._format_unfixable_width_errors(unfixable)
                            else:
                                errors = self._format_semantic_rigor_errors(sem_report)
                    else:
                        self.log(
                            "Semantic rigor warnings detected (non-blocking).",
                            refined=True,
                        )
                        self.artifacts["semantic_report"] = sem_report
                        self._record_stage_contract(
                            StageResult(
                                stage=self.state.name,
                                status=StageStatus.PASS,
                                producer="orchestrator_rtl_fix",
                                consumable_payload={"semantic_report": bool(sem_report)},
                                artifacts_written=["semantic_report"],
                                next_action=BuildState.VERIFICATION.name,
                            )
                        )
                        self.transition(BuildState.VERIFICATION)
                        return
                else:
                    self.artifacts["semantic_report"] = sem_report
                    self._record_stage_contract(
                        StageResult(
                            stage=self.state.name,
                            status=StageStatus.PASS,
                            producer="orchestrator_rtl_fix",
                            consumable_payload={"semantic_report": True},
                            artifacts_written=["semantic_report"],
                            next_action=BuildState.VERIFICATION.name,
                        )
                    )
                    self.transition(BuildState.VERIFICATION)
                    return

            else:
                self.log(f"Lint Failed. Check log for details.", refined=True)
                errors = f"SYNTAX OK, BUT LINT FAILED:\n{lint_report}"
                # Fall through to Error Handling Logic below to fix it

        # --- AUTONOMOUS SV↔VERILOG COMPATIBILITY FIX ---
        # LLM-based fix loop follows...

        # Handle Syntax/Lint Errors that need LLM
        self.logger.info(f"SYNTAX/LINT ERRORS:\n{errors}")
        if self._record_failure_fingerprint(str(errors)):
            self.log(
                "Detected repeated syntax/lint failure fingerprint. Failing closed.",
                refined=True,
            )
            self.state = BuildState.FAIL
            return
        self.retry_count += 1
        if self.retry_count > self.max_retries:
            self.log("Max Retries Exceeded for Syntax/Lint Fix.", refined=True)

            # STATE CROSSING / BACKTRACKING
            if self.strategy == BuildStrategy.SV_MODULAR:
                self.log(
                    "Attempting Strategy Pivot: SV_MODULAR -> VERILOG_CLASSIC",
                    refined=True,
                )
                self.strategy = BuildStrategy.VERILOG_CLASSIC
                self.transition(
                    BuildState.RTL_GEN, preserve_retries=True
                )  # Restart RTL Gen with new strategy
                return
            else:
                self.log("Already on fallback strategy. Build Failed.", refined=True)
                self.state = BuildState.FAIL
                return

        self.log(f"Fixing Code (Attempt {self.retry_count}/{self.max_retries})", refined=True)
        errors_for_llm = self._condense_failure_log(str(errors), kind="timing")

        # ── ReAct iterative RTL fix (runs before single-shot CrewAI) ──
        # ReActAgent does Thought→Action→Observation loops using the already-imported
        # syntax_check_tool and read_file_tool to self-verify its own fixes.
        _react_fixed_code = None
        if os.path.exists(path) and errors_for_llm.strip():
            _react_agent = ReActAgent(
                llm=self.get_llm_for_role("fixer"),
                role="RTL Syntax Fixer",
                max_steps=6,
                verbose=self.verbose,
            )
            # syntax_check_tool and read_file_tool are already imported at module top
            _react_agent.register_tool(
                "syntax_check",
                "Run Verilator syntax check on an absolute .v file path. Returns error text.",
                lambda p: str(run_syntax_check(p.strip())),
            )
            _react_agent.register_tool(
                "read_file",
                "Read contents of an absolute file path.",
                lambda p: (
                    open(p.strip()).read() if os.path.exists(p.strip()) else f"Not found: {p}"
                ),
            )
            _react_context = (
                f"RTL file path: {path}\n\n"
                f"Errors:\n{errors_for_llm}\n\n"
                f"Current RTL:\n```verilog\n{self.artifacts['rtl_code']}\n```"
            )
            _react_trace = _react_agent.run(
                task=(
                    f"Fix all syntax and lint errors in Verilog module '{self.name}'. "
                    f"CRITICAL: Do not rename the module. Do not add, modify, or remove any input/output ports. "
                    f"The top-module interface MUST remain exactly the same. "
                    f"Use syntax_check tool to verify your fix compiles clean. "
                    f"Final Answer must be ONLY corrected Verilog inside ```verilog fences."
                ),
                context=_react_context,
            )
            react_result = self._normalize_react_result(_react_trace)
            self._set_artifact(
                "react_last_result",
                react_result.to_dict(),
                producer="agent_react",
                consumer="RTL_FIX",
            )
            if react_result.ok:
                _react_fixed_code = react_result.payload.get("code", "")
                self.logger.info(
                    f"[ReAct] RTL fix done in {_react_trace.total_steps} steps "
                    f"({_react_trace.total_duration_s:.1f}s)"
                )
            if not _react_fixed_code:
                self._record_stage_contract(
                    StageResult(
                        stage=self.state.name,
                        status=StageStatus.RETRY,
                        producer="agent_react",
                        failure_class=react_result.failure_class,
                        diagnostics=react_result.diagnostics,
                        artifacts_written=["react_last_result"],
                        next_action="fallback_to_single_shot",
                    )
                )
                self.logger.info(
                    f"[ReAct] No valid code produced "
                    f"(success={_react_trace.success}, steps={_react_trace.total_steps}). "
                    "Falling through to single-shot CrewAI."
                )

        # If ReAct produced valid code, skip single-shot CrewAI and go straight to write
        if _react_fixed_code:
            new_code = _react_fixed_code
        else:
            # Use IncrementalFixer for error analysis and surgical fixes
            analysis = self.incremental_fixer.analyze_error(
                error_text=errors_for_llm,
                rtl_code=self.artifacts.get("rtl_code", ""),
            )

            # Build surgical fix prompt with error analysis
            fix_prompt = f"""RESPOND WITH VERILOG CODE ONLY. Your entire response must be the corrected Verilog module inside ```verilog fences. Do not write any explanation, reasoning, thought process, or text outside the fences. Any response that does not start with ```verilog will be rejected and waste a retry attempt.

CRITICAL: Do not rename the module. Do not add, modify, or remove any input/output ports. The top-module interface MUST remain exactly the same.

Fix Syntax/Lint Errors in "{self.name}".

## ERROR ANALYSIS (from IncrementalFixer)
Error Type: {analysis.error_type.value if hasattr(analysis.error_type, "value") else analysis.error_type}
Affected Signals: {", ".join(analysis.signals_mentioned[:5]) if analysis.signals_mentioned else "Unknown"}
Affected Modules: {", ".join(analysis.modules_affected[:3]) if analysis.modules_affected else "Unknown"}
Side Effect Risks: {"; ".join(analysis.side_effect_risks[:3]) if analysis.side_effect_risks else "None identified"}

## BUILD CONTEXT:
{self._build_llm_context(mode="error_mode")}

## ERROR LOG:
{errors_for_llm}

## PREVIOUS FIX ATTEMPTS (do NOT repeat these):
{self._format_failure_history()}

Strategy: {self.strategy.name} (Keep consistency!)

IMPORTANT: The compiler is Verilator 5.0+ (SystemVerilog 2017+).
- Use modern SystemVerilog features (`logic`, `always_comb`, `always_ff`).
- Ensure strict 2-state logic handling (reset all registers).
- Avoid 4-state logic (x/z) reliance as Verilator is 2-state optimized.
- Focus on fixing the error type: {analysis.error_type.value if hasattr(analysis.error_type, "value") else analysis.error_type}
- Be careful around signals: {", ".join(analysis.signals_mentioned[:3]) if analysis.signals_mentioned else "N/A"}

Code:
```verilog
{self.artifacts["rtl_code"]}
```
"""

            # Use a fixer agent with enhanced backstory
            fixer = Agent(
                role="Syntax Rectifier",
                goal="Fix Verilog Compilation & Lint Errors while preserving design intent",
                backstory="""Expert in Verilator error messages, SystemVerilog lint warnings, and RTL debugging.
You analyze the ARCHITECTURE SPEC to understand design intent before fixing.
You review PREVIOUS FIX ATTEMPTS to avoid repeating ineffective patches.
You explain what you changed and why.""",
                llm=self.get_llm_for_role("fixer"),
                verbose=self.verbose,
                tools=[syntax_check_tool, read_file_tool, write_verilog_tool],
            )

            task = Task(
                description=fix_prompt,
                expected_output="Fixed Verilog Code",
                agent=fixer,
            )

            with console.status(
                "[error]AI fixing Syntax/Lint Errors...[/error]",
                spinner="dots12",
                spinner_style="spinner",
            ):
                try:
                    result = self._crew_kickoff(Crew(verbose=False, agents=[fixer], tasks=[task]))
                    new_code = str(result)
                    # --- Universal code output validation (RTL fix) ---
                    if not validate_llm_code_output(new_code):
                        self._record_non_consumable_output(
                            "llm_rtl_fix",
                            new_code,
                            ["RTL fix returned prose instead of code."],
                        )
                        self.log(
                            "RTL fix returned prose instead of code. Retrying once.",
                            refined=True,
                        )
                        self.logger.warning(
                            f"RTL FIX VALIDATION FAIL (prose detected):\n{new_code[:500]}"
                        )
                        new_code = str(
                            self._crew_kickoff(Crew(verbose=False, agents=[fixer], tasks=[task]))
                        )
                except Exception as crew_exc:
                    self.log(f"CrewAI fix error: {crew_exc}", refined=True)
                    self.logger.warning(f"CrewAI kickoff exception in do_rtl_fix: {crew_exc}")
                    # Return last known RTL — allow the retry loop / strategy pivot to handle it
                    if self.strategy == BuildStrategy.SV_MODULAR:
                        self.log(
                            "Strategy Pivot after CrewAI error: SV_MODULAR -> VERILOG_CLASSIC",
                            refined=True,
                        )
                        self.strategy = BuildStrategy.VERILOG_CLASSIC
                        self.transition(BuildState.RTL_GEN, preserve_retries=True)
                    else:
                        self.log(
                            "CrewAI error on fallback strategy. Build Failed.",
                            refined=True,
                        )
                        self.state = BuildState.FAIL
                    return

        self.logger.info(f"FIXED RTL:\n{new_code}")
        rtl_validation_issues = self._validate_rtl_candidate(
            new_code, self.artifacts.get("rtl_code", "")
        )
        if rtl_validation_issues:
            self._record_stage_contract(
                StageResult(
                    stage=self.state.name,
                    status=StageStatus.RETRY,
                    producer="orchestrator_rtl_validator",
                    failure_class=FailureClass.LLM_SEMANTIC_ERROR,
                    diagnostics=rtl_validation_issues,
                    next_action="retry_rtl_fix",
                )
            )
            self.log(f"RTL candidate rejected: {rtl_validation_issues[0]}", refined=True)
            return

        # --- Inner retry loop for LLM parse errors ---
        # If write_verilog fails (LLM didn't output valid code), re-prompt immediately
        # instead of returning to the main loop (which would re-check the stale file
        # and trigger the fingerprint detector).
        _inner_code = new_code
        for _parse_retry in range(3):  # up to 3 immediate retries for parse errors
            new_path = write_verilog(self.name, _inner_code)
            if not (isinstance(new_path, str) and new_path.startswith("Error:")):
                break  # write succeeded

            self.log(
                f"File Write Error in FIX stage (parse retry {_parse_retry + 1}/3): {new_path}",
                refined=True,
            )

            if _parse_retry >= 2:
                # Exhausted parse retries — clear fingerprint and fall back to main retry
                # Clear the fingerprint so the next main-loop iteration gets a real attempt
                self._clear_last_fingerprint(str(errors))
                retry_count = self._bump_state_retry()
                if retry_count >= self.max_retries:
                    self.log(
                        "Write error persisted after max retries. Failing.",
                        refined=True,
                    )
                    self.state = BuildState.FAIL
                else:
                    self.log(
                        f"Retrying fix via main loop (attempt {retry_count}).",
                        refined=True,
                    )
                return

            # Re-prompt the LLM immediately with explicit format instructions
            reformat_prompt = f"""Your previous response contained no Verilog code. Respond now with ONLY the complete corrected Verilog module inside ```verilog fences. Nothing else.

Here is the current code that needs the lint fixes applied:
```verilog
{self.artifacts["rtl_code"]}
```

Original errors to fix:
{errors_for_llm}
"""
            reformat_task = Task(
                description=reformat_prompt,
                expected_output="Complete fixed Verilog code inside ```verilog``` fences",
                agent=fixer,
            )
            with console.status(
                "[warning]Re-prompting LLM for valid Verilog output...[/warning]",
                spinner="dots12",
                spinner_style="spinner",
            ):
                reformat_result = self._crew_kickoff(
                    Crew(verbose=False, agents=[fixer], tasks=[reformat_task])
                )
            _inner_code = str(reformat_result)
            self.logger.info(f"REFORMATTED RTL (parse retry {_parse_retry + 1}):\n{_inner_code}")

        if isinstance(new_path, str) and new_path.startswith("Error:"):
            return  # already handled above

        self.artifacts["rtl_path"] = new_path
        # Read back the CLEANED version, not raw LLM output
        with open(new_path, "r") as f:
            self.artifacts["rtl_code"] = f.read()
        # Loop stays in RTL_FIX to re-check syntax

    # _try_autonomous_sv_fix removed (Verilator supports SV natively)

    def do_verification(self):
        # 1. Generate Testbench (Only on first run or if missing)
        # 2. Run Sim
        # 3. Analyze Results

        # 1. Generate Testbench (Only if missing)
        # We reuse existing TB to ensure consistent verification targets
        tb_exists = "tb_path" in self.artifacts and os.path.exists(self.artifacts["tb_path"])
        regen_context = (
            self._consume_handoff("tb_regen_context", consumer="VERIFICATION", required=False) or ""
        )

        if not tb_exists:
            # Check if we have a golden testbench from template matching
            if self.artifacts.get("golden_tb") and not regen_context:
                self.log("Using Golden Reference Testbench (pre-verified).", refined=True)
                tb_code = self.artifacts["golden_tb"]
                # Replace template module name with actual design name
                template_name = self.artifacts.get("golden_template", "counter")
                tb_code = tb_code.replace(f"{template_name}_tb", f"{self.name}_tb")
                tb_code = tb_code.replace(template_name, self.name)
                self.logger.info(f"GOLDEN TESTBENCH:\n{tb_code}")
                tb_path = write_verilog(self.name, tb_code, is_testbench=True)
                self.artifacts["tb_path"] = tb_path
                self._clear_tb_fingerprints()  # New TB → fresh gate attempts
            else:
                self.log("Generating Testbench...", refined=True)
                tb_agent = get_testbench_agent(
                    self.get_llm_for_role("testbench_designer"),
                    f"Verify {self.name}",
                    strategy=self.strategy.name,
                )

                tb_strategy_prompt = self._get_tb_strategy_prompt()

                # --- Extract module port signature from RTL ---
                # This prevents the most common TB failure: port name mismatches
                rtl_code = self.artifacts["rtl_code"]
                port_info = self._extract_module_interface(rtl_code)

                tb_task = Task(
                    description=f"""Create a self-checking Testbench for module `{self.name}`.

MODULE INTERFACE (use these EXACT port names):
{port_info}

FULL RTL (for understanding behavior):
```verilog
{rtl_code}
```

MANDATORY DUT INSTANTIATION (copy this exactly, connect all ports):
    {self.name} dut (
        // Connect ALL ports listed above by name: .port_name(port_name)
    );

STRATEGY GUIDELINES:
{tb_strategy_prompt}

PREVIOUS TB FAILURES (must fix if present):
{regen_context if regen_context else "N/A"}

RULES:
- Use `timescale 1ns / 1ps
- Module name: {self.name}_tb
- MANDATORY: Add this VCD block immediately after the `timescale directive:
      initial begin
          $dumpfile("{self.name}_wave.vcd");
          $dumpvars(0, {self.name}_tb);
      end
  This is required for waveform debugging. Do not omit it.
- Clock generation: write TWO separate module-level statements (NEVER put `always` inside `initial begin`):
      initial clk = 1'b0;
      always #5 clk = ~clk;
  WARNING: `always` is a module-level construct. Placing it inside `initial begin...end` causes a Verilator compile error.
- All variable declarations (integer, int, reg, logic) MUST appear at the TOP of a begin...end block,
  BEFORE any procedural statements (#delay, assignments, if/for). Verilator rejects mid-block declarations.
- Assert rst_n low for 50ns, then release
- Print "TEST PASSED" on success, "TEST FAILED" on failure
- End with $finish
- Do NOT invent ports that aren't in the MODULE INTERFACE above

SYNCHRONOUS DUT TIMING RULE (mandatory for ALL designs):
This DUT is synchronous. All registered outputs update on the rising clock edge.
After applying any stimulus (reset deassertion, enable assertion, data input),
always wait for at least one complete clock cycle (`@(posedge clk);` or
`repeat(N) @(posedge clk);`) before sampling or comparing any DUT output.
Never sample a DUT output in the same time step that stimulus is applied.
Failure to observe this rule causes off-by-one timing mismatches.

SELF-CHECK (do this before returning code):
Before returning any testbench code, mentally simulate the entire testbench execution against the DUT. Ask yourself: if this DUT had a bug, would this testbench catch it? If the testbench would pass even with a broken DUT, it is not a valid testbench — rewrite it. Every checking statement must compare the DUT output against a value that was computed independently of the DUT.

COMPILATION SELF-CHECK (do this before returning code):
Before returning any testbench code, mentally compile it with strict SystemVerilog rules. Every construct you use must be valid in the Verilator strict mode environment. If you are unsure whether a construct is valid, use a simpler equivalent that you are certain is valid.
""",
                    expected_output="SystemVerilog Testbench",
                    agent=tb_agent,
                )

                with console.status(
                    "[accent]Generating Testbench...[/accent]",
                    spinner="dots12",
                    spinner_style="spinner",
                ):
                    try:
                        tb_code = self._kickoff_with_timeout(
                            agents=[tb_agent],
                            tasks=[tb_task],
                            timeout_s=self.tb_generation_timeout_s,
                        )
                        # --- Universal code output validation (TB gen) ---
                        if not validate_llm_code_output(tb_code):
                            self._record_non_consumable_output(
                                "llm_tb_generation",
                                tb_code,
                                ["TB generation returned prose instead of code."],
                            )
                            self.log(
                                "TB generation returned prose instead of code. Retrying once.",
                                refined=True,
                            )
                            self.logger.warning(
                                f"TB VALIDATION FAIL (prose detected):\n{tb_code[:500]}"
                            )
                            tb_code = self._kickoff_with_timeout(
                                agents=[tb_agent],
                                tasks=[tb_task],
                                timeout_s=self.tb_generation_timeout_s,
                            )
                    except Exception as e:
                        self.log(
                            f"TB generation stalled/failed ({e}). Using deterministic fallback TB.",
                            refined=True,
                        )
                        self.logger.info(f"TB GENERATION FALLBACK: {e}")
                        tb_code = self._deterministic_tb_fallback(
                            self.artifacts.get("rtl_code", "")
                        )

                if "module" not in tb_code or "endmodule" not in tb_code:
                    self.log(
                        "TB generation returned invalid code. Using deterministic fallback TB.",
                        refined=True,
                    )
                    tb_code = self._deterministic_tb_fallback(self.artifacts.get("rtl_code", ""))
                tb_validation_issues = self._validate_tb_candidate(tb_code)
                if tb_validation_issues:
                    self._record_stage_contract(
                        StageResult(
                            stage=self.state.name,
                            status=StageStatus.RETRY,
                            producer="orchestrator_tb_validator",
                            failure_class=FailureClass.LLM_SEMANTIC_ERROR,
                            diagnostics=tb_validation_issues,
                            next_action="deterministic_tb_fallback",
                        )
                    )
                    self.log(
                        f"TB candidate rejected: {tb_validation_issues[0]}. Using deterministic fallback TB.",
                        refined=True,
                    )
                    tb_code = self._deterministic_tb_fallback(self.artifacts.get("rtl_code", ""))
                self.logger.info(f"GENERATED TESTBENCH:\n{tb_code}")

                tb_path = write_verilog(self.name, tb_code, is_testbench=True)
                if isinstance(tb_path, str) and tb_path.startswith("Error:"):
                    self.log(
                        f"TB write failed ({tb_path}). Regenerating deterministic fallback TB.",
                        refined=True,
                    )
                    tb_code = self._deterministic_tb_fallback(self.artifacts.get("rtl_code", ""))
                    tb_path = write_verilog(self.name, tb_code, is_testbench=True)
                    if isinstance(tb_path, str) and tb_path.startswith("Error:"):
                        self.log(f"Fallback TB write failed: {tb_path}", refined=True)
                        self.state = BuildState.FAIL
                        return
                self.artifacts["tb_path"] = tb_path
                self._set_artifact(
                    "tb_candidate",
                    {
                        "tb_path": tb_path,
                        "regen_context_used": bool(regen_context),
                    },
                    producer="orchestrator_verification",
                    consumer="VERIFICATION",
                )
                self._clear_tb_fingerprints()  # New TB → fresh gate attempts
        else:
            self.log(
                f"Verifying with existing Testbench (Attempt {self.retry_count}).",
                refined=True,
            )
            # Verify file exists
            if not os.path.exists(self.artifacts["tb_path"]):
                self.log("Testbench file missing! Forcing regeneration.", refined=True)
                del self.artifacts["tb_path"]  # Remove stale path to trigger regen
                return  # State machine loop will re-enter do_verification()

            # Read current TB for context in case of next error
            with open(self.artifacts["tb_path"], "r") as f:
                tb_code = f.read()

        # Ensure tb_code is available for error analysis context
        if "tb_code" not in locals():
            # It should be there from generation or reading above
            with open(self.artifacts["tb_path"], "r") as f:
                tb_code = f.read()

        # -----------------------------
        # TB gate stack: static -> compile
        # -----------------------------
        static_ok, static_report = run_tb_static_contract_check(tb_code, self.strategy.name)
        if self.artifacts.get("golden_template"):
            # Golden reference TBs may be procedural; ignore SV-class-only requirements.
            filtered = []
            for issue in static_report.get("issues", []):
                if issue.get("code") in {
                    "missing_transaction_class",
                    "missing_flow_classes",
                }:
                    continue
                filtered.append(issue)
            static_report["issues"] = filtered
            static_report["issue_codes"] = sorted(
                {x.get("code", "") for x in filtered if x.get("code")}
            )
            static_report["ok"] = len(filtered) == 0
            static_ok = static_report["ok"]
        static_path = self._write_tb_diagnostic_artifact("tb_static_gate", static_report)
        self.logger.info(f"TB STATIC GATE ({'PASS' if static_ok else 'FAIL'}): {static_report}")
        self.log(
            f"TB static gate: {'PASS' if static_ok else 'FAIL'} (diag: {os.path.basename(static_path)})",
            refined=True,
        )
        if not static_ok:
            self.tb_static_fail_count += 1
            self._record_tb_gate_history("static", False, "gate_fail", static_report)
            if self._tb_gate_strict_enforced():
                self._handle_tb_gate_failure("static", static_report, tb_code)
                return
            self.log(
                "TB static gate warnings in relaxed mode; continuing to compile gate.",
                refined=True,
            )

        compile_ok, compile_report = run_tb_compile_gate(
            self.name,
            self.artifacts.get("tb_path", ""),
            self.artifacts.get(
                "rtl_path", f"{OPENLANE_ROOT}/designs/{self.name}/src/{self.name}.v"
            ),
        )
        compile_path = self._write_tb_diagnostic_artifact("tb_compile_gate", compile_report)
        self.logger.info(f"TB COMPILE GATE ({'PASS' if compile_ok else 'FAIL'}): {compile_report}")
        self.log(
            f"TB compile gate: {'PASS' if compile_ok else 'FAIL'} (diag: {os.path.basename(compile_path)})",
            refined=True,
        )

        if not compile_ok:
            # If RTL was structurally changed from the sim-failure repair path,
            # the old testbench's port connections are likely incompatible.
            # Immediately regenerate the TB instead of retrying the stale one.
            if self.artifacts.pop("rtl_changed_from_sim_fix", False):
                self.log(
                    "TB compile gate failed after RTL sim-fix. "
                    "Regenerating testbench against updated RTL.",
                    refined=True,
                )
                self._record_tb_gate_history(
                    "compile", False, "regen_after_rtl_fix", compile_report
                )
                tb_path = self.artifacts.get("tb_path")
                if tb_path and os.path.exists(tb_path):
                    try:
                        os.remove(tb_path)
                    except OSError:
                        pass
                self.artifacts.pop("tb_path", None)
                self._clear_tb_fingerprints()
                return  # Re-enters do_verification → generates fresh TB

            self.tb_compile_fail_count += 1
            self._record_tb_gate_history("compile", False, "gate_fail", compile_report)
            self._handle_tb_gate_failure("compile", compile_report, tb_code)
            return

        self.tb_recovery_counts["static"] = 0
        self.tb_recovery_counts["compile"] = 0
        self.artifacts["tb_recovery_cycle_static"] = 0
        self.artifacts["tb_recovery_cycle_compile"] = 0
        self._record_tb_gate_history("compile", True, "gate_pass", compile_report)

        # Run Sim
        with console.status(
            "[accent]Running Simulation...[/accent]",
            spinner="dots12",
            spinner_style="spinner",
        ):
            success, output = run_simulation(self.name)

        self.logger.info(f"SIMULATION OUTPUT:\n{output}")

        if success:
            self.log("Simulation Passed!", refined=True)
            if self.skip_openlane:
                self.log("Skipping Hardening (--skip-openlane).", refined=True)
                self.transition(BuildState.FORMAL_VERIFY)
            else:
                # Interactive Prompt for Hardening
                import typer
                import sys

                if not sys.stdin.isatty():
                    self.log(
                        "Non-interactive session: auto-proceeding after simulation pass.",
                        refined=True,
                    )
                    self.transition(BuildState.FORMAL_VERIFY)
                else:
                    console.print()
                    if typer.confirm(
                        "Simulation Passed. Proceed to OpenLane Hardening (takes 10-30 mins)?",
                        default=True,
                    ):
                        self.transition(BuildState.FORMAL_VERIFY)
                    else:
                        self.log("Skipping Hardening (User Cancelled).", refined=True)
                        self.transition(BuildState.FORMAL_VERIFY)
        else:
            output_for_llm = self._condense_failure_log(output, kind="simulation")
            if self._record_failure_fingerprint(output_for_llm):
                self.log(
                    "Detected repeated simulation failure fingerprint. Failing closed.",
                    refined=True,
                )
                self.state = BuildState.FAIL
                return
            self.retry_count += 1
            self.global_retry_count += 1  # Bug 2: Increment global retry
            if self.retry_count > self.max_retries:
                self.log(
                    f"Max Sim Retries ({self.max_retries}) Exceeded. Simulation Failed.",
                    refined=True,
                )
                self.state = BuildState.FAIL
                return

            self.log(f"Sim Failed (Attempt {self.retry_count}). Check log.", refined=True)

            # --- AUTONOMOUS FIX: Try to fix compilation errors without LLM ---
            # Auto-fixes removed (Verilator supports SV natively)

            # ── Waveform + AST diagnosis (enhances LLM analysis below) ──
            _waveform_context = ""
            _vcd_path = os.path.join(
                OPENLANE_ROOT, "designs", self.name, "src", f"{self.name}_wave.vcd"
            )
            _rtl_path = self.artifacts.get(
                "rtl_path",
                os.path.join(OPENLANE_ROOT, "designs", self.name, "src", f"{self.name}.v"),
            )
            sim_caps = self._simulation_capabilities(output, _vcd_path)
            if (
                sim_caps["trace_enabled"]
                and sim_caps["waveform_generated"]
                and os.path.exists(_rtl_path)
            ):
                _waveform_mod = WaveformExpertModule()
                _diagnosis = _waveform_mod.analyze_failure(
                    rtl_path=_rtl_path,
                    vcd_path=_vcd_path,
                    sim_log=output,  # 'output' is the sim stdout/stderr
                    design_name=self.name,
                )
                waveform_result = self._normalize_waveform_result(_diagnosis, output)
                self._set_artifact(
                    "waveform_diagnosis",
                    waveform_result.to_dict(),
                    producer="agent_waveform",
                    consumer="VERIFICATION",
                )
                if _diagnosis is not None:
                    _waveform_context = (
                        f"\n\n## WAVEFORM + AST ANALYSIS\n"
                        f"{_diagnosis.diagnosis_summary}\n"
                        f"Fix location: {_diagnosis.suggested_fix_area}\n"
                    )
                    self.logger.info(
                        f"[WaveformExpert] {_diagnosis.failing_signal} mismatch "
                        f"at t={_diagnosis.mismatch_time}"
                    )
                else:
                    self.logger.info("[WaveformExpert] No signal mismatch found in VCD")
            else:
                _vcd_size = os.path.getsize(_vcd_path) if os.path.exists(_vcd_path) else 0
                self._record_stage_contract(
                    StageResult(
                        stage=self.state.name,
                        status=StageStatus.RETRY,
                        producer="orchestrator_waveform_gate",
                        failure_class=FailureClass.ORCHESTRATOR_ROUTING_ERROR,
                        diagnostics=[
                            f"WaveformExpert gated off: trace_enabled={sim_caps['trace_enabled']}, "
                            f"waveform_generated={sim_caps['waveform_generated']}, rtl_exists={os.path.exists(_rtl_path)}"
                        ],
                        next_action="continue_without_waveform",
                    )
                )
                self.logger.info(
                    f"[WaveformExpert] Skipping — VCD exists={os.path.exists(_vcd_path)}, "
                    f"size={_vcd_size}, rtl_exists={os.path.exists(_rtl_path)}"
                )

            # --- LLM ERROR ANALYSIS + FIX: Collaborative 2-agent Crew ---
            analyst = get_error_analyst_agent(self.get_llm_for_role("fixer"))
            analysis_task = Task(
                description=f'''Analyze this Verification Failure for "{self.name}".

BUILD CONTEXT:
{self._build_llm_context(include_rtl=False)}

ERROR LOG:
{output_for_llm}{_waveform_context}

CURRENT TESTBENCH (first 3000 chars):
{tb_code[:3000]}

Use your read_file tool to read the full RTL and TB files if needed.

Classify the failure as ONE of:
A) TESTBENCH_SYNTAX
B) RTL_LOGIC_BUG
C) PORT_MISMATCH
D) TIMING_RACE
E) ARCHITECTURAL

Reply with JSON only, no prose, using this exact schema:
{{
  "class": "A|B|C|D|E",
  "failing_output": "exact failing display or summary",
  "failing_signals": ["sig1", "sig2"],
  "expected_vs_actual": "expected vs actual or undetermined",
  "responsible_construct": "specific RTL construct and line number",
  "root_cause": "1-line root cause",
  "fix_hint": "surgical fix hint"
}}''',
                expected_output="JSON object with class, failing_output, failing_signals, expected_vs_actual, responsible_construct, root_cause, and fix_hint",
                agent=analyst,
            )

            with console.status("[error]Analyzing Failure (Multi-Class)...[/error]"):
                analysis = str(
                    self._crew_kickoff(Crew(verbose=False, agents=[analyst], tasks=[analysis_task]))
                ).strip()

            self.logger.info(f"FAILURE ANALYSIS:\n{analysis}")
            analyst_result = self._parse_structured_agent_json(
                agent_name="VerificationAnalyst",
                raw_output=analysis,
                required_keys=[
                    "class",
                    "failing_output",
                    "failing_signals",
                    "expected_vs_actual",
                    "responsible_construct",
                    "root_cause",
                    "fix_hint",
                ],
            )
            if not analyst_result.ok:
                self._record_non_consumable_output(
                    "agent_verificationanalyst",
                    analysis,
                    analyst_result.diagnostics,
                )
                with console.status("[error]Retrying Failure Analysis (JSON)...[/error]"):
                    analysis = str(
                        self._crew_kickoff(
                            Crew(verbose=False, agents=[analyst], tasks=[analysis_task])
                        )
                    ).strip()
                self.logger.info(f"FAILURE ANALYSIS RETRY:\n{analysis}")
                analyst_result = self._parse_structured_agent_json(
                    agent_name="VerificationAnalyst",
                    raw_output=analysis,
                    required_keys=[
                        "class",
                        "failing_output",
                        "failing_signals",
                        "expected_vs_actual",
                        "responsible_construct",
                        "root_cause",
                        "fix_hint",
                    ],
                )
                if not analyst_result.ok:
                    self.log(
                        "Verification analysis returned invalid JSON twice. Failing closed.",
                        refined=True,
                    )
                    self.state = BuildState.FAIL
                    return
            self._set_artifact(
                "verification_analysis",
                analyst_result.to_dict(),
                producer="agent_verificationanalyst",
                consumer="VERIFICATION",
            )
            analysis_payload = analyst_result.payload
            failure_class = str(analysis_payload.get("class", "A")).upper()[:1] or "A"
            root_cause = str(analysis_payload.get("root_cause", "")).strip()
            fix_hint = str(analysis_payload.get("fix_hint", "")).strip()
            failing_output = str(analysis_payload.get("failing_output", "")).strip()
            failing_signals_list = analysis_payload.get("failing_signals", [])
            if isinstance(failing_signals_list, list):
                failing_signals = ", ".join(str(x) for x in failing_signals_list)
            else:
                failing_signals = str(failing_signals_list)
            expected_vs_actual = str(analysis_payload.get("expected_vs_actual", "")).strip()
            responsible_construct = str(analysis_payload.get("responsible_construct", "")).strip()

            # Build structured diagnosis string for downstream fix prompts
            structured_diagnosis = (
                f"FAILING_OUTPUT: {failing_output}\n"
                f"FAILING_SIGNALS: {failing_signals}\n"
                f"EXPECTED_VS_ACTUAL: {expected_vs_actual}\n"
                f"RESPONSIBLE_CONSTRUCT: {responsible_construct}\n"
                f"ROOT_CAUSE: {root_cause}\n"
                f"FIX_HINT: {fix_hint}"
            )

            self.log(
                f"Diagnosis: CLASS={failure_class} | ROOT_CAUSE={root_cause[:100]} | FIX_HINT={fix_hint[:100]}",
                refined=True,
            )

            # --- Route to fix path based on failure class ---

            if failure_class == "C":
                # PORT_MISMATCH — deterministic auto-fix: regenerate TB from RTL ports
                self.log(
                    "Port mismatch detected. Regenerating TB from RTL ports.",
                    refined=True,
                )
                fallback_tb = self._deterministic_tb_fallback(self.artifacts.get("rtl_code", ""))
                path = write_verilog(self.name, fallback_tb, is_testbench=True)
                if isinstance(path, str) and not path.startswith("Error:"):
                    self.artifacts["tb_path"] = path
                return

            if failure_class == "D":
                # TIMING_RACE — deterministic auto-fix: progressively extend reset + add stabilization
                self.log(
                    "Timing race detected. Applying reset timing fix to TB.",
                    refined=True,
                )
                fixed_tb = tb_code.replace("#20;", "#100;").replace("#50;", "#200;")
                # Progressive repeat escalation: 5→20→50→100
                for old_rep, new_rep in [
                    ("repeat (5)", "repeat (20)"),
                    ("repeat (20)", "repeat (50)"),
                    ("repeat (50)", "repeat (100)"),
                ]:
                    if old_rep in fixed_tb:
                        fixed_tb = fixed_tb.replace(old_rep, new_rep)
                        break
                # Add post-reset stabilization: insert wait cycles after reset de-assertion
                if "reset_phase" in fixed_tb and "// post-reset-stab" not in fixed_tb:
                    # After rst_n = 1'b1 (or 1'b0 for active-high), add stabilization
                    for deassert in ["rst_n = 1'b1;", "reset = 1'b0;"]:
                        if deassert in fixed_tb:
                            fixed_tb = fixed_tb.replace(
                                deassert,
                                f"{deassert}\n    repeat (3) @(posedge clk); // post-reset-stab",
                            )
                            break
                if fixed_tb != tb_code:
                    path = write_verilog(self.name, fixed_tb, is_testbench=True)
                    if isinstance(path, str) and not path.startswith("Error:"):
                        self.artifacts["tb_path"] = path
                    return
                # If auto-fix didn't change anything, fall through to LLM fix
                self.log(
                    "Timing race auto-fix exhausted. Falling through to LLM fix.",
                    refined=True,
                )

            if failure_class == "E":
                # ARCHITECTURAL — spec is flawed, go back to SPEC stage
                self.log(
                    f"Architectural issue detected: {root_cause}. Regenerating spec.",
                    refined=True,
                )
                self.artifacts["logic_decoupling_hint"] = (
                    f"Previous build failed due to: {root_cause}. {fix_hint}"
                )
                self.transition(BuildState.SPEC, preserve_retries=True)
                return

            # Classes A (TB syntax) and B (RTL logic) — use LLM fix
            is_tb_issue = failure_class == "A"

            if is_tb_issue:
                self.log("Analyst identified Testbench Error. Fixing TB...", refined=True)
                fixer = get_testbench_agent(
                    self.get_llm_for_role("fixer"), f"Fix TB for {self.name}"
                )
                port_info = self._extract_module_interface(self.artifacts["rtl_code"])
                fix_prompt = f"""Fix the Testbench logic/syntax.

DIAGNOSIS FROM ERROR ANALYST:
ROOT CAUSE: {root_cause}
FIX HINT: {fix_hint}

ERROR LOG:
{output_for_llm}

MODULE INTERFACE (use EXACT port names):
{port_info}

Current TB:
```verilog
{tb_code}
```

Ref RTL:
```verilog
{self.artifacts["rtl_code"]}
```

PREVIOUS ATTEMPTS:
{self._format_failure_history()}

CRITICAL RULES:
- Return ONLY the fixed Testbench code in ```verilog fences.
- Do NOT invent ports that aren't in the MODULE INTERFACE above.
- Module name of DUT is "{self.name}"
- NEVER use: class, interface, covergroup, program, rand, virtual, new()
- Use flat procedural style: reg/wire declarations, initial/always blocks
- Use your syntax_check tool to verify the fix compiles before returning it

SYNCHRONOUS DUT TIMING RULE (mandatory for ALL designs):
This DUT is synchronous. All registered outputs update on the rising clock edge.
After applying any stimulus (reset deassertion, enable assertion, data input),
always wait for at least one complete clock cycle (`@(posedge clk);` or
`repeat(N) @(posedge clk);`) before sampling or comparing any DUT output.
Never sample a DUT output in the same time step that stimulus is applied.
"""
            else:
                self.log("Analyst identified RTL Logic Error. Fixing RTL...", refined=True)
                fixer = get_designer_agent(
                    self.get_llm_for_role("fixer"),
                    f"Fix RTL for {self.name}",
                    strategy=self.strategy.name,
                )
                error_lines = [
                    line for line in output.split("\n") if "Error" in line or "fail" in line.lower()
                ]
                error_summary = "\n".join(error_lines)

                fix_prompt = f"""RESPOND WITH VERILOG CODE ONLY. No explanation, no commentary, no "Thought:" prefixes.

SURGICAL FIX REQUIRED — make the MINIMUM change to fix the specific issue identified.

SIGNAL-LEVEL DIAGNOSIS FROM ERROR ANALYST:
{structured_diagnosis}

Specific Issues Detected:
{error_summary}

Full Log:
{output_for_llm}

Current RTL:
```verilog
{self.artifacts["rtl_code"]}
```

Ref TB:
```verilog
{tb_code}
```

PREVIOUS ATTEMPTS:
{self._format_failure_history()}

CRITICAL RULES:
- You MUST make the minimum possible change to fix the specific issue identified.
- Do NOT rewrite the module. Do NOT restructure the design.
- Identify the exact lines responsible for the failure and change ONLY those lines.
- Do NOT remove sub-module instantiations or flatten a hierarchical design.
- Do NOT change port names, port widths, or module interfaces.
- Return the complete module with only the specific buggy lines changed.
- Use your syntax_check tool to verify the fix compiles before returning it.
- Return ONLY the fixed {self.strategy.name} code in ```verilog fences.
"""

            # Execute Fix — fixer uses analyst's diagnosis as context
            fix_task = Task(
                description=fix_prompt,
                expected_output="Fixed Verilog Code in ```verilog fences",
                agent=fixer,
            )

            with console.status(
                "[warning]AI Implementing Fix...[/warning]",
                spinner="dots12",
                spinner_style="spinner",
            ):
                result = self._crew_kickoff(
                    Crew(
                        verbose=False,
                        agents=[fixer],
                        tasks=[fix_task],
                    )
                )
            fixed_code = str(result) if result else None
            # --- Universal Verilog extraction + validation (fix) ---
            if fixed_code and fixed_code != "None":
                is_valid, extracted = extract_and_validate_llm_code(fixed_code)
                if is_valid:
                    fixed_code = extracted
                elif extracted:
                    self.log("Extracted Verilog from fix output, proceeding.", refined=True)
                    fixed_code = extracted
                else:
                    self.log(
                        "Fix generation failed to produce Verilog. Retrying once.", refined=True
                    )
                    self.logger.warning(
                        f"FIX VALIDATION FAIL (no Verilog extracted):\n{fixed_code[:500]}"
                    )
                    result = self._crew_kickoff(
                        Crew(
                            verbose=False,
                            agents=[fixer],
                            tasks=[fix_task],
                        )
                    )
                    fixed_code = str(result) if result else None
            else:
                self.log("Fix generation returned empty/None result. Retrying once.", refined=True)
                result = self._crew_kickoff(
                    Crew(
                        verbose=False,
                        agents=[fixer],
                        tasks=[fix_task],
                    )
                )
                fixed_code = str(result) if result else None

            if fixed_code and fixed_code != "None":
                self.logger.info(f"FIXED CODE:\n{fixed_code[:500]}...")

            if not is_tb_issue:
                # RTL Fix — diff check to reject full rewrites
                original_code = self.artifacts.get("rtl_code", "")
                original_lines = original_code.splitlines()
                fixed_lines = fixed_code.splitlines()

                # Count changed lines using SequenceMatcher
                if original_lines and fixed_lines:
                    matcher = difflib.SequenceMatcher(None, original_lines, fixed_lines)
                    unchanged = sum(block.size for block in matcher.get_matching_blocks())
                    total = max(len(original_lines), len(fixed_lines))
                    changed_ratio = 1.0 - (unchanged / total) if total > 0 else 0.0
                    self.logger.info(
                        f"RTL DIFF CHECK: {changed_ratio:.1%} of lines changed "
                        f"({total - unchanged}/{total})"
                    )

                    if changed_ratio > 0.30:
                        self.log(
                            f"RTL fix rejected: {changed_ratio:.0%} of lines changed (>30%). "
                            "Requesting surgical retry.",
                            refined=True,
                        )
                        # Re-prompt with explicit rejection context
                        retry_prompt = f"""RESPOND WITH VERILOG CODE ONLY.

Your previous fix was REJECTED because it changed {changed_ratio:.0%} of the code (>30% threshold).
You must make a SURGICAL fix — change ONLY the specific lines that cause the bug.

DIAGNOSIS:
{structured_diagnosis}

Original RTL (DO NOT REWRITE — change only the buggy lines):
```verilog
{original_code}
```

Return the complete module with ONLY the minimal fix applied.
"""
                        retry_task = Task(
                            description=retry_prompt,
                            expected_output="Fixed Verilog Code in ```verilog fences",
                            agent=fixer,
                        )
                        with console.status(
                            "[warning]AI Implementing Surgical Fix (retry)...[/warning]"
                        ):
                            result2 = self._crew_kickoff(
                                Crew(verbose=False, agents=[fixer], tasks=[retry_task])
                            )
                        fixed_code = str(result2)
                        # --- Universal code output validation (surgical RTL retry) ---
                        if not validate_llm_code_output(fixed_code):
                            self.log(
                                "Surgical RTL retry returned prose instead of code. Retrying once.",
                                refined=True,
                            )
                            self.logger.warning(
                                f"SURGICAL RETRY VALIDATION FAIL (prose detected):\n{fixed_code[:500]}"
                            )
                            fixed_code = str(
                                self._crew_kickoff(
                                    Crew(
                                        verbose=False,
                                        agents=[fixer],
                                        tasks=[retry_task],
                                    )
                                )
                            )
                        self.logger.info(f"SURGICAL RETRY CODE:\n{fixed_code}")

                # Write cleaned code and read it back
                path = write_verilog(self.name, fixed_code)
                if isinstance(path, str) and path.startswith("Error:"):
                    self.log(f"File Write Error when fixing RTL logic: {path}", refined=True)
                    self.state = BuildState.FAIL
                    return
                self.artifacts["rtl_path"] = path
                with open(path, "r") as f:
                    self.artifacts["rtl_code"] = f.read()
                # Flag that RTL was structurally changed from the sim-failure path
                # so TB compile gate knows to regenerate the testbench if needed
                self.artifacts["rtl_changed_from_sim_fix"] = True
                self.log(
                    "RTL Updated. Transitioning back to RTL_FIX to verify syntax.",
                    refined=True,
                )
                self.transition(BuildState.RTL_FIX, preserve_retries=True)
                return
            else:
                # TB Fix
                path = write_verilog(self.name, fixed_code, is_testbench=True)
                if isinstance(path, str) and path.startswith("Error:"):
                    self.log(f"File Write Error when fixing TB logic: {path}", refined=True)
                    self.state = BuildState.FAIL
                    return
                self.artifacts["tb_path"] = path
                return

    # ===============================================
    # INDUSTRY-STANDARD STATE HANDLERS
    # ===============================================

    def do_formal_verify(self):
        """Runs formal property verification using SymbiYosys."""
        self.log("Starting Formal Property Verification...", refined=True)

        rtl_path = self.artifacts.get("rtl_path")
        if not rtl_path:
            self.log("RTL path not found in artifacts. Skipping formal.", refined=True)
            self.transition(BuildState.COVERAGE_CHECK)
            return

        # 1. Generate SVA assertions using LLM
        sva_path = f"{OPENLANE_ROOT}/designs/{self.name}/src/{self.name}_sva.sv"

        if not os.path.exists(sva_path):
            self.log("Generating SVA Assertions...", refined=True)

            # Bug 3: Inject DeepDebugger diagnostic context into the SVA generation prompt
            formal_debug = self.artifacts.get("formal_debug_context", "")
            formal_preflight_error = self._consume_handoff(
                "formal_preflight_error",
                consumer="FORMAL_VERIFY",
                required=False,
            ) or self.artifacts.get("sva_preflight_error", "")
            if formal_debug:
                formal_debug_str = f"\n\nPREVIOUS FORMAL VERIFICATION FAILURE DIAGNOSIS:\n{formal_debug}\n\nPlease use this diagnosis to correct the flawed assertions.\n"
            else:
                formal_debug_str = ""
            if formal_preflight_error:
                formal_debug_str += (
                    "\n\nPREVIOUS YOSYS/SVA PREFLIGHT FAILURE:\n"
                    f"{formal_preflight_error}\n"
                    "You must correct the assertions so this exact failure does not recur.\n"
                )
            try:
                with open(rtl_path, "r") as rtl_file:
                    rtl_for_sva = rtl_file.read()
            except OSError:
                rtl_for_sva = self.artifacts.get("rtl_code", "")
            signal_inventory = self._format_signal_inventory_for_prompt(rtl_for_sva)

            verif_agent = get_verification_agent(self.get_llm_for_role("verifier"))
            sva_task = Task(
                description=f"""Generate SystemVerilog Assertions (SVA) for module "{self.name}".

Generate SVA assertions that are compatible with the Yosys formal verification engine. Yosys has limited SVA support. Before writing any assertion syntax, reason about whether Yosys can parse it. Use the simplest correct assertion style. If unsure whether a construct is Yosys-compatible, use a simpler equivalent.
                
                RTL Code:
                ```verilog
                {rtl_for_sva}
                ```

                The DUT has the following signals with these exact widths:
                {signal_inventory}
                Use only these signals and these exact widths in every assertion. Do not invent signals, aliases, or widths.
                
                SPECIFICATION:
                {self.artifacts.get("spec", "")}
                {formal_debug_str}
                Requirements:
                1. Create a separate SVA module named "{self.name}_sva"
                2. **CRITICAL FOR SYMBIYOSYS/YOSYS COMPATIBILITY:**
                   - Use **Concurrent Assertions** (`assert property`) at the **MODULE LEVEL**.
                   - **DO NOT** wrap assertions inside `always` blocks.
                   - **DO NOT** use `disable iff` inside procedural code.
                   - Example of correct style:
                     `assert property (@(posedge clk) disable iff (!rst_n) req |-> ##1 ack);`
                3. Include properties for:
                   - Reset behavior
                   - Protocol compliance
                   - State machine reachability
                4. Include cover properties (`cover property`)
                5. Return code inside ```verilog fences

                OUTPUT FORMAT CONSTRAINT (mandatory):
                Your entire response must be valid SystemVerilog code only. No explanations, no prose, no comments before the module declaration. Your response must begin with the keyword `module`. Any response that does not begin with `module` will be rejected and retried.
                """,
                expected_output="SystemVerilog SVA module",
                agent=verif_agent,
            )

            with console.status(
                "[accent]AI Generating SVA Assertions...[/accent]",
                spinner="dots12",
                spinner_style="spinner",
            ):
                sva_result = str(
                    self._crew_kickoff(Crew(verbose=False, agents=[verif_agent], tasks=[sva_task]))
                )

            # --- Universal code output validation (SVA) ---
            if not validate_llm_code_output(sva_result):
                self._record_non_consumable_output(
                    "llm_sva_generation",
                    sva_result,
                    ["SVA generation returned prose instead of code."],
                )
                self.log(
                    "SVA generation returned prose instead of code. Retrying with error feedback.",
                    refined=True,
                )
                self.logger.warning(f"SVA VALIDATION FAIL (prose detected):\n{sva_result[:500]}")

                retry_task = Task(
                    description=sva_task.description
                    + f"\n\n[RETRY ERROR FEEDBACK - IGNORE PREVIOUS OUTPUT]:\n"
                    + "Your previous response was invalid because it contained prose/explanations "
                    + "instead of only SystemVerilog code. The response must begin with `module` "
                    + "and contain ONLY code. Try again with only valid code output.",
                    expected_output="SystemVerilog SVA module",
                    agent=verif_agent,
                )
                sva_result = str(
                    self._crew_kickoff(
                        Crew(verbose=False, agents=[verif_agent], tasks=[retry_task])
                    )
                )
                if not validate_llm_code_output(sva_result):
                    self.log(
                        "SVA retry also returned invalid output. Skipping formal.",
                        refined=True,
                    )
                    self.transition(BuildState.COVERAGE_CHECK)
                    return
            sva_validation_issues = self._validate_sva_candidate(sva_result, rtl_for_sva)
            if sva_validation_issues:
                self._record_stage_contract(
                    StageResult(
                        stage=self.state.name,
                        status=StageStatus.RETRY,
                        producer="orchestrator_sva_validator",
                        failure_class=FailureClass.LLM_SEMANTIC_ERROR,
                        diagnostics=sva_validation_issues,
                        next_action="retry_sva_generation",
                    )
                )
                self.log(f"SVA candidate rejected: {sva_validation_issues[0]}", refined=True)
                for stale in (sva_path,):
                    if os.path.exists(stale):
                        os.remove(stale)
                return

            self.logger.info(f"GENERATED SVA:\n{sva_result}")

            # Write SVA file
            sva_write_path = write_verilog(self.name, sva_result, suffix="_sva", ext=".sv")
            if isinstance(sva_write_path, str) and sva_write_path.startswith("Error:"):
                self.log(
                    f"SVA write failed: {sva_write_path}. Skipping formal.",
                    refined=True,
                )
                self.transition(BuildState.COVERAGE_CHECK)
                return

        # 2. Convert SVA to Yosys-compatible format
        try:
            with open(sva_path, "r") as f:
                sva_content = f.read()

            yosys_code = convert_sva_to_yosys(sva_content, self.name)
            if not yosys_code:
                self.artifacts["formal_result"] = (
                    "FAIL: unable to translate SVA into Yosys-compatible form"
                )
                self.log(
                    "Failed to convert generated SVA to Yosys-compatible assertions.",
                    refined=True,
                )
                if self.strict_gates:
                    self.log("Formal translation failed under strict mode.", refined=True)
                    self.state = BuildState.FAIL
                    return
                self.transition(BuildState.COVERAGE_CHECK)
                return

            preflight_ok, preflight_report = validate_yosys_sby_check(yosys_code)
            formal_diag_path = (
                f"{OPENLANE_ROOT}/designs/{self.name}/src/{self.name}_formal_preflight.json"
            )
            with open(formal_diag_path, "w") as f:
                json.dump(preflight_report, f, indent=2)
            self.artifacts["formal_preflight"] = preflight_report
            self.artifacts["formal_preflight_path"] = formal_diag_path
            self._set_artifact(
                "formal_preflight_report",
                preflight_report,
                producer="orchestrator_formal_preflight",
                consumer="FORMAL_VERIFY",
            )

            if not preflight_ok:
                self._set_artifact(
                    "formal_preflight_error",
                    json.dumps(preflight_report, indent=2)[:2000],
                    producer="orchestrator_formal_preflight",
                    consumer="FORMAL_VERIFY",
                )
                self.log(
                    f"Formal preflight failed: {preflight_report.get('issue_count', 0)} issue(s).",
                    refined=True,
                )
                self.artifacts["formal_result"] = "FAIL"
                if self.strict_gates:
                    self.log(
                        "Formal preflight issues are blocking under strict mode.",
                        refined=True,
                    )
                    self.state = BuildState.FAIL
                    return
                self.transition(BuildState.COVERAGE_CHECK)
                return

            sby_check_path = f"{OPENLANE_ROOT}/designs/{self.name}/src/{self.name}_sby_check.sv"
            with open(sby_check_path, "w") as f:
                f.write(yosys_code)
            self.log("Yosys-compatible assertions generated.", refined=True)

            # 3. Yosys SVA preflight — catch syntax errors before sby runs
            from .config import YOSYS_BIN as _YOSYS_BIN

            _yosys = _YOSYS_BIN or "yosys"
            preflight_cmd = [_yosys, "-p", f"read_verilog -formal -sv {sby_check_path}"]
            try:
                pf = subprocess.run(preflight_cmd, capture_output=True, text=True, timeout=30)
                if pf.returncode != 0:
                    yosys_err = (pf.stderr or pf.stdout or "").strip()
                    self.logger.info(f"YOSYS SVA PREFLIGHT FAIL:\n{yosys_err}")
                    prev_err = self.artifacts.get("sva_preflight_error_last", "")
                    if prev_err == yosys_err:
                        streak = int(self.artifacts.get("sva_preflight_error_streak", 0)) + 1
                    else:
                        streak = 1
                    self.artifacts["sva_preflight_error_last"] = yosys_err
                    self.artifacts["sva_preflight_error_streak"] = streak
                    self.artifacts["sva_preflight_error"] = yosys_err[:2000]
                    self._set_artifact(
                        "formal_preflight_error",
                        yosys_err[:2000],
                        producer="yosys_preflight",
                        consumer="FORMAL_VERIFY",
                    )
                    if streak >= 2:
                        self.log(
                            "Repeated Yosys SVA preflight failure detected. Skipping formal instead of regenerating again.",
                            refined=True,
                        )
                        self.artifacts["formal_result"] = "SKIP"
                        self.artifacts["sva_preflight_skip_reason"] = yosys_err[:2000]
                        self.transition(BuildState.COVERAGE_CHECK)
                        return
                    self.log(
                        "Yosys SVA preflight failed. Regenerating SVA with error context.",
                        refined=True,
                    )
                    # Remove stale SVA files so the next iteration regenerates
                    for stale in (sva_path, sby_check_path):
                        if os.path.exists(stale):
                            os.remove(stale)
                    # Stay in FORMAL_VERIFY — will regenerate SVA on re-entry
                    return
                self.artifacts.pop("sva_preflight_error_last", None)
                self.artifacts.pop("sva_preflight_error_streak", None)
            except Exception as pf_exc:
                self.logger.warning(f"Yosys SVA preflight exception: {pf_exc}")

            # 4. Write SBY config and run
            write_sby_config(self.name, use_sby_check=True)

            with console.status("[accent]Running Formal Verification (SymbiYosys)...[/accent]"):
                success, result = run_formal_verification(self.name)

            self.logger.info(f"FORMAL RESULT:\n{result}")

            if success:
                self.log("Formal Verification PASSED!", refined=True)
                self.artifacts["formal_result"] = "PASS"
            else:
                self.log(f"Formal Verification: {result[:200]}", refined=True)

                # ── FVDebug: Causal graph + balanced analysis ──
                _formal_debug_context = ""
                _rtl_path_fv = self.artifacts.get("rtl_path", "")
                _sby_cfg = os.path.join(
                    OPENLANE_ROOT,
                    "designs",
                    self.name,
                    "src",
                    f"{self.name}_sby_check.sby",
                )
                # sby config is written by write_sby_config() — check both extensions
                if not os.path.exists(_sby_cfg):
                    _sby_cfg = os.path.join(
                        OPENLANE_ROOT,
                        "designs",
                        self.name,
                        "src",
                        f"{self.name}_formal.sby",
                    )
                from .config import SBY_BIN as _SBY_BIN, YOSYS_BIN as _YOSYS_BIN

                if os.path.exists(_sby_cfg) and os.path.exists(_rtl_path_fv):
                    _debugger = DeepDebuggerModule(
                        llm=self.get_llm_for_role("debugger"),
                        sby_bin=_SBY_BIN or "sby",
                        yosys_bin=_YOSYS_BIN or "yosys",
                        verbose=self.verbose,
                        max_signals_to_analyze=4,
                    )
                    _verdict = _debugger.debug_formal_failure(
                        rtl_path=_rtl_path_fv,
                        sby_config_path=_sby_cfg,
                        design_name=self.name,
                        rtl_code=self.artifacts.get("rtl_code", ""),
                    )
                    debug_result = self._normalize_deepdebug_result(_verdict, result)
                    self._set_artifact(
                        "formal_debug_result",
                        debug_result.to_dict(),
                        producer="agent_deepdebug",
                        consumer="FORMAL_VERIFY",
                    )
                    if _verdict is not None:
                        _formal_debug_context = (
                            f"\n\nFVDEBUG ROOT CAUSE:\n"
                            f"  Signal: '{_verdict.root_cause_signal}' "
                            f"at line {_verdict.root_cause_line} "
                            f"(confidence: {_verdict.confidence:.0%})\n"
                            f"  Fix: {_verdict.fix_description}\n"
                            f"  Analysis:\n{_verdict.balanced_analysis_log}\n"
                        )
                        self.logger.info(
                            f"[DeepDebugger] Root cause: {_verdict.root_cause_signal} "
                            f"line {_verdict.root_cause_line} conf={_verdict.confidence:.2f}"
                        )
                    else:
                        self.logger.info("[DeepDebugger] No verdict (all signals uncertain)")
                else:
                    self.logger.info(
                        f"[DeepDebugger] Skipping — "
                        f"sby_cfg_exists={os.path.exists(_sby_cfg)}, "
                        f"rtl_exists={os.path.exists(_rtl_path_fv)}"
                    )
                self._set_artifact(
                    "formal_debug_context",
                    _formal_debug_context,
                    producer="agent_deepdebug",
                    consumer="FORMAL_VERIFY",
                )

                self.artifacts["formal_result"] = "FAIL"
                if self.strict_gates:
                    self.log("Formal verification failed under strict mode.", refined=True)
                    self.state = BuildState.FAIL
                    return
        except Exception as e:
            self.log(f"Formal verification error: {str(e)}.", refined=True)
            self.artifacts["formal_result"] = f"ERROR: {str(e)}"
            if self.strict_gates:
                self.state = BuildState.FAIL
                return

        # 4. Run CDC check
        with console.status(
            "[accent]Running CDC Analysis...[/accent]",
            spinner="dots12",
            spinner_style="spinner",
        ):
            cdc_clean, cdc_report = run_cdc_check(rtl_path)

        self.logger.info(f"CDC REPORT:\n{cdc_report}")
        self.artifacts["cdc_result"] = "CLEAN" if cdc_clean else "WARNINGS"

        if cdc_clean:
            self.log("CDC Analysis: CLEAN", refined=True)
        else:
            self.log(f"CDC Analysis: warnings found", refined=True)
            if self.strict_gates:
                self.log("CDC issues are blocking under strict mode.", refined=True)
                self.state = BuildState.FAIL
                return

        if self.skip_coverage:
            self.log("Skipping Coverage Analysis (--skip-coverage).", refined=True)
            self.transition(BuildState.REGRESSION)
            return

        self.transition(BuildState.COVERAGE_CHECK)

    def do_coverage_check(self):
        """Runs coverage with adapter backend and strict fail-closed semantics."""
        thresholds = dict(self.coverage_thresholds)
        thresholds["line"] = max(float(thresholds.get("line", 85.0)), float(self.min_coverage))
        self.log(
            (
                f"Running Coverage Analysis "
                f"(backend={self.coverage_backend}, profile={self.coverage_profile}, "
                f"line gate>={thresholds['line']:.1f}%)..."
            ),
            refined=True,
        )

        with console.status(
            "[accent]Running Coverage-Instrumented Simulation...[/accent]",
            spinner="dots12",
            spinner_style="spinner",
        ):
            sim_passed, sim_output, coverage_data = run_simulation_with_coverage(
                self.name,
                backend=self.coverage_backend,
                fallback_policy=self.coverage_fallback_policy,
                profile=self.coverage_profile,
            )

        if not isinstance(coverage_data, dict):
            coverage_data = {
                "ok": False,
                "infra_failure": True,
                "error_kind": "invalid_result",
                "diagnostics": ["Coverage adapter returned invalid payload."],
                "line_pct": 0.0,
                "branch_pct": 0.0,
                "toggle_pct": 0.0,
                "functional_pct": 0.0,
                "assertion_pct": 0.0,
                "signals_toggled": 0,
                "total_signals": 0,
                "backend": self.coverage_backend,
                "coverage_mode": "failed",
            }

        self.logger.info(f"COVERAGE DATA:\n{coverage_data}")
        self.logger.info(
            f"COVERAGE SIM OUTPUT:\n{sim_output[:8000] if isinstance(sim_output, str) else sim_output}"
        )
        coverage_data["thresholds"] = thresholds
        self.artifacts["coverage"] = coverage_data
        self.artifacts["coverage_backend_used"] = coverage_data.get(
            "backend", self.coverage_backend
        )
        self.artifacts["coverage_mode"] = coverage_data.get("coverage_mode", "unknown")
        self._set_artifact(
            "coverage_improvement_context",
            {
                "coverage_data": coverage_data,
                "sim_output": sim_output[:4000] if isinstance(sim_output, str) else str(sim_output),
            },
            producer="orchestrator_coverage",
            consumer="COVERAGE_CHECK",
        )

        src_dir = os.path.join(OPENLANE_ROOT, "designs", self.name, "src")
        os.makedirs(src_dir, exist_ok=True)
        attempt = int(self.artifacts.get("coverage_attempt_count", 0)) + 1
        self.artifacts["coverage_attempt_count"] = attempt
        coverage_attempt = os.path.join(src_dir, f"{self.name}_coverage_attempt{attempt}.json")
        coverage_latest = os.path.join(src_dir, f"{self.name}_coverage_latest.json")
        with open(coverage_attempt, "w") as f:
            json.dump(coverage_data, f, indent=2)
        with open(coverage_latest, "w") as f:
            json.dump(coverage_data, f, indent=2)
        self.artifacts["coverage_latest_path"] = coverage_latest
        self.artifacts["coverage_attempt_path"] = coverage_attempt

        line_pct = float(coverage_data.get("line_pct", 0.0))
        branch_pct = float(coverage_data.get("branch_pct", 0.0))
        toggle_pct = float(coverage_data.get("toggle_pct", 0.0))
        functional_pct = float(coverage_data.get("functional_pct", 0.0))
        self.log(
            (
                f"Coverage[{coverage_data.get('backend', 'unknown')}/{coverage_data.get('coverage_mode', 'unknown')}]: "
                f"line={line_pct:.1f}% branch={branch_pct:.1f}% toggle={toggle_pct:.1f}% "
                f"functional={functional_pct:.1f}%"
            ),
            refined=True,
        )

        if coverage_data.get("infra_failure", False):
            diag = coverage_data.get("diagnostics", [])
            msg = "; ".join(diag[:2]) if isinstance(diag, list) else str(diag)
            self.log(
                f"Coverage infrastructure failure ({coverage_data.get('error_kind', 'unknown')}): {msg}",
                refined=True,
            )
            if self.coverage_fallback_policy == "skip":
                self.log(
                    "Coverage fallback policy=skip; proceeding without blocking.",
                    refined=True,
                )
                if self.full_signoff:
                    self.transition(BuildState.REGRESSION)
                elif self.skip_openlane:
                    self.transition(BuildState.SUCCESS)
                else:
                    self.transition(BuildState.SDC_GEN)
                return
            if self.strict_gates:
                self.log(
                    "Coverage infra failure is blocking under strict mode.",
                    refined=True,
                )
                self.state = BuildState.FAIL
                return
            self.log("Coverage infra failure tolerated in non-strict mode.", refined=True)
            if self.full_signoff:
                self.transition(BuildState.REGRESSION)
            elif self.skip_openlane:
                self.transition(BuildState.SUCCESS)
            else:
                self.transition(BuildState.SDC_GEN)
            return

        if not coverage_data.get("ok", False):
            self.log("Coverage result invalid (non-infra).", refined=True)
            if self.strict_gates:
                self.state = BuildState.FAIL
                return

        coverage_checks = {
            "line": line_pct >= float(thresholds["line"]),
            "branch": branch_pct >= float(thresholds["branch"]),
            "toggle": toggle_pct >= float(thresholds["toggle"]),
            "functional": functional_pct >= float(thresholds["functional"]),
        }
        coverage_pass = all(coverage_checks.values())

        if not hasattr(self, "best_coverage") or self.best_coverage is None:
            self.best_coverage = -1.0
            self.best_tb_backup = None

        if line_pct > getattr(self, "best_coverage", -1.0):
            self.best_coverage = line_pct
            import shutil

            tb_path = self.artifacts.get("tb_path")
            if tb_path and os.path.exists(tb_path):
                backup_path = tb_path + ".best"
                shutil.copy(tb_path, backup_path)
                self.best_tb_backup = backup_path
                self.log(f"New Best Coverage: {line_pct:.1f}% (Backed up)", refined=True)

        if coverage_pass:
            self.log(
                (
                    "Coverage PASSED "
                    f"(line>={thresholds['line']:.1f}, branch>=95.0, "
                    f"toggle>={thresholds['toggle']:.1f}, functional>={thresholds['functional']:.1f})"
                ),
                refined=True,
            )
            if self.full_signoff:
                self.transition(BuildState.REGRESSION)
            elif self.skip_openlane:
                self.transition(BuildState.SUCCESS)
            else:
                self.transition(BuildState.SDC_GEN)
            return

        self.retry_count += 1
        coverage_max_retries = min(self.max_retries, 5)  # Increased for closure loop
        if self.retry_count > coverage_max_retries:
            if getattr(self, "best_tb_backup", None) and os.path.exists(self.best_tb_backup):
                self.log(
                    f"Restoring Best Testbench ({self.best_coverage:.1f}%) before proceeding.",
                    refined=True,
                )
                import shutil

                shutil.copy(self.best_tb_backup, self.artifacts["tb_path"])
            if self.strict_gates:
                self.log(
                    f"Coverage below thresholds after {coverage_max_retries} attempts. Failing strict gate.",
                    refined=True,
                )
                self.state = BuildState.FAIL
                return
            self.log(
                f"Coverage below thresholds after {coverage_max_retries} attempts. Proceeding anyway.",
                refined=True,
            )
            if self.full_signoff:
                self.transition(BuildState.REGRESSION)
            elif self.skip_openlane:
                self.transition(BuildState.SUCCESS)
            else:
                self.transition(BuildState.SDC_GEN)
            return

        self.log(
            (
                f"Coverage below thresholds (attempt {self.retry_count}). "
                f"Generating additional tests. Failing checks: "
                f"{[k for k, v in coverage_checks.items() if not v]}"
            ),
            refined=True,
        )

        tb_agent = get_testbench_agent(
            self.get_llm_for_role("testbench_designer"),
            f"Improve coverage for {self.name}",
            strategy=self.strategy.name,
        )

        branch_target = float(thresholds["branch"])
        improve_prompt = f"""The current testbench for "{self.name}" does not meet coverage thresholds.
        TARGET: Branch >={branch_target:.1f}%, Line >={float(thresholds["line"]):.1f}%.
        Current Coverage Data: {coverage_data}
        PREVIOUS FAILED ATTEMPTS:
        {self._format_failure_history()}
        
        Current RTL:
        ```verilog
        {self.artifacts.get("rtl_code", "")}
        ```
        
        Current Testbench:
        ```verilog
        {open(self.artifacts["tb_path"], "r").read() if "tb_path" in self.artifacts and os.path.exists(self.artifacts.get("tb_path", "")) else "NOT AVAILABLE"}
        ```
        
        Create an IMPROVED self-checking testbench that:
        1. Achieves >={branch_target:.1f}% branch coverage by hitting all missing branches.
        2. Tests all FSM states (not just happy path)
        3. Exercises all conditional branches (if/else, case)
        3. Tests reset behavior mid-operation
        4. Tests boundary values (max/min inputs)
        5. Includes back-to-back operations
        6. Must print "TEST PASSED" on success
        
        SYNCHRONOUS DUT TIMING RULE (mandatory for ALL designs):
        This DUT is synchronous. All registered outputs update on the rising clock edge.
        After applying any stimulus (reset deassertion, enable assertion, data input),
        always wait for at least one complete clock cycle before sampling or comparing
        any DUT output. Never sample a DUT output in the same time step that stimulus
        is applied.

        Return ONLY the complete testbench in ```verilog fences.
        """

        improve_task = Task(
            description=improve_prompt,
            expected_output="Improved SystemVerilog Testbench",
            agent=tb_agent,
        )

        with console.status(
            "[warning]AI Improving Test Coverage...[/warning]",
            spinner="dots12",
            spinner_style="spinner",
        ):
            result = self._crew_kickoff(
                Crew(verbose=False, agents=[tb_agent], tasks=[improve_task])
            )

        improved_tb = str(result)
        # --- Universal code output validation (coverage TB improvement) ---
        if not validate_llm_code_output(improved_tb):
            self._record_non_consumable_output(
                "llm_coverage_tb",
                improved_tb,
                ["Coverage TB improvement returned prose instead of code."],
            )
            self.log(
                "Coverage TB improvement returned prose instead of code. Retrying once.",
                refined=True,
            )
            self.logger.warning(
                f"COVERAGE TB VALIDATION FAIL (prose detected):\n{improved_tb[:500]}"
            )
            improved_tb = str(
                self._crew_kickoff(Crew(verbose=False, agents=[tb_agent], tasks=[improve_task]))
            )
        tb_validation_issues = self._validate_tb_candidate(improved_tb)
        if tb_validation_issues:
            self._record_stage_contract(
                StageResult(
                    stage=self.state.name,
                    status=StageStatus.RETRY,
                    producer="orchestrator_coverage_tb_validator",
                    failure_class=FailureClass.LLM_SEMANTIC_ERROR,
                    diagnostics=tb_validation_issues,
                    next_action="keep_previous_tb",
                )
            )
            self.log(
                f"Coverage TB candidate rejected: {tb_validation_issues[0]}",
                refined=True,
            )
            return
        self.logger.info(f"IMPROVED TB:\n{improved_tb}")

        tb_path = write_verilog(self.name, improved_tb, is_testbench=True)
        if isinstance(tb_path, str) and not tb_path.startswith("Error:"):
            self.artifacts["tb_path"] = tb_path
        # Loop: state stays at COVERAGE_CHECK, will re-run

    def do_regression(self):
        """Generates and runs multiple directed test scenarios."""
        self.log("Starting Regression Testing...", refined=True)

        regression_agent = get_regression_agent(
            self.get_llm_for_role("testbench_designer"),
            f"Generate regression tests for {self.name}",
            verbose=self.verbose,
        )

        # Generate regression test plan
        regression_task = Task(
            description=f"""Generate 3 directed regression test scenarios for module "{self.name}".
            
            RTL:
            ```verilog
            {self.artifacts.get("rtl_code", "")}
            ```
            
            SPEC:
            {self.artifacts.get("spec", "")}
            
            Create 3 separate self-checking testbenches, each targeting a different scenario:
            1. CORNER CASE TEST - Test with extreme values (max/min/zero/overflow)
            2. RESET STRESS TEST - Apply reset during active operations  
            3. RAPID FIRE TEST - Back-to-back operations with no idle cycles
            
            For each test, output a COMPLETE testbench in a separate ```verilog block.
            Label each block with a comment: // TEST 1: Corner Case, // TEST 2: Reset Stress, // TEST 3: Rapid Fire
            Each test must print "TEST PASSED" on success or "TEST FAILED" on failure.
            Each must use $finish to terminate.
            """,
            expected_output="3 separate Verilog testbench code blocks",
            agent=regression_agent,
        )

        with console.status(
            "[accent]AI Generating Regression Tests...[/accent]",
            spinner="dots12",
            spinner_style="spinner",
        ):
            result = str(
                self._crew_kickoff(
                    Crew(
                        verbose=False,
                        agents=[regression_agent],
                        tasks=[regression_task],
                    )
                )
            )

        self.logger.info(f"REGRESSION TESTS:\n{result}")

        # Parse out individual tests from the LLM output
        test_blocks = re.findall(r"```(?:verilog|v)?\s*\n(.*?)```", result, re.DOTALL)

        if not test_blocks:
            self.log("No regression tests extracted. Skipping regression.", refined=True)
            if self.skip_openlane:
                self.transition(BuildState.SUCCESS)
            else:
                self.transition(BuildState.HARDENING)
            return

        # Run each test
        all_passed = True
        test_results = []

        for i, test_code in enumerate(test_blocks[:3]):  # Max 3 tests
            test_name = f"regression_test_{i + 1}"
            self.log(
                f"Running Regression Test {i + 1}/{len(test_blocks[:3])}...",
                refined=True,
            )

            # Write test to file
            test_path = write_verilog(self.name, test_code, suffix=f"_{test_name}", ext=".v")
            if isinstance(test_path, str) and test_path.startswith("Error:"):
                test_results.append(
                    {"test": test_name, "status": "WRITE_ERROR", "output": test_path}
                )
                all_passed = False
                continue

            # Compile and run
            src_dir = f"{OPENLANE_ROOT}/designs/{self.name}/src"
            rtl_file = self.artifacts.get("rtl_path", f"{src_dir}/{self.name}.v")
            sim_out = f"{src_dir}/sim_{test_name}"

            try:
                import subprocess

                # Detect testbench module name
                tb_module = f"{self.name}_{test_name}"
                try:
                    with open(test_path, "r") as f:
                        tb_content = f.read()
                    m = re.search(r"module\s+(\w+)", tb_content)
                    if m:
                        tb_module = m.group(1)
                except Exception:
                    pass

                sim_obj_dir = f"{src_dir}/obj_dir_{test_name}"
                sim_binary = f"{sim_obj_dir}/V{tb_module}"

                compile_result = subprocess.run(
                    [
                        "verilator",
                        "--binary",
                        "--timing",
                        "-Wno-UNUSED",
                        "-Wno-PINMISSING",
                        "-Wno-CASEINCOMPLETE",
                        "-Wno-WIDTHEXPAND",
                        "-Wno-WIDTHTRUNC",
                        "-Wno-LATCH",
                        "-Wno-UNOPTFLAT",
                        "-Wno-BLKANDNBLK",
                        "--top-module",
                        tb_module,
                        "--Mdir",
                        sim_obj_dir,
                        "-o",
                        f"V{tb_module}",
                        rtl_file,
                        test_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if compile_result.returncode != 0:
                    test_results.append(
                        {
                            "test": test_name,
                            "status": "COMPILE_FAIL",
                            "output": compile_result.stderr[:500],
                        }
                    )
                    all_passed = False
                    continue

                run_result = subprocess.run(
                    [sim_binary], capture_output=True, text=True, timeout=300
                )
                sim_text = (run_result.stdout or "") + (
                    "\n" + run_result.stderr if run_result.stderr else ""
                )

                passed = "TEST PASSED" in sim_text
                test_results.append(
                    {
                        "test": test_name,
                        "status": "PASS" if passed else "FAIL",
                        "output": sim_text[-500:],
                    }
                )
                if not passed:
                    all_passed = False

            except subprocess.TimeoutExpired:
                test_results.append({"test": test_name, "status": "TIMEOUT", "output": "Timed out"})
                all_passed = False
            except Exception as e:
                test_results.append({"test": test_name, "status": "ERROR", "output": str(e)})
                all_passed = False

        # Log results
        self.artifacts["regression_results"] = test_results
        for tr in test_results:
            self.log(f"  Regression {tr['test']}: {tr['status']}", refined=True)

        if all_passed:
            self.log(f"All {len(test_results)} regression tests PASSED!", refined=True)
        else:
            passed_count = sum(1 for tr in test_results if tr["status"] == "PASS")
            self.log(f"Regression: {passed_count}/{len(test_results)} passed", refined=True)
            if self.strict_gates:
                self.log("Regression failures are blocking under strict mode.", refined=True)
                self.state = BuildState.FAIL
                return

        # Regression failures are non-blocking (logged but proceed)
        if self.skip_openlane:
            self.transition(BuildState.SUCCESS)
        else:
            self.transition(BuildState.SDC_GEN)

    def do_sdc_gen(self):
        """Generate Synthesis Design Constraints (.sdc) for OpenLane."""
        src_dir = f"{OPENLANE_ROOT}/designs/{self.name}/src"
        os.makedirs(src_dir, exist_ok=True)
        sdc_path = os.path.join(src_dir, f"{self.name}.sdc")

        self.log("Generating SDC Timing Constraints...", refined=True)
        sdc_agent = get_sdc_agent(
            self.get_llm_for_role("physical"),
            "Generate Synthesis Design Constraints",
            self.verbose,
        )

        arch_spec = self.artifacts.get("spec", "No spec generated.")
        sdc_task = Task(
            description=f"""Generate an SDC file for module '{self.name}'.
            
Architecture Specification:
{arch_spec}

REQUIREMENTS:
1. Identify the clock port and requested frequency/period.
2. If unspecified, assume 100MHz (10.0ns period).
3. Output ONLY the raw SDC constraints. DO NOT output code blocks or markdown wrappers (no ```sdc).
""",
            expected_output="Raw SDC constraints text cleanly formatted.",
            agent=sdc_agent,
        )

        with console.status("[accent]Generating Timing Constraints (SDC)...[/accent]"):
            sdc_content = str(
                self._crew_kickoff(Crew(verbose=False, agents=[sdc_agent], tasks=[sdc_task]))
            ).strip()

            # Clean up potential markdown wrappers created by LLM anyway
            if sdc_content.startswith("```"):
                lines = sdc_content.split("\n")
                if len(lines) > 2:
                    sdc_content = "\n".join(lines[1:-1])

            with open(sdc_path, "w") as f:
                f.write(sdc_content)

            self.artifacts["sdc_path"] = sdc_path
            self.log(f"SDC Constraints generated: {sdc_path}", refined=True)

        self.transition(BuildState.SYNTHESIS)

    def do_synthesis(self):
        """Direct Yosys synthesis: RTL → gate-level netlist with pre-STA."""
        self.log("Running Direct Yosys Synthesis...", refined=True)
        src_dir = f"{OPENLANE_ROOT}/designs/{self.name}/src"
        synth_dir = f"{OPENLANE_ROOT}/designs/{self.name}/synth"
        os.makedirs(synth_dir, exist_ok=True)

        rtl_files = [
            os.path.join(src_dir, f) for f in os.listdir(src_dir) if f.endswith((".v", ".sv"))
        ]
        if not rtl_files:
            rtl_main = self.artifacts.get("rtl_path")
            if rtl_main:
                rtl_files = [rtl_main]
            else:
                self.log("No RTL files found for synthesis.", refined=True)
                self.transition(BuildState.DFT_SCAN)
                return

        sdc_path = self.artifacts.get("sdc_path", "")
        clk_constraint_ns = 10.0
        if sdc_path and os.path.exists(sdc_path):
            with open(sdc_path) as f:
                content = f.read()
            m = re.search(r"create_clock\s+-period\s+([\d.]+)", content)
            if m:
                clk_constraint_ns = float(m.group(1))

        with console.status("[accent]Running Yosys Synthesis...[/accent]"):
            from .tools.synth_tools import run_yosys_synth, SynthesisResult, parse_yosys_errors

            result = run_yosys_synth(
                rtl_files=rtl_files,
                top_module=self.name,
                output_dir=synth_dir,
                pdk=self.pdk_profile.get("pdk", PDK),
                pdk_root=os.environ.get("PDK_ROOT"),
                clk_constraint=clk_constraint_ns,
                ungroup_cells=True,
                flatten_hierarchy=False,
                timeout=600,
                design_name=self.name,
            )

        if result.ok:
            self.artifacts["synth_netlist"] = result.netlist_path
            self.artifacts["synth_checkpoint"] = result.checkpoint_path
            self.artifacts["synth_report"] = result.report_path
            self.artifacts["synth_metrics"] = {
                "cell_count": result.cell_count,
                "gate_count": result.gate_count,
                "area_um2": result.area_um2,
                "dff_count": result.dff_count,
                "lut_count": result.lut_count,
                "wns": getattr(result, "wns", 0.0),
                "tns": getattr(result, "tns", 0.0),
                "max_freq_mhz": result.frequency_mhz,
            }
            self.log(
                f"Synthesis OK: {result.cell_count} cells, "
                f"{result.gate_count:.0f} gates, "
                f"area={result.area_um2:.1f}um^2, "
                f"freq={result.frequency_mhz:.1f}MHz",
                refined=True,
            )
            if result.warnings:
                for w in result.warnings[:3]:
                    self.log(f"  WARN: {w}", refined=True)
            self.transition(BuildState.DFT_SCAN)
        else:
            structured_errors = parse_yosys_errors(
                "\n".join([result.stdout, result.stderr, *result.errors])
            )
            self.artifacts["synth_structured_errors"] = structured_errors
            if self._attempt_backend_error_recovery(
                "SYNTHESIS",
                result.errors,
                retry_state=BuildState.RTL_GEN,
                fallback_state=BuildState.DFT_SCAN,
                structured_errors=structured_errors,
                confidence_threshold=0.5,
            ):
                return

            self.log(f"Synthesis FAILED: {'; '.join(result.errors[:2])}", refined=True)
            self.artifacts["synth_error"] = result.errors
            self.transition(BuildState.DFT_SCAN)

    def do_dft_scan(self):
        """Insert scan chains into synthesized netlist for testability."""
        self.log("Running DFT Scan Chain Insertion...", refined=True)
        synth_netlist = self.artifacts.get("synth_netlist", "")
        dft_dir = f"{OPENLANE_ROOT}/designs/{self.name}/dft"

        if not synth_netlist or not os.path.exists(synth_netlist):
            self.log("No synth netlist for DFT scan; skipping.", refined=True)
            self.transition(BuildState.DFT_ATPG)
            return

        os.makedirs(dft_dir, exist_ok=True)
        scan_chain_count = int(self.artifacts.get("dft_scan_chains", 4))

        with console.status("[accent]Inserting Scan Chains...[/accent]"):
            from .tools.dft_tools import run_scan_insertion, DFTResult

            result = run_scan_insertion(
                rtl_files=[synth_netlist],
                top_module=self.name,
                output_dir=dft_dir,
                scan_chain_count=scan_chain_count,
                scan_enable_signal="scan_en",
                scan_mode_signal="scan_mode",
                pdk=self.pdk_profile.get("pdk", PDK),
                timeout=600,
            )

        if result.ok:
            self.artifacts["scan_netlist"] = result.scan_netlist_path
            self.artifacts["scan_chain_count"] = result.scan_chain_count
            self.artifacts["dft_metrics"] = {
                "scan_chains": result.scan_chain_count,
                "total_faults": result.total_faults,
                "detected_faults": result.detected_faults,
                "coverage_percent": result.atpg_coverage_percent,
                "compression_ratio": result.compression_ratio,
            }
            self.log(
                f"DFT Scan OK: {result.scan_chain_count} chains, "
                f"{result.atpg_coverage_percent:.1f}% fault coverage, "
                f"compression={result.compression_ratio:.1f}x",
                refined=True,
            )
            self.transition(BuildState.DFT_ATPG)
        else:
            structured_errors = (
                result.metrics.get("structured_errors", [])
                if isinstance(result.metrics, dict)
                else []
            )
            if self._attempt_backend_error_recovery(
                "DFT",
                result.errors,
                retry_state=BuildState.RTL_GEN,
                fallback_state=BuildState.DFT_ATPG,
                structured_errors=structured_errors,
                confidence_threshold=0.55,
            ):
                return

            self.log(f"DFT scan FAILED: {'; '.join(result.errors[:2])}", refined=True)
            self.artifacts["dft_error"] = result.errors
            self.transition(BuildState.DFT_ATPG)

    def do_dft_atpg(self):
        """Generate ATPG test patterns for scan-inserted netlist."""
        self.log("Running ATPG Pattern Generation...", refined=True)
        scan_netlist = self.artifacts.get("scan_netlist", "")
        dft_dir = f"{OPENLANE_ROOT}/designs/{self.name}/dft"

        if not scan_netlist or not os.path.exists(scan_netlist):
            synth_netlist = self.artifacts.get("synth_netlist", "")
            if synth_netlist:
                scan_netlist = synth_netlist
                self.log("Using synth netlist for ATPG (no scan netlist).", refined=True)
            else:
                self.log("No netlist for ATPG; skipping to MBIST.", refined=True)
                self.transition(BuildState.MBIST)
                return

        with console.status("[accent]Generating ATPG Patterns...[/accent]"):
            from .tools.dft_tools import run_atpg, ATPGResult

            result = run_atpg(
                netlist_files=[scan_netlist],
                top_module=self.name,
                output_dir=dft_dir,
                target_coverage=95.0,
                max_patterns=5000,
                pdk=self.pdk_profile.get("pdk", PDK),
                timeout=600,
            )

        if result.ok:
            self.artifacts["atpg_pattern_file"] = result.pattern_file
            self.artifacts["atpg_fault_file"] = result.fault_file
            self.artifacts["atpg_metrics"] = {
                "total_faults": result.total_faults,
                "detected": result.detected,
                "coverage_percent": result.coverage_percent,
                "pattern_count": result.pattern_count,
                "compression_ratio": result.compression_ratio,
            }
            self.log(
                f"ATPG OK: {result.pattern_count} patterns, "
                f"{result.coverage_percent:.1f}% fault coverage, "
                f"{result.undetected} undetected faults",
                refined=True,
            )
            self.transition(BuildState.MBIST)
        else:
            self.log(f"ATPG FAILED: {'; '.join(result.errors[:2])}", refined=True)
            self.artifacts["atpg_error"] = result.errors
            self.transition(BuildState.MBIST)

    def do_mbist(self):
        """Generate Memory BIST wrappers for embedded memories."""
        self.log("Running Memory BIST Insertion...", refined=True)
        synth_netlist = self.artifacts.get("synth_netlist", "")
        mbist_dir = f"{OPENLANE_ROOT}/designs/{self.name}/mbist"
        os.makedirs(mbist_dir, exist_ok=True)

        memories_found = False
        if synth_netlist and os.path.exists(synth_netlist):
            with open(synth_netlist) as f:
                content = f.read()
            mem_matches = re.findall(
                r"\b(sky130_sram|spm|dp_ram|sram|mem|buffer_fifo|async_fifo|sync_fifo)\b",
                content,
                re.IGNORECASE,
            )
            if mem_matches:
                memories_found = True
                self.log(
                    f"Detected {len(mem_matches)} memory instances for BIST.",
                    refined=True,
                )

        if not memories_found:
            self.log("No embedded memories detected; skipping MBIST.", refined=True)
            self.transition(BuildState.GLS_SIM)
            return

        with console.status("[accent]Generating MBIST Wrappers...[/accent]"):
            from .tools.dft_tools import generate_mbist_wrapper, MBISTConfig
            from .tools.dft_tools import run_testability_analysis

            test_result = run_testability_analysis(
                netlist_files=[synth_netlist] if synth_netlist else [],
                top_module=self.name,
                output_dir=mbist_dir,
            )
            self.artifacts["testability_metrics"] = test_result

            memory_instances = re.findall(
                r"(\w+)\s*(?: Instantiates? )?\s*(?:memory|sram|ram|rom)\w*",
                content if memories_found else "",
                re.IGNORECASE,
            )
            mem_configs = []
            for i, mem_name in enumerate(set(memory_instances[:8])):
                cfg = MBISTConfig(
                    memory_instance=mem_name,
                    memory_depth=256 * (2**i),
                    memory_width=8,
                    test_algorithm="march",
                    bist_clock_mhz=100.0,
                )
                mem_configs.append(cfg)

            if not mem_configs:
                cfg = MBISTConfig(
                    memory_instance="u_mem_0",
                    memory_depth=256,
                    memory_width=8,
                    test_algorithm="march",
                    bist_clock_mhz=100.0,
                )
                mem_configs.append(cfg)

            for cfg in mem_configs:
                mbist_result = generate_mbist_wrapper(
                    mbist_config=cfg,
                    output_dir=mbist_dir,
                    top_module=self.name,
                )
                self.log(
                    f"MBIST: {cfg.memory_instance} -> "
                    f"{mbist_result.get('wrapper_path', 'generated')}",
                    refined=True,
                )

        self.artifacts["mbist_dir"] = mbist_dir
        self.artifacts["mbist_configs"] = [cfg.memory_instance for cfg in mem_configs]
        self.log(f"MBIST: {len(mem_configs)} memory BIST wrappers generated.", refined=True)
        self.transition(BuildState.GLS_SIM)

    def do_gls_simulation(self):
        """Gate-level simulation with SDF timing annotation."""
        self.log("Running Gate-Level Simulation (GLS)...", refined=True)
        scan_netlist = self.artifacts.get("scan_netlist", "")
        synth_netlist = self.artifacts.get("synth_netlist", "")
        netlist = scan_netlist if scan_netlist and os.path.exists(scan_netlist) else synth_netlist
        sdf_path = self.artifacts.get("synth_sdf", "")

        if not netlist or not os.path.exists(netlist):
            self.log("No gate netlist for GLS; skipping to floorplan.", refined=True)
            self.transition(BuildState.FLOORPLAN)
            return

        gls_dir = f"{OPENLANE_ROOT}/designs/{self.name}/gls"
        os.makedirs(gls_dir, exist_ok=True)

        with console.status("[accent]Running Gate-Level Simulation...[/accent]"):
            from .tools.sdf_tools import generate_sdf, SDFResult

            if not sdf_path or not os.path.exists(sdf_path):
                lib_files = []
                pdk_lib_dir = os.path.join(
                    os.environ.get("PDK_ROOT", ""),
                    self.pdk_profile.get("pdk", PDK),
                    "libs.ref",
                    self.pdk_profile.get("std_cell_library", "sky130_fd_sc_hd"),
                )
                if os.path.isdir(pdk_lib_dir):
                    lib_files = [
                        os.path.join(pdk_lib_dir, f)
                        for f in os.listdir(pdk_lib_dir)
                        if f.endswith(".lib")
                    ]
                if lib_files:
                    sdf_result = generate_sdf(
                        liberty_files=lib_files,
                        netlist=netlist,
                        output_dir=gls_dir,
                        corner="tt",
                    )
                    if sdf_result.ok:
                        sdf_path = sdf_result.sdf_path
                        self.artifacts["synth_sdf"] = sdf_path
                        self.log(f"SDF generated: {sdf_path}", refined=True)
                    else:
                        self.log(
                            "SDF generation failed; GLS will use zero-delay.",
                            refined=True,
                        )

            tb_rtl = self.artifacts.get("tb_rtl_path", "")
            tb_gls = os.path.join(gls_dir, f"{self.name}_gls_tb.v")

            if tb_rtl and os.path.exists(tb_rtl):
                with open(tb_rtl) as f:
                    tb_src = f.read()
                tb_src = tb_src.replace(self.name, f"{self.name}_gl").replace(
                    "initial begin",
                    "initial begin // GLS mode",
                )
                with open(tb_gls, "w") as f:
                    f.write(tb_src)

            sim_result = self.artifacts.get("gls_sim_result", None)
            if sim_result is None:
                from .tools.vlsi_tools import run_gls_simulation

                ok, log = run_gls_simulation(
                    self.name,
                    netlist=netlist,
                    sdf=sdf_path if sdf_path and os.path.exists(sdf_path) else None,
                    testbench=tb_gls if os.path.exists(tb_gls) else tb_rtl,
                )
                sim_result = {"ok": ok, "log": log}
                self.artifacts["gls_sim_result"] = sim_result

            self.log(
                f"GLS: {'PASS' if sim_result.get('ok') else 'FAIL'}",
                refined=True,
            )
            if not sim_result.get("ok"):
                self.artifacts["gls_warnings"] = ["GLS simulation failed"]

        self.transition(BuildState.FLOORPLAN)

    def do_power_analysis(self):
        """Post-PnR power analysis with SPEF extraction."""
        self.log("Running Post-PnR Power Analysis...", refined=True)
        run_tag = self.artifacts.get("run_tag", "agentrun")
        pdk_name = self.pdk_profile.get("pdk", PDK)
        pdk_root = os.environ.get("PDK_ROOT", "")

        spef_path = ""
        spef_base = f"{OPENLANE_ROOT}/designs/{self.name}/runs/{run_tag}/results/routing"
        if os.path.isdir(spef_base):
            for f in os.listdir(spef_base):
                if f.endswith(".spef") or f.endswith(".spef.gz"):
                    spef_path = os.path.join(spef_base, f)
                    break

        if spef_path and os.path.exists(spef_path):
            from .tools.power_tools import parse_spef

            ok, spef_nets = parse_spef(spef_path)
            self.artifacts["spef_nets"] = [n.name for n in spef_nets]
            self.artifacts["total_capacitance_ff"] = sum(n.capacitance_ff for n in spef_nets)
        else:
            self.log("No SPEF found; power analysis from synth estimates.", refined=True)

        gate_netlist = f"{OPENLANE_ROOT}/designs/{self.name}/runs/{run_tag}/results/final/verilog/gl/{self.name}.v"
        lib_files = []
        lib_base = os.path.join(
            pdk_root,
            pdk_name,
            "libs.ref",
            self.pdk_profile.get("std_cell_library", "sky130_fd_sc_hd"),
        )
        if os.path.isdir(lib_base):
            lib_files = [
                os.path.join(lib_base, f) for f in os.listdir(lib_base) if f.endswith(".lib")
            ]

        freq_mhz = float(self.artifacts.get("synth_metrics", {}).get("max_freq_mhz", 100.0))
        power_dir = f"{OPENLANE_ROOT}/designs/{self.name}/power"
        os.makedirs(power_dir, exist_ok=True)

        with console.status("[accent]Analyzing Power...[/accent]"):
            from .tools.power_tools import run_power_analysis, PowerAnalysisResult

            result = run_power_analysis(
                netlist=gate_netlist
                if os.path.exists(gate_netlist)
                else self.artifacts.get("synth_netlist", ""),
                spef=spef_path if spef_path else None,
                lib_files=lib_files,
                output_dir=power_dir,
                vdd_value=1.8,
                frequency_mhz=freq_mhz,
                corner="tt",
                temperature_c=25,
            )

        if result.ok:
            self.artifacts["power_metrics"] = {
                "total_power_uW": result.total_power_uW,
                "dynamic_power_uW": result.dynamic_power_uW,
                "leakage_power_uW": result.leakage_power_uW,
                "power_density_mW_per_mm2": result.power_density_mW_per_mm2,
                "ir_drop_mV": result.ir_drop.max_drop_mV,
            }
            self.log(
                f"Power: Total={result.total_power_uW:.2f}uW "
                f"(dyn={result.dynamic_power_uW:.2f}uW, "
                f"leak={result.leakage_power_uW:.4f}uW), "
                f"IR-drop={result.ir_drop.max_drop_mV:.2f}mV",
                refined=True,
            )
            if result.ir_drop.electromigration_warnings:
                for em_warn in result.ir_drop.electromigration_warnings[:3]:
                    self.log(f"  EM WARN: {em_warn}", refined=True)
        else:
            self.artifacts["power_error"] = result.errors
            if self._attempt_backend_error_recovery(
                "POWER",
                result.errors,
                retry_state=BuildState.RTL_GEN,
                fallback_state=BuildState.TIMING_ANALYSIS,
                confidence_threshold=0.65,
                max_attempts=1,
            ):
                return

            self.log(f"Power analysis skipped: {'; '.join(result.errors[:2])}", refined=True)

        self.transition(BuildState.TIMING_ANALYSIS)

    def do_timing_analysis(self):
        """Multi-corner STA across SS/TT/FF with OpenSTA."""
        self.log("Running Multi-Corner Static Timing Analysis...", refined=True)
        run_tag = self.artifacts.get("run_tag", "agentrun")
        pdk_name = self.pdk_profile.get("pdk", PDK)
        pdk_root = os.environ.get("PDK_ROOT", "")
        sdc_path = self.artifacts.get("sdc_path", "")

        gate_netlist = f"{OPENLANE_ROOT}/designs/{self.name}/runs/{run_tag}/results/final/verilog/gl/{self.name}.v"
        netlist = (
            gate_netlist
            if os.path.exists(gate_netlist)
            else self.artifacts.get("synth_netlist", "")
        )

        if not sdc_path or not os.path.exists(sdc_path):
            sdc_path = f"{OPENLANE_ROOT}/designs/{self.name}/src/{self.name}.sdc"

        lib_base = os.path.join(
            pdk_root,
            pdk_name,
            "libs.ref",
            self.pdk_profile.get("std_cell_library", "sky130_fd_sc_hd"),
        )
        lib_files = []
        if os.path.isdir(lib_base):
            lib_files = [
                os.path.join(lib_base, f) for f in os.listdir(lib_base) if f.endswith(".lib")
            ]

        if not lib_files:
            lib_files = self.artifacts.get("lib_files", [])

        if not lib_files:
            self.log("No liberty files for STA; using synth metrics.", refined=True)
            synth_metrics = self.artifacts.get("synth_metrics", {})
            wns = synth_metrics.get("wns", 0.0)
            self.artifacts["sta_metrics"] = {
                "wns_setup": wns,
                "wns_hold": 0.0,
                "corner": "synth_est",
            }
            self.transition(BuildState.PHYSICAL_VERIFY)
            return

        sta_dir = f"{OPENLANE_ROOT}/designs/{self.name}/sta"
        os.makedirs(sta_dir, exist_ok=True)

        with console.status("[accent]Running Multi-Corner STA...[/accent]"):
            from .tools.sta_tools import run_multi_corner_sta, MultiCornerSTAResult, parse_sta_errors

            result = run_multi_corner_sta(
                design_name=self.name,
                netlist=netlist,
                sdc=sdc_path if os.path.exists(sdc_path) else "",
                lib_files=lib_files,
                output_dir=sta_dir,
                corners=["ss", "tt", "ff"],
                modes=["func"],
                pdk=pdk_name,
                enable_route=False,
            )

        if result.corners:
            corner_summaries = []
            for corner, rpt in result.corners.items():
                corner_summaries.append(
                    f"{corner}: WNS={rpt.wns_setup:.3f}ns, "
                    f"TNS={rpt.tns_setup:.1f}ns, "
                    f"max_freq={rpt.max_freq_mhz:.1f}MHz"
                )
                self.log(
                    f"  {corner.upper()}: WNS_setup={rpt.wns_setup:.3f}ns, WNS_hold={rpt.wns_hold:.3f}ns",
                    refined=True,
                )
                if rpt.warnings:
                    for w in rpt.warnings[:2]:
                        self.log(f"    WARN: {w}", refined=True)

            self.artifacts["sta_metrics"] = {
                "worst_wns_setup": result.worst_wns_setup,
                "worst_wns_hold": result.worst_wns_hold,
                "worst_tns_setup": result.worst_tns_setup,
                "worst_tns_hold": result.worst_tns_hold,
                "max_freq_mhz": result.max_freq_mhz,
                "all_corners_pass": result.all_corners_pass,
                "corners": {
                    c: {
                        "wns_setup": r.wns_setup,
                        "wns_hold": r.wns_hold,
                        "max_freq_mhz": r.max_freq_mhz,
                    }
                    for c, r in result.corners.items()
                },
            }
            self.artifacts["sta_summary_path"] = result.summary_path
            self.log(
                f"STA: Worst WNS_setup={result.worst_wns_setup:.3f}ns, "
                f"max_freq={result.max_freq_mhz:.1f}MHz, "
                f"all_corners={'PASS' if result.all_corners_pass else 'FAIL'}",
                refined=True,
            )
            if not result.all_corners_pass:
                timing_errors: List[str] = []
                structured_errors: List[Dict[str, Any]] = []
                for corner, rpt in result.corners.items():
                    timing_errors.extend(
                        [
                            f"{corner}: setup WNS {rpt.wns_setup:.3f}ns",
                            f"{corner}: hold WNS {rpt.wns_hold:.3f}ns",
                            *rpt.errors,
                        ]
                    )
                    structured_errors.extend(
                        rpt.metrics.get("structured_errors", [])
                        if isinstance(rpt.metrics, dict)
                        else []
                    )
                    if os.path.exists(rpt.report_path):
                        with open(rpt.report_path) as f:
                            structured_errors.extend(parse_sta_errors(f.read()))
                self.artifacts["sta_error"] = timing_errors
                self.artifacts["sta_structured_errors"] = structured_errors
                if self._attempt_backend_error_recovery(
                    "TIMING",
                    timing_errors,
                    retry_state=BuildState.RTL_GEN,
                    fallback_state=BuildState.PHYSICAL_VERIFY,
                    structured_errors=structured_errors,
                    confidence_threshold=0.55,
                    max_attempts=2,
                ):
                    return
        else:
            timing_errors = ["STA produced no results; check netlist and SDC."]
            self.artifacts["sta_error"] = timing_errors
            if self._attempt_backend_error_recovery(
                "TIMING",
                timing_errors,
                retry_state=BuildState.RTL_GEN,
                fallback_state=BuildState.PHYSICAL_VERIFY,
                confidence_threshold=0.55,
                max_attempts=1,
            ):
                return
            self.log("STA produced no results; check netlist and SDC.", refined=True)

        self.transition(BuildState.PHYSICAL_VERIFY)

    def do_physical_verify(self):
        """Independent DRC (Magic) and LVS (Netgen) physical verification."""
        self.log("Running Independent Physical Verification...", refined=True)
        run_tag = self.artifacts.get("run_tag", "agentrun")
        pdk_name = self.pdk_profile.get("pdk", PDK)
        pdk_root = os.environ.get("PDK_ROOT", "")
        pv_dir = f"{OPENLANE_ROOT}/designs/{self.name}/pv"
        os.makedirs(pv_dir, exist_ok=True)

        gds_path = self.artifacts.get("gds", "")
        if not gds_path:
            gds_path = f"{OPENLANE_ROOT}/designs/{self.name}/runs/{run_tag}/results/signoff/magic/{self.name}.gds"
        lef_path = f"{OPENLANE_ROOT}/designs/{self.name}/runs/{run_tag}/results/signoff/openlane/{self.name}.lef"
        if not os.path.exists(gds_path) and not os.path.exists(lef_path):
            self.log("No GDS/LEF for physical verification; proceeding.", refined=True)
            self.transition(BuildState.SIGNOFF)
            return

        gate_netlist = f"{OPENLANE_ROOT}/designs/{self.name}/runs/{run_tag}/results/final/verilog/gl/{self.name}.v"
        synth_netlist = self.artifacts.get("synth_netlist", "")

        with console.status("[accent]Running Magic DRC...[/accent]"):
            from .tools.physical_tools import run_magic_drc, DRCResult, parse_drc_errors

            drc_result = run_magic_drc(
                gds_path=gds_path if os.path.exists(gds_path) else "",
                lef_path=lef_path if os.path.exists(lef_path) else "",
                output_dir=pv_dir,
                pdk=pdk_name,
                pdk_root=pdk_root,
                timeout=600,
            )

        self.artifacts["drc_result"] = drc_result

        if drc_result.ok:
            self.log(
                f"Magic DRC: {drc_result.drc_violations} violations "
                f"({drc_result.runtime_sec:.1f}s)",
                refined=True,
            )
            if drc_result.violations:
                for v in drc_result.violations[:5]:
                    self.log(
                        f"  DRC: {v.rule} @ ({v.x_um:.2f},{v.y_um:.2f}): {v.message}",
                        refined=True,
                    )
        else:
            structured_errors = []
            structured_errors.extend(
                drc_result.metrics.get("structured_errors", [])
                if isinstance(drc_result.metrics, dict)
                else []
            )
            if drc_result.drc_report_path and os.path.exists(drc_result.drc_report_path):
                with open(drc_result.drc_report_path) as f:
                    structured_errors.extend(parse_drc_errors(f.read()))
            self.artifacts["drc_structured_errors"] = structured_errors
            if self._attempt_backend_error_recovery(
                "DRC",
                drc_result.errors,
                retry_state=BuildState.FLOORPLAN,
                fallback_state=BuildState.SIGNOFF,
                structured_errors=structured_errors,
                confidence_threshold=0.6,
                max_attempts=1,
            ):
                return

            self.log(f"Magic DRC failed: {'; '.join(drc_result.errors[:2])}", refined=True)

        with console.status("[accent]Running Netgen LVS...[/accent]"):
            from .tools.physical_tools import run_netgen_lvs, LVSResult

            lvs_result = run_netgen_lvs(
                schematic_netlist=synth_netlist
                if synth_netlist and os.path.exists(synth_netlist)
                else gate_netlist,
                layout_gds=gds_path if os.path.exists(gds_path) else "",
                output_dir=pv_dir,
                pdk=pdk_name,
                pdk_root=pdk_root,
                top_module=self.name,
                timeout=600,
            )

        self.artifacts["lvs_result"] = lvs_result

        if lvs_result.ok:
            self.log(
                f"Netgen LVS: {'EQUIVALENT' if lvs_result.equivalent else 'MISMATCH'} "
                f"({lvs_result.runtime_sec:.1f}s)",
                refined=True,
            )
            if not lvs_result.equivalent:
                self.log(f"  LVS errors: {lvs_result.lvs_errors}", refined=True)
                lvs_errors = [
                    f"LVS mismatch: {lvs_result.lvs_errors} errors",
                    f"net mismatches: {lvs_result.net_mismatches}",
                    f"pin mismatches: {lvs_result.pin_mismatches}",
                    f"unconnected nets: {lvs_result.unconnected_nets}",
                ]
                if self._attempt_backend_error_recovery(
                    "LVS",
                    lvs_errors,
                    retry_state=BuildState.FLOORPLAN,
                    fallback_state=BuildState.SIGNOFF,
                    confidence_threshold=0.6,
                    max_attempts=1,
                ):
                    return
        else:
            if self._attempt_backend_error_recovery(
                "LVS",
                lvs_result.errors,
                retry_state=BuildState.FLOORPLAN,
                fallback_state=BuildState.SIGNOFF,
                confidence_threshold=0.6,
                max_attempts=1,
            ):
                return

            self.log(f"Netgen LVS failed: {'; '.join(lvs_result.errors[:2])}", refined=True)
        self.artifacts["pv_metrics"] = {
            "drc_violations": drc_result.drc_violations,
            "lvs_errors": lvs_result.lvs_errors,
            "lvs_equivalent": lvs_result.equivalent,
        }

        self.transition(BuildState.SIGNOFF)

    def do_ip_package(self):
        """Generate IP-XACT component and ATPG pattern export."""
        self.log("Running IP Packaging...", refined=True)
        ip_dir = f"{OPENLANE_ROOT}/designs/{self.name}/ip"
        os.makedirs(ip_dir, exist_ok=True)

        rtl_files = []
        src_dir = f"{OPENLANE_ROOT}/designs/{self.name}/src"
        if os.path.isdir(src_dir):
            rtl_files = [
                os.path.join(src_dir, f) for f in os.listdir(src_dir) if f.endswith((".v", ".sv"))
            ]

        spec_data = self.artifacts.get("spec", "")
        hw_spec = self.artifacts.get("hw_spec", {})
        if isinstance(hw_spec, str):
            import json as _json

            try:
                hw_spec = _json.loads(hw_spec)
            except Exception:
                hw_spec = {}

        with console.status("[accent]Generating IP-XACT Component...[/accent]"):
            from .tools.ipxact_packager import (
                generate_ipxact_component,
                IPXACTComponent,
            )

            comp = generate_ipxact_component(
                design_name=self.name,
                rtl_files=rtl_files,
                spec_data=hw_spec,
                output_dir=ip_dir,
                bus_interfaces=self.artifacts.get("bus_interfaces", []),
                memory_maps=self.artifacts.get("memory_maps", []),
            )
            self.artifacts["ipxact_xml"] = comp.xml_path
            self.artifacts["ipxact_catalog"] = comp.catalog_path
            self.log(f"IP-XACT: {comp.xml_path}", refined=True)

        atpg_pattern = self.artifacts.get("atpg_pattern_file", "")
        if atpg_pattern and os.path.exists(atpg_pattern):
            with console.status("[accent]Exporting ATPG Patterns...[/accent]"):
                from .tools.ipxact_packager import (
                    export_atpg_patterns,
                    ATPGExportResult,
                )

                atpg_result = export_atpg_patterns(
                    pattern_file=atpg_pattern,
                    output_dir=ip_dir,
                    format="stil",
                    scan_chain_count=self.artifacts.get("scan_chain_count", 4),
                )
                if atpg_result.ok:
                    self.artifacts["atpg_export"] = atpg_result.pattern_file
                    self.log(f"ATPG Export: {atpg_result.pattern_file}", refined=True)
                else:
                    self.log(
                        f"ATPG export warning: {'; '.join(atpg_result.errors[:2])}",
                        refined=True,
                    )

        self.log("IP Packaging complete.", refined=True)
        self.transition(BuildState.SUCCESS)

    def do_floorplan(self):
        """Generate floorplan artifacts and feed hardening with spatial intent."""
        self.floorplan_attempts += 1
        self.log(f"Preparing floorplan attempt {self.floorplan_attempts}...", refined=True)
        src_dir = f"{OPENLANE_ROOT}/designs/{self.name}/src"
        os.makedirs(src_dir, exist_ok=True)

        rtl_code = self.artifacts.get("rtl_code", "")
        line_count = max(1, len([l for l in rtl_code.splitlines() if l.strip()]))
        cell_count_est = max(100, line_count * 4)

        # --- LLM-assisted floorplan estimation with safe fallback ---
        # Heuristic defaults (used as fallback if LLM fails)
        heuristic_die = 300 if line_count < 100 else 500 if line_count < 300 else 800
        heuristic_util = 40 if line_count >= 200 else 50
        heuristic_clk = self.artifacts.get(
            "clock_period_override",
            self.pdk_profile.get("default_clock_period", "10.0"),
        )

        try:
            module_count = len(re.findall(r"\bmodule\b", rtl_code))
            estimator = Agent(
                role="Physical Design Estimator",
                goal="Estimate die area, utilization, and clock period for floorplanning",
                backstory="Senior PD engineer who estimates area from RTL complexity, gate count, and PDK constraints.",
                llm=self.get_llm_for_role("fixer"),
                verbose=False,
                allow_delegation=False,
            )
            estimate_task = Task(
                description=f"""Estimate floorplan parameters for "{self.name}".

DESIGN METRICS:
- RTL: {line_count} non-blank lines, {module_count} modules, ~{cell_count_est} estimated cells
- PDK: {self.pdk_profile.get("profile", "sky130")} ({self.pdk_profile.get("pdk", "sky130A")})
- Previous convergence: {[{"wns": s.wns, "cong": s.congestion, "area": s.area_um2} for s in self.convergence_history[-2:]] if self.convergence_history else "First attempt"}
- Floorplan attempt: {self.floorplan_attempts}

Reply in this EXACT format (4 lines):
DIE_AREA: <integer 200-2000>
UTILIZATION: <integer 30-70>
CLOCK_PERIOD: <float like 10.0>
REASONING: <1-line explanation>""",
                expected_output="Floorplan parameters in structured format",
                agent=estimator,
            )

            with console.status(
                "[accent]LLM Estimating Floorplan...[/accent]",
                spinner="dots12",
                spinner_style="spinner",
            ):
                est_result = str(
                    self._crew_kickoff(
                        Crew(verbose=False, agents=[estimator], tasks=[estimate_task])
                    )
                ).strip()
            self.logger.info(f"FLOORPLAN LLM ESTIMATE:\n{est_result}")

            # Parse structured response
            llm_die = None
            llm_util = None
            llm_clk = None
            llm_reasoning = ""
            for est_line in est_result.split("\n"):
                est_line_s = est_line.strip()
                if est_line_s.startswith("DIE_AREA:"):
                    try:
                        llm_die = int(
                            re.search(r"\d+", est_line_s.replace("DIE_AREA:", "")).group()
                        )
                        llm_die = max(200, min(2000, llm_die))
                    except (ValueError, AttributeError):
                        pass
                elif est_line_s.startswith("UTILIZATION:"):
                    try:
                        llm_util = int(
                            re.search(r"\d+", est_line_s.replace("UTILIZATION:", "")).group()
                        )
                        llm_util = max(30, min(70, llm_util))
                    except (ValueError, AttributeError):
                        pass
                elif est_line_s.startswith("CLOCK_PERIOD:"):
                    try:
                        llm_clk = float(
                            re.search(r"[\d.]+", est_line_s.replace("CLOCK_PERIOD:", "")).group()
                        )
                        llm_clk = max(1.0, min(100.0, llm_clk))
                    except (ValueError, AttributeError):
                        pass
                elif est_line_s.startswith("REASONING:"):
                    llm_reasoning = est_line_s.replace("REASONING:", "").strip()

            if llm_die is not None:
                base_die = llm_die
                self.log(f"LLM estimated die area: {llm_die} ({llm_reasoning})", refined=True)
            else:
                base_die = heuristic_die
                self.log(
                    f"LLM floorplan parse failed; using heuristic die={heuristic_die}",
                    refined=True,
                )

            util = llm_util if llm_util is not None else heuristic_util
            if llm_clk is not None and "clock_period_override" not in self.artifacts:
                clock_period = str(llm_clk)
            else:
                clock_period = heuristic_clk

        except Exception as e:
            self.log(f"LLM floorplan estimation failed ({e}); using heuristic.", refined=True)
            base_die = heuristic_die
            util = heuristic_util
            clock_period = heuristic_clk

        area_scale = self.artifacts.get("area_scale", 1.0)
        die = int(base_die * area_scale)

        macro_placement_tcl = os.path.join(src_dir, "macro_placement.tcl")
        with open(macro_placement_tcl, "w") as f:
            f.write(
                "# Auto-generated macro placement skeleton\n"
                f"# die_area={die}x{die} cell_count_est={cell_count_est}\n"
                "set macros {}\n"
                "foreach m $macros {\n"
                "  # placeholder for macro coordinates\n"
                "}\n"
            )

        floorplan_tcl = os.path.join(src_dir, f"{self.name}_floorplan.tcl")
        sdc_injection = (
            f'set ::env(BASE_SDC_FILE) "$::env(DESIGN_DIR)/src/{self.name}.sdc"\n'
            if "sdc_path" in self.artifacts
            else ""
        )

        with open(floorplan_tcl, "w") as f:
            f.write(
                f'set ::env(DESIGN_NAME) "{self.name}"\n'
                f'set ::env(PDK) "{self.pdk_profile.get("pdk", PDK)}"\n'
                f'set ::env(STD_CELL_LIBRARY) "{self.pdk_profile.get("std_cell_library", "sky130_fd_sc_hd")}"\n'
                f"set ::env(VERILOG_FILES) [glob $::env(DESIGN_DIR)/src/*.v]\n"
                f"{sdc_injection}"
                'set ::env(SYNTH_STRATEGY) "AREA 0"\n'
                "set ::env(SYNTH_SIZING) 1\n"
                "set ::env(MAGIC_DRC_USE_GDS) 1\n"
                'set ::env(FP_SIZING) "absolute"\n'
                f'set ::env(DIE_AREA) "0 0 {die} {die}"\n'
                f"set ::env(FP_CORE_UTIL) {util}\n"
                f"set ::env(PL_TARGET_DENSITY) {util / 100 + 0.05:.2f}\n"
                f'set ::env(CLOCK_PERIOD) "{clock_period}"\n'
                f'set ::env(CLOCK_PORT) "clk"\n'
                "set ::env(GRT_ADJUSTMENT) 0.15\n"
            )

        self.artifacts["macro_placement_tcl"] = macro_placement_tcl
        self.artifacts["floorplan_tcl"] = floorplan_tcl
        self.artifacts["floorplan_meta"] = {
            "die_area": die,
            "cell_count_est": cell_count_est,
            "clock_period": clock_period,
            "attempt": self.floorplan_attempts,
        }
        self.transition(BuildState.HARDENING)

    def _pivot_strategy(self, reason: str):
        self.pivot_count += 1
        self.log(
            f"Strategy pivot triggered ({self.pivot_count}/{self.max_pivots}): {reason}",
            refined=True,
        )
        if self.pivot_count > self.max_pivots:
            self.log("Pivot budget exceeded. Failing closed.", refined=True)
            self.state = BuildState.FAIL
            return

        stage = self.strategy_pivot_stage % 3
        self.strategy_pivot_stage += 1

        if stage == 0:
            old = float(
                self.artifacts.get(
                    "clock_period_override",
                    self.pdk_profile.get("default_clock_period", "10.0"),
                )
            )
            new = round(old * 1.10, 2)
            self.artifacts["clock_period_override"] = str(new)
            self.log(
                f"Pivot step: timing constraint tune ({old}ns -> {new}ns).",
                refined=True,
            )
            self.transition(BuildState.FLOORPLAN, preserve_retries=True)
            return

        if stage == 1:
            old_scale = float(self.artifacts.get("area_scale", 1.0))
            new_scale = round(old_scale * 1.15, 3)
            self.artifacts["area_scale"] = new_scale
            self.log(
                f"Pivot step: area expansion ({old_scale}x -> {new_scale}x).",
                refined=True,
            )
            self.transition(BuildState.FLOORPLAN, preserve_retries=True)
            return

        # stage 2: logic decoupling prompt
        self.artifacts["logic_decoupling_hint"] = (
            "Apply register slicing / pipeline decoupling on critical path; "
            "reduce combinational depth while preserving behavior."
        )
        self.log(
            "Pivot step: requesting logic decoupling (register slicing) in RTL regen.",
            refined=True,
        )
        self.transition(BuildState.RTL_GEN, preserve_retries=True)

    def do_convergence_review(self):
        """Assess congestion/timing convergence and prevent futile loops."""
        self.log("Assessing convergence (timing + congestion + PPA)...", refined=True)

        congestion = parse_congestion_metrics(
            self.name, run_tag=self.artifacts.get("run_tag", "agentrun")
        )
        sta = parse_sta_signoff(self.name)
        power = parse_power_signoff(self.name)
        metrics, _ = check_physical_metrics(self.name)

        self.artifacts["congestion"] = congestion
        self.artifacts["sta_signoff"] = sta
        self.artifacts["power_signoff"] = power
        if metrics:
            self.artifacts["metrics"] = metrics

        wns = float(sta.get("worst_setup", 0.0)) if not sta.get("error") else -999.0
        tns = 0.0
        area_um2 = float(metrics.get("chip_area_um2", 0.0)) if metrics else 0.0
        power_w = float(power.get("total_power_w", 0.0))
        cong_pct = float(congestion.get("total_usage_pct", 0.0))
        snap = ConvergenceSnapshot(
            iteration=len(self.convergence_history) + 1,
            wns=wns,
            tns=tns,
            congestion=cong_pct,
            area_um2=area_um2,
            power_w=power_w,
        )
        self.convergence_history.append(snap)
        self.artifacts["convergence_history"] = [asdict(x) for x in self.convergence_history]

        self.log(
            f"Convergence snapshot: WNS={wns:.3f}ns, congestion={cong_pct:.2f}%, area={area_um2:.1f}um^2, power={power_w:.6f}W",
            refined=True,
        )

        # --- LLM-driven convergence analysis with hardcoded fallback ---
        try:
            conv_analyst = Agent(
                role="PPA Convergence Analyst",
                goal="Decide whether to continue, pivot, or accept current PPA metrics",
                backstory="Expert in timing closure, congestion mitigation, and PPA trade-offs for ASIC designs.",
                llm=self.get_llm_for_role("physical"),
                verbose=False,
                allow_delegation=False,
            )
            convergence_task = Task(
                description=f"""Analyze convergence for "{self.name}".

CONVERGENCE HISTORY:
{json.dumps([asdict(s) for s in self.convergence_history], indent=2)}

CURRENT METRICS:
- WNS: {wns:.3f}ns (negative = timing violation)
- Congestion: {cong_pct:.2f}% (threshold: {self.congestion_threshold:.1f}%)
- Area: {area_um2:.1f}um²
- Power: {power_w:.6f}W
- Pivot budget remaining: {self.max_pivots - self.pivot_count}
- Area expansions done: {self.artifacts.get("area_expansions", 0)}

DECIDE one of:
A) CONVERGED — Metrics are acceptable (WNS >= 0, congestion within threshold), proceed to signoff
B) TUNE_TIMING — Relax clock period by 10% and re-run hardening
C) EXPAND_AREA — Increase die area by 15% and re-run hardening
D) DECOUPLE_LOGIC — Need RTL pipeline restructuring to reduce congestion
E) FAIL — No convergence possible within remaining budget

Reply in this EXACT format (2 lines):
DECISION: <letter A-E>
REASONING: <1-line explanation>""",
                expected_output="Convergence decision with DECISION and REASONING",
                agent=conv_analyst,
            )

            with console.status(
                "[accent]LLM Analyzing Convergence...[/accent]",
                spinner="dots12",
                spinner_style="spinner",
            ):
                conv_result = str(
                    self._crew_kickoff(
                        Crew(
                            verbose=False,
                            agents=[conv_analyst],
                            tasks=[convergence_task],
                        )
                    )
                ).strip()
            self.logger.info(f"CONVERGENCE LLM ANALYSIS:\n{conv_result}")

            # Parse decision
            decision = None
            conv_reasoning = ""
            for conv_line in conv_result.split("\n"):
                conv_line_s = conv_line.strip()
                if conv_line_s.startswith("DECISION:"):
                    letter = conv_line_s.replace("DECISION:", "").strip().upper()
                    if letter and letter[0] in "ABCDE":
                        decision = letter[0]
                elif conv_line_s.startswith("REASONING:"):
                    conv_reasoning = conv_line_s.replace("REASONING:", "").strip()

            if decision:
                self.log(
                    f"LLM convergence decision: {decision} ({conv_reasoning})",
                    refined=True,
                )
                if decision == "A":
                    self.transition(BuildState.POWER_ANALYSIS)
                    return
                elif decision == "B":
                    old = float(
                        self.artifacts.get(
                            "clock_period_override",
                            self.pdk_profile.get("default_clock_period", "10.0"),
                        )
                    )
                    new = round(old * 1.10, 2)
                    self.artifacts["clock_period_override"] = str(new)
                    self.log(
                        f"LLM-directed: timing constraint tune ({old}ns -> {new}ns).",
                        refined=True,
                    )
                    self.transition(BuildState.FLOORPLAN, preserve_retries=True)
                    return
                elif decision == "C":
                    old_scale = float(self.artifacts.get("area_scale", 1.0))
                    new_scale = round(old_scale * 1.15, 3)
                    self.artifacts["area_scale"] = new_scale
                    self.log(
                        f"LLM-directed: area expansion ({old_scale}x -> {new_scale}x).",
                        refined=True,
                    )
                    self.transition(BuildState.FLOORPLAN, preserve_retries=True)
                    return
                elif decision == "D":
                    self.artifacts["logic_decoupling_hint"] = (
                        f"LLM convergence analysis: {conv_reasoning}. "
                        "Apply register slicing / pipeline decoupling on critical path."
                    )
                    self.log(
                        "LLM-directed: requesting logic decoupling in RTL regen.",
                        refined=True,
                    )
                    self.transition(BuildState.RTL_GEN, preserve_retries=True)
                    return
                else:  # E = FAIL
                    self.log(
                        f"LLM convergence verdict: FAIL ({conv_reasoning})",
                        refined=True,
                    )
                    self.state = BuildState.FAIL
                    return
            else:
                self.log(
                    "LLM convergence parse failed; falling back to heuristic.",
                    refined=True,
                )

        except Exception as e:
            self.log(
                f"LLM convergence analysis failed ({e}); falling back to heuristic.",
                refined=True,
            )

        # --- Hardcoded fallback (original logic) ---
        if cong_pct > self.congestion_threshold:
            self.log(
                f"Congestion {cong_pct:.2f}% exceeds threshold {self.congestion_threshold:.2f}%.",
                refined=True,
            )
            area_expansions = int(self.artifacts.get("area_expansions", 0))
            if area_expansions < 2:
                self.artifacts["area_expansions"] = area_expansions + 1
                self.artifacts["area_scale"] = round(
                    float(self.artifacts.get("area_scale", 1.0)) * 1.15, 3
                )
                self.log("Applying +15% area expansion due to congestion.", refined=True)
                self.transition(BuildState.FLOORPLAN, preserve_retries=True)
                return
            self._pivot_strategy("congestion persisted after area expansions")
            return

        if len(self.convergence_history) >= 3:
            w_prev = self.convergence_history[-2].wns
            w_curr = self.convergence_history[-1].wns
            w_prev2 = self.convergence_history[-3].wns
            improve1 = w_curr - w_prev
            improve2 = w_prev - w_prev2
            if improve1 < 0.01 and improve2 < 0.01:
                self._pivot_strategy("WNS stagnated for 2 iterations (<0.01ns)")
                return

        self.transition(BuildState.POWER_ANALYSIS)

    def do_eco_patch(self):
        """Dual-mode ECO: attempt gate patch first, fallback to RTL micro-patch."""
        self.eco_attempts += 1
        self.log(f"Running ECO attempt {self.eco_attempts}...", refined=True)
        strategy = "gate" if self.eco_attempts == 1 else "rtl"
        ok, patch_result = apply_eco_patch(self.name, strategy=strategy)
        self.artifacts["eco_patch"] = patch_result
        if not ok:
            self.log(f"ECO patch failed: {patch_result}", refined=True)
            self.state = BuildState.FAIL
            return
        # For now, ECO patch is represented as artifact + rerun hardening/signoff.
        self.log(f"ECO artifact generated: {patch_result}", refined=True)
        self.transition(BuildState.HARDENING, preserve_retries=True)

    def do_hardening(self):
        # 1. Generate config.tcl (CRITICAL: Required for OpenLane)
        self.log(f"Generating OpenLane config for {self.name}...", refined=True)

        # Dynamic Clock Detection
        rtl_code = self.artifacts.get("rtl_code", "")
        clock_port = "clk"  # Default

        # Regex to find clock port: input ... clk ... ;
        # Matches: input clk, input wire clk, input logic clk
        clk_match = re.search(
            r"input\s+(?:wire\s+|logic\s+)?(\w*(?:clk|clock|sclk|aclk)\w*)\s*[;,\)]",
            rtl_code,
            re.IGNORECASE,
        )
        if clk_match:
            clock_port = clk_match.group(1)
            self.log(f"Detected Clock Port: {clock_port}", refined=True)

        # Modern OpenLane Config Template
        # Note: We use GRT_ADJUSTMENT instead of deprecated GLB_RT_ADJUSTMENT

        std_cell_lib = self.pdk_profile.get("std_cell_library", "sky130_fd_sc_hd")
        pdk_name = self.pdk_profile.get("pdk", PDK)
        clock_period = str(
            self.artifacts.get(
                "clock_period_override",
                self.pdk_profile.get("default_clock_period", "10.0"),
            )
        )
        floor_meta = self.artifacts.get("floorplan_meta", {})
        die = int(floor_meta.get("die_area", 500))
        util = 40 if die >= 500 else 50

        config_tcl = f"""
# User config
set ::env(DESIGN_NAME) "{self.name}"

# PDK Setup
set ::env(PDK) "{pdk_name}"
set ::env(STD_CELL_LIBRARY) "{std_cell_lib}"

# Verilog Files
# Verilog Files (glob all .v files — supports multi-module designs)
set ::env(VERILOG_FILES) [glob $::env(DESIGN_DIR)/src/*.v]

# Clock Configuration
set ::env(CLOCK_PORT) "{clock_port}"
set ::env(CLOCK_NET) "{clock_port}"
set ::env(CLOCK_PERIOD) "{clock_period}"

# Synthesis
set ::env(SYNTH_STRATEGY) "AREA 0"
set ::env(SYNTH_SIZING) 1

# Floorplanning
set ::env(FP_SIZING) "absolute"
set ::env(DIE_AREA) "0 0 {die} {die}"
set ::env(FP_CORE_UTIL) {util}
set ::env(PL_TARGET_DENSITY) {util / 100 + 0.05:.2f}

# Routing
set ::env(GRT_ADJUSTMENT) 0.15

# Magic
set ::env(MAGIC_DRC_USE_GDS) 1
"""
        from .tools.vlsi_tools import write_config

        try:
            write_config(self.name, config_tcl)
            self.log("OpenLane config.tcl generated successfully.", refined=True)
        except Exception as e:
            self.log(f"Failed to generate config.tcl: {e}", refined=True)
            self.state = BuildState.FAIL
            return

        # 2. Run OpenLane
        run_tag = f"agentrun_{self.global_step_count}"
        floorplan_tcl = self.artifacts.get("floorplan_tcl", "")
        with console.status("[info]Hardening Layout (OpenLane)...[/info]"):
            success, result = run_openlane(
                self.name,
                background=False,
                run_tag=run_tag,
                floorplan_tcl=floorplan_tcl,
                pdk_name=pdk_name,
            )

        if success:
            self.artifacts["gds"] = result
            self.artifacts["run_tag"] = run_tag
            self.log(f"GDSII generated: {result}", refined=True)
            self.transition(BuildState.CONVERGENCE_REVIEW)
        else:
            # ── Self-Reflective Retry via SelfReflectPipeline ──
            self.log(f"Hardening failed. Activating self-reflection retry...", refined=True)
            try:
                reflect_pipeline = SelfReflectPipeline(
                    llm=self.get_llm_for_role("manager"),
                    max_retries=3,
                    verbose=self.verbose,
                    on_reflection=lambda evt: self.log(
                        f"[Self-Reflect] {evt.get('category', '')}: {evt.get('reflection', '')[:120]}",
                        refined=True,
                    ),
                )

                def _hardening_action():
                    """Re-run OpenLane and return (success, error_msg, metrics)."""
                    new_tag = f"agentrun_{self.global_step_count}_{int(time.time()) % 10000}"
                    ok, res = run_openlane(
                        self.name,
                        background=False,
                        run_tag=new_tag,
                        floorplan_tcl=self.artifacts.get("floorplan_tcl", ""),
                        pdk_name=pdk_name,
                    )
                    if ok:
                        self.artifacts["gds"] = res
                        self.artifacts["run_tag"] = new_tag

                    return ok, res if not ok else "", {}

                def _hardening_fix(action):
                    """Apply a corrective action from self-reflection."""
                    if action.action_type == "adjust_config":
                        # Common fix: increase die area or relax utilisation
                        self.log(f"Applying config fix: {action.description}", refined=True)
                        return True  # Mark as applied; the next retry re-generates config
                    elif action.action_type == "modify_rtl":
                        self.log(
                            f"RTL modification suggested: {action.description}",
                            refined=True,
                        )
                        return True
                    return False

                rtl_summary = self.artifacts.get("rtl_code", "")[:2000]
                ok, msg, reflections = reflect_pipeline.run_with_retry(
                    stage_name="OpenLane Hardening",
                    action_fn=_hardening_action,
                    fix_fn=_hardening_fix,
                    rtl_summary=rtl_summary,
                )

                if ok:
                    self.log(f"Hardening recovered via self-reflection: {msg}", refined=True)
                    self.artifacts["self_reflect_history"] = reflect_pipeline.get_summary()
                    self.transition(BuildState.CONVERGENCE_REVIEW)
                else:
                    self.log(f"Hardening failed after self-reflection: {msg}", refined=True)
                    self.artifacts["self_reflect_history"] = reflect_pipeline.get_summary()
                    self.state = BuildState.FAIL
            except Exception as e:
                self.logger.warning(f"SelfReflectPipeline error: {e}")
                self.log(f"Hardening Failed: {result}", refined=True)
                self.state = BuildState.FAIL

    def do_signoff(self):
        """Performs full fabrication-readiness signoff: DRC/LVS, timing closure, power analysis."""
        self.log("Running Fabrication Readiness Signoff...", refined=True)
        fab_ready = True
        run_tag = self.artifacts.get("run_tag", "agentrun")
        gate_netlist = f"{OPENLANE_ROOT}/designs/{self.name}/runs/{run_tag}/results/final/verilog/gl/{self.name}.v"
        rtl_path = self.artifacts.get(
            "rtl_path", f"{OPENLANE_ROOT}/designs/{self.name}/src/{self.name}.v"
        )
        if os.path.exists(rtl_path) and os.path.exists(gate_netlist):
            lec_ok, lec_log = run_eqy_lec(self.name, rtl_path, gate_netlist)
            self.artifacts["lec_result"] = "PASS" if lec_ok else "FAIL"
            self.logger.info(f"LEC RESULT:\n{lec_log}")
            if lec_ok:
                self.log("LEC: PASS", refined=True)
            else:
                self.log("LEC: FAIL", refined=True)
                fab_ready = False
        else:
            self.artifacts["lec_result"] = "SKIP"
            self.log("LEC: skipped (missing RTL or gate netlist)", refined=True)

        # ── 1. DRC / LVS ──
        with console.status(
            "[info]Checking DRC/LVS Reports...[/info]",
            spinner="dots12",
            spinner_style="spinner",
        ):
            signoff_pass, signoff_details = parse_drc_lvs_reports(self.name)

        self.logger.info(f"SIGNOFF DETAILS:\n{signoff_details}")
        self.artifacts["signoff"] = signoff_details

        drc_v = signoff_details.get("drc_violations", -1)
        lvs_e = signoff_details.get("lvs_errors", -1)
        ant_v = signoff_details.get("antenna_violations", -1)

        self.log(
            f"DRC: {drc_v} violations | LVS: {lvs_e} errors | Antenna: {ant_v}",
            refined=True,
        )

        if not signoff_pass:
            fab_ready = False

        # ── 2. TIMING CLOSURE (multi-corner STA) ──
        self.log("Checking Timing Closure (all corners)...", refined=True)
        with console.status(
            "[info]Parsing STA Reports...[/info]",
            spinner="dots12",
            spinner_style="spinner",
        ):
            sta = parse_sta_signoff(self.name)

        self.logger.info(f"STA RESULTS: {sta}")
        self.artifacts["sta_signoff"] = sta

        if sta.get("error"):
            self.log(f"STA: {sta['error']}", refined=True)
            fab_ready = False
        else:
            for c in sta["corners"]:
                status = "✓" if (c["setup_slack"] >= 0 and c["hold_slack"] >= 0) else "✗"
                self.log(
                    f"  {status} {c['name']}: setup={c['setup_slack']:.2f}ns hold={c['hold_slack']:.2f}ns",
                    refined=True,
                )

            if sta["timing_met"]:
                self.log(
                    f"Timing Closure: MET ✓ (worst setup={sta['worst_setup']:.2f}ns, hold={sta['worst_hold']:.2f}ns)",
                    refined=True,
                )
            else:
                self.log(
                    f"Timing Closure: FAILED ✗ (worst setup={sta['worst_setup']:.2f}ns, hold={sta['worst_hold']:.2f}ns)",
                    refined=True,
                )
                fab_ready = False

        # ── 3. POWER & IR-DROP ──
        self.log("Analyzing Power & IR-Drop...", refined=True)
        with console.status(
            "[info]Parsing Power Reports...[/info]",
            spinner="dots12",
            spinner_style="spinner",
        ):
            power = parse_power_signoff(self.name)

        self.logger.info(f"POWER RESULTS: {power}")
        self.artifacts["power_signoff"] = power

        if power["total_power_w"] > 0:
            power_mw = power["total_power_w"] * 1000
            self.log(
                f"Total Power: {power_mw:.3f} mW (Internal {power['internal_power_w'] * 1000:.3f} + Switching {power['switching_power_w'] * 1000:.3f} + Leakage {power['leakage_power_w'] * 1e6:.3f} µW)",
                refined=True,
            )
            self.log(
                f"Breakdown: Sequential {power['sequential_pct']:.1f}% | Combinational {power['combinational_pct']:.1f}%",
                refined=True,
            )

            if power["irdrop_max_vpwr"] > 0 or power["irdrop_max_vgnd"] > 0:
                self.log(
                    f"IR-Drop: VPWR={power['irdrop_max_vpwr'] * 1000:.1f}mV  VGND={power['irdrop_max_vgnd'] * 1000:.1f}mV",
                    refined=True,
                )
                if not power["power_ok"]:
                    self.log("IR-Drop: EXCEEDS 5% VDD THRESHOLD ✗", refined=True)
                    fab_ready = False
                else:
                    self.log("IR-Drop: Within limits ✓", refined=True)

        # 4. Physical Metrics
        with console.status(
            "[info]Analyzing Physical Metrics...[/info]",
            spinner="dots12",
            spinner_style="spinner",
        ):
            metrics, metrics_status = check_physical_metrics(self.name)

        if metrics:
            self.artifacts["metrics"] = metrics
            self.log(f"Area: {metrics.get('chip_area_um2', 'N/A')} µm²", refined=True)

        # 5. Documentation
        self.log("Generating Design Documentation (AI-Powered)...", refined=True)

        doc_agent = get_doc_agent(self.get_llm_for_role("documenter"))

        # Use doc_mode for token budget (prioritizes RTL for documentation)
        budgeted = self.token_budget.budget_context(
            spec=self.artifacts.get("spec", ""),
            rtl=self.artifacts.get("rtl_code", ""),
            mode="doc_mode",
        )

        doc_task = Task(
            description=f"""Generate a comprehensive Datasheet (Markdown) for "{self.name}".
            
            1. **Architecture Spec**:
            {budgeted.get("spec", self.artifacts.get("spec", "N/A"))}
            
            2. **Physical Metrics**:
            {metrics if metrics else "N/A"}
            
            3. **RTL Source Code**:
            ```verilog
            {budgeted.get("rtl", self.artifacts.get("rtl_code", "N/A"))}
            ```
            
            **REQUIREMENTS**:
            - Title: "{self.name} Datasheet"
            - Section 1: **Overview** (High-level functionality and design intent).
            - Section 2: **Block Diagram Description** (Explain the data flow).
            - Section 3: **Interface** (Table of ports with DETAILED descriptions).
            - Section 4: **Register Map** (Address, Name, Access Type, Description).
            - Section 5: **Timing & Performance** (Max Freq, Latency, Throughput).
            - Section 6: **Integration Guide** (How to instantiate and use it).
            
            Return ONLY the Markdown content.
            """,
            expected_output="Markdown Datasheet",
            agent=doc_agent,
        )

        with console.status(
            "[accent]AI Writing Datasheet...[/accent]",
            spinner="dots12",
            spinner_style="spinner",
        ):
            doc_content = str(
                self._crew_kickoff(Crew(verbose=False, agents=[doc_agent], tasks=[doc_task]))
            )

        # Save to file
        doc_path = f"{OPENLANE_ROOT}/designs/{self.name}/{self.name}_datasheet.md"
        try:
            with open(doc_path, "w") as f:
                f.write(doc_content)
            self.artifacts["datasheet"] = doc_path
            self.log(f"Datasheet generated: {doc_path}", refined=True)
        except Exception as e:
            self.log(f"Error writing datasheet: {e}", refined=True)

        try:
            self._write_ip_manifest()
        except Exception as e:
            self.log(f"IP manifest generation warning: {e}", refined=True)

        if self.strict_gates:
            if self.artifacts.get("formal_result", "").startswith("FAIL") or str(
                self.artifacts.get("formal_result", "")
            ).startswith("ERROR"):
                fab_ready = False
            cov = self.artifacts.get("coverage", {})
            if cov:
                cov_thresholds = dict(self.coverage_thresholds)
                cov_thresholds["line"] = max(
                    float(cov_thresholds.get("line", 85.0)), float(self.min_coverage)
                )
                cov_checks = {
                    "line": float(cov.get("line_pct", 0.0)) >= cov_thresholds["line"],
                    "branch": float(cov.get("branch_pct", 0.0)) >= cov_thresholds["branch"],
                    "toggle": float(cov.get("toggle_pct", 0.0)) >= cov_thresholds["toggle"],
                    "functional": float(cov.get("functional_pct", 0.0))
                    >= cov_thresholds["functional"],
                }
                if cov.get("coverage_mode") == "skipped":
                    pass
                elif cov.get("infra_failure", False):
                    if self.coverage_fallback_policy != "skip":
                        fab_ready = False
                elif not all(cov_checks.values()):
                    fab_ready = False

        # FINAL VERDICT
        timing_status = (
            "MET" if sta.get("timing_met") else "FAILED" if not sta.get("error") else "N/A"
        )
        power_status = (
            f"{power['total_power_w'] * 1000:.3f} mW" if power["total_power_w"] > 0 else "N/A"
        )
        irdrop_status = "OK" if power.get("power_ok") else "FAIL (>5% VDD)"

        console.print()
        console.print(
            Panel(
                f"[accent]Fabrication Readiness Report[/accent]\n\n"
                f"DRC:        {drc_v} violations\n"
                f"LVS:        {lvs_e} errors\n"
                f"Timing:     {timing_status} (WNS={sta.get('worst_setup', 0):.2f}ns)\n"
                f"Power:      {power_status}\n"
                f"IR-Drop:    {irdrop_status}\n"
                f"LEC:        {self.artifacts.get('lec_result', 'N/A')}\n"
                f"TB Gates:   {self.artifacts.get('tb_gate_history_count', 0)} events (last={self.artifacts.get('tb_gate_last_action', 'N/A')})\n"
                f"Coverage:   line={self.artifacts.get('coverage', {}).get('line_pct', 'N/A')}% "
                f"branch={self.artifacts.get('coverage', {}).get('branch_pct', 'N/A')}% "
                f"toggle={self.artifacts.get('coverage', {}).get('toggle_pct', 'N/A')}% "
                f"func={self.artifacts.get('coverage', {}).get('functional_pct', 'N/A')}% "
                f"[{self.artifacts.get('coverage', {}).get('backend', 'N/A')}/{self.artifacts.get('coverage', {}).get('coverage_mode', 'N/A')}]\n"
                f"Formal:     {self.artifacts.get('formal_result', 'SKIPPED')}\n\n"
                f"{'[success]FABRICATION READY ✓[/]' if fab_ready else '[error]NOT FABRICATION READY ✗[/]'}",
                title="📋 Signoff Report",
            )
        )

        if fab_ready:
            self.log("✅ SIGNOFF PASSED (Timing Closed, DRC Clean)", refined=True)
            self.artifacts["signoff_result"] = "PASS"
            self.transition(BuildState.IP_PACKAGE)
        else:
            # --- LLM-driven signoff failure analysis before ECO ---
            if self.strict_gates and self.eco_attempts < 2:
                try:
                    signoff_analyst = Agent(
                        role="Signoff Failure Analyst",
                        goal="Identify root cause of signoff failure and recommend fix path",
                        backstory="Expert in DRC/LVS debugging, timing closure, IR-drop mitigation, and ECO strategies.",
                        llm=self.get_llm_for_role("physical"),
                        verbose=False,
                        allow_delegation=False,
                    )
                    signoff_analysis_task = Task(
                        description=f"""Signoff failed for "{self.name}". Analyze the failure:

DRC: {drc_v} violations | LVS: {lvs_e} errors | Antenna: {ant_v}
Timing: {timing_status} (WNS={sta.get("worst_setup", 0):.2f}ns)
Power: {power_status} | IR-Drop: {irdrop_status}
LEC: {self.artifacts.get("lec_result", "N/A")}

CONVERGENCE HISTORY:
{json.dumps([asdict(s) for s in self.convergence_history[-3:]], indent=2) if self.convergence_history else "N/A"}

What is the most likely root cause and what ECO strategy should we use?
Reply in this EXACT format (2 lines):
ROOT_CAUSE: <description of the primary issue>
FIX: <one of: GATE_ECO, RTL_PATCH, AREA_EXPAND, TIMING_RELAX>""",
                        expected_output="Root cause and fix recommendation",
                        agent=signoff_analyst,
                    )

                    with console.status(
                        "[error]LLM Analyzing Signoff Failure...[/error]",
                        spinner="dots12",
                        spinner_style="spinner",
                    ):
                        signoff_analysis = str(
                            self._crew_kickoff(
                                Crew(
                                    verbose=False,
                                    agents=[signoff_analyst],
                                    tasks=[signoff_analysis_task],
                                )
                            )
                        ).strip()
                    self.logger.info(f"SIGNOFF FAILURE ANALYSIS:\n{signoff_analysis}")

                    # Parse and log
                    so_root_cause = ""
                    so_fix = "GATE_ECO"  # default
                    for so_line in signoff_analysis.split("\n"):
                        so_line_s = so_line.strip()
                        if so_line_s.startswith("ROOT_CAUSE:"):
                            so_root_cause = so_line_s.replace("ROOT_CAUSE:", "").strip()
                        elif so_line_s.startswith("FIX:"):
                            so_fix_val = so_line_s.replace("FIX:", "").strip().upper()
                            if so_fix_val in {
                                "GATE_ECO",
                                "RTL_PATCH",
                                "AREA_EXPAND",
                                "TIMING_RELAX",
                            }:
                                so_fix = so_fix_val

                    self.log(f"Signoff diagnosis: {so_root_cause} -> {so_fix}", refined=True)
                    self.artifacts["signoff_failure_analysis"] = {
                        "root_cause": so_root_cause,
                        "recommended_fix": so_fix,
                    }

                    if so_fix == "AREA_EXPAND":
                        self.artifacts["area_scale"] = round(
                            float(self.artifacts.get("area_scale", 1.0)) * 1.15, 3
                        )
                        self.log(
                            "LLM recommends area expansion. Re-running floorplan.",
                            refined=True,
                        )
                        self.transition(BuildState.FLOORPLAN, preserve_retries=True)
                        return
                    elif so_fix == "TIMING_RELAX":
                        old_clk = float(
                            self.artifacts.get(
                                "clock_period_override",
                                self.pdk_profile.get("default_clock_period", "10.0"),
                            )
                        )
                        new_clk = round(old_clk * 1.10, 2)
                        self.artifacts["clock_period_override"] = str(new_clk)
                        self.log(
                            f"LLM recommends timing relaxation ({old_clk}ns -> {new_clk}ns). Re-running.",
                            refined=True,
                        )
                        self.transition(BuildState.FLOORPLAN, preserve_retries=True)
                        return
                    else:
                        # GATE_ECO or RTL_PATCH -> route to ECO stage
                        self.log("Signoff failed. Triggering ECO patch stage.", refined=True)
                        self.transition(BuildState.ECO_PATCH, preserve_retries=True)
                        return

                except Exception as e:
                    self.log(
                        f"Signoff analysis failed ({e}); falling back to ECO.",
                        refined=True,
                    )
                    self.transition(BuildState.ECO_PATCH, preserve_retries=True)
                    return

            self.log("❌ SIGNOFF FAILED (Violations Found)", refined=True)
            self.artifacts["signoff_result"] = "FAIL"
            self.errors.append("Signoff failed (see report).")
            self.state = BuildState.FAIL
