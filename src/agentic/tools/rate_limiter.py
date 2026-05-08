"""
Rate Limiter — LLM API Rate-Limit Handling with Per-Provider Strategies
======================================================================
Provides automatic retry with exponential backoff when LLM providers
return 429 (Too Many Requests) or 503 (Service Unavailable) errors.

Supported strategies:
  - openai:     60 req/min (tier 1), exponential backoff up to 60s
  - anthropic: 50 req/min, backoff with Claude-specific headers
  - generic:       30 req/min (free tier), aggressive backoff
  - openrouter: varies by model, conservative backoff
  - ollama:     local — no rate limits, fast retries
  - generic_nim: varies by model, conservative backoff
  - together_ai: 20 req/min free, conservative backoff
  - azure:      configurable, exponential backoff
  - gemini:     15 req/min, conservative backoff
  - generic:    fallback for any OpenAI-compatible provider

Usage:
    from agentic.tools.rate_limiter import rate_limited_call

    result = rate_limited_call(
        crew.kickoff,
        {"description": "...", "expected_output": "..."},
        model_name="gpt-4o",
        base_url="https://api.openai.com/v1",
    )
"""

import random
import time
import threading
import functools
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# Provider Strategies
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RateLimitStrategy:
    """Per-provider rate-limit retry configuration."""

    name: str
    base_delay_s: float
    max_delay_s: float
    max_retries: int
    jitter_range_s: Tuple[float, float] = (0.5, 1.5)
    respect_retry_after: bool = True
    exponential_base: float = 2.0


@dataclass
class GlobalRateLimitConfig:
    """Process-wide request queue limits shared across all providers."""

    requests_per_minute: int = int(os.environ.get("AGENTIC_GLOBAL_LLM_RPM", "120"))
    max_concurrent: int = int(os.environ.get("AGENTIC_GLOBAL_LLM_CONCURRENCY", "4"))
    token_warning_ratio: float = float(os.environ.get("AGENTIC_TOKEN_WARNING_RATIO", "0.85"))


_PROVIDER_STRATEGIES: Dict[str, RateLimitStrategy] = {
    "openai": RateLimitStrategy(
        name="openai",
        base_delay_s=1.0,
        max_delay_s=60.0,
        max_retries=5,
        exponential_base=2.0,
    ),
    "anthropic": RateLimitStrategy(
        name="anthropic",
        base_delay_s=1.5,
        max_delay_s=90.0,
        max_retries=5,
        exponential_base=2.0,
        respect_retry_after=True,
    ),
    "generic": RateLimitStrategy(
        name="generic",
        base_delay_s=2.0,
        max_delay_s=120.0,
        max_retries=5,
        exponential_base=2.5,
    ),
    "openrouter": RateLimitStrategy(
        name="openrouter",
        base_delay_s=2.0,
        max_delay_s=90.0,
        max_retries=4,
        exponential_base=2.0,
    ),
    "ollama": RateLimitStrategy(
        name="ollama",
        base_delay_s=0.1,
        max_delay_s=2.0,
        max_retries=3,
        exponential_base=1.5,
    ),
    "generic_nim": RateLimitStrategy(
        name="generic_nim",
        base_delay_s=2.0,
        max_delay_s=60.0,
        max_retries=4,
        exponential_base=2.0,
    ),
    "together_ai": RateLimitStrategy(
        name="together_ai",
        base_delay_s=3.0,
        max_delay_s=120.0,
        max_retries=4,
        exponential_base=2.0,
    ),
    "azure": RateLimitStrategy(
        name="azure",
        base_delay_s=1.0,
        max_delay_s=30.0,
        max_retries=4,
        exponential_base=2.0,
    ),
    "gemini": RateLimitStrategy(
        name="gemini",
        base_delay_s=3.0,
        max_delay_s=120.0,
        max_retries=3,
        exponential_base=2.0,
    ),
    "z_ai": RateLimitStrategy(
        name="z_ai",
        base_delay_s=3.0,
        max_delay_s=300.0,
        max_retries=8,
        exponential_base=2.0,
        respect_retry_after=True,
    ),
    "generic": RateLimitStrategy(
        name="generic",
        base_delay_s=2.0,
        max_delay_s=60.0,
        max_retries=4,
        exponential_base=2.0,
    ),
}


