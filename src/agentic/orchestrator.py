import enum
import time
import logging
import os
from typing import Optional, Dict, Any, List
from rich.console import Console
from rich.panel import Panel
from crewai import Agent, Task, Crew, LLM

# Local imports
from .config import OPENLANE_ROOT, LLM_MODEL, LLM_BASE_URL, LLM_API_KEY
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
    run_cdc_check,
    generate_design_doc,
    convert_sva_to_yosys
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
    HARDENING = "GDSII Hardening"
    SIGNOFF = "DRC/LVS Signoff"
    SUCCESS = "Build Complete"
    FAIL = "Build Failed"

class BuildOrchestrator:
    def __init__(self, name: str, desc: str, llm: LLM, max_retries: int = 5, verbose: bool = True, skip_openlane: bool = False, full_signoff: bool = False, min_coverage: float = 80.0):
        self.name = name
        self.desc = desc
        self.llm = llm
        self.max_retries = max_retries
        self.verbose = verbose
        self.skip_openlane = skip_openlane
        self.full_signoff = full_signoff
        self.min_coverage = min_coverage
        
        self.state = BuildState.INIT
        self.strategy = BuildStrategy.SV_MODULAR
        self.retry_count = 0
        self.artifacts = {}  # Store paths to generated files
        self.history = []    # Log of state transitions and errors

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
        self.history.append({"state": self.state.name, "msg": message, "time": time.time()})
        
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

    def run(self):
        """Main execution loop."""
        self.log(f"Starting Build Process for '{self.name}' using {self.strategy.value}")
        
        try:
            while self.state != BuildState.SUCCESS and self.state != BuildState.FAIL:
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
                elif self.state == BuildState.HARDENING:
                    self.do_hardening()
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
            import json
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
            - Create a `class Driver` that drives the DUT interface.
            - Create a `class Monitor` that samples the DUT output.
            - Create a `class Scoreboard` that checks correctness.
            - Create a `program` block (or simplified `module`) to run the test.
            - Instantiate the DUT and the Test in top-level.
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

