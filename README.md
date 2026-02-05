# AgentIC: Sovereign AI-Powered Silicon Design Framework

![Status](https://img.shields.io/badge/Status-Beta_v2.0-orange) ![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![License](https://img.shields.io/badge/License-MIT-green) ![OpenLane](https://img.shields.io/badge/OpenLane-Integrated-purple) ![Verification](https://img.shields.io/badge/Formal_Verification-SVA-red)

**AgentIC** is an automated, sovereign AI Agent framework that transforms natural language descriptions directly into industry-standard physical chip layouts (GDSII). Unlike simple code generators, AgentIC employs a **Self-Correcting Multi-Agent System** that iteratively designs, verifies, fixes, and physically hardens custom silicon.

It acts as a **"Text-to-Silicon" Compiler**, orchestrating a crew of specialized AI agents (Architect, Designer, Verification Engineer, Physical Design Lead) to ensure functional correctness and manufacturability.

---

## 🌊 The Flow: From Text to GDSII

AgentIC does not just write code; it follows a rigorous engineering pipeline.

```mermaid
graph TD
    User([User Input: "Build a Secure Processor"]) --> Architect
    
    subgraph "Phase 1: Front-End Design"
        Architect[Architect Agent] -->|Specs| Designer[Designer Agent]
        Designer -->|RTL Code| SyntaxCheck{Syntax Check}
        SyntaxCheck -- Fail --> Fixer[Auto-Fixer]
        Fixer --> SyntaxCheck
        SyntaxCheck -- Pass --> QA[Senior Reviewer]
        QA -- Reject --> Designer
    end

    subgraph "Phase 2: Formal & Dynamic Verification"
        QA -- Approve --> Formal[SVA / SymbiYosys]
        Formal -->|Mathematical Proof| Testbench[Testbench Agent]
        Testbench -->|Simulation| Sim{Icarus Verilog}
        Sim -- Fail --> Debugger[Error Analyst]
        Debugger -->|Fix Logic| Designer
        Debugger -->|Fix Test| Testbench
    end

    subgraph "Phase 3: Deep Physical Hardening"
        Sim -- Pass --> OpenLane[OpenLane Flow]
        OpenLane -->|GDSII| PPA[PPA Analyzer]
        PPA -- "Timing/Area Violations" --> Optimizer[Backend Engineer]
        Optimizer -->|Optimize RTL| Designer
    end

    PPA -- "Metrics OK" --> Tapeout([Final GDSII])
```

---

## 🚀 Key Capabilities v2.0

### 1. 🛡️ Robust "Anti-Hallucination" Engine
Traditional LLMs often leak "thought processes" or Markdown artifacts into code, breaking compilers. AgentIC v2.0 features:
*   **Hardened VLSI Tools**: Custom I/O handlers (`vlsi_tools.py`) that strictly sanitize outputs, stripping "Thought:", "Action:", and non-Verilog artifacts.
*   **Compiler-Aware Auto-Fix**: Automatically detects and repairs syntax errors (e.g., mismatched port widths, invalid SystemVerilog constructs) without human intervention.
*   **Filesystem Guard**: Enforces correct file extensions (`.sv`, `.v`, `.tcl`) and prevents file path hallucination.

### 2. 🧠 Autonomous Verification Loop
*   **Formal Verification (SVA)**: Before simulation, the `Verification Agent` writes **SystemVerilog Assertions** and runs **SymbiYosys** to mathematically prove safety properties (e.g., "Reset must clear registers").
*   **Dynamic Simulation**: Generates self-checking testbenches, runs `iverilog` simulations, and parses logs.
*   **Root Cause Analysis**: If a test fails, the `Error Analyst` determines if the bug is in the Design (RTL) or the Testbench, fixing the correct file.

### 3. 🏭 Physical Design Feedback Loop (PPA)
*   **Beyond Code**: AgentIC checks real-world metrics—**Power, Performance (Timing), and Area**.
*   **Optimization Cycle**: 
    *   If **Timing** fails (Negative Slack), the agent inserts pipeline stages.
    *   If **Area** is too high (Congestion), the agent simplifies logic or increases core size.
*   **OpenLane Integration**: Full control over `config.tcl` generation and disaster recovery (e.g., loosening density constraints when placement fails).

---

## 📦 Installation

### Prerequisites
*   **Linux/WSL2** (Ubuntu 20.04+ recommended)
*   **Python 3.10+**
*   **Docker** (for OpenLane)
*   **Icarus Verilog** (`sudo apt install iverilog`)
*   **GTKWave** (optional, for viewing waveforms)

### Setup

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/Vickyrrrrrr/AgentIC.git
    cd AgentIC
    ```

2.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure Environment**
    Create a `.env` file in the root directory:
    ```bash
    # LLM Provider (Examples)
    OPENAI_API_KEY="sk-..."
    # OR 
    NVIDIA_API_KEY="nvapi-..."
    
    # Tool Paths (Optional, defaults provided)
    OPENLANE_ROOT="/home/user/OpenLane"
    PDK_ROOT="/home/user/pdk"
    ```

---

## 💻 Usage

### 1. Build a Chip (The Command Center)
The `build` command is the main entry point. It runs the full flow: Architecture -> RTL -> Verify -> GDSII.

```bash
python3 AgentIC/main.py build \
    --name my_processor \
    --desc "A 5-stage pipelined RISC-V processor with hazard detection and forwarding unit. 32-bit width."
```

**Options:**
*   `--show-thinking`: Displays the raw "Thought Process" (DeepSeek/Qwen CoT) in the terminal.
*   `--skip-openlane`: Stops after verification (useful for quick RTL iteration).
*   `--max-retries 5`: Sets how many times the agent can attempt to fix its own errors.

### 2. Manual Simulation & Fix
If you have existing code and just want the agent to fix bugs:
```bash
python3 AgentIC/main.py simulate --name my_processor
```

### 3. Hardening Only
To run OpenLane physical design on an existing Verilog file:
```bash
python3 AgentIC/main.py harden --name my_processor
```

---

## 🏗️ Internal Architecture

| Component | Responsibility | Tools Used |
| :--- | :--- | :--- |
| **Architect Agent** | Defines Micro-Architecture, States, and Interfaces. | Markdown Spec |
| **Designer Agent** | Writes Synthesizable SystemVerilog. | `vlsi_tools.write_verilog` |
| **QA Agent** | "Senior Engineer" that rejects bad coding styles (Latches, Multi-drivers). | Static Analysis |
| **Verification Agent** | Writes SVA properties and Testbenches. | `sby`, `iverilog` |
| **Backend Agent** | Configures OpenLane (`config.tcl`) and optimizes for PPA. | OpenLane Docker |

---

## ❓ Troubleshooting

### "OpenLane Failed / Docker Error"
*   **Cause**: Docker not running or PDK mismatch.
*   **Fix**: Ensure `docker ps` works. Check `PDK_ROOT` matches your Sky130 install.

### "Simulation Failed (Compilation Error)"
*   **Cause**: LLM hallucinated invalid syntax.
*   **Fix**: AgentIC v2.0 usually fixes this automatically. If it persists, use `--max-retries 10` to give it more attempts.

### "Code contains 'Thought:' lines"
*   **Status**: **SOLVED**. The new `vlsi_tools.py` regex filters strip these artifacts automatically.

---

## 📜 License
MIT License. Free for Research and Sovereign Development.
