<div align="center">

<br/>

<!-- ──────────────── WORDMARK ──────────────── -->
<h1>
  <img src="https://readme-typing-svg.demolab.com?font=Fira+Code&weight=700&size=42&pause=1000&color=C9643E&center=true&vCenter=true&width=600&lines=AgentIC" alt="AgentIC" />
</h1>

<h3><em>Describe a chip. Get silicon.</em></h3>

<br/>

<!-- ──────────────── BRAND BADGES ──────────────── -->

![](https://img.shields.io/badge/Autonomous%20Silicon%20Compiler-informational?style=for-the-badge&labelColor=1C1A17&color=C9643E)
![](https://img.shields.io/badge/All%205%20Core%20Modules%20Active-success?style=for-the-badge&labelColor=1C1A17&color=3A7856)
![](https://img.shields.io/badge/Fail--Closed%20by%20Default-critical?style=for-the-badge&labelColor=1C1A17&color=B83030)

<br/>

![](https://img.shields.io/badge/Python-3.10%2B-C9643E?style=flat-square&logo=python&logoColor=white&labelColor=1C1A17)
![](https://img.shields.io/badge/PDK-Sky130%20%7C%20GF180-C9643E?style=flat-square&labelColor=1C1A17)
![](https://img.shields.io/badge/Formal%20Verification-SymbiYosys-C9643E?style=flat-square&labelColor=1C1A17)
![](https://img.shields.io/badge/Physical%20Flow-OpenLane-C9643E?style=flat-square&labelColor=1C1A17)
![](https://img.shields.io/badge/License-Proprietary-A67828?style=flat-square&labelColor=1C1A17)

<br/><br/>

<table>
<tr>
<td align="center" width="220"><b>Natural Language In ⟶</b></td>
<td align="center" width="60">→</td>
<td align="center" width="220"><b>⟶ Verified RTL + GDS Out</b></td>
</tr>
</table>

<br/>

</div>

---

## What is AgentIC?

AgentIC is a **fully autonomous hardware compiler** that takes a plain-English description of a digital circuit and produces a complete, verified, physically-implemented chip design — with no human in the loop unless you want one.

It is not a code-generation copilot. It is not a template filler. It is an **end-to-end autonomous agent system** that reasons, verifies, debugs, repairs, and re-verifies until the design meets every quality gate — then hands you the GDS.

> *"You wrote the spec. We wrote the chip."*

---

## The Problem It Solves

Traditional hardware design has two unavoidable costs:

| Problem | Industry Reality | AgentIC's Answer |
|---------|-----------------|-----------------|
| **Iteration time** | Hours per RTL-to-sim cycle | Fully automated multi-stage pipeline |
| **Silent bugs** | Weak checks ship bad silicon | Every gate is fail-closed — if it cannot prove correctness, it does not proceed |
| **Expert bottleneck** | Needs senior RTL + verification + physical engineers | One prompt, autonomous resolution |
| **Infinite churn** | Teams retry the same broken strategy | Loop budgets, loop-identity detection, and strategy pivots are baked in |

---

<div align="center">

## Pipeline at a Glance

</div>

```
Your Prompt
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│                        AGENTRIC PIPELINE                         │
│                                                                  │
│  ① Specification     →  Structured design contract              │
│  ② RTL Generation    →  Architecture-aware Verilog              │
│  ③ RTL Hardening     →  Iterative syntax · lint · semantic fix  │
│  ④ Verification      →  Testbench compile · simulation          │
│  ⑤ Formal Proof      →  SVA assertions · SymbiYosys bounded MC  │
│  ⑥ Coverage          →  Profile-driven closure · anti-regression│
│  ⑦ Regression        →  Directed corner-case execution          │
│  ⑧ Physical Flow     →  Floorplan · Hardening · Convergence     │
│  ⑨ Signoff           →  DRC · LVS · STA · Power · IR · LEC     │
│                                                                  │
│              SUCCESS  ──────────────────── GDS + Reports        │
└─────────────────────────────────────────────────────────────────┘
```

Every transition between stages is gated. Nothing proceeds until the previous stage passes cleanly.

---

## Five Core Intelligence Modules

AgentIC's reasoning layer is built on five proprietary modules — all active in the current production pipeline.

<br/>

<table>
<thead>
<tr>
<th width="220">Module</th>
<th>Capability</th>
<th width="200">When It Activates</th>
</tr>
</thead>
<tbody>
<tr>
<td><b>Specification Architect</b></td>
<td>Converts ambiguous prose into a precise, structured design contract shared by all downstream agents — preventing conflicting interpretations before a single line of RTL is written</td>
<td>Before RTL generation</td>
</tr>
<tr>
<td><b>Iterative Reasoning Agent</b></td>
<td>Applies a multi-step Think → Act → Observe loop to reason through RTL issues before committing any repair, reducing unnecessary edits and preserving design intent</td>
<td>Every RTL repair cycle</td>
</tr>
<tr>
<td><b>Waveform Intelligence</b></td>
<td>Reads simulation waveforms and traces every incorrectly-driven output back to the exact RTL construct responsible — giving the repair layer deterministic evidence instead of inference</td>
<td>On any simulation failure</td>
</tr>
<tr>
<td><b>Formal Causal Debugger</b></td>
<td>Builds a signal causality graph from the failing formal property, applies balanced for-and-against analysis, and returns the root-cause signal, source line, and a confidence score</td>
<td>On any formal failure</td>
</tr>
<tr>
<td><b>Self-Reflection Recovery</b></td>
<td>Categorises physical implementation failures, reflects on convergence history, proposes corrective actions, applies them, and tracks whether metrics are improving or stagnating before retrying</td>
<td>On any hardening failure</td>
</tr>
</tbody>
</table>

<br/>

> **Intellectual Property Notice** — The internal algorithms, decision logic, prompt architecture, repair heuristics, scoring mechanisms, and module interfaces are proprietary and confidential. The table above describes *capabilities*, not *implementations*. No part of AgentIC's core reasoning design is disclosed in this document.

---

## Quality Architecture

AgentIC is engineered around one principle: **trust nothing, verify everything.**

### Fail-Closed Gates

Every stage either **passes** or **halts with a diagnosis**. There is no silent forwarding of a broken artifact.

```
  RTL FIX ──► [Syntax Gate] ──► [Lint Gate] ──► [Semantic Gate] ──► Next Stage
                   │                 │                  │
                 FAIL              FAIL              FAIL
                   └─────────────────┴──────────────────┘
                                     │
                             Repair Loop (budgeted)
                                     │
                         [Loop-Identity Guard] ◄── prevents infinite churn
```

### Layered Autonomous Repair

At every gate, the system applies repair in strict priority order:

1. **Deterministic pass** — machine-precise corrections with zero LLM involvement
2. **Reasoned pass** — multi-step agentic reasoning with hardware-specific tool use
3. **Generative pass** — LLM-guided surgical correction, minimum diff enforced
4. **Strategy pivot** — if all passes exhaust their budget, the build fails closed; it does not ship a broken artifact

### Loop Safety

Every repair loop has a hard step budget. Identical repeated artifacts are detected and rejected before an LLM call is made. If the system cannot demonstrate measurable forward progress, it escalates to fail-closed rather than spinning.

---

## Multi-Agent Collaboration

The generative layer is a collaborative crew of specialised AI agents, each scoped to a single responsibility and equipped with hardware-specific tooling:

| Agent Role | Responsibility |
|------------|---------------|
| **RTL Designer** | Architecture-aware Verilog generation with pre-submission self-verification |
| **Testbench Designer** | Simulator-safe testbenches with stimulus integrity guarantees |
| **Failure Analyst** | Signal-level diagnosis — always cites specific RTL line, construct, and expected vs. actual values |
| **Verification Engineer** | SVA assertion generation tuned for the open-source formal toolchain |
| **Regression Architect** | Directed corner-case scenario planning |
| **Physical Constraints** | SDC timing constraint synthesis |
| **Documentation** | Design specification and IP declaration |

All agents operate from the structured design contract established at the Specification stage — eliminating the divergence that occurs when different agents interpret the same prose spec independently.

---

## Human-in-the-Loop Web Interface

<div align="center">

![](https://img.shields.io/badge/Real--time%20SSE%20Streaming-C9643E?style=for-the-badge&labelColor=1C1A17)
![](https://img.shields.io/badge/Approval%20Gates-C9643E?style=for-the-badge&labelColor=1C1A17)
![](https://img.shields.io/badge/Live%20Agent%20Reasoning%20View-C9643E?style=for-the-badge&labelColor=1C1A17)

</div>

AgentIC ships with a production-grade web application (React 19 + Vite frontend, FastAPI backend). Every pipeline event streams to the UI in real time. Three build modes are available:

| Mode | Description |
|------|-------------|
| **Autonomous** | Zero human checkpoints — fully hands-off |
| **Supervised** | Pause and approve at user-defined stages |
| **Interactive** | Full step-by-step control with per-decision approval |

Design artifacts, agent reasoning steps, signal traces, formal proof results, and physical convergence metrics are all visible in the interface as they are produced.

---

## Signoff Coverage

| Domain | What Is Verified |
|--------|-----------------|
| **Functional** | Simulation correctness across all generated and directed stimuli |
| **Formal** | Bounded model checking with SVA property coverage |
| **Structural** | DRC — design rule compliance for the target PDK |
| **Physical** | LVS — layout-versus-schematic equivalence |
| **Timing** | Multi-corner STA — setup and hold across all paths |
| **Power** | Peak and average power estimation |
| **IR Drop** | Supply integrity validation |
| **Equivalence** | LEC — RTL-to-GDS logical equivalence |

---

## Getting Started

### Prerequisites

```bash
# Core
Python 3.10+, Verilator 5.x, Icarus Verilog (iverilog + vvp)

# Formal verification
oss-cad-suite  — provides sby, yosys, eqy

# Physical implementation (optional, skip with --skip-openlane)
OpenLane + Docker
```

### Install

```bash
git clone https://github.com/Vickyrrrrrr/AgentIC.git
cd AgentIC
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Configure `.env`

```bash
# LLM backend — cloud
NVIDIA_API_KEY="your-key-here"

# LLM backend — local
LLM_BASE_URL="http://localhost:11434"

# Physical flow roots (only needed for --full-signoff builds)
OPENLANE_ROOT="/path/to/OpenLane"
PDK_ROOT="/path/to/pdk"
```

---

## CLI Reference

```bash
# Functional verification only — fast, no physical tools needed
python3 main.py build \
  --name my_design \
  --desc "32-bit APB timer with interrupt" \
  --skip-openlane

# Full build through physical signoff
python3 main.py build \
  --name my_design \
  --desc "32-bit APB timer with interrupt" \
  --full-signoff \
  --pdk-profile sky130

# Exploration mode — relaxed gates
python3 main.py build \
  --name my_design \
  --desc "32-bit APB timer with interrupt" \
  --skip-openlane \
  --no-strict-gates
```

### All build flags

```
--strict-gates / --no-strict-gates   Enforce all quality gates (default: strict)
--skip-openlane                      Stop after formal/coverage signoff
--pdk-profile {sky130, gf180}        Target PDK (default: sky130)
--full-signoff                       Run full DRC/LVS/STA/Power/LEC suite
--max-retries N                      Per-stage LLM repair budget
--min-coverage N                     Coverage closure threshold (%)
--max-pivots N                       Physical flow strategy pivot limit
--congestion-threshold FLOAT         Routing congestion abort threshold
--hierarchical {auto, off, on}       Hierarchical flow mode
```

---

## Generated Artifacts

Every completed build produces a full artifact set:

```
designs/<name>/
├── src/
│   ├── <name>.v              # Production RTL
│   ├── <name>_tb.v           # Verified testbench
│   ├── <name>_sva.sv         # SVA property suite
│   └── <name>.sdc            # Timing constraints
├── formal/
│   └── <name>.sby            # SymbiYosys config + results
├── <name>.eqy                # Equivalence check config
├── <name>_eco_patch.tcl      # ECO patch (when signoff required it)
├── config.tcl                # OpenLane configuration
├── macro_placement.tcl       # Floorplan
└── ip_manifest.json          # IP declaration manifest

metircs/<name>/
├── latest.json               # Machine-readable benchmark snapshot
└── latest.md                 # Human-readable signoff report
```

---

## CI

```bash
# PR gate — syntax + unit tests (fast, ~2 min)
bash scripts/ci/smoke.sh

# Nightly — full build + signoff
bash scripts/ci/nightly_full.sh
```

Workflow definition: `.github/workflows/ci.yml`

---

## Supported PDKs

| PDK | Process Node | Status |
|-----|-------------|--------|
| SkyWater Sky130 | 130 nm | Production |
| GlobalFoundries GF180MCU | 180 nm | Production |

---

## Practical Scope

AgentIC is designed for **OSS PDK prototype tape-out and research-grade autonomous hardware design**. It is not a replacement for a certified commercial foundry sign-off flow. ECO and hierarchical flows produce concrete, functional artifacts but are not tuned for production process corners.

---

<div align="center">

## Design Philosophy

<br/>

> *The system is only as trustworthy as its most lenient gate.*
>
> Every component is designed to fail loudly, repair precisely,
> and proceed only when correctness is demonstrated — not assumed.

<br/>

| Principle | What It Means in Practice |
|-----------|--------------------------|
| **Fail closed** | No stage silently degrades quality |
| **Minimum diff** | Repairs change the least possible — intent is preserved |
| **Bounded loops** | Every retry has a hard budget |
| **Determinism first** | Machine-precise fixes are always attempted before LLM fixes |
| **Evidence-driven** | Every diagnosis cites signal names and line numbers — never guesses |

</div>

---

## License

**Proprietary and Confidential.**

Copyright © 2026 Vicky Nishad. All rights reserved.

This software, its architecture, algorithms, agent designs, internal logic, prompt methodologies, repair heuristics, and all associated intellectual property are the exclusive property of the author. No part of this system — in whole or in part — may be reproduced, decompiled, reverse-engineered, distributed, sublicensed, or used in any derivative work without explicit written permission from the copyright holder.

Unauthorised use is a violation of applicable intellectual property law.

---

<div align="center">

<br/>

![](https://img.shields.io/badge/Built%20with%20intention-C9643E?style=for-the-badge&labelColor=1C1A17)
![](https://img.shields.io/badge/Designed%20to%20last-C9643E?style=for-the-badge&labelColor=1C1A17)

<br/>

*AgentIC — from words to wafers.*

<br/>

</div>
