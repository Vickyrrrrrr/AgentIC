# AgentIC Multi-LLM Role Map

This document outlines the exact mapping of all 16 internal AgentIC specialized agents to their respective Large Language Models (LLMs), as configured intelligently in `config.py` and routed via `orchestrator.py`.

## 🧠 Brain Trust (GLM-4-Plus via ZhipuAI)
*Driven by deep reasoning, large context windows (128k), and strong analytical capabilities.*

| Agent Name | Internal Role | Stage | Purpose |
| :--- | :--- | :--- | :--- |
| **ArchitectModule** | `architect` | `SPEC` | Generates detailed subsystem specifications and interfaces. |
| **HardwareSpecGenerator**| `architect` | `SPEC_VALIDATE`| Reviews initial prompts and creates valid architectural plans. |
| **HierarchyExpander** | `architect` | `HIERARCHY_EXPAND`| Breaks down large SoC designs into modular pieces. |
| **Error Analyst** | `debugger` | `HEALING_LOOP` | Analyzes complex simulation failures for root-cause issues. |
| **Convergence Expert** | `manager` | `CONVERGENCE_REVIEW`| Checks timing, area, and power metrics for convergence. |
| **Documentation Agent** | `manager` | `SUCCESS` | Reads the entire build log and generates final `.docx` reports. |

## 💻 Code Masters (Llama-3.3-70B via NVIDIA NIM)
*Driven by strong coding benchmarks, strict formatting instructions, and long-output capabilities.*

| Agent Name | Internal Role | Stage | Purpose |
| :--- | :--- | :--- | :--- |
| **DesignerModule** | `designer` | `RTL_GEN` | Standard generic module for creating Verilog code chunks. |
| **RTL Implementation Engineer**| `designer` | `RTL_GEN` | Main agent responsible for writing Synthesizable SystemVerilog. |
| **Verification Agent** | `verifier` | `VERIFICATION` | SystemVerilog class generator for the test framework. |
| **Testbench Engineer** | `verifier` | `VERIFICATION` | Testbench generator (SystemVerilog / UVM-Lite). |

## ⚡ Fast Iterators (Llama-3.3-70B via Groq)
*Driven by blazing-fast inference speeds (800+ tokens/sec) for tight iterative loops and small patches.*

| Agent Name | Internal Role | Stage | Purpose |
| :--- | :--- | :--- | :--- |
| **Syntax Fixer** | `fixer` | `RTL_FIX` | Fixes line-by-line syntax errors quickly for Icarus/Verilator. |
| **RTL Verilog Expert** | `fixer` | `RTL_FIX` | Dedicated agent for repairing broken logic in submodules. |
| **Fix TB Agent** | `fixer` | `VERIFICATION` | Fast, iterative testbench compilation repair loops. |
| **ECO Engineer** | `fixer` | `ECO_PATCH` | Rapid logic patching for formal equivalence fixes. |
| **Floorplan Engineer** | `physical` | `FLOORPLAN` | Generates macro placement constraints (OpenROAD). |
| **Physical Design Engineer**| `physical` | `SDC_GEN` | Generates `.sdc` timing constraints for synthesis. |
