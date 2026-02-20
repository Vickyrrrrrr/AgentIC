import os
from dotenv import load_dotenv

# Project Paths
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(WORKSPACE_ROOT, ".env"))
load_dotenv()

OPENLANE_ROOT = os.environ.get("OPENLANE_ROOT", os.path.expanduser("~/OpenLane"))
DESIGNS_DIR = os.path.join(OPENLANE_ROOT, "designs")
SCRIPTS_DIR = os.path.join(WORKSPACE_ROOT, "scripts")

# LLM Configuration
# To use GROQ (Free Cloud):
# 1. Get Key from https://console.groq.com
# 2. export GROQ_API_KEY="gsk_..."

# Strict Three-Model Policy:
# 1. Backup GLM5 Cloud
GLM5_CONFIG = {
    "model": os.environ.get("BACKUP_MODEL", "openai/z-ai/glm5"),
    "base_url": os.environ.get("BACKUP_BASE_URL", "https://integrate.api.nvidia.com/v1"),
    "api_key": os.environ.get("NVIDIA_API_KEY", "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")
}

# 2. NVIDIA Qwen Cloud (High Performance) -> Now nemotron-3-nano
NVIDIA_CONFIG = {
    "model": os.environ.get("NVIDIA_MODEL", "nvidia/nemotron-3-nano-30b-a3b"),
    "base_url": os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
    "api_key": os.environ.get("NVIDIA_API_KEY", "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")
}

# 3. VeriReason Local (Fallback)
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
