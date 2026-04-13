import json
import os
import platform as _platform
import tempfile
from typing import Dict, Any, Optional
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

_DEFAULT_LLM_CONFIG = {
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
        os.environ.get("LLM_API_KEY", "").strip() or _DEFAULT_GROUP.get("api_key", "")
    ),
}

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


PDK_PROFILES: Dict[str, Dict[str, Any]] = {
    "sky130": {
        "pdk": "sky130A",
        "std_cell_library": "sky130_fd_sc_hd",
        "default_clock_period": "10.0",
        "voltage_vdd": "1.8",
        "min_cell_height": "0.46",
        "description": "SkyWater 130nm — most mature open PDK, best tool support",
    },
    "gf180mcu": {
        "pdk": "gf180mcuC",
        "std_cell_library": "gf180mcu_fd_sc_mcu7t5v0",
        "default_clock_period": "15.0",
        "voltage_vdd": "1.8",
        "min_cell_height": "0.54",
        "description": "GlobalFoundries 180nm — automotive grade, high voltage options",
    },
    "asap7": {
        "pdk": "asap7",
        "std_cell_library": "asap7sc7p5t",
        "default_clock_period": "5.0",
        "voltage_vdd": "0.7",
        "min_cell_height": "0.144",
        "description": "ASAP 7nm — cutting-edge predictive PDK, high density",
    },
    "nangate45": {
        "pdk": "nangate45",
        "std_cell_library": "NangateOpenCellLibrary",
        "default_clock_period": "10.0",
        "voltage_vdd": "1.1",
        "min_cell_height": "0.4",
        "description": "NanGate 45nm — academic/research, clean and simple",
    },
    "osu018": {
        "pdk": "osu018",
        "std_cell_library": "osu018_stdcells",
        "default_clock_period": "12.0",
        "voltage_vdd": "1.8",
        "min_cell_height": "0.5",
        "description": "Oklahoma State 180nm — educational/research focus",
    },
    "osu035": {
        "pdk": "osu035",
        "std_cell_library": "osu035_stdcells",
        "default_clock_period": "15.0",
        "voltage_vdd": "3.3",
        "min_cell_height": "0.6",
        "description": "Oklahoma State 350nm — high voltage, easy to probe",
    },
    "efly45": {
        "pdk": "efly45",
        "std_cell_library": "sky130_fd_sc_hd",
        "default_clock_period": "10.0",
        "voltage_vdd": "1.8",
        "min_cell_height": "0.46",
        "description": "EFLY 45nm — emerging foundry, compatible with sky130 libs",
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

    return available


DEFAULT_PDK_PROFILE = os.environ.get("PDK_PROFILE", "sky130").strip().lower()
# Normalize legacy aliases
_PDK_ALIASES = {
    "gf180": "gf180mcu",
    "asap7": "asap7",
    "nangate45": "nangate45",
    "osu018": "osu018",
    "osu035": "osu035",
    "sky130": "sky130",
    "efly45": "efly45",
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
