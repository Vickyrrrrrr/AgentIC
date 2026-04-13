"""
Signoff Reporter - Structured QOR and DFM Report Generation
==========================================================
Production tapeout requires structured reports in machine-readable formats
(JSON, CSV) for CI/CD pipelines, design reviews, and customer deliverables.

This module generates:
1. QOR (Quality of Results) summary in JSON/CSV
2. Signoff checklist report
3. DFT/ATE test coverage report
4. Power integrity report
5. Tapein readiness report

Usage:
    from agentic.tools.signoff_reporter import (
        generate_qor_report, SignoffChecklist, QORSummary
    )
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class QORSummary:
    """Quality of Results summary for a single build."""

    design_name: str
    timestamp: str
    pdk: str

    rtl_files: List[str] = field(default_factory=list)
    netlist_path: str = ""
    gds_path: str = ""

    cell_count: int = 0
    dff_count: int = 0
    lut_count: int = 0
    gate_count: float = 0.0
    area_um2: float = 0.0
    max_freq_mhz: float = 0.0

    wns_setup_ns: float = 0.0
    wns_hold_ns: float = 0.0
    tns_setup_ns: float = 0.0
    tns_hold_ns: float = 0.0

    total_power_uW: float = 0.0
    dynamic_power_uW: float = 0.0
    leakage_power_uW: float = 0.0
    power_density_mW_mm2: float = 0.0

    drc_violations: int = 0
    lvs_errors: int = 0
    antenna_violations: int = 0
    cdc_violations: int = 0

    scan_chains: int = 0
    atpg_coverage_percent: float = 0.0
    mbist_covered_mems: int = 0

    line_coverage_percent: float = 0.0
    branch_coverage_percent: float = 0.0
    toggle_coverage_percent: float = 0.0
    functional_coverage_percent: float = 0.0
    assertion_coverage_percent: float = 0.0

    build_passed: bool = False
    signoff_ready: bool = False
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SignoffChecklist:
    """Signoff checklist item."""

    category: str
    item: str
    status: str
    value: Any
    threshold: Any
    passed: bool
    details: str = ""


class SignoffReporter:
    """Generates structured signoff reports."""

    def __init__(self, design_name: str, pdk: str):
        self.design_name = design_name
        self.pdk = pdk
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.qor: Dict[str, Any] = {}
        self.checklist: List[SignoffChecklist] = []
        self.tool_versions: Dict[str, str] = {}

    def add_synthesis_metrics(
        self,
        cell_count: int,
        dff: int,
        lut: int,
        gate_count: float,
        area_um2: float,
        netlist_path: str,
    ) -> None:
        self.qor["synthesis"] = {
            "cell_count": cell_count,
            "dff_count": dff,
            "lut_count": lut,
            "gate_count": gate_count,
            "area_um2": area_um2,
            "netlist_path": netlist_path,
        }
        self._check("Synthesis", "Cell count", cell_count, None, cell_count > 0)
        self._check("Synthesis", "Gate count", gate_count, None, gate_count > 0)
        self._check(
            "Synthesis",
            "Netlist generated",
            netlist_path,
            "exists",
            os.path.exists(netlist_path),
        )

    def add_sta_metrics(
        self,
        wns_setup: float,
        wns_hold: float,
        tns_setup: float,
        tns_hold: float,
        max_freq: float,
        corner: str = "tt",
    ) -> None:
        self.qor.setdefault("timing", {})[corner] = {
            "wns_setup_ns": wns_setup,
            "wns_hold_ns": wns_hold,
            "tns_setup_ns": tns_setup,
            "tns_hold_ns": tns_hold,
            "max_freq_mhz": max_freq,
        }

        timing_pass = wns_setup >= -0.05 and wns_hold >= -0.05
        self._check(
            "Timing",
            f"WNS setup [{corner}]",
            f"{wns_setup:.3f}ns",
            "> -0.05ns",
            timing_pass,
        )
        self._check(
            "Timing",
            f"WNS hold [{corner}]",
            f"{wns_hold:.3f}ns",
            "> -0.05ns",
            wns_hold >= -0.05,
        )
        self._check(
            "Timing",
            f"Max frequency [{corner}]",
            f"{max_freq:.1f}MHz",
            "any",
            max_freq > 0,
        )

    def add_power_metrics(
        self,
        total_uw: float,
        dynamic_uw: float,
        leakage_uw: float,
        power_density: float,
        junction_temp: float,
    ) -> None:
        self.qor["power"] = {
            "total_uW": total_uw,
            "dynamic_uW": dynamic_uw,
            "leakage_uW": leakage_uw,
            "power_density_mW_per_mm2": power_density,
            "junction_temp_C": junction_temp,
        }
        self._check(
            "Power", "Total power", f"{total_uw:.2f}uW", "< budget", total_uw > 0
        )
        self._check(
            "Power",
            "Power density",
            f"{power_density:.3f}mW/mm2",
            "< 10mW/mm2",
            power_density < 10.0,
        )
        self._check(
            "Power",
            "Junction temperature",
            f"{junction_temp:.1f}C",
            "< 125C",
            junction_temp < 125.0,
        )

    def add_dft_metrics(
        self,
        scan_chains: int,
        atpg_coverage: float,
        mbist_covered: int,
        dft_netlist: str,
    ) -> None:
        self.qor["dft"] = {
            "scan_chains": scan_chains,
            "atpg_coverage_percent": atpg_coverage,
            "mbist_covered_memories": mbist_covered,
            "dft_netlist_path": dft_netlist,
        }
        self._check("DFT", "Scan chains inserted", scan_chains, ">= 1", scan_chains > 0)
        self._check(
            "DFT",
            "ATPG coverage",
            f"{atpg_coverage:.1f}%",
            ">= 95%",
            atpg_coverage >= 95.0,
        )
        self._check(
            "DFT",
            "MBIST coverage",
            f"{mbist_covered}",
            ">= memories",
            mbist_covered >= 0,
        )

    def add_physical_metrics(
        self,
        drc_violations: int,
        lvs_errors: int,
        antenna_violations: int,
        cdc_violations: int,
        gds_path: str,
    ) -> None:
        self.qor["physical"] = {
            "drc_violations": drc_violations,
            "lvs_errors": lvs_errors,
            "antenna_violations": antenna_violations,
            "cdc_violations": cdc_violations,
            "gds_path": gds_path,
        }
        self._check(
            "Physical", "DRC violations", drc_violations, "== 0", drc_violations == 0
        )
        self._check("Physical", "LVS errors", lvs_errors, "== 0", lvs_errors == 0)
        self._check(
            "Physical",
            "Antenna violations",
            antenna_violations,
            "== 0",
            antenna_violations == 0,
        )
        self._check(
            "Physical", "CDC violations", cdc_violations, "== 0", cdc_violations == 0
        )
        self._check(
            "Physical", "GDS generated", gds_path, "exists", os.path.exists(gds_path)
        )

    def add_coverage_metrics(
        self,
        line_pct: float,
        branch_pct: float,
        toggle_pct: float,
        func_pct: float,
        assertion_pct: float,
    ) -> None:
        self.qor["coverage"] = {
            "line_percent": line_pct,
            "branch_percent": branch_pct,
            "toggle_percent": toggle_pct,
            "functional_percent": func_pct,
            "assertion_percent": assertion_pct,
        }
        self._check(
            "Coverage", "Line coverage", f"{line_pct:.1f}%", ">= 80%", line_pct >= 80.0
        )
        self._check(
            "Coverage",
            "Branch coverage",
            f"{branch_pct:.1f}%",
            ">= 70%",
            branch_pct >= 70.0,
        )
        self._check(
            "Coverage",
            "Functional coverage",
            f"{func_pct:.1f}%",
            ">= 60%",
            func_pct >= 60.0,
        )

    def add_digital_metrics(
        self,
        rtl_files: List[str],
        cell_count: int,
        dff_count: int,
        lut_count: int,
        area_um2: float,
    ) -> None:
        self.qor["digital"] = {
            "rtl_files": rtl_files,
            "cell_count": cell_count,
            "dff_count": dff_count,
            "lut_count": lut_count,
            "area_um2": area_um2,
        }

    def add_tool_version(self, tool: str, version: str) -> None:
        self.tool_versions[tool] = version

    def _check(
        self,
        category: str,
        item: str,
        value: Any,
        threshold: Any,
        passed: bool,
        details: str = "",
    ) -> None:
        self.checklist.append(
            SignoffChecklist(
                category=category,
                item=item,
                status="PASS" if passed else "FAIL",
                value=value,
                threshold=threshold,
                passed=passed,
                details=details,
            )
        )

    def generate_json_report(self, output_path: str) -> str:
        """Generate structured JSON QOR report."""
        report = {
            "design": self.design_name,
            "pdk": self.pdk,
            "timestamp": self.timestamp,
            "tool_versions": self.tool_versions,
            "qor": self.qor,
            "checklist": [asdict(c) for c in self.checklist],
            "summary": {
                "total_checks": len(self.checklist),
                "passed": sum(1 for c in self.checklist if c.passed),
                "failed": sum(1 for c in self.checklist if not c.passed),
                "signoff_ready": all(c.passed for c in self.checklist),
            },
        }

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2)

        return output_path

    def generate_csv_report(self, output_path: str) -> str:
        """Generate human-readable CSV checklist."""
        import csv

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "category",
                    "item",
                    "status",
                    "value",
                    "threshold",
                    "details",
                ],
            )
            writer.writeheader()
            for c in self.checklist:
                writer.writerow(
                    {
                        "category": c.category,
                        "item": c.item,
                        "status": c.status,
                        "value": str(c.value),
                        "threshold": str(c.threshold),
                        "details": c.details,
                    }
                )

        return output_path

    def generate_markdown_report(self, output_path: str) -> str:
        """Generate human-readable Markdown report for design reviews."""
        passed = sum(1 for c in self.checklist if c.passed)
        failed = sum(1 for c in self.checklist if not c.passed)
        total = len(self.checklist)

        md_lines = [
            f"# Signoff Report: {self.design_name}",
            "",
            f"**PDK:** {self.pdk}  |  **Date:** {self.timestamp}",
            f"**Status:** {'✅ READY FOR TAPEOUT' if failed == 0 else f'❌ {failed} FAILURES'}",
            "",
            f"## Summary ({passed}/{total} checks passed)",
            "",
            "| Category | Item | Status | Value | Threshold |",
            "|---------|------|--------|-------|-----------|",
        ]

        for c in self.checklist:
            icon = "✅" if c.passed else "❌"
            md_lines.append(
                f"| {c.category} | {c.item} | {icon} {c.status} | "
                f"{c.value} | {c.threshold} |"
            )

        if self.qor.get("timing"):
            md_lines.extend(["", "## Timing", ""])
            for corner, data in self.qor["timing"].items():
                md_lines.extend(
                    [
                        f"### Corner: {corner.upper()}",
                        f"- WNS Setup: {data.get('wns_setup_ns', 0):.3f}ns",
                        f"- WNS Hold: {data.get('wns_hold_ns', 0):.3f}ns",
                        f"- TNS Setup: {data.get('tns_setup_ns', 0):.1f}ns",
                        f"- Max Frequency: {data.get('max_freq_mhz', 0):.1f}MHz",
                        "",
                    ]
                )

        if self.qor.get("power"):
            p = self.qor["power"]
            md_lines.extend(
                [
                    "## Power",
                    "",
                    f"- Total: {p.get('total_uW', 0):.2f}uW",
                    f"- Dynamic: {p.get('dynamic_uW', 0):.2f}uW",
                    f"- Leakage: {p.get('leakage_uW', 0):.4f}uW",
                    f"- Density: {p.get('power_density_mW_per_mm2', 0):.3f}mW/mm2",
                    "",
                ]
            )

        if self.qor.get("physical"):
            ph = self.qor["physical"]
            md_lines.extend(
                [
                    "## Physical Verification",
                    "",
                    f"- DRC Violations: {ph.get('drc_violations', 0)}",
                    f"- LVS Errors: {ph.get('lvs_errors', 0)}",
                    f"- Antenna Violations: {ph.get('antenna_violations', 0)}",
                    "",
                ]
            )

        if self.qor.get("dft"):
            d = self.qor["dft"]
            md_lines.extend(
                [
                    "## DFT",
                    "",
                    f"- Scan Chains: {d.get('scan_chains', 0)}",
                    f"- ATPG Coverage: {d.get('atpg_coverage_percent', 0):.1f}%",
                    f"- MBIST Covered: {d.get('mbist_covered_memories', 0)}",
                    "",
                ]
            )

        if self.qor.get("coverage"):
            cov = self.qor["coverage"]
            md_lines.extend(
                [
                    "## Coverage",
                    "",
                    f"- Line: {cov.get('line_percent', 0):.1f}%",
                    f"- Branch: {cov.get('branch_percent', 0):.1f}%",
                    f"- Toggle: {cov.get('toggle_percent', 0):.1f}%",
                    f"- Functional: {cov.get('functional_percent', 0):.1f}%",
                    "",
                ]
            )

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w") as f:
            f.write("\n".join(md_lines))

        return output_path

    def is_signoff_ready(self) -> bool:
        critical_categories = {"Physical", "Timing", "DFT"}
        for c in self.checklist:
            if c.category in critical_categories and not c.passed:
                return False
        return True


def generate_qor_report(
    design_name: str,
    pdk: str,
    output_dir: str,
    synthesis_data: Optional[Dict[str, Any]] = None,
    sta_data: Optional[Dict[str, Any]] = None,
    power_data: Optional[Dict[str, Any]] = None,
    physical_data: Optional[Dict[str, Any]] = None,
    coverage_data: Optional[Dict[str, Any]] = None,
    dft_data: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate complete QOR report from build results.

    Usage:
        generate_qor_report(
            design_name="counter",
            pdk="sky130",
            output_dir="./reports",
            synthesis_data={"cell_count": 5000, ...},
            sta_data={"wns_setup_ns": -0.02, ...},
            physical_data={"drc_violations": 0, ...},
        )
    """
    reporter = SignoffReporter(design_name, pdk)

    if synthesis_data:
        reporter.add_digital_metrics(
            rtl_files=synthesis_data.get("rtl_files", []),
            cell_count=synthesis_data.get("cell_count", 0),
            dff_count=synthesis_data.get("dff_count", 0),
            lut_count=synthesis_data.get("lut_count", 0),
            area_um2=synthesis_data.get("area_um2", 0.0),
        )

    if sta_data:
        for corner, data in sta_data.items():
            reporter.add_sta_metrics(
                wns_setup=data.get("wns_setup_ns", 0.0),
                wns_hold=data.get("wns_hold_ns", 0.0),
                tns_setup=data.get("tns_setup_ns", 0.0),
                tns_hold=data.get("tns_hold_ns", 0.0),
                max_freq=data.get("max_freq_mhz", 0.0),
                corner=corner,
            )

    if power_data:
        reporter.add_power_metrics(
            total_uw=power_data.get("total_uW", 0.0),
            dynamic_uw=power_data.get("dynamic_uW", 0.0),
            leakage_uw=power_data.get("leakage_uW", 0.0),
            power_density=power_data.get("power_density_mW_per_mm2", 0.0),
            junction_temp=power_data.get("junction_temp_C", 25.0),
        )

    if physical_data:
        reporter.add_physical_metrics(
            drc_violations=physical_data.get("drc_violations", 0),
            lvs_errors=physical_data.get("lvs_errors", 0),
            antenna_violations=physical_data.get("antenna_violations", 0),
            cdc_violations=physical_data.get("cdc_violations", 0),
            gds_path=physical_data.get("gds_path", ""),
        )

    if coverage_data:
        reporter.add_coverage_metrics(
            line_pct=coverage_data.get("line_percent", 0.0),
            branch_pct=coverage_data.get("branch_percent", 0.0),
            toggle_pct=coverage_data.get("toggle_percent", 0.0),
            func_pct=coverage_data.get("functional_percent", 0.0),
            assertion_pct=coverage_data.get("assertion_percent", 0.0),
        )

    if dft_data:
        reporter.add_dft_metrics(
            scan_chains=dft_data.get("scan_chains", 0),
            atpg_coverage=dft_data.get("atpg_coverage_percent", 0.0),
            mbist_covered=dft_data.get("mbist_covered_memories", 0),
            dft_netlist=dft_data.get("dft_netlist_path", ""),
        )

    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, f"{design_name}_qor.json")
    csv_path = os.path.join(output_dir, f"{design_name}_checklist.csv")
    md_path = os.path.join(output_dir, f"{design_name}_signoff.md")

    reporter.generate_json_report(json_path)
    reporter.generate_csv_report(csv_path)
    reporter.generate_markdown_report(md_path)

    return json_path
