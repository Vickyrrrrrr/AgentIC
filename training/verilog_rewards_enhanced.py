#!/usr/bin/env python3
"""
Enhanced GRPO Reward Function for VeriReason
=============================================
This replaces the original verilog_rewards_tb.py with additional
reward signals from Verilator lint checking.

Original VeriReason reward: testbench pass/fail only (binary)
Enhanced reward: testbench + lint + structural quality checks

Usage with GRPO training:
    Copy this file to src/open-r1/ in the OpenR1 directory, then
    reference it in your GRPO training config.

Reward breakdown:
    +0.4  Testbench simulation passes
    +0.2  Verilator lint clean (no warnings)
    +0.1  Syntax compiles without errors
    +0.1  No multi-driven signals (MULTIDRIVEN)
    +0.1  Proper reset initialization (all registers reset)
    +0.05 Uses always_ff/always_comb (not legacy always)
    +0.05 No width mismatches
    -0.5  Testbench fails
    -0.3  Syntax error (doesn't compile)
    -0.2  Contains X/Z propagation issues
"""

import subprocess
import tempfile
import os
import re
from typing import Dict, Tuple


def extract_verilog_from_response(response: str) -> str:
    """Extract Verilog code from LLM response, handling markdown blocks."""
    # Try to extract from code blocks
    match = re.search(
        r"```(?:verilog|systemverilog|sv)?\s*\n([\s\S]*?)```", response
    )
    if match:
        return match.group(1).strip()

    # If no code block, try to find module..endmodule
    match = re.search(r"(module\s+\w+[\s\S]*?endmodule)", response)
    if match:
        return match.group(1).strip()

    return response.strip()


