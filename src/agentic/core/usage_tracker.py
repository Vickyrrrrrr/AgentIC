"""
Usage Tracker
============

Tracks API usage, costs, and performance metrics.
Features:
- Per-provider, per-model tracking
- Token usage statistics
- Cache hit rate analysis
- Cost estimation
- Build-level aggregations

Usage:
    tracker = UsageTracker()

    # Record an API call
    tracker.record_call(
        provider="openai",
        model="gpt-4o",
        prompt_tokens=1000,
        completion_tokens=500,
        cache_hit=False,
    )

    # Get usage report
    report = tracker.get_report()
"""

import sqlite3
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)


@dataclass
class APIUsage:
    """Single API usage record."""

    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cache_hit: bool
    duration_ms: int
    success: bool
    error_type: Optional[str] = None
    build_name: Optional[str] = None
    stage: Optional[str] = None
    timestamp: str = ""


@dataclass
class UsageReport:
    """Aggregated usage report."""

    total_calls: int
    cache_hits: int
    cache_hit_rate: float
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_cost: float
    avg_duration_ms: float
    error_rate: float
    by_provider: Dict[str, Dict]
    by_model: Dict[str, Dict]
    by_stage: Dict[str, Dict]


class UsageTracker:
    """
    Tracks LLM API usage and costs.

    Features:
    - SQLite-based persistent storage
    - Per-build, per-stage tracking
    - Cache efficiency metrics
    - Cost estimation per provider
    """

    # Cost per 1K tokens by provider/model
    TOKEN_COSTS = {
        ("openai", "gpt-4o"): 0.005,
        ("openai", "gpt-4o-mini"): 0.00015,
        ("openai", "gpt-4-turbo"): 0.01,
        ("anthropic", "claude-3-5-sonnet"): 0.003,
        ("anthropic", "claude-3-5-haiku"): 0.00025,
        ("anthropic", "claude-3-opus"): 0.015,
        ("generic", "llama-3.3-70b"): 0.00059,
        ("generic", "mixtral-8x7b"): 0.00024,
        ("openrouter", "default"): 0.005,
    }

    # Default cost if unknown
    DEFAULT_COST_PER_1K = 0.005

    def __init__(
        self,
        db_path: Optional[str] = None,
    ):
        """
        Initialize usage tracker.

        Args:
            db_path: Path to SQLite database (auto-created if not provided)
        """
        if db_path:
            self.db_path = Path(db_path)
        else:
            from ..config import WORKSPACE_ROOT

            cache_dir = Path(WORKSPACE_ROOT) / ".agentic_cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = cache_dir / "usage.db"

        self._lock = Lock()
        self._init_db()

        logger.info(f"UsageTracker initialized at {self.db_path}")

    def _init_db(self):
        """Initialize database schema."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_tokens INTEGER DEFAULT 0,
                    completion_tokens INTEGER DEFAULT 0,
                    cache_hit INTEGER DEFAULT 0,
                    duration_ms INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 1,
                    error_type TEXT,
                    build_name TEXT,
                    stage TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_provider_model 
                ON api_usage(provider, model)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at 
                ON api_usage(created_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_build 
                ON api_usage(build_name)
            """)

            conn.commit()
        finally:
            conn.close()

    def record_call(
        self,
        provider: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cache_hit: bool = False,
        duration_ms: int = 0,
        success: bool = True,
        error_type: Optional[str] = None,
        build_name: Optional[str] = None,
        stage: Optional[str] = None,
    ):
        """
        Record an API call.

        Args:
            provider: Provider name (e.g., 'openai')
            model: Model name (e.g., 'gpt-4o')
            prompt_tokens: Number of prompt tokens
            completion_tokens: Number of completion tokens
            cache_hit: Whether response came from cache
            duration_ms: Call duration in milliseconds
            success: Whether call succeeded
            error_type: Type of error if failed
            build_name: Name of the build
            stage: Current build stage
        """
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.execute(
                    """
                    INSERT INTO api_usage 
                    (provider, model, prompt_tokens, completion_tokens, 
                     cache_hit, duration_ms, success, error_type, build_name, stage)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        provider,
                        model,
                        prompt_tokens,
                        completion_tokens,
                        int(cache_hit),
                        duration_ms,
                        int(success),
                        error_type,
                        build_name,
                        stage,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def record_batch(self, calls: List[APIUsage]):
        """Record multiple calls efficiently."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))
            try:
                conn.executemany(
                    """
                    INSERT INTO api_usage 
                    (provider, model, prompt_tokens, completion_tokens, 
                     cache_hit, duration_ms, success, error_type, build_name, stage)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    [
                        (
                            c.provider,
                            c.model,
                            c.prompt_tokens,
                            c.completion_tokens,
                            int(c.cache_hit),
                            c.duration_ms,
                            int(c.success),
                            c.error_type,
                            c.build_name,
                            c.stage,
                        )
                        for c in calls
                    ],
                )
                conn.commit()
            finally:
                conn.close()

    def get_cost(self, provider: str, model: str, total_tokens: int) -> float:
        """Calculate cost for token usage."""
        cost_per_1k = self.TOKEN_COSTS.get((provider, model), self.DEFAULT_COST_PER_1K)
        return (total_tokens / 1000) * cost_per_1k

    def get_report(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        build_name: Optional[str] = None,
    ) -> UsageReport:
        """
        Generate usage report.

        Args:
            start_date: Start date (ISO format)
            end_date: End date (ISO format)
            build_name: Filter by build name

        Returns:
            UsageReport with aggregated statistics
        """
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))

            # Build query
            where_clauses = []
            params = []

            if start_date:
                where_clauses.append("created_at >= ?")
                params.append(start_date)

            if end_date:
                where_clauses.append("created_at <= ?")
                params.append(end_date)

            if build_name:
                where_clauses.append("build_name = ?")
                params.append(build_name)

            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

            # Overall stats
            cursor = conn.execute(
                f"""
                SELECT 
                    COUNT(*) as total_calls,
                    SUM(cache_hit) as cache_hits,
                    SUM(prompt_tokens) as prompt_tokens,
                    SUM(completion_tokens) as completion_tokens,
                    AVG(duration_ms) as avg_duration,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as errors
                FROM api_usage
                WHERE {where_sql}
            """,
                params,
            )

            row = cursor.fetchone()
            total_calls = row[0] or 0
            cache_hits = row[1] or 0
            prompt_tokens = row[2] or 0
            completion_tokens = row[3] or 0
            avg_duration = row[4] or 0
            errors = row[5] or 0

            # Calculate totals
            total_tokens = prompt_tokens + completion_tokens
            cache_hit_rate = (cache_hits / total_calls * 100) if total_calls > 0 else 0
            error_rate = (errors / total_calls * 100) if total_calls > 0 else 0

            # Calculate cost
            total_cost = 0
            cursor = conn.execute(
                f"""
                SELECT provider, model, SUM(prompt_tokens + completion_tokens) as tokens
                FROM api_usage
                WHERE {where_sql}
                GROUP BY provider, model
            """,
                params,
            )

            for prov, mod, tokens in cursor:
                total_cost += self.get_cost(prov, mod, tokens or 0)

            # By provider
            by_provider = {}
            cursor = conn.execute(
                f"""
                SELECT 
                    provider,
                    COUNT(*) as calls,
                    SUM(prompt_tokens + completion_tokens) as tokens,
                    SUM(cache_hit) as hits,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as errors
                FROM api_usage
                WHERE {where_sql}
                GROUP BY provider
            """,
                params,
            )

            for row in cursor:
                prov = row[0]
                calls = row[1]
                tokens = row[2] or 0
                hits = row[3] or 0
                errs = row[4] or 0
                by_provider[prov] = {
                    "calls": calls,
                    "tokens": tokens,
                    "cost": self.get_cost(prov, "", tokens),
                    "cache_hit_rate": f"{(hits / calls * 100):.1f}%" if calls > 0 else "0%",
                    "error_rate": f"{(errs / calls * 100):.1f}%" if calls > 0 else "0%",
                }

            # By model
            by_model = {}
            cursor = conn.execute(
                f"""
                SELECT 
                    provider || '/' || model as key,
                    COUNT(*) as calls,
                    SUM(prompt_tokens + completion_tokens) as tokens,
                    SUM(cache_hit) as hits
                FROM api_usage
                WHERE {where_sql}
                GROUP BY provider, model
            """,
                params,
            )

            for row in cursor:
                key = row[0]
                calls = row[1]
                tokens = row[2] or 0
                hits = row[3] or 0
                by_model[key] = {
                    "calls": calls,
                    "tokens": tokens,
                    "cost": 0,  # Calculated at report level
                    "cache_hit_rate": f"{(hits / calls * 100):.1f}%" if calls > 0 else "0%",
                }

            # By stage
            by_stage = {}
            cursor = conn.execute(
                f"""
                SELECT 
                    COALESCE(stage, 'unknown') as stage,
                    COUNT(*) as calls,
                    SUM(prompt_tokens + completion_tokens) as tokens
                FROM api_usage
                WHERE {where_sql}
                GROUP BY stage
            """,
                params,
            )

            for row in cursor:
                stage = row[0]
                by_stage[stage] = {
                    "calls": row[1],
                    "tokens": row[2] or 0,
                }

            conn.close()

            return UsageReport(
                total_calls=total_calls,
                cache_hits=cache_hits,
                cache_hit_rate=cache_hit_rate,
                total_prompt_tokens=prompt_tokens,
                total_completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                total_cost=total_cost,
                avg_duration_ms=avg_duration,
                error_rate=error_rate,
                by_provider=by_provider,
                by_model=by_model,
                by_stage=by_stage,
            )

    def get_build_summary(self, build_name: str) -> Dict[str, Any]:
        """Get usage summary for a specific build."""
        report = self.get_report(build_name=build_name)

        return {
            "build": build_name,
            "total_calls": report.total_calls,
            "cache_hit_rate": f"{report.cache_hit_rate:.1f}%",
            "total_tokens": report.total_tokens,
            "estimated_cost": f"${report.total_cost:.4f}",
            "by_stage": report.by_stage,
        }

    def get_daily_usage(self, days: int = 7) -> List[Dict[str, Any]]:
        """Get daily usage for the last N days."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))

            cursor = conn.execute(
                """
                SELECT 
                    DATE(created_at) as date,
                    COUNT(*) as calls,
                    SUM(prompt_tokens + completion_tokens) as tokens,
                    SUM(cache_hit) as hits
                FROM api_usage
                WHERE created_at >= datetime('now', ?)
                GROUP BY DATE(created_at)
                ORDER BY date
            """,
                (f"-{days} days",),
            )

            results = []
            for row in cursor:
                calls = row[1]
                hits = row[3] or 0
                results.append(
                    {
                        "date": row[0],
                        "calls": calls,
                        "tokens": row[2] or 0,
                        "cache_hit_rate": f"{(hits / calls * 100):.1f}%" if calls > 0 else "0%",
                    }
                )

            conn.close()
            return results

    def get_provider_comparison(self) -> List[Dict[str, Any]]:
        """Compare performance across providers."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))

            cursor = conn.execute("""
                SELECT 
                    provider,
                    COUNT(*) as calls,
                    AVG(duration_ms) as avg_latency,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as error_rate
                FROM api_usage
                GROUP BY provider
                ORDER BY calls DESC
            """)

            results = []
            for row in cursor:
                results.append(
                    {
                        "provider": row[0],
                        "calls": row[1],
                        "avg_latency_ms": round(row[2] or 0),
                        "error_rate": f"{row[3]:.1f}%" if row[3] else "0%",
                    }
                )

            conn.close()
            return results

    def prune_old_records(self, older_than_days: int = 30) -> int:
        """Remove records older than N days."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))

            cursor = conn.execute(
                """
                DELETE FROM api_usage
                WHERE created_at < datetime('now', ?)
            """,
                (f"-{older_than_days} days",),
            )

            conn.commit()
            deleted = cursor.rowcount
            conn.close()

            logger.info(f"Pruned {deleted} old usage records")
            return deleted

    def get_stats_summary(self) -> Dict[str, Any]:
        """Get quick stats summary."""
        with self._lock:
            conn = sqlite3.connect(str(self.db_path))

            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as total,
                    (SELECT COUNT(*) FROM api_usage WHERE cache_hit = 1) as cached,
                    (SELECT SUM(prompt_tokens + completion_tokens) FROM api_usage) as tokens,
                    (SELECT COUNT(DISTINCT build_name) FROM api_usage WHERE build_name IS NOT NULL) as builds
                FROM api_usage
            """)

            row = cursor.fetchone()
            total = row[0] or 0
            cached = row[1] or 0
            tokens = row[2] or 0
            builds = row[3] or 0

            conn.close()

            return {
                "total_calls": total,
                "cache_hits": cached,
                "cache_rate": f"{(cached / total * 100):.1f}%" if total > 0 else "0%",
                "total_tokens": tokens,
                "total_builds": builds,
                "db_path": str(self.db_path),
            }


# Global instance
_global_tracker: Optional[UsageTracker] = None
_tracker_lock = Lock()


def get_usage_tracker() -> UsageTracker:
    """Get or create global usage tracker."""
    global _global_tracker

    if _global_tracker is None:
        with _tracker_lock:
            if _global_tracker is None:
                _global_tracker = UsageTracker()

    return _global_tracker


def record_api_call(**kwargs):
    """Convenience function to record a single call."""
    tracker = get_usage_tracker()
    tracker.record_call(**kwargs)
