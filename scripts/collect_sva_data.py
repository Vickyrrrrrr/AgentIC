#!/usr/bin/env python3
"""
collect_sva_data.py — Extract SVA/formal data from open-source HW repos.
Handles both raw SVA and macro-wrapped assertions (OpenTitan style).
Uses direct GitHub raw URLs for speed.

Usage:
    python3 collect_sva_data.py
Output: sva_train.jsonl
"""

import os
import re
import json
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Dict, Tuple

OUTPUT_FILE = "sva_train.jsonl"
GITHUB_RAW = "https://raw.githubusercontent.com"

# ── Curated direct URLs ─────────────────────────────────────────────────────
# Format: (repo_context, module_hint, raw_url)
FILE_URLS: List[Tuple[str, str, str]] = [
    # OpenTitan — assertion macros + RTL
    ("opentitan", "uart",         f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/uart/rtl/uart.sv"),
    ("opentitan", "gpio",         f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/gpio/rtl/gpio.sv"),
    ("opentitan", "prim_assert",  f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/prim/rtl/prim_assert.sv"),
    ("opentitan", "prim_subreg",  f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/prim/rtl/prim_subreg.sv"),
    ("opentitan", "rv_timer",     f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/rv_timer/rtl/rv_timer.sv"),
    ("opentitan", "spi_device",   f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/spi_device/rtl/spi_device.sv"),
    ("opentitan", "i2c",          f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/i2c/rtl/i2c.sv"),
    ("opentitan", "pwm",          f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/pwm/rtl/pwm.sv"),
    # PicoRV32
    ("picorv32", "picorv32",      f"{GITHUB_RAW}/YosysHQ/picorv32/master/picorv32.v"),
    # SERV
    ("serv", "serv_top",          f"{GITHUB_RAW}/olofk/serv/master/rtl/serv_top.v"),
    ("serv", "serv_mem_if",       f"{GITHUB_RAW}/olofk/serv/master/rtl/serv_mem_if.v"),
    ("serv", "serv_alu",          f"{GITHUB_RAW}/olofk/serv/master/rtl/serv_alu.v"),
    # Ibex
    ("ibex", "ibex_core",         f"{GITHUB_RAW}/lowRISC/ibex/master/rtl/ibex_core.sv"),
    ("ibex", "ibex_id_stage",     f"{GITHUB_RAW}/lowRISC/ibex/master/rtl/ibex_id_stage.sv"),
    ("ibex", "ibex_controller",   f"{GITHUB_RAW}/lowRISC/ibex/master/rtl/ibex_controller.sv"),
    # CVA6
    ("cva6", "cva6",              f"{GITHUB_RAW}/openhwgroup/cva6/master/core/cva6.sv"),
    # BlackParrot
    ("blackparrot", "bp_be_checker", f"{GITHUB_RAW}/black-parrot/black-parrot/master/bp_be/src/v/bp_be_checker/bp_be_checker_top.sv"),
    # More OpenTitan IPs
    ("opentitan", "hmac",          f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/hmac/rtl/hmac.sv"),
    ("opentitan", "aes",           f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/aes/rtl/aes.sv"),
    ("opentitan", "entropy_src",   f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/entropy_src/rtl/entropy_src.sv"),
    ("opentitan", "csrng",         f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/csrng/rtl/csrng.sv"),
    ("opentitan", "edn",           f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/edn/rtl/edn.sv"),
    ("opentitan", "keymgr",        f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/keymgr/rtl/keymgr.sv"),
    ("opentitan", "otp_ctrl",      f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/otp_ctrl/rtl/otp_ctrl.sv"),
    ("opentitan", "lc_ctrl",       f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/lc_ctrl/rtl/lc_ctrl.sv"),
    ("opentitan", "clkmgr",        f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/clkmgr/rtl/clkmgr.sv"),
    ("opentitan", "rstmgr",        f"{GITHUB_RAW}/lowRISC/opentitan/master/hw/ip/rstmgr/rtl/rstmgr.sv"),
]

SYSTEM_PROMPT = (
    "You are a formal verification engineer. "
    "Generate only SystemVerilog Assertions (SVA), assertion macros, covergroups, or formal properties. "
    "Never explain in prose. Output must be valid SystemVerilog."
)

# ── Network ─────────────────────────────────────────────────────────────────

def fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "agentic-sva/1.0")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"  [warn] HTTP {e.code}: {url}")
        return ""
    except Exception as e:
        print(f"  [warn] {url}: {e}")
        return ""


# ── Extraction patterns ─────────────────────────────────────────────────────

# Raw SVA
PATTERNS = [
    # assert/assume/cover property(...);
    re.compile(
        r'(\b(?:assert|assume|cover)\s+property\s*\([^;]+?\);)',
        re.DOTALL | re.IGNORECASE,
    ),
    # property ... endproperty
    re.compile(
        r'(\bproperty\s+\w+\s*\([^)]*\)\s*;.*?\bendproperty\s*)',
        re.DOTALL | re.IGNORECASE,
    ),
    # sequence ... endsequence
    re.compile(
        r'(\bsequence\s+\w+\s*\([^)]*\)\s*;.*?\bendsequence\s*)',
        re.DOTALL | re.IGNORECASE,
    ),
    # covergroup ... endgroup
    re.compile(
        r'(\bcovergroup\s+\w+\s*[@(].*?\bendgroup\s*)',
        re.DOTALL | re.IGNORECASE,
    ),
    # assert(...)
    re.compile(
        r'(\bassert\s*\([^;)]+\)\s*;)',
        re.DOTALL | re.IGNORECASE,
    ),
    # OpenTitan macro assertions: ASSERT(.*), ASSERT_KNOWN(.*), etc.
    re.compile(
        r'(`ASSERT\w*\s*\([^)]+\))',
        re.DOTALL,
    ),
    # Other common macros
    re.compile(
        r'(`ASSUME\w*\s*\([^)]+\))',
        re.DOTALL,
    ),
    re.compile(
        r'(`COVER\w*\s*\([^)]+\))',
        re.DOTALL,
    ),
]


def extract(content: str, min_lines: int = 1) -> List[str]:
    blocks = []
    for pat in PATTERNS:
        for m in pat.finditer(content):
            blk = m.group(1).strip()
            if len(blk.splitlines()) >= min_lines:
                blocks.append(blk)
    return blocks


def make_prompt(repo: str, module: str, block: str) -> str:
    prop = re.search(r'\b(?:property|covergroup|sequence|ASSERT)\s+(\w+)', block, re.I)
    prop_name = prop.group(1) if prop else "unnamed"
    return (
        f"Module: {module}\n"
        f"Property: {prop_name}\n"
        f"Context: Formal verification for {repo} IP block.\n"
        f"Write the complete SystemVerilog assertion/covergroup code."
    )


def to_chat(user: str, code: str) -> Dict:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
            {"role": "assistant", "content": f"```systemverilog\n{code}\n```"},
        ]
    }


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("SVA Collector v2")
    print("=" * 60)

    raw: List[Tuple[str, str, str]] = []

    for repo, module, url in FILE_URLS:
        print(f"  {repo}/{module} …", end=" ")
        content = fetch(url)
        if not content:
            print("SKIP")
            continue
        blocks = extract(content)
        print(f"{len(blocks)} blocks")
        for blk in blocks:
            raw.append((repo, module, blk))

    # dedup
    seen = set()
    unique = []
    for r, m, b in raw:
        h = hash(b)
        if h not in seen:
            seen.add(h)
            unique.append((r, m, b))

    print(f"\n[+] Unique blocks: {len(unique)}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for repo, module, blk in unique:
            user = make_prompt(repo, module, blk)
            ex = to_chat(user, blk)
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    size = os.path.getsize(OUTPUT_FILE) / 1024
    print(f"[+] Wrote {OUTPUT_FILE}  ({size:.1f} KB, {len(unique)} examples)")

    if len(unique) < 50:
        print(f"\n[!] Only {len(unique)} examples. Add more FILE_URLS.")
    else:
        print("\n[+] Done.")


if __name__ == "__main__":
    main()
