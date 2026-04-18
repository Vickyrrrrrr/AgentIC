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

**Supported PDKs:** `sky130`, `gf180mcu`, `asap7`, `nangate45`, `freepdk45`, `osu018`, `osu035`

---

## PDK Comparison Guide

| PDK | Node | Voltage | Max Freq | Maturity | Use Case |
|-----|------|---------|----------|----------|----------|
| **sky130** | 130nm | 1.8V | 150 MHz | ⭐⭐⭐ Production | **Best choice** - Complete tool support, real tapeouts |
| **gf180mcu** | 180nm | 1.8V | 100 MHz | ⭐⭐ Production | Automotive grade, high voltage options |
| **asap7** | 7nm | 0.7V | 1000 MHz | ⭐ Research | Research/learning 7nm flows |
| **nangate45** | 45nm | 1.1V | 500 MHz | ⭐ Research | Academic, single corner |
| **freepdk45** | 45nm | 1.1V | 500 MHz | ⭐ Research | NC State, educational |
| **osu018** | 180nm | 1.8V | 100 MHz | ⭐ Educational | Limited cells, easy setup |
| **osu035** | 350nm | 3.3V | 50 MHz | ⭐ Educational | High voltage, easy probing |

### What Each PDK Supports

| PDK | Synthesis | Place&Route | DRC/LVS | Real Tapeout |
|-----|-----------|-------------|---------|--------------|
| sky130 | ✅ | ✅ | ✅ Complete | ✅ Yes |
| gf180mcu | ✅ | ✅ | ✅ Complete | ✅ Yes |
| asap7 | ✅ | ✅ | ⚠️ Limited | ❌ No |
| nangate45 | ✅ | ✅ | ⚠️ Limited | ❌ No |
| freepdk45 | ✅ | ✅ | ⚠️ Limited | ❌ No |
| osu018 | ✅ | ❌ | ⚠️ Limited | ❌ No |
| osu035 | ✅ | ❌ | ⚠️ Limited | ❌ No |

### Which PDK Should You Choose?

**For beginners:** Use `sky130` - everything works, lots of examples, real chip fabrication possible.

**For research/learning 7nm:** Use `asap7` - simulates 7nm FinFET technology.

**For automotive/industrial:** Use `gf180mcu` - higher voltage, automotive grade.

---

## PDK Installation Guide

### Sky130 (Recommended - Auto Install)

```bash
# One-command installation
agentic install-pdk sky130

# Or use volare (recommended for full control)
volare enable --pdk sky130
volare add --pdk sky130 --tag 2024.12.2_01.51
```

**What gets installed:**
```
~/.ciel/
└── sky130A/                    # Main PDK directory
    ├── libs.ref/                # Cell libraries
    │   ├── sky130_fd_sc_hd/     # High-density standard cells
    │   │   ├── verilog/         # Verilog models (for Yosys)
    │   │   ├── lib/            # Liberty timing files
    │   │   └── gds/            # GDS layouts
    │   └── sky130_sram/         # SRAM memories
    └── libs.tech/               # Tool-specific files
        ├── magic/              # Magic DRC tech files
        ├── netgen/             # Netgen LVS setup
        └── klayout/            # KLayout DRC/LVS rules
```

### GF180MCU (Auto Install)

```bash
# One-command installation
agentic install-pdk gf180mcu

# Or use volare
volare enable --pdk gf180mcu
volare add --pdk gf180mcu --tag 2024.06.2_01.00
```

**What gets installed:**
```
~/.ciel/
└── gf180mcuC/                  # 5-metal stack variant
    ├── libs.ref/
    │   ├── gf180mcu_fd_sc_mcu7t5v0/  # 7-track standard cells
    │   └── gf180mcu_fd_io/    # I/O cells
    └── libs.tech/
        ├── magic/
        ├── netgen/
        └── klayout/
```

### ASAP7 (Manual Installation Required)

ASAP7 is a **predictive** 7nm PDK - it cannot be fabricated but works with open-source tools.

```bash
# 1. Clone OpenROAD-flow-scripts (has ASAP7 support)
git clone https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts.git
cd OpenROAD-flow-scripts

# 2. Download ASAP7 PDK files
make setup-asap7

# 3. Set environment
export PDK_ROOT=$(pwd)/pdks
```

**Expected structure:**
```
pdks/
└── asap7/
    ├── asap7sc7p5t/            # Standard cells
    │   ├── lib/                # Liberty files
    │   ├── lef/                # LEF for place&route
    │   └── verilog/            # Verilog for Yosys
    └── asap7/                  # OpenROAD platform
```

### Nangate45/FreePDK45 (Manual Installation)

```bash
# 1. Clone OpenROAD-flow-scripts
git clone https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts.git
cd OpenROAD-flow-scripts

# 2. Download PDK files
make setup-freepdk45   # or make setup-nangate45

# 3. Set environment
export PDK_ROOT=$(pwd)/pdks
```

### OSU018/OSU035 (Manual Installation)

Oklahoma State University educational PDKs:

```bash
# 1. Get files from OSU website or GitHub
# Visit: https://github.com/osu-icssr?tab=repositories&q=pdk

# 2. Create directory structure
mkdir -p $PDK_ROOT/osu018
# Copy PDK files here

mkdir -p $PDK_ROOT/osu035
# Copy PDK files here
```

---

## Where to Place PDK Files (Simple Explanation)

### The Short Answer

```
PDK_ROOT/           ← Set this in your environment
└── {pdk_name}/    ← e.g., sky130A, gf180mcuC, asap7
    ├── libs.ref/  ← Cell libraries (standard cells, SRAM)
    └── libs.tech/ ← Tool files (DRC, LVS, timing)
```

### What Each Folder Contains (Simple Terms)

```
libs.ref/           "Reference Libraries" - What cells exist
├── {std_cell_lib}/   Your standard cell library
│   ├── verilog/      Cell behavior in Verilog (for synthesis)
│   ├── lib/          Cell timing in Liberty format (for timing analysis)
│   └── gds/          Cell layouts in GDS format (for final chip)
└── {sram_lib}/       Memory blocks (optional)

libs.tech/          "Technology files" - How tools work with the PDK
├── magic/            Files for Magic DRC tool
├── netgen/           Files for Netgen LVS tool
└── klayout/          Files for KLayout DRC/LVS tool
```

### Example: Sky130 Structure

```
~/.ciel/                    ← PDK_ROOT
└── sky130A/                ← PDK variant
    ├── libs.ref/
    │   ├── sky130_fd_sc_hd/     ← Standard cells (HD = High Density)
    │   │   ├── verilog/
    │   │   │   └── sky130_fd_sc_hd.v
    │   │   ├── lib/
    │   │   │   └── sky130_tt.lib
    │   │   └── gds/
    │   │       └── sky130_fd_sc_hd.gds
    │   └── sky130_sram/         ← Memories
    └── libs.tech/
        ├── magic/
        │   └── sky130A.tech     ← Magic DRC rules
        ├── netgen/
        │   └── sky130_setup.tcl ← Netgen LVS rules
        └── klayout/
            └── sky130.lydrc    ← KLayout DRC rules
```

### Common Mistakes

❌ **Wrong:** Putting files in random locations
```
~/downloads/asap7/...      ← Tools won't find this
~/my_pdk_files/...         ← Tools won't find this
```

✅ **Correct:** Set PDK_ROOT and put files there
```
export PDK_ROOT=~/.ciel    ← Define this
~/.ciel/sky130A/...        ← Tools will find files here
~/.ciel/asap7/...          ← Tools will find files here
```

---

## Verifying PDK Installation

```bash
# Check what PDKs are installed
ls -la $PDK_ROOT/

# Verify sky130 installation
ls $PDK_ROOT/sky130A/libs.tech/

# Check for required files
find $PDK_ROOT/sky130A -name "*.v" -o -name "*.lib" -o -name "*.tech" | head -20

# Test with AgentIC
agentic doctor
```

After installation, add to your shell profile:

```bash
export PDK_ROOT=~/.ciel   # or wherever you installed the PDK
```

### 4. Setup AgentIC (First Run)

On first run, AgentIC will automatically guide you through setup. Or run it manually:

```bash
agentic login
```

The interactive setup wizard will ask for:
- **LLM API Key** — Your OpenAI, Anthropic, Groq, or any OpenAI-compatible API key
- **Custom Base URL** — Optional, for self-hosted models (LM Studio, vLLM, Ollama, etc.)
- **AgentIC License Key** — Optional, for production features
- **Supabase URL** — Optional, for cloud features

Any OpenAI-compatible provider works:
| Provider | Base URL | Example Model |
|----------|----------|---------------|
| OpenAI | (default) | gpt-4o |
| Anthropic | (none needed) | claude-3-5-sonnet |
| Groq | api.groq.com/openai/v1 | llama-3.3-70b |
| Ollama | localhost:11434 | qwen2.5-coder:7b |
| LM Studio | localhost:1234 | any local model |
| vLLM / Zai | your-endpoint.com/v1 | meta-llama-3.1-70b |

### 5. Build your first chip

```bash
agentic build \
  --name fast_multiplier \
  --desc "A high-speed 16-bit pipelined hardware multiplier with active-low synchronous reset." \
  --pdk sky130
```

---

## Quick Command Reference

| Command | Description |
|---------|-------------|
| `agentic doctor` | Check environment and toolchain |
| `agentic install-pdk <name>` | Install a PDK (sky130, gf180mcu, etc.) |
| `agentic install-pdk list` | Show all available PDKs |
| `agentic login` | Interactive setup wizard (first run) |
| `agentic configure` | Reconfigure LLM API keys |
| `agentic build --name X --desc "..."` | Build a chip from natural language |
| `agentic synth --rtl <path> --top <name>` | Run Yosys synthesis |
| `agentic sta --netlist <path> --sdc <path> --lib <path>` | Run OpenSTA timing analysis |
| `agentic power --netlist <path>` | Run power analysis |
| `agentic dft --rtl <path> --top <name>` | Run DFT scan insertion |
| `agentic drc --gds <path> --tech <path>` | Run Magic DRC |
| `agentic lvs --sch <path> --gds <path> --setup <path>` | Run Netgen LVS |
| `agentic report --design <name>` | Generate QOR report |
| `agentic harden --name <name>` | Run OpenLane hardening |
| `agentic simulate --name <name>` | Run simulation with auto-fix |

