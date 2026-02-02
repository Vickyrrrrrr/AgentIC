import os

# Paths
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OPENLANE_ROOT = os.environ.get("OPENLANE_ROOT", os.path.expanduser("~/OpenLane"))
DESIGNS_DIR = os.path.join(OPENLANE_ROOT, "designs")
SCRIPTS_DIR = os.path.join(WORKSPACE_ROOT, "scripts")

# LLM Configuration
LLM_MODEL = os.environ.get("LLM_MODEL", "ollama/deepseek-r1")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434")
LLM_API_KEY = os.environ.get("OPENAI_API_KEY", "NA")

# Tool Settings
PDK_ROOT = os.environ.get('PDK_ROOT', os.path.expanduser('~/.ciel'))
OPENLANE_IMAGE = "ghcr.io/the-openroad-project/openlane:ff5509f65b17bfa4068d5336495ab1718987ff69-amd64"
