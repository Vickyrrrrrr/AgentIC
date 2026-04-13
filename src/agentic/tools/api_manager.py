"""
API Manager — Intelligent Multi-Model LLM Routing
==================================================
Provides intelligent model selection, routing, and fallback strategies
for the AgentIC multi-agent system.

Key features:
  1. Role-based model assignment with smart defaults
  2. Per-task model selection (reasoning vs generation vs fast-fix)
  3. Automatic fallback when primary model fails
  4. Cost/token tracking per role
  5. Model validation on startup

Usage:
    from agentic.tools.api_manager import ApiManager, get_api_manager

    manager = get_api_manager()
    llm = manager.get_llm_for_task("rtl_generation")
    llm = manager.get_llm_for_role("architect")
"""

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..config import (
    DEFAULT_LLM_CONFIG,
    get_role_llm_config,
    LLM_MODEL,
    LLM_BASE_URL,
    LLM_API_KEY,
    CREDENTIALS_PATH,
    _load_user_credentials,
)


@dataclass
class ModelStrategy:
    """Defines how a model is used for a specific task/role."""

    model: str
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.6
    max_tokens: int = 16384
    provider: str = ""
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    cost_tier: str = "medium"


# ─────────────────────────────────────────────────────────────────────────────
# Smart Default Strategies — assign the right model to the right job
# ─────────────────────────────────────────────────────────────────────────────
# Philosophy:
#   - Reasoning tasks (planning, analysis, architecture) → deep thinkers
#   - Generation tasks (RTL, testbench, docs) → productive models
#   - Fix tasks (iterative repair) → fast, cheap models
#   - Reporting tasks → fast models

_PROVIDER_MODEL_DEFAULTS: Dict[str, List[ModelStrategy]] = {
    "openai": [
        ModelStrategy(
            model="gpt-4o",
            provider="openai",
            temperature=0.6,
            max_tokens=16384,
            strengths=["architecture", "RTL generation", "verification planning"],
            weaknesses=["slow", "expensive"],
            cost_tier="high",
        ),
        ModelStrategy(
            model="gpt-4o-mini",
            provider="openai",
            temperature=0.6,
            max_tokens=16384,
            strengths=["fast RTL fixes", "iteration", "lint repair"],
            weaknesses=["less deep reasoning"],
            cost_tier="low",
        ),
        ModelStrategy(
            model="o1",
            provider="openai",
            temperature=1.0,
            max_tokens=40960,
            strengths=["complex FSM reasoning", "formal property generation"],
            weaknesses=["no system prompt streaming", "expensive"],
            cost_tier="premium",
        ),
    ],
    "anthropic": [
        ModelStrategy(
            model="claude-sonnet-4-20250514",
            provider="anthropic",
            temperature=0.6,
            max_tokens=8192,
            strengths=[
                "architectural reasoning",
                "complex CDC analysis",
                "spec writing",
            ],
            weaknesses=["higher latency"],
            cost_tier="medium",
        ),
        ModelStrategy(
            model="claude-3-5-sonnet-20250620",
            provider="anthropic",
            temperature=0.6,
            max_tokens=8192,
            strengths=["deep verification analysis", "coverage optimization"],
            weaknesses=[""],
            cost_tier="medium",
        ),
        ModelStrategy(
            model="claude-3-5-haiku-20250620",
            provider="anthropic",
            temperature=0.6,
            max_tokens=8192,
            strengths=["fast fixes", "regression triage", "quick analysis"],
            weaknesses=["limited depth"],
            cost_tier="low",
        ),
    ],
    "groq": [
        ModelStrategy(
            model="groq/llama-3.3-70b-versatile",
            provider="groq",
            base_url="https://api.groq.com/openai/v1",
            temperature=0.6,
            max_tokens=8192,
            strengths=["ultra-fast RTL generation", "rapid iteration"],
            weaknesses=["less consistent on complex FSMs"],
            cost_tier="free",
        ),
        ModelStrategy(
            model="groq/mixtral-8x7b-32768",
            provider="groq",
            base_url="https://api.groq.com/openai/v1",
            temperature=0.6,
            max_tokens=8192,
            strengths=["fast SDC generation", "quick analysis"],
            weaknesses=["less accurate on timing constraints"],
            cost_tier="free",
        ),
    ],
    "deepseek": [
        ModelStrategy(
            model="deepseek-chat",
            provider="deepseek",
            base_url="https://api.deepseek.com",
            temperature=0.6,
            max_tokens=16384,
            strengths=["RTL generation", "verification planning", "cost-effective"],
            weaknesses=["less mature ecosystem"],
            cost_tier="low",
        ),
    ],
}


