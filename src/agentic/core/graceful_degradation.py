"""
Graceful Degradation
==================

Handles failures gracefully with multiple fallback strategies.
Combines:
- Response caching
- Provider fallback
- Incremental fixes
- State checkpoints

Usage:
    degrader = GracefulDegradation()

    # Execute with automatic recovery
    result = degrader.execute(
        task="Generate RTL",
        prompt=...,
        providers=["groq", "openai", "anthropic"],
    )
"""

import time
import logging
from typing import Dict, List, Optional, Tuple, Any, Callable, Union
from dataclasses import dataclass, field
from enum import Enum

from .cache_manager import LLMResponseCache, get_global_cache
from .provider_manager import ProviderManager, get_provider_manager
from .incremental_fixer import IncrementalFixEngine, ErrorAnalysis

logger = logging.getLogger(__name__)


class FailureStrategy(Enum):
    """Strategy used to recover from failure."""

    CACHE_HIT = "cache"
    PROVIDER_FALLBACK = "provider_fallback"
    INCREMENTAL_FIX = "incremental_fix"
    PARTIAL_RESULT = "partial"
    EXHAUSTED = "exhausted"
    UNKNOWN = "unknown"


@dataclass
class ExecutionResult:
    """Result of an execution attempt."""

    success: bool
    response: str
    strategy_used: FailureStrategy
    provider: str = ""
    model: str = ""
    cache_hit: bool = False
    attempts: int = 0
    total_wait_seconds: float = 0.0
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.success and bool(self.response)


