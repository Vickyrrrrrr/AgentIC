"""
LLM Response Cache Manager
========================

File-based SQLite cache for LLM responses to reduce API calls and costs.
Implements prompt hash-based lookup with TTL and provider-specific caching.

Usage:
    cache = LLMResponseCache()
    cached = cache.get(prompt, model, provider)
    if cached:
        return cached
    response = call_llm(prompt)
    cache.set(prompt, response, model, provider)
"""

import sqlite3
import os
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from threading import Lock
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CacheStats:
    """Statistics about cache usage."""

    total_entries: int = 0
    total_hits: int = 0
    total_misses: int = 0
    hit_rate: float = 0.0
    avg_token_count: int = 0
    oldest_entry: Optional[str] = None
    newest_entry: Optional[str] = None
    total_saved_api_calls: int = 0
    estimated_cost_saved: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_entries": self.total_entries,
            "total_hits": self.total_hits,
            "total_misses": self.total_misses,
            "hit_rate": f"{self.hit_rate:.1f}%",
            "avg_token_count": self.avg_token_count,
            "oldest_entry": self.oldest_entry,
            "newest_entry": self.newest_entry,
            "saved_api_calls": self.total_saved_api_calls,
            "estimated_cost_saved": f"${self.estimated_cost_saved:.4f}",
        }


@dataclass
class CacheEntry:
    """A cached LLM response entry."""

    prompt_hash: str
    prompt_preview: str
    response: str
    model: str
    provider: str
    created_at: str
    last_used: Optional[str] = None
    hit_count: int = 0
    token_count: int = 0
    response_hash: str = ""
    validity_hours: int = 24

    def is_valid(self) -> bool:
        """Check if entry is still valid."""
        created = datetime.fromisoformat(self.created_at)
        expiry = created + timedelta(hours=self.validity_hours)
        return datetime.now() < expiry


