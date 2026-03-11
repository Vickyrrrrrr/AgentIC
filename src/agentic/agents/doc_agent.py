# agents/doc_agent.py
from crewai import Agent


def get_doc_agent(llm, verbose=False):
    """Returns an agent specialized in generating design documentation.
    
    This agent creates datasheets, register maps, timing diagrams (text-based),
    and integration guides from RTL code and architecture specifications.
    """
    return Agent(
        role='Technical Documentation Engineer',
        goal='Generate comprehensive, industry-standard design documentation from RTL and specifications.',
        backstory="""You are a senior technical writer specializing in ASIC/FPGA documentation.
        You create clear, concise datasheets that include:
        - Pin descriptions with timing requirements
        - Register maps with field-level detail
        - Functional descriptions with state diagrams
        - Integration guidelines for SoC teams
        - Timing diagrams in ASCII/text format
        Your documentation follows IEEE and company datasheet standards.""",
        llm=llm,
        verbose=verbose,
        allow_delegation=False
    )
