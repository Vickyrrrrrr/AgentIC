# agents/sdc_agent.py
from crewai import Agent
from .. import prompts as agentic_prompts

def get_sdc_agent(llm, goal, verbose=False):
    """
    Returns an agent tailored for Synopsys Design Constraints (SDC) generation.
    """
    role = agentic_prompts.SDC_AGENT_ROLE
    backstory = agentic_prompts.SDC_AGENT_BACKSTORY

    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        llm=llm,
        verbose=verbose,
        allow_delegation=False
    )