def _detect_provider(base_url: str, model: str) -> str:
    """Detect the provider from base URL or model prefix."""
    if not base_url and not model:
        return "generic"

    url_lower = (base_url or "").lower()
    model_lower = (model or "").lower()

    if not url_lower:
        # Infer from model prefix
        for prefix in ("openai/", "gpt-", "o1-", "o3-"):
            if model_lower.startswith(prefix):
                return "openai"
        if model_lower.startswith(("claude/", "anthropic/")):
            return "anthropic"
        if model_lower.startswith("generic/"):
            return "generic"
        if model_lower.startswith("generic_nim/"):
            return "generic_nim"
        if model_lower.startswith("ollama/"):
            return "ollama"
        if model_lower.startswith("gemini/"):
            return "gemini"
        if model_lower.startswith("together_ai/"):
            return "together_ai"
        if model_lower.startswith("openrouter/"):
            return "openrouter"
        return "generic"

    if "api.openai.com" in url_lower:
        return "openai"
    if "api.anthropic.com" in url_lower:
        return "anthropic"
    if "generic.com" in url_lower:
        return "generic"
    if "openrouter.ai" in url_lower:
        return "openrouter"
    if "localhost" in url_lower or "ollama" in url_lower:
        return "ollama"
    if "generic.com" in url_lower:
        return "generic_nim"
    if "together.xyz" in url_lower:
        return "together_ai"
    if "azure" in url_lower or "cognitive.microsoft" in url_lower:
        return "azure"
    if "generativelanguage" in url_lower or "gemini" in url_lower:
        return "gemini"
    if "z.ai" in url_lower or "bigmodel.cn" in url_lower:
        return "z_ai"
    return "generic"


def _parse_retry_after(retry_after_header: Optional[str]) -> Optional[float]:
    """Parse Retry-After header value (seconds or HTTP date)."""
    if not retry_after_header:
        return None
    try:
        return float(retry_after_header)
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Per-Provider Thread-Safe Global Rate Limiters
# ─────────────────────────────────────────────────────────────────────────────


class _ProviderRateLimiter:
    """Thread-safe per-provider token/request bucket."""

    def __init__(self, strategy: RateLimitStrategy):
        self.strategy = strategy
        self._lock = threading.Lock()
        self._min_interval: float = 0.0
        self._last_call: float = 0.0

    def acquire(self) -> None:
        """Block until a slot is available (thread-safe)."""
        with self._lock:
            now = time.monotonic()
            wait_time = self._min_interval - (now - self._last_call)
            if wait_time > 0:
                time.sleep(wait_time)
            self._last_call = time.monotonic()

    def set_rate(self, requests_per_minute: int) -> None:
        """Override the rate limit dynamically."""
        with self._lock:
            self._min_interval = 60.0 / max(1, requests_per_minute)


class _GlobalRequestQueue:
    """Small process-wide queue to prevent aggregate LLM provider overload."""

    def __init__(self, config: Optional[GlobalRateLimitConfig] = None):
        self.config = config or GlobalRateLimitConfig()
        self._semaphore = threading.BoundedSemaphore(max(1, self.config.max_concurrent))
        self._lock = threading.Lock()
        self._last_call: float = 0.0

    def acquire(self) -> None:
        self._semaphore.acquire()
        with self._lock:
            min_interval = 60.0 / max(1, self.config.requests_per_minute)
            now = time.monotonic()
            wait_time = min_interval - (now - self._last_call)
            if wait_time > 0:
                time.sleep(wait_time)
            self._last_call = time.monotonic()

    def release(self) -> None:
        self._semaphore.release()

    def update(self, requests_per_minute: Optional[int] = None, max_concurrent: Optional[int] = None) -> None:
        if requests_per_minute is not None:
            self.config.requests_per_minute = max(1, requests_per_minute)
        if max_concurrent is not None and max_concurrent != self.config.max_concurrent:
            self.config.max_concurrent = max(1, max_concurrent)
            self._semaphore = threading.BoundedSemaphore(self.config.max_concurrent)


