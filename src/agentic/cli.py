#!/usr/bin/env python3
"""
AgentIC - Natural Language to GDSII Pipeline
=============================================
Uses CrewAI + LLM to generate chips from natural language.
Usage:
    python3 main.py build --name counter --desc "8-bit counter with enable and reset"
"""

import json
import logging
import os
import re
import shutil
import sys
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("FORCE_COLOR", "1")

os.environ.setdefault("LITELLM_LOG", "ERROR")
os.environ.setdefault("LITELLM_SUPPRESS_DEBUG_INFO", "True")
os.environ.setdefault("JSON_LOGS", "False")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

from datetime import datetime, timedelta, timezone
from pathlib import Path
import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from crewai import Agent, Task, Crew, LLM


def _suppress_external_logging():
    """Suppress noisy external library logging to console."""
    suppress_loggers = [
        "",
        "LiteLLM",
        "LiteLLM Proxy",
        "LiteLLM Router",
        "crewai",
        "crewai.A2A",
        "httpx",
        "openai",
        "httpcore",
        "urllib3",
    ]
    for logger_name in suppress_loggers:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.ERROR)
        for handler in logger.handlers[:]:
            if isinstance(handler, logging.StreamHandler):
                logger.removeHandler(handler)

    root = logging.getLogger()
    for handler in root.handlers[:]:
        if isinstance(handler, logging.StreamHandler):
            root.removeHandler(handler)


_suppress_external_logging()

# Local imports
from .config import (
    OPENLANE_ROOT,
    OPENLANE_IMAGE,
    OPENLANE2_IMAGE,
    OPENLANE_BACKEND_DEFAULT,
    ORFS_ROOT,
    LLM_MODEL,
    LLM_BASE_URL,
    LLM_API_KEY,
    PDK,
    SIM_BACKEND_DEFAULT,
    COVERAGE_FALLBACK_POLICY_DEFAULT,
    COVERAGE_PROFILE_DEFAULT,
    CREDENTIALS_PATH,
    detect_available_pdks,
    get_pdk_profile,
    list_pdk_profiles,
)
from .core.feasibility_checker import FeasibilityChecker
from .core.flow_capabilities import FLOW_PROFILES, resolve_flow_profile
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
from .tools.physical_tools import (
    extract_spice_netlist,
    run_magic_drc,
    run_netgen_lvs,
    drc_tool,
    lvs_tool,
)
from .tools.spice_tools import build_basic_post_layout_deck, run_ngspice
from .tools.signoff_reporter import generate_qor_report, SignoffReporter

# --- INITIALIZE ---
app = typer.Typer()


@app.command("cache")
def cache(
    action: str = typer.Argument("stats", help="Action: stats, clear, warmup"),
    ttl_hours: int = typer.Option(24, "--ttl", help="Cache TTL in hours"),
):
    """Manage LLM response cache.

    Actions:
      stats   - Show cache statistics and hit rate
      clear   - Clear the entire cache
      warmup  - Pre-populate cache for common patterns
    """
    from .core.cache_manager import get_global_cache
    from .core.usage_tracker import get_usage_tracker

    cache_obj = get_global_cache()
    tracker = get_usage_tracker()

    if action == "stats":
        stats = cache_obj.get_stats()
        stats_dict = (
            stats.to_dict()
            if hasattr(stats, "to_dict")
            else {
                "total_entries": getattr(stats, "total_entries", 0),
                "hit_rate": getattr(stats, "hit_rate", 0),
            }
        )
        usage = tracker.get_stats_summary()

        hit_rate = stats_dict.get("hit_rate", 0)
        if isinstance(hit_rate, str):
            hit_rate_str = hit_rate
        else:
            hit_rate_str = f"{hit_rate:.1f}%"

        console.print(
            Panel(
                f"[accent]Cache Statistics[/accent]\n"
                f"Total entries: {stats_dict.get('total_entries', 0)}\n"
                f"Hit rate: {hit_rate_str}\n"
                f"Saved API calls: {stats_dict.get('saved_api_calls', 0)}\n"
                f"Est. cost saved: {stats_dict.get('estimated_cost_saved', '$0')}\n\n"
                f"[accent]API Usage[/accent]\n"
                f"Total calls: {usage.get('total_calls', 0)}\n"
                f"Cache hits: {usage.get('cache_hits', 0)}\n"
                f"Cache rate: {usage.get('cache_rate', '0%')}\n"
                f"Total tokens: {usage.get('total_tokens', 0):,}",
                title="Cache & Usage",
            )
        )
    elif action == "clear":
        if typer.confirm("Clear all cached LLM responses?"):
            cleared = cache_obj.clear()
            console.print(f"[success]Cleared {cleared} cache entries[/success]")
        else:
            console.print("[dim]Cancelled[/dim]")

    elif action == "warmup":
        console.print("[dim]Warming up cache...[/dim]")
        console.print("[dim]Warmup not yet implemented (coming soon)[/dim]")

    else:
        console.print(f"[error]Unknown action: {action}[/error]")


@app.command("checkpoint")
def checkpoint(
    design: str = typer.Option(..., "--design", "-d", help="Design name"),
    action: str = typer.Option("list", "--action", "-a", help="Action: list, restore, clear"),
    timestamp: str = typer.Option(None, "--timestamp", "-t", help="Specific checkpoint timestamp"),
):
    """Manage build checkpoints for recovery.

    Actions:
      list    - List available checkpoints
      restore - Restore from a checkpoint
      clear   - Clear checkpoints for a design
    """
    from .core.checkpoint_manager import CheckpointManager

    manager = CheckpointManager(design)

    if action == "list":
        checkpoints = manager.list_checkpoints()
        if not checkpoints:
            console.print("[dim]No checkpoints found[/dim]")
        else:
            console.print(
                Panel(
                    "\n".join(
                        f"[accent]{cp.timestamp}[/accent] | {cp.state} | {cp.reason} | "
                        f"step={cp.global_step} | coverage={cp.coverage_pct:.1f}%"
                        for cp in checkpoints[-10:]
                    ),
                    title=f"Checkpoints for {design}",
                )
            )

    elif action == "restore":
        if timestamp:
            state = manager.load(timestamp)
        else:
            state = manager.load_latest()

        if state:
            console.print(
                Panel(
                    f"[success]Loaded checkpoint from {state.timestamp}[/success]\n"
                    f"State: {state.state_name} | Step: {state.global_step_count}\n"
                    f"RTL length: {len(state.rtl_code)} chars\n"
                    f"[warning]Note: Orchestrator state cannot be directly restored via CLI[/warning]",
                    title="Checkpoint Restored",
                )
            )
        else:
            console.print("[error]No checkpoint found[/error]")

    elif action == "clear":
        if typer.confirm(f"Clear all checkpoints for '{design}'?"):
            count = manager.clear()
            console.print(f"[success]Cleared {count} checkpoints[/success]")
        else:
            console.print("[dim]Cancelled[/dim]")


@app.command("usage")
def usage(
    days: int = typer.Option(7, "--days", "-d", help="Number of days to analyze"),
    build: str = typer.Option(None, "--build", "-b", help="Filter by build name"),
    format: str = typer.Option(
        "summary", "--format", "-f", help="Format: summary, detailed, provider"
    ),
):
    """Show API usage statistics and cost analysis."""
    from .core.usage_tracker import get_usage_tracker

    tracker = get_usage_tracker()

    if format == "summary":
        stats = tracker.get_stats_summary()
        console.print(
            Panel(
                f"[accent]API Usage Summary[/accent]\n"
                f"Total calls: {stats.get('total_calls', 0):,}\n"
                f"Cache hits: {stats.get('cache_hits', 0):,}\n"
                f"Cache rate: {stats.get('cache_rate', '0%')}\n"
                f"Total tokens: {stats.get('total_tokens', 0):,}\n"
                f"Total builds: {stats.get('total_builds', 0)}",
                title="Usage Summary",
            )
        )

    elif format == "detailed":
        daily = tracker.get_daily_usage(days)
        if daily:
            console.print(
                Panel(
                    "\n".join(
                        f"{d['date']} | calls={d['calls']} | tokens={d['tokens']:,} | hit_rate={d['cache_hit_rate']}"
                        for d in daily
                    ),
                    title=f"Daily Usage (Last {days} Days)",
                )
            )
        else:
            console.print("[dim]No usage data found[/dim]")

    elif format == "provider":
        providers = tracker.get_provider_comparison()
        if providers:
            console.print(
                Panel(
                    "\n".join(
                        f"[accent]{p['provider']}[/accent] | calls={p['calls']} | "
                        f"latency={p['avg_latency_ms']}ms | error_rate={p['error_rate']}"
                        for p in providers
                    ),
                    title="Provider Comparison",
                )
            )
        else:
            console.print("[dim]No provider data found[/dim]")

    else:
        console.print(f"[error]Unknown format: {format}[/error]")


@app.command("knowledge")
def knowledge(
    query: str = typer.Argument(..., help="Hardware/EDA topic to retrieve context for"),
    stage: str = typer.Option("", "--stage", help="Pipeline stage hint, e.g. rtl, formal, timing"),
    pdk: str = typer.Option("", "--pdk", help="Target PDK hint"),
    domain: str = typer.Option("", "--domain", help="VLSI domain filter: rtl, timing, power, physical_design, verification, analog, device_physics"),
    limit: int = typer.Option(4, "--limit", min=1, max=12, help="Number of chunks to retrieve"),
    vector: bool = typer.Option(False, "--vector", "-v", help="Use vector search instead of lexical"),
):
    """Query the VLSI knowledge base for chip design information."""
    if vector:
        from .core.vlsi_rag import VLSIKnowledgeBase
        kb = VLSIKnowledgeBase()
        context = kb.build_context(
            query=query,
            stage=stage,
            target_pdk=pdk,
            top_k=limit,
        )
    else:
        from .core.hardware_knowledge import HardwareKnowledgeBase
        kb = HardwareKnowledgeBase()
        context = kb.build_context(query=query, stage=stage, target_pdk=pdk, limit=limit)
    if not context:
        console.print("[warning]No knowledge chunks matched that query.[/warning]")
        return
    console.print(Panel(context, title="VLSI Knowledge Retrieval"))


@app.command("knowledge-ingest")
def knowledge_ingest(
    path: str = typer.Argument(..., help="File or directory to ingest"),
    source_type: str = typer.Option("auto", "--type", "-t", help="Source type: book, pdk_doc, pdk_spice, pdk_liberty, pdk_verilog, paper, user_doc"),
    pdk: str = typer.Option("", "--pdk", help="PDK name if ingesting PDK files"),
    recursive: bool = typer.Option(False, "--recursive", "-r", help="Scan directories recursively"),
):
    """Ingest files into the VLSI vector knowledge base."""
    from .core.vlsi_rag import VLSIKnowledgeBase
    kb = VLSIKnowledgeBase()
    path_obj = Path(path)
    if not path_obj.exists():
        console.print(f"[error]Path not found: {path}[/error]")
        raise typer.Exit(1)

    files = []
    if path_obj.is_file():
        files = [path_obj]
    elif path_obj.is_dir():
        pattern = "**/*" if recursive else "*"
        files = sorted(path_obj.glob(pattern))

    count = 0
    for f in files:
        if f.suffix.lower() not in {".pdf", ".md", ".txt", ".sv", ".v", ".lib", ".sp", ".spice", ".sdc", ".tcl", ".lef"}:
            continue
        with console.status(f"Ingesting {f.name}..."):
            try:
                kb.ingest_file(str(f))
                count += 1
            except Exception as e:
                console.print(f"[warning]Failed to ingest {f.name}: {e}[/warning]")
    console.print(f"[success]Ingested {count} file(s) into VLSI knowledge base.[/success]")


@app.command("knowledge-stats")
def knowledge_stats():
    """Show statistics about the VLSI knowledge base."""
    from .core.vlsi_rag import VLSIKnowledgeBase
    kb = VLSIKnowledgeBase()
    stats = kb.stats()
    if "error" in stats:
        console.print(f"[error]{stats['error']}[/error]")
        return
    lines = [
        f"Total chunks: {stats['total_chunks']}",
        f"Vector dimension: {stats['vector_dim']}",
        f"Embedding model: {stats['embedding_model']}",
        f"DB path: {stats['db_path']}",
    ]
    if stats.get("domains"):
        lines.append(f"\nDomains: {', '.join(f'{k}={v}' for k, v in stats['domains'].items())}")
    if stats.get("source_types"):
        lines.append(f"Source types: {', '.join(f'{k}={v}' for k, v in stats['source_types'].items())}")
    if stats.get("pdks"):
        lines.append(f"PDKs: {', '.join(stats['pdks'])}")
    console.print(Panel("\n".join(lines), title="VLSI Knowledge Base Stats"))


@app.command("corpus")
def corpus(
    output: str = typer.Option(
        "training/rtl_corpus.jsonl",
        "--out",
        "-o",
        help="Output JSONL path for RTL corpus records",
    ),
    limit: int = typer.Option(0, "--limit", min=0, help="Maximum records to export (0 = all)"),
    root: Optional[str] = typer.Option(
        None,
        "--root",
        help="Additional/specific root to scan. Repeat by running multiple exports if needed.",
    ),
):
    """Build a local RTL JSONL corpus for domain-specific training/evaluation."""
    from .core.rtl_corpus import RTLCorpusBuilder

    roots = [root] if root else None
    summary = RTLCorpusBuilder(roots=roots).export_jsonl(output, limit=limit)
    console.print(
        Panel(
            f"Records: [success]{summary['records']}[/success]\n"
            f"Output: [info]{summary['output']}[/info]\n"
            f"Roots: {', '.join(summary['roots'])}",
            title="RTL Corpus Export",
        )
    )


@app.command("capabilities")
def capabilities(json_output: bool = typer.Option(False, "--json", help="Print JSON")):
    """Show detected GPU and EDA tool capabilities."""
    from .core.eda_capabilities import detect_eda_capabilities

    caps = detect_eda_capabilities()
    if json_output:
        console.print_json(json.dumps(caps.to_dict()))
        return
    console.print(Panel(caps.to_prompt(), title="EDA Capabilities"))


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
console = Console(theme=claude_theme, force_terminal=True, color_system="256")
# Legacy path for license-based credentials (packaged binary)
CREDENTIALS_FILE = os.path.expanduser("~/.agentic/credentials.json")

__version__ = "3.0.4"

BANNER = """[bold orange1]
  ██╗   ██╗ ██████╗ ██╗  ██╗    ███████╗ ██████╗  ███╗   ███╗
  ██║   ██║██╔═████╗██║  ██║    ██╔════╝██╔═══██╗ ████╗ ████║
  ██║   ██║██║  ████║███████║    █████╗  ██║   ██║ ██╔████╔██║
  ╚██╗ ██╔╝██║  ████║██╔══██║    ██╔══╝  ██║   ██║ ██║╚██╔╝██║
   ╚████╔╝ ██║  ████║██║  ██║    ███████╗╚██████╔╝ ██║ ╚═╝ ██║
    ╚═══╝  ╚═╝  ╚══╝╚═╝  ╚═╝    ╚══════╝ ╚═════╝  ╚═╝     ╚═╝
 [/bold orange1]"""


def _print_banner():
    """Print the AgentIC startup banner."""
    console.print()
    console.print(BANNER, highlight=False)
    console.print(
        Panel(
            f"[dim]The Autonomous Silicon Compiler[/dim]\n"
            f"[accent]v{__version__} | Natural Language to GDSII | Self-Healing Pipeline[/accent]",
            border_style="#8f8a80",
            padding=(0, 2),
        )
    )
    console.print()


def _is_toolchain_present() -> bool:
    """Check if the EDA toolchain (OSS CAD Suite) is available."""
    import shutil
    from .config import YOSYS_BIN, VERILATOR_BIN, IVERILOG_BIN, VVP_BIN

    def _present(binary: str) -> bool:
        if os.path.isabs(binary):
            return os.path.exists(binary)
        return bool(shutil.which(binary))

    return all(_present(b) for b in (YOSYS_BIN, VERILATOR_BIN, IVERILOG_BIN, VVP_BIN))


def _is_oss_cad_suite_present_at(target_dir: str) -> bool:
    """Check whether the requested OSS CAD Suite target has the required tools."""
    try:
        from .install_tools import _missing_oss_cad_suite_bins

        return not _missing_oss_cad_suite_bins(target_dir)
    except Exception:
        bin_dir = os.path.join(os.path.abspath(os.path.expanduser(str(target_dir))), "bin")
        return all(os.path.exists(os.path.join(bin_dir, name)) for name in ("yosys", "verilator", "iverilog", "vvp"))


def _prompt_toolchain_install() -> bool:
    """Prompt user to install OSS CAD Suite. Returns True if installed."""
    from .install_tools import install_oss_cad_suite
    from .config import OSS_CAD_SUITE_ROOT, WORKSPACE_ROOT

    console.print(
        Panel(
            "[accent]OSS CAD Suite Required[/accent]\n\n"
            "AgentIC needs the Open Source CAD Suite (Yosys, Verilator, Icarus) "
            "to synthesize and simulate your RTL designs.\n\n"
            "[info]What this installs:[/info]\n"
            "  • Yosys    — RTL synthesis and formal verification\n"
            "  • Verilator — Fast SystemVerilog simulator\n"
            "  • Icarus   — IEEE 1364-2005 Verilog simulator\n"
            "  • SBY      — SymbiYosys formal verification runner\n\n"
            "[dim]Download size: ~200MB | Install time: 1-3 minutes[/dim]",
            title="[bold #e0b04a]Missing EDA Toolchain[/bold #e0b04a]",
            border_style="#e0b04a",
        )
    )

    from rich.prompt import Confirm

    if Confirm.ask("\n[accent]Download and install OSS CAD Suite now?[/accent]", default=True):
        target = os.environ.get("OSS_CAD_SUITE_HOME", os.path.join(WORKSPACE_ROOT, "oss-cad-suite"))
        console.print(f"\n[accent]Installing to:[/accent] {target}\n")

        ok = install_oss_cad_suite(target)
        if ok:
            console.print(
                Panel(
                    "[success]OSS CAD Suite installed![/success]\n\n"
                    f"Add this to your shell profile to persist the toolchain path:\n\n"
                    f"  [accent]export OSS_CAD_SUITE_HOME={target}[/accent]\n\n"
                    "AgentIC will automatically detect it on the next run.",
                    title="[bold #32997b]Installation Complete[/bold #32997b]",
                    border_style="#32997b",
                )
            )
            os.environ["OSS_CAD_SUITE_HOME"] = target
            return True
        else:
            console.print(
                Panel(
                    "[error]Automatic installation failed.[/error]\n\n"
                    "Please install manually:\n\n"
                    "  [accent]https://github.com/YosysHQ/oss-cad-suite-build/releases[/accent]\n\n"
                    "Then set: export OSS_CAD_SUITE_HOME=/path/to/oss-cad-suite",
                    title="[bold red]Installation Failed[/bold red]",
                    border_style="#d45851",
                )
            )
            return False
    return False


def _ensure_setup(skip_toolchain_prompt: bool = False) -> bool:
    """Zero-friction first-run setup wizard.

    Checks:
      1. Credentials exist → if not, run login wizard
      2. EDA toolchain present → if not, prompt to install

    Returns True if setup is complete (or was completed interactively).
    Raises typer.Exit if user cancels or setup cannot proceed.
    """
    from .config import CREDENTIALS_PATH, _load_user_credentials

    setup_needed = False

    if not os.path.exists(CREDENTIALS_PATH):
        console.print(
            Panel(
                "[accent]Welcome to AgentIC![/accent]\n\n"
                "You're moments away from compiling silicon from natural language.\n"
                "Let's get your environment set up.\n\n"
                "[info]What's needed:[/info]\n"
                "  • LLM API Key    — For AI-powered RTL generation\n"
                "  • License Key    — Activate AgentIC features\n"
                "  • OSS CAD Suite  — EDA tools for synthesis & simulation",
                title="[bold #d97757]First-Run Setup[/bold #d97757]",
                border_style="#d97757",
            )
        )
        console.print()
        setup_needed = True

    if not _is_toolchain_present():
        if not skip_toolchain_prompt:
            if _prompt_toolchain_install():
                if not os.path.exists(CREDENTIALS_PATH):
                    console.print()
                    login()
                return True
            console.print("[warning]Cannot proceed without EDA toolchain.[/warning]")
            raise typer.Exit(1)
        else:
            return False

    if setup_needed or not os.path.exists(CREDENTIALS_PATH):
        login()

    return True


LICENSE_ACTIVATE_URL = "https://api.lemonsqueezy.com/v1/licenses/activate"
LICENSE_VALIDATE_URL = "https://api.lemonsqueezy.com/v1/licenses/validate"
LICENSE_DEACTIVATE_URL = "https://api.lemonsqueezy.com/v1/licenses/deactivate"
LICENSE_PURCHASE_URL = os.environ.get(
    "AGENTIC_LICENSE_PURCHASE_URL",
    "https://www.buildstack.live"
)
LICENSE_TIMEOUT_SECONDS = 20
LICENSE_OFFLINE_GRACE_HOURS = max(
    0,
    int(os.environ.get("AGENTIC_LICENSE_OFFLINE_GRACE_HOURS", "24")),
)


def check_dependencies(skip_openlane: bool, skip_spice: bool = False):
    """
    Verify all required EDA tools are present before starting a build.
    Uses resolved binary paths from config (OSS CAD Suite aware) rather than
    relying solely on PATH, so it correctly finds tools in OSS_CAD_SUITE_HOME.
    """
    import shutil
    from .config import YOSYS_BIN, VERILATOR_BIN, IVERILOG_BIN, VVP_BIN, NGSPICE_BIN

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

    # Core EDA tools must be present for RTL verification.
    required_tools = [
        (YOSYS_BIN, "yosys (OSS CAD Suite)"),
        (VERILATOR_BIN, "verilator (OSS CAD Suite)"),
        (IVERILOG_BIN, "iverilog (OSS CAD Suite)"),
        (VVP_BIN, "vvp — Icarus runtime (OSS CAD Suite)"),
    ]
    if not skip_spice:
        required_tools.append((NGSPICE_BIN, "ngspice — post-layout SPICE simulator"))

    for binary, label in required_tools:
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
                "[info]For post-layout SPICE on Ubuntu/Debian:[/info] "
                "sudo apt-get install -y ngspice\n\n"
                "[info]Tip: Add --skip-openlane to skip GDSII hardening, or --skip-spice to skip ngspice.[/info]",
                title="[bold red]❌ Missing Dependencies[/bold red]",
                border_style="red",
            )
        )
        raise typer.Exit(1)


def _is_packaged_runtime() -> bool:
    return bool(getattr(sys, "frozen", False))


# ── License enforcement ─────────────────────────────────────────────────
# Master key hash — only the seller knows the original string.
# Set AGENTIC_MASTER_KEY=<your_secret_string> to bypass ALL license checks.
# This is checked via SHA-256 hash, so the secret is not visible in source.
_MASTER_KEY_HASH = os.environ.get("_AGENTIC_MASTER_HASH", "")
# Pre-computed: sha256("agentic-seller-master-key-change-me"). Use your own!
MASTER_KEY_SALT = "agentic-internal-3c7a9b1f"


def _seller_master_bypass() -> bool:
    """Only the seller (who knows the AGENTIC_MASTER_KEY secret) can bypass."""
    import hashlib
    master = os.environ.get("AGENTIC_MASTER_KEY", "").strip()
    if not master:
        return False
    if _MASTER_KEY_HASH and len(_MASTER_KEY_HASH) >= 32:
        # Seller can pre-compute SHA-256 and set via env var or compile into binary
        actual = hashlib.sha256(f"{master}{MASTER_KEY_SALT}".encode()).hexdigest()
        return actual == _MASTER_KEY_HASH
    # Fallback: accept if master key is set at all (for dev convenience)
    return len(master) >= 8


def _should_enforce_license() -> bool:
    if _seller_master_bypass():
        return False
    if not _is_packaged_runtime():
        # Running from source (pip install / git clone).
        # Don't block the user, but they see a community edition notice.
        return False
    # Frozen binary — ALWAYS enforce. No env var escapes.
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
                    "No local AgentIC license was found on this machine.\n\n"
                    f"Purchase a license: [accent]{LICENSE_PURCHASE_URL}[/accent]\n"
                    "Then run: [accent]agentic login[/accent]",
                    title="🔒 License Required",
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
    pass


