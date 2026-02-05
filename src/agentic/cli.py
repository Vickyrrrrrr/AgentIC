#!/usr/bin/env python3
"""
AgentIC - Natural Language to GDSII Pipeline
=============================================
Uses CrewAI + LLM (DeepSeek/Llama/Groq) to generate chips from natural language.


Usage:
    python main.py build --name counter --desc "8-bit counter with enable and reset"
"""

import os
import re
import sys
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from crewai import Agent, Task, Crew, LLM

# Local imports
from .config import OPENLANE_ROOT, LLM_MODEL, LLM_BASE_URL, LLM_API_KEY, NVIDIA_CONFIG, GROQ_CONFIG, NVIDIA_BACKUP_CONFIG, NVIDIA_USER_CONFIG
from .agents.designer import get_designer_agent
from .agents.testbench_designer import get_testbench_agent
from .agents.verifier import get_verification_agent, get_error_analyst_agent
from .tools.vlsi_tools import (
    write_verilog,
    write_config, 
    run_syntax_check,
    syntax_check_tool,
    read_file_content,
    read_file_tool,
    run_simulation, 
    run_openlane,
    run_verification,
    SecurityCheck,
    write_sby_config,
    run_formal_verification,
    check_physical_metrics
)

# --- INITIALIZE ---
app = typer.Typer()
console = Console()

# Setup Brain
def get_llm():
    """Returns the LLM instance for agents, with Nemotron -> Backup -> Groq fallback."""
    # 1. Try NVIDIA User Config (Primary - Llama 405B)
    try:
        return LLM(
            model=NVIDIA_USER_CONFIG["model"],
            base_url=NVIDIA_USER_CONFIG["base_url"],
            api_key=NVIDIA_USER_CONFIG["api_key"]
        )
    except Exception as e:
         console.print(f"[yellow]⚠ NVIDIA User Config Init failed: {e}. Falling back to Backup...[/yellow]")

    # 2. Try NVIDIA Backup (Llama 3.1 405B - Secondary)
    try:
        return LLM(
            model=NVIDIA_BACKUP_CONFIG["model"],
            base_url=NVIDIA_BACKUP_CONFIG["base_url"],
            api_key=NVIDIA_BACKUP_CONFIG["api_key"]
        )
    except Exception as e:
         console.print(f"[yellow]⚠ NVIDIA Backup Init failed: {e}. Falling back to Groq...[/yellow]")

    # 3. Try Groq (Tertiary)
    if GROQ_CONFIG["api_key"]:
        try:
            return LLM(
                model=GROQ_CONFIG["model"],
                base_url=GROQ_CONFIG["base_url"],
                api_key=GROQ_CONFIG["api_key"]
            )
        except Exception as e:
            console.print(f"[yellow]⚠ Groq Init failed: {e}. Falling back to NVIDIA...[/yellow]")

    # 3. Try NVIDIA (Tertiary)
    try:
        return LLM(
            model=NVIDIA_CONFIG["model"],
            base_url=NVIDIA_CONFIG["base_url"],
            api_key=NVIDIA_CONFIG["api_key"]
        )
    except Exception as e:
        console.print(f"[yellow]⚠ NVIDIA Init failed: {e}. Falling back to Local...[/yellow]")
    
    # 4. Last Resort: Default/Local
    return LLM(
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY
    )

