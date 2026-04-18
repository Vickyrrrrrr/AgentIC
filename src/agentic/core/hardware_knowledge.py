"""
Hardware Knowledge Retrieval
============================

Lightweight retrieval-augmented context for RTL, verification, CDC, timing,
and physical-design prompts.  This intentionally avoids a mandatory vector DB:
it provides deterministic lexical retrieval over built-in hardware guidance plus
optional user documents in AGENTIC_KNOWLEDGE_DIR.
"""

import os
import re
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from ..config import WORKSPACE_ROOT


@dataclass
class KnowledgeChunk:
    """A retrievable hardware-design knowledge chunk."""

    title: str
    text: str
    tags: List[str]
    source: str = "builtin"


@dataclass
class RetrievalResult:
    """A scored retrieval hit."""

    chunk: KnowledgeChunk
    score: float


_BUILTIN_CHUNKS: List[KnowledgeChunk] = [
    KnowledgeChunk(
        title="Synthesizable Verilog Discipline",
        tags=["rtl", "verilog", "synthesis", "lint"],
        text=(
            "Use one clocked always block for registers and separate combinational logic. "
            "Assign every combinational output on every path, reset all state explicitly, "
            "avoid delays, initial blocks for design state, force/release, hierarchical references, "
            "and unsized arithmetic in critical datapaths."
        ),
    ),
    KnowledgeChunk(
        title="Width-Safe RTL",
        tags=["rtl", "width", "verilator", "lint"],
        text=(
            "Size constants and casts to the destination width. Extend operands before addition, "
            "subtraction, shifts, and comparisons. For counters, compare against a same-width localparam "
            "and avoid truncating carry bits unless the wrap behavior is specified."
        ),
    ),
    KnowledgeChunk(
        title="CDC Handshake Pattern",
        tags=["cdc", "clock-domain", "verification"],
        text=(
            "Single-bit CDC controls need two-flop synchronizers. Multi-bit data crossing domains should "
            "use async FIFOs, toggle handshakes, or valid/ready protocols with stable payload windows. "
            "Never synchronize each bit of a multi-bit bus independently unless it is Gray-coded."
        ),
    ),
    KnowledgeChunk(
        title="Formal-Friendly Control Logic",
        tags=["formal", "verification", "sva", "fsm"],
        text=(
            "Keep FSM states enumerated with a safe default transition. Add assumptions for legal inputs "
            "and assertions for reset convergence, no illegal states, request eventually acknowledged, "
            "FIFO bounds, and one-hot or mutually-exclusive grants."
        ),
    ),
    KnowledgeChunk(
        title="Testbench Self-Checking Rules",
        tags=["testbench", "verification", "regression"],
        text=(
            "A production testbench should be self-checking, deterministic, reset-aware, and should end "
            "with an explicit pass/fail marker. Scoreboards should compare observable behavior instead "
            "of internal implementation details."
        ),
    ),
    KnowledgeChunk(
        title="Timing Closure Tactics",
        tags=["timing", "sdc", "synthesis", "physical"],
        text=(
            "Negative setup slack is usually improved by pipelining long datapaths, reducing fanout, "
            "registering outputs, avoiding large priority chains, and tightening unrealistic combinational "
            "logic. Hold issues should not be fixed by changing RTL latency unless the interface contract permits it."
        ),
    ),
    KnowledgeChunk(
        title="Open-Source PDK Practical Limits",
        tags=["pdk", "sky130", "gf180mcu", "asap7", "openroad"],
        text=(
            "For SKY130 and GF180, keep clocks conservative unless the design is shallow and pipelined. "
            "Predictive PDKs such as ASAP7 are useful for research comparisons but are not foundry tapeout kits. "
            "Commercial PDKs require manually installed Liberty, LEF, tech, DRC, and LVS decks."
        ),
    ),
    KnowledgeChunk(
        title="Physical Verification Checklist",
        tags=["drc", "lvs", "signoff", "physical"],
        text=(
            "Do not treat GDS as signoff-ready until DRC is clean, LVS matches, extracted timing is reviewed, "
            "power intent is consistent, and generated reports are archived with tool versions and PDK corner data."
        ),
    ),
]


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z0-9_]+", (text or "").lower())


