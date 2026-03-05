# AgentIC

AgentIC is an autonomous digital design pipeline that takes a natural-language chip specification and drives it through RTL generation, verification, formal checks, coverage, regression, and optional physical implementation. The system is built as a gated flow around standard EDA tools and AI-assisted generation and debugging.

This README is written for both technical readers and non-specialists. It explains what the system does, what the build stages mean, and what the repository contains, without exposing internal proprietary logic.

## What AgentIC Actually Does

At a high level, AgentIC performs four jobs:

1. Turn an ambiguous prose specification into a structured hardware task.
2. Generate candidate RTL, testbenches, properties, and constraints.
3. Push those artifacts through quality gates with controlled repair loops.
4. Stop only when the design either passes the configured flow or fails with a concrete diagnosis.

The system is not a simple code generator. It is a build-and-check pipeline that keeps testing what it generates.

## System Model

AgentIC is organized around three layers:

### 1. Orchestration Layer

The orchestrator owns stage transitions, retry budgets, artifact routing, and failure handling. It is the source of truth for:

- which stage runs next
- what inputs each stage is allowed to consume
- what counts as pass, fail, skip, or tool error
- how many retries are allowed
- when the build must halt instead of spinning

Core implementation: [orchestrator.py](/home/vickynishad/AgentIC/src/agentic/orchestrator.py)

### 2. Tool Layer

EDA tool wrappers execute Verilator, Icarus Verilog, Yosys, and SymbiYosys. Intermediate work is staged in temporary directories; human-relevant artifacts remain in the design tree.

Core implementation: [vlsi_tools.py](/home/vickynishad/AgentIC/src/agentic/tools/vlsi_tools.py)

### 3. Agent Layer

Specialist AI components assist specific tasks such as RTL generation, testbench creation, failure analysis, and formal-property generation. These components do not bypass the tool checks; their output must still pass the relevant stage gates.

## End-to-End Pipeline

The full build pipeline is:

```text
Specification
  -> RTL_GEN
  -> RTL_FIX
  -> VERIFICATION
  -> FORMAL_VERIFY
  -> COVERAGE_CHECK
  -> REGRESSION
  -> HARDENING
  -> CONVERGENCE
  -> SIGNOFF
```

Each stage is gated. A stage can only advance if its required checks pass. There is no silent forwarding of a broken artifact.

### Stage Intent

| Stage | Purpose | Typical Outputs |
|------|---------|-----------------|
| `SPECIFICATION` | Normalize the user prompt into a design contract | structured prompt context |
| `RTL_GEN` | Generate initial RTL and supporting files | `<name>.v`, supporting metadata |
| `RTL_FIX` | Enforce syntax, lint, and semantic rigor; repair failures | corrected RTL, diagnostics |
| `VERIFICATION` | Build TB, run simulation, analyze functional failures | `<name>_tb.v`, sim logs, VCD |
| `FORMAL_VERIFY` | Generate SVA, preflight it, run formal checks | `<name>_sva.sv`, formal summaries |
| `COVERAGE_CHECK` | Improve or validate coverage after functional success | coverage metrics, coverage JSON |
| `REGRESSION` | Run directed corner-case validation | regression results |
| `HARDENING` | Invoke OpenLane flow if enabled | layout flow outputs |
| `CONVERGENCE` | Recover from physical-flow failures | updated configs or constraints |
| `SIGNOFF` | Run DRC/LVS/STA/power/equivalence style checks | signoff reports |

## Reliability Model

AgentIC is designed around one rule: every stage must provide enough evidence to justify the next stage.

### Fail-Closed By Default

If syntax, lint, simulation, formal checks, or physical checks fail, the build does not continue unless a replacement artifact passes the relevant gate.

### Deterministic Before Generative

Repair is layered:

1. deterministic cleanup or mechanical fix
2. targeted analysis
3. constrained regeneration
4. strategy pivot or fail-closed halt

This ordering matters. The system tries to preserve design intent and minimize uncontrolled rewrites.

### Budgeted Loops

Each repair path is budgeted. The system tracks repeated failures and retry exhaustion to avoid infinite churn.

## Core Reasoning Components

AgentIC uses multiple specialized components rather than one undifferentiated "agent".

These components are used for tasks such as:

- RTL generation
- testbench generation
- SVA generation
- constraint generation
- documentation

These outputs are still stage-gated. AgentIC does not assume that generated code is valid just because an AI model produced it.

## How Reliability Is Managed

Reliability work in AgentIC focuses on three practical areas:

- clear stage boundaries
- explicit artifact passing between stages
- replayable testing from benchmark failures

The goal is to make failures diagnosable and repeatable, not to hide them behind optimistic retries.

## Toolchain

AgentIC is built around open-source digital design tools:

- Verilator
- Icarus Verilog (`iverilog`, `vvp`)
- Yosys
- SymbiYosys (`sby`)
- OpenLane for physical implementation

Formal and physical-flow stages are optional depending on the selected build mode and installed environment.

## Repository Structure

Top-level layout:

```text
AgentIC/
├── src/agentic/          # orchestrator, tool wrappers, agents, CLI
├── tests/                # unit and reliability tests
├── benchmark/            # benchmark runner and reports
├── docs/                 # supporting documentation
├── web/                  # frontend
├── server/               # backend/service layer
├── scripts/              # helper and CI scripts
├── artifacts/            # generated runtime artifacts
└── metircs/              # benchmark and design metrics
```

