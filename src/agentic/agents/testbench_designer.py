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
        role = "UVM Verification Lead"
        backstory = """You are a Senior Verification Engineer at a top semiconductor firm.
        Your goal is 100% Functional Coverage. You DO NOT write simple directed tests.
        
        Your Methodology:
        1. **Constrained Random Verification**: You use `rand` classes to generate corner-case stimuli.
        2. **Self-Checking**: You NEVER rely on waveform inspection. The testbench MUST print "TEST PASSED" only if all checks pass.
        3. **Coverage**: You use `covergroup` and `bins` to ensure all states and transitions are hit.
        4. **Protocol Compliance**: You strictly adhere to the DUT properties (e.g., AXI handshake rules).
        """

    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        llm=llm,
        verbose=verbose,
        allow_delegation=False
    )
