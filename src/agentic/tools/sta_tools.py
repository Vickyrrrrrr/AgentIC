"""
Static Timing Analysis Tools - OpenSTA Integration
==================================================
Provides standalone pre-route and post-route STA using OpenSTA.

Production VLSI tapeout requires MULTI-CORNER multi-mode timing analysis.
OpenSTA is the open-source golden reference for signoff-quality STA.

Multi-corner analysis covers:
  - SS (slow-slow): worst setup, max delay at low voltage / high temp
  - TT (typical-typical): nominal corner
  - FF (fast-fast): worst hold, min delay at high voltage / low temp

Multi-mode analysis covers:
  - Functional mode (normal operation)
  - Test mode (scan shift, launch/capture)
  - Sleep mode (retention, low-power)

Usage:
    from agentic.tools.sta_tools import run_opensta, run_multi_corner_sta

    result = run_opensta(
        netlist="gate_level.v",
        sdc="design.sdc",
        libs=["sky130_fd_sc_hd__tt.lib", "sky130_fd_sc_hd__ss.lib"],
        output_dir="./sta",
        corner="tt",
    )
"""

import os
import re
import subprocess
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import WORKSPACE_ROOT


OPENSTA_BIN = os.environ.get("OPENSTA_BIN", "sta")


@dataclass
class STAReport:
    """Structured STA result for a single corner."""

    ok: bool
    corner: str
    mode: str
    wns_setup: float
    wns_hold: float
    tns_setup: float
    tns_hold: float
    max_freq_mhz: float
    report_path: str
    critical_paths: List[Dict[str, Any]]
    clock_tree_report: Dict[str, float]
    warnings: List[str]
    errors: List[str]
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MultiCornerSTAResult:
    """Aggregated multi-corner STA result."""

    design_name: str
    corners: Dict[str, STAReport]
    worst_wns_setup: float
    worst_wns_hold: float
    worst_tns_setup: float
    worst_tns_hold: float
    max_freq_mhz: float
    all_corners_pass: bool
    summary_path: str


