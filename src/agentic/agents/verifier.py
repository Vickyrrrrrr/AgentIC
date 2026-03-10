from crewai import Agent
from .. import prompts as agentic_prompts
from ..tools.vlsi_tools import syntax_check_tool, read_file_tool

def get_verification_agent(llm, verbose=False):
    return Agent(
        role=agentic_prompts.FORMAL_VERIFICATION_AGENT_ROLE,
        goal=agentic_prompts.FORMAL_VERIFICATION_AGENT_GOAL,
        backstory=agentic_prompts.FORMAL_VERIFICATION_AGENT_BACKSTORY,
        llm=llm,
        verbose=verbose,
        tools=[syntax_check_tool, read_file_tool],
        allow_delegation=False
    )

def get_error_analyst_agent(llm, verbose=False):
    return Agent(
        role=agentic_prompts.ERROR_ANALYST_AGENT_ROLE,
        goal=agentic_prompts.ERROR_ANALYST_AGENT_GOAL,
        backstory=agentic_prompts.ERROR_ANALYST_AGENT_BACKSTORY,
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
        role=agentic_prompts.REGRESSION_AGENT_ROLE,
        goal=goal,
        backstory=agentic_prompts.REGRESSION_AGENT_BACKSTORY,
        llm=llm,
        verbose=verbose,
        tools=[syntax_check_tool, read_file_tool],
        allow_delegation=False
    )
