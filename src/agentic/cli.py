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
    check_physical_metrics,
    run_lint_check
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
1. **Timing is USUALLY THE PROBLEM**: If "TEST FAILED" appears, the testbench is checking outputs TOO EARLY.
   - Count the FSM states in the RTL. Wait at least (num_states + 10) clock cycles.
   - Use `repeat(25) @(posedge clk);` minimum before checking ANY output.
   - If there's a `done` or `valid` signal, use `while(!done) @(posedge clk);`
2. **Race Conditions**: Add `#1` delays after clock edges before sampling.
3. **Reset**: Ensure reset is held for at least 4 clock cycles.
4. **Between Tests**: Wait for FSM to return to IDLE with `repeat(10) @(posedge clk);`
5. **Format**: Return ONLY corrected testbench code inside ```verilog fences.

Simulation Error/Output:
{sim_output_text}

Current RTL (Reference - count the FSM states):
```verilog
{open(rtl_path,'r').read()}
```

Current Testbench (To Fix - increase wait cycles):
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
    show_thinking: bool = typer.Option(False, "--show-thinking", help="Print DeepSeek <think> reasoning for each generation/fix step")
):
    """Build a chip from natural language description."""
    
    """Build a chip from natural language description (Autonomous Orchestrator 2.0)."""
    
    from .orchestrator import BuildOrchestrator
    
    console.print(Panel(
        f"[bold cyan]AgentIC: Natural Language → GDSII[/bold cyan]\n"
        f"Design: [yellow]{name}[/yellow]\n"
        f"Description: {desc}",
        title="🚀 Starting Autonomous Orchestrator"
    ))

    llm = get_llm()
    
    orchestrator = BuildOrchestrator(
        name=name,
        desc=desc,
        llm=llm,
        max_retries=max_retries,
        verbose=show_thinking,
        skip_openlane=skip_openlane
    )
    
    orchestrator.run()
@app.command()
def verify(name: str = typer.Argument(..., help="Design name to verify")):
    """Run verification on an existing design."""
    console.print(f"[yellow]Running verification for {name}...[/yellow]")
    output = run_verification(name)
    console.print(output)


if __name__ == "__main__":
    app()