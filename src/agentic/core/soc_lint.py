"""Structural lint checks for generated SoC RTL.

These checks catch integration mistakes that syntax, DRC, and LVS cannot see:
for example a CPU address bus being wired directly into peripherals instead of
going through a memory-mapped interconnect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class SocLintIssue:
    rule: str
    severity: str
    message: str


def _has_any(patterns: Iterable[str], text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE | re.MULTILINE) for pattern in patterns)


def lint_soc_rtl(rtl_text: str) -> List[SocLintIssue]:
    """Return structural SoC issues in generated RTL."""
    text = rtl_text or ""
    issues: List[SocLintIssue] = []

    has_picorv = bool(re.search(r"\bpicorv32\b", text))
    if not has_picorv:
        return issues

    if re.search(r"assign\s+\w*data_out\s*=\s*mem_wstrb\s*\?\s*mem_wdata\s*:\s*mem_addr\s*;", text):
        issues.append(
            SocLintIssue(
                rule="picorv32_native_bus_hidden",
                severity="error",
                message=(
                    "PicoRV32 wrapper collapses mem_addr/mem_wdata into one data_out signal. "
                    "Expose mem_valid, mem_addr, mem_wdata, mem_wstrb, mem_rdata, and mem_ready."
                ),
            )
        )

    if _has_any(
        [
            r"\.tx_start\s*\(\s*\w+\[[0-9]+\]\s*\)",
            r"\.tx_valid\s*\(\s*\w+\[[0-9]+\]\s*\)",
            r"\.enable\s*\(\s*\w+\[[0-9]+\]\s*\)",
            r"\.kick\s*\(\s*\w+\[[0-9]+\]\s*\)",
            r"\.gpio_dir\s*\(\s*\w+\s*\)",
            r"\.gpio_out\s*\(\s*\w+\s*\)",
        ],
        text,
    ) and not _has_any(
        [
            r"\bcase\s*\([^)]*addr",
            r"\bcase\s*\([^)]*offset",
            r"PERIPH_BASE",
            r"REG_GPIO",
            r"mem_wstrb",
        ],
        text,
    ):
        issues.append(
            SocLintIssue(
                rule="cpu_bus_broadcast_to_peripherals",
                severity="error",
                message=(
                    "CPU output bits appear to drive peripheral controls directly. "
                    "Use an address decoder and memory-mapped registers."
                ),
            )
        )

    if re.search(r"\.mem_wstrb\s*\(\s*mem_wstrb\s*\)", text) and not _has_any(
        [r"\.mem_wstrb\s*\([^)]*cpu", r"output\s+wire\s+\[3:0\]\s+mem_wstrb"],
        text,
    ):
        issues.append(
            SocLintIssue(
                rule="mem_wstrb_not_exported",
                severity="error",
                message="PicoRV32 mem_wstrb is internal but not exported to the SoC interconnect.",
            )
        )

    if re.search(r"Not\s+used\s+by\s+PicoRV32", text, re.IGNORECASE):
        issues.append(
            SocLintIssue(
                rule="dead_instruction_input",
                severity="error",
                message="Instruction input is documented as unused; add a boot ROM or real loader path.",
            )
        )

    if re.search(r"sky130_sram_.*u_sram_macro", text) and not _has_any(
        [r"boot_rom", r"BOOT_ROM", r"ROM_BASE", r"jtag", r"loader", r"flash"],
        text,
    ):
        issues.append(
            SocLintIssue(
                rule="volatile_sram_without_boot_path",
                severity="error",
                message="Volatile SRAM is present without a boot ROM or firmware loader path.",
            )
        )

    return issues
