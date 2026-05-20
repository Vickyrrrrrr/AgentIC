import json
import os
import platform as _platform
import re
import tempfile
from typing import Dict, Any, Optional, List, Tuple
from dotenv import load_dotenv

# Project Paths
WORKSPACE_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


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
    url = os.path.expandvars((raw_url or "").strip().strip('"').strip("'"))
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
    group = _credential_group("build")
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
#   Generic:        LLM_BASE_URL=https://api.generic.com/openai/v1  LLM_MODEL=llama-3.3-70b-versatile
#   Generic NIM:  LLM_BASE_URL=https://integrate.api.generic.com/v1
#   Ollama:      LLM_BASE_URL=http://localhost:11434           LLM_MODEL=ollama/qwen2.5-coder:7b
#   Together AI: LLM_BASE_URL=https://api.together.xyz/v1
#   OpenRouter:  LLM_BASE_URL=https://openrouter.ai/api/v1
#
# Per-role overrides: set ROLE_{ROLE}_MODEL / ROLE_{ROLE}_BASE_URL / ROLE_{ROLE}_API_KEY
# e.g. ROLE_DESIGNER_MODEL=gpt-4o  ROLE_FIXER_MODEL=anthropic/claude-3-5-sonnet

_DEFAULT_GROUP = _default_group_credentials()

_DEFAULT_LLM_CONFIG = {
    "model": (
        os.environ.get("LLM_MODEL", "").strip()
        or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
        or os.environ.get("AZURE_DEPLOYMENT_NAME", "").strip()
        or _DEFAULT_GROUP.get("model", "")
        or "openai/gpt-4o"
    ),
    "base_url": _normalize_base_url(
        os.environ.get("LLM_BASE_URL", "").strip()
        or os.environ.get("AZURE_API_BASE", "").strip()
        or os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        or _DEFAULT_GROUP.get("base_url", "")
        or "https://api.openai.com/v1"
    ),
    "api_key": (
        os.environ.get("OPENAI_API_KEY", "").strip() or 
        os.environ.get("LLM_API_KEY", "").strip() or 
        os.environ.get("AZURE_API_KEY", "").strip() or
        os.environ.get("AZURE_OPENAI_API_KEY", "").strip() or
        _DEFAULT_GROUP.get("api_key", "")
    ),
}

DEFAULT_LLM_CONFIG = _DEFAULT_LLM_CONFIG.copy()

LLM_MODEL = DEFAULT_LLM_CONFIG["model"]
LLM_BASE_URL = DEFAULT_LLM_CONFIG["base_url"]
LLM_API_KEY = DEFAULT_LLM_CONFIG["api_key"]

# =============================================================================
# AgentIC Model (Server-side model for AgentIC-paid builds)
# =============================================================================
# Used when plan_type="agentic_paid" and no BYOK key is provided.
# Env vars: AGENTIC_MODEL_ENABLED, AGENTIC_MODEL_MODEL, AGENTIC_MODEL_BASE_URL, AGENTIC_MODEL_API_KEY
AGENTIC_MODEL_ENABLED = os.environ.get("AGENTIC_MODEL_ENABLED", "0").strip().lower() in (
    "1", "true", "yes", "on"
)

AGENTIC_MODEL_CONFIG = {
    "model": (
        os.environ.get("AGENTIC_MODEL_MODEL", "").strip()
        or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
        or os.environ.get("AZURE_DEPLOYMENT_NAME", "").strip()
        or DEFAULT_LLM_CONFIG["model"]
    ),
    "base_url": _normalize_base_url(
        os.environ.get("AGENTIC_MODEL_BASE_URL", "").strip()
        or os.environ.get("AZURE_API_BASE", "").strip()
        or os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        or DEFAULT_LLM_CONFIG["base_url"]
    ),
    "api_key": (
        os.environ.get("AGENTIC_MODEL_API_KEY", "").strip()
        or os.environ.get("AZURE_API_KEY", "").strip()
        or os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
        or DEFAULT_LLM_CONFIG["api_key"]
    ),
}