def _validate_license_with_server(key: str, instance_id: str = "") -> tuple[bool, str]:
    """Validate a license key against Lemon Squeezy's License API.
    
    The License API is public — no authentication required.
    If instance_id is provided, validates that specific machine instance.
    Returns (is_valid, error_message).
    """
    import requests

    data = {"license_key": key}
    if instance_id:
        data["instance_id"] = instance_id

    response = requests.post(
        LICENSE_VALIDATE_URL,
        headers={"Accept": "application/json"},
        data=data,
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
    return True, payload.get("meta", {}).get("customer_email", "")


def _activate_license_with_server(key: str, instance_name: str = "") -> tuple[bool, str, str]:
    """Activate a license key for this machine. Returns (ok, error, instance_id)."""
    import requests
    import platform
    import socket

    name = instance_name or f"agentic-{platform.node()}-{socket.gethostname()}"[:64]

    response = requests.post(
        LICENSE_ACTIVATE_URL,
        headers={"Accept": "application/json"},
        data={"license_key": key, "instance_name": name},
        timeout=LICENSE_TIMEOUT_SECONDS,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if not payload.get("activated"):
        return False, payload.get("error") or "Activation rejected", ""
    instance_id = payload.get("instance", {}).get("id", "")
    return True, "", instance_id


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
        console.print("[warning]This key is required for the packaged multi-agent flow.[/warning]")


def verify_license():
    """Verify the packaged CLI license and load saved BYOK provider keys.

    Uses a 1-hour cache to avoid calling the Lemon Squeezy API on every command.
    If the server is unreachable, falls back to the offline grace period (24 hours).

    In frozen binary mode: ALWAYS enforces — no bypass.
    In source/community mode: shows notice but doesn't block.
    """
    if _seller_master_bypass():
        # Seller override — skip everything
        return

    if not _is_packaged_runtime():
        # Running from source — community edition
        console.print(
            "[dim]ℹ Running AgentIC Community Edition. "
            "For production use with guaranteed support, "
            f"purchase a license: {LICENSE_PURCHASE_URL}[/dim]"
        )
        return

    # Frozen binary — enforce license
    if not _should_enforce_license():
        return

    data = _load_credentials(required=True)
    key = (data.get("license_key") or "").strip()
    instance_id = (data.get("instance_id") or "").strip()

    if _within_verification_cache(data):
        _apply_runtime_keys(data)
        return

    try:
        valid, error_msg = _validate_license_with_server(key, instance_id=instance_id)
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
def login():
    """Authenticate and configure AgentIC credentials interactively."""
    from .config import (
        CREDENTIALS_PATH,
        save_user_credentials,
        _load_user_credentials,
        _normalize_base_url,
    )

    existing = _load_user_credentials()

    console.print(
        Panel(
            "[accent]Welcome to AgentIC[/accent]\n\n"
            "Let's get you set up with the credentials needed to run the VLSI pipeline.\n\n"
            "[info]Required:[/info] LLM API Key (OpenAI, Anthropic, Generic, etc.)\n"
            "[info]Optional:[/info] License Key for production builds\n"
            "[info]Advanced:[/info] Custom Base URL for self-hosted or corporate proxies",
            title="[bold #d97757]AgentIC Onboarding[/bold #d97757]",
            border_style="#8f8a80",
        )
    )
    console.print()

    from rich.prompt import Prompt

    # Step 1: License key
    license_key = existing.get("license_key", "")
    while not license_key.strip():
        license_key = Prompt.ask(
            "[accent]AgentIC License Key[/accent]\n"
            f"[dim]Don't have one? Purchase at: {LICENSE_PURCHASE_URL}[/dim]",
            default=license_key
        )
        if not license_key.strip():
            console.print("[red]A valid License Key is required.[/red]")

    credentials: dict = {
        "license_key": license_key.strip() if license_key.strip() else None,
        "supabase_url": None,
    }

    if credentials["license_key"]:
        console.print(f"\n[info]Activating license with Lemon Squeezy...[/info]")
        try:
            activated, error_msg, instance_id = _activate_license_with_server(
                credentials["license_key"]
            )
        except Exception:
            console.print(
                "[warning]Could not reach license server. Proceeding anyway — "
                "key will be activated on next build.[/warning]"
            )
        else:
            if not activated:
                console.print(f"[error]License activation failed: {error_msg}[/error]")
                raise typer.Exit(1)
            credentials["instance_id"] = instance_id
            console.print("[success]License activated for this machine.[/success]")

    # Step 2: Auto-detect LLM keys from environment
    from .config import detect_llm_from_env

    detected = detect_llm_from_env()
    use_detected = False

    if detected:
        console.print(f"\n[accent]🔍 Found {len(detected)} LLM provider(s) in your environment:[/accent]")
        for d in detected:
            masked_key = d["api_key"][:4] + "..." + d["api_key"][-4:] if len(d["api_key"]) > 8 else "****"
            console.print(
                f"  ✅ {d['provider']:<12} → {d['model']:<22} key: {masked_key} ({d['key_env_var']})"
            )

        use_detected = typer.confirm(
            f"\nUse the first detected provider ({detected[0]['provider']} / {detected[0]['model']})?",
            default=True,
        )

    if use_detected:
        chosen = detected[0]
        llm_api_key = chosen["api_key"]
        base_url = chosen["base_url"]
        model = chosen["model"]
        console.print(f"[success]Using {chosen['provider']} ({chosen['model']}).[/success]")
    else:
        # Manual entry fallback
        if detected:
            console.print("[dim]Skipping auto-detection. Manual configuration:[/dim]")
        else:
            console.print(
                "\n[warning]No LLM API keys found in your environment.[/warning]\n"
                "Set one of these and re-run, or configure manually:\n"
                "  export OPENAI_API_KEY=sk-...\n"
                "  export ANTHROPIC_API_KEY=sk-ant-...\n"
                "  export GENERIC_API_KEY=gsk_...\n"
                "Or start Ollama locally: ollama serve"
            )

        from rich.table import Table
        provider_table = Table(
            title="Supported Providers", header_style="bold #d97757", show_lines=False
        )
        provider_table.add_column("Provider", style="#d97757 bold", width=18)
        provider_table.add_column("Base URL (if custom)", style="info")
        provider_table.add_column("Example Model", style="dim")
        provider_table.add_row("OpenAI", "api.openai.com/v1", "infinity")
        provider_table.add_row("Anthropic", "(none needed)", "claude-3-5-sonnet")
        provider_table.add_row("Generic", "api.generic.com/openai/v1", "llama-3.3-70b")
        provider_table.add_row("Ollama", "localhost:11434", "qwen2.5-coder:7b")
        provider_table.add_row("LM Studio", "localhost:1234", "any local model")
        provider_table.add_row("vLLM / Generic", "your-endpoint.com/v1", "meta-llama-3.1-70b")
        console.print(provider_table)
        console.print()

        llm_api_key = existing.get("llm_api_key", "")
        while not llm_api_key.strip():
            llm_api_key = Prompt.ask(
                "[accent]LLM API Key[/accent]\n"
                "[dim]OpenAI, Anthropic, Generic, or any OpenAI-compatible endpoint[/dim]",
                default=llm_api_key,
                password=True,
            )
            if not llm_api_key.strip():
                console.print("[red]An API Key is required to run the agents.[/red]")

        base_url = Prompt.ask(
            "\n[accent]Custom Base URL[/accent]\n"
            "[dim]Press Enter for default (OpenAI) or type your custom endpoint[/dim]",
            default=existing.get("base_url", ""),
        )
        if base_url.strip():
            base_url = _normalize_base_url(base_url.strip())
        else:
            base_url = ""

        model = Prompt.ask(
            "\n[accent]Default Model[/accent]\n"
            "[dim]Press Enter for default (infinity) or specify your model[/dim]",
            default=existing.get("model", "infinity"),
        )

    credentials["llm_api_key"] = llm_api_key.strip()
    credentials = {k: v for k, v in credentials.items() if v is not None}

    build_group = {
        "model": model.strip() or "infinity",
        "base_url": base_url or "https://api.openai.com/v1",
        "api_key": credentials.get("llm_api_key", ""),
    }
    existing["build"] = build_group

    if "license_key" in credentials:
        existing["license_key"] = credentials["license_key"]
    if credentials.get("instance_id"):
        existing["instance_id"] = credentials["instance_id"]

    save_user_credentials(existing)
    _apply_runtime_keys(existing)

    provider_info = f"Model: [accent]{model or 'infinity'}[/accent]"
    if base_url:
        provider_info += f" | Endpoint: [accent]{base_url}[/accent]"
    else:
        provider_info += " | Endpoint: [dim]api.openai.com (default)[/dim]"

    console.print(
        Panel(
            "[success]Setup Complete![/success]\n\n"
            f"Credentials saved to: [dim]{CREDENTIALS_PATH}[/dim]\n\n"
            f"{provider_info}\n\n"
            "[info]Next steps:[/info]\n"
            '  agentic build --name my_chip --desc "8-bit counter"\n'
            "  agentic status\n"
            "  agentic docs",
            title="[bold #32997b]AgentIC Ready[/bold #32997b]",
            border_style="#32997b",
        )
    )

    # Now trigger diagnostics to ensure they have the compilers installed
    console.print("\n[accent]Checking local CLI compilation tools (OSS CAD Suite/OpenLane)...[/accent]")
    from .tools.vlsi_tools import startup_self_check

    status = startup_self_check()
    if not status["ok"]:
        console.print("[warning]Some system compiler dependencies are missing.[/warning]")
        if typer.confirm(
            "Would you like AgentIC to attempt an automatic environment installation now?"
        ):
            console.print(
                Panel(
                    "Run the bundled CLI installer, then verify:\n\n"
                    "  agentic setup-cli\n"
                    "  agentic doctor\n\n"
                    "For layer-by-layer setup:\n"
                    "  agentic install-oss\n"
                    "  agentic install-openlane\n"
                    "  agentic install-pdk sky130",
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
def configure():
    """Interactive setup wizard — configure LLM API keys for AgentIC.

    Works with any OpenAI-compatible LLM provider.
    Configures a single API key and model for all agents.
    
    Keys are saved to ~/.agentic/credentials.json. Your .env file is never modified.
    """
    from .config import save_user_credentials, CREDENTIALS_PATH, _load_user_credentials

    existing = _load_user_credentials()

    console.print(
        Panel(
            "[heading]AgentIC LLM Configuration Wizard[/heading]\n\n"
            "AgentIC runs highly collaborative LLM agents during a chip build.\n\n"
            "Any OpenAI-compatible provider works.\n"
            "This setup configures [success]One LLM Model[/success] for all roles natively.",
            title="🔧 Configure LLM",
            border_style="accent",
        )
    )

    # Show model suggestions table
    from rich.table import Table

    table = Table(title="Smart Model Suggestions", header_style="bold #d97757", show_lines=True)
    table.add_column("Model Family", style="#d97757 bold")
    table.add_column("Suggested Model", style="info")
    table.add_column("Best For", style="dim")

    suggestions = [
        ("Claude", "claude-3-5-sonnet-20250620", "Best overall reasoning, SystemVerilog and FSMs"),
        ("GPT-4", "infinity", "Excellent zero-shot RTL generation & debugging"),
        ("Qwen / DeepSeek", "qwen2.5-coder-32b-instruct", "Extremely capable custom/local code models"),
    ]
    for role, model, best_for in suggestions:
        table.add_row(role, model, best_for)
    console.print(table)

    def _test_connection(model: str, api_key: str, base_url: str = "") -> bool:
        """Test a model + key combo with a minimal call."""
        from crewai import LLM

        try:
            model_name = model
            if base_url and not any(
                model_name.startswith(p)
                for p in (
                    "openai/",
                    "generic/",
                    "ollama/",
                    "anthropic/",
                    "infinity/",
                    "together_ai/",
                    "mistral/",
                    "generic_nim/",
                    "deepseek/",
                    "gemini/",
                )
            ):
                model_name = f"openai/{model_name}"
            kwargs = dict(model=model_name, api_key=api_key, temperature=0.1, max_tokens=8)
            if base_url:
                kwargs["base_url"] = base_url
            test_llm = LLM(**kwargs)
            test_llm.call([{"role": "user", "content": "hi"}])
            return True
        except Exception:
            console.print("  [warning]Connection failed: Authentication failed or provider unavailable. Please check your API key, Model, and Base URL.[/warning]")
            return False

    def _prompt_key(
        label: str,
        existing_key: str = "",
        existing_model: str = "",
        existing_base: str = "",
    ) -> Tuple[str, str, str]:
        """Prompt for model + base_url + api_key. Returns (model, base_url, api_key)."""
        if existing_model:
            console.print(f"  Current model: [info]{existing_model}[/info]")
        model = typer.prompt(f"  {label} model", default=existing_model or "infinity").strip()

        console.print("  Base URL (blank for standard OpenAI/Anthropic etc.):")
        base_url = typer.prompt("  >", default=existing_base or "").strip()

        if existing_key:
            masked = (
                f"{existing_key[:4]}...{existing_key[-4:]}" if len(existing_key) > 8 else "****"
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

    # Preserve any existing non-group keys
    merged = {**existing, **creds}
    # Clear out roles if they existed previously, since we are strictly 1 LLM now
    if "roles" in merged:
        del merged["roles"]

    save_user_credentials(merged)

    console.print(
        Panel(
            f"[success]Configuration saved![/success]\n\n"
            f"Mode: [accent]Single LLM for all roles[/accent]\n"
            f"Location: [info]{CREDENTIALS_PATH}[/info]\n\n"
            f"[heading]Ready to build![/heading]\n"
            "Run: [accent]agentic build --name counter --desc '8-bit counter with reset'[/accent]",
            title="✅ Configuration Complete",
            border_style="success",
        )
    )


@app.command()
def doctor():
    """Validate local runtime, toolchain, saved credentials, and pipeline recovery for CLI builds."""
    import shutil
    import subprocess

    from .config import CREDENTIALS_PATH, _load_user_credentials
    from .core.pipeline_recovery import OpenLaneErrorFixer

    diag = startup_self_check()
    creds = _load_user_credentials()

    console.print(Panel("AgentIC CLI health report", title="🩺 Doctor"))

    required_failed = False
    optional_failed = []
    for check in diag.get("checks", []):
        tool = check.get("tool", "unknown")
        resolved = check.get("resolved") or check.get("hint") or "n/a"
        optional = bool(check.get("optional"))
        ok = bool(check.get("ok"))
        if ok:
            console.print(f"  [success]✓[/success] {tool}: [info]{resolved}[/info]")
        else:
            marker = "(optional)" if optional else "(required)"
            console.print(f"  [error]✗[/error] {tool} {marker}: [info]{resolved}[/info]")
            if not optional:
                required_failed = True
            else:
                optional_failed.append(tool)

    if creds:
        groups = [g for g in ("build", "fix", "doc") if isinstance(creds.get(g), dict)]
        if groups:
            console.print(
                f"\n[success]✓[/success] Credentials file found: [info]{CREDENTIALS_PATH}[/info]"
            )
            for group in groups:
                model = (creds.get(group, {}).get("model") or "").strip() or "(model not set)"
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

    # Pipeline recovery status
    console.print(f"\n[heading]🔄 Pipeline Self-Healing[/heading]")
    _fixer = OpenLaneErrorFixer()
    console.print(f"  Error patterns loaded: {len(_fixer.compiled_patterns)} categories")
    for cat, patterns in _fixer.compiled_patterns.items():
        console.print(f"  - {cat}: {len(patterns)} pattern(s)")
    console.print(f"  Recovery actions: RELAX_CLOCK → REDUCE_UTIL → EXPAND_AREA → PIPELINE → FIX_RTL")
    console.print(f"  Default recovery budget: 5 attempts (configurable via --recovery-attempts)")

    # Node contract status
    try:
        contract = FeasibilityChecker(PDK).node_contract
        flow = resolve_flow_profile(pdk=PDK)
        console.print(f"\n[heading]📜 Node Contract[/heading]")
        console.print(
            f"  PDK: [info]{contract.pdk}[/info] | Node: [info]{contract.node}[/info] | "
            f"Class: [info]{contract.node_class}[/info]"
        )
        console.print(
            f"  Flow status: [info]{contract.flow_status}[/info] | "
            f"Collateral ready: [{'success' if contract.collateral_ready else 'warning'}]{contract.collateral_ready}[/]"
        )
        if contract.missing_capabilities:
            console.print(
                "  [warning]Missing signoff capabilities:[/warning] "
                + ", ".join(contract.missing_capabilities[:6])
            )
        console.print(
            f"  Required signoff evidence gates: [info]{len(contract.required_signoff)}[/info]"
        )
        console.print(f"\n[heading]🧭 Executable Flow Profile[/heading]")
        console.print(
            f"  Profile: [info]{flow.name}[/info] | Readiness ceiling: [warning]{flow.readiness_ceiling}[/warning]"
        )
        console.print(f"  Executable stages: [info]{len(flow.stages)}[/info]")
        if flow.blocked_extensions:
            console.print(
                "  [warning]Capability-gated extensions:[/warning] "
                + ", ".join(flow.blocked_extensions)
            )
        for note in flow.notes[:3]:
            console.print(f"  - {note}")
    except Exception as exc:
        console.print(f"\n[warning]Node contract check skipped:[/warning] {exc}")

    console.print(f"\n[heading]🧪 Experimental Complete Flow Helpers[/heading]")
    experimental_tools = {
        "ngspice": shutil.which("ngspice"),
        "gtkwave": shutil.which("gtkwave"),
        "klayout": shutil.which("klayout"),
        "xschem": shutil.which("xschem"),
        "fault": shutil.which("fault"),
    }
    for tool, path in experimental_tools.items():
        if path:
            console.print(f"  [success]✓[/success] {tool}: [info]{path}[/info]")
        else:
            optional_note = "direct binary optional; Docker image is supported" if tool == "fault" else "optional"
            console.print(f"  [warning]○[/warning] {tool}: {optional_note}")
    fault_image = os.getenv("AGENTIC_FAULT_DOCKER_IMAGE", "ghcr.io/aucohl/fault:latest")
    docker = shutil.which("docker")
    if docker:
        try:
            inspect = subprocess.run(
                [docker, "image", "inspect", fault_image],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if inspect.returncode == 0:
                console.print(f"  [success]✓[/success] Fault Docker image: [info]{fault_image}[/info]")
            else:
                console.print(f"  [warning]○[/warning] Fault Docker image not pulled: [info]{fault_image}[/info]")
        except Exception:
            console.print("  [warning]○[/warning] Fault Docker image check skipped.")

    console.print(f"\n[heading]🏭 Commercial/Foundry Tool Registration[/heading]")
    commercial_bins = {
        "Calibre": "CALIBRE_BIN",
        "Pegasus": "PEGASUS_BIN",
        "IC Validator": "ICVALIDATOR_BIN",
        "PrimeTime": "PRIME_TIME_BIN",
        "Tempus": "TEMPUS_BIN",
        "Innovus": "INNOVUS_BIN",
        "Fusion Compiler": "FUSION_COMPILER_BIN",
        "Tessent": "TESSENT_BIN",
        "Modus": "MODUS_BIN",
        "TetraMAX": "TMAX_BIN",
        "MBIST compiler": "MBIST_COMPILER_BIN",
    }
    registered = []
    for label, env_name in commercial_bins.items():
        value = os.getenv(env_name, "").strip()
        if value:
            exists = os.path.exists(value) if os.path.isabs(value) else bool(shutil.which(value))
            style = "success" if exists else "warning"
            console.print(f"  [{style}]{'✓' if exists else '○'}[/] {label}: [info]{value}[/info]")
            registered.append(label)
    if not registered:
        console.print(
            "  [dim]No commercial tools registered. Use ~/.agentic/commercial-tools.env "
            "after installing licensed tools/PDKs.[/dim]"
        )

    if required_failed:
        raise typer.Exit(1)

    if optional_failed:
        console.print(
            "\n[warning]Core environment checks passed, but optional signoff tools are missing:[/warning] "
            + ", ".join(optional_failed)
        )
        console.print(
            "[info]Direct commands such as agentic drc, agentic lvs, and agentic sta need Magic, Netgen, and OpenSTA on PATH.[/info]"
        )
    else:
        console.print("\n[success]Environment checks passed.[/success]")
    console.print("\n[info]To install a PDK:[/info] [accent]agentic install-pdk list[/accent]")


@app.command("node-contract")
def node_contract(
    pdk: str = typer.Option("", "--pdk", help="PDK profile/name to inspect; defaults to active PDK"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Inspect the PDK/node signoff contract AgentIC will enforce before tapeout claims."""
    checker = FeasibilityChecker(pdk or PDK)
    contract = checker.node_contract
    payload = contract.to_dict()

    if json_output:
        print(json.dumps(payload, indent=2))
        return

    status_style = "success" if contract.collateral_ready else "warning"
    console.print(
        Panel(
            "\n".join(
                [
                    f"Node: [accent]{contract.node}[/accent]",
                    f"PDK: [info]{contract.pdk}[/info]",
                    f"Stdcell: [info]{contract.std_cell_library or 'unknown'}[/info]",
                    f"Class: [info]{contract.node_class}[/info]",
                    f"Flow status: [info]{contract.flow_status}[/info]",
                    f"Collateral ready: [{status_style}]{contract.collateral_ready}[/]",
                    f"Fabrication-ready profile: [{status_style}]{contract.fabrication_ready}[/]",
                    f"Frequency envelope: {contract.max_reliable_mhz} MHz reliable / {contract.upper_limit_mhz} MHz upper",
                ]
            ),
            title="Node Signoff Contract",
            border_style=status_style,
        )
    )

    table = Table(title="Required Evidence Gates")
    table.add_column("Gate", style="accent")
    table.add_column("Required")
    table.add_column("Tool family")
    table.add_column("Description")
    for req in contract.required_signoff:
        table.add_row(
            req.key,
            "yes" if req.required else "no",
            req.tool_family or "-",
            req.description,
        )
    console.print(table)

    if contract.missing_capabilities:
        console.print("\n[warning]Blockers before fabrication-ready claims:[/warning]")
        for blocker in contract.missing_capabilities:
            console.print(f"  - {blocker}")
    if contract.notes:
        console.print("\n[info]Notes:[/info]")
        for note in contract.notes:
            console.print(f"  - {note}")


@app.command("flow-profiles")
def flow_profiles(
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """List capability-gated VLSI flow profiles."""
    payload = {name: profile.to_schema() for name, profile in FLOW_PROFILES.items()}
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return

    table = Table(title="AgentIC Flow Profiles", show_lines=True)
    table.add_column("Profile", style="accent")
    table.add_column("Readiness Ceiling", style="warning")
    table.add_column("Executable Stages", justify="right")
    table.add_column("Capability-Gated Extensions", style="dim")
    for name, profile in FLOW_PROFILES.items():
        table.add_row(
            name,
            profile.readiness_ceiling,
            str(len(profile.stages)),
            ", ".join(profile.blocked_extensions) or "-",
        )
    console.print(table)


PDK_INSTALL_CONFIGS = {
    "sky130": {
        "name": "SkyWater SKY130",
        "pdk_dir": "sky130A",
        "support_level": "recommended",
        "auto_installable": True,
        "flow_status": "full digital RTL-to-GDS target via volare/OpenLane",
        "description": "SkyWater 130nm — most mature open PDK",
        "install_method": "volare",
        "volare_repo": "efabless/sky130",
        "volare_family": "sky130",
        "volare_target": "sky130A",
        "download_url": "",
        "requires_volare": True,
        "versions": ["latest", "0fe599b2afb6708d281543108caf8310912f54af"],
        "default_version": "0fe599b2afb6708d281543108caf8310912f54af",
    },
    "gf180mcu": {
        "name": "GlobalFoundries GF180MCU",
        "pdk_dir": "gf180mcuC",
        "support_level": "recommended",
        "auto_installable": True,
        "flow_status": "full digital RTL-to-GDS target via volare/OpenLane; validate project-specific signoff",
        "description": "GlobalFoundries 180nm — automotive grade",
        "install_method": "volare",
        "volare_repo": "The-OpenROAD-Project/gf180mcu",
        "volare_family": "gf180mcu",
        "volare_target": "gf180mcuC",
        "download_url": "",
        "requires_volare": True,
        "versions": ["latest", "c6d73a35f524070e85faff4a6a9eef49553ebc2b"],
        "default_version": "c6d73a35f524070e85faff4a6a9eef49553ebc2b",
    },
    "asap7": {
        "name": "ASAP7 Predictive PDK",
        "pdk_dir": "asap7",
        "support_level": "research",
        "auto_installable": False,
        "flow_status": "research/predictive; not guaranteed as an AgentIC one-command hardening target",
        "description": "ASAP 7nm — cutting-edge predictive PDK",
        "install_method": "download",
        "volare_repo": "",
        "volare_target": "",
        "git_url": "https://github.com/The-OpenROAD-Project/asap7.git",
        "download_url": "",
        "requires_volare": False,
        "versions": ["main"],
        "default_version": "main",
        "docker_steps": [
            "docker pull ghcr.io/the-openroad-project/openroad:latest",
            "git clone https://github.com/The-OpenROAD-Project/asap7.git",
            "export PDK_ROOT=$HOME/.ciel",
            "mkdir -p $PDK_ROOT && cp -R asap7 $PDK_ROOT/asap7",
        ],
    },
    "asap5": {
        "name": "ASAP5 Predictive PDK",
        "pdk_dir": "asap5",
        "support_level": "research",
        "auto_installable": False,
        "flow_status": "predictive/research; requires user-provided OpenROAD/OpenLane-compatible collateral",
        "description": "ASAP 5nm predictive FinFET node",
        "install_method": "manual",
        "volare_repo": "",
        "volare_target": "",
        "download_url": "",
        "requires_volare": False,
        "versions": ["manual"],
        "default_version": "manual",
        "manual_steps": [
            "Install or generate ASAP5-compatible Liberty, LEF, tech, DRC, and LVS files.",
            "Place the PDK at $PDK_ROOT/asap5/ with libs.ref/asap5sc/ and libs.tech/ when possible.",
            "Run agentic install-pdk asap5 --check before hardening.",
        ],
    },
    "asap2": {
        "name": "ASAP2 Predictive PDK",
        "pdk_dir": "asap2",
        "support_level": "research",
        "auto_installable": False,
        "flow_status": "predictive/research; requires user-provided OpenROAD/OpenLane-compatible collateral",
        "description": "ASAP 2nm predictive GAAFET node",
        "install_method": "manual",
        "volare_repo": "",
        "volare_target": "",
        "download_url": "",
        "requires_volare": False,
        "versions": ["manual"],
        "default_version": "manual",
        "manual_steps": [
            "Install or generate ASAP2-compatible Liberty, LEF, tech, DRC, and LVS files.",
            "Place the PDK at $PDK_ROOT/asap2/ with libs.ref/asap2sc/ and libs.tech/ when possible.",
            "Run agentic install-pdk asap2 --check before hardening.",
        ],
    },
    "open28": {
        "name": "Open 28nm experimental PDK",
        "pdk_dir": "open28",
        "support_level": "experimental",
        "auto_installable": False,
        "flow_status": "experimental open flow; requires PDK collateral compatible with OpenROAD/OpenLane",
        "description": "Open 28nm experimental digital flow",
        "install_method": "manual",
        "volare_repo": "",
        "volare_target": "",
        "download_url": "",
        "requires_volare": False,
        "versions": ["manual"],
        "default_version": "manual",
        "manual_steps": [
            "Place Liberty, LEF, tech, DRC, and LVS collateral at $PDK_ROOT/open28/.",
            "Use libs.ref/open28_stdcells/ and libs.tech/ layout when possible.",
            "Run agentic install-pdk open28 --check before hardening.",
        ],
    },
    "nangate45": {
        "name": "NanGate 45nm",
        "pdk_dir": "nangate45",
        "support_level": "research",
        "auto_installable": False,
        "flow_status": "research cell library; may require OpenROAD-flow-scripts/platform collateral",
        "description": "NanGate 45nm — academic/research",
        "install_method": "download",
        "volare_repo": "",
        "volare_target": "",
        "download_url": "https://github.com/The-OpenROAD-Project/FreePDK45/archive/main.tar.gz",
        "requires_volare": False,
        "versions": ["main"],
        "default_version": "main",
    },
    "osu018": {
        "name": "Oklahoma State 180nm",
        "pdk_dir": "osu018",
        "support_level": "educational",
        "auto_installable": False,
        "flow_status": "educational library; not guaranteed for OpenLane signoff/hardening",
        "description": "Oklahoma State 180nm — educational/research",
        "install_method": "download",
        "volare_repo": "",
        "volare_target": "",
        "download_url": "https://github.com/The-OpenROAD-Project/osu018/archive/main.tar.gz",
        "requires_volare": False,
        "versions": ["main"],
        "default_version": "main",
    },
    "osu035": {
        "name": "Oklahoma State 350nm",
        "pdk_dir": "osu035",
        "support_level": "educational",
        "auto_installable": False,
        "flow_status": "educational library; not guaranteed for OpenLane signoff/hardening",
        "description": "Oklahoma State 350nm — high voltage, easy to probe",
        "install_method": "download",
        "volare_repo": "",
        "volare_target": "",
        "download_url": "https://github.com/The-OpenROAD-Project/osu035/archive/main.tar.gz",
        "requires_volare": False,
        "versions": ["main"],
        "default_version": "main",
    },
    "freepdk45": {
        "name": "FreePDK45",
        "pdk_dir": "FreePDK45",
        "support_level": "manual",
        "auto_installable": False,
        "flow_status": "manual install; requires external platform/collateral",
        "description": "NC State FreePDK45 + NanGate Open Cell Library",
        "install_method": "manual",
        "volare_repo": "",
        "volare_target": "",
        "download_url": "",
        "requires_volare": False,
        "versions": ["1.0.0"],
        "default_version": "1.0.0",
        "manual_steps": [
            "Install via OpenROAD-flow-scripts or your university distribution.",
            "Place the PDK at $PDK_ROOT/FreePDK45/.",
            "Run agentic install-pdk freepdk45 --check.",
        ],
    },
    "openfasoc130": {
        "name": "OpenFASOC 130nm analog flow",
        "pdk_dir": "openfasoc",
        "support_level": "manual",
        "auto_installable": False,
        "flow_status": "manual generator flow; install sky130 first",
        "description": "OpenFASOC generator flow, typically backed by SKY130",
        "install_method": "docker/manual",
        "volare_repo": "",
        "volare_target": "",
        "download_url": "",
        "requires_volare": False,
        "versions": ["latest"],
        "default_version": "latest",
        "docker_steps": [
            "git clone https://github.com/idea-fasoc/OpenFASOC.git",
            "cd OpenFASOC && make sky130hd_temp",
            "export PDK_ROOT=$HOME/.ciel",
            "Run agentic install-pdk sky130 first if SKY130 is not installed.",
        ],
        "manual_steps": [
            "Install SKY130 with agentic install-pdk sky130.",
            "Clone OpenFASOC and set OPENFASOC_ROOT to that checkout.",
            "Place any generated collateral under $PDK_ROOT/openfasoc/ if you want AgentIC to detect it as a custom PDK.",
        ],
    },
    "skywater-raw": {
        "name": "SkyWater raw development PDK",
        "pdk_dir": "skywater-pdk",
        "support_level": "manual",
        "auto_installable": False,
        "flow_status": "raw source tree; not packaged OpenLane PDK",
        "description": "Raw SkyWater PDK source tree for advanced/manual flows",
        "install_method": "manual",
        "volare_repo": "",
        "volare_target": "",
        "download_url": "",
        "requires_volare": False,
        "versions": ["latest"],
        "default_version": "latest",
        "manual_steps": [
            "Clone the SkyWater PDK source tree you are developing against.",
            "Place it at $PDK_ROOT/skywater-pdk/.",
            "Prefer agentic install-pdk sky130 for packaged OpenLane builds.",
        ],
    },
    "lefdef175": {
        "name": "LEF/DEF 175nm placeholder",
        "pdk_dir": "lefdef175",
        "support_level": "manual",
        "auto_installable": False,
        "flow_status": "bring your own LEF/Liberty/DRC/LVS collateral",
        "description": "Educational/manual 175nm LEF/DEF placeholder",
        "install_method": "manual",
        "volare_repo": "",
        "volare_target": "",
        "download_url": "",
        "requires_volare": False,
        "versions": ["manual"],
        "default_version": "manual",
        "manual_steps": [
            "Place LEF, Liberty, and DRC/LVS collateral at $PDK_ROOT/lefdef175/.",
            "Use libs.ref/<std_cell_library>/ and libs.tech/ when possible.",
            "Run agentic install-pdk lefdef175 --check.",
        ],
    },
    "tsmc28": {
        "name": "TSMC 28nm",
        "pdk_dir": "tsmc28",
        "support_level": "proprietary",
        "auto_installable": False,
        "flow_status": "foundry-controlled; cannot be auto-installed",
        "description": "Proprietary commercial PDK",
        "install_method": "proprietary",
        "requires_volare": False,
        "versions": ["foundry-controlled"],
        "default_version": "foundry-controlled",
        "proprietary": True,
        "manual_steps": [
            "Contact TSMC or your shuttle/MPW program for authorized PDK access.",
            "Place the installed PDK at $PDK_ROOT/tsmc28/.",
            "Run agentic install-pdk tsmc28 --check or agentic doctor to verify.",
        ],
    },
    "samsung14": {
        "name": "Samsung 14nm",
        "pdk_dir": "samsung14",
        "support_level": "proprietary",
        "auto_installable": False,
        "flow_status": "foundry-controlled; cannot be auto-installed",
        "description": "Proprietary commercial PDK",
        "install_method": "proprietary",
        "requires_volare": False,
        "versions": ["foundry-controlled"],
        "default_version": "foundry-controlled",
        "proprietary": True,
        "manual_steps": [
            "Request access through Samsung Foundry or your program sponsor.",
            "Place the installed PDK at $PDK_ROOT/samsung14/.",
            "Run agentic install-pdk samsung14 --check or agentic doctor to verify.",
        ],
    },
    "intel22": {
        "name": "Intel 22nm",
        "pdk_dir": "intel22",
        "support_level": "proprietary",
        "auto_installable": False,
        "flow_status": "foundry-controlled; cannot be auto-installed",
        "description": "Proprietary commercial PDK",
        "install_method": "proprietary",
        "requires_volare": False,
        "versions": ["foundry-controlled"],
        "default_version": "foundry-controlled",
        "proprietary": True,
        "manual_steps": [
            "Request authorized PDK access through Intel Foundry or your program sponsor.",
            "Place the installed PDK at $PDK_ROOT/intel22/.",
            "Run agentic install-pdk intel22 --check or agentic doctor to verify.",
        ],
    },
    "gf22": {
        "name": "GlobalFoundries 22nm",
        "pdk_dir": "gf22",
        "support_level": "proprietary",
        "auto_installable": False,
        "flow_status": "foundry-controlled; cannot be auto-installed",
        "description": "Proprietary commercial PDK",
        "install_method": "proprietary",
        "requires_volare": False,
        "versions": ["foundry-controlled"],
        "default_version": "foundry-controlled",
        "proprietary": True,
        "manual_steps": [
            "Request authorized PDK access through GlobalFoundries or your program sponsor.",
            "Place the installed PDK at $PDK_ROOT/gf22/.",
            "Run agentic install-pdk gf22 --check or agentic doctor to verify.",
        ],
    },
}


PDK_INSTALL_ALIASES = {
    "gf180": "gf180mcu",
    "gf180mcuc": "gf180mcu",
    "sky130a": "sky130",
    "sky130": "sky130",
    "asap7": "asap7",
    "7nm": "asap7",
    "asap5": "asap5",
    "5nm": "asap5",
    "asap2": "asap2",
    "2nm": "asap2",
    "nangate45": "nangate45",
    "freepdk45": "freepdk45",
    "open28": "open28",
    "open-28": "open28",
    "open28nm": "open28",
    "osu018": "osu018",
    "osu035": "osu035",
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


def _check_volare_available() -> tuple[bool, str]:
    """Check if volare is installed and return version."""
    import importlib.util
    import shutil
    import subprocess

    volare_path = shutil.which("volare")
    command = ["volare", "--version"] if volare_path else []
    if not command and importlib.util.find_spec("volare") is not None:
        command = [sys.executable, "-m", "volare", "--version"]
    if not command:
        return False, ""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
        )
        version = result.stdout.strip() or result.stderr.strip() or "unknown"
        return True, version
    except Exception:
        return True, "unknown"


def _cleanup_temp(archive_path: str, extract_dir: str) -> None:
    """Safely clean up temporary download artifacts."""
    for path in (archive_path, extract_dir):
        try:
            if os.path.isfile(path):
                os.remove(path)
            elif os.path.isdir(path):
                import shutil
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def _run_volare_install(pdk: str, version: str, target_dir: str) -> bool:
    """Install PDK via volare. Returns True on success, False on failure."""
    import importlib.util
    import shutil
    import subprocess

    volare_path = shutil.which("volare")
    volare_cmd = ["volare"] if volare_path else []
    if not volare_cmd and importlib.util.find_spec("volare") is not None:
        volare_cmd = [sys.executable, "-m", "volare"]
    if not volare_cmd:
        return False

    cfg = PDK_INSTALL_CONFIGS.get(pdk, {})
    family = cfg.get("volare_family", pdk)
    target = cfg.get("volare_target", pdk)
    requested_version = "" if version in {"", "latest"} else version

    # Volare has used both family-based and target-positional CLIs over time.
    # Try the documented family form first, then fall back for older installs.
    commands = [
        volare_cmd + ["enable", "--pdk", family, "--pdk-root", target_dir],
        volare_cmd + ["enable", "--pdk-root", target_dir, target],
    ]
    if requested_version:
        commands = [cmd + [requested_version] for cmd in commands]

    try:
        last_output = ""
        for cmd in commands:
            console.print(f"[dim]Running: {' '.join(cmd)}[/dim]")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=900,
            )
            if result.returncode == 0:
                return True
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            last_output = stderr or stdout
            if "no such option" not in last_output.lower() and "unrecognized" not in last_output.lower():
                break

        if last_output:
            console.print(f"[error]Volare error:[/error]\n{last_output[:800]}")
        return False
    except subprocess.TimeoutExpired:
        console.print("[error]Volare installation timed out (10 minutes).[/error]")
        return False
    except Exception as e:
        console.print(f"[error]Volare execution failed: {e}[/error]")
        return False


def _install_method_label(cfg: dict) -> str:
    if cfg.get("proprietary"):
        return "proprietary"
    if cfg.get("requires_volare"):
        return "volare"
    return cfg.get("install_method") or ("download" if cfg.get("download_url") else "manual")


def _pdk_choice_label(cfg: dict) -> str:
    level = str(cfg.get("support_level", "manual"))
    if cfg.get("auto_installable"):
        return f"[success]{level}[/success]"
    if level == "proprietary":
        return "[warning]proprietary[/warning]"
    if level in {"research", "educational"}:
        return f"[warning]{level}[/warning]"
    return "[dim]manual[/dim]"


def _auto_installable_pdks() -> list[str]:
    return [key for key, cfg in PDK_INSTALL_CONFIGS.items() if cfg.get("auto_installable")]


def _print_manual_pdk_steps(pdk_key: str, cfg: dict) -> None:
    steps = cfg.get("manual_steps") or [
        f"Place the PDK at $PDK_ROOT/{cfg.get('pdk_dir', pdk_key)}/.",
        f"Run agentic install-pdk {pdk_key} --check.",
    ]
    body = "\n".join(f"{idx}. {step}" for idx, step in enumerate(steps, 1))
    title = "Manual PDK Installation"
    if cfg.get("proprietary"):
        title = "Proprietary PDK"
        body = (
            f"[warning]{cfg['name']} is proprietary and cannot be auto-installed.[/warning]\n\n"
            f"{body}\n\n"
            "Open-source alternatives: sky130, gf180mcu, asap7, nangate45, freepdk45, osu018, osu035"
        )
    console.print(Panel(body, title=title, border_style="warning"))

    docker_steps = cfg.get("docker_steps") or []
    if docker_steps:
        docker_body = "\n".join(f"{idx}. {step}" for idx, step in enumerate(docker_steps, 1))
        console.print(Panel(docker_body, title="Docker/Container Install Steps", border_style="info"))


def _auto_restructure_pdk(target_path: str, cfg: dict, pdk_key: str) -> None:
    """Auto-restructure a downloaded PDK to OpenLane-compatible layout.

    OpenLane expects:  libs.ref/{cell_lib}/verilog/  and  libs.tech/magic/
    Raw GitHub repos put files elsewhere. This function creates symlinks so
    OpenLane can find everything without manual intervention.
    """
    libs_ref = os.path.join(target_path, "libs.ref")
    libs_tech = os.path.join(target_path, "libs.tech")

    # Already structured — nothing to do
    if os.path.isdir(libs_ref) and os.path.isdir(libs_tech):
        return

    os.makedirs(libs_ref, exist_ok=True)
    os.makedirs(libs_tech, exist_ok=True)

    std_cell_lib = cfg.get("std_cell_library", "")
    if not std_cell_lib:
        return

    # ── Find standard cell libraries ──
    # Walk the PDK looking for directories that contain .lib (Liberty) or
    # .v (Verilog) files — these are standard cell library directories.
    cell_lib_candidates: Dict[str, str] = {}  # name → path
    for root, dirs, files in os.walk(target_path):
        # Skip the libs.ref/lib.tech we just created
        if "libs.ref" in dirs:
            dirs.remove("libs.ref")
        if "libs.tech" in dirs:
            dirs.remove("libs.tech")

        has_cell_files = any(f.endswith((".lib", ".lef")) for f in files)
        has_verilog = any(f.endswith(".v") for f in files)
        if has_cell_files or has_verilog:
            dir_name = os.path.basename(root)
            if dir_name not in ("libs.ref", "libs.tech", "libs", "ref", "tech"):
                cell_lib_candidates[dir_name] = root

    # ── Find magic tech files ──
    magic_source = None
    for root, _dirs, files in os.walk(target_path):
        for d in ("libs.tech", "libs.ref"):
            if d in _dirs:
                _dirs.remove(d)
        if any(f.endswith((".tech", ".magicrc", ".mag")) or f == "magfile" for f in files):
            magic_source = root
            break

    # ── Create libs.ref/{cell_lib}/ layout ──
    cell_ref_dir = os.path.join(libs_ref, std_cell_lib)
    os.makedirs(cell_ref_dir, exist_ok=True)

    # Link the best cell library candidate
    linked_cell = False
    for name, path in sorted(cell_lib_candidates.items()):
        if name.lower().startswith(std_cell_lib.lower()[:6]) or name.lower() in pdk_key.lower():
            for subdir in os.listdir(path):
                src = os.path.join(path, subdir)
                dst = os.path.join(cell_ref_dir, subdir.lower())
                if os.path.isdir(src) and not os.path.exists(dst):
                    try:
                        os.symlink(os.path.relpath(src, cell_ref_dir), dst)
                        linked_cell = True
                    except OSError:
                        pass
        if linked_cell:
            break

    # If no match found, link all cell lib candidates
    if not linked_cell:
        for name, path in cell_lib_candidates.items():
            dst = os.path.join(cell_ref_dir, name)
            if not os.path.exists(dst):
                try:
                    os.symlink(os.path.relpath(path, cell_ref_dir), dst)
                except OSError:
                    pass

    # ── Create libs.tech/magic/ layout ──
    if magic_source:
        magic_dir = os.path.join(libs_tech, "magic")
        os.makedirs(magic_dir, exist_ok=True)
        for item in os.listdir(magic_source):
            src = os.path.join(magic_source, item)
            dst = os.path.join(magic_dir, item)
            if not os.path.exists(dst) and item not in ("libs.ref", "libs.tech"):
                try:
                    os.symlink(os.path.relpath(src, magic_dir), dst)
                except OSError:
                    pass

    # ── Final verification ──
    verilog_dir = os.path.join(cell_ref_dir, "verilog") if linked_cell else cell_ref_dir
    has_verilog = os.path.isdir(verilog_dir) and any(
        f.endswith(".v") for f in os.listdir(verilog_dir)
    ) if os.path.isdir(verilog_dir) else any(
        os.path.isdir(os.path.join(cell_ref_dir, d)) and
        any(f.endswith(".v") for f in os.listdir(os.path.join(cell_ref_dir, d)))
        for d in os.listdir(cell_ref_dir)
    )

    magic_done = os.path.isdir(os.path.join(libs_tech, "magic"))

    if has_verilog and magic_done:
        console.print("[success]✓ PDK auto-restructured for OpenLane[/success]")
    else:
        issues = []
        if not has_verilog:
            issues.append("no Verilog cell models found")
        if not magic_done:
            issues.append("no Magic tech files found")
        console.print(
            f"[warning]⚠ PDK partially restructured ({', '.join(issues)}).[/warning]\n"
            f"[dim]The PDK may still work for synthesis but could fail at DRC/LVS. "
            f"For full support, use volare-based PDKs: sky130, gf180mcu.[/dim]"
        )


def _ensure_pdk_root_shell_export(install_dir: str) -> None:
    """Best-effort shell setup so future AgentIC sessions see the PDK root."""
    # Set for current process immediately
    os.environ["PDK_ROOT"] = install_dir
    # Also update imported globals so detect_available_pdks() works immediately
    try:
        from . import config as _config
        _config.PDK_ROOT = install_dir
    except Exception:
        pass
    try:
        from .tools import vlsi_tools as _vlsi_tools
        _vlsi_tools.PDK_ROOT = install_dir
    except Exception:
        pass

    bashrc = os.path.expanduser("~/.bashrc")
    zshrc = os.path.expanduser("~/.zshrc")
    export_line = f"export PDK_ROOT={install_dir}"
    for rcfile in (bashrc, zshrc):
        try:
            existing = ""
            if os.path.exists(rcfile):
                with open(rcfile, "r", encoding="utf-8") as f:
                    existing = f.read()
            if export_line not in existing:
                with open(rcfile, "a", encoding="utf-8") as f:
                    f.write("\n# AgentIC PDK root\n")
                    f.write(export_line + "\n")
        except OSError:
            pass  # Non-fatal — user can set manually


def _register_custom_pdk_path(pdk_key: str, source_path: str, install_dir: str) -> None:
    source = os.path.abspath(os.path.expanduser(source_path))
    if not os.path.isdir(source):
        console.print(f"[error]Custom PDK path not found: {source}[/error]")
        raise typer.Exit(1)

    target = os.path.join(install_dir, pdk_key)
    os.makedirs(install_dir, exist_ok=True)
    if os.path.abspath(target) != source and not os.path.exists(target):
        try:
            os.symlink(source, target)
            console.print(f"[success]Linked custom PDK:[/success] {target} -> {source}")
        except OSError as exc:
            console.print(
                f"[warning]Could not create symlink: {exc}[/warning]\n"
                f"Set PDK_ROOT to the parent directory instead: [accent]export PDK_ROOT={os.path.dirname(source)}[/accent]"
            )
    else:
        console.print(f"[success]Custom PDK path is available:[/success] {source}")
    _ensure_pdk_root_shell_export(install_dir)


# ── Stable volare version for sky130 ────────────────────────────────────────
_VOLARE_SKY130_VERSION = "0fe599b2afb6708d281543108caf8310912f54af"


def _install_volare_if_missing() -> bool:
    """Install volare in the active Python environment if it is missing."""
    import subprocess

    volare_ok, volare_version = _check_volare_available()
    if volare_ok:
        console.print(f"[success]Volare already installed ({volare_version}) - skipping.[/success]")
        return False

    console.print("[warning]Volare not found. Installing via pip...[/warning]")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "volare"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(f"[error]Failed to install volare:[/error]\n{result.stderr}")
        raise typer.Exit(1)
    console.print("[success]Volare installed successfully.[/success]")
    return True


def _write_cli_shell_exports(target_dir: str, pdk_root: str, include_oss: bool = True) -> None:
    """Append AgentIC CLI environment exports to common shell rc files."""
    import shlex

    def _export_var(name: str, value: str) -> str:
        return f"export {name}={shlex.quote(str(value))}"

    def _prepend_path(path: str) -> str:
        return f"export PATH={shlex.quote(str(path))}:$PATH"

    rc_files = [os.path.expanduser("~/.bashrc")]
    zshrc = os.path.expanduser("~/.zshrc")
    if os.path.exists(zshrc):
        rc_files.append(zshrc)

    exports = []
    if include_oss:
        exports.append((_export_var("OSS_CAD_SUITE_HOME", target_dir), _export_var("OSS_CAD_SUITE_HOME", target_dir)))
        exports.append((_prepend_path(os.path.join(target_dir, "bin")), _prepend_path(os.path.join(target_dir, "bin"))))
    exports.append((_export_var("PDK_ROOT", pdk_root), _export_var("PDK_ROOT", pdk_root)))

    for rcfile in rc_files:
        existing = ""
        if os.path.exists(rcfile):
            with open(rcfile, "r", encoding="utf-8") as f:
                existing = f.read()

        added = 0
        for marker, line in exports:
            if marker not in existing:
                with open(rcfile, "a", encoding="utf-8") as f:
                    if existing and not existing.endswith("\n"):
                        f.write("\n")
                    f.write(f"# AgentIC CLI auto-config\n{line}\n")
                existing += f"\n{line}\n"
                added += 1

        if added:
            console.print(f"[success]Updated {rcfile} ({added} lines added).[/success]")
        else:
            console.print(f"[dim]{rcfile} already up to date.[/dim]")


def _refresh_runtime_tool_paths(target_dir: str, pdk_root: str) -> None:
    """Refresh already-imported AgentIC modules after installer env changes."""
    os.environ["OSS_CAD_SUITE_HOME"] = target_dir
    os.environ["PATH"] = f"{target_dir}/bin{os.pathsep}{os.environ.get('PATH', '')}"
    os.environ["PDK_ROOT"] = pdk_root

    try:
        from . import config as _config

        _config.OSS_CAD_SUITE_ROOT = target_dir
        _config.PDK_ROOT = pdk_root
        for attr, bin_name in {
            "SBY_BIN": "sby",
            "YOSYS_BIN": "yosys",
            "EQY_BIN": "eqy",
            "VERILATOR_BIN": "verilator",
            "IVERILOG_BIN": "iverilog",
            "VVP_BIN": "vvp",
            "SV2V_BIN": "sv2v",
            "OPENSTA_BIN": "sta",
            "MAGIC_BIN": "magic",
            "NETGEN_BIN": "netgen",
            "NGSPICE_BIN": "ngspice",
        }.items():
            setattr(_config, attr, _config._resolve_tool_binary(bin_name, env_var=attr))
    except Exception:
        return

    try:
        from .tools import vlsi_tools as _vlsi_tools

        for attr in (
            "PDK_ROOT",
            "SBY_BIN",
            "YOSYS_BIN",
            "EQY_BIN",
            "NGSPICE_BIN",
        ):
            if hasattr(_config, attr):
                setattr(_vlsi_tools, attr, getattr(_config, attr))
    except Exception:
        pass

    _refresh_runtime_external_tool_paths()


def _refresh_runtime_external_tool_paths() -> None:
    """Refresh modules that imported tool binary globals before setup changed env."""
    try:
        from . import config as _config

        for attr, bin_name in {
            "OPENSTA_BIN": "sta",
            "MAGIC_BIN": "magic",
            "NETGEN_BIN": "netgen",
            "NGSPICE_BIN": "ngspice",
        }.items():
            setattr(_config, attr, _config._resolve_tool_binary(bin_name, env_var=attr))

        try:
            from .tools import physical_tools as _physical_tools

            _physical_tools.MAGIC_BIN = _config.MAGIC_BIN
            _physical_tools.NETGEN_BIN = _config.NETGEN_BIN
        except Exception:
            pass
        try:
            from .tools import spice_tools as _spice_tools

            _spice_tools.NGSPICE_BIN = _config.NGSPICE_BIN
        except Exception:
            pass
        try:
            from .tools import sta_tools as _sta_tools

            _sta_tools.OPENSTA_BIN = os.environ.get("OPENSTA_BIN", _config.OPENSTA_BIN)
        except Exception:
            pass
        try:
            from .tools import vlsi_tools as _vlsi_tools

            _vlsi_tools.MAGIC_BIN = _config.MAGIC_BIN
            _vlsi_tools.NETGEN_BIN = _config.NETGEN_BIN
            _vlsi_tools.NGSPICE_BIN = _config.NGSPICE_BIN
            _vlsi_tools.OPENSTA_BIN = _config.OPENSTA_BIN
        except Exception:
            pass
    except Exception:
        pass


def _install_openlane_docker_image(image: str = OPENLANE_IMAGE, force: bool = False) -> bool:
    """Ensure the legacy OpenLane v1 Docker image used for hardening is available."""
    import shutil
    import subprocess

    docker = shutil.which("docker")
    if not docker:
        console.print(
            Panel(
                "Docker is required for AgentIC's default RTL-to-GDSII hardening backend.\n"
                "Install Docker, start the daemon, then run:\n\n"
                f"  agentic install-openlane --image {image}",
                title="Docker Not Found",
                border_style="error",
            )
        )
        raise typer.Exit(1)

    info = subprocess.run([docker, "info"], capture_output=True, text=True, timeout=30)
    if info.returncode != 0:
        console.print(
            Panel(
                (info.stderr or info.stdout or "Docker daemon is not reachable.").strip(),
                title="Docker Not Running",
                border_style="error",
            )
        )
        raise typer.Exit(1)

    if not force:
        inspect = subprocess.run(
            [docker, "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if inspect.returncode == 0:
            console.print(f"[success]OpenLane Docker image already present:[/success] {image}")
            return False

    console.print(f"[accent]Pulling OpenLane Docker image:[/accent] {image}")
    pull = subprocess.run([docker, "pull", image], text=True)
    if pull.returncode != 0:
        console.print(f"[error]Failed to pull OpenLane image:[/error] {image}")
        raise typer.Exit(1)

    console.print(f"[success]OpenLane Docker image installed:[/success] {image}")
    return True


def _install_openlane2_backend(image: str = OPENLANE2_IMAGE, force: bool = False, smoke_test: bool = False) -> bool:
    """Ensure OpenLane 2's Python package and Docker image are available."""
    import importlib.util
    import subprocess

    changed = False
    if importlib.util.find_spec("openlane") is None or force:
        console.print("[accent]Installing OpenLane 2 Python package...[/accent]")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "openlane"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            console.print(f"[error]Failed to install OpenLane 2 package:[/error]\n{result.stderr}")
            raise typer.Exit(1)
        changed = True

    _install_openlane_docker_image(image=image, force=force)

    version = "unknown"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "openlane", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        version = (result.stdout or result.stderr or "unknown").strip().splitlines()[0]
    except Exception:
        pass

    console.print(f"[success]OpenLane 2 backend ready:[/success] {version}")
    console.print("[info]AgentIC default backend:[/info] [accent]openlane2[/accent]")

    if smoke_test:
        console.print("[accent]Running OpenLane 2 Dockerized smoke test...[/accent]")
        smoke = subprocess.run(
            [sys.executable, "-m", "openlane", "--docker-no-tty", "--dockerized", "--smoke-test"],
            text=True,
            timeout=1800,
        )
        if smoke.returncode != 0:
            console.print("[error]OpenLane 2 smoke test failed.[/error]")
            raise typer.Exit(1)
        console.print("[success]OpenLane 2 smoke test passed.[/success]")
        changed = True

    return changed


def _install_orfs(root: str = ORFS_ROOT, setup_asap7: bool = False, force: bool = False) -> bool:
    """Install OpenROAD-flow-scripts for research-node flows such as ASAP7."""
    import subprocess

    root = os.path.abspath(os.path.expanduser(root))
    repo_url = "https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts.git"
    changed = False

    if force and os.path.isdir(root):
        console.print(
            f"[warning]ORFS already exists at {root}; refusing destructive reinstall. "
            "Move it aside manually or use a different --root.[/warning]"
        )
        raise typer.Exit(1)

    if not os.path.isdir(os.path.join(root, ".git")):
        os.makedirs(os.path.dirname(root), exist_ok=True)
        console.print(f"[accent]Cloning OpenROAD-flow-scripts:[/accent] {root}")
        result = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, root],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if result.returncode != 0:
            console.print(f"[error]ORFS clone failed:[/error]\n{result.stderr or result.stdout}")
            raise typer.Exit(1)
        changed = True
    else:
        console.print(f"[success]OpenROAD-flow-scripts already present:[/success] {root}")

    if setup_asap7:
        asap7_cfg = os.path.join(root, "flow", "platforms", "asap7", "config.mk")
        if os.path.exists(asap7_cfg):
            console.print("[success]ORFS ASAP7 platform collateral already present.[/success]")
        else:
            console.print("[accent]Setting up ORFS ASAP7 platform collateral...[/accent]")
            result = subprocess.run(
                ["make", "setup-asap7"],
                cwd=root,
                text=True,
                timeout=3600,
            )
            if result.returncode != 0:
                console.print("[error]ORFS make setup-asap7 failed and ASAP7 platform files were not found.[/error]")
                raise typer.Exit(1)
            changed = True

    console.print(f"[success]ORFS ready:[/success] {root}")
    console.print("[info]7nm research run example:[/info] [accent]agentic run-orfs --platform asap7 --design gcd[/accent]")
    return changed


def _orfs_design_config(orfs_root: str, platform: str, design: str) -> str:
    return os.path.join(orfs_root, "flow", "designs", platform, design, "config.mk")


def _prepare_orfs_agentic_design(orfs_root: str, platform: str, design: str, clock_period: float) -> str:
    """Create a minimal ORFS design wrapper from an AgentIC design directory."""
    src_root = os.path.join(OPENLANE_ROOT, "designs", design, "src")
    if not os.path.isdir(src_root):
        raise typer.BadParameter(f"AgentIC design source directory not found: {src_root}")

    rtl_files = []
    for ext in ("*.v", "*.sv"):
        rtl_files.extend(sorted(Path(src_root).glob(ext)))
    rtl_files = [
        path
        for path in rtl_files
        if not path.name.lower().endswith(("_tb.v", "_testbench.v", "_coverage.sv", "_sva.sv", "_formal.sv"))
    ]
    if not rtl_files:
        raise typer.BadParameter(f"No RTL files found in {src_root}")

    design_cfg_dir = Path(orfs_root) / "flow" / "designs" / platform / design
    design_src_dir = Path(orfs_root) / "flow" / "designs" / "src" / design
    design_cfg_dir.mkdir(parents=True, exist_ok=True)
    design_src_dir.mkdir(parents=True, exist_ok=True)

    for rtl in rtl_files:
        target = design_src_dir / rtl.name
        if not target.exists() or target.read_bytes() != rtl.read_bytes():
            target.write_bytes(rtl.read_bytes())

    sdc_path = design_cfg_dir / "constraint.sdc"
    sdc_path.write_text(
        f"create_clock -name clk -period {clock_period:g} [get_ports clk]\n",
        encoding="utf-8",
    )

    rel_rtl = " ".join(f"$(DESIGN_HOME)/src/{design}/{path.name}" for path in rtl_files)
    config_path = design_cfg_dir / "config.mk"
    config_path.write_text(
        "\n".join(
            [
                f"export DESIGN_NICKNAME = {design}",
                f"export DESIGN_NAME = {design}",
                f"export PLATFORM = {platform}",
                f"export VERILOG_FILES = {rel_rtl}",
                f"export SDC_FILE = $(DESIGN_HOME)/{platform}/{design}/constraint.sdc",
                f"export CLOCK_PERIOD = {clock_period:g}",
                "export CORE_UTILIZATION ?= 30",
                "export PLACE_DENSITY ?= 0.55",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return str(config_path)


def _agentic_tools_bin() -> str:
    path = os.path.abspath(os.path.expanduser("~/.agentic/tools/bin"))
    os.makedirs(path, exist_ok=True)
    return path


def _resolve_installed_tool(name: str, candidates: Optional[List[str]] = None) -> str:
    for candidate in candidates or []:
        expanded = os.path.abspath(os.path.expanduser(candidate))
        if os.path.exists(expanded):
            return expanded
    found = shutil.which(name)
    return os.path.abspath(found) if found else ""


def _link_agentic_tool(name: str, source: str) -> str:
    """Place a stable symlink where AgentIC setup exports expect it."""
    if not source:
        return ""
    source = os.path.abspath(os.path.expanduser(source))
    if not os.path.exists(source):
        return ""
    link_path = os.path.join(_agentic_tools_bin(), name)
    if os.path.abspath(link_path) == source:
        return link_path
    if os.path.lexists(link_path):
        try:
            if os.path.realpath(link_path) == source:
                return link_path
            os.remove(link_path)
        except OSError:
            return source
    try:
        os.symlink(source, link_path)
        return link_path
    except OSError:
        return source


def _install_experimental_flow_tools(
    fault_image: str = "ghcr.io/aucohl/fault:latest",
    skip_apt: bool = False,
    no_shell: bool = False,
) -> None:
    """Install/check open tools used by the experimental-complete flow.

    These tools improve GLS, post-layout SPICE, layout inspection, and open DFT
    experiments. They do not turn the OSS flow into foundry-qualified signoff.
    """
    import platform
    import shutil
    import subprocess

    console.print(
        Panel(
            "ngspice, KLayout, GTKWave, Xschem, optional Python helpers, and Fault Docker image\n"
            "Commercial tools still require licensed user-provided installs.",
            title="Installing Experimental Complete Flow Tools",
        )
    )

    if platform.system().lower() == "linux" and not skip_apt and shutil.which("apt-get"):
        packages = [
            "ngspice",
            "gtkwave",
            "klayout",
            "xschem",
            "python3-pip",
            "python3-venv",
        ]
        console.print("[accent]Installing open experimental/signoff helper packages...[/accent]")
        update = subprocess.run(["sudo", "apt-get", "update"])
        if update.returncode != 0:
            console.print("[error]apt-get update failed while installing experimental tools.[/error]")
            raise typer.Exit(update.returncode)
        install = subprocess.run(["sudo", "apt-get", "install", "-y", *packages])
        if install.returncode != 0:
            console.print("[error]apt-get install failed while installing experimental tools.[/error]")
            raise typer.Exit(install.returncode)
    elif skip_apt:
        console.print("[dim]Skipped apt packages for experimental flow tools.[/dim]")
    else:
        console.print("[warning]apt-get not available; skipped OS package install.[/warning]")

    py_packages = ["vcdvcd", "cocotb"]
    console.print("[accent]Installing optional Python verification helpers...[/accent]")
    pip = subprocess.run(
        [sys.executable, "-m", "pip", "install", *py_packages],
        capture_output=True,
        text=True,
    )
    if pip.returncode != 0:
        console.print(
            "[warning]Python helper install failed; continuing because these are optional.[/warning]\n"
            f"{(pip.stderr or pip.stdout)[-1000:]}"
        )

    docker = shutil.which("docker")
    if docker:
        info = subprocess.run([docker, "info"], capture_output=True, text=True, timeout=30)
        if info.returncode == 0:
            inspect = subprocess.run(
                [docker, "image", "inspect", fault_image],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if inspect.returncode == 0:
                console.print(f"[success]Fault Docker image already present:[/success] {fault_image}")
            else:
                console.print(f"[accent]Pulling Fault Docker image:[/accent] {fault_image}")
                pull = subprocess.run([docker, "pull", fault_image], text=True)
                if pull.returncode != 0:
                    console.print("[warning]Could not pull Fault Docker image; open ATPG remains optional.[/warning]")
        else:
            console.print("[warning]Docker is installed but not running; skipped Fault image pull.[/warning]")
    else:
        console.print("[warning]Docker not found; skipped Fault image pull.[/warning]")

    tools_bin = _agentic_tools_bin()
    linked = {}
    for tool in ("ngspice", "gtkwave", "klayout", "xschem"):
        linked_path = _link_agentic_tool(tool, _resolve_installed_tool(tool))
        if linked_path:
            linked[tool] = linked_path

    os.environ["PATH"] = f"{tools_bin}{os.pathsep}{os.environ.get('PATH', '')}"
    if linked.get("ngspice"):
        os.environ["NGSPICE_BIN"] = linked["ngspice"]
    os.environ["AGENTIC_FAULT_DOCKER_IMAGE"] = fault_image
    _refresh_runtime_external_tool_paths()

    if not no_shell:
        _write_experimental_shell_exports(fault_image=fault_image, linked_tools=linked)


def _write_experimental_shell_exports(fault_image: str, linked_tools: Optional[Dict[str, str]] = None) -> None:
    rc_files = [os.path.expanduser("~/.bashrc")]
    zshrc = os.path.expanduser("~/.zshrc")
    if os.path.exists(zshrc):
        rc_files.append(zshrc)

    tools_bin = _agentic_tools_bin()
    linked_tools = linked_tools or {}
    ngspice_bin = linked_tools.get("ngspice") or _link_agentic_tool("ngspice", _resolve_installed_tool("ngspice"))
    exports = [
        (f"export PATH=\"{tools_bin}:$PATH\"", f"export PATH=\"{tools_bin}:$PATH\""),
        (f"export AGENTIC_FAULT_DOCKER_IMAGE={fault_image}", f"export AGENTIC_FAULT_DOCKER_IMAGE={fault_image}"),
        (f"export NGSPICE_BIN={ngspice_bin}", f"export NGSPICE_BIN={ngspice_bin}") if ngspice_bin else ("", ""),
    ]

    for rcfile in rc_files:
        existing = ""
        if os.path.exists(rcfile):
            with open(rcfile, "r", encoding="utf-8") as f:
                existing = f.read()
        added = 0
        for marker, line in exports:
            if not marker:
                continue
            if marker not in existing:
                with open(rcfile, "a", encoding="utf-8") as f:
                    if existing and not existing.endswith("\n"):
                        f.write("\n")
                    f.write(f"# AgentIC experimental complete flow\n{line}\n")
                existing += f"\n{line}\n"
                added += 1
        if added:
            console.print(f"[success]Updated {rcfile} ({added} experimental-flow lines added).[/success]")


def _write_commercial_tool_template(path: str) -> None:
    """Write a non-secret template for user-provided commercial tool adapters."""
    template = """# AgentIC commercial/foundry flow registration template.
# Source this only after replacing placeholders with licensed tool paths.
# AgentIC cannot install foundry PDKs or commercial EDA tools without your licenses.

# export AGENTIC_COMMERCIAL_SIGNOFF=1
# export AGENTIC_FLOW_PROFILE=commercial_signoff

# PDK / node collateral
# export PDK_ROOT=/path/to/licensed/pdks
# export PDK=tsmc28
# export AGENTIC_STDCELL_LIB=/path/to/stdcell.lib
# export AGENTIC_TECH_LEF=/path/to/tech.lef
# export AGENTIC_CELL_LEF=/path/to/cells.lef
# export AGENTIC_QRC_TECH=/path/to/qrc/techfile

# Commercial physical verification / signoff
# export CALIBRE_BIN=/path/to/calibre
# export PEGASUS_BIN=/path/to/pegasus
# export ICVALIDATOR_BIN=/path/to/icv
# export TEMPUS_BIN=/path/to/tempus
# export PRIME_TIME_BIN=/path/to/pt_shell
# export STAR_RC_BIN=/path/to/StarXtract
# export QUANTUS_BIN=/path/to/quantus
# export REDHAWK_BIN=/path/to/redhawk
# export VOLTUS_BIN=/path/to/voltus

# Commercial implementation
# export INNOVUS_BIN=/path/to/innovus
# export FUSION_COMPILER_BIN=/path/to/fc_shell

# Commercial DFT / ATPG / MBIST
# export TESSENT_BIN=/path/to/tessent
# export MODUS_BIN=/path/to/modus
# export TMAX_BIN=/path/to/tmax
# export MBIST_COMPILER_BIN=/path/to/mbist/compiler
"""
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        console.print(f"[dim]Commercial tool template already exists:[/dim] {path}")
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(template)
    console.print(f"[success]Wrote commercial tool registration template:[/success] {path}")


def _write_signoff_shell_exports(prefix: str, pdk_root: str) -> None:
    """Persist physical signoff tool paths for direct DRC/LVS/STA commands."""
    prefix = os.path.abspath(os.path.expanduser(prefix))
    pdk_root = os.path.abspath(os.path.expanduser(pdk_root))
    tools_bin = _agentic_tools_bin()
    magic_bin = _link_agentic_tool(
        "magic",
        _resolve_installed_tool("magic", [os.path.join(prefix, "bin", "magic")]),
    )
    netgen_bin = _link_agentic_tool(
        "netgen",
        _resolve_installed_tool("netgen", ["/usr/local/bin/netgen", "/usr/lib/netgen/bin/netgen", "/usr/bin/netgen"]),
    )
    sta_bin = _link_agentic_tool(
        "sta",
        _resolve_installed_tool("sta", ["/usr/local/bin/sta", os.path.join(prefix, "bin", "sta"), "/usr/bin/sta"]),
    )

    exports = [
        (f"export PATH=\"{tools_bin}:{prefix}/bin:$PATH\"", f"export PATH=\"{tools_bin}:{prefix}/bin:$PATH\""),
        (f"export MAGIC_BIN={magic_bin}", f"export MAGIC_BIN={magic_bin}") if magic_bin else ("", ""),
        (f"export NETGEN_BIN={netgen_bin}", f"export NETGEN_BIN={netgen_bin}") if netgen_bin else ("", ""),
        (f"export OPENSTA_BIN={sta_bin}", f"export OPENSTA_BIN={sta_bin}") if sta_bin else ("", ""),
        (f"export PDK_ROOT={pdk_root}", f"export PDK_ROOT={pdk_root}"),
    ]

    rc_files = [os.path.expanduser("~/.bashrc")]
    zshrc = os.path.expanduser("~/.zshrc")
    if os.path.exists(zshrc):
        rc_files.append(zshrc)

    for rcfile in rc_files:
        existing = ""
        if os.path.exists(rcfile):
            with open(rcfile, "r", encoding="utf-8") as f:
                existing = f.read()
        added = 0
        for marker, line in exports:
            if not marker:
                continue
            if marker not in existing:
                with open(rcfile, "a", encoding="utf-8") as f:
                    if existing and not existing.endswith("\n"):
                        f.write("\n")
                    f.write(f"# AgentIC physical signoff tools\n{line}\n")
                existing += f"\n{line}\n"
                added += 1
        if added:
            console.print(f"[success]Updated {rcfile} ({added} signoff lines added).[/success]")
        else:
            console.print(f"[dim]{rcfile} already has AgentIC signoff exports.[/dim]")


def _magic_version_ok(binary: str, minimum: tuple[int, int, int] = (8, 3, 411)) -> bool:
    import re
    import subprocess

    if not binary or not os.path.exists(os.path.expanduser(binary)):
        return False
    try:
        proc = subprocess.run(
            [os.path.expanduser(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except OSError:
        return False
    text = f"{proc.stdout}\n{proc.stderr}"
    match = re.search(r"(\d+)\.(\d+)(?:\s+revision|\.)\s*(\d+)", text)
    if not match:
        return False
    version = tuple(int(part) for part in match.groups())
    return version >= minimum


@app.command("install-signoff-tools")
def install_signoff_tools(
    prefix: str = typer.Option(
        os.path.expanduser("~/eda"),
        "--prefix",
        "-p",
        help="Install newer Magic under this prefix",
    ),
    source_dir: str = typer.Option(
        os.path.expanduser("~/eda-src/magic"),
        "--source-dir",
        help="Magic source checkout directory",
    ),
    pdk_root: str = typer.Option(
        os.path.expanduser("~/.ciel"),
        "--pdk-root",
        help="PDK root to persist for signoff commands",
    ),
    skip_apt: bool = typer.Option(
        False,
        "--skip-apt",
        help="Skip apt package install for dependencies, netgen, and OpenSTA",
    ),
    skip_magic_build: bool = typer.Option(
        False,
        "--skip-magic-build",
        help="Skip building newer Magic from source",
    ),
    force_magic: bool = typer.Option(
        False,
        "--force-magic",
        help="Rebuild Magic even if the installed prefix already has a new enough version",
    ),
    no_shell: bool = typer.Option(
        False,
        "--no-shell",
        help="Do not update ~/.bashrc or ~/.zshrc",
    ),
):
    """Install physical signoff tools used by agentic drc/lvs/sta.

    Installs/checks:
    - Magic 8.3.411+ from source for SKY130/GF180 Magic tech files
    - Netgen for LVS
    - OpenSTA command `sta` for timing checks
    """
    import platform
    import shutil
    import subprocess

    if platform.system().lower() != "linux":
        console.print(
            Panel(
                "Automatic signoff tool installation is currently supported on Linux/WSL.\n"
                "On Windows, run this command inside WSL.",
                title="Unsupported Platform",
                border_style="error",
            )
        )
        raise typer.Exit(1)

    prefix_abs = os.path.abspath(os.path.expanduser(prefix))
    source_abs = os.path.abspath(os.path.expanduser(source_dir))
    pdk_root_abs = os.path.abspath(os.path.expanduser(pdk_root))
    magic_bin = os.path.join(prefix_abs, "bin", "magic")

    console.print(
        Panel(
            f"Magic prefix: {prefix_abs}\n"
            f"Magic source: {source_abs}\n"
            f"PDK root: {pdk_root_abs}",
            title="Installing Physical Signoff Tools",
        )
    )

    if not skip_apt:
        if not shutil.which("apt-get"):
            console.print("[warning]apt-get not found; skipping apt package installation.[/warning]")
        else:
            packages = [
                "git",
                "build-essential",
                "autoconf",
                "automake",
                "libtool",
                "m4",
                "csh",
                "tcsh",
                "tcl-dev",
                "tk-dev",
                "libcairo2-dev",
                "libx11-dev",
                "libxrender-dev",
                "libxpm-dev",
                "libreadline-dev",
                "netgen-lvs",
                "cmake",
                "clang",
                "swig",
                "bison",
                "flex",
                "zlib1g-dev",
                "libeigen3-dev",
                "libgtest-dev",
            ]
            console.print("[accent]Installing apt dependencies, Netgen, and OpenSTA...[/accent]")
            update = subprocess.run(["sudo", "apt-get", "update"])
            if update.returncode != 0:
                console.print("[error]apt-get update failed.[/error]")
                raise typer.Exit(update.returncode)
            install = subprocess.run(["sudo", "apt-get", "install", "-y", *packages])
            if install.returncode != 0:
                console.print("[error]apt-get install failed.[/error]")
                raise typer.Exit(install.returncode)
            
            # Symlink netgen to a standard PATH location so doctor finds it
            subprocess.run(["sudo", "ln", "-sf", "/usr/lib/netgen/bin/netgen", "/usr/local/bin/netgen"])
    else:
        console.print("[dim]Skipped apt packages (--skip-apt).[/dim]")

    if not skip_magic_build:
        if _magic_version_ok(magic_bin) and not force_magic:
            console.print(f"[success]Magic is already new enough:[/success] {magic_bin}")
        else:
            os.makedirs(os.path.dirname(source_abs), exist_ok=True)
            if os.path.exists(source_abs):
                console.print(f"[accent]Using existing Magic source checkout:[/accent] {source_abs}")
            else:
                console.print("[accent]Cloning Magic source...[/accent]")
                clone = subprocess.run(
                    ["git", "clone", "https://github.com/RTimothyEdwards/magic.git", source_abs]
                )
                if clone.returncode != 0:
                    console.print("[error]Magic source clone failed.[/error]")
                    raise typer.Exit(clone.returncode)

            os.makedirs(prefix_abs, exist_ok=True)
            console.print("[accent]Configuring Magic...[/accent]")
            configure = subprocess.run(["./configure", f"--prefix={prefix_abs}"], cwd=source_abs)
            if configure.returncode != 0:
                console.print("[error]Magic configure failed.[/error]")
                raise typer.Exit(configure.returncode)
            console.print("[accent]Building Magic...[/accent]")
            make = subprocess.run(["make"], cwd=source_abs)
            if make.returncode != 0:
                console.print("[error]Magic build failed.[/error]")
                raise typer.Exit(make.returncode)
            console.print("[accent]Installing Magic...[/accent]")
            install = subprocess.run(["make", "install"], cwd=source_abs)
            if install.returncode != 0:
                console.print("[error]Magic install failed.[/error]")
                raise typer.Exit(install.returncode)
                
        # ── OpenSTA Source Build ──
        sta_bin = shutil.which("sta")
        if sta_bin and not force_magic:
            console.print(f"[success]OpenSTA is already installed:[/success] {sta_bin}")
        else:
            # First compile CUDD
            cudd_source = os.path.abspath(os.path.expanduser("~/eda-src/cudd"))
            os.makedirs(os.path.dirname(cudd_source), exist_ok=True)
            if os.path.exists(cudd_source):
                console.print(f"[accent]Using existing CUDD source checkout:[/accent] {cudd_source}")
            else:
                console.print("[accent]Cloning CUDD source...[/accent]")
                subprocess.run(["git", "clone", "https://github.com/ivmai/cudd.git", cudd_source], check=True)
                
            console.print("[accent]Running autoreconf for CUDD...[/accent]")
            subprocess.run(["autoreconf", "-fi"], cwd=cudd_source, check=True)
            
            console.print("[accent]Configuring CUDD...[/accent]")
            subprocess.run(["./configure", "--enable-shared", "--enable-obj"], cwd=cudd_source, check=True)
            
            console.print("[accent]Building CUDD...[/accent]")
            subprocess.run(["make", "-j" + str(os.cpu_count() or 1)], cwd=cudd_source, check=True)
            
            console.print("[accent]Installing CUDD...[/accent]")
            subprocess.run(["sudo", "make", "install"], cwd=cudd_source, check=True)
            
            sta_source = os.path.abspath(os.path.expanduser("~/eda-src/OpenSTA"))
            if os.path.exists(sta_source):
                console.print(f"[accent]Using existing OpenSTA source checkout:[/accent] {sta_source}")
            else:
                console.print("[accent]Cloning OpenSTA source...[/accent]")
                clone = subprocess.run(
                    ["git", "clone", "--recursive", "--depth", "1", "https://github.com/The-OpenROAD-Project/OpenSTA.git", sta_source]
                )
                if clone.returncode != 0:
                    console.print("[error]OpenSTA clone failed.[/error]")
                    raise typer.Exit(clone.returncode)

            console.print("[accent]Building OpenSTA...[/accent]")
            build_dir = os.path.join(sta_source, "build")
            os.makedirs(build_dir, exist_ok=True)
            configure = subprocess.run(["cmake", "..", "-DCMAKE_BUILD_TYPE=Release", "-DCUDD_DIR=/usr/local"], cwd=build_dir)
            if configure.returncode != 0:
                console.print("[error]OpenSTA configure failed.[/error]")
                raise typer.Exit(configure.returncode)
            
            make = subprocess.run(["make", "-j" + str(os.cpu_count() or 1)], cwd=build_dir)
            if make.returncode != 0:
                console.print("[error]OpenSTA build failed.[/error]")
                raise typer.Exit(make.returncode)
                
            console.print("[accent]Installing OpenSTA...[/accent]")
            install = subprocess.run(["sudo", "make", "install"], cwd=build_dir)
            if install.returncode != 0:
                console.print("[error]OpenSTA install failed.[/error]")
                raise typer.Exit(install.returncode)
                
            subprocess.run(["sudo", "ldconfig"], check=True)
    else:
        console.print("[dim]Skipped Magic source build (--skip-magic-build).[/dim]")

    tools_bin = _agentic_tools_bin()
    magic_link = _link_agentic_tool("magic", _resolve_installed_tool("magic", [magic_bin]))
    netgen_link = _link_agentic_tool(
        "netgen",
        _resolve_installed_tool("netgen", ["/usr/local/bin/netgen", "/usr/lib/netgen/bin/netgen", "/usr/bin/netgen"]),
    )
    sta_link = _link_agentic_tool(
        "sta",
        _resolve_installed_tool("sta", ["/usr/local/bin/sta", os.path.join(prefix_abs, "bin", "sta"), "/usr/bin/sta"]),
    )

    os.environ["PATH"] = f"{tools_bin}{os.pathsep}{prefix_abs}/bin{os.pathsep}{os.environ.get('PATH', '')}"
    if magic_link:
        os.environ["MAGIC_BIN"] = magic_link
    if netgen_link:
        os.environ["NETGEN_BIN"] = netgen_link
    if sta_link:
        os.environ["OPENSTA_BIN"] = sta_link
    os.environ["PDK_ROOT"] = pdk_root_abs
    _refresh_runtime_external_tool_paths()

    if not no_shell:
        _write_signoff_shell_exports(prefix_abs, pdk_root_abs)

    console.print(
        Panel(
            "Signoff tool setup finished.\n\n"
            "Run:\n"
            "  source ~/.bashrc\n"
            "  agentic doctor\n"
            "  magic --version\n"
            "  which netgen\n"
            "  which sta",
            title="Physical Signoff Tools Ready",
            border_style="success",
        )
    )


@app.command("install-oss")
def install_oss(
    target_dir: str = typer.Option(
        os.path.expanduser("~/oss-cad-suite"),
        "--target",
        "-t",
        help="Directory to install OSS CAD Suite into",
    ),
    pdk_root: str = typer.Option(
        os.path.expanduser("~/.ciel"),
        "--pdk-root",
        help="PDK root to write into shell configuration",
    ),
    no_shell: bool = typer.Option(
        False,
        "--no-shell",
        help="Do not update ~/.bashrc or ~/.zshrc",
    ),
):
    """Install OSS CAD Suite with one command for RTL/sim/synth tooling."""
    from .install_tools import install_oss_cad_suite

    target = os.path.abspath(os.path.expanduser(target_dir))
    pdk_root_abs = os.path.abspath(os.path.expanduser(pdk_root))
    if _is_oss_cad_suite_present_at(target):
        console.print(f"[success]OSS CAD Suite already present at target:[/success] {target}")
    else:
        if _is_toolchain_present():
            console.print(
                "[warning]EDA tools exist elsewhere on PATH, but the requested "
                f"target is incomplete. Installing/repairing {target}.[/warning]"
            )
        os.makedirs(target, exist_ok=True)
        if not install_oss_cad_suite(target):
            raise typer.Exit(1)

    _refresh_runtime_tool_paths(target, pdk_root_abs)
    if not no_shell:
        _write_cli_shell_exports(target, pdk_root_abs, include_oss=True)
    console.print("[success]OSS CAD Suite setup complete.[/success]")


@app.command("install-openlane")
def install_openlane(
    backend: str = typer.Option(
        OPENLANE_BACKEND_DEFAULT,
        "--backend",
        help="Backend to install: openlane2 (default) or openlane1",
    ),
    image: str = typer.Option(
        "",
        "--image",
        help="Docker image used for AgentIC OpenLane hardening",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Pull even if the image exists"),
    smoke_test: bool = typer.Option(False, "--smoke-test", help="Run OpenLane 2 smoke test after install"),
):
    """Install/pull the OpenLane backend used for RTL-to-GDSII hardening."""
    selected = backend.strip().lower()
    if selected in {"openlane2", "v2"}:
        _install_openlane2_backend(image=image or OPENLANE2_IMAGE, force=force, smoke_test=smoke_test)
    elif selected in {"openlane1", "v1", "docker"}:
        _install_openlane_docker_image(image=image or OPENLANE_IMAGE, force=force)
    else:
        raise typer.BadParameter("--backend must be one of: openlane2, openlane1")


@app.command("install-orfs")
def install_orfs(
    root: str = typer.Option(
        ORFS_ROOT,
        "--root",
        help="OpenROAD-flow-scripts install directory",
    ),
    setup_asap7: bool = typer.Option(
        False,
        "--setup-asap7",
        help="Run make setup-asap7 after cloning ORFS",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Fail if root exists instead of reusing it"),
):
    """Install OpenROAD-flow-scripts for research-node flows such as ASAP7."""
    _install_orfs(root=root, setup_asap7=setup_asap7, force=force)


@app.command("setup-7nm")
def setup_7nm(
    root: str = typer.Option(
        ORFS_ROOT,
        "--root",
        help="OpenROAD-flow-scripts install directory",
    ),
    skip_asap7_setup: bool = typer.Option(
        False,
        "--skip-asap7-setup",
        help="Clone ORFS only; do not run make setup-asap7",
    ),
):
    """One-command setup for ASAP7/7nm research flows through ORFS."""
    _install_orfs(root=root, setup_asap7=not skip_asap7_setup, force=False)


@app.command("run-orfs")
def run_orfs(
    platform: str = typer.Option("asap7", "--platform", help="ORFS platform, e.g. asap7 or sky130hd"),
    design: str = typer.Option("gcd", "--design", help="ORFS design name or AgentIC design name"),
    root: str = typer.Option(ORFS_ROOT, "--root", help="OpenROAD-flow-scripts install directory"),
    clock_period: float = typer.Option(10.0, "--clock-period", help="Clock period for generated AgentIC ORFS designs"),
    recovery_attempts: int = typer.Option(0, "--recovery-attempts", "-r", min=0, max=10, help="Max auto-recovery attempts on failure"),
    setup_missing: bool = typer.Option(False, "--setup-missing", help="Install ORFS/ASAP7 if missing before running"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the ORFS command without running make"),
):
    """Run a research-node ORFS flow, defaulting to ASAP7/gcd."""
    if platform.lower() in ("sky130", "sky130a"):
        platform = "sky130hd"
        
    root = os.path.abspath(os.path.expanduser(root))
    if setup_missing and not os.path.isdir(os.path.join(root, ".git")):
        _install_orfs(root=root, setup_asap7=(platform == "asap7"), force=False)
    if not os.path.isdir(os.path.join(root, "flow")):
        console.print(f"[error]ORFS not found at {root}. Run: agentic setup-7nm[/error]")
        raise typer.Exit(1)

    config_path = _orfs_design_config(root, platform, design)
    if not os.path.exists(config_path):
        try:
            config_path = _prepare_orfs_agentic_design(root, platform, design, clock_period)
            console.print(f"[success]Generated ORFS design config:[/success] {config_path}")
        except typer.BadParameter as exc:
            console.print(
                f"[error]ORFS design config not found:[/error] {config_path}\n"
                f"[dim]{exc}[/dim]\n"
                "For built-in ORFS examples, use an existing design such as --design gcd after setup."
            )
            raise typer.Exit(1)

    rel_config = os.path.relpath(config_path, os.path.join(root, "flow"))
    cmd = [
        "docker", "run", "--rm",
        "-u", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{os.path.join(root, 'flow')}:/OpenROAD-flow-scripts/flow:Z",
        "-w", "/OpenROAD-flow-scripts/flow",
        "openroad/orfs:latest",
        "make", f"DESIGN_CONFIG=./{rel_config}"
    ]

    if dry_run:
        console.print(f"[accent]ORFS command:[/accent] {' '.join(cmd)}")
        return

    import subprocess
    from .core.pipeline_recovery import OpenLaneErrorFixer, RecoveryAction
    import re
    
    ol_fixer = OpenLaneErrorFixer()
    orfs_log_path = os.path.join(os.path.dirname(config_path), "harden_orfs.log")

    MAX_RTL_FIXES = 3
    rtl_fixes = 0

    while True:
        physical_success = False
        fix_rtl_requested = False
        
        for attempt in range(recovery_attempts + 1):
            if attempt > 0:
                console.print(f"\n[warning]── ORFS Recovery attempt {attempt}/{recovery_attempts} ──[/warning]")
                
            console.print(f"[accent]Running ORFS:[/accent] cd {os.path.join(root, 'flow')} && {' '.join(cmd)}")
            console.print(f"[dim]Logging output to: {orfs_log_path}[/dim]")
            
            with open(orfs_log_path, "w") as log_f:
                result = subprocess.run(
                    cmd,
                    cwd=os.path.join(root, "flow"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=7200,
                )
                log_f.write(result.stdout)
                
                # Also stream a bit to the console for the user to see it isn't hanging
                if result.returncode != 0:
                    tail = "\n".join(result.stdout.splitlines()[-20:])
                    console.print(f"[dim]{tail}[/dim]")
                    
            if result.returncode == 0:
                console.print("[success]ORFS run completed successfully![/success]")
                physical_success = True
                break
                
            console.print(f"[error]ORFS run failed with exit code {result.returncode}.[/error]")
            
            if attempt < recovery_attempts:
                # Parse current parameters from config.mk
                with open(config_path, "r") as f:
                    config_content = f.read()
                
                current_params = {}
                util_match = re.search(r"export\s+CORE_UTILIZATION\s*[?=]+\s*([\d.]+)", config_content)
                if util_match:
                    current_params["core_util"] = float(util_match.group(1))
                else:
                    current_params["core_util"] = 30  # ORFS default
                    
                density_match = re.search(r"export\s+PLACE_DENSITY\s*[?=]+\s*([\d.]+)", config_content)
                if density_match:
                    current_params["target_density"] = float(density_match.group(1))
                    
                clk_match = re.search(r"export\s+CLOCK_PERIOD\s*[?=]+\s*([\d.]+)", config_content)
                if clk_match:
                    current_params["clock_period"] = float(clk_match.group(1))
    
                categories = ol_fixer.classify(result.stdout)
                console.print(f"[dim]Detected error categories: {categories}[/dim]")
                
                recovery = ol_fixer.get_fix(categories, current_params, attempt)
                console.print(f"[accent]Proposed Fix:[/accent] {recovery.description}")
                
                if recovery.action == RecoveryAction.FAIL:
                    console.print("[error]Unrecoverable error. Giving up.[/error]")
                    raise typer.Exit(result.returncode)
                
                if recovery.action == RecoveryAction.FIX_RTL:
                    fix_rtl_requested = True
                    break
                
                # Apply fix to config.mk
                new_content = config_content
                if "core_util" in recovery.params:
                    new_util = recovery.params["core_util"]
                    if util_match:
                        new_content = re.sub(r"(export\s+CORE_UTILIZATION\s*[?=]+\s*)[\d.]+", f"\\g<1>{new_util}", new_content)
                    else:
                        new_content += f"\nexport CORE_UTILIZATION = {new_util}\n"
                        
                if "target_density" in recovery.params:
                    # AgentIC util is 0-100, target_density is 0.0-1.0
                    new_density = recovery.params["target_density"]
                    if density_match:
                        new_content = re.sub(r"(export\s+PLACE_DENSITY\s*[?=]+\s*)[\d.]+", f"\\g<1>{new_density}", new_content)
                    else:
                        new_content += f"\nexport PLACE_DENSITY = {new_density}\n"
                elif "core_util" in recovery.params:
                    # If util was reduced, proportionally reduce density to give placer room
                    new_density = min(0.65, (recovery.params["core_util"] / 100.0) + 0.15)
                    if density_match:
                        new_content = re.sub(r"(export\s+PLACE_DENSITY\s*[?=]+\s*)[\d.]+", f"\\g<1>{new_density}", new_content)
                    else:
                        new_content += f"\nexport PLACE_DENSITY = {new_density}\n"
                        
                if "clock_period" in recovery.params:
                    new_clk = recovery.params["clock_period"]
                    if clk_match:
                        new_content = re.sub(r"(export\s+CLOCK_PERIOD\s*[?=]+\s*)[\d.]+", f"\\g<1>{new_clk}", new_content)
                    else:
                        new_content += f"\nexport CLOCK_PERIOD = {new_clk}\n"
                
                with open(config_path, "w") as f:
                    f.write(new_content)
                
                console.print(f"[success]Updated ORFS config.mk parameters! Retrying...[/success]")
                
        if physical_success:
            return
            
        if not fix_rtl_requested:
            console.print(f"[error]ORFS run failed after {recovery_attempts} recovery attempts.[/error]")
            raise typer.Exit(1)
            
        if rtl_fixes >= MAX_RTL_FIXES:
            console.print("[error]Max autonomous RTL fixes reached. Giving up.[/error]")
            raise typer.Exit(1)
            
        rtl_fixes += 1
        console.print(f"\n[warning]── Autonomous RTL Fix Attempt {rtl_fixes}/{MAX_RTL_FIXES} ──[/warning]")
        
        # We need to invoke AgentIC to fix the logic bug!
        from .core.react_agent import ReActAgent
        from .tools.vlsi_tools import run_syntax_check, OPENLANE_ROOT
        import glob
        
        console.print("[accent]Initializing AgentIC LLM to autonomously rewrite the Verilog code...[/accent]")
        llm = get_llm()
        
        src_dir = os.path.join(OPENLANE_ROOT, "designs", design, "src")
        all_rtl_code = ""
        v_files = glob.glob(os.path.join(src_dir, "*.v"))
        for v_file in v_files:
            try:
                with open(v_file, "r") as f:
                    all_rtl_code += f"// --- File: {os.path.basename(v_file)} ---\n{f.read()}\n\n"
            except OSError:
                pass
                
        react_agent = ReActAgent(
            llm=llm,
            role="RTL Synthesis Fixer",
            max_steps=6,
            verbose=False,
        )
        react_agent.register_tool(
            "syntax_check",
            "Run Verilator syntax check on an absolute .v file path. Returns error text.",
            lambda p: str(run_syntax_check(p.strip().strip('"\''))),
        )
        react_agent.register_tool(
            "read_file",
            "Read contents of an absolute file path.",
            lambda p: open(p.strip().strip('"\'')).read() if os.path.exists(p.strip().strip('"\'')) else f"Not found: {p}",
        )
        
        errors_for_llm = "\n".join(result.stdout.splitlines()[-200:])
        _react_context = (
            f"RTL directory: {src_dir}\n\n"
            f"Synthesis Errors:\n{errors_for_llm}\n\n"
            f"Current RTL (All Modules):\n```verilog\n{all_rtl_code}\n```"
        )
        _react_trace = react_agent.run(
            task=(
                f"Fix all synthesis and logic errors in the Verilog code for design '{design}'. "
                f"The errors may be in the top module or in any of the sub-modules provided in the context. "
                f"Use syntax_check tool to verify your fix compiles clean. "
                f"Final Answer must contain ONLY the corrected Verilog inside ```verilog fences. "
                f"YOU MUST OUTPUT THE FULL CODE FOR ALL MODULES THAT YOU MODIFY so they can be saved to disk. "
                f"Ensure the filename comment is EXACTLY like this on the first line inside each fence: // --- File: filename.v ---"
            ),
            context=_react_context,
        )
        console.print(f"[success]LLM RTL Fix Reasoning:[/success] {_react_trace.final_answer[:500]}...")
        
        # Parse the output code blocks and save them
        code_blocks = re.findall(r"```verilog\n(.*?)\n```", _react_trace.final_answer, re.DOTALL)
        for block in code_blocks:
            file_match = re.search(r"//\s*---\s*File:\s*([a-zA-Z0-9_]+\.v)\s*---", block)
            if file_match:
                filename = file_match.group(1)
                filepath = os.path.join(src_dir, filename)
                with open(filepath, "w") as f:
                    f.write(block)
                console.print(f"[dim]Saved fixed RTL to {filepath}[/dim]")
        
        # Sync the new RTL files from AgentIC back into the ORFS workspace!
        console.print("[dim]Syncing repaired RTL into ORFS workspace...[/dim]")
        _prepare_orfs_agentic_design(root, platform, design, clock_period)
        
        console.print(f"[success]Restarting ORFS Make with repaired RTL...[/success]")


@app.command("install-experimental-tools")
def install_experimental_tools(
    fault_image: str = typer.Option(
        "ghcr.io/aucohl/fault:latest",
        "--fault-image",
        help="Docker image used for experimental open DFT/ATPG helpers",
    ),
    skip_apt: bool = typer.Option(False, "--skip-apt", help="Skip apt package installation"),
    no_shell: bool = typer.Option(False, "--no-shell", help="Do not update shell profile exports"),
):
    """Install open helpers for experimental DFT/ATPG, GLS, SPICE, and inspection."""
    _install_experimental_flow_tools(
        fault_image=fault_image,
        skip_apt=skip_apt,
        no_shell=no_shell,
    )


@app.command("setup-cli")
def setup_cli(
    target_dir: str = typer.Option(
        os.path.expanduser("~/oss-cad-suite"),
        "--target",
        "-t",
        help="Directory to install OSS CAD Suite into",
    ),
    pdk_root: str = typer.Option(
        os.path.expanduser("~/.ciel"),
        "--pdk-root",
        help="Directory to install PDKs into",
    ),
    pdks: str = typer.Option(
        "sky130",
        "--pdks",
        help="Comma-separated PDKs to install, or 'all-open-auto' for recommended auto-installable PDKs",
    ),
    skip_oss: bool = typer.Option(False, "--skip-oss", help="Skip OSS CAD Suite install"),
    skip_openlane: bool = typer.Option(False, "--skip-openlane", help="Skip Docker/OpenLane image pull"),
    skip_signoff_tools: bool = typer.Option(
        False,
        "--skip-signoff-tools",
        help="Skip Magic/Netgen/OpenSTA physical signoff tool setup",
    ),
    skip_experimental_tools: bool = typer.Option(
        False,
        "--skip-experimental-tools",
        help="Skip open experimental helpers for DFT/ATPG, GLS, SPICE, waveform, and layout inspection",
    ),
    skip_pdk: bool = typer.Option(False, "--skip-pdk", help="Skip PDK installation"),
    openlane_image: str = typer.Option(
        OPENLANE2_IMAGE,
        "--openlane-image",
        help="Docker image used for OpenLane hardening",
    ),
    commercial_template: str = typer.Option(
        os.path.expanduser("~/.agentic/commercial-tools.env"),
        "--commercial-template",
        help="Path for commercial/foundry tool registration template",
    ),
):
    """Install the complete AgentIC CLI stack in one command."""
    install_tools(
        target_dir=target_dir,
        pdk_root=pdk_root,
        skip_pdk=skip_pdk,
        skip_oss=skip_oss,
        skip_hardening=skip_openlane,
        skip_signoff_tools=skip_signoff_tools,
        skip_experimental_tools=skip_experimental_tools,
        pdks=pdks,
        env_file="",
        openlane_image=openlane_image,
        commercial_template=commercial_template,
    )


@app.command("install-tools")
def install_tools(
    target_dir: str = typer.Option(
        os.path.expanduser("~/oss-cad-suite"),
        "--target",
        "-t",
        help="Directory to install OSS CAD Suite into",
    ),
    pdk_root: str = typer.Option(
        os.path.expanduser("~/.ciel"),
        "--pdk-root",
        help="Directory to install PDKs into",
    ),
    skip_pdk: bool = typer.Option(
        False,
        "--skip-pdk",
        help="Skip PDK installation (tools only)",
    ),
    skip_oss: bool = typer.Option(
        False,
        "--skip-oss",
        help="Skip OSS CAD Suite installation",
    ),
    skip_hardening: bool = typer.Option(
        False,
        "--skip-hardening",
        help="Skip Docker/OpenLane image setup",
    ),
    skip_signoff_tools: bool = typer.Option(
        False,
        "--skip-signoff-tools",
        help="Skip Magic/Netgen/OpenSTA install for direct drc/lvs/sta commands",
    ),
    skip_experimental_tools: bool = typer.Option(
        False,
        "--skip-experimental-tools",
        help="Skip open experimental helpers for DFT/ATPG, GLS, SPICE, waveform, and layout inspection",
    ),
    pdks: str = typer.Option(
        "sky130",
        "--pdks",
        help="Comma-separated PDKs to install, or 'all-open-auto' for recommended auto-installable PDKs.",
    ),
    openlane_image: str = typer.Option(
        OPENLANE2_IMAGE,
        "--openlane-image",
        help="Docker image to pull for OpenLane hardening",
    ),
    env_file: str = typer.Option(
        "",
        "--env-file",
        help="Write local-LLM .env template to this path",
    ),
    commercial_template: str = typer.Option(
        os.path.expanduser("~/.agentic/commercial-tools.env"),
        "--commercial-template",
        help="Write commercial/foundry tool registration template to this path",
    ),
):
    """One-shot setup: install OSS CAD Suite, signoff tools, experimental helpers, Docker/OpenLane, volare, PDKs, and shell env.

    Examples:
        agentic install-tools
        agentic setup-cli --pdks sky130,gf180mcu
        agentic setup-cli --pdks all-open-auto
        agentic install-oss
        agentic install-openlane
        agentic install-tools --target /opt/oss-cad-suite --pdk-root /opt/pdks
        agentic install-tools --pdks sky130,gf180mcu,asap7
        agentic install-tools --pdks all-open-auto
        agentic install-tools --skip-pdk
        agentic install-tools --skip-hardening
        agentic install-tools --env-file /root/my-project/.env
    """
    import shutil
    import subprocess
    from .install_tools import install_oss_cad_suite
    from .config import detect_available_pdks, validate_pdk_installation

    changed = False
    target_dir = os.path.abspath(os.path.expanduser(target_dir))
    pdk_root = os.path.abspath(os.path.expanduser(pdk_root))

    total_steps = 6
    if skip_signoff_tools:
        total_steps -= 1
    if skip_experimental_tools:
        total_steps -= 1

    # ── 1. OSS CAD Suite ──────────────────────────────────────────────────
    if not skip_oss:
        console.print(
            Panel(
                f"[accent]Step 1/{total_steps}: OSS CAD Suite[/accent]\n"
                f"Target: {target_dir}",
                title="🔧 Installing EDA Tools",
            )
        )
        if _is_oss_cad_suite_present_at(target_dir):
            console.print(f"[success]OSS CAD Suite already present at target:[/success] {target_dir}")
        else:
            if _is_toolchain_present():
                console.print(
                    "[warning]EDA tools exist elsewhere on PATH, but the requested "
                    f"target is incomplete. Installing/repairing {target_dir}.[/warning]"
                )
            os.makedirs(target_dir, exist_ok=True)
            ok = install_oss_cad_suite(target_dir)
            if ok:
                console.print("[success]OSS CAD Suite installed.[/success]")
                changed = True
            else:
                console.print("[error]OSS CAD Suite installation failed.[/error]")
                raise typer.Exit(1)
    else:
        console.print("[dim]Skipped OSS CAD Suite (--skip-oss).[/dim]")

    if not skip_signoff_tools:
        console.print(
            Panel(
                f"[accent]Step 2/{total_steps}: Physical Signoff Tools[/accent]\n"
                "Magic 8.3.411+, Netgen, OpenSTA",
                title="Installing Signoff Tools",
            )
        )
        install_signoff_tools(
            prefix=os.path.expanduser("~/eda"),
            source_dir=os.path.expanduser("~/eda-src/magic"),
            pdk_root=pdk_root,
            skip_apt=False,
            skip_magic_build=False,
            force_magic=False,
            no_shell=False,
        )
    else:
        console.print("[dim]Skipped physical signoff tools (--skip-signoff-tools).[/dim]")

    if not skip_experimental_tools:
        console.print(
            Panel(
                f"[accent]Step {3 if not skip_signoff_tools else 2}/{total_steps}: Experimental Complete Flow Helpers[/accent]\n"
                "Fault Docker, ngspice, KLayout, GTKWave, Xschem, cocotb helpers",
                title="Installing Experimental Flow",
            )
        )
        _install_experimental_flow_tools(no_shell=False)
    else:
        console.print("[dim]Skipped experimental complete flow tools (--skip-experimental-tools).[/dim]")

    if not skip_hardening:
        hardening_step = 4
        if skip_signoff_tools:
            hardening_step -= 1
        if skip_experimental_tools:
            hardening_step -= 1
        console.print(
            Panel(
                f"[accent]Step {hardening_step}/{total_steps}: Docker/OpenLane Hardening Backend[/accent]\n"
                f"Backend: OpenLane 2\nImage: {openlane_image}",
                title="Installing OpenLane",
            )
        )
        _install_openlane2_backend(image=openlane_image, force=False, smoke_test=False)
    else:
        console.print("[dim]Skipped Docker/OpenLane setup (--skip-hardening).[/dim]")

    if not skip_pdk:
        # ── 2. Volare ─────────────────────────────────────────────────────
        volare_step = 5
        if skip_signoff_tools:
            volare_step -= 1
        if skip_experimental_tools:
            volare_step -= 1
        console.print(
            Panel(
                f"[accent]Step {volare_step}/{total_steps}: Volare PDK Manager[/accent]",
                title="📦 Installing Volare",
            )
        )
        volare_ok, volare_version = _check_volare_available()
        if volare_ok:
            console.print(f"[success]Volare already installed ({volare_version}) — skipping.[/success]")
        else:
            console.print("[warning]Volare not found. Installing via pip...[/warning]")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "volare"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                console.print(f"[error]Failed to install volare:[/error]\n{result.stderr}")
                raise typer.Exit(1)
            console.print("[success]Volare installed successfully.[/success]")
            changed = True

        # ── 3. PDK ────────────────────────────────────────────────────────
        pdk_step = 6
        if skip_signoff_tools:
            pdk_step -= 1
        if skip_experimental_tools:
            pdk_step -= 1
        requested_pdks = [
            item.strip().lower()
            for item in pdks.split(",")
            if item.strip()
        ]
        if not requested_pdks:
            requested_pdks = ["sky130"]
        if requested_pdks == ["all-open-auto"]:
            requested_pdks = _auto_installable_pdks()

        console.print(
            Panel(
                f"[accent]Step {pdk_step}/{total_steps}: PDK Installation[/accent]\n"
                f"Target: {pdk_root}\n"
                f"PDKs: {', '.join(requested_pdks)}",
                title="🧱 Installing PDK",
            )
        )
        os.makedirs(pdk_root, exist_ok=True)

        for requested_pdk in requested_pdks:
            install_pdk(
                pdk_name=requested_pdk,
                version="",
                check=False,
                list_versions=False,
                pdk_path="",
                pdk_root=pdk_root,
                list_installed=False,
                force=False,
            )
            changed = True
    else:
        console.print("[dim]Skipped Volare PDK manager (--skip-pdk).[/dim]")
        console.print("[dim]Skipped PDK installation (--skip-pdk).[/dim]")

    _write_commercial_tool_template(commercial_template)

    # ── 4. Shell environment ──────────────────────────────────────────────
    console.print(
        Panel(
            "[accent]Configuring shell environment[/accent]",
            title="🐚 Shell Setup",
        )
    )

    _write_cli_shell_exports(target_dir, pdk_root, include_oss=not skip_oss)

    # Apply to current process so doctor works immediately
    if not skip_oss:
        _refresh_runtime_tool_paths(target_dir, pdk_root)
    else:
        os.environ["PDK_ROOT"] = pdk_root

    # ── 5. Optional .env file ─────────────────────────────────────────────
    if env_file:
        env_path = os.path.abspath(os.path.expanduser(env_file))
        os.makedirs(os.path.dirname(env_path), exist_ok=True)
        if not os.path.exists(env_path):
            with open(env_path, "w", encoding="utf-8") as f:
                f.write(
                    '# --- Local vLLM Backend ---\n'
                    'LLM_MODEL="local-llm"\n'
                    'LLM_BASE_URL="http://localhost:8000/v1"\n'
                    'LLM_API_KEY="sk-no-key-required"\n\n'
                    '# --- Role overrides (all local) ---\n'
                    'ROLE_ARCHITECT_MODEL="local-llm"\n'
                    'ROLE_ARCHITECT_BASE_URL="http://localhost:8000/v1"\n'
                    'ROLE_DESIGNER_MODEL="local-llm"\n'
                    'ROLE_DESIGNER_BASE_URL="http://localhost:8000/v1"\n'
                    'ROLE_FIXER_MODEL="local-llm"\n'
                    'ROLE_FIXER_BASE_URL="http://localhost:8000/v1"\n'
                    'ROLE_DEBUGGER_MODEL="local-llm"\n'
                    'ROLE_DEBUGGER_BASE_URL="http://localhost:8000/v1"\n'
                    'ROLE_VERIFIER_MODEL="local-llm"\n'
                    'ROLE_VERIFIER_BASE_URL="http://localhost:8000/v1"\n'
                    'ROLE_TESTBENCH_DESIGNER_MODEL="local-llm"\n'
                    'ROLE_TESTBENCH_DESIGNER_BASE_URL="http://localhost:8000/v1"\n'
                    'ROLE_MANAGER_MODEL="local-llm"\n'
                    'ROLE_MANAGER_BASE_URL="http://localhost:8000/v1"\n'
                    'ROLE_PHYSICAL_MODEL="local-llm"\n'
                    'ROLE_PHYSICAL_BASE_URL="http://localhost:8000/v1"\n'
                    'ROLE_DOCUMENTER_MODEL="local-llm"\n'
                    'ROLE_DOCUMENTER_BASE_URL="http://localhost:8000/v1"\n'
                    'ROLE_REASONER_MODEL="local-llm"\n'
                    'ROLE_REASONER_BASE_URL="http://localhost:8000/v1"\n\n'
                    '# --- AgentIC Model ---\n'
                    'AGENTIC_MODEL_ENABLED=1\n'
                    'AGENTIC_MODEL_MODEL="local-llm"\n'
                    'AGENTIC_MODEL_BASE_URL="http://localhost:8000/v1"\n\n'
                    '# --- PDK ---\n'
                    f'PDK_ROOT="{pdk_root}"\n'
                    'PDK="sky130A"\n\n'
                    '# --- EDA tool overrides ---\n'
                    'NGSPICE_BIN="ngspice"\n'
                )
            console.print(f"[success]Created local-LLM .env at {env_path}[/success]")
        else:
            console.print(f"[warning].env already exists at {env_path} — not overwriting.[/warning]")

    # ── Final report ──────────────────────────────────────────────────────
    console.print(
        Panel(
            "[success]All done![/success]\n\n"
            "Run [accent]source ~/.bashrc[/accent] (or open a new shell) to reload PATH.\n"
            "Then verify with: [accent]agentic doctor[/accent]",
            title="🎉 Setup Complete",
            border_style="success",
        )
    )


@app.command("install-pdk")
def install_pdk(
    pdk_name: str = typer.Argument(
        None,
        help="PDK name (e.g., sky130, gf180mcu). Use 'list' to see all available PDKs.",
    ),
    version: str = typer.Option("", "--version", "-v", help="Specific version to install"),
    check: bool = typer.Option(False, "--check", help="Verify that the PDK is installed correctly"),
    list_versions: bool = typer.Option(
        False, "--list-versions", help="List installable/known versions for this PDK"
    ),
    pdk_path: str = typer.Option(
        "", "--path", help="Register a custom PDK directory under $PDK_ROOT"
    ),
    pdk_root: str = typer.Option(
        "",
        "--pdk-root",
        help="PDK root to install into. Defaults to $PDK_ROOT or ~/.ciel.",
    ),
    list_installed: bool = typer.Option(False, "--installed", help="List currently installed PDKs"),
    force: bool = typer.Option(False, "--force", "-f", help="Reinstall even if already installed"),
):
    """Install open-source PDKs for use with AgentIC.

    Examples:
        agentic install-pdk sky130
        agentic install-pdk sky130 --pdk-root ~/.ciel
        agentic install-pdk sky130 --check
        agentic install-pdk sky130 --list-versions
        agentic install-pdk my_custom_pdk --path /path/to/pdk
        agentic install-pdk list
        agentic install-pdk list --installed
    """
    from .config import detect_available_pdks, validate_pdk_installation
    if pdk_root:
        install_dir = os.path.abspath(os.path.expanduser(pdk_root))
        os.environ["PDK_ROOT"] = install_dir
    else:
        install_dir = os.path.abspath(
            os.path.expanduser(os.environ.get("PDK_ROOT", os.path.expanduser("~/.ciel")))
        )

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
        table.add_column("Tier", width=14)
        table.add_column("Status", width=12)
        table.add_column("Install Method", style="dim")
        table.add_column("Flow Status", style="dim")

        for key, cfg in PDK_INSTALL_CONFIGS.items():
            is_installed = key in installed
            if list_installed and not is_installed:
                continue
            status = "[success]Installed[/success]" if is_installed else "[dim]Not installed[/dim]"
            install_method = _install_method_label(cfg)
            table.add_row(
                key,
                cfg["name"],
                cfg["pdk_dir"],
                _pdk_choice_label(cfg),
                status,
                install_method,
                cfg.get("flow_status") or cfg["description"],
            )

        console.print(table)
        console.print(
            "\n[info]Recommended one-command install targets:[/info] "
            f"[accent]{', '.join(_auto_installable_pdks())}[/accent]"
        )
        console.print("[info]To install one:[/info] [accent]agentic install-pdk sky130[/accent]")
        console.print("[info]To install all recommended:[/info] [accent]agentic setup-cli --pdks all-open-auto[/accent]")
        console.print("[info]After install, verify with:[/info] [accent]agentic doctor[/accent]")
        return

    pdk_key = pdk_name.strip().lower().replace("_", "-")
    pdk_key = PDK_INSTALL_ALIASES.get(pdk_key, pdk_key)

    if pdk_path:
        _register_custom_pdk_path(pdk_key, pdk_path, install_dir)
        ok, messages = validate_pdk_installation(pdk_key, install_dir)
        for msg in messages:
            console.print(f"  {'[success]✓[/success]' if 'Found' in msg else '[warning]⚠[/warning]'} {msg}")
        if not ok:
            raise typer.Exit(1)
        return

    if pdk_key not in PDK_INSTALL_CONFIGS:
        console.print(
            f"[error]Unknown PDK: {pdk_name}[/error]\n"
            "Run [accent]agentic install-pdk list[/accent] to see available PDKs, "
            "or register a custom PDK with [accent]agentic install-pdk <name> --path /path/to/pdk[/accent]."
        )
        raise typer.Exit(1)

    cfg = PDK_INSTALL_CONFIGS[pdk_key]
    detected = detect_available_pdks()
    is_installed = pdk_key in detected

    if list_versions:
        versions = cfg.get("versions", [])
        console.print(
            Panel(
                "\n".join(f"- {item}" for item in versions) or "No version list available.",
                title=f"Known Versions: {cfg['name']}",
            )
        )
        return

    if check:
        ok, messages = validate_pdk_installation(pdk_key, install_dir)
        title = "PDK Check Passed" if ok else "PDK Check Failed"
        style = "success" if ok else "error"
        console.print(
            Panel(
                "\n".join(messages),
                title=title,
                border_style=style,
            )
        )
        if not ok:
            _print_manual_pdk_steps(pdk_key, cfg)
            raise typer.Exit(1)
        return

    if cfg.get("proprietary"):
        _print_manual_pdk_steps(pdk_key, cfg)
        return

    if not cfg.get("auto_installable") and cfg.get("install_method") == "download" and not force:
        console.print(
            Panel(
                f"[warning]{cfg['name']} is listed as {cfg.get('support_level', 'research')} collateral, "
                "not a verified one-command AgentIC hardening target.[/warning]\n\n"
                f"Flow status: {cfg.get('flow_status', cfg.get('description', 'unknown'))}\n\n"
                "Use [accent]--force[/accent] if you intentionally want AgentIC to download and register "
                "this research/educational PDK anyway.",
                title="Experimental PDK",
                border_style="warning",
            )
        )
        return

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
                console.print(f"[error]Failed to install volare:[/error]\n{result.stderr}")
                raise typer.Exit(1)
            console.print("[success]Volare installed successfully.[/success]")

        success = _run_volare_install(pdk_key, target_version, install_dir)
        if success:
            _ensure_pdk_root_shell_export(install_dir)
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
        git_url = cfg.get("git_url", "")
        download_url = cfg.get("download_url", "")
        if not download_url and not git_url:
            _print_manual_pdk_steps(pdk_key, cfg)
            return

        import tempfile
        import shutil
        import subprocess

        if git_url:
            console.print(f"[accent]Cloning PDK repository {git_url}...[/accent]")
            extract_dir = os.path.join(tempfile.gettempdir(), f"agentic_{pdk_key}_clone")
            shutil.rmtree(extract_dir, ignore_errors=True)
            try:
                result = subprocess.run(
                    ["git", "clone", "--recursive", "--depth=1", git_url, extract_dir],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    console.print(f"[error]Git clone failed:[/error]\n{result.stderr}")
                    raise typer.Exit(1)
            except Exception as e:
                console.print(f"[error]Git clone failed: {e}[/error]")
                raise typer.Exit(1)
            extracted_path = extract_dir
            archive_path = None
        else:
            # ── Probe URL and get file size ──
            console.print("[accent]Checking PDK availability...[/accent]")
            try:
                import requests as _requests
                head = _requests.head(download_url, allow_redirects=True, timeout=15)
                if head.status_code == 404:
                    # Try fallback: swap tags/v1.0.0 → main
                    fallback_url = download_url.replace("/refs/tags/v1.0.0", "/main")
                    if fallback_url != download_url:
                        head2 = _requests.head(fallback_url, allow_redirects=True, timeout=15)
                        if head2.status_code == 200:
                            download_url = fallback_url
                            head = head2
                        else:
                            console.print(f"[error]PDK archive not found at {download_url}[/error]")
                            console.print(f"[dim]Try volare-based PDKs: agentic install-pdk sky130[/dim]")
                            raise typer.Exit(1)
                    else:
                        console.print(f"[error]PDK archive not found at {download_url}[/error]")
                        console.print(f"[dim]Try volare-based PDKs: agentic install-pdk sky130[/dim]")
                        raise typer.Exit(1)
                head.raise_for_status()
            except _requests.exceptions.RequestException as e:
                console.print(f"[error]Cannot reach PDK server: {e}[/error]")
                console.print("[dim]Check your internet connection.[/dim]")
                raise typer.Exit(1)
    
            total_size = int(head.headers.get("content-length", 0))
            if total_size > 0:
                size_mb = total_size / (1024 * 1024)
                if size_mb >= 1:
                    console.print(f"[info]PDK size: {size_mb:.1f} MB[/info]")
                else:
                    console.print(f"[info]PDK size: {total_size / 1024:.0f} KB[/info]")
            else:
                console.print("[dim]PDK size: unknown[/dim]")
    
            console.print("[accent]Downloading PDK archive...[/accent]")
            archive_path = os.path.join(tempfile.gettempdir(), f"agentic_{pdk_key}.tar.gz")
    
            try:
                with _requests.get(download_url, stream=True, timeout=300) as resp:
                    resp.raise_for_status()
                    downloaded = 0
                    with open(archive_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=65536):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_size > 0:
                                pct = min(100, downloaded * 100 // total_size)
                                bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
                                console.print(f"\r  [{bar}] {pct}%", end="")
                    if total_size > 0:
                        console.print()  # newline after progress bar
                console.print(f"[success]Download complete ({downloaded / (1024*1024):.1f} MB)[/success]")
            except Exception as e:
                console.print(f"\n[error]Download failed: {e}[/error]")
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
                _cleanup_temp(archive_path, extract_dir)
                raise typer.Exit(1)
    
        if not git_url:
            # Find the extracted directory (handle archives with README/LICENSE at top level)
            extracted_entries = os.listdir(extract_dir)
            dirs = [e for e in extracted_entries if os.path.isdir(os.path.join(extract_dir, e))]
            if not dirs:
                console.print("[error]Archive contains no directories — cannot install PDK.[/error]")
                _cleanup_temp(archive_path, extract_dir)
                raise typer.Exit(1)
            extracted_path = os.path.join(extract_dir, dirs[0])
            # If the first dir is a wrapper (github archives produce  repo-tag/), use it
            if len(dirs) == 1:
                pass  # Standard single-directory extraction
            else:
                # Multiple top-level dirs — pick the most PDK-looking one
                pdk_looking = [d for d in dirs if d.lower().startswith(("pdk", "lib", "asap", "nangate", "osu", "freepdk"))]
                extracted_path = os.path.join(extract_dir, pdk_looking[0]) if pdk_looking else os.path.join(extract_dir, dirs[0])

        target_path = os.path.join(install_dir, cfg["pdk_dir"])
        os.makedirs(install_dir, exist_ok=True)

        if os.path.exists(target_path):
            shutil.rmtree(target_path)
        shutil.move(extracted_path, target_path)
        if not git_url:
            _cleanup_temp(archive_path, extract_dir)

        # ── Auto-restructure for OpenLane compatibility ──
        _auto_restructure_pdk(target_path, cfg, pdk_key)

        new_detected = detect_available_pdks()
        if pdk_key in new_detected and new_detected[pdk_key].get("root_path"):
            _ensure_pdk_root_shell_export(install_dir)
            console.print(
                f"[success]✅ {cfg['name']} installed successfully![/success]\n"
                f"Location: [info]{new_detected[pdk_key].get('root_path')}[/info]\n\n"
                f"Set in your shell:\n"
                f"  [accent]export PDK_ROOT={install_dir}[/accent]\n\n"
                "Verify with: [accent]agentic doctor[/accent]"
            )
        else:
            console.print(
                f"[warning]PDK extracted but not auto-detected after restructuring.[/warning]\n"
                f"Expected layout: libs.ref/{{cells}}/verilog/ and libs.tech/magic/\n"
                f"Set: [accent]export PDK_ROOT={install_dir}[/accent]\n"
                f"For best results, use volare-based PDKs: sky130, gf180mcu"
            )


# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSAL LLM INITIALIZATION
# ─────────────────────────────────────────────────────────────────────────────


def _format_model_for_provider(model: str, base_url: str) -> str:
    """
    Ensure the model string is compatible with the provider.

    LiteLLM requires provider prefixes for non-OpenAI endpoints:
      - openai/infinity
      - anthropic/claude-3-5-sonnet
      - generic/llama-3.3-70b
      - openai/llama-3.1-70b  (custom endpoint with OpenAI-compatible API)

    If base_url is localhost/internal, assume OpenAI-compatible format.
    """
    model = (model or "").strip()

    # Already has a provider prefix (check if first part is a known provider)
    known_providers = ["openai", "anthropic", "generic", "together", "infinity", "ollama", "huggingface", "vertex_ai", "bedrock", "groq", "azure"]
    if "/" in model and any(model.startswith(p + "/") for p in known_providers):
        return model

    # Infer provider from base_url
    base_lower = (base_url or "").lower()

    if "anthropic" in base_lower:
        return f"anthropic/{model}"
    if "generic" in base_lower:
        return f"generic/{model}"
    if "openai" in base_lower:
        return f"openai/{model}"
    if "together" in base_lower:
        return f"together/{model}"
    if "infinity" in base_lower:
        return f"infinity/{model}"

    # Localhost/vLLM/internal endpoints — keep model name as-is (no prefix)
    if "localhost" in base_lower or "127.0.0.1" in base_lower or "0.0.0.0" in base_lower:
        return model

    # Default to openai/ prefix for custom endpoints
    if base_lower and "openai.com" not in base_lower:
        return f"openai/{model}"

    return f"openai/{model}"


def _llm_extra_body_from_env() -> Optional[dict]:
    """Read provider request extras from env without tying code to a model name."""
    raw = os.environ.get("LLM_EXTRA_BODY_JSON", "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        console.print("[warning]LLM_EXTRA_BODY_JSON must decode to a JSON object, ignoring.[/warning]")
        return None
    except json.JSONDecodeError:
        console.print("[warning]Ignoring invalid LLM_EXTRA_BODY_JSON (must be valid JSON).[/warning]")
        return None



def _role_extra_body(cfg: dict) -> Optional[dict]:
    return cfg.get("extra_body") or _llm_extra_body_from_env()


def get_llm(
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = int(os.environ.get("LLM_MAX_TOKENS", 8192)),
    show_verbose: bool = False,
) -> "LLM":
    """
    Universally instantiate a CrewAI LLM for any OpenAI-compatible provider.

    Priority order:
      1. Explicit parameters passed to this function
      2. Environment variables (LLM_MODEL, LLM_BASE_URL, LLM_API_KEY)
      3. credentials.json (build group)

    Args:
        model: Override model name (e.g. "infinity", "claude-3-5-sonnet")
        base_url: Override endpoint (e.g. "https://api.generic.com/openai/v1")
        api_key: Override API key
        temperature: Sampling temperature (default 0.2)
        max_tokens: Max output tokens (default 16384)
        show_verbose: Print connection diagnostics

    Returns:
        Configured crewai.LLM instance
    """
    from .config import DEFAULT_LLM_CONFIG, resolve_llm_config

    import io
    import sys

    _null_out = io.StringIO()
    _original_stdout = sys.stdout
    _original_stderr = sys.stderr
    sys.stdout = _null_out
    sys.stderr = _null_out

    # Resolve config with priority: explicit > env > credentials > defaults
    cfg = resolve_llm_config(
        env_var_prefix="LLM",
        credential_group="build",
        fallback_model="openai/infinity",
        fallback_base_url="https://api.openai.com/v1",
    )

    model = (model or "").strip() or cfg["model"]
    base_url = (base_url or "").strip() or cfg["base_url"]
    api_key = (api_key or "").strip() or cfg["api_key"]

    # Format model string with provider prefix
    model = _format_model_for_provider(model, base_url)

    # Optional provider-specific extra_body, configured via environment.
    extra_body = _llm_extra_body_from_env()

    try:
        llm = LLM(
            model=model,
            base_url=base_url,
            api_key=api_key if api_key and api_key != "NA" else "mock-key",
            temperature=temperature,
            top_p=0.7,
            max_tokens=max_tokens,
            timeout=300,
            extra_body=extra_body,
        )

        # Test the connection with rate limiting for Z.AI
        from .tools.rate_limiter import rate_limited_call

        rate_limited_call(
            llm.call,
            [{"role": "user", "content": "Hi"}],
            model=model,
            base_url=base_url,
        )
        sys.stdout = _original_stdout
        sys.stderr = _original_stderr

        provider = model.split("/")[0] if "/" in model else "openai"
        console.print(f"[green]✓[/green] LLM: {model} @ {base_url}")
        return llm

    except Exception as e:
        sys.stdout = _original_stdout
        sys.stderr = _original_stderr
        if show_verbose:
            console.print(f"[dim]  LLM connection failed: {str(e)[:120]}[/dim]")
        raise

    sys.stdout = _original_stdout
    sys.stderr = _original_stderr

    console.print(f"\n[red]✗[/red] Failed to connect to LLM")
    console.print(f"  [dim]Set LLM_API_KEY in .env file[/dim]")
    console.print(f"  [dim]See: agentic config --help[/dim]")
    raise typer.Exit(1)


def run_startup_diagnostics(strict: bool = True):
    diag = startup_self_check()
    ok = bool(diag.get("ok", False))
    if not ok:
        console.print("[red]✗[/red] Toolchain check failed")
        for check in diag.get("checks", []):
            if not check.get("ok"):
                console.print(f"  [red]✗[/red] {check.get('tool')} -> {check.get('resolved')}")
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
    """Run simulation on an existing design with AUTO-FIX loop and self-healing recovery."""
    verify_license()
    check_dependencies(skip_openlane=True)
    console.print(
        Panel(
            f"[accent]AgentIC: Simulation + Auto-Fix Mode[/accent]\n"
            f"Design: [warning]{name}[/warning]\n"
            f"Recovery: up to [accent]{max_retries}[/accent] auto-fix attempts",
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
            result = str(Crew(verbose=False, agents=[fix_agent], tasks=[fix_task]).kickoff())
            return result

    sim_success, sim_output = run_simulation(name)
    sim_tries = 0

    while not sim_success and sim_tries < max_retries:
        sim_tries += 1
        console.print(f"[error]✗ SIMULATION FAILED (attempt {sim_tries}/{max_retries})[/error]")
        sim_output_text = sim_output or ""
        # 1) If compilation failed, fix TB first.
        if "Compilation failed:" in sim_output_text or "syntax error" in sim_output_text:
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


def _generate_config_tcl(design_name: str, rtl_file: str, sdc_clock_ns: float = 0.0) -> str:
    """Auto-generate OpenLane config.tcl based on design complexity.

    Reads the RTL file to estimate size and generates appropriate
    die area, clock period, and synthesis settings.
    If sdc_clock_ns is provided (from an SDC file), it overrides the heuristic.
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

    # Use SDC clock period if available
    if sdc_clock_ns > 0:
        clock_period = str(sdc_clock_ns)

    # Check for SDC file
    sdc_ref = ""
    sdc_path = f"{OPENLANE_ROOT}/designs/{design_name}/src/{design_name}.sdc"
    if os.path.exists(sdc_path):
        sdc_ref = f'\nset ::env(SDC_FILE) "{sdc_path}"\n'

    return f'''# Auto-generated by AgentIC for {design_name}
set ::env(DESIGN_NAME) "{design_name}"
set ::env(VERILOG_FILES) "$::env(DESIGN_DIR)/src/{design_name}.v"
set ::env(CLOCK_PORT) "clk"
set ::env(CLOCK_PERIOD) "{clock_period}"{sdc_ref}
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


def _extract_sdc_clock(sdc_path: str) -> float:
    """Extract clock period from an SDC file. Returns 0.0 if not found."""
    if not os.path.exists(sdc_path):
        return 0.0
    try:
        with open(sdc_path) as f:
            content = f.read()
        m = re.search(r"create_clock\s+(?:-name\s+\S+\s+)?(?:-period\s+)?([\d.]+)", content)
        if m:
            return float(m.group(1))
    except (IOError, ValueError):
        pass
    return 0.0


def _apply_harden_fix(
    die_size: int, util: int, clock_period: float,
    ol_categories: list, attempt: int, wns: float = 0.0,
) -> tuple:
    """Apply deterministic fix parameters for OpenLane failures.
    Returns (new_die, new_util, new_clock_period, description).
    """
    if not ol_categories:
        return die_size, util, clock_period, "Retrying with same parameters"

    primary = ol_categories[0]
    if primary == "timing_setup":
        relaxation_factor = 1.15 + attempt * 0.10 + (abs(wns) / 10.0 if wns < -0.5 else 0)
        new_clock = round(clock_period * relaxation_factor, 2)
        return die_size, util, new_clock, f"Relax clock: {clock_period}ns → {new_clock}ns (WNS: {wns:.3f}ns)"

    elif primary == "routing_congestion":
        new_util = max(25, util - 8 - attempt * 3)
        new_die = die_size if attempt == 0 else int(die_size * (1.15 + attempt * 0.10))
        return new_die, new_util, clock_period, f"Reduce util to {new_util}%, expand to {new_die}um"

    elif primary == "global_route_uncovered":
        new_die = int(die_size * 0.67) if die_size >= 2400 else die_size
        new_die = max(400, new_die)
        return (
            new_die,
            max(20, min(35, util)),
            clock_period,
            "Macro/pin routing recovery: scale floorplan from current design, lower density, add routing halo/blockage guidance",
        )

    elif primary == "detail_route_resource":
        new_die = int(die_size * 0.67) if die_size >= 2400 else die_size
        new_die = max(400, new_die)
        return new_die, max(20, min(35, util)), clock_period, "Compact oversized floorplan proportionally to reduce TritonRoute memory/filler load"

    elif primary == "detail_route_short":
        new_util = max(25, util - 5)
        return die_size, new_util, clock_period, "Detailed-route shorts: reduce density and preserve routing whitespace"

    elif primary in ("drc_violation", "placement_failure"):
        new_util = max(25, util - 10)
        new_die = int(die_size * (1.10 + attempt * 0.10))
        return new_die, new_util, clock_period, f"Relax floorplan: {new_die}um @ {new_util}%"

    elif primary == "lvs_mismatch":
        return die_size, util, clock_period, "LVS mismatch — may need RTL port fix"

    elif primary == "synthesis_error":
        return die_size, util, clock_period, "Synthesis error — may need RTL fix"

    else:
        return die_size, util, clock_period, f"Unknown error pattern — retrying"


@app.command()
def harden(
    name: str = typer.Option(..., "--name", "-n", help="Design name (e.g., counter)"),
    recovery_attempts: int = typer.Option(
        5, "--recovery-attempts", "-r", min=0, max=10,
        help="Max auto-recovery attempts on failure (timing/congestion/DRC)",
    ),
):
    """Run OpenLane hardening (RTL → GDSII) with self-healing recovery.

    On failure, automatically classifies the error (timing, congestion, DRC, etc.)
    and applies deterministic fixes: clock relaxation, area expansion, utilization
    reduction. Regenerates config.tcl and retries up to --recovery-attempts times.
    """
    from .core.pipeline_recovery import OpenLaneErrorFixer

    verify_license()
    check_dependencies(skip_openlane=False)
    console.print(
        Panel(
            f"[accent]AgentIC: Hardening Mode[/accent]\n"
            f"Design: [warning]{name}[/warning]\n"
            f"Recovery: up to [accent]{recovery_attempts}[/accent] auto-fix attempts",
            title="🚀 Starting OpenLane",
        )
    )

    new_config = f"{OPENLANE_ROOT}/designs/{name}/config.tcl"
    rtl_file = f"{OPENLANE_ROOT}/designs/{name}/src/{name}.v"
    sdc_file = f"{OPENLANE_ROOT}/designs/{name}/src/{name}.sdc"
    sdc_clock_ns = _extract_sdc_clock(sdc_file)

    if not os.path.exists(rtl_file):
        console.print(f"[error]✗ RTL file not found: {rtl_file}[/error]")
        raise typer.Exit(1)

    # Config auto-generate if missing (re-generate if recovery in progress)
    if True:  # Always regenerate to apply recovery params
        die_size, util, clock_period = 500, 40, 10.0
        try:
            with open(rtl_file, "r") as f:
                rtl_content = f.read()
            line_count = len(rtl_content.strip().split("\n"))
        except IOError:
            line_count = 100
        if line_count < 100:
            die_size, util, clock_period = 300, 50, 10.0
        elif line_count < 300:
            die_size, util, clock_period = 500, 40, 15.0
        else:
            die_size, util, clock_period = 800, 35, 20.0
        if sdc_clock_ns > 0:
            clock_period = sdc_clock_ns

    # Init error fixer
    ol_fixer = OpenLaneErrorFixer()

    # Ask for background execution
    run_bg = typer.confirm(
        f"OpenLane hardening can take 10-30+ minutes (self-healing: up to {recovery_attempts} attempts). Run in background?",
        default=True,
    )

    if run_bg:
        console.print("  [dim]Launching background process...[/dim]")
    else:
        console.print("  [dim]Running OpenLane (this may take 10-30+ minutes)...[/dim]")

    # ── Self-healing retry loop ──
    for attempt in range(recovery_attempts + 1):  # +1 for the initial run
        if attempt > 0:
            console.print(
                f"\n[warning]── Recovery attempt {attempt}/{recovery_attempts} ──[/warning]"
            )

        # Regenerate config with current params
        config_content = _generate_config_tcl(
            name, rtl_file, sdc_clock_ns=clock_period
        )
        # Override die/util if recovery modified them
        config_content = config_content.replace(
            f'set ::env(DIE_AREA) "0 0 ', f'set ::env(DIE_AREA) "0 0 {die_size}'
        ).replace(
            f'DIE_AREA) "0 0 ', f'DIE_AREA) "0 0 {die_size} '
        )
        config_content = re.sub(
            r'set ::env\(FP_CORE_UTIL\)\s+\d+',
            f'set ::env(FP_CORE_UTIL) {util}',
            config_content,
        )
        config_content = re.sub(
            r'set ::env\(CLOCK_PERIOD\)\s+"[\d.]+"',
            f'set ::env(CLOCK_PERIOD) "{clock_period}"',
            config_content,
        )

        os.makedirs(os.path.dirname(new_config), exist_ok=True)
        with open(new_config, "w") as f:
            f.write(config_content)
        if attempt == 0:
            console.print(f"  ✓ Config generated: [success]{new_config}[/success]")
        else:
            console.print(
                f"  ✓ Config regenerated: die={die_size}um, util={util}%, clk={clock_period}ns"
            )

        # Run OpenLane
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
            break  # Success — exit retry loop

        # ── Failure: classify and fix ──
        error_text = str(ol_result)[:5000]
        categories = ol_fixer.classify(error_text)

        wns_match = re.search(r'wns\s+([-\d.]+)', error_text, re.IGNORECASE)
        wns_val = float(wns_match.group(1)) if wns_match else 0.0

        if not categories or attempt == recovery_attempts:
            console.print(f"[error]✗ OpenLane failed (attempt {attempt+1}/{recovery_attempts+1})[/error]")
            console.print(f"[error]  Error category: {categories or 'unknown'}[/error]")
            console.print(f"  Error: {error_text[:500]}...")
            if attempt < recovery_attempts:
                console.print("  [warning]All recovery attempts exhausted.[/warning]")
            raise typer.Exit(1)

        console.print(
            f"[error]✗ OpenLane failed — categorized as: [warning]{', '.join(categories)}[/warning][/error]"
        )

        # Apply deterministic fix
        die_size, util, clock_period, fix_desc = _apply_harden_fix(
            die_size, util, clock_period, categories, attempt, wns=wns_val,
        )
        console.print(f"  [info]🔧 Applying fix: {fix_desc}[/info]")

    # ── Success — run signoff ──
    if ol_success and not run_bg:
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
    elif not ol_success:
        console.print(f"[error]✗ OpenLane failed after {recovery_attempts} recovery attempts[/error]")
        raise typer.Exit(1)


# --- THE BUILD COMMAND ---
@app.command()
def build(
    name: str = typer.Option(..., "--name", "-n", help="Design name (e.g., counter)"),
    desc: str = typer.Option(..., "--desc", "-d", help="Natural language description"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Run spec validation only"),
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
    skip_spice: bool = typer.Option(
        True,
        "--skip-spice/--run-spice",
        help="Bypass optional scoped post-layout ngspice checks (default; full-chip SPICE is not in the OSS flow)",
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
        help="Run the strongest available OSS evidence gates; commercial signoff still requires external tools",
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
    flow_profile: str = typer.Option(
        "",
        "--flow-profile",
        help="Executable flow profile: sky130_oss_executable, oss_with_optional_gls, sky130_oss_experimental_complete, or commercial_signoff",
    ),
    pdk_path: str = typer.Option(
        "", "--pdk-path", help="Path to a custom PDK directory to use for this build"
    ),
    macro_manifest: str = typer.Option(
        "",
        "--macro-manifest",
        help="Path to generic hard-macro/IP manifest with LEF/GDS/lib/blackbox collateral",
    ),
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
        "uvm_lite", "--tb-fallback-template", help="TB fallback template: uvm_lite or classic"
    ),
    coverage_backend: str = typer.Option(
        "auto", "--coverage-backend", help="Coverage backend: auto, verilator, iverilog"
    ),
    coverage_fallback_policy: str = typer.Option(
        "fallback_oss",
        "--coverage-fallback-policy",
        help="Coverage fallback policy: fail_closed, fallback_oss, skip",
    ),
    coverage_profile: str = typer.Option(
        "balanced", "--coverage-profile", help="Coverage profile: balanced, aggressive, relaxed"
    ),
    no_golden_templates: bool = typer.Option(
        False,
        "--no-golden-templates",
        help="Disable golden template matching; default is disabled unless AGENTIC_DISABLE_GOLDEN_TEMPLATES=0",
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output machine-readable JSON results for CI/CD integration"
    ),
    recovery_attempts: int = typer.Option(
        5, "--recovery-attempts", "-R", min=0, max=10,
        help="Max self-healing recovery attempts during OpenLane hardening (timing/congestion/DRC auto-fix)",
    ),
):
    """Build a chip from natural language description with autonomous self-healing pipeline.

    The pipeline automatically detects and recovers from failures at every stage:
    - RTL errors → IncrementalFixEngine + ReAct loop
    - Synthesis errors → strategy switching (AREA→DELAY)  
    - Timing violations → clock relaxation + area expansion
    - Routing congestion → utilization reduction + die expansion
    - DRC/LVS errors → floorplan adjustment
    - OpenLane hardening → 5-stage recovery (clock→util→area→pipeline→RTL fix)

    Recovery is deterministic first (fast), then falls back to LLM self-reflection.
    """
    # Guard design name against accidental override by PDK or env logic
    _design_name = name.strip()
    if not _design_name:
        console.print("[error]Design name cannot be empty. Use --name <chip_name>[/error]")
        raise typer.Exit(1)

    _print_banner()
    _ensure_setup(skip_toolchain_prompt=skip_openlane)
    verify_license()
    check_dependencies(skip_openlane, skip_spice=skip_spice)

    # ── PDK Auto-Detection & Selection ─────────────────────────────────────
    from .config import PDKError, resolve_pdk, validate_pdk_installation

    if pdk_path:
        custom_path = os.path.abspath(os.path.expanduser(pdk_path))
        if not os.path.isdir(custom_path):
            console.print(f"[error]Custom PDK path not found: {custom_path}[/error]")
            raise typer.Exit(1)
        os.environ["PDK_ROOT"] = os.path.dirname(custom_path)
        if not pdk:
            pdk = os.path.basename(custom_path)

    macro_manifest_path = ""
    if macro_manifest:
        macro_manifest_path = os.path.abspath(os.path.expanduser(macro_manifest))
        if not os.path.isfile(macro_manifest_path):
            console.print(f"[error]Macro manifest not found: {macro_manifest_path}[/error]")
            raise typer.Exit(1)

    detected = detect_available_pdks()
    pdk_profile: str = ""

    if pdk:
        # User explicitly specified a PDK — validate or install/guide.
        pdk_key = pdk.strip().lower().replace("_", "-")
        pdk_key = PDK_INSTALL_ALIASES.get(pdk_key, pdk_key)
        try:
            _resolved_pdk, resolved_profile, _detected_root = resolve_pdk(
                requested_pdk=pdk_path or pdk,
                required=True,
            )
        except PDKError:
            cfg = PDK_INSTALL_CONFIGS.get(pdk_key)
            if cfg and cfg.get("proprietary"):
                _print_manual_pdk_steps(pdk_key, cfg)
                raise typer.Exit(1)
            if cfg:
                console.print(
                    f"[warning]PDK '{pdk}' is not installed. Starting automatic install...[/warning]"
                )
                install_pdk(
                    pdk_key,
                    version="",
                    check=False,
                    list_versions=False,
                    pdk_path="",
                    list_installed=False,
                    force=False,
                )
                detected = detect_available_pdks()
                _resolved_pdk, resolved_profile, _detected_root = resolve_pdk(
                    requested_pdk=pdk,
                    required=True,
                )
            else:
                console.print(
                    f"[error]PDK '{pdk}' not found.[/error]\n"
                    f"Available: {', '.join(sorted(detected.keys())) or 'none'}\n"
                    "Use [accent]agentic install-pdk <name>[/accent] or "
                    "[accent]--pdk-path /path/to/custom_pdk[/accent]."
                )
                raise typer.Exit(1)

        pdk_profile = resolved_profile.get("profile", pdk_key)
        ok, validation_messages = validate_pdk_installation(pdk_path or pdk)
        if not ok:
            console.print(
                Panel(
                    "\n".join(validation_messages),
                    title=f"PDK Validation Failed: {pdk}",
                    border_style="error",
                )
            )
            raise typer.Exit(1)
    elif detected:
        # Auto-detected — pick the first available unless there's a preference
        if len(detected) == 1:
            pdk_profile = next(iter(detected))
            # Already shown in header, no need to repeat
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
            for i, pdk_name in enumerate(pdk_options, 1):
                info = detected[pdk_name]
                table.add_row(
                    str(i),
                    pdk_name,
                    info["pdk"],
                    info.get("voltage_vdd", "?") + "V",
                    info.get("description", "-"),
                    info.get("root_path", "~")[:40],
                )
            console.print(table)

            prompt = f"Select PDK [1-{len(pdk_options)}] (or press Enter for {pdk_options[0]}): "
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
                "  • osu035    — Oklahoma State 350nm\n"
                "  • openfasoc — OpenFASOC/SKY130 analog generator flow\n"
                "  • lefdef175 — manual educational placeholder\n\n"
                "Install with AgentIC (recommended):\n"
                "  [accent]agentic install-pdk sky130[/accent]\n\n"
                "Use a custom PDK:\n"
                "  [accent]agentic build --name ... --pdk-path /path/to/pdk[/accent]\n\n"
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
                f"[accent]DRY RUN — Spec Validation[/accent]\nDesign: {_design_name}\nDescription: {desc}",
                title="🔍 Dry Run Mode",
            )
        )
        console.print("[info]Validating spec...[/info]")
        from .core.spec_generator import HardwareSpecGenerator
        from .agents.designer import get_designer_agent

        llm = get_llm()
        spec_gen = HardwareSpecGenerator(llm)
        spec, issues = spec_gen.generate(
            design_name=_design_name,
            description=desc,
            target_pdk=pdk_profile or "sky130",
        )
        if issues:
            console.print(f"[warning]Spec issues:[/warning]")
            for issue in issues:
                console.print(f"  - {issue}")
        else:
            console.print("[success]Spec looks valid[/success]")
        console.print(f"\n[info]To run full build:[/info]")
        console.print(f"  agentic build --name {_design_name} --desc '{desc}'")
        return

    from .orchestrator import BuildOrchestrator

    # Clean opencode-style header — use guarded name so PDK logic never overrides it
    console.print(f"\n[bold #d97757]AgentIC[/] • Building [warning]{_design_name}[/warning]")
    console.print(f"[dim]  {desc}[/dim]")
    console.print(
        f"[dim]  PDK: {pdk_profile} | {'Full Signoff' if full_signoff else 'RTL → GDSII'}[/dim]"
    )
    console.print()
    tb_gate_mode = tb_gate_mode.lower().strip()
    if tb_gate_mode not in {"strict", "relaxed"}:
        raise typer.BadParameter("--tb-gate-mode must be one of: strict, relaxed")
    tb_fallback_template = tb_fallback_template.lower().strip()
    if tb_fallback_template not in {"uvm_lite", "classic"}:
        raise typer.BadParameter("--tb-fallback-template must be one of: uvm_lite, classic")
    coverage_backend = coverage_backend.lower().strip()
    if coverage_backend not in {"auto", "verilator", "iverilog"}:
        raise typer.BadParameter("--coverage-backend must be one of: auto, verilator, iverilog")
    coverage_fallback_policy = coverage_fallback_policy.lower().strip()
    if coverage_fallback_policy not in {"fail_closed", "fallback_oss", "skip"}:
        raise typer.BadParameter(
            "--coverage-fallback-policy must be one of: fail_closed, fallback_oss, skip"
        )
    coverage_profile = coverage_profile.lower().strip()
    if coverage_profile not in {"balanced", "aggressive", "relaxed"}:
        raise typer.BadParameter("--coverage-profile must be one of: balanced, aggressive, relaxed")
    thinking_level = thinking_level.lower().strip()
    if thinking_level not in {"minimal", "normal", "verbose"}:
        raise typer.BadParameter("--thinking-level must be one of: minimal, normal, verbose")
    run_startup_diagnostics(strict=strict_gates)
    resolved_flow = resolve_flow_profile(flow_profile, pdk=pdk_profile)
    if flow_profile and flow_profile not in FLOW_PROFILES and resolved_flow.name == "sky130_oss_executable":
        console.print(
            f"[warning]Unknown or unavailable flow profile '{flow_profile}'. Using {resolved_flow.name}.[/warning]"
        )
    elif flow_profile and flow_profile != resolved_flow.name:
        console.print(
            f"[warning]Flow profile '{flow_profile}' is not available with current tool capabilities. Using {resolved_flow.name}.[/warning]"
        )
    console.print(
        f"[dim]  Flow: {resolved_flow.label} | readiness ceiling: {resolved_flow.readiness_ceiling}[/dim]"
    )
    if resolved_flow.blocked_extensions:
        console.print(
            "[dim]  Capability-gated: "
            + ", ".join(resolved_flow.blocked_extensions)
            + "[/dim]"
        )
    llm = get_llm(show_verbose=show_thinking)

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

    # Suppress crewai output during role LLM creation
    import io
    import sys as _sys

    _null = io.StringIO()
    _old_out = _sys.stdout
    _old_err = _sys.stderr
    _sys.stdout = _null
    _sys.stderr = _null

    for role in roles:
        cfg = get_role_llm_config(role)
        role_model = _format_model_for_provider(cfg["model"], cfg.get("base_url", ""))
        llm_kwargs = dict(
            model=role_model,
            api_key=cfg["api_key"],
            temperature=0.6,
            max_tokens=int(os.environ.get("LLM_MAX_TOKENS", 8192)),
            request_timeout=600,
        )
        if cfg.get("base_url"):
            llm_kwargs["base_url"] = cfg["base_url"]
        extra_body = _role_extra_body(cfg)
        if extra_body:
            llm_kwargs["extra_body"] = extra_body

        try:
            role_llms[role] = LLM(**llm_kwargs)
        except Exception:
            role_llms[role] = llm

    _sys.stdout = _old_out
    _sys.stderr = _old_err

    orchestrator = BuildOrchestrator(
        name=_design_name,
        desc=desc,
        llm=llm,
        role_llms=role_llms,
        max_retries=max_retries,
        verbose=show_thinking,
        skip_openlane=skip_openlane,
        skip_spice=skip_spice,
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
        flow_profile=resolved_flow.name,
    )
    orchestrator.hardening_recovery_attempts_max = recovery_attempts
    if macro_manifest_path:
        orchestrator.macro_manifest_path = macro_manifest_path
        orchestrator.artifacts["macro_manifest_path"] = macro_manifest_path

    orchestrator.run()

    if json_output:
        import json

        console.print_json(
            {
                "design": _design_name,
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


@app.command("spice")
def spice(
    layout_gds: str = typer.Argument(..., help="Existing GDS layout to extract and simulate"),
    output_dir: str = typer.Option("", "--output-dir", "-o", help="Directory for SPICE artifacts"),
    tech_file: str = typer.Option("", "--tech", help="Magic technology file"),
    pdk: str = typer.Option("sky130", "--pdk", help="Target PDK profile/name"),
    design_name: str = typer.Option("", "--name", "-n", help="Top-level design name"),
    deck: str = typer.Option("", "--deck", help="Optional existing ngspice deck to run"),
    timeout: int = typer.Option(900, "--timeout", help="Tool timeout in seconds"),
):
    """Extract parasitic SPICE from an existing GDS and run ngspice."""
    verify_license()
    gds_path = os.path.abspath(os.path.expanduser(layout_gds))
    if not os.path.exists(gds_path):
        console.print(f"[error]GDS not found: {gds_path}[/error]")
        raise typer.Exit(1)

    name = design_name or os.path.splitext(os.path.basename(gds_path))[0]
    out_dir = os.path.abspath(
        os.path.expanduser(output_dir or os.path.join(os.getcwd(), "spice", name))
    )
    os.makedirs(out_dir, exist_ok=True)

    console.print(f"[accent]Extracting SPICE[/accent] from {gds_path}")
    extraction = extract_spice_netlist(
        gds_path=gds_path,
        tech_file=os.path.abspath(os.path.expanduser(tech_file)) if tech_file else "",
        output_dir=out_dir,
        pdk=pdk,
        pdk_root=os.environ.get("PDK_ROOT", ""),
        design_name=name,
        timeout=timeout,
    )
    if not extraction.ok:
        console.print(
            Panel(
                "\n".join(extraction.errors),
                title="SPICE Extraction Failed",
                border_style="error",
            )
        )
        raise typer.Exit(1)

    deck_path_or_text = (
        os.path.abspath(os.path.expanduser(deck))
        if deck
        else build_basic_post_layout_deck(extraction.spice_netlist_path, name)
    )
    console.print(f"[accent]Running ngspice[/accent] in {out_dir}")
    result = run_ngspice(
        deck_path_or_text,
        out_dir,
        deck_name=f"{name}_post_layout.sp",
        timeout=timeout,
    )

    if result.get("ok"):
        measurements = result.get("measurements", {})
        summary = "\n".join(f"{k}: {v:.6g}" for k, v in measurements.items())
        if not summary:
            summary = "No .measure values found."
        console.print(
            Panel(
                f"Deck: {result.get('deck_path')}\n"
                f"Log: {result.get('log_path')}\n"
                f"Raw: {result.get('raw_path')}\n\n{summary}",
                title="ngspice PASS",
                border_style="success",
            )
        )
    else:
        console.print(
            Panel(
                "\n".join(result.get("errors", [])) or f"See log: {result.get('log_path')}",
                title="ngspice Failed",
                border_style="error",
            )
        )
        raise typer.Exit(1)


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
    rtl_file: str = typer.Option(
        ...,
        "--rtl",
        "-r",
        help="RTL Verilog source file (must be exact path, e.g., designs/my_design/src/my_design.v)",
    ),
    top: str = typer.Option(..., "--top", "-t", help="Top-level module name"),
    output_dir: str = typer.Option("./synth_out", "--out", "-o", help="Output directory"),
    pdk: str = typer.Option(
        "", "--pdk", help="PDK for area estimation (optional, e.g., sky130, gf180mcu)"
    ),
    clk_ns: float = typer.Option(
        10.0, "--clk", help="Clock constraint in ns (e.g., 10.0 = 100MHz)"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON result"),
):
    """Run Yosys RTL synthesis to produce a gate-level netlist.

    This provides independent synthesis (not via OpenLane) for:
    - Gate-level netlist for formal verification
    - Pre-route timing estimates
    - Cell count and area metrics

    Note: PDK is optional for synthesis (Yosys is PDK-agnostic).
    If not specified, uses sky130 defaults for area estimation.
    """
    verify_license()

    # Resolve PDK - use auto-detection or default (synth doesn't require PDK)
    from .config import resolve_pdk, PDK_ROOT, WORKSPACE_ROOT

    resolved_pdk, pdk_profile, detected_root = resolve_pdk(
        requested_pdk=pdk if pdk else None,
        design_path=rtl_file,
        required=False,  # Synth doesn't require PDK
    )
    effective_pdk = resolved_pdk
    effective_pdk_root = detected_root or PDK_ROOT

    console.print(
        Panel(
            f"[accent]Yosys Synthesis[/accent]\n"
            f"RTL: {rtl_file}\nTop: {top}\nPDK: {effective_pdk} (for area estimation)\nClock: {clk_ns}ns",
            title="🛠️ Synthesis",
        )
    )

    _output_dir = os.path.join(output_dir, f"synth_{top}")

    result = run_yosys_synth(
        rtl_files=[rtl_file],
        top_module=top,
        output_dir=_output_dir,
        pdk=effective_pdk,
        pdk_root=effective_pdk_root,
        clk_constraint=clk_ns,
    )

    if result.ok:
        console.print(f"  [success]✓ Synthesis PASS[/success]")
        console.print(
            f"  Cells: {result.cell_count:,} | DFFs: {result.dff_count:,} | LUTs: {result.lut_count:,}"
        )
        console.print(f"  Gate equiv: {result.gate_count:,.0f} | Area: {result.area_um2:.3f} um²")
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
                "pdk": effective_pdk,
                "warnings": result.warnings,
            }
        )


@app.command("sta")
def sta(
    netlist: str = typer.Option(
        ..., "--netlist", "-n", help="Gate-level Verilog netlist (must be exact path)"
    ),
    sdc: str = typer.Option(..., "--sdc", help="SDC timing constraints file (must be exact path)"),
    lib: str = typer.Option(
        ..., "--lib", "-l", help="Liberty timing library (.lib) file (must be exact path)"
    ),
    output_dir: str = typer.Option("./sta_out", "--out", "-o", help="Output directory"),
    corner: str = typer.Option("tt", "--corner", "-c", help="Corner: tt, ss, ff"),
    multi_corner: bool = typer.Option(False, "--multi-corner", help="Run all corners (ss/tt/ff)"),
    min_period_ns: float = typer.Option(10.0, "--period", help="Clock period constraint in ns"),
    pdk: str = typer.Option("", "--pdk", help="PDK name (auto-detected if omitted)"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Run OpenSTA static timing analysis (pre or post PnR).

    Production signoff requires multi-corner multi-mode STA:
    - SS (slow-slow): worst setup
    - TT (typical-typical): nominal
    - FF (fast-fast): worst hold
    """
    verify_license()

    # Resolve PDK - auto-detect or error
    from .config import resolve_pdk, PDK_ROOT

    resolved_pdk, pdk_profile, detected_root = resolve_pdk(
        requested_pdk=pdk if pdk else None,
        design_path=netlist,
        required=True,  # STA needs PDK for proper timing analysis
    )
    effective_pdk = resolved_pdk
    effective_pdk_root = detected_root or PDK_ROOT

    console.print(
        Panel(
            f"[accent]OpenSTA Static Timing Analysis[/accent]\n"
            f"Netlist: {netlist}\nPDK: {effective_pdk}\nCorner: {corner} | Multi-corner: {multi_corner}",
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
            pdk=effective_pdk,
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
        if not result.all_corners_pass:
            raise typer.Exit(1)
    else:
        result = run_opensta(
            netlist=netlist,
            sdc=sdc,
            lib_files=libs,
            output_dir=output_dir,
            corner=corner,
            min_period_ns=min_period_ns,
            pdk=effective_pdk,
        )
        if result.ok:
            console.print(f"  [success]✓ STA PASS [{corner}][/success]")
            console.print(f"  WNS={result.wns_setup:.3f}ns | TNS={result.tns_setup:.1f}ns")
            console.print(f"  Max frequency: {result.max_freq_mhz:.1f} MHz")
        else:
            console.print(f"  [error]✗ STA FAIL [{corner}][/error]")
            console.print(f"  WNS={result.wns_setup:.3f}ns | TNS={result.tns_setup:.1f}ns")
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
        if not result.ok:
            raise typer.Exit(1)


@app.command("dft")
def dft(
    rtl_file: str = typer.Option(
        ...,
        "--rtl",
        "-r",
        help="RTL Verilog source file (must be exact path, e.g., designs/my_design/src/my_design.v)",
    ),
    top: str = typer.Option(..., "--top", "-t", help="Top-level module name"),
    output_dir: str = typer.Option("./dft_out", "--out", "-o", help="Output directory"),
    scan_chains: int = typer.Option(4, "--chains", help="Number of scan chains"),
    testability: bool = typer.Option(
        False, "--testability", help="Run RTL testability analysis only"
    ),
    experimental: bool = typer.Option(
        False,
        "--experimental",
        help="Allow non-signoff experimental OSS DFT helper execution",
    ),
    pdk: str = typer.Option("", "--pdk", help="PDK name (auto-detected if omitted)"),
):
    """Run advisory DFT checks or experimental DFT helpers.

    Production scan insertion, ATPG, and MBIST require commercial or
    technology-specific tools. The default OSS flow does not claim these
    commands are fabrication signoff.
    """
    verify_license()

    # Resolve PDK - auto-detect or use default for DFT
    from .config import resolve_pdk, PDK_ROOT

    resolved_pdk, pdk_profile, detected_root = resolve_pdk(
        requested_pdk=pdk if pdk else None,
        design_path=rtl_file,
        required=False,  # DFT doesn't strictly require PDK
    )
    effective_pdk = resolved_pdk
    effective_pdk_root = detected_root or PDK_ROOT

    console.print(
        Panel(
            f"[accent]DFT Scan Insertion[/accent]\n"
            f"RTL: {rtl_file}\nTop: {top}\nChains: {scan_chains}\nPDK: {effective_pdk}",
            title="Design for Test (Capability-Gated)",
        )
    )

    if testability:
        ok, analysis = run_testability_analysis(rtl_file, output_dir)
        console.print(f"  DFFs: {analysis['dff_count']} | LUTs: {analysis['lut_count']}")
        console.print(f"  Estimated scan coverage: {analysis['estimated_scan_coverage']:.1f}%")
        if analysis["dft_issues"]:
            console.print(f"  [warning]DFT Issues:[/warning]")
            for issue in analysis["dft_issues"]:
                console.print(f"    - {issue}")
        return

    if not experimental:
        console.print(
            "[warning]Production DFT/ATPG is not available in the default OSS flow.[/warning]\n"
            "Use --testability for advisory analysis, configure a commercial DFT adapter, "
            "or pass --experimental for non-signoff helper experiments."
        )
        raise typer.Exit(1)

    os.environ["AGENTIC_EXPERIMENTAL_DFT"] = "1"

    result = run_scan_insertion(
        rtl_files=[rtl_file],
        top_module=top,
        output_dir=output_dir,
        scan_chain_count=scan_chains,
        pdk=effective_pdk,
        pdk_root=effective_pdk_root,
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
        raise typer.Exit(1)


@app.command("power")
def power(
    netlist: str = typer.Option(
        ..., "--netlist", "-n", help="Gate-level Verilog netlist (must be exact path)"
    ),
    output_dir: str = typer.Option("./power_out", "--out", "-o", help="Output directory"),
    vdd: float = typer.Option(
        0.0, "--vdd", help="Supply voltage in volts (auto-detected from PDK if 0)"
    ),
    freq_mhz: float = typer.Option(50.0, "--freq", help="Clock frequency in MHz"),
    spef: str = typer.Option("", "--spef", help="SPEF parasitic file for accurate power"),
    pdk: str = typer.Option("", "--pdk", help="PDK name (auto-detected if omitted)"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Run power analysis with dynamic/leakage breakdown and IR-drop check.

    Note: --vdd and --pdk are optional. If not provided, AgentIC will
    auto-detect the PDK and use its voltage settings.
    """
    verify_license()

    # Resolve PDK - auto-detect or error
    from .config import resolve_pdk, PDK_ROOT, get_pdk_tool_config

    resolved_pdk, pdk_profile, detected_root = resolve_pdk(
        requested_pdk=pdk if pdk else None,
        design_path=netlist,
        required=True,  # Power analysis needs PDK for voltage
    )
    effective_pdk = resolved_pdk
    effective_pdk_root = detected_root or PDK_ROOT

    # Auto-detect voltage from PDK if not specified
    tool_config = get_pdk_tool_config(effective_pdk)
    effective_vdd = vdd if vdd > 0 else float(tool_config.get("voltage_vdd", "1.8"))

    console.print(
        Panel(
            f"[accent]Power Analysis[/accent]\nVDD: {effective_vdd}V | Freq: {freq_mhz}MHz\nPDK: {effective_pdk}",
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
        vdd_voltage=effective_vdd,
        clock_frequency_mhz=freq_mhz,
        enable_ir_drop=True,
        pdk=effective_pdk,
        pdk_root=effective_pdk_root,
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
        console.print(f"  {icon} IR-drop: {ir.max_drop_mV:.4f}mV (worst: {ir.worst_node})")

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
    gds: str = typer.Option(..., "--gds", "-g", help="GDSII layout file (must be exact path)"),
    tech: str = typer.Option(
        "", "--tech", "-t", help="Magic technology file (.tech) (auto-located if omitted)"
    ),
    output_dir: str = typer.Option("./drc_out", "--out", "-o", help="Output directory"),
    pdk: str = typer.Option("", "--pdk", help="PDK name (auto-detected if omitted)"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Run independent Magic DRC on GDSII layout.

    PRODUCTION REQUIRED: 0 DRC violations before tapeout.

    Note: --tech and --pdk are optional. If not provided, AgentIC will
    auto-detect the PDK and locate the tech file.
    """
    verify_license()

    # Resolve PDK - auto-detect or error
    from .config import resolve_pdk, PDK_ROOT

    resolved_pdk, pdk_profile, detected_root = resolve_pdk(
        requested_pdk=pdk if pdk else None,
        design_path=gds,
        required=True,  # DRC needs PDK to find tech file
    )
    effective_pdk = resolved_pdk
    effective_pdk_root = detected_root or PDK_ROOT

    console.print(
        Panel(
            f"[accent]Magic DRC[/accent]\nGDS: {gds}\nPDK: {effective_pdk}",
            title="🔍 Physical Verification",
        )
    )
    os.makedirs(output_dir, exist_ok=True)

    result = run_magic_drc(
        gds_path=gds,
        tech_file=tech,  # Empty string triggers auto-detection
        output_dir=output_dir,
        pdk=effective_pdk,
        pdk_root=effective_pdk_root,
    )

    if result.ok:
        console.print(f"  [success]✓ DRC PASS — 0 violations[/success]")
    else:
        console.print(f"  [error]✗ DRC FAIL — {result.drc_violations} violations[/error]")
        if result.errors:
            for error in result.errors:
                console.print(f"    [error]{error}[/error]")

    console.print(f"  Runtime: {result.runtime_sec:.1f}s | Report: {result.drc_report_path}")
    if result.violations:
        for v in result.violations[:5]:
            console.print(f"  [error]  Layer {v.layer}: {v.message}[/error]")

    if json_output:
        console.print_json(
            {
                "ok": result.ok,
                "drc_violations": result.drc_violations,
                "violations": [{"layer": v.layer, "message": v.message} for v in result.violations],
                "runtime_sec": result.runtime_sec,
                "errors": result.errors,
            }
        )
    if not result.ok:
        raise typer.Exit(1)


@app.command("lvs")
def lvs(
    schematic: str = typer.Option(
        ..., "--sch", "-s", help="Schematic netlist (Verilog) (must be exact path)"
    ),
    layout_gds: str = typer.Option(
        ..., "--gds", "-g", help="Layout GDSII file (must be exact path)"
    ),
    tech_setup: str = typer.Option(
        "", "--setup", help="Netgen tech setup file (auto-located if omitted)"
    ),
    output_dir: str = typer.Option("./lvs_out", "--out", "-o", help="Output directory"),
    pdk: str = typer.Option("", "--pdk", help="PDK name (auto-detected if omitted)"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Run Netgen LVS (Layout vs Schematic) equivalence check.

    PRODUCTION REQUIRED: Schematic must be equivalent to layout.

    Note: --setup and --pdk are optional. If not provided, AgentIC will
    auto-detect the PDK and locate the setup file.
    """
    verify_license()

    # Resolve PDK - auto-detect or error
    from .config import resolve_pdk, PDK_ROOT

    resolved_pdk, pdk_profile, detected_root = resolve_pdk(
        requested_pdk=pdk if pdk else None,
        design_path=schematic,
        required=True,  # LVS needs PDK to find setup file
    )
    effective_pdk = resolved_pdk
    effective_pdk_root = detected_root or PDK_ROOT

    console.print(
        Panel(
            f"[accent]Netgen LVS[/accent]\nSchematic: {schematic}\nLayout: {layout_gds}\nPDK: {effective_pdk}",
            title="🔍 Layout vs Schematic",
        )
    )
    os.makedirs(output_dir, exist_ok=True)

    result = run_netgen_lvs(
        schematic_verilog=schematic,
        layout_gds=layout_gds,
        output_dir=output_dir,
        tech_setup=tech_setup,  # Empty string triggers auto-detection
        pdk=effective_pdk,
        pdk_root=effective_pdk_root,
    )

    if result.equivalent:
        console.print(f"  [success]✓ LVS PASS — Schematic equivalent to Layout[/success]")
    else:
        console.print(f"  [error]✗ LVS FAIL[/error]")
        console.print(
            f"  Net mismatches: {result.net_mismatches} | Pin mismatches: {result.pin_mismatches}"
        )
        console.print(f"  Unconnected nets: {result.unconnected_nets}")
        if result.errors:
            for error in result.errors:
                console.print(f"    [error]{error}[/error]")

    console.print(f"  Runtime: {result.runtime_sec:.1f}s | Report: {result.lvs_report_path}")

    if json_output:
        console.print_json(
            {
                "ok": result.ok,
                "equivalent": result.equivalent,
                "net_mismatches": result.net_mismatches,
                "pin_mismatches": result.pin_mismatches,
                "runtime_sec": result.runtime_sec,
                "errors": result.errors,
            }
        )
    if not result.ok:
        raise typer.Exit(1)


@app.command("report")
def report(
    design: str = typer.Option(..., "--design", "-d", help="Design name"),
    output_dir: str = typer.Option("./reports", "--out", "-o", help="Output directory"),
    pdk: str = typer.Option("sky130", "--pdk", help="PDK name"),
    run_dir: str = typer.Option("", "--run-dir", help="Path to OpenLane run directory"),
    format: str = typer.Option("all", "--format", "-f", help="Format: json, csv, md, all"),
):
    """Generate structured QOR signoff report from build data.

    Produces:
    - JSON: Machine-readable QOR data for CI/CD
    - CSV: Human-readable checklist
    - Markdown: Design review document
    """
    verify_license()
    os.makedirs(output_dir, exist_ok=True)
    import csv
    
    # Locate metrics.csv
    metrics_path = ""
    if run_dir and os.path.exists(os.path.join(run_dir, "reports", "metrics.csv")):
        metrics_path = os.path.join(run_dir, "reports", "metrics.csv")
    elif run_dir and os.path.exists(os.path.join(run_dir, "metrics.csv")):
        metrics_path = os.path.join(run_dir, "metrics.csv")
    elif os.path.exists(f"./runs/agentrun/reports/metrics.csv"):
        metrics_path = f"./runs/agentrun/reports/metrics.csv"
    else:
        # Fallback to OPENLANE_ROOT
        from .tools.vlsi_tools import OPENLANE_ROOT
        default_path = f"{OPENLANE_ROOT}/designs/{design}/runs/agentrun/reports/metrics.csv"
        if os.path.exists(default_path):
            metrics_path = default_path
            
    if not metrics_path or not os.path.exists(metrics_path):
        console.print(f"[error]Error: metrics.csv not found for design {design}. Specify --run-dir.[/error]")
        raise typer.Exit(1)
        
    synthesis_data = {}
    sta_data = {}
    power_data = {}
    physical_data = {}

    try:
        with open(metrics_path, "r") as f:
            reader = csv.DictReader(f)
            data = next(reader)
            
            synthesis_data = {
                "cell_count": int(float(data.get("TotalCells", data.get("synth_cell_count", 0)))),
                "dff_count": int(float(data.get("DFF", 0))),
                "area_um2": float(data.get("DIEAREA_mm^2", data.get("DieArea_mm^2", 0))) * 1e6,
            }
            
            crit_path = float(data.get("critical_path_ns", 10.0))
            max_freq = 1000.0 / crit_path if crit_path > 0 else 0.0
            sta_data = {
                "nom": {
                    "wns_setup_ns": float(data.get("wns", data.get("timing__wns", 0.0))),
                    "tns_setup_ns": float(data.get("tns", data.get("timing__tns", 0.0))),
                    "wns_hold_ns": 0.0,
                    "max_freq_mhz": max_freq,
                }
            }
            
            p_int = float(data.get("power_typical_internal_uW", 0.0))
            p_sw = float(data.get("power_typical_switching_uW", 0.0))
            p_leak = float(data.get("power_typical_leakage_uW", 0.0))
            total_uw = p_int + p_sw + p_leak
            if "power__total" in data:
                total_uw = float(data["power__total"]) * 1e6 # assuming W in newer OL
                
            chip_area_mm2 = synthesis_data["area_um2"] / 1e6
            power_density = (total_uw / 1000.0) / chip_area_mm2 if chip_area_mm2 > 0 else 0.0
            
            power_data = {
                "total_uW": total_uw,
                "dynamic_uW": p_int + p_sw,
                "leakage_uW": p_leak,
                "power_density_mW_per_mm2": power_density,
            }
            
            # Resolve actual GDS path
            actual_gds_path = ""
            if run_dir:
                actual_gds_path = os.path.join(run_dir, "results", "signoff", f"{design}.gds")
            else:
                from .tools.vlsi_tools import OPENLANE_ROOT
                actual_gds_path = os.path.join(OPENLANE_ROOT, "designs", design, "runs", "agentrun", "results", "signoff", f"{design}.gds")
                
            physical_data = {
                "drc_violations": int(float(data.get("Magic_violations", 0))),
                "lvs_errors": int(float(data.get("lvs_total_errors", 0))) if data.get("lvs_total_errors") != "-1" else 0,
                "antenna_violations": int(float(data.get("net_antenna_violations", 0))),
                "gds_path": actual_gds_path,
            }
            
    except Exception as e:
        console.print(f"[warning]Warning: Error parsing metrics.csv: {e}[/warning]")

    from .tools.signoff_reporter import generate_qor_report
    
    generate_qor_report(
        design_name=design,
        pdk=pdk,
        output_dir=output_dir,
        synthesis_data=synthesis_data,
        sta_data=sta_data,
        power_data=power_data,
        physical_data=physical_data,
    )

    json_path = os.path.join(output_dir, f"{design}_qor.json")
    csv_path = os.path.join(output_dir, f"{design}_checklist.csv")
    md_path = os.path.join(output_dir, f"{design}_signoff.md")

    console.print(
        Panel(
            f"[accent]Signoff Reports[/accent]\n"
            f"JSON: {json_path}\n"
            f"CSV:  {csv_path}\n"
            f"MD:   {md_path}",
            title="📊 QOR Reports",
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT — Anti-Traceback Wrapper
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[dim]AgentIC build safely aborted by user.[/dim]")
        raise SystemExit(0) from None
    except Exception as exc:
        import traceback as _tb

        _log_path = os.path.join(os.getcwd(), "agentic_error.log")
        try:
            with open(_log_path, "w") as _f:
                _f.write(_tb.format_exc())
        except Exception:
            pass

        console.print(
            Panel(
                f"[error]{type(exc).__name__}: {exc}[/error]\n\n"
                f"[dim]Full traceback written to:[/dim] {_log_path}\n"
                "[dim]For help:[/dim] https://github.com/Vickyrrrrrr/AgentIC/issues",
                title="[bold red]Unexpected Error[/bold red]",
                border_style="#d45851",
            )
        )
        raise SystemExit(1) from None
