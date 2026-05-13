"""
Advanced VLSI RAG Engine
=======================

Multi-stage hybrid retrieval pipeline:
  1. Query analysis (domain, node, abbreviation expansion, planning)
  2. Hybrid retrieval (dense vector + BM25 sparse)
  3. Merge + deduplicate
  4. Reranking
  5. Parent-context expansion
  6. Confidence-based second-pass
  7. Context compression
  8. Grounded answer generation
  9. Guardrail verification

Usage:
    kb = VLSIKnowledgeBase()
    result = kb.answer("How does DIBL affect Vth in FinFETs?")
    print(result["answer"])
"""

import os
import re
import math
import copy
import uuid
import json
import atexit
import logging
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from collections import Counter
from openai import OpenAI

from ..config import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Domain & Node Classification
# ═══════════════════════════════════════════════════════════════════════════════

VLSI_DOMAINS = {
    "device_physics": [
        "FinFET", "MOSFET", "threshold voltage", "DIBL", "leakage",
        "bandgap", "BSIM", "short channel", "velocity saturation",
        "mobility", "doping", "body effect", "subthreshold swing",
        "gate oxide", "tunneling", "GAA", "nanosheet", "RibbonFET",
    ],
    "timing": [
        "setup time", "hold time", "slack", "STA", "clock skew",
        "OCV", "CPPR", "clock jitter", "timing closure", "pipeline",
        "data path", "critical path", "clock gating", "multicycle",
        "false path", "sdc", "delay", "transition", "slew",
    ],
    "power": [
        "dynamic power", "leakage power", "clock gating", "DVFS",
        "power gating", "IR drop", "electromigration", "power grid",
        "low power", "voltage island", "power intent", "UPF",
        "switching activity", "short circuit power",
    ],
    "physical_design": [
        "floorplan", "placement", "routing", "DRC", "LVS",
        "CTS", "antenna", "metal stack", "via", "std cell",
        "pin access", "congestion", "design rule", "tap cell",
        "end cap", "decap", "power rail", "track",
    ],
    "rtl": [
        "Verilog", "SystemVerilog", "RTL", "always", "module",
        "synthesis", "combinational", "sequential", "FSM",
        "state machine", "pipeline", "register", "flip-flop",
        "latch", "case", "assign", "parameter", "interface",
    ],
    "verification": [
        "UVM", "testbench", "assertion", "SVA", "coverage",
        "simulation", "formal", "property", "checker", "monitor",
        "driver", "scoreboard", "sequence", "transaction",
    ],
    "analog": [
        "op-amp", "OTA", "ADC", "DAC", "PLL", "noise",
        "bandwidth", "gain margin", "phase margin", "feedback",
        "mismatch", "offset", "CMRR", "PSRR", "linearity",
    ],
}

VLSI_NODES = [
    "2nm", "3nm", "5nm", "7nm", "10nm", "14nm",
    "28nm", "45nm", "65nm", "90nm", "130nm", "180nm",
]

# ═══════════════════════════════════════════════════════════════════════════════
# Abbreviation Dictionary (Step 3)
# ═══════════════════════════════════════════════════════════════════════════════

VLSI_ABBREVIATIONS = {
    "DRC": "design rule check",
    "LVS": "layout versus schematic",
    "CTS": "clock tree synthesis",
    "OCV": "on-chip variation",
    "CPPR": "common path pessimism removal",
    "STA": "static timing analysis",
    "EM": "electromigration",
    "IR drop": "voltage drop in power delivery network",
    "DVFS": "dynamic voltage and frequency scaling",
    "UPF": "unified power format",
    "SDC": "synopsys design constraints",
    "SVA": "systemverilog assertions",
    "UVM": "universal verification methodology",
    "DIBL": "drain-induced barrier lowering",
    "GAA": "gate-all-around",
    "FinFET": "fin field-effect transistor",
    "MOSFET": "metal-oxide-semiconductor field-effect transistor",
    "CMOS": "complementary metal-oxide-semiconductor",
    "RTL": "register-transfer level",
    "FSM": "finite state machine",
    "PLL": "phase-locked loop",
    "ADC": "analog-to-digital converter",
    "DAC": "digital-to-analog converter",
    "OTA": "operational transconductance amplifier",
    "OP-AMP": "operational amplifier",
    "SoC": "system-on-chip",
    "VLSI": "very-large-scale integration",
    "EDA": "electronic design automation",
    "PDK": "process design kit",
    "GDS": "graphic database system",
    "LEF": "library exchange format",
    "DEF": "design exchange format",
    "SPEF": "standard parasitic exchange format",
    "SDF": "standard delay format",
    "LIB": "liberty library format",
    "CDL": "circuit description language",
    "PVT": "process voltage temperature",
    "ECO": "engineering change order",
    "DFT": "design for test",
    "ATPG": "automatic test pattern generation",
    "MBIST": "memory built-in self-test",
    "BIST": "built-in self-test",
}

# ═══════════════════════════════════════════════════════════════════════════════
# Data Models (Step 1 — enhanced metadata)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ChunkMetadata:
    source: str = ""
    source_id: str = ""
    source_type: str = "builtin"
    domain: str = "general"
    node: str = "general"
    pdk: str = ""
    page: int = 0
    title: str = ""
    tags: List[str] = field(default_factory=list)
    parent_id: str = ""       # parent section/chunk ID for expansion
    chapter: str = ""
    section: str = ""
    author: str = ""
    year: int = 0
    content_kind: str = ""

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "domain": self.domain,
            "node": self.node,
            "pdk": self.pdk,
            "page": self.page,
            "title": self.title,
            "tags": self.tags or [],
            "parent_id": self.parent_id,
            "chapter": self.chapter,
            "section": self.section,
            "author": self.author,
            "year": self.year,
            "content_kind": self.content_kind,
        }


@dataclass
class Chunk:
    text: str
    metadata: ChunkMetadata
    chunk_id: str = ""
    vector: Optional[List[float]] = None

    def __post_init__(self):
        if not self.chunk_id:
            self.chunk_id = hashlib.md5(
                f"{self.metadata.source}:{self.text[:200]}".encode()
            ).hexdigest()


@dataclass
class RetrievalHit:
    chunk: Chunk
    score: float
    method: str = "vector"

# ═══════════════════════════════════════════════════════════════════════════════
# Built-in Knowledge Chunks
# ═══════════════════════════════════════════════════════════════════════════════


