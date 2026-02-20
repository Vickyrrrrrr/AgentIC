import os
import sys

# Add src to path to import config
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))
from agentic.config import NVIDIA_CONFIG, GLM5_CONFIG, LOCAL_CONFIG
from crewai import LLM

print(f"--- Debugging LLM Flow ---")

def try_model(name, config):
    print(f"\nTesting {name}...")
    print(f"Model:    {config['model']}")
    print(f"Base URL: {config['base_url']}")
    masked_key = '*' * 5 + config['api_key'][-4:] if config['api_key'] and len(config['api_key']) > 4 else 'None'
    print(f"API Key:  {masked_key}")

    if not config['api_key'] or config['api_key'] == "NA":
        print(f"⏭ Skipping {name}: No API Key found.")
        return False

    try:
        llm = LLM(
            model=config['model'],
            base_url=config['base_url'],
            api_key=config['api_key']
        )
        resp = llm.call(
            messages=[{"role": "user", "content": "Return the word 'CONNECTED' if you hear me."}]
        )
        print(f"✅ {name} Success! Response:\n{resp}")
        return True
    except Exception as e:
        print(f"❌ {name} Failed: {e}")
        return False

# 1. Try NVIDIA
if try_model("NVIDIA (Primary)", NVIDIA_CONFIG):
    print("\n🎉 Verification Complete: Using NVIDIA.")
    sys.exit(0)

# 2. Try GLM5
if try_model("GLM5 (Backup)", GLM5_CONFIG):
    print("\n🎉 Verification Complete: Using GLM5.")
    sys.exit(0)

# 3. Try Local
if try_model("Local (Default)", LOCAL_CONFIG):
    print("\n🎉 Verification Complete: Using Local LLM.")
    sys.exit(0)

print("\n🔥 All LLM connections failed.")
