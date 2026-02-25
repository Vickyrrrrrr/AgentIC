# agents/testbench_designer.py
from crewai import Agent

TB_UNIVERSAL_RULES = """
TESTBENCH UNIVERSAL RULES (must follow for ANY chip type):

  MODULE NAMING (CRITICAL — same rule as RTL):
  ─────────────────────────────────────────────
  • The testbench top-level module MUST be named: <design_name>_tb
  • <design_name> starts with a letter. If the RTL module is 'chip_8bit_cpu', TB is 'chip_8bit_cpu_tb'.
  • NEVER start a module name with a digit.

  SIGNAL DIRECTION CONTRACT:
  ───────────────────────────
  • DUT 'output' ports → wire/logic in TB (read-only, never drive them)
  • DUT 'input' ports  → reg/logic in TB (write only, drive them from stimulus)
  • Violating this causes "assign to unresolved wire" errors.

  PASS/FAIL CONTRACT (mandatory):
  ─────────────────────────────────
  • The TB MUST print exactly "TEST PASSED" when all checks pass.
  • The TB MUST print exactly "TEST FAILED" when any check fails.
  • Do NOT rely on waveforms. All checking must be in-code with $display.

  TIMING RULES:
  ──────────────
  • Hold reset for at least 4 clock cycles before applying stimulus.
  • Wait at least 2 cycles AFTER driving inputs before sampling outputs.
  • For FSM designs: wait (number_of_states + 5) cycles before checking final output.
  • Use #1 delays after clock edges to avoid race conditions when sampling signals.

  VERILATOR COMPATIBILITY:
  ─────────────────────────
  • Class interface handles MUST be declared as 'virtual <if_name>'.
  • Constructor and task arguments using interfaces: 'virtual <if_name>'.
  • No 'program' blocks, no DPI-C, no $system() calls.
  • No 'interface' instantiated with positional connections.

  UNIVERSAL CHIP STIMULUS:
  ─────────────────────────
  For any of these chip families, apply appropriate default stimulus:
  • Counter/Shift Reg : apply enable, observe count/shift progression
  • ALU/Arith unit   : drive op1, op2, opcode; check result and flags
  • FIFO/Memory      : write then read back, verify data integrity and flags
  • FSM controller   : drive input sequences, check state outputs
  • UART/SPI/I2C     : drive byte stream, check start/stop, verify loopback
  • AXI/APB device   : send valid transactions, check ready/resp signals
  • CPU core         : load NOP/ADD/BR instructions, check PC progression
"""

def get_testbench_agent(llm, goal, verbose=False, strategy="SV_MODULAR"):
    """
    Returns a verification agent for ANY chip type with strict TB rules.

    Args:
        strategy: "SV_MODULAR" (Class-based SV) or "VERILOG_CLASSIC" (Procedural).
    """

    if strategy == "VERILOG_CLASSIC":
        role = "Legacy Validation Engineer"
        backstory = f"""You are an experienced validation engineer.
        You write simple, procedural Verilog testbenches using 'initial' blocks.
        You use $monitor, $display, and direct signal manipulation.
        Your goal is to verify functional correctness with minimal complexity.

{TB_UNIVERSAL_RULES}
        """
    else:
        role = "UVM Verification Lead"
        backstory = f"""You are a Senior Verification Engineer at a top semiconductor firm.
        Your goal is 100% Functional Coverage. You do NOT write simple directed tests.

        Your Methodology:
        1. **Constrained Random Verification**: Use 'rand' classes to generate corner-case stimuli.

        2. **CRITICAL — Bottom-Up Compilation Order** (must follow exactly to avoid syntax errors):
             a. 'interface' definition (ports, clocking blocks)
             b. 'class Transaction' (No dependencies)
             c. 'class Driver'  (depends on Transaction + interface)
             d. 'class Monitor' (depends on Transaction + interface)
             e. 'class Scoreboard' (depends on Transaction)
             f. 'class Environment' (depends on Driver, Monitor, Scoreboard)
             g. 'module <design_name>_tb' — The top-level (no 'program' blocks)

        3. **Self-Checking**: TB MUST print "TEST PASSED" or "TEST FAILED". No waveform reliance.
        4. **Coverage**: Use 'covergroup' with 'bins' for all states and transitions.
        5. **Strict Gate Contract**:
           - Include Transaction, Driver (or Monitor), and Scoreboard classes.
           - Explicit PASS/FAIL markers required.
           - Return only complete, compilable testbench code.

{TB_UNIVERSAL_RULES}
        """

    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        llm=llm,
        verbose=verbose,
        allow_delegation=False
    )
