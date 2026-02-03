import os
import sys

# Add src to path to import config
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))
from agentic.config import LLM_MODEL, LLM_BASE_URL, LLM_API_KEY
from crewai import LLM

print(f"--- Debugging LLM Configuration ---")
print(f"Model:    {LLM_MODEL}")
print(f"Base URL: {LLM_BASE_URL}")
print(f"API Key:  {'*' * 5}{LLM_API_KEY[-4:] if LLM_API_KEY and len(LLM_API_KEY) > 4 else 'NA'}")

print(f"\n--- Sending Test Prompt to '{LLM_MODEL}' ---")

try:
    llm = LLM(
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY
    )
    # Simple direct generation check
    resp = llm.call(
        messages=[{"role": "user", "content": "Hello! Are you ready to design chips?"}]
    )
    print(f"✅ Response received:\n{resp}")

except Exception as e:
    print(f"❌ Error contacting model: {e}")
