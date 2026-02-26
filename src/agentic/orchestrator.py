import enum
import time
import logging
import os
import re
import hashlib
import json
import signal
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List
from rich.console import Console
from rich.panel import Panel
from crewai import Agent, Task, Crew, LLM

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
from .agents.verifier import get_verification_agent, get_error_analyst_agent, get_regression_agent
from .agents.doc_agent import get_doc_agent
from .agents.sdc_agent import get_sdc_agent
from .tools.vlsi_tools import (
    write_verilog,
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
    parse_eda_log_summary,
    parse_congestion_metrics,
    run_eqy_lec,
    apply_eco_patch,
    run_tb_static_contract_check,
    run_tb_compile_gate,
    repair_tb_for_verilator,
    get_coverage_thresholds,
)

console = Console()

class BuildStrategy(enum.Enum):
    SV_MODULAR = "SystemVerilog Modular (Modern)"
    VERILOG_CLASSIC = "Verilog-2005 (Legacy/Robust)"

class BuildState(enum.Enum):
    INIT = "Initializing"
    SPEC = "Architectural Planning"
    RTL_GEN = "RTL Generation"
    RTL_FIX = "RTL Syntax Fixing"
    VERIFICATION = "Verification & Testbench"
    FORMAL_VERIFY = "Formal Property Verification"
    COVERAGE_CHECK = "Coverage Analysis"
    REGRESSION = "Regression Testing"
    SDC_GEN = "Timing Constraints Generation"
    FLOORPLAN = "Floorplanning"
    HARDENING = "GDSII Hardening"
    CONVERGENCE_REVIEW = "Convergence Review"
    ECO_PATCH = "ECO Patch"
    SIGNOFF = "DRC/LVS Signoff"
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
    def __init__(
        self,
        name: str,
        desc: str,
        llm: LLM,
        max_retries: int = 5,
        verbose: bool = True,
        skip_openlane: bool = False,
        full_signoff: bool = False,
        min_coverage: float = 80.0,
        strict_gates: bool = True,
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
    ):
        self.name = name
        self.desc = desc
        self.llm = llm
        self.event_sink = event_sink  # Web API hook: callable(event_dict) or None
        self.max_retries = max_retries
        self.verbose = verbose
        self.skip_openlane = skip_openlane
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
        self.coverage_fallback_policy = (coverage_fallback_policy or COVERAGE_FALLBACK_POLICY_DEFAULT).lower()
        if self.coverage_fallback_policy not in {"fail_closed", "fallback_oss", "skip"}:
            self.coverage_fallback_policy = COVERAGE_FALLBACK_POLICY_DEFAULT
        self.coverage_profile = (coverage_profile or COVERAGE_PROFILE_DEFAULT).lower()
        if self.coverage_profile not in {"balanced", "aggressive", "relaxed"}:
            self.coverage_profile = COVERAGE_PROFILE_DEFAULT
        self.coverage_thresholds = get_coverage_thresholds(self.coverage_profile)
        
        self.state = BuildState.INIT
        self.strategy = BuildStrategy.SV_MODULAR
        self.retry_count = 0
        self.state_retry_counts: Dict[str, int] = {}
        self.failure_fingerprint_history: Dict[str, int] = {}
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
        self.history: List[Dict[str, Any]] = []    # Log of state transitions and errors
        self.errors: List[str] = []     # List of error messages

    def setup_logger(self):
        """Sets up a file logger for the build process."""
        log_file = os.path.join(self.artifacts['root'], f"{self.name}.log")
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(logging.DEBUG)
        
        # File Handler
        fh = logging.FileHandler(log_file, mode='w')
        fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        self.logger.addHandler(fh)
        
        self.log(f"Logging initialized to {log_file}", refined=True)

    def log(self, message: str, refined: bool = False):
        """Logs a message to the console (if refined) and file (always)."""
        now = time.time()
        self.history.append({"state": self.state.name, "msg": message, "time": now})
        self.build_history.append(BuildHistory(state=self.state.name, message=message, timestamp=now))
        
        # File Log
        if hasattr(self, 'logger'):
            self.logger.info(f"[{self.state.name}] {message}")

        # Web API event sink — emit every log entry for live streaming
        if self.event_sink is not None:
            try:
                self.event_sink({
                    "type": "checkpoint" if refined else "log",
                    "state": self.state.name,
                    "message": message,
                })
            except Exception:
                pass  # Never let sink errors crash the build
            
        # Console Log - Only if refined/important
        if refined or self.verbose: 
             # We actually want to reduce verbosity based on User Request.
             # Only print if 'refined' is True (High level updates)
             if refined:
                style = "bold green"
                console.print(f"[{style}][{self.state.name}][/] {message}")

    def transition(self, new_state: BuildState, preserve_retries: bool = False):
        self.log(f"Transitioning: {self.state.name} -> {new_state.name}", refined=True)
        self.state = new_state
        if not preserve_retries:
            self.retry_count = 0  # Reset retries on state change
            self.state_retry_counts[new_state.name] = 0
        # Emit a dedicated transition event for the web UI checkpoint timeline
        if self.event_sink is not None:
            try:
                self.event_sink({
                    "type": "transition",
                    "state": new_state.name,
                    "message": f"▶ {new_state.value}",
                })
            except Exception:
                pass

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
        return count >= 2

    def _build_llm_context(self, include_rtl: bool = True, max_rtl_chars: int = 15000) -> str:
        """Build cumulative context string for LLM calls.

        Aggregates build state so the LLM can reason across the full pipeline
        instead of seeing only the immediate error + code.
        """
        sections = []

        # 1. Architecture spec
        spec = self.artifacts.get("spec", "")
        if spec:
            sections.append(f"ARCHITECTURE SPEC:\n{spec[:6000]}")

        # 2. Current RTL snippet
        if include_rtl:
            rtl = self.artifacts.get("rtl_code", "")
            if rtl:
                sections.append(f"CURRENT RTL ({len(rtl)} chars, truncated):\n```verilog\n{rtl[:max_rtl_chars]}\n```")

        # 3. Build strategy & state
        sections.append(
            f"BUILD STATE: {self.state.name} | Strategy: {self.strategy.value} | "
            f"Step: {self.global_step_count}/{self.global_step_budget} | "
            f"Pivots: {self.pivot_count}/{self.max_pivots} | "
            f"Retries this state: {self.state_retry_counts.get(self.state.name, 0)}/{self.max_retries}"
        )

        # 4. Convergence history (last 3 snapshots)
        if self.convergence_history:
            recent = self.convergence_history[-3:]
            conv_lines = []
            for s in recent:
                conv_lines.append(
                    f"  iter={s.iteration}: WNS={s.wns:.3f}ns cong={s.congestion:.2f}% "
                    f"area={s.area_um2:.1f}um² power={s.power_w:.6f}W"
                )
            sections.append("CONVERGENCE HISTORY:\n" + "\n".join(conv_lines))

        # 5. Coverage data
        cov = self.artifacts.get("coverage", {})
        if cov:
            sections.append(
                f"COVERAGE: line={cov.get('line_pct', 'N/A')}% "
                f"branch={cov.get('branch_pct', 'N/A')}% "
                f"toggle={cov.get('toggle_pct', 'N/A')}% "
                f"functional={cov.get('functional_pct', 'N/A')}% "
                f"[{cov.get('backend', 'N/A')}/{cov.get('coverage_mode', 'N/A')}]"
            )

        # 6. Formal + signoff results
        formal = self.artifacts.get("formal_result", "")
        if formal:
            sections.append(f"FORMAL RESULT: {formal}")
        signoff = self.artifacts.get("signoff_result", "")
        if signoff:
            sections.append(f"SIGNOFF RESULT: {signoff}")

        # 7. Semantic rigor report
        sem = self.artifacts.get("semantic_report", "")
        if sem:
            sections.append(f"SEMANTIC RIGOR: {str(sem)[:2000]}")

        # 8. Previous failure history
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

        # Build history (last 5 state transitions with errors)
        error_entries = [h for h in self.build_history if "error" in h.message.lower() or "fail" in h.message.lower()]
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
        self.log(f"Starting Build Process for '{self.name}' using {self.strategy.value}", refined=True)
        
        try:
            while self.state != BuildState.SUCCESS and self.state != BuildState.FAIL:
                self.global_step_count += 1
                if self.global_step_count > self.global_step_budget:
                    self.log(f"Global step budget exceeded ({self.global_step_budget}). Failing closed.", refined=True)
                    self.state = BuildState.FAIL
                    break
                if self.state == BuildState.INIT:
                    self.do_init()
                elif self.state == BuildState.SPEC:
                    self.do_spec()
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
            self.log(f"CRITICAL ERROR: {str(e)}", refined=False)
            import traceback
            console.print(traceback.format_exc())
            self.state = BuildState.FAIL

        if self.state == BuildState.SUCCESS:
            try:
                self._save_industry_benchmark_metrics()
            except Exception as e:
                self.log(f"Benchmark metrics export warning: {e}", refined=True)
            # Create a clean summary of just the paths
            summary = {k: v for k, v in self.artifacts.items() if 'code' not in k and 'spec' not in k}
            
            console.print(Panel(
                f"[bold green]BUILD SUCCESSFUL[/]\n\n" + 
                "\n".join([f"[bold]{k.upper()}:[/] {v}" for k, v in summary.items()]),
                title="Done"
            ))
        else:
            console.print(Panel(f"[bold red]BUILD FAILED[/]", title="Failed"))

    # --- ACTION HANDLERS ---

    def do_init(self):
        with console.status("[bold green]Initializing Workspace...[/bold green]"):
            # Setup directories, check tools
            self.artifacts['root'] = f"{OPENLANE_ROOT}/designs/{self.name}"
            self.setup_logger() # Setup logging to file
            self.artifacts["pdk_profile"] = self.pdk_profile
            self.log(
                f"PDK profile: {self.pdk_profile.get('profile')} "
                f"(PDK={self.pdk_profile.get('pdk')}, LIB={self.pdk_profile.get('std_cell_library')})",
                refined=True,
            )
            diag = startup_self_check()
            self.artifacts["startup_check"] = diag
            self.logger.info(f"STARTUP SELF CHECK: {diag}")
            if self.strict_gates and not diag.get("ok", False):
                self.log("Startup self-check failed in strict mode.", refined=True)
                self.state = BuildState.FAIL
                return
            time.sleep(1) # Visual pause
            self.transition(BuildState.SPEC)

    def do_spec(self):
        # Derive safe module name upfront — Verilog identifiers cannot start with a digit
        safe_name = self.name
        if safe_name and safe_name[0].isdigit():
            safe_name = "chip_" + safe_name
        # Also store the corrected name so RTL_GEN uses it
        self.name = safe_name

        arch_agent = Agent(
            role='Chief System Architect',
            goal=f'Define a robust micro-architecture for {self.name}',
            backstory=(
                "Veteran Silicon Architect with 20+ years spanning CPUs, DSPs, FIFOs, "
                "crypto cores, interface bridges, and SoC peripherals. "
                "Defines clean, complete, production-ready interfaces, FSMs, and datapaths. "
                "Never uses placeholders or simplified approximations."
            ),
            llm=self.llm,
            verbose=self.verbose
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
            expected_output='Complete Markdown Specification with all sections filled in',
            agent=arch_agent
        )

        with console.status("[bold cyan]Architecting Chip Specification...[/bold cyan]"):
            result = Crew(agents=[arch_agent], tasks=[spec_task]).kickoff()

        self.artifacts['spec'] = str(result)
        self.log("Architecture Plan Generated", refined=True)
        self.transition(BuildState.RTL_GEN)

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
            return """Use SystemVerilog Class-Based Verification:
            - Create a `class Transaction` with `rand` fields.
            - Create a `class Driver`, `class Monitor`, `class Scoreboard`.
            - **CRITICAL FOR VERILATOR:** DO NOT use `program` blocks. Use a standard `module` for the testbench.
            - Any class interface handle MUST be `virtual <if_name>`.
            - Constructor/task/function args that pass interfaces MUST use `virtual <if_name>`.
            - Covergroups may only sample declared in-scope symbols (no dangling bare signal names).
            - Instantiate the DUT in the top-level `module`.
            - Use `initial` blocks for test sequencing.
            - Ensure randomization and coverage."""
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
        params = re.findall(r'parameter\s+(\w+)\s*=\s*([^,;\)]+)', rtl_code)
        if params:
            lines.append("Parameters: " + ", ".join(f"{n} = {v.strip()}" for n, v in params))
        
        # Extract ports — match input/output/inout declarations
        # Support parameterized widths like [DATA_W-1:0] in addition to [7:0]
        inputs = []
        outputs = []
        inouts = []
        
        for match in re.finditer(
            r'\b(input|output|inout)\s+(?:reg|wire|logic)?\s*(?:signed\s*)?(?:(\[[^\]]+\]))?\s*(\w+)',
            rtl_code
        ):
            direction, width, name = match.groups()
            port_str = name + (f" {width}" if width else "")
            if direction == 'input':
                inputs.append(port_str)
            elif direction == 'output':
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
        header_match = re.search(r'(module\s+\w+[\s\S]*?;)', rtl_code)
        return header_match.group(1) if header_match else "Could not extract ports — see full RTL below."

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
            if all(token not in text for token in ["class Driver", "class Monitor", "class Scoreboard"]):
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
                                if re.match(r'^[\d\s\+\-\*\/\(\)]+$', part):
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
        return count >= 2

    def generate_uvm_lite_tb_from_rtl_ports(self, design_name: str, rtl_code: str) -> str:
        """Deterministic UVM-lite template (Verilator-safe) generated from RTL ports."""
        ports = self._extract_module_ports(rtl_code)
        if not ports:
            return self._generate_fallback_testbench(rtl_code)

        if_name = f"{design_name}_if"
        clock_name = None
        reset_name = None
        input_ports: List[Dict[str, str]] = []
        output_ports: List[Dict[str, str]] = []
        inout_ports: List[Dict[str, str]] = []

        for p in ports:
            pname = p["name"]
            if p["direction"] == "input":
                input_ports.append(p)
                if clock_name is None and re.search(r'(?:^|_)(?:clk|clock|sclk|aclk)(?:_|$)|^i_clk', pname, re.IGNORECASE):
                    clock_name = pname
                if reset_name is None and re.search(r'(?:^|_)(?:rst|reset|nrst|areset)(?:_|$)|^i_rst', pname, re.IGNORECASE):
                    reset_name = pname
            elif p["direction"] == "output":
                output_ports.append(p)
            else:
                inout_ports.append(p)

        non_clk_inputs = [p for p in input_ports if p["name"] != clock_name]

        lines: List[str] = ["`timescale 1ns/1ps", ""]
        lines.append(f"interface {if_name};")
        for p in ports:
            width = f"{p['width']} " if p["width"] else ""
            lines.append(f"  logic {width}{p['name']};")
        drv_input = clock_name if clock_name else (input_ports[0]["name"] if input_ports else ports[0]["name"])
        drv_outputs = [p["name"] for p in non_clk_inputs + inout_ports if p["name"] != drv_input]
        if drv_outputs:
            lines.append(f"  modport drv (input {drv_input}, output {', '.join(drv_outputs)});")
        else:
            lines.append(f"  modport drv (input {drv_input});")

        mon_inputs: List[str] = []
        if clock_name:
            mon_inputs.append(clock_name)
        mon_inputs.extend([p["name"] for p in output_ports + inout_ports])
        mon_inputs = list(dict.fromkeys(mon_inputs))
        if mon_inputs:
            lines.append(f"  modport mon (input {', '.join(mon_inputs)});")
        lines.append("endinterface")
        lines.append("")

        lines.extend(
            [
                f"module {design_name}_tb;",
                f"  {if_name} vif();",
                "",
                "class Transaction;",
                "  rand bit [31:0] stimulus;",
                "  bit has_x;",
                "endclass",
                "",
                "class Driver;",
                f"  virtual {if_name} vif;",
                f"  function new(virtual {if_name} vif);",
                "    this.vif = vif;",
                "  endfunction",
                "",
                "  task reset_phase();",
            ]
        )
        if reset_name:
            if reset_name.lower().endswith("_n"):
                lines.extend(
                    [
                        f"    vif.{reset_name} = 1'b0;",
                        "    repeat (5) @(posedge vif." + (clock_name if clock_name else non_clk_inputs[0]["name"]) + ");" if clock_name else "    #50;",
                        f"    vif.{reset_name} = 1'b1;",
                    ]
                )
            else:
                lines.extend(
                    [
                        f"    vif.{reset_name} = 1'b1;",
                        "    repeat (5) @(posedge vif." + (clock_name if clock_name else non_clk_inputs[0]["name"]) + ");" if clock_name else "    #50;",
                        f"    vif.{reset_name} = 1'b0;",
                    ]
                )
        lines.append("  endtask")
        lines.append("")
        lines.append("  task drive_step();")
        if clock_name:
            lines.append(f"    @(posedge vif.{clock_name});")
        else:
            lines.append("    #10;")
        for p in non_clk_inputs:
            pname = p["name"]
            if pname == reset_name:
                continue
            width = p["width"]
            if width:
                lines.append(f"    vif.{pname} = $urandom;")
            else:
                lines.append(f"    vif.{pname} = $random % 2;")
        lines.append("  endtask")
        lines.append("endclass")
        lines.append("")
        lines.extend(
            [
                "class Monitor;",
                f"  virtual {if_name} vif;",
                f"  function new(virtual {if_name} vif);",
                "    this.vif = vif;",
                "  endfunction",
                "  function bit has_unknown_output();",
                "    bit bad;",
                "    bad = 0;",
            ]
        )
        for p in output_ports + inout_ports:
            lines.append(f"    if (^(vif.{p['name']}) === 1'bx) bad = 1;")
        lines.extend(
            [
                "    return bad;",
                "  endfunction",
                "endclass",
                "",
                "class Scoreboard;",
                "  int errors;",
                "  function new();",
                "    errors = 0;",
                "  endfunction",
                "  function void sample(bit has_x);",
                "    if (has_x) begin",
                '      $display("TEST FAILED: X/Z detected on DUT output.");',
                "      errors++;",
                "    end",
                "  endfunction",
                "endclass",
                "",
                "class Environment;",
                f"  virtual {if_name} vif;",
                "  Driver drv;",
                "  Monitor mon;",
                "  Scoreboard sb;",
                f"  function new(virtual {if_name} vif);",
                "    this.vif = vif;",
                "    drv = new(vif);",
                "    mon = new(vif);",
                "    sb = new();",
                "  endfunction",
                "  task run();",
                "    drv.reset_phase();",
                "    repeat (40) begin",
                "      drv.drive_step();",
                "      sb.sample(mon.has_unknown_output());",
                "    end",
                "    if (sb.errors == 0) begin",
                '      $display("TEST PASSED");',
                "    end else begin",
                '      $display("TEST FAILED");',
                "    end",
                "  endtask",
                "endclass",
                "",
            ]
        )
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
        conn = [f"    .{p['name']}(vif.{p['name']})" for p in ports]
        lines.append(",\n".join(conn))
        lines.extend(["  );", ""])
        if clock_name:
            lines.extend(
                [
                    "  initial begin",
                    f"    vif.{clock_name} = 1'b0;",
                    f"    forever #5 vif.{clock_name} = ~vif.{clock_name};",
                    "  end",
                    "",
                ]
            )
        lines.extend(
            [
                "  initial begin",
                "    Environment env;",
                "    env = new(vif);",
                "    env.run();",
                "    $finish;",
                "  end",
                "endmodule",
                "",
            ]
        )
        return "\n".join(lines)

    def _deterministic_tb_fallback(self, rtl_code: str) -> str:
        if self.tb_fallback_template == "classic":
            return self._generate_fallback_testbench(rtl_code)
        return self.generate_uvm_lite_tb_from_rtl_ports(self.name, rtl_code)

    def _handle_tb_gate_failure(self, gate: str, report: Dict[str, Any], tb_code: str) -> None:
        if self._record_tb_failure_fingerprint(gate, report):
            self.log(f"Repeated TB {gate} fingerprint detected. Failing closed.", refined=True)
            self.state = BuildState.FAIL
            return

        cycle = int(self.tb_recovery_counts.get(gate, 0)) + 1
        self.tb_recovery_counts[gate] = cycle
        self.artifacts[f"tb_recovery_cycle_{gate}"] = cycle

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
                    self.log("TB gate failed; applied deterministic auto-repair.", refined=True)
                    return
            self.tb_repair_fail_count += 1
            self._record_tb_gate_history(gate, False, "auto_repair_nochange", report)
            return

        if cycle == 2:
            self.artifacts["tb_regen_context"] = json.dumps(report, indent=2, default=str)[:5000]
            tb_path = self.artifacts.get("tb_path")
            if tb_path and os.path.exists(tb_path):
                try:
                    os.remove(tb_path)
                except OSError:
                    pass
            self.artifacts.pop("tb_path", None)
            self._record_tb_gate_history(gate, False, "llm_regenerate", report)
            self.log("TB gate failed; forcing LLM TB regeneration with structured diagnostics.", refined=True)
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
        self.tb_recovery_counts[gate] = 0
        self.artifacts[f"tb_recovery_cycle_{gate}"] = 0
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
                    body.append(f"        $display(\"TEST FAILED: X detected on {outp}\");")
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
        timeout_s = max(1, int(timeout_s))
        if not hasattr(signal, "SIGALRM"):
            return str(Crew(agents=agents, tasks=tasks).kickoff())  # type: ignore

        def _timeout_handler(signum, frame):
            raise TimeoutError(f"Crew kickoff exceeded {timeout_s}s timeout")

        prev_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout_s)
        try:
            return str(Crew(agents=agents, tasks=tasks).kickoff())  # type: ignore
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, prev_handler)

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
                    "line": float(coverage.get("line_pct", 0.0)) >= max(float(self.coverage_thresholds.get("line", 85.0)), float(self.min_coverage)),
                    "branch": float(coverage.get("branch_pct", 0.0)) >= float(self.coverage_thresholds.get("branch", 80.0)),
                    "toggle": float(coverage.get("toggle_pct", 0.0)) >= float(self.coverage_thresholds.get("toggle", 75.0)),
                    "functional": float(coverage.get("functional_pct", 0.0)) >= float(self.coverage_thresholds.get("functional", 80.0)),
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
                "clock_period_ns": self.artifacts.get("clock_period_override", self.pdk_profile.get("default_clock_period")),
                "pivots_used": self.pivot_count,
                "global_steps": self.global_step_count,
            },
        }
        return snapshot

    def _save_industry_benchmark_metrics(self):
        """Write benchmark metrics after successful chip creation to metircs/."""
        snapshot = self._build_industry_benchmark_snapshot()
        metrics_root = os.path.join(WORKSPACE_ROOT, "metircs")
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
        # Check Golden Reference Library for a matching template
        from .golden_lib import get_best_template
        
        template = get_best_template(self.desc, self.name)
        
        if template:
            self.log(f"Golden Reference MATCH: {template['ip_type']} (score={template['score']})", refined=True)
            self.artifacts['golden_template'] = template['ip_type']
            
            # Use the golden RTL DIRECTLY — just rename the module.
            # This guarantees RTL ↔ TB compatibility (both are pre-verified together).
            rtl_code = template['template_code']
            original_name = template['ip_type']  # e.g. 'counter', 'fifo', 'uart_tx'
            rtl_code = rtl_code.replace(f'module {original_name}', f'module {self.name}')
            self.log(f"Using golden {original_name} template with module renamed to {self.name}.", refined=True)
            self.logger.info(f"GOLDEN RTL ({original_name}):\n{rtl_code}")
            
            # Also save the golden testbench if available
            if template.get('tb_code'):
                self.artifacts['golden_tb'] = template['tb_code']
        else:
            # No template match — pure LLM generation
            self.log("No golden template match. Generating from scratch.", refined=True)
            strategy_prompt = self._get_strategy_prompt()
            
            rtl_agent = get_designer_agent(
                self.llm, 
                goal=f"Create {self.strategy.name} RTL for {self.name}", 
                verbose=self.verbose,
                strategy=self.strategy.name
            )
            
            rtl_task = Task(
                description=f"""Design module "{self.name}" based on SPEC.
            
SPECIFICATION:
{self.artifacts.get('spec', '')}

STRATEGY GUIDELINES:
{strategy_prompt}

LOGIC DECOUPLING HINT:
{self.artifacts.get('logic_decoupling_hint', 'N/A')}

CRITICAL RULES:
1. Top-level module name MUST be "{self.name}"
2. Async active-low reset `rst_n`
3. Flatten ports on the TOP module (no multi-dim arrays on top-level ports). Internal modules can use them.
4. **IMPLEMENT EVERYTHING**: Do not leave any logic as "to be implemented" or "simplified".
5. **MODULAR HIERARCHY**: For complex designs, break them into smaller sub-modules. Output ALL modules in your response.
6. Return code in ```verilog fence.
""",
                expected_output='Verilog Code',
                agent=rtl_agent
            )
            
            with console.status(f"[bold yellow]Generating RTL ({self.strategy.name})...[/bold yellow]"):
                result = Crew(agents=[rtl_agent], tasks=[rtl_task]).kickoff()
            
            rtl_code = str(result)
            self.logger.info(f"GENERATED RTL ({self.strategy.name}):\n{rtl_code}")
        
        # Save file (write_verilog cleans LLM output: strips markdown, think tags, etc.)
        path = write_verilog(self.name, rtl_code)
        if "Error" in path:
            self.log(f"File Write Error: {path}", refined=True)
            self.state = BuildState.FAIL
            return

        self.artifacts['rtl_path'] = path
        # Store the CLEANED code (read back from file), not raw LLM output
        with open(path, 'r') as f:
            self.artifacts['rtl_code'] = f.read()
        self._evaluate_hierarchy(self.artifacts['rtl_code'])
        self._emit_hierarchical_block_artifacts()
        self.transition(BuildState.RTL_FIX)

    def do_rtl_fix(self):
        # Check syntax
        path = self.artifacts['rtl_path']
        success, errors = run_syntax_check(path)
        
        if success:
            self.log("Syntax Check Passed (Verilator)", refined=True)
            
            # --- START VERILATOR LINT CHECK ---
            with console.status("[bold yellow]Running Verilator Lint...[/bold yellow]"):
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
                    with open(path, 'r') as f:
                        self.artifacts['rtl_code'] = f.read()
                    # Re-check syntax after fix (stay in RTL_FIX)
                    return

                sem_ok, sem_report = run_semantic_rigor_check(path)
                self.logger.info(f"SEMANTIC RIGOR: {sem_report}")
                if not sem_ok:
                    if self.strict_gates:
                        self.log("Semantic rigor gate failed. Routing back to RTL fix.", refined=True)
                        errors = f"SEMANTIC_RIGOR_FAILURE: {sem_report}"
                    else:
                        self.log("Semantic rigor warnings detected (non-blocking).", refined=True)
                        self.artifacts["semantic_report"] = sem_report
                        self.transition(BuildState.VERIFICATION)
                        return
                else:
                    self.artifacts["semantic_report"] = sem_report
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
            self.log("Detected repeated syntax/lint failure fingerprint. Failing closed.", refined=True)
            self.state = BuildState.FAIL
            return
        self.retry_count += 1
        if self.retry_count > self.max_retries:
            self.log("Max Retries Exceeded for Syntax/Lint Fix.", refined=True)
            
            # STATE CROSSING / BACKTRACKING
            if self.strategy == BuildStrategy.SV_MODULAR:
                self.log("Attempting Strategy Pivot: SV_MODULAR -> VERILOG_CLASSIC", refined=True)
                self.strategy = BuildStrategy.VERILOG_CLASSIC
                self.transition(BuildState.RTL_GEN) # Restart RTL Gen with new strategy
                return
            else:
                self.log("Already on fallback strategy. Build Failed.", refined=True)
                self.state = BuildState.FAIL
                return

        self.log(f"Fixing Code (Attempt {self.retry_count}/{self.max_retries})", refined=True)
        errors_for_llm = self._condense_failure_log(str(errors), kind="timing")
        
        # Agents fix syntax — with full build context and failure history
        fix_prompt = f"""Fix Syntax/Lint Errors in "{self.name}".

BUILD CONTEXT:
{self._build_llm_context()}

ERROR LOG:
{errors_for_llm}

PREVIOUS FIX ATTEMPTS (do NOT repeat these):
{self._format_failure_history()}

Strategy: {self.strategy.name} (Keep consistency!)

IMPORTANT: The compiler is Verilator 5.0+ (SystemVerilog 2017+).
- Use modern SystemVerilog features (`logic`, `always_comb`, `always_ff`).
- Ensure strict 2-state logic handling (reset all registers).
- Avoid 4-state logic (x/z) reliance as Verilator is 2-state optimized.
- Explain what you changed and why in a brief comment at the top.

Code:
```verilog
{self.artifacts['rtl_code']}
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
            llm=self.llm,
            verbose=self.verbose,
            tools=[syntax_check_tool, read_file_tool]
        )
        
        task = Task(
             description=fix_prompt,
             expected_output="Fixed Verilog Code",
             agent=fixer
        )
        
        with console.status("[bold red]AI fixing Syntax/Lint Errors...[/bold red]"):
            result = Crew(agents=[fixer], tasks=[task]).kickoff()
            
        new_code = str(result)
        self.logger.info(f"FIXED RTL:\n{new_code}")
        
        new_path = write_verilog(self.name, new_code)
        if isinstance(new_path, str) and new_path.startswith("Error:"):
            self.log(f"File Write Error in FIX stage: {new_path}", refined=True)
            # Don't fail immediately — the LLM returned bad output, retry within budget
            retry_count = self.state_retry_counts.get(self.state.name, 0)
            if retry_count >= self.max_retries:
                self.log("Write error persisted after max retries. Failing.", refined=True)
                self.state = BuildState.FAIL
            else:
                self.log(f"Retrying fix (LLM output was unparsable).", refined=True)
            return
            
        self.artifacts['rtl_path'] = new_path
        # Read back the CLEANED version, not raw LLM output
        with open(new_path, 'r') as f:
            self.artifacts['rtl_code'] = f.read()
        # Loop stays in RTL_FIX to re-check syntax

    # _try_autonomous_sv_fix removed (Verilator supports SV natively)

    def do_verification(self):
        # 1. Generate Testbench (Only on first run or if missing)
        # 2. Run Sim
        # 3. Analyze Results
        
        # 1. Generate Testbench (Only if missing)
        # We reuse existing TB to ensure consistent verification targets
        tb_exists = 'tb_path' in self.artifacts and os.path.exists(self.artifacts['tb_path'])
        
        if not tb_exists:
            # Check if we have a golden testbench from template matching
            if self.artifacts.get('golden_tb'):
                self.log("Using Golden Reference Testbench (pre-verified).", refined=True)
                tb_code = self.artifacts['golden_tb']
                # Replace template module name with actual design name
                template_name = self.artifacts.get('golden_template', 'counter')
                tb_code = tb_code.replace(f'{template_name}_tb', f'{self.name}_tb')
                tb_code = tb_code.replace(template_name, self.name)
                self.logger.info(f"GOLDEN TESTBENCH:\n{tb_code}")
                tb_path = write_verilog(self.name, tb_code, is_testbench=True)
                self.artifacts['tb_path'] = tb_path
            else:
                self.log("Generating Testbench...", refined=True)
                tb_agent = get_testbench_agent(self.llm, f"Verify {self.name}", verbose=self.verbose, strategy=self.strategy.name)
                
                tb_strategy_prompt = self._get_tb_strategy_prompt()
                regen_context = self.artifacts.pop("tb_regen_context", "")
                
                # --- Extract module port signature from RTL ---
                # This prevents the most common TB failure: port name mismatches
                rtl_code = self.artifacts['rtl_code']
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
- Drive clk with: initial clk = 0; always #5 clk = ~clk;
- Assert rst_n low for 50ns, then release
- Print "TEST PASSED" on success, "TEST FAILED" on failure
- End with $finish
- Do NOT invent ports that aren't in the MODULE INTERFACE above
""",
                    expected_output="SystemVerilog Testbench",
                    agent=tb_agent
                )
                
                with console.status("[bold magenta]Generating Testbench...[/bold magenta]"):
                    try:
                        tb_code = self._kickoff_with_timeout(
                            agents=[tb_agent],
                            tasks=[tb_task],
                            timeout_s=self.tb_generation_timeout_s,
                        )
                    except Exception as e:
                        self.log(f"TB generation stalled/failed ({e}). Using deterministic fallback TB.", refined=True)
                        self.logger.info(f"TB GENERATION FALLBACK: {e}")
                        tb_code = self._deterministic_tb_fallback(self.artifacts.get("rtl_code", ""))

                if "module" not in tb_code or "endmodule" not in tb_code:
                    self.log("TB generation returned invalid code. Using deterministic fallback TB.", refined=True)
                    tb_code = self._deterministic_tb_fallback(self.artifacts.get("rtl_code", ""))
                self.logger.info(f"GENERATED TESTBENCH:\n{tb_code}")
                
                tb_path = write_verilog(self.name, tb_code, is_testbench=True)
                if isinstance(tb_path, str) and tb_path.startswith("Error:"):
                    self.log(f"TB write failed ({tb_path}). Regenerating deterministic fallback TB.", refined=True)
                    tb_code = self._deterministic_tb_fallback(self.artifacts.get("rtl_code", ""))
                    tb_path = write_verilog(self.name, tb_code, is_testbench=True)
                    if isinstance(tb_path, str) and tb_path.startswith("Error:"):
                        self.log(f"Fallback TB write failed: {tb_path}", refined=True)
                        self.state = BuildState.FAIL
                        return
                self.artifacts['tb_path'] = tb_path
        else:
            self.log(f"Verifying with existing Testbench (Attempt {self.retry_count}).", refined=True)
            # Verify file exists
            if not os.path.exists(self.artifacts['tb_path']):
                 self.log("Testbench file missing! Forcing regeneration.", refined=True)
                 del self.artifacts['tb_path']  # Remove stale path to trigger regen
                 return  # State machine loop will re-enter do_verification()
            
            # Read current TB for context in case of next error
            with open(self.artifacts['tb_path'], 'r') as f:
                tb_code = f.read()

        # Ensure tb_code is available for error analysis context
        if 'tb_code' not in locals():
             # It should be there from generation or reading above
             with open(self.artifacts['tb_path'], 'r') as f:
                tb_code = f.read()

        # -----------------------------
        # TB gate stack: static -> compile
        # -----------------------------
        static_ok, static_report = run_tb_static_contract_check(tb_code, self.strategy.name)
        if self.artifacts.get("golden_template"):
            # Golden reference TBs may be procedural; ignore SV-class-only requirements.
            filtered = []
            for issue in static_report.get("issues", []):
                if issue.get("code") in {"missing_transaction_class", "missing_flow_classes"}:
                    continue
                filtered.append(issue)
            static_report["issues"] = filtered
            static_report["issue_codes"] = sorted({x.get("code", "") for x in filtered if x.get("code")})
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
            self.log("TB static gate warnings in relaxed mode; continuing to compile gate.", refined=True)

        compile_ok, compile_report = run_tb_compile_gate(
            self.name,
            self.artifacts.get("tb_path", ""),
            self.artifacts.get("rtl_path", f"{OPENLANE_ROOT}/designs/{self.name}/src/{self.name}.v"),
        )
        compile_path = self._write_tb_diagnostic_artifact("tb_compile_gate", compile_report)
        self.logger.info(f"TB COMPILE GATE ({'PASS' if compile_ok else 'FAIL'}): {compile_report}")
        self.log(
            f"TB compile gate: {'PASS' if compile_ok else 'FAIL'} (diag: {os.path.basename(compile_path)})",
            refined=True,
        )

        if not compile_ok:
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
        with console.status("[bold magenta]Running Simulation...[/bold magenta]"):
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
                     self.log("Non-interactive session: auto-proceeding after simulation pass.", refined=True)
                     self.transition(BuildState.FORMAL_VERIFY)
                 else:
                     console.print()
                     if typer.confirm("Simulation Passed. Proceed to OpenLane Hardening (takes 10-30 mins)?", default=True):
                         self.transition(BuildState.FORMAL_VERIFY)
                     else:
                         self.log("Skipping Hardening (User Cancelled).", refined=True)
                         self.transition(BuildState.FORMAL_VERIFY)
        else:
            output_for_llm = self._condense_failure_log(output, kind="timing")
            if self._record_failure_fingerprint(output_for_llm):
                self.log("Detected repeated simulation failure fingerprint. Failing closed.", refined=True)
                self.state = BuildState.FAIL
                return
            self.retry_count += 1
            if self.retry_count > self.max_retries:
                 self.log(f"Max Sim Retries ({self.max_retries}) Exceeded. Simulation Failed.", refined=True)
                 self.state = BuildState.FAIL
                 return
            
            self.log(f"Sim Failed (Attempt {self.retry_count}). Check log.", refined=True)
            
            # --- AUTONOMOUS FIX: Try to fix compilation errors without LLM ---
            # Auto-fixes removed (Verilator supports SV natively)
            
            # --- LLM ERROR ANALYSIS: Multi-class structured diagnosis ---
            analyst = get_error_analyst_agent(self.llm, verbose=self.verbose)
            analysis_task = Task(
                description=f'''Analyze this Verification Failure for "{self.name}".

BUILD CONTEXT:
{self._build_llm_context(include_rtl=False)}

ERROR LOG:
{output_for_llm}

CURRENT TESTBENCH (first 3000 chars):
{tb_code[:3000]}

Classify the failure as ONE of:
A) TESTBENCH_SYNTAX — TB compilation/syntax error (missing semicolons, undeclared signals, class errors)
B) RTL_LOGIC_BUG — Functional error in RTL design (wrong state transitions, bad arithmetic, logic errors)
C) PORT_MISMATCH — TB and RTL have incompatible port names, widths, or missing connections
D) TIMING_RACE — Clock/reset timing issue in TB stimulus (setup/hold violations, race conditions)
E) ARCHITECTURAL — Design spec is ambiguous, contradictory, or fundamentally flawed

Reply in this EXACT format (3 lines):
CLASS: <letter A-E>
ROOT_CAUSE: <1-line description of the specific problem>
FIX_HINT: <specific suggestion for how to fix it>''',
                expected_output='Structured failure classification with CLASS, ROOT_CAUSE, and FIX_HINT',
                agent=analyst
            )
            
            with console.status("[bold red]Analyzing Failure (Multi-Class)...[/bold red]"):
                analysis = str(Crew(agents=[analyst], tasks=[analysis_task]).kickoff()).strip()
            
            self.logger.info(f"FAILURE ANALYSIS:\n{analysis}")
            
            # Parse structured response
            failure_class = "A"  # default fallback
            root_cause = ""
            fix_hint = ""
            for line in analysis.split("\n"):
                line_stripped = line.strip()
                if line_stripped.startswith("CLASS:"):
                    letter = line_stripped.replace("CLASS:", "").strip().upper()
                    if letter and letter[0] in "ABCDE":
                        failure_class = letter[0]
                elif line_stripped.startswith("ROOT_CAUSE:"):
                    root_cause = line_stripped.replace("ROOT_CAUSE:", "").strip()
                elif line_stripped.startswith("FIX_HINT:"):
                    fix_hint = line_stripped.replace("FIX_HINT:", "").strip()
            
            self.log(
                f"Diagnosis: CLASS={failure_class} | ROOT_CAUSE={root_cause[:100]} | FIX_HINT={fix_hint[:100]}",
                refined=True,
            )
            
            # --- Route to fix path based on failure class ---
            
            if failure_class == "C":
                # PORT_MISMATCH — deterministic auto-fix: regenerate TB from RTL ports
                self.log("Port mismatch detected. Regenerating TB from RTL ports.", refined=True)
                fallback_tb = self._deterministic_tb_fallback(self.artifacts.get("rtl_code", ""))
                path = write_verilog(self.name, fallback_tb, is_testbench=True)
                if isinstance(path, str) and not path.startswith("Error:"):
                    self.artifacts['tb_path'] = path
                return
            
            if failure_class == "D":
                # TIMING_RACE — deterministic auto-fix: progressively extend reset + add stabilization
                self.log("Timing race detected. Applying reset timing fix to TB.", refined=True)
                fixed_tb = tb_code.replace("#20;", "#100;").replace("#50;", "#200;")
                # Progressive repeat escalation: 5→20→50→100
                for old_rep, new_rep in [("repeat (5)", "repeat (20)"), ("repeat (20)", "repeat (50)"), ("repeat (50)", "repeat (100)")]:
                    if old_rep in fixed_tb:
                        fixed_tb = fixed_tb.replace(old_rep, new_rep)
                        break
                # Add post-reset stabilization: insert wait cycles after reset de-assertion
                if "reset_phase" in fixed_tb and "// post-reset-stab" not in fixed_tb:
                    # After rst_n = 1'b1 (or 1'b0 for active-high), add stabilization
                    for deassert in ["vif.rst_n = 1'b1;", "vif.reset = 1'b0;"]:
                        if deassert in fixed_tb:
                            fixed_tb = fixed_tb.replace(
                                deassert,
                                f"{deassert}\n    repeat (3) @(posedge vif.clk); // post-reset-stab"
                            )
                            break
                if fixed_tb != tb_code:
                    path = write_verilog(self.name, fixed_tb, is_testbench=True)
                    if isinstance(path, str) and not path.startswith("Error:"):
                        self.artifacts['tb_path'] = path
                    return
                # If auto-fix didn't change anything, fall through to LLM fix
                self.log("Timing race auto-fix exhausted. Falling through to LLM fix.", refined=True)
            
            if failure_class == "E":
                # ARCHITECTURAL — spec is flawed, go back to SPEC stage
                self.log(f"Architectural issue detected: {root_cause}. Regenerating spec.", refined=True)
                self.artifacts["logic_decoupling_hint"] = f"Previous build failed due to: {root_cause}. {fix_hint}"
                self.transition(BuildState.SPEC)
                return
            
            # Classes A (TB syntax) and B (RTL logic) — use LLM fix
            is_tb_issue = failure_class == "A"
            
            if is_tb_issue:
                self.log("Analyst identified Testbench Error. Fixing TB...", refined=True)
                fixer = get_testbench_agent(self.llm, f"Fix TB for {self.name}", verbose=self.verbose)
                port_info = self._extract_module_interface(self.artifacts['rtl_code'])
                fix_prompt = f"""Fix the Testbench logic/syntax.

DIAGNOSIS:
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
{self.artifacts['rtl_code']}
```

PREVIOUS ATTEMPTS:
{self._format_failure_history()}

CRITICAL:
- Return ONLY the fixed Testbench code in ```verilog fences.
- Do NOT invent ports that aren't in the MODULE INTERFACE above.
- Module name of DUT is "{self.name}"
"""
            else:
                self.log("Analyst identified RTL Logic Error. Fixing RTL...", refined=True)
                fixer = get_designer_agent(self.llm, f"Fix RTL for {self.name}", verbose=self.verbose, strategy=self.strategy.name)
                error_lines = [line for line in output.split('\n') if "Error" in line or "fail" in line.lower()]
                error_summary = "\n".join(error_lines)
                
                fix_prompt = f"""Fix the RTL logic to pass verification.

DIAGNOSIS:
ROOT CAUSE: {root_cause}
FIX HINT: {fix_hint}

BUILD CONTEXT:
{self._build_llm_context(include_rtl=False)}

Specific Issues Detected:
{error_summary}

Full Log:
{output_for_llm}

Current RTL:
```verilog
{self.artifacts['rtl_code']}
```

Ref TB:
```verilog
{tb_code}
```

PREVIOUS ATTEMPTS:
{self._format_failure_history()}

CRITICAL:
- Address the ROOT CAUSE and FIX HINT above directly.
- Maintain design intent from the architecture spec.
- Return ONLY the fixed {self.strategy.name} logic in ```verilog fences.
"""
            
            # Execute Fix
            fix_task = Task(
                description=fix_prompt,
                expected_output="Fixed Verilog Code",
                agent=fixer
            )
            
            with console.status("[bold yellow]AI Implementing Fix...[/bold yellow]"):
                 result = Crew(agents=[fixer], tasks=[fix_task]).kickoff()
            fixed_code = str(result)
            self.logger.info(f"FIXED CODE:\n{fixed_code}")
            
            if not is_tb_issue:
                # RTL Fix — write cleaned code and read it back
                path = write_verilog(self.name, fixed_code)
                if isinstance(path, str) and path.startswith("Error:"):
                    self.log(f"File Write Error when fixing RTL logic: {path}", refined=True)
                    self.state = BuildState.FAIL
                    return
                self.artifacts['rtl_path'] = path
                with open(path, 'r') as f:
                    self.artifacts['rtl_code'] = f.read()
                self.log("RTL Updated. Transitioning back to RTL_FIX to verify syntax.", refined=True)
                self.transition(BuildState.RTL_FIX, preserve_retries=True)
                return
            else:
                # TB Fix
                path = write_verilog(self.name, fixed_code, is_testbench=True)
                if isinstance(path, str) and path.startswith("Error:"):
                    self.log(f"File Write Error when fixing TB logic: {path}", refined=True)
                    self.state = BuildState.FAIL
                    return
                self.artifacts['tb_path'] = path
                return


    # ===============================================
    # INDUSTRY-STANDARD STATE HANDLERS
    # ===============================================

    def do_formal_verify(self):
        """Runs formal property verification using SymbiYosys."""
        self.log("Starting Formal Property Verification...", refined=True)
        
        rtl_path = self.artifacts.get('rtl_path')
        if not rtl_path:
            self.log("RTL path not found in artifacts. Skipping formal.", refined=True)
            self.transition(BuildState.COVERAGE_CHECK)
            return
        
        # 1. Generate SVA assertions using LLM
        sva_path = f"{OPENLANE_ROOT}/designs/{self.name}/src/{self.name}_sva.sv"
        
        if not os.path.exists(sva_path):
            self.log("Generating SVA Assertions...", refined=True)
            
            verif_agent = get_verification_agent(self.llm, verbose=self.verbose)
            sva_task = Task(
                description=f"""Generate SystemVerilog Assertions (SVA) for module "{self.name}".
                
                RTL Code:
                ```verilog
                {self.artifacts.get('rtl_code', '')}
                ```
                
                SPECIFICATION:
                {self.artifacts.get('spec', '')}
                
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
                """,
                expected_output='SystemVerilog SVA module',
                agent=verif_agent
            )
            
            with console.status("[bold cyan]AI Generating SVA Assertions...[/bold cyan]"):
                sva_result = str(Crew(agents=[verif_agent], tasks=[sva_task]).kickoff())
            
            self.logger.info(f"GENERATED SVA:\n{sva_result}")
            
            # Write SVA file
            sva_write_path = write_verilog(self.name, sva_result, suffix="_sva", ext=".sv")
            if isinstance(sva_write_path, str) and sva_write_path.startswith("Error:"):
                self.log(f"SVA write failed: {sva_write_path}. Skipping formal.", refined=True)
                self.transition(BuildState.COVERAGE_CHECK)
                return
        
        # 2. Convert SVA to Yosys-compatible format
        try:
            with open(sva_path, 'r') as f:
                sva_content = f.read()
            
            yosys_code = convert_sva_to_yosys(sva_content, self.name)
            if not yosys_code:
                self.artifacts['formal_result'] = "FAIL: unable to translate SVA into Yosys-compatible form"
                self.log("Failed to convert generated SVA to Yosys-compatible assertions.", refined=True)
                if self.strict_gates:
                    self.log("Formal translation failed under strict mode.", refined=True)
                    self.state = BuildState.FAIL
                    return
                self.transition(BuildState.COVERAGE_CHECK)
                return

            preflight_ok, preflight_report = validate_yosys_sby_check(yosys_code)
            formal_diag_path = f"{OPENLANE_ROOT}/designs/{self.name}/src/{self.name}_formal_preflight.json"
            with open(formal_diag_path, "w") as f:
                json.dump(preflight_report, f, indent=2)
            self.artifacts["formal_preflight"] = preflight_report
            self.artifacts["formal_preflight_path"] = formal_diag_path

            if not preflight_ok:
                self.log(f"Formal preflight failed: {preflight_report.get('issue_count', 0)} issue(s).", refined=True)
                self.artifacts['formal_result'] = 'FAIL'
                if self.strict_gates:
                    self.log("Formal preflight issues are blocking under strict mode.", refined=True)
                    self.state = BuildState.FAIL
                    return
                self.transition(BuildState.COVERAGE_CHECK)
                return

            sby_check_path = f"{OPENLANE_ROOT}/designs/{self.name}/src/{self.name}_sby_check.sv"
            with open(sby_check_path, 'w') as f:
                f.write(yosys_code)
            self.log("Yosys-compatible assertions generated.", refined=True)
            
            # 3. Write SBY config and run
            write_sby_config(self.name, use_sby_check=True)
            
            with console.status("[bold cyan]Running Formal Verification (SymbiYosys)...[/bold cyan]"):
                success, result = run_formal_verification(self.name)
            
            self.logger.info(f"FORMAL RESULT:\n{result}")
            
            if success:
                self.log("Formal Verification PASSED!", refined=True)
                self.artifacts['formal_result'] = 'PASS'
            else:
                self.log(f"Formal Verification: {result[:200]}", refined=True)
                self.artifacts['formal_result'] = 'FAIL'
                if self.strict_gates:
                    self.log("Formal verification failed under strict mode.", refined=True)
                    self.state = BuildState.FAIL
                    return
        except Exception as e:
            self.log(f"Formal verification error: {str(e)}.", refined=True)
            self.artifacts['formal_result'] = f'ERROR: {str(e)}'
            if self.strict_gates:
                self.state = BuildState.FAIL
                return
        
        # 4. Run CDC check
        with console.status("[bold cyan]Running CDC Analysis...[/bold cyan]"):
            cdc_clean, cdc_report = run_cdc_check(rtl_path)
        
        self.logger.info(f"CDC REPORT:\n{cdc_report}")
        self.artifacts['cdc_result'] = 'CLEAN' if cdc_clean else 'WARNINGS'
        
        if cdc_clean:
            self.log("CDC Analysis: CLEAN", refined=True)
        else:
            self.log(f"CDC Analysis: warnings found", refined=True)
            if self.strict_gates:
                self.log("CDC issues are blocking under strict mode.", refined=True)
                self.state = BuildState.FAIL
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

        with console.status("[bold magenta]Running Coverage-Instrumented Simulation...[/bold magenta]"):
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
        self.logger.info(f"COVERAGE SIM OUTPUT:\n{sim_output[:8000] if isinstance(sim_output, str) else sim_output}")
        coverage_data["thresholds"] = thresholds
        self.artifacts["coverage"] = coverage_data
        self.artifacts["coverage_backend_used"] = coverage_data.get("backend", self.coverage_backend)
        self.artifacts["coverage_mode"] = coverage_data.get("coverage_mode", "unknown")

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
                self.log("Coverage fallback policy=skip; proceeding without blocking.", refined=True)
                if self.full_signoff:
                    self.transition(BuildState.REGRESSION)
                elif self.skip_openlane:
                    self.transition(BuildState.SUCCESS)
                else:
                    self.transition(BuildState.SDC_GEN)
                return
            if self.strict_gates:
                self.log("Coverage infra failure is blocking under strict mode.", refined=True)
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
            "branch": branch_pct >= 95.0, # Industry Standard Coverage Closure
            "toggle": toggle_pct >= float(thresholds["toggle"]),
            "functional": functional_pct >= float(thresholds["functional"]),
        }
        coverage_pass = all(coverage_checks.values())

        if self.retry_count == 0:
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
        coverage_max_retries = min(self.max_retries, 5) # Increased for closure loop
        if self.retry_count > coverage_max_retries:
            if getattr(self, "best_tb_backup", None) and os.path.exists(self.best_tb_backup):
                self.log(f"Restoring Best Testbench ({self.best_coverage:.1f}%) before proceeding.", refined=True)
                import shutil
                shutil.copy(self.best_tb_backup, self.artifacts["tb_path"])
            if self.strict_gates:
                self.log(f"Coverage below thresholds after {coverage_max_retries} attempts. Failing strict gate.", refined=True)
                self.state = BuildState.FAIL
                return
            self.log(f"Coverage below thresholds after {coverage_max_retries} attempts. Proceeding anyway.", refined=True)
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

        tb_agent = get_testbench_agent(self.llm, f"Improve coverage for {self.name}", verbose=self.verbose, strategy=self.strategy.name)

        improve_prompt = f"""The current testbench for "{self.name}" does not meet coverage thresholds.
        TARGET: Industry Standard >95.0% Branch Coverage.
        Current Coverage Data: {coverage_data}
        
        Current RTL:
        ```verilog
        {self.artifacts.get('rtl_code', '')}
        ```
        
        Current Testbench:
        ```verilog
        {open(self.artifacts['tb_path'], 'r').read() if 'tb_path' in self.artifacts and os.path.exists(self.artifacts.get('tb_path', '')) else 'NOT AVAILABLE'}
        ```
        
        Create an IMPROVED self-checking testbench that:
        1. Achieves >95% branch coverage by hitting all missing branches.
        2. Tests all FSM states (not just happy path)
        3. Exercises all conditional branches (if/else, case)
        3. Tests reset behavior mid-operation
        4. Tests boundary values (max/min inputs)
        5. Includes back-to-back operations
        6. Must print "TEST PASSED" on success
        
        Return ONLY the complete testbench in ```verilog fences.
        """

        improve_task = Task(
            description=improve_prompt,
            expected_output='Improved SystemVerilog Testbench',
            agent=tb_agent
        )

        with console.status("[bold yellow]AI Improving Test Coverage...[/bold yellow]"):
            result = Crew(agents=[tb_agent], tasks=[improve_task]).kickoff()

        improved_tb = str(result)
        self.logger.info(f"IMPROVED TB:\n{improved_tb}")

        tb_path = write_verilog(self.name, improved_tb, is_testbench=True)
        if isinstance(tb_path, str) and not tb_path.startswith("Error:"):
            self.artifacts["tb_path"] = tb_path
        # Loop: state stays at COVERAGE_CHECK, will re-run

    def do_regression(self):
        """Generates and runs multiple directed test scenarios."""
        self.log("Starting Regression Testing...", refined=True)
        
        regression_agent = get_regression_agent(
            self.llm, 
            f"Generate regression tests for {self.name}", 
            verbose=self.verbose
        )
        
        # Generate regression test plan
        regression_task = Task(
            description=f"""Generate 3 directed regression test scenarios for module "{self.name}".
            
            RTL:
            ```verilog
            {self.artifacts.get('rtl_code', '')}
            ```
            
            SPEC:
            {self.artifacts.get('spec', '')}
            
            Create 3 separate self-checking testbenches, each targeting a different scenario:
            1. CORNER CASE TEST - Test with extreme values (max/min/zero/overflow)
            2. RESET STRESS TEST - Apply reset during active operations  
            3. RAPID FIRE TEST - Back-to-back operations with no idle cycles
            
            For each test, output a COMPLETE testbench in a separate ```verilog block.
            Label each block with a comment: // TEST 1: Corner Case, // TEST 2: Reset Stress, // TEST 3: Rapid Fire
            Each test must print "TEST PASSED" on success or "TEST FAILED" on failure.
            Each must use $finish to terminate.
            """,
            expected_output='3 separate Verilog testbench code blocks',
            agent=regression_agent
        )
        
        with console.status("[bold magenta]AI Generating Regression Tests...[/bold magenta]"):
            result = str(Crew(agents=[regression_agent], tasks=[regression_task]).kickoff())
        
        self.logger.info(f"REGRESSION TESTS:\n{result}")
        
        # Parse out individual tests from the LLM output
        import re as regex
        test_blocks = regex.findall(r'```(?:verilog|v)?\s*\n(.*?)```', result, regex.DOTALL)
        
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
            test_name = f"regression_test_{i+1}"
            self.log(f"Running Regression Test {i+1}/{len(test_blocks[:3])}...", refined=True)
            
            # Write test to file
            test_path = write_verilog(self.name, test_code, suffix=f"_{test_name}", ext=".v")
            if isinstance(test_path, str) and test_path.startswith("Error:"):
                test_results.append({"test": test_name, "status": "WRITE_ERROR", "output": test_path})
                all_passed = False
                continue
            
            # Compile and run
            src_dir = f"{OPENLANE_ROOT}/designs/{self.name}/src"
            rtl_file = self.artifacts.get('rtl_path', f"{src_dir}/{self.name}.v")
            sim_out = f"{src_dir}/sim_{test_name}"
            
            try:
                import subprocess
                
                # Detect testbench module name
                tb_module = f"{self.name}_{test_name}"
                try:
                    with open(test_path, 'r') as f:
                        tb_content = f.read()
                    import re as _re
                    m = _re.search(r'module\s+(\w+)', tb_content)
                    if m:
                        tb_module = m.group(1)
                except Exception:
                    pass
                
                sim_obj_dir = f"{src_dir}/obj_dir_{test_name}"
                sim_binary = f"{sim_obj_dir}/V{tb_module}"
                
                compile_result = subprocess.run(
                    ["verilator", "--binary", "--timing",
                     "-Wno-UNUSED", "-Wno-PINMISSING", "-Wno-CASEINCOMPLETE",
                     "-Wno-WIDTHEXPAND", "-Wno-WIDTHTRUNC", "-Wno-LATCH",
                     "-Wno-UNOPTFLAT", "-Wno-BLKANDNBLK",
                     "--top-module", tb_module,
                     "--Mdir", sim_obj_dir,
                     "-o", f"V{tb_module}",
                     rtl_file, test_path],
                    capture_output=True, text=True, timeout=300
                )
                if compile_result.returncode != 0:
                    test_results.append({"test": test_name, "status": "COMPILE_FAIL", "output": compile_result.stderr[:500]})
                    all_passed = False
                    continue
                
                run_result = subprocess.run(
                    [sim_binary],
                    capture_output=True, text=True, timeout=300
                )
                sim_text = (run_result.stdout or "") + ("\n" + run_result.stderr if run_result.stderr else "")
                
                passed = "TEST PASSED" in sim_text
                test_results.append({"test": test_name, "status": "PASS" if passed else "FAIL", "output": sim_text[-500:]})
                if not passed:
                    all_passed = False
                    
            except subprocess.TimeoutExpired:
                test_results.append({"test": test_name, "status": "TIMEOUT", "output": "Timed out"})
                all_passed = False
            except Exception as e:
                test_results.append({"test": test_name, "status": "ERROR", "output": str(e)})
                all_passed = False
        
        # Log results
        self.artifacts['regression_results'] = test_results
        for tr in test_results:
            self.log(f"  Regression {tr['test']}: {tr['status']}", refined=True)
        
        if all_passed:
            self.log(f"All {len(test_results)} regression tests PASSED!", refined=True)
        else:
            passed_count = sum(1 for tr in test_results if tr['status'] == 'PASS')
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
        sdc_agent = get_sdc_agent(self.llm, "Generate Synthesis Design Constraints", self.verbose)
        
        arch_spec = self.artifacts.get('spec', 'No spec generated.')
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
            agent=sdc_agent
        )
        
        with console.status("[bold cyan]Generating Timing Constraints (SDC)...[/bold cyan]"):
            sdc_content = str(Crew(agents=[sdc_agent], tasks=[sdc_task]).kickoff()).strip()
            
            # Clean up potential markdown wrappers created by LLM anyway
            if sdc_content.startswith("```"):
                lines = sdc_content.split("\n")
                if len(lines) > 2:
                    sdc_content = "\n".join(lines[1:-1])
            
            with open(sdc_path, "w") as f:
                f.write(sdc_content)
                
            self.artifacts["sdc_path"] = sdc_path
            self.log(f"SDC Constraints generated: {sdc_path}", refined=True)
            
        self.transition(BuildState.FLOORPLAN)

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
        heuristic_clk = self.artifacts.get("clock_period_override", self.pdk_profile.get("default_clock_period", "10.0"))

        try:
            module_count = len(re.findall(r"\bmodule\b", rtl_code))
            estimator = Agent(
                role='Physical Design Estimator',
                goal='Estimate die area, utilization, and clock period for floorplanning',
                backstory='Senior PD engineer who estimates area from RTL complexity, gate count, and PDK constraints.',
                llm=self.llm,
                verbose=False,
                allow_delegation=False,
            )
            estimate_task = Task(
                description=f"""Estimate floorplan parameters for "{self.name}".

DESIGN METRICS:
- RTL: {line_count} non-blank lines, {module_count} modules, ~{cell_count_est} estimated cells
- PDK: {self.pdk_profile.get('profile', 'sky130')} ({self.pdk_profile.get('pdk', 'sky130A')})
- Previous convergence: {[{'wns': s.wns, 'cong': s.congestion, 'area': s.area_um2} for s in self.convergence_history[-2:]] if self.convergence_history else 'First attempt'}
- Floorplan attempt: {self.floorplan_attempts}

Reply in this EXACT format (4 lines):
DIE_AREA: <integer 200-2000>
UTILIZATION: <integer 30-70>
CLOCK_PERIOD: <float like 10.0>
REASONING: <1-line explanation>""",
                expected_output='Floorplan parameters in structured format',
                agent=estimator,
            )

            with console.status("[bold cyan]LLM Estimating Floorplan...[/bold cyan]"):
                est_result = str(Crew(agents=[estimator], tasks=[estimate_task]).kickoff()).strip()
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
                        llm_die = int(re.search(r"\d+", est_line_s.replace("DIE_AREA:", "")).group())
                        llm_die = max(200, min(2000, llm_die))
                    except (ValueError, AttributeError):
                        pass
                elif est_line_s.startswith("UTILIZATION:"):
                    try:
                        llm_util = int(re.search(r"\d+", est_line_s.replace("UTILIZATION:", "")).group())
                        llm_util = max(30, min(70, llm_util))
                    except (ValueError, AttributeError):
                        pass
                elif est_line_s.startswith("CLOCK_PERIOD:"):
                    try:
                        llm_clk = float(re.search(r"[\d.]+", est_line_s.replace("CLOCK_PERIOD:", "")).group())
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
                self.log(f"LLM floorplan parse failed; using heuristic die={heuristic_die}", refined=True)

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
        sdc_injection = f"set ::env(BASE_SDC_FILE) \"$::env(DESIGN_DIR)/src/{self.name}.sdc\"\n" if "sdc_path" in self.artifacts else ""
        
        with open(floorplan_tcl, "w") as f:
            f.write(
                f"set ::env(DESIGN_NAME) \"{self.name}\"\n"
                f"set ::env(PDK) \"{self.pdk_profile.get('pdk', PDK)}\"\n"
                f"set ::env(STD_CELL_LIBRARY) \"{self.pdk_profile.get('std_cell_library', 'sky130_fd_sc_hd')}\"\n"
                f"set ::env(VERILOG_FILES) [glob $::env(DESIGN_DIR)/src/{self.name}.v]\n"
                f"{sdc_injection}"
                "set ::env(FP_SIZING) \"absolute\"\n"
                f"set ::env(DIE_AREA) \"0 0 {die} {die}\"\n"
                f"set ::env(FP_CORE_UTIL) {util}\n"
                f"set ::env(PL_TARGET_DENSITY) {util / 100 + 0.05:.2f}\n"
                f"set ::env(CLOCK_PERIOD) \"{clock_period}\"\n"
                f"set ::env(CLOCK_PORT) \"clk\"\n"
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
        self.log(f"Strategy pivot triggered ({self.pivot_count}/{self.max_pivots}): {reason}", refined=True)
        if self.pivot_count > self.max_pivots:
            self.log("Pivot budget exceeded. Failing closed.", refined=True)
            self.state = BuildState.FAIL
            return

        stage = self.strategy_pivot_stage % 3
        self.strategy_pivot_stage += 1

        if stage == 0:
            old = float(self.artifacts.get("clock_period_override", self.pdk_profile.get("default_clock_period", "10.0")))
            new = round(old * 1.10, 2)
            self.artifacts["clock_period_override"] = str(new)
            self.log(f"Pivot step: timing constraint tune ({old}ns -> {new}ns).", refined=True)
            self.transition(BuildState.FLOORPLAN, preserve_retries=True)
            return

        if stage == 1:
            old_scale = float(self.artifacts.get("area_scale", 1.0))
            new_scale = round(old_scale * 1.15, 3)
            self.artifacts["area_scale"] = new_scale
            self.log(f"Pivot step: area expansion ({old_scale}x -> {new_scale}x).", refined=True)
            self.transition(BuildState.FLOORPLAN, preserve_retries=True)
            return

        # stage 2: logic decoupling prompt
        self.artifacts["logic_decoupling_hint"] = (
            "Apply register slicing / pipeline decoupling on critical path; "
            "reduce combinational depth while preserving behavior."
        )
        self.log("Pivot step: requesting logic decoupling (register slicing) in RTL regen.", refined=True)
        self.transition(BuildState.RTL_GEN, preserve_retries=True)

    def do_convergence_review(self):
        """Assess congestion/timing convergence and prevent futile loops."""
        self.log("Assessing convergence (timing + congestion + PPA)...", refined=True)

        congestion = parse_congestion_metrics(self.name, run_tag=self.artifacts.get("run_tag", "agentrun"))
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
                role='PPA Convergence Analyst',
                goal='Decide whether to continue, pivot, or accept current PPA metrics',
                backstory='Expert in timing closure, congestion mitigation, and PPA trade-offs for ASIC designs.',
                llm=self.llm,
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
- Area expansions done: {self.artifacts.get('area_expansions', 0)}

DECIDE one of:
A) CONVERGED — Metrics are acceptable (WNS >= 0, congestion within threshold), proceed to signoff
B) TUNE_TIMING — Relax clock period by 10% and re-run hardening
C) EXPAND_AREA — Increase die area by 15% and re-run hardening
D) DECOUPLE_LOGIC — Need RTL pipeline restructuring to reduce congestion
E) FAIL — No convergence possible within remaining budget

