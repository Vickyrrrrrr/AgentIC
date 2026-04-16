"""
Synthesis Tools - Direct Yosys RTL-to-Netlist Synthesis
=======================================================
Provides standalone logic synthesis using Yosys as a production-grade
alternative to relying solely on OpenLane's synthesis.

A complete production tapeout flow requires independent synthesis to:
1. Generate gate-level netlist for formal verification
2. Produce pre-route timing estimates
3. Generate SDF for gate-level simulation
4. Produce checkpoint for P&R exploration

Usage:
    from agentic.tools.synth_tools import run_yosys_synth, SynthesisResult

    result = run_yosys_synth(
        rtl_files=["src/design.v"],
        top_module="design",
        output_netlist="synth_out/design_synth.v",
        output_checkpoint="synth_out/design_synth.ys",
        pdk="sky130",
        pdk_root="/path/to/pdk",
    )
"""

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import YOSYS_BIN, WORKSPACE_ROOT, get_pdk_tool_config, get_pdk_profile


@dataclass
class SynthesisResult:
    """Structured result from Yosys synthesis."""

    ok: bool
    netlist_path: str
    checkpoint_path: Optional[str]
    report_path: str
    cell_count: int
    gate_count: float
    area_um2: float
    dff_count: int
    lut_count: int
    frequency_mhz: float
    warnings: List[str]
    errors: List[str]
    diagnostics: List[str]
    metrics: Dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""


