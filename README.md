---
title: AgentIC
emoji: 🤖
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
app_port: 7860
---

# AgentIC

AgentIC is an autonomous RTL-to-GDSII pipeline. Give it a natural-language chip specification — it generates RTL, writes testbenches, runs simulation, formal verification, coverage, and regression, and optionally drives physical implementation through GDSII hardening. Every stage is AI-assisted, EDA-tool-verified, and gated behind a Human-in-the-Loop approval checkpoint.

## How It Runs

AgentIC is deployed as a Docker container on HuggingFace Spaces. The backend is a FastAPI server (`server/api.py`) running on port 7860. The frontend is a React app (`web/`) that connects to it via HTTP and Server-Sent Events.

```
Browser (React frontend)
    │  HTTP + SSE (real-time streaming)
    ▼
FastAPI  — uvicorn server.api:app :7860   ← HuggingFace Space
    │
    ├── CrewAI agents  →  LLM (NVIDIA API)   ← remote call
    └── EDA tools (iverilog, verilator, yosys, sby)  ← installed in Docker
```

## Build Stages

| Stage | What happens |
|---|---|
| **SPEC** | LLM structures the natural-language description into a hardware spec |
| **RTL_GEN** | Designer agent generates synthesizable Verilog |
| **RTL_FIX** | Syntax errors are caught and repaired in a repair loop |
| **VERIFICATION** | Testbench agent writes a testbench; iverilog/verilator runs simulation |
| **FORMAL_VERIFY** | yosys + sby runs bounded model checking on the RTL |
| **COVERAGE_CHECK** | Verilator coverage analysis; fails if below threshold |
| **REGRESSION** | Multi-seed regression run across corner cases |
| **SDC_GEN** | Timing constraints file generated |
| **FLOORPLAN** | *(requires skip_openlane: false)* OpenLane floorplanning |
| **HARDENING** | *(requires skip_openlane: false)* GDSII hardening |
| **SIGNOFF** | *(requires skip_openlane: false)* DRC/LVS sign-off |
| **SUCCESS** | All gates passed; artifacts available |

## Human-in-the-Loop (HITL)

Before physical implementation begins (FLOORPLAN), the build pauses and sends an `approval_required` event over the SSE stream. The Human-in-the-Loop Build page in the frontend shows the RTL summary, verification results, and formal proof status. You approve or reject before hardening starts.

- **Approve** → build continues into FLOORPLAN → HARDENING → SIGNOFF
- **Reject** → build stops with a full failure record

To run RTL-only (no physical stages, no HITL pause), set `skip_openlane: true` in the build request. This is the default on HuggingFace since OpenLane requires a full PDK installation.

## EDA Tools

All EDA tools run inside the Docker container:

| Tool | Installed via | Used for |
|---|---|---|
| `iverilog` | apt | Verilog compilation + simulation |
| `verilator` | apt | Coverage analysis |
| `yosys` | apt | Synthesis + formal prep |
| `sby` (SymbiYosys) | built from source | Formal verification |

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/build` | Start a build job |
| `GET` | `/build/stream/{job_id}` | Real-time SSE event stream |
| `GET` | `/build/status/{job_id}` | Job status |
| `GET` | `/build/result/{job_id}` | Final artifacts |
| `GET` | `/approval/status` | Check if waiting for HITL |
| `POST` | `/approve` | Approve pending stage |
| `POST` | `/reject` | Reject pending stage |
| `GET` | `/designs` | List completed designs |
| `GET` | `/metrics/{design_name}` | Physical metrics for a design |

Interactive docs: `https://huggingface.co/spaces/vxkyyy/AgentIC` → `/docs`

## Deployment

AgentIC is deployed to HuggingFace Spaces via Docker. Every push to `main` on GitHub automatically deploys via GitHub Actions.

See [docs/DEPLOY_HUGGINGFACE.md](docs/DEPLOY_HUGGINGFACE.md) for the full deployment guide, CI/CD explanation, and HITL operational details.

## Local Development

