import json
import os
import platform as _platform
import tempfile
from typing import Dict, Any, Optional, List, Tuple
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


# ─────────────────────────────────────────────────────────────────────────────
# LLM PROVIDER ENVIRONMENT INJECTION
# ─────────────────────────────────────────────────────────────────────────────


def _inject_llm_env_vars(base_url: str, api_key: str) -> None:
    """Propagate LLM endpoint config into os.environ for LiteLLM/httpx."""
    if base_url:
        os.environ["OPENAI_API_BASE"] = base_url
        os.environ["LITELLM_API_BASE"] = base_url
        os.environ["LITELLM_BASE_URL"] = base_url
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key


# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSAL CREDENTIAL RESOLUTION
# ─────────────────────────────────────────────────────────────────────────────


def resolve_llm_config(
    env_var_prefix: str = "LLM",
    credential_group: str = "build",
    fallback_model: str = "openai/gpt-4o",
    fallback_base_url: str = "https://api.openai.com/v1",
) -> Dict[str, str]:
    """Resolve LLM config with priority order: env var > credentials.json > defaults."""
    model = os.environ.get(f"{env_var_prefix}_MODEL", "").strip()
    api_key = os.environ.get(f"{env_var_prefix}_API_KEY", "").strip()
    base_url = os.environ.get(f"{env_var_prefix}_BASE_URL", "").strip()
    group = _credential_group(credential_group)

    return {
        "model": model or group.get("model", "") or fallback_model,
        "base_url": _normalize_base_url(base_url or group.get("base_url", "") or fallback_base_url),
        "api_key": api_key or group.get("api_key", ""),
    }


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

_DEFAULT_LLM_CONFIG = resolve_llm_config(
    env_var_prefix="LLM",
    credential_group="build",
    fallback_model="openai/gpt-4o",
    fallback_base_url="https://api.openai.com/v1",
)

# Propagate into os.environ so LiteLLM/httpx clients route correctly
_inject_llm_env_vars(_DEFAULT_LLM_CONFIG["base_url"], _DEFAULT_LLM_CONFIG["api_key"])

DEFAULT_LLM_CONFIG = _DEFAULT_LLM_CONFIG.copy()
CLOUD_CONFIG = _DEFAULT_LLM_CONFIG.copy()
NVIDIA_CONFIG = _DEFAULT_LLM_CONFIG.copy()
LOCAL_CONFIG = _DEFAULT_LLM_CONFIG.copy()
GROQ_CONFIG = _DEFAULT_LLM_CONFIG.copy()
GLM_CONFIG = _DEFAULT_LLM_CONFIG.copy()
DEEPSEEK_CONFIG = _DEFAULT_LLM_CONFIG.copy()

LLM_MODEL = DEFAULT_LLM_CONFIG["model"]
LLM_BASE_URL = DEFAULT_LLM_CONFIG["base_url"]
LLM_API_KEY = DEFAULT_LLM_CONFIG["api_key"]

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

    model = os.environ.get(f"ROLE_{role_upper}_MODEL", "").strip() or group_cfg.get("model", "")
    base_url = _normalize_base_url(
        os.environ.get(f"ROLE_{role_upper}_BASE_URL", "").strip() or group_cfg.get("base_url", "")
    )
    api_key = os.environ.get(f"ROLE_{role_upper}_API_KEY", "").strip() or group_cfg.get(
        "api_key", ""
    )

    # Apply per-role base_url globally so LiteLLM uses it for this role
    if base_url:
        _inject_llm_env_vars(base_url, api_key)

    return {
        "model": model or DEFAULT_LLM_CONFIG["model"],
        "base_url": base_url or DEFAULT_LLM_CONFIG["base_url"],
        "api_key": api_key or DEFAULT_LLM_CONFIG["api_key"],
    }