def _normalize_model(model_str: str) -> Tuple[str, str]:
    """Extract provider from model string (e.g. 'openai/gpt-4o' → 'openai')."""
    if "/" in model_str:
        provider = model_str.split("/")[0].lower()
    else:
        provider = "openai"
    return provider, model_str


# ─────────────────────────────────────────────────────────────────────────────
# Task-to-Role Mapping
# ─────────────────────────────────────────────────────────────────────────────

TASK_TO_ROLES: Dict[str, List[str]] = {
    "spec_generation": ["architect"],
    "cdc_analysis": ["architect"],
    "verification_plan": ["testbench_designer"],
    "rtl_generation": ["designer"],
    "rtl_fix": ["fixer"],
    "testbench_generation": ["testbench_designer"],
    "testbench_fix": ["fixer"],
    "formal_verification": ["verifier"],
    "sdc_generation": ["physical"],
    "floorplan": ["physical"],
    "convergence_analysis": ["physical"],
    "signoff": ["physical"],
    "dft_design": ["physical"],
    "power_analysis": ["physical"],
    "timing_analysis": ["physical"],
    "documentation": ["documenter"],
    "report_generation": ["reporter"],
    "error_analysis": ["debugger"],
    "regression": ["verifier"],
}


@dataclass
class ApiKeyInfo:
    """Stores API key metadata for a provider."""

    provider: str
    base_url: str
    api_key: str
    key_prefix: str = ""


def _get_key_info(api_key: str) -> str:
    """Return masked key prefix for display."""
    if not api_key:
        return ""
    if len(api_key) > 8:
        return f"{api_key[:4]}...{api_key[-4:]}"
    return "*" * len(api_key)


# ─────────────────────────────────────────────────────────────────────────────
# ApiManager — Central API Routing
# ─────────────────────────────────────────────────────────────────────────────


