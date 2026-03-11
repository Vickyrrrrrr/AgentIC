#!/usr/bin/env python3
#!/usr/bin/env python3
"""
AgentIC Benchmark Runner v2
============================
Runs 10 chip designs through the AgentIC pipeline and produces
a detailed report of real pass/fail rates, stage failures, timing,
and artifact recovery.

Usage (from AgentIC root directory):
    python3 benchmark/run_benchmark.py
    python3 benchmark/run_benchmark.py --skip-openlane
    python3 benchmark/run_benchmark.py --full-signoff
    python3 benchmark/run_benchmark.py --design uart_tx
    python3 benchmark/run_benchmark.py --pdk gf180
"""

import os
import re
import sys
import json
import time
import argparse
import datetime
import subprocess
from pathlib import Path

# ─────────────────────────────────────────────────────────────
# 10 TEST DESIGNS — simple to complex
# ─────────────────────────────────────────────────────────────
TEST_DESIGNS = [
    {
        "id": "counter8",
        "complexity": "Simple",
        "desc": (
            "8-bit synchronous up-counter with active-high synchronous reset and "
            "active-high enable. On every rising clock edge, if reset is high the "
            "counter clears to zero. If enable is high and reset is low, the counter "
            "increments by one. When it reaches 255 it wraps to zero. Output the 8-bit count."
        ),
    },
    {
        "id": "uart_tx",
        "complexity": "Simple",
        "desc": (
            "UART transmitter at 115200 baud, 8N1 format. Accepts parallel 8-bit data "
            "and a start signal. Outputs a serial TX line. 50 MHz system clock. "
            "Signals transmission complete via a done flag. Idle state is logic high."
        ),
    },
    {
        "id": "pwm_gen",
        "complexity": "Simple",
        "desc": (
            "PWM generator with a 16-bit period register and 16-bit duty cycle register, "
            "both writable via a simple register interface with address and write-enable. "
            "Outputs a single PWM signal. 50 MHz clock. Edge-aligned mode."
        ),
    },
    {
        "id": "spi_master",
        "complexity": "Simple",
        "desc": (
            "SPI master controller, mode 0 only (CPOL=0 CPHA=0). 8-bit transfers. "
            "Generates SCLK, MOSI, CS. Accepts MISO. Clock divider to set SPI speed "
            "from 50 MHz system clock. Busy and done status signals."
        ),
    },
    {
        "id": "sync_fifo",
        "complexity": "Simple",
        "desc": (
            "Synchronous FIFO, 8-bit data width, 16-entry depth, single clock domain. "
            "Push and pop with full and empty flags. Almost-full flag when 2 or fewer "
            "slots remain. Almost-empty flag when 2 or fewer entries stored."
        ),
    },
    {
        "id": "alu8",
        "complexity": "Medium",
        "desc": (
            "8-bit ALU with 4-bit opcode selecting: ADD, SUB, AND, OR, XOR, NOT, "
            "left shift by 1, right shift by 1, increment, decrement. Outputs 8-bit "
            "result and 4-bit flags: zero, carry, overflow, negative. Fully combinational."
        ),
    },
    {
        "id": "i2c_master",
        "complexity": "Medium",
        "desc": (
            "I2C master controller, standard mode 100 kHz. Generates SCL and SDA with "
            "open-drain outputs. 7-bit addressing. Handles START, STOP conditions. "
            "ACK/NACK detection. Register interface for address, data, read/write, "
            "start trigger. Busy, done, error status. 50 MHz system clock."
        ),
    },
    {
        "id": "apb_timer",
        "complexity": "Medium",
        "desc": (
            "32-bit APB timer peripheral with interrupt. APB3 slave interface with "
            "PCLK, PRESETn, PSEL, PENABLE, PWRITE, PADDR, PWDATA, PRDATA, PREADY. "
            "Registers: control, prescaler, reload value, current count, interrupt status. "
            "Supports one-shot and continuous modes. Interrupt when counter reaches zero. "
            "Prescaler divides clock 1 to 65536."
        ),
    },
    {
        "id": "vga_ctrl",
        "complexity": "Medium",
        "desc": (
            "VGA timing controller for 640x480 at 60 Hz. Generates HSYNC and VSYNC "
            "with correct timing. Outputs current pixel X and Y coordinates and "
            "active video enable signal. Pixel clock input at 25 MHz."
        ),
    },
    {
        "id": "wb_uart",
        "complexity": "Complex",
        "desc": (
            "UART transceiver with Wishbone B4 slave interface. 8N1 format. "
            "Configurable baud rate via baud divisor register. 16-byte TX FIFO and "
            "16-byte RX FIFO. Wishbone registers: TX data, RX data, status "
            "(TX full, TX empty, RX full, RX empty, overrun), control (baud divisor, "
            "loopback enable), interrupt enable. Interrupt on RX available and TX empty. "
            "50 MHz clock. Wishbone signals: CLK_I RST_I ADR_I DAT_I DAT_O WE_I STB_I ACK_O CYC_I."
        ),
    },
]