# Global limiter registry (one per detected provider)
_global_limiters: Dict[str, _ProviderRateLimiter] = {}
_limiter_lock = threading.Lock()
_global_request_queue = _GlobalRequestQueue()


def _get_limiter(base_url: str, model: str) -> _ProviderRateLimiter:
    provider = _detect_provider(base_url, model)
    strategy = _PROVIDER_STRATEGIES.get(provider, _PROVIDER_STRATEGIES["generic"])
    with _limiter_lock:
        if provider not in _global_limiters:
            _global_limiters[provider] = _ProviderRateLimiter(strategy)
        return _global_limiters[provider]


# ─────────────────────────────────────────────────────────────────────────────
# Rate-Limited API Call
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RateLimitResult:
    """Result from a rate-limited call."""

    ok: bool
    result: Any
    attempts: int
    total_wait_s: float
    errors: list = field(default_factory=list)
    rate_limited: bool = False
    provider: str = ""


def rate_limited_call(
    callable_fn: Callable[..., Any],
    *args: Any,
    model: str = "",
    base_url: str = "",
    max_retries: Optional[int] = None,
    estimated_tokens: int = 0,
    token_budget: int = 0,
    on_warning: Optional[Callable[[str], None]] = None,
    on_retry: Optional[Callable[[int, str, Exception], None]] = None,
    **kwargs: Any,
) -> RateLimitResult:
    """Call an LLM API with automatic rate-limit retry.

    Args:
        callable_fn: The function to call (e.g. crew.kickoff)
        *args: Positional arguments for callable_fn
        model: Model name (used for provider detection)
        base_url: API base URL (used for provider detection)
        max_retries: Override per-provider max retries
        estimated_tokens: Estimated total tokens for this request.
        token_budget: Token budget for warning before hitting limits.
        on_warning: Callback for proactive budget warnings.
        on_retry: Callback invoked on each retry with (attempt, error_type, exc)
        **kwargs: Keyword arguments for callable_fn

    Returns:
        RateLimitResult with result, attempt count, total wait time, and errors
    """
    provider = _detect_provider(base_url, model)
    strategy = _PROVIDER_STRATEGIES.get(provider, _PROVIDER_STRATEGIES["generic"])
    effective_max_retries = max_retries if max_retries is not None else strategy.max_retries

    limiter = _get_limiter(base_url, model)
    total_wait = 0.0
    errors = []

    if estimated_tokens and token_budget:
        ratio = estimated_tokens / max(1, token_budget)
        if ratio >= _global_request_queue.config.token_warning_ratio and on_warning:
            try:
                on_warning(
                    f"Estimated token use ({estimated_tokens}) is {ratio:.0%} of budget ({token_budget})."
                )
            except Exception:
                pass

    for attempt in range(1, effective_max_retries + 2):
        wait_time = 0.0

        try:
            # Process-wide queue first, then provider-specific pacing.
            _global_request_queue.acquire()
            try:
                limiter.acquire()
                result = callable_fn(*args, **kwargs)
            finally:
                _global_request_queue.release()
            return RateLimitResult(
                ok=True,
                result=result,
                attempts=attempt,
                total_wait_s=total_wait,
                provider=provider,
            )

        except Exception as exc:  # broad: LLM errors come in many shapes
            error_str = str(exc)
            error_type = type(exc).__name__

            # ── Rate limit detection ──────────────────────────────────────
            is_rate_limit = False
            http_code = 0

            # Try to extract HTTP status code
            if hasattr(exc, "status_code"):
                http_code = getattr(exc, "status_code", 0)
            elif hasattr(exc, "response"):
                resp = getattr(exc, "response", None)
                if resp and hasattr(resp, "status_code"):
                    http_code = resp.status_code

            # Check exception message for rate limit indicators
            if http_code in (429, 503) or any(
                k in error_str.lower()
                for k in (
                    "rate limit",
                    "rate_limit",
                    "too many requests",
                    "throttl",
                    "over limit",
                )
            ):
                is_rate_limit = True

                # For z_ai or generic, if it's a connection/timeout error, retry anyway
                is_retryable = any(k in error_str.lower() for k in ["timeout", "connection", "disconnected", "reset", "peer", "exhausted", "limit"])
                if is_retryable and attempt <= effective_max_retries:
                    is_rate_limit = True  # Treat as retryable
                else:
                    # Non-retryable error — surface immediately
                    err_msg = f"{error_type}: {error_str}"
                    # LOG TO CONSOLE so the user sees the real reason
                    from rich.console import Console
                    Console(stderr=True).print(f"[bold red]LLM Provider Error ({provider}):[/bold red] {err_msg}")
                    
                    return RateLimitResult(
                        ok=False,
                        result=None,
                        attempts=attempt,
                        total_wait_s=total_wait,
                        errors=[err_msg],
                        provider=provider,
                    )

            errors.append(f"[attempt {attempt}] {error_type}: {error_str}")

            if attempt > effective_max_retries:
                return RateLimitResult(
                    ok=False,
                    result=None,
                    attempts=attempt,
                    total_wait_s=total_wait,
                    errors=errors,
                    rate_limited=True,
                    provider=provider,
                )

            # ── Compute backoff delay ────────────────────────────────────
            if strategy.respect_retry_after:
                retry_after = None
                if hasattr(exc, "response") and hasattr(exc.response, "headers"):
                    headers = dict(exc.response.headers)
                    retry_after = _parse_retry_after(headers.get("Retry-After"))
                if retry_after:
                    wait_time = retry_after
                else:
                    wait_time = min(
                        strategy.base_delay_s * (strategy.exponential_base ** (attempt - 1)),
                        strategy.max_delay_s,
                    )
            else:
                wait_time = min(
                    strategy.base_delay_s * (strategy.exponential_base ** (attempt - 1)),
                    strategy.max_delay_s,
                )

            # Add jitter
            jitter = random.uniform(*strategy.jitter_range_s)
            wait_time = wait_time * jitter

            total_wait += wait_time

            # Notify via callback
            if on_retry:
                try:
                    on_retry(attempt, provider, exc)
                except Exception:
                    pass

            time.sleep(wait_time)

    # Should not reach here
    return RateLimitResult(
        ok=False,
        result=None,
        attempts=effective_max_retries + 1,
        total_wait_s=total_wait,
        errors=errors,
        rate_limited=True,
        provider=provider,
    )