# =============================================================================
# PDK Profiles
# =============================================================================
# Each profile defines the PDK variant name, the standard cell library to use,
# timing/voltage parameters for OpenLane config generation, and a human-readable
# description shown in the CLI.
#
# Cell library sources:
#   sky130   / gf180mcu  — managed by Volare/Ciel, installed automatically.
#   asap7                — https://github.com/The-OpenROAD-Project/asap7 (Apache 2.0)
#   nangate45            — NanGate 45nm Open Cell Library via Si2 (Apache 2.0)
#   freepdk45            — FreePDK45 + NangateOpenCellLibrary (NC State / Si2)
#   osu018 / osu035      — Oklahoma State University educational libs (limited cells)
#
# NOTE: efly45 was removed — it had no publicly available cell library and its
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
        "lc_area_um2": 0.054,
        "max_reliable_mhz": 150,
        "upper_limit_mhz": 200,
        "description": "SkyWater 130nm — most mature open PDK, best tool support",
    },
    "gf180mcu": {
        "pdk": "gf180mcuC",
        "std_cell_library": "gf180mcu_fd_sc_mcu7t5v0",
        "default_clock_period": "15.0",
        "voltage_vdd": "1.8",
        "min_cell_height": "0.54",
        "lc_area_um2": 0.036,
        "max_reliable_mhz": 100,
        "upper_limit_mhz": 125,
        "description": "GlobalFoundries 180nm — automotive grade, high voltage options",
    },
    "asap7": {
        "pdk": "asap7",
        "std_cell_library": "asap7sc7p5t",
        "default_clock_period": "5.0",
        "voltage_vdd": "0.7",
        "min_cell_height": "0.144",
        "lc_area_um2": 0.005,
        "max_reliable_mhz": 1000,
        "upper_limit_mhz": 1200,
        "description": "ASAP 7nm predictive PDK — research/academic, not a real foundry",
    },
    "nangate45": {
        "pdk": "nangate45",
        "std_cell_library": "NangateOpenCellLibrary",
        "default_clock_period": "10.0",
        "voltage_vdd": "1.1",
        "min_cell_height": "0.4",
        "lc_area_um2": 0.028,
        "max_reliable_mhz": 500,
        "upper_limit_mhz": 600,
        "description": "NanGate 45nm Open Cell Library — academic/research, Apache 2.0",
    },
    "freepdk45": {
        "pdk": "FreePDK45",
        "std_cell_library": "NangateOpenCellLibrary",
        "default_clock_period": "10.0",
        "voltage_vdd": "1.1",
        "min_cell_height": "0.4",
        "lc_area_um2": 0.028,
        "max_reliable_mhz": 500,
        "upper_limit_mhz": 600,
        "description": "FreePDK45 (NC State 45nm) + NanGate Open Cell Library — academic/research",
    },
    "osu018": {
        "pdk": "osu018",
        "std_cell_library": "osu018_stdcells",
        "default_clock_period": "12.0",
        "voltage_vdd": "1.8",
        "min_cell_height": "0.5",
        "lc_area_um2": 0.036,
        "max_reliable_mhz": 100,
        "upper_limit_mhz": 125,
        "description": "Oklahoma State 180nm — educational/research, limited cell set",
    },
    "osu035": {
        "pdk": "osu035",
        "std_cell_library": "osu035_stdcells",
        "default_clock_period": "15.0",
        "voltage_vdd": "3.3",
        "min_cell_height": "0.6",
        "lc_area_um2": 0.064,
        "max_reliable_mhz": 50,
        "upper_limit_mhz": 75,
        "description": "Oklahoma State 350nm — high voltage, easy to probe, educational",
    },
}


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
    import glob as _glob
    import subprocess as _subprocess

    available = {}

    # Collect all candidate roots to scan
    pdk_roots = []
    for env_var in ("PDK_ROOT", "OPENLANE_PDK_ROOT", "PDKS_ROOT"):
        val = os.environ.get(env_var, "").strip()
        if val and val not in pdk_roots:
            pdk_roots.append(val)

    # Standard OSS-CAD / CIEL / Volare locations
    standard_roots = [
        os.path.expanduser("~/.ciel"),
        os.path.expanduser("~/.volare"),
        "/usr/local/pdk",
        "/opt/pdk",
    ]
    for r in standard_roots:
        if r not in pdk_roots:
            pdk_roots.append(r)

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
                os.path.join(found_root, "libs.tech", "netgen", f"{pdk_name}_tech.setup"),
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

    return available


DEFAULT_PDK_PROFILE = os.environ.get("PDK_PROFILE", "sky130").strip().lower()
# Normalize legacy aliases
_PDK_ALIASES = {
    "gf180": "gf180mcu",
    "asap7": "asap7",
    "nangate45": "nangate45",
    "freepdk45": "freepdk45",
    "osu018": "osu018",
    "osu035": "osu035",
    "sky130": "sky130",
}
if DEFAULT_PDK_PROFILE not in PDK_PROFILES:
    DEFAULT_PDK_PROFILE = _PDK_ALIASES.get(DEFAULT_PDK_PROFILE, "sky130")
if DEFAULT_PDK_PROFILE not in PDK_PROFILES:
    DEFAULT_PDK_PROFILE = "sky130"

# Tool Settings
PDK_ROOT = os.environ.get("PDK_ROOT", os.path.expanduser("~/.ciel"))
PDK = os.environ.get("PDK", PDK_PROFILES[DEFAULT_PDK_PROFILE]["pdk"])