# ─────────────────────────────────────────────────────────────
# STAGE METADATA
# ─────────────────────────────────────────────────────────────
STAGE_INFO = {
    "INIT":           {"name": "Environment Setup",        "critical": True},
    "SPEC":           {"name": "Architectural Planning",   "critical": True},
    "RTL_GEN":        {"name": "RTL Generation",           "critical": True},
    "RTL_FIX":        {"name": "RTL Lint & Syntax Fix",    "critical": True},
    "VERIFICATION":   {"name": "Functional Simulation",    "critical": True},
    "FORMAL_VERIFY":  {"name": "Formal Verification",      "critical": False},
    "COVERAGE_CHECK": {"name": "Coverage Closure",         "critical": False},
    "REGRESSION":     {"name": "Regression Testing",       "critical": False},
    "SDC_GEN":        {"name": "Timing Constraints",       "critical": True},
    "FLOORPLAN":      {"name": "Physical Floorplanning",   "critical": True},
    "HARDENING":      {"name": "Place & Route",            "critical": True},
    "CONVERGENCE":    {"name": "Timing Convergence",       "critical": True},
    "ECO_PATCH":      {"name": "Engineering Change Order", "critical": False},
    "SIGNOFF":        {"name": "DRC/LVS/STA Signoff",      "critical": True},
}

SUCCESS_MARKERS = [
    "PIPELINE COMPLETE", "BUILD COMPLETE", "ALL STAGES PASSED",
    "SIGNOFF PASSED", "BUILD SUCCEEDED", "SUCCESS",
]
FAILURE_MARKERS = [
    "PIPELINE FAILED", "BUILD FAILED", "FATAL ERROR",
    "STAGE FAILED", "ABORTING", "FAIL-CLOSED",
]

STAGE_PATTERN = re.compile(
    r"\b(INIT|SPEC|RTL_GEN|RTL_FIX|VERIFICATION|FORMAL_VERIFY|"
    r"COVERAGE_CHECK|REGRESSION|SDC_GEN|FLOORPLAN|HARDENING|"
    r"CONVERGENCE|ECO_PATCH|SIGNOFF)\b",
    re.IGNORECASE,
)


def parse_args():
    p = argparse.ArgumentParser(description="AgentIC Benchmark Runner v2")
    p.add_argument("--pdk", default="sky130", choices=["sky130", "gf180"])
    p.add_argument("--skip-openlane", action="store_true")
    p.add_argument("--skip-coverage", action="store_true")
    p.add_argument("--full-signoff", action="store_true")
    p.add_argument("--design", default=None)
    p.add_argument("--max-retries", default=3, type=int)
    p.add_argument("--output-dir", default="benchmark/results")
    p.add_argument("--attempts", default=1, type=int)
    p.add_argument("--timeout", default=3600, type=int)
    return p.parse_args()


def build_command(design, args):
    cmd = [
        "python3", "main.py", "build",
        "--name", design["id"],
        "--desc", design["desc"],
        "--pdk-profile", args.pdk,
        "--max-retries", str(args.max_retries),
        "--strict-gates",
    ]
    if args.skip_openlane:
        cmd.append("--skip-openlane")
    if args.skip_coverage:
        cmd.append("--skip-coverage")
    if args.full_signoff:
        cmd.append("--full-signoff")
    return cmd


def detect_pass_fail(stdout, stderr, returncode):
    combined = (stdout + stderr).upper()
    for marker in FAILURE_MARKERS:
        if marker in combined:
            return False
    for marker in SUCCESS_MARKERS:
        if marker in combined:
            return True
    return returncode == 0


def extract_failed_stage(stdout, stderr):
    combined = stdout + stderr
    last_stage = None
    for line in combined.split("\n"):
        m = STAGE_PATTERN.search(line)
        if m:
            last_stage = m.group(1).upper()
        if any(kw in line.upper() for kw in ["FAILED", "ERROR", "FATAL", "ABORT", "FAIL-CLOSED"]):
            return last_stage, line.strip()[:250]
    return None, None