# Robust aliases for VERILOG_CODEGEN across branches
VERILOG_CODEGEN_ENABLED = os.environ.get("VERILOG_CODEGEN_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on") or AGENTIC_MODEL_ENABLED
VERILOG_CODEGEN_CONFIG = {
    "model": (
        os.environ.get("VERILOG_CODEGEN_MODEL", "").strip()
        or os.environ.get("AGENTIC_MODEL_MODEL", "").strip()
        or os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
        or os.environ.get("AZURE_DEPLOYMENT_NAME", "").strip()
        or AGENTIC_MODEL_CONFIG["model"]
    ),
    "base_url": _normalize_base_url(
        os.environ.get("VERILOG_CODEGEN_BASE_URL", "").strip()
        or os.environ.get("AGENTIC_MODEL_BASE_URL", "").strip()
        or os.environ.get("AZURE_API_BASE", "").strip()
        or os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        or AGENTIC_MODEL_CONFIG["base_url"]
    ),
    "api_key": (
        os.environ.get("VERILOG_CODEGEN_API_KEY", "").strip()
        or os.environ.get("AGENTIC_MODEL_API_KEY", "").strip()
        or os.environ.get("AZURE_API_KEY", "").strip()
        or os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
        or AGENTIC_MODEL_CONFIG["api_key"]
    ),
}

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
        ROLE_DOCUMENTER_BASE_URL=https://api.generic.com/openai/v1
        ROLE_DOCUMENTER_API_KEY=gsk_...
    """
    role_key = role.lower().replace("-", "_")
    role_upper = role_key.upper()
    role_group = _ROLE_TO_GROUP.get(role_key, "build")
    group_cfg = _credential_group(role_group)

    model = os.environ.get(f"ROLE_{role_upper}_MODEL", "").strip() or group_cfg.get(
        "model", ""
    )
    base_url = _normalize_base_url(
        os.environ.get(f"ROLE_{role_upper}_BASE_URL", "").strip()
        or group_cfg.get("base_url", "")
    )
    api_key = os.environ.get(f"ROLE_{role_upper}_API_KEY", "").strip() or group_cfg.get(
        "api_key", ""
    )

    return {
        "model": model or DEFAULT_LLM_CONFIG["model"],
        "base_url": base_url or DEFAULT_LLM_CONFIG["base_url"],
        "api_key": api_key or DEFAULT_LLM_CONFIG["api_key"],
    }


def resolve_llm_config(
    env_var_prefix: str = "LLM",
    credential_group: str = "default",
    fallback_model: str = "openai/gpt-4o",
    fallback_base_url: str = "https://api.openai.com/v1",
) -> Dict[str, Any]:
    """Resolve LLM config with priority: explicit > env > credentials > defaults.

    Priority order:
    1. env_var_prefix + "_MODEL" / "_BASE_URL" / "_API_KEY"
    2. Saved credentials for credential_group
    3. Fallback values

    Args:
        env_var_prefix: Prefix for environment variables (e.g., "LLM")
        credential_group: Group name in saved credentials (e.g., "build")
        fallback_model: Default model if nothing configured
        fallback_base_url: Default base URL if nothing configured

    Returns:
        Dict with keys: model, base_url, api_key
    """
    # 1. Check environment variables
    model = os.environ.get(f"{env_var_prefix}_MODEL", "").strip()
    base_url = os.environ.get(f"{env_var_prefix}_BASE_URL", "").strip()
    api_key = os.environ.get(f"{env_var_prefix}_API_KEY", "").strip()

    if not api_key:
        # 2. Try saved credentials
        group_cfg = _credential_group(credential_group)
        model = model or group_cfg.get("model", "")
        base_url = base_url or group_cfg.get("base_url", "")
        api_key = api_key or group_cfg.get("api_key", "")

    # 3. Apply fallbacks
    if not model:
        model = fallback_model
    if not base_url:
        base_url = fallback_base_url

    # Normalize base URL
    base_url = _normalize_base_url(base_url)

    return {
        "model": model,
        "base_url": base_url,
        "api_key": api_key or DEFAULT_LLM_CONFIG["api_key"],
    }


def detect_llm_from_env() -> list[dict]:
    """Scan environment for common LLM API keys across all major providers.

    Returns a list of detected configurations, each with:
        provider, model, base_url, api_key, key_env_var

    Providers detected (priority order):
        Anthropic  â€” ANTHROPIC_API_KEY  â†’ claude-3-5-sonnet
        OpenAI     â€” OPENAI_API_KEY     â†’ gpt-4o
        Generic       â€” GENERIC_API_KEY       â†’ llama-3.3-70b
        DeepSeek   â€” DEEPSEEK_API_KEY   â†’ deepseek-chat
        ZhipuAI    â€” ZHIPUAI_API_KEY    â†’ ZHIPUAI_MODEL or LLM_MODEL
        Together   â€” TOGETHER_API_KEY   â†’ meta-llama-3.1-70b
        Ollama     â€” localhost:11434    â†’ auto-detected model
    """
    detected = []

    PROVIDER_MAP = [
        ("openai", "gpt-4o", "https://api.openai.com/v1", "OPENAI_API_KEY"),
        ("anthropic", "claude-3-5-sonnet", "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
        ("generic", "llama-3.3-70b", "https://api.generic.com/openai/v1", "GENERIC_API_KEY"),
        ("deepseek", "deepseek-chat", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
        (
            "zhipuai",
            os.environ.get("ZHIPUAI_MODEL", "").strip()
            or os.environ.get("LLM_MODEL", "").strip(),
            os.environ.get("ZHIPUAI_BASE_URL", "").strip()
            or os.environ.get("LLM_BASE_URL", "").strip()
            or "https://api.z.ai/api/paas/v4/",
            "ZHIPUAI_API_KEY",
        ),
        ("together", "meta-llama-3.1-70b", "https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    ]

    for provider, model, base_url, env_var in PROVIDER_MAP:
        key = os.environ.get(env_var, "").strip()
        if key:
            detected.append({
                "provider": provider,
                "model": model or "provider-default",
                "base_url": base_url,
                "api_key": key,
                "key_env_var": env_var,
            })

    # Check Ollama
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        resp = urllib.request.urlopen(req, timeout=3)
        if resp.status == 200:
            import json as _json
            data = _json.loads(resp.read())
            models = [m.get("name", "") for m in data.get("models", [])]
            model = models[0] if models else "qwen2.5-coder:7b"
            detected.append({
                "provider": "ollama",
                "model": model,
                "base_url": "http://localhost:11434/v1",
                "api_key": "ollama",
                "key_env_var": "OLLAMA (auto-detected)",
            })
    except Exception:
        pass

    # Also check generic LLM_API_KEY
    generic = os.environ.get("LLM_API_KEY", "").strip()
    if generic and not any(d["api_key"] == generic for d in detected):
        detected.append({
            "provider": "generic",
            "model": os.environ.get("LLM_MODEL", "gpt-4o"),
            "base_url": os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            "api_key": generic,
            "key_env_var": "LLM_API_KEY",
        })

    return detected


# =============================================================================
# PDK Profiles
# =============================================================================
# Each profile defines the PDK variant name, the standard cell library to use,
# timing/voltage parameters for OpenLane config generation, and a human-readable
# description shown in the CLI.
#
# Cell library sources:
#   sky130   / gf180mcu  â€” managed by Volare/Ciel, installed automatically.
#   asap7                â€” https://github.com/The-OpenROAD-Project/asap7 (Apache 2.0)
#   nangate45            â€” NanGate 45nm Open Cell Library via Si2 (Apache 2.0)
#   freepdk45            â€” FreePDK45 + NangateOpenCellLibrary (NC State / Si2)
#   osu018 / osu035      â€” Oklahoma State University educational libs (limited cells)
#
# NOTE: efly45 was removed â€” it had no publicly available cell library and its
# original entry incorrectly mapped a 45nm node to sky130 (130nm) cells, which
# produces completely wrong timing, area, and DRC results.  If a real efly45 PDK
# becomes available in the future, add it here with its correct lib name.

PDK_PROFILES: Dict[str, Dict[str, Any]] = {
    "sky130": {
        "pdk": "sky130A",
        "std_cell_library": "sky130_fd_sc_hd",
        "default_clock_period": "10.0",
        "voltage_vdd": "1.8",
        "min_cell_height": "0.46",
        "description": "SkyWater 130nm â€” most mature open PDK, best tool support",
        "fabrication_ready": True,
        "maturity": "production",
    },
    "gf180mcu": {
        "pdk": "gf180mcuC",
        "std_cell_library": "gf180mcu_fd_sc_mcu7t5v0",
        "default_clock_period": "15.0",
        "voltage_vdd": "1.8",
        "min_cell_height": "0.54",
        "description": "GlobalFoundries 180nm â€” automotive grade, high voltage options",
        "fabrication_ready": False,
        "maturity": "experimental",
    },
    "asap7": {
        "pdk": "asap7",
        "std_cell_library": "asap7sc7p5t",
        "default_clock_period": "5.0",
        "voltage_vdd": "0.7",
        "min_cell_height": "0.144",
        "description": "ASAP 7nm predictive PDK â€” research/academic, not a real foundry",
        "fabrication_ready": False,
        "maturity": "research",
    },
    "nangate45": {
        "pdk": "nangate45",
        "std_cell_library": "NangateOpenCellLibrary",
        "default_clock_period": "10.0",
        "voltage_vdd": "1.1",
        "min_cell_height": "0.4",
        "description": "NanGate 45nm Open Cell Library â€” academic/research, Apache 2.0",
        "fabrication_ready": False,
        "maturity": "research",
    },
    "freepdk45": {
        # FreePDK45 is the NC State 45nm predictive PDK.  Its standard cell
        # library is the same NangateOpenCellLibrary used by nangate45, but
        # accessed via the FreePDK45 PDK stack (different tech files / LEF rules).
        # Install: follow https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts
        #          or place under $PDK_ROOT/FreePDK45/
        "pdk": "FreePDK45",
        "std_cell_library": "NangateOpenCellLibrary",
        "default_clock_period": "10.0",
        "voltage_vdd": "1.1",
        "min_cell_height": "0.4",
        "description": "FreePDK45 (NC State 45nm) + NanGate Open Cell Library â€” academic/research",
        "fabrication_ready": False,
        "maturity": "research",
    },
    "osu018": {
        "pdk": "osu018",
        "std_cell_library": "osu018_stdcells",
        "default_clock_period": "12.0",
        "voltage_vdd": "1.8",
        "min_cell_height": "0.5",
        "description": "Oklahoma State 180nm â€” educational/research, limited cell set",
        "fabrication_ready": False,
        "maturity": "research",
    },
    "osu035": {
        "pdk": "osu035",
        "std_cell_library": "osu035_stdcells",
        "default_clock_period": "15.0",
        "voltage_vdd": "3.3",
        "min_cell_height": "0.6",
        "description": "Oklahoma State 350nm â€” high voltage, easy to probe, educational",
        "fabrication_ready": False,
        "maturity": "research",
    },
    "asap5": {
        "pdk": "asap5",
        "std_cell_library": "asap5sc",
        "default_clock_period": "3.0",
        "voltage_vdd": "0.65",
        "min_cell_height": "0.1",
        "description": "ASAP 5nm predictive PDK â€” research/academic",
        "fabrication_ready": False,
        "maturity": "research",
    },
    "asap2": {
        "pdk": "asap2",
        "std_cell_library": "asap2sc",
        "default_clock_period": "2.0",
        "voltage_vdd": "0.5",
        "min_cell_height": "0.08",
        "description": "ASAP 2nm predictive PDK â€” research/academic",
        "fabrication_ready": False,
        "maturity": "research",
    },
    "open28": {
        "pdk": "open28",
        "std_cell_library": "open28_stdcells",
        "default_clock_period": "4.0",
        "voltage_vdd": "0.9",
        "min_cell_height": "0.2",
        "description": "Open 28nm PDK â€” experimental open flow",
        "fabrication_ready": False,
        "maturity": "experimental",
    },
    "openfasoc130": {
        "pdk": "openfasoc",
        "std_cell_library": "sky130_fd_sc_hd",
        "default_clock_period": "20.0",
        "voltage_vdd": "1.8",
        "min_cell_height": "0.46",
        "description": "OpenFASOC 130nm analog/generator flow â€” requires sky130 to be installed separately under same PDK_ROOT",
        "requires_parent_pdk": "sky130",
        "fabrication_ready": False,
        "maturity": "experimental",
    },
    "skywater-raw": {
        "pdk": "skywater-pdk",
        "std_cell_library": "sky130_fd_sc_hd",
        "default_clock_period": "10.0",
        "voltage_vdd": "1.8",
        "min_cell_height": "0.46",
        "description": "Raw SkyWater PDK development tree â€” advanced users, not a packaged OpenLane PDK",
        "fabrication_ready": False,
        "maturity": "experimental",
    },
    "lefdef175": {
        "pdk": "lefdef175",
        "std_cell_library": "lefdef175_stdcells",
        "default_clock_period": "20.0",
        "voltage_vdd": "1.8",
        "min_cell_height": "0.6",
        "description": "LEF/DEF 175nm educational placeholder â€” manual setup required",
        "fabrication_ready": False,
        "maturity": "research",
    },
    "tsmc28": {
        "pdk": "tsmc28",
        "std_cell_library": "tsmc28_stdcell",
        "default_clock_period": "2.5",
        "voltage_vdd": "0.9",
        "min_cell_height": "0.2",
        "description": "TSMC 28nm â€” proprietary PDK, manual foundry access required",
        "proprietary": True,
        "fabrication_ready": False,
        "maturity": "proprietary",
    },
    "samsung14": {
        "pdk": "samsung14",
        "std_cell_library": "samsung14_stdcell",
        "default_clock_period": "1.5",
        "voltage_vdd": "0.8",
        "min_cell_height": "0.1",
        "description": "Samsung 14nm â€” proprietary PDK, manual foundry access required",
        "proprietary": True,
        "fabrication_ready": False,
        "maturity": "proprietary",
    },
    "intel22": {
        "pdk": "intel22",
        "std_cell_library": "intel22_stdcell",
        "default_clock_period": "2.0",
        "voltage_vdd": "0.9",
        "min_cell_height": "0.16",
        "description": "Intel 22nm â€” proprietary PDK, manual foundry access required",
        "proprietary": True,
        "fabrication_ready": False,
        "maturity": "proprietary",
    },
    "gf22": {
        "pdk": "gf22",
        "std_cell_library": "gf22_stdcell",
        "default_clock_period": "2.0",
        "voltage_vdd": "0.8",
        "min_cell_height": "0.16",
        "description": "GlobalFoundries 22nm â€” proprietary PDK, manual foundry access required",
        "proprietary": True,
    },
}


def _normalize_pdk_key(value: str) -> str:
    """Normalize user-facing PDK names, aliases, and directory names."""
    key = os.path.basename(str(value or "").strip()).lower()
    return key.replace("_", "-").replace(" ", "-")


def _candidate_pdk_roots() -> List[str]:
    """Return configured PDK root directories in search order."""
    roots: List[str] = []
    for env_var in ("PDK_ROOT", "OPENLANE_PDK_ROOT", "PDKS_ROOT"):
        val = os.environ.get(env_var, "").strip()
        if val and val not in roots:
            roots.append(val)

    standard_roots = [
        os.path.expanduser("~/.ciel"),
        os.path.expanduser("~/.volare"),
        "/usr/local/pdk",
        "/opt/pdk",
    ]
    for root in standard_roots:
        if root not in roots:
            roots.append(root)

    ol_root = os.environ.get("OPENLANE_ROOT", "").strip()
    if ol_root:
        ol_pdks = os.path.join(ol_root, "pdks")
        if ol_pdks not in roots:
            roots.append(ol_pdks)

    return roots


def _looks_like_pdk_dir(path: str) -> bool:
    """Best-effort check for user-provided PDK directories."""
    if not path or not os.path.isdir(path):
        return False

    strong_markers = [
        os.path.join(path, "libs.ref"),
        os.path.join(path, "libs.tech"),
        os.path.join(path, "libs", "ref"),
        os.path.join(path, "libs", "tech"),
    ]
    if any(os.path.isdir(marker) for marker in strong_markers):
        return True

    marker_files = []
    for root, _dirs, files in os.walk(path):
        rel_depth = os.path.relpath(root, path).count(os.sep)
        if rel_depth > 3:
            _dirs[:] = []
            continue
        for filename in files:
            if filename.endswith((".lib", ".lef", ".tech", ".tf", ".gds", ".spice", ".cdl")):
                marker_files.append(filename)
                if len(marker_files) >= 2:
                    return True
    return False


def _infer_std_cell_library(pdk_dir: str, fallback: str) -> str:
    """Infer a standard-cell library name from common PDK layouts."""
    libs_ref = os.path.join(pdk_dir, "libs.ref")
    if os.path.isdir(libs_ref):
        for child in sorted(os.listdir(libs_ref)):
            verilog_dir = os.path.join(libs_ref, child, "verilog")
            if os.path.isdir(verilog_dir):
                return child

    for root, dirs, files in os.walk(pdk_dir):
        rel_depth = os.path.relpath(root, pdk_dir).count(os.sep)
        if rel_depth > 4:
            dirs[:] = []
            continue
        for filename in files:
            if filename.endswith(".lib"):
                return os.path.splitext(filename)[0]
    return fallback


def _custom_pdk_profile(name: str, root_path: Optional[str] = None) -> Dict[str, Any]:
    key = _normalize_pdk_key(name) or "custom"
    pdk_dir = os.path.basename(os.path.abspath(root_path)) if root_path else key
    std_cell_library = _infer_std_cell_library(root_path, f"{key}_stdcells") if root_path else f"{key}_stdcells"
    return {
        "profile": key,
        "pdk": pdk_dir,
        "std_cell_library": std_cell_library,
        "default_clock_period": "10.0",
        "voltage_vdd": "1.8",
        "min_cell_height": "0.46",
        "description": "Custom user-provided PDK detected from filesystem",
        "available": bool(root_path),
        "root_path": root_path or "",
        "lib_path": "",
        "tech_ok": False,
        "custom": True,
    }


def _infer_node_nm(*values: str) -> Optional[int]:
    """Infer process node from profile names like sky130, open28, or samsung14."""
    combined = " ".join(str(v or "") for v in values).lower()
    matches = re.findall(r"(?<!\d)(\d{1,3})\s*(?:nm|n)?(?!\d)", combined)
    if not matches:
        return None
    plausible = [int(m) for m in matches if 1 <= int(m) <= 1000]
    return min(plausible) if plausible else None


def detect_available_pdks() -> Dict[str, Dict[str, Any]]:
    """Auto-detect which PDKs are installed on this system.

    Searches in order:
      1. $PDK_ROOT/{pdk_name}              (user-configured PDK root)
      2. $PDK_ROOT/{pdk}                   (variant name)
      3. $OPENLANE_ROOT/pdks/{pdk_name}    (OpenLane bundled PDKs)
      4. ~/.ciel/{pdk_name}                (Ciel/volare default)
      5. ~/.volare/{pdk_name}              (Volare PDK manager)
      6. /usr/local/pdk/{pdk_name}

    A PDK is considered "available" when:
      - libs.ref/{lib_name}/verilog/*.v  exists  (cell models)
      - libs.tech/magic/{pdk}.tech exists OR  (Magic DRC)
        libs.tech/netgen/{pdk}_tech.setup  exists (Netgen LVS)

    Returns:
        Dict mapping detected profile name -> its profile dict (with extra fields:
        'available', 'root_path', 'lib_path')
    """
    available = {}

    # Collect all candidate roots to scan
    pdk_roots = _candidate_pdk_roots()

    for profile_name, profile_data in PDK_PROFILES.items():
        pdk_name = profile_data["pdk"]
        lib_name = profile_data["std_cell_library"]
        found_root = None
        found_lib_dir = None

        for root in pdk_roots:
            if not root or not os.path.isdir(root):
                continue

            # Try {root}/{pdk_name}  e.g. ~/.ciel/sky130A
            candidate = os.path.join(root, pdk_name)
            if os.path.isdir(candidate):
                # Check for libs.ref/{lib}/verilog/
                lib_dir = os.path.join(candidate, "libs.ref", lib_name, "verilog")
                if os.path.isdir(lib_dir):
                    found_root = candidate
                    found_lib_dir = lib_dir
                    break

            # Try {root}/{profile_name}  e.g. ~/.ciel/sky130
            candidate2 = os.path.join(root, profile_name)
            if os.path.isdir(candidate2):
                lib_dir2 = os.path.join(candidate2, "libs.ref", lib_name, "verilog")
                if os.path.isdir(lib_dir2):
                    found_root = candidate2
                    found_lib_dir = lib_dir2
                    break

        # Also check OpenLane's pdks subdirectory
        ol_root = os.environ.get("OPENLANE_ROOT", "").strip()
        if not found_root and ol_root:
            ol_pdk_dir = os.path.join(ol_root, "pdks", pdk_name)
            if os.path.isdir(ol_pdk_dir):
                lib_dir = os.path.join(ol_pdk_dir, "libs.ref", lib_name, "verilog")
                if os.path.isdir(lib_dir):
                    found_root = ol_pdk_dir
                    found_lib_dir = lib_dir

        # Validate tech files exist
        tech_ok = False
        if found_root:
            tech_candidates = [
                os.path.join(found_root, "libs.tech", "magic", f"{pdk_name}.tech"),
                os.path.join(
                    found_root, "libs.tech", "netgen", f"{pdk_name}_tech.setup"
                ),
                os.path.join(found_root, "libs", "tech", "magic", f"{pdk_name}.tech"),
            ]
            tech_ok = any(os.path.isfile(p) for p in tech_candidates)

        if found_root:
            result = dict(profile_data)
            result["profile"] = profile_name
            result["available"] = True
            result["root_path"] = found_root
            result["lib_path"] = found_lib_dir
            result["tech_ok"] = tech_ok
            available[profile_name] = result

    known_dirs = {
        _normalize_pdk_key(profile.get("pdk", name))
        for name, profile in PDK_PROFILES.items()
    }
    known_dirs.update(_normalize_pdk_key(name) for name in PDK_PROFILES)
    known_roots = {os.path.abspath(info.get("root_path", "")) for info in available.values()}

    # Add any user-provided/custom PDK directories found under PDK roots.
    for root in pdk_roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for entry in entries:
            child = os.path.join(root, entry)
            if not os.path.isdir(child):
                continue
            child_abs = os.path.abspath(child)
            key = _normalize_pdk_key(entry)
            if key in known_dirs or child_abs in known_roots or key in available:
                continue
            if not _looks_like_pdk_dir(child):
                continue

            custom = _custom_pdk_profile(entry, child)
            custom["available"] = True
            custom["root_path"] = child
            custom["tech_ok"] = bool(
                os.path.isdir(os.path.join(child, "libs.tech"))
                or os.path.isdir(os.path.join(child, "libs", "tech"))
            )
            available[key] = custom

    return available


DEFAULT_PDK_PROFILE = os.environ.get("PDK_PROFILE", "sky130").strip().lower()
# Normalize legacy aliases
_PDK_ALIASES = {
    "gf180": "gf180mcu",
    "gf180mcuc": "gf180mcu",
    "asap7": "asap7",
    "asap5": "asap5",
    "asap2": "asap2",
    "nangate45": "nangate45",
    "freepdk45": "freepdk45",
    "open28": "open28",
    "open-28": "open28",
    "open28nm": "open28",
    "osu018": "osu018",
    "osu035": "osu035",
    "sky130": "sky130",
    "sky130a": "sky130",
    "openfasoc": "openfasoc130",
    "openfasoc-130": "openfasoc130",
    "skywater_raw": "skywater-raw",
    "skywater-pdk": "skywater-raw",
    "lefdef175": "lefdef175",
    "tsmc-28": "tsmc28",
    "tsmc28nm": "tsmc28",
    "samsung-14": "samsung14",
    "samsung14nm": "samsung14",
    "intel-22": "intel22",
    "intel22nm": "intel22",
    "gf-22": "gf22",
    "gf22nm": "gf22",
}
if DEFAULT_PDK_PROFILE not in PDK_PROFILES:
    DEFAULT_PDK_PROFILE = _PDK_ALIASES.get(DEFAULT_PDK_PROFILE, "sky130")
if DEFAULT_PDK_PROFILE not in PDK_PROFILES:
    DEFAULT_PDK_PROFILE = "sky130"

# Tool Settings
PDK_ROOT = os.environ.get("PDK_ROOT", os.path.expanduser("~/.ciel"))
PDK = os.environ.get("PDK", PDK_PROFILES[DEFAULT_PDK_PROFILE]["pdk"])

# OpenLane image used by the default Docker hardening backend.
OPENLANE_IMAGE = os.environ.get(
    "OPENLANE_IMAGE",
    "ghcr.io/the-openroad-project/openlane:ff5509f65b17bfa4068d5336495ab1718987ff69",
)

# Simulation/Coverage adapter defaults
SIM_BACKEND_DEFAULT = os.environ.get("SIM_BACKEND_DEFAULT", "auto").strip().lower()
if SIM_BACKEND_DEFAULT not in {"auto", "verilator", "iverilog"}:
    SIM_BACKEND_DEFAULT = "auto"

COVERAGE_FALLBACK_POLICY_DEFAULT = (
    os.environ.get("COVERAGE_FALLBACK_POLICY", "fallback_oss").strip().lower()
)
if COVERAGE_FALLBACK_POLICY_DEFAULT not in {"fail_closed", "fallback_oss", "skip"}:
    COVERAGE_FALLBACK_POLICY_DEFAULT = "fallback_oss"

COVERAGE_PROFILE_DEFAULT = (
    os.environ.get("COVERAGE_PROFILE", "balanced").strip().lower()
)
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
        # Some older AgentIC installers extracted the archive into
        # $OSS_CAD_SUITE_HOME/oss-cad-suite.  Keep detecting that layout so
        # existing machines recover without manual PATH surgery.
        roots.append(os.path.join(oss_home, "oss-cad-suite"))
    roots.append(os.path.expanduser("~/oss-cad-suite"))
    roots.append(os.path.expanduser("~/oss-cad-suite/oss-cad-suite"))
    roots.append(os.path.join(WORKSPACE_ROOT, "oss-cad-suite"))
    roots.append(os.path.join(WORKSPACE_ROOT, "oss-cad-suite", "oss-cad-suite"))

    for root in roots:
        candidate = os.path.join(root, "bin", bin_name)
        if os.path.exists(candidate):
            return candidate

    # Final fallback: rely on PATH
    return bin_name


OSS_CAD_SUITE_ROOT = os.environ.get(
    "OSS_CAD_SUITE_HOME", os.path.join(WORKSPACE_ROOT, "oss-cad-suite")
)

SBY_BIN = _resolve_tool_binary("sby", env_var="SBY_BIN")
YOSYS_BIN = _resolve_tool_binary("yosys", env_var="YOSYS_BIN")
EQY_BIN = _resolve_tool_binary("eqy", env_var="EQY_BIN")
VERILATOR_BIN = _resolve_tool_binary("verilator", env_var="VERILATOR_BIN")
IVERILOG_BIN = _resolve_tool_binary("iverilog", env_var="IVERILOG_BIN")
VVP_BIN = _resolve_tool_binary("vvp", env_var="VVP_BIN")
SV2V_BIN = _resolve_tool_binary("sv2v", env_var="SV2V_BIN")
OPENSTA_BIN = _resolve_tool_binary("sta", env_var="OPENSTA_BIN")
MAGIC_BIN = _resolve_tool_binary("magic", env_var="MAGIC_BIN")
NETGEN_BIN = _resolve_tool_binary("netgen", env_var="NETGEN_BIN")
NGSPICE_BIN = _resolve_tool_binary("ngspice", env_var="NGSPICE_BIN")


def get_pdk_profile(profile: Optional[str]) -> Dict[str, Any]:
    key = _normalize_pdk_key(profile or DEFAULT_PDK_PROFILE)
    key = _PDK_ALIASES.get(key, key)
    if key not in PDK_PROFILES:
        expanded = os.path.expanduser(str(profile or "").strip())
        if expanded and os.path.isdir(expanded):
            return _custom_pdk_profile(os.path.basename(expanded), expanded)

        detected = detect_available_pdks()
        if key in detected:
            return dict(detected[key])

        pdk_root = os.environ.get("PDK_ROOT", "").strip()
        candidate = os.path.join(pdk_root, str(profile or "").strip()) if pdk_root else ""
        if candidate and os.path.isdir(candidate):
            return _custom_pdk_profile(profile or os.path.basename(candidate), candidate)

        key = "sky130"
    data = dict(PDK_PROFILES[key])
    data["profile"] = key
    return data


PDK_TOOL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "sky130": {
        "max_reliable_mhz": 150,
        "upper_limit_mhz": 200,
        "metal_layers": 6,
        "lc_area_um2": 0.054,
        "openlane_max_routing_layer": "met5",
        "enforce_openlane_max_routing_layer": False,
        "min_die_um": 250,
        "default_core_util": 45,
        "max_core_util": 55,
        "grt_adjustment": 0.15,
        "drc_rules": "sky130A.drc",
        "lvs_rules": "sky130A.lvs",
        "timing_libs": ["libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"],
    },
    "gf180mcu": {
        "max_reliable_mhz": 100,
        "upper_limit_mhz": 125,
        "metal_layers": 5,
        "lc_area_um2": 0.036,
        "openlane_max_routing_layer": "Metal5",
        "enforce_openlane_max_routing_layer": False,
        "min_die_um": 300,
        "default_core_util": 40,
        "max_core_util": 50,
        "grt_adjustment": 0.18,
        "drc_rules": "gf180mcu.drc",
        "lvs_rules": "gf180mcu.lvs",
        "timing_libs": ["libs.ref/gf180mcu_fd_sc_mcu7t5v0/lib/*.lib"],
    },
    "asap7": {
        "max_reliable_mhz": 1000,
        "upper_limit_mhz": 1200,
        "metal_layers": 9,
        "lc_area_um2": 0.005,
        "openlane_max_routing_layer": "M9",
        "enforce_openlane_max_routing_layer": False,
        "min_die_um": 100,
        "default_core_util": 35,
        "max_core_util": 45,
        "grt_adjustment": 0.25,
        "drc_rules": "asap7.drc",
        "lvs_rules": "asap7.lvs",
        "timing_libs": ["libs.ref/asap7sc7p5t/lib/*.lib"],
        "advanced_node": True,
        "multi_patterning": True,
        "double_patterning_warning": True,
        "euv_placeholder": True,
    },
    "nangate45": {
        "max_reliable_mhz": 500,
        "upper_limit_mhz": 600,
        "metal_layers": 10,
        "lc_area_um2": 0.028,
        "openlane_max_routing_layer": "metal10",
        "enforce_openlane_max_routing_layer": False,
        "min_die_um": 150,
        "default_core_util": 40,
        "max_core_util": 55,
        "grt_adjustment": 0.20,
        "drc_rules": "nangate45.drc",
        "lvs_rules": "nangate45.lvs",
        "timing_libs": ["libs.ref/NangateOpenCellLibrary/lib/*.lib"],
    },
    "freepdk45": {
        "max_reliable_mhz": 500,
        "upper_limit_mhz": 600,
        "metal_layers": 10,
        "lc_area_um2": 0.028,
        "openlane_max_routing_layer": "metal10",
        "enforce_openlane_max_routing_layer": False,
        "min_die_um": 150,
        "default_core_util": 40,
        "max_core_util": 55,
        "grt_adjustment": 0.20,
        "drc_rules": "FreePDK45.drc",
        "lvs_rules": "FreePDK45.lvs",
        "timing_libs": ["libs.ref/NangateOpenCellLibrary/lib/*.lib"],
    },
    "osu018": {
        "max_reliable_mhz": 100,
        "upper_limit_mhz": 125,
        "metal_layers": 6,
        "lc_area_um2": 0.036,
        "openlane_max_routing_layer": "Metal6",
        "enforce_openlane_max_routing_layer": False,
        "min_die_um": 300,
        "default_core_util": 40,
        "max_core_util": 50,
        "grt_adjustment": 0.18,
        "drc_rules": "osu018.drc",
        "lvs_rules": "osu018.lvs",
        "timing_libs": ["libs.ref/osu018_stdcells/lib/*.lib"],
    },
    "asap5": {
        "max_reliable_mhz": 2000,
        "upper_limit_mhz": 3000,
        "metal_layers": 10,
        "lc_area_um2": 0.008,
        "openlane_max_routing_layer": "M10",
        "enforce_openlane_max_routing_layer": False,
        "min_die_um": 90,
        "default_core_util": 35,
        "max_core_util": 45,
        "grt_adjustment": 0.28,
        "drc_rules": "asap5.drc",
        "lvs_rules": "asap5.lvs",
        "timing_libs": ["libs.ref/asap5sc/lib/*.lib"],
    },
    "asap2": {
        "max_reliable_mhz": 3000,
        "upper_limit_mhz": 5000,
        "metal_layers": 12,
        "lc_area_um2": 0.003,
        "openlane_max_routing_layer": "M12",
        "enforce_openlane_max_routing_layer": False,
        "min_die_um": 80,
        "default_core_util": 32,
        "max_core_util": 42,
        "grt_adjustment": 0.30,
        "drc_rules": "asap2.drc",
        "lvs_rules": "asap2.lvs",
        "timing_libs": ["libs.ref/asap2sc/lib/*.lib"],
    },
    "open28": {
        "max_reliable_mhz": 800,
        "upper_limit_mhz": 1200,
        "metal_layers": 8,
        "lc_area_um2": 0.015,
        "openlane_max_routing_layer": "M8",
        "enforce_openlane_max_routing_layer": False,
        "min_die_um": 120,
        "default_core_util": 35,
        "max_core_util": 48,
        "grt_adjustment": 0.24,
        "drc_rules": "open28.drc",
        "lvs_rules": "open28.lvs",
        "timing_libs": ["libs.ref/open28_stdcells/lib/*.lib"],
    },
    "tsmc28": {
        "max_reliable_mhz": 1200,
        "upper_limit_mhz": 1800,
        "metal_layers": 8,
        "lc_area_um2": 0.015,
        "openlane_max_routing_layer": "",
        "enforce_openlane_max_routing_layer": False,
        "min_die_um": 120,
        "default_core_util": 35,
        "max_core_util": 48,
        "grt_adjustment": 0.24,
        "drc_rules": "tsmc28.drc",
        "lvs_rules": "tsmc28.lvs",
        "timing_libs": ["libs.ref/tsmc28_stdcell/lib/*.lib"],
        "manual_collateral_required": True,
    },
    "samsung14": {
        "max_reliable_mhz": 1600,
        "upper_limit_mhz": 2400,
        "metal_layers": 10,
        "lc_area_um2": 0.008,
        "openlane_max_routing_layer": "",
        "enforce_openlane_max_routing_layer": False,
        "min_die_um": 100,
        "default_core_util": 32,
        "max_core_util": 42,
        "grt_adjustment": 0.28,
        "drc_rules": "samsung14.drc",
        "lvs_rules": "samsung14.lvs",
        "timing_libs": ["libs.ref/samsung14_stdcell/lib/*.lib"],
        "manual_collateral_required": True,
    },
    "intel22": {
        "max_reliable_mhz": 1400,
        "upper_limit_mhz": 2000,
        "metal_layers": 9,
        "lc_area_um2": 0.012,
        "openlane_max_routing_layer": "",
        "enforce_openlane_max_routing_layer": False,
        "min_die_um": 110,
        "default_core_util": 35,
        "max_core_util": 45,
        "grt_adjustment": 0.26,
        "drc_rules": "intel22.drc",
        "lvs_rules": "intel22.lvs",
        "timing_libs": ["libs.ref/intel22_stdcell/lib/*.lib"],
        "manual_collateral_required": True,
    },
    "gf22": {
        "max_reliable_mhz": 1400,
        "upper_limit_mhz": 2000,
        "metal_layers": 9,
        "lc_area_um2": 0.012,
        "openlane_max_routing_layer": "",
        "enforce_openlane_max_routing_layer": False,
        "min_die_um": 110,
        "default_core_util": 35,
        "max_core_util": 45,
        "grt_adjustment": 0.26,
        "drc_rules": "gf22.drc",
        "lvs_rules": "gf22.lvs",
        "timing_libs": ["libs.ref/gf22_stdcell/lib/*.lib"],
        "manual_collateral_required": True,
    },
}


def get_pdk_tool_config(pdk: Optional[str] = None) -> Dict[str, Any]:
    """Return normalized tool configuration for a known or custom PDK."""
    raw_key = _normalize_pdk_key(pdk or DEFAULT_PDK_PROFILE)
    key = _PDK_ALIASES.get(raw_key, raw_key)
    profile = get_pdk_profile(key)
    profile_key = profile.get("profile", key)
    tool_defaults = PDK_TOOL_CONFIGS.get(profile_key, {})

    config: Dict[str, Any] = {
        "pdk_dir": profile.get("pdk", pdk or PDK),
        "std_cell_library": profile.get("std_cell_library", "sky130_fd_sc_hd"),
        "default_clock_period": profile.get("default_clock_period", "10.0"),
        "max_reliable_mhz": 150,
        "upper_limit_mhz": 200,
        "metal_layers": 6,
        "voltage_vdd": profile.get("voltage_vdd", "1.8"),
        "min_cell_height": profile.get("min_cell_height", "0.46"),
        "lc_area_um2": 0.054,
        "openlane_max_routing_layer": "",
        "enforce_openlane_max_routing_layer": False,
        "min_die_um": 250,
        "default_core_util": 40,
        "max_core_util": 50,
        "grt_adjustment": 0.20,
        "drc_rules": f"{profile.get('pdk', pdk or PDK)}.drc",
        "lvs_rules": f"{profile.get('pdk', pdk or PDK)}.lvs",
        "timing_libs": [],
        "proprietary": bool(profile.get("proprietary")),
        "custom": bool(profile.get("custom")),
    }
    config.update(tool_defaults)
    config["pdk_dir"] = profile.get("pdk", config["pdk_dir"])
    config["std_cell_library"] = profile.get("std_cell_library", config["std_cell_library"])
    config["voltage_vdd"] = profile.get("voltage_vdd", config["voltage_vdd"])
    return config


def get_pdk_flow_capabilities(pdk: Optional[str] = None) -> Dict[str, Any]:
    """Return node-aware guidance used by autonomous RTL-to-GDS flow stages.

    This is deliberately capability-oriented rather than vendor-specific: known
    PDKs get tuned defaults, while custom/proprietary PDKs still receive safe
    conservative behavior as soon as their collateral is discoverable.
    """
    profile = get_pdk_profile(pdk or DEFAULT_PDK_PROFILE)
    tool = get_pdk_tool_config(pdk or profile.get("profile") or profile.get("pdk"))
    node_nm = _infer_node_nm(
        profile.get("profile", ""),
        profile.get("pdk", ""),
        profile.get("description", ""),
    )
    profile_key = profile.get("profile", _normalize_pdk_key(pdk or "custom"))
    maturity = profile.get("maturity", "custom" if profile.get("custom") else "unknown")
    advanced_node = bool(tool.get("advanced_node")) or (
        node_nm is not None and node_nm <= 28
    ) or profile_key.startswith("asap")
    legacy_node = bool(node_nm is not None and node_nm >= 130)
    proprietary = bool(profile.get("proprietary") or tool.get("manual_collateral_required"))
    custom = bool(profile.get("custom"))

    if advanced_node:
        node_class = "advanced"
        rc_risk = "high"
        rtl_guidance = (
            "Treat wires and routing congestion as first-order constraints: pipeline wide "
            "datapaths, keep fanout local, and map memories to macros."
        )
    elif legacy_node:
        node_class = "legacy"
        rc_risk = "moderate"
        rtl_guidance = (
            "Logic depth is usually more important than wire RC: keep combinational cones "
            "short, but use fewer speculative pipeline stages than advanced nodes."
        )
    else:
        node_class = "generic"
        rc_risk = "unknown"
        rtl_guidance = (
            "Use conservative timing, explicit resets, bounded fanout, and memory macros "
            "for large arrays until PDK-specific data proves tighter limits."
        )

    collateral_ready = bool(profile.get("available")) and bool(profile.get("tech_ok", True))
    flow_status = "ready" if collateral_ready else "requires_pdk_collateral"
    if proprietary:
        flow_status = "requires_authorized_foundry_collateral"
    elif custom and not collateral_ready:
        flow_status = "requires_custom_pdk_validation"

    return {
        "profile": profile_key,
        "pdk": profile.get("pdk", pdk or PDK),
        "std_cell_library": profile.get("std_cell_library", ""),
        "node_nm": node_nm,
        "node_class": node_class,
        "maturity": maturity,
        "advanced_node": advanced_node,
        "legacy_node": legacy_node,
        "proprietary": proprietary,
        "custom": custom,
        "flow_status": flow_status,
        "fabrication_ready": bool(profile.get("fabrication_ready")) and collateral_ready,
        "collateral_ready": collateral_ready,
        "wire_rc_risk": rc_risk,
        "metal_layers": int(tool.get("metal_layers", 6) or 6),
        "max_routing_layer": str(tool.get("openlane_max_routing_layer", "") or ""),
        "enforce_max_routing_layer": bool(tool.get("enforce_openlane_max_routing_layer", False)),
        "lc_area_um2": float(tool.get("lc_area_um2", 0.054) or 0.054),
        "min_die_um": int(tool.get("min_die_um", 250) or 250),
        "default_core_util": int(tool.get("default_core_util", 40) or 40),
        "max_core_util": int(tool.get("max_core_util", 50) or 50),
        "grt_adjustment": float(tool.get("grt_adjustment", 0.20) or 0.20),
        "memory_macro_threshold_bytes": 1024,
        "rtl_guidance": rtl_guidance,
        "tool_config": tool,
    }


class PDKError(Exception):
    """Base exception for PDK-related errors."""

    def __init__(
        self,
        message: str,
        suggested_action: str = "",
        available_pdks: Optional[List[str]] = None,
    ):
        self.message = message
        self.suggested_action = suggested_action
        self.available_pdks = available_pdks or []
        super().__init__(message)


class PDKNotInstalledError(PDKError):
    """Raised when a requested PDK is not installed on the system."""


class PDKNotFoundError(PDKError):
    """Raised when no PDK is available on the system."""


def _find_pdk_root() -> Optional[str]:
    """Find a PDK root directory from the environment or common locations."""
    for root in _candidate_pdk_roots():
        if root and os.path.isdir(root):
            return root
    return None


def _parent_pdk_root(pdk_dir_path: str) -> str:
    return os.path.dirname(os.path.abspath(pdk_dir_path)) if pdk_dir_path else ""


def resolve_pdk(
    requested_pdk: Optional[str] = None,
    design_path: Optional[str] = None,
    pdk_root: Optional[str] = None,
    required: bool = True,
) -> Tuple[str, Dict[str, Any], str]:
    """Resolve a PDK to (pdk_dir, profile_dict, pdk_root_parent)."""
    detected = detect_available_pdks()
    resolved_pdk_root = pdk_root or _find_pdk_root() or ""

    if requested_pdk:
        requested_raw = str(requested_pdk).strip()
        expanded = os.path.expanduser(requested_raw)
        if os.path.isdir(expanded):
            profile = _custom_pdk_profile(os.path.basename(expanded), expanded)
            return profile["pdk"], profile, _parent_pdk_root(expanded)

        key = _PDK_ALIASES.get(_normalize_pdk_key(requested_raw), _normalize_pdk_key(requested_raw))
        if key in detected:
            profile = dict(detected[key])
            return profile["pdk"], profile, _parent_pdk_root(profile.get("root_path", ""))

        candidate = os.path.join(resolved_pdk_root, requested_raw) if resolved_pdk_root else ""
        if candidate and os.path.isdir(candidate) and _looks_like_pdk_dir(candidate):
            profile = _custom_pdk_profile(requested_raw, candidate)
            return profile["pdk"], profile, _parent_pdk_root(candidate)

        profile = get_pdk_profile(key)
        if required:
            raise PDKNotInstalledError(
                message=f"PDK '{requested_pdk}' is not installed on this system.",
                suggested_action=f"Run: agentic install-pdk {requested_pdk}",
                available_pdks=sorted(detected.keys()),
            )
        return profile.get("pdk", key), profile, resolved_pdk_root

    if design_path:
        design_lower = design_path.lower()
        for profile_name, profile in detected.items():
            if profile_name in design_lower or str(profile.get("pdk", "")).lower() in design_lower:
                return profile["pdk"], profile, _parent_pdk_root(profile.get("root_path", ""))

    if detected:
        first = sorted(detected.keys())[0]
        profile = dict(detected[first])
        return profile["pdk"], profile, _parent_pdk_root(profile.get("root_path", ""))

    if required:
        raise PDKNotFoundError(
            message="No PDK installed on this system.",
            suggested_action="Run: agentic install-pdk sky130",
            available_pdks=[],
        )

    profile = get_pdk_profile("sky130")
    return profile["pdk"], profile, resolved_pdk_root


def validate_pdk_installation(pdk: str, pdk_root: Optional[str] = None) -> Tuple[bool, List[str]]:
    """Validate that a PDK has enough structure for AgentIC tool flows."""
    messages: List[str] = []
    try:
        resolved_pdk, profile, resolved_root = resolve_pdk(
            requested_pdk=pdk,
            pdk_root=pdk_root,
            required=True,
        )
    except PDKError as exc:
        return False, [exc.message, exc.suggested_action]

    pdk_dir = os.path.join(resolved_root, resolved_pdk) if resolved_root else profile.get("root_path", "")
    if not pdk_dir or not os.path.isdir(pdk_dir):
        return False, [f"PDK directory not found for {pdk}: {pdk_dir or 'unknown'}"]

    std_cell_library = profile.get("std_cell_library", "")
    lib_dir = os.path.join(pdk_dir, "libs.ref", std_cell_library, "verilog")
    if std_cell_library and os.path.isdir(lib_dir):
        messages.append(f"Found standard-cell Verilog models: {lib_dir}")
    else:
        messages.append(f"Standard-cell Verilog models not found for {std_cell_library}")

    tech_markers = [
        os.path.join(pdk_dir, "libs.tech", "magic"),
        os.path.join(pdk_dir, "libs.tech", "netgen"),
        os.path.join(pdk_dir, "libs", "tech"),
    ]
    if any(os.path.isdir(path) for path in tech_markers):
        messages.append("Found physical verification tech files.")
    else:
        messages.append("Physical verification tech files were not found.")

    ok = any("Found" in msg for msg in messages)
    return ok, messages


def list_pdk_profiles() -> Dict[str, Dict[str, Any]]:
    """Return all known PDK profile definitions (metadata only, no filesystem check)."""
    return {k: {kk: vv for kk, vv in v.items()} for k, v in PDK_PROFILES.items()}


def get_toolchain_diagnostics() -> Dict[str, Any]:
    """Return resolved toolchain paths and existence info for startup checks."""
    bins = {
        "sby": SBY_BIN,
        "yosys": YOSYS_BIN,
        "eqy": EQY_BIN,
        "verilator": VERILATOR_BIN,
        "iverilog": IVERILOG_BIN,
        "vvp": VVP_BIN,
        "sv2v": SV2V_BIN,
        "opensta": OPENSTA_BIN,
        "magic": MAGIC_BIN,
        "netgen": NETGEN_BIN,
        "ngspice": NGSPICE_BIN,
    }
    detected_pdks = detect_available_pdks()
    return {
        "workspace_root": WORKSPACE_ROOT,
        "openlane_root": OPENLANE_ROOT,
        "pdk_root": PDK_ROOT,
        "pdk": PDK,
        "oss_cad_suite_home": os.environ.get("OSS_CAD_SUITE_HOME", ""),
        "llm_model": LLM_MODEL,
        "llm_base_url": LLM_BASE_URL,
        "available_pdks": {
            name: {
                "available": info.get("available", False),
                "root_path": info.get("root_path", ""),
                "tech_ok": info.get("tech_ok", False),
                "description": info.get("description", ""),
            }
            for name, info in detected_pdks.items()
        },
        "bins": {
            name: {
                "path": path,
                "exists": os.path.exists(path) if os.path.isabs(path) else False,
            }
            for name, path in bins.items()
        },
    }
