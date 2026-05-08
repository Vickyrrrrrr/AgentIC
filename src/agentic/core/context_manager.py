"""
Token Budget Manager
===================

Intelligent token budgeting for LLM context management.
Implements semantic-aware truncation that preserves critical information.

Features:
- Token-based budgeting (not character-based)
- Smart RTL truncation that preserves module structure
- Priority-based context allocation
- Error-focused context preservation

Usage:
    budget = TokenBudgetManager(model="gpt-4o", provider="openai")
    context = budget.budget_context(
        spec="...",
        rtl="...",
        error="...",
        history=["..."],
    )
"""

import re
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ContextPriority(Enum):
    """Priority levels for context items."""

    CRITICAL = 1  # Error messages, critical state
    HIGH = 2  # RTL interface, specs
    MEDIUM = 3  # Current working code
    LOW = 4  # History, old attempts
    IGNORED = 5  # Testbenches, boilerplate


@dataclass
class ContextItem:
    """A piece of context with priority and content."""

    content: str
    priority: ContextPriority
    category: str
    tokens_estimate: int = 0

    def __post_init__(self):
        if self.tokens_estimate == 0:
            self.tokens_estimate = self._estimate_tokens(self.content)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate tokens from text. ~4 chars per token for English."""
        return max(1, len(text) // 4)


@dataclass
class BudgetAllocation:
    """How to allocate the token budget."""

    spec_tokens: int = 2000
    rtl_tokens: int = 3000
    error_tokens: int = 1500
    history_tokens: int = 1000
    other_tokens: int = 500

    @property
    def total(self) -> int:
        return (
            self.spec_tokens
            + self.rtl_tokens
            + self.error_tokens
            + self.history_tokens
            + self.other_tokens
        )


class TokenBudgetManager:
    """
    Manages token budgets for LLM context windows.

    Handles:
    - Provider/model-specific context limits
    - Dynamic allocation based on content type
    - Smart RTL truncation
    - Priority-based retention

    Token Limits by Provider/Model:
    - OpenAI GPT-4o: 128K context
    - Anthropic Claude-3.5: 200K context
    - Generic: 32K context
    """

    # Provider context limits (input tokens)
    PROVIDER_LIMITS = {
        "openai": {
            "gpt-4o": 128000,
            "gpt-4o-mini": 128000,
            "gpt-4-turbo": 128000,
            "gpt-4": 8192,
            "gpt-3.5-turbo": 16385,
        },
        "anthropic": {
            "claude-3-5-sonnet": 200000,
            "claude-3-5-haiku": 200000,
            "claude-3-opus": 200000,
            "claude-3-sonnet": 200000,
            "claude-3-haiku": 200000,
        },
        "generic": {
            "llama-3.3-70b": 32768,
            "llama-3.1-70b": 32768,
            "mixtral-8x7b": 32768,
            "llama-3.1-8b": 32768,
        },
        "openrouter": {
            "default": 32000,
        },
        "ollama": {
            "default": 4096,
        },
        "azure": {
            "gpt-4o": 128000,
            "gpt-4": 8192,
        },
    }

    # Default allocation ratios (fractions of available budget)
    DEFAULT_ALLOCATION = {
        "spec": 0.20,
        "rtl": 0.35,
        "error": 0.25,
        "history": 0.10,
        "other": 0.10,
    }

    # Conservative allocation for uncertain contexts
    CONSERVATIVE_ALLOCATION = {
        "spec": 0.15,
        "rtl": 0.25,
        "error": 0.35,
        "history": 0.15,
        "other": 0.10,
    }

    # Mode-based allocations (VLSI-expert optimized)
    MODE_ALLOCATION = {
        "error_mode": {
            "spec": 0.20,
            "rtl": 0.35,
            "error": 0.25,
            "history": 0.10,
            "other": 0.10,
        },
        "generation_mode": {
            "spec": 0.30,
            "rtl": 0.30,
            "error": 0.10,
            "history": 0.15,
            "other": 0.15,
        },
        "doc_mode": {
            "spec": 0.20,
            "rtl": 0.50,
            "error": 0.05,
            "history": 0.10,
            "other": 0.15,
        },
        "verification_mode": {
            "spec": 0.15,
            "rtl": 0.40,
            "error": 0.25,
            "history": 0.10,
            "other": 0.10,
        },
    }

    def __init__(
        self,
        model: str = "gpt-4o",
        provider: str = "openai",
        reserved_response_tokens: int = 2000,
        allocation_strategy: str = "balanced",
        mode: Optional[str] = None,
    ):
        """
        Initialize token budget manager.

        Args:
            model: Model name (e.g., 'gpt-4o')
            provider: Provider name (e.g., 'openai')
            reserved_response_tokens: Reserve this many tokens for response
            allocation_strategy: 'balanced', 'conservative', or 'aggressive'
        """
        self.model = model
        self.provider = provider
        self.reserved_tokens = reserved_response_tokens

        # Get context limit
        self.max_tokens = self._get_max_tokens()
        self.available_tokens = self.max_tokens - self.reserved_tokens

        # Set allocation strategy
        self.mode = mode
        if mode and mode in self.MODE_ALLOCATION:
            self.allocation = self.MODE_ALLOCATION[mode]
        elif allocation_strategy == "conservative":
            self.allocation = self.CONSERVATIVE_ALLOCATION
        else:
            self.allocation = self.DEFAULT_ALLOCATION

        logger.debug(
            f"TokenBudgetManager: {provider}/{model} "
            f"max={self.max_tokens}, available={self.available_tokens}"
        )

    def _get_max_tokens(self) -> int:
        """Get max tokens for model, with fallback."""
        provider_limits = self.PROVIDER_LIMITS.get(self.provider, {})

        # Try exact model match
        if self.model in provider_limits:
            return provider_limits[self.model]

        # Try partial match
        for known_model, limit in provider_limits.items():
            if (
                known_model.lower() in self.model.lower()
                or self.model.lower() in known_model.lower()
            ):
                return limit

        # Fallback to conservative default
        logger.warning(f"Unknown model {self.provider}/{self.model}, using conservative 32K limit")
        return 32000

    def budget_context(
        self,
        spec: str = "",
        rtl: str = "",
        error: str = "",
        history: List[str] = None,
        other_items: Dict[str, str] = None,
        priorities: Optional[Dict[str, float]] = None,
        mode: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Budget context items by available tokens.

        Args:
            spec: Architecture specification text
            rtl: RTL code
            error: Error message to fix
            history: List of recent history items
            other_items: Dict of other context items
            priorities: Override default allocation ratios
            mode: Override mode for this call ('error_mode', 'generation_mode', 'doc_mode', 'verification_mode')

        Returns:
            Dict with budgeted content for each category
        """
        history = history or []
        other_items = other_items or {}

        # Use mode-specific allocation if provided, otherwise use default
        if mode and mode in self.MODE_ALLOCATION:
            allocation = self.MODE_ALLOCATION[mode]
        else:
            allocation = priorities or self.allocation

        # Calculate budget targets
        targets = {
            "spec": int(self.available_tokens * allocation["spec"]),
            "rtl": int(self.available_tokens * allocation["rtl"]),
            "error": int(self.available_tokens * allocation["error"]),
            "history": int(self.available_tokens * allocation["history"]),
        }

        result = {}

        # Budget spec (keep beginning + important parts)
        if spec:
            result["spec"] = self._budget_text(spec, targets["spec"])

        # Budget RTL (smart truncation)
        if rtl:
            result["rtl"] = self._smart_truncate_rtl(rtl, targets["rtl"])

        # Budget error (keep full or truncate to generous limit)
        if error:
            error_budget = min(targets["error"], 2000)
            result["error"] = self._budget_text(error, error_budget)

        # Budget history (most recent first)
        if history:
            result["history"] = self._budget_history(history, targets["history"])

        # Budget other items
        other_budget = self.available_tokens * allocation.get("other", 0.10)
        remaining = other_budget
        for name, content in other_items.items():
            item_budget = min(remaining / len(other_items), remaining)
            result[name] = self._budget_text(content, int(item_budget))
            remaining -= len(result[name]) // 4

        return result

    def _budget_text(self, text: str, max_tokens: int) -> str:
        """Simple token-based text truncation."""
        if not text:
            return ""

        max_chars = max_tokens * 4
        if len(text) <= max_chars:
            return text

        return text[:max_chars] + "\n... [truncated]"

    def _budget_history(self, history: List[str], max_tokens: int) -> str:
        """Budget history items, most recent first."""
        if not history:
            return ""

        # Reverse to get most recent first
        items = list(reversed(history))

        result = []
        current_tokens = 0

        for item in items:
            item_tokens = len(item) // 4
            if current_tokens + item_tokens <= max_tokens:
                result.append(item)
                current_tokens += item_tokens
            else:
                # Take partial if there's room
                remaining = max_tokens - current_tokens
                if remaining > 100:  # At least some content
                    result.append(item[: remaining * 4] + "\n... [truncated]")
                break

        # Return in chronological order
        return "\n\n---\n\n".join(reversed(result))

    def _smart_truncate_rtl(self, rtl: str, max_tokens: int) -> str:
        """
        Smart RTL truncation that preserves module structure.

        Strategy:
        1. Parse module boundaries
        2. Prioritize modules by importance:
           - Top-level interface (ports)
           - State machines
           - Critical datapath
           - Testbenches (lower priority)
        3. Fill budget with highest priority content
        """
        if not rtl:
            return ""

        total_tokens = len(rtl) // 4

        if total_tokens <= max_tokens:
            return rtl

        # Parse module structure
        modules = self._parse_modules(rtl)

        if not modules:
            # Fallback to simple truncation
            return self._budget_text(rtl, max_tokens)

        # Prioritize modules
        prioritized = self._prioritize_modules(modules)

        # Build truncated RTL
        result_lines = []
        current_tokens = 0

        for priority, module_name, start_line, end_line, lines in prioritized:
            module_tokens = len("\n".join(lines)) // 4

            if current_tokens + module_tokens <= max_tokens:
                result_lines.extend(lines)
                current_tokens += module_tokens
            else:
                # Take partial module
                remaining = max_tokens - current_tokens
                remaining_chars = remaining * 4

                # Build partial module
                partial = []
                chars_so_far = 0
                for line in lines:
                    if chars_so_far + len(line) <= remaining_chars:
                        partial.append(line)
                        chars_so_far += len(line)
                    else:
                        partial.append(f"// ... [{len(lines) - len(partial)} more lines truncated]")
                        break

                result_lines.extend(partial)
                break

        return "\n".join(result_lines)

    def _parse_modules(self, rtl: str) -> Dict[str, Tuple[int, int, List[str]]]:
        """Parse RTL to find module boundaries."""
        lines = rtl.split("\n")
        modules = {}

        current_module = None
        module_start = 0
        module_lines = []

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Module start
            module_match = re.match(r"^\s*module\s+(\w+)", stripped)
            if module_match:
                if current_module:
                    modules[current_module] = (module_start, i, module_lines)

                current_module = module_match.group(1)
                module_start = i
                module_lines = [line]

            # Module end
            elif current_module:
                module_lines.append(line)
                if stripped == "endmodule" or re.match(r"^\s*endmodule\b", stripped):
                    modules[current_module] = (module_start, i + 1, module_lines)
                    current_module = None

        # Handle unclosed module
        if current_module and module_lines:
            modules[current_module] = (module_start, len(lines), module_lines)

        return modules

    def _prioritize_modules(
        self, modules: Dict[str, Tuple[int, int, List[str]]]
    ) -> List[Tuple[int, str, int, int, List[str]]]:
        """
        Prioritize modules for retention.

        Returns list of (priority_score, name, start, end, lines)
        Higher score = higher priority (keep first).
        """
        prioritized = []

        for name, (start, end, lines) in modules.items():
            score = 50  # Base score

            name_lower = name.lower()

            # Boost for important modules
            if "top" in name_lower or name_lower.endswith("_top"):
                score += 30
            if "interface" in name_lower:
                score += 20
            if "fsm" in name_lower or "state" in name_lower:
                score += 15
            if "axi" in name_lower or "ahb" in name_lower or "wishbone" in name_lower:
                score += 15

            # Boost for modules with sequential logic
            code_text = " ".join(lines)
            if any(kw in code_text for kw in ["always @(posedge", "always_ff", "always_comb"]):
                score += 10
            if "case" in code_text and "state" in code_text:
                score += 10

            # Penalize testbenches and boilerplate
            if "testbench" in name_lower or name_lower.endswith("_tb"):
                score -= 40
            if name_lower.startswith("tb_") or name_lower.startswith("test_"):
                score -= 40
            if "dump" in name_lower or "monitor" in name_lower:
                score -= 20

            # Penalize very short modules (likely utilities)
            if len(lines) < 20:
                score -= 10

            prioritized.append((score, name, start, end, lines))

        # Sort by priority (highest first), then by line number (earlier first)
        prioritized.sort(key=lambda x: (-x[0], x[2]))

        return prioritized

    def build_context_string(
        self,
        spec: str = "",
        rtl: str = "",
        error: str = "",
        history: List[str] = None,
        other_items: Dict[str, str] = None,
        format: str = "sections",
    ) -> str:
        """
        Build full context string with proper formatting.

        Args:
            spec: Architecture spec
            rtl: RTL code
            error: Error to fix
            history: History items
            other_items: Other context items
            format: 'sections' for markdown sections, 'compact' for condensed

        Returns:
            Formatted context string
        """
        budgeted = self.budget_context(
            spec=spec,
            rtl=rtl,
            error=error,
            history=history,
            other_items=other_items,
        )

        if format == "compact":
            return self._build_compact_context(budgeted)

        return self._build_sections_context(budgeted)

    def _build_sections_context(self, budgeted: Dict[str, str]) -> str:
        """Build context as labeled sections."""
        sections = []

        if budgeted.get("spec"):
            sections.append(f"## Architecture Specification\n{budgeted['spec']}")

        if budgeted.get("rtl"):
            sections.append(f"## Current RTL Code\n```verilog\n{budgeted['rtl']}\n```")

        if budgeted.get("error"):
            sections.append(f"## Error to Fix\n```\n{budgeted['error']}\n```")

        if budgeted.get("history"):
            sections.append(f"## Recent History\n{budgeted['history']}")

        for name, content in budgeted.items():
            if name not in ("spec", "rtl", "error", "history") and content:
                sections.append(f"## {name}\n{content}")

        return "\n\n".join(sections)

    def _build_compact_context(self, budgeted: Dict[str, str]) -> str:
        """Build context as compact single-block."""
        parts = []

        if budgeted.get("spec"):
            parts.append(f"SPEC: {budgeted['spec']}")

        if budgeted.get("rtl"):
            parts.append(f"RTL:\n```verilog\n{budgeted['rtl']}\n```")

        if budgeted.get("error"):
            parts.append(f"ERROR: {budgeted['error']}")

        return "\n\n".join(parts)

    def get_token_summary(self, budgeted: Dict[str, str]) -> Dict[str, int]:
        """Get token counts for budgeted content."""
        summary = {}
        for key, content in budgeted.items():
            if content:
                summary[key] = len(content) // 4
        return summary