@app.command()
def simulate(
    name: str = typer.Option(..., "--name", "-n", help="Design name (e.g., counter)"),
    max_retries: int = typer.Option(5, "--max-retries", "-r", min=0, help="Max auto-fix retries for failures"),
    show_thinking: bool = typer.Option(True, "--show-thinking", help="Print DeepSeek <think> reasoning")
):
    """Run simulation on an existing design with AUTO-FIX loop."""
    console.print(Panel(
        f"[bold cyan]AgentIC: Manual Simulation + Auto-Fix Mode[/bold cyan]\n"
        f"Design: [yellow]{name}[/yellow]",
        title="🚀 Starting Simulation"
    ))

    llm = get_llm()

    def log_thinking(raw_text: str, step: str):
        """Emit DeepSeek <think> content."""
        if not show_thinking: return
        # Simple logging for sim tool
        pass 

    from .agents.verifier import get_error_analyst_agent
    from .tools.vlsi_tools import syntax_check_tool, read_file_tool
    
    rtl_path = f"{OPENLANE_ROOT}/designs/{name}/src/{name}.v"
    tb_path = f"{OPENLANE_ROOT}/designs/{name}/src/{name}_tb.v"

    def _fix_with_llm(agent_role: str, goal: str, prompt: str) -> str:
        # Give the agent TOOLS to self-correct
        fix_agent = Agent(
            role=agent_role,
            goal=goal,
            backstory='Expert in SystemVerilog and verification.',
            llm=llm,
            verbose=show_thinking,
            tools=[syntax_check_tool, read_file_tool]
        )
        fix_task = Task(
            description=prompt,
            expected_output='Corrected SystemVerilog code in a ```verilog fence',
            agent=fix_agent
        )
        with console.status(f"[cyan]AI is fixing ({agent_role})...[/cyan]"):
            result = str(Crew(agents=[fix_agent], tasks=[fix_task]).kickoff())
            return result
    
    sim_success, sim_output = run_simulation(name)
    sim_tries = 0
    
    while not sim_success and sim_tries < max_retries:
        sim_tries += 1
        console.print(f"[bold red]✗ SIMULATION FAILED (attempt {sim_tries}/{max_retries})[/bold red]")
        sim_output_text = sim_output or ""

        # 1) If compilation failed, fix TB first.
        if "Compilation failed:" in sim_output_text or "syntax error" in sim_output_text:
            fix_tb_prompt = f'''Fix this SystemVerilog testbench so it compiles and avoids directionality errors.

CRITICAL FIXING RULES:
1. **Unresolved Wires**: If you see "Unable to assign to unresolved wires", it means you are driving a DUT OUTPUT. Stop driving it!
2. **Signal Directions**:
   - Check the DUT definition.
   - If a port is `output` in DUT, it is a `wire` in TB (Read-Only).
   - If a port is `input` in DUT, it is a `reg/logic` in TB (Write-Only).
3. **Format**: Return ONLY corrected testbench code inside ```verilog fences.

Simulation output / errors:
{sim_output_text}

Current RTL (do not modify unless absolutely necessary):
```verilog
{open(rtl_path,'r').read()}
```

Current testbench:
```verilog
{open(tb_path,'r').read()}
```
'''
            fixed_tb = _fix_with_llm('Verification Engineer', f'Fix testbench for {name}', fix_tb_prompt)
            tb_path = write_verilog(name, fixed_tb, is_testbench=True)
            sim_success, sim_output = run_simulation(name)
            continue

        # 2) Logic or Runtime Errors
        if "TEST FAILED" in sim_output_text or "TEST PASSED" not in sim_output_text:
            
            # AI-Based Error Classification
            analyst = get_error_analyst_agent(llm, verbose=False)
            analysis_task = Task(
                description=f'''Analyze this Verification Failure.
Error Log:
{sim_output_text}
Is this a:
A) TESTBENCH_ERROR (Syntax, $monitor usage, race condition, compilation fail)
B) RTL_LOGIC_ERROR (Mismatch, Wrong State, Functional Failure)
Reply with ONLY "A" or "B".''',
                expected_output='Single letter A or B',
                agent=analyst
            )
            analysis = str(Crew(agents=[analyst], tasks=[analysis_task]).kickoff()).strip()
            
            is_tb_issue = "A" in analysis

            if is_tb_issue:
                 console.print("[yellow]  -> [Analyst] Root Cause: Testbench Error. Fixing TB...[/yellow]")
                 fix_tb_logic_prompt = f'''Fix the Testbench logic/syntax. The simulation failed or generated runtime errors.
                 
CRITICAL FIXING RULES:
1. **Timing**: Ensure you `wait(done)` or `repeat(N) @(posedge clk)` before checking results.
2. **Race Conditions**: If "TEST FAILED" happens immediately, add delays (`#1`) before sampling or driving signals.
3. **Format**: Return ONLY corrected testbench code inside ```verilog fences.

Simulation Error/Output:
{sim_output_text}

Current RTL (Reference):
```verilog
{open(rtl_path,'r').read()}
```

Current Testbench (To Fix):
```verilog
{open(tb_path,'r').read()}
```
'''
                 fixed_tb = _fix_with_llm('Verification Engineer', f'Fix testbench logic for {name}', fix_tb_logic_prompt)
                 tb_path = write_verilog(name, fixed_tb, is_testbench=True)
                 sim_success, sim_output = run_simulation(name)
                 continue

            else:
                console.print("[yellow]  -> Detecting Design Logic mismatch. Fixing RTL...[/yellow]")
                fix_rtl_prompt = f'''The simulation did not pass. Fix the RTL (module "{name}") so that the testbench passes.

CRITICAL REQUIREMENTS:
- **NO CONVERSATION**: Return ONLY the code inside ```verilog fences. Do NOT write "Thought:", "Here is the code", or any explanation.
- Keep module name exactly "{name}"
- SystemVerilog only
- Keep ports: clk, rst_n (active-low) present
- **MAINTAIN DESIGN INTENT**: Do NOT simplify the logic to pass the test case.
  - If the design is an NPU or Processor, do NOT replace complex logic with simple static assignments.
  - You must fix the BUGS in the implementation, not delete the implementation.
  - If the testbench expects a result after N cycles, ensure your pipeline matches that latency.
- Return ONLY corrected RTL code inside ```verilog fences

Simulation output:
{sim_output_text}

Current testbench (do not change in this step):
```verilog
{open(tb_path,'r').read()}
```

Current RTL:
```verilog
{open(rtl_path,'r').read()}
```
'''
                fixed_rtl = _fix_with_llm('VLSI Design Engineer', f'Fix RTL behavior for {name}', fix_rtl_prompt)
                rtl_path = write_verilog(name, fixed_rtl)
                
                # Check syntax of fix
                success, errors = run_syntax_check(rtl_path)
                if not success:
                    sim_output = f"RTL fix introduced syntax error:\n{errors}"
                    continue

                sim_success, sim_output = run_simulation(name)
                continue
    
    if not sim_success:
        console.print(f"[bold red]✗ SIMULATION FAILED:[/bold red]\n{sim_output}")
        raise typer.Exit(1)

    sim_lines = sim_output.strip().split('\n')
    for line in sim_lines[-20:]:  # Print last 20 lines of log
        console.print(f"  [dim]{line}[/dim]")
    console.print("  ✓ Simulation [green]passed[/green]")

@app.command()
def harden(
    name: str = typer.Option(..., "--name", "-n", help="Design name (e.g., counter)"),
):
    """Run OpenLane hardening (RTL -> GDSII) on an existing design."""
    console.print(Panel(
        f"[bold cyan]AgentIC: Manual Hardening Mode[/bold cyan]\n"
        f"Design: [yellow]{name}[/yellow]",
        title="🚀 Starting OpenLane"
    ))
    
    # Check config first
    template_config = f"{OPENLANE_ROOT}/designs/simple_counter/config.tcl"
    new_config = f"{OPENLANE_ROOT}/designs/{name}/config.tcl"

    if not os.path.exists(new_config):
        if os.path.exists(template_config):
            with open(template_config, 'r') as f:
                content = f.read().replace("simple_counter", name)

            content = content.replace(
                "set ::env(VERILOG_FILES) [glob $::env(DESIGN_DIR)/src/*.v]",
                f"set ::env(VERILOG_FILES) \"$::env(DESIGN_DIR)/src/{name}.v\""
            )
            content = content.replace(
                "set ::env(VERILOG_FILES) \"$::env(DESIGN_DIR)/src/simple_counter.v\"",
                f"set ::env(VERILOG_FILES) \"$::env(DESIGN_DIR)/src/{name}.v\""
            )

            os.makedirs(os.path.dirname(new_config), exist_ok=True)
            with open(new_config, 'w') as f:
                f.write(content)
            console.print(f"  ✓ Config written: [green]{new_config}[/green]")
        else:
             console.print(f"[bold red]✗ Config not found and template missing.[/bold red]")
             raise typer.Exit(1)
    
    # Ask for background execution
    run_bg = typer.confirm("OpenLane hardening can take 10-30+ minutes. Run in background?", default=True)
    
    if run_bg:
        console.print("  [dim]Launching background process...[/dim]")
    else:
        console.print("  [dim]Running OpenLane (this may take 10-30 minutes)...[/dim]")

    ol_success, ol_result = run_openlane(name, background=run_bg)
    
    if ol_success:
        if run_bg:
             console.print(f"  ✓ [green]{ol_result}[/green]")
             console.print(f"  [dim]Monitor logs: tail -f {OPENLANE_ROOT}/designs/{name}/harden.log[/dim]")
             return
        console.print(f"  ✓ GDSII generated: [green]{ol_result}[/green]")
    else:
        console.print(f"[bold red]✗ OpenLane failed[/bold red]")
        console.print(f"  Error: {ol_result[:500]}...")
        raise typer.Exit(1)

