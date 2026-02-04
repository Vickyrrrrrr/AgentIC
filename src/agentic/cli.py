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
from .config import OPENLANE_ROOT, LLM_MODEL, LLM_BASE_URL, LLM_API_KEY, NVIDIA_CONFIG, GROQ_CONFIG
from .agents.designer import get_designer_agent
from .agents.testbench_designer import get_testbench_agent
from .agents.verifier import get_verification_agent, get_error_analyst_agent
from .tools.vlsi_tools import (
    write_verilog,
    write_config, 
    run_syntax_check,
    read_file_content,
    run_simulation, 
    run_openlane,
    run_verification,
    SecurityCheck
)

# --- INITIALIZE ---
app = typer.Typer()
console = Console()

# Setup Brain
def get_llm():
    """Returns the LLM instance for agents, with NVIDIA -> Groq fallback."""
    # 1. Try NVIDIA (Primary)
    try:
        return LLM(
            model=NVIDIA_CONFIG["model"],
            base_url=NVIDIA_CONFIG["base_url"],
            api_key=NVIDIA_CONFIG["api_key"]
        )
    except Exception as e:
        console.print(f"[yellow]⚠ NVIDIA Init failed: {e}. Falling back to Groq...[/yellow]")
    
    # 2. Fallback to Groq if available
    if GROQ_CONFIG["api_key"]:
        return LLM(
            model=GROQ_CONFIG["model"],
            base_url=GROQ_CONFIG["base_url"],
            api_key=GROQ_CONFIG["api_key"]
        )
    
    # 3. Last Resort: Default/Local
    return LLM(
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY
    )

