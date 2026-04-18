"""
Provider Manager
==============

Manages multiple LLM providers with intelligent fallback.
Features:
- Provider priority configuration
- Rate limit tracking
- Automatic failover
- Cost optimization

Usage:
    manager = ProviderManager()

    # Get best available provider
    provider, model = manager.get_provider()

    # Execute with automatic fallback
    result = manager.execute_with_fallback(task)
"""

import time
import logging
from typing import Dict, List, Optional, Tuple, Any, Callable
from dataclasses import dataclass, field
from collections import deque
from threading import Lock
from enum import Enum

logger = logging.getLogger(__name__)


class ProviderStatus(Enum):
    """Provider availability status."""

    AVAILABLE = "available"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


@dataclass
class RateLimitConfig:
    """Rate limit configuration for a provider."""

    requests_per_minute: int = 60
    tokens_per_minute: int = 100000
    max_retries: int = 5
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    exponential_base: float = 2.0


@dataclass
class ProviderConfig:
    """Configuration for an LLM provider."""

    name: str
    priority: int = 100  # Lower = higher priority
    models: List[str] = field(default_factory=list)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    status: ProviderStatus = ProviderStatus.AVAILABLE
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    cost_per_1k_tokens: float = 0.005

    # Tracking
    last_used: float = 0.0
    consecutive_failures: int = 0
    total_requests: int = 0
    total_errors: int = 0
    rate_limited_until: float = 0.0


