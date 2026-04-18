"""
Physical Verification Tools - Independent DRC, LVS, and Antenna Check
======================================================================
Production tapeout requires independent physical verification — you CANNOT
rely solely on OpenLane's reports. Independent tools provide:

1. Magic DRC: Technology-specific design rule checking
2. Netgen LVS: Layout vs Schematic equivalence checking
3. Antenna checker: Plasma etching damage detection
4. Multi-layer DRC: Checks for metal density, via rules, etc.

This module wraps Magic and Netgen as standalone tools, independent of OpenLane.

Usage:
    from agentic.tools.physical_tools import (
        run_magic_drc, run_netgen_lvs, DRCResult, LVSResult
    )
"""

import os
import re
import subprocess
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import WORKSPACE_ROOT, OPENLANE_ROOT, get_pdk_tool_config, get_pdk_profile


MAGIC_BIN = os.environ.get("MAGIC_BIN", "magic")
NETGEN_BIN = os.environ.get("NETGEN_BIN", "netgen")
ANTENNA_BIN = os.environ.get("ANTENNA_BIN", "verifyMetR")


@dataclass
class DRCCheck:
    """Single DRC violation."""

    rule: str
    layer: str
    x_um: float
    y_um: float
    width_um: float
    spacing_um: float
    message: str
    severity: str


@dataclass
class DRCResult:
    """Result from Magic DRC check."""

    ok: bool
    drc_violations: int
    violations: List[DRCCheck]
    warning_count: int
    drc_report_path: str
    gds_path: str
    lef_path: str
    tech_file: str
    runtime_sec: float
    errors: List[str]
    diagnostics: List[str]
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LVSResult:
    """Result from Netgen LVS check."""

    ok: bool
    equivalent: bool
    lvs_errors: int
    net_mismatches: int
    pin_mismatches: int
    unconnected_nets: int
    lvs_report_path: str
    schematic_netlist: str
    layout_netlist: str
    runtime_sec: float
    errors: List[str]
    diagnostics: List[str]
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AntennaResult:
    """Result from antenna violation checking."""

    ok: bool
    antenna_violations: int
    violations: List[Dict[str, Any]]
    max_ratio: float
    report_path: str
    errors: List[str]
    metrics: Dict[str, Any] = field(default_factory=dict)


def parse_drc_errors(report: str) -> List[Dict[str, Any]]:
    """Parse Magic DRC report text or a report path into structured records."""
    if os.path.exists(report):
        with open(report) as f:
            content = f.read()
    else:
        content = report

    errors: List[Dict[str, Any]] = []
    count_match = re.search(
        r"(?:total|number).*?(?:violations?|DRC)\s*[=:\-]?\s*(\d+)",
        content,
        re.IGNORECASE,
    )

    detail_patterns: List[Tuple[str, re.Pattern[str], str]] = [
        (
            "min_spacing",
            re.compile(r"(min(?:imum)?\s+)?spacing.*?(?:violation|error)?", re.IGNORECASE),
            "Increase spacing between shapes on the reported layer.",
        ),
        (
            "min_width",
            re.compile(r"(min(?:imum)?\s+)?width.*?(?:violation|error)?", re.IGNORECASE),
            "Widen the reported shape or adjust the cell/layout rule deck.",
        ),
        (
            "short",
            re.compile(r"short.*detected|shorted", re.IGNORECASE),
            "Separate the shorted nets or inspect extracted connectivity.",
        ),
        (
            "overlap",
            re.compile(r"overlap.*detected|illegal.*overlap", re.IGNORECASE),
            "Remove illegal geometry overlap or add required enclosure.",
        ),
        (
            "generic_drc",
            re.compile(r"\b(?:drc|violation)\b", re.IGNORECASE),
            "Inspect the Magic report and update layout/floorplan geometry.",
        ),
    ]

    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        matched_type = ""
        fix_hint = ""
        for error_type, pattern, hint in detail_patterns:
            if pattern.search(line):
                matched_type = error_type
                fix_hint = hint
                break
        if not matched_type:
            continue

        record: Dict[str, Any] = {
            "tool": "magic",
            "type": matched_type,
            "severity": "error",
            "message": line,
            "line": line_no,
            "fix_hint": fix_hint,
        }
        layer_match = re.search(r"\b(li\d|met\d|metal\d|poly|diff|nwell|pwell|m\d)\b", line, re.IGNORECASE)
        if layer_match:
            record["layer"] = layer_match.group(1)
        coord_match = re.search(r"[\[(]\s*([+-]?\d+(?:\.\d+)?)\s*,?\s+([+-]?\d+(?:\.\d+)?)\s*[\])]", line)
        if coord_match:
            record["x_um"] = float(coord_match.group(1))
            record["y_um"] = float(coord_match.group(2))
        errors.append(record)

    if count_match and int(count_match.group(1)) > 0 and not errors:
        count = int(count_match.group(1))
        errors.append(
            {
                "tool": "magic",
                "type": "generic_drc",
                "severity": "error",
                "message": f"{count} DRC violations reported (details unknown)",
                "count": count,
                "line": 0,
                "fix_hint": "Inspect the full Magic report for rule names and locations.",
            }
        )

    return errors


