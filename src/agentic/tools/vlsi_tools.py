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

    # --- AUTO-FIXES FOR COMPILER COMPATIBILITY ---
    # 1. Fix signed casting: signed'(val) -> $signed(val)
    clean_code = re.sub(r"signed'\s*\((.*?)\)", r"$signed(\1)", clean_code)
    
    # 2. Fix Loop Variables: "for (int i=0..." -> "for (i=0..." 
    # (assuming 'integer i' is declared elsewhere or we strip the type to be safe in blocks)
    # clean_code = re.sub(r'for\s*\(\s*int\s+(\w+)\s*=', r'for (\1 =', clean_code)

    # 3. Remove unsupported SystemVerilog qualifiers for iverilog
    clean_code = re.sub(r'\bunique\s+case\b', 'case', clean_code)
    clean_code = re.sub(r'\bpriority\s+case\b', 'case', clean_code)
    
    with open(path, "w") as f:
        f.write(clean_code)
    return path

def run_syntax_check(file_path):
    """Runs iverilog syntax check."""
    result = subprocess.run(
        ["iverilog", "-g2012", "-t", "null", file_path], 
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
        ["iverilog", "-g2012", "-o", sim_out, rtl_file, tb_file],
        capture_output=True, text=True
    )
    if compile_result.returncode != 0:
        return False, f"Compilation failed:\n{compile_result.stderr}"
    
    # Run
    run_result = subprocess.run(
        ["vvp", sim_out],
        capture_output=True,
        text=True,
        timeout=30
    )

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

def run_openlane(design_name):
    """Triggers the OpenLane flow via Docker."""
    if not PDK_ROOT or not os.path.exists(PDK_ROOT):
        return False, f"PDK_ROOT not found: {PDK_ROOT}. Set PDK_ROOT env var or install Sky130 PDKs."

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
        "./flow.tcl", "-design", design_name, "-tag", "agentrun", "-overwrite", "-ignore_mismatches"
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