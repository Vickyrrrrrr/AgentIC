# agents/designer.py
from crewai import Agent
from .. import prompts as agentic_prompts
from ..tools.vlsi_tools import syntax_check_tool, read_file_tool

def get_designer_agent(llm, goal, verbose=False, strategy="SV_MODULAR"):
    """
    Returns a designer agent for ANY chip type, with hard RTL rules baked in.

    Args:
        strategy: "SV_MODULAR" (Modern SystemVerilog) or "VERILOG_CLASSIC" (Verilog-2005).
    """

    if strategy == "VERILOG_CLASSIC":
        role = agentic_prompts.LEGACY_VERILOG_ENGINEER_ROLE
        backstory = agentic_prompts.get_legacy_verilog_engineer_backstory()
    else:
        role = agentic_prompts.SYSTEMVERILOG_ARCHITECT_ROLE
        backstory = agentic_prompts.get_systemverilog_architect_backstory()

    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        llm=llm,
        verbose=verbose,
        allow_delegation=False,
        tools=[syntax_check_tool, read_file_tool]
    )
