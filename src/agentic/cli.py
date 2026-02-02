#!/usr/bin/env python3
"""
AgentIC - Natural Language to GDSII Pipeline
=============================================
Uses CrewAI + Ollama (DeepSeek) to generate chips from natural language.

Usage:
    python main.py build --name counter --desc "8-bit counter with enable and reset"
"""

import os
import sys
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from crewai import Agent, Task, Crew, LLM

# Local imports
from .config import OPENLANE_ROOT, LLM_MODEL, LLM_BASE_URL, LLM_API_KEY
from .tools.vlsi_tools import (
    write_verilog, 
    run_syntax_check,
    run_simulation, 
    run_openlane,
    run_verification
)

# --- INITIALIZE ---
app = typer.Typer()
console = Console()

# Setup Brain
def get_llm():
    """Returns the LLM instance for agents."""
    return LLM(
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY
    )

# --- THE BUILD COMMAND ---
@app.command()
def build(
    name: str = typer.Option(..., "--name", "-n", help="Design name (e.g., counter)"),
    desc: str = typer.Option(..., "--desc", "-d", help="Natural language description")
):
    """Build a chip from natural language description."""
    
    console.print(Panel(
        f"[bold cyan]AgentIC: Natural Language → GDSII[/bold cyan]\n"
        f"Design: [yellow]{name}[/yellow]\n"
        f"Description: {desc}",
        title="🚀 Starting Pipeline"
    ))

    llm = get_llm()
    
    # ===== STEP 1: Generate RTL =====
    console.print("\n[bold yellow]━━━ Step 1/5: Generating Verilog RTL ━━━[/bold yellow]")
    
    rtl_agent = Agent(
        role='VLSI Design Engineer',
        goal=f'Create synthesizable Verilog for: {desc}',
        backstory='Expert in digital design with Sky130 PDK experience.',
        llm=llm,
        verbose=False
    )
    
    rtl_task = Task(
        description=f'''Design a Verilog module named "{name}" that implements: {desc}
        
        CRITICAL REQUIREMENTS:
        - Module name must be exactly "{name}"
        - Use VERILOG-2005 ONLY (NOT SystemVerilog)
        - Use "always @(*)" NOT "always_comb"
        - Use "reg" for outputs in combinational logic
        - Include clock (clk) and active-low reset (rst_n) if sequential
        - Return ONLY the Verilog code wrapped in ```verilog fences
        - NO explanations, NO comments about the code
        
        Example format:
        ```verilog
        module {name}(...);
        ...
        endmodule
        ```
        ''',
        expected_output='Complete Verilog-2005 module code in markdown fence',
        agent=rtl_agent
    )
    
    with console.status("[cyan]AI is designing your chip...[/cyan]"):
        rtl_result = Crew(agents=[rtl_agent], tasks=[rtl_task]).kickoff()
    
    rtl_path = write_verilog(name, str(rtl_result))
    console.print(f"  ✓ RTL saved to: [green]{rtl_path}[/green]")
    
    # ===== STEP 2: Syntax Check RTL =====
    console.print("\n[bold yellow]━━━ Step 2/5: Syntax Verification ━━━[/bold yellow]")
    
    success, errors = run_syntax_check(rtl_path)
    if not success:
        console.print(f"[bold red]✗ SYNTAX ERROR:[/bold red]\n{errors}")
        raise typer.Exit(1)
    console.print("  ✓ Verilog syntax is [green]valid[/green]")
    
    # ===== STEP 3: Generate Testbench =====
    console.print("\n[bold yellow]━━━ Step 3/5: Generating Testbench ━━━[/bold yellow]")
    
    # Read the generated RTL for context
    with open(rtl_path, 'r') as f:
        rtl_code = f.read()
    
    tb_agent = Agent(
        role='Verification Engineer',
        goal=f'Create a testbench for {name}',
        backstory='Expert in Verilog verification and simulation.',
        llm=llm,
        verbose=False
    )
    
    tb_task = Task(
        description=f'''Create a Verilog-2005 testbench for this module:
        
```verilog
{rtl_code}
```

CRITICAL REQUIREMENTS:
- Module name must be "{name}_tb"
- Use VERILOG-2005 ONLY (NOT SystemVerilog)
- Use explicit port connections: .clk(clk), .a(a), etc.
- Use integer delays like #5 or #10, NOT #5ns or #10ns
- Clock: forever #5 clk = ~clk (inside initial block)
- Put $monitor INSIDE an initial block
- Use $dumpfile("{name}.vcd") and $dumpvars(0, {name}_tb)
- End with $finish
- Return ONLY the testbench code wrapped in ```verilog fences

Example structure:
```verilog
module {name}_tb;
  reg clk, rst_n;
  // declare inputs as reg, outputs as wire
  
  {name} uut(.clk(clk), .rst_n(rst_n), ...);
  
  initial begin
    clk = 0;
    forever #5 clk = ~clk;
  end
  
  initial begin
    $dumpfile("{name}.vcd");
    $dumpvars(0, {name}_tb);
    $monitor(...);
    // test sequence
    $finish;
  end
endmodule
```
''',
        expected_output='Complete Verilog-2005 testbench in markdown fence',
        agent=tb_agent
    )
    
    with console.status("[cyan]AI is creating testbench...[/cyan]"):
        tb_result = Crew(agents=[tb_agent], tasks=[tb_task]).kickoff()
    
    tb_path = write_verilog(name, str(tb_result), is_testbench=True)
    console.print(f"  ✓ Testbench saved to: [green]{tb_path}[/green]")
    
    # ===== STEP 4: Run Simulation =====
    console.print("\n[bold yellow]━━━ Step 4/5: Running Simulation ━━━[/bold yellow]")
    
    sim_success, sim_output = run_simulation(name)
    if not sim_success:
        console.print(f"[bold red]✗ SIMULATION FAILED:[/bold red]\n{sim_output}")
        raise typer.Exit(1)
    
    # Show last few lines of simulation
    sim_lines = sim_output.strip().split('\n')
    for line in sim_lines[-5:]:
        console.print(f"  [dim]{line}[/dim]")
    console.print("  ✓ Simulation [green]passed[/green]")
    
    # ===== STEP 5: Run OpenLane =====
    console.print("\n[bold yellow]━━━ Step 5/5: Running OpenLane (RTL → GDSII) ━━━[/bold yellow]")
    console.print("  [dim]This may take 5-10 minutes...[/dim]")
    
    # Create config.tcl from template
    template_config = f"{OPENLANE_ROOT}/designs/simple_counter/config.tcl"
    new_config = f"{OPENLANE_ROOT}/designs/{name}/config.tcl"
    
    if os.path.exists(template_config) and not os.path.exists(new_config):
        with open(template_config, 'r') as f:
            content = f.read().replace("simple_counter", name)
        # Fix: Ensure we only synthesize the design, not the testbench
        if "[glob $::env(DESIGN_DIR)/src/*.v]" in content:
            content = content.replace(
                "[glob $::env(DESIGN_DIR)/src/*.v]", 
                f'"$::env(DESIGN_DIR)/src/{name}.v"'
            )
        with open(new_config, 'w') as f:
            f.write(content)
        console.print(f"  ✓ Config created from template")
    
    ol_success, ol_result = run_openlane(name)
    
    if ol_success:
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