class DynamicTokenBudget(TokenBudgetManager):
    """
    Dynamic token budget that adjusts based on content analysis.

    Additional features:
    - Detects content type automatically
    - Adjusts allocation based on error severity
    - Preserves signal declarations even when truncating
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.error_severity = "medium"

    def set_error_context(self, error: str, rtl: str = "", focus_on_module: str = None):
        """
        Prepare context for error fixing.

        Args:
            error: The error message
            rtl: The RTL code containing the error
            focus_on_module: Module name to focus on
        """
        self.error_severity = self._assess_error_severity(error)

        # Increase error budget for high-severity issues
        if self.error_severity == "critical":
            self.allocation = {
                "spec": 0.10,
                "rtl": 0.30,
                "error": 0.45,
                "history": 0.10,
                "other": 0.05,
            }
        elif self.error_severity == "high":
            self.allocation = {
                "spec": 0.15,
                "rtl": 0.30,
                "error": 0.35,
                "history": 0.15,
                "other": 0.05,
            }

        # If focusing on module, prioritize it
        if focus_on_module and rtl:
            rtl = self._extract_module_focus(rtl, focus_on_module)

        return rtl

    def _assess_error_severity(self, error: str) -> str:
        """Assess how severe an error is."""
        error_lower = error.lower()

        critical_patterns = [
            "multiple drivers",
            "latch inferred",
            "combinational loop",
            "unreachable state",
            "zero-width",
        ]

        high_patterns = [
            "width mismatch",
            "undefined",
            "not declared",
            "syntax error",
            "unexpected token",
        ]

        if any(p in error_lower for p in critical_patterns):
            return "critical"
        elif any(p in error_lower for p in high_patterns):
            return "high"
        else:
            return "medium"

    def _extract_module_focus(self, rtl: str, module_name: str) -> str:
        """Extract just the focused module and its dependencies."""
        modules = self._parse_modules(rtl)

        if module_name not in modules:
            return rtl

        # Get focused module
        _, _, focused_lines = modules[module_name]

        # Find signal declarations
        signals = self._extract_signal_names(focused_lines)

        # Find modules that use these signals
        dependents = set()
        for name, (_, _, lines) in modules.items():
            if name == module_name:
                continue
            code = " ".join(lines)
            for sig in signals:
                if sig in code:
                    dependents.add(name)
                    break

        # Build focused RTL
        result = []

        # Add focused module
        result.extend(focused_lines)

        # Add dependent modules
        for dep_name in dependents:
            if dep_name in modules:
                _, _, dep_lines = modules[dep_name]
                result.extend([f"\n// From module {dep_name}:"])
                result.extend(dep_lines)

        return "\n".join(result)

    def _extract_signal_names(self, lines: List[str]) -> List[str]:
        """Extract signal names from module lines."""
        signals = set()

        for line in lines:
            # Match port declarations
            for match in re.finditer(
                r"(?:input|output|wire|reg|logic)\s+(?:reg\s+)?(?:logic\s+)?(?:unsigned\s+)?(?:signed\s+)?(?:\[\d+:\d+\]\s+)?(\w+)",
                line,
            ):
                signals.add(match.group(1))

            # Match variable declarations
            for match in re.finditer(r"\b(\w+)\s*(?:=|<=)", line):
                signals.add(match.group(1))

        return list(signals)


# Convenience function
def create_context(
    spec: str,
    rtl: str,
    error: str = "",
    model: str = "gpt-4o",
    provider: str = "openai",
    mode: str = "balanced",
) -> str:
    """
    Quick context creation helper.

    Args:
        spec: Architecture specification
        rtl: RTL code
        error: Error to fix (optional)
        model: LLM model
        provider: LLM provider
        mode: 'balanced', 'conservative', or 'error_focus'

    Returns:
        Budgeted context string
    """
    _MODE_MAP = {
        "balanced": "generation_mode",
        "conservative": "verification_mode",
        "error_focus": "error_mode",
    }
    internal_mode = _MODE_MAP.get(mode, "generation_mode")

    budget = DynamicTokenBudget(
        model=model,
        provider=provider,
        allocation_strategy="conservative" if mode == "conservative" else "balanced",
        mode=internal_mode,
    )

    if mode == "error_focus" and error:
        rtl = budget.set_error_context(error, rtl)

    return budget.build_context_string(
        spec=spec,
        rtl=rtl,
        error=error,
    )
