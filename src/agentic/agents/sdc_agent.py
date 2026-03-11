# agents/sdc_agent.py
from crewai import Agent

def get_sdc_agent(llm, goal, verbose=False):
    """
    Returns an agent tailored for Synopsys Design Constraints (SDC) generation.
    """
    role = "Timing Constraint Engineer"
    backstory = """You are a Senior Physical Design (PD) Engineer responsible for timing closure.
    You write high-quality Synthesizable Design Constraints (SDC) files for ASIC flow (e.g., OpenLane, Yosys, OpenSTA).
    
    Your Methodology:
    1. Read the provided Architecture Specification.
    2. Identify the primary clock port (usually 'clk') and its target frequency/period.
    3. Generate a standard SDC file implementing:
       - create_clock
       - set_input_delay (usually 20% of clock period)
       - set_output_delay (usually 20% of clock period)
       - set_driving_cell (optional/default)
       - set_load (optional/default)
    4. Only output valid SDC commands, no markdown wrappers, no explanations in the final block.
    """

    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        llm=llm,
        verbose=verbose,
        allow_delegation=False
    )