Reply in this EXACT format (2 lines):
DECISION: <letter A-E>
REASONING: <1-line explanation>""",
                expected_output='Convergence decision with DECISION and REASONING',
                agent=conv_analyst,
            )

            with console.status("[bold cyan]LLM Analyzing Convergence...[/bold cyan]"):
                conv_result = str(Crew(agents=[conv_analyst], tasks=[convergence_task]).kickoff()).strip()
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
                self.log(f"LLM convergence decision: {decision} ({conv_reasoning})", refined=True)
                if decision == "A":
                    self.transition(BuildState.SIGNOFF)
                    return
                elif decision == "B":
                    old = float(self.artifacts.get("clock_period_override", self.pdk_profile.get("default_clock_period", "10.0")))
                    new = round(old * 1.10, 2)
                    self.artifacts["clock_period_override"] = str(new)
                    self.log(f"LLM-directed: timing constraint tune ({old}ns -> {new}ns).", refined=True)
                    self.transition(BuildState.FLOORPLAN, preserve_retries=True)
                    return
                elif decision == "C":
                    old_scale = float(self.artifacts.get("area_scale", 1.0))
                    new_scale = round(old_scale * 1.15, 3)
                    self.artifacts["area_scale"] = new_scale
                    self.log(f"LLM-directed: area expansion ({old_scale}x -> {new_scale}x).", refined=True)
                    self.transition(BuildState.FLOORPLAN, preserve_retries=True)
                    return
                elif decision == "D":
                    self.artifacts["logic_decoupling_hint"] = (
                        f"LLM convergence analysis: {conv_reasoning}. "
                        "Apply register slicing / pipeline decoupling on critical path."
                    )
                    self.log("LLM-directed: requesting logic decoupling in RTL regen.", refined=True)
                    self.transition(BuildState.RTL_GEN, preserve_retries=True)
                    return
                else:  # E = FAIL
                    self.log(f"LLM convergence verdict: FAIL ({conv_reasoning})", refined=True)
                    self.state = BuildState.FAIL
                    return
            else:
                self.log("LLM convergence parse failed; falling back to heuristic.", refined=True)

        except Exception as e:
            self.log(f"LLM convergence analysis failed ({e}); falling back to heuristic.", refined=True)

        # --- Hardcoded fallback (original logic) ---
        if cong_pct > self.congestion_threshold:
            self.log(
                f"Congestion {cong_pct:.2f}% exceeds threshold {self.congestion_threshold:.2f}%.",
                refined=True,
            )
            area_expansions = int(self.artifacts.get("area_expansions", 0))
            if area_expansions < 2:
                self.artifacts["area_expansions"] = area_expansions + 1
                self.artifacts["area_scale"] = round(float(self.artifacts.get("area_scale", 1.0)) * 1.15, 3)
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

        self.transition(BuildState.SIGNOFF)

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
        rtl_code = self.artifacts.get('rtl_code', '')
        clock_port = "clk" # Default
        
        # Regex to find clock port: input ... clk ... ;
        # Matches: input clk, input wire clk, input logic clk
        clk_match = re.search(r'input\s+(?:wire\s+|logic\s+)?(\w*(?:clk|clock|sclk|aclk)\w*)\s*[;,\)]', rtl_code, re.IGNORECASE)
        if clk_match:
            clock_port = clk_match.group(1)
            self.log(f"Detected Clock Port: {clock_port}", refined=True)
        
# Modern OpenLane Config Template
        # Note: We use GRT_ADJUSTMENT instead of deprecated GLB_RT_ADJUSTMENT
        
        std_cell_lib = self.pdk_profile.get("std_cell_library", "sky130_fd_sc_hd")
        pdk_name = self.pdk_profile.get("pdk", PDK)
        clock_period = str(self.artifacts.get("clock_period_override", self.pdk_profile.get("default_clock_period", "10.0")))
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
        with console.status("[bold blue]Hardening Layout (OpenLane)...[/bold blue]"):
            success, result = run_openlane(
                self.name,
                background=False,
                run_tag=run_tag,
                floorplan_tcl=floorplan_tcl,
                pdk_name=pdk_name,
            )
            
        if success:
            self.artifacts['gds'] = result
            self.artifacts['run_tag'] = run_tag
            self.log(f"GDSII generated: {result}", refined=True)
            self.transition(BuildState.CONVERGENCE_REVIEW)
        else:
            self.log(f"Hardening Failed: {result}", refined=True)
            self.state = BuildState.FAIL

    def do_signoff(self):
        """Performs full fabrication-readiness signoff: DRC/LVS, timing closure, power analysis."""
        self.log("Running Fabrication Readiness Signoff...", refined=True)
        fab_ready = True
        run_tag = self.artifacts.get("run_tag", "agentrun")
        gate_netlist = f"{OPENLANE_ROOT}/designs/{self.name}/runs/{run_tag}/results/final/verilog/gl/{self.name}.v"
        rtl_path = self.artifacts.get("rtl_path", f"{OPENLANE_ROOT}/designs/{self.name}/src/{self.name}.v")
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
        with console.status("[bold blue]Checking DRC/LVS Reports...[/bold blue]"):
            signoff_pass, signoff_details = parse_drc_lvs_reports(self.name)
        
        self.logger.info(f"SIGNOFF DETAILS:\n{signoff_details}")
        self.artifacts['signoff'] = signoff_details
        
        drc_v = signoff_details.get('drc_violations', -1)
        lvs_e = signoff_details.get('lvs_errors', -1)
        ant_v = signoff_details.get('antenna_violations', -1)
        
        self.log(f"DRC: {drc_v} violations | LVS: {lvs_e} errors | Antenna: {ant_v}", refined=True)
        
        if not signoff_pass:
            fab_ready = False
        
        # ── 2. TIMING CLOSURE (multi-corner STA) ──
        self.log("Checking Timing Closure (all corners)...", refined=True)
        with console.status("[bold blue]Parsing STA Reports...[/bold blue]"):
            sta = parse_sta_signoff(self.name)
        
        self.logger.info(f"STA RESULTS: {sta}")
        self.artifacts['sta_signoff'] = sta
        
        if sta.get('error'):
            self.log(f"STA: {sta['error']}", refined=True)
            fab_ready = False
        else:
            for c in sta['corners']:
                status = "✓" if (c['setup_slack'] >= 0 and c['hold_slack'] >= 0) else "✗"
                self.log(f"  {status} {c['name']}: setup={c['setup_slack']:.2f}ns hold={c['hold_slack']:.2f}ns", refined=True)
            
            if sta['timing_met']:
                self.log(f"Timing Closure: MET ✓ (worst setup={sta['worst_setup']:.2f}ns, hold={sta['worst_hold']:.2f}ns)", refined=True)
            else:
                self.log(f"Timing Closure: FAILED ✗ (worst setup={sta['worst_setup']:.2f}ns, hold={sta['worst_hold']:.2f}ns)", refined=True)
                fab_ready = False
        
        # ── 3. POWER & IR-DROP ──
        self.log("Analyzing Power & IR-Drop...", refined=True)
        with console.status("[bold blue]Parsing Power Reports...[/bold blue]"):
            power = parse_power_signoff(self.name)
        
        self.logger.info(f"POWER RESULTS: {power}")
        self.artifacts['power_signoff'] = power
        
        if power['total_power_w'] > 0:
            power_mw = power['total_power_w'] * 1000
            self.log(f"Total Power: {power_mw:.3f} mW (Internal {power['internal_power_w']*1000:.3f} + Switching {power['switching_power_w']*1000:.3f} + Leakage {power['leakage_power_w']*1e6:.3f} µW)", refined=True)
            self.log(f"Breakdown: Sequential {power['sequential_pct']:.1f}% | Combinational {power['combinational_pct']:.1f}%", refined=True)
            
            if power['irdrop_max_vpwr'] > 0 or power['irdrop_max_vgnd'] > 0:
                self.log(f"IR-Drop: VPWR={power['irdrop_max_vpwr']*1000:.1f}mV  VGND={power['irdrop_max_vgnd']*1000:.1f}mV", refined=True)
                if not power['power_ok']:
                    self.log("IR-Drop: EXCEEDS 5% VDD THRESHOLD ✗", refined=True)
                    fab_ready = False
                else:
                    self.log("IR-Drop: Within limits ✓", refined=True)
        
        # 4. Physical Metrics
        with console.status("[bold blue]Analyzing Physical Metrics...[/bold blue]"):
            metrics, metrics_status = check_physical_metrics(self.name)
        
        if metrics:
            self.artifacts['metrics'] = metrics
            self.log(f"Area: {metrics.get('chip_area_um2', 'N/A')} µm²", refined=True)
        
        # 5. Documentation
        self.log("Generating Design Documentation (AI-Powered)...", refined=True)
        
        doc_agent = get_doc_agent(self.llm, verbose=self.verbose)
        
        doc_task = Task(
            description=f"""Generate a comprehensive Datasheet (Markdown) for "{self.name}".
            
            1. **Architecture Spec**:
            {self.artifacts.get('spec', 'N/A')}
            
            2. **Physical Metrics**:
            {metrics if metrics else 'N/A'}
            
            3. **RTL Source Code**:
            ```verilog
            {self.artifacts.get('rtl_code', '')[:15000]} 
            // ... truncated if too long
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
            expected_output='Markdown Datasheet',
            agent=doc_agent
        )
        
        with console.status("[bold cyan]AI Writing Datasheet...[/bold cyan]"):
            doc_content = str(Crew(agents=[doc_agent], tasks=[doc_task]).kickoff())
        
        # Save to file
        doc_path = f"{OPENLANE_ROOT}/designs/{self.name}/{self.name}_datasheet.md"
        try:
            with open(doc_path, 'w') as f:
                f.write(doc_content)
            self.artifacts['datasheet'] = doc_path
            self.log(f"Datasheet generated: {doc_path}", refined=True)
        except Exception as e:
            self.log(f"Error writing datasheet: {e}", refined=True)

        try:
            self._write_ip_manifest()
        except Exception as e:
            self.log(f"IP manifest generation warning: {e}", refined=True)

        if self.strict_gates:
            if self.artifacts.get("formal_result", "").startswith("FAIL") or str(self.artifacts.get("formal_result", "")).startswith("ERROR"):
                fab_ready = False
            cov = self.artifacts.get("coverage", {})
            if cov:
                cov_thresholds = dict(self.coverage_thresholds)
                cov_thresholds["line"] = max(float(cov_thresholds.get("line", 85.0)), float(self.min_coverage))
                cov_checks = {
                    "line": float(cov.get("line_pct", 0.0)) >= cov_thresholds["line"],
                    "branch": float(cov.get("branch_pct", 0.0)) >= cov_thresholds["branch"],
                    "toggle": float(cov.get("toggle_pct", 0.0)) >= cov_thresholds["toggle"],
                    "functional": float(cov.get("functional_pct", 0.0)) >= cov_thresholds["functional"],
                }
                if cov.get("coverage_mode") == "skipped":
                    pass
                elif cov.get("infra_failure", False):
                    if self.coverage_fallback_policy != "skip":
                        fab_ready = False
                elif not all(cov_checks.values()):
                    fab_ready = False
        
        # FINAL VERDICT
        timing_status = "MET" if sta.get('timing_met') else "FAILED" if not sta.get('error') else "N/A"
        power_status = f"{power['total_power_w']*1000:.3f} mW" if power['total_power_w'] > 0 else "N/A"
        irdrop_status = "OK" if power.get('power_ok') else "FAIL (>5% VDD)"
        
        console.print()
        console.print(Panel(
            f"[bold cyan]Fabrication Readiness Report[/bold cyan]\n\n"
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
            f"{'[bold green]FABRICATION READY ✓[/]' if fab_ready else '[bold red]NOT FABRICATION READY ✗[/]'}",
            title="📋 Signoff Report"
        ))

        if fab_ready:
            self.log("✅ SIGNOFF PASSED (Timing Closed, DRC Clean)", refined=True)
            self.artifacts['signoff_result'] = 'PASS'
            self.transition(BuildState.SUCCESS)
        else:
            # --- LLM-driven signoff failure analysis before ECO ---
            if self.strict_gates and self.eco_attempts < 2:
                try:
                    signoff_analyst = Agent(
                        role='Signoff Failure Analyst',
                        goal='Identify root cause of signoff failure and recommend fix path',
                        backstory='Expert in DRC/LVS debugging, timing closure, IR-drop mitigation, and ECO strategies.',
                        llm=self.llm,
                        verbose=False,
                        allow_delegation=False,
                    )
                    signoff_analysis_task = Task(
                        description=f"""Signoff failed for "{self.name}". Analyze the failure:

DRC: {drc_v} violations | LVS: {lvs_e} errors | Antenna: {ant_v}
Timing: {timing_status} (WNS={sta.get('worst_setup', 0):.2f}ns)
Power: {power_status} | IR-Drop: {irdrop_status}
LEC: {self.artifacts.get('lec_result', 'N/A')}

CONVERGENCE HISTORY:
{json.dumps([asdict(s) for s in self.convergence_history[-3:]], indent=2) if self.convergence_history else 'N/A'}

What is the most likely root cause and what ECO strategy should we use?
Reply in this EXACT format (2 lines):
ROOT_CAUSE: <description of the primary issue>
FIX: <one of: GATE_ECO, RTL_PATCH, AREA_EXPAND, TIMING_RELAX>""",
                        expected_output='Root cause and fix recommendation',
                        agent=signoff_analyst,
                    )

                    with console.status("[bold red]LLM Analyzing Signoff Failure...[/bold red]"):
                        signoff_analysis = str(Crew(agents=[signoff_analyst], tasks=[signoff_analysis_task]).kickoff()).strip()
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
                            if so_fix_val in {"GATE_ECO", "RTL_PATCH", "AREA_EXPAND", "TIMING_RELAX"}:
                                so_fix = so_fix_val

                    self.log(f"Signoff diagnosis: {so_root_cause} -> {so_fix}", refined=True)
                    self.artifacts["signoff_failure_analysis"] = {
                        "root_cause": so_root_cause,
                        "recommended_fix": so_fix,
                    }

                    if so_fix == "AREA_EXPAND":
                        self.artifacts["area_scale"] = round(float(self.artifacts.get("area_scale", 1.0)) * 1.15, 3)
                        self.log("LLM recommends area expansion. Re-running floorplan.", refined=True)
                        self.transition(BuildState.FLOORPLAN, preserve_retries=True)
                        return
                    elif so_fix == "TIMING_RELAX":
                        old_clk = float(self.artifacts.get("clock_period_override", self.pdk_profile.get("default_clock_period", "10.0")))
                        new_clk = round(old_clk * 1.10, 2)
                        self.artifacts["clock_period_override"] = str(new_clk)
                        self.log(f"LLM recommends timing relaxation ({old_clk}ns -> {new_clk}ns). Re-running.", refined=True)
                        self.transition(BuildState.FLOORPLAN, preserve_retries=True)
                        return
                    else:
                        # GATE_ECO or RTL_PATCH -> route to ECO stage
                        self.log("Signoff failed. Triggering ECO patch stage.", refined=True)
                        self.transition(BuildState.ECO_PATCH, preserve_retries=True)
                        return

                except Exception as e:
                    self.log(f"Signoff analysis failed ({e}); falling back to ECO.", refined=True)
                    self.transition(BuildState.ECO_PATCH, preserve_retries=True)
                    return

            self.log("❌ SIGNOFF FAILED (Violations Found)", refined=True)
            self.artifacts['signoff_result'] = 'FAIL'
            self.errors.append("Signoff failed (see report).")
            self.state = BuildState.FAIL
