# agents/testbench_designer.py
from crewai import Agent
from .. import prompts as agentic_prompts
from ..tools.vlsi_tools import syntax_check_tool, read_file_tool

def get_testbench_agent(llm, goal, verbose=False, strategy="SV_MODULAR"):
    """
    Returns a verification agent for ANY chip type with strict TB rules.

    Args:
        strategy: "SV_MODULAR" (Class-based SV) or "VERILOG_CLASSIC" (Procedural).
    """

    if strategy == "VERILOG_CLASSIC":
        role = agentic_prompts.LEGACY_VALIDATION_ENGINEER_ROLE
        backstory = agentic_prompts.get_legacy_validation_engineer_backstory()
    else:
        role = agentic_prompts.UVM_VERIFICATION_LEAD_ROLE
        backstory = agentic_prompts.get_uvm_verification_lead_backstory()

    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        llm=llm,
        verbose=verbose,
        allow_delegation=False,
        tools=[syntax_check_tool, read_file_tool]
    )