Key files:

- [README.md](/home/vickynishad/AgentIC/README.md)
- [main.py](/home/vickynishad/AgentIC/main.py)
- [cli.py](/home/vickynishad/AgentIC/src/agentic/cli.py)
- [orchestrator.py](/home/vickynishad/AgentIC/src/agentic/orchestrator.py)
- [vlsi_tools.py](/home/vickynishad/AgentIC/src/agentic/tools/vlsi_tools.py)
- [USER_GUIDE.md](/home/vickynishad/AgentIC/docs/USER_GUIDE.md)

## Installation

### Prerequisites

Minimum verification flow:

```text
Python 3.10+
Verilator 5.x
Icarus Verilog
Yosys / SymbiYosys via oss-cad-suite
```

Optional physical flow:

```text
OpenLane
Docker
Installed PDK (for example sky130 or gf180)
```

### Setup

```bash
git clone https://github.com/Vickyrrrrrr/AgentIC.git
cd AgentIC
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Environment

Typical `.env` values:

```bash
NVIDIA_API_KEY="your-key-here"
LLM_BASE_URL="http://localhost:11434"
OPENLANE_ROOT="/path/to/OpenLane"
PDK_ROOT="/path/to/pdk"
```

See [USER_GUIDE.md](/home/vickynishad/AgentIC/docs/USER_GUIDE.md) for model backend selection details.

## CLI Usage

All commands are invoked through `main.py`.

### Build

Fast RTL and verification flow:

```bash
python3 main.py build \
  --name my_design \
  --desc "32-bit APB timer with interrupt" \
  --skip-openlane
```

Full flow with signoff-oriented stages:

```bash
python3 main.py build \
  --name my_design \
  --desc "32-bit APB timer with interrupt" \
  --full-signoff \
  --pdk-profile sky130
```

Skip coverage while still continuing from formal to regression:

```bash
python3 main.py build \
  --name my_design \
  --desc "UART transmitter with programmable baud divisor" \
  --skip-openlane \
  --skip-coverage
```

Important build flags:

```text
--skip-openlane
--skip-coverage
--full-signoff
--strict-gates / --no-strict-gates
--min-coverage
--max-retries
--max-pivots
--pdk-profile {sky130,gf180}
--hierarchical {auto,off,on}
--congestion-threshold
```

### Other Commands

```bash
python3 main.py simulate --name <design>
python3 main.py harden --name <design>
python3 main.py verify <design>
```

## Generated Artifacts

A typical design directory contains:

```text
designs/<name>/
├── src/
│   ├── <name>.v
│   ├── <name>_tb.v
│   ├── <name>_sva.sv
│   ├── *_formal_result.json
│   ├── *_coverage_result.json
│   └── *.vcd
├── formal/
├── config.tcl
├── macro_placement.tcl
└── ip_manifest.json
```

Design-local `src/` is intended to keep permanent, human-useful artifacts. Tool intermediates such as Verilator build trees, compiled simulators, `.sby` working directories, coverage work products, and Yosys scratch outputs are staged in temporary directories and cleaned automatically.

## Benchmarking

The repository includes a benchmark runner for multi-design evaluation:

```bash
python3 benchmark/run_benchmark.py --design counter8 --attempts 1 --skip-openlane
```

Generated summaries live under [benchmark/results](/home/vickynishad/AgentIC/benchmark/results).

Benchmarking matters here because repeated failures usually point to pipeline issues, validation gaps, or repair-routing problems. Those failures are used to improve the system over time.

## Web Interface

AgentIC includes a frontend and backend for interactive execution and live streaming of pipeline events. The UI is useful when you want:

- stage-by-stage visibility
- human approval gates
- real-time log streaming
- artifact inspection during a build

Frontend and service code:

- [web](/home/vickynishad/AgentIC/web)
- [server](/home/vickynishad/AgentIC/server)

## Scope And Limits

AgentIC is aimed at autonomous digital design exploration, verification-heavy iteration, and open-source PDK implementation flows. It is not yet a replacement for a certified commercial signoff stack or a production ASIC team with foundry-qualified internal methodology.

Practical implications:

- benchmark pass rate still matters more than demo quality
- hierarchical repair is harder than single-module repair and is treated explicitly
- formal and coverage stages are valuable, but must be routed correctly to be useful
- "industry-grade" here means constrained, diagnosable, replayable, and fail-closed

## Design Principles

The project is built around a small number of non-negotiable rules:

- fail closed
- prefer deterministic fixes before LLM fixes
- preserve design intent with minimum-diff repair
- validate every generated artifact before downstream use
- treat routing bugs as seriously as model bugs
- turn observed benchmark failures into regression tests

## IP Note

This README describes capabilities and workflow at a high level. It does not document the internal prompt architecture, private heuristics, decision policies, or proprietary reasoning logic used inside the system.

## License

Proprietary and Confidential.

Copyright © 2026 Vicky Nishad. All rights reserved.

This repository, including its architecture, algorithms, prompts, agent logic, repair heuristics, and associated intellectual property, may not be reproduced, distributed, reverse-engineered, or used in derivative works without explicit written permission.
