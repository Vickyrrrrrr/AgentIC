import os
import sys
import json

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agentic.orchestrator import BuildOrchestrator, BuildState
from agentic.cli import get_llm
from agentic.config import OPENLANE_ROOT

def run_npu_build():
    llm = get_llm()
    
    # Load the pre-defined spec
    spec_path = "agentic_npu_tapeout/npu_spec.json"
    with open(spec_path, "r") as f:
        spec_data = json.load(f)
    
    design_name = spec_data["design_name"]
    sid_save_path = f"{OPENLANE_ROOT}/designs/{design_name}/sid_checkpoint.json"

    # Create orchestrator
    orchestrator = BuildOrchestrator(
        name=design_name,
        desc=spec_data["description"],
        llm=llm,
        skip_openlane=False, 
        full_signoff=True,   
    )
    
    # Check for existing checkpoint
    if os.path.exists(sid_save_path):
        print(f"--- [RESUME] Loading existing architectural 'thinking' from {sid_save_path} ---")
        with open(sid_save_path, "r") as f:
            orchestrator.artifacts['sid'] = f.read()
        orchestrator.state = BuildState.SPEC_VALIDATE
    
    orchestrator.run()

if __name__ == "__main__":
    run_npu_build()