def extract_completed_stages(stdout):
    completed = []
    for line in stdout.split("\n"):
        m = STAGE_PATTERN.search(line)
        if m:
            stage = m.group(1).upper()
            if any(kw in line.upper() for kw in [
                "COMPLETE", "PASSED", "SUCCESS", "DONE", "TRANSITION", "FINISHED"
            ]):
                if stage not in completed:
                    completed.append(stage)
    return completed


def find_openlane_root():
    env = os.environ.get("OPENLANE_ROOT")
    if env and Path(env).exists():
        return env
    for c in [Path.home() / "OpenLane", Path("/opt/OpenLane")]:
        if c.exists():
            return str(c)
    return str(Path.home() / "OpenLane")


def find_artifacts(design_id, openlane_root):
    """Only find files actually belonging to THIS design — no false positives."""
    found = {}
    type_map = {
        ".v": "RTL", ".sv": "RTL", ".sva": "FORMAL", ".sby": "FORMAL",
        ".sdc": "TIMING", ".tcl": "PHYSICAL", ".lef": "PHYSICAL",
        ".def": "PHYSICAL", ".gds": "PHYSICAL", ".json": "CONFIG",
        ".log": "LOG", ".rpt": "SIGNOFF",
    }
    scan_dirs = [
        Path(f"outputs/{design_id}"),
        Path(f"results/{design_id}"),
        Path(f"designs/{design_id}"),
        Path(openlane_root) / "designs" / design_id,
    ]
    for d in scan_dirs:
        if not d.exists():
            continue
        for f in d.rglob("*"):
            if not f.is_file():
                continue
            # STRICT: only files where design_id is in filename OR direct parent folder
            in_name   = design_id.lower() in f.name.lower()
            in_parent = design_id.lower() in f.parent.name.lower()
            if not (in_name or in_parent):
                continue
            atype = type_map.get(f.suffix.lower())
            if not atype:
                continue
            if atype == "RTL" and "_tb" in f.name.lower():
                atype = "TESTBENCH"
            if atype not in found:
                found[atype] = []
            sz = f.stat().st_size
            found[atype].append({
                "file": f.name,
                "path": str(f),
                "size_bytes": sz,
                "size_human": fmt_size(sz),
            })
    return found


def fmt_size(b):
    if b < 1024:      return f"{b} B"
    elif b < 1048576: return f"{b/1024:.1f} KB"
    else:             return f"{b/1048576:.1f} MB"


def run_build(design, args, attempt):
    openlane_root = find_openlane_root()
    cmd = build_command(design, args)
    timestamp = datetime.datetime.now().isoformat()

    print(f"\n{'─'*60}")
    print(f"  Design   : {design['id']} ({design['complexity']})")
    print(f"  Attempt  : {attempt}")
    print(f"  PDK      : {args.pdk}")
    print(f"  Command  : {' '.join(cmd[:6])} ...")
    print(f"{'─'*60}")

    start = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout)
        stdout, stderr, retcode = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        dur = round((time.time() - start) / 60, 1)
        print(f"  Result   : ✗ TIMEOUT ({dur} min)")
        return make_result(design, attempt, args, False, "TIMEOUT",
                           f"Exceeded {args.timeout}s timeout", [], {}, dur, timestamp, True)
    except FileNotFoundError:
        print("ERROR: main.py not found. Run from AgentIC root.")
        sys.exit(1)

    dur    = round((time.time() - start) / 60, 1)
    passed = detect_pass_fail(stdout, stderr, retcode)

    # Sanity check — real builds never finish in under 2 minutes
    # BUT only apply this if no real stages completed (otherwise it was a real fast failure)
    completed = extract_completed_stages(stdout)
    if dur < 2.0 and len(completed) <= 1:
        print(f"  ⚠ WARNING: Finished in {dur} min with no meaningful progress.")
        print(f"  ⚠ Check that your CLI args match and the orchestrator actually ran.")
        passed       = False
        failed_stage = "INIT"
        failed_reason = f"Build exited in {dur} min with ≤1 stage — CLI args may be wrong."
    elif dur < 2.0:
        # Build ran real stages but failed fast — use real failure data
        print(f"  ⚠ NOTE: Build completed in {dur} min (fast failure after {len(completed)} stages).")
        failed_stage, failed_reason = (None, None) if passed else extract_failed_stage(stdout, stderr)
    else:
        failed_stage, failed_reason = (None, None) if passed else extract_failed_stage(stdout, stderr)


    artifacts  = find_artifacts(design["id"], openlane_root)

    status    = "✓ PASS" if passed else "✗ FAIL"
    fail_info = ""
    if failed_stage:
        name      = STAGE_INFO.get(failed_stage, {}).get("name", failed_stage)
        fail_info = f" — failed at {failed_stage} ({name})"

    print(f"  Result   : {status}{fail_info}")
    print(f"  Time     : {dur} min")
    print(f"  Stages   : {len(completed)} completed")
    print(f"  Artifacts: {', '.join(artifacts.keys()) if artifacts else 'none found for this design'}")

    return make_result(design, attempt, args, passed, failed_stage,
                       failed_reason, completed, artifacts, dur, timestamp)


