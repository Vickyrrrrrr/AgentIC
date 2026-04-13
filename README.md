# AgentIC: The Limitless AI-Driven Silicon Compiler

**AgentIC** is a next-generation, physics-aware AI hardware design suite. It seamlessly bridges the gap between natural language intention and fabrication-ready GDSII chip layouts.

Whether you are designing a specialized cryptography accelerator, a machine learning NPU, or a custom RISC-V processor, AgentIC acts as your automated VLSI architecture team. Instead of manually writing thousands of lines of Verilog and debugging synthesis loops, you simply describe your chip. AgentIC handles the logic generation, verification, timing constraints, and physical routing.

---

## Installation

### 1. Install the package

```bash
pip install agentic-ic
```

### 2. Check your environment

```bash
agentic doctor
```

This checks that OSS CAD Suite, Docker, and other required tools are available. See the output for any missing dependencies.

### 3. Install a PDK

AgentIC requires an open-source PDK (Process Design Kit) to build chips.

```bash
# See all available PDKs
agentic install-pdk list

# Install SkyWater 130nm (recommended for beginners)
agentic install-pdk sky130

# Install GlobalFoundries 180nm
agentic install-pdk gf180mcu
```

Supported PDKs: `sky130`, `gf180mcu`, `asap7`, `nangate45`, `osu018`, `osu035`, `efly45`

After installation, add to your shell profile:

```bash
export PDK_ROOT=~/.ciel   # or wherever you installed the PDK
```

### 4. Enter your license key

