#!/usr/bin/env python3
"""
AgentIC - Natural Language to GDSII Pipeline
=============================================
Uses CrewAI + LLM to generate chips from natural language.
Usage:
    python3 main.py build --name counter --desc "8-bit counter with enable and reset"
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from crewai import Agent, Task, Crew, LLM

# Local imports
from .config import (
    OPENLANE_ROOT,
    LLM_MODEL,
    LLM_BASE_URL,
    LLM_API_KEY,
    NVIDIA_CONFIG,
    LOCAL_CONFIG,
    CLOUD_CONFIG,
    PDK,
    SIM_BACKEND_DEFAULT,
    COVERAGE_FALLBACK_POLICY_DEFAULT,
    COVERAGE_PROFILE_DEFAULT,
    detect_available_pdks,
    get_pdk_profile,
    list_pdk_profiles,
)
from .agents.designer import get_designer_agent
from .agents.testbench_designer import get_testbench_agent
from .agents.verifier import get_verification_agent, get_error_analyst_agent
from .tools.vlsi_tools import (
    write_verilog,
    write_config,
    run_syntax_check,
    syntax_check_tool,
    read_file_content,
    read_file_tool,
    run_simulation,
    run_openlane,
    run_verification,
    SecurityCheck,
    write_sby_config,
    run_formal_verification,
    check_physical_metrics,
    run_lint_check,
    run_gls_simulation,
    signoff_check_tool,
    startup_self_check,
)
from .tools.synth_tools import run_yosys_synth, synth_tool
from .tools.sta_tools import run_opensta, run_multi_corner_sta, sta_tool, parse_sdc_file
from .tools.dft_tools import run_scan_insertion, run_testability_analysis, dft_tool
from .tools.power_tools import run_power_analysis, power_tool
from .tools.physical_tools import run_magic_drc, run_netgen_lvs, drc_tool, lvs_tool
from .tools.signoff_reporter import generate_qor_report, SignoffReporter

# --- INITIALIZE ---
app = typer.Typer()
from rich.theme import Theme

claude_theme = Theme(
    {
        "info": "dim white",
        "accent": "#d97757",
        "success": "#32997b",
        "warning": "#e0b04a",
        "error": "#d45851",
        "heading": "bold #e5e1d8",
        "border": "#8f8a80",
        "spinner": "#d97757",
    }
)
console = Console(theme=claude_theme)
# Legacy path for license-based credentials (packaged binary)
CREDENTIALS_FILE = os.path.expanduser("~/.agentic/credentials.json")
LICENSE_VERIFY_URL = "https://api.lemonsqueezy.com/v1/licenses/validate"
LICENSE_TIMEOUT_SECONDS = 20
LICENSE_OFFLINE_GRACE_HOURS = max(
    0,
    int(os.environ.get("AGENTIC_LICENSE_OFFLINE_GRACE_HOURS", "24")),
)


def check_dependencies(skip_openlane: bool):
    """
    Verify all required EDA tools are present before starting a build.
    Uses resolved binary paths from config (OSS CAD Suite aware) rather than
    relying solely on PATH, so it correctly finds tools in OSS_CAD_SUITE_HOME.
    """
    import shutil
    from .config import YOSYS_BIN, VERILATOR_BIN, IVERILOG_BIN, VVP_BIN

    def _tool_present(binary: str) -> bool:
        """Check if binary exists at absolute path or on PATH."""
        if os.path.isabs(binary):
            return os.path.exists(binary)
        return bool(shutil.which(binary))

    missing = []

    # Docker is only needed for OpenLane GDSII hardening
    if not skip_openlane and not shutil.which("docker"):
        missing.append(
            "🐳 [bold red]Docker[/bold red] "
            "(required for OpenLane GDSII hardening — add --skip-openlane to bypass)"
        )

    # All four EDA tools must be present for RTL verification
    for binary, label in [
        (YOSYS_BIN, "yosys (OSS CAD Suite)"),
        (VERILATOR_BIN, "verilator (OSS CAD Suite)"),
        (IVERILOG_BIN, "iverilog (OSS CAD Suite)"),
        (VVP_BIN, "vvp — Icarus runtime (OSS CAD Suite)"),
    ]:
        if not _tool_present(binary):
            missing.append(f"🛠️  [bold red]{label}[/bold red]")

    if missing:
        console.print(
            Panel(
                "AgentIC requires OSS CAD Suite for RTL verification and synthesis.\n\n"
                "Missing tools:\n"
                + "\n".join(["  - " + m for m in missing])
                + "\n\n[info]Install OSS CAD Suite:[/info] "
                "https://github.com/YosysHQ/oss-cad-suite-build/releases\n"
                "[info]After installing, set:[/info] "
                "export OSS_CAD_SUITE_HOME=/path/to/oss-cad-suite\n\n"
                "[info]Tip: Add --skip-openlane to skip GDSII hardening (no Docker needed).[/info]",
                title="[bold red]❌ Missing Dependencies[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(1)


def _is_packaged_runtime() -> bool:
    return bool(getattr(sys, "frozen", False))


def _allow_dev_bypass() -> bool:
    return os.environ.get("AGENTIC_DEV_MODE") == "1" and not _is_packaged_runtime()


def _should_enforce_license() -> bool:
    if _allow_dev_bypass():
        return False
    if os.environ.get("AGENTIC_DISABLE_LICENSE_CHECK") == "1":
        return False
    # Enforce license checks by default for packaged and pip-installed CLI.
    return True


def _mask_secret(value: str) -> str:
    value = (value or "").strip()
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _load_credentials(required: bool = True) -> dict:
    if not os.path.exists(CREDENTIALS_FILE):
        if required:
            console.print(
                Panel(
                    "[error]Authorization Required[/error]\n"
                    "No local AgentIC license was found on this machine.\n"
                    "Please run: [accent]agentic login <your_license_key>[/accent]",
                    title="🔒 License Check Failed",
                )
            )
            raise typer.Exit(1)
        return {}

    try:
        with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        console.print(f"[error]Error reading credentials: {exc}[/error]")
        raise typer.Exit(1)

    if required and not (data.get("license_key") or "").strip():
        console.print(
            Panel(
                "[error]Stored credentials are incomplete.[/error]\n"
                "Please re-run: [accent]agentic login <your_license_key>[/accent]",
                title="🔒 License Check Failed",
            )
        )
        raise typer.Exit(1)
    return data


def _persist_credentials(data: dict) -> None:
    os.makedirs(os.path.dirname(CREDENTIALS_FILE) or ".", exist_ok=True)
    with open(CREDENTIALS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    try:
        os.chmod(CREDENTIALS_FILE, 0o600)
    except OSError:
        pass


def _apply_runtime_keys(data: dict) -> None:
    from . import config

    mappings = (
        ("nvidia_api_key", config.CLOUD_CONFIG, "NVIDIA_API_KEY"),
        ("groq_api_key", config.GROQ_CONFIG, "GROQ_API_KEY"),
        ("glm_api_key", config.GLM_CONFIG, "GLM_API_KEY"),
    )
    for field, cfg, env_name in mappings:
        value = (data.get(field) or "").strip()
        if value:
            cfg["api_key"] = value
            os.environ[env_name] = value


def _validate_license_with_server(key: str) -> tuple[bool, str]:
    import requests

    response = requests.post(
        LICENSE_VERIFY_URL,
        headers={"Accept": "application/json"},
        data={"license_key": key},
        timeout=LICENSE_TIMEOUT_SECONDS,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code != 200:
        return False, payload.get("error") or f"HTTP {response.status_code}"
    if not payload.get("valid"):
        return False, payload.get("error") or "Key rejected by server"
    return True, ""


def _offline_grace_remaining(data: dict) -> timedelta | None:
    raw_value = (data.get("last_verified_at") or "").strip()
    if not raw_value or LICENSE_OFFLINE_GRACE_HOURS <= 0:
        return None
    try:
        verified_at = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)

    deadline = verified_at + timedelta(hours=LICENSE_OFFLINE_GRACE_HOURS)
    remaining = deadline - datetime.now(timezone.utc)
    if remaining.total_seconds() <= 0:
        return None
    return remaining


def _remember_verified_license(data: dict) -> None:
    data["last_verified_at"] = datetime.now(timezone.utc).isoformat()
    _persist_credentials(data)


LICENSE_CACHE_HOURS = max(
    1,
    int(os.environ.get("AGENTIC_LICENSE_CACHE_HOURS", "1")),
)


def _within_verification_cache(data: dict) -> bool:
    raw_value = (data.get("last_verified_at") or "").strip()
    if not raw_value:
        return False
    try:
        verified_at = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)
    cache_deadline = verified_at + timedelta(hours=LICENSE_CACHE_HOURS)
    return datetime.now(timezone.utc) < cache_deadline


def _prompt_secret(label: str, existing_value: str = "", optional: bool = False) -> str:
    existing_value = (existing_value or "").strip()
    if existing_value and typer.confirm(
        f"Keep the saved {label} ({_mask_secret(existing_value)})?",
        default=True,
    ):
        return existing_value

    while True:
        if optional:
            value = typer.prompt(label, hide_input=True, default="").strip()
        else:
            value = typer.prompt(label, hide_input=True).strip()
        if value or optional:
            return value
        console.print(
            "[warning]This key is required for the packaged multi-agent flow.[/warning]"
        )


def verify_license():
    """Verify the packaged CLI license and load saved BYOK provider keys.

    Uses a 1-hour cache to avoid calling the Lemon Squeezy API on every command.
    If the server is unreachable, falls back to the offline grace period (24 hours).
    """
    if not _should_enforce_license():
        return

    data = _load_credentials(required=True)
    key = (data.get("license_key") or "").strip()

    if _within_verification_cache(data):
        _apply_runtime_keys(data)
        return

    try:
        valid, error_msg = _validate_license_with_server(key)
    except Exception:
        remaining = _offline_grace_remaining(data)
        if remaining is None:
            console.print(
                Panel(
                    "[error]Could not verify your license with Lemon Squeezy.[/error]\n"
                    "This packaged CLI requires a successful verification at least once every "
                    f"{LICENSE_OFFLINE_GRACE_HOURS} hours.\n"
                    "Reconnect to the internet and run: [accent]agentic login <your_license_key>[/accent]",
                    title="🔒 License Check Failed",
                )
            )
            raise typer.Exit(1)

        hours_left = max(1, int(remaining.total_seconds() // 3600))
        console.print(
            f"[warning]License server unreachable. Using cached verification for up to {hours_left} more hour(s).[/warning]"
        )
        _apply_runtime_keys(data)
        return

    if not valid:
        console.print(
            Panel(
                f"[error]Invalid License Key[/error]\n"
                f"The stored key ({_mask_secret(key)}) was rejected by the server: {error_msg}\n"
                "Please run: [accent]agentic login <your_license_key>[/accent]",
                title="🔒 License Check Failed",
            )
        )
        raise typer.Exit(1)

    _remember_verified_license(data)
    _apply_runtime_keys(data)


@app.command()
def login(
    key: str = typer.Argument(..., help="Your AgentIC (Lemon Squeezy) License Key"),
):
    """Authenticate this computer and setup LLM API keys for multi-agent capabilities."""
    key = key.strip()
    console.print(
        "[accent]Verifying your AgentIC license with Lemon Squeezy...[/accent]"
    )

    if _allow_dev_bypass() and key.startswith("sk_test_dev_bypass"):
        console.print(
            "[warning]Developer bypass active for local non-packaged testing.[/warning]"
        )
    else:
        try:
            valid, error_msg = _validate_license_with_server(key)
        except Exception:
            console.print(
                "[error]✗ Could not reach the license verification server. Check your connection.[/error]"
            )
            raise typer.Exit(1)
        if not valid:
            console.print(f"[error]✗ Invalid License Key: {error_msg}[/error]")
            raise typer.Exit(1)

    existing = _load_credentials(required=False)
    console.print(
        Panel(
            "[success]Authentication Successful![/success]\n"
            f"License key: [accent]{_mask_secret(key)}[/accent]\n"
            "Stored at: ~/.agentic/credentials.json\n\n"
            "Now configure the provider keys used by each agent class.\n\n"
            "[heading]Role Map[/heading]\n"
            "GLM / ZhipuAI: architect, designer, verifier, manager, physical\n"
            "NVIDIA / DeepSeek-style routing: fixer, debugger, reasoner\n"
            "Groq: documenter and reporting flows",
            title="🔒 Login Complete",
        )
    )

    def _env_first(*names: str) -> tuple[str, str]:
        for name in names:
            value = (os.environ.get(name) or "").strip()
            if value:
                return name, value
        return "", ""

    def _resolve_key(
        label: str, stored_value: str, optional: bool, env_names: tuple[str, ...]
    ) -> str:
        stored_value = (stored_value or "").strip()
        if stored_value:
            console.print(f"[info]Using stored key for {label}.[/info]")
            return stored_value
        env_name, env_value = _env_first(*env_names)
        if env_value:
            console.print(
                f"[info]Using {env_name} from environment for {label}.[/info]"
            )
            return env_value
        return _prompt_secret(label, "", optional=optional)

    glm_key = _resolve_key(
        "GLM / ZhipuAI API key for core build agents",
        existing.get("glm_api_key", ""),
        optional=True,
        env_names=("GLM_API_KEY", "DEEPSEEK_API_KEY", "LLM_API_KEY"),
    )
    nvidia_key = _resolve_key(
        "NVIDIA API key for fixer and debugger agents",
        existing.get("nvidia_api_key", ""),
        optional=False,
        env_names=("NVIDIA_API_KEY", "DEEPSEEK_API_KEY", "LLM_API_KEY"),
    )
    groq_key = _resolve_key(
        "Groq API key for report and documentation agents",
        existing.get("groq_api_key", ""),
        optional=False,
        env_names=("GROQ_API_KEY", "LLM_API_KEY"),
    )

    # Preserve any existing group/provider configuration while refreshing
    # license-gated credentials collected by login.
    data = dict(existing)
    data.update(
        {
            "license_key": key,
            "nvidia_api_key": nvidia_key,
            "groq_api_key": groq_key,
            "glm_api_key": glm_key,
        }
    )
    _remember_verified_license(data)
    _apply_runtime_keys(data)
    console.print(
        f"\n[success]✅ Credentials saved locally in {CREDENTIALS_FILE}[/success]"
    )

    # Now trigger diagnostics to ensure they have the compilers installed
    console.print(
        "\n[accent]Checking local compilation tools (OSS CAD Suite, Docker)...[/accent]"
    )
    from .tools.vlsi_tools import startup_self_check

    status = startup_self_check()
    if not status["ok"]:
        console.print(
            "[warning]Some system compiler dependencies are missing.[/warning]"
        )
        if typer.confirm(
            "Would you like AgentIC to attempt an automatic environment installation now?"
        ):
            console.print(
                Panel(
                    "Automatic shell installer scripts are not bundled in the pip package.\n\n"
                    "Install prerequisites manually:\n"
                    "1. Install OSS CAD Suite and set OSS_CAD_SUITE_HOME\n"
                    "2. Ensure docker is installed and running\n"
                    "3. Re-run agentic doctor to verify",
                    title="🛠 Manual Setup Required",
                    border_style="warning",
                )
            )
    else:
        console.print("[success]Compiler environment looks pristine! 🚀[/success]")

    console.print(
        "\n[success]You are completely set up! Try running: agentic build --name my_design --desc '...'[/success]"
    )


@app.command()
def configure(
    single_key: bool = typer.Option(
        False, "--single-key", "-s", help="Use a single API key for all agent roles"
    ),
):
    """Interactive setup wizard — configure LLM API keys for AgentIC.

    Works with any OpenAI-compatible LLM provider (OpenAI, Anthropic, Groq,
    ZhipuAI, NVIDIA, DeepSeek, Together, Mistral, Ollama, etc.).

    Supports three configuration modes:
      1. Single key — one API key for all agents
      2. Group keys — separate keys for build/fix/doc groups
      3. Per-role keys — different model for each agent role (recommended)

    Keys are saved to ~/.agentic/credentials.json. Your .env file is never modified.
    """
    from .config import save_user_credentials, CREDENTIALS_PATH, _load_user_credentials
    from .tools.api_manager import _PROVIDER_MODEL_DEFAULTS, ApiManager

    existing = _load_user_credentials()

    console.print(
        Panel(
            "[heading]AgentIC LLM Configuration Wizard[/heading]\n\n"
            "AgentIC runs [accent]12+ LLM agents[/accent] during a chip build.\n"
            "Choose how to configure them:\n\n"
            "  [accent]1.[/accent] [success]Single Key[/success]  — One model for all agents (simplest)\n"
            "  [accent]2.[/accent] [success]3 Groups[/success]   — Build / Fix / Doc groups (balanced)\n"
            "  [accent]3.[/accent] [success]Per-Role[/success]   — Best model per agent role (recommended)\n\n"
            "Any OpenAI-compatible provider works (OpenAI, Groq, Anthropic, DeepSeek, etc.)",
            title="🔧 Configure LLM",
            border_style="accent",
        )
    )

    # Show model suggestions table
    from rich.table import Table

    table = Table(
        title="Smart Model Suggestions", header_style="bold #d97757", show_lines=True
    )
    table.add_column("Role / Agent", style="#d97757 bold")
    table.add_column("Suggested Model", style="info")
    table.add_column("Best For", style="dim")

    suggestions = [
        ("architect", "claude-3-5-sonnet-20250620", "Architecture, CDC, FSM reasoning"),
        ("designer", "gpt-4o", "RTL generation, SystemVerilog"),
        ("verifier", "gpt-4o", "Verification planning, coverage analysis"),
        ("physical", "gpt-4o", "Floorplanning, STA, DRC/LVS"),
        ("fixer", "gpt-4o-mini", "Fast iterative RTL fixes, lint repair"),
        (
            "debugger",
            "claude-3-5-haiku-20250620",
            "Quick error triage, regression analysis",
        ),
        ("documenter", "groq/llama-3.3-70b-versatile", "Fast docs, report generation"),
    ]
    for role, model, best_for in suggestions:
        table.add_row(role, model, best_for)
    console.print(table)

    mode_map = {
        "1": "single",
        "2": "group",
        "3": "per_role",
    }

    if not single_key:
        mode = typer.prompt("\nSelect configuration mode [1/2/3]", default="3").strip()
    else:
        mode = "single"

    mode = mode_map.get(mode, "per_role")

    def _test_connection(model: str, api_key: str, base_url: str = "") -> bool:
        """Test a model + key combo with a minimal call."""
        from crewai import LLM

        try:
            model_name = model
            if base_url and not any(
                model_name.startswith(p)
                for p in (
                    "openai/",
                    "groq/",
                    "ollama/",
                    "anthropic/",
                    "azure/",
                    "together_ai/",
                    "mistral/",
                    "nvidia_nim/",
                    "deepseek/",
                    "gemini/",
                )
            ):
                model_name = f"openai/{model_name}"
            kwargs = dict(
                model=model_name, api_key=api_key, temperature=0.1, max_tokens=8
            )
            if base_url:
                kwargs["base_url"] = base_url
            test_llm = LLM(**kwargs)
            test_llm.call([{"role": "user", "content": "hi"}])
            return True
        except Exception as e:
            console.print(f"  [warning]Connection failed: {e}[/warning]")
            return False

    def _prompt_key(
        label: str,
        existing_key: str = "",
        existing_model: str = "",
        existing_base: str = "",
        skip_key: bool = False,
    ) -> Tuple[str, str, str]:
        """Prompt for model + base_url + api_key. Returns (model, base_url, api_key)."""
        if existing_model:
            console.print(f"  Current model: [info]{existing_model}[/info]")
        model = typer.prompt(
            f"  {label} model", default=existing_model or "gpt-4o"
        ).strip()

        console.print("  Base URL (blank for OpenAI/Anthropic/Groq):")
        base_url = typer.prompt("  >", default=existing_base or "").strip()

        if skip_key and not existing_key:
            api_key = ""
        else:
            if existing_key:
                masked = (
                    f"{existing_key[:4]}...{existing_key[-4:]}"
                    if len(existing_key) > 8
                    else "****"
                )
                keep = typer.confirm(f"  Keep saved API key ({masked})?", default=True)
                if keep:
                    api_key = existing_key
                else:
                    api_key = typer.prompt("  API Key", hide_input=True).strip()
            else:
                api_key = typer.prompt("  API Key", hide_input=True).strip()

        return model, base_url, api_key

    creds: Dict[str, Any] = {}

    if mode == "single":
        model, base_url, api_key = _prompt_key(
            "Primary",
            existing_model=existing.get("build", {}).get("model", ""),
            existing_key=existing.get("build", {}).get("api_key", ""),
            existing_base=existing.get("build", {}).get("base_url", ""),
        )
        if not api_key:
            api_key = LLM_API_KEY
        if api_key:
            console.print("[accent]Testing connection...[/accent]")
            if not _test_connection(model, api_key, base_url):
                if not typer.confirm("Save anyway?", default=False):
                    console.print("[info]Configuration cancelled.[/info]")
                    raise typer.Exit(0)
        for g in ("build", "fix", "doc"):
            creds[g] = {"model": model, "base_url": base_url, "api_key": api_key}

    elif mode == "group":
        for key, label in [
            ("build", "Build (Architect, Designer, Verifier, Physical)"),
            ("fix", "Fix (Fixer, Debugger, Reasoner)"),
            ("doc", "Doc (Documenter, Reporter)"),
        ]:
            ex = existing.get(key, {})
            model, base_url, api_key = _prompt_key(
                label,
                existing_model=ex.get("model", ""),
                existing_key=ex.get("api_key", ""),
                existing_base=ex.get("base_url", ""),
            )
            if not api_key:
                api_key = LLM_API_KEY
            if api_key:
                console.print(f"[accent]Testing {key}...[/accent]")
                if not _test_connection(model, api_key, base_url):
                    if not typer.confirm(f"  Save {key} anyway?", default=False):
                        console.print("[info]Configuration cancelled.[/info]")
                        raise typer.Exit(0)
            creds[key] = {"model": model, "base_url": base_url, "api_key": api_key}

    elif mode == "per_role":
        console.print(
            "\n[info]Per-Role Configuration[/info] — assign the best model to each role.\n"
            "Press Enter to use the suggested model.\n"
        )
        from crewai import LLM

        # Per-role config stored under "roles" key in credentials
        existing_roles = existing.get("roles", {})
        role_defaults = {
            "architect": ("claude-3-5-sonnet-20250620", "Anthropic", ""),
            "designer": ("gpt-4o", "OpenAI", ""),
            "verifier": ("gpt-4o", "OpenAI", ""),
            "physical": ("gpt-4o", "OpenAI", ""),
            "fixer": ("gpt-4o-mini", "OpenAI", ""),
            "debugger": ("claude-3-5-haiku-20250620", "Anthropic", ""),
            "documenter": (
                "groq/llama-3.3-70b-versatile",
                "Groq",
                "https://api.groq.com/openai/v1",
            ),
            "reporter": (
                "groq/llama-3.3-70b-versatile",
                "Groq",
                "https://api.groq.com/openai/v1",
            ),
            "testbench_designer": ("gpt-4o", "OpenAI", ""),
            "manager": ("gpt-4o", "OpenAI", ""),
            "reasoner": ("gpt-4o", "OpenAI", ""),
        }

        role_configs = {}
        skip_key = False
        for role, (suggested, provider, suggested_base) in role_defaults.items():
            ex = existing_roles.get(role, {})
            ex_model = ex.get("model", suggested)
            ex_base = ex.get("base_url", suggested_base)
            ex_key = ex.get("api_key", "")

            if not ex_key and not skip_key:
                # Try to reuse key from earlier role
                prev_key = ""
                for prev_role in role_configs:
                    if role_configs[prev_role].get("api_key"):
                        prev_key = role_configs[prev_role]["api_key"]
                        break
                if prev_key:
                    ex_key = prev_key

            default_model = ex_model or suggested
            model = typer.prompt(
                f"  {role} model",
                default=default_model,
            ).strip()

            default_base = ex_base or suggested_base
            base_url = typer.prompt(
                f"  {role} base URL",
                default=default_base,
            ).strip()

            if not skip_key:
                if ex_key:
                    masked = (
                        f"{ex_key[:4]}...{ex_key[-4:]}" if len(ex_key) > 8 else "****"
                    )
                    keep = typer.confirm(
                        f"  {role} API key ({masked}) — keep?", default=True
                    )
                    if keep:
                        api_key = ex_key
                    else:
                        api_key = typer.prompt(
                            f"  {role} API Key", hide_input=True
                        ).strip()
                        if not api_key:
                            skip_key = True
                else:
                    api_key = typer.prompt(f"  {role} API Key", hide_input=True).strip()
                    if not api_key:
                        skip_key = True

            role_configs[role] = {
                "model": model,
                "base_url": base_url,
                "api_key": api_key,
                "provider": provider,
            }

            if api_key:
                console.print(f"  [dim]Testing {role}...[/dim]")
                if _test_connection(model, api_key, base_url):
                    console.print(f"  [success]  ✓[/success]")
                else:
                    if not typer.confirm(f"  Save {role} anyway?", default=False):
                        console.print("[info]Configuration cancelled.[/info]")
                        raise typer.Exit(0)

        creds = {"roles": role_configs}
        # Also set group-level for backward compat
        build_key = next(
            (rc["api_key"] for rc in role_configs.values() if rc.get("api_key")), ""
        )
        if build_key:
            build_model = role_configs.get("designer", {}).get("model", "gpt-4o")
            creds["build"] = {
                "model": build_model,
                "base_url": "",
                "api_key": build_key,
            }

    # Preserve any existing non-group keys
    merged = {**existing, **creds}
    save_user_credentials(merged)

    summary_text = (
        "Single key"
        if mode == "single"
        else ("3 groups (build/fix/doc)" if mode == "group" else "per-role models")
    )
    console.print(
        Panel(
            f"[success]Configuration saved![/success]\n\n"
            f"Mode: [accent]{summary_text}[/accent]\n"
            f"Location: [info]{CREDENTIALS_PATH}[/info]\n\n"
            f"[heading]Ready to build![/heading]\n"
            "Run: [accent]agentic build --name counter --desc '8-bit counter with reset'[/accent]",
            title="✅ Configuration Complete",
            border_style="success",
        )
    )


@app.command()
def doctor():
    """Validate local runtime, toolchain, and saved credentials for CLI builds."""
    from .config import CREDENTIALS_PATH, _load_user_credentials

    diag = startup_self_check()
    creds = _load_user_credentials()

    console.print(Panel("AgentIC CLI health report", title="🩺 Doctor"))

    required_failed = False
    for check in diag.get("checks", []):
        tool = check.get("tool", "unknown")
        resolved = check.get("resolved") or check.get("hint") or "n/a"
        optional = bool(check.get("optional"))
        ok = bool(check.get("ok"))
        if ok:
            console.print(f"  [success]✓[/success] {tool}: [info]{resolved}[/info]")
        else:
            marker = "(optional)" if optional else "(required)"
            console.print(
                f"  [error]✗[/error] {tool} {marker}: [info]{resolved}[/info]"
            )
            if not optional:
                required_failed = True

    if creds:
        groups = [g for g in ("build", "fix", "doc") if isinstance(creds.get(g), dict)]
        if groups:
            console.print(
                f"\n[success]✓[/success] Credentials file found: [info]{CREDENTIALS_PATH}[/info]"
            )
            for group in groups:
                model = (
                    creds.get(group, {}).get("model") or ""
                ).strip() or "(model not set)"
                has_key = bool((creds.get(group, {}).get("api_key") or "").strip())
                key_state = "key set" if has_key else "key missing"
                console.print(f"  - {group}: {model} [{key_state}]")
        else:
            console.print(
                f"\n[warning]Credentials file exists but no build/fix/doc groups found:[/warning] [info]{CREDENTIALS_PATH}[/info]"
            )
    else:
        console.print(
            f"\n[warning]No saved credentials found at[/warning] [info]{CREDENTIALS_PATH}[/info]"
        )

    if required_failed:
        raise typer.Exit(1)

    console.print("\n[success]Environment checks passed.[/success]")
    console.print(
        "\n[info]To install a PDK:[/info] [accent]agentic install-pdk list[/accent]"
    )


PDK_INSTALL_CONFIGS = {
    "sky130": {
        "name": "SkyWater SKY130",
        "pdk_dir": "sky130A",
        "description": "SkyWater 130nm — most mature open PDK",
        "volare_repo": "efabless/sky130",
        "volare_target": "sky130A",
        "download_url": "",
        "requires_volare": True,
        "versions": ["1.0.0", "0.16.0"],
        "default_version": "1.0.0",
    },
    "gf180mcu": {
        "name": "GlobalFoundries GF180MCU",
        "pdk_dir": "gf180mcuC",
        "description": "GlobalFoundries 180nm — automotive grade",
        "volare_repo": "The-OpenROAD-Project/gf180mcu",
        "volare_target": "gf180mcuC",
        "download_url": "",
        "requires_volare": True,
        "versions": ["2.0.0", "1.0.0"],
        "default_version": "1.0.0",
    },
    "asap7": {
        "name": "ASAP7 Predictive PDK",
        "pdk_dir": "asap7",
        "description": "ASAP 7nm — cutting-edge predictive PDK",
        "volare_repo": "",
        "volare_target": "",
        "download_url": "https://github.com/The-OpenROAD-Project/asap7/archive/refs/tags/v1.0.0.tar.gz",
        "requires_volare": False,
        "versions": ["1.0.0"],
        "default_version": "1.0.0",
    },
    "nangate45": {
        "name": "NanGate 45nm",
        "pdk_dir": "nangate45",
        "description": "NanGate 45nm — academic/research",
        "volare_repo": "",
        "volare_target": "",
        "download_url": "https://github.com/nangate/nangate45/archive/refs/tags/v1.0.0.tar.gz",
        "requires_volare": False,
        "versions": ["1.0.0"],
        "default_version": "1.0.0",
    },
    "osu018": {
        "name": "Oklahoma State 180nm",
        "pdk_dir": "osu018",
        "description": "Oklahoma State 180nm — educational/research",
        "volare_repo": "",
        "volare_target": "",
        "download_url": "https://github.com/The-OpenROAD-Project/osu018/archive/refs/tags/v1.0.0.tar.gz",
        "requires_volare": False,
        "versions": ["1.0.0"],
        "default_version": "1.0.0",
    },
    "osu035": {
        "name": "Oklahoma State 350nm",
        "pdk_dir": "osu035",
        "description": "Oklahoma State 350nm — high voltage, easy to probe",
        "volare_repo": "",
        "volare_target": "",
        "download_url": "https://github.com/The-OpenROAD-Project/osu035/archive/refs/tags/v1.0.0.tar.gz",
        "requires_volare": False,
        "versions": ["1.0.0"],
        "default_version": "1.0.0",
    },
    "efly45": {
        "name": "EFLY 45nm",
        "pdk_dir": "efly45",
        "description": "EFLY 45nm — emerging foundry",
        "volare_repo": "",
        "volare_target": "",
        "download_url": "",
        "requires_volare": False,
        "versions": ["1.0.0"],
        "default_version": "1.0.0",
    },
}


def _check_volare_available() -> tuple[bool, str]:
    """Check if volare is installed and return version."""
    import shutil
    import subprocess

    volare_path = shutil.which("volare")
    if not volare_path:
        return False, ""
    try:
        result = subprocess.run(
            ["volare", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        version = result.stdout.strip() or result.stderr.strip() or "unknown"
        return True, version
    except Exception:
        return True, "unknown"


def _run_volare_install(pdk: str, version: str, target_dir: str) -> bool:
    """Install PDK via volare."""
    import shutil
    import subprocess

    volare_path = shutil.which("volare")
    if not volare_path:
        return False

    cmd = ["volare", "enable", "--pdk-root", target_dir]
    if version:
        cmd.extend(["--set-version", version])

    repo = PDK_INSTALL_CONFIGS.get(pdk, {}).get("volare_repo", "")
    target = PDK_INSTALL_CONFIGS.get(pdk, {}).get("volare_target", "")
    if repo:
        cmd.extend(["--repository", repo])
    if target:
        cmd.append(target)
    else:
        cmd.append(pdk)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
        return result.returncode == 0
    except Exception:
        return False


@app.command("install-pdk")
def install_pdk(
    pdk_name: str = typer.Argument(
        None,
        help="PDK name (e.g., sky130, gf180mcu). Use 'list' to see all available PDKs.",
    ),
    version: str = typer.Option(
        "", "--version", "-v", help="Specific version to install"
    ),
    list_installed: bool = typer.Option(
        False, "--installed", help="List currently installed PDKs"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Reinstall even if already installed"
    ),
):
    """Install open-source PDKs for use with AgentIC.

    Examples:
        agentic install-pdk sky130
        agentic install-pdk sky130 --version 1.0.0
        agentic install-pdk list
        agentic install-pdk list --installed
    """
    from .config import detect_available_pdks

    if pdk_name is None or pdk_name.lower() == "list":
        detected = detect_available_pdks()
        installed = set(detected.keys())

        from rich.table import Table

        table = Table(
            title="Available PDKs",
            header_style="bold #d97757",
            show_lines=True,
        )
        table.add_column("PDK", style="#d97757 bold", width=12)
        table.add_column("Name", style="info")
        table.add_column("Technology", style="dim")
        table.add_column("Status", width=12)
        table.add_column("Install Method", style="dim")
        table.add_column("Description", style="dim")

        for key, cfg in PDK_INSTALL_CONFIGS.items():
            is_installed = key in installed
            status = (
                "[success]Installed[/success]"
                if is_installed
                else "[dim]Not installed[/dim]"
            )
            install_method = "volare" if cfg.get("requires_volare") else "download"
            table.add_row(
                key,
                cfg["name"],
                cfg["pdk_dir"],
                status,
                install_method,
                cfg["description"],
            )

        console.print(table)
        console.print(
            "\n[info]To install:[/info] [accent]agentic install-pdk <name>[/accent]"
        )
        console.print(
            "[info]After install, verify with:[/info] [accent]agentic doctor[/accent]"
        )
        return

    pdk_key = pdk_name.strip().lower()

    if pdk_key not in PDK_INSTALL_CONFIGS:
        console.print(
            f"[error]Unknown PDK: {pdk_name}[/error]\n"
            "Run [accent]agentic install-pdk list[/accent] to see available PDKs."
        )
        raise typer.Exit(1)

    cfg = PDK_INSTALL_CONFIGS[pdk_key]
    detected = detect_available_pdks()
    is_installed = pdk_key in detected

    if is_installed and not force:
        console.print(
            Panel(
                f"[success]PDK '{pdk_key}' is already installed.[/success]\n"
                f"Location: [info]{detected[pdk_key].get('root_path', 'unknown')}[/info]\n\n"
                f"[info]Use [accent]--force[/accent] to reinstall.[/info]",
                title="✅ Already Installed",
                border_style="success",
            )
        )
        return

    target_version = version or cfg.get("default_version", "")
    install_dir = os.environ.get("PDK_ROOT", os.path.expanduser("~/.ciel"))
    os.makedirs(install_dir, exist_ok=True)

    console.print(
        Panel(
            f"[accent]Installing {cfg['name']}[/accent]\n"
            f"Target: {install_dir}\n"
            f"Version: {target_version or 'latest'}",
            title="📦 PDK Installation",
        )
    )

    if cfg.get("requires_volare"):
        volare_ok, volare_version = _check_volare_available()
        if not volare_ok:
            console.print("[warning]Volare not found. Installing via pip...[/warning]")
            import shutil
            import subprocess

            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "volare"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                console.print(
                    f"[error]Failed to install volare:[/error]\n{result.stderr}"
                )
                raise typer.Exit(1)
            console.print("[success]Volare installed successfully.[/success]")

        success = _run_volare_install(pdk_key, target_version, install_dir)
        if success:
            console.print(
                f"[success]✅ {cfg['name']} installed successfully![/success]\n"
                f"PDK root: [info]{install_dir}[/info]\n\n"
                "Set environment variable in your shell:\n"
                f"  [accent]export PDK_ROOT={install_dir}[/accent]\n\n"
                "Then verify with: [accent]agentic doctor[/accent]"
            )
        else:
            console.print(
                f"[error]Volare installation failed.[/error]\n"
                "Try installing manually:\n"
                f"  volare enable --pdk-root {install_dir} {cfg.get('volare_target', pdk_key)}"
            )
            raise typer.Exit(1)

    else:
        download_url = cfg.get("download_url", "")
        if not download_url:
            console.print(
                f"[error]No download URL configured for {pdk_key}.[/error]\n"
                "Manual installation required. See the PDK documentation."
            )
            raise typer.Exit(1)

        import tempfile
        import shutil
        import subprocess

        console.print("[accent]Downloading PDK archive...[/accent]")
        archive_path = os.path.join(tempfile.gettempdir(), f"agentic_{pdk_key}.tar.gz")

        try:
            import requests as _requests

            with _requests.get(download_url, stream=True, timeout=300) as resp:
                resp.raise_for_status()
                total_size = int(resp.headers.get("content-length", 0))
                with open(archive_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
        except Exception as e:
            console.print(f"[error]Download failed: {e}[/error]")
            raise typer.Exit(1)

        console.print("[accent]Extracting archive...[/accent]")
        extract_dir = os.path.join(tempfile.gettempdir(), f"agentic_{pdk_key}_extract")
        shutil.rmtree(extract_dir, ignore_errors=True)
        os.makedirs(extract_dir, exist_ok=True)

        try:
            result = subprocess.run(
                ["tar", "-xzf", archive_path, "-C", extract_dir],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                console.print(f"[error]Extraction failed:[/error]\n{result.stderr}")
                raise typer.Exit(1)
        except Exception as e:
            console.print(f"[error]Extraction failed: {e}[/error]")
            raise typer.Exit(1)

        extracted_name = os.listdir(extract_dir)[0]
        extracted_path = os.path.join(extract_dir, extracted_name)
        target_path = os.path.join(install_dir, cfg["pdk_dir"])
        os.makedirs(install_dir, exist_ok=True)

        if os.path.exists(target_path):
            shutil.rmtree(target_path)
        shutil.move(extracted_path, target_path)
        os.remove(archive_path)
        shutil.rmtree(extract_dir, ignore_errors=True)

        new_detected = detect_available_pdks()
        if pdk_key in new_detected:
            console.print(
                f"[success]✅ {cfg['name']} installed successfully![/success]\n"
                f"Location: [info]{new_detected[pdk_key].get('root_path')}[/info]\n\n"
                f"Set in your shell:\n"
                f"  [accent]export PDK_ROOT={install_dir}[/accent]\n\n"
                "Verify with: [accent]agentic doctor[/accent]"
            )
        else:
            console.print(
                f"[warning]PDK installed but not auto-detected.[/warning]\n"
                f"Set: [accent]export PDK_ROOT={install_dir}[/accent]\n"
                f"Then run: [accent]agentic doctor[/accent] to verify."
            )


# Setup Brain
def get_llm():
    """Returns the LLM instance from the best available provider:
    1. NVIDIA Cloud (e.g. Llama 3.3, DeepSeek)
    2. Local Compute Engine (VeriReason/Ollama)
    """

    configs = [
        ("Cloud Compute Engine", CLOUD_CONFIG),
        ("Local Compute Engine", LOCAL_CONFIG),
    ]

    for name, cfg in configs:
        key = cfg.get("api_key", "")
        # For Cloud, skip if no key.
        if "Cloud" in name and (not key or key.strip() == "" or key == "mock-key"):
            console.print(f"[dim]⏭ {name}: No valid API key set, skipping.[/dim]")
            continue

        try:
            console.print(f"[dim]Testing {name}...[/dim]")
            # Add extra parameters for reasoning models
            extra_t = {}
            if "glm5" in cfg["model"].lower():
                extra_t = {
                    "chat_template_kwargs": {
                        "enable_thinking": True,
                        "clear_thinking": False,
                    }
                }
            elif "deepseek-v3.2" in cfg["model"].lower():
                extra_t = {"chat_template_kwargs": {"thinking": True}}

            llm = LLM(
                model=cfg["model"],
                base_url=cfg["base_url"],
                api_key=key
                if key and key != "NA"
                else "mock-key",  # Local LLMs might use mock-key
                temperature=0.2,  # Standardized for RTL generation stability
                top_p=0.7,  # Optimized for code output
                max_tokens=16384,
                timeout=300,
                extra_body=extra_t,
            )
            # Make a lightweight API call to validate the endpoint
            llm.call([{"role": "user", "content": "Hi"}])
            console.print(
                f"[success]✓ AgentIC is working on your chip using {name}[/success]"
            )
            return llm
        except Exception as e:
            console.print(f"[warning]⚠ {name} init failed[/warning]")

    # Critical Failure if both fail
    console.print(
        Panel(
            "[error]CRITICAL: No AI API Key Found[/error]\n\n"
            "AgentIC is a [warning]Bring-Your-Own-Key[/warning] application. "
            "To build chips using cloud AI clusters, you must provide your own API key.\n\n"
            "[accent]How to fix this:[/accent]\n"
            "1. Create a file named [bold].env[/bold] in your current directory.\n"
            "2. Add your provider's API key to the file. For example:\n"
            '   [success]LLM_API_KEY="your-key-here"[/success]\n'
            '   [success]LLM_BASE_URL="https://api.openai.com/v1"[/success]\n'
            '   [success]LLM_MODEL="gpt-4o"[/success]\n\n'
            "[dim]Alternatively, you can export these as environment variables before running AgentIC.[/dim]",
            title="🔑 Missing API Key Setup",
            border_style="red",
        )
    )
    raise typer.Exit(1)


def run_startup_diagnostics(strict: bool = True):
    diag = startup_self_check()
    ok = bool(diag.get("ok", False))
    status = "[success]PASS[/success]" if ok else "[error]FAIL[/error]"
    console.print(Panel(f"Startup Toolchain Check: {status}", title="🔧 Environment"))
    if not ok:
        for check in diag.get("checks", []):
            if not check.get("ok"):
                console.print(
                    f"  [error]✗ {check.get('tool')}[/error] -> {check.get('resolved')}"
                )
        if strict:
            raise typer.Exit(1)


@app.command()
def simulate(
    name: str = typer.Option(..., "--name", "-n", help="Design name (e.g., counter)"),
    max_retries: int = typer.Option(
        5, "--max-retries", "-r", min=0, help="Max auto-fix retries for failures"
    ),
    show_thinking: bool = typer.Option(
        True, "--show-thinking", help="Print DeepSeek <think> reasoning"
    ),
):
    """Run simulation on an existing design with AUTO-FIX loop."""
    verify_license()
    check_dependencies(skip_openlane=True)
    console.print(
        Panel(
            f"[accent]AgentIC: Manual Simulation + Auto-Fix Mode[/accent]\n"
            f"Design: [warning]{name}[/warning]",
            title="🚀 Starting Simulation",
        )
    )
    llm = get_llm()

    def log_thinking(raw_text: str, step: str):
        """Emit DeepSeek <think> content."""
        if not show_thinking:
            return
        # Simple logging for sim tool
        pass

    rtl_path = f"{OPENLANE_ROOT}/designs/{name}/src/{name}.v"
    tb_path = f"{OPENLANE_ROOT}/designs/{name}/src/{name}_tb.v"

    def _fix_with_llm(agent_role: str, goal: str, prompt: str) -> str:
        # Give the agent TOOLS to self-correct
        fix_agent = Agent(
            role=agent_role,
            goal=goal,
            backstory="Expert in SystemVerilog and verification.",
            llm=llm,
            verbose=show_thinking,
            tools=[syntax_check_tool, read_file_tool],
        )
        fix_task = Task(
            description=prompt,
            expected_output="Corrected SystemVerilog code in a ```verilog fence",
            agent=fix_agent,
        )
        with console.status(f"[accent]AI is fixing ({agent_role})...[/accent]"):
            result = str(
                Crew(verbose=False, agents=[fix_agent], tasks=[fix_task]).kickoff()
            )
            return result

    sim_success, sim_output = run_simulation(name)
    sim_tries = 0

    while not sim_success and sim_tries < max_retries:
        sim_tries += 1
        console.print(
            f"[error]✗ SIMULATION FAILED (attempt {sim_tries}/{max_retries})[/error]"
        )
        sim_output_text = sim_output or ""
        # 1) If compilation failed, fix TB first.
        if (
            "Compilation failed:" in sim_output_text
            or "syntax error" in sim_output_text
        ):
            fix_tb_prompt = f"""Fix this SystemVerilog testbench so it compiles and avoids directionality errors.