class ProviderManager:
    """
    Manages multiple LLM providers with intelligent routing.

    Features:
    - Automatic provider selection based on availability
    - Rate limit tracking per provider
    - Consecutive failure tracking
    - Cost-aware provider selection
    """

    # Default provider configurations
    DEFAULT_PROVIDERS = {
        "groq": ProviderConfig(
            name="groq",
            priority=1,
            models=["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],
            rate_limit=RateLimitConfig(
                requests_per_minute=30,
                tokens_per_minute=6000,
                max_retries=5,
                base_delay_seconds=2.0,
                max_delay_seconds=120.0,
            ),
            cost_per_1k_tokens=0.00059,
        ),
        "openai": ProviderConfig(
            name="openai",
            priority=2,
            models=["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
            rate_limit=RateLimitConfig(
                requests_per_minute=500,
                tokens_per_minute=150000,
                max_retries=5,
                base_delay_seconds=1.0,
                max_delay_seconds=60.0,
            ),
            cost_per_1k_tokens=0.005,
        ),
        "anthropic": ProviderConfig(
            name="anthropic",
            priority=3,
            models=["claude-3-5-sonnet-20240620", "claude-3-opus-20240229"],
            rate_limit=RateLimitConfig(
                requests_per_minute=50,
                tokens_per_minute=100000,
                max_retries=5,
                base_delay_seconds=1.5,
                max_delay_seconds=90.0,
            ),
            cost_per_1k_tokens=0.003,
        ),
        "openrouter": ProviderConfig(
            name="openrouter",
            priority=4,
            models=["anthropic/claude-3.5-sonnet", "openai/gpt-4o"],
            rate_limit=RateLimitConfig(
                requests_per_minute=60,
                tokens_per_minute=120000,
                max_retries=4,
                base_delay_seconds=2.0,
                max_delay_seconds=90.0,
            ),
            cost_per_1k_tokens=0.005,
        ),
    }

    def __init__(self, config: Optional[Dict[str, ProviderConfig]] = None):
        """
        Initialize provider manager.

        Args:
            config: Optional provider configurations (uses defaults if not provided)
        """
        self.providers: Dict[str, ProviderConfig] = config or dict(self.DEFAULT_PROVIDERS)

        # Rate limit tracking (sliding window)
        self.request_times: Dict[str, deque] = {name: deque(maxlen=1000) for name in self.providers}
        self.token_usage: Dict[str, deque] = {name: deque(maxlen=1000) for name in self.providers}

        self._lock = Lock()

        # Preferred provider (user-specified)
        self.preferred_provider: Optional[str] = None
        self.preferred_model: Optional[str] = None

    def set_preferred(self, provider: Optional[str] = None, model: Optional[str] = None):
        """Set preferred provider/model."""
        self.preferred_provider = provider
        self.preferred_model = model

    def get_provider(
        self,
        preferred: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Tuple[str, str]:
        """
        Get best available provider and model.

        Args:
            preferred: Preferred provider name
            model: Preferred model name

        Returns:
            Tuple of (provider_name, model_name)
        """
        with self._lock:
            # Check preferred first
            if preferred and preferred in self.providers:
                prov = self.providers[preferred]
                if self._is_available(prov):
                    return prov.name, model or prov.models[0]

            # Try in priority order
            sorted_providers = sorted(self.providers.items(), key=lambda x: x[1].priority)

            for name, prov in sorted_providers:
                if self._is_available(prov):
                    return name, model or prov.models[0]

            # All rate limited, return preferred or first
            if preferred and preferred in self.providers:
                return preferred, model or self.providers[preferred].models[0]

            return list(self.providers.items())[0]

    def get_fallback_chain(self, original: str, max_providers: int = 3) -> List[str]:
        """Get fallback provider chain."""
        chain = []

        if original in self.providers:
            orig_priority = self.providers[original].priority
            for name, prov in sorted(self.providers.items(), key=lambda x: x[1].priority):
                if name != original and len(chain) < max_providers:
                    chain.append(name)

        return chain

    def _is_available(self, provider: ProviderConfig) -> bool:
        """Check if provider has capacity."""
        if provider.status == ProviderStatus.UNAVAILABLE:
            return False
        if provider.status == ProviderStatus.RATE_LIMITED:
            if time.time() < provider.rate_limited_until:
                return False
            provider.status = ProviderStatus.AVAILABLE
            provider.rate_limited_until = 0.0

        # Check consecutive failures
        if provider.consecutive_failures >= 5:
            return False

        # Check sliding window rate limit
        now = time.time()
        window_start = now - 60  # 1 minute window

        # Clean old entries
        while (
            self.request_times[provider.name]
            and self.request_times[provider.name][0] < window_start
        ):
            self.request_times[provider.name].popleft()

        # Check rate limit
        recent_requests = len(self.request_times[provider.name])
        return recent_requests < provider.rate_limit.requests_per_minute

    def record_request(self, provider: str, tokens: int = 0, success: bool = True):
        """Record a request for rate tracking."""
        with self._lock:
            if provider not in self.providers:
                return

            prov = self.providers[provider]

            # Record request time
            self.request_times[provider].append(time.time())

            # Record token usage
            if tokens > 0:
                self.token_usage[provider].append((time.time(), tokens))

            # Update stats
            prov.total_requests += 1
            prov.last_used = time.time()

            if success:
                prov.consecutive_failures = 0
                prov.rate_limited_until = 0.0
                if prov.status == ProviderStatus.RATE_LIMITED:
                    prov.status = ProviderStatus.AVAILABLE
            else:
                prov.consecutive_failures += 1
                prov.total_errors += 1

    def record_rate_limit(self, provider: str, retry_after_seconds: Optional[float] = None):
        """Mark a provider as rate limited."""
        with self._lock:
            if provider in self.providers:
                prov = self.providers[provider]
                cooldown = retry_after_seconds or min(
                    prov.rate_limit.max_delay_seconds,
                    prov.rate_limit.base_delay_seconds
                    * (prov.rate_limit.exponential_base ** max(0, prov.consecutive_failures)),
                )
                prov.status = ProviderStatus.RATE_LIMITED
                prov.rate_limited_until = time.time() + cooldown
                prov.consecutive_failures += 1

    def reset_provider(self, provider: str):
        """Reset a provider's status."""
        with self._lock:
            if provider in self.providers:
                self.providers[provider].status = ProviderStatus.AVAILABLE
                self.providers[provider].consecutive_failures = 0
                self.providers[provider].rate_limited_until = 0.0

    def get_stats(self) -> Dict[str, Any]:
        """Get provider statistics."""
        stats = {}

        for name, prov in self.providers.items():
            now = time.time()
            window_start = now - 60

            # Count recent requests
            recent = sum(1 for t in self.request_times[name] if t >= window_start)
            recent_tokens = sum(tokens for ts, tokens in self.token_usage[name] if ts >= window_start)

            stats[name] = {
                "status": prov.status.value,
                "priority": prov.priority,
                "model": prov.models[0] if prov.models else None,
                "recent_requests_per_min": recent,
                "recent_tokens_per_min": recent_tokens,
                "total_requests": prov.total_requests,
                "total_errors": prov.total_errors,
                "error_rate": f"{(prov.total_errors / prov.total_requests * 100):.1f}%"
                if prov.total_requests > 0
                else "0%",
                "consecutive_failures": prov.consecutive_failures,
                "rate_limited_until": prov.rate_limited_until,
                "last_used": prov.last_used,
                "cost_per_1k_tokens": prov.cost_per_1k_tokens,
            }

        return stats

    def calculate_wait_time(self, provider: str) -> float:
        """Calculate minimum wait time before retry."""
        if provider not in self.providers:
            return 0

        prov = self.providers[provider]
        now = time.time()
        window_start = now - 60

        # Count requests in window
        requests = [t for t in self.request_times[provider] if t >= window_start]

        if len(requests) < prov.rate_limit.requests_per_minute:
            return 0

        # Calculate time until oldest request exits window
        oldest = min(requests)
        wait = 60 - (now - oldest)

        return max(0, wait)


def get_provider_manager() -> ProviderManager:
    """Get or create global provider manager instance."""
    global _provider_manager
    if _provider_manager is None:
        _provider_manager = ProviderManager()
    return _provider_manager


_global_provider_manager: Optional[ProviderManager] = None


from typing import Optional


def get_provider_manager() -> ProviderManager:
    """Get or create global provider manager."""
    global _global_provider_manager
    if _global_provider_manager is None:
        _global_provider_manager = ProviderManager()
    return _global_provider_manager
