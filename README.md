# AgentIC: The Limitless AI-Driven Silicon Compiler

🚀 **Notice:** The AgentIC Web Platform is currently in active development and will be launching soon! Early access to the reasoning engine is currently available via the local CLI package.

---

## ⚡ Introduction

**AgentIC** is a next-generation, physics-aware AI hardware design suite. It seamlessly bridges the massive gap between natural language intention and fabrication-ready GDSII chip layouts. 

Whether you are designing a specialized cryptography accelerator, a machine learning NPU, or a custom RISC-V processor, AgentIC acts as your automated VLSI architecture team. Instead of manually writing thousands of lines of Verilog and debugging synthesis loops, you simply describe your chip. AgentIC handles the logic generation, verification, timing constraints, and physical routing.

---

## 🧠 How It Works

AgentIC utilizes a proprietary, highly scalable inference pipeline designed to conquer the complexities of modern SoC design. While the internal methodologies are strictly confidential, the high-level workflow guarantees robust silicon:

1. **Cognitive Parsing:** The engine deconstructs complex architectural prompts into structured, hierarchical logic blocks. It understands exactly how to split monolithic systems into manageable micro-components.
2. **Physics-Aware Blueprinting:** Before any code is synthesized, the system cross-references the requested logic against target foundry physics (e.g., Sky130, GF180). It ensures your requested speeds and logic densities are actually realizable in silicon.
3. **Hierarchical Synthesis:** AgentIC autonomously generates individual sub-components in strict isolation. It rigorously verifies their interfaces and seamlessly stitches them into a robust overarching chip hierarchy.
4. **Automated Foundry Hand-off:** Delivers timing constraints (SDC), verified behavioral models (RTL), and hardened layout macros (GDSII) ready for physical tapeout.

---

## 💻 CLI Installation & Quick Start

AgentIC is installable as a normal Python package.

### What `pip install` does

`pip install agentic-ic` installs the Python package and its Python runtime dependencies automatically.

### What users still need to install manually

AgentIC depends on external EDA tooling that `pip` cannot install for you:

1. **Docker**
   Required for OpenLane RTL-to-GDSII hardening.
2. **OSS CAD Suite on PATH**
   Required binaries: `verilator`, `iverilog`, `vvp`, `yosys`, `sby`
3. **LLM API credentials**
   Configure these with `agentic configure` after install.

### Install the package

```bash
pip install agentic-ic
```

### Check your machine before building

```bash
agentic doctor
```

This command tells users:
- what the package installed automatically
- which external tools are still missing
- whether Docker / OSS CAD Suite are available
- whether API keys are configured

### Configure LLM access

```bash
agentic configure
```

This stores credentials in:

```text
~/.agentic/credentials.json
```

You can use one provider for everything or separate providers for:
- Build agents
- Fix/debug agents
- Documentation/report agents

### Generate your first chip

```bash
agentic build \
  --name fast_multiplier \
  --desc "A high-speed 16-bit pipelined hardware multiplier with an active-low synchronous reset." \
  --pdk-profile sky130 \
  --no-strict-gates
```

### Notes for users

- Keep Docker running if you want the physical hardening flow.
- If you only want RTL generation and verification, use `--skip-openlane`.
- OpenLane is pulled through Docker on demand; users do not need a separate manual OpenLane install.
- Build outputs are written under your configured `OPENLANE_ROOT/designs/` workspace.

---

## ⚖️ License & Intellectual Property

**COPYRIGHT © 2026. ALL RIGHTS RESERVED.**

This software, including its source code, architecture, algorithms, prompting strategies, and associated documentation, is **Proprietary and Confidential Intellectual Property**. 

**Strictly Prohibited Actions:**
* Unauthorized copying, reproduction, or distribution of any part of this software.
* Reverse-engineering, decompiling, or attempting to extract the reasoning schemas, multi-agent frameworks, or flow sequences.
* Using this software to create competing automated hardware design products.

Any unauthorized use, modification, or distribution is a violation of international copyright and intellectual property laws, and will be met with immediate legal action. By using this software, you agree to not disclose its internal mechanisms to any third party.

---
*AgentIC — From Thought to Silicon.*
