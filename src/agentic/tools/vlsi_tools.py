# tools/vlsi_tools.py
import os
import subprocess
from ..config import OPENLANE_ROOT, SCRIPTS_DIR, PDK_ROOT, OPENLANE_IMAGE

def write_verilog(design_name, code, is_testbench=False):
    """Writes Verilog code to the OpenLane design directory."""
    suffix = "_tb" if is_testbench else ""
    path = f"{OPENLANE_ROOT}/designs/{design_name}/src/{design_name}{suffix}.v"
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
    # Fix SystemVerilog to Verilog-2005
    clean_code = clean_code.replace('always_comb', 'always @(*)')
    clean_code = clean_code.replace('always_ff', 'always')
    clean_code = clean_code.replace('logic', 'reg')
    # Fix time units: #5ns -> #5, #10ps -> #10
    clean_code = re.sub(r'#(\d+)(ns|ps|us|ms|s)\b', r'#\1', clean_code)
    # Fix wildcard port connections (SystemVerilog)
    clean_code = re.sub(r'\(\s*\.\*\s*\)', '', clean_code)
    # Remove any leftover special chars
    clean_code = re.sub(r'[▁｜]', '', clean_code)
    
    with open(path, "w") as f:
        f.write(clean_code)
    return path

def run_syntax_check(file_path):
    """Runs iverilog syntax check."""
    result = subprocess.run(
        ["iverilog", "-t", "null", file_path], 
        capture_output=True, text=True
    )
    return result.returncode == 0, result.stderr

def run_simulation(design_name):
    """Compiles and runs the testbench simulation."""
    src_dir = f"{OPENLANE_ROOT}/designs/{design_name}/src"
    rtl_file = f"{src_dir}/{design_name}.v"
    tb_file = f"{src_dir}/{design_name}_tb.v"
    sim_out = f"{src_dir}/sim"
    
    # Compile
    compile_result = subprocess.run(
        ["iverilog", "-o", sim_out, rtl_file, tb_file],
        capture_output=True, text=True
    )
    if compile_result.returncode != 0:
        return False, f"Compilation failed:\n{compile_result.stderr}"
    
    # Run
    run_result = subprocess.run(
        ["vvp", sim_out],
        capture_output=True, text=True,
        timeout=30
    )
    return True, run_result.stdout

def run_openlane(design_name):
    """Triggers the OpenLane flow via Docker."""
    os.chdir(OPENLANE_ROOT)
    
    # Direct Docker command (non-interactive)
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{OPENLANE_ROOT}:/openlane",
        "-v", f"{PDK_ROOT}:{PDK_ROOT}",
        "-e", f"PDK_ROOT={PDK_ROOT}",
        "-e", "PDK=sky130A",
        "-e", "PWD=/openlane",
        OPENLANE_IMAGE,
        "./flow.tcl", "-design", design_name, "-tag", "agentrun", "-overwrite"
    ]
    
    process = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=900  # 15 min timeout
    )
    
    # Check if GDS was created
    gds_path = f"{OPENLANE_ROOT}/designs/{design_name}/runs/agentrun/results/final/gds/{design_name}.gds"
    success = os.path.exists(gds_path)
    
    return success, gds_path if success else process.stderr

def run_verification(design_name):
    """Runs the verify_design.sh script."""
    script_path = os.path.join(SCRIPTS_DIR, "verify_design.sh")
    
    result = subprocess.run(
        ["bash", script_path, design_name],
        capture_output=True,
        text=True
    )
    return result.stdout