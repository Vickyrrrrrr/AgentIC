import enum
import time
import logging
import os
import re
import hashlib
import json
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List
from rich.console import Console
from rich.panel import Panel
from crewai import Agent, Task, Crew, LLM

# Local imports
from .config import OPENLANE_ROOT, LLM_MODEL, LLM_BASE_URL, LLM_API_KEY, PDK, WORKSPACE_ROOT, get_pdk_profile
from .agents.designer import get_designer_agent
from .agents.testbench_designer import get_testbench_agent
from .agents.verifier import get_verification_agent, get_error_analyst_agent, get_regression_agent
from .agents.doc_agent import get_doc_agent
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
    check_physical_metrics,
    run_cdc_check,
    generate_design_doc,
    convert_sva_to_yosys,
    startup_self_check,
    run_semantic_rigor_check,
    parse_eda_log_summary,
    parse_congestion_metrics,
    run_eqy_lec,
    apply_eco_patch,
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
    ):
        self.name = name
        self.desc = desc
        self.llm = llm
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
        self.artifacts = {}  # Store paths to gathered files
        self.history = []    # Log of state transitions and errors
        self.errors = []     # List of error messages

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
            
        # Console Log - Only if refined/important
        if refined or self.verbose: 
             # We actually want to reduce verbosity based on User Request.
             # Only print if 'refined' is True (High level updates)
             if refined:
                style = "bold green"
                console.print(f"[{style}][{self.state.name}][/] {message}")

    def transition(self, new_state: BuildState, preserve_retries: bool = False):
        self.log(f"Transitioning: {self.state.name} -> {new_state.name}")
        self.state = new_state
        if not preserve_retries:
            self.retry_count = 0  # Reset retries on state change
            self.state_retry_counts[new_state.name] = 0

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

    def run(self):
        """Main execution loop."""
        self.log(f"Starting Build Process for '{self.name}' using {self.strategy.value}")
        
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
        # 1. Architecture Spec
        arch_agent = Agent(
            role='Chief System Architect',
            goal=f'Define a robust micro-architecture for {self.name}',
            backstory='Veteran Silicon Architect. Defines interfaces, FSMs, and datapaths.',
            llm=self.llm,
            verbose=self.verbose
        )
        
        spec_task = Task(
            description=f"""Create a DETAILED Micro-Architecture Specification (MAS) for "{self.name}".
Requirement: {self.desc}
Outputs:
1. IO Interface (clk, rst_n, etc)
2. Internal Registers & FSM States
3. Design Intent (Pipelining, Complexity)
""",
            expected_output='Markdown Specification',
            agent=arch_agent
        )
        
        with console.status("[bold cyan]Architecting Chip Specification...[/bold cyan]"):
            result = Crew(agents=[arch_agent], tasks=[spec_task]).kickoff()
        
        self.artifacts['spec'] = str(result)
        self.log("Architecture Plan Generated")
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
        inputs = []
        outputs = []
        inouts = []
        
        for match in re.finditer(
            r'\b(input|output|inout)\s+(reg|wire|logic)?\s*(?:(signed)\s*)?(\[[\d:]+\])?\s*(\w+)',
            rtl_code
        ):
            direction, _, _, width, name = match.groups()
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
1. Module name must be "{self.name}"
2. Async active-low reset `rst_n`
3. Flatten ports (no multi-dim arrays on ports)
4. **IMPLEMENT EVERYTHING**: Do not leave any logic as "to be implemented" or "simplified".
5. **VERIFY CONNECTIVITY**: Ensure all sub-modules (if any) are correctly connected.
6. Return code in ```verilog fence
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
        
        # Agents fix syntax
        fix_prompt = f"""Fix Syntax/Lint Errors in "{self.name}".
        
        Error Log:
        {errors_for_llm}
        
        Strategy: {self.strategy.name} (Keep consistency!)
        
        IMPORTANT: The compiler is Verilator 5.0+ (SystemVerilog 2017+).
        - Use modern SystemVerilog features (`logic`, `always_comb`, `always_ff`).
        - Ensure strict 2-state logic handling (reset all registers).
        - Avoid 4-state logic (x/z) reliance as Verilator is 2-state optimized.
        
        Code:
        ```verilog
        {self.artifacts['rtl_code']}
        ```
        """
        
        # Use a fixer agent (can be same designer or specialized)
        fixer = Agent(
            role="Syntax Rectifier",
            goal="Fix Verilog Compilation & Lint Errors",
            backstory="Expert in compiler error messages and Verilator lint warnings.",
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
            self.state = BuildState.FAIL
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
                    result = Crew(agents=[tb_agent], tasks=[tb_task]).kickoff()
                tb_code = str(result)
                self.logger.info(f"GENERATED TESTBENCH:\n{tb_code}")
                
                tb_path = write_verilog(self.name, tb_code, is_testbench=True)
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

        if self.strict_gates:
            tb_ok, tb_issues = self._tb_meets_strict_contract(tb_code, self.strategy)
            if self.artifacts.get("golden_template"):
                # Golden library TBs are pre-verified and may be procedural.
                tb_issues = [i for i in tb_issues if "class" not in i.lower()]
                tb_ok = len(tb_issues) == 0
            if not tb_ok:
                self.log(f"Testbench strict gate failed: {tb_issues}", refined=True)
                self.logger.info(f"TB STRICT GATE FAILURE: {tb_issues}")
                self.retry_count += 1
                if self.retry_count > self.max_retries:
                    self.log("Max TB generation retries exceeded.", refined=True)
                    self.state = BuildState.FAIL
                    return
                # Force regeneration path on next loop
                if 'tb_path' in self.artifacts:
                    try:
                        os.remove(self.artifacts['tb_path'])
                    except OSError:
                        pass
                    self.artifacts.pop('tb_path', None)
                return
        
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
            
            # --- LLM ERROR ANALYSIS (only if autonomous fix didn't work) ---
            analyst = get_error_analyst_agent(self.llm, verbose=self.verbose)
            analysis_task = Task(
                description=f'''Analyze this Verification Failure.
Error Log:
{output_for_llm}
Is this a:
A) TESTBENCH_ERROR (Syntax, $monitor usage, race condition, compilation fail)
B) RTL_LOGIC_ERROR (Mismatch, Wrong State, Functional Failure)
Reply with ONLY "A" or "B".''',
                expected_output='Single letter A or B',
                agent=analyst
            )
            
            with console.status("[bold red]Analyzing Failure...[/bold red]"):
                analysis = str(Crew(agents=[analyst], tasks=[analysis_task]).kickoff()).strip()
            
            self.logger.info(f"FAILURE ANALYSIS: {analysis}")
            is_tb_issue = "A" in analysis
            
            if is_tb_issue:
                self.log("Analyst identified Testbench Error. Fixing TB...", refined=True)
                fixer = get_testbench_agent(self.llm, f"Fix TB for {self.name}", verbose=self.verbose)
                port_info = self._extract_module_interface(self.artifacts['rtl_code'])
                fix_prompt = f"""Fix the Testbench logic/syntax.

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

CRITICAL:
- Return ONLY the fixed Testbench code in ```verilog fences.
- Do NOT invent ports that aren't in the MODULE INTERFACE above.
- Module name of DUT is "{self.name}"
"""
                target_file_key = 'tb_path'
            else:
                self.log("Analyst identified RTL Logic Error. Fixing RTL...", refined=True)
                fixer = get_designer_agent(self.llm, f"Fix RTL for {self.name}", verbose=self.verbose, strategy=self.strategy.name)
                # Extract specific error lines for emphasis
                error_lines = [line for line in output.split('\n') if "Error" in line or "fail" in line.lower()]
                error_summary = "\n".join(error_lines)
                
                fix_prompt = f"""Fix the RTL logic to pass verification.
                
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
                
                CRITICAL: 
                - Address the 'Specific Issues' above directly.
                - Maintain design intent. 
                - Return ONLY the fixed {self.strategy.name} logic in ```verilog fences.
                """
                target_file_key = 'rtl_path' # We will update RTL code and re-run syntax check? 
                                            # Actually if we fix RTL, we should go back to RTL_FIX state to ensure syntax is clean!
            
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
            
            # If IDLE -> RTL_FIX, we need to save the new code to artifact
            if not is_tb_issue:
                # RTL Fix — write cleaned code and read it back
                path = write_verilog(self.name, fixed_code)
                if isinstance(path, str) and path.startswith("Error:"):
                    self.log(f"File Write Error when fixing RTL logic: {path}", refined=True)
                    self.state = BuildState.FAIL
                    return
                self.artifacts['rtl_path'] = path
                # Read back the CLEANED version, not raw LLM output
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
                # We update local var tb_code for next loop iteration if we stayed in loop, 
                # but since we rely on artifacts/re-reading, we should probably just continue the loop.
                # But wait, 'tb_code' var in this function scope is stale now.
                # Easier to just Loop.
                # NOTE: loops in python method? 
                # My orchestration loop calls `do_verification` repeatedly if state is unchanged.
                # So I just need to return.
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
            if yosys_code:
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
        """Runs simulation with coverage instrumentation and gates on threshold."""
        self.log(f"Running Coverage Analysis (threshold: {self.min_coverage}%)...", refined=True)
        
        with console.status("[bold magenta]Running Coverage-Instrumented Simulation...[/bold magenta]"):
            sim_passed, sim_output, coverage_data = run_simulation_with_coverage(self.name)
        
        self.logger.info(f"COVERAGE DATA:\n{coverage_data}")
        
        line_pct = coverage_data.get('line_pct', 0.0)
        self.artifacts['coverage'] = coverage_data
        
        self.log(f"Coverage: {line_pct:.1f}% line | {coverage_data.get('signals_toggled', 0)}/{coverage_data.get('total_signals', 0)} signals toggled", refined=True)
        
        # Track Best Coverage & Backup Best Testbench
        if self.retry_count == 0:
             self.best_coverage = -1.0
             self.best_tb_backup = None

        if line_pct > getattr(self, 'best_coverage', -1.0):
            self.best_coverage = line_pct
            # Backup valid testbench
            import shutil
            tb_path = self.artifacts.get('tb_path')
            if tb_path and os.path.exists(tb_path):
                backup_path = tb_path + ".best"
                shutil.copy(tb_path, backup_path)
                self.best_tb_backup = backup_path
                self.log(f"New Best Coverage: {line_pct:.1f}% (Backed up)", refined=True)
        
        if line_pct >= self.min_coverage:
            self.log(f"Coverage PASSED (≥{self.min_coverage}%)", refined=True)
            if self.full_signoff:
                self.transition(BuildState.REGRESSION)
            elif self.skip_openlane:
                self.transition(BuildState.SUCCESS)
            else:
                self.transition(BuildState.FLOORPLAN)
        else:
            self.retry_count += 1
            # Cap coverage retries at 2 — the metric is heuristic-based (iverilog
            # doesn't support native code coverage), so more LLM retries won't help.
            coverage_max_retries = min(self.max_retries, 2)
            if self.retry_count > coverage_max_retries:
                # REVERT TO BEST KNOWN TB
                if getattr(self, 'best_tb_backup', None) and os.path.exists(self.best_tb_backup):
                     self.log(f"Restoring Best Testbench ({self.best_coverage:.1f}%) before proceeding.", refined=True)
                     import shutil
                     shutil.copy(self.best_tb_backup, self.artifacts['tb_path'])
                if self.strict_gates:
                    self.log(f"Coverage below threshold after {coverage_max_retries} attempts. Failing strict gate.", refined=True)
                    self.state = BuildState.FAIL
                    return
                self.log(f"Coverage below threshold after {coverage_max_retries} attempts. Proceeding anyway.", refined=True)
                if self.full_signoff:
                    self.transition(BuildState.REGRESSION)
                elif self.skip_openlane:
                    self.transition(BuildState.SUCCESS)
                else:
                    self.transition(BuildState.FLOORPLAN)
                return
            
            # Ask LLM to generate additional tests to improve coverage
            self.log(f"Coverage {line_pct:.1f}% < {self.min_coverage}%. Generating additional tests (attempt {self.retry_count})...", refined=True)
            
            tb_agent = get_testbench_agent(self.llm, f"Improve coverage for {self.name}", verbose=self.verbose, strategy=self.strategy.name)
            
            improve_prompt = f"""The current testbench for "{self.name}" only achieves {line_pct:.1f}% coverage.
            Target: {self.min_coverage}%
            
            Coverage Data: {coverage_data}
            
            Current RTL:
            ```verilog
            {self.artifacts.get('rtl_code', '')}
            ```
            
            Current Testbench:
            ```verilog
            {open(self.artifacts['tb_path'], 'r').read() if 'tb_path' in self.artifacts and os.path.exists(self.artifacts.get('tb_path', '')) else 'NOT AVAILABLE'}
            ```
            
            Create an IMPROVED testbench that:
            1. Tests all FSM states (not just happy path)
            2. Exercises all conditional branches (if/else, case)
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
                self.artifacts['tb_path'] = tb_path
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

        base_die = 300 if line_count < 100 else 500 if line_count < 300 else 800
        area_scale = self.artifacts.get("area_scale", 1.0)
        die = int(base_die * area_scale)
        util = 40 if line_count >= 200 else 50
        clock_period = self.artifacts.get("clock_period_override", self.pdk_profile.get("default_clock_period", "10.0"))

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
        with open(floorplan_tcl, "w") as f:
            f.write(
                f"set ::env(DESIGN_NAME) \"{self.name}\"\n"
                f"set ::env(PDK) \"{self.pdk_profile.get('pdk', PDK)}\"\n"
                f"set ::env(STD_CELL_LIBRARY) \"{self.pdk_profile.get('std_cell_library', 'sky130_fd_sc_hd')}\"\n"
                f"set ::env(VERILOG_FILES) [glob $::env(DESIGN_DIR)/src/{self.name}.v]\n"
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

        # Congestion-driven loop
        if cong_pct > self.congestion_threshold:
            self.log(
                f"Congestion {cong_pct:.2f}% exceeds threshold {self.congestion_threshold:.2f}%.",
                refined=True,
            )
            # area expansion up to 2 times, then pivot logic
            area_expansions = int(self.artifacts.get("area_expansions", 0))
            if area_expansions < 2:
                self.artifacts["area_expansions"] = area_expansions + 1
                self.artifacts["area_scale"] = round(float(self.artifacts.get("area_scale", 1.0)) * 1.15, 3)
                self.log("Applying +15% area expansion due to congestion.", refined=True)
                self.transition(BuildState.FLOORPLAN, preserve_retries=True)
                return
            self._pivot_strategy("congestion persisted after area expansions")
            return

        # WNS stagnation logic
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
        clk_match = re.search(r'input\s+(?:wire\s+|logic\s+)?(\w*clk\w*)\s*[;,]', rtl_code, re.IGNORECASE)
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
set ::env(VERILOG_FILES) [glob $::env(DESIGN_DIR)/src/{self.name}.v]

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
            self.log(f"Hardening Failed: {result}")
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
            if cov and float(cov.get("line_pct", 0.0)) < float(self.min_coverage):
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
            f"Coverage:   {self.artifacts.get('coverage', {}).get('line_pct', 'N/A')}%\n"
            f"Formal:     {self.artifacts.get('formal_result', 'SKIPPED')}\n\n"
            f"{'[bold green]FABRICATION READY ✓[/]' if fab_ready else '[bold red]NOT FABRICATION READY ✗[/]'}",
            title="📋 Signoff Report"
        ))

        if fab_ready:
            self.log("✅ SIGNOFF PASSED (Timing Closed, DRC Clean)", refined=True)
            self.artifacts['signoff_result'] = 'PASS'
            self.transition(BuildState.SUCCESS)
        else:
            # Trigger ECO path before final fail when strict gates are enabled.
            if self.strict_gates and self.eco_attempts < 2:
                self.log("Signoff failed. Triggering ECO patch stage.", refined=True)
                self.transition(BuildState.ECO_PATCH, preserve_retries=True)
                return
            self.log("❌ SIGNOFF FAILED (Violations Found)", refined=True)
            self.artifacts['signoff_result'] = 'FAIL'
            self.errors.append("Signoff failed (see report).")
            self.state = BuildState.FAIL
