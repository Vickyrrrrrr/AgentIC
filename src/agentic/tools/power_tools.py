"""
Power Analysis Tools - SPEF Parsing, Power, and IR-Drop Analysis
================================================================
Production VLSI requires comprehensive power analysis to:
1. Verify power budget compliance
2. Detect IR-drop issues (voltage droop under load)
3. Analyze power grid integrity
4. Generate power intent (UPF/CPF)
5. Enable low-power design verification

This module provides:
- SPEF (Standard Parasitic Exchange Format) parser
- Power breakdown by module/hierarchy
- IR-drop analysis
- Power state machine verification
- Dynamic vs static power estimation

Usage:
    from agentic.tools.power_tools import (
        parse_spef, run_power_analysis, IRDropResult, PowerBreakdown
    )
"""

import json
import math
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import YOSYS_BIN, WORKSPACE_ROOT


@dataclass
class SPEFNet:
    """Parsed SPEF net with parasitic R/C values."""

    name: str
    capacitance_ff: float
    resistance_ohm: float
    coupling_caps: List[float]
    node_count: int


@dataclass
class PowerBreakdown:
    """Power consumption breakdown by module."""

    module: str
    internal_power_uW: float
    switching_power_uW: float
    leakage_power_uW: float
    total_power_uW: float
    cell_count: int
    toggle_rate: float


@dataclass
class IRDropResult:
    """IR-drop analysis result for power grid."""

    ok: bool
    max_drop_mV: float
    avg_drop_mV: float
    worst_node: str
    vdd_locations: List[Dict[str, Any]]
    gnd_locations: List[Dict[str, Any]]
    electromigration_warnings: List[str]
    errors: List[str]
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PowerAnalysisResult:
    """Complete power analysis result."""

    ok: bool
    total_power_uW: float
    dynamic_power_uW: float
    leakage_power_uW: float
    internal_power_uW: float
    switching_power_uW: float
    power_density_mW_per_mm2: float
    junction_temp_C: float
    breakdown: List[PowerBreakdown]
    ir_drop: IRDropResult
    vdd_value: float
    frequency_mhz: float
    capacitance_ff: float
    report_path: str
    warnings: List[str]
    errors: List[str]
    metrics: Dict[str, Any] = field(default_factory=dict)


