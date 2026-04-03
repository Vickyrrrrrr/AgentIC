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

You can easily run the AgentIC engine locally on your machine using our standalone command-line package.

### Prerequisites
* **Docker** (Required for the OpenLane physical synthesis flow)
* **OSS CAD Suite** (Optional but recommended for RTL linting and simulation)

### 1. Download the Engine
AgentIC is distributed as a standalone, securely compiled executable. You do not need to install Python or manage source code dependencies.
1. Download the latest `agentic` executable for your operating system (e.g., `agentic.exe` for Windows, `agentic-linux` for Linux/macOS) from our official release portal.
2. Place it in an empty folder where you want to construct your chips.
   * *Linux/macOS users:* Open your terminal and make the binary executable: `chmod +x agentic-linux`

### 2. Configure Your Environment
Create a plain text file named `.env` in the EXACT same folder as your executable to securely link your AI credentials. Add your keys like this:

```env
GLM_API_KEY=your_zhipu_api_key_here
NVIDIA_API_KEY=your_nvidia_api_key_here
GROQ_API_KEY=your_groq_api_key_here
```

### 3. Generate Your First Chip
Open your terminal (or Command Prompt / PowerShell) in the folder containing the executable and run the build command. 

*For Linux/macOS:*
```bash
./agentic-linux build --name fast_multiplier \
  --desc "A high-speed 16-bit pipelined hardware multiplier with an active-low synchronous reset." \
  --pdk-profile sky130 \
  --no-strict-gates
```

*For Windows:*
```cmd
agentic.exe build --name fast_multiplier ^
  --desc "A high-speed 16-bit pipelined hardware multiplier with an active-low synchronous reset." ^
  --pdk-profile sky130 ^
  --no-strict-gates
```

**Note:** Ensure **Docker** is running in the background on your machine. This prompt uses the `--no-strict-gates` flag to allow the engine to push through to the GDSII physical layout even if AI-generated testbenches encounter edge cases.

The resulting RTL, Testbenches, and GDS layouts will be strictly verified and saved in your configured designs directory.

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