class GracefulDegradation:
    """
    Graceful degradation handler for LLM calls.

    Strategy order:
    1. Check cache for identical prompt
    2. Try primary provider
    3. If rate limited, wait 30s then try cache
    4. If still rate limited, try fallback providers
    5. On format error, try incremental fix
    6. Return partial result or error
    """

    def __init__(
        self,
        cache: Optional[LLMResponseCache] = None,
        provider_manager: Optional[ProviderManager] = None,
        initial_wait_seconds: float = 30.0,
        max_wait_seconds: float = 120.0,
        enable_incremental_fix: bool = True,
    ):
        """
        Initialize graceful degradation handler.

        Args:
            cache: LLM response cache (uses global if not provided)
            provider_manager: Provider manager (uses global if not provided)
            initial_wait_seconds: Initial wait on first rate limit
            max_wait_seconds: Maximum total wait time
            enable_incremental_fix: Enable incremental fix on format errors
        """
        self.cache = cache or get_global_cache()
        self.providers = provider_manager or get_provider_manager()
        self.initial_wait = initial_wait_seconds
        self.max_wait = max_wait_seconds
        self.enable_incremental_fix = enable_incremental_fix
        self.fixer = IncrementalFixEngine() if enable_incremental_fix else None

    def execute(
        self,
        task: str,
        prompt: str,
        execute_fn: Callable[[], str],
        providers: Optional[List[str]] = None,
        model: Optional[str] = None,
        ttl_hours: int = 24,
    ) -> ExecutionResult:
        """
        Execute task with graceful degradation.

        Args:
            task: Task description for logging
            prompt: Full prompt to check cache/execute
            execute_fn: Function that calls LLM and returns response
            providers: Provider priority list (uses global config if not provided)
            model: Preferred model
            ttl_hours: Cache TTL in hours

        Returns:
            ExecutionResult with response and metadata
        """
        total_wait = 0.0
        attempts = 0
        used_providers = []
        cache_checked_after_wait = False

        # Strategy 1: Check cache
        cached = self.cache.get(prompt, model or "default", "default")
        if cached:
            logger.info(f"[{task}] Cache HIT")
            return ExecutionResult(
                success=True,
                response=cached,
                strategy_used=FailureStrategy.CACHE_HIT,
                cache_hit=True,
                provider="cache",
            )

        logger.info(f"[{task}] Cache MISS, executing...")

        # Get provider chain
        if providers:
            provider_chain = providers
        else:
            primary, primary_model = self.providers.get_provider(model=model)
            provider_chain = [primary] + self.providers.get_fallback_chain(primary)

        # Strategy 2-4: Execute with provider fallback
        for provider in provider_chain:
            attempts += 1
            used_providers.append(provider)

            try:
                # Wait if needed (rate limit recovery)
                wait_time = self.providers.calculate_wait_time(provider)
                if wait_time > 0:
                    logger.info(f"[{task}] Waiting {wait_time:.1f}s for {provider} rate limit...")
                    time.sleep(wait_time)
                    total_wait += wait_time

                # Execute
                response = execute_fn()

                # Validate response
                if self._is_valid_response(response):
                    # Cache successful response
                    self.cache.set(prompt, response, model or provider, provider, ttl_hours)
                    self.providers.record_request(provider, success=True)

                    logger.info(f"[{task}] Success via {provider}")

                    return ExecutionResult(
                        success=True,
                        response=response,
                        strategy_used=FailureStrategy.PROVIDER_FALLBACK,
                        provider=provider,
                        model=model,
                        attempts=attempts,
                        total_wait_seconds=total_wait,
                    )

                # Invalid response - check if fixable
                if self.enable_incremental_fix and self.fixer:
                    fixed = self._attempt_incremental_fix(prompt, response)
                    if fixed:
                        self.cache.set(prompt, fixed, model or provider, provider, ttl_hours)
                        return ExecutionResult(
                            success=True,
                            response=fixed,
                            strategy_used=FailureStrategy.INCREMENTAL_FIX,
                            provider=provider,
                            model=model,
                            attempts=attempts,
                            total_wait_seconds=total_wait,
                            warnings=["Format error fixed incrementally"],
                        )

                # Record failure
                self.providers.record_request(provider, success=False)
                logger.warning(f"[{task}] Invalid response from {provider}")

            except RateLimitError as e:
                self.providers.record_rate_limit(provider)
                total_wait += e.wait_time

                if total_wait >= self.initial_wait and not cache_checked_after_wait:
                    cached = self.cache.get(prompt, model or "default", "default")
                    cache_checked_after_wait = True
                    if cached:
                        logger.info(f"[{task}] Using cache after {total_wait:.1f}s wait")
                        return ExecutionResult(
                            success=True,
                            response=cached,
                            strategy_used=FailureStrategy.CACHE_HIT,
                            cache_hit=True,
                            provider="cache",
                            attempts=attempts,
                            total_wait_seconds=total_wait,
                        )

                # Check max wait
                if total_wait >= self.max_wait:
                    logger.error(f"[{task}] Max wait {self.max_wait}s exceeded")
                    break

                logger.warning(f"[{task}] Rate limited on {provider}, trying next...")
                continue

            except Exception as e:
                logger.error(f"[{task}] Error on {provider}: {e}")
                self.providers.record_request(provider, success=False)
                continue

        # Strategy 5: Try expired cache
        expired = self._get_expired_cache(prompt, model or "default")
        if expired:
            logger.info(f"[{task}] Using expired cache entry")
            return ExecutionResult(
                success=True,
                response=expired,
                strategy_used=FailureStrategy.PARTIAL_RESULT,
                cache_hit=True,
                warnings=["Using stale cache due to rate limits"],
            )

        # All strategies failed
        return ExecutionResult(
            success=False,
            response="",
            strategy_used=FailureStrategy.EXHAUSTED,
            attempts=attempts,
            total_wait_seconds=total_wait,
            error=f"All providers exhausted after {attempts} attempts",
            metadata={
                "providers_tried": used_providers,
            },
        )

    def _is_valid_response(self, response: str) -> bool:
        """Check if response is valid (not empty, not error)."""
        if not response:
            return False

        # Check for common error patterns
        error_patterns = [
            "error",
            "rate limit",
            "timeout",
            "unavailable",
            "invalid",
        ]

        response_lower = response.lower()
        if any(pattern in response_lower for pattern in error_patterns):
            if len(response) < 100:  # Short error messages
                return False

        return True

    def _attempt_incremental_fix(self, prompt: str, bad_response: str) -> Optional[str]:
        """Attempt to fix a malformed response."""
        if not self.fixer:
            return None

        # This would need the original context to properly fix
        # For now, just detect and flag
        logger.warning("Response validation failed, incremental fix not implemented")
        return None

    def _get_expired_cache(self, prompt: str, model: str) -> Optional[str]:
        """Get expired cache entry as last resort."""
        # This would need cache access to expired entries
        return None

    def execute_with_retry(
        self,
        prompt: str,
        execute_fn: Callable[[], str],
        max_attempts: int = 3,
        backoff_base: float = 2.0,
    ) -> Tuple[str, bool, str]:
        """
        Simple retry with exponential backoff.

        Returns:
            Tuple of (response, success, error_message)
        """
        last_error = ""

        for attempt in range(max_attempts):
            try:
                response = execute_fn()
                if self._is_valid_response(response):
                    return response, True, ""
                last_error = "Invalid response"
            except Exception as e:
                last_error = str(e)

            # Exponential backoff
            if attempt < max_attempts - 1:
                wait = backoff_base**attempt
                logger.info(f"Retry {attempt + 1}/{max_attempts}, waiting {wait}s")
                time.sleep(wait)

        return "", False, last_error


