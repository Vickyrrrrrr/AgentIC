# agents/testbench_designer.py
from crewai import Agent
from ..tools.vlsi_tools import syntax_check_tool, read_file_tool

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
        Your goal is 100% Functional Coverage with Verilator-compatible output.

        CRITICAL: Your target compiler is Verilator 5.0+.
        Verilator does NOT support: classes, interfaces, covergroups, program blocks, virtual, new(), rand.
        You MUST use FLAT PROCEDURAL SystemVerilog only.

        Your Methodology:
        1. **Flat Procedural TB**: Use reg/wire declarations, initial blocks, and direct signal driving.
        2. **Randomized Stimulus**: Use $urandom for random data generation (Verilator-safe).
        3. **Self-Checking**: Compare DUT outputs against expected values with if-statements.
        4. **Error Tracking**: Use `integer fail_count;` — increment on each check failure.
        5. **PASS/FAIL**: Print "TEST PASSED" if fail_count==0, "TEST FAILED" otherwise.
        6. **Timeout Watchdog**: Always add `initial begin #100000; $display("TEST FAILED: Timeout"); $finish; end`
        7. **Waveform Dump**: Always add $dumpfile/$dumpvars.

        NEVER USE: interface, class, virtual, covergroup, coverpoint, program, new(), rand, constraint.
        These are NOT supported by Verilator and will cause immediate compile failure.

{TB_UNIVERSAL_RULES}
        """

    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        llm=llm,
        verbose=verbose,
        allow_delegation=False,
        tools=[syntax_check_tool, read_file_tool]
    )