```bash
# Build and run the backend locally
docker build -t agentic:local .
docker compose up

# API available at http://localhost:7860
# Docs at http://localhost:7860/docs
```

```bash
# Run the frontend (separate process)
cd web
npm install
npm run dev
# Frontend at http://localhost:5173
```

## Environment Variables

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
# Edit .env — set NVIDIA_API_KEY at minimum
```

| Variable | Required | Description |
|---|---|---|
| `NVIDIA_API_KEY` | Yes | Cloud LLM API key |
| `NVIDIA_MODEL` | No | Default: `meta/llama-3.3-70b-instruct` |
| `NVIDIA_BASE_URL` | No | Default: `https://integrate.api.nvidia.com/v1` |
| `GROQ_API_KEY` | No | Optional fallback LLM |
| `LLM_MODEL` | No | Local Ollama model override |
| `LLM_BASE_URL` | No | Local LLM endpoint |

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

## Installation & Quickstart

AgentIC is distributed as a standalone, compiled executable. You do not need to install Python or set up virtual environments!

### 1. Prerequisites
AgentIC orchestrates industry-standard open-source EDA tools. You must have these installed on your system:

- **oss-cad-suite** (Provides `yosys`, `verilator 5.x`, `iverilog`)
- **Docker** (Required for OpenLane physical implementation)

### 2. Setup AgentIC

1. Download the `agentic` executable provided with your purchase.
2. Place it anywhere in your PATH (e.g., `sudo mv agentic /usr/local/bin/agentic`)
3. Make it executable: `chmod +x agentic`
4. Authenticate your machine using your license key:
   ```bash
   agentic login <your_license_key>
   ```

### 3. Bring Your Own Key (BYOK)

AgentIC is a BYOK application. To generate chips, you need an API key from a supported cloud provider (NVIDIA, Groq, or OpenAI). 

Inside the folder where you plan to build your chip, create a file named `.env` and add your key:

```bash
# Example .env file
NVIDIA_API_KEY="nvapi-your-key-here"
GROQ_API_KEY="gsk_your-key-here"

# Advanced (Optional)
OPENLANE_ROOT="/path/to/OpenLane"
PDK_ROOT="/path/to/pdk"
```

## CLI Usage

Because AgentIC is now a standalone tool, you run all commands directly using `agentic`.

You can view all available commands and options at any time by running:
```bash
agentic --help
agentic build --help
```

### Building a Chip

To create a fast RTL and verification flow (Skipping physical GDSII layout):

```bash
agentic build \
  --name apb_timer \
  --desc "32-bit APB timer with interrupt" \
  --skip-openlane
```

To run a full industry signoff flow (RTL all the way to GDSII layout via physical synthesis):

```bash
agentic build \
  --name i2c_master \
  --desc "I2C Master Controller with 8-byte FIFO" \
  --full-signoff \
  --pdk-profile sky130
```

To skip coverage analysis but still continue from formal verification to regression:

```bash
agentic build \
  --name uart_tx \
  --desc "UART transmitter with programmable baud divisor" \
  --skip-openlane \
  --skip-coverage
```

### Advanced Build Flags

AgentIC provides granular control over the autonomous hardware generation process:

```text
--skip-openlane                     Stop after verification (No physical hardening)
--skip-coverage                     Skip Verilator coverage gates
--full-signoff                      Run rigorous DRC/LVS/STA verification
--strict-gates / --no-strict-gates  Stop the build if any intermediate check fails
--min-coverage FLOAT                Minimum line coverage required to advance
--max-retries INT                   How many times the AI can attempt to fix bugs
--max-pivots INT                    How many strategy pivots allowed on logic failures
--pdk-profile {sky130,gf180}        Target physical layout technology
--hierarchical {auto,off,on}        Multi-module extraction logic
--congestion-threshold FLOAT        Physical routing congestion tolerance
```

### Debugging & Verification

If you want to manually run stages on a design that a previous build generated:

```bash
agentic simulate --name <design>
agentic harden --name <design>
agentic verify <design>
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
