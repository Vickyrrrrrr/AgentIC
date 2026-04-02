# AgentIC Architecture Overview

Welcome to the new architecture of AgentIC! 
AgentIC has significantly evolved from a purely local tool to a scalable, cloud-ready execution engine capable of running in platforms like HuggingFace Spaces.

## What's New?

### 1. The React & Vite Frontend
The frontend has been updated to use **React** via **Vite**. This allows us to have a snappier, more modern user interface with native components for interacting with EDA APIs.

### 2. Multi-Stage Dockerfile (HuggingFace & Cloud Ready)
The new `Dockerfile` is built with a **Multi-Stage Build** system.
* **Stage 1 (Node.js)**: Compiles the React UI using Vite (`npm run build`).
* **Stage 2 (Python)**: Installs the industry-grade EDA tools (Yosys, Verilator), sets up the FastAPI backend, and copies the built static assets from Stage 1 into the same image.
* **Result**: A single Docker container that serves both the UI and backend logic on port `7860`, strictly required for HuggingFace Spaces.

### 3. FastAPI StaticFiles Integration
The FastAPI backend (`server/api.py`) was updated to serve the compiled Vite static files directly:
```python
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="web/dist", html=True), name="static")
```
This is what enables a single port to serve everything.

### 4. "Top-Giants" EDA Tools
We updated the internal lab infrastructure (`server/lab.py` and `web/src/pages/EDALab.tsx`) to use top open-source tools:
* **Verilator**: Used for strict, cycle-accurate Verilog syntax linting (`--lint-only`).
* **Yosys**: Used for Logic Synthesis of RTL code into proper gate-level representations.
* **Icarus Verilog**: Simulates the waveform.

### 5. Dual Waveform Viewer (Vcdrom + GTKWave)
* **VCDrom**: Embeeded browser-based waveform viewer. Passed simulator `.vcd` data directly via an `iframe`. Runs *anywhere* safely (perfect for cloud).
* **GTKWave**: A desktop native GUI triggered via `subprocess.Popen` running on local sessions via X11.
* **Environment Aware**: The `VITE_IS_CLOUD` env variable decides which UI gets exposed to prevent crashing head-less servers.

### 6. LLMs for Assisted Verilog Writing
The architecture integrates Large Language Models (LLMs) tightly with the new EDA workflow in the `EDALab`. LLMs write initial draft Verilog snippets. When the user clicks "Check Syntax" or "Synthesize", the backend responds with Verilator compilation errors or Yosys logic metrics. These errors are instantly fed *back* to the LLM to iterate and fix the Verilog.

## Flow Comparison

**Previous Flow (Simple & Local)**
1. User writes Verilog.
2. Runs custom regex or rudimentary python checks.
3. GTKWave spawned natively.
4. UI and CLI bound to Localhost entirely.

**New Flow (Scalable & Cloud)**
1. User generates code through LLM -> Vite Frontend updates.
2. User triggers verification -> Backend (FastAPI) triggers **Verilator** & **Yosys**.
3. Backend returns synthesized gate counts and structured simulator metrics.
4. Waveform is passed up through API as Base64/String -> Embedded instantly into **VCDrom iframe**.
5. Everything happens on port `7860` over HTTP, entirely Dockerized.

