# tools/vlsi_tools.py
import os
import re
import subprocess
from crewai.tools import tool
from ..config import OPENLANE_ROOT, SCRIPTS_DIR, PDK_ROOT, OPENLANE_IMAGE

def SecurityCheck(rtl_code: str) -> bool:
    """
    Performs a static security analysis on the generated RTL.
    Returns True if safe, False if malicious patterns detected,
    """
    # 1. Blacklist malicious system calls in Verilog 
    # (Though Icarus usually ignores these in synthesis, they are dangerous in sim)
    blacklist = [
        r'\$system', r'\$fopen', r'\$fwrite', r'\$call', 
        r'\/bin\/sh', r'rm -rf', r'wget', r'curl'
    ]
    
    for pattern in blacklist:
        if re.search(pattern, rtl_code, re.IGNORECASE):
            return False, f"Detected potentially malicious pattern: {pattern}"
            
    return True, "Safe"

def write_config(design_name, code):
    """Writes config.tcl to the OpenLane design directory."""
    path = f"{OPENLANE_ROOT}/designs/{design_name}/config.tcl"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    
    # Clean output
    clean_code = code
    # Remove <think> tags if present
    if "<think>" in clean_code:
        clean_code = re.sub(r'<think>.*?</think>', '', clean_code, flags=re.DOTALL)

    if "```tcl" in clean_code:
        clean_code = clean_code.split("```tcl")[1].split("```")[0].strip()
    elif "```" in clean_code:
        clean_code = clean_code.split("```")[1].split("```")[0].strip()
    
    # Remove "Thought:" lines
    clean_code = re.sub(r'^(Thought|Action|Observation):.*$', '', clean_code, flags=re.MULTILINE)
        
    with open(path, "w") as f:
        f.write(clean_code)
    return path

def write_verilog(design_name, code, is_testbench=False, suffix=None, ext=".v"):
    """Writes Verilog code to the OpenLane design directory."""
    if suffix:
        file_suffix = suffix
    else:
        file_suffix = "_tb" if is_testbench else ""
        
    path = f"{OPENLANE_ROOT}/designs/{design_name}/src/{design_name}{file_suffix}{ext}"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    
    # Clean LLM output (remove markdown fences, think tags, etc.)
    clean_code = code
    
    # Remove <think> tags if present (DeepSeek-R1 reasoning)
    if "<think>" in clean_code:
        import re
        clean_code = re.sub(r'<think>.*?</think>', '', clean_code, flags=re.DOTALL)
    
    # Extract code from markdown fences
    if "```verilog" in clean_code:
        clean_code = clean_code.split("```verilog")[1].split("```")[0].strip()
    elif "```v" in clean_code:
        clean_code = clean_code.split("```v")[1].split("```")[0].strip()
    elif "```" in clean_code:
        # Find the code block
        parts = clean_code.split("```")
        for i, part in enumerate(parts):
            if i % 2 == 1 and "module" in part:  # Odd indices are inside fences
                clean_code = part.strip()
                # Remove language identifier if present
                lines = clean_code.split('\n')
                if lines[0].strip() in ['verilog', 'v', 'systemverilog']:
                    clean_code = '\n'.join(lines[1:])
                break
    
    # Ensure we have a module
    if "module" not in clean_code:
        # Try to find module in original
        if "module" in code:
            start = code.find("module")
            end = code.rfind("endmodule")
            if end > start:
                clean_code = code[start:end+9]  # +9 for "endmodule"
    
    # Sanitize model artifacts and fix common issues
    import re
    # Remove model tokens like <｜begin▁of▁sentence｜>
    clean_code = re.sub(r'<[｜\|][^>]+[｜\|]>', '', clean_code)
    
    # Allow SystemVerilog constructs (removed forced downgrade)
    # clean_code = clean_code.replace('always_comb', 'always @(*)')
    # clean_code = clean_code.replace('always_ff', 'always')
    # clean_code = clean_code.replace('logic', 'reg')
    
    # Fix time units: #5ns -> #5, #10ps -> #10
    clean_code = re.sub(r'#(\d+)(ns|ps|us|ms|s)\b', r'#\1', clean_code)
    # Fix wildcard port connections (SystemVerilog)
    clean_code = re.sub(r'\(\s*\.\*\s*\)', '', clean_code)
    # Remove any leftover special chars
    clean_code = re.sub(r'[▁｜]', '', clean_code)
    
    # Remove "Thought:" or "Action:" lines that might have leaked (common in LangChain/CrewAI raw output)
    # Be careful not to remove comments, so look for start of line
    clean_code = re.sub(r'^(Thought|Action|Observation):.*$', '', clean_code, flags=re.MULTILINE)

    # --- VALIDATION ---
    if "module" not in clean_code:
        # If we still can't find a module, this is likely garbage text or pure reasoning.
        # We should NOT write this to the file as it will break simulation.
        # Instead, we return a special error message that the Agent will see as the tool output.
        return f"Error: No Verilog 'module' definition found in the provided code. Please ensure you output the full Verilog code inside ```verilog``` fences."

    # --- AUTO-FIXES FOR COMPILER COMPATIBILITY ---
    # 1. Fix signed casting: signed'(val) -> $signed(val)
    clean_code = re.sub(r"signed'\s*\((.*?)\)", r"$signed(\1)", clean_code)
    
    # 2. Fix Loop Variables: "for (int i=0..." -> "for (i=0..." 
    # (assuming 'integer i' is declared elsewhere or we strip the type to be safe in blocks)
    # clean_code = re.sub(r'for\s*\(\s*int\s+(\w+)\s*=', r'for (\1 =', clean_code)

    # 3. Remove unsupported SystemVerilog qualifiers for iverilog
    clean_code = re.sub(r'\bunique\s+case\b', 'case', clean_code)
    clean_code = re.sub(r'\bpriority\s+case\b', 'case', clean_code)

    # 4. CRITICAL: Fix "Single Line Output" Bug
    # Some models dump the entire code on one line. If that line starts with //, 
    # the whole file is commented out. We MUST enforce newlines.
    if clean_code.strip().startswith("//") and clean_code.count('\n') < 5:
        # Heuristic: Inject newlines before 'module', 'endmodule', ';', and after comments
        clean_code = clean_code.replace(" module ", "\nmodule ")
        clean_code = clean_code.replace(" endmodule", "\nendmodule")
        clean_code = clean_code.replace(";", ";\n")
        clean_code = clean_code.replace(" begin ", " begin\n")
        clean_code = clean_code.replace(" end ", "\nend ")
    
    with open(path, "w") as f:
        f.write(clean_code)
    return path

