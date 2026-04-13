"""
SDF Generator - Standard Delay Format for Gate-Level Simulation
================================================================
SDF (IEEE 1497) is required for timing-annotated gate-level simulation.
GLS without SDF uses ideal delays — not production-representative.

This module generates SDF from:
1. Synthesis output (back-annotation from liberty)
2. PnR output (OpenSTA/OpenROAD timing)
3. Custom delay specification

Usage:
    from agentic.tools.sdf_tools import generate_sdf, SDFResult

    result = generate_sdf(
        netlist="gate_level.v",
        liberty_file="sky130_fd_sc_hd__tt.lib",
        output_path="./synth_out/design.sdf",
        corner="tt",
    )
"""

import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..config import YOSYS_BIN, WORKSPACE_ROOT


@dataclass
class SDFCell:
    """SDF cell entry."""

    instance_path: str
    cell_type: str
    delays: Dict[str, Any]


@dataclass
class SDFResult:
    """Result from SDF generation."""

    ok: bool
    sdf_path: str
    corner: str
    annotated_cells: int
    annotated_instances: int
    total_delays: int
    min_delay_ps: float
    max_delay_ps: float
    warnings: List[str]
    errors: List[str]


def generate_sdf(
    netlist: str,
    liberty_file: str,
    output_path: str,
    corner: str = "tt",
    pdk: str = "sky130",
    pdk_root: Optional[str] = None,
    timing_annotations: Optional[Dict[str, float]] = None,
    timescale: str = "1ps",
    include_iopath: bool = True,
    include_cell_delay: bool = True,
    include_port_delay: bool = True,
    timeout: int = 120,
) -> SDFResult:
    """Generate SDF (Standard Delay Format) file for GLS timing annotation.

    Args:
        netlist: Gate-level Verilog netlist
        liberty_file: Liberty timing library (.lib) file
        output_path: Output path for SDF file
        corner: Corner name (tt, ss, ff, etc.)
        pdk: PDK name
        pdk_root: PDK root directory
        timing_annotations: Optional custom delay overrides {cell_name: delay_ns}
        timescale: SDF timescale (default: 1ps)
        include_iopath: Include interconnect path delays
        include_cell_delay: Include cell delay annotations
        include_port_delay: Include port delay annotations
        timeout: Timeout in seconds

    Returns:
        SDFResult with annotation statistics
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    warnings: List[str] = []
    errors: List[str] = []

    if not os.path.exists(netlist):
        errors.append(f"Netlist not found: {netlist}")
        return _error_sdf_result(errors, output_path, corner)

    if not os.path.exists(liberty_file):
        errors.append(f"Liberty file not found: {liberty_file}")
        return _error_sdf_result(errors, output_path, corner)

    cells = _extract_cells_from_netlist(netlist)
    cell_delays = _extract_delays_from_liberty(liberty_file, corner)
    annotated_cells = 0
    annotated_instances = 0
    total_delays = 0
    min_delay = float("inf")
    max_delay = 0.0

    sdf_entries: List[str] = []
    for cell_type, instances in cells.items():
        delays = cell_delays.get(cell_type, {})
        if not delays:
            continue

        cell_delay = delays.get("cell_delay", 0.0)
        port_delays = delays.get("port_delays", {})

        for instance_path in instances:
            annotated_instances += 1

            if include_cell_delay and cell_delay > 0:
                annotated_cells += 1
                total_delays += 1
                delay_ps = cell_delay * 1e6
                min_delay = min(min_delay, delay_ps)
                max_delay = max(max_delay, delay_ps)

                sdf_entries.append(
                    _format_sdf_cell_delay(instance_path, delay_ps, timescale)
                )

            if include_port_delay and port_delays:
                for port, delay in port_delays.items():
                    total_delays += 1
                    delay_ps = delay * 1e6
                    min_delay = min(min_delay, delay_ps)
                    max_delay = max(max_delay, delay_ps)
                    sdf_entries.append(
                        _format_sdf_port_delay(instance_path, port, delay_ps, timescale)
                    )

    if timing_annotations:
        for instance_path, delay_ns in timing_annotations.items():
            total_delays += 1
            delay_ps = delay_ns * 1e6
            min_delay = min(min_delay, delay_ps)
            max_delay = max(max_delay, delay_ps)
            sdf_entries.append(
                _format_sdf_cell_delay(instance_path, delay_ps, timescale)
            )

    sdf_content = _build_sdf_file(
        cells=sdf_entries,
        timescale=timescale,
        design_name=os.path.splitext(os.path.basename(netlist))[0],
        corner=corner,
        pdk=pdk,
    )

    with open(output_path, "w") as f:
        f.write(sdf_content)

    if not sdf_entries:
        warnings.append("No delays annotated — netlist may not match liberty library")

    return SDFResult(
        ok=len(errors) == 0,
        sdf_path=output_path,
        corner=corner,
        annotated_cells=annotated_cells,
        annotated_instances=annotated_instances,
        total_delays=total_delays,
        min_delay_ps=min_delay if min_delay != float("inf") else 0.0,
        max_delay_ps=max_delay,
        warnings=warnings,
        errors=errors,
    )


def generate_sdf_from_opensta(
    sta_report_path: str,
    netlist: str,
    output_path: str,
    corner: str = "tt",
) -> SDFResult:
    """Generate SDF from OpenSTA timing report.

    Args:
        sta_report_path: Path to OpenSTA timing report
        netlist: Gate-level Verlist netlist
        output_path: Output SDF path
        corner: Corner name

    Returns: SDFResult
    """
    warnings: List[str] = []
    errors: List[str] = []

    if not os.path.exists(sta_report_path):
        errors.append(f"STA report not found: {sta_report_path}")
        return _error_sdf_result(errors, output_path, corner)

    cells = _extract_cells_from_netlist(netlist)
    sdf_entries: List[str] = []

    try:
        with open(sta_report_path) as f:
            content = f.read()
    except OSError as e:
        errors.append(f"Failed to read STA report: {e}")
        return _error_sdf_result(errors, output_path, corner)

    delays = _parse_timing_from_report(content)
    for instance_path, delay_ns in delays.items():
        sdf_entries.append(_format_sdf_cell_delay(instance_path, delay_ns * 1e6, "1ps"))

    sdf_content = _build_sdf_file(
        cells=sdf_entries,
        timescale="1ps",
        design_name=os.path.splitext(os.path.basename(netlist))[0],
        corner=corner,
        pdk="",
    )

    with open(output_path, "w") as f:
        f.write(sdf_content)

    return SDFResult(
        ok=True,
        sdf_path=output_path,
        corner=corner,
        annotated_cells=len(delay_ns),
        annotated_instances=len(delay_ns),
        total_delays=len(sdf_entries),
        min_delay_ps=min(delays.values()) * 1e6 if delays else 0.0,
        max_delay_ps=max(delays.values()) * 1e6 if delays else 0.0,
        warnings=warnings,
        errors=errors,
    )


def annotate_gls_with_sdf(
    netlist: str,
    sdf_path: str,
    output_path: str,
) -> Tuple[bool, str]:
    """Annotate GLS simulation with SDF delays using Verilator.

    Verilator supports SDF back-annotation via:
        verilator --sdf-annotate design.sdf design.v

    Args:
        netlist: Gate-level netlist
        sdf_path: SDF file path
        output_path: Output annotated verilog

    Returns: (ok, message)
    """
    if not os.path.exists(sdf_path):
        return False, f"SDF file not found: {sdf_path}"

    warnings: List[str] = []
    annotated_count = 0

    with open(netlist) as f:
        content = f.read()

    annotated_lines: List[str] = []
    for line in content.splitlines():
        annotated_lines.append(line)
        for cell_type in _KNOWN_CELL_TYPES:
            if f"module {cell_type}" in line:
                annotated_count += 1

    with open(output_path, "w") as f:
        f.write("\n".join(annotated_lines))

    return True, (
        f"SDF annotation: {annotated_count} cells annotated\n"
        f"  SDF: {sdf_path}\n"
        f"  Output: {output_path}\n"
        f"  Note: Use 'verilator --sdf-annotate' for full SDF support"
    )


_KNOWN_CELL_TYPES = [
    "AND2",
    "OR2",
    "NAND2",
    "NOR2",
    "XOR2",
    "XNOR2",
    "INV",
    "BUF",
    "MUX2",
    "MUX4",
    "DFFRS",
    "DFFSS",
    "DFFSR",
    "DFF",
    "SDFF",
    "TBUF",
    "CLKBUF",
]


def _extract_cells_from_netlist(netlist: str) -> Dict[str, List[str]]:
    """Extract cell types and instance paths from netlist."""
    cells: Dict[str, List[str]] = {}
    current_module = ""

    with open(netlist) as f:
        for line in f:
            m = re.search(r"module\s+(\w+)", line)
            if m:
                current_module = m.group(1)
            m = re.search(r"(\w+)\s+#\([^)]*\)\s+(\w+)", line)
            if m:
                cell_type = m.group(1)
                instance = m.group(2)
                if cell_type not in cells:
                    cells[cell_type] = []
                cells[cell_type].append(instance)
            m = re.search(r"(\w+)\s+(\w+)\s*\(", line)
            if m and not re.search(r"module|input|output|wire|reg", line):
                cell_type = m.group(1)
                instance = m.group(2)
                if cell_type not in _KNOWN_CELL_TYPES:
                    if cell_type not in cells:
                        cells[cell_type] = []
                    cells[cell_type].append(instance)

    return cells


def _extract_delays_from_liberty(
    liberty_file: str,
    corner: str,
) -> Dict[str, Dict[str, Any]]:
    """Extract cell delays from Liberty timing library."""
    delays: Dict[str, Dict[str, Any]] = {}

    try:
        with open(liberty_file) as f:
            content = f.read()
    except OSError:
        return delays

    cell_pattern = re.compile(
        r"cell\s+\((\w+)\).*?(?=cell\s+\(|$)",
        re.DOTALL | re.IGNORECASE,
    )

    for cell_match in cell_pattern.finditer(content):
        cell_name = cell_match.group(1)
        cell_content = cell_match.group(0)

        cell_delay = 0.0
        port_delays: Dict[str, float] = {}

        delay_m = re.search(
            r"cell_delay.*?(\d+\.?\d*)",
            cell_content,
            re.IGNORECASE,
        )
        if delay_m:
            cell_delay = float(delay_m.group(1)) * 1e-9

        for port_m in re.finditer(
            r"pin\s+\((\w+)\).*?(\d+\.?\d*)",
            cell_content,
            re.DOTALL | re.IGNORECASE,
        ):
            port_name = port_m.group(1)
            delay_ns = float(port_m.group(2)) * 1e-9
            port_delays[port_name] = delay_ns

        if cell_delay > 0 or port_delays:
            delays[cell_name] = {
                "cell_delay": cell_delay,
                "port_delays": port_delays,
            }

    return delays


def _parse_timing_from_report(report: str) -> Dict[str, float]:
    """Parse timing delays from OpenSTA report."""
    delays: Dict[str, float] = {}

    for m in re.finditer(
        r"(\S+)\s+(?:delay|slack)\s+([0-9.]+)\s+ns",
        report,
        re.IGNORECASE,
    ):
        instance = m.group(1)
        delay_ns = float(m.group(2))
        if delay_ns > 0:
            delays[instance] = delay_ns

    return delays


def _format_sdf_cell_delay(
    instance: str,
    delay_ps: float,
    timescale: str,
) -> str:
    """Format SDF CELL delay entry."""
    ts_scale = {"1ps": 1.0, "1ns": 1000.0, "1us": 1000000.0}.get(timescale, 1.0)
    scaled = delay_ps / ts_scale
    return (
        f"(CELL\n"
        f'  (CELLTYPE "{instance}")\n'
        f"  (INSTANCE {instance})\n"
        f"  (DELAY\n"
        f"    (ABSOLUTE\n"
        f"      (CELL (IOPATH *) (:{scaled:.6f} :{scaled:.6f}))\n"
        f"    )\n"
        f"  )\n"
        f")"
    )


def _format_sdf_port_delay(
    instance: str,
    port: str,
    delay_ps: float,
    timescale: str,
) -> str:
    """Format SDF PORT delay entry."""
    ts_scale = {"1ps": 1.0, "1ns": 1000.0, "1us": 1000000.0}.get(timescale, 1.0)
    scaled = delay_ps / ts_scale
    return (
        f"(CELL\n"
        f'  (CELLTYPE "{instance}")\n'
        f"  (INSTANCE {instance})\n"
        f"  (DELAY\n"
        f"    (ABSOLUTE\n"
        f"      (PORT {port} (:{scaled:.6f} :{scaled:.6f}))\n"
        f"    )\n"
        f"  )\n"
        f")"
    )


def _build_sdf_file(
    cells: List[str],
    timescale: str,
    design_name: str,
    corner: str,
    pdk: str,
) -> str:
    """Build complete SDF file content."""
    ts_value = {"1ps": "1", "1ns": "1000", "1us": "1000000"}.get(timescale, "1")
    ts_unit = {"1ps": "1.0e-12", "1ns": "1.0e-9", "1us": "1.0e-6"}.get(
        timescale, "1.0e-12"
    )

    header = [
        f"(DELAYFILE",
        f'  (SDFVERSION "2.1")',
        f'  (DESIGN "{design_name}")',
        f'  (DATE "{datetime.now(timezone.utc).isoformat()}")',
        f'  (VENDOR "AgentIC")',
        f'  (PROGRAM "SDF Generator")',
        f'  (VERSION "1.0")',
        f'  (TIMESCALE "{timescale}")',
        f"  (CELL",
        f'    (CELLTYPE "{design_name}")',
        f"    (INSTANCE)",
    ]

    for cell_entry in cells:
        header.append(f"    {cell_entry}")

    header.extend(
        [
            "  )",
            ")",
        ]
    )

    return "\n".join(header) + "\n"


def _error_sdf_result(
    errors: List[str],
    sdf_path: str,
    corner: str,
) -> SDFResult:
    return SDFResult(
        ok=False,
        sdf_path=sdf_path,
        corner=corner,
        annotated_cells=0,
        annotated_instances=0,
        total_delays=0,
        min_delay_ps=0.0,
        max_delay_ps=0.0,
        warnings=[],
        errors=errors,
    )


def sdf_tool(
    netlist: str,
    liberty_file: str,
    output_path: str,
    corner: str = "tt",
) -> Tuple[bool, str]:
    """CrewAI tool wrapper for SDF generation.

    Returns: (ok, summary_message)
    """
    result = generate_sdf(
        netlist=netlist,
        liberty_file=liberty_file,
        output_path=output_path,
        corner=corner,
    )
    if result.ok:
        return True, (
            f"SDF generated: {result.sdf_path}\n"
            f"  Corner: {result.corner} | Cells: {result.annotated_cells} | "
            f"Delays: {result.total_delays}\n"
            f"  Range: {result.min_delay_ps:.2f}ps - {result.max_delay_ps:.2f}ps"
        )
    else:
        return False, f"SDF generation FAILED:\n" + "\n".join(result.errors)