class LLMResponseCache:
    """
    SQLite-based cache for LLM responses.

    Features:
    - Prompt hash-based lookup (SHA256)
    - Provider/model specific caching
    - Configurable TTL (default 24 hours)
    - Thread-safe operations
    - Automatic pruning of expired entries
    - Usage statistics

    Cost Estimation:
    - openai/infinity: $0.005/1K tokens
    - anthropic/claude-3-5-sonnet: $0.003/1K tokens
    - generic/llama-3.3-70b: $0.00059/1K tokens
    """

    # Cost per 1K tokens by provider/model
    TOKEN_COSTS = {
        ("openai", "infinity"): 0.005,
        ("openai", "infinity-mini"): 0.00015,
        ("openai", "gpt-4-turbo"): 0.01,
        ("anthropic", "claude-3-5-sonnet"): 0.003,
        ("anthropic", "claude-3-opus"): 0.015,
        ("anthropic", "claude-3-sonnet"): 0.003,
        ("generic", "llama-3.3-70b"): 0.00059,
        ("generic", "mixtral-8x7b"): 0.00024,
        ("openrouter", "default"): 0.005,
    }

    def __init__(
        self,
        cache_dir: Optional[str] = None,
        default_ttl_hours: int = 24,
        max_entries: int = 10000,
        prune_on_init: bool = True,
    ):
        """
        Initialize the LLM response cache.

        Args:
            cache_dir: Directory for cache database. Defaults to ~/.agentic_cache/
            default_ttl_hours: Default TTL for cache entries
            max_entries: Maximum number of entries before pruning
            prune_on_init: Whether to prune expired entries on init
        """
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = Path.home() / ".agentic_cache" / "llm_cache"

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.cache_dir / "llm_responses.db"
        self.default_ttl = default_ttl_hours
        self.max_entries = max_entries
        self._lock = Lock()

        self._init_db()

        if prune_on_init:
            self.prune_expired()

        logger.info(f"LLM Response Cache initialized at {self.db_path}")

    def _init_db(self):
        """Initialize SQLite database with optimized schema."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        try:
            # Performance optimizations
            conn.execute("PRAGMA journal_mode=WAL")  # Write-ahead logging
            conn.execute("PRAGMA synchronous=NORMAL")  # Less fsync
            conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA mmap_size=268435456")  # 256MB memory-mapped I/O

            # Create main cache table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prompt_hash TEXT NOT NULL,
                    prompt_preview TEXT NOT NULL,
                    response TEXT NOT NULL,
                    model TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    last_used TEXT,
                    hit_count INTEGER DEFAULT 0,
                    token_count INTEGER DEFAULT 0,
                    response_hash TEXT,
                    validity_hours INTEGER DEFAULT 24,
                    UNIQUE(prompt_hash, model, provider)
                )
            """)

            # Create indexes for fast lookup
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_prompt_hash 
                ON llm_cache(prompt_hash, model, provider)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at 
                ON llm_cache(created_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_provider_model 
                ON llm_cache(provider, model)
            """)

            conn.commit()
        finally:
            conn.close()

    def _hash_prompt(self, prompt: str) -> str:
        """Create deterministic SHA256 hash of prompt."""
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def _hash_response(self, response: str) -> str:
        """Create hash of response for integrity checking."""
        return hashlib.sha256(response.encode("utf-8")).hexdigest()

    def _estimate_tokens(self, text: str) -> int:
        """
        Estimate token count from text.
        Uses simple heuristic: ~4 characters per token for English.
        """
        return len(text) // 4

    def _estimate_cost(self, token_count: int, model: str, provider: str) -> float:
        """Estimate cost for given token count."""
        cost_per_1k = self.TOKEN_COSTS.get((provider, model), 0.005)
        return (token_count / 1000) * cost_per_1k

    def get(
        self,
        prompt: str,
        model: str,
        provider: str,
        validate: bool = True,
    ) -> Optional[str]:
        """
        Get cached response if available and valid.

        Args:
            prompt: The full prompt that was sent to LLM
            model: Model name (e.g., 'infinity')
            provider: Provider name (e.g., 'openai')
            validate: If True, check TTL validity

        Returns:
            Cached response string or None if not found/invalid
        """
        prompt_hash = self._hash_prompt(prompt)

        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            try:
                cursor = conn.execute(
                    """
                    SELECT response, created_at, validity_hours, token_count, hit_count
                    FROM llm_cache 
                    WHERE prompt_hash = ? AND model = ? AND provider = ?
                """,
                    (prompt_hash, model, provider),
                )

                row = cursor.fetchone()

                if not row:
                    return None

                response, created_at, validity_hours, token_count, hit_count = row

                # Check validity if requested
                if validate:
                    created = datetime.fromisoformat(created_at)
                    expiry = created + timedelta(hours=validity_hours)
                    if datetime.now() >= expiry:
                        # Mark as expired but don't delete yet
                        return None

                # Update hit count and last_used
                conn.execute(
                    """
                    UPDATE llm_cache 
                    SET hit_count = hit_count + 1, 
                        last_used = datetime('now')
                    WHERE prompt_hash = ? AND model = ? AND provider = ?
                """,
                    (prompt_hash, model, provider),
                )
                conn.commit()

                return response

            finally:
                conn.close()

    def get_or_compute(
        self,
        prompt: str,
        model: str,
        provider: str,
        compute_fn,
        ttl_hours: Optional[int] = None,
    ) -> Tuple[str, bool]:
        """
        Get cached response or compute and cache it.

        This is the main entry point for cache-aware LLM calls.

        Args:
            prompt: The prompt
            model: Model name
            provider: Provider name
            compute_fn: Callable that returns the LLM response
            ttl_hours: Optional TTL override

        Returns:
            Tuple of (response, was_cached) where was_cached indicates if from cache
        """
        # Try cache first
        cached = self.get(prompt, model, provider)
        if cached is not None:
            logger.debug(f"Cache HIT: {provider}/{model}")
            return cached, True

        # Compute fresh response
        logger.debug(f"Cache MISS: {provider}/{model}")
        response = compute_fn()

        if response:
            self.set(prompt, response, model, provider, ttl_hours)

        return response, False

    def set(
        self,
        prompt: str,
        response: str,
        model: str,
        provider: str,
        ttl_hours: Optional[int] = None,
    ) -> bool:
        """
        Cache a response.

        Args:
            prompt: The prompt that generated this response
            response: The LLM response to cache
            model: Model name
            provider: Provider name
            ttl_hours: Optional TTL override (default 24h)

        Returns:
            True if cached successfully
        """
        if not response:
            return False

        prompt_hash = self._hash_prompt(prompt)
        prompt_preview = prompt[:200]
        response_hash = self._hash_response(response)
        token_count = self._estimate_tokens(response)
        validity = ttl_hours or self.default_ttl

        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO llm_cache 
                    (prompt_hash, prompt_preview, response, model, provider, 
                     created_at, last_used, hit_count, token_count, 
                     response_hash, validity_hours)
                    VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'), 
                            COALESCE((SELECT hit_count FROM llm_cache 
                                     WHERE prompt_hash = ? AND model = ? AND provider = ?), 0),
                            ?, ?, ?)
                """,
                    (
                        prompt_hash,
                        prompt_preview,
                        response,
                        model,
                        provider,
                        prompt_hash,
                        model,
                        provider,
                        token_count,
                        response_hash,
                        validity,
                    ),
                )
                conn.commit()

                # Check if pruning is needed
                cursor = conn.execute("SELECT COUNT(*) FROM llm_cache")
                count = cursor.fetchone()[0]

                if count > self.max_entries:
                    self._prune_oldest(conn)

                return True

            finally:
                conn.close()

    def _prune_oldest(self, conn: sqlite3.Connection):
        """Remove oldest/least-used entries to make room."""
        conn.execute(
            """
            DELETE FROM llm_cache 
            WHERE id IN (
                SELECT id FROM llm_cache 
                ORDER BY hit_count ASC, created_at ASC 
                LIMIT ?
            )
        """,
            (self.max_entries // 10,),
        )  # Remove 10%
        conn.commit()

    def invalidate(self, prompt: str, model: str, provider: str) -> bool:
        """Remove a specific entry from cache."""
        prompt_hash = self._hash_prompt(prompt)

        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            try:
                cursor = conn.execute(
                    """
                    DELETE FROM llm_cache 
                    WHERE prompt_hash = ? AND model = ? AND provider = ?
                """,
                    (prompt_hash, model, provider),
                )
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

    def invalidate_pattern(self, pattern: str) -> int:
        """
        Remove all entries matching a pattern in prompt_preview.

        Args:
            pattern: Substring to match in prompt_preview

        Returns:
            Number of entries removed
        """
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            try:
                cursor = conn.execute(
                    """
                    DELETE FROM llm_cache 
                    WHERE prompt_preview LIKE ?
                """,
                    (f"%{pattern}%",),
                )
                conn.commit()
                return cursor.rowcount
            finally:
                conn.close()

    def prune_expired(self) -> int:
        """Remove all expired cache entries."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            try:
                cursor = conn.execute("""
                    DELETE FROM llm_cache 
                    WHERE datetime(created_at, '+' || validity_hours || ' hours') < datetime('now')
                """)
                conn.commit()
                removed = cursor.rowcount

                if removed > 0:
                    logger.info(f"Pruned {removed} expired cache entries")

                return removed
            finally:
                conn.close()

    def prune_low_hit(self, min_hits: int = 0, older_than_days: int = 7) -> int:
        """
        Remove low/no-hit entries older than specified days.

        Args:
            min_hits: Remove entries with fewer than this many hits
            older_than_days: Only consider entries older than this many days
        """
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            try:
                cursor = conn.execute(
                    """
                    DELETE FROM llm_cache 
                    WHERE hit_count <= ? 
                    AND datetime(created_at) < datetime('now', ? || ' days')
                """,
                    (min_hits, older_than_days),
                )
                conn.commit()
                return cursor.rowcount
            finally:
                conn.close()

    def clear(self) -> int:
        """Clear all cache entries."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            try:
                cursor = conn.execute("DELETE FROM llm_cache")
                conn.commit()
                return cursor.rowcount
            finally:
                conn.close()

    def get_stats(self) -> CacheStats:
        """Get comprehensive cache statistics."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            try:
                cursor = conn.execute("""
                    SELECT 
                        COUNT(*) as total,
                        SUM(hit_count) as hits,
                        AVG(token_count) as avg_tokens,
                        MIN(created_at) as oldest,
                        MAX(created_at) as newest
                    FROM llm_cache
                """)
                row = cursor.fetchone()

                total, hits, avg_tokens, oldest, newest = row
                total = total or 0
                hits = hits or 0

                # Calculate hit rate
                lookups = hits + (total - hits)  # Approximate
                hit_rate = (hits / total * 100) if total > 0 else 0

                # Estimate cost savings
                cursor2 = conn.execute(
                    """
                    SELECT SUM(token_count * ?), SUM(hit_count)
                    FROM llm_cache, (
                        SELECT AVG(
                            CASE 
                                WHEN provider = 'openai' AND model = 'infinity' THEN 0.005
                                WHEN provider = 'openai' AND model = 'infinity-mini' THEN 0.00015
                                WHEN provider = 'anthropic' AND model LIKE '%sonnet%' THEN 0.003
                                WHEN provider = 'generic' THEN 0.00059
                                ELSE 0.005
                            END
                        ) as avg_cost
                        FROM llm_cache
                    )
                """,
                    (1 / 1000,),
                )
                row2 = cursor2.fetchone()

                # Use a simpler cost calculation
                cursor3 = conn.execute("""
                    SELECT SUM(token_count), SUM(hit_count)
                    FROM llm_cache
                """)
                row3 = cursor3.fetchone()
                total_tokens = row3[0] or 0
                total_hits = row3[1] or 0

                # Assume average cost of $0.002 per 1K tokens
                estimated_saved = (total_tokens / 1000) * 0.002

                stats = CacheStats(
                    total_entries=total,
                    total_hits=hits,
                    total_misses=0,  # Not tracked
                    hit_rate=hit_rate,
                    avg_token_count=int(avg_tokens) if avg_tokens else 0,
                    oldest_entry=oldest,
                    newest_entry=newest,
                    total_saved_api_calls=total_hits,
                    estimated_cost_saved=estimated_saved,
                )

                return stats

            finally:
                conn.close()

    def get_by_provider(self, provider: str) -> List[CacheEntry]:
        """Get all cache entries for a provider."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            try:
                cursor = conn.execute(
                    """
                    SELECT prompt_hash, prompt_preview, response, model, provider,
                           created_at, last_used, hit_count, token_count, 
                           response_hash, validity_hours
                    FROM llm_cache
                    WHERE provider = ?
                    ORDER BY created_at DESC
                """,
                    (provider,),
                )

                entries = []
                for row in cursor:
                    entries.append(CacheEntry(*row))

                return entries
            finally:
                conn.close()

    def warmup(self, entries: List[Tuple[str, str, str, str]]) -> int:
        """
        Pre-populate cache with known good entries.

        Args:
            entries: List of (prompt, response, model, provider) tuples

        Returns:
            Number of entries successfully cached
        """
        cached = 0
        for prompt, response, model, provider in entries:
            if self.set(prompt, response, model, provider):
                cached += 1
        return cached


# Global singleton instance
_global_cache: Optional[LLMResponseCache] = None
_cache_lock = Lock()


def get_global_cache() -> LLMResponseCache:
    """Get or create the global cache instance."""
    global _global_cache

    if _global_cache is None:
        with _cache_lock:
            if _global_cache is None:
                _global_cache = LLMResponseCache()

    return _global_cache


def cached_llm_call(
    prompt: str,
    model: str,
    provider: str,
    compute_fn,
    ttl_hours: Optional[int] = None,
) -> Tuple[str, bool, CacheStats]:
    """
    Convenience function for making cache-aware LLM calls.

    Args:
        prompt: The prompt
        model: Model name
        provider: Provider name
        compute_fn: Function to call if cache miss
        ttl_hours: Optional TTL override

    Returns:
        Tuple of (response, was_cached, stats)
    """
    cache = get_global_cache()
    response, was_cached = cache.get_or_compute(prompt, model, provider, compute_fn, ttl_hours)
    stats = cache.get_stats()
    return response, was_cached, stats
