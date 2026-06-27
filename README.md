# AgentIC: Autonomous AI Chip Design Agents

An open-source multi-agent system that turns a natural-language chip description into RTL, verification artifacts, timing constraints, and — when PDK + EDA tools are available — GDSII.

Built on [CrewAI](https://github.com/joaomdmoura/crewai) with specialized agents for architecture, RTL design, verification, testbench generation, STA/SDC, and documentation.

## Quick Start

```bash
pip install agentic-ic
agentic build --name counter --desc "8-bit counter with active-low reset"
```

Requires Python 3.10+, an LLM API key (OpenAI, Anthropic, Groq, or any OpenAI-compatible), and [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build/releases) for simulation/synthesis.

## Architecture

Six CrewAI agents collaborate through a state-machine orchestrator:

| Agent | Role |
|-------|------|
| **Architect** | Writes specification and architecture plan |
| **RTL Designer** | Generates synthesizable Verilog/SystemVerilog |
| **Verifier** | Lint checks, formal verification (SymbiYosys) |
| **Testbench Designer** | UVM-lite / classic testbenches with coverage |
| **SDC Agent** | Timing constraints generation |
| **Documentation Agent** | Design docs and QoR reports |

**Pipeline stages:** Spec → RTL → Lint → Verification → Testbench → Synthesis → SDC → STA → DFT → Power → Floorplan → Hardening (OpenLane) → DRC → LVS → Post-layout → Signoff

The orchestrator runs bounded correction loops at each stage with retry budgets, strategy pivoting, and checkpoint recovery.

## CLI Commands

```
agentic build --name <name> --desc "<desc>"  Build a chip from description
agentic doctor                                 Check environment
agentic synth --rtl <path> --top <name>        Run Yosys synthesis
agentic simulate --name <name>                 Run simulation
agentic sta --netlist <path> --sdc <path>      Run STA
agentic drc --gds <path>                       Run DRC
agentic lvs --sch <path> --gds <path>          Run LVS
agentic harden --name <name>                   Run OpenLane hardening
agentic report --design <name>                 Generate QoR report
```

## Requirements

- **Python 3.10+** (your own venv, not OSS CAD Suite's bundled Python)
- **OSS CAD Suite** — verilator, iverilog, yosys, sby
- **Docker** — for OpenLane RTL-to-GDSII hardening
- **Open PDK** — install via `agentic install-pdk sky130`
- **LLM API key** — set in `.env` or environment

See [pipeline.md](pipeline.md) for the detailed RTL-to-GDSII flow diagram.
