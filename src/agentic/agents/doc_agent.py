# agents/doc_agent.py
from crewai import Agent
from .. import prompts as agentic_prompts


def get_doc_agent(llm, verbose=False):
    """Returns an agent specialized in generating design documentation.
    
    This agent creates datasheets, register maps, timing diagrams (text-based),
    and integration guides from RTL code and architecture specifications.
    """
    return Agent(
        role=agentic_prompts.DOC_AGENT_ROLE,
        goal=agentic_prompts.DOC_AGENT_GOAL,
        backstory=agentic_prompts.DOC_AGENT_BACKSTORY,
        llm=llm,
        verbose=verbose,
        allow_delegation=False
    )