> **Important:** All file paths must be **exact paths** (e.g., `designs/my_design/src/my_design.v`). Relative paths like `tiny_alu.v` are not auto-resolved.

**For complete CLI documentation with all options, see: [docs/CLI_COMMANDS.md](docs/CLI_COMMANDS.md)**

---

## LLM Caching & Rate Limiting

AgentIC includes intelligent caching to reduce API costs and handle rate limits gracefully.

### How It Works

1. **Response Caching**: Identical prompts are cached for 24 hours (configurable)
2. **Rate Limit Recovery**: On 429 errors, waits 30s then tries cache
3. **Provider Fallback**: Automatically retries with different providers
4. **Token Budgeting**: Smart context truncation based on model limits

### Cache Commands

```bash
# View cache statistics and hit rate
agentic cache stats

# Clear all cached responses
agentic cache clear

# Clear expired entries only
agentic cache prune
```

### What Gets Cached

- RTL generation prompts and outputs
- Testbench generation prompts
- Verification result analysis
- SVA assertion generation

### Cache Location

```
~/.agentic_cache/
├── response_cache.db    # LLM response cache (SQLite)
└── usage.db             # API usage tracking
```

### Rate Limit Behavior

When rate limited by an LLM provider:

1. **First 30s**: Wait with exponential backoff
2. **After 30s**: Check cache for identical prompt
3. **After 60s**: Try fallback provider
4. **After 120s**: Fail gracefully with error

---

## Checkpoint Management

AgentIC automatically saves checkpoints at key build milestones, enabling recovery from failures.

### When Checkpoints Are Saved

- After RTL generation (`RTL_GEN`)
- After verification (`VERIFICATION`)
- After synthesis (`SYNTHESIS`)
- After floorplanning (`FLOORPLAN`)
- After hardening (`HARDENING`)
- After signoff (`SIGNOFF`)
- On failure

### Checkpoint Commands

```bash
# List checkpoints for a design
agentic checkpoint --design my_design list

# Show details of latest checkpoint
agentic checkpoint --design my_design list --latest

# Restore from checkpoint (view only - full restore requires orchestrator API)
agentic checkpoint --design my_design restore

# Clear all checkpoints
agentic checkpoint --design my_design clear
```

### Checkpoint Contents

Each checkpoint saves:
- Current build state and step count
- RTL code and testbench
- Architecture specification
- Coverage metrics
- Convergence history
- Error history

### Checkpoint Location

```
checkpoints/
└── {design_name}/
    ├── latest.json              # Most recent checkpoint
    ├── checkpoint_20240115_120000.json
    ├── checkpoint_20240115_121500.json
    └── metadata_index.json      # Checkpoint history
```

---

## API Usage Tracking

Track your API costs and optimize usage with detailed analytics.

### Usage Commands

```bash
# Summary view (default)
agentic usage

# Detailed daily breakdown
agentic usage --format detailed --days 7

# Compare providers
agentic usage --format provider

# Filter by build
agentic usage --build my_design --days 30
```

### What Gets Tracked

- **Per-call**: Provider, model, tokens, duration, success/failure
- **Per-build**: Total calls, cache hits, cost estimate
- **Per-stage**: RTL_GEN, VERIFICATION, SYNTHESIS, etc.

### Cost Estimation

| Provider | Model | Cost/1K Tokens |
|----------|-------|-----------------|
| OpenAI | gpt-4o | $0.005 |
| OpenAI | gpt-4o-mini | $0.00015 |
| Anthropic | claude-3-5-sonnet | $0.003 |
| Groq | llama-3.3-70b | $0.00059 |

### Reducing API Costs

1. **Use cache**: Identical prompts are free after first call
2. **Use smaller models**: gpt-4o-mini is 30x cheaper than gpt-4o
3. **Use Groq**: Free tier available, fast inference
4. **Batch builds**: Cache hits increase with repeated similar designs

---

## Token Budget Management

AgentIC uses intelligent token budgeting to maximize context efficiency.

### How It Works

- **Provider-aware**: Respects context limits (GPT-4o: 128K, Claude: 200K, Groq: 32K)
- **Priority-based**: Error messages > Spec > RTL > History
- **Smart truncation**: Preserves module structure when truncating

### Default Allocation

| Content Type | Budget |
|--------------|--------|
| Error messages | 25% |
| RTL code | 35% |
| Architecture spec | 20% |
| History | 10% |
| Other | 10% |

### Error-Focused Mode

When fixing errors, allocation shifts:
- Error messages: 45%
- RTL code: 30%
- Spec: 10%

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
