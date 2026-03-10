import os
from crewai import Agent
from langchain_openai import ChatOpenAI
from .. import prompts as agentic_prompts

def get_architect_agent(llm, tools, verbose=False):
    deepseek_llm = ChatOpenAI(
        model="deepseek-ai/deepseek-v3.2",
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=os.environ.get("NVIDIA_API_KEY", ""),
        temperature=1.0,
        model_kwargs={
            "top_p": 0.95,
            "extra_body": {"chat_template_kwargs": {"thinking": True}}
        },
        max_tokens=8192
    )

    return Agent(
        role=agentic_prompts.ARCHITECT_AGENT_ROLE,
        goal=agentic_prompts.ARCHITECT_AGENT_GOAL,
        backstory=agentic_prompts.ARCHITECT_AGENT_BACKSTORY,
        tools=tools,
        llm=deepseek_llm,
        verbose=verbose,
        allow_delegation=False
    )