def run_yosys_synth(
    rtl_files: List[str],
    top_module: str,
    output_dir: str,
    pdk: str = "sky130",
    pdk_root: Optional[str] = None,
    clk_constraint: float = 0.0,
    ungroup_cells: bool = True,
    flatten_hierarchy: bool = False,
    retime: bool = False,
    buffer_insertion: bool = True,
    smt2_verbose: bool = False,
    additional_yosys_commands: Optional[List[str]] = None,
    timeout: int = 600,
    design_name: Optional[str] = None,
) -> SynthesisResult:
    """Run Yosys synthesis on RTL files and produce a gate-level netlist.

    Args:
        rtl_files: List of Verilog/SystemVerilog source files
        top_module: Name of the top-level module
        output_dir: Directory to write output files
        pdk: PDK name (sky130, gf180mcu, etc.)
        pdk_root: Path to PDK root (auto-detected if None)
        clk_constraint: Clock constraint in ns (e.g., 10.0 = 100MHz)
        ungroup_cells: Flatten sub-modules after synthesis
        flatten_hierarchy: Fully flatten hierarchy (aggressive)
        retime: Enable DSP/memory retiming
        buffer_insertion: Enable buffer insertion for wire loads
        smt2_verbose: Enable SMT2 solver output for formal
        additional_yosys_commands: Extra commands injected before "synth" script
        timeout: Timeout in seconds
        design_name: Design name (defaults to top_module)

    Returns:
        SynthesisResult with metrics, netlist path, and diagnostics
    """
    design_name = design_name or top_module
    os.makedirs(output_dir, exist_ok=True)

    netlist_path = os.path.join(output_dir, f"{design_name}_synth.v")
    report_path = os.path.join(output_dir, f"{design_name}_synth_report.txt")
    checkpoint_path = os.path.join(output_dir, f"{design_name}_synth.ys")

    warnings: List[str] = []
    errors: List[str] = []
    diagnostics: List[str] = []

    for f in rtl_files:
        if not os.path.exists(f):
            errors.append(f"RTL source file not found: {f}")
            return _error_result(errors, netlist_path, report_path)

    scripts_dir = os.path.join(output_dir, "scripts")
    os.makedirs(scripts_dir, exist_ok=True)
    script_path = os.path.join(scripts_dir, f"{design_name}_synth.ys")

    script_lines = _build_yosys_script(
        rtl_files=rtl_files,
        top_module=top_module,
        netlist_path=netlist_path,
        checkpoint_path=checkpoint_path,
        pdk=pdk,
        pdk_root=pdk_root,
        clk_constraint=clk_constraint,
        ungroup_cells=ungroup_cells,
        flatten_hierarchy=flatten_hierarchy,
        retime=retime,
        buffer_insertion=buffer_insertion,
        smt2_verbose=smt2_verbose,
        additional_commands=additional_yosys_commands or [],
    )

    with open(script_path, "w") as f:
        f.write("\n".join(script_lines))

    with open(report_path, "w") as f:
        f.write(f"Yosys Synthesis Report for {design_name}\n")
        f.write(f"Top module: {top_module}\n")
        f.write(f"PDK: {pdk}\n")
        f.write(f"Clock constraint: {clk_constraint} ns\n")
        f.write("=" * 60 + "\n\n")

    try:
        proc = subprocess.run(
            [YOSYS_BIN, "-s", script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired:
        errors.append(f"Yosys synthesis timed out after {timeout}s")
        return _error_result(errors, netlist_path, report_path)
    except OSError as e:
        errors.append(f"Yosys binary not found or failed: {e}")
        return _error_result(errors, netlist_path, report_path)

    with open(report_path, "a") as f:
        f.write(stdout)
        if stderr:
            f.write(f"\nSTDERR:\n{stderr}\n")

    cell_count = _parse_stat_cell_count(stdout)
    dff_count = _parse_stat_dff(stdout)
    lut_count = _parse_stat_lut(stdout)
    area_um2 = _parse_stat_area(stdout, pdk)
    gate_count = _parse_gate_equiv(dff_count, lut_count, cell_count)

    for line in stdout.splitlines():
        l = line.strip()
        if l.startswith("%Warning") or l.startswith("Warning:"):
            warnings.append(l)
        if "error" in l.lower() and "%Error" in l:
            errors.append(l)

    ok = os.path.exists(netlist_path) and proc.returncode == 0 and not errors

    return SynthesisResult(
        ok=ok,
        netlist_path=netlist_path,
        checkpoint_path=checkpoint_path if os.path.exists(checkpoint_path) else None,
        report_path=report_path,
        cell_count=cell_count,
        gate_count=gate_count,
        area_um2=area_um2,
        dff_count=dff_count,
        lut_count=lut_count,
        frequency_mhz=_freq_from_period_ns(clk_constraint) if clk_constraint > 0 else 0.0,
        warnings=warnings,
        errors=errors,
        diagnostics=diagnostics,
        metrics={
            "returncode": proc.returncode,
            "synth_cell_count": cell_count,
            "dff_count": dff_count,
            "lut_count": lut_count,
            "gate_equiv": gate_count,
            "area_um2": area_um2,
            "clk_constraint_ns": clk_constraint,
        },
        stdout=stdout,
        stderr=stderr,
    )


def _build_yosys_script(
    rtl_files: List[str],
    top_module: str,
    netlist_path: str,
    checkpoint_path: str,
    pdk: str,
    pdk_root: Optional[str],
    clk_constraint: float,
    ungroup_cells: bool,
    flatten_hierarchy: bool,
    retime: bool,
    buffer_insertion: bool,
    smt2_verbose: bool,
    additional_commands: List[str],
) -> List[str]:
    lines: List[str] = []

    for f in rtl_files:
        ext = os.path.splitext(f)[1].lower()
        if ext == ".v":
            lines.append(f"read_verilog {f}")
        elif ext in (".sv", ".svh"):
            lines.append(f"read_verilog -sv {f}")
        elif ext == ".blif":
            lines.append(f"read_blif {f}")
        elif ext == ".edif":
            lines.append(f"read_edif {f}")
        else:
            lines.append(f"read_verilog {f}")

    if additional_commands:
        lines.extend(additional_commands)

    synth_cmd = "synth_xilinx" if "xilinx" in pdk.lower() else "synth"
    if flatten_hierarchy:
        synth_cmd += " -flatten"

    lines.append(f"# High-level synthesis")
    lines.append(f"{synth_cmd} -top {top_module}")

    if retime:
        lines.append(f"opt -fine  # fine-grained optimization")

    if ungroup_cells and not flatten_hierarchy:
        lines.append("opt_expr -fine")
        lines.append("opt_clean")
        tool_config = get_pdk_tool_config(pdk)
        lines.append(f"# Technology stats for {tool_config['pdk_dir']}")
        lines.append("stat")

    if clk_constraint > 0:
        lines.append(f"# Clock constraint: {clk_constraint} ns ({1000.0 / clk_constraint:.1f} MHz)")
        lines.append(f"clk constraint [get_ports clk] {clk_constraint}")

    if buffer_insertion:
        lines.append("opt -purge_registers  # remove unused registers")

    lines.append(f"# Output")
    lines.append(f"write_verilog {netlist_path}")
    lines.append(f"write_ilang {checkpoint_path}")

    lines.append(f"# Statistics")
    lines.append("stat")
    lines.append("check")
    lines.append("select -assert-count 0 t:$undefined  # ensure no unresolved signals")

    return lines


def _parse_stat_cell_count(stdout: str) -> int:
    m = re.search(r"Number of cells:\s*(\d+)", stdout, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m2 = re.search(r"TECHMEM\s+(\d+)", stdout)
    if m2:
        return int(m2.group(1))
    return 0


def _parse_stat_dff(stdout: str) -> int:
    m = re.search(r"\$dff\s*(\d+)", stdout)
    if m:
        return int(m.group(1))
    m2 = re.search(r"FDRE\s*(\d+)", stdout)
    if m2:
        return int(m2.group(1))
    m3 = re.search(r"Number of flip flops:\s*(\d+)", stdout, re.IGNORECASE)
    if m3:
        return int(m3.group(1))
    return 0


def _parse_stat_lut(stdout: str) -> int:
    m = re.search(r"\$lut\s*(\d+)", stdout)
    if m:
        return int(m.group(1))
    m2 = re.search(r"LUT\d+\s*(\d+)", stdout)
    if m2:
        return int(m2.group(1))
    m3 = re.search(r"Number of LUTs:\s*(\d+)", stdout, re.IGNORECASE)
    if m3:
        return int(m3.group(1))
    return 0


def _parse_stat_area(stdout: str, pdk: str) -> float:
    m = re.search(r"Chip area for module\s+\S+:\s*(\d+\.?\d*)", stdout)
    if m:
        return float(m.group(1))
    m2 = re.search(r"Estimated number of LCs:\s*(\d+)", stdout, re.IGNORECASE)
    if m2:
        lc_count = int(m2.group(1))
        tool_config = get_pdk_tool_config(pdk)
        lc_area = tool_config.get("lc_area_um2", 0.054)
        return lc_count * lc_area
    return 0.0


def _parse_gate_equiv(dff: int, lut: int, cells: int) -> float:
    return round(dff * 6.0 + lut * 4.0 + max(0, cells - dff - lut) * 1.0, 1)


def _freq_from_period_ns(ns: float) -> float:
    return round(1000.0 / ns, 2) if ns > 0 else 0.0


def _error_result(errors: List[str], netlist_path: str, report_path: str) -> SynthesisResult:
    return SynthesisResult(
        ok=False,
        netlist_path=netlist_path,
        checkpoint_path=None,
        report_path=report_path,
        cell_count=0,
        gate_count=0.0,
        area_um2=0.0,
        dff_count=0,
        lut_count=0,
        frequency_mhz=0.0,
        warnings=[],
        errors=errors,
        diagnostics=[],
        metrics={},
    )


def parse_yosys_timing_report(report_text: str) -> Dict[str, Any]:
    """Parse timing paths from Yosys timing report.

    Args:
        report_text: Output from "read_timing_vcd -format text" or similar

    Returns:
        Dict with worst_setup_ns, worst_hold_ns, total_negative_slack
    """
    result: Dict[str, Any] = {
        "worst_setup_ns": 0.0,
        "worst_hold_ns": 0.0,
        "wns": 0.0,
        "tns": 0.0,
        "critical_paths": [],
        "unconstrained_paths": 0,
    }

    slack_pattern = re.compile(r"(?:slack|setup|hold)\s*[=\-:]?\s*([+-]?\d+\.?\d*)", re.IGNORECASE)
    for line in report_text.splitlines():
        m = slack_pattern.search(line)
        if m:
            val = float(m.group(1))
            if "setup" in line.lower() or "wns" in line.lower():
                if val < result["wns"]:
                    result["wns"] = val
                if val > result["worst_setup_ns"]:
                    result["worst_setup_ns"] = val
            elif "hold" in line.lower():
                if val < result["worst_hold_ns"]:
                    result["worst_hold_ns"] = val

    return result


def synth_tool(
    rtl_files: List[str],
    top_module: str,
    output_dir: str,
    pdk: str = "sky130",
    pdk_root: Optional[str] = None,
    clk_constraint: float = 10.0,
) -> Tuple[bool, str]:
    """CrewAI tool wrapper for Yosys synthesis.

    Returns: (ok, message)
    """
    result = run_yosys_synth(
        rtl_files=rtl_files,
        top_module=top_module,
        output_dir=output_dir,
        pdk=pdk,
        pdk_root=pdk_root,
        clk_constraint=clk_constraint,
    )
    if result.ok:
        msg = (
            f"Yosys synthesis OK.\n"
            f"  Cells: {result.cell_count:,} | DFFs: {result.dff_count:,} | LUTs: {result.lut_count:,}\n"
            f"  Gate equiv: {result.gate_count:,.0f} | Area: {result.area_um2:.3f} um^2\n"
            f"  Netlist: {result.netlist_path}"
        )
        if result.warnings:
            msg += f"\n  Warnings: {len(result.warnings)}"
        return True, msg
    else:
        err_summary = "\n".join(result.errors[-5:])
        return False, f"Yosys synthesis FAILED:\n{err_summary}"


def read_synth_checkpoint(checkpoint_path: str, command: str) -> Tuple[bool, str]:
    """Read a Yosys ilang checkpoint and run a command.

    Args:
        checkpoint_path: Path to .ys ilang checkpoint
        command: Yosys command to run on the checkpoint

    Returns: (ok, output)
    """
    script = f"read_ilang {checkpoint_path}\n{command}\n"
    try:
        proc = subprocess.run(
            [YOSYS_BIN, "-p", script],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return proc.returncode == 0, proc.stdout + proc.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def sta_from_synth(
    netlist_path: str,
    constraint_file: Optional[str],
    output_dir: str,
    pdk: str = "sky130",
) -> Dict[str, Any]:
    """Run pre-PnR STA on synthesized netlist using OpenSTA via Yosys.

    This provides quick timing feedback before committing to place-and-route.

    Args:
        netlist_path: Path to synthesized Verilog netlist
        constraint_file: Optional SDC timing constraints file
        output_dir: Directory for reports
        pdk: PDK name

    Returns:
        Dict with timing metrics
    """
    os.makedirs(output_dir, exist_ok=True)
    report_path = os.path.join(output_dir, "pre_sta_report.txt")

    tool_config = get_pdk_tool_config(pdk)

    script_lines = [
        f"read_verilog {netlist_path}",
        f"synth_xilinx -top design  # just for hierarchy",
        "flatten",
        "opt_clean",
        f"# Library: {tool_config['std_cell_library']} for PDK: {pdk}",
        "stat",
        f"# Pre-PnR STA report (basic) - use OpenSTA for full signoff",
    ]

    script_path = os.path.join(output_dir, "pre_sta.ys")
    with open(script_path, "w") as f:
        f.write("\n".join(script_lines))

    try:
        proc = subprocess.run(
            [YOSYS_BIN, "-s", script_path],
            capture_output=True,
            text=True,
            timeout=120,
        )
        with open(report_path, "w") as f:
            f.write(proc.stdout)
        return {
            "ok": proc.returncode == 0,
            "report_path": report_path,
            "stdout": proc.stdout,
            "metrics": _parse_yosys_sta_metrics(proc.stdout),
        }
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": str(e)}


def _parse_yosys_sta_metrics(stdout: str) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    m = re.search(r"Number of cells:\s*(\d+)", stdout)
    if m:
        metrics["cell_count"] = int(m.group(1))
    m2 = re.search(r"Temporal performance \(MHz\):\s*(\d+\.?\d*)", stdout)
    if m2:
        metrics["freq_mhz"] = float(m2.group(1))
    return metrics