def run_verilator_syntax(verilog_code: str, work_dir: str) -> Tuple[bool, str]:
    """Run Verilator syntax check. Returns (passed, output)."""
    src_path = os.path.join(work_dir, "dut.sv")
    with open(src_path, "w") as f:
        f.write(verilog_code)

    try:
        result = subprocess.run(
            ["verilator", "--lint-only", "--sv", "--timing", src_path],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout + result.stderr
        passed = result.returncode == 0
        return passed, output
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, "verilator not available or timeout"


def run_verilator_lint(verilog_code: str, work_dir: str) -> Tuple[bool, str, Dict[str, int]]:
    """Run Verilator lint check with detailed warning analysis."""
    src_path = os.path.join(work_dir, "dut.sv")
    with open(src_path, "w") as f:
        f.write(verilog_code)

    try:
        result = subprocess.run(
            ["verilator", "--lint-only", "--sv", "--timing", "-Wall", src_path],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout + result.stderr

        # Count warnings by type
        warnings: Dict[str, int] = {}
        for match in re.finditer(r"%Warning-(\w+):", output):
            wtype = match.group(1)
            warnings[wtype] = warnings.get(wtype, 0) + 1

        lint_clean = len(warnings) == 0 and result.returncode == 0
        return lint_clean, output, warnings
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, "verilator not available or timeout", {}


def check_structural_quality(verilog_code: str) -> Dict[str, float]:
    """Check structural code quality. Returns individual reward components."""
    rewards = {}

    # 1. Uses modern always_ff/always_comb instead of legacy always
    has_modern = bool(re.search(r'\balways_ff\b|\balways_comb\b', verilog_code))
    has_legacy = bool(re.search(r'\balways\s*@', verilog_code))
    if has_modern and not has_legacy:
        rewards["modern_sv"] = 0.05
    elif has_modern:
        rewards["modern_sv"] = 0.025  # partial credit
    else:
        rewards["modern_sv"] = 0.0

    # 2. All registers have reset initialization
    ff_blocks = re.findall(r'always_ff\s*@\s*\(.*?\)\s*begin([\s\S]*?)end', verilog_code)
    if ff_blocks:
        all_have_reset = all("rst" in block.lower() or "reset" in block.lower() for block in ff_blocks)
        rewards["reset_init"] = 0.1 if all_have_reset else 0.0
    else:
        # Check legacy always blocks for reset
        always_blocks = re.findall(r'always\s*@\s*\(.*?\)\s*begin([\s\S]*?)end', verilog_code)
        if always_blocks:
            all_have_reset = all("rst" in block.lower() or "reset" in block.lower() for block in always_blocks)
            rewards["reset_init"] = 0.1 if all_have_reset else 0.0
        else:
            rewards["reset_init"] = 0.0

    # 3. No X/Z literal usage (2-state safe for Verilator)
    xz_usage = len(re.findall(r"\b[0-9]+'[bBoOhHdD].*[xXzZ]", verilog_code))
    rewards["no_xz"] = 0.05 if xz_usage == 0 else -0.1

    # 4. Has proper module header with types
    has_typed_ports = bool(re.search(r'\b(input|output)\s+(logic|wire|reg)\b', verilog_code))
    rewards["typed_ports"] = 0.025 if has_typed_ports else 0.0

    return rewards


def run_simulation(
    verilog_code: str, testbench_code: str, work_dir: str
) -> Tuple[bool, str]:
    """Compile and simulate with Verilator or iverilog. Returns (passed, output)."""
    rtl_path = os.path.join(work_dir, "dut.sv")
    tb_path = os.path.join(work_dir, "tb.sv")

    with open(rtl_path, "w") as f:
        f.write(verilog_code)
    with open(tb_path, "w") as f:
        f.write(testbench_code)

    # Try iverilog first (more lenient with SV features)
    try:
        compile_result = subprocess.run(
            ["iverilog", "-g2012", "-o", os.path.join(work_dir, "sim"), rtl_path, tb_path],
            capture_output=True, text=True, timeout=30,
        )
        if compile_result.returncode != 0:
            return False, f"Compile error: {compile_result.stderr}"

        sim_result = subprocess.run(
            ["vvp", os.path.join(work_dir, "sim")],
            capture_output=True, text=True, timeout=60,
        )
        output = sim_result.stdout + sim_result.stderr
        passed = "TEST PASSED" in output and "TEST FAILED" not in output
        return passed, output
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, "simulation timeout or tools not found"


def compute_reward(
    response: str,
    testbench_code: str = "",
    reference_code: str = "",
) -> float:
    """
    Compute the total reward for a generated Verilog response.

    This is the main function called by the GRPO training loop.

    Args:
        response: The LLM's generated response (may contain markdown)
        testbench_code: Optional testbench for simulation testing
        reference_code: Optional reference implementation for comparison

    Returns:
        float: Reward score between -1.0 and 1.0
    """
    verilog_code = extract_verilog_from_response(response)

    if not verilog_code or "module" not in verilog_code:
        return -0.5  # No valid Verilog generated

    total_reward = 0.0

    with tempfile.TemporaryDirectory(prefix="verireason_") as work_dir:
        # 1. Syntax check (+0.1)
        syntax_ok, syntax_output = run_verilator_syntax(verilog_code, work_dir)
        if syntax_ok:
            total_reward += 0.1
        else:
            total_reward -= 0.3
            return total_reward  # No point continuing if syntax fails

        # 2. Lint check (+0.2)
        lint_ok, lint_output, warnings = run_verilator_lint(verilog_code, work_dir)
        if lint_ok:
            total_reward += 0.2
        else:
            # Partial penalties for specific warning types
            if "MULTIDRIVEN" in warnings:
                total_reward -= 0.1  # Severe: multi-driven signals
            if "WIDTH" in warnings:
                total_reward -= 0.05  # Width mismatch
            if warnings and "MULTIDRIVEN" not in warnings and "WIDTH" not in warnings:
                total_reward += 0.1  # Minor warnings = partial credit

        # 3. Structural quality checks (+0.2 max)
        quality_rewards = check_structural_quality(verilog_code)
        total_reward += sum(quality_rewards.values())

        # 4. Simulation test (+0.4 / -0.5)
        if testbench_code:
            sim_passed, sim_output = run_simulation(verilog_code, testbench_code, work_dir)
            if sim_passed:
                total_reward += 0.4
            else:
                total_reward -= 0.5
                # Check for X/Z issues specifically
                if "X/Z detected" in sim_output:
                    total_reward -= 0.2

    # Clamp to [-1.0, 1.0]
    return max(-1.0, min(1.0, total_reward))


# ─── Batch interface for GRPO training ──────────────────────────────

def compute_rewards_batch(
    responses: list,
    testbenches: list = None,
    references: list = None,
) -> list:
    """
    Batch reward computation for GRPO training.

    Args:
        responses: List of LLM responses
        testbenches: Optional list of testbench codes (parallel to responses)
        references: Optional list of reference codes

    Returns:
        List of reward floats
    """
    if testbenches is None:
        testbenches = [""] * len(responses)
    if references is None:
        references = [""] * len(responses)

    rewards = []
    for resp, tb, ref in zip(responses, testbenches, references):
        try:
            r = compute_reward(resp, tb, ref)
        except Exception:
            r = -0.5  # Fail-safe
        rewards.append(r)
    return rewards


if __name__ == "__main__":
    # Quick test
    test_code = """
module counter #(parameter WIDTH = 8) (
    input  logic clk,
    input  logic rst_n,
    input  logic en,
    output logic [WIDTH-1:0] cnt
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            cnt <= '0;
        else if (en)
            cnt <= cnt + 1'b1;
    end
endmodule
"""
    print(f"Reward for clean counter: {compute_reward(test_code):.3f}")

    bad_code = """
module counter(input clk, output reg [7:0] cnt);
    always @(posedge clk) cnt <= cnt + 1;
    always @(negedge clk) cnt <= 0;  // MULTIDRIVEN!
endmodule
"""
    print(f"Reward for bad counter:   {compute_reward(bad_code):.3f}")
