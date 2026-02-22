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
        
        2. **CRITICAL: Bottom-Up Compilation Order**: You MUST define classes in this EXACT order to avoid syntax errors:
             a. `class Transaction` (No dependencies)
             b. `class Driver` and `class Monitor` (Depend on Transaction)
             c. `class Scoreboard` (Depends on Transaction)
             d. `class Environment` (Depends on Driver, Monitor, Scoreboard)
             e. `module tb` (The top level)
             
        3. **Self-Checking**: You NEVER rely on waveform inspection. The testbench MUST print "TEST PASSED" only if all checks pass.
        4. **Coverage**: You use `covergroup` and `bins` to ensure all states and transitions are hit.
        5. **Strict Gate Contract**:
           - Include `class Transaction` and at least one of `class Driver`/`class Monitor`/`class Scoreboard`.
           - Emit explicit PASS/FAIL markers (`TEST PASSED` and `TEST FAILED` paths).
           - Return complete compilable testbench code only.
        """

    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        llm=llm,
        verbose=verbose,
        allow_delegation=False
    )
