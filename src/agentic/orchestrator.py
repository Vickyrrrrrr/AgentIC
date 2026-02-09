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
from .agents.verifier import get_verification_agent, get_error_analyst_agent
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
    run_formal_verification,
    check_physical_metrics,
    run_lint_check
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
    HARDENING = "GDSII Hardening"
    SUCCESS = "Build Complete"
    FAIL = "Build Failed"

class BuildOrchestrator:
    def __init__(self, name: str, desc: str, llm: LLM, max_retries: int = 5, verbose: bool = True, skip_openlane: bool = False):
        self.name = name
        self.desc = desc
        self.llm = llm
        self.max_retries = max_retries
        self.verbose = verbose
        self.skip_openlane = skip_openlane
        
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

    def transition(self, new_state: BuildState):
        self.log(f"Transitioning: {self.state.name} -> {new_state.name}")
        self.state = new_state
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
                elif self.state == BuildState.HARDENING:
                    self.do_hardening()
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

    def do_rtl_gen(self):
        # Generate RTL based on strategy
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
        
        # Save raw file
        path = write_verilog(self.name, rtl_code)
        if "Error" in path:
            self.log(f"File Write Error: {path}", refined=True)
            # If we can't write, we can't proceed.
            self.state = BuildState.FAIL
            return

        self.artifacts['rtl_path'] = path
        self.artifacts['rtl_code'] = rtl_code
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
                self.transition(BuildState.VERIFICATION)
                return
            else:
                self.log(f"Lint Failed. Check log for details.", refined=True)
                errors = f"SYNTAX OK, BUT LINT FAILED:\n{lint_report}"
                # Fall through to Error Handling Logic below to fix it
                # We treat Lint errors as "Syntax/Quality" errors that need fixing before Sim.

        # Handle Syntax/Lint Errors
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
        self.artifacts['rtl_code'] = new_code
        # Loop stays in RTL_FIX to re-check syntax

    def do_verification(self):
        # 1. Generate Testbench (Only on first run or if missing)
        # 2. Run Sim
        # 3. Analyze Results
        
        # 1. Generate Testbench (Only if missing)
        # We reuse existing TB to ensure consistent verification targets
        tb_exists = 'tb_path' in self.artifacts and os.path.exists(self.artifacts['tb_path'])
        
        if not tb_exists:
            self.log("Generating Testbench...", refined=True)
            tb_agent = get_testbench_agent(self.llm, f"Verify {self.name}", verbose=self.verbose, strategy=self.strategy.name)
            
            tb_strategy_prompt = self._get_tb_strategy_prompt()
            
            tb_task = Task(
                description=f"""Create a self-checking Testbench for {self.name}.
                
                RTL:
                {self.artifacts['rtl_code']}
                
                STRATEGY GUIDELINES:
                {tb_strategy_prompt}
                
                Rules:
                - Instantiation: {self.name} dut (.*);
                - Wait for Reset
                - Check Output
                - $finish
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
                 self.retry_count = 0 # Reset to force regen
                 self.do_verification()
                 return
            
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
                 self.transition(BuildState.SUCCESS)
            else:
                 # Interactive Prompt for Hardening
                 import typer
                 console.print()
                 if typer.confirm("Simulation Passed. Proceed to OpenLane Hardening (takes 10-30 mins)?", default=True):
                     self.transition(BuildState.HARDENING)
                 else:
                     self.log("Skipping Hardening (User Cancelled).", refined=True)
                     self.transition(BuildState.SUCCESS)
        else:
            self.retry_count += 1
            if self.retry_count > self.max_retries:
                 self.log(f"Max Sim Retries ({self.max_retries}) Exceeded. Simulation Failed.", refined=True)
                 self.state = BuildState.FAIL
                 return
            
            self.log(f"Sim Failed (Attempt {self.retry_count}). Check log.", refined=True)
            
            # --- ERROR ANALYSIS ---
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
                fix_prompt = f"""Fix the Testbench logic/syntax.
                
                Error:
                {output}
                
                Current TB:
                ```verilog
                {tb_code}
                ```
                
                Ref RTL:
                ```verilog
                {self.artifacts['rtl_code']}
                ```
                
                CRITICAL: Return ONLY the fixed SystemVerilog Testbench code in ```verilog fences.
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
                # RTL Fix
                # Update artifact
                # We need to write to file to check it
                path = write_verilog(self.name, fixed_code)
                self.artifacts['rtl_path'] = path
                self.artifacts['rtl_code'] = fixed_code
                self.log("RTL Updated. Transitioning back to RTL_FIX to verify syntax.", refined=True)
                self.transition(BuildState.RTL_FIX)
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


    def do_hardening(self):
        # Run OpenLane
        with console.status("[bold blue]Hardening Layout (OpenLane)...[/bold blue]"):
            success, result = run_openlane(self.name, background=False)
            
        if success:
            self.artifacts['gds'] = result
            self.transition(BuildState.SUCCESS)
        else:
            self.log(f"Hardening Failed: {result}")
            self.state = BuildState.FAIL

