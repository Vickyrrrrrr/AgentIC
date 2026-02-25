#!/usr/bin/env python3
"""
AgentIC → VeriReason Training Data Collector
=============================================
Automatically collects training data from AgentIC build logs.

Usage:
    python collect_training_data.py                    # scan all builds
    python collect_training_data.py --design my_chip   # scan specific design
    python collect_training_data.py --output data.jsonl # custom output path

The output is a JSONL file ready for SFT (Supervised Fine-Tuning) with
LLamaFactory or OpenR1.
"""

import argparse
import json
import os
import re
import glob
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Any


OPENLANE_ROOT = os.environ.get("OPENLANE_ROOT", os.path.expanduser("~/OpenLane"))


def extract_spec(log_text: str) -> str:
    """Extract the architecture spec from the log."""
    match = re.search(r"\[SPEC\] Architecture Plan Generated", log_text)
    if not match:
        return ""
    # Spec content is typically between SPEC and RTL_GEN transitions
    spec_section = re.search(
        r"Architecture Plan Generated([\s\S]*?)\[(?:SPEC|RTL_GEN)\].*Transitioning",
        log_text,
    )
    return spec_section.group(1).strip() if spec_section else ""


def extract_rtl_blocks(log_text: str) -> List[Dict[str, str]]:
    """Extract all RTL code blocks (generated + fixed versions)."""
    blocks = []
    # Match both GENERATED RTL and FIXED RTL
    pattern = re.compile(
        r"(GENERATED RTL|FIXED RTL).*?:\s*\n```(?:verilog|systemverilog)?\n([\s\S]*?)```",
        re.IGNORECASE,
    )
    for match in pattern.finditer(log_text):
        label = match.group(1).strip()
        code = match.group(2).strip()
        blocks.append({"type": label, "code": code})
    return blocks


def extract_testbench(log_text: str) -> str:
    """Extract the generated testbench code."""
    match = re.search(
        r"GENERATED TESTBENCH:\s*\n([\s\S]*?)(?:\n\d{4}-\d{2}-\d{2}|\Z)", log_text
    )
    return match.group(1).strip() if match else ""


def extract_errors(log_text: str) -> List[Dict[str, str]]:
    """Extract error logs with their context."""
    errors = []
    # Syntax/lint errors
    for match in re.finditer(
        r"SYNTAX/LINT ERRORS:\s*\n([\s\S]*?)(?:\n\d{4}-\d{2}-\d{2}|\Z)", log_text
    ):
        errors.append({"type": "syntax_lint", "content": match.group(1).strip()})

    # Simulation failures with diagnosis
    for match in re.finditer(
        r"\[VERIFICATION\] Diagnosis: CLASS=(\w)\s*\|\s*ROOT_CAUSE=(.*?)\s*\|\s*FIX_HINT=(.*?)$",
        log_text,
        re.MULTILINE,
    ):
        errors.append(
            {
                "type": "sim_diagnosis",
                "class": match.group(1),
                "root_cause": match.group(2).strip(),
                "fix_hint": match.group(3).strip(),
            }
        )
    return errors


def extract_final_status(log_text: str) -> str:
    """Extract final build status."""
    if "BUILD FAILED" in log_text:
        return "FAIL"
    if "SIGNOFF PASSED" in log_text or "[SUCCESS]" in log_text:
        return "PASS"
    return "UNKNOWN"