class HardwareKnowledgeBase:
    """Simple deterministic RAG store for hardware context."""

    def __init__(self, knowledge_dir: Optional[str] = None):
        self.knowledge_dir = Path(
            os.path.expanduser(
                knowledge_dir
                or os.environ.get("AGENTIC_KNOWLEDGE_DIR", "")
                or os.path.join(WORKSPACE_ROOT, "knowledge", "hardware")
            )
        )
        self._chunks: Optional[List[KnowledgeChunk]] = None

    def chunks(self) -> List[KnowledgeChunk]:
        if self._chunks is None:
            self._chunks = list(_BUILTIN_CHUNKS)
            self._chunks.extend(self._load_user_chunks())
        return self._chunks

    def _load_user_chunks(self) -> Iterable[KnowledgeChunk]:
        if not self.knowledge_dir.is_dir():
            return []

        chunks: List[KnowledgeChunk] = []
        for path in sorted(self.knowledge_dir.rglob("*")):
            if path.suffix.lower() not in {".md", ".txt", ".sv", ".v", ".sdc", ".tcl"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for idx, block in enumerate(self._split_text(text)):
                chunks.append(
                    KnowledgeChunk(
                        title=f"{path.name}#{idx + 1}",
                        text=block,
                        tags=self._infer_tags(path.name, block),
                        source=str(path),
                    )
                )
        return chunks

    @staticmethod
    def _split_text(text: str, max_chars: int = 1400) -> List[str]:
        blocks = [b.strip() for b in re.split(r"\n\s*\n", text or "") if b.strip()]
        chunks: List[str] = []
        current = ""
        for block in blocks:
            if len(current) + len(block) + 2 <= max_chars:
                current = f"{current}\n\n{block}".strip()
            else:
                if current:
                    chunks.append(current)
                current = block[:max_chars]
        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _infer_tags(name: str, text: str) -> List[str]:
        haystack = f"{name} {text}".lower()
        known = [
            "rtl",
            "verilog",
            "systemverilog",
            "cdc",
            "formal",
            "sva",
            "timing",
            "sdc",
            "physical",
            "drc",
            "lvs",
            "pdk",
            "testbench",
            "coverage",
        ]
        return [tag for tag in known if tag in haystack] or ["hardware"]

    def search(
        self,
        query: str,
        tags: Optional[List[str]] = None,
        limit: int = 4,
    ) -> List[RetrievalResult]:
        query_terms = _tokenize(query)
        if not query_terms:
            return []
        query_set = set(query_terms)
        tag_set = set(tags or [])
        results: List[RetrievalResult] = []

        for chunk in self.chunks():
            text_terms = _tokenize(" ".join([chunk.title, chunk.text, " ".join(chunk.tags)]))
            if not text_terms:
                continue
            term_counts: Dict[str, int] = {}
            for term in text_terms:
                term_counts[term] = term_counts.get(term, 0) + 1

            overlap = query_set.intersection(term_counts)
            lexical = sum(1.0 + math.log(term_counts[t]) for t in overlap)
            tag_bonus = 1.5 * len(tag_set.intersection(chunk.tags))
            if lexical + tag_bonus <= 0:
                continue
            score = lexical + tag_bonus
            results.append(RetrievalResult(chunk=chunk, score=score))

        return sorted(results, key=lambda r: r.score, reverse=True)[:limit]

    def build_context(
        self,
        query: str,
        stage: str = "",
        target_pdk: str = "",
        limit: int = 4,
        max_chars: int = 2600,
    ) -> str:
        tags = self._stage_tags(stage)
        if target_pdk:
            tags.append(target_pdk.lower())
        hits = self.search(query=query, tags=tags, limit=limit)
        if not hits:
            return ""

        lines = ["HARDWARE KNOWLEDGE RETRIEVAL:"]
        used = 0
        for hit in hits:
            entry = (
                f"- {hit.chunk.title} [{', '.join(hit.chunk.tags)} | {hit.chunk.source}]\n"
                f"  {hit.chunk.text.strip()}"
            )
            if used + len(entry) > max_chars:
                break
            lines.append(entry)
            used += len(entry)
        return "\n".join(lines)

    @staticmethod
    def _stage_tags(stage: str) -> List[str]:
        stage_lower = (stage or "").lower()
        tags = []
        mapping = {
            "rtl": ["rtl", "verilog", "lint"],
            "verification": ["testbench", "verification"],
            "formal": ["formal", "sva"],
            "coverage": ["coverage", "testbench"],
            "cdc": ["cdc"],
            "timing": ["timing", "sdc"],
            "physical": ["physical", "drc", "lvs"],
            "signoff": ["signoff", "drc", "lvs", "timing"],
        }
        for needle, mapped in mapping.items():
            if needle in stage_lower:
                tags.extend(mapped)
        return tags


def build_hardware_context(
    query: str,
    stage: str = "",
    target_pdk: str = "",
    limit: int = 4,
) -> str:
    """Convenience helper for prompt builders."""
    return HardwareKnowledgeBase().build_context(
        query=query,
        stage=stage,
        target_pdk=target_pdk,
        limit=limit,
    )
