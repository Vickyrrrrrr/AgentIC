# VibeCoder Guide

## Switch Simulation Backend from iverilog to Verilator

### Problem
`iverilog` cannot handle SystemVerilog. The LLM fix loop wastes retries downgrading valid SV to Verilog-2001.

### Solution: Deterministic Tool Selection

| Stage | Tool | Why |
|-------|------|-----|
| Syntax Check (RTL) | **Verilator** | Full SV support |
| Syntax Check (TB) | **Verilator** | Full SV support |
| RTL Simulation | **Verilator** | Compiles RTL+TB together |
| GLS Simulation | **iverilog** | PDK models use `#1` delays that Verilator rejects |

> [!IMPORTANT]
> GLS **must** keep iverilog. PDK cell models (sky130) use `specify` blocks and `#delay` syntax which Verilator does not support. This is not auto-detectable per chip — it's a fundamental tool limitation. The split is deterministic and permanent.

### Changes implemented

#### [vlsi_tools.py](file:///home/vickynishad/AgentIC/src/agentic/tools/vlsi_tools.py)

1. **`run_syntax_check()`:** Replaced `iverilog` with `verilator --lint-only --timing`
2. **`run_simulation()`:** Replaced `iverilog`+`vvp` with `verilator --binary --timing` + direct execution
3. **`run_simulation_with_coverage()`:** Same as above + `--coverage`
4. **`run_gls_simulation()`:** Kept `iverilog` unchanged
5. **Auto-fix regexes:** Removed SV-to-Verilog downgrade hacks

#### [orchestrator.py](file:///home/vickynishad/AgentIC/src/agentic/orchestrator.py)

- Removed `_try_autonomous_sv_fix()` method (no longer needed)
- Removed SV compatibility fallback logic
