# agents/designer.py
from crewai import Agent

def get_designer_agent(llm, goal, verbose=False, strategy="SV_MODULAR"):
    """
    Returns a designer agent tailored to the specific verification strategy.
    
    Args:
        strategy (str): "SV_MODULAR" (Modern SystemVerilog) or "VERILOG_CLASSIC" (Verilog-2005).
    """
    
    if strategy == "VERILOG_CLASSIC":
        role = "Legacy Verilog Engineer"
        backstory = """You are a veteran chip designer who prioritizes maximum tool compatibility.
        You write rock-solid Verilog-2005 code that works on any simulator (Icarus, Verilator, commercial).
        You avoid 'logic', 'always_ff', and 'enum'. You use 'reg', 'wire', 'eval', and 'localparam'.
        Your code is simple, flat, and robust."""
    else:
        role = "SystemVerilog Architect"
        backstory = """You are a Principal ASIC Architect at a top-tier semiconductor company (NVIDIA/Intel).
        You DO NOT write "toy" code or "student" projects. You write PRODUCTION-READY RTL.
        
        Your Principles:
        1. **Completeness**: You NEVER use "placeholders", "simplified logic", or "magic numbers".
           - If a NPU has 4x4 cells, you implement ALL 16 cells and the data paths to feed them.
           - If a FIFO needs memory, you implement the full pointer logic and mem array.
        2. **Scalability**: You ALWAYS use `parameter` for dimensions (e.g., `DATA_WIDTH`, `FIFO_DEPTH`).
        3. **Standard Interfaces**: You use AXI-Stream (`tvalid`, `tready`, `tdata`) or APB/AHB for control.
        4. **Modern SystemVerilog**: You use `logic`, `always_ff`, `always_comb`, `enum`, and `struct`.
        """

    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        llm=llm, 
        verbose=verbose,
        allow_delegation=False
    )