def run_syntax_check(file_path: str):
    """
    Runs iverilog syntax check on a Verilog file.
    Returns: (True, "OK") if clean, or (False, "Error message") if failed.
    """
    result = subprocess.run(
        ["iverilog", "-g2012", "-t", "null", file_path], 
        capture_output=True, text=True
    )
    if result.returncode == 0:
        return True, "Syntax OK"
    return False, result.stderr

@tool("Syntax Checker")
def syntax_check_tool(file_path: str):
    """
    Runs iverilog syntax check on a Verilog file.
    Useful for checking if your Verilog code is valid before submitting it.
    Input: file_path (string)
    """
    return run_syntax_check(file_path)

def write_sby_config(design_name):
    """Writes a default SBY config for the design."""
    path = f"{OPENLANE_ROOT}/designs/{design_name}/src/{design_name}.sby"
    config = f"""[options]
mode prove

[engines]
smtbmc

[script]
read -formal {design_name}.v
read -formal {design_name}_sva.sv
prep -top {design_name}

[files]
{design_name}.v
{design_name}_sva.sv
"""
    with open(path, "w") as f:
        f.write(config)
    return path

def run_formal_verification(design_name):
    """Runs SymbiYosys (SBY) for formal verification."""
    design_dir = f"{OPENLANE_ROOT}/designs/{design_name}/src"
    sby_file = f"{design_dir}/{design_name}.sby"
    
    if not os.path.exists(sby_file):
        return False, "SBY configuration file not found."

    # Run SBY
    try:
        result = subprocess.run(
            ["sby", "-f", f"{design_name}.sby"],
            cwd=design_dir,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return True, f"Formal Verification PASSED.\\n{result.stdout}"
        else:
            return False, f"Formal Verification FAILED:\\n{result.stdout}\\n{result.stderr}"
    except FileNotFoundError:
        return False, "SymbiYosys (sby) tool not installed/found in path."

def read_file_content(file_path: str):
    """
    Reads the content of a file.
    """
    try:
        if not os.path.exists(file_path):
             return f"Error: File {file_path} not found."
        with open(file_path, 'r') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"

@tool("File Reader")
def read_file_tool(file_path: str):
    """
    Reads the content of a file.
    Useful for reading declarations in other files to fix mismatch errors.
    """
    return read_file_content(file_path)

def check_physical_metrics(design_name):
    """
    Parses OpenLane output metrics (Area, Timing, Power).
    Returns a dictionary of metrics or Error if not found.
    """
    import csv
    metrics_path = f"{OPENLANE_ROOT}/designs/{design_name}/runs/agentrun/reports/metrics.csv"
    
    if not os.path.exists(metrics_path):
        # Fallback: Find latest run
        runs_dir = f"{OPENLANE_ROOT}/designs/{design_name}/runs"
        if os.path.exists(runs_dir):
            runs = sorted([d for d in os.listdir(runs_dir) if os.path.isdir(os.path.join(runs_dir, d))])
            if runs:
                metrics_path = f"{runs_dir}/{runs[-1]}/reports/metrics.csv"

    if not os.path.exists(metrics_path):
        return None, "Metrics file not found. OpenLane might have failed."

    try:
        with open(metrics_path, 'r') as f:
            reader = csv.DictReader(f)
            data = next(reader) # Only one row usually
            
            # Extract key metrics
            metrics = {
                "area": float(data.get("Total_Physical_Cells", 0)),
                "chip_area_um2": float(data.get("DieArea_mm^2", 0)) * 1e6,
                "timing_tns": float(data.get("timing__tns", 0)), # Total Negative Slack
                "timing_wns": float(data.get("timing__wns", 0)), # Worst Negative Slack
                "power_total": float(data.get("power__total", 0)),
                "utilization": float(data.get("design__instance__utilization", 0))
            }
            return metrics, "OK"
    except Exception as e:
        return None, f"Error parsing metrics: {str(e)}"

def run_simulation(design_name):
    """Compiles and runs the testbench simulation."""
    src_dir = f"{OPENLANE_ROOT}/designs/{design_name}/src"
    rtl_file = f"{src_dir}/{design_name}.v"
    tb_file = f"{src_dir}/{design_name}_tb.v"
    sim_out = f"{src_dir}/sim"
    
    # Compile
    compile_result = subprocess.run(
        ["iverilog", "-g2012", "-o", sim_out, rtl_file, tb_file],
        capture_output=True, text=True
    )
    if compile_result.returncode != 0:
        return False, f"Compilation failed:\n{compile_result.stderr}"
    
    # Run
    # Increased timeout to 300s (5 mins) for complex simulations (processors/crypto)
    try:
        run_result = subprocess.run(
            ["vvp", sim_out],
            capture_output=True,
            text=True,
            timeout=300 
        )
    except subprocess.TimeoutExpired:
        return False, "Simulation Timed Out (Exceeded 300 seconds). Logic might be stuck in an infinite loop."

    sim_text = (run_result.stdout or "") + ("\n" + run_result.stderr if run_result.stderr else "")

    # Many Verilog testbenches don't set a failing process exit code.
    # AgentIC requires a clear PASS marker to treat simulation as successful.
    # If the TB doesn't print TEST PASSED, we'll consider it a failure so the
    # verification/fix loop can improve the TB.
    if "TEST PASSED" in sim_text:
        return True, sim_text

    # Explicit failure marker
    if "TEST FAILED" in sim_text:
        return False, sim_text

    # Fallback: if vvp itself failed, fail. Otherwise, still fail due to missing PASS.
    if run_result.returncode != 0:
        return False, sim_text
    return False, sim_text

def run_openlane(design_name, background=False):
    """Triggers the OpenLane flow via Docker."""
    
    # --- Autonomous Environment Fix ---
    # If PDK_ROOT is not set, try to find it in common locations
    global PDK_ROOT
    if not PDK_ROOT or not os.path.exists(PDK_ROOT):
        common_paths = [
            os.path.expanduser("~/.ciel"),
            os.path.expanduser("~/.volare"),
            "/usr/local/pdk",
            "/opt/pdk",
            os.path.join(OPENLANE_ROOT, "pdks")
        ]
        found = False
        for path in common_paths:
            if os.path.exists(path) and os.path.exists(os.path.join(path, "sky130A")):
                PDK_ROOT = path
                found = True
                break
        
        if not found:
            return False, f"PDK_ROOT not found in environment or common paths ({common_paths}). Please set PDK_ROOT."

    os.chdir(OPENLANE_ROOT)
    
    # Ensure design dir exists
    design_dir = f"{OPENLANE_ROOT}/designs/{design_name}"
    if not os.path.exists(design_dir):
        return False, f"Design directory not found: {design_dir}"

    # Direct Docker command (non-interactive)
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{OPENLANE_ROOT}:/openlane",
        "-v", f"{PDK_ROOT}:{PDK_ROOT}",
        "-e", f"PDK_ROOT={PDK_ROOT}",
        "-e", "PDK=sky130A",
        "-e", "PWD=/openlane",
        OPENLANE_IMAGE,
        "./flow.tcl", "-design", design_name, "-tag", "agentrun", "-overwrite", "-ignore_mismatches"
    ]
    
    if background:
        log_file_path = os.path.join(design_dir, "harden.log")
        try:
            with open(log_file_path, "w") as f:
                subprocess.Popen(
                    cmd,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    start_new_session=True
                )
            return True, f"Background task started. Logs: {log_file_path}"
        except Exception as e:
            return False, f"Failed to start background process: {str(e)}"
    
    # Increased timeout to 3600s (1 hour) for complex placement/routing
    try:
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600 
        )
    except subprocess.TimeoutExpired:
        return False, "OpenLane Hardening Timed Out (Exceeded 60 mins)."
    
    # Check if GDS was created
    gds_path = f"{OPENLANE_ROOT}/designs/{design_name}/runs/agentrun/results/final/gds/{design_name}.gds"
    success = os.path.exists(gds_path)

    if success:
        return True, gds_path

    error_text = (process.stderr or "")
    if process.stdout:
        error_text = (process.stdout + "\n" + error_text).strip()
    return False, error_text

def run_verification(design_name):
    """Runs the verify_design.sh script."""
    script_path = os.path.join(SCRIPTS_DIR, "verify_design.sh")
    
    result = subprocess.run(
        ["bash", script_path, design_name],
        capture_output=True,
        text=True
    )
    return result.stdout