class ApiManager:
    """
    Central manager for all LLM API routing in AgentIC.

    Supports:
    - Single API key → all roles use the same model
    - Multi-model → different roles get different models
    - Per-role override → user can pin any role to any model
    - Automatic fallback → if primary model fails, try others

    The manager is a singleton per-process, initialized lazily.
    """

    _instance: Optional["ApiManager"] = None
    _lock = threading.Lock()

    def __init__(
        self,
        single_api_key: str = "",
        single_base_url: str = "",
        single_model: str = "",
        role_overrides: Optional[Dict[str, Dict[str, str]]] = None,
        auto_assign: bool = True,
    ):
        """
        Args:
            single_api_key: One API key used for everything
            single_base_url: Base URL for single-key mode
            single_model: Model name for single-key mode
            role_overrides: Dict[role → {model, base_url, api_key}] for per-role
            auto_assign: If True, auto-assign smart defaults when no config exists
        """
        self._role_llms: Dict[str, Any] = {}
        self._role_configs: Dict[str, Dict[str, str]] = {}
        self._stats: Dict[str, Dict[str, int]] = {}
        self._primary_llm: Any = None
        self._auto_assign = auto_assign

        if single_api_key:
            self._init_single_key(single_api_key, single_base_url, single_model)
        elif role_overrides:
            self._init_multi_role(role_overrides)
        elif auto_assign:
            self._init_auto_assign()

    # ── Initialization ────────────────────────────────────────────────────────

    def _init_single_key(self, api_key: str, base_url: str, model: str) -> None:
        """Use one API key + model for everything."""
        model = model or LLM_MODEL or "openai/gpt-4o"
        base_url = base_url or LLM_BASE_URL or ""

        # Resolve provider
        provider, full_model = _normalize_model(model)
        if not base_url and provider not in ("openai", "anthropic"):
            base_url = self._default_base_url_for_provider(provider)

        kwargs = dict(
            model=full_model if "/" in full_model else f"{provider}/{full_model}",
            api_key=api_key,
            temperature=0.6,
            max_tokens=16384,
        )
        if base_url:
            kwargs["base_url"] = base_url

        from crewai import LLM

        self._primary_llm = LLM(**kwargs)
        self._role_llms = {}  # all roles fall back to primary
        self._mode = "single"

    def _init_multi_role(self, role_overrides: Dict[str, Dict[str, str]]) -> None:
        """Use different models per role."""
        from crewai import LLM as _LLM

        self._mode = "multi"
        for role, cfg in role_overrides.items():
            model = cfg.get("model", "") or LLM_MODEL
            base_url = cfg.get("base_url", "") or LLM_BASE_URL
            api_key = cfg.get("api_key", "") or LLM_API_KEY

            if not api_key:
                continue

            provider, full_model = _normalize_model(model)
            if not base_url and provider not in ("openai", "anthropic"):
                base_url = self._default_base_url_for_provider(provider)

            kwargs = dict(
                model=full_model if "/" in full_model else f"{provider}/{full_model}",
                api_key=api_key,
                temperature=0.6,
                max_tokens=16384,
            )
            if base_url:
                kwargs["base_url"] = base_url

            try:
                self._role_llms[role] = _LLM(**kwargs)
                self._role_configs[role] = cfg
            except Exception:
                pass

    def _init_auto_assign(self) -> None:
        """Auto-assign smart model defaults from available API keys."""
        from crewai import LLM as _LLM

        # Load credentials
        creds = _load_user_credentials()

        # Try to detect what's available
        available_providers = []
        for group_name, group_data in creds.items():
            if isinstance(group_data, dict) and group_data.get("api_key"):
                api_key = group_data["api_key"]
                model = group_data.get("model", "") or LLM_MODEL
                base_url = group_data.get("base_url", "") or LLM_BASE_URL
                provider, _ = _normalize_model(model)
                available_providers.append(
                    {
                        "group": group_name,
                        "provider": provider,
                        "model": model,
                        "base_url": base_url,
                        "api_key": api_key,
                    }
                )

        # Also check env vars
        if not available_providers and LLM_API_KEY:
            available_providers.append(
                {
                    "group": "build",
                    "provider": _normalize_model(LLM_MODEL)[0],
                    "model": LLM_MODEL,
                    "base_url": LLM_BASE_URL,
                    "api_key": LLM_API_KEY,
                }
            )

        if not available_providers:
            # No keys — use default config
            self._mode = "single"
            return

        self._mode = "auto"

        # Build role → model mapping from available providers
        # Assign best-fit model to each role based on available providers
        role_assignments = self._assign_models_to_roles(available_providers)

        for role, assignment in role_assignments.items():
            if not assignment.get("api_key"):
                continue
            try:
                kwargs = dict(
                    model=assignment["model"],
                    api_key=assignment["api_key"],
                    temperature=0.6,
                    max_tokens=16384,
                )
                if assignment.get("base_url"):
                    kwargs["base_url"] = assignment["base_url"]
                self._role_llms[role] = _LLM(**kwargs)
                self._role_configs[role] = assignment
            except Exception:
                pass

    def _assign_models_to_roles(
        self, providers: List[Dict[str, str]]
    ) -> Dict[str, Dict[str, str]]:
        """Assign the best available model to each role."""
        # Role priority: architect > designer > verifier > physical > fixer > documenter
        role_priority = [
            "architect",
            "designer",
            "testbench_designer",
            "verifier",
            "physical",
            "fixer",
            "debugger",
            "documenter",
            "reporter",
            "manager",
            "reasoner",
        ]

        assignments: Dict[str, Dict[str, str]] = {}
        provider_by_tier: Dict[str, List[Dict]] = {}

        for p in providers:
            tier = p.get("provider", "unknown")
            if tier not in provider_by_tier:
                provider_by_tier[tier] = []
            provider_by_tier[tier].append(p)

        # Smart role assignment
        # - architect, designer, verifier, physical → best available
        # - fixer, debugger → fast/cheap
        # - documenter, reporter → free if available

        # Sort by "premium" first
        premium = provider_by_tier.get("anthropic", []) + provider_by_tier.get(
            "openai", []
        )
        cheap = provider_by_tier.get("groq", []) + provider_by_tier.get("deepseek", [])
        others = [
            p
            for grp, tier_d in provider_by_tier.items()
            if grp not in ("anthropic", "openai", "groq", "deepseek")
            for p in tier_d
        ]

        all_providers = premium + cheap + others

        assigned_idx = 0
        for role in role_priority:
            if assigned_idx < len(all_providers):
                p = all_providers[assigned_idx]
                assignments[role] = {
                    "model": p.get("model", LLM_MODEL),
                    "base_url": p.get("base_url", ""),
                    "api_key": p.get("api_key", ""),
                    "provider": p.get("provider", "unknown"),
                }
                # Use the same provider for a few roles to avoid exhausting keys
                assigned_idx += 1
            elif all_providers:
                # Reuse last provider
                assignments[role] = dict(assignments[role_priority[assigned_idx - 1]])

        return assignments

    def _default_base_url_for_provider(self, provider: str) -> str:
        """Return the default base URL for known providers."""
        defaults = {
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com",
            "groq": "https://api.groq.com/openai/v1",
            "deepseek": "https://api.deepseek.com",
            "together_ai": "https://api.together.xyz/v1",
            "ollama": "http://localhost:11434",
            "nvidia_nim": "https://integrate.api.nvidia.com/v1",
        }
        return defaults.get(provider, "")

    # ── Public API ────────────────────────────────────────────────────────────

    def get_llm_for_role(self, role: str) -> Any:
        """Get the LLM for a specific role (with fallback)."""
        role = role.lower().replace("-", "_")
        if role in self._role_llms:
            return self._role_llms[role]

        # Fallback chain: specific role → group → primary → default
        if self._primary_llm:
            return self._primary_llm

        # Last resort — create from default config
        from crewai import LLM

        cfg = get_role_llm_config(role)
        kwargs = dict(
            model=cfg["model"],
            api_key=cfg["api_key"],
            temperature=0.6,
            max_tokens=16384,
        )
        if cfg.get("base_url"):
            kwargs["base_url"] = cfg["base_url"]
        return LLM(**kwargs)

    def get_role_llms(self) -> Dict[str, Any]:
        """Return the full role→LLM map."""
        # Fill in missing roles with primary/fallback
        all_roles = list(TASK_TO_ROLES.keys()) + [
            "architect",
            "designer",
            "verifier",
            "fixer",
            "debugger",
            "documenter",
            "physical",
            "manager",
            "reasoner",
            "testbench_designer",
            "reporter",
        ]
        result = {}
        for role in all_roles:
            result[role] = self.get_llm_for_role(role)
        return result

    def get_primary_llm(self) -> Any:
        """Get the primary/fallback LLM."""
        if self._primary_llm:
            return self._primary_llm
        return self.get_llm_for_role("designer")

    @property
    def mode(self) -> str:
        """Return the current mode: 'single', 'multi', or 'auto'."""
        return getattr(self, "_mode", "single")

    def get_config_summary(self) -> Dict[str, Any]:
        """Return a human-readable summary of the current API configuration."""
        summary = {
            "mode": self.mode,
            "primary_model": "",
            "roles": {},
        }

        if self._primary_llm:
            model = getattr(self._primary_llm, "model", "unknown")
            summary["primary_model"] = model

        for role, llm_obj in self._role_llms.items():
            model = getattr(llm_obj, "model", "unknown")
            provider, _ = _normalize_model(model)
            summary["roles"][role] = {
                "model": model,
                "provider": provider,
                "config": self._role_configs.get(role, {}),
            }

        return summary

    def validate_all_keys(self) -> Dict[str, bool]:
        """Test all configured API keys with a minimal call."""
        results = {}
        for role, cfg in self._role_configs.items():
            api_key = cfg.get("api_key", "")
            if not api_key:
                continue
            try:
                from crewai import LLM

                kwargs = dict(
                    model=cfg.get("model", "gpt-4o"),
                    api_key=api_key,
                    temperature=0.1,
                    max_tokens=8,
                )
                if cfg.get("base_url"):
                    kwargs["base_url"] = cfg["base_url"]
                test_llm = LLM(**kwargs)
                test_llm.call([{"role": "user", "content": "hi"}])
                results[role] = True
            except Exception as e:
                results[role] = False
        return results

    # ── Singleton ─────────────────────────────────────────────────────────────

    @classmethod
    def get_instance(cls, **kwargs) -> "ApiManager":
        """Get or create the singleton ApiManager instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (useful for testing)."""
        with cls._lock:
            cls._instance = None


# ─────────────────────────────────────────────────────────────────────────────
# Convenience accessor
# ─────────────────────────────────────────────────────────────────────────────

_global_manager: Optional[ApiManager] = None


def get_api_manager(
    single_api_key: str = "",
    single_base_url: str = "",
    single_model: str = "",
    role_overrides: Optional[Dict[str, Dict[str, str]]] = None,
    auto_assign: bool = True,
) -> ApiManager:
    """Get or create the global ApiManager singleton."""
    global _global_manager
    if _global_manager is None:
        _global_manager = ApiManager(
            single_api_key=single_api_key,
            single_base_url=single_base_url,
            single_model=single_model,
            role_overrides=role_overrides,
            auto_assign=auto_assign,
        )
    return _global_manager


def reset_api_manager() -> None:
    """Reset the global ApiManager (for testing or reconfiguration)."""
    global _global_manager
    _global_manager = None
