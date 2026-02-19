import os
from dotenv import load_dotenv

# Paths
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(WORKSPACE_ROOT, ".env"))

OPENLANE_ROOT = os.environ.get("OPENLANE_ROOT", os.path.expanduser("~/OpenLane"))
DESIGNS_DIR = os.path.join(OPENLANE_ROOT, "designs")
SCRIPTS_DIR = os.path.join(WORKSPACE_ROOT, "scripts")

# LLM Configuration
# To use GROQ (Free Cloud):
# 1. Get Key from https://console.groq.com
# 2. export GROQ_API_KEY="gsk_..."

# Strict Two-Model Policy:
# 1. NVIDIA Qwen Cloud (Primary)
NVIDIA_CONFIG = {
    "model": os.environ.get("NVIDIA_MODEL", "openai/qwen/qwen3-coder-480b-a35b-instruct"),
    "base_url": os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
    "api_key": os.environ.get("NVIDIA_API_KEY", "")
}

# 2. VeriReason Local (Fallback)
# Explicitly uses the VeriReason model defined in .env
LOCAL_CONFIG = {
    "model": os.environ.get("LLM_MODEL", "ollama/hf.co/mradermacher/VeriReason-Qwen2.5-3b-RTLCoder-Verilog-GRPO-reasoning-tb-GGUF:Q4_K_M"),
    "base_url": os.environ.get("LLM_BASE_URL", "http://localhost:11434"),
    "api_key": os.environ.get("LLM_API_KEY", "NA")
}

# Expose 'active' config variables (Defaults to Local if NVIDIA missing, but CLI handles logic)
LLM_MODEL = LOCAL_CONFIG["model"]
LLM_BASE_URL = LOCAL_CONFIG["base_url"]
LLM_API_KEY = LOCAL_CONFIG["api_key"]

# Tool Settings
PDK_ROOT = os.environ.get('PDK_ROOT', os.path.expanduser('~/.ciel'))
PDK = os.environ.get('PDK', 'sky130A') # Default to SkyWater 130nm
OPENLANE_IMAGE = "ghcr.io/the-openroad-project/openlane:ff5509f65b17bfa4068d5336495ab1718987ff69-amd64"

# OSS CAD Suite (SymbiYosys, Yosys) - Self-contained within AgentIC
OSS_CAD_SUITE_ROOT = os.environ.get('OSS_CAD_SUITE_HOME', os.path.join(WORKSPACE_ROOT, 'oss-cad-suite'))
SBY_BIN = os.path.join(OSS_CAD_SUITE_ROOT, 'bin', 'sby')
