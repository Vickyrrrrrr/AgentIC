from crewai import Agent
from ..tools.vlsi_tools import syntax_check_tool, read_file_tool

def get_verification_agent(llm, verbose=False):
    return Agent(
        role='Formal Verification Engineer',
        goal='Ensure chip correctness using SVA-style inline assertions and rigorous log analysis.',
        backstory="""Senior Verification Engineer targeting Verilator 5 simulation flow.
IMPORTANT CONSTRAINTS (Verilator compatibility):
- NEVER use: class, interface (inside modules), covergroup, program, rand, virtual
- Use inline SVA: assert property (@(posedge clk) condition);
- Use immediate assertions: assert(condition) else $error("...");
- All verification constructs must be Verilator 5 compatible
- Use flat procedural testbenches with reg/wire declarations
You have tools to read files and check syntax — USE THEM to verify your output compiles.""",
        llm=llm,
        verbose=verbose,
        tools=[syntax_check_tool, read_file_tool],
        allow_delegation=False
    )

def get_error_analyst_agent(llm, verbose=False):
    return Agent(
        role='EDA Log Analyst',
        goal='Analyze simulation/compilation logs and determine the root cause of failure (Design vs Testbench vs Tool).',
        backstory="""Expert in parsing EDA tool error messages (Icarus Verilog, Verilator, Yosys).
You have access to file reading tools — USE THEM to read the actual RTL and TB source
files when analyzing errors. Don't guess at the code — read it.
Key diagnostic patterns:
- "Cannot find interface" = code uses interface but Verilator doesn't support it inside modules
- "Unsupported: class" = code uses SystemVerilog classes which Verilator rejects
- Port mismatch = TB instantiates ports not in RTL module declaration
- Undeclared identifier = signal used but not declared as reg/wire
Always recommend Verilator-compatible fixes (no classes, no interfaces inside modules).""",
        llm=llm,
        verbose=verbose,
        tools=[syntax_check_tool, read_file_tool],
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
        
        VERILATOR COMPATIBILITY (MANDATORY):
        - NEVER use: class, interface, covergroup, program, rand, virtual, new()
        - Use flat procedural testbenches with reg/wire declarations
        - Use initial/always blocks for stimulus and checking
        - Instantiate DUT with positional or named port connections
        
        You output self-checking Verilog testbenches with clear PASS/FAIL markers.
        Each test must print "TEST PASSED" on success or "TEST FAILED" on failure.""",
        llm=llm,
        verbose=verbose,
        tools=[syntax_check_tool, read_file_tool],
        allow_delegation=False
    )