CRITICAL FIXING RULES:
1. **Unresolved Wires**: If you see "Unable to assign to unresolved wires", it means you are driving a DUT OUTPUT. Stop driving it!
2. **Signal Directions**:
   - Check the DUT definition.
   - If a port is `output` in DUT, it is a `wire` in TB (Read-Only).
   - If a port is `input` in DUT, it is a `reg/logic` in TB (Write-Only).
3. **Format**: Return ONLY corrected testbench code inside ```verilog fences.
Simulation output / errors:
{sim_output_text}
Current RTL (do not modify unless absolutely necessary):
```verilog
{read_file_content(rtl_path)}
```
Current testbench:
```verilog
{read_file_content(tb_path)}
```
"""
            fixed_tb = _fix_with_llm(
                "Verification Engineer", f"Fix testbench for {name}", fix_tb_prompt
            )
            result_path = write_verilog(name, fixed_tb, is_testbench=True)
            if isinstance(result_path, str) and result_path.startswith("Error:"):
                sim_output = f"Failed to write fixed TB: {result_path}"
                continue
            tb_path = result_path
            sim_success, sim_output = run_simulation(name)
            continue
        # 2) Logic or Runtime Errors
        if "TEST FAILED" in sim_output_text or "TEST PASSED" not in sim_output_text:
            # AI-Based Error Classification
            analyst = get_error_analyst_agent(llm, verbose=False)
            analysis_task = Task(
                description=f"""Analyze this Verification Failure.
Error Log:
{sim_output_text}
Is this a:
A) TESTBENCH_ERROR (Syntax, $monitor usage, race condition, compilation fail)
B) RTL_LOGIC_ERROR (Mismatch, Wrong State, Functional Failure)
Reply with ONLY "A" or "B".""",
                expected_output="Single letter A or B",
                agent=analyst,
            )
            analysis = str(
                Crew(verbose=False, agents=[analyst], tasks=[analysis_task]).kickoff()
            ).strip()

            is_tb_issue = "A" in analysis
            if is_tb_issue:
                console.print(
                    "[warning]  -> [Analyst] Root Cause: Testbench Error. Fixing TB...[/warning]"
                )
                fix_tb_logic_prompt = f"""Fix the Testbench logic/syntax. The simulation failed or generated runtime errors.
                 
CRITICAL FIXING RULES:
1. **Timing is USUALLY THE PROBLEM**: If "TEST FAILED" appears, the testbench is checking outputs TOO EARLY.
   - Count the FSM states in the RTL. Wait at least (num_states + 10) clock cycles.
   - Use `repeat(25) @(posedge clk);` minimum before checking ANY output.
   - If there's a `done` or `valid` signal, use `while(!done) @(posedge clk);`
2. **Race Conditions**: Add `#1` delays after clock edges before sampling.
3. **Reset**: Ensure reset is held for at least 4 clock cycles.
4. **Between Tests**: Wait for FSM to return to IDLE with `repeat(10) @(posedge clk);`
5. **Format**: Return ONLY corrected testbench code inside ```verilog fences.
Simulation Error/Output:
{sim_output_text}
Current RTL (Reference - count the FSM states):
```verilog
{read_file_content(rtl_path)}
```
Current Testbench (To Fix - increase wait cycles):
```verilog
{read_file_content(tb_path)}
```
"""
                fixed_tb = _fix_with_llm(
                    "Verification Engineer",
                    f"Fix testbench logic for {name}",
                    fix_tb_logic_prompt,
                )
                result_path = write_verilog(name, fixed_tb, is_testbench=True)
                if isinstance(result_path, str) and result_path.startswith("Error:"):
                    sim_output = f"Failed to write fixed TB: {result_path}"
                    continue
                tb_path = result_path
                sim_success, sim_output = run_simulation(name)
                continue
            else:
                console.print(
                    "[warning]  -> Detecting Design Logic mismatch. Fixing RTL...[/warning]"
                )
                fix_rtl_prompt = f'''The simulation did not pass. Fix the RTL (module "{name}") so that the testbench passes.
CRITICAL REQUIREMENTS:
- **NO CONVERSATION**: Return ONLY the code inside ```verilog fences. Do NOT write "Thought:", "Here is the code", or any explanation.
- Keep module name exactly "{name}"
- SystemVerilog only
- Keep ports: clk, rst_n (active-low) present
- **MAINTAIN DESIGN INTENT**: Do NOT simplify the logic to pass the test case.
  - If the design is an NPU or Processor, do NOT replace complex logic with simple static assignments.
  - You must fix the BUGS in the implementation, not delete the implementation.
  - If the testbench expects a result after N cycles, ensure your pipeline matches that latency.
