# agents/designer.py
from crewai import Agent

def get_designer_agent(llm, goal, verbose=False):
    return Agent(
        role='VLSI Design Engineer',
        goal=goal,
        backstory='Specialist in ECE from Lucknow University. Expert in digital design with Sky130 PDK experience.',
        llm=llm, 
        verbose=verbose,
        allow_delegation=False
    )