Purchase a license at **[buildstack.live](https://www.buildstack.live)**. After purchase, enter your license key:

```bash
agentic login <your-license-key>
```

This validates your license once and stores credentials locally. You only need to do this once per machine.

### 5. Configure LLM API keys

AgentIC is a **Bring-Your-Own-Key** application. You provide your own LLM API credentials.

```bash
agentic configure
```

Choose a configuration mode:
- **Single Key** — One API key for all agents (simplest)
- **3 Groups** — Separate keys for Build / Fix / Doc agent groups
- **Per-Role** — Different model per agent role (recommended)

Any OpenAI-compatible provider works: OpenAI, Anthropic, Groq, DeepSeek, Together, Ollama, etc.

### 6. Build your first chip

```bash
agentic build \
  --name fast_multiplier \
  --desc "A high-speed 16-bit pipelined hardware multiplier with active-low synchronous reset." \
  --pdk-profile sky130 \
  --no-strict-gates
```

---

## Quick Command Reference

| Command | Description |
|---------|-------------|
| `agentic doctor` | Check environment and toolchain |
| `agentic install-pdk <name>` | Install a PDK (sky130, gf180mcu, etc.) |
| `agentic install-pdk list` | Show all available PDKs |
| `agentic login <key>` | Activate your license |
| `agentic configure` | Configure LLM API keys |
| `agentic build --name X --desc "..."` | Build a chip from natural language |
| `agentic build --dry-run ...` | Validate spec without running build |
| `agentic simulate --name X` | Run simulation with auto-fix loop |
| `agentic harden --name X` | Run OpenLane RTL→GDSII hardening |
| `agentic synth --rtl X.v --top Y` | Run Yosys synthesis |
| `agentic sta --netlist X.v --sdc Y.sdc --lib Z.lib` | Run OpenSTA timing analysis |
| `agentic dft --rtl X.v --top Y` | Run DFT scan insertion + ATPG |
| `agentic power --netlist X.v` | Run power analysis |
| `agentic drc --gds X.gds --tech Y.tech` | Run Magic DRC |
| `agentic lvs --sch X.v --gds Y.gds --setup Z.setup` | Run Netgen LVS |
| `agentic report --design X` | Generate QOR signoff report |

---

## Build Command Options

### Core options

```
--name TEXT           Design name (required)
--desc TEXT           Natural language description (required)
--pdk-profile TEXT    Target PDK (auto-detected if omitted)
--skip-openlane       Stop after simulation (no GDSII hardening)
```

### Verification options

```
--skip-coverage       Skip coverage analysis
--min-coverage FLOAT  Minimum line coverage % (default: 80.0)
--full-signoff        Run full industry signoff (formal + coverage + DRC/LVS)
```

### Control flow options

```
--max-retries N       Max auto-fix retries (default: 5)
--strict-gates/--no-strict-gates  Enable/disable fail-closed gating
--dry-run             Validate spec without running build
--json                Output machine-readable JSON
```

### Thinking display

```
--show-thinking       Print LLM reasoning for each step
--thinking-level       minimal (default) | normal | verbose
```

### Testbench options

```
--tb-gate-mode        strict (default) | relaxed
--tb-max-retries N    Max TB recovery attempts (default: 3)
--tb-fallback-template  uvm_lite (default) | classic
```

### Coverage options

```
--coverage-backend    auto (default) | verilator | iverilog
--coverage-fallback-policy  fallback_oss (default) | fail_closed | skip
--coverage-profile     balanced (default) | aggressive | relaxed
```

---

## System Requirements

### Required

- **Python 3.10+** — your own virtual environment (see below)
- **OSS CAD Suite** — verilator, iverilog, vvp, yosys, sby
  - Download: https://github.com/YosysHQ/oss-cad-suite-build/releases
  - Set: `export OSS_CAD_SUITE_HOME=/path/to/oss-cad-suite`
- **LLM API key** — OpenAI, Anthropic, Groq, or any OpenAI-compatible provider

### Optional

- **Docker** — Required for OpenLane RTL→GDSII hardening
  - Install: https://docs.docker.com/get-docker/
- **Volare** — For automated PDK installation via volare
  - Install: `pip install volare`

---

## Python Environment Setup

### Do NOT use OSS CAD Suite's bundled Python

OSS CAD Suite ships with its own Python interpreter (`oss-cad-suite/py3bin/python3`). **This is not the Python you should use to run AgentIC.**

OSS CAD Suite's Python is compiled alongside the EDA binaries for internal tool compatibility. It likely lacks AgentIC's dependencies (`crewai`, `litellm`, `typer`, `rich`, etc.).

### The correct setup: your own virtual environment

Create a separate Python virtual environment for AgentIC. AgentIC calls EDA tools as subprocesses — it does **not** need to run inside the OSS CAD Suite Python.

```bash
# 1. Create your own virtual environment
python3 -m venv ~/agentic-env
source ~/agentic-env/bin/activate

# 2. Install AgentIC (this installs all Python dependencies)
pip install agentic-ic

# 3. Point to OSS CAD Suite (where the EDA binaries live)
export OSS_CAD_SUITE_HOME=/path/to/oss-cad-suite

# 4. Set PDK location
export PDK_ROOT=~/.ciel

# 5. Run AgentIC
agentic build --name counter --desc "8-bit counter"
```

### Why this works

AgentIC does not import or run inside OSS CAD Suite's Python. It simply:
1. Finds EDA tool binaries via `OSS_CAD_SUITE_HOME` or PATH
2. Calls them as independent subprocesses (`subprocess.run(['yosys', ...])`)
3. Reads back the output

Your own virtual environment only needs the AgentIC pip package. The EDA tools (`yosys`, `verilator`, `iverilog`, `sby`, `magic`, `netgen`, etc.) are standalone binaries found by the `OSS_CAD_SUITE_HOME` environment variable.

### Environment variable reference

Add these to your shell profile (`~/.bashrc`, `~/.zshrc`, etc.):

```bash
# Python virtual environment (your own)
source ~/agentic-env/bin/activate

# OSS CAD Suite location
export OSS_CAD_SUITE_HOME=/path/to/oss-cad-suite

# PDK installation root
export PDK_ROOT=~/.ciel
```

---

## Notes

- Keep Docker running if you want the physical hardening flow.
- If you only want RTL generation and verification, use `--skip-openlane`.
- OpenLane is pulled through Docker on demand — no separate manual install needed.
- Build outputs are written to `$OPENLANE_ROOT/designs/` (or `agentic-workspace/` by default).
- After first login, subsequent commands run silently without re-verification.
- License works offline for up to 24 hours (then needs re-verification).

---

## License

**COPYRIGHT © 2026. ALL RIGHTS RESERVED.**

AgentIC is proprietary software. Purchase a license at **[buildstack.live](https://www.buildstack.live)**.

Unauthorized copying, reproduction, reverse-engineering, or distribution of this software is strictly prohibited.

---

*AgentIC — From Thought to Silicon.*
