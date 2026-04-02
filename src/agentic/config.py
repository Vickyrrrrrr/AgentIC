import os
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Project Paths
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env file ONLY if it exists, and NEVER override environment variables
# already set by the platform (e.g. HuggingFace Spaces secrets).
_dotenv_path = os.path.join(WORKSPACE_ROOT, ".env")
if os.path.isfile(_dotenv_path):
    load_dotenv(_dotenv_path, override=False)

OPENLANE_ROOT = os.environ.get("OPENLANE_ROOT", os.path.expanduser("~/MVP/OpenLane"))
DESIGNS_DIR = os.path.join(OPENLANE_ROOT, "designs")
SCRIPTS_DIR = os.path.join(WORKSPACE_ROOT, "scripts")

CLOUD_CONFIG = {
    "model": os.environ.get("NVIDIA_MODEL", "openai/meta/llama-3.3-70b-instruct"),
    "base_url": os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
    "api_key": os.environ.get("NVIDIA_API_KEY", ""),
}

GLM_CONFIG = {
    "model": os.environ.get("GLM_MODEL", "glm-4-plus"),
    "base_url": os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4/"),
    "api_key": os.environ.get("GLM_API_KEY", ""),
}

LOCAL_CONFIG = {
    "model": os.environ.get(
        "LLM_MODEL",
        "ollama/hf.co/mradermacher/VeriReason-Qwen2.5-3b-RTLCoder-Verilog-GRPO-reasoning-tb-GGUF:Q4_K_M",
    ),
    "base_url": os.environ.get("LLM_BASE_URL", "http://localhost:11434"),
    "api_key": os.environ.get("LLM_API_KEY", "NA"),
}

GROQ_CONFIG = {
    "model": os.environ.get("GROQ_MODEL", "groq/llama-3.3-70b-versatile"),
    "base_url": "",  # litellm resolves groq routing from the model prefix
    "api_key": os.environ.get("GROQ_API_KEY", ""),
}

# Backward-compat alias used by parts of the codebase/docs
NVIDIA_CONFIG = CLOUD_CONFIG

# Expose active defaults (CLI chooses concrete backend)
LLM_MODEL = LOCAL_CONFIG["model"]
LLM_BASE_URL = LOCAL_CONFIG["base_url"]
LLM_API_KEY = LOCAL_CONFIG["api_key"]

def get_role_llm_config(role: str) -> Dict[str, str]:
    """
    Resolve the LLM config for a specific multi-agent role.
    Intelligently maps agent roles to preferred backend engines:
    - Architect, Designer, Verifier -> NVIDIA (Heavy code generation & JSON formatting)
    - Debugger, Manager -> GLM (Deep reasoning & log summarization)
    - Fixer, Physical -> Groq (Blazing fast iterative speed)
    """
    role = role.lower()
    
    # 1. Select the preferred engine
    preferred_engine = CLOUD_CONFIG
    if role in ("architect", "debugger", "manager"):
        preferred_engine = GLM_CONFIG
    elif role in ("designer", "testbench_designer", "verifier"):
        preferred_engine = CLOUD_CONFIG
    elif role in ("fixer", "physical"):
        preferred_engine = GROQ_CONFIG

    # Helper to check if an engine has a valid API key
    def is_valid(cfg):
        k = cfg.get("api_key", "")
        return bool(k and k not in ("mock-key", "NA", ""))

    # 2. Fallback execution order (Preferred -> GLM -> NVIDIA -> Groq -> Local)
    # If the user's setup lacks a key for the preferred engine, shift to the next best
    engines = [preferred_engine, GLM_CONFIG, CLOUD_CONFIG, GROQ_CONFIG, LOCAL_CONFIG]
    
    for cfg in engines:
        # LOCAL_CONFIG is always considered a valid fallback if we reach the end
        if cfg is LOCAL_CONFIG or is_valid(cfg):
            model = cfg.get("model", "")
            # Ensure proper prefixing: use 'openai/' for custom OpenAI-compatible endpoints like Zhipu
            if cfg is GLM_CONFIG and not model.startswith("openai/"):
                model = f"openai/{model}"
            
            return {
                "model": model,
                "api_key": cfg.get("api_key", ""),
                "base_url": cfg.get("base_url", "")
            }
            
    return LOCAL_CONFIG.copy()

