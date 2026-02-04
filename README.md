# AgentIC: Sovereign AI-Powered Silicon Design Framework

![Status](https://img.shields.io/badge/Status-Beta-orange) ![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![License](https://img.shields.io/badge/License-MIT-green) ![OpenLane](https://img.shields.io/badge/OpenLane-Integrated-purple)

**AgentIC** is an automated, sovereign AI Agent framework that transforms natural language descriptions directly into industry-standard physical chip layouts (GDSII). Designed with the **"Atmanirbhar" (Self-Reliant)** philosophy, it empowers engineers to design secure, custom silicon locally, reducing dependency on foreign EDA tools and supply chains.

It acts as a **"Text-to-Silicon" Compiler**, orchestrating a crew of specialized AI agents (powered by **Qwen Coder**, Llama 3, etc.) to write SystemVerilog RTL, verify it with self-generated testbenches, and harden the design using the OpenLane open-source flow.

---

## 🛡️ Mission: Sovereign Silicon (Atmanirbhar Bharat)
In the context of national defense and critical infrastructure, reliance on closed-source foreign chips and tools poses significant risks (Hardware Trojans, Supply Chain Denial, Backdoors). 

**AgentIC addresses these challenges by:**
1.  **Democratizing Design**: Enabling rapid creation of custom functional blocks without a full design team.
2.  **Open Source Chain**: Utilizing **OpenLane** and standard PDKs (SkyWater 130nm), ensuring the entire flow is auditable.
3.  **Data Privacy**: Promoting the use of **Local LLMs** (running on premises) so sensitive design prompts and logical structures never leave the secure environment.

---

## 🚀 Key Capabilities

### 🧠 AI-Driven RTL Design
*   **Natural Language → SystemVerilog**: Simply describe the module (e.g., "A secure 32-bit RISC-V core with encrypted instruction memory").
*   **Context-Aware Coding**: Agents use proper `logic`, `always_ff`, and `always_comb` blocks and parameterized widths.
*   **Flattened I/O**: Generates hardware-ready ports compatible with physical implementation tools.

### 🔄 Self-Correcting Verification Loop
*   **Agent Swarm**:
    *   **Designer Agent**: Writes the RTL implementation.
    *   **Testbench Agent**: Writes a comprehensive self-checking testbench (`_tb.v`).
    *   **Verifier Agent**: Analyzes simulation logs (Icarus Verilog).
*   **Auto-Fix**: If the simulation fails, the agents analyze the error, rewrite the RTL/Testbench, and retry automatically until `TEST PASSED`.
*   **Robust Fallback**: Automatically switches between models (e.g., from Cloud to Local/Qwen) if one provider is unavailable or fails.

### 🏭 Physical Design Automation
*   **One-Click Hardening**: Seamless integration with **OpenLane**.
*   **GDSII Generation**: Produces the final layout files ready for the foundry.
*   **Artifacts**: Generates LEF, DEF, GDS, and Mag files automatically.

### 🖥️ Secure "Deep Void" Dashboard
*   **Streamlit Operations Center**: Monitor the entire design process visually.
*   **Market Benchmarking**: Compare your design's PPA (Power, Performance, Area) against standard metrics.
*   **GDS Viewer**: Built-in viewer for finalized layouts.

---

## 📦 Installation

### Prerequisites
*   Python 3.10+
*   [OpenLane](https://github.com/The-OpenROAD-Project/OpenLane) (installed and configured via Docker)
*   Icarus Verilog (`iverilog`)

### Setup
1.  **Clone the Repository**
    ```bash
    git clone https://github.com/Vickyrrrrrr/AgentIC.git
    cd AgentIC
    ```

2.  **Create Virtual Environment**
    ```bash
    python -m venv venv
    source venv/bin/activate
    ```

3.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configure Environment**
    Create a `.env` file in the root directory:
    ```env
    # Choose your provider (NVIDIA, GROQ, orQwen Coder via  OPENAI)
    # Leave empty if using Local LLM (e.g., Ollama)
    OPENAI_API_KEY=sk-...
    NVIDIA_API_KEY=nvapi-...
    GROQ_API_KEY=gsk_...
    
    # Path to OpenLane
    OPENLANE_ROOT=/path/to/openlane
    PDK_ROOT=/path/to/pdk
    ```

---

## 🛠️ Usage

### 1. Command Line Interface (CLI)
The fastest way to build a specific module.

**Build & Verify (RTL Level):**
```bash
python main.py build --name secure_lock --desc "A 4-digit PIN based electronic lock with a lockout timer after 3 failed attempts."
```

**Full Flow (RTL + GDSII):**
*(Ensure OpenLane is running)*
```bash
python main.py harden --name secure_lock
```

### 2. Interactive Web Dashboard
Launch the Mission Control interface.
```bash
streamlit run app.py
```
*   Navigate to **"Design Studio"** to chat with the AI Agents.
*   Use **"Fabrication"** tab to trigger OpenLane flows.
*   Check **"GDS Viewer"** to inspect final chips.

---

## 📂 Project Structure

```text
AgentIC/
├── artifacts/          # Generated VCD waveforms & GDSII layouts
├── designs/            # Source RTL & Testbenches
│   ├── minicount/
│   └── secure_lock/
├── src/
│   └── agentic/
│       ├── agents/     # AI Personas (Designer, Verifier)
│       └── tools/      # Interfaces for compilers & simulators
├── app.py              # Streamlit Web Dashboard
├── main.py             # CLI Entry Point
└── debug_llm.py        # Utility to check LLM connectivity
```

---

## 🔮 Roadmap
*   Support for Analog/Mixed-Signal descriptions.
*   Integration with open-source FPGA toolchains (Yosys/Nextpnr).
*   Formal Verification agent integration.

---

## ⚖️ License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