_BUILTIN_CHUNKS: List[Chunk] = [
    Chunk(
        text="Use one clocked always block for registers and separate combinational logic. "
        "Assign every combinational output on every path, reset all state explicitly, "
        "avoid delays, initial blocks for design state, force/release, hierarchical references, "
        "and unsized arithmetic in critical datapaths.",
        metadata=ChunkMetadata(
            title="Synthesizable Verilog Discipline",
            tags=["rtl", "verilog", "synthesis", "lint"],
            domain="rtl",
            source="builtin",
            source_type="builtin",
        ),
    ),
    Chunk(
        text="Size constants and casts to the destination width. Extend operands before addition, "
        "subtraction, shifts, and comparisons. For counters, compare against a same-width localparam "
        "and avoid truncating carry bits unless the wrap behavior is specified.",
        metadata=ChunkMetadata(
            title="Width-Safe RTL",
            tags=["rtl", "width", "verilator", "lint"],
            domain="rtl",
            source="builtin",
            source_type="builtin",
        ),
    ),
    Chunk(
        text="Single-bit CDC controls need two-flop synchronizers. Multi-bit data crossing domains should "
        "use async FIFOs, toggle handshakes, or valid/ready protocols with stable payload windows. "
        "Never synchronize each bit of a multi-bit bus independently unless it is Gray-coded.",
        metadata=ChunkMetadata(
            title="CDC Handshake Pattern",
            tags=["cdc", "clock-domain", "verification"],
            domain="verification",
            source="builtin",
            source_type="builtin",
        ),
    ),
    Chunk(
        text="Keep FSM states enumerated with a safe default transition. Add assumptions for legal inputs "
        "and assertions for reset convergence, no illegal states, request eventually acknowledged, "
        "FIFO bounds, and one-hot or mutually-exclusive grants.",
        metadata=ChunkMetadata(
            title="Formal-Friendly Control Logic",
            tags=["formal", "verification", "sva", "fsm"],
            domain="verification",
            source="builtin",
            source_type="builtin",
        ),
    ),
    Chunk(
        text="A production testbench should be self-checking, deterministic, reset-aware, and should end "
        "with an explicit pass/fail marker. Scoreboards should compare observable behavior instead "
        "of internal implementation details.",
        metadata=ChunkMetadata(
            title="Testbench Self-Checking Rules",
            tags=["testbench", "verification", "regression"],
            domain="verification",
            source="builtin",
            source_type="builtin",
        ),
    ),
    Chunk(
        text="Negative setup slack is usually improved by pipelining long datapaths, reducing fanout, "
        "registering outputs, avoiding large priority chains, and tightening unrealistic combinational "
        "logic. Hold issues should not be fixed by changing RTL latency unless the interface contract permits it.",
        metadata=ChunkMetadata(
            title="Timing Closure Tactics",
            tags=["timing", "sdc", "synthesis", "physical"],
            domain="timing",
            source="builtin",
            source_type="builtin",
        ),
    ),
    Chunk(
        text="For SKY130 and GF180, keep clocks conservative unless the design is shallow and pipelined. "
        "Predictive PDKs such as ASAP7 are useful for research comparisons but are not foundry tapeout kits. "
        "Commercial PDKs require manually installed Liberty, LEF, tech, DRC, and LVS decks.",
        metadata=ChunkMetadata(
            title="Open-Source PDK Practical Limits",
            tags=["pdk", "sky130", "gf180mcu", "asap7", "openroad"],
            domain="physical_design",
            source="builtin",
            source_type="builtin",
        ),
    ),
    Chunk(
        text="Do not treat GDS as signoff-ready until DRC is clean, LVS matches, extracted timing is reviewed, "
        "power intent is consistent, and generated reports are archived with tool versions and PDK corner data.",
        metadata=ChunkMetadata(
            title="Physical Verification Checklist",
            tags=["drc", "lvs", "signoff", "physical"],
            domain="physical_design",
            source="builtin",
            source_type="builtin",
        ),
    ),
    Chunk(
        text="RTL linting with Verilator requires strict width matching. Every signal assignment, "
        "port connection, arithmetic operation, and parameter comparison must have matching widths. "
        "Use $clog2 for address width calculation. Explicitly cast or extend operands before operations. "
        "Undriven nets and unused inputs cause lint warnings that should be resolved.",
        metadata=ChunkMetadata(
            title="Verilator Lint Rules",
            tags=["verilator", "lint", "rtl", "verification"],
            domain="rtl",
            source="builtin",
            source_type="builtin",
        ),
    ),
    Chunk(
        text="Clock tree synthesis (CTS) balances clock skew across all sequential elements. "
        "Key metrics: insertion delay, skew, and power. H-tree and mesh topologies distribute "
        "clocks with minimum skew. Useful skew can fix setup violations by delaying early paths.",
        metadata=ChunkMetadata(
            title="Clock Tree Synthesis Basics",
            tags=["cts", "timing", "physical", "clock"],
            domain="timing",
            source="builtin",
            source_type="builtin",
        ),
    ),
    Chunk(
        text="Standard cell libraries contain combinational (AND, OR, MUX, XOR, AOI, OAI) and "
        "sequential (DFF, latch, scan-DFF) cells. Cells are characterized at multiple PVT corners "
        "with Liberty (.lib) files containing timing arcs, power tables, and constraints. "
        "Multi-corner STA checks setup (max delay, fast process) and hold (min delay, slow process).",
        metadata=ChunkMetadata(
            title="Standard Cell Library Fundamentals",
            tags=["stdcell", "library", "liberty", "timing", "synthesis"],
            domain="physical_design",
            source="builtin",
            source_type="builtin",
        ),
    ),
    Chunk(
        text="Good floorplanning starts with: (1) Placing memory and analog macros at periphery, "
        "(2) Grouping related logic to minimize wirelength, (3) Reserving routing tracks for clock and power, "
        "(4) Setting utilization targets (50-70% for most designs), (5) Placing I/O pads with signal integrity "
        "considerations. Aspect ratio near 1:1 minimizes wirelength.",
        metadata=ChunkMetadata(
            title="Floorplan Guidelines",
            tags=["floorplan", "physical", "placement"],
            domain="physical_design",
            source="builtin",
            source_type="builtin",
        ),
    ),
]

# ═══════════════════════════════════════════════════════════════════════════════
# Classification Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def classify_domain(text: str) -> str:
    text_lower = text.lower()
    domain_scores: Dict[str, int] = {}
    for domain, keywords in VLSI_DOMAINS.items():
        score = sum(1 for kw in keywords if kw.lower() in text_lower)
        if score > 0:
            domain_scores[domain] = score
    return max(domain_scores, key=domain_scores.get) if domain_scores else "general"


def classify_node(text: str) -> str:
    for node in VLSI_NODES:
        if node in text:
            return node
    return "general"


def classify_source_type(filepath: str) -> str:
    ext = Path(filepath).suffix.lower()
    name = Path(filepath).name.lower()
    if ext == ".pdf":
        return "book" if any(k in name for k in ["book", "text", "chapt", "cmos", "digital", "vlsi"]) else "pdk_doc"
    if ext == ".md":
        return "user_doc"
    if ext == ".txt":
        return "user_doc"
    if ext == ".v" or ext == ".sv":
        return "pdk_verilog"
    if ext == ".lib":
        return "pdk_liberty"
    if ext in (".sp", ".spice", ".cdl"):
        return "pdk_spice"
    if ext == ".sdc":
        return "pdk_doc"
    if ext == ".tcl":
        return "pdk_doc"
    if ext == ".lef":
        return "pdk_doc"
    return "user_doc"


def _infer_tags(name: str, text: str) -> List[str]:
    haystack = f"{name} {text}".lower()
    known = [
        "rtl", "verilog", "systemverilog", "cdc", "formal", "sva",
        "timing", "sdc", "physical", "drc", "lvs", "pdk", "testbench",
        "coverage", "floorplan", "placement", "routing", "synthesis",
        "power", "analog", "finfet", "clock", "sta",
    ]
    return [tag for tag in known if tag in haystack] or ["hardware"]


# ═══════════════════════════════════════════════════════════════════════════════
# Abbreviation Expansion (Step 3)
# ═══════════════════════════════════════════════════════════════════════════════


def expand_abbreviations(text: str) -> str:
    """Replace known VLSI abbreviations with expanded forms."""
    result = text
    for abbr, expansion in sorted(VLSI_ABBREVIATIONS.items(), key=lambda x: -len(x[0])):
        pattern = re.compile(r'\b' + re.escape(abbr) + r'\b', re.IGNORECASE)
        result = pattern.sub(f"{abbr} ({expansion})", result)
    return result


def expand_query(query: str) -> List[str]:
    """Generate multiple query variants with expanded abbreviations."""
    queries = [query]
    expanded = expand_abbreviations(query)
    if expanded != query:
        queries.append(expanded)
    for abbr, expansion in VLSI_ABBREVIATIONS.items():
        if re.search(r'\b' + re.escape(abbr) + r'\b', query, re.IGNORECASE):
            queries.append(re.sub(
                r'\b' + re.escape(abbr) + r'\b',
                expansion,
                query,
                flags=re.IGNORECASE,
            ))
    return list(dict.fromkeys(queries))  # deduplicate preserving order


# ═══════════════════════════════════════════════════════════════════════════════
# Query Planning (Step 8)
# ═══════════════════════════════════════════════════════════════════════════════


def plan_query(query: str) -> List[str]:
    """Decompose complex queries into focused sub-queries."""
    if len(query.split()) < 8:
        return [query]
    sub_queries = [query]
    connectors = [" and ", " what about ", " how about ", " also ", " additionally "]
    for conn in connectors:
        if conn in query.lower():
            parts = re.split(re.escape(conn), query, flags=re.IGNORECASE)
            if len(parts) > 1:
                sub_queries.extend([p.strip().rstrip("?.") + "?" for p in parts])
    return list(dict.fromkeys(sub_queries))


# ═══════════════════════════════════════════════════════════════════════════════
# BM25 Sparse Retrieval (Step 4)
# ═══════════════════════════════════════════════════════════════════════════════


