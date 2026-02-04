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