# OpenLane image — auto-detect ARM64 (Oracle A1 Ampere) vs x86_64
_ARCH = "arm64" if _platform.machine() in ("aarch64", "arm64") else "amd64"
OPENLANE_IMAGE = os.environ.get(
    "OPENLANE_IMAGE",
    f"ghcr.io/the-openroad-project/openlane:ff5509f65b17bfa4068d5336495ab1718987ff69-{_ARCH}",
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


def get_pdk_profile(profile: Optional[str]) -> Dict[str, Any]:
    key = (profile or DEFAULT_PDK_PROFILE).strip().lower()
    key = _PDK_ALIASES.get(key, key)
    if key not in PDK_PROFILES:
        key = "sky130"
    data = dict(PDK_PROFILES[key])
    data["profile"] = key
    return data


def get_pdk_tool_config(profile: Optional[str] = None) -> Dict[str, Any]:
    """Get tool-specific configuration values for a PDK.

    This function provides a unified way to get all tool-specific values
    needed by synthesis, DRC, LVS, and other tools. Falls back to
    sky130 defaults if the PDK doesn't specify a value.

    Args:
        profile: PDK profile name (e.g., 'sky130', 'gf180mcu'). Uses default if None.

    Returns:
        Dict with keys:
        - lc_area_um2: Logic cell area in square micrometers
        - max_reliable_mhz: Maximum reliable clock frequency
        - upper_limit_mhz: Upper frequency limit (warning threshold)
        - std_cell_library: Standard cell library name
        - pdk_dir: PDK directory name (for tool paths)
    """
    pdk_data = get_pdk_profile(profile)
    return {
        "lc_area_um2": pdk_data.get("lc_area_um2", 0.054),
        "max_reliable_mhz": pdk_data.get("max_reliable_mhz", 150),
        "upper_limit_mhz": pdk_data.get("upper_limit_mhz", 200),
        "std_cell_library": pdk_data.get("std_cell_library", "sky130_fd_sc_hd"),
        "pdk_dir": pdk_data.get("pdk", "sky130A"),
        "voltage_vdd": pdk_data.get("voltage_vdd", "1.8"),
    }


def list_pdk_profiles() -> Dict[str, Dict[str, Any]]:
    """Return all known PDK profile definitions (metadata only, no filesystem check)."""
    return {k: {kk: vv for kk, vv in v.items()} for k, v in PDK_PROFILES.items()}


class PDKError(Exception):
    """Base exception for PDK-related errors."""

    def __init__(self, message: str, suggested_action: str = "", available_pdks: List[str] = None):
        self.message = message
        self.suggested_action = suggested_action
        self.available_pdks = available_pdks or []
        super().__init__(message)


class PDKNotInstalledError(PDKError):
    """Raised when a requested PDK is not installed on the system."""

    pass


class PDKNotFoundError(PDKError):
    """Raised when no PDK is available on the system."""

    pass


def resolve_pdk(
    requested_pdk: Optional[str] = None,
    design_path: Optional[str] = None,
    pdk_root: Optional[str] = None,
    required: bool = True,
) -> Tuple[str, Dict[str, Any], str]:
    """Resolve PDK with smart auto-detection.

    Resolution order:
    1. Use explicitly requested PDK (--pdk flag)
    2. Detect from design path (designs/sky130_design/ → sky130)
    3. Use first available installed PDK
    4. Error with helpful message

    Args:
        requested_pdk: Explicitly requested PDK name (from --pdk flag)
        design_path: Path to design file (to auto-detect from path)
        pdk_root: Explicit pdk_root path
        required: If True, raises error when no PDK available. If False, returns defaults.

    Returns:
        Tuple of (profile_name, profile_dict, pdk_root_path)

    Raises:
        PDKNotInstalledError: When requested PDK is not installed
        PDKNotFoundError: When no PDK is available and required=True
    """
    detected = detect_available_pdks()
    resolved_pdk_root = pdk_root or _find_pdk_root() or ""

    # Case 1: User specified --pdk
    if requested_pdk:
        requested = requested_pdk.strip().lower()
        profile = get_pdk_profile(requested)["profile"]

        if profile not in detected:
            if required:
                raise PDKNotInstalledError(
                    message=f"PDK '{requested_pdk}' is not installed on this system.",
                    suggested_action=f"Run: agentic install-pdk {requested_pdk}",
                    available_pdks=list(detected.keys()),
                )
            else:
                # Return default profile if not required
                return profile, get_pdk_profile(profile), resolved_pdk_root

        return profile, detected[profile], detected[profile].get("root_path", "")

    # Case 2: Detect from design path
    if design_path:
        design_lower = design_path.lower()
        for profile_name in detected.keys():
            if profile_name in design_lower:
                return (
                    profile_name,
                    detected[profile_name],
                    detected[profile_name].get("root_path", ""),
                )

    # Case 3: Use first available
    if detected:
        first = list(detected.keys())[0]
        return first, detected[first], detected[first].get("root_path", "")

    # Case 4: No PDK available
    if required:
        raise PDKNotFoundError(
            message="No PDK installed on this system.",
            suggested_action="Run: agentic install-pdk sky130",
            available_pdks=[],
        )

    # Case 5: Not required, return defaults
    return "sky130", get_pdk_profile("sky130"), ""


def _find_pdk_root() -> Optional[str]:
    """Find PDK root directory from environment or common locations."""
    import glob

    # Check environment variable
    env_root = os.environ.get("PDK_ROOT", "").strip()
    if env_root and os.path.isdir(env_root):
        return env_root

    # Check common locations
    common_locations = [
        os.path.expanduser("~/.ciel"),
        os.path.expanduser("~/.volare"),
        "/usr/local/pdk",
        "/opt/pdk",
    ]

    for loc in common_locations:
        if os.path.isdir(loc):
            return loc

    return None


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