class BM25Index:
    """In-memory BM25 sparse retrieval index."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_freqs: List[Counter] = []
        self.doc_lengths: List[int] = []
        self.idf: Dict[str, float] = {}
        self.avg_doc_length: float = 0.0
        self.vocab: set = set()
        self.doc_texts: List[str] = []
        self.doc_ids: List[str] = []
        self.doc_metadata: List[Dict] = []
        self.built = False

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r'[a-zA-Z0-9_]+', text.lower())

    def index(self, chunk_id: str, text: str, metadata: dict):
        tokens = self._tokenize(text)
        self.doc_freqs.append(Counter(tokens))
        self.doc_lengths.append(len(tokens))
        self.doc_texts.append(text)
        self.doc_ids.append(chunk_id)
        self.doc_metadata.append(metadata)
        self.vocab.update(tokens)

    def build(self):
        n = len(self.doc_freqs)
        if n == 0:
            self.built = True
            return
        self.avg_doc_length = sum(self.doc_lengths) / n
        df: Counter = Counter()
        for freq in self.doc_freqs:
            df.update(freq.keys())
        self.idf = {
            term: math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5) + 1)
            for term in self.vocab
        }
        self.built = True

    def search(self, query: str, top_k: int = 30) -> List[Tuple[int, float]]:
        if not self.built or not self.doc_freqs:
            return []
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
        scores = []
        for i, freq in enumerate(self.doc_freqs):
            score = 0.0
            for qt in set(query_tokens):
                if qt not in self.idf:
                    continue
                tf = freq.get(qt, 0)
                if tf == 0:
                    continue
                idf = self.idf[qt]
                num = tf * (self.k1 + 1)
                denom = tf + self.k1 * (1 - self.b + self.b * self.doc_lengths[i] / self.avg_doc_length)
                score += idf * num / denom
            if score > 0:
                scores.append((i, score))
        scores.sort(key=lambda x: -x[1])
        return scores[:top_k]


# ═══════════════════════════════════════════════════════════════════════════════
# Chunking (Step 2 — structure-aware)
# ═══════════════════════════════════════════════════════════════════════════════

CHUNK_CONFIG = {
    "book":         {"chunk_size": 600,  "overlap": 100},
    "pdk_doc":      {"chunk_size": 400,  "overlap": 80},
    "pdk_spice":    {"chunk_size": 800,  "overlap": 0},
    "pdk_liberty":  {"chunk_size": 500,  "overlap": 0},
    "pdk_verilog":  {"chunk_size": 600,  "overlap": 0},
    "paper":        {"chunk_size": 500,  "overlap": 100},
    "user_doc":     {"chunk_size": 500,  "overlap": 80},
    "builtin":      {"chunk_size": 1000, "overlap": 0},
}

MAX_CHUNK_CHARS = 12000


def _split_oversized(text: str, source: str, max_chars: int = MAX_CHUNK_CHARS) -> List[str]:
    parts = []
    while len(text) > max_chars:
        split_at = text.rfind("\n\n", 0, max_chars)
        if split_at == -1:
            split_at = text.rfind(". ", 0, max_chars)
        if split_at == -1:
            split_at = text.rfind(" ", 0, max_chars)
        if split_at == -1:
            split_at = max_chars
        parts.append(text[:split_at].strip())
        text = text[split_at:].strip()
    if text:
        parts.append(text)
    return parts


def smart_chunk(text: str, source: str = "", source_type: str = "user_doc",
                title: str = "", chapter: str = "", section: str = "") -> List[Chunk]:
    cfg = CHUNK_CONFIG.get(source_type, {"chunk_size": 500, "overlap": 100})
    chunk_size = cfg["chunk_size"]
    overlap = cfg["overlap"]

    if source_type == "pdk_liberty":
        chunks = _chunk_by_liberty_cell(text, source)
    elif source_type == "pdk_verilog":
        chunks = _chunk_by_verilog_module(text, source)
    elif source_type == "pdk_spice":
        chunks = _chunk_by_subcircuit(text, source)
    elif source_type in ("pdk_doc",):
        chunks = _chunk_by_paragraph(text, source, chunk_size, overlap)
    else:
        chunks = _chunk_by_structure(text, source, chunk_size, overlap)

    final = []
    for c in chunks:
        if len(c.text) > MAX_CHUNK_CHARS:
            parts = _split_oversized(c.text, source)
            for p in parts:
                new_c = Chunk(text=p, metadata=copy.deepcopy(c.metadata))
                new_c.metadata.title = title or c.metadata.title
                new_c.metadata.chapter = chapter or c.metadata.chapter
                new_c.metadata.section = section or c.metadata.section
                final.append(new_c)
        else:
            c.metadata.title = title or c.metadata.title
            c.metadata.chapter = chapter or c.metadata.chapter
            c.metadata.section = section or c.metadata.section
            final.append(c)

    for chunk in final:
        chunk.metadata.source_type = source_type
        chunk.metadata.domain = classify_domain(chunk.text)
        chunk.metadata.node = classify_node(chunk.text)
        chunk.metadata.tags = _infer_tags(Path(source).name, chunk.text)

    return final


def _chunk_by_paragraph(text: str, source: str, chunk_size: int, overlap: int) -> List[Chunk]:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text or "") if b.strip()]
    chunks: List[Chunk] = []
    current = ""
    for block in blocks:
        if len(current) + len(block) + 2 <= chunk_size:
            current = f"{current}\n\n{block}".strip()
        else:
            if current:
                chunks.append(Chunk(text=current, metadata=ChunkMetadata(source=source)))
            current = block[:chunk_size]
    if current:
        chunks.append(Chunk(text=current, metadata=ChunkMetadata(source=source)))

    if overlap > 0 and len(chunks) > 1:
        merged = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = merged[-1].text
            curr = chunks[i].text
            overlap_text = _find_overlap_text(prev, curr, overlap)
            if overlap_text:
                merged.append(Chunk(text=overlap_text, metadata=ChunkMetadata(source=source)))
            merged.append(chunks[i])
        chunks = merged

    return chunks


def _chunk_by_structure(text: str, source: str, chunk_size: int, overlap: int) -> List[Chunk]:
    """Split text by document structure (headings) instead of just paragraphs.

    Detects Markdown, RST, numbered, and chapter/section heading patterns.
    Each section becomes one chunk unless it exceeds chunk_size (sub-split by paragraph).
    """
    lines = (text or "").split("\n")

    HEADING_PATTERNS = [
        (re.compile(r"^#{1,6}\s+\S"), 1),
        (re.compile(r"^[A-Z][A-Za-z\s]{2,60}\n={3,}\s*$", re.MULTILINE), 1),
        (re.compile(r"^[A-Z][A-Za-z\s]{2,60}\n-{3,}\s*$", re.MULTILINE), 2),
        (re.compile(r"^(?:CHAPTER|Chapter|SECTION|Section|Lecture|Topic)\s+\S"), 1),
        (re.compile(r"^\d+\.\d+(?:\.\d+)*\s+\S"), 1),
        (re.compile(r"^[A-Z][A-Z\s]{3,50}$"), 2),
    ]

    heading_indices = []
    for i, line in enumerate(lines):
        for pat, _level in HEADING_PATTERNS:
            if isinstance(pat, re.Pattern):
                if pat.match(line.lstrip()):
                    heading_indices.append((i, line.strip()))
                    break
            else:
                m = re.match(pat, line.lstrip())
                if m:
                    heading_indices.append((i, line.strip()))
                    break

    if not heading_indices:
        return _chunk_by_paragraph(text, source, chunk_size, overlap)

    sections = []
    for idx, (start, heading) in enumerate(heading_indices):
        end = heading_indices[idx + 1][0] if idx + 1 < len(heading_indices) else len(lines)
        body = "\n".join(lines[start:end]).strip()
        sections.append((heading, body))

    chunks = []
    for heading, body in sections:
        body_len = len(body)
        if body_len <= chunk_size:
            chunks.append(Chunk(text=body, metadata=ChunkMetadata(source=source)))
        else:
            paras = [b.strip() for b in re.split(r"\n\s*\n", body) if b.strip()]
            current = heading
            for para in paras:
                candidate = f"{current}\n\n{para}".strip()
                if len(candidate) <= chunk_size:
                    current = candidate
                else:
                    if current and len(current) > len(heading):
                        chunks.append(Chunk(text=current, metadata=ChunkMetadata(source=source)))
                    current = f"{heading}\n\n{para[:max(0, chunk_size - len(heading) - 2)]}".strip()
            if current and len(current) > len(heading):
                chunks.append(Chunk(text=current, metadata=ChunkMetadata(source=source)))

    if overlap > 0 and len(chunks) > 1:
        merged = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = merged[-1].text
            curr = chunks[i].text
            overlap_text = _find_overlap_text(prev, curr, overlap)
            if overlap_text:
                merged.append(Chunk(text=overlap_text, metadata=ChunkMetadata(source=source)))
            merged.append(chunks[i])
        chunks = merged

    return chunks


def _find_overlap_text(prev: str, curr: str, target_chars: int) -> str:
    words = prev.split()
    overlap_words = []
    count = 0
    for word in reversed(words):
        if count >= target_chars:
            break
        overlap_words.insert(0, word)
        count += len(word) + 1
    if overlap_words and " ".join(overlap_words) in curr:
        return " ".join(overlap_words)
    return ""


def _chunk_by_liberty_cell(text: str, source: str) -> List[Chunk]:
    chunks = []
    current_cell = []
    cell_name = ""
    for line in text.split("\n"):
        cell_match = re.match(r"cell\s*\(\s*(\S+)\s*\)", line)
        if cell_match:
            if current_cell:
                chunks.append(Chunk(
                    text="\n".join(current_cell),
                    metadata=ChunkMetadata(source=source, title=cell_name),
                ))
            current_cell = [line]
            cell_name = cell_match.group(1)
        elif current_cell is not None:
            current_cell.append(line)
    if current_cell:
        chunks.append(Chunk(
            text="\n".join(current_cell),
            metadata=ChunkMetadata(source=source, title=cell_name),
        ))
    return chunks


def _chunk_by_verilog_module(text: str, source: str) -> List[Chunk]:
    chunks = []
    current_module = []
    module_name = ""
    in_module = False
    brace_depth = 0
    for line in text.split("\n"):
        mod_match = re.match(r"\s*module\s+(\S+)", line)
        if mod_match and not in_module:
            if current_module:
                chunks.append(Chunk(
                    text="\n".join(current_module),
                    metadata=ChunkMetadata(source=source, title=module_name),
                ))
            current_module = [line]
            module_name = mod_match.group(1)
            in_module = True
            brace_depth = line.count("(") - line.count(")")
        elif in_module:
            current_module.append(line)
            brace_depth += line.count("(") - line.count(")")
            if re.match(r"\s*endmodule\b", line) and brace_depth <= 0:
                in_module = False
    if current_module:
        chunks.append(Chunk(
            text="\n".join(current_module),
            metadata=ChunkMetadata(source=source, title=module_name),
        ))
    return chunks


def _chunk_by_subcircuit(text: str, source: str) -> List[Chunk]:
    chunks = []
    current = []
    name = ""
    for line in text.split("\n"):
        sub_match = re.match(r"\.subckt\s+(\S+)", line, re.IGNORECASE)
        if sub_match:
            if current:
                chunks.append(Chunk(
                    text="\n".join(current),
                    metadata=ChunkMetadata(source=source, title=name),
                ))
            current = [line]
            name = sub_match.group(1)
        elif re.match(r"\.ends", line, re.IGNORECASE):
            if current:
                current.append(line)
                chunks.append(Chunk(
                    text="\n".join(current),
                    metadata=ChunkMetadata(source=source, title=name),
                ))
                current = []
        elif current is not None:
            current.append(line)
    if current:
        chunks.append(Chunk(
            text="\n".join(current),
            metadata=ChunkMetadata(source=source, title=name),
        ))
    return chunks


# ═══════════════════════════════════════════════════════════════════════════════
# Parent-Document Expansion (Step 6)
# ═══════════════════════════════════════════════════════════════════════════════

def expand_with_parent(chunks: List[Chunk], kb: "VLSIKnowledgeBase",
                       max_expansion_chars: int = 2000) -> List[Chunk]:
    """Expand child chunks with their parent section context."""
    expanded = []
    for chunk in chunks:
        parent_id = chunk.metadata.parent_id
        if parent_id:
            try:
                results = kb.client.scroll(
                    collection_name=kb.collection_name,
                    limit=1,
                    with_payload=True,
                    with_vectors=False,
                )[0]
                for r in results:
                    pid = r.payload.get("parent_id", "")
                    cid = r.payload.get("chunk_id", "")
                    if cid == parent_id or pid == parent_id:
                        parent_text = r.payload.get("text", "")
                        combined = f"[Parent context]: {parent_text[:max_expansion_chars]}\n\n[Chunk]: {chunk.text}"
                        new_chunk = copy.deepcopy(chunk)
                        new_chunk.text = combined[:max_expansion_chars + 1000]
                        expanded.append(new_chunk)
                        break
                else:
                    expanded.append(chunk)
            except Exception:
                expanded.append(chunk)
        else:
            expanded.append(chunk)
    return expanded


# ═══════════════════════════════════════════════════════════════════════════════
# Context Compression (Step 10)
# ═══════════════════════════════════════════════════════════════════════════════

def compress_context(hits: List[RetrievalHit], max_chunks: int = 6) -> List[RetrievalHit]:
    """Remove duplicates, favour source diversity, keep best evidence."""
    seen_texts: set = set()
    seen_sources: set = set()
    selected: List[RetrievalHit] = []
    source_type_counts: Counter = Counter()

    for hit in hits:
        text = hit.chunk.text.strip()[:100]
        source = hit.chunk.metadata.source
        stype = hit.chunk.metadata.source_type

        if text in seen_texts:
            continue
        if source in seen_sources and source_type_counts[stype] >= 2:
            continue

        selected.append(hit)
        seen_texts.add(text)
        seen_sources.add(source)
        source_type_counts[stype] += 1

        if len(selected) >= max_chunks:
            break

    return selected


# ═══════════════════════════════════════════════════════════════════════════════
# Confidence & Second-Pass (Step 9)
# ═══════════════════════════════════════════════════════════════════════════════

def needs_second_pass(hits: List[RetrievalHit], query: str, threshold: float = 0.5) -> Tuple[bool, str]:
    """Determine if first-pass retrieval is weak enough to warrant a second pass."""
    if not hits:
        return True, "no results"

    avg_score = sum(h.score for h in hits) / len(hits)
    if avg_score < threshold * 0.5:
        return True, f"low average score ({avg_score:.3f})"

    sources = set(h.chunk.metadata.source for h in hits)
    if len(sources) < 2:
        return True, "insufficient source diversity"

    domains = set(h.chunk.metadata.domain for h in hits)
    query_domain = classify_domain(query)
    if query_domain != "general" and query_domain not in domains:
        return True, f"no matches in expected domain ({query_domain})"

    query_node = classify_node(query)
    if query_node != "general":
        has_node = any(h.chunk.metadata.node == query_node for h in hits)
        if not has_node:
            return True, f"no node-specific results for {query_node}"

    return False, ""


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-Encoder Reranker (Step 5)
# ═══════════════════════════════════════════════════════════════════════════════

class Reranker:
    """Lightweight reranker: combines vector score + BM25 score + domain bonus."""

    def __init__(self):
        self._model = None

    def rerank(self, query: str, hits: List[RetrievalHit],
               query_domain: str = "", top_k: int = 8) -> List[RetrievalHit]:
        if not hits:
            return []

        query_lower = query.lower()
        query_terms = set(re.findall(r'[a-zA-Z0-9_]+', query_lower))

        scored = []
        for hit in hits:
            score = hit.score * 0.6
            text = hit.chunk.text.lower()
            text_terms = set(re.findall(r'[a-zA-Z0-9_]+', text))
            overlap = query_terms.intersection(text_terms)
            lexical_score = len(overlap) / max(len(query_terms), 1)
            score += lexical_score * 0.25

            if query_domain and hit.chunk.metadata.domain == query_domain:
                score += 0.1

            query_node = classify_node(query)
            if query_node != "general" and hit.chunk.metadata.node == query_node:
                score += 0.05

            scored.append((hit, score))

        scored.sort(key=lambda x: -x[1])
        return [s[0] for s in scored[:top_k]]


# ═══════════════════════════════════════════════════════════════════════════════
# Embedding
# ═══════════════════════════════════════════════════════════════════════════════

class EmbeddingEngine:
    """Produces embeddings for text chunks. Uses SentenceTransformers locally
    with optional OpenAI / NVIDIA NIM for higher quality."""

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        use_openai: bool = False,
        embedding_mode: Optional[str] = None,
    ):
        self.model_name = model_name
        self.use_openai = use_openai
        self.embedding_mode = embedding_mode
        self._model = None
        self._openai_client = None
        self._dimension = 384
        if "text-embedding-3-large" in model_name:
            self._dimension = 3072
        elif "text-embedding-3-small" in model_name:
            self._dimension = 1536
        elif "all-MiniLM" in model_name:
            self._dimension = 384
        elif "nv-embed" in model_name:
            self._dimension = 4096

    @property
    def dimension(self) -> int:
        return self._dimension

    def _load_model(self):
        if self._model is None and not self.use_openai:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.model_name)
                try:
                    self._dimension = self._model.get_embedding_dimension()
                except AttributeError:
                    self._dimension = self._model.get_sentence_embedding_dimension()
            except Exception as e:
                logger.warning(f"Failed to load SentenceTransformer '{self.model_name}': {e}")
                logger.warning("Falling back to simple hash-based embedding")
                self._model = None

    def _get_openai_embedding(self, text: str) -> List[float]:
        if self._openai_client is None:
            try:
                from openai import OpenAI
                api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
                base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("LLM_BASE_URL") or "https://api.openai.com/v1"
                self._openai_client = OpenAI(api_key=api_key, base_url=base_url)
            except Exception as e:
                logger.error(f"Failed to init OpenAI client: {e}")
                return self._fallback_embed(text)

        kwargs = dict(input=text, model=self.model_name)
        if self.embedding_mode in ("query", "passage"):
            kwargs["extra_body"] = {"input_type": self.embedding_mode}

        try:
            response = self._openai_client.embeddings.create(**kwargs)
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"OpenAI embedding failed: {e}")
            return self._fallback_embed(text)

    def _fallback_embed(self, text: str) -> List[float]:
        h = hashlib.md5(text.encode())
        seed = int(h.hexdigest()[:8], 16)
        rng = __import__("random").Random(seed)
        return [rng.gauss(0, 0.1) for _ in range(self._dimension)]

    def embed(self, text: str, mode: Optional[str] = None) -> List[float]:
        prev_mode = self.embedding_mode
        if mode is not None:
            self.embedding_mode = mode
        try:
            if self.use_openai:
                return self._get_openai_embedding(text)
            self._load_model()
            if self._model is not None:
                try:
                    emb = self._model.encode(text, normalize_embeddings=True)
                    return emb.tolist()
                except Exception as e:
                    logger.warning(f"Embedding failed: {e}")
                    return self._fallback_embed(text)
            return self._fallback_embed(text)
        finally:
            if mode is not None:
                self.embedding_mode = prev_mode

    def embed_batch(self, texts: List[str], batch_size: int = 32, mode: Optional[str] = None) -> List[List[float]]:
        prev_mode = self.embedding_mode
        if mode is not None:
            self.embedding_mode = mode
        try:
            if self.use_openai or self._model is None:
                self._load_model()
                if self._model is None:
                    return [self.embed(t, mode=mode) for t in texts]
            try:
                embs = self._model.encode(texts, normalize_embeddings=True, batch_size=batch_size)
                return [e.tolist() for e in embs]
            except Exception as e:
                logger.warning(f"Batch embedding failed: {e}")
                return [self.embed(t, mode=mode) for t in texts]
        finally:
            if mode is not None:
                self.embedding_mode = prev_mode


# ═══════════════════════════════════════════════════════════════════════════════
# Main RAG Engine
# ═══════════════════════════════════════════════════════════════════════════════

class VLSIKnowledgeBase:
    """Multi-stage hybrid VLSI RAG engine.

    Pipeline:
        query → expand → plan → dense + BM25 → merge → rerank →
        parent-expand → compress → generate → guard
    """

    _instances: Dict[str, "VLSIKnowledgeBase"] = {}

    def __new__(cls, *args, **kwargs):
        db_path = kwargs.get("db_path") or os.environ.get("VLSI_RAG_DB_PATH", "") or str(Path.home() / ".agentic" / "vlsi_rag")
        if db_path not in cls._instances:
            instance = super().__new__(cls)
            instance._initialized = False
            cls._instances[db_path] = instance
        return cls._instances[db_path]

    def __init__(
        self,
        collection_name: str = "vlsi_knowledge",
        db_path: Optional[str] = None,
        embedding_model: str = "all-MiniLM-L6-v2",
        use_openai_embedding: bool = False,
        knowledge_dir: Optional[str] = None,
    ):
        if self._initialized:
            return
        self._initialized = True

        self.collection_name = collection_name
        self.db_path = Path(
            db_path
            or os.environ.get("VLSI_RAG_DB_PATH", "")
            or os.path.join(Path.home(), ".agentic", "vlsi_rag")
        )
        self.embedding = EmbeddingEngine(
            model_name=embedding_model,
            use_openai=use_openai_embedding,
            embedding_mode=os.environ.get("NVIDIA_EMBEDDING_MODE"),
        )

        self.knowledge_dir = Path(
            os.path.expanduser(
                knowledge_dir
                or os.environ.get("AGENTIC_KNOWLEDGE_DIR", "")
                or os.path.join(WORKSPACE_ROOT, "knowledge", "hardware")
            )
        )

        self._client = None
        self._builtin_indexed = False
        self._bm25: Optional[BM25Index] = None
        self.reranker = Reranker()

    @property
    def client(self):
        if self._client is None:
            self.db_path.mkdir(parents=True, exist_ok=True)
            from qdrant_client import QdrantClient
            self._client = QdrantClient(path=str(self.db_path))
            self._ensure_collection()
        return self._client

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def __del__(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass

    def _ensure_collection(self):
        from qdrant_client.models import VectorParams, Distance
        collections = [c.name for c in self.client.get_collections().collections]
        if self.collection_name not in collections:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=self.embedding.dimension,
                    distance=Distance.COSINE,
                ),
            )
            self._index_builtin_chunks()
            self._index_user_chunks()

    @staticmethod
    def _make_uuid(chunk_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id))

    def _point_from_chunk(self, chunk: Chunk) -> Any:
        from qdrant_client.models import PointStruct
        vec = chunk.vector
        if vec is None:
            vec = self.embedding.embed(chunk.text, mode="passage")
        return PointStruct(
            id=self._make_uuid(chunk.chunk_id),
            vector=vec,
            payload={
                "text": chunk.text[:10000],
                "chunk_id": chunk.chunk_id,
                **chunk.metadata.to_dict(),
            },
        )

    def _index_builtin_chunks(self):
        chunks = self._ensure_indexed()
        if not _BUILTIN_CHUNKS or any(c.chunk_id in chunks for c in _BUILTIN_CHUNKS):
            return
        points = []
        for chunk in _BUILTIN_CHUNKS:
            chunk.vector = self.embedding.embed(chunk.text, mode="passage")
            points.append(self._point_from_chunk(chunk))
        if points:
            self.client.upsert(collection_name=self.collection_name, points=points)
            logger.info(f"Indexed {len(points)} builtin VLSI knowledge chunks")

    def _index_user_chunks(self):
        if not self.knowledge_dir.is_dir():
            return
        chunks = self._load_user_chunks()
        existing = self._ensure_indexed()
        new_chunks = [c for c in chunks if c.chunk_id not in existing]
        if not new_chunks:
            return
        texts = [c.text for c in new_chunks]
        vectors = self.embedding.embed_batch(texts, mode="passage")
        points = []
        for chunk, vec in zip(new_chunks, vectors):
            chunk.vector = vec
            points.append(self._point_from_chunk(chunk))
        if points:
            self.client.upsert(collection_name=self.collection_name, points=points)
            logger.info(f"Indexed {len(points)} user knowledge chunks from {self.knowledge_dir}")

    def _ensure_indexed(self) -> set:
        return set()

    def _load_user_chunks(self) -> List[Chunk]:
        if not self.knowledge_dir.is_dir():
            return []
        chunks: List[Chunk] = []
        for path in sorted(self.knowledge_dir.rglob("*")):
            if path.suffix.lower() not in {".md", ".txt", ".sv", ".v", ".sdc", ".tcl"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            source_type = classify_source_type(str(path))
            file_chunks = smart_chunk(text, source=str(path), source_type=source_type)
            chunks.extend(file_chunks)
        return chunks

    # ── Ingestion ──────────────────────────────────────────────────────────

    def ingest_text(
        self,
        text: str,
        source: str = "manual",
        source_type: str = "user_doc",
        metadata: Optional[dict] = None,
    ):
        chunks = smart_chunk(text, source=source, source_type=source_type)
        texts = [c.text for c in chunks]
        vectors = self.embedding.embed_batch(texts, mode="passage")
        from qdrant_client.models import PointStruct
        points = []
        for chunk, vec in zip(chunks, vectors):
            chunk.vector = vec
            meta = chunk.metadata.to_dict()
            if metadata:
                meta.update(metadata)
            points.append(PointStruct(
                id=self._make_uuid(chunk.chunk_id),
                vector=vec,
                payload={"text": chunk.text[:10000], "chunk_id": chunk.chunk_id, **meta},
            ))
        self.client.upsert(collection_name=self.collection_name, points=points)
        logger.info(f"Ingested {len(points)} chunks from {source}")

    def ingest_file(self, filepath: str):
        path = Path(filepath)
        if not path.exists():
            logger.warning(f"File not found: {filepath}")
            return
        source_type = classify_source_type(filepath)
        if path.suffix.lower() == ".pdf":
            self._ingest_pdf(filepath)
            return
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            logger.warning(f"Cannot read {filepath}: {e}")
            return
        self.ingest_text(text, source=filepath, source_type=source_type)

    def _ingest_pdf(self, filepath: str):
        try:
            import pdfplumber
        except ImportError:
            logger.warning("pdfplumber not installed, skipping PDF ingestion")
            return
        chunks = []
        with pdfplumber.open(filepath) as pdf:
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text and len(text.strip()) > 50:
                    page_chunks = smart_chunk(text, source=filepath, source_type="book")
                    for c in page_chunks:
                        c.metadata.page = page_num + 1
                    chunks.extend(page_chunks)
        texts = [c.text for c in chunks]
        vectors = self.embedding.embed_batch(texts, mode="passage")
        from qdrant_client.models import PointStruct
        points = []
        for chunk, vec in zip(chunks, vectors):
            chunk.vector = vec
            points.append(PointStruct(
                id=self._make_uuid(chunk.chunk_id),
                vector=vec,
                payload={"text": chunk.text[:10000], "chunk_id": chunk.chunk_id, **chunk.metadata.to_dict()},
            ))
        self.client.upsert(collection_name=self.collection_name, points=points)
        logger.info(f"Ingested {len(points)} PDF chunks from {filepath}")

    # ── BM25 Index Rebuild ─────────────────────────────────────────────────

    def rebuild_bm25(self):
        """Rebuild the BM25 index from all stored chunks."""
        logger.info("Rebuilding BM25 index...")
        self._bm25 = BM25Index()
        try:
            all_points = self.client.scroll(
                collection_name=self.collection_name,
                limit=100000,
                with_payload=True,
                with_vectors=False,
            )[0]
            for point in all_points:
                payload = point.payload or {}
                text = payload.get("text", "")
                cid = payload.get("chunk_id", "")
                meta = {k: v for k, v in payload.items() if k not in ("text", "chunk_id")}
                if text:
                    self._bm25.index(cid, text, meta)
            self._bm25.build()
            logger.info(f"BM25 index built with {len(all_points)} documents")
        except Exception as e:
            logger.warning(f"BM25 rebuild failed: {e}")

    def _get_bm25(self) -> BM25Index:
        if self._bm25 is None:
            self.rebuild_bm25()
        return self._bm25

    # ── Retrieval Pipeline ─────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        domain: Optional[str] = None,
        node: Optional[str] = None,
        pdk: Optional[str] = None,
        source_types: Optional[List[str]] = None,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> List[RetrievalHit]:
        """Full multi-stage retrieval: expand → plan → dense + BM25 → merge → rerank → compress."""
        query_domain = domain or classify_domain(query)
        query_node = node or classify_node(query)

        expanded_queries = expand_query(query)
        sub_queries = []
        for eq in expanded_queries:
            sub_queries.extend(plan_query(eq))

        all_hits: List[RetrievalHit] = []
        for sq in list(dict.fromkeys(sub_queries)):
            hits = self._single_retrieve(
                query=sq,
                domain=query_domain if domain else None,
                node=query_node if node else None,
                pdk=pdk,
                source_types=source_types,
                top_k=top_k * 2,
            )
            all_hits.extend(hits)

        merged = self._merge_hits_with_bm25(
            query=query,
            vector_hits=all_hits,
            top_k=top_k * 3,
            domain_filter=query_domain,
            node_filter=query_node,
            pdk_filter=pdk,
            source_type_filter=source_types,
        )

        reranked = self.reranker.rerank(query, merged, query_domain=query_domain, top_k=top_k * 2)

        need_second, reason = needs_second_pass(reranked, query)
        if need_second:
            logger.info(f"Second-pass triggered: {reason}")
            relaxed = self._single_retrieve(
                query=query,
                domain=None,
                node=None,
                pdk=pdk,
                source_types=source_types,
                top_k=top_k * 3,
            )
            reranked.extend(relaxed)
            reranked = self._merge_hits_with_bm25(query, reranked, top_k=top_k * 3)
            reranked = self.reranker.rerank(query, reranked, top_k=top_k * 2)

        compressed = compress_context(reranked, max_chunks=top_k + 2)

        if min_score > 0:
            compressed = [h for h in compressed if h.score >= min_score]

        return compressed[:top_k]

    def _single_retrieve(
        self,
        query: str,
        domain: Optional[str],
        node: Optional[str],
        pdk: Optional[str],
        source_types: Optional[List[str]],
        top_k: int,
    ) -> List[RetrievalHit]:
        return self._vector_search(
            query=query,
            domain=domain,
            node=node,
            pdk=pdk,
            source_types=source_types,
            top_k=top_k,
        )

    def _vector_search(
        self,
        query: str,
        domain: Optional[str],
        node: Optional[str],
        pdk: Optional[str],
        source_types: Optional[List[str]],
        top_k: int,
    ) -> List[RetrievalHit]:
        query_vec = self.embedding.embed(query, mode="query")
        search_filter = self._build_filter(domain, node, pdk, source_types)

        try:
            results = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vec,
                limit=top_k,
                query_filter=search_filter,
                with_payload=True,
                score_threshold=0.3,
            )
            points = getattr(results, 'points', results) if not hasattr(results, 'points') else results.points
            return [
                RetrievalHit(
                    chunk=Chunk(
                        text=r.payload.get("text", ""),
                        metadata=ChunkMetadata(
                            source=r.payload.get("source", ""),
                            source_type=r.payload.get("source_type", ""),
                            domain=r.payload.get("domain", ""),
                            node=r.payload.get("node", ""),
                            pdk=r.payload.get("pdk", ""),
                            page=r.payload.get("page", 0),
                            title=r.payload.get("title", ""),
                            tags=r.payload.get("tags", []),
                            parent_id=r.payload.get("parent_id", ""),
                            chapter=r.payload.get("chapter", ""),
                            section=r.payload.get("section", ""),
                            author=r.payload.get("author", ""),
                            year=r.payload.get("year", 0),
                        ),
                        chunk_id=r.payload.get("chunk_id", ""),
                    ),
                    score=r.score,
                    method="vector",
                )
                for r in points
            ]
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            return []

    def _merge_hits_with_bm25(
        self,
        query: str,
        vector_hits: List[RetrievalHit],
        top_k: int,
        domain_filter: str = "",
        node_filter: str = "",
        pdk_filter: Optional[str] = None,
        source_type_filter: Optional[List[str]] = None,
    ) -> List[RetrievalHit]:
        """Merge vector hits with BM25 sparse hits."""
        seen: Dict[str, RetrievalHit] = {}
        for hit in vector_hits:
            cid = hit.chunk.chunk_id
            seen[cid] = hit

        try:
            bm25 = self._get_bm25()
            bm25_results = bm25.search(query, top_k=top_k)
            for idx, bm25_score in bm25_results:
                cid = bm25.doc_ids[idx]
                if cid in seen:
                    seen[cid].score = max(seen[cid].score, bm25_score * 0.1)
                    seen[cid].method = "hybrid"
                else:
                    meta_dict = bm25.doc_metadata[idx] if idx < len(bm25.doc_metadata) else {}
                    pd = meta_dict.get("domain", "")
                    pn = meta_dict.get("node", "")
                    pp = meta_dict.get("pdk", "")
                    ps = meta_dict.get("source_type", "")

                    if domain_filter and pd != domain_filter:
                        continue
                    if node_filter and pn != node_filter:
                        continue
                    if pdk_filter and pp != pdk_filter:
                        continue
                    if source_type_filter and ps not in source_type_filter:
                        continue

                    seen[cid] = RetrievalHit(
                        chunk=Chunk(
                            text=bm25.doc_texts[idx],
                            metadata=ChunkMetadata(
                                source=meta_dict.get("source", ""),
                                source_type=ps,
                                domain=pd,
                                node=pn,
                                pdk=pp,
                                page=meta_dict.get("page", 0),
                                title=meta_dict.get("title", ""),
                                tags=meta_dict.get("tags", []),
                                parent_id=meta_dict.get("parent_id", ""),
                                chapter=meta_dict.get("chapter", ""),
                                section=meta_dict.get("section", ""),
                            ),
                            chunk_id=cid,
                        ),
                        score=bm25_score * 0.1,
                        method="bm25",
                    )
        except Exception as e:
            logger.warning(f"BM25 merge failed: {e}")

        dedup = list(seen.values())
        dedup.sort(key=lambda h: h.score, reverse=True)
        return dedup[:top_k]

    def _keyword_search(
        self,
        query: str,
        domain: Optional[str],
        node: Optional[str],
        pdk: Optional[str],
        source_types: Optional[List[str]],
        top_k: int,
    ) -> List[RetrievalHit]:
        """Fallback lexical search when BM25 is unavailable."""
        query_terms = set(re.findall(r"[a-zA-Z0-9_]+", query.lower()))
        if not query_terms:
            return []

        try:
            all_points = self.client.scroll(
                collection_name=self.collection_name,
                limit=10000,
                with_payload=True,
                with_vectors=False,
            )[0]
        except Exception:
            return []

        hits = []
        for point in all_points:
            payload = point.payload or {}
            text = (payload.get("text") or "") + " " + (payload.get("title") or "")
            text_terms = set(re.findall(r"[a-zA-Z0-9_]+", text.lower()))
            overlap = query_terms.intersection(text_terms)
            if not overlap:
                continue
            score = sum(1.0 + math.log(1 + len(overlap)) for _ in overlap)
            score = score / (len(text_terms) + 1) * 100

            meta = payload
            pd = meta.get("domain", "") or ""
            pn = meta.get("node", "") or ""
            pp = meta.get("pdk", "") or ""
            ps = meta.get("source_type", "") or ""

            if domain and pd != domain:
                continue
            if node and pn != node:
                continue
            if pdk and pp != pdk:
                continue
            if source_types and ps not in source_types:
                continue

            chunk = Chunk(
                text=payload.get("text", ""),
                metadata=ChunkMetadata(
                    source=meta.get("source", ""),
                    source_type=ps,
                    domain=pd,
                    node=pn,
                    pdk=pp,
                    page=meta.get("page", 0),
                    title=meta.get("title", ""),
                    tags=meta.get("tags", []),
                    parent_id=meta.get("parent_id", ""),
                    chapter=meta.get("chapter", ""),
                    section=meta.get("section", ""),
                ),
                chunk_id=payload.get("chunk_id", ""),
            )
            hits.append(RetrievalHit(chunk=chunk, score=score, method="keyword"))

        return sorted(hits, key=lambda h: h.score, reverse=True)[:top_k]

    def _build_filter(
        self,
        domain: Optional[str],
        node: Optional[str],
        pdk: Optional[str],
        source_types: Optional[List[str]],
    ):
        from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny
        conditions = []
        if domain:
            conditions.append(FieldCondition(key="domain", match=MatchValue(value=domain)))
        if node:
            conditions.append(FieldCondition(key="node", match=MatchValue(value=node)))
        if pdk:
            conditions.append(FieldCondition(key="pdk", match=MatchValue(value=pdk)))
        if source_types:
            conditions.append(FieldCondition(key="source_type", match=MatchAny(any=source_types)))
        if conditions:
            return Filter(must=conditions)
        return None

    @staticmethod
    def _merge_hits(vector_hits: List[RetrievalHit], keyword_hits: List[RetrievalHit], top_k: int) -> List[RetrievalHit]:
        seen: Dict[str, RetrievalHit] = {}
        for hit in vector_hits:
            seen[hit.chunk.chunk_id] = hit
        for hit in keyword_hits:
            cid = hit.chunk.chunk_id
            if cid in seen:
                seen[cid].score = max(seen[cid].score, hit.score * 0.01)
            else:
                seen[cid] = hit
        return sorted(seen.values(), key=lambda h: h.score, reverse=True)[:top_k]

    # ── Context Building ───────────────────────────────────────────────────

    def build_context(
        self,
        query: str,
        stage: str = "",
        target_pdk: str = "",
        top_k: int = 4,
        max_chars: int = 2600,
        min_score: float = 0.0,
    ) -> str:
        """Build a formatted context string for LLM prompt injection."""
        domain = self._stage_domain(stage)
        hits = self.retrieve(
            query=query,
            domain=domain,
            pdk=target_pdk.lower() if target_pdk else None,
            top_k=top_k,
            min_score=min_score,
        )
        if not hits:
            return ""

        lines = ["VLSI KNOWLEDGE RETRIEVAL:"]
        used = 0
        for hit in hits:
            m = hit.chunk.metadata
            source_label = f"{m.source}" if m.source else "builtin"
            domain_label = m.domain if m.domain != "general" else ""
            tag_str = f"[{domain_label}]" if domain_label else ""
            entry = (
                f"- {m.title or 'Untitled'} {tag_str} [{source_label}] (score={hit.score:.3f})\n"
                f"  {hit.chunk.text.strip()[:600]}"
            )
            if used + len(entry) > max_chars:
                break
            lines.append(entry)
            used += len(entry)
        return "\n".join(lines)

    @staticmethod
    def _stage_domain(stage: str) -> Optional[str]:
        stage_lower = (stage or "").lower()
        mapping = {
            "rtl": "rtl", "verification": "verification", "formal": "verification",
            "coverage": "verification", "cdc": "verification", "timing": "timing",
            "sdc": "timing", "synthesis": "device_physics", "physical": "physical_design",
            "signoff": "physical_design", "power": "power", "floorplan": "physical_design",
            "dft": "rtl",
        }
        for needle, mapped in mapping.items():
            if needle in stage_lower:
                return mapped
        return None

    # ── Full Answer Pipeline (Step 11 + 12) ────────────────────────────────

    NVIDIA_API_KEY = "REMOVED_SECRET"
    NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
    SYNTHESIS_MODEL = "qwen/qwen3-next-80b-a3b-thinking"
    SYNTHESIS_FALLBACK = "meta/llama-4-maverick-17b-128e-instruct"

    _PROPRIETARY_PDKS = {"tsmc", "samsung", "intel", "globalfoundries", "smic", "umc"}

    def _synthesis_llm(self) -> OpenAI:
        return OpenAI(
            base_url=self.NVIDIA_BASE_URL,
            api_key=self.NVIDIA_API_KEY,
            timeout=120,
        )

    def generate_grounded_answer(
        self,
        query: str,
        reranked_chunks: List[Chunk],
        reasoning_effort: str = "high",
    ) -> Dict[str, Any]:
        """Synthesize a grounded answer from reranked chunks using an LLM."""
        if not reranked_chunks:
            return {
                "answer": "This information is not available in the public knowledge base. "
                          "It may require proprietary foundry data under NDA.",
                "citations": [],
                "confidence": "low",
                "domain": classify_domain(query),
                "node": classify_node(query),
                "grounded": False,
                "chunk_count": 0,
                "refusal": True,
            }

        chunks = expand_with_parent(reranked_chunks, self)

        context_parts = []
        for c in chunks:
            m = c.metadata
            parts = []
            if m.title:
                parts.append(f"Title: {m.title}")
            if m.chapter:
                parts.append(f"Chapter: {m.chapter}")
            if m.section:
                parts.append(f"Section: {m.section}")
            if m.page:
                parts.append(f"Page: {m.page}")
            parts.append(f"Domain: {m.domain}")
            if m.pdk:
                parts.append(f"PDK: {m.pdk}")
            header = " | ".join(parts)
            context_parts.append(
                f"[Source: {m.source or 'builtin'}]\n{header}\n{c.text}"
            )

        context_str = "\n\n---\n\n".join(context_parts)
        sources = list(dict.fromkeys(
            m.source for c in chunks if (m := c.metadata).source
        ))

        system_prompt = (
            "You are a world-class VLSI chip design expert with deep knowledge of "
            "semiconductor physics, process technology, and design rules.\n\n"
            "Answer the user question using the context provided below from textbooks, "
            "PDK docs, and research papers.\n\n"
            "Every factual claim must be cited as [Source: title, chapter, page].\n\n"
            "Rules:\n"
            "1. If the EXACT answer is in the context → cite it directly.\n\n"
            "2. If the exact answer is NOT in the context but the context contains "
            "GENERAL PRINCIPLES (scaling theory, design rule trends, physical limits, "
            "device physics, textbook fundamentals), REASON OVER THEM to give a "
            "best-effort answer. Label extrapolated values clearly with phrases like "
            "\"based on scaling trends from [Source]\" or "
            "\"by extrapolating the pitch reduction factor in [Source]\".\n\n"
            "3. CRITICAL - ONLY refuse if the context provides ZERO relevant "
            "information about the topic (e.g. asking about a completely unrelated "
            "field). Do NOT refuse just because the context does not contain the "
            "exact numbers — general textbook knowledge about the topic is enough "
            "to give a helpful answer. General CMOS principles, device physics "
            "explanations, and design methodology descriptions all count as "
            "relevant context.\n\n"
            "4. Never fabricate numbers without citing a source for the trend you "
            "extrapolated from.\n"
            "5. Clearly distinguish predictive/academic PDKs (ASAP7, FreePDK45)\n"
            "  from real manufacturable PDKs (SKY130, GF180MCU).\n"
            "6. If multiple chunks agree, synthesize them into one coherent answer.\n"
            "7. If chunks conflict, report the conflict and cite both sources.\n"
        )

        user_prompt = (
            f"Context:\n{context_str}\n\n"
            f"Question: {query}"
        )

        try:
            client = self._synthesis_llm()
            response = client.chat.completions.create(
                model=self.SYNTHESIS_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3 if reasoning_effort == "low" else 0.7,
                top_p=0.95,
                max_tokens=4096,
            )
            answer_text = response.choices[0].message.content or ""
        except Exception:
            try:
                client = OpenAI(
                    base_url=self.NVIDIA_BASE_URL,
                    api_key=self.NVIDIA_API_KEY,
                )
                response = client.chat.completions.create(
                    model=self.SYNTHESIS_FALLBACK,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3 if reasoning_effort == "low" else 0.7,
                    top_p=0.95,
                    max_tokens=4096,
                )
                answer_text = response.choices[0].message.content or ""
            except Exception:
                answer_text = (
                    "This information is not available in the public knowledge base. "
                    "It may require proprietary foundry data under NDA."
                )

        refusal_exact = (
            "this information is not available in the public knowledge base. "
            "it may require proprietary foundry data under nda."
        )
        answer_lower = answer_text.strip().lower()
        is_refusal = answer_lower == refusal_exact or (
            "not available in the public knowledge base" in answer_lower
            and "[source:" not in answer_lower
        )

        citation_count = answer_text.count("[Source:")
        grounded = citation_count >= 1 and not is_refusal

        if is_refusal:
            confidence = "low"
        else:
            last_hits = getattr(self, "_last_hits", [])
            top_score = max((h.score for h in last_hits), default=0)
            if grounded and len(sources) >= 3 and citation_count >= 2:
                confidence = "high"
            elif grounded and len(sources) >= 1:
                confidence = "medium"
            elif top_score >= 0.65:
                confidence = "medium"
            else:
                confidence = "low"

        return {
            "answer": answer_text,
            "citations": sources[:5],
            "confidence": confidence,
            "domain": classify_domain(query),
            "node": classify_node(query),
            "grounded": grounded,
            "chunk_count": len(chunks),
            "refusal": is_refusal,
        }

    def answer(self, query: str, domain: Optional[str] = None,
               pdk: Optional[str] = None, top_k: int = 6) -> Dict[str, Any]:
        """End-to-end: retrieve → expand → rerank → generate → guard."""
        hits = self.retrieve(
            query=query,
            domain=domain,
            pdk=pdk,
            top_k=top_k,
        )

        self._last_hits = hits
        detected_domain = domain or classify_domain(query)

        if not hits or max(h.score for h in hits) < 0.50:
            return {
                "answer": "This information is not available in the public knowledge base. "
                          "It may require proprietary foundry data under NDA.",
                "citations": [],
                "confidence": "low",
                "domain": detected_domain,
                "node": classify_node(query),
                "grounded": False,
                "chunk_count": 0,
                "refusal": True,
            }

        # Check for proprietary PDK names in query to refuse early
        query_lower = query.lower()
        for prop_pdk in self._PROPRIETARY_PDKS:
            if prop_pdk in query_lower:
                return {
                    "answer": "This information is not available in the public knowledge base. "
                              "It may require proprietary foundry data under NDA.",
                    "citations": [],
                    "confidence": "low",
                    "domain": detected_domain,
                    "node": classify_node(query),
                    "grounded": False,
                    "chunk_count": 0,
                    "refusal": True,
                }

        chunks = [h.chunk for h in hits]
        reasoning_effort = "high" if len(query.split()) > 15 else "low"

        return self.generate_grounded_answer(
            query=query,
            reranked_chunks=chunks,
            reasoning_effort=reasoning_effort,
        )

    # ── Stats ──────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        try:
            collection_info = self.client.get_collection(self.collection_name)
            points = self.client.scroll(
                collection_name=self.collection_name,
                limit=10000,
                with_payload=True,
                with_vectors=False,
            )[0]
            domains = {}
            source_types = {}
            pdks = set()
            for p in points:
                pl = p.payload or {}
                d = pl.get("domain", "unknown")
                domains[d] = domains.get(d, 0) + 1
                st = pl.get("source_type", "unknown")
                source_types[st] = source_types.get(st, 0) + 1
                if pl.get("pdk"):
                    pdks.add(pl.get("pdk"))
            return {
                "total_chunks": collection_info.points_count,
                "vector_dim": self.embedding.dimension,
                "embedding_model": self.embedding.model_name,
                "domains": dict(sorted(domains.items(), key=lambda x: -x[1])),
                "source_types": dict(sorted(source_types.items(), key=lambda x: -x[1])),
                "pdks": sorted(pdks),
                "db_path": str(self.db_path),
                "knowledge_dir": str(self.knowledge_dir),
            }
        except Exception as e:
            return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Register atexit to close all Qdrant instances
# ═══════════════════════════════════════════════════════════════════════════════

def _close_all_qdrant():
    for instance in list(VLSIKnowledgeBase._instances.values()):
        instance.close()


atexit.register(_close_all_qdrant)


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience Functions
# ═══════════════════════════════════════════════════════════════════════════════


def build_vlsi_context(
    query: str,
    stage: str = "",
    target_pdk: str = "",
    top_k: int = 4,
) -> str:
    try:
        return VLSIKnowledgeBase().build_context(
            query=query, stage=stage, target_pdk=target_pdk, top_k=top_k,
        )
    except Exception as e:
        logger.warning(f"VLSI RAG context build failed: {e}")
        return ""