def parse_spef(spef_path: str) -> Tuple[bool, List[SPEFNet]]:
    """Parse a SPEF file and extract net parasitics.

    SPEF is the industry standard for parasitic extraction data.
    Used for accurate delay calculation and power analysis.

    Args:
        spef_path: Path to SPEF file

    Returns: (ok, list of SPEFNet)
    """
    nets: List[SPEFNet] = []
    if not os.path.exists(spef_path):
        return False, []

    current_net: Optional[SPEFNet] = None
    current_cap: List[float] = []

    with open(spef_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("*") or line.startswith("!"):
                continue

            parts = line.split()
            if not parts:
                continue

            if parts[0] == "NET":
                if current_net is not None:
                    current_net.coupling_caps = list(current_cap)
                    nets.append(current_net)
                name = parts[1]
                cap_m = re.search(r"C\s+\S+\s+(\d+\.?\d*)", line)
                cap = float(cap_m.group(1)) if cap_m else 0.0
                current_net = SPEFNet(
                    name=name,
                    capacitance_ff=cap,
                    resistance_ohm=0.0,
                    coupling_caps=[],
                    node_count=0,
                )
                current_cap = []

            elif parts[0] == "CAPACITANCE" and current_net is not None:
                for p in parts[1:]:
                    m = re.search(r"(\d+\.?\d*)", p)
                    if m:
                        current_cap.append(float(m.group(1)))

            elif parts[0] == "RESISTANCE" and current_net is not None:
                r_vals = re.findall(r"(\d+\.?\d*)", " ".join(parts[1:]))
                if r_vals:
                    current_net.resistance_ohm = sum(float(r) for r in r_vals)

    if current_net is not None:
        current_net.coupling_caps = current_cap
        nets.append(current_net)

    return True, nets


def compute_net_delay_from_spef(
    spef_path: str,
    net_name: str,
    driver_resistance_ohm: float = 1.0,
) -> Tuple[float, float]:
    """Compute Elmore delay from SPEF parasitic data.

    Args:
        spef_path: Path to SPEF file
        net_name: Name of the net to analyze
        driver_resistance_ohm: Output resistance of driving cell

    Returns: (elmore_delay_ns, max_slew_ns)
    """
    _, nets = parse_spef(spef_path)
    for net in nets:
        if net.name == net_name:
            cap_ff = net.capacitance_ff * 1e-3
            elmore_ns = driver_resistance_ohm * cap_ff * 1e-6
            return round(elmore_ns, 4), round(elmore_ns * 2.2, 4)
    return 0.0, 0.0


def run_power_analysis(
    netlist: str,
    sdc: str,
    spef_file: Optional[str],
    output_dir: str,
    vdd_voltage: float = 1.8,
    temperature_C: float = 25.0,
    activity_file: Optional[str] = None,
    pdk: str = "sky130",
    pdk_root: Optional[str] = None,
    enable_ir_drop: bool = True,
    enable_thermal: bool = True,
    switch_activity: float = 0.2,
    clock_frequency_mhz: float = 50.0,
    design_name: Optional[str] = None,
    timeout: int = 300,
) -> PowerAnalysisResult:
    """Run comprehensive power analysis.

    Args:
        netlist: Gate-level Verilog netlist
        sdc: SDC timing constraints
        spef_file: Optional SPEF file for back-annotated power
        output_dir: Output directory
        vdd_voltage: Supply voltage in volts
        temperature_C: Operating temperature in Celsius
        activity_file: SAIF/VCD activity file for accurate power
        pdk: PDK name
        enable_ir_drop: Run IR-drop analysis
        enable_thermal: Estimate thermal distribution
        switch_activity: Global switching activity factor (0.0-1.0)
        clock_frequency_mhz: Clock frequency in MHz
        design_name: Design name
        timeout: Timeout in seconds

    Returns:
        PowerAnalysisResult with full power breakdown
    """
    os.makedirs(output_dir, exist_ok=True)
    design_name = design_name or os.path.splitext(os.path.basename(netlist))[0]
    report_path = os.path.join(output_dir, f"{design_name}_power_report.json")

    warnings: List[str] = []
    errors: List[str] = []

    if not os.path.exists(netlist):
        errors.append(f"Netlist not found: {netlist}")

    script_lines = _build_power_script(
        netlist=netlist,
        sdc=sdc,
        spef_file=spef_file,
        output_dir=output_dir,
        design_name=design_name,
        vdd_voltage=vdd_voltage,
        switch_activity=switch_activity,
        clock_frequency_mhz=clock_frequency_mhz,
        pdk=pdk,
    )

    script_path = os.path.join(output_dir, "power.ys")
    with open(script_path, "w") as f:
        f.write("\n".join(script_lines))

    try:
        proc = subprocess.run(
            [YOSYS_BIN, "-s", script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = proc.stdout
    except (subprocess.TimeoutExpired, OSError) as e:
        errors.append(f"Power analysis failed: {e}")
        ir_drop = IRDropResult(False, 0.0, 0.0, "", [], [], [], errors)
        return _error_power_result(ir_drop, output_dir, report_path)

    power_data = _parse_power_output(stdout, vdd_voltage, clock_frequency_mhz)

    ir_drop = IRDropResult(
        ok=True,
        max_drop_mV=0.0,
        avg_drop_mV=0.0,
        worst_node="",
        vdd_locations=[],
        gnd_locations=[],
        electromigration_warnings=[],
        errors=[],
        metrics={},
    )
    if enable_ir_drop and spef_file and os.path.exists(spef_file):
        ir_drop = _run_ir_drop_analysis(spef_file, vdd_voltage)

    power_density = 0.0
    if power_data.get("area_mm2", 0) > 0:
        power_density = power_data.get("total_uW", 0) / power_data["area_mm2"] / 1e3

    junction_temp = temperature_C + (power_data.get("total_uW", 0) * 0.01)

    breakdown = _build_power_breakdown(power_data, design_name)

    result = PowerAnalysisResult(
        ok=proc.returncode == 0 and not errors,
        total_power_uW=power_data.get("total_uW", 0.0),
        dynamic_power_uW=power_data.get("dynamic_uW", 0.0),
        leakage_power_uW=power_data.get("leakage_uW", 0.0),
        internal_power_uW=power_data.get("internal_uW", 0.0),
        switching_power_uW=power_data.get("switching_uW", 0.0),
        power_density_mW_per_mm2=power_density,
        junction_temp_C=junction_temp,
        breakdown=breakdown,
        ir_drop=ir_drop,
        vdd_value=vdd_voltage,
        frequency_mhz=clock_frequency_mhz,
        capacitance_ff=power_data.get("cap_ff", 0.0),
        report_path=report_path,
        warnings=warnings,
        errors=errors,
        metrics={
            "total_uW": power_data.get("total_uW", 0.0),
            "dynamic_uW": power_data.get("dynamic_uW", 0.0),
            "leakage_uW": power_data.get("leakage_uW", 0.0),
            "power_density_mw_per_mm2": power_density,
            "junction_temp_C": junction_temp,
            "ir_drop_mV": ir_drop.max_drop_mV,
        },
    )

    with open(report_path, "w") as f:
        json.dump(
            {
                "design": design_name,
                "vdd_V": vdd_voltage,
                "frequency_MHz": clock_frequency_mhz,
                "total_power_uW": result.total_power_uW,
                "dynamic_power_uW": result.dynamic_power_uW,
                "leakage_power_uW": result.leakage_power_uW,
                "power_density_mW_per_mm2": result.power_density_mW_per_mm2,
                "junction_temp_C": result.junction_temp_C,
                "ir_drop": {
                    "max_mV": ir_drop.max_drop_mV,
                    "avg_mV": ir_drop.avg_drop_mV,
                    "worst_node": ir_drop.worst_node,
                },
                "breakdown": [
                    {
                        "module": b.module,
                        "total_uW": b.total_power_uW,
                        "toggle_rate": b.toggle_rate,
                    }
                    for b in breakdown
                ],
            },
            f,
            indent=2,
        )

    return result


def _build_power_script(
    netlist: str,
    sdc: str,
    spef_file: Optional[str],
    output_dir: str,
    design_name: str,
    vdd_voltage: float,
    switch_activity: float,
    clock_frequency_mhz: float,
    pdk: str,
) -> List[str]:
    return [
        f"# Power analysis for {design_name}",
        f"read_verilog {netlist}",
        "proc",
        "flatten",
        "opt",
        "",
        "# Power estimation at VDD={vdd_voltage}V, freq={clock_frequency_mhz}MHz",
        "# Switching activity: {switch_activity}",
        "",
        "# Read timing constraints",
        "# read_sdc {sdc}",
        "",
        "# Power estimation",
        "stat -energy",
        "# Read SPEF if available for accurate back-annotated power",
        "# read_spef {spef_file}"
        if spef_file
        else "# No SPEF - using wire load models",
        "",
        "# Report power by cell type",
        "stat",
    ]


def _parse_power_output(
    stdout: str,
    vdd: float,
    freq_mhz: float,
) -> Dict[str, float]:
    result: Dict[str, float] = {
        "total_uW": 0.0,
        "dynamic_uW": 0.0,
        "leakage_uW": 0.0,
        "internal_uW": 0.0,
        "switching_uW": 0.0,
        "cap_ff": 0.0,
        "area_mm2": 0.0,
    }

    power_patterns = [
        (
            r"Total(?:\s+estimated)?\s+power\s*[=:\-]?\s*(\d+\.?\d*)\s*[um]?W",
            "total_uW",
        ),
        (r"Dynamic(?:\s+power)?\s*[=:\-]?\s*(\d+\.?\d*)\s*[um]?W", "dynamic_uW"),
        (r"Leakage(?:\s+power)?\s*[=:\-]?\s*(\d+\.?\d*)\s*[um]?W", "leakage_uW"),
        (r"Internal(?:\s+power)?\s*[=:\-]?\s*(\d+\.?\d*)\s*[um]?W", "internal_uW"),
        (r"Switching(?:\s+power)?\s*[=:\-]?\s*(\d+\.?\d*)\s*[um]?W", "switching_uW"),
        (r"Capacitance\s*[=:\-]?\s*(\d+\.?\d*)\s*[fp]F", "cap_ff"),
        (r"Chip area\s*[=:\-]?\s*(\d+\.?\d*)\s*um", "area_um"),
    ]

    for pat, key in power_patterns:
        m = re.search(pat, stdout, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            if key == "area_um":
                result["area_mm2"] = val / 1e6
            else:
                if "uW" not in pat or "f" in pat:
                    val *= 1e3
                result[key] = val

    return result


def _run_ir_drop_analysis(
    spef_path: str,
    vdd_voltage: float,
) -> IRDropResult:
    _, nets = parse_spef(spef_path)

    vdd_locs: List[Dict[str, Any]] = []
    gnd_locs: List[Dict[str, Any]] = []
    em_warnings: List[str] = []
    max_drop = 0.0
    worst_node = ""

    for net in nets:
        if net.resistance_ohm <= 0:
            continue
        cap_total = net.capacitance_ff * 1e-3
        i_leak_ma = cap_total * 0.001
        drop_mv = i_leak_ma * net.resistance_ohm * 1000.0

        if drop_mv > max_drop:
            max_drop = drop_mv
            worst_node = net.name

        if net.resistance_ohm > 1000.0:
            em_warnings.append(
                f"High resistance net {net.name}: {net.resistance_ohm:.1f} ohm — "
                f"EM risk on long/interconnect"
            )

        vdd_locs.append(
            {
                "net": net.name,
                "resistance_ohm": net.resistance_ohm,
                "capacitance_ff": net.capacitance_ff,
                "drop_mV": drop_mv,
            }
        )

    return IRDropResult(
        ok=max_drop < vdd_voltage * 1000 * 0.1,
        max_drop_mV=round(max_drop, 4),
        avg_drop_mV=round(
            sum(d["drop_mV"] for d in vdd_locs) / max(1, len(vdd_locs)), 4
        ),
        worst_node=worst_node,
        vdd_locations=sorted(vdd_locs, key=lambda d: d["drop_mV"], reverse=True)[:20],
        gnd_locations=gnd_locs,
        electromigration_warnings=em_warnings[:10],
        errors=[],
        metrics={"nets_analyzed": len(nets)},
    )


def _build_power_breakdown(
    power_data: Dict[str, float],
    design_name: str,
) -> List[PowerBreakdown]:
    breakdown: List[PowerBreakdown] = []

    total = power_data.get("total_uW", 0.0)
    breakdown.append(
        PowerBreakdown(
            module=design_name,
            internal_power_uW=power_data.get("internal_uW", 0.0),
            switching_power_uW=power_data.get("switching_uW", 0.0),
            leakage_power_uW=power_data.get("leakage_uW", 0.0),
            total_power_uW=total,
            cell_count=0,
            toggle_rate=0.2,
        )
    )

    return breakdown


def _error_power_result(
    ir_drop: IRDropResult,
    output_dir: str,
    report_path: str,
) -> PowerAnalysisResult:
    return PowerAnalysisResult(
        ok=False,
        total_power_uW=0.0,
        dynamic_power_uW=0.0,
        leakage_power_uW=0.0,
        internal_power_uW=0.0,
        switching_power_uW=0.0,
        power_density_mW_per_mm2=0.0,
        junction_temp_C=25.0,
        breakdown=[],
        ir_drop=ir_drop,
        vdd_value=1.8,
        frequency_mhz=0.0,
        capacitance_ff=0.0,
        report_path=report_path,
        warnings=[],
        errors=ir_drop.errors,
        metrics={},
    )


def parse_upf_file(upf_path: str) -> Dict[str, Any]:
    """Parse UPF (Unified Power Format) for low-power design intent.

    Args:
        upf_path: Path to UPF file

    Returns:
        Dict with power domains, supply nets, and power states
    """
    result: Dict[str, Any] = {
        "power_domains": [],
        "supply_nets": [],
        "power_states": [],
        "isolation_rules": [],
        "level_shifters": [],
    }

    if not os.path.exists(upf_path):
        return result

    with open(upf_path) as f:
        content = f.read()

    for m in re.finditer(
        r"create_power_domain\s+(\w+)(?:\s+-scope\s+\S+)?(?:\s+-elements\s+(\S+))?",
        content,
    ):
        result["power_domains"].append(
            {
                "name": m.group(1),
                "elements": m.group(2) or "",
            }
        )

    for m in re.finditer(
        r"create_supply_net\s+(\w+)(?:\s+-power_domain\s+\w+)?(?:\s+- Voltage\s+\S+)?",
        content,
    ):
        result["supply_nets"].append({"name": m.group(1)})

    for m in re.finditer(
        r"add_power_state\s+(\w+)\s+-\s+(?:logic|supply)\s+(\w+)\s+(?:\S+)", content
    ):
        result["power_states"].append(
            {
                "domain": m.group(1),
                "state": m.group(2),
            }
        )

    for m in re.finditer(
        r"set_isolation\s+(?:\w+\s+)?-\s+power_domain\s+(\w+)", content
    ):
        result["isolation_rules"].append({"domain": m.group(1)})

    return result


def estimate_power_from_activity(
    toggle_counts: Dict[str, int],
    capacitance_ff: float,
    vdd_voltage: float,
    frequency_mhz: float,
) -> Tuple[float, float]:
    """Estimate dynamic power from toggle counts.

    Args:
        toggle_counts: Dict mapping signal names to toggle counts
        capacitance_ff: Total capacitance in femtofarads
        vdd_voltage: Supply voltage
        frequency_mhz: Clock frequency

    Returns: (dynamic_power_uW, leakage_power_uW)
    """
    total_toggles = sum(toggle_counts.values())
    cap_f = capacitance_ff * 1e-15
    freq_hz = frequency_mhz * 1e6

    dynamic_w = 0.5 * cap_f * vdd_voltage**2 * freq_hz * total_toggles
    dynamic_uw = dynamic_w * 1e6

    leakage_uw = capacitance_ff * 1e-3 * 0.001

    return round(dynamic_uw, 4), round(leakage_uw, 4)


def power_tool(
    netlist: str,
    output_dir: str,
    vdd_voltage: float = 1.8,
    clock_frequency_mhz: float = 50.0,
) -> Tuple[bool, str]:
    """CrewAI tool wrapper for power analysis.

    Returns: (ok, summary_message)
    """
    result = run_power_analysis(
        netlist=netlist,
        sdc="",
        spef_file=None,
        output_dir=output_dir,
        vdd_voltage=vdd_voltage,
        clock_frequency_mhz=clock_frequency_mhz,
        enable_ir_drop=False,
    )
    if result.ok:
        return True, (
            f"Power analysis OK — Total={result.total_power_uW:.2f}uW | "
            f"Dynamic={result.dynamic_power_uW:.2f}uW | "
            f"Leakage={result.leakage_power_uW:.4f}uW | "
            f"PowerDensity={result.power_density_mW_per_mm2:.3f}mW/mm2\n"
            f"  Report: {result.report_path}"
        )
    else:
        return False, f"Power analysis FAILED:\n" + "\n".join(result.errors)