def rate_limited_crew_kickoff(
    crew_instance: Any,
    task_overrides: Optional[Dict[str, Any]] = None,
    model: str = "",
    base_url: str = "",
) -> RateLimitResult:
    """Run a CrewAI crew with automatic rate-limit retry.

    Args:
        crew_instance: A crewai.Crew instance
        task_overrides: Optional dict of {task_description: "..."} to override task descs
        model: Model name for provider detection
        base_url: API base URL for provider detection

    Returns:
        RateLimitResult with crew.kickoff() result or error
    """

    def _kickoff():
        return crew_instance.kickoff()

    return rate_limited_call(
        _kickoff,
        model=model,
        base_url=base_url,
    )


def set_provider_rate(requests_per_minute: int, base_url: str = "", model: str = "") -> None:
    """Override the per-call rate limit for a provider."""
    limiter = _get_limiter(base_url, model)
    limiter.set_rate(requests_per_minute)


def set_global_rate_limit(
    requests_per_minute: Optional[int] = None,
    max_concurrent: Optional[int] = None,
) -> None:
    """Override process-wide LLM request pacing."""
    _global_request_queue.update(
        requests_per_minute=requests_per_minute,
        max_concurrent=max_concurrent,
    )


def get_provider_stats() -> Dict[str, Dict[str, Any]]:
    """Return current state of all tracked provider limiters."""
    with _limiter_lock:
        stats = {
            provider: {
                "strategy": lim.strategy.name,
                "base_delay_s": lim.strategy.base_delay_s,
                "max_delay_s": lim.strategy.max_delay_s,
                "max_retries": lim.strategy.max_retries,
                "last_call": lim._last_call,
            }
            for provider, lim in _global_limiters.items()
        }
    stats["_global"] = {
        "requests_per_minute": _global_request_queue.config.requests_per_minute,
        "max_concurrent": _global_request_queue.config.max_concurrent,
        "token_warning_ratio": _global_request_queue.config.token_warning_ratio,
    }
    return stats
