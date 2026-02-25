#!/usr/bin/env python3
"""
VeriReason Reasoning Generator (Log-Based)
============================================
Feeds actual AgentIC build logs to VeriReason and asks it to generate
chain-of-thought (CoT) reasoning about what happened — what went wrong,
why fixes worked, what the correct approach should have been.

This creates training data where VeriReason learns from REAL build
experiences, not generic reasoning.

Usage:
    # Step 1: Collect data from cloud builds
    python3 training/collect_training_data.py

    # Step 2: Generate log-based reasoning
    ollama serve  # make sure Ollama is running
    python3 training/generate_reasoning.py

    # Step 3: Fine-tune
    llamafactory-cli train training/agentic_sft_config.yaml
"""

import argparse
import glob
import json
import os
import re
import requests
import time
from typing import Optional, List, Dict


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
VERIREASON_MODEL = os.environ.get(
    "VERIREASON_MODEL",
    "hf.co/mradermacher/VeriReason-Qwen2.5-3b-RTLCoder-Verilog-GRPO-reasoning-tb-GGUF:Q4_K_M",
)
OPENLANE_ROOT = os.environ.get("OPENLANE_ROOT", os.path.expanduser("~/OpenLane"))


def check_ollama() -> bool:
    """Check if Ollama is running and VeriReason is available."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def ask_verireason(prompt: str, timeout: int = 180) -> Optional[str]:
    """Send a prompt to VeriReason via Ollama and get the response."""
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": VERIREASON_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 2048},
            },
            timeout=timeout,
        )
        if r.status_code == 200:
            return r.json().get("response", "").strip()
        return None
    except Exception as e:
        print(f"  Ollama error: {e}")
        return None


def extract_log_summary(log_path: str, max_chars: int = 4000) -> str:
    """Extract a condensed summary of key events from a build log."""
    with open(log_path, "r") as f:
        log_text = f.read()

    events = []

    # Extract state transitions
    for m in re.finditer(r"\[(\w+)\] Transitioning: (\w+) -> (\w+)", log_text):
        events.append(f"STATE: {m.group(2)} → {m.group(3)}")

    # Extract errors
    for m in re.finditer(r"(SYNTAX/LINT ERRORS|LINT REPORT):\s*\n([\s\S]*?)(?:\n\d{4}-\d{2}|\Z)", log_text):
        error_text = m.group(2).strip()[:500]
        events.append(f"ERROR: {error_text}")

    # Extract RTL code blocks (first and last only)
    rtl_blocks = re.findall(r"(GENERATED RTL|FIXED RTL).*?:\s*\n```(?:verilog)?\n([\s\S]*?)```", log_text)
    if rtl_blocks:
        events.append(f"INITIAL RTL:\n```verilog\n{rtl_blocks[0][1][:1500]}\n```")
        if len(rtl_blocks) > 1:
            events.append(f"FINAL RTL:\n```verilog\n{rtl_blocks[-1][1][:1500]}\n```")

    # Extract simulation results
    for m in re.finditer(r"\[VERIFICATION\] (Sim Failed|Simulation Passed|Diagnosis:.*?)$", log_text, re.MULTILINE):
        events.append(f"SIM: {m.group(1)[:200]}")

    # Extract TB gate results
    for m in re.finditer(r"TB (COMPILE|STATIC) GATE \((PASS|FAIL)\)", log_text):
        events.append(f"TB GATE: {m.group(1)} {m.group(2)}")

    # Extract final status
    if "BUILD FAILED" in log_text:
        events.append("RESULT: BUILD FAILED")
    elif "SIGNOFF PASSED" in log_text or "Simulation Passed" in log_text:
        events.append("RESULT: BUILD PASSED")

    summary = "\n".join(events)
    return summary[:max_chars]


def generate_reasoning_from_log(design_name: str, log_summary: str, category: str) -> Optional[str]:
    """
    Ask VeriReason to read a build log and generate chain-of-thought
    reasoning about what happened and what should have been done.
    """
    if category == "rtl_generation":
        prompt = f"""You are a Verilog RTL expert reviewing a build log for "{design_name}".

BUILD LOG:
{log_summary}

Based on this build log, write a detailed chain-of-thought reasoning that explains:
1. What the design requirements were
2. What approach the RTL generator took
3. What errors occurred (if any) and why
4. What the correct implementation strategy should be
5. Key lessons for generating this type of design

Write your reasoning inside <think> tags. Be specific about Verilog/SystemVerilog best practices.
Focus on the WHY, not just the WHAT."""

    elif category == "rtl_fix":
        prompt = f"""You are a Verilog RTL expert reviewing an error-fix cycle for "{design_name}".

BUILD LOG:
{log_summary}

Based on this build log, write a chain-of-thought reasoning that explains:
1. What the original error was and its root cause
2. Why the initial code was wrong (specific Verilog/synthesis reason)
3. How the fix addresses the root cause
4. What pattern to recognize to avoid this error in future
5. Any remaining risks or edge cases

