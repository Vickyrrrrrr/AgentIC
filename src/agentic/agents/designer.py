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
        backstory = """You are a modern ASIC designer who uses the full power of SystemVerilog (IEEE 1800-2012).
        You use 'logic' for everything, 'always_ff' for sequentials, and 'always_comb' for logic.
        You use 'enum' for FSMs and 'interface' where appropriate. Your code is clean and modular."""

    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        llm=llm, 
        verbose=verbose,
        allow_delegation=False
    )