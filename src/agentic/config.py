import json
import os
import platform as _platform
import tempfile
from typing import Dict, Any, Optional
from dotenv import load_dotenv

# Project Paths
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env from explicit env var, current working directory, and package root.
# Never override already-exported environment variables.
def _load_dotenv_candidates() -> None:
    candidates = []
    explicit = (os.environ.get("AGENTIC_ENV_FILE") or "").strip()
    if explicit:
        candidates.append(os.path.expanduser(explicit))

    candidates.append(os.path.join(os.getcwd(), ".env"))
    candidates.append(os.path.join(WORKSPACE_ROOT, ".env"))

    seen = set()
    for candidate in candidates:
        normalized = os.path.abspath(candidate)
        if normalized in seen:
            continue
        seen.add(normalized)
        if os.path.isfile(normalized):
            load_dotenv(normalized, override=False)


_load_dotenv_candidates()

CREDENTIALS_PATH = os.path.expanduser("~/.agentic/credentials.json")


def _load_user_credentials() -> Dict[str, Any]:
    """Load persisted CLI credentials from user home directory."""
    try:
        with open(CREDENTIALS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_user_credentials(credentials: Dict[str, Any]) -> None:
    """Persist CLI credentials with secure file permissions."""
    os.makedirs(os.path.dirname(CREDENTIALS_PATH), exist_ok=True)
    with open(CREDENTIALS_PATH, "w", encoding="utf-8") as f:
        json.dump(credentials, f, indent=2)
    try:
        os.chmod(CREDENTIALS_PATH, 0o600)
    except OSError:
        pass


def _normalize_base_url(raw_url: str) -> str:
    """Ensure provider base URLs include a protocol."""
    url = (raw_url or "").strip().strip('"').strip("'")
    if not url:
        return ""
    if "://" in url:
        return url
    if url.startswith(("localhost", "127.0.0.1", "0.0.0.0")):
        return f"http://{url}"
    return f"https://{url}"

# For end-users, we want designs to generate exactly where they run the command.
# Developers can still override OPENLANE_ROOT with an environment variable.
def _ensure_writable_dir(path: str) -> bool:
    """Best-effort writability check for runtime workspace directories."""
    try:
        os.makedirs(path, exist_ok=True)
        probe_path = os.path.join(path, ".agentic_write_probe")
        with open(probe_path, "w", encoding="utf-8") as probe:
            probe.write("ok")
        os.remove(probe_path)
        return True
    except OSError:
        return False


_requested_openlane_root = os.environ.get("OPENLANE_ROOT", os.getcwd())
_fallback_openlane_root = os.environ.get(
    "AGENTIC_WORKSPACE_ROOT",
    os.path.join(tempfile.gettempdir(), "agentic-workspace"),
)

if _ensure_writable_dir(os.path.join(_requested_openlane_root, "designs")):
    OPENLANE_ROOT = _requested_openlane_root
else:
    OPENLANE_ROOT = _fallback_openlane_root
    # Keep downstream modules in sync once fallback is selected.
    os.environ["OPENLANE_ROOT"] = OPENLANE_ROOT
    if not _ensure_writable_dir(os.path.join(OPENLANE_ROOT, "designs")):
        raise RuntimeError(
            "No writable designs workspace found. "
            f"Checked OPENLANE_ROOT={_requested_openlane_root!r} and fallback={OPENLANE_ROOT!r}."
        )

DESIGNS_DIR = os.path.join(OPENLANE_ROOT, "designs")
SCRIPTS_DIR = os.path.join(WORKSPACE_ROOT, "scripts")

_SAVED_CREDENTIALS = _load_user_credentials()


def _credential_group(group_name: str) -> Dict[str, str]:
    group = _SAVED_CREDENTIALS.get(group_name)
    if isinstance(group, dict):
        return {
            "model": str(group.get("model", "") or "").strip(),
            "base_url": _normalize_base_url(str(group.get("base_url", "") or "")),
            "api_key": str(group.get("api_key", "") or "").strip(),
        }
    return {"model": "", "base_url": "", "api_key": ""}


def _default_group_credentials() -> Dict[str, str]:
    for group_name in ("build", "fix", "doc"):
        group = _credential_group(group_name)
        if group.get("api_key"):
            return group
    return {"model": "", "base_url": "", "api_key": ""}

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

_DEFAULT_GROUP = _default_group_credentials()

DEFAULT_LLM_CONFIG = {
    "model": (
        os.environ.get("LLM_MODEL", "").strip()
        or _DEFAULT_GROUP.get("model", "")
        or "openai/gpt-4o"
    ),
    "base_url": _normalize_base_url(
        os.environ.get("LLM_BASE_URL", "").strip()
        or _DEFAULT_GROUP.get("base_url", "")
        or "https://api.openai.com/v1"
    ),
    "api_key": (
        os.environ.get("LLM_API_KEY", "").strip()
        or _DEFAULT_GROUP.get("api_key", "")
    ),
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

_ROLE_TO_GROUP = {
    "architect": "build",
    "designer": "build",
    "verifier": "build",
    "manager": "build",
    "physical": "build",
    "testbench_designer": "build",
    "fixer": "fix",
    "debugger": "fix",
    "reasoner": "fix",
    "documenter": "doc",
    "reporter": "doc",
    "doc_gen": "doc",
}


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
    role_key = role.lower().replace("-", "_")
    role_upper = role_key.upper()
    role_group = _ROLE_TO_GROUP.get(role_key, "build")
    group_cfg = _credential_group(role_group)

    model    = os.environ.get(f"ROLE_{role_upper}_MODEL", "").strip() or group_cfg.get("model", "")
    base_url = _normalize_base_url(
        os.environ.get(f"ROLE_{role_upper}_BASE_URL", "").strip()
        or group_cfg.get("base_url", "")
    )
    api_key  = os.environ.get(f"ROLE_{role_upper}_API_KEY", "").strip() or group_cfg.get("api_key", "")

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
