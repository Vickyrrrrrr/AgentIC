# AgentIC: Natural Language to Chip Layout (GDSII)

**AgentIC** is an automated AI Agent framework that transforms natural language descriptions into industry-standard physical chip layouts. It essentially acts as a "Text-to-Silicon" compiler, leveraging LLMs (DeepSeek, Llama 3, etc.) to write RTL, verify it, and drive the OpenLane physical design toolchain.

## 🚀 Capabilities

*   **Natural Language → RTL**: Generates synthesizable **SystemVerilog** code based on your prompt.
*   **Industry Standard Enforced**:
    *   Automatic usage of `logic`, `always_ff`, and `always_comb`.
    *   **Flattened I/O Ports**: Ensures compatibility with hardening tools.
    *   **Scalable Architecture**: Uses `parameters` for bus widths.
*   **Self-Correcting Agents**:
    *   **Design Agent**: Writes the code.
    *   **Verification Agent**: Writes a self-checking Testbench (`_tb.v`).
    *   **Auto-Fix Loop**: If compilation or simulation fails, the agents read the error logs and patch the code automatically.
*   **Physical Design (Hardening)**: Integrates directly with [OpenLane](https://github.com/The-OpenROAD-Project/OpenLane) to generate GDSII layouts.

## 🖥️ Web Interface (UI)

AgentIC includes a futuristic "Atmanirbhar" Dashboard for monitoring designs, benchmarking against market standards, and analyzing GDSII layouts.

```bash
streamlit run AgentIC/app.py
```

Features:
*   **Sci-Fi Themed Dashboard**: Premium "Deep Void" visualization.
*   **Market Benchmarking**: Compare your design's Cost, Power, and Area against imported chips (Nvidia, STM32, etc.).
*   **Design Studio**: Interactive RTL editor and AI planner.
*   **GDSII Viewer**: Download and inspect tapeout files.

---

## 🛠️ Workflow

The workflow consists of three main stages. You can run them all at once or individually.

### 1. Build (Design & Verify)
This is the main entry point. It invites the AI to design the chip and verify it.
```bash
python3 AgentIC/main.py build --name <design_name> --desc "<your description>"
```
**Example:**
```bash
python3 AgentIC/main.py build --name my_processor --desc "A 4x4 Systolic Array NPU with AXI Stream interface"
```
*   **Output**: Generates `src/<name>.v` and `src/<name>_tb.v`.
*   **Action**: Runs syntax checks and simulations until `TEST PASSED` is confirmed.

### 2. Simulate (Manual Verification)
If you have manually modified the Verilog files (or want to re-run verification without triggering the AI to overwrite your files), use this command.
```bash
python3 AgentIC/main.py simulate --name <design_name>
```

### 3. Harden (RTL → GDSII)
Once your Simulation passes, turn the Verilog into a physical layout using OpenLane. This step runs synthesis, placement, routing, and signoff checks.
```bash
python3 AgentIC/main.py harden --name <design_name>
```
*   **Output**: A `.gds` file in `OpenLane/designs/<name>/runs/.../results/final/gds/`.

---

## 📂 Project Structure

```text
/home/vickynishad/
├── AgentIC/              # The AI Core
│   ├── main.py           # CLI Entry point
│   ├── .env              # API Keys & Config
│   └── src/agentic/      # Source code for Agents & Tools
│
├── OpenLane/             # Physical Design Engine
│   └── designs/
│       ├── simple_counter/ # Template configuration (DO NOT DELETE)
│       └── <your_design>/  # Generated chips go here
│           ├── config.tcl  # Auto-generated OpenLane config
│           └── src/
│               ├── <name>.v     # SystemVerilog RTL
│               └── <name>_tb.v  # Testbench
```

## 🔌 Setup & Prerequisites

1.  **Python 3.10+** & **Docker** (for OpenLane).
2.  **Icarus Verilog (`iverilog`)**.
3.  **LLM Configuration**:
    You can use a local model (Ollama) or a Cloud API (Groq/DeepSeek) for faster inference.
    
    Create a `.env` file in `AgentIC/`:
    ```dotenv
    # Option 1: Cloud (Recommended for Speed)
    GROQ_API_KEY=gsk_...
    LLM_MODEL=openai/llama-3.3-70b-versatile
    LLM_BASE_URL=https://api.groq.com/openai/v1

    # Option 2: Local (DeepSeek R1 via Ollama)
    # LLM_MODEL=ollama/deepseek-r1
    # LLM_BASE_URL=http://localhost:11434
    ```

## 🧠 AI Reasoning
By default, if the model supports "Chain of Thought" (like DeepSeek R1), the tool can show the hidden reasoning process.
```bash
python3 AgentIC/main.py build ... --show-thinking
```

---
**Author**: Vickyrrrrrr
**Powered by**: CrewAI & OpenLane