# --- THE BUILD COMMAND ---
@app.command()
def build(
    name: str = typer.Option(..., "--name", "-n", help="Design name (e.g., counter)"),
    desc: str = typer.Option(..., "--desc", "-d", help="Natural language description"),
    max_retries: int = typer.Option(5, "--max-retries", "-r", min=0, help="Max auto-fix retries for RTL/TB/sim failures"),
    skip_openlane: bool = typer.Option(False, "--skip-openlane", help="Stop after simulation (no RTL→GDSII hardening)"),
    show_thinking: bool = typer.Option(True, "--show-thinking", help="Print DeepSeek <think> reasoning for each generation/fix step")
):
    """Build a chip from natural language description."""
    
    console.print(Panel(
        f"[bold cyan]AgentIC: Natural Language → GDSII[/bold cyan]\n"
        f"Design: [yellow]{name}[/yellow]\n"
        f"Description: {desc}",
        title="🚀 Starting Pipeline"
    ))

    llm = get_llm()

    def log_thinking(raw_text: str, step: str):
        """Emit DeepSeek <think> content to the console when requested."""
        if not show_thinking or "<think>" not in raw_text:
            return
        thoughts = re.findall(r"<think>(.*?)</think>", raw_text, flags=re.DOTALL)
        cleaned = [t.strip() for t in thoughts if t.strip()]
        if not cleaned:
            return
        console.print(Panel("\n\n".join(cleaned), title=f"🧠 DeepSeek thinking — {step}", expand=False))

    def _fix_with_llm(agent_role: str, goal: str, prompt: str) -> str:
        # Give the agent TOOLS to self-correct
        fix_agent = Agent(
            role=agent_role,
            goal=goal,
            backstory='Expert in SystemVerilog and ASIC flows (Sky130/OpenLane). I fix compilation errors by analyzing files and checking syntax.',
            llm=llm,
            verbose=show_thinking,
            tools=[syntax_check_tool, read_file_tool]
        )
        
        # Add explicit instruction to avoid dynamic variable names in loops
        enhanced_prompt = prompt + "\n\nCRITICAL STRATEGY: Use the 'Syntax Checker' tool to verify your code before answering. If you need to check port definitions in other files, use 'File Reader'.\nCRITICAL VERILOG RULE: Do NOT use variable names like 'input_i' or 'wire_j' inside loops."
        
        fix_task = Task(
            description=enhanced_prompt,
            expected_output='Corrected SystemVerilog code in a ```verilog fence',
            agent=fix_agent
        )
        with console.status("[cyan]AI is fixing the design...[/cyan]"):
            result = str(Crew(agents=[fix_agent], tasks=[fix_task]).kickoff())
            log_thinking(result, step=f"Fix: {agent_role}")
            return result
    
    # ===== STEP 0: Architecture Spec =====
    console.print("\n[bold yellow]━━━ Step 1/6: Architectural Planning ━━━[/bold yellow]")

    arch_agent = Agent(
        role='Chief System Architect',
        goal=f'Define a robust micro-architecture and test plan for {name}',
        backstory='You are a veteran Silicon Architect. You do not write code; you define interfaces, state machines, and data paths to ensure the implementation is flawless.',
        llm=llm,
        verbose=show_thinking
    )
    
    spec_task = Task(
        description=f"""Create a DETAILED Micro-Architecture Specification (MAS) for a chip named "{name}".
Requirement: {desc}

OUTPUT MUST INCLUDE:
1. **IO Interface**: Precise port names, widths, and directions.
   - Mandatory: `clk`, `rst_n`.
   - No multi-dimensional ports (flatten them).
2. **Internal Architecture**:
   - List detailed registers and signals (e.g. `logic [31:0] acc`).
   - Describe Key FSM States (IDLE, PROCESS, etc).
   - Define the Datapath logic (Parallelism, Pipelining).
3. **Design Intent**: explicitly state logic complexity (e.g. "Do not simplify matrix mul").

""",
        expected_output='Markdown Specification',
        agent=arch_agent
    )

    with console.status("[cyan]AI is planning the architecture...[/cyan]"):
        spec_result = str(Crew(agents=[arch_agent], tasks=[spec_task]).kickoff())
        log_thinking(spec_result, step="Architecture Spec")
    
    console.print("  ✓ Architecture Plan Generated")

    # ===== STEP 1/2: Generate RTL with syntax-fix loop =====
    console.print("\n[bold yellow]━━━ Step 2/6: Generating Verilog RTL ━━━[/bold yellow]")

    rtl_agent = get_designer_agent(
        llm=llm,
        goal=f'Create synthesizable SystemVerilog for: {desc}',
        verbose=show_thinking
    )

    rtl_task = Task(
        description=f'''Design an INDUSTRY STANDARD SystemVerilog module named "{name}" based on the ARCHITECTURE SPECIFICATION below.

SOURCE SPECIFICATION:
{spec_result}

CRITICAL SILICON RULES (STRICTLY FOLLOW OR SILICON WILL FAIL):
1. **Module & Ports**:
   - Module name must be exactly "{name}"
   - ALWAYS include `input logic clk`, `input logic rst_n` (active-low asynchronous reset).
   - **FLATTENED PORTS**: Do not use multidimensional arrays for top-level ports. Use `input logic [WIDTH*COUNT-1:0] flat_bus`.

2. **Logic & Structure**:
   - **NO MULTI-DRIVERS**: NEVER assign the same signal in `always_ff` and `always_comb`. This is a FATAL error.
     - BAD: `always_ff @(clk) x <= y; always_comb x = z;`
   - **NO HUGE REGISTERS**: Do NOT declare arrays like `logic [7:0] mem [0:1024]`. This creates 8000 registers!
     - Instead, use a smaller buffer or assume an AXI interface to external memory.
   - **PIPELINE ARITHMETIC**: Do NOT loop `for (i=0; i<16; i++)` to do 16 multiplications in one cycle. 
     - Use a state machine or pipeline registers.

3. **Standard Syntax**:
   - Use `logic` for all internal signals.
   - Use `parameter` for widths.
   - Separate `always_ff` (sequential) and `always_comb` (logic).

4. **Output Format**:
   - Return ONLY the code wrapped in ```verilog fences
   - Add comments explaining the architecture.

Example format:
```verilog
module {name} #(parameter WIDTH=32) (
    input logic clk,
    input logic rst_n
);
    // 1. Declarations
    logic [WIDTH-1:0] data_reg;
    integer i;
    
    // 2. Continuous Assignments
    assign w_ready = (data_reg != 0);

    // 3. Sequential Logic
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            data_reg <= 0;
            // Loop example
            for (i=0; i<WIDTH; i=i+1) ...
        end else begin
            data_reg <= ...;
        end
    end
endmodule
```
''',
        expected_output='Complete SystemVerilog module code in markdown fence',
        agent=rtl_agent
    )

    with console.status("[cyan]AI is designing your chip (this may take 2-5 mins)...[/cyan]"):
        rtl_result = Crew(agents=[rtl_agent], tasks=[rtl_task]).kickoff()

    rtl_raw = str(rtl_result)
    log_thinking(rtl_raw, step="RTL generation")

    # [1.5] Security & Design Audit
    is_safe, msg = SecurityCheck(rtl_raw)
    if not is_safe:
        console.print(f"[bold red]🛑 SECURITY AUDIT FAILED[/bold red]: {msg}")
        raise typer.Exit(1)
    console.print(f"  ✓ Logic Security Check: [green]{msg}[/green]")
    
    # [1.6] Senior Engineer Review Loop
    qa_passed = False
    qa_retries = 0
    current_rtl = rtl_raw

    while not qa_passed and qa_retries < 3:
        qa_agent = Agent(
            role='Senior Silicon Architect',
            goal=f'Reject RTL code that violates physical design rules',
            backstory='You are a pessimistic chip lead. You HATE inefficient code. You look for multi-drivers, huge arrays, and timing violations.',
            llm=llm,
            verbose=show_thinking
        )
        qa_task = Task(
            description=f'''Review the following Verilog code for "Silicon-Fatal" errors.
            
FATAL ERRORS TO REJECT:
1. **MULTI-DRIVEN NETS**: The same signal assigned in `always_ff` AND `always_comb`. This is FORBIDDEN.
2. **HUGE REGISTERS**: Any array declaration larger than 128 bits (e.g. `reg [7:0] mem [0:1024]`).
   - If found: Suggest using an external memory interface OR reduce size.
3. **COMBINATIONAL LOOPS**: Massive `for` loops inside `always_comb` doing arithmetic (e.g. 16 multipliers in 1 cycle).
4. **LATCHES**: `if` without `else` in `always_comb`.

CODE:
```verilog
{current_rtl}
```

OUTPUT:
- If PASS: Reply `PASS`.
- If FAIL: Reply `FAIL: <Reason>`. Then provide the FIXED code inside ```verilog fences.

CRITICAL:
- You are a **Senior Architect**. You are NOT allowed to simplify the logic.
- If you see a violation (e.g., Combinational Loop), you must **FIX IT** by adding pipeline registers (`always_ff`), not by deleting the math.
- **NEVER** return a "toy" or "dummy" module. Maintain the full functionality of the original code, but make it clean.
''',
            expected_output='PASS or FAIL with code',
            agent=qa_agent
        )
        
        with console.status(f"[cyan]Senior Engineer Reviewing (Attempt {qa_retries+1})...[/cyan]"):
            qa_result = str(Crew(agents=[qa_agent], tasks=[qa_task]).kickoff())
            log_thinking(qa_result, step=f"QA Review {qa_retries}")
        
        if "FAIL:" in qa_result:
            console.print(f"[bold red]⚠ QA Reject:[/bold red] {qa_result.split('FAIL:')[1].splitlines()[0][:100]}...")
            # Extract fixed code
            if "```verilog" in qa_result:
                current_rtl = qa_result
                qa_retries += 1
            else:
                 # If AI complained but didn't provide code, ask Designer to fix it
                 qa_retries += 1
                 # (Logic to re-prompt designer could go here, but for now we trust the Senior's fix if present)
        else:
            qa_passed = True
            console.print("  ✓ Senior Architect Review: [green]PASS[/green]")

    rtl_path = write_verilog(name, current_rtl)
    console.print(f"  ✓ RTL saved to: [green]{rtl_path}[/green]")

    console.print("\n[bold yellow]━━━ Step 3/6: Syntax Verification ━━━[/bold yellow]")
    success, errors = run_syntax_check(rtl_path)
    tries = 0
    while not success and tries < max_retries:
        tries += 1
        console.print(f"[bold red]✗ SYNTAX ERROR (attempt {tries}/{max_retries})[/bold red]\n{errors}")
        fix_prompt = f'''Fix the following SystemVerilog code so it compiles with iverilog -g2012.

CRITICAL FIXING RULES:
1. **Unresolved Wires**: Declare ALL signals at the top of the file using `logic [width:0] name;`. If a signal is used in `assign` or `always`, it MUST be declared.
2. **"Constant Selects" Error**: If you see "constant selects in always process", move the slicing logic OUTSIDE the always block into an `assign` statement.
   - Bad: `always_comb x = reg_file[ addr[4:0] ];`
   - Good: `wire [4:0] idx = addr[4:0]; always_comb x = reg_file[idx];`
3. **Loop Syntax**: Use `integer i;` declared outside, and `for(i=0;...)`.
4. **Logic separation**: Use `always_ff` for registers, `assign` for simple logic.
5. **Bit Width Mismatches**: Check if you are assigning a value larger than the signal width (e.g. `enum [1:0]` with 5 items). Increase the width if necessary (e.g. `enum [2:0]`).

CRITICAL REQUIREMENTS:
- Keep module name exactly "{name}"
- Use Standard SystemVerilog
- Return ONLY corrected code inside ```verilog fences
- MAINTAIN DESIGN INTENT: Do NOT simplify the logic to bypass errors. If the logic is complex (e.g., matrix multiplication), FIX the syntax, do NOT replace it with simple arithmetic.

Compiler error:
{errors}

Current code:
```verilog
{open(rtl_path,'r').read()}
```
'''
        fixed = _fix_with_llm(
            agent_role='VLSI Design Engineer',
            goal=f'Fix SystemVerilog syntax for {name}',
            prompt=fix_prompt
        )
        rtl_path = write_verilog(name, fixed)
        success, errors = run_syntax_check(rtl_path)

    if not success:
        console.print(f"[bold red]✗ SYNTAX ERROR:[/bold red]\n{errors}")
        raise typer.Exit(1)
    console.print("  ✓ Verilog syntax is [green]valid[/green]")

    # ===== STEP 2.5: Formal Assertions (SVA) =====
    console.print("\n[bold yellow]━━━ Step 2.5: Formal Verification (Autonomous Vendor Mode) ━━━[/bold yellow]")
    
    with open(rtl_path, 'r') as f:
        rtl_code = f.read()

    verify_agent = get_verification_agent(llm, verbose=show_thinking)
    sva_task = Task(
        description=f'''Analyze this RTL and generate a separate SystemVerilog Assertion (SVA) module (`{name}_sva.sv`) to formally verify behavior.

Rules:
- Module name: `{name}_sva`
- Bind to target module using `bind {name} {name}_sva inst_sva (.*);`
- Use `property` and `assert property (@(posedge clk) ...)` 
- Check resets, state transitions, and critical safety logic.
- NO `initial` blocks, NO `$display`. use pure SVA.

RTL:
```verilog
{rtl_code}
```
''',
        expected_output='SVA module code in markdown fence',
        agent=verify_agent
    )
    
    with console.status("[cyan]AI is proving mathematical correctness (SymbiYosys)...[/cyan]"):
        sva_result = Crew(agents=[verify_agent], tasks=[sva_task]).kickoff()
        sva_raw = str(sva_result)
        
        # Save SVA file using robust cleaner
        write_verilog(name, sva_raw, suffix="_sva", ext=".sv")
            
        # Write SBY Config
        write_sby_config(name)
        
        # Run Formal Verification
        formal_success, formal_msg = run_formal_verification(name)
        
        if formal_success:
            console.print(f"  ✓ [bold green]Mathematical Proof PASSED[/bold green]")
            log_thinking(formal_msg, step="Formal Verification Log")
        else:
            # Check for missing tool vs actual failure
            if "SymbiYosys (sby) tool not installed" in formal_msg:
                 console.print(f"  [yellow]⚠ Formal Verification Skipped[/yellow]: [dim]SymbiYosys (sby) not installed.[/dim]")
                 console.print(f"    [dim]- Install: https://github.com/YosysHQ/oss-cad-suite-build[/dim]")
            else:
                 console.print(f"  ✗ [bold red]Formal Proof FAILED (Design is risky)[/bold red]")
                 console.print(formal_msg)

    # ===== STEP 3: Generate Testbench (self-checking) =====
    console.print("\n[bold yellow]━━━ Step 4/6: Generating Testbench (Spec + RTL Analysis) ━━━[/bold yellow]")

    tb_agent = get_testbench_agent(
        llm=llm,
        goal=f'Create a self-checking testbench for {name}',
        verbose=show_thinking
    )

    tb_task = Task(
        description=f'''Create a SystemVerilog self-checking testbench for this module.

REFERENCE SPECIFICATION:
{spec_result}

ACTUAL RTL IMPLEMENTATION:
```verilog
{rtl_code}
```

CRITICAL VERIFICATION RULES (DO NOT VIOLATE):
1. **Module Name**: Must be "{name}_tb"
2. **Signal Directions**:
   - **DUT INPUTS** must be declared as `logic` (or `reg`) in the Testbench and **DRIVEN** in `initial` blocks.
   - **DUT OUTPUTS** must be declared as `logic` (or `wire`) in the Testbench and **OBSERVED** only.
   - **NEVER** assign values to DUT OUTPUT signals in the Testbench (e.g. if `ready` is an output of DUT, do NOT write `ready = 1`).
3. **Data Types**: Use `logic` for everything.
4. **Instantiation**: Use explicit name based connection `.port(signal)`.
5. **Clock/Reset**:
   - Clock: `initial forever #5 clk = ~clk;`
   - Reset: Drive `rst_n` low for 20 units, then high.
6. **Timing & Handshaking (CRITICAL)**:
   - **WAIT FOR RESULTS**: If the DUT has a pipeline or state machine, you MUST wait multiple clock cycles before checking outputs.
   - If there is a `valid_out` or `done` signal, `wait(valid_out);` or `while(!valid_out) @(posedge clk);`.
   - If fixed latency, use `repeat(10) @(posedge clk);` (or more) to allow data to propagate.
   - Do NOT check `data_out` in the same cycle you assert inputs (unless combinational).
7. **Reporting**:
   - Use `$dumpfile("{name}.vcd")` and `$dumpvars(0, {name}_tb)`.
   - On success: `$display("TEST PASSED");`
   - On failure: `$display("TEST FAILED");`
   - Always end with `$finish;`

Return ONLY the testbench code wrapped in ```verilog fences
''',
        expected_output='Complete SystemVerilog testbench in markdown fence',
        agent=tb_agent
    )

    with console.status("[cyan]AI is creating testbench...[/cyan]"):
        tb_result = Crew(agents=[tb_agent], tasks=[tb_task]).kickoff()

    tb_raw = str(tb_result)
    log_thinking(tb_raw, step="Testbench generation")

    tb_path = write_verilog(name, tb_raw, is_testbench=True)
    console.print(f"  ✓ Testbench saved to: [green]{tb_path}[/green]")
    
    # ===== STEP 4: Run Simulation with fix loop =====
    console.print("\n[bold yellow]━━━ Step 5/6: Running Simulation ━━━[/bold yellow]")

    sim_success, sim_output = run_simulation(name)
    sim_tries = 0
    while not sim_success and sim_tries < max_retries:
        sim_tries += 1
        console.print(f"[bold red]✗ SIMULATION FAILED (attempt {sim_tries}/{max_retries})[/bold red]")

        sim_output_text = sim_output or ""

        # 1) If compilation failed, fix TB first.
        if "Compilation failed:" in sim_output_text or "syntax error" in sim_output_text:
            fix_tb_prompt = f'''Fix this SystemVerilog testbench so it compiles and avoids directionality errors.

CRITICAL FIXING RULES:
1. **Unresolved Wires**: If you see "Unable to assign to unresolved wires", it means you are driving a DUT OUTPUT. Stop driving it!
2. **Signal Directions**:
   - Check the DUT definition.
   - If a port is `output` in DUT, it is a `wire` in TB (Read-Only).
   - If a port is `input` in DUT, it is a `reg/logic` in TB (Write-Only).
3. **Format**: Return ONLY corrected testbench code inside ```verilog fences.

Simulation output / errors:
{sim_output_text}

Current RTL (do not modify unless absolutely necessary):
```verilog
{open(rtl_path,'r').read()}
```

Current testbench:
```verilog
{open(tb_path,'r').read()}
```
'''
            fixed_tb = _fix_with_llm(
                agent_role='Verification Engineer',
                goal=f'Fix testbench for {name}',
                prompt=fix_tb_prompt
            )
            tb_path = write_verilog(name, fixed_tb, is_testbench=True)
            sim_success, sim_output = run_simulation(name)
            continue

        # 2) Logic or Runtime Errors
        if "TEST FAILED" in sim_output_text or "TEST PASSED" not in sim_output_text:
            
            # AI-Based Error Classification (Production Grade)
            analyst = get_error_analyst_agent(llm, verbose=False)
            analysis_task = Task(
                description=f'''Analyze this Verification Failure.
                
Error Log:
{sim_output_text}

Is this a:
A) TESTBENCH_ERROR (Syntax, $monitor usage, race condition, compilation fail)
B) RTL_LOGIC_ERROR (Mismatch, Wrong State, Functional Failure)

Reply with ONLY "A" or "B".''',
                expected_output='Single letter A or B',
                agent=analyst
            )
            analysis = str(Crew(agents=[analyst], tasks=[analysis_task]).kickoff()).strip()
            
            is_tb_issue = "A" in analysis

            if is_tb_issue:
                 console.print("[yellow]  -> [Analyst] Root Cause: Testbench Error. Fixing TB...[/yellow]")
                 fix_tb_logic_prompt = f'''Fix the Testbench logic/syntax. The simulation failed or generated runtime errors.
                 
CRITICAL FIXING RULES:
1. **$monitor Limitations**: Do NOT use complex logic (ternary operators, function calls) inside $monitor. Move them to `assign` wires first.
2. **Race Conditions**: If "TEST FAILED" happens immediately, add delays (`#1`) before sampling or driving signals.
3. **Format**: Return ONLY corrected testbench code inside ```verilog fences.

Simulation Error/Output:
{sim_output_text}

Current RTL (Reference):
```verilog
{open(rtl_path,'r').read()}
```

Current Testbench (To Fix):
```verilog
{open(tb_path,'r').read()}
```
'''
                 fixed_tb = _fix_with_llm(
                    agent_role='Verification Engineer',
                    goal=f'Fix testbench logic for {name}',
                    prompt=fix_tb_logic_prompt
                 )
                 tb_path = write_verilog(name, fixed_tb, is_testbench=True)
                 sim_success, sim_output = run_simulation(name)
                 continue

            else:
                console.print("[yellow]  -> Detecting Design Logic mismatch. Fixing RTL...[/yellow]")
                fix_rtl_prompt = f'''The simulation did not pass. Fix the RTL (module "{name}") so that the testbench passes.

CRITICAL REQUIREMENTS:
- **NO CONVERSATION**: Return ONLY the code inside ```verilog fences. Do NOT write "Thought:", "Here is the code", or any explanation.
- Keep module name exactly "{name}"
- SystemVerilog only
- Keep ports: clk, rst_n (active-low) present
- **MAINTAIN DESIGN INTENT**: Do NOT simplify the logic to pass the test case.
  - If the design is an NPU or Processor, do NOT replace complex logic with simple static assignments.
  - You must fix the BUGS in the implementation (e.g. wrong state transitions, incorrect offsets), not delete the implementation.
  - If the testbench expects a result after N cycles, ensure your pipeline matches that latency.
- Return ONLY corrected RTL code inside ```verilog fences

Simulation output:
{sim_output_text}

Current testbench (do not change in this step):
```verilog
{open(tb_path,'r').read()}
```

Current RTL:
```verilog
{open(rtl_path,'r').read()}
```
'''
                fixed_rtl = _fix_with_llm(
                    agent_role='VLSI Design Engineer',
                    goal=f'Fix RTL behavior for {name}',
                    prompt=fix_rtl_prompt
                )
                rtl_path = write_verilog(name, fixed_rtl)

                success, errors = run_syntax_check(rtl_path)
                if not success:
                    sim_output = f"RTL fix introduced syntax error:\n{errors}"
                    continue

                sim_success, sim_output = run_simulation(name)
                continue

        # Default: improve TB robustness
        fix_tb_prompt = f'''Improve this Verilog-2005 testbench so it robustly checks the design and prints TEST PASSED/TEST FAILED.

CRITICAL REQUIREMENTS:
- **NO CONVERSATION**: Return ONLY the code inside ```verilog fences.
- Testbench module name must be "{name}_tb"
- Must print exactly "TEST PASSED" on success
- Must print exactly "TEST FAILED" on failure
- **DO NOT LOWER STANDARDS**: Do not remove test cases just to make it pass.
- **TIMING**: Ensure you wait enough cycles (`repeat(10) @(posedge clk)`) for results to propagate through pipelines.
- Return ONLY corrected testbench code inside ```verilog fences

Current RTL:
```verilog
{open(rtl_path,'r').read()}
```

Current testbench:
```verilog
{open(tb_path,'r').read()}
```
'''
        fixed_tb = _fix_with_llm(
            agent_role='Verification Engineer',
            goal=f'Improve testbench for {name}',
            prompt=fix_tb_prompt
        )
        tb_path = write_verilog(name, fixed_tb, is_testbench=True)
        sim_success, sim_output = run_simulation(name)

    if not sim_success:
        console.print(f"[bold red]✗ SIMULATION FAILED:[/bold red]\n{sim_output}")
        raise typer.Exit(1)

    sim_lines = sim_output.strip().split('\n')
    for line in sim_lines[-8:]:
        console.print(f"  [dim]{line}[/dim]")
    console.print("  ✓ Simulation [green]passed[/green]")
    
    if skip_openlane:
        console.print("\n[bold green]✓ Stopped after simulation (--skip-openlane).[/bold green]")
        return

    # ===== STEP 5: Generate Config =====
    console.print("\n[bold yellow]━━━ Step 6/7: Generating OpenLane Config ━━━[/bold yellow]")
    
    config_agent = get_designer_agent(
        llm=llm, 
        goal="Configure OpenLane flow", 
        verbose=show_thinking
    )
    
    config_task = Task(
        description=f'''Create the OpenLane `config.tcl` for the design "{name}".
        
CRITICAL SETTINGS:
1. **Design Name**: `set ::env(DESIGN_NAME) "{name}"`
2. **Source File**: `set ::env(VERILOG_FILES) "$::env(DESIGN_DIR)/src/{name}.v"`
3. **Clock & Performance (Universal Standard)**:
   - **Detect Port**: Find the clock port in RTL (clk, clock, etc).
   - **Infer Period**: 
     - "High Performance/Processor/AI": `set ::env(CLOCK_PERIOD) "5.0"` (200MHz)
     - " Standard/Control": `set ::env(CLOCK_PERIOD) "10.0"` (100MHz)
     - "Low Power/IoT": `set ::env(CLOCK_PERIOD) "20.0"` (50MHz)
     - Default: "10.0"
4. **Sizing & Utilization**:
   - `set ::env(FP_SIZING) relative`
   - **TINY DESIGNS (Gates, Muxes, Counters)**: Set `FP_CORE_UTIL` 5 (Very Low) to ensure enough area for Power Grid (PDN).
   - "Compact/Small (Modules)": `FP_CORE_UTIL` 40.
   - "Complex/Routing Heavy (SoC/CPU)": `FP_CORE_UTIL` 30.
   - `set ::env(PL_TARGET_DENSITY) 0.50`
5. **PDK**:
   - `set ::env(PDK) "sky130A"`
   - `set ::env(STD_CELL_LIBRARY) "sky130_fd_sc_hd"`
   - `set ::env(VDD_NETS) [list {{vccd1}}]`
   - `set ::env(GND_NETS) [list {{vssd1}}]`

Output Format:
Return ONLY valid TCL commands inside ```tcl fences.
''',
        expected_output="OpenLane config.tcl content",
        agent=config_agent
    )
    
    with console.status("[cyan]AI is configuring the backend flow...[/cyan]"):
        config_result = str(Crew(agents=[config_agent], tasks=[config_task]).kickoff())
    
    config_path = write_config(name, config_result)
    console.print(f"  ✓ Config saved to: [green]{config_path}[/green]")
    log_thinking(config_result, step="Config generation")

    # ===== STEP 6: Run OpenLane =====
    console.print("\n[bold yellow]━━━ Step 7/7: Running OpenLane (RTL → GDSII) ━━━[/bold yellow]")
    
    run_bg = typer.confirm("OpenLane hardening can take 10-30+ minutes. Run in background?", default=False)
    
    if run_bg:
         console.print("  [dim]Launching background process...[/dim]")
    else:
         console.print("  [dim]Running OpenLane (this may take 10-30 minutes)...[/dim]")

    # Auto-healing OpenLane Loop
    ol_tries = 0
    ol_success = False
    ol_result = ""

    while ol_tries <= max_retries:
        if ol_tries > 0:
             console.print(f"[bold yellow]  ↻ Retry {ol_tries}/{max_retries}: Fix Attempted...[/bold yellow]")

        ol_success, ol_result = run_openlane(name, background=run_bg)
        
        # Background mode: cannot verify immediately
        if run_bg:
            if ol_success:
                console.print(f"  ✓ [green]{ol_result}[/green]")
                console.print(f"  [dim]Monitor logs: tail -f {OPENLANE_ROOT}/designs/{name}/harden.log[/dim]")
            else:
                console.print(f"[bold red]✗ Failed to start background process:[/bold red] {ol_result}")
            return # Exit build command

        if ol_success:
             console.print(f"  ✓ GDSII generated: [green]{ol_result}[/green]")
             break
        
        # Failure Analysis
        ol_tries += 1
        if ol_tries > max_retries:
            console.print(f"[bold red]✗ OpenLane Hardening Failed after {max_retries} attempts.[/bold red]")
            console.print(f"  Last Error: {ol_result[-1000:]}")
            raise typer.Exit(1)

        console.print(f"[bold red]✗ OpenLane Error (Attempt {ol_tries}):[/bold red]")
        console.print(f"  [dim]{ol_result[-500:]}[/dim]") # Show snippet

        # AI Fixer
        fix_ol_prompt = f'''OpenLane Hardening Failed. Analyze the error and fix `config.tcl` or the RTL.

ERROR LOG (Tail):
{ol_result[-2500:]}

Current Config ({name}/config.tcl):
```tcl
{open(config_path, 'r').read()}
```

ANALYSIS GUIDE:
1. **PDK/Env Errors**: If "PDK_ROOT not found", suggest nothing (System handles it).
2. **Linter/Synthesis Errors**: If "syntax error" or "module not found" or "multi-driven", FIX THE RTL.
   - Reply with: `FIX_RTL: <Corrected Verilog Code>`
3. **Flow Errors**: If "detailed placement failed" or "congestion" or "pin access", LOOSEN constraints in Config.
   - Increase `FP_CORE_UTIL` (e.g. 40 -> 30).
   - Decrease `PL_TARGET_DENSITY` (e.g. 0.50 -> 0.40).
   - Reply with: `FIX_CONFIG: <Corrected TCL Code>`

OUTPUT FORMAT:
Start your response with either `FIX_RTL:` or `FIX_CONFIG:` followed strictly by the code block.
'''
        
        fix_ol_agent = Agent(
            role='Physical Design Lead',
            goal='Fix OpenLane Hardening Errors',
            backstory='Expert in OpenLane flow, Tcl configs, and PPA optimization.',
            llm=llm,
            verbose=show_thinking
        )
        fix_task = Task(description=fix_ol_prompt, expected_output='Fix string starting with tag', agent=fix_ol_agent)
        
        with console.status("[cyan]AI is analyzing backend failure...[/cyan]"):
            fix_result = str(Crew(agents=[fix_ol_agent], tasks=[fix_task]).kickoff())
            log_thinking(fix_result, step=f"Backend Fix {ol_tries}")
        
        # Apply Fix
        if "FIX_CONFIG:" in fix_result:
            parts = fix_result.split("FIX_CONFIG:")
            if len(parts) > 1:
                # Use hardened write_config tool
                write_config(name, parts[1])
                console.print("  ✓ Applied Config Fix")
            else:
                console.print("[red]AI returned invalid Config fix format. [/red]")

        elif "FIX_RTL:" in fix_result:
             parts = fix_result.split("FIX_RTL:")
             if len(parts) > 1:
                 new_rtl = parts[1].split("```verilog")[1].split("```")[0].strip() if "```verilog" in parts[1] else parts[1].strip()
                 if "```" in new_rtl: new_rtl = new_rtl.replace("```","")
                 
                 write_verilog(name, new_rtl)
                 console.print("  ✓ Applied RTL Fix (Re-running flow)")
             else:
                 console.print("[red]AI returned invalid RTL fix format.[/red]")
        else:
             console.print("[red]AI could not determine a fix. Retrying...[/red]")
    
    if not run_bg:
        # ===== STEP 7: Physical Feedback Loop (Autonomous Vendor) =====
        console.print("\n[bold yellow]━━━ Step 7: Physical Verification (PPA Analysis) ━━━[/bold yellow]")
        
        metrics, msg = check_physical_metrics(name)
        
        if metrics:
            console.print(Panel(
                f"**Physical Metrics:**\n"
                f"- Area: {metrics['chip_area_um2']:.2f} um²\n"
                f"- Logic Utility: {metrics['utilization']*100:.2f}%\n"
                f"- Timing Slack (WNS): {metrics['timing_wns']} ns (Negative is BAD)\n"
                f"- Total Power: {metrics['power_total']} W",
                title="🏭 Fabrication Stats"
            ))
            
            # Autonomous Decision Logic
            needs_opt = False
            opt_reason = ""
            
            if metrics['timing_wns'] < 0:
                needs_opt = True
                opt_reason += f"Timing Violation (Slack: {metrics['timing_wns']}ns). Logic is too slow. "
                
            if metrics['utilization'] > 0.85:
                needs_opt = True
                opt_reason += f"Congestion Risk (Util: {metrics['utilization']*100:.0f}%). too crowded. "

            if needs_opt:
                console.print(f"[bold red]⚠ DESIGN REJECTED: {opt_reason}[/bold red]")
                
                # Feedback Prompt for PPA Optimization
                fix_ppa_prompt = f'''The previous design failed Physical Verification. You must optimized the Verilog RTL.

Failure Reason: {opt_reason}
Design Name: {name}

OPTIMIZATION STRATEGIES:
1. **Timing Failures (Slack < 0)**:
   - Break long combinational logic paths.
   - Insert pipeline registers between complex operations (Multiplier -> Reg -> Adder).
   - Use `always_ff` to register outputs.
2. **Congestion/Area**:
   - Reuse operators (Resource Sharing).
   - Simplify logic equations.

Current RTL:
```verilog
{open(rtl_path,'r').read()}
```

Return ONLY the optimized SystemVerilog code inside ```verilog fences.
'''
                optimized_rtl = _fix_with_llm(
                    agent_role='Senior Backend Engineer',
                    goal=f'Optimize RTL for PPA (Timing/Area) - {name}',
                    prompt=fix_ppa_prompt
                )
                
                # Save optimized design
                opt_path = write_verilog(name, optimized_rtl)
                console.print(f"  ✓ Optimized RTL generated: [green]{opt_path}[/green]")
                console.print(Panel(
                    "The design has been largely modified for physical constraints.\n"
                    "Please re-run the build command to verify and harden the new version.",
                    title="↺ Optimization Loop Triggered",
                    style="blue"
                ))
            else:
                 console.print("[bold green]✓ DESIGN ACCEPTED FOR FABRICATION[/bold green]")
                 console.print(Panel(
                     "You are ready to Tape-out! 🚀\n"
                     f"GDSII: designs/{name}/runs/agentrun/results/final/gds/{name}.gds",
                     title="Universal Chip Vendor - Certificate"
                 ))
        else:
            console.print(f"[red]Could not read metrics: {msg}[/red]")

    if ol_success:
        if run_bg:
            console.print(f"  ✓ [green]{ol_result}[/green]")
            console.print(f"  [dim]Monitor with: tail -f {OPENLANE_ROOT}/designs/{name}/harden.log[/dim]")
            
            # Print Final Summary immediately for BG case
            console.print("\n" + "="*50)
            console.print(Panel(
                f"[bold green]✓ CHIP GENERATION INITIATED![/bold green]\n\n"
                f"Design: [cyan]{name}[/cyan]\n"
                f"Status: [yellow]Hardening in background[/yellow]\n\n"
                f"Log file: {OPENLANE_ROOT}/designs/{name}/harden.log",
                title="🚀 Background Job Started"
            ))
            return

        console.print(f"  ✓ GDSII generated: [green]{ol_result}[/green]")
    else:
        console.print(f"[bold red]✗ OpenLane failed[/bold red]")
        console.print(f"  Error: {ol_result[:500]}...")
        raise typer.Exit(1)
    
    # ===== FINAL: Summary =====
    console.print("\n" + "="*50)
    console.print(Panel(
        f"[bold green]✓ CHIP GENERATION COMPLETE![/bold green]\n\n"
        f"Design: [cyan]{name}[/cyan]\n"
        f"GDSII: [yellow]{ol_result}[/yellow]\n\n"
        f"Run verification: [dim]python main.py verify {name}[/dim]",
        title="🎉 Success"
    ))


@app.command()
def verify(name: str = typer.Argument(..., help="Design name to verify")):
    """Run verification on an existing design."""
    console.print(f"[yellow]Running verification for {name}...[/yellow]")
    output = run_verification(name)
    console.print(output)


if __name__ == "__main__":
    app()