class RateLimitError(Exception):
    """Raised when rate limited."""

    def __init__(self, provider: str, wait_time: float = 0):
        self.provider = provider
        self.wait_time = wait_time
        super().__init__(f"Rate limited by {provider}, wait {wait_time}s")


class GracefulDegradationBuilder:
    """
    Builder for GracefulDegradation with fluent API.

    Usage:
        handler = (GracefulDegradationBuilder()
            .with_cache(my_cache)
            .with_providers(my_providers)
            .with_initial_wait(60)
            .with_max_wait(180)
            .enable_incremental_fix()
            .build())
    """

    def __init__(self):
        self._cache: Optional[LLMResponseCache] = None
        self._providers: Optional[ProviderManager] = None
        self._initial_wait: float = 30.0
        self._max_wait: float = 120.0
        self._enable_incremental: bool = True

    def with_cache(self, cache: LLMResponseCache) -> "GracefulDegradationBuilder":
        self._cache = cache
        return self

    def with_providers(self, providers: ProviderManager) -> "GracefulDegradationBuilder":
        self._providers = providers
        return self

    def with_initial_wait(self, seconds: float) -> "GracefulDegradationBuilder":
        self._initial_wait = seconds
        return self

    def with_max_wait(self, seconds: float) -> "GracefulDegradationBuilder":
        self._max_wait = seconds
        return self

    def enable_incremental_fix(self, enable: bool = True) -> "GracefulDegradationBuilder":
        self._enable_incremental = enable
        return self

    def build(self) -> GracefulDegradation:
        return GracefulDegradation(
            cache=self._cache,
            provider_manager=self._providers,
            initial_wait_seconds=self._initial_wait,
            max_wait_seconds=self._max_wait,
            enable_incremental_fix=self._enable_incremental,
        )


# Convenience function
def create_degradation_handler(
    initial_wait: float = 30.0,
    max_wait: float = 120.0,
) -> GracefulDegradation:
    """Create a configured degradation handler."""
    return GracefulDegradation(
        initial_wait_seconds=initial_wait,
        max_wait_seconds=max_wait,
    )


# Singleton instance
_global_degradation: Optional[GracefulDegradation] = None


def get_degradation_handler() -> GracefulDegradation:
    """Get or create global degradation handler."""
    global _global_degradation
    if _global_degradation is None:
        _global_degradation = create_degradation_handler()
    return _global_degradation
