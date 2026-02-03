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
# 3. export LLM_MODEL="openai/llama-3.3-70b-versatile" (or similar)
# 4. export LLM_BASE_URL="https://api.groq.com/openai/v1"

LLM_MODEL = os.environ.get("LLM_MODEL", "ollama/deepseek-r1")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434")
LLM_API_KEY = os.environ.get("GROQ_API_KEY", os.environ.get("OPENAI_API_KEY", "NA"))

# Tool Settings
PDK_ROOT = os.environ.get('PDK_ROOT', os.path.expanduser('~/.ciel'))
OPENLANE_IMAGE = "ghcr.io/the-openroad-project/openlane:ff5509f65b17bfa4068d5336495ab1718987ff69-amd64"
