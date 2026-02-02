# agents/designer.py
from crewai import Agent
from ..config import LLM_MODEL

def get_designer_agent():
    return Agent(
        role='VLSI Design Engineer',
        goal='Create optimized Verilog for {design_input}',
        backstory='Specialist in ECE from Lucknow University. Expert in Sky130 PDK.',
        # IMPORTANT: Use the "ollama/" prefix so CrewAI doesn't look for OpenAI
        llm=LLM_MODEL, 
        verbose=True,
        allow_delegation=False
    )