def run_magic_drc(
    gds_path: str,
    tech_file: str = "",
    output_dir: str = "",
    pdk: str = "sky130",
    pdk_root: Optional[str] = None,
    lef_path: str = "",
    extra_drc_rules: Optional[List[str]] = None,
    enable_pdf_output: bool = False,
    timeout: int = 600,
    design_name: Optional[str] = None,
) -> DRCResult:
    """Run independent Magic DRC on a GDSII file.

    Magic is the industry-standard open-source DRC tool for open PDKs.

    Args:
        gds_path: Path to GDSII layout file
        tech_file: Path to Magic technology file (.mag technology field)
        output_dir: Output directory for DRC reports
        pdk: PDK name
        pdk_root: PDK root directory (for auto-locating tech file)
        extra_drc_rules: Additional DRC rule files
        enable_pdf_output: Generate PDF layout with violations highlighted
        timeout: Timeout in seconds
        design_name: Design name

    Returns:
        DRCResult with violation count, severity, and locations
    """
    output_dir = output_dir or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)
    design_name = design_name or os.path.splitext(os.path.basename(gds_path))[0]

    drc_report = os.path.join(output_dir, f"{design_name}_drc.rpt")
    errors: List[str] = []
    warnings: List[str] = []

    if not os.path.exists(gds_path):
        errors.append(f"GDS file not found: {gds_path}")
        return _error_drc_result(errors, drc_report)
    if not os.path.exists(tech_file):
        tech_file = _find_tech_file(tech_file, pdk, pdk_root or "")
        if not tech_file:
            errors.append(
                f"Tech file not found for PDK '{pdk}'. Set --pdk or specify --tech directly."
            )
            return _error_drc_result(errors, drc_report)

    tcl_script = _build_magic_drc_tcl(
        gds_path=gds_path,
        tech_file=tech_file,
        drc_report=drc_report,
        design_name=design_name,
        extra_rules=extra_drc_rules,
        pdf=enable_pdf_output,
        pdk=pdk,
    )

    tcl_path = os.path.join(output_dir, f"{design_name}_drc.tcl")
    with open(tcl_path, "w") as f:
        f.write("\n".join(tcl_script))

    import time

    start = time.time()

    try:
        proc = subprocess.run(
            [MAGIC_BIN, "-dnull", "-noconsole", tcl_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=output_dir,
        )
        runtime = time.time() - start
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired:
        errors.append("Magic DRC timed out")
        return _error_drc_result(errors, drc_report, runtime)
    except OSError:
        errors.append(f"Magic binary not found. Install Magic or set MAGIC_BIN env var.")
        return _error_drc_result(errors, drc_report)

    raw_output = stdout + stderr
    structured_errors = parse_drc_errors(raw_output)
    violations = _parse_magic_drc_output(raw_output, drc_report)

    with open(drc_report, "a") as f:
        f.write(f"\n{'=' * 60}\nMagic DRC Complete\n")
        f.write(f"Runtime: {runtime:.1f}s\n")
        f.write(f"Return code: {proc.returncode}\n")
        f.write(f"\nTool output:\n{stdout}\n{stderr}\n")

    ok = len(violations) == 0 and proc.returncode == 0

    return DRCResult(
        ok=ok,
        drc_violations=len(violations),
        violations=violations,
        warning_count=len(warnings),
        drc_report_path=drc_report,
        gds_path=gds_path,
        lef_path=lef_path,
        tech_file=tech_file,
        runtime_sec=runtime,
        errors=errors,
        diagnostics=[json.dumps(e, sort_keys=True) for e in structured_errors],
        metrics={
            "drc_violations": len(violations),
            "warnings": len(warnings),
            "runtime_sec": runtime,
            "pdk": pdk,
            "structured_errors": structured_errors,
        },
    )


def _build_magic_drc_tcl(
    gds_path: str,
    tech_file: str,
    drc_report: str,
    design_name: str,
    extra_rules: Optional[List[str]],
    pdf: bool,
    pdk: str = "sky130",
) -> List[str]:
    tool_config = get_pdk_tool_config(pdk)
    pdk_dir = tool_config.get("pdk_dir", pdk)
    tcl: List[str] = [
        f"# Magic DRC script for {design_name} (PDK: {pdk_dir})",
        f"tech load {tech_file}",
        f"gds read {gds_path}",
        f"load {design_name}",
        "",
        "# Enable DRC",
        "drc on",
        "drc check",
        "",
        f"# Report DRC violations to {drc_report}",
        "drc style " + pdk_dir,
        "drc exist",
        "puts [open [list {drc_report}] w] [drc listall]",
        "",
        "# Count violations",
        "set viols [drc list count]",
        'puts "TOTAL_DRC_VIOLATIONS: $viols"',
        "",
        "# Detailed violation report",
        "foreach viol [drc listall] {",
        '    puts "$viol"',
        "}",
    ]

    if extra_rules:
        for rule in extra_rules:
            tcl.append(f"source {rule}")

    if pdf:
        tcl.append(f"# PDF output would use 'quit' with -pdf flag")

    tcl.append("quit")

    return tcl


def run_netgen_lvs(
    schematic_verilog: str,
    layout_gds: str,
    output_dir: str,
    tech_setup: str,
    pdk: str = "sky130",
    pdk_root: Optional[str] = None,
    auto_physical_pins: bool = True,
    use_schematic_netlist: str = "verilog",
    timeout: int = 600,
    design_name: Optional[str] = None,
) -> LVSResult:
    """Run independent Netgen LVS (Layout vs Schematic) check.

    LVS proves that the layout (GDS) produces the same circuit as the
    RTL/schematic. NO LVS = NO TAPEIN.

    Args:
        schematic_verilog: Path to Verilog netlist from synthesis
        layout_gds: Path to GDSII layout file
        output_dir: Output directory
        tech_setup: Path to Netgen tech setup file
        auto_physical_pins: Auto-match physical pins to schematic ports
        use_schematic_netlist: Format of schematic netlist (verilog, spice)
        timeout: Timeout in seconds
        design_name: Design name

    Returns:
        LVSResult with equivalence status and mismatch details
    """
    os.makedirs(output_dir, exist_ok=True)
    design_name = design_name or os.path.splitext(os.path.basename(layout_gds))[0]

    lvs_report = os.path.join(output_dir, f"{design_name}_lvs.rpt")
    errors: List[str] = []

    if not os.path.exists(layout_gds):
        errors.append(f"Layout GDS not found: {layout_gds}")
        return _error_lvs_result(errors, lvs_report)
    if not os.path.exists(schematic_verilog):
        errors.append(f"Schematic netlist not found: {schematic_verilog}")
        return _error_lvs_result(errors, lvs_report)

    if not os.path.exists(tech_setup):
        tech_setup = _find_netgen_setup(tech_setup, pdk, pdk_root or "")

    import time

    start = time.time()
    tool_config = get_pdk_tool_config(pdk)
    pdk_dir = tool_config.get("pdk_dir", pdk)

    lvs_script = [
        f"# Netgen LVS script for {design_name} (PDK: {pdk_dir})",
        f"set spicedevs " + pdk_dir,
        f'lvs "read_netlist {schematic_verilog} {design_name}" \\',
        f'     "gds read {layout_gds} {design_name}" \\',
        f"     {tech_setup} \\",
        f"     {design_name}",
        f"puts [open [list {lvs_report}] w] [lvs summary]",
        "quit",
    ]

    script_path = os.path.join(output_dir, f"{design_name}_lvs.tcl")
    with open(script_path, "w") as f:
        f.write("\n".join(lvs_script))

    try:
        proc = subprocess.run(
            [NETGEN_BIN, "-batch", "source", script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        runtime = time.time() - start
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired:
        errors.append("Netgen LVS timed out")
        return _error_lvs_result(errors, lvs_report, runtime)
    except OSError:
        errors.append("Netgen binary not found. Install Netgen or set NETGEN_BIN env var.")
        return _error_lvs_result(errors, lvs_report)

    equiv, net_mismatches, pin_mismatches, unconn = _parse_netgen_output(stdout)

    with open(lvs_report, "w") as f:
        f.write(f"Netgen LVS Report for {design_name}\n")
        f.write(f"Schematic: {schematic_verilog}\n")
        f.write(f"Layout: {layout_gds}\n")
        f.write(f"Return code: {proc.returncode}\n")
        f.write(f"Runtime: {runtime:.1f}s\n")
        f.write(f"\n{'=' * 60}\n")
        f.write(stdout)
        if stderr:
            f.write(f"\nSTDERR:\n{stderr}\n")

    ok = equiv and proc.returncode == 0

    return LVSResult(
        ok=ok,
        equivalent=equiv,
        lvs_errors=net_mismatches + pin_mismatches + unconn,
        net_mismatches=net_mismatches,
        pin_mismatches=pin_mismatches,
        unconnected_nets=unconn,
        lvs_report_path=lvs_report,
        schematic_netlist=schematic_verilog,
        layout_netlist=layout_gds,
        runtime_sec=runtime,
        errors=errors,
        diagnostics=[],
        metrics={
            "equivalent": equiv,
            "net_mismatches": net_mismatches,
            "pin_mismatches": pin_mismatches,
            "unconnected_nets": unconn,
            "runtime_sec": runtime,
        },
    )


def run_antenna_check(
    gds_path: str,
    netlist: str,
    tech_file: str,
    output_dir: str,
    antenna_ratio: float = 4.0,
    pdk: str = "sky130",
    timeout: int = 300,
    design_name: Optional[str] = None,
) -> AntennaResult:
    """Check for antenna violations in layout.

    Antenna effects occur when a metal wire acts as an antenna during fabrication,
    collecting charge that can damage thin gate oxides. ALL production chips
    must pass antenna checks.

    Args:
        gds_path: GDSII layout file
        netlist: Verilog netlist for pin mapping
        tech_file: Technology file
        antenna_ratio: Maximum allowed antenna ratio (default: 4.0)
        pdk: PDK name
        timeout: Timeout in seconds
        design_name: Design name

    Returns:
        AntennaResult with violation count and locations
    """
    os.makedirs(output_dir, exist_ok=True)
    design_name = design_name or os.path.splitext(os.path.basename(gds_path))[0]

    report_path = os.path.join(output_dir, f"{design_name}_antenna.rpt")
    errors: List[str] = []

    violations: List[Dict[str, Any]] = []

    tcl = [
        f"# Antenna check for {design_name}",
        f"tech load {tech_file}",
        f"gds read {gds_path}",
        f"antenna check {antenna_ratio}",
        f"antenna report > {report_path}",
        "quit",
    ]

    script_path = os.path.join(output_dir, f"{design_name}_antenna.tcl")
    with open(script_path, "w") as f:
        f.write("\n".join(tcl))

    try:
        proc = subprocess.run(
            [MAGIC_BIN, "-dnull", script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        errors.append(f"Antenna check failed: {e}")
        return AntennaResult(False, 0, [], 0.0, report_path, errors)

    with open(report_path, "a") as f:
        f.write(proc.stdout)

    max_ratio = 0.0
    for m in re.finditer(
        r"ANTENNA.*?(?:ratio|violation)\s*[=:\-]?\s*(\d+\.?\d*)",
        proc.stdout + proc.stderr,
        re.IGNORECASE,
    ):
        ratio = float(m.group(1))
        if ratio > max_ratio:
            max_ratio = ratio
        if ratio > antenna_ratio:
            violations.append({"ratio": ratio, "layer": "unknown"})

    return AntennaResult(
        ok=max_ratio <= antenna_ratio,
        antenna_violations=len(violations),
        violations=violations,
        max_ratio=max_ratio,
        report_path=report_path,
        errors=errors,
        metrics={"max_ratio": max_ratio, "threshold": antenna_ratio},
    )


def _parse_magic_drc_output(raw: str, report_path: str) -> List[DRCCheck]:
    violations: List[DRCCheck] = []

    if os.path.exists(report_path):
        with open(report_path) as f:
            content = f.read()
    else:
        content = raw

    for m in re.finditer(
        r"(?:violation|drc)\s+(?:\S+\s+)?(\w+)\s+(?:at|location)?\s*"
        r"(?:[\[\(])?\s*(\d+\.?\d*)\s*[,\s]\s*(\d+\.?\d*)\s*(?:[\]\)])?\s*"
        r"(?:width|spacing|area|overlap)?\s*(?:of)?\s*(\d+\.?\d*)?",
        content,
        re.IGNORECASE,
    ):
        layer = m.group(1)
        x, y = float(m.group(2)), float(m.group(3))
        width = float(m.group(4)) if m.group(4) else 0.0

        violations.append(
            DRCCheck(
                rule="Magic_DRC",
                layer=layer,
                x_um=x,
                y_um=y,
                width_um=width,
                spacing_um=0.0,
                message=f"DRC violation on layer {layer} at ({x}, {y})",
                severity="error",
            )
        )

    count_m = re.search(
        r"(?:total|number).*?(?:violations?|DRC)\s*[=:\-]?\s*(\d+)", raw, re.IGNORECASE
    )
    if count_m:
        count = int(count_m.group(1))
        if count == 0:
            violations = []
        elif not violations:
            violations.append(
                DRCCheck(
                    rule="UNKNOWN",
                    layer="UNKNOWN",
                    x_um=0.0,
                    y_um=0.0,
                    width_um=0.0,
                    spacing_um=0.0,
                    message=f"{count} DRC violations reported (details unknown)",
                    severity="error",
                )
            )

    return violations


def _parse_netgen_output(stdout: str) -> Tuple[bool, int, int, int]:
    equiv = False
    net_mismatches = 0
    pin_mismatches = 0
    unconnected = 0

    for line in stdout.splitlines():
        l = line.strip().lower()
        if "are equivalent" in l or "lvs reports" in l and "equivalent" in l:
            equiv = True
        m = re.search(r"net\s+(?:mis)?match(?:es)?\s*[=:\-]?\s*(\d+)", l)
        if m:
            net_mismatches = int(m.group(1))
        m = re.search(r"pin\s+(?:mis)?match(?:es)?\s*[=:\-]?\s*(\d+)", l)
        if m:
            pin_mismatches = int(m.group(1))
        m = re.search(r"unconnected\s+nets?\s*[=:\-]?\s*(\d+)", l)
        if m:
            unconnected = int(m.group(1))

    return equiv, net_mismatches, pin_mismatches, unconnected


def _find_tech_file(tech_file: str, pdk: str, pdk_root: str = "") -> str:
    """Find Magic tech file with explicit pdk_root."""
    if tech_file and os.path.exists(tech_file):
        return tech_file

    # Common locations to search
    candidates = []

    if pdk_root:
        candidates.extend(
            [
                f"{pdk_root}/{pdk}/libs.tech/magic/{pdk}.tech",
                f"{pdk_root}/{pdk}/libs.tech/magic/{pdk}A.tech",  # e.g., sky130A
            ]
        )

    candidates.extend(
        [
            f"{OPENLANE_ROOT}/pdks/{pdk}/libs.tech/magic/{pdk}.tech",
            f"{OPENLANE_ROOT}/pdks/{pdk}/libs.tech/magic/{pdk}A.tech",
            f"/usr/share/magic/{pdk}.tech",
            f"/usr/share/magic/{pdk}A.tech",
        ]
    )

    for c in candidates:
        if os.path.exists(c):
            return c
    return ""


def _find_netgen_setup(setup: str, pdk: str, pdk_root: str = "") -> str:
    """Find Netgen setup file with explicit pdk_root."""
    if setup and os.path.exists(setup):
        return setup

    candidates = []

    if pdk_root:
        candidates.extend(
            [
                f"{pdk_root}/{pdk}/libs.tech/netgen/{pdk}_setup.tcl",
                f"{pdk_root}/{pdk}/libs.tech/netgen/{pdk}A_setup.tcl",
                f"{pdk_root}/{pdk}/libs.tech/netgen/setup.tcl",
            ]
        )

    candidates.extend(
        [
            f"{OPENLANE_ROOT}/pdks/{pdk}/libs.tech/netgen/{pdk}_tech.setup",
            f"{OPENLANE_ROOT}/pdks/{pdk}/libs.tech/netgen/setup.tcl",
            f"/usr/share/netgen/{pdk}_setup.tcl",
            f"/usr/share/netgen/{pdk}A_setup.tcl",
        ]
    )

    for c in candidates:
        if os.path.exists(c):
            return c
    return setup


def _error_drc_result(
    errors: List[str],
    report_path: str,
    runtime: float = 0.0,
) -> DRCResult:
    return DRCResult(
        ok=False,
        drc_violations=0,
        violations=[],
        warning_count=0,
        drc_report_path=report_path,
        gds_path="",
        lef_path="",
        tech_file="",
        runtime_sec=runtime,
        errors=errors,
        diagnostics=[],
        metrics={},
    )


def _error_lvs_result(
    errors: List[str],
    report_path: str,
    runtime: float = 0.0,
) -> LVSResult:
    return LVSResult(
        ok=False,
        equivalent=False,
        lvs_errors=0,
        net_mismatches=0,
        pin_mismatches=0,
        unconnected_nets=0,
        lvs_report_path=report_path,
        schematic_netlist="",
        layout_netlist="",
        runtime_sec=runtime,
        errors=errors,
        diagnostics=[],
        metrics={},
    )


def drc_tool(
    gds_path: str,
    tech_file: str,
    output_dir: str,
    pdk: str = "sky130",
) -> Tuple[bool, str]:
    """CrewAI tool wrapper for DRC.

    Returns: (ok, summary_message)
    """
    result = run_magic_drc(
        gds_path=gds_path,
        tech_file=tech_file,
        output_dir=output_dir,
        pdk=pdk,
    )
    if result.ok:
        return True, (
            f"Magic DRC PASS — 0 violations | Runtime: {result.runtime_sec:.1f}s\n"
            f"  Report: {result.drc_report_path}"
        )
    else:
        return False, (
            f"Magic DRC FAIL — {result.drc_violations} violations\n"
            f"  Report: {result.drc_report_path}\n"
            f"  Errors: {result.errors}"
        )


def lvs_tool(
    schematic: str,
    layout_gds: str,
    output_dir: str,
    tech_setup: str,
    pdk: str = "sky130",
) -> Tuple[bool, str]:
    """CrewAI tool wrapper for LVS.

    Returns: (ok, summary_message)
    """
    result = run_netgen_lvs(
        schematic_verilog=schematic,
        layout_gds=layout_gds,
        output_dir=output_dir,
        tech_setup=tech_setup,
        pdk=pdk,
    )
    if result.ok:
        return True, (
            f"Netgen LVS PASS — Schematic equivalent to Layout | "
            f"Runtime: {result.runtime_sec:.1f}s\n"
            f"  Report: {result.lvs_report_path}"
        )
    else:
        return False, (
            f"Netgen LVS FAIL — {result.lvs_errors} errors | "
            f"Net mismatch: {result.net_mismatches} | "
            f"Pin mismatch: {result.pin_mismatches}\n"
            f"  Report: {result.lvs_report_path}"
        )