def run_opensta(
    netlist: str,
    sdc: str,
    lib_files: List[str],
    output_dir: str,
    corner: str = "tt",
    mode: str = "func",
    top_module: Optional[str] = None,
    pdk: str = "sky130",
    enable_corners: bool = False,
    min_period_ns: float = 0.0,
    max_transition_ns: float = 0.5,
    max_capacitance_ff: float = 10.0,
    report_paths: int = 10,
    report_unconstrained: bool = True,
    enable_pre_cts: bool = True,
    enable_post_cts: bool = True,
    enable_route: bool = True,
    opensta_script_extra: Optional[str] = None,
    timeout: int = 300,
) -> STAReport:
    """Run OpenSTA on a gate-level netlist.

    Args:
        netlist: Path to gate-level Verilog netlist
        sdc: Path to SDC timing constraints file
        lib_files: List of Liberty timing library files (.lib)
        corner: Corner name (tt, ss, ff, etc.)
        mode: Mode name (func, test, scan, etc.)
        output_dir: Output directory for reports
        top_module: Top-level module name
        pdk: PDK name
        enable_corners: Enable corner-specific analysis
        min_period_ns: Minimum clock period constraint in ns
        max_transition_ns: Max transition time in ns
        max_capacitance_ff: Max capacitance in femtofarads
        report_paths: Number of critical paths to report
        report_unconstrained: Report unconstrained paths
        enable_pre_cts: Run pre-clock-tree analysis
        enable_post_cts: Run post-clock-tree analysis
        opensta_script_extra: Additional OpenSTA tcl commands
        timeout: Timeout in seconds

    Returns:
        STAReport with timing metrics and critical paths
    """
    os.makedirs(output_dir, exist_ok=True)
    design_name = top_module or os.path.splitext(os.path.basename(netlist))[0]

    rpt_path = os.path.join(output_dir, f"{design_name}_{corner}_{mode}.rpt")
    slack_path = os.path.join(output_dir, f"{design_name}_{corner}_{mode}_slack.txt")

    warnings: List[str] = []
    errors: List[str] = []

    for f in [netlist, sdc]:
        if not os.path.exists(f):
            errors.append(f"File not found: {f}")

    for lib in lib_files:
        if not os.path.exists(lib):
            errors.append(f"Liberty file not found: {lib}")

    if errors:
        return _error_sta_report(errors, corner, mode, rpt_path)

    tcl_lines = _build_opensta_tcl(
        netlist=netlist,
        sdc=sdc,
        lib_files=lib_files,
        top_module=top_module or design_name,
        corner=corner,
        mode=mode,
        rpt_path=rpt_path,
        slack_path=slack_path,
        min_period_ns=min_period_ns,
        max_transition_ns=max_transition_ns,
        max_capacitance_ff=max_capacitance_ff,
        report_paths=report_paths,
        report_unconstrained=report_unconstrained,
        enable_pre_cts=enable_pre_cts,
        enable_post_cts=enable_post_cts,
        enable_route=enable_route,
        extra=opensta_script_extra or "",
    )

    tcl_path = os.path.join(output_dir, f"{design_name}_{corner}_{mode}.tcl")
    with open(tcl_path, "w") as f:
        f.write("\n".join(tcl_lines))

    try:
        proc = subprocess.run(
            [OPENSTA_BIN, "-exit", tcl_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired:
        errors.append(f"OpenSTA timed out after {timeout}s")
        return _error_sta_report(errors, corner, mode, rpt_path)
    except OSError:
        errors.append(
            "OpenSTA binary not found. Install OpenSTA or set OPENSTA_BIN env var."
        )
        return _error_sta_report(errors, corner, mode, rpt_path)

    with open(rpt_path, "a") as f:
        f.write(f"\n{'=' * 60}\nCorner: {corner} | Mode: {mode}\n")
        f.write(f"Return code: {proc.returncode}\n")
        f.write(f"\nSTDOUT:\n{stdout}\n")
        if stderr:
            f.write(f"\nSTDERR:\n{stderr}\n")

    wns_setup = _parse_wns(stdout, mode="setup")
    wns_hold = _parse_wns(stdout, mode="hold")
    tns_setup = _parse_tns(stdout, mode="setup")
    tns_hold = _parse_tns(stdout, mode="hold")
    max_freq = _freq_from_wns(wns_setup, min_period_ns)

    paths = _parse_critical_paths(stdout, limit=report_paths)
    cts_metrics = _parse_cts_report(stdout)
    unconst = _count_unconstrained(stdout)

    for line in (stdout + stderr).splitlines():
        if re.search(r"warning", line, re.IGNORECASE):
            warnings.append(line.strip())

    ok = wns_setup >= -0.05 and wns_hold >= -0.05 and proc.returncode == 0

    return STAReport(
        ok=ok,
        corner=corner,
        mode=mode,
        wns_setup=wns_setup,
        wns_hold=wns_hold,
        tns_setup=tns_setup,
        tns_hold=tns_hold,
        max_freq_mhz=max_freq,
        report_path=rpt_path,
        critical_paths=paths,
        clock_tree_report=cts_metrics,
        warnings=warnings,
        errors=errors,
        metrics={
            "wns_setup": wns_setup,
            "wns_hold": wns_hold,
            "tns_setup": tns_setup,
            "tns_hold": tns_hold,
            "max_freq_mhz": max_freq,
            "unconstrained_paths": unconst,
            "corner": corner,
            "mode": mode,
        },
    )


def run_multi_corner_sta(
    netlist: str,
    sdc: str,
    lib_files: List[str],
    output_dir: str,
    corners: Optional[List[str]] = None,
    modes: Optional[List[str]] = None,
    top_module: Optional[str] = None,
    min_period_ns: float = 10.0,
    pdk: str = "sky130",
    opensta_script_extra: Optional[str] = None,
    timeout_per_corner: int = 300,
) -> MultiCornerSTAResult:
    """Run multi-corner multi-mode STA across all specified corners and modes.

    This is the production-grade signoff flow. Every corner must pass
    timing before tapeout.

    Args:
        netlist: Gate-level Verilog netlist
        sdc: SDC constraints file
        lib_files: Liberty library files (all corners)
        corners: List of corners to analyze (default: [ss, tt, ff])
        modes: List of modes (default: [func, test])
        output_dir: Output directory
        top_module: Top-level module name
        min_period_ns: Minimum clock period constraint
        pdk: PDK name
        opensta_script_extra: Extra OpenSTA TCL commands
        timeout_per_corner: Timeout per corner in seconds

    Returns:
        MultiCornerSTAResult with aggregated metrics from all corners
    """
    corners = corners or ["ss", "tt", "ff"]
    modes = modes or ["func", "test"]

    corner_lib_map = _map_libs_to_corners(lib_files, corners)
    design_name = top_module or os.path.splitext(os.path.basename(netlist))[0]

    corner_reports: Dict[str, STAReport] = {}
    worst_wns_setup = 0.0
    worst_wns_hold = 0.0
    worst_tns_setup = 0.0
    worst_tns_hold = 0.0
    max_freq = 0.0

    for corner in corners:
        libs_for_corner = corner_lib_map.get(corner, lib_files)
        if not libs_for_corner:
            continue

        for mode in modes:
            report = run_opensta(
                netlist=netlist,
                sdc=sdc,
                lib_files=libs_for_corner,
                corner=corner,
                mode=mode,
                output_dir=output_dir,
                top_module=top_module,
                pdk=pdk,
                min_period_ns=min_period_ns,
                opensta_script_extra=opensta_script_extra,
                timeout=timeout_per_corner,
            )
            key = f"{corner}_{mode}"
            corner_reports[key] = report

            if report.wns_setup < worst_wns_setup:
                worst_wns_setup = report.wns_setup
            if report.wns_hold < worst_wns_hold:
                worst_wns_hold = report.wns_hold
            if report.tns_setup < worst_tns_setup:
                worst_tns_setup = report.tns_setup
            if report.tns_hold < worst_tns_hold:
                worst_tns_hold = report.tns_hold
            if report.max_freq_mhz > max_freq:
                max_freq = report.max_freq_mhz

    all_pass = all(r.ok for r in corner_reports.values())

    summary_path = os.path.join(output_dir, f"{design_name}_multi_corner_summary.json")
    _write_multi_corner_summary(
        summary_path,
        design_name,
        corner_reports,
        worst_wns_setup,
        worst_wns_hold,
        worst_tns_setup,
        worst_tns_hold,
        max_freq,
        all_pass,
    )

    return MultiCornerSTAResult(
        design_name=design_name,
        corners=corner_reports,
        worst_wns_setup=worst_wns_setup,
        worst_wns_hold=worst_wns_hold,
        worst_tns_setup=worst_tns_setup,
        worst_tns_hold=worst_tns_hold,
        max_freq_mhz=max_freq,
        all_corners_pass=all_pass,
        summary_path=summary_path,
    )


def _build_opensta_tcl(
    netlist: str,
    sdc: str,
    lib_files: List[str],
    top_module: str,
    corner: str,
    mode: str,
    rpt_path: str,
    slack_path: str,
    min_period_ns: float,
    max_transition_ns: float,
    max_capacitance_ff: float,
    report_paths: int,
    report_unconstrained: bool,
    enable_pre_cts: bool,
    enable_post_cts: bool,
    enable_route: bool,
    extra: str,
) -> List[str]:
    tcl: List[str] = []

    tcl.append(f"# OpenSTA script for {top_module}")
    tcl.append(f"# Corner: {corner} | Mode: {mode}")
    tcl.append("")

    for lib in lib_files:
        tcl.append(f"read_liberty {lib}")

    tcl.extend(
        [
            f"read_verilog {netlist}",
            f"link_design {top_module}",
            f"read_sdc {sdc}",
            "",
            "# Disable specific timing arcs for test mode",
            "# set_disable_timing ... (add as needed)",
            "",
            "# Report settings",
            f"set max_transition {max_transition_ns}",
            f"set max_capacitance {max_capacitance_ff}",
            "",
        ]
    )

    if enable_pre_cts:
        tcl.extend(
            [
                "# Pre-CTS analysis (ideal clock network)",
                "set clock_uncertainty 0.5 [all_clocks]",
                "set_load 0.1 [all_outputs]",
                "report_checks -path_delay max -fields {net slack cap trans fanout} > {rpt_path}",
                "report_checks -path_delay min -fields {net slack cap} >> {rpt_path}",
            ]
        )

    if enable_post_cts:
        tcl.extend(
            [
                "# Post-CTS analysis (real clock network)",
                "report_checks -path_delay max -fields {net slack cap trans fanout} >> {rpt_path}",
                "report_checks -path_delay min -fields {net slack cap} >> {rpt_path}",
            ]
        )

    tcl.extend(
        [
            "# Critical path and slack reports",
            f"report_worst_slack -max > {slack_path}",
            f"report_worst_slack -min >> {slack_path}",
            f"report_timing -max -max_paths {report_paths} >> {rpt_path}",
            f"report_timing -min -max_paths {report_paths} >> {rpt_path}",
        ]
    )

    if report_unconstrained:
        tcl.append(f"report_unconstrained >> {rpt_path}")

    tcl.extend(
        [
            "# Clock tree report",
            "report_clock_tree -verbose >> {rpt_path}",
            "# Power report",
            "report_power >> {rpt_path}",
            "# Metrics summary",
            'echo "WNS_SETUP: $wns_setup" >> {rpt_path}',
            'echo "WNS_HOLD: $wns_hold" >> {rpt_path}',
            'echo "TNS_SETUP: $tns_setup" >> {rpt_path}',
            'echo "TNS_HOLD: $tns_hold" >> {rpt_path}',
        ]
    )

    if extra:
        tcl.append(f"\n# Extra user commands\n{extra}")

    return tcl


def _map_libs_to_corners(
    lib_files: List[str], corners: List[str]
) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = {c: [] for c in corners}
    for lib in lib_files:
        lib_lower = os.path.basename(lib).lower()
        for corner in corners:
            if corner in lib_lower:
                mapping[corner].append(lib)
                break
    for corner in corners:
        if not mapping[corner]:
            mapping[corner] = lib_files[:1]
    return mapping


def _parse_wns(output: str, mode: str = "setup") -> float:
    patterns = [
        rf"(?:wns|worst slack|slack)\s*[=\-:]?\s*([+-]?\d+\.?\d*)",
        rf"Setup (?:wns|worst slack)\s*([+-]?\d+\.?\d*)",
        rf"{mode.upper()} slack\s+([+-]?\d+\.?\d*)",
        rf"design_rule.*?slack\s+([+-]?\d+\.?\d*)",
    ]
    values: List[float] = []
    for pat in patterns:
        for m in re.finditer(pat, output, re.IGNORECASE):
            try:
                values.append(float(m.group(1)))
            except ValueError:
                pass
    if values:
        return min(values)
    return 0.0


def _parse_tns(output: str, mode: str = "setup") -> float:
    patterns = [
        rf"(?:tns|total negative slack)\s*[=\-:]?\s*([+-]?\d+\.?\d*)",
        rf"Setup total_negative_slack\s*([+-]?\d+\.?\d*)",
    ]
    for pat in patterns:
        m = re.search(pat, output, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return 0.0


def _parse_critical_paths(output: str, limit: int = 10) -> List[Dict[str, Any]]:
    paths: List[Dict[str, Any]] = []
    path_blocks = re.split(r"(?=Path \d+:)", output)
    for block in path_blocks[:limit]:
        if not block.strip():
            continue
        path: Dict[str, Any] = {"segments": []}
        slack_m = re.search(r"slack\s+([+-]?\d+\.?\d*)", block, re.IGNORECASE)
        delay_m = re.search(
            r"(?:total|endpoint)\s+(?:delay|slack)\s+([+-]?\d+\.?\d*)",
            block,
            re.IGNORECASE,
        )
        from_m = re.search(r"Startpoint:\s*(\S+)", block)
        to_m = re.search(r"Endpoint:\s*(\S+)", block)
        if slack_m:
            path["slack_ns"] = float(slack_m.group(1))
        if delay_m:
            path["delay_ns"] = float(delay_m.group(1))
        if from_m:
            path["from"] = from_m.group(1)
        if to_m:
            path["to"] = to_m.group(1)
        paths.append(path)
    return paths


def _parse_cts_report(output: str) -> Dict[str, float]:
    metrics: Dict[str, float] = {}
    skew_m = re.search(
        r"(?:clock|clk)\s+skew\s+([+-]?\d+\.?\d*)", output, re.IGNORECASE
    )
    if skew_m:
        metrics["clock_skew_ns"] = float(skew_m.group(1))
    insertion_m = re.search(
        r"insertion delay\s+([+-]?\d+\.?\d*)", output, re.IGNORECASE
    )
    if insertion_m:
        metrics["insertion_delay_ns"] = float(insertion_m.group(1))
    return metrics


def _count_unconstrained(output: str) -> int:
    m = re.search(
        r"(\d+)\s+(?:unconstrained|unclocks|no constraint)", output, re.IGNORECASE
    )
    return int(m.group(1)) if m else 0


def _freq_from_wns(wns: float, period_ns: float) -> float:
    if period_ns <= 0:
        return 0.0
    effective = period_ns + wns
    if effective <= 0:
        return 0.0
    return round(1000.0 / effective, 2)


def _error_sta_report(
    errors: List[str], corner: str, mode: str, rpt_path: str
) -> STAReport:
    return STAReport(
        ok=False,
        corner=corner,
        mode=mode,
        wns_setup=0.0,
        wns_hold=0.0,
        tns_setup=0.0,
        tns_hold=0.0,
        max_freq_mhz=0.0,
        report_path=rpt_path,
        critical_paths=[],
        clock_tree_report={},
        warnings=[],
        errors=errors,
        metrics={},
    )


def _write_multi_corner_summary(
    path: str,
    design_name: str,
    corner_reports: Dict[str, STAReport],
    worst_wns_setup: float,
    worst_wns_hold: float,
    worst_tns_setup: float,
    worst_tns_hold: float,
    max_freq: float,
    all_pass: bool,
) -> None:
    summary = {
        "design": design_name,
        "timestamp": str(__import__("datetime").datetime.now()),
        "all_corners_pass": all_pass,
        "worst_wns_setup_ns": worst_wns_setup,
        "worst_wns_hold_ns": worst_wns_hold,
        "worst_tns_setup_ns": worst_tns_setup,
        "worst_tns_hold_ns": worst_tns_hold,
        "max_freq_mhz": max_freq,
        "corners": {
            key: {
                "ok": r.ok,
                "wns_setup_ns": r.wns_setup,
                "wns_hold_ns": r.wns_hold,
                "tns_setup_ns": r.tns_setup,
                "tns_hold_ns": r.tns_hold,
                "max_freq_mhz": r.max_freq_mhz,
                "critical_path_count": len(r.critical_paths),
                "warnings": len(r.warnings),
                "errors": r.errors,
            }
            for key, r in corner_reports.items()
        },
    }
    with open(path, "w") as f:
        json.dump(summary, f, indent=2)


def sta_tool(
    netlist: str,
    sdc: str,
    lib_files: List[str],
    corner: str = "tt",
    output_dir: str = "./sta",
) -> Tuple[bool, str]:
    """CrewAI tool wrapper for OpenSTA.

    Returns: (ok, summary_message)
    """
    result = run_opensta(
        netlist=netlist,
        sdc=sdc,
        lib_files=lib_files,
        corner=corner,
        output_dir=output_dir,
    )
    if result.ok:
        return True, (
            f"STA PASS [{corner}] — WNS={result.wns_setup:.3f}ns | "
            f"TNS={result.tns_setup:.1f}ns | MaxFreq={result.max_freq_mhz:.1f}MHz\n"
            f"  Report: {result.report_path}"
        )
    else:
        return False, (
            f"STA FAIL [{corner}] — WNS={result.wns_setup:.3f}ns | "
            f"TNS={result.tns_setup:.1f}ns\n"
            f"  Errors: {result.errors}"
        )


def parse_sdc_file(sdc_path: str) -> Dict[str, Any]:
    """Parse SDC file and extract timing constraints.

    Args:
        sdc_path: Path to SDC file

    Returns:
        Dict with clock definitions, input/output delays, false paths
    """
    result: Dict[str, Any] = {
        "clocks": [],
        "input_delays": [],
        "output_delays": [],
        "false_paths": [],
        "multicycle_paths": [],
        "max_transition_ns": 0.5,
        "operating_conditions": None,
    }

    if not os.path.exists(sdc_path):
        return result

    with open(sdc_path) as f:
        content = f.read()

    clock_pattern = re.compile(
        r"create_clock\s+(?:\[NAME\s+\w+\]\s+)?-period\s+([0-9.]+)"
        r"(?:\s+\[get_ports\s+(\w+)\])?"
        r"(?:\s+\[get_pins\s+(\S+)\])?"
        r"(?:\s+-name\s+(\w+))?",
        re.IGNORECASE,
    )
    for m in clock_pattern.finditer(content):
        result["clocks"].append(
            {
                "period_ns": float(m.group(1)),
                "port": m.group(2) or "",
                "pin": m.group(3) or "",
                "name": m.group(4) or m.group(2) or "",
            }
        )

    for m in re.finditer(r"set_input_delay\s+([0-9.]+)\s+(.*)", content, re.IGNORECASE):
        result["input_delays"].append(
            {"delay_ns": float(m.group(1)), "spec": m.group(2)}
        )

    for m in re.finditer(
        r"set_output_delay\s+([0-9.]+)\s+(.*)", content, re.IGNORECASE
    ):
        result["output_delays"].append(
            {"delay_ns": float(m.group(1)), "spec": m.group(2)}
        )

    for m in re.finditer(
        r"set_false_path\s+(-from\s+\S+\s+)?(-to\s+\S+\s+)?(.*)", content, re.IGNORECASE
    ):
        result["false_paths"].append(
            {"from": m.group(1) or "", "to": m.group(2) or "", "extra": m.group(3)}
        )

    for m in re.finditer(r"set_multicycle_path\s+(\d+)\s+(.*)", content, re.IGNORECASE):
        result["multicycle_paths"].append(
            {"cycles": int(m.group(1)), "spec": m.group(2)}
        )

    oc_m = re.search(r"set_operating_conditions\s+(\S+)", content, re.IGNORECASE)
    if oc_m:
        result["operating_conditions"] = oc_m.group(1)

    return result
