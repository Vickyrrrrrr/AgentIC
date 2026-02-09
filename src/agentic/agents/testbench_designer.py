# agents/testbench_designer.py
from crewai import Agent

def get_testbench_agent(llm, goal, verbose=False, strategy="SV_MODULAR"):
    """
    Returns a verification agent tailored to the specific strategy.
    
    Args:
        strategy (str): "SV_MODULAR" (Class-based SV) or "VERILOG_CLASSIC" (Procedural Verilog).
    """
    
    if strategy == "VERILOG_CLASSIC":
        role = "Legacy Validation Engineer"
        backstory = """You are an experienced validation engineer.
        You write simple, procedural Verilog testbenches using `initial` blocks.
        You use `$monitor`, `$display`, and direct signal manipulation.
        Your goal is to verify functional correctness with minimal complexity."""
    else:
        role = "UVM Verification Expert"
        backstory = """You are a senior verification engineer specializing in SystemVerilog.
        You act as a 'UVM-Lite' architect.
        You ALWAYS design a modular testbench environment using:
        - `class Transaction`: Randomized inputs
        - `class Driver`: Drives the DUT interface
        - `class Monitor`: Samples outputs
        - `class Scoreboard`: Checks data integrity
        - `program`: For the test flow
        You prioritize randomization and self-checking mechanisms."""

    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        llm=llm,
        verbose=verbose,
        allow_delegation=False
    )
