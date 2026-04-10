import os
import platform as _platform
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Project Paths
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env file ONLY if it exists, and NEVER override environment variables
# already set by the platform (e.g. HuggingFace Spaces secrets).
_dotenv_path = os.path.join(WORKSPACE_ROOT, ".env")
if os.path.isfile(_dotenv_path):
    load_dotenv(_dotenv_path, override=False)

# For end-users, we want designs to generate exactly where they run the command.
# Developers can still override OPENLANE_ROOT with an environment variable.
OPENLANE_ROOT = os.environ.get("OPENLANE_ROOT", os.getcwd())
DESIGNS_DIR = os.path.join(OPENLANE_ROOT, "designs")
SCRIPTS_DIR = os.path.join(WORKSPACE_ROOT, "scripts")

# =============================================================================
# Universal LLM Config
# =============================================================================
# Any OpenAI-compatible endpoint works. Set via environment variables:
#
#   OpenAI:      LLM_BASE_URL=https://api.openai.com/v1       LLM_MODEL=gpt-4o
#   Anthropic:   LLM_MODEL=anthropic/claude-3-5-sonnet        (no base_url needed)
#   Groq:        LLM_BASE_URL=https://api.groq.com/openai/v1  LLM_MODEL=llama-3.3-70b-versatile
#   NVIDIA NIM:  LLM_BASE_URL=https://integrate.api.nvidia.com/v1
#   Ollama:      LLM_BASE_URL=http://localhost:11434           LLM_MODEL=ollama/qwen2.5-coder:7b
#   Together AI: LLM_BASE_URL=https://api.together.xyz/v1
#   OpenRouter:  LLM_BASE_URL=https://openrouter.ai/api/v1
#
# Per-role overrides: set ROLE_{ROLE}_MODEL / ROLE_{ROLE}_BASE_URL / ROLE_{ROLE}_API_KEY
# e.g. ROLE_DESIGNER_MODEL=gpt-4o  ROLE_FIXER_MODEL=anthropic/claude-3-5-sonnet

DEFAULT_LLM_CONFIG = {
    "model":    os.environ.get("LLM_MODEL",    "openai/gpt-4o"),
    "base_url": os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
    "api_key":  os.environ.get("LLM_API_KEY",  ""),
}

# Backward-compat aliases — keeps the rest of the codebase unchanged
CLOUD_CONFIG    = DEFAULT_LLM_CONFIG
NVIDIA_CONFIG   = DEFAULT_LLM_CONFIG
LOCAL_CONFIG    = DEFAULT_LLM_CONFIG
GROQ_CONFIG     = DEFAULT_LLM_CONFIG
GLM_CONFIG      = DEFAULT_LLM_CONFIG
DEEPSEEK_CONFIG = DEFAULT_LLM_CONFIG

LLM_MODEL    = DEFAULT_LLM_CONFIG["model"]
LLM_BASE_URL = DEFAULT_LLM_CONFIG["base_url"]
LLM_API_KEY  = DEFAULT_LLM_CONFIG["api_key"]


def get_role_llm_config(role: str) -> Dict[str, str]:
    """
    Resolve the LLM config for a specific agent role.
    Checks for per-role env var overrides first, then falls back to DEFAULT_LLM_CONFIG.
    Works transparently with any OpenAI-compatible provider via LiteLLM.

    Override examples:
        ROLE_DESIGNER_MODEL=gpt-4o
        ROLE_FIXER_MODEL=anthropic/claude-3-5-sonnet
        ROLE_DOCUMENTER_MODEL=llama-3.3-70b-versatile
        ROLE_DOCUMENTER_BASE_URL=https://api.groq.com/openai/v1
        ROLE_DOCUMENTER_API_KEY=gsk_...
    """
    role_upper = role.upper().replace("-", "_")
    model    = os.environ.get(f"ROLE_{role_upper}_MODEL",    "").strip()
    base_url = os.environ.get(f"ROLE_{role_upper}_BASE_URL", "").strip()
    api_key  = os.environ.get(f"ROLE_{role_upper}_API_KEY",  "").strip()

    return {
        "model":    model    or DEFAULT_LLM_CONFIG["model"],
        "base_url": base_url or DEFAULT_LLM_CONFIG["base_url"],
        "api_key":  api_key  or DEFAULT_LLM_CONFIG["api_key"],
    }


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

# OpenLane image — auto-detect ARM64 (Oracle A1 Ampere) vs x86_64
_ARCH = "arm64" if _platform.machine() in ("aarch64", "arm64") else "amd64"
OPENLANE_IMAGE = os.environ.get(
    "OPENLANE_IMAGE",
    f"ghcr.io/the-openroad-project/openlane:ff5509f65b17bfa4068d5336495ab1718987ff69-{_ARCH}"
)

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
    """Resolve EDA tool binary using OSS CAD Suite before falling back to PATH.

    Fallback order:
    1) Explicit env var for that tool (e.g. YOSYS_BIN=/opt/oss-cad-suite/bin/yosys)
    2) OSS_CAD_SUITE_HOME/bin  (set in Docker ENV or user shell)
    3) WORKSPACE_ROOT/oss-cad-suite/bin  (bundled local install)
    4) bin_name from system PATH  (last resort)
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

    # Final fallback: rely on PATH
    return bin_name


OSS_CAD_SUITE_ROOT = os.environ.get("OSS_CAD_SUITE_HOME", os.path.join(WORKSPACE_ROOT, "oss-cad-suite"))

# All EDA tool binaries resolved via OSS CAD Suite
SBY_BIN       = _resolve_tool_binary("sby",       env_var="SBY_BIN")
YOSYS_BIN     = _resolve_tool_binary("yosys",     env_var="YOSYS_BIN")
EQY_BIN       = _resolve_tool_binary("eqy",       env_var="EQY_BIN")
VERILATOR_BIN = _resolve_tool_binary("verilator", env_var="VERILATOR_BIN")
IVERILOG_BIN  = _resolve_tool_binary("iverilog",  env_var="IVERILOG_BIN")
VVP_BIN       = _resolve_tool_binary("vvp",       env_var="VVP_BIN")
SV2V_BIN      = _resolve_tool_binary("sv2v",      env_var="SV2V_BIN")


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
        "sby":       SBY_BIN,
        "yosys":     YOSYS_BIN,
        "eqy":       EQY_BIN,
        "verilator": VERILATOR_BIN,
        "iverilog":  IVERILOG_BIN,
        "vvp":       VVP_BIN,
        "sv2v":      SV2V_BIN,
    }
    return {
        "workspace_root":     WORKSPACE_ROOT,
        "openlane_root":      OPENLANE_ROOT,
        "pdk_root":           PDK_ROOT,
        "pdk":                PDK,
        "oss_cad_suite_home": os.environ.get("OSS_CAD_SUITE_HOME", ""),
        "llm_model":          LLM_MODEL,
        "llm_base_url":       LLM_BASE_URL,
        "bins": {
            name: {"path": path, "exists": os.path.exists(path) if os.path.isabs(path) else False}
            for name, path in bins.items()
        },
    }