- Return ONLY corrected RTL code inside ```verilog fences
Simulation output:
{sim_output_text}
Current testbench (do not change in this step):
```verilog
{read_file_content(tb_path)}
```
Current RTL:
```verilog
{read_file_content(rtl_path)}
```
'''
                fixed_rtl = _fix_with_llm(
                    "VLSI Design Engineer",
                    f"Fix RTL behavior for {name}",
                    fix_rtl_prompt,
                )
                rtl_path = write_verilog(name, fixed_rtl)

                # Check syntax of fix
                success, errors = run_syntax_check(rtl_path)
                if not success:
                    sim_output = f"RTL fix introduced syntax error:\n{errors}"
                    continue
                sim_success, sim_output = run_simulation(name)
                continue

    if not sim_success:
        console.print(f"[error]✗ SIMULATION FAILED:[/error]\n{sim_output}")
        raise typer.Exit(1)
    sim_lines = sim_output.strip().split("\n")
    for line in sim_lines[-20:]:  # Print last 20 lines of log
        console.print(f"  [dim]{line}[/dim]")
    console.print("  ✓ Simulation [success]passed[/success]")


def _generate_config_tcl(design_name: str, rtl_file: str) -> str:
    """Auto-generate OpenLane config.tcl based on design complexity.

    Reads the RTL file to estimate size and generates appropriate
    die area, clock period, and synthesis settings.
    """
    # Estimate design complexity from file size
    try:
        with open(rtl_file, "r") as f:
            rtl_content = f.read()
        line_count = len(rtl_content.strip().split("\n"))
    except IOError:
        line_count = 100  # Fallback
    # Scale parameters based on complexity
    if line_count < 100:
        # Small: counter, shift register, PWM
        die_size, util, clock_period = 300, 50, "10"
    elif line_count < 300:
        # Medium: FIFO, UART, SPI, FSM
        die_size, util, clock_period = 500, 40, "15"
    else:
        # Large: TMR, AES, processors
        die_size, util, clock_period = 800, 35, "20"
    return f'''# Auto-generated by AgentIC for {design_name}
set ::env(DESIGN_NAME) "{design_name}"
set ::env(VERILOG_FILES) "$::env(DESIGN_DIR)/src/{design_name}.v"
set ::env(CLOCK_PORT) "clk"
set ::env(CLOCK_PERIOD) "{clock_period}"
# Floorplanning (scaled for ~{line_count} lines of RTL)
set ::env(FP_SIZING) "absolute"
set ::env(DIE_AREA) "0 0 {die_size} {die_size}"
set ::env(FP_CORE_UTIL) {util}
set ::env(PL_TARGET_DENSITY) {util / 100 + 0.05:.2f}
# Synthesis
set ::env(SYNTH_STRATEGY) "AREA 0"
set ::env(MAX_FANOUT_CONSTRAINT) 8
# Routing
set ::env(GRT_OVERFLOW_ITERS) 64
# PDK
set ::env(PDK) "{PDK}"
'''


@app.command()
def harden(
    name: str = typer.Option(..., "--name", "-n", help="Design name (e.g., counter)"),
):
    """Run OpenLane hardening (RTL -> GDSII) on an existing design."""
    verify_license()
    check_dependencies(skip_openlane=False)
    console.print(
        Panel(
            f"[accent]AgentIC: Manual Hardening Mode[/accent]\n"
            f"Design: [warning]{name}[/warning]",
            title="🚀 Starting OpenLane",
        )
    )

    new_config = f"{OPENLANE_ROOT}/designs/{name}/config.tcl"
    rtl_file = f"{OPENLANE_ROOT}/designs/{name}/src/{name}.v"
    if not os.path.exists(new_config):
        if not os.path.exists(rtl_file):
            console.print(f"[error]✗ RTL file not found: {rtl_file}[/error]")
            raise typer.Exit(1)

        # Auto-generate config.tcl based on design size
        config_content = _generate_config_tcl(name, rtl_file)
        os.makedirs(os.path.dirname(new_config), exist_ok=True)
        with open(new_config, "w") as f:
            f.write(config_content)
        console.print(f"  ✓ Config auto-generated: [success]{new_config}[/success]")

    # Ask for background execution
    run_bg = typer.confirm(
        "OpenLane hardening can take 10-30+ minutes. Run in background?", default=True
    )

    if run_bg:
        console.print("  [dim]Launching background process...[/dim]")
    else:
        console.print("  [dim]Running OpenLane (this may take 10-30 minutes)...[/dim]")
    ol_success, ol_result = run_openlane(name, background=run_bg)

    if ol_success:
        if run_bg:
            console.print(f"  ✓ [success]{ol_result}[/success]")
            console.print(
                f"  [dim]Monitor logs: tail -f {OPENLANE_ROOT}/designs/{name}/harden.log[/dim]"
            )
            console.print(
                "  [warning]Note: Run manual signoff check after background job completes.[/warning]"
            )
            return
        console.print(f"  ✓ GDSII generated: [success]{ol_result}[/success]")

        # --- Strict Signoff Check ---
        console.print(
            Panel(
                f"[accent]Running Signoff Checks (STA/Power)...[/accent]",
                title="🔍 Fabrication Readiness",
            )
        )
        success, report = signoff_check_tool(name)
        if success:
            console.print(f"[success]✅ SIGNOFF PASSED[/success]")
            console.print(report)
        else:
            console.print(f"[error]❌ SIGNOFF FAILED[/error]")
            console.print(report)
            raise typer.Exit(1)
    else:
        console.print(f"[error]✗ OpenLane failed[/error]")
        console.print(f"  Error: {ol_result[:500]}...")
        raise typer.Exit(1)


# --- THE BUILD COMMAND ---
@app.command()
def build(
    name: str = typer.Option(..., "--name", "-n", help="Design name (e.g., counter)"),
    desc: str = typer.Option(..., "--desc", "-d", help="Natural language description"),
    max_retries: int = typer.Option(
        5,
        "--max-retries",
        "-r",
        min=0,
        help="Max auto-fix retries for RTL/TB/sim failures",
    ),
    skip_openlane: bool = typer.Option(
        False, "--skip-openlane", help="Stop after simulation (no RTL→GDSII hardening)"
    ),
    skip_coverage: bool = typer.Option(
        False,
        "--skip-coverage",
        help="Bypass COVERAGE_CHECK and continue from formal verification to regression",
    ),
    show_thinking: bool = typer.Option(
        False,
        "--show-thinking",
        help="Print DeepSeek <think> reasoning for each generation/fix step",
    ),
    thinking_level: str = typer.Option(
        "minimal",
        "--thinking-level",
        help="Thinking display level: minimal (default), normal, or verbose",
    ),
    full_signoff: bool = typer.Option(
        False,
        "--full-signoff",
        help="Run full industry signoff (formal + coverage + regression + DRC/LVS)",
    ),
    min_coverage: float = typer.Option(
        80.0,
        "--min-coverage",
        help="Minimum line coverage percentage to pass verification",
    ),
    strict_gates: bool = typer.Option(
        True,
        "--strict-gates/--no-strict-gates",
        help="Enable strict fail-closed gating",
    ),
    pdk: str = typer.Option("", "--pdk", help="Target PDK (auto-detected if omitted)"),
    max_pivots: int = typer.Option(
        2, "--max-pivots", min=0, help="Maximum strategy pivots before fail-closed"
    ),
    congestion_threshold: float = typer.Option(
        10.0, "--congestion-threshold", help="Routing congestion threshold (%)"
    ),
    hierarchical: str = typer.Option(
        "auto", "--hierarchical", help="Hierarchical mode: auto, off, on"
    ),
    tb_gate_mode: str = typer.Option(
        "strict", "--tb-gate-mode", help="TB gate mode: strict or relaxed"
    ),
    tb_max_retries: int = typer.Option(
        3, "--tb-max-retries", min=1, help="Maximum TB gate recovery attempts"
    ),
    tb_fallback_template: str = typer.Option(
        "uvm_lite",
        "--tb-fallback-template",
        help="TB fallback template: uvm_lite or classic",
    ),
    coverage_backend: str = typer.Option(
        SIM_BACKEND_DEFAULT,
        "--coverage-backend",
        help="Coverage backend: auto, verilator, iverilog",
    ),
    coverage_fallback_policy: str = typer.Option(
        COVERAGE_FALLBACK_POLICY_DEFAULT,
        "--coverage-fallback-policy",
        help="Coverage fallback policy: fail_closed, fallback_oss, skip",
    ),
    coverage_profile: str = typer.Option(
        COVERAGE_PROFILE_DEFAULT,
        "--coverage-profile",
        help="Coverage profile: balanced, aggressive, relaxed",
    ),
    no_golden_templates: bool = typer.Option(
        False,
        "--no-golden-templates",
        help="Disable golden template matching in RTL_GEN; force LLM to generate RTL from scratch",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate spec and generate reports without running the full build",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output machine-readable JSON results for CI/CD integration",
    ),
):
    """Build a chip from natural language description (Autonomous Orchestrator 2.0)."""
    verify_license()
    check_dependencies(skip_openlane)

    # ── PDK Auto-Detection & Selection ─────────────────────────────────────
    detected = detect_available_pdks()
    pdk_profile: str = ""

    if pdk:
        # User explicitly specified a PDK — validate it
        resolved = get_pdk_profile(pdk)
        pdk_profile = resolved["profile"]
        if pdk_profile not in detected:
            console.print(
                f"[warning]PDK '{pdk}' not found on this system. "
                f"Available: {', '.join(sorted(detected.keys())) or 'none'}[/warning]"
            )
    elif detected:
        # Auto-detected — pick the first available unless there's a preference
        if len(detected) == 1:
            pdk_profile = next(iter(detected))
            console.print(
                f"[success]Auto-detected PDK: {pdk_profile} "
                f"({detected[pdk_profile].get('description', '')})[/success]"
            )
        else:
            # Multiple PDKs — interactive selection
            from rich.table import Table

            table = Table(
                title="🔧 Select Target PDK",
                show_lines=True,
                header_style="bold #d97757",
            )
            table.add_column("#", style="dim", width=3)
            table.add_column("PDK", style="#d97757 bold")
            table.add_column("Technology", style="info")
            table.add_column("Voltage", style="info")
            table.add_column("Description", style="dim")
            table.add_column("Location", style="info")

            pdk_options = sorted(detected.keys())
            for i, name in enumerate(pdk_options, 1):
                info = detected[name]
                table.add_row(
                    str(i),
                    name,
                    info["pdk"],
                    info.get("voltage_vdd", "?") + "V",
                    info.get("description", "-"),
                    info.get("root_path", "~")[:40],
                )
            console.print(table)

            prompt = (
                f"Select PDK [1-{len(pdk_options)}]"
                f" (or press Enter for {pdk_options[0]}): "
            )
            choice = typer.prompt(prompt, default="1").strip()
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(pdk_options):
                    pdk_profile = pdk_options[idx]
                else:
                    pdk_profile = pdk_options[0]
            except ValueError:
                pdk_profile = pdk_options[0]
            console.print(
                f"[success]Selected: {pdk_profile} — "
                f"{detected[pdk_profile].get('description', '')}[/success]"
            )
    else:
        # No PDK detected anywhere
        console.print(
            Panel(
                "[error]No Open-Source PDK Detected on This System[/error]\n\n"
                "AgentIC requires at least one open-source PDK to be installed.\n\n"
                "Supported PDKs:\n"
                "  • sky130    — SkyWater 130nm (recommended)\n"
                "  • gf180mcu  — GlobalFoundries 180nm\n"
                "  • nangate45 — NanGate 45nm\n"
                "  • asap7     — ASAP 7nm (predictive)\n"
                "  • osu018    — Oklahoma State 180nm\n"
                "  • osu035    — Oklahoma State 350nm\n\n"
                "Install with AgentIC (recommended):\n"
                "  [accent]agentic install-pdk sky130[/accent]\n\n"
                "Then re-run: [accent]agentic build --name ...[/accent]\n\n"
                "[info]Tip: Set PDK_ROOT=/path/to/your/pdks if non-standard location.[/info]",
                title="⚠️ PDK Not Found",
                border_style="warning",
            )
        )
        raise typer.Exit(1)

    if dry_run:
        console.print(
            Panel(
                f"[accent]DRY RUN — Spec Validation[/accent]\n"
                f"Design: {name}\n"
                f"Description: {desc}",
                title="🔍 Dry Run Mode",
            )
        )
        console.print("[info]Validating spec...[/info]")
        from .core.spec_generator import HardwareSpecGenerator
        from .agents.designer import get_designer_agent

        llm = get_llm()
        spec_gen = HardwareSpecGenerator(llm)
        spec, issues = spec_gen.generate(desc, name)
        if issues:
            console.print(f"[warning]Spec issues:[/warning]")
            for issue in issues:
                console.print(f"  - {issue}")
        else:
            console.print("[success]Spec looks valid[/success]")
        console.print(f"\n[info]To run full build:[/info]")
        console.print(f"  agentic build --name {name} --desc '{desc}'")
        return

    from .orchestrator import BuildOrchestrator

    console.print(
        Panel(
            f"[accent]AgentIC: Natural Language → GDSII[/accent]\n"
            f"Design: [warning]{name}[/warning]\n"
            f"Description: {desc}\n"
            f"PDK: [success]{pdk_profile}[/success]  "
            f"{'[success]Full Industry Signoff Enabled[/success]' if full_signoff else ''}",
            title="🚀 Starting Autonomous Orchestrator",
        )
    )
    tb_gate_mode = tb_gate_mode.lower().strip()
    if tb_gate_mode not in {"strict", "relaxed"}:
        raise typer.BadParameter("--tb-gate-mode must be one of: strict, relaxed")
    tb_fallback_template = tb_fallback_template.lower().strip()
    if tb_fallback_template not in {"uvm_lite", "classic"}:
        raise typer.BadParameter(
            "--tb-fallback-template must be one of: uvm_lite, classic"
        )
    coverage_backend = coverage_backend.lower().strip()
    if coverage_backend not in {"auto", "verilator", "iverilog"}:
        raise typer.BadParameter(
            "--coverage-backend must be one of: auto, verilator, iverilog"
        )
    coverage_fallback_policy = coverage_fallback_policy.lower().strip()
    if coverage_fallback_policy not in {"fail_closed", "fallback_oss", "skip"}:
        raise typer.BadParameter(
            "--coverage-fallback-policy must be one of: fail_closed, fallback_oss, skip"
        )
    coverage_profile = coverage_profile.lower().strip()
    if coverage_profile not in {"balanced", "aggressive", "relaxed"}:
        raise typer.BadParameter(
            "--coverage-profile must be one of: balanced, aggressive, relaxed"
        )
    thinking_level = thinking_level.lower().strip()
    if thinking_level not in {"minimal", "normal", "verbose"}:
        raise typer.BadParameter(
            "--thinking-level must be one of: minimal, normal, verbose"
        )
    run_startup_diagnostics(strict=strict_gates)
    llm = get_llm()

    # Build Multi-LLM Role Map for the CLI
    from .config import get_role_llm_config
    from crewai import LLM

    roles = [
        "architect",
        "designer",
        "testbench_designer",
        "verifier",
        "fixer",
        "debugger",
        "manager",
        "physical",
        "documenter",
        "reporter",
    ]
    role_llms = {}

    print("\n--- 🤖 Local Compute Routing Map ---", flush=True)
    for role in roles:
        cfg = get_role_llm_config(role)
        llm_kwargs = dict(
            model=cfg["model"],
            api_key=cfg["api_key"],
            temperature=0.6,
            max_tokens=16384,
        )
        if cfg.get("base_url"):
            llm_kwargs["base_url"] = cfg["base_url"]
        if "extra_body" in cfg:
            llm_kwargs["extra_body"] = cfg["extra_body"]

        print(f"[Router] {role.upper():<20} -> Model: {cfg['model']}", flush=True)
        try:
            role_llms[role] = LLM(**llm_kwargs)
        except Exception:
            role_llms[role] = llm
    print("------------------------------------\n", flush=True)

    orchestrator = BuildOrchestrator(
        name=name,
        desc=desc,
        llm=llm,
        role_llms=role_llms,
        max_retries=max_retries,
        verbose=show_thinking,
        skip_openlane=skip_openlane,
        skip_coverage=skip_coverage,
        full_signoff=full_signoff,
        min_coverage=min_coverage,
        strict_gates=strict_gates,
        pdk_profile=pdk_profile,
        max_pivots=max_pivots,
        congestion_threshold=congestion_threshold,
        hierarchical_mode=hierarchical,
        tb_gate_mode=tb_gate_mode,
        tb_max_retries=tb_max_retries,
        tb_fallback_template=tb_fallback_template,
        coverage_backend=coverage_backend,
        coverage_fallback_policy=coverage_fallback_policy,
        coverage_profile=coverage_profile,
        no_golden_templates=no_golden_templates,
        thinking_level=thinking_level,
    )

    orchestrator.run()

    if json_output:
        import json

        console.print_json(
            {
                "design": name,
                "description": desc,
                "pdk": pdk_profile,
                "state": orchestrator.state.name,
                "final_state": orchestrator.state.name,
                "build_passed": orchestrator.state.name == "SUCCESS",
                "artifacts": {
                    k: v if isinstance(v, (str, int, float, bool)) else str(v)
                    for k, v in orchestrator.artifacts.items()
                },
                "stages_completed": [s.name for s in orchestrator.state_history],
            }
        )


@app.command()
def verify(name: str = typer.Argument(..., help="Design name to verify")):
    """Run verification on an existing design."""
    verify_license()
    console.print(f"[warning]Running verification for {name}...[/warning]")
    output = run_verification(name)
    console.print(output)


# --- PRODUCTION CLI COMMANDS ---


@app.command("synth")
def synth(
    rtl_file: str = typer.Option(..., "--rtl", "-r", help="RTL Verilog source file"),
    top: str = typer.Option(..., "--top", "-t", help="Top-level module name"),
    output_dir: str = typer.Option(
        "./synth_out", "--out", "-o", help="Output directory"
    ),
    pdk: str = typer.Option("sky130", "--pdk", help="PDK: sky130, gf180mcu"),
    clk_ns: float = typer.Option(
        10.0, "--clk", help="Clock constraint in ns (e.g., 10.0 = 100MHz)"
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output machine-readable JSON result"
    ),
):
    """Run Yosys RTL synthesis to produce a gate-level netlist.

    This provides independent synthesis (not via OpenLane) for:
    - Gate-level netlist for formal verification
    - Pre-route timing estimates
    - Cell count and area metrics
    """
    verify_license()
    console.print(
        Panel(
            f"[accent]Yosys Synthesis[/accent]\n"
            f"RTL: {rtl_file}\nTop: {top}\nPDK: {pdk}\nClock: {clk_ns}ns",
            title="🛠️ Synthesis",
        )
    )
    from .config import WORKSPACE_ROOT, PDK_ROOT, PDK

    _output_dir = os.path.join(output_dir, f"synth_{top}")

    result = run_yosys_synth(
        rtl_files=[rtl_file],
        top_module=top,
        output_dir=_output_dir,
        pdk=pdk,
        pdk_root=PDK_ROOT,
        clk_constraint=clk_ns,
    )

    if result.ok:
        console.print(f"  [success]✓ Synthesis PASS[/success]")
        console.print(
            f"  Cells: {result.cell_count:,} | DFFs: {result.dff_count:,} | LUTs: {result.lut_count:,}"
        )
        console.print(
            f"  Gate equiv: {result.gate_count:,.0f} | Area: {result.area_um2:.3f} um²"
        )
        console.print(f"  Netlist: {result.netlist_path}")
        if result.warnings:
            console.print(f"  [warning]⚠ Warnings: {len(result.warnings)}[/warning]")
    else:
        console.print(f"  [error]✗ Synthesis FAILED[/error]")
        for e in result.errors:
            console.print(f"    {e}")
        if json_output:
            console.print_json({"ok": False, "errors": result.errors})
        raise typer.Exit(1)

    if json_output:
        console.print_json(
            {
                "ok": result.ok,
                "netlist_path": result.netlist_path,
                "cell_count": result.cell_count,
                "dff_count": result.dff_count,
                "lut_count": result.lut_count,
                "gate_count": result.gate_count,
                "area_um2": result.area_um2,
                "warnings": result.warnings,
            }
        )


@app.command("sta")
def sta(
    netlist: str = typer.Option(
        ..., "--netlist", "-n", help="Gate-level Verilog netlist"
    ),
    sdc: str = typer.Option(..., "--sdc", help="SDC timing constraints file"),
    lib: str = typer.Option(
        ..., "--lib", "-l", help="Liberty timing library (.lib) file"
    ),
    output_dir: str = typer.Option("./sta_out", "--out", "-o", help="Output directory"),
    corner: str = typer.Option("tt", "--corner", "-c", help="Corner: tt, ss, ff"),
    multi_corner: bool = typer.Option(
        False, "--multi-corner", help="Run all corners (ss/tt/ff)"
    ),
    min_period_ns: float = typer.Option(
        10.0, "--period", help="Clock period constraint in ns"
    ),
    pdk: str = typer.Option("sky130", "--pdk", help="PDK name"),
    json_output: bool = typer.Option(
        False, "--json", help="Output machine-readable JSON"
    ),
):
    """Run OpenSTA static timing analysis (pre or post PnR).

    Production signoff requires multi-corner multi-mode STA:
    - SS (slow-slow): worst setup
    - TT (typical-typical): nominal
    - FF (fast-fast): worst hold
    """
    verify_license()
    console.print(
        Panel(
            f"[accent]OpenSTA Static Timing Analysis[/accent]\n"
            f"Netlist: {netlist}\nCorner: {corner} | Multi-corner: {multi_corner}",
            title="⏱️ Timing Analysis",
        )
    )
    os.makedirs(output_dir, exist_ok=True)

    libs = lib.split(",") if "," in lib else [lib]

    if multi_corner:
        result = run_multi_corner_sta(
            netlist=netlist,
            sdc=sdc,
            lib_files=libs,
            output_dir=output_dir,
            corners=["ss", "tt", "ff"],
            min_period_ns=min_period_ns,
            pdk=pdk,
        )
        for key, report in result.corners.items():
            icon = "✓" if report.ok else "✗"
            status = f"[success]PASS[/success]" if report.ok else f"[error]FAIL[/error]"
            console.print(
                f"  {icon} {key}: WNS={report.wns_setup:.3f}ns | TNS={report.tns_setup:.1f}ns | {status}"
            )
        if result.all_corners_pass:
            console.print(f"  [success]✓ All corners PASS[/success]")
        else:
            console.print(f"  [error]✗ Some corners FAIL — check reports[/error]")
        if json_output:
            import json

            with open(result.summary_path) as f:
                console.print_json(f.read())
    else:
        result = run_opensta(
            netlist=netlist,
            sdc=sdc,
            lib_files=libs,
            output_dir=output_dir,
            corner=corner,
            min_period_ns=min_period_ns,
            pdk=pdk,
        )
        if result.ok:
            console.print(f"  [success]✓ STA PASS [{corner}][/success]")
            console.print(
                f"  WNS={result.wns_setup:.3f}ns | TNS={result.tns_setup:.1f}ns"
            )
            console.print(f"  Max frequency: {result.max_freq_mhz:.1f} MHz")
        else:
            console.print(f"  [error]✗ STA FAIL [{corner}][/error]")
            console.print(
                f"  WNS={result.wns_setup:.3f}ns | TNS={result.tns_setup:.1f}ns"
            )
            if result.errors:
                for e in result.errors:
                    console.print(f"    {e}")
        if json_output:
            console.print_json(
                {
                    "ok": result.ok,
                    "corner": corner,
                    "wns_setup_ns": result.wns_setup,
                    "wns_hold_ns": result.wns_hold,
                    "tns_setup_ns": result.tns_setup,
                    "max_freq_mhz": result.max_freq_mhz,
                    "critical_paths": len(result.critical_paths),
                }
            )


@app.command("dft")
def dft(
    rtl_file: str = typer.Option(..., "--rtl", "-r", help="RTL Verilog source file"),
    top: str = typer.Option(..., "--top", "-t", help="Top-level module name"),
    output_dir: str = typer.Option("./dft_out", "--out", "-o", help="Output directory"),
    scan_chains: int = typer.Option(4, "--chains", help="Number of scan chains"),
    testability: bool = typer.Option(
        False, "--testability", help="Run RTL testability analysis only"
    ),
    pdk: str = typer.Option("sky130", "--pdk", help="PDK name"),
):
    """Run DFT scan insertion and ATPG pattern generation.

    PRODUCTION REQUIRED: No chip ships without DFT.
    - Scan chain insertion
    - ATPG pattern generation
    - MBIST wrapper generation
    - JTAG infrastructure
    """
    verify_license()
    console.print(
        Panel(
            f"[accent]DFT Scan Insertion[/accent]\n"
            f"RTL: {rtl_file}\nTop: {top}\nChains: {scan_chains}",
            title="🔬 Design for Test",
        )
    )

    if testability:
        ok, analysis = run_testability_analysis(rtl_file, output_dir)
        console.print(
            f"  DFFs: {analysis['dff_count']} | LUTs: {analysis['lut_count']}"
        )
        console.print(
            f"  Estimated scan coverage: {analysis['estimated_scan_coverage']:.1f}%"
        )
        if analysis["dft_issues"]:
            console.print(f"  [warning]DFT Issues:[/warning]")
            for issue in analysis["dft_issues"]:
                console.print(f"    - {issue}")
        return

    result = run_scan_insertion(
        rtl_files=[rtl_file],
        top_module=top,
        output_dir=output_dir,
        scan_chain_count=scan_chains,
    )

    if result.ok:
        console.print(f"  [success]✓ DFT scan insertion PASS[/success]")
        console.print(f"  Scan chains: {result.scan_chain_count}")
        console.print(f"  Fault coverage: {result.atpg_coverage_percent:.1f}%")
        console.print(
            f"  Total faults: {result.total_faults:,} | Detected: {result.detected_faults:,}"
        )
        console.print(f"  Netlist: {result.scan_netlist_path}")
    else:
        console.print(f"  [error]✗ DFT FAILED[/error]")
        for e in result.errors:
            console.print(f"    {e}")


@app.command("power")
def power(
    netlist: str = typer.Option(
        ..., "--netlist", "-n", help="Gate-level Verilog netlist"
    ),
    output_dir: str = typer.Option(
        "./power_out", "--out", "-o", help="Output directory"
    ),
    vdd: float = typer.Option(1.8, "--vdd", help="Supply voltage in volts"),
    freq_mhz: float = typer.Option(50.0, "--freq", help="Clock frequency in MHz"),
    spef: str = typer.Option(
        "", "--spef", help="SPEF parasitic file for accurate power"
    ),
    pdk: str = typer.Option("sky130", "--pdk", help="PDK name"),
    json_output: bool = typer.Option(
        False, "--json", help="Output machine-readable JSON"
    ),
):
    """Run power analysis with dynamic/leakage breakdown and IR-drop check."""
    verify_license()
    console.print(
        Panel(
            f"[accent]Power Analysis[/accent]\n"
            f"VDD: {vdd}V | Freq: {freq_mhz}MHz | PDK: {pdk}",
            title="⚡ Power Analysis",
        )
    )
    spef_file = spef if spef and os.path.exists(spef) else None
    os.makedirs(output_dir, exist_ok=True)

    result = run_power_analysis(
        netlist=netlist,
        sdc="",
        spef_file=spef_file,
        output_dir=output_dir,
        vdd_voltage=vdd,
        clock_frequency_mhz=freq_mhz,
        enable_ir_drop=True,
        pdk=pdk,
    )

    if result.ok:
        console.print(f"  [success]✓ Power analysis complete[/success]")
    console.print(f"  Total: {result.total_power_uW:.2f}uW")
    console.print(
        f"  Dynamic: {result.dynamic_power_uW:.2f}uW | Leakage: {result.leakage_power_uW:.4f}uW"
    )
    console.print(f"  Power density: {result.power_density_mW_per_mm2:.3f}mW/mm²")
    console.print(f"  Junction temp: {result.junction_temp_C:.1f}°C")

    ir = result.ir_drop
    if ir.max_drop_mV > 0:
        icon = "✓" if ir.ok else "⚠"
        console.print(
            f"  {icon} IR-drop: {ir.max_drop_mV:.4f}mV (worst: {ir.worst_node})"
        )

    if result.errors:
        console.print(f"  [error]Errors:[/error]")
        for e in result.errors:
            console.print(f"    {e}")

    if json_output:
        console.print_json(
            {
                "total_uW": result.total_power_uW,
                "dynamic_uW": result.dynamic_power_uW,
                "leakage_uW": result.leakage_power_uW,
                "power_density_mW_mm2": result.power_density_mW_per_mm2,
                "junction_temp_C": result.junction_temp_C,
                "ir_drop_max_mV": ir.max_drop_mV,
            }
        )


@app.command("drc")
def drc(
    gds: str = typer.Option(..., "--gds", "-g", help="GDSII layout file"),
    tech: str = typer.Option(..., "--tech", "-t", help="Magic technology file (.tech)"),
    output_dir: str = typer.Option("./drc_out", "--out", "-o", help="Output directory"),
    pdk: str = typer.Option("sky130", "--pdk", help="PDK name"),
    json_output: bool = typer.Option(
        False, "--json", help="Output machine-readable JSON"
    ),
):
    """Run independent Magic DRC on GDSII layout.

    PRODUCTION REQUIRED: 0 DRC violations before tapeout.
    """
    verify_license()
    console.print(
        Panel(
            f"[accent]Magic DRC[/accent]\nGDS: {gds}\nTech: {tech}",
            title="🔍 Physical Verification",
        )
    )
    os.makedirs(output_dir, exist_ok=True)

    result = run_magic_drc(
        gds_path=gds,
        tech_file=tech,
        output_dir=output_dir,
        pdk=pdk,
    )

    if result.ok:
        console.print(f"  [success]✓ DRC PASS — 0 violations[/success]")
    else:
        console.print(
            f"  [error]✗ DRC FAIL — {result.drc_violations} violations[/error]"
        )

    console.print(
        f"  Runtime: {result.runtime_sec:.1f}s | Report: {result.drc_report_path}"
    )
    if result.violations:
        for v in result.violations[:5]:
            console.print(f"  [error]  Layer {v.layer}: {v.message}[/error]")

    if json_output:
        console.print_json(
            {
                "ok": result.ok,
                "drc_violations": result.drc_violations,
                "violations": [
                    {"layer": v.layer, "message": v.message} for v in result.violations
                ],
                "runtime_sec": result.runtime_sec,
            }
        )


@app.command("lvs")
def lvs(
    schematic: str = typer.Option(
        ..., "--sch", "-s", help="Schematic netlist (Verilog)"
    ),
    layout_gds: str = typer.Option(..., "--gds", "-g", help="Layout GDSII file"),
    tech_setup: str = typer.Option(..., "--setup", help="Netgen tech setup file"),
    output_dir: str = typer.Option("./lvs_out", "--out", "-o", help="Output directory"),
    pdk: str = typer.Option("sky130", "--pdk", help="PDK name"),
    json_output: bool = typer.Option(
        False, "--json", help="Output machine-readable JSON"
    ),
):
    """Run Netgen LVS (Layout vs Schematic) equivalence check.

    PRODUCTION REQUIRED: Schematic must be equivalent to layout.
    """
    verify_license()
    console.print(
        Panel(
            f"[accent]Netgen LVS[/accent]\nSchematic: {schematic}\nLayout: {layout_gds}",
            title="🔍 Layout vs Schematic",
        )
    )
    os.makedirs(output_dir, exist_ok=True)

    result = run_netgen_lvs(
        schematic_verilog=schematic,
        layout_gds=layout_gds,
        output_dir=output_dir,
        tech_setup=tech_setup,
        pdk=pdk,
    )

    if result.equivalent:
        console.print(
            f"  [success]✓ LVS PASS — Schematic equivalent to Layout[/success]"
        )
    else:
        console.print(f"  [error]✗ LVS FAIL[/error]")
        console.print(
            f"  Net mismatches: {result.net_mismatches} | Pin mismatches: {result.pin_mismatches}"
        )
        console.print(f"  Unconnected nets: {result.unconnected_nets}")

    console.print(
        f"  Runtime: {result.runtime_sec:.1f}s | Report: {result.lvs_report_path}"
    )

    if json_output:
        console.print_json(
            {
                "ok": result.ok,
                "equivalent": result.equivalent,
                "net_mismatches": result.net_mismatches,
                "pin_mismatches": result.pin_mismatches,
                "runtime_sec": result.runtime_sec,
            }
        )


@app.command("report")
def report(
    design: str = typer.Option(..., "--design", "-d", help="Design name"),
    output_dir: str = typer.Option("./reports", "--out", "-o", help="Output directory"),
    pdk: str = typer.Option("sky130", "--pdk", help="PDK name"),
    format: str = typer.Option(
        "all", "--format", "-f", help="Format: json, csv, md, all"
    ),
):
    """Generate structured QOR signoff report from build data.

    Produces:
    - JSON: Machine-readable QOR data for CI/CD
    - CSV: Human-readable checklist
    - Markdown: Design review document
    """
    verify_license()
    os.makedirs(output_dir, exist_ok=True)

    reporter = SignoffReporter(design, pdk)

    json_path = reporter.generate_json_report(
        os.path.join(output_dir, f"{design}_qor.json")
    )
    csv_path = reporter.generate_csv_report(
        os.path.join(output_dir, f"{design}_checklist.csv")
    )
    md_path = reporter.generate_markdown_report(
        os.path.join(output_dir, f"{design}_signoff.md")
    )

    console.print(
        Panel(
            f"[accent]Signoff Reports[/accent]\n"
            f"JSON: {json_path}\n"
            f"CSV:  {csv_path}\n"
            f"MD:   {md_path}",
            title="📊 QOR Reports",
        )
    )


if __name__ == "__main__":
    app()
