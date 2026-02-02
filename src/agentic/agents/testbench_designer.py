# agents/testbench_designer.py
from crewai import Agent

def get_testbench_agent(llm):
    """Returns an agent specialized in writing Verilog testbenches."""
    return Agent(
        role='Verification Engineer',
        goal='Create comprehensive Verilog testbenches for {design_name}',
        backstory='''Expert in digital verification with deep knowledge of 
        Verilog simulation. Focuses on edge cases, reset behavior, and 
        timing checks. Always includes $monitor, $dumpfile, and $finish.''',
        llm=llm,
        verbose=True,
        allow_delegation=False
    )