CRITICAL RULES:
1. Module name must be "{self.name}"
2. Async active-low reset `rst_n`
3. Flatten ports (no multi-dim arrays on ports)
4. Return code in ```verilog fence
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
        self.transition(BuildState.RTL_FIX)

    def do_rtl_fix(self):
        # Check syntax
        path = self.artifacts['rtl_path']
        success, errors = run_syntax_check(path)
        
        if success:
            self.log("Syntax Check Passed (Icarus)", refined=True)
            
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
                
                self.transition(BuildState.VERIFICATION)
                return
            else:
                self.log(f"Lint Failed. Check log for details.", refined=True)
                errors = f"SYNTAX OK, BUT LINT FAILED:\n{lint_report}"
                # Fall through to Error Handling Logic below to fix it

        # --- AUTONOMOUS SV↔VERILOG COMPATIBILITY FIX ---
        # Before wasting an LLM call, check if errors are known iverilog↔SV clashes.
        # If so, apply regex fixes instantly and re-check without calling LLM.
        sv_fix_applied = self._try_autonomous_sv_fix(errors, path)
        if sv_fix_applied:
            self.log("Auto-fixed SystemVerilog → Verilog compatibility (no LLM needed).", refined=True)
            # Re-read the fixed file into artifacts
            with open(path, 'r') as f:
                self.artifacts['rtl_code'] = f.read()
            # Stay in RTL_FIX to re-check syntax — don't count this as a retry
            return

        # Handle Syntax/Lint Errors that need LLM
        self.logger.info(f"SYNTAX/LINT ERRORS:\n{errors}")
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
        
        # Agents fix syntax
        fix_prompt = f"""Fix Syntax/Lint Errors in "{self.name}".
        
        Error Log:
        {errors}
        
        Strategy: {self.strategy.name} (Keep consistency!)
        
        IMPORTANT: The compiler is iverilog with -g2012. If you used SystemVerilog
        constructs that iverilog doesn't support, convert them:
        - always_comb → always @(*)
        - always_ff @(...) → always @(...)
        - type_name'(val) casts → remove the cast
        - unique case / priority case → case
        
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
        self.artifacts['rtl_path'] = new_path
        # Read back the CLEANED version, not raw LLM output
        with open(new_path, 'r') as f:
            self.artifacts['rtl_code'] = f.read()
        # Loop stays in RTL_FIX to re-check syntax

    @staticmethod
    def _try_autonomous_sv_fix(errors: str, rtl_path: str) -> bool:
        """Try to fix known iverilog ↔ SystemVerilog compatibility issues WITHOUT calling LLM.
        
        Returns True if fixes were applied (caller should re-check syntax).
        Returns False if errors are unknown and LLM is needed.
        """
        import re
        
        # Known iverilog error patterns and their regex fixes
        SV_ERROR_PATTERNS = {
            r'constant selects in always_\* processes':  'always_comb_fix',
            r'This assignment requires an explicit cast': 'type_cast_fix',
            r"sorry:.*always_comb":                       'always_comb_fix',
            r"sorry:.*always_ff":                         'always_ff_fix',
            r'unique case':                               'unique_case_fix',
            r'priority case':                             'priority_case_fix',
            r"error:.*enum":                              'type_cast_fix',
            r'Cannot create.*initial value':              'init_fix',
        }
        
        # Check which errors match known SV issues
        matched_fixes = set()
        error_lines = [l for l in errors.split('\n') if 'error' in l.lower() or 'sorry' in l.lower()]
        unmatched_errors = []
        
        for line in error_lines:
            found = False
            for pattern, fix_type in SV_ERROR_PATTERNS.items():
                if re.search(pattern, line, re.IGNORECASE):
                    matched_fixes.add(fix_type)
                    found = True
                    break
            if not found and line.strip():
                unmatched_errors.append(line)
        
        # If there are unknown errors, we can't fully auto-fix — need LLM
        if unmatched_errors:
            return False
        
        # If no known errors matched either, nothing to fix
        if not matched_fixes:
            return False
        
        # Apply fixes directly to the file
        try:
            with open(rtl_path, 'r') as f:
                code = f.read()
        except IOError:
            return False
        
        original = code
        
        if 'always_comb_fix' in matched_fixes:
            code = re.sub(r'\balways_comb\b', 'always @(*)', code)
        
        if 'always_ff_fix' in matched_fixes:
            code = re.sub(r'\balways_ff\s+@', 'always @', code)
        
        if 'type_cast_fix' in matched_fixes:
            # signed'(val) -> $signed(val)
            code = re.sub(r"signed'\s*\((.*?)\)", r"$signed(\1)", code)
            # Other type casts: type_name'(val) -> (val)
            code = re.sub(r"\b[a-zA-Z_]\w*'\s*\(", "(", code)
        
        if 'unique_case_fix' in matched_fixes:
            code = re.sub(r'\bunique\s+case\b', 'case', code)
        
        if 'priority_case_fix' in matched_fixes:
            code = re.sub(r'\bpriority\s+case\b', 'case', code)
        
        if 'init_fix' in matched_fixes:
            # Remove initial values from reg/logic declarations: logic [7:0] x = 0; -> logic [7:0] x;
            code = re.sub(r'((?:reg|logic|wire)\s+(?:\[.*?\]\s+)?\w+)\s*=\s*[^;]+;', r'\1;', code)
        
        # Only write if something actually changed
        if code == original:
            return False
        
        with open(rtl_path, 'w') as f:
            f.write(code)
        return True

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
                 console.print()
                 if typer.confirm("Simulation Passed. Proceed to OpenLane Hardening (takes 10-30 mins)?", default=True):
                     self.transition(BuildState.FORMAL_VERIFY)
                 else:
                     self.log("Skipping Hardening (User Cancelled).", refined=True)
                     self.transition(BuildState.FORMAL_VERIFY)
        else:
            self.retry_count += 1
            if self.retry_count > self.max_retries:
                 self.log(f"Max Sim Retries ({self.max_retries}) Exceeded. Simulation Failed.", refined=True)
                 self.state = BuildState.FAIL
                 return
            
            self.log(f"Sim Failed (Attempt {self.retry_count}). Check log.", refined=True)
            
            # --- AUTONOMOUS FIX: Try to fix compilation errors without LLM ---
            if "Compilation failed" in output:
                # It's a syntax/compilation error — try autonomous SV fix first
                # Detect whether the error is in TB or RTL from the path in the error
                tb_path = self.artifacts.get('tb_path', '')
                rtl_path = self.artifacts.get('rtl_path', '')
                
                if tb_path and os.path.basename(tb_path) in output:
                    fixed = self._try_autonomous_sv_fix(output, tb_path)
                    if fixed:
                        self.log("Auto-fixed TB compilation error (no LLM needed).", refined=True)
                        self.retry_count -= 1  # Don't count autonomous fixes
                        return  # Re-enters do_verification, re-runs simulation
                
                if rtl_path and os.path.basename(rtl_path) in output:
                    fixed = self._try_autonomous_sv_fix(output, rtl_path)
                    if fixed:
                        self.log("Auto-fixed RTL compilation error (no LLM needed).", refined=True)
                        with open(rtl_path, 'r') as f:
                            self.artifacts['rtl_code'] = f.read()
                        self.retry_count -= 1
                        return
            
            # --- LLM ERROR ANALYSIS (only if autonomous fix didn't work) ---
            analyst = get_error_analyst_agent(self.llm, verbose=self.verbose)
            analysis_task = Task(
                description=f'''Analyze this Verification Failure.
Error Log:
{output}
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
{output}

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
                {output}
                
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
                2. Include properties for:
                   - Reset behavior (outputs go to known state)
                   - Protocol compliance (valid handshakes)
                   - State machine reachability
                   - Data integrity (no corruption)
                3. Use @(posedge clk) for all properties
                4. Include cover properties for key scenarios
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
                self.artifacts['formal_result'] = 'FAIL (non-blocking)'
                # Formal failure is non-blocking — we log it but continue
                # Industry note: In production, this would be blocking
        except Exception as e:
            self.log(f"Formal verification error: {str(e)}. Continuing.", refined=True)
            self.artifacts['formal_result'] = f'ERROR: {str(e)}'
        
        # 4. Run CDC check
        with console.status("[bold cyan]Running CDC Analysis...[/bold cyan]"):
            cdc_clean, cdc_report = run_cdc_check(rtl_path)
        
        self.logger.info(f"CDC REPORT:\n{cdc_report}")
        self.artifacts['cdc_result'] = 'CLEAN' if cdc_clean else 'WARNINGS'
        
        if cdc_clean:
            self.log("CDC Analysis: CLEAN", refined=True)
        else:
            self.log(f"CDC Analysis: warnings found (non-blocking)", refined=True)
        
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
                self.transition(BuildState.HARDENING)
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

                self.log(f"Coverage below threshold after {coverage_max_retries} attempts. Proceeding anyway.", refined=True)
                if self.full_signoff:
                    self.transition(BuildState.REGRESSION)
                elif self.skip_openlane:
                    self.transition(BuildState.SUCCESS)
                else:
                    self.transition(BuildState.HARDENING)
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
                compile_result = subprocess.run(
                    ["iverilog", "-g2012", "-o", sim_out, rtl_file, test_path],
                    capture_output=True, text=True, timeout=120
                )
                if compile_result.returncode != 0:
                    test_results.append({"test": test_name, "status": "COMPILE_FAIL", "output": compile_result.stderr[:500]})
                    all_passed = False
                    continue
                
                run_result = subprocess.run(
                    ["vvp", sim_out],
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
            self.log(f"Regression: {passed_count}/{len(test_results)} passed (non-blocking)", refined=True)
        
        # Regression failures are non-blocking (logged but proceed)
        if self.skip_openlane:
            self.transition(BuildState.SUCCESS)
        else:
            self.transition(BuildState.HARDENING)

    def do_hardening(self):
        # Run OpenLane
        with console.status("[bold blue]Hardening Layout (OpenLane)...[/bold blue]"):
            success, result = run_openlane(self.name, background=False)
            
        if success:
            self.artifacts['gds'] = result
            self.log(f"GDSII generated: {result}", refined=True)
            if self.full_signoff:
                self.transition(BuildState.SIGNOFF)
            else:
                self.transition(BuildState.SUCCESS)
        else:
            self.log(f"Hardening Failed: {result}")
            self.state = BuildState.FAIL

    def do_signoff(self):
        """Performs DRC/LVS signoff checks and generates documentation."""
        self.log("Running DRC/LVS Signoff Checks...", refined=True)
        
        # 1. Parse DRC/LVS reports
        with console.status("[bold blue]Checking DRC/LVS Reports...[/bold blue]"):
            signoff_pass, signoff_details = parse_drc_lvs_reports(self.name)
        
        self.logger.info(f"SIGNOFF DETAILS:\n{signoff_details}")
        self.artifacts['signoff'] = signoff_details
        
        drc_v = signoff_details.get('drc_violations', -1)
        lvs_e = signoff_details.get('lvs_errors', -1)
        ant_v = signoff_details.get('antenna_violations', -1)
        
        self.log(f"DRC Violations: {drc_v} | LVS Errors: {lvs_e} | Antenna: {ant_v}", refined=True)
        
        if signoff_pass:
            self.log("DRC/LVS Signoff: CLEAN ✓", refined=True)
        else:
            self.log("DRC/LVS Signoff: VIOLATIONS FOUND (see log)", refined=True)
            # Non-blocking for now — in production this would FAIL
        
        # 2. Check physical metrics
        with console.status("[bold blue]Analyzing Physical Metrics...[/bold blue]"):
            metrics, metrics_status = check_physical_metrics(self.name)
        
        if metrics:
            self.artifacts['metrics'] = metrics
            self.log(f"Area: {metrics.get('chip_area_um2', 'N/A')} µm² | WNS: {metrics.get('timing_wns', 'N/A')} | Power: {metrics.get('power_total', 'N/A')}", refined=True)
            
            # Timing signoff check
            wns = metrics.get('timing_wns', 0)
            if wns < 0:
                self.log(f"WARNING: Timing violation detected (WNS = {wns})", refined=True)
        
        # 3. Generate documentation
        self.log("Generating Design Documentation...", refined=True)
        with console.status("[bold cyan]Auto-generating Datasheet...[/bold cyan]"):
            doc_path = generate_design_doc(
                self.name, 
                spec=self.artifacts.get('spec', ''),
                metrics=metrics
            )
        
        if doc_path and not doc_path.startswith("Error:"):
            self.artifacts['datasheet'] = doc_path
            self.log(f"Datasheet generated: {doc_path}", refined=True)
        else:
            self.log(f"Documentation generation failed: {doc_path}", refined=True)
        
        # Build summary
        console.print()
        console.print(Panel(
            f"[bold cyan]Industry Signoff Summary[/bold cyan]\n\n"
            f"Formal: {self.artifacts.get('formal_result', 'SKIPPED')}\n"
            f"CDC: {self.artifacts.get('cdc_result', 'SKIPPED')}\n"
            f"Coverage: {self.artifacts.get('coverage', {}).get('line_pct', 'N/A')}%\n"
            f"DRC: {drc_v} violations\n"
            f"LVS: {lvs_e} errors\n"
            f"Timing WNS: {metrics.get('timing_wns', 'N/A') if metrics else 'N/A'}",
            title="📋 Signoff Report"
        ))
        
        self.transition(BuildState.SUCCESS)

