# AgentIC: Current Architecture & Flow

This document outlines the current processing flow and structural architecture of the **AgentIC** system, specifically detailing the upgrades made to support "limitless" SoC generation via recursive graph compilation and intelligent LLM routing.

## 1. Input & LLM Routing Phase
**Files Involved:** `src/agentic/cli.py`, `src/agentic/config.py`
* **The Hook:** The user submits a prompt via the CLI or Server (e.g., `superscalar_rv32im`).
* **Role-Based Dynamic Routing:** To maximize reasoning capability while saving context windows and API costs, AgentIC distributes cognitive load across different model backends:
  * **Heavy Logic** *(Architect, Fixer, Designer)*: Routed to **GLM-4-Plus** for deep reasoning and code generation.
  * **Prose & Documentation** *(Documenter)*: Routed to **NVIDIA LLMs**.
  * **Rapid Iteration** *(Physical/Floorplanning)*: Routed to extremely fast models like **Llama-3 (Groq)**.

## 2. Specification & Structural Blueprinting
**Files Involved:** `src/agentic/core/architect.py`
* **The Goal:** Break the plaintext prompt into a structured JSON architecture map known as the **SID (System Interface Document)**.
* **Protections (Context Management):** 
  * To prevent JSON decoder crashes (`Expecting ',' delimiter`) caused by token cutoffs on massive chips, the Architect is strictly instructed to keep logic definitions under 100 words.
  * **No RTL skeletons** are allowed in the JSON phase. This ensures the output fits safely inside the LLM's context limits.

## 3. PDK Feasibility Analysis
**Files Involved:** `src/agentic/core/feasibility_checker.py`
* **The Check:** Before writing a single line of Verilog, AgentIC reads the active `self.pdk`.
* **Dynamic Constraints:** 
  * If evaluating against **GF180**, it caps safe frequency ranges at ~125MHz. 
  * If **Sky130**, it bounds them to ~200MHz. 
* **Early Exit:** It kills the build early if the user asks for impossible physics, saving compute and preventing hallucination loops later in the pipeline.

## 4. Recursive Graph Compilation (The Core Engine)
**Files Involved:** `src/agentic/core/graph_builder.py`, `src/agentic/orchestrator.py`
* **Parsing the AST:** The orchestrator takes the massive JSON SID from the Architect and initializes a `DependencyGraph`.
* **Topological Sort:** It draws a map of dependencies (which submodules rely on which). 
* **Bottom-Up Execution:** 
  * Instead of prompting the LLM to write the entire monolithic chip at once, the Orchestrator extracts the **Leaf Nodes** (the absolute lowest-level components like sub-ALUs or decoders that have zero internal dependencies).
  * The Designer LLM builds these exact sub-modules in complete isolation.
  * The generated RTL is locked to the local file system (e.g., `artifacts/locked_modules/`).
  * The LLM then moves up the graph, assembling the parent modules using the fixed I/O signatures of the locked children.

## 5. Strictly Gated RTL Fixer Loop
**Files Involved:** `src/agentic/orchestrator.py` (`do_rtl_fix()`)
* **The Safety Net:** If the lower-level RTL compiles with syntax errors (e.g., missing semicolons, incorrect wire assignments), the code is sent to the GLM Fixer agent.
* **The Guardrails:** We injected hard-coded system instructions into the prompt: 
  > `"CRITICAL: Do not rename the module. Do not add, modify, or remove any input/output ports. The top-module interface MUST remain exactly the same."`
* **Impact:** This prevents the previously fatal bug where the LLM Fixer would hallucinate a new variable, mutate the module interface, and permanently break the top-level parent assembly string.

## 6. Verification & Testbench Generation
**Files Involved:** `src/agentic/core/verifier.py`, `src/agentic/orchestrator.py`
* **Testing:** Testbenches are automatically generated tailored to the strict I/O footprints guaranteed by the Fixer loop.
* **Simulation:** Simulators (like Icarus/Verilator) run the testbenches. 
* **Feedback Loop:** If behavioral specs fail, the error trace loops back to the Designer state for refinement.

---

### Why this architecture is "Limitless"
Previously, AgentIC choked because it attempted to hold the entire chip's Verilog structure in a single LLM response, resulting in hallucinated hierarchies and syntax breaks. 

By representing chips as **hierarchical graph sets**, the GLM-4-Plus reasoning engine only ever has to focus on solving *one micro-component at a time*, strictly obeying the locked hardware interfaces of the dependencies below it. This enables the automated generation of massively complex SoCs.