def make_result(design, attempt, args, passed, failed_stage, failed_reason,
                completed, artifacts, duration, timestamp, timed_out=False):
    info = STAGE_INFO.get(failed_stage, {}) if failed_stage else {}
    return {
        "design_id":              design["id"],
        "complexity":             design["complexity"],
        "attempt":                attempt,
        "passed":                 passed,
        "timed_out":              timed_out,
        "failed_stage":           failed_stage,
        "failed_stage_name":      info.get("name"),
        "failed_stage_critical":  info.get("critical"),
        "failed_reason":          failed_reason,
        "completed_stages":       completed,
        "completed_stages_count": len(completed),
        "artifacts":              artifacts,
        "artifact_types":         list(artifacts.keys()),
        "rtl_generated":          "RTL" in artifacts,
        "testbench_generated":    "TESTBENCH" in artifacts,
        "gds_generated":          "PHYSICAL" in artifacts,
        "duration_minutes":       duration,
        "timestamp":              timestamp,
        "pdk":                    args.pdk,
    }


def print_summary(results):
    passed  = [r for r in results if r["passed"]]
    failed  = [r for r in results if not r["passed"]]
    rate    = len(passed) / len(results) * 100 if results else 0
    avg     = sum(r["duration_minutes"] for r in results) / len(results)
    fails   = {}
    for r in failed:
        s = r.get("failed_stage")
        if s: fails[s] = fails.get(s, 0) + 1

    print(f"\n{'═'*60}")
    print(f"  BENCHMARK COMPLETE")
    print(f"{'═'*60}")
    print(f"  Pass Rate : {rate:.0f}%  ({len(passed)}/{len(results)})")
    print(f"  Avg Time  : {avg:.1f} min")
    print(f"{'─'*60}")
    for r in passed:
        print(f"    ✓  {r['design_id']:<22} {r['duration_minutes']} min")
    for r in failed:
        at = r.get("failed_stage_name") or r.get("failed_stage") or "unknown"
        print(f"    ✗  {r['design_id']:<22} failed at {at}")
    if fails:
        worst = max(fails, key=fails.get)
        name  = STAGE_INFO.get(worst, {}).get("name", worst)
        print(f"\n  ⚠  Bottleneck: {worst} ({name}) — fix this first")
    print(f"{'═'*60}\n")


