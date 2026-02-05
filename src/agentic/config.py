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

# NVIDIA NIM Configuration (Primary)
NVIDIA_CONFIG = {
    "model": os.environ.get("NVIDIA_MODEL", "openai/qwen/qwen3-coder-480b-a35b-instruct"),
    "base_url": os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
    "api_key": os.environ.get("NVIDIA_API_KEY", "")
}

# Groq Configuration (Fallback)
GROQ_CONFIG = {
    "model": "openai/llama-3.3-70b-versatile",
    "base_url": "https://api.groq.com/openai/v1",
    "api_key": os.environ.get("GROQ_API_KEY", "")
}

# NVIDIA Backup Configuration (Llama 3.1 405B)
NVIDIA_BACKUP_CONFIG = {
    "model": "meta/llama-3.1-405b-instruct",
    "base_url": "https://integrate.api.nvidia.com/v1",
    "api_key": os.environ.get("NVIDIA_BACKUP_API_KEY", "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")
}

# NVIDIA User Provided Configuration (New Key)
NVIDIA_USER_CONFIG = {
    # Note: User requested 'lama-3_2-nemoretriever-300m-embed-v1' which is an embedding model.
    # Switched to 'meta/llama-3.1-405b-instruct' for code generation capability.
    "model": "meta/llama-3.1-405b-instruct", 
    "base_url": "https://integrate.api.nvidia.com/v1",
    "api_key": "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"
}

# Local/Default Configuration
LOCAL_CONFIG = {
    "model": os.environ.get("LLM_MODEL", "ollama/deepseek-r1"),
    "base_url": os.environ.get("LLM_BASE_URL", "http://localhost:11434"),
    "api_key": os.environ.get("LLM_API_KEY", "NA")
}

# Expose 'active' config variables for backward compatibility if needed, 
# but preferably uses will import the CONFIG dicts or use the get_llm logic.
LLM_MODEL = LOCAL_CONFIG["model"]
LLM_BASE_URL = LOCAL_CONFIG["base_url"]
LLM_API_KEY = LOCAL_CONFIG["api_key"]

# Tool Settings
PDK_ROOT = os.environ.get('PDK_ROOT', os.path.expanduser('~/.ciel'))
OPENLANE_IMAGE = "ghcr.io/the-openroad-project/openlane:ff5509f65b17bfa4068d5336495ab1718987ff69-amd64"
