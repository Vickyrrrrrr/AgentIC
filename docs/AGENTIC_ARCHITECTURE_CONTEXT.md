# AgentIC Architecture & Context Map 🚀

## Executive Summary
AgentIC is a state-of-the-art **Full-Stack Autonomous Silicon Engineering Framework**. It implements cutting-edge, academic-grade VLSI workflows that rival the internal pipelines of NVIDIA and Synopsys, covering deep pre-silicon verification, physical feasibility, and timing constraints. This document serves as the absolute baseline context for understanding the framework's entire multi-agent hierarchy.

## Directory Structure
The intelligence is predominantly split across two core directories:
- `src/agentic/core/`: The heavyweight algorithmic engines, handling graph expansion, AST-parsing, verification math, and strict silicon feasibility rules.
- `src/agentic/agents/`: The execution bots orchestrating specific payload generation tasks (e.g., testbenches, documentation, SDC constraints) supervised by the core components.

---

## 🧠 The Core Engines (`src/agentic/core/`)

### 1. Requirements & System Architecture
* **`spec_generator.py`**: Converts the user's natural language prompt into a deterministic JSON behavioral contract containing port data, clock frequencies, and latency requirements.
* **`hierarchy_expander.py`**: The "Lead Architect". Recursively translates high-level functional blocks into detailed module/sub-module trees. It enforces standard-cell design constraints dynamically in LLM prompts (e.g., banning internal tri-states, limiting SRAM sizes in favor of DFF registers, pipelining heavy DSP macros).
* **`graph_builder.py`**: Constructs and manages the Directed Acyclic Graph (DAG) of the expanded modules to orchestrate dependency-ordered execution and parallelized leaf code generation.
* **`feasibility_checker.py`**: The physical sanity layer. Validates the generated DAG against standard-cell rules (like Sky130 PDK limits). Eliminates hallucinatory logic (e.g., generating 17GB of SRAM on a chip) and rejects inherently inout/tri-state bus internal meshes.

### 2. High-Level Pre-Silicon Verification
* **`verification_planner.py`**: Reads module specs and deterministically outputs a strict Verification Plan. Sorts tests into P0 (Mandatory) and P1 (Edge-case). Critically, it auto-generates **SystemVerilog Assertions (SVA)** for proving logic formally.
* **`cdc_analyzer.py`**: Implements native **Clock Domain Crossing (CDC)** analysis. Inspects the DAG for multi-clock boundaries and enforces synchronization primitives (2-stage synchronizers, async FIFOs) natively before code is emitted.

### 3. Generation & Iterative Feedback
* **`architect.py` & `react_agent.py`**: Handle execution loops, delegating specific implementation tasks to the LLM backend while managing state and preserving standard CMOS constraints across calls.
* **`self_reflect.py`**: Analyzes the outputs produced in an iterative stage. Provides internal critic feedback unprompted by the user (essentially "did I answer the prompt correctly?").

### 4. Advanced Debugging & Convergence
* **`waveform_expert.py`**: The absolute game-changer. Handles dynamic debugging via VCD back-tracing! Parses Icarus Verilog output waveforms, builds an Abstract Syntax Tree (AST) using Pyverilog, traces the X/Z or mismatch signals backward in time, and identifies the exact line of Verilog code causing the failure.
* **`deep_debugger.py`**: Takes tool failures (Yosys Linter, Verilator coverage, Pytest errors) and automatically forces LLM code rewrites until the design passes the Verification Plan matrix fully.

---

## 🤖 The Task Executors (`src/agentic/agents/`)

* **`designer.py` & `architect.py` (Local)**: Writes the standard RTL `.v` / `.sv` implementation files for the leaf nodes of the design tree.
* **`testbench_designer.py`**: Consumes the verification plan from `verification_planner.py` to write explicit UVM-lite class-based SystemVerilog testbenches or directed Verilog checks. Asserts deterministic `TEST PASSED` string matching.
* **`verifier.py`**: Oversees the execution of the testbenches via standard digital simulation tools (Icarus/Verilator) and feeds logs upstream.
* **`sdc_agent.py`**: The Synthesis Engineer. Generates Synopsys Design Constraints (`.sdc`). Implements clock periods, uncertainty, setup/hold targets, and IO delays directly ready for OpenSTA/OpenLane ingestion.
* **`doc_agent.py`**: Reads finalized RTL and generates accurate technical documentation, register maps, and port descriptions for integration.

---

## 🔬 Research-Grade Upgrades for Enterprise Readiness
AgentIC operates at DAC-level (Design Automation Conference) research capabilities today. To push it into absolute bleeding-edge enterprise commercialization, here are the exact algorithms needed:

1. **Unified Power Format (UPF) / Power Intent Agent:**
   * *Status:* Missing.
   * *Upgrade:* Construct a `upf_agent.py`. It should analyze the data-path DAG for idle/sleep cycles and automatically emit IEEE 1801 UPF files. It should dictate OpenLane to place isolation cells, level-shifters, and header/footer power switches. 

2. **Logic Equivalence Checking (LEC):**
   * *Status:* Relies on dynamic behavioral simulation.
   * *Upgrade:* Integrate formal LEC. As the LLM optimizes the pipelining of modules, use Yosys `equiv` or Cadence Conformal. The agent should mathematically prove that the generated multi-cycle pipelined structural netlist behaves identically to the initial functional JSON contract over infinite cycles.

3. **Graph Neural Network (GNN) Congestion & IR Drop Predictor:**
   * *Status:* `feasibility_checker.py` statically counts Gates (GEs).
   * *Upgrade:* Route OpenLane physical density metrics back into a GNN model. Allow the DAG to dynamically refactor its hierarchy if the GNN predicts a major routing congestion bottleneck or standard-cell hotspot *before* the synthesis stage even begins.

4. **Universal Verification Methodology (UVM) Factory Support:**
   * *Status:* Generates directed testbenches with assertions.
   * *Upgrade:* Extend `testbench_designer.py` to emit full UVM verification environments (Sequencers, Drivers, Monitors, Scoreboards). This demands multi-file class generation and UVM RAL (Register Abstraction Layer) instantiation.
