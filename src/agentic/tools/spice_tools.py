"""
ngspice post-layout simulation helpers.

The wrapper writes a deck, runs ngspice in batch mode, and extracts common
.measure values into structured metrics for the orchestrator and reports.
"""

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..config import NGSPICE_BIN


@dataclass
class NgspiceResult:
    ok: bool
    deck_path: str
    log_path: str
    raw_path: str
    runtime_sec: float
    measurements: Dict[str, float]
    errors: List[str]
    stdout: str = ""
    stderr: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)


def run_ngspice(
    spice_deck: str,
    output_dir: str,
    deck_name: str = "sim.sp",
    timeout: int = 900,
) -> Dict[str, Any]:
    """Run ngspice in batch mode and parse timing/power measurements."""
    os.makedirs(output_dir, exist_ok=True)

    deck_is_path = "\n" not in spice_deck and len(spice_deck) < 4096 and os.path.exists(spice_deck)
    if deck_is_path:
        deck_path = os.path.abspath(spice_deck)
        deck_stem = os.path.splitext(os.path.basename(deck_path))[0]
    else:
        deck_path = os.path.join(output_dir, deck_name)
        deck_stem = os.path.splitext(deck_name)[0]
        with open(deck_path, "w") as f:
            f.write(spice_deck)

    log_path = os.path.join(output_dir, f"{deck_stem}.log")
    raw_path = os.path.join(output_dir, "sim.raw")

    start = time.time()
    try:
        proc = subprocess.run(
            [NGSPICE_BIN, "-b", "-o", log_path, deck_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=output_dir,
        )
        runtime = time.time() - start
    except subprocess.TimeoutExpired:
        return _result_to_dict(
            NgspiceResult(
                ok=False,
                deck_path=deck_path,
                log_path=log_path,
                raw_path=raw_path,
                runtime_sec=time.time() - start,
                measurements={},
                errors=["ngspice simulation timed out"],
            )
        )
    except OSError:
        return _result_to_dict(
            NgspiceResult(
                ok=False,
                deck_path=deck_path,
                log_path=log_path,
                raw_path=raw_path,
                runtime_sec=0.0,
                measurements={},
                errors=[
                    "ngspice binary not found. Install ngspice or set NGSPICE_BIN env var."
                ],
            )
        )

    log_text = ""
    if os.path.exists(log_path):
        with open(log_path, errors="replace") as f:
            log_text = f.read()

    combined = "\n".join(part for part in [proc.stdout, proc.stderr, log_text] if part)
    measurements = parse_ngspice_measurements(combined)
    errors = _parse_ngspice_errors(combined)
    ok = proc.returncode == 0 and not errors

    return _result_to_dict(
        NgspiceResult(
            ok=ok,
            deck_path=deck_path,
            log_path=log_path,
            raw_path=raw_path,
            runtime_sec=runtime,
            measurements=measurements,
            errors=errors,
            stdout=proc.stdout,
            stderr=proc.stderr,
            metrics={
                "returncode": proc.returncode,
                "runtime_sec": runtime,
                "measurement_count": len(measurements),
                **_named_metric_aliases(measurements),
            },
        )
    )


def parse_ngspice_measurements(output: str) -> Dict[str, float]:
    """Parse .measure output such as `delay = 1.23e-09` from ngspice logs."""
    measurements: Dict[str, float] = {}
    number = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    patterns = [
        re.compile(rf"^\s*([A-Za-z_][\w.]*)\s*=\s*{number}\b", re.MULTILINE),
        re.compile(rf"^\s*([A-Za-z_][\w.]*)\s*:\s*{number}\b", re.MULTILINE),
    ]

    for pattern in patterns:
        for match in pattern.finditer(output):
            try:
                measurements[match.group(1).lower()] = float(match.group(2))
            except ValueError:
                continue

    return measurements


def build_basic_post_layout_deck(
    extracted_spice_path: str,
    design_name: str,
    supply_v: float = 1.8,
    sim_time_ns: float = 20.0,
    clock_period_ns: float = 10.0,
) -> str:
    """Create a conservative post-layout deck around an extracted netlist."""
    escaped_path = extracted_spice_path.replace("\\", "\\\\")
    return f"""* AgentIC post-layout SPICE deck for {design_name}
.include "{escaped_path}"

.param VDD={supply_v}
.param CLKPER={clock_period_ns}n

* Common supply aliases used by open PDK digital cells.
Vvdd vdd 0 DC {{VDD}}
Vvccd1 vccd1 0 DC {{VDD}}
Vvpwr VPWR 0 DC {{VDD}}

.op
.tran 10p {sim_time_ns}n
.measure tran peak_vccd1 MAX v(vccd1)
.control
set noaskquit
run
write sim.raw
quit
.endc
.end
"""


def _parse_ngspice_errors(output: str) -> List[str]:
    errors: List[str] = []
    for line in output.splitlines():
        lower = line.lower()
        if "warning" in lower:
            continue
        if any(token in lower for token in ["error", "fatal", "failed", "singular matrix"]):
            errors.append(line.strip())
    return errors[:20]


def _named_metric_aliases(measurements: Dict[str, float]) -> Dict[str, float]:
    aliases: Dict[str, float] = {}
    alias_map = {
        "rise_time": ("rise_time", "risetime", "trise", "tr"),
        "fall_time": ("fall_time", "falltime", "tfall", "tf"),
        "delay": ("delay", "prop_delay", "tpd", "tdelay"),
        "peak_power": ("peak_power", "ppeak", "max_power"),
    }
    for canonical, names in alias_map.items():
        for name in names:
            if name in measurements:
                aliases[canonical] = measurements[name]
                break
    return aliases


def _result_to_dict(result: NgspiceResult) -> Dict[str, Any]:
    return {
        "ok": result.ok,
        "deck_path": result.deck_path,
        "log_path": result.log_path,
        "raw_path": result.raw_path,
        "runtime_sec": result.runtime_sec,
        "measurements": result.measurements,
        "errors": result.errors,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "metrics": result.metrics,
    }