def generate_markdown(results, args):
    today  = datetime.date.today().strftime("%B %d, %Y")
    passed = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]
    rate   = len(passed) / len(results) * 100 if results else 0
    avg    = sum(r["duration_minutes"] for r in results) / len(results)
    fails  = {}
    for r in failed:
        s = r.get("failed_stage")
        if s: fails[s] = fails.get(s, 0) + 1

    L = [
        f"# AgentIC Benchmark Report",
        f"**Date:** {today}  ",
        f"**PDK:** {args.pdk}  ",
        f"**Model:** NVIDIA NIM — Llama 3.3 70B  ",
        f"**Mode:** {'RTL only' if args.skip_openlane else 'Full pipeline'}",
        "",
        "## Summary",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total Designs | {len(results)} |",
        f"| **First-Attempt Pass Rate** | **{rate:.0f}% ({len(passed)}/{len(results)})** |",
        f"| Average Build Time | {avg:.1f} min |",
        f"| RTL Generated (incl. failures) | {sum(1 for r in results if r.get('rtl_generated'))}/{len(results)} |",
        f"| GDS Generated | {sum(1 for r in results if r.get('gds_generated'))}/{len(results)} |",
        "",
        "## Results",
        "| Design | Complexity | Pass? | Failed At | Time | RTL | GDS |",
        "|--------|-----------|-------|-----------|------|-----|-----|",
    ]
    for r in results:
        s = "✓" if r["passed"] else "✗"
        f = r.get("failed_stage_name") or r.get("failed_stage") or "—"
        L.append(f"| {r['design_id']} | {r['complexity']} | {s} | {f} | {r['duration_minutes']} min | {'✓' if r.get('rtl_generated') else '✗'} | {'✓' if r.get('gds_generated') else '✗'} |")

    if fails:
        L += ["", "## Stage Failure Analysis",
              "| Stage | Industry Name | Failures | Critical? |",
              "|-------|--------------|----------|-----------|"]
        for stage, count in sorted(fails.items(), key=lambda x: -x[1]):
            info = STAGE_INFO.get(stage, {})
            L.append(f"| {stage} | {info.get('name', stage)} | {count} | {'🔴 Yes' if info.get('critical') else '🟡 Optional'} |")
        worst = max(fails, key=fails.get)
        L += ["", f"**Fix `{worst}` first.**"]

    L += [
        "", "## Which Stages Matter in Industry",
        "| Stage | Skip OK? | Why |",
        "|-------|----------|-----|",
        "| RTL_GEN + RTL_FIX | ❌ Never | This is the chip |",
        "| VERIFICATION | ❌ Never | Proves it works |",
        "| HARDENING | ❌ Never | Physical layout |",
        "| SIGNOFF | ❌ Never | Fab requirement |",
        "| FORMAL_VERIFY | ✅ Simple designs | Optional for non-safety-critical |",
        "| COVERAGE_CHECK | ✅ If sim passes | Nice to have |",
        "| REGRESSION | ✅ Yes | Corner cases only |",
        "| ECO_PATCH | ✅ First attempt | Only if signoff fails |",
        "| CONVERGENCE | ✅ Simple designs | Embedded in hardening |",
        "", "---",
        f"*Generated by AgentIC Benchmark Runner — {today}*",
    ]
    return "\n".join(L)


def main():
    args = parse_args()

    if not Path("main.py").exists():
        print("ERROR: Run from AgentIC root directory (where main.py is).")
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    designs = TEST_DESIGNS
    if args.design:
        designs = [d for d in TEST_DESIGNS if d["id"] == args.design]
        if not designs:
            print(f"ERROR: '{args.design}' not found.")
            print(f"Available IDs: {[d['id'] for d in TEST_DESIGNS]}")
            sys.exit(1)

    total    = len(designs) * args.attempts
    est_mins = total * 25

    print(f"\n{'═'*60}")
    print(f"  AgentIC Benchmark Runner v2")
    print(f"{'═'*60}")
    print(f"  Designs   : {len(designs)}")
    print(f"  Total runs: {total}")
    print(f"  PDK       : {args.pdk}")
    mode_parts = []
    if args.skip_openlane:
        mode_parts.append("RTL only (--skip-openlane)")
    else:
        mode_parts.append("Full pipeline")
    if args.skip_coverage:
        mode_parts.append("skip coverage (--skip-coverage)")
    print(f"  Mode      : {', '.join(mode_parts)}")
    print(f"  Est. time : ~{est_mins} min")
    print(f"  Output    : {output_dir}/")
    print(f"{'═'*60}\n")

    all_results = []
    date_str    = datetime.date.today().strftime("%Y-%m-%d")

    for design in designs:
        for attempt in range(1, args.attempts + 1):
            result = run_build(design, args, attempt)
            all_results.append(result)
            # Save after every build
            with open(output_dir / f"interim_{date_str}.json", "w") as f:
                json.dump({"results": all_results}, f, indent=2)

    # Final saves
    with open(output_dir / f"benchmark_{date_str}.json", "w") as f:
        json.dump({
            "meta": {
                "date": date_str, "pdk": args.pdk,
                "pass_rate_pct": round(
                    len([r for r in all_results if r["passed"]]) / len(all_results) * 100, 1
                )
            },
            "results": all_results
        }, f, indent=2)

    md_path = output_dir / f"benchmark_{date_str}.md"
    with open(md_path, "w") as f:
        f.write(generate_markdown(all_results, args))

    print_summary(all_results)
    print(f"  Saved: {md_path}")
    print(f"  Saved: {output_dir}/benchmark_{date_str}.json\n")


if __name__ == "__main__":
    main()