@app.command()
def simulate(
    name: str = typer.Option(..., "--name", "-n", help="Design name (e.g., counter)"),
):
    """Run simulation on an existing design without regenerating RTL."""
    console.print(Panel(
        f"[bold cyan]AgentIC: Manual Simulation Mode[/bold cyan]\n"
        f"Design: [yellow]{name}[/yellow]",
        title="🚀 Starting Simulation"
    ))
    
    sim_success, sim_output = run_simulation(name)
    
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
            tools=[run_syntax_check, read_file_content]
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
    
    # ===== STEP 1/2: Generate RTL with syntax-fix loop =====
    console.print("\n[bold yellow]━━━ Step 1/5: Generating Verilog RTL ━━━[/bold yellow]")

    rtl_agent = get_designer_agent(
        llm=llm,
        goal=f'Create synthesizable SystemVerilog for: {desc}',
        verbose=show_thinking
    )

    rtl_task = Task(
        description=f'''Design an INDUSTRY STANDARD SystemVerilog module named "{name}" that implements: {desc}

CRITICAL VERILOG RULES (STRICTLY FOLLOW OR COMPILATION WILL FAIL):
1. **Module & Ports**:
   - Module name must be exactly "{name}"
   - ALWAYS include `input logic clk`, `input logic rst_n` (active-low asynchronous reset).
   - **FLATTENED PORTS**: Do not use multidimensional arrays for top-level ports. Use `input logic [WIDTH*COUNT-1:0] flat_bus`.
   - Use `parameter` for all bit-widths.

2. **Data Types & Declarations**:
   - Use `logic` for ALL internal signals and ports (Standard SystemVerilog 2012).
   - **DECLARE BEFORE USE**: You MUST declare signals (`logic [31:0] my_sig;`) at the top of the module interaction section, BEFORE they are used in any `always` block or `assign`.

3. **Coding Logic Structure**:
   - **SEPARATE LOGIC**:
     - Use `always_ff @(posedge clk or negedge rst_n)` ONLY for registers/flip-flops.
     - Use `assign` statements for renaming wires or simple math (e.g. `assign opcode = ir[6:0];`).
     - Use `always_comb` ONLY for complex muxing/Next-State-Logic.
   - **ENUM WIDTHS**: When defining `typedef enum logic [W-1:0] { ... } name_t`, ensure `W` is large enough to hold all enum values. 
     - Example: 5 states require `logic [2:0]` (3 bits), NOT `logic [1:0]`.
   - **NO CONSTANT SELECTS IN ALWAYS**: Do NOT do `reg_file[ ir[19:15] ]` inside an `always` block. 
     - CORRECT: 
       ```verilog
       wire [4:0] rs1_addr = ir[19:15]; // Create alias wire using assign
       ...
       always_comb val = reg_file[rs1_addr];
       ```
   - **LOOPS**: For register files, declare `integer i;` OUTSIDE the always block. Use `for(i=0; i<32; i=i+1)`.
   - **CASTING**: Do NOT use `signed'(x)`. Use `$signed(x)` for signed arithmetic.

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

    # [1.5] Security Audit
    is_safe, msg = SecurityCheck(rtl_raw)
    if not is_safe:
        console.print(f"[bold red]🛑 SECURITY AUDIT FAILED[/bold red]: {msg}")
        raise typer.Exit(1)
    console.print(f"  ✓ Logic Security Check: [green]{msg}[/green]")

    rtl_path = write_verilog(name, rtl_raw)
    console.print(f"  ✓ RTL saved to: [green]{rtl_path}[/green]")

    console.print("\n[bold yellow]━━━ Step 2/5: Syntax Verification ━━━[/bold yellow]")
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
    console.print("\n[bold yellow]━━━ Step 2.5: Formal Verification Setup ━━━[/bold yellow]")
    
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

RTL:
```verilog
{rtl_code}
```
''',
        expected_output='SVA module code in markdown fence',
        agent=verify_agent
    )
    # Optional SVA step (non-blocking for now as full formal tools aren't in this Env)
    # In production, we would run SymbiYosys here.
    # sva_result = Crew(agents=[verify_agent], tasks=[sva_task]).kickoff()
    # console.print("  ✓ SVA Assertions generated (Formal verification skipped in MVP)")

    # ===== STEP 3: Generate Testbench (self-checking) =====
    console.print("\n[bold yellow]━━━ Step 3/5: Generating Testbench ━━━[/bold yellow]")

    tb_agent = get_testbench_agent(
        llm=llm,
        goal=f'Create a self-checking testbench for {name}',
        verbose=show_thinking
    )

    tb_task = Task(
        description=f'''Create a SystemVerilog self-checking testbench for this module:

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
6. **Reporting**:
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
    console.print("\n[bold yellow]━━━ Step 4/5: Running Simulation ━━━[/bold yellow]")

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
- Keep module name exactly "{name}"
- SystemVerilog only
- Keep ports: clk, rst_n (active-low) present
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
- Testbench module name must be "{name}_tb"
- Must print exactly "TEST PASSED" on success
- Must print exactly "TEST FAILED" on failure
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
    console.print("\n[bold yellow]━━━ Step 5/6: Generating OpenLane Config ━━━[/bold yellow]")
    
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
3. **Clock**:
   - Check the RTL to find the clock port (usually `clk`, `clock`, or `clk_i`).
   - `set ::env(CLOCK_PORT) "YOUR_CLOCK_PORT"`
   - `set ::env(CLOCK_PERIOD) "10.0"`
4. **Sizing**:
   - `set ::env(FP_SIZING) relative`
   - `set ::env(FP_CORE_UTIL) 40`
   - `set ::env(PL_TARGET_DENSITY) 0.5`
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
    console.print("\n[bold yellow]━━━ Step 6/6: Running OpenLane (RTL → GDSII) ━━━[/bold yellow]")
    
    run_bg = typer.confirm("OpenLane hardening can take 10-30+ minutes. Run in background?", default=True)
    
    if run_bg:
         console.print("  [dim]Launching background process...[/dim]")
    else:
         console.print("  [dim]Running OpenLane (this may take 10-30 minutes)...[/dim]")

    ol_success, ol_result = run_openlane(name, background=run_bg)
    
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