Write your reasoning inside <think> tags. Be very specific about the Verilog error patterns."""

    elif category == "error_classification":
        prompt = f"""You are a Verilog verification expert reviewing a simulation failure for "{design_name}".

BUILD LOG:
{log_summary}

Based on this build log, write a chain-of-thought reasoning that explains:
1. What type of failure this is (syntax, logic, timing, architectural)
2. How to diagnose this class of error systematically
3. What the root cause was
4. What the fix strategy should be
5. How to prevent this type of failure in future designs

Write your reasoning inside <think> tags."""

    else:
        prompt = f"""You are a Verilog expert reviewing a build process for "{design_name}".

BUILD LOG:
{log_summary}

Write a chain-of-thought reasoning about:
1. What happened in this build
2. What went well and what went wrong
3. What the correct approach should have been
4. Key lessons learned

Write your reasoning inside <think> tags."""

    response = ask_verireason(prompt)
    if not response:
        return None

    # Extract <think> content
    think_match = re.search(r"<think>([\s\S]*?)</think>", response)
    if think_match:
        return f"<think>\n{think_match.group(1).strip()}\n</think>"

    # If no <think> tags, wrap the whole response
    if len(response) > 50:
        return f"<think>\n{response[:1500].strip()}\n</think>"

    return None


def process_designs(input_file: str, output_file: str, designs_dir: str, max_pairs: int):
    """Main processing loop."""
    # Load existing training pairs
    pairs = []
    if os.path.exists(input_file):
        with open(input_file, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    pairs.append(json.loads(line))
        print(f"Loaded {len(pairs)} training pairs from {input_file}")
    else:
        print(f"No training data found at {input_file}")
        print("Run 'python3 training/collect_training_data.py' first!")
        return

    # Build a map of design → log files
    design_logs: Dict[str, str] = {}
    for entry in os.listdir(designs_dir):
        design_dir = os.path.join(designs_dir, entry)
        if os.path.isdir(design_dir):
            logs = glob.glob(os.path.join(design_dir, "*.log"))
            if logs:
                design_logs[entry] = logs[0]  # Use first log

    print(f"Found logs for {len(design_logs)} designs: {', '.join(design_logs.keys())}")

    enriched = []
    reasoning_count = 0

    for i, pair in enumerate(pairs[:max_pairs]):
        design = pair.get("design", "")
        category = pair.get("category", "")

        # Check if we have a log for this design
        if design in design_logs:
            print(f"  [{i+1}/{min(len(pairs), max_pairs)}] {design} ({category})...", end=" ", flush=True)

            log_summary = extract_log_summary(design_logs[design])
            reasoning = generate_reasoning_from_log(design, log_summary, category)

            if reasoning:
                enriched_pair = pair.copy()
                enriched_pair["output"] = reasoning + "\n" + pair["output"]
                enriched_pair["has_reasoning"] = True
                enriched_pair["reasoning_source"] = "build_log"
                enriched.append(enriched_pair)
                reasoning_count += 1
                print("✅")
            else:
                enriched.append(pair)
                print("⚠️ (kept without reasoning)")
        else:
            enriched.append(pair)

        time.sleep(0.5)

    # Write output
    os.makedirs(os.path.dirname(output_file) if os.path.dirname(output_file) else ".", exist_ok=True)
    with open(output_file, "w") as f:
        for pair in enriched:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    print(f"\n{'='*50}")
    print(f"Total pairs: {len(enriched)}")
    print(f"  With log-based reasoning: {reasoning_count}")
    print(f"  Without reasoning: {len(enriched) - reasoning_count}")
    print(f"Output: {output_file}")
    print(f"{'='*50}")
    print(f"\nNext: llamafactory-cli train training/agentic_sft_config.yaml")


def main():
    parser = argparse.ArgumentParser(description="Generate reasoning from build logs")
    parser.add_argument("--input", default="training/agentic_sft_data.jsonl")
    parser.add_argument("--output", default="training/agentic_sft_data_with_reasoning.jsonl")
    parser.add_argument("--designs-dir", default=f"{OPENLANE_ROOT}/designs")
    parser.add_argument("--max", type=int, default=100)
    parser.add_argument("--model", type=str, default=None)
    args = parser.parse_args()

    global VERIREASON_MODEL
    if args.model:
        VERIREASON_MODEL = args.model

    print("VeriReason Log-Based Reasoning Generator")
    print(f"  Model: {VERIREASON_MODEL}")
    print(f"  Ollama: {OLLAMA_URL}")
    print(f"  Designs: {args.designs_dir}")
    print()

    if not check_ollama():
        print("Error: Ollama is not running!")
        print("  Start it: ollama serve")
        return

    process_designs(args.input, args.output, args.designs_dir, args.max)


if __name__ == "__main__":
    main()
