#!/usr/bin/env python3

import os
import sys
import json
import argparse
import subprocess
import tempfile
import traceback

# Ensure we can import from the AgentIC codebase
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env')))
except ImportError:
    pass

from datasets import load_dataset
from src.agentic.orchestrator import BuildOrchestrator, BuildState
from src.agentic.cli import get_llm
from src.agentic.config import OPENLANE_ROOT
from src.agentic.tools.vlsi_tools import run_openlane

def simulate_design(rtl_code: str, tb_code: str) -> tuple[bool, str]:
    """
    Compiles rtl + tb using iverilog, simulates with vvp.
    Checks stdout for 'Mismatches: 0'
    Returns (success: bool, output/error: str)
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        rtl_file = os.path.join(temp_dir, "design.v")
        tb_file = os.path.join(temp_dir, "tb.v")
        sim_file = os.path.join(temp_dir, "sim.vvp")

        with open(rtl_file, "w") as f:
            f.write(rtl_code)
        with open(tb_file, "w") as f:
            f.write(tb_code)

        try:
            # -g2012 ensures SystemVerilog features are supported 
            compile_res = subprocess.run(
                ["iverilog", "-g2012", "-o", sim_file, tb_file, rtl_file],
                capture_output=True, text=True, timeout=30
            )
            if compile_res.returncode != 0:
                return False, f"Compilation failed:\n{compile_res.stderr}"
        except Exception as e:
            return False, f"Iverilog execution error: {e}"

        try:
            sim_res = subprocess.run(
                ["vvp", sim_file],
                capture_output=True, text=True, timeout=60
            )
            out = sim_res.stdout + "\n" + sim_res.stderr
            
            # The prompt requested explicitly checking stdout for 'Mismatches: 0'
            if "Mismatches: 0" in out:
                return True, out
            else:
                return False, f"Simulation output mismatch/fail:\n{out[:1000]}"
                
        except subprocess.TimeoutExpired:
            return False, "Simulation timed out"
        except Exception as e:
            return False, f"VVP execution error: {e}"


def main():
    parser = argparse.ArgumentParser(description="AgentIC VerilogEval-Human Benchmark")
    parser.add_argument("--limit", type=int, default=20, help="Number of problems to evaluate")
    parser.add_argument("--harden", action="store_true", help="Run OpenLane hardening on passing designs")
    parser.add_argument("--output-dir", type=str, default="benchmark/results", help="Output directory")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    report_file = os.path.join(args.output_dir, "benchmark_report.json")

    print("[*] Loading dakies/nvlabs-verilogeval test split...")
    try:
        # Pass token automatically if users have HF_TOKEN set, or use logged in credentials
        ds = load_dataset("dakies/nvlabs-verilogeval", split="test", token=os.environ.get("HF_TOKEN", True))
    except Exception as e:
        print(f"Error loading dataset: {e}")
        print("Please ensure you have accepted the dataset terms on huggingface and set HF_TOKEN environment variable.")
        return

    print(f"[*] Loaded {len(ds)} tasks from Hugging Face dataset.")

    try:
        try:
            llm = get_llm()
        except BaseException as e: # Catch typer.Exit or SystemExit
            print(f"Failed to initialize LLM. Make sure API keys are configured: {e}")
            return
    except Exception as e:
        print(f"Failed to initialize LLM: {e}")
        return

    results = []
    total = 0
    passed = 0
    hardened = 0

    print(f"[*] Starting benchmark for {args.limit} tasks...\n")

    for row in ds:
        if total >= args.limit:
            break
            
        task_id = row.get("task_id", f"task_{total}")
        prompt = row.get("prompt", "")
        testbench = row.get("test", "")
        
        # design_name should be sanitized for directory creation inside AgentIC
        design_name = task_id.replace("/", "_").replace("-", "_").lower()

        total += 1
        
        task_passed = False
        task_hardened = False
        failure_stage = ""
        failure_reason = ""
        
        try:
            # 1. Run AgentIC pipeline directly
            orchestrator = BuildOrchestrator(
                name=design_name,
                desc=prompt,
                llm=llm,
                max_retries=3,
                skip_openlane=True,
                tb_fallback_template="classic"  # Optional, but prevents prompt overrides
            )
            
            orchestrator.run()

            # Identify if orchestration inherently failed
            if getattr(orchestrator, "state", None) == BuildState.FAIL:
                failure_stage = "AgentIC Orchestrator"
                failure_reason = "Hit maximum retries or internal failure state."
            
            # Predict the RTL artifact path based on standard AgentIC behaviors
            rtl_path = orchestrator.artifacts.get(
                "rtl_path", 
                os.path.join(OPENLANE_ROOT, "designs", design_name, "src", f"{design_name}.v")
            )

            if not os.path.exists(rtl_path):
                # Don't overwrite error logs if it already failed above
                if not failure_stage:
                    failure_stage = "Output Missing"
                    failure_reason = f"AgentIC did not write the RTL file to: {rtl_path}"
            
            # Continue to Simulation if no failure so far
            if not failure_stage:
                with open(rtl_path, "r") as f:
                    generated_rtl = f.read()

                sim_ok, sim_msg = simulate_design(generated_rtl, testbench)
                
                if not sim_ok:
                    failure_stage = "Simulation"
                    failure_reason = sim_msg
                else:
                    task_passed = True
                    passed += 1
                    
                    # 3. Harden if passed and flag is present
                    if args.harden:
                        harden_ok, harden_msg = run_openlane(design_name=design_name, run_tag="agentrun")
                        
                        gds_path = os.path.join(
                            OPENLANE_ROOT, "designs", design_name, "runs", "agentrun", "results", "final", "gds", f"{design_name}.gds"
                        )
                        
                        if harden_ok or os.path.exists(gds_path):
                            task_hardened = True
                            hardened += 1
                        else:
                            # It passed simulation, but failed hardening
                            failure_stage = "OpenLane Hardening"
                            failure_reason = harden_msg
        
        except Exception as e:
            failure_stage = "Exception"
            failure_reason = str(e) + "\n" + traceback.format_exc()

        # Console Progress Output [3/20] task_id — PASSED / FAILED: reason
        if task_passed and (not args.harden or task_hardened):
            status_str = "PASSED"
            if args.harden:
                status_str += " AND HARDENED"
            print(f"[{total}/{args.limit}] {task_id} — {status_str}")
            
        elif task_passed and args.harden and not task_hardened:
            reason_str = str(failure_reason).split('\n')[0][:100]
            print(f"[{total}/{args.limit}] {task_id} — PASSED SIMULATION / FAILED HARDENING: {reason_str}")
            
        else:
            reason_str = str(failure_reason).split('\n')[0][:100]
            print(f"[{total}/{args.limit}] {task_id} — FAILED [{failure_stage}]: {reason_str}")

        # Add to JSON output
        results.append({
            "task_id": task_id,
            "passed": task_passed,
            "hardened": task_hardened,
            "failure_stage": failure_stage if not task_passed or (args.harden and not task_hardened) else None,
            "failure_reason": failure_reason if not task_passed or (args.harden and not task_hardened) else None
        })

        # Process and save incremental report
        report_data = {
            "total": total,
            "passed": passed,
            "hardened": hardened,
            "pass_rate": round(passed / total * 100, 2) if total > 0 else 0,
            "harden_rate": round(hardened / passed * 100, 2) if passed > 0 else 0,
            "results": results
        }
        
        with open(report_file, "w") as f:
            json.dump(report_data, f, indent=2)

    print(f"\n[*] Benchmark Complete! Passed Simulation: {passed}/{total}.")
    if args.harden:
        print(f"[*] Hardened: {hardened}/{passed}.")
    print(f"[*] Final results saved to {report_file}")

if __name__ == "__main__":
    main()
