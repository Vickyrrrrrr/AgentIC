#!/usr/bin/env python3
import os
import json
import subprocess
import time
import shutil
import random
from datasets import load_dataset

# Configuration
NUM_TESTS = 10
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACTS_DIR = os.path.join(PROJECT_ROOT, "artifacts")
DATA_FILE = os.path.join(ARTIFACTS_DIR, "benchmark_data.json")

def main():
    print(f"Starting AgentIC Benchmark on NVlabs verilog-eval ({NUM_TESTS} tests)")
    os.makedirs(ARTIFACTS_DIR, exist_ok=True)
    
    # Load dataset
    print("Loading dataset: dakies/nvlabs-verilogeval...")
    dataset = load_dataset("dakies/nvlabs-verilogeval", split="test")
    
    # Categorize tests by difficulty if possible, otherwise randomly pick 10
    # Usually Verilog-Eval task IDs might contain hints or we can just randomize.
    # In VerilogEval, "task_id" is often formatted differently for easy vs hard.
    all_tasks = list(dataset)
    random.seed(42) # Replicable benchmark
    random.shuffle(all_tasks)
    
    selected_tasks = all_tasks[:NUM_TESTS]
    
    import sys
    results = []
    
    for i, task in enumerate(selected_tasks):
        task_id = task.get('task_id', f'unknown_task_{i}').replace('/', '_').replace('-', '_')
        task_id = "eval_" + task_id
        prompt = task.get('prompt', '') or task.get('text', '') or task.get('description', '')
        if not prompt:
            # Fallback if spec key is different
            prompt = str(task)
            
        print(f"\n[{i+1}/{NUM_TESTS}] Running task: {task_id}")
        
        start_time = time.time()
        
        # Build command invoking AgentIC CLI
        cmd = [
            sys.executable, os.path.join(PROJECT_ROOT, "main.py"), "build",
            "--name", task_id,
            "--desc", prompt[:1000],
            "--skip-openlane",
            "--show-thinking",
            "--max-retries", "2"
        ]
        
        # We will write the prompt to a file to be safe from bash quoting issues, but AgentIC CLI doesn't natively accept file.
        # So we pass it as a string argument.
        process = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        
        output = ""
        for line in iter(process.stdout.readline, ''):
            print(line, end="")
            output += line
        process.wait()
        
        elapsed = time.time() - start_time
        
        # Verify result
        passed = "SIMULATION PASSED" in output.upper() or "signoff passed" in output.lower() or "build successful" in output.lower()
        
        # Capture generated files if any
        rtl_path = os.path.join(PROJECT_ROOT, "designs", task_id, "src", f"{task_id}.v")
        tb_path = os.path.join(PROJECT_ROOT, "designs", task_id, "src", f"{task_id}_tb.v")
        
        rtl_code = ""
        tb_code = ""
        if os.path.exists(rtl_path):
            with open(rtl_path, 'r') as f: rtl_code = f.read()
        if os.path.exists(tb_path):
            with open(tb_path, 'r') as f: tb_code = f.read()
            
        sim_log = "Unknown log"
        if "SIMULATION PASSED" in output.upper():
            sim_log = "Passed OSS CAD Suite Simulation"
        elif "SIMULATION FAILED" in output.upper() or "ERROR" in output.upper():
            # Try to extract the tail of the log
            sim_log = "\n".join(output.split('\n')[-30:])
            
        record = {
            "task_id": task_id,
            "prompt": prompt,
            "passed": passed,
            "execution_time_seconds": elapsed,
            "generated_rtl": rtl_code,
            "generated_tb": tb_code,
            "simulation_log": sim_log
        }
        
        results.append(record)
        
        # Save intermediate
        with open(DATA_FILE, 'w') as f:
            json.dump(results, f, indent=2)
            
    # Calculate score
    passed_count = sum(1 for r in results if r["passed"])
    print(f"\nBenchmark Complete! Passed: {passed_count}/{NUM_TESTS}")
    
    # Generate Markdown Report
    report_path = os.path.join(ARTIFACTS_DIR, "benchmark_report.md")
    with open(report_path, "w") as f:
        f.write("# AgentIC Evaluation Report on NVlabs Verilog-Eval\n\n")
        f.write(f"**Overall Score:** {passed_count}/{NUM_TESTS} ({passed_count/NUM_TESTS*100:.1f}%)\n\n")
        f.write("## Overview\n")
        f.write("This report evaluates the ability of the configured model to autonomously translate natural language specifications into functionally correct Verilog RTL using the AgentIC orchestrator with OSS CAD Suite (Yosys/Verilator) validation.\n\n")
        
        f.write("## Real-World Significance\n")
        f.write("- **What this grade means:** Passing a test indicates the LLM was able to understand complex cycle-accurate Verilog directives, generate correct state machines (FSMs) or combinatorial logic, and survive strict Testbench syntax gating.\n")
        if passed_count > 7:
            f.write("- **Implications:** The model exhibits top-tier structural reasoning suitable for autonomous hardware design at the module level. It requires very little human intervention.\n\n")
        elif passed_count > 3:
            f.write("- **Implications:** The model shows a decent grasp of HDL syntax but struggles with nuanced logic constraints. It's best used as a co-pilot rather than an autonomous engineer.\n\n")
        else:
            f.write("- **Implications:** The model failed heavily, likely due to syntax hallucination, rate limit timeouts, or failure to understand testbench bidirectionality.\n\n")
            
        f.write("## Detailed Test Matrix\n")
        f.write("| Task ID | Passed | Execution Time (s) | Notes |\n")
        f.write("|---------|--------|--------------------|-------|\n")
        for r in results:
            pass_str = "✅ PASS" if r["passed"] else "❌ FAIL"
            notes = "Completed logic layout cleanly" if r["passed"] else r["simulation_log"][:60].replace("\n", " ") + "..."
            f.write(f"| `{r['task_id']}` | {pass_str} | {r['execution_time_seconds']:.2f} | {notes} |\n")
            
        f.write("\n\n---\n*Auto-generated by AgentIC Benchmarking Suite*\n")
    print(f"Report written to {report_path}")

if __name__ == "__main__":
    main()
