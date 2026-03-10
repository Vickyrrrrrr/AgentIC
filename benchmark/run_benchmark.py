#!/usr/bin/env python3
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import traceback


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.agentic.cli import get_llm
from src.agentic.orchestrator import Orchestrator
from src.agentic.tools.vlsi_tools import run_lint_check, run_simulation

DESIGNS_DIR = REPO_ROOT / "benchmark" / "designs"
RESULTS_PATH = REPO_ROOT / "benchmark" / "results.tsv"
RESULTS_HEADER = "commit\tdesign\tlint\tsimulation\tformal\toverall\ttimestamp\n"


def parse_args():
    parser = argparse.ArgumentParser(description="Run AgentIC benchmark designs.")
    parser.add_argument(
        "--designs",
        type=int,
        default=None,
        help="Limit the benchmark to the first N designs.",
    )
    return parser.parse_args()


def ensure_results_file():
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not RESULTS_PATH.exists():
        RESULTS_PATH.write_text(RESULTS_HEADER)


def get_commit_hash():
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        return "UNKNOWN"


def list_design_specs(limit=None):
    specs = sorted(DESIGNS_DIR.glob("*.md"))
    if limit is not None:
        return specs[: max(0, limit)]
    return specs


def pass_fail(ok):
    return "PASS" if ok else "FAIL"


def latest_timestamp():
    return datetime.now(timezone.utc).isoformat()


def append_result_row(commit_hash, design, lint, simulation, formal, overall):
    row = "\t".join(
        [
            commit_hash,
            design,
            lint,
            simulation,
            formal,
            overall,
            latest_timestamp(),
        ]
    )
    with RESULTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{row}\n")


def compute_stage_results(orchestrator, design_name):
    lint_result = "FAIL"
    simulation_result = "FAIL"
    formal_result = "FAIL"
    overall_result = "FAIL"

    rtl_path = ""
    if orchestrator is not None:
        rtl_path = str(orchestrator.artifacts.get("rtl_path", "") or "")
        formal_result = pass_fail(orchestrator.artifacts.get("formal_result") == "PASS")
        overall_result = pass_fail(getattr(orchestrator, "state", None) is not None and orchestrator.state.name == "SUCCESS")

    if rtl_path:
        try:
            lint_ok, _ = run_lint_check(rtl_path)
            lint_result = pass_fail(lint_ok)
        except Exception:
            lint_result = "FAIL"

    try:
        sim_ok, _ = run_simulation(design_name)
        simulation_result = pass_fail(sim_ok)
    except Exception:
        simulation_result = "FAIL"

    return lint_result, simulation_result, formal_result, overall_result


def print_stage(design_name, stage_name, status):
    print(f"[{design_name}] {stage_name}: {status}", flush=True)


def run_design(spec_path, commit_hash):
    design_name = spec_path.stem
    design_spec = spec_path.read_text(encoding="utf-8")
    orchestrator = None

    print(f"[{design_name}] start", flush=True)

    try:
        llm = get_llm()
        orchestrator = Orchestrator(
            name=design_name,
            desc=design_spec,
            llm=llm,
            max_retries=5,
            verbose=False,
            skip_openlane=True,
            skip_coverage=False,
            full_signoff=False,
            strict_gates=True,
        )
        orchestrator.run()
    except Exception as exc:
        print(f"[{design_name}] exception: {exc}", flush=True)
        print(traceback.format_exc(), flush=True)

    lint, simulation, formal, overall = compute_stage_results(orchestrator, design_name)
    print_stage(design_name, "lint", lint)
    print_stage(design_name, "simulation", simulation)
    print_stage(design_name, "formal", formal)
    print_stage(design_name, "overall", overall)
    append_result_row(commit_hash, design_name, lint, simulation, formal, overall)
    print(f"[{design_name}] results.tsv appended", flush=True)


def main():
    args = parse_args()
    ensure_results_file()
    commit_hash = get_commit_hash()
    for spec_path in list_design_specs(args.designs):
        try:
            run_design(spec_path, commit_hash)
        except Exception as exc:
            design_name = spec_path.stem
            print(f"[{design_name}] outer exception: {exc}", flush=True)
            print(traceback.format_exc(), flush=True)
            append_result_row(commit_hash, design_name, "FAIL", "FAIL", "FAIL", "FAIL")
            print(f"[{design_name}] results.tsv appended after outer exception", flush=True)


if __name__ == "__main__":
    main()