def build_sft_pairs(
    design_name: str,
    description: str,
    rtl_blocks: List[Dict[str, str]],
    errors: List[Dict[str, str]],
    testbench: str,
    spec: str,
) -> List[Dict[str, str]]:
    """Generate SFT training pairs from extracted data."""
    pairs = []

    # 1. Spec → RTL generation pair
    if spec and rtl_blocks:
        first_rtl = rtl_blocks[0]["code"]
        pairs.append(
            {
                "instruction": f"Generate synthesizable SystemVerilog RTL for: {description}",
                "input": f"ARCHITECTURE SPEC:\n{spec[:4000]}",
                "output": first_rtl,
                "category": "rtl_generation",
                "design": design_name,
            }
        )

    # 2. Error → Fix pairs (the gold mine for training)
    for i in range(len(rtl_blocks) - 1):
        if rtl_blocks[i]["type"] == "GENERATED RTL" or rtl_blocks[i]["type"] == "FIXED RTL":
            before = rtl_blocks[i]["code"]
            after = rtl_blocks[i + 1]["code"]
            # Find the error between these two versions
            relevant_error = ""
            if i < len(errors):
                relevant_error = json.dumps(errors[i], indent=2)

            if before != after:
                pairs.append(
                    {
                        "instruction": "Fix the following Verilog code based on the error report.",
                        "input": f"ERROR:\n{relevant_error}\n\nCODE:\n```verilog\n{before}\n```",
                        "output": after,
                        "category": "rtl_fix",
                        "design": design_name,
                    }
                )

    # 3. Error classification pairs
    for err in errors:
        if err["type"] == "sim_diagnosis":
            pairs.append(
                {
                    "instruction": "Classify this simulation failure and provide root cause analysis.",
                    "input": f"Simulation failed for design '{design_name}'.\nError details: {err.get('root_cause', '')}",
                    "output": f"CLASS: {err['class']}\nROOT_CAUSE: {err['root_cause']}\nFIX_HINT: {err['fix_hint']}",
                    "category": "error_classification",
                    "design": design_name,
                }
            )

    # 4. RTL → Testbench pair
    if rtl_blocks and testbench:
        final_rtl = rtl_blocks[-1]["code"]
        pairs.append(
            {
                "instruction": f"Generate a UVM-lite SystemVerilog testbench for the following RTL module.",
                "input": f"```verilog\n{final_rtl}\n```",
                "output": testbench,
                "category": "tb_generation",
                "design": design_name,
            }
        )

    return pairs


def process_design(design_dir: str) -> List[Dict[str, str]]:
    """Process a single design directory and extract training pairs."""
    design_name = os.path.basename(design_dir)
    log_files = glob.glob(os.path.join(design_dir, "*.log"))
    if not log_files:
        return []

    all_pairs = []
    for log_file in log_files:
        try:
            with open(log_file, "r") as f:
                log_text = f.read()
        except Exception:
            continue

        if len(log_text) < 100:
            continue

        # Extract description from log header
        desc_match = re.search(r"Description:\s*(.+?)$", log_text, re.MULTILINE)
        description = desc_match.group(1).strip() if desc_match else design_name

        spec = extract_spec(log_text)
        rtl_blocks = extract_rtl_blocks(log_text)
        testbench = extract_testbench(log_text)
        errors = extract_errors(log_text)
        status = extract_final_status(log_text)

        if not rtl_blocks:
            continue

        pairs = build_sft_pairs(design_name, description, rtl_blocks, errors, testbench, spec)

        # Tag with metadata
        for pair in pairs:
            pair["source_log"] = log_file
            pair["build_status"] = status
            pair["timestamp"] = datetime.now().isoformat()

        all_pairs.extend(pairs)

    return all_pairs


def main():
    parser = argparse.ArgumentParser(description="Collect VeriReason training data from AgentIC builds")
    parser.add_argument("--design", type=str, default=None, help="Process specific design only")
    parser.add_argument("--output", type=str, default="training/agentic_sft_data.jsonl", help="Output JSONL file")
    parser.add_argument("--designs-dir", type=str, default=f"{OPENLANE_ROOT}/designs", help="Designs directory")
    args = parser.parse_args()

    all_pairs: List[Dict[str, str]] = []

    if args.design:
        design_dir = os.path.join(args.designs_dir, args.design)
        if os.path.isdir(design_dir):
            all_pairs = process_design(design_dir)
        else:
            print(f"Design directory not found: {design_dir}")
            return
    else:
        # Process all designs
        for entry in sorted(os.listdir(args.designs_dir)):
            design_dir = os.path.join(args.designs_dir, entry)
            if os.path.isdir(design_dir):
                pairs = process_design(design_dir)
                all_pairs.extend(pairs)
                if pairs:
                    print(f"  {entry}: {len(pairs)} training pairs")

    # Write output
    os.makedirs(os.path.dirname(args.output) if os.path.dirname(args.output) else ".", exist_ok=True)
    with open(args.output, "w") as f:
        for pair in all_pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    # Summary
    categories = {}
    for p in all_pairs:
        cat = p.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1

    print(f"\n{'='*50}")
    print(f"Total training pairs: {len(all_pairs)}")
    print(f"Output: {args.output}")
    print(f"Categories:")
    for cat, count in sorted(categories.items()):
        print(f"  {cat}: {count}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
