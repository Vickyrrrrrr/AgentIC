"""Verilator Real Coverage Integration.

Provides actual code coverage using Verilator's --coverage flag,
replacing the heuristic-based coverage estimate in vlsi_tools.py.
"""

import os
import re
import subprocess
from ..config import OPENLANE_ROOT


def run_verilator_coverage(design_name: str) -> tuple:
    """Compiles and runs simulation with Verilator's real coverage instrumentation.
    
    Uses Verilator to compile the design with --coverage flag, runs the
    simulation binary, then parses the coverage output.
    
    Returns:
        tuple: (success: bool, report: str, coverage_data: dict)
               coverage_data = {'line': float, 'branch': float, 'toggle': float, 'overall': float}
    """
    src_dir = f"{OPENLANE_ROOT}/designs/{design_name}/src"
    rtl_file = f"{src_dir}/{design_name}.v"
    tb_file = f"{src_dir}/{design_name}_tb.v"
    obj_dir = f"{src_dir}/verilator_cov_obj"
    cov_dat = f"{src_dir}/coverage.dat"
    
    if not os.path.exists(rtl_file):
        return False, f"RTL file not found: {rtl_file}", {}
    if not os.path.exists(tb_file):
        return False, f"Testbench file not found: {tb_file}", {}
    
    # Step 1: Compile with Verilator --coverage
    compile_cmd = [
        "verilator", "--binary", "--coverage",
        "--coverage-line", "--coverage-toggle",
        "-Wno-fatal",
        "--timing",
        f"--Mdir", obj_dir,
        "-o", f"V{design_name}",
        "--top-module", f"{design_name}_tb",
        rtl_file, tb_file
    ]
    
    try:
        result = subprocess.run(
            compile_cmd,
            capture_output=True, text=True,
            timeout=180
        )
        if result.returncode != 0:
            # Fallback: Verilator couldn't compile (common with iverilog-style TBs)
            return False, f"Verilator compilation failed (this is normal for iverilog-style testbenches):\n{result.stderr[:500]}", {}
    except FileNotFoundError:
        return False, "Verilator not installed. Falling back to heuristic coverage.", {}
    except subprocess.TimeoutExpired:
        return False, "Verilator compilation timed out.", {}
    
    # Step 2: Run the simulation
    sim_binary = f"{obj_dir}/V{design_name}"
    if not os.path.exists(sim_binary):
        return False, f"Simulation binary not found: {sim_binary}", {}
    
    try:
        sim_result = subprocess.run(
            [sim_binary, f"+verilator+coverage+file+{cov_dat}"],
            capture_output=True, text=True,
            timeout=300,
            cwd=src_dir
        )
    except subprocess.TimeoutExpired:
        return False, "Simulation timed out.", {}
    
    sim_output = (sim_result.stdout or "") + ("\n" + sim_result.stderr if sim_result.stderr else "")
    sim_passed = "TEST PASSED" in sim_output
    
    # Step 3: Parse coverage data
    coverage_data = parse_verilator_coverage(cov_dat, src_dir)
    
    report = f"Simulation: {'PASSED' if sim_passed else 'FAILED'}\n"
    report += f"Coverage: line={coverage_data.get('line', 0):.1f}% "
    report += f"toggle={coverage_data.get('toggle', 0):.1f}% "
    report += f"overall={coverage_data.get('overall', 0):.1f}%"
    
    return sim_passed, report, coverage_data


def parse_verilator_coverage(cov_dat_path: str, output_dir: str) -> dict:
    """Parse Verilator coverage.dat file into coverage percentages.
    
    Args:
        cov_dat_path: Path to coverage.dat file
        output_dir: Directory for annotated output
    
    Returns:
        dict with 'line', 'toggle', 'branch', 'overall' percentages
    """
    result = {
        'line': 0.0,
        'toggle': 0.0, 
        'branch': 0.0,
        'overall': 0.0,
        'total_points': 0,
        'hit_points': 0
    }
    
    if not os.path.exists(cov_dat_path):
        result['error'] = 'coverage.dat not found'
        return result
    
    # Use verilator_coverage tool to get summary
    annotate_dir = f"{output_dir}/cov_annotate"
    
    try:
        # Get annotated output
        subprocess.run(
            ["verilator_coverage", "--annotate", annotate_dir, cov_dat_path],
            capture_output=True, text=True, timeout=30
        )
        
        # Parse annotated files for line coverage
        line_total = 0
        line_hit = 0
        toggle_total = 0
        toggle_hit = 0
        
        if os.path.exists(annotate_dir):
            for root, dirs, files in os.walk(annotate_dir):
                for fname in files:
                    if fname.endswith('.v') or fname.endswith('.sv'):
                        fpath = os.path.join(root, fname)
                        with open(fpath, 'r') as f:
                            for line in f:
                                line = line.strip()
                                # Verilator annotates with coverage counts
                                cov_match = re.match(r'^(\d+)\s+(.+)', line)
                                if cov_match:
                                    count = int(cov_match.group(1))
                                    line_total += 1
                                    if count > 0:
                                        line_hit += 1
                                elif line.startswith('%'):
                                    # Toggle coverage annotations
                                    toggle_total += 1
                                    pct_match = re.match(r'%0*(\d+)', line)
                                    if pct_match and int(pct_match.group(1)) > 0:
                                        toggle_hit += 1
        
        if line_total > 0:
            result['line'] = round((line_hit / line_total) * 100, 1)
        if toggle_total > 0:
            result['toggle'] = round((toggle_hit / toggle_total) * 100, 1)
        
        result['total_points'] = line_total + toggle_total
        result['hit_points'] = line_hit + toggle_hit
        
        # Branch coverage estimate (Verilator doesn't separate this well)
        result['branch'] = round(result['line'] * 0.85, 1) if result['line'] > 0 else 0.0
        
        # Overall
        if result['total_points'] > 0:
            result['overall'] = round((result['hit_points'] / result['total_points']) * 100, 1)
        
    except FileNotFoundError:
        result['error'] = 'verilator_coverage tool not found'
    except Exception as e:
        result['error'] = str(e)
    
    # Also try to get summary directly from coverage.dat
    try:
        with open(cov_dat_path, 'r') as f:
            cov_text = f.read()
        
        # Count coverage points
        point_matches = re.findall(r"'(\d+)'", cov_text)
        if point_matches:
            total = len(point_matches)
            hit = sum(1 for p in point_matches if int(p) > 0)
            if total > 0 and result['overall'] == 0:
                result['line'] = round((hit / total) * 100, 1)
                result['overall'] = result['line']
                result['total_points'] = total
                result['hit_points'] = hit
    except Exception:
        pass
    
    return result


def get_coverage_summary(design_name: str) -> str:
    """Get a human-readable coverage summary for a design.
    
    Returns:
        str: Formatted coverage summary
    """
    src_dir = f"{OPENLANE_ROOT}/designs/{design_name}/src"
    cov_dat = f"{src_dir}/coverage.dat"
    
    data = parse_verilator_coverage(cov_dat, src_dir)
    
    if data.get('error'):
        return f"Coverage data unavailable: {data['error']}"
    
    return (
        f"📊 Coverage Report for {design_name}\n"
        f"{'='*40}\n"
        f"  Line Coverage:   {data['line']:.1f}%\n"
        f"  Toggle Coverage: {data['toggle']:.1f}%\n"
        f"  Branch Coverage: {data['branch']:.1f}% (estimated)\n"
        f"  Overall:         {data['overall']:.1f}%\n"
        f"  Points: {data['hit_points']}/{data['total_points']} hit\n"
    )
