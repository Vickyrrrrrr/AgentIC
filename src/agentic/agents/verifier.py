from crewai import Agent

def get_verification_agent(llm, verbose=False):
    return Agent(
        role='Formal Verification Engineer',
        goal='Ensure chip correctness using SystemVerilog Assertions (SVA) and rigorous log analysis.',
        backstory='Senior Verification Engineer who assumes all code has bugs. Expert in SVA, Covergroups, and Formal Property Verification.',
        llm=llm,
        verbose=verbose,
        allow_delegation=False
    )

def get_error_analyst_agent(llm, verbose=False):
    return Agent(
        role='EDA Log Analyst',
        goal='Analyze simulation/compilation logs and determine the root cause of failure (Design vs Testbench vs Tool).',
        backstory='Expert in parsing cryptic EDA tool error messages (Icarus, Verilator, DC Compiler).',
        llm=llm,
        verbose=verbose,
        allow_delegation=False
    )


def get_regression_agent(llm, goal, verbose=False):
    """Returns an agent that generates multiple directed test scenarios for regression.
    
    This agent analyzes the RTL spec and creates corner-case, edge-case,
    and stress tests to achieve broader coverage.
    """
    return Agent(
        role='Regression Test Architect',
        goal=goal,
        backstory="""You are a senior verification lead who specializes in test planning.
        You analyze RTL specifications and create comprehensive test plans covering:
        - Corner cases (min/max values, overflow/underflow)
        - Reset behavior (async reset during operation, double-reset)
        - Edge cases (back-to-back operations, simultaneous events)  
        - Boundary conditions (full FIFO, empty buffer, max count)
        - Stress tests (rapid toggling, sustained load)
        You output self-checking Verilog testbenches with clear PASS/FAIL markers.
        Each test must print "TEST PASSED" on success or "TEST FAILED" on failure.""",
        llm=llm,
        verbose=verbose,
        allow_delegation=False
    )