# Portable OSS-PDK profiles (adapter-style)
PDK_PROFILES: Dict[str, Dict[str, Any]] = {
    "sky130": {
        "pdk": "sky130A",
        "std_cell_library": "sky130_fd_sc_hd",
        "default_clock_period": "10.0",
    },
    "gf180": {
        "pdk": "gf180mcuC",
        "std_cell_library": "gf180mcu_fd_sc_mcu7t5v0",
        "default_clock_period": "15.0",
    },
}

DEFAULT_PDK_PROFILE = os.environ.get("PDK_PROFILE", "sky130").strip().lower()
if DEFAULT_PDK_PROFILE not in PDK_PROFILES:
    DEFAULT_PDK_PROFILE = "sky130"

# Tool Settings
PDK_ROOT = os.environ.get("PDK_ROOT", os.path.expanduser("~/.ciel"))
PDK = os.environ.get("PDK", PDK_PROFILES[DEFAULT_PDK_PROFILE]["pdk"])
OPENLANE_IMAGE = "ghcr.io/the-openroad-project/openlane:ff5509f65b17bfa4068d5336495ab1718987ff69-amd64"

# Simulation/Coverage adapter defaults
SIM_BACKEND_DEFAULT = os.environ.get("SIM_BACKEND_DEFAULT", "auto").strip().lower()
if SIM_BACKEND_DEFAULT not in {"auto", "verilator", "iverilog"}:
    SIM_BACKEND_DEFAULT = "auto"

COVERAGE_FALLBACK_POLICY_DEFAULT = os.environ.get("COVERAGE_FALLBACK_POLICY", "fallback_oss").strip().lower()
if COVERAGE_FALLBACK_POLICY_DEFAULT not in {"fail_closed", "fallback_oss", "skip"}:
    COVERAGE_FALLBACK_POLICY_DEFAULT = "fallback_oss"

COVERAGE_PROFILE_DEFAULT = os.environ.get("COVERAGE_PROFILE", "balanced").strip().lower()
if COVERAGE_PROFILE_DEFAULT not in {"balanced", "aggressive", "relaxed"}:
    COVERAGE_PROFILE_DEFAULT = "balanced"


def _resolve_tool_binary(bin_name: str, env_var: Optional[str] = None) -> str:
    """Resolve tool binary using configured roots before PATH.

    Fallback order:
    1) Explicit env var for that tool (if provided)
    2) OSS_CAD_SUITE_HOME/bin
    3) WORKSPACE_ROOT/oss-cad-suite/bin
    4) /home/vickynishad/oss-cad-suite/bin
    5) bin_name from PATH
    """
    explicit = os.environ.get(env_var, "").strip() if env_var else ""
    if explicit and os.path.exists(explicit):
        return explicit

    roots = []
    oss_home = os.environ.get("OSS_CAD_SUITE_HOME", "").strip()
    if oss_home:
        roots.append(oss_home)
    roots.append(os.path.join(WORKSPACE_ROOT, "oss-cad-suite"))

    for root in roots:
        candidate = os.path.join(root, "bin", bin_name)
        if os.path.exists(candidate):
            return candidate

    return bin_name


OSS_CAD_SUITE_ROOT = os.environ.get("OSS_CAD_SUITE_HOME", os.path.join(WORKSPACE_ROOT, "oss-cad-suite"))
SBY_BIN = _resolve_tool_binary("sby", env_var="SBY_BIN")
YOSYS_BIN = _resolve_tool_binary("yosys", env_var="YOSYS_BIN")
EQY_BIN = _resolve_tool_binary("eqy", env_var="EQY_BIN")


def get_pdk_profile(profile: str) -> Dict[str, Any]:
    key = (profile or DEFAULT_PDK_PROFILE).strip().lower()
    if key not in PDK_PROFILES:
        key = "sky130"
    data = dict(PDK_PROFILES[key])
    data["profile"] = key
    return data


def get_toolchain_diagnostics() -> Dict[str, Any]:
    """Return resolved toolchain paths and existence info for startup checks."""
    bins = {
        "sby": SBY_BIN,
        "yosys": YOSYS_BIN,
        "eqy": EQY_BIN,
    }
    return {
        "workspace_root": WORKSPACE_ROOT,
        "openlane_root": OPENLANE_ROOT,
        "pdk_root": PDK_ROOT,
        "pdk": PDK,
        "oss_cad_suite_home": os.environ.get("OSS_CAD_SUITE_HOME", ""),
        "bins": {
            name: {"path": path, "exists": os.path.exists(path) if os.path.isabs(path) else False}
            for name, path in bins.items()
        },
    }
