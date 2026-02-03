# agents/testbench_designer.py
from crewai import Agent

def get_testbench_agent(llm, goal, verbose=False):
    """Returns an agent specialized in writing Verilog testbenches."""
    return Agent(
        role='Verification Engineer',
        goal=goal,
        backstory='''Expert in digital verification with deep knowledge of 
        Verilog simulation. Focuses on edge cases, reset behavior, and 
        timing checks. Always includes $monitor, $dumpfile, and $finish.''',
        llm=llm,
        verbose=verbose,
        allow_delegation=False
    )
