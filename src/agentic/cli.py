#!/usr/bin/env python3
"""
AgentIC - Natural Language to GDSII Pipeline
=============================================
Uses CrewAI + LLM (DeepSeek/Llama/Groq) to generate chips from natural language.


Usage:
    python3 main.py build --name counter --desc "8-bit counter with enable and reset"
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
from .config import (
    OPENLANE_ROOT,
    LLM_MODEL,
    LLM_BASE_URL,
    LLM_API_KEY,
    NVIDIA_CONFIG,
    LOCAL_CONFIG,
    CLOUD_CONFIG,
    PDK,
    SIM_BACKEND_DEFAULT,
    COVERAGE_FALLBACK_POLICY_DEFAULT,
    COVERAGE_PROFILE_DEFAULT,
)
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
    run_lint_check,
    run_gls_simulation,
    signoff_check_tool,
    startup_self_check,
)

# --- INITIALIZE ---
app = typer.Typer()
from rich.theme import Theme
claude_theme = Theme({
    "info": "dim white",
    "accent": "#d97757",
    "success": "#32997b",
    "warning": "#e0b04a",
    "error": "#d45851",
    "heading": "bold #e5e1d8",
    "border": "#8f8a80",
    "spinner": "#d97757"
})
console = Console(theme=claude_theme)


CREDENTIALS_FILE = os.path.expanduser("~/.agentic_credentials.json")








def check_dependencies(skip_openlane: bool):
    import shutil
    import typer
    from rich.panel import Panel
    
    missing = []
    
    # Only block for Docker if the user actually wants physical GDSII layout!
    if not skip_openlane and not shutil.which("docker"):
        missing.append("🐳 [bold red]Docker[/bold red] (required for OpenLane physical GDSII synthesis)")
        
    if not shutil.which("yosys"):
        missing.append("🛠️  [bold red]OSS CAD Suite[/bold red] (required for verifying the logic via Yosys/Verilator)")
        
    if missing:
        console.print(Panel(
            "AgentIC requires standard open-source hardware tools to compile silicon.\n\n"
            "The following tools are missing from your system PATH:\n" + 
            "\n".join(["  - " + m for m in missing]) + 
            "\n\n[info]💡 Note: You can bypass the physical layout phase by adding `--skip-openlane` to your build command (Docker will not be required).[/info]\n\n"
            "[info]Please refer to the installation guide (README) for setup instructions.[/info]",
            title="[bold red]❌ Missing Dependencies[/bold red]",
            border_style="red"
        ))
        raise typer.Exit(1)


def verify_license():
    """Verify license key before running any CLI commands (Real Lemon Squeezy integration)"""
    # Developer backdoor/bypass
    if os.environ.get("AGENTIC_DEV_MODE") == "1":
        return
        
    # Also skip license checking when running locally (not packaged by PyInstaller)
    if not getattr(sys, "frozen", False) and not os.environ.get("AGENTIC_FORCE_LICENSE_CHECK"):
        return

    if not os.path.exists(CREDENTIALS_FILE):
        console.print(Panel(
            "[error]Authorization Required[/error]
"
            "You do not have a valid license locally.
"
            "Please run: [accent]agentic login <your_license_key>[/accent]",
            title="🔒 License Check Failed"
        ))
        raise typer.Exit(1)
    
    import json
    import requests
    try:
        with open(CREDENTIALS_FILE, 'r') as f:
            data = json.load(f)
            key = data.get("license_key", "")
            
            # Real Lemon Squeezy integration
            try:
                # Need a real API endpoint, but using validate endpoint for now
                response = requests.post(
                    "https://api.lemonsqueezy.com/v1/licenses/validate",
                    headers={"Accept": "application/json"},
                    data={"license_key": key}
                )
                
                if response.status_code != 200 or not response.json().get("valid"):
                    if key.startswith("sk_test_dev_bypass"): # local backdoor
                        pass
                    else:
                        console.print(Panel(
                            f"[error]Invalid License Key[/error]
"
                            f"The key we found ({key[:8]}...) was rejected by the server.
"
                            "Please run: [accent]agentic login <your_license_key>[/accent]",
                            title="🔒 License Check Failed"
                        ))
                        raise typer.Exit(1)
            except requests.exceptions.RequestException as e:
                console.print(f"[warning]Warning: Could not connect to license server to verify your key. Allowing offline bypass for now.[/warning]")
                # We let it pass if there's no internet since they already bought it

            # --- Inject user-provided API keys into runtime configs ---
            from . import config
            if data.get("nvidia_api_key"): 
                config.CLOUD_CONFIG["api_key"] = data["nvidia_api_key"]
                os.environ["NVIDIA_API_KEY"] = data["nvidia_api_key"]
            if data.get("groq_api_key"): 
                config.GROQ_CONFIG["api_key"] = data["groq_api_key"]
                os.environ["GROQ_API_KEY"] = data["groq_api_key"]
            if data.get("glm_api_key"): 
                config.GLM_CONFIG["api_key"] = data["glm_api_key"]
                os.environ["GLM_API_KEY"] = data["glm_api_key"]
                
    except Exception as e:
        console.print(f"[error]Error reading credentials: {e}[/error]")
        raise typer.Exit(1)
    
    import json
    import requests
    try:
        with open(CREDENTIALS_FILE, 'r') as f:
            data = json.load(f)
            key = data.get("license_key", "")
            
            # Real Lemon Squeezy integration
            try:
                # Need a real API endpoint, but using validate endpoint for now
                response = requests.post(
                    "https://api.lemonsqueezy.com/v1/licenses/validate",
                    headers={"Accept": "application/json"},
                    data={"license_key": key}
                )
                
                if response.status_code != 200 or not response.json().get("valid"):
                    if key.startswith("sk_test_dev_bypass"): # local backdoor
                        pass
                    else:
                        console.print(Panel(
                            f"[error]Invalid License Key[/error]
"
                            f"The key we found ({key[:8]}...) was rejected by the server.
"
                            "Please run: [accent]agentic login <your_license_key>[/accent]",
                            title="🔒 License Check Failed"
                        ))
                        raise typer.Exit(1)
            except requests.exceptions.RequestException as e:
                console.print(f"[warning]Warning: Could not connect to license server to verify your key. Allowing offline bypass for now.[/warning]")
                # We let it pass if there's no internet since they already bought it

            # --- Inject user-provided API keys into runtime configs ---
            from . import config
            if data.get("nvidia_api_key"): 
                config.CLOUD_CONFIG["api_key"] = data["nvidia_api_key"]
                os.environ["NVIDIA_API_KEY"] = data["nvidia_api_key"]
            if data.get("groq_api_key"): 
                config.GROQ_CONFIG["api_key"] = data["groq_api_key"]
                os.environ["GROQ_API_KEY"] = data["groq_api_key"]
            if data.get("glm_api_key"): 
                config.GLM_CONFIG["api_key"] = data["glm_api_key"]
                os.environ["GLM_API_KEY"] = data["glm_api_key"]
                
    except Exception as e:
        console.print(f"[error]Error reading credentials: {e}[/error]")
        raise typer.Exit(1)
    
    import json
    try:
        with open(CREDENTIALS_FILE, 'r') as f:
            data = json.load(f)
            key = data.get("license_key", "")
            
            # TODO: Replace with real API call to Stripe/LemonSqueezy
            # For now, we simulate that any key starting with "sk_live_" is valid
            if not key.startswith("sk_live_"):
                console.print(Panel(
                    f"[error]Invalid License Key[/error]\n"
                    f"The key we found ({key[:8]}...) was rejected by the server.\n"
                    "Please run: [accent]agentic login <your_license_key>[/accent]",
                    title="🔒 License Check Failed"
                ))
                raise typer.Exit(1)

            # --- Inject user-provided API keys into runtime configs ---
            from . import config
            if data.get("nvidia_api_key"): 
                config.CLOUD_CONFIG["api_key"] = data["nvidia_api_key"]
                os.environ["NVIDIA_API_KEY"] = data["nvidia_api_key"]
            if data.get("groq_api_key"): 
                config.GROQ_CONFIG["api_key"] = data["groq_api_key"]
                os.environ["GROQ_API_KEY"] = data["groq_api_key"]
            if data.get("glm_api_key"): 
                config.GLM_CONFIG["api_key"] = data["glm_api_key"]
                os.environ["GLM_API_KEY"] = data["glm_api_key"]
                
    except Exception as e:
        console.print(f"[error]Error reading credentials: {e}[/error]")
        raise typer.Exit(1)

@app.command()
def login(key: str = typer.Argument(..., help="Your AgentIC (Lemon Squeezy) License Key")):
    """Authenticate this computer and setup LLM API keys for multi-agent capabilities."""
    import json
    import requests
    
    console.print(f"Verifying license key securely with server...")
    
    if key.startswith("sk_test_dev_bypass"):
        console.print("[success]Developer Bypass active.[/success]")
    else:
        # Real Lemon Squeezy API Request
        try:
            response = requests.post(
                "https://api.lemonsqueezy.com/v1/licenses/validate",
                headers={"Accept": "application/json"},
                data={"license_key": key}
            )
            data = response.json()
            
            if response.status_code != 200 or not data.get("valid"):
                error_msg = data.get("error", "Key rejected by server")
                console.print(f"[error]✗ Invalid License Key: {error_msg}[/error]")
                raise typer.Exit(1)
        except requests.exceptions.RequestException as e:
            console.print(f"[error]✗ Could not reach license verification server. Check your connection.[/error]")
            raise typer.Exit(1)
    
    console.print(Panel(
        f"[success]Authentication Successful![/success]
"
        f"License verified. Now, let's configure your AI engines.
"
        f"AgentIC relies on 3 specific models for optimal speed & logic.",
        title="🔒 Login Complete"
    ))
    
    nvidia_key = typer.prompt("🔑 Enter your NVIDIA API Key (for Heavy Reasoning)", hide_input=True)
    groq_key = typer.prompt("⚡ Enter your GROQ API Key (for Fast Iterations)", hide_input=True)
    glm_key = typer.prompt("🧠 Enter your ZhipuAI/GLM API Key (Optional, press Enter to skip)", default="", hide_input=True)
        
    os.makedirs(os.path.dirname(CREDENTIALS_FILE) or ".", exist_ok=True)
    with open(CREDENTIALS_FILE, 'w') as f:
        json.dump({
            "license_key": key,
            "nvidia_api_key": nvidia_key,
            "groq_api_key": groq_key,
            "glm_api_key": glm_key
        }, f, indent=4)
        
    # Inject them immediately for this session
    from . import config
    config.CLOUD_CONFIG["api_key"] = nvidia_key
    config.GROQ_CONFIG["api_key"] = groq_key
    config.GLM_CONFIG["api_key"] = glm_key

    console.print(f"
[success]✅ API Keys securely saved in {CREDENTIALS_FILE}[/success]")
    
    # Now trigger diagnostics to ensure they have the compilers installed
    console.print("
[accent]Checking local compilation tools (OSS CAD Suite, Docker)...[/accent]")
    from .tools.vlsi_tools import startup_self_check
    status = startup_self_check()
    if not status["ok"]:
        console.print("[warning]Some system compiler dependencies are missing.[/warning]")
        if typer.confirm("Would you like AgentIC to attempt an automatic environment installation now?"):
            console.print("[dim]Running installation script (this may take a few minutes)...[/dim]")
            import subprocess
            os.system("bash scripts/setup_env.sh || bash scripts/setup_desktop.sh")
            console.print("[success]Dependencies setup attempted! Re-run 'agentic login' or 'source scripts/env.sh' to apply changes.[/success]")
    else:
        console.print("[success]Compiler environment looks pristine! 🚀[/success]")
        
    console.print("\n[success]You are completely set up! Try running: agentic build --name my_design --desc '...'[/success]")


# Setup Brain
def get_llm():
    """Returns the LLM instance from the best available provider:
       1. NVIDIA Cloud (e.g. Llama 3.3, DeepSeek)
       2. Local Compute Engine (VeriReason/Ollama)
    """
    
    configs = [
        ("Cloud Compute Engine", CLOUD_CONFIG),
        ("Local Compute Engine", LOCAL_CONFIG),
    ]
    
    for name, cfg in configs:
        key = cfg.get("api_key", "")
        # For Cloud, skip if no key.
        if "Cloud" in name and (not key or key.strip() == "" or key == "mock-key"):
             console.print(f"[dim]⏭ {name}: No valid API key set, skipping.[/dim]")
             continue
            
        try:
            console.print(f"[dim]Testing {name}...[/dim]")
            # Add extra parameters for reasoning models
            extra_t = {}
            if "glm5" in cfg["model"].lower():
                 extra_t = {
                     "chat_template_kwargs": {"enable_thinking": True, "clear_thinking": False}
                 }
            elif "deepseek-v3.2" in cfg["model"].lower():
                 extra_t = {
                     "chat_template_kwargs": {"thinking": True}
                 }
                
            llm = LLM(
                model=cfg["model"],
                base_url=cfg["base_url"],
                api_key=key if key and key != "NA" else "mock-key", # Local LLMs might use mock-key
                temperature=0.2, # Standardized for RTL generation stability
                top_p=0.7,   # Optimized for code output
                max_tokens=16384,
                timeout=300,
                extra_body=extra_t,
                model_kwargs={"presence_penalty": 0, "repetition_penalty": 1}
            )
            # Make a lightweight API call to validate the endpoint
            llm.call([{"role": "user", "content": "Hi"}])
            console.print(f"[success]✓ AgentIC is working on your chip using {name}[/success]")
            return llm
        except Exception as e:
            console.print(f"[warning]⚠ {name} init failed[/warning]")
    
    # Critical Failure if both fail
    console.print(Panel(
        "[error]CRITICAL: No AI API Key Found[/error]\n\n"
        "AgentIC is a [warning]Bring-Your-Own-Key[/warning] application. "
        "To build chips using cloud AI clusters, you must provide your own API key.\n\n"
        "[accent]How to fix this:[/accent]\n"
        "1. Create a file named [bold].env[/bold] in your current directory.\n"
        "2. Add your provider's API key to the file. For example:\n"
        "   [success]NVIDIA_API_KEY=\"nvapi-...\"[/success]\n"
        "   [success]GROQ_API_KEY=\"gsk_...\"[/success]\n\n"
        "[dim]Alternatively, you can export these as environment variables before running AgentIC.[/dim]",
        title="🔑 Missing API Key Setup",
        border_style="red"
    ))
    raise typer.Exit(1)


def run_startup_diagnostics(strict: bool = True):
    diag = startup_self_check()
    ok = bool(diag.get("ok", False))
    status = "[success]PASS[/success]" if ok else "[error]FAIL[/error]"
    console.print(Panel(f"Startup Toolchain Check: {status}", title="🔧 Environment"))
    if not ok:
        for check in diag.get("checks", []):
            if not check.get("ok"):
                console.print(f"  [error]✗ {check.get('tool')}[/error] -> {check.get('resolved')}")
        if strict:
            raise typer.Exit(1)


@app.command()
def simulate(
    name: str = typer.Option(..., "--name", "-n", help="Design name (e.g., counter)"),
    max_retries: int = typer.Option(5, "--max-retries", "-r", min=0, help="Max auto-fix retries for failures"),
    show_thinking: bool = typer.Option(True, "--show-thinking", help="Print DeepSeek <think> reasoning")
):
    """Run simulation on an existing design with AUTO-FIX loop."""
    verify_license()
    check_dependencies(skip_openlane)
    console.print(Panel(
        f"[accent]AgentIC: Manual Simulation + Auto-Fix Mode[/accent]\n"
        f"Design: [warning]{name}[/warning]",
        title="🚀 Starting Simulation"
    ))

    llm = get_llm()

    def log_thinking(raw_text: str, step: str):
        """Emit DeepSeek <think> content."""
        if not show_thinking: return
        # Simple logging for sim tool
        pass 

    
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
        with console.status(f"[accent]AI is fixing ({agent_role})...[/accent]"):
            result = str(Crew(verbose=False, agents=[fix_agent], tasks=[fix_task]).kickoff())
            return result
    
    sim_success, sim_output = run_simulation(name)
    sim_tries = 0
    
    while not sim_success and sim_tries < max_retries:
        sim_tries += 1
        console.print(f"[error]✗ SIMULATION FAILED (attempt {sim_tries}/{max_retries})[/error]")
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
{read_file_content(rtl_path)}
```

Current testbench:
```verilog
{read_file_content(tb_path)}
```
'''
            fixed_tb = _fix_with_llm('Verification Engineer', f'Fix testbench for {name}', fix_tb_prompt)
            result_path = write_verilog(name, fixed_tb, is_testbench=True)
            if isinstance(result_path, str) and result_path.startswith("Error:"):
                sim_output = f"Failed to write fixed TB: {result_path}"
                continue
            tb_path = result_path
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
            analysis = str(Crew(verbose=False, agents=[analyst], tasks=[analysis_task]).kickoff()).strip()
            
            is_tb_issue = "A" in analysis

            if is_tb_issue:
                 console.print("[warning]  -> [Analyst] Root Cause: Testbench Error. Fixing TB...[/warning]")
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
{read_file_content(rtl_path)}
```

Current Testbench (To Fix - increase wait cycles):
```verilog
{read_file_content(tb_path)}
```
'''
                 fixed_tb = _fix_with_llm('Verification Engineer', f'Fix testbench logic for {name}', fix_tb_logic_prompt)
                 result_path = write_verilog(name, fixed_tb, is_testbench=True)
                 if isinstance(result_path, str) and result_path.startswith("Error:"):
                     sim_output = f"Failed to write fixed TB: {result_path}"
                     continue
                 tb_path = result_path
                 sim_success, sim_output = run_simulation(name)
                 continue

            else:
                console.print("[warning]  -> Detecting Design Logic mismatch. Fixing RTL...[/warning]")
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
{read_file_content(tb_path)}
```

Current RTL:
```verilog
{read_file_content(rtl_path)}
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
        console.print(f"[error]✗ SIMULATION FAILED:[/error]\n{sim_output}")
        raise typer.Exit(1)

    sim_lines = sim_output.strip().split('\n')
    for line in sim_lines[-20:]:  # Print last 20 lines of log
        console.print(f"  [dim]{line}[/dim]")
    console.print("  ✓ Simulation [success]passed[/success]")


def _generate_config_tcl(design_name: str, rtl_file: str) -> str:
    """Auto-generate OpenLane config.tcl based on design complexity.
    
    Reads the RTL file to estimate size and generates appropriate
    die area, clock period, and synthesis settings.
    """
    # Estimate design complexity from file size
    try:
        with open(rtl_file, 'r') as f:
            rtl_content = f.read()
        line_count = len(rtl_content.strip().split('\n'))
    except IOError:
        line_count = 100  # Fallback

    # Scale parameters based on complexity
    if line_count < 100:
        # Small: counter, shift register, PWM
        die_size, util, clock_period = 300, 50, "10"
    elif line_count < 300:
        # Medium: FIFO, UART, SPI, FSM
        die_size, util, clock_period = 500, 40, "15"
    else:
        # Large: TMR, AES, processors
        die_size, util, clock_period = 800, 35, "20"

    return f'''# Auto-generated by AgentIC for {design_name}
set ::env(DESIGN_NAME) "{design_name}"
set ::env(VERILOG_FILES) "$::env(DESIGN_DIR)/src/{design_name}.v"
set ::env(CLOCK_PORT) "clk"
set ::env(CLOCK_PERIOD) "{clock_period}"

# Floorplanning (scaled for ~{line_count} lines of RTL)
set ::env(FP_SIZING) "absolute"
set ::env(DIE_AREA) "0 0 {die_size} {die_size}"
set ::env(FP_CORE_UTIL) {util}
set ::env(PL_TARGET_DENSITY) {util / 100 + 0.05:.2f}

# Synthesis
set ::env(SYNTH_STRATEGY) "AREA 0"
set ::env(MAX_FANOUT_CONSTRAINT) 8

# Routing
set ::env(GRT_OVERFLOW_ITERS) 64

# PDK
set ::env(PDK) "{PDK}"
'''


@app.command()
def harden(
    name: str = typer.Option(..., "--name", "-n", help="Design name (e.g., counter)"),
):
    """Run OpenLane hardening (RTL -> GDSII) on an existing design."""
    verify_license()
    check_dependencies(skip_openlane)
    console.print(Panel(
        f"[accent]AgentIC: Manual Hardening Mode[/accent]\n"
        f"Design: [warning]{name}[/warning]",
        title="🚀 Starting OpenLane"
    ))
    
    new_config = f"{OPENLANE_ROOT}/designs/{name}/config.tcl"
    rtl_file = f"{OPENLANE_ROOT}/designs/{name}/src/{name}.v"

    if not os.path.exists(new_config):
        if not os.path.exists(rtl_file):
            console.print(f"[error]✗ RTL file not found: {rtl_file}[/error]")
            raise typer.Exit(1)
        
        # Auto-generate config.tcl based on design size
        config_content = _generate_config_tcl(name, rtl_file)
        os.makedirs(os.path.dirname(new_config), exist_ok=True)
        with open(new_config, 'w') as f:
            f.write(config_content)
        console.print(f"  ✓ Config auto-generated: [success]{new_config}[/success]")
    
    # Ask for background execution
    run_bg = typer.confirm("OpenLane hardening can take 10-30+ minutes. Run in background?", default=True)
    
    if run_bg:
        console.print("  [dim]Launching background process...[/dim]")
    else:
        console.print("  [dim]Running OpenLane (this may take 10-30 minutes)...[/dim]")

    ol_success, ol_result = run_openlane(name, background=run_bg)
    
    if ol_success:
        if run_bg:
             console.print(f"  ✓ [success]{ol_result}[/success]")
             console.print(f"  [dim]Monitor logs: tail -f {OPENLANE_ROOT}/designs/{name}/harden.log[/dim]")
             console.print("  [warning]Note: Run manual signoff check after background job completes.[/warning]")
             return
        console.print(f"  ✓ GDSII generated: [success]{ol_result}[/success]")
        
        # --- Strict Signoff Check ---
        console.print(Panel(
            f"[accent]Running Signoff Checks (STA/Power)...[/accent]",
            title="🔍 Fabrication Readiness"
        ))
        success, report = signoff_check_tool(name)
        if success:
            console.print(f"[success]✅ SIGNOFF PASSED[/success]")
            console.print(report)
        else:
            console.print(f"[error]❌ SIGNOFF FAILED[/error]")
            console.print(report)
            raise typer.Exit(1)

    else:
        console.print(f"[error]✗ OpenLane failed[/error]")
        console.print(f"  Error: {ol_result[:500]}...")
        raise typer.Exit(1)

# --- THE BUILD COMMAND ---
@app.command()
def build(
    name: str = typer.Option(..., "--name", "-n", help="Design name (e.g., counter)"),
    desc: str = typer.Option(..., "--desc", "-d", help="Natural language description"),
    max_retries: int = typer.Option(5, "--max-retries", "-r", min=0, help="Max auto-fix retries for RTL/TB/sim failures"),
    skip_openlane: bool = typer.Option(False, "--skip-openlane", help="Stop after simulation (no RTL→GDSII hardening)"),
    skip_coverage: bool = typer.Option(False, "--skip-coverage", help="Bypass COVERAGE_CHECK and continue from formal verification to regression"),
    show_thinking: bool = typer.Option(False, "--show-thinking", help="Print DeepSeek <think> reasoning for each generation/fix step"),
    full_signoff: bool = typer.Option(False, "--full-signoff", help="Run full industry signoff (formal + coverage + regression + DRC/LVS)"),
    min_coverage: float = typer.Option(80.0, "--min-coverage", help="Minimum line coverage percentage to pass verification"),
    strict_gates: bool = typer.Option(True, "--strict-gates/--no-strict-gates", help="Enable strict fail-closed gating"),
    pdk_profile: str = typer.Option("sky130", "--pdk-profile", help="PDK adapter profile: sky130 or gf180"),
    max_pivots: int = typer.Option(2, "--max-pivots", min=0, help="Maximum strategy pivots before fail-closed"),
    congestion_threshold: float = typer.Option(10.0, "--congestion-threshold", help="Routing congestion threshold (%)"),
    hierarchical: str = typer.Option("auto", "--hierarchical", help="Hierarchical mode: auto, off, on"),
    tb_gate_mode: str = typer.Option("strict", "--tb-gate-mode", help="TB gate mode: strict or relaxed"),
    tb_max_retries: int = typer.Option(3, "--tb-max-retries", min=1, help="Maximum TB gate recovery attempts"),
    tb_fallback_template: str = typer.Option("uvm_lite", "--tb-fallback-template", help="TB fallback template: uvm_lite or classic"),
    coverage_backend: str = typer.Option(SIM_BACKEND_DEFAULT, "--coverage-backend", help="Coverage backend: auto, verilator, iverilog"),
    coverage_fallback_policy: str = typer.Option(COVERAGE_FALLBACK_POLICY_DEFAULT, "--coverage-fallback-policy", help="Coverage fallback policy: fail_closed, fallback_oss, skip"),
    coverage_profile: str = typer.Option(COVERAGE_PROFILE_DEFAULT, "--coverage-profile", help="Coverage profile: balanced, aggressive, relaxed"),
    no_golden_templates: bool = typer.Option(False, "--no-golden-templates", help="Disable golden template matching in RTL_GEN; force LLM to generate RTL from scratch"),
):
    """Build a chip from natural language description (Autonomous Orchestrator 2.0)."""
    verify_license()
    check_dependencies(skip_openlane)
    
    from .orchestrator import BuildOrchestrator
    
    console.print(Panel(
        f"[accent]AgentIC: Natural Language → GDSII[/accent]\n"
        f"Design: [warning]{name}[/warning]\n"
        f"Description: {desc}\n"
        f"{'[success]Full Industry Signoff Enabled[/success]' if full_signoff else ''}",
        title="🚀 Starting Autonomous Orchestrator"
    ))

    tb_gate_mode = tb_gate_mode.lower().strip()
    if tb_gate_mode not in {"strict", "relaxed"}:
        raise typer.BadParameter("--tb-gate-mode must be one of: strict, relaxed")

    tb_fallback_template = tb_fallback_template.lower().strip()
    if tb_fallback_template not in {"uvm_lite", "classic"}:
        raise typer.BadParameter("--tb-fallback-template must be one of: uvm_lite, classic")

    coverage_backend = coverage_backend.lower().strip()
    if coverage_backend not in {"auto", "verilator", "iverilog"}:
        raise typer.BadParameter("--coverage-backend must be one of: auto, verilator, iverilog")

    coverage_fallback_policy = coverage_fallback_policy.lower().strip()
    if coverage_fallback_policy not in {"fail_closed", "fallback_oss", "skip"}:
        raise typer.BadParameter("--coverage-fallback-policy must be one of: fail_closed, fallback_oss, skip")

    coverage_profile = coverage_profile.lower().strip()
    if coverage_profile not in {"balanced", "aggressive", "relaxed"}:
        raise typer.BadParameter("--coverage-profile must be one of: balanced, aggressive, relaxed")

    run_startup_diagnostics(strict=strict_gates)
    llm = get_llm()
    
    # Build Multi-LLM Role Map for the CLI
    from .config import get_role_llm_config
    from crewai import LLM
    roles = ["architect", "designer", "testbench_designer", "verifier", "fixer", "debugger", "manager", "physical"]
    role_llms = {}
    for role in roles:
        cfg = get_role_llm_config(role)
        llm_kwargs = dict(model=cfg["model"], api_key=cfg["api_key"], temperature=0.6, max_tokens=16384)
        if cfg.get("base_url"):
            llm_kwargs["base_url"] = cfg["base_url"]
        try:
            role_llms[role] = LLM(**llm_kwargs)
        except Exception:
            role_llms[role] = llm
            
    orchestrator = BuildOrchestrator(
        name=name,
        desc=desc,
        llm=llm,
        role_llms=role_llms,

        max_retries=max_retries,
        verbose=show_thinking,
        skip_openlane=skip_openlane,
        skip_coverage=skip_coverage,
        full_signoff=full_signoff,
        min_coverage=min_coverage,
        strict_gates=strict_gates,
        pdk_profile=pdk_profile,
        max_pivots=max_pivots,
        congestion_threshold=congestion_threshold,
        hierarchical_mode=hierarchical,
        tb_gate_mode=tb_gate_mode,
        tb_max_retries=tb_max_retries,
        tb_fallback_template=tb_fallback_template,
        coverage_backend=coverage_backend,
        coverage_fallback_policy=coverage_fallback_policy,
        coverage_profile=coverage_profile,
        no_golden_templates=no_golden_templates,
    )
    
    orchestrator.run()

@app.command()
def verify(name: str = typer.Argument(..., help="Design name to verify")):
    """Run verification on an existing design."""
    console.print(f"[warning]Running verification for {name}...[/warning]")
    output = run_verification(name)
    console.print(output)



if __name__ == "__main__":
    app()
