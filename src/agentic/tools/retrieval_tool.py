"""
VLSI Knowledge Retrieval Tools for CrewAI Agents
=================================================
Provides @tool-decorated functions that agents call during chip building
to retrieve VLSI knowledge from the advanced multi-stage RAG pipeline.

Tools:
    vlsi_search        - Hybrid VLSI knowledge search (dense + BM25 + rerank)
    pdk_rule_lookup    - PDK-specific design rule search
    paper_search       - Research paper search via arXiv + stored papers
    vlsi_ask           - End-to-end grounded answer with citations
    expand_abbr        - Expand VLSI abbreviations in a query
"""

import logging
from pathlib import Path

from crewai.tools import tool

logger = logging.getLogger(__name__)


def _get_kb():
    """Lazy-init the VLSI knowledge base (avoids loading model at import time)."""
    from agentic.core.vlsi_rag import VLSIKnowledgeBase
    return VLSIKnowledgeBase()


@tool("VLSI Knowledge Search")
def vlsi_search(query: str, domain: str = "", top_k: int = 5) -> str:
    """
    Search the VLSI knowledge base for chip design information.
    Uses hybrid retrieval (dense + BM25) with reranking and abbreviation expansion.
    Use this to look up hardware design patterns, circuit techniques,
    RTL coding rules, verification methods, and physical design guidance.

    Args:
        query: The specific question or topic (e.g., "how to fix setup violation", "CDC synchronizer", "FSM encoding")
        domain: Optional filter - rtl, timing, power, physical_design, verification, analog, device_physics
        top_k: Number of results to return (1-10)

    Returns:
        Formatted knowledge entries with source citations.
    """
    try:
        kb = _get_kb()
        domain_filter = domain if domain else None
        hits = kb.retrieve(
            query=query,
            domain=domain_filter,
            top_k=min(top_k, 10),
        )
        if not hits:
            return "No relevant VLSI knowledge found for this query."

        lines = ["Retrieved VLSI Knowledge (hybrid dense+BM25+rerank):"]
        for i, hit in enumerate(hits, 1):
            m = hit.chunk.metadata
            domain_str = f"[{m.domain}]" if m.domain and m.domain != "general" else ""
            source_str = f"source: {m.source}" if m.source else "builtin knowledge"
            title_str = f" {m.title}" if m.title else ""
            method_str = f" ({hit.method})" if hit.method else ""
            lines.append(
                f"{i}.{title_str} {domain_str}{method_str} ({source_str}, score={hit.score:.3f})\n"
                f"   {hit.chunk.text.strip()[:500]}"
            )
        return "\n\n".join(lines)
    except Exception as e:
        logger.warning(f"VLSI search tool failed: {e}")
        return f"Knowledge search unavailable (error: {e})"


@tool("PDK Rule Lookup")
def pdk_rule_lookup(query: str, pdk: str = "sky130", top_k: int = 3) -> str:
    """
    Search PDK-specific documentation for design rules, layer stacks,
    standard cell information, and technology constraints.

    Args:
        query: The specific PDK rule or parameter (e.g., "minimum via width", "metal stack", "cell library timing")
        pdk: PDK name - one of: sky130, gf180mcu, asap7, freepdk45
        top_k: Number of results to return (1-5)

    Returns:
        PDK-specific design information with source references.
    """
    try:
        kb = _get_kb()
        hits = kb.retrieve(
            query=query,
            pdk=pdk.lower(),
            top_k=min(top_k, 5),
        )
        if not hits:
            return f"No PDK information found for '{pdk}' matching this query."

        lines = [f"PDK {pdk.upper()} Documentation:"]
        for i, hit in enumerate(hits, 1):
            m = hit.chunk.metadata
            source_str = f"source: {m.source}" if m.source else "builtin knowledge"
            lines.append(
                f"{i}. [{m.domain}] ({source_str}, score={hit.score:.3f})\n"
                f"   {hit.chunk.text.strip()[:500]}"
            )
        return "\n\n".join(lines)
    except Exception as e:
        logger.warning(f"PDK lookup tool failed: {e}")
        return f"PDK lookup unavailable (error: {e})"


@tool("Paper Search")
def paper_search(query: str, top_k: int = 3) -> str:
    """
    Search VLSI research papers from arXiv and the local knowledge base.
    Use this for cutting-edge techniques, academic references,
    or recent advancements in chip design.

    Args:
        query: The research topic (e.g., "GAA transistor 2024", "ML for timing prediction", "3nm FinFET")
        top_k: Number of results to return (1-5)

    Returns:
        Research paper summaries with publication year and source.
    """
    results = []

    try:
        import arxiv as arxiv_client
        search = arxiv_client.Search(
            query=query,
            max_results=min(top_k, 5),
            sort_by=arxiv_client.SortCriterion.Relevance,
        )
        for result in search.results():
            results.append(
                f"[arXiv] {result.title} ({result.published.year})\n"
                f"        Authors: {', '.join(a.name for a in result.authors[:3])}\n"
                f"        Summary: {result.summary[:400]}...\n"
                f"        URL: {result.entry_id}"
            )
    except Exception as e:
        logger.warning(f"arXiv search failed: {e}")

    try:
        kb = _get_kb()
        stored = kb.retrieve(
            query=query,
            source_types=["paper"],
            top_k=min(top_k, 5),
        )
        for hit in stored:
            m = hit.chunk.metadata
            results.append(
                f"[Paper] {m.title or 'Untitled'} (score={hit.score:.3f})\n"
                f"        Source: {m.source}\n"
                f"        {hit.chunk.text.strip()[:400]}"
            )
    except Exception as e:
        logger.warning(f"Stored paper search failed: {e}")

    if not results:
        return "No research papers found for this query."

    return "\n\n".join(["Research Papers:", *results])


@tool("VLSI Ask")
def vlsi_ask(query: str, domain: str = "") -> str:
    """
    Get a grounded, citation-backed answer to any VLSI question.
    Uses the full multi-stage pipeline: abbreviation expansion, hybrid retrieval,
    reranking, parent-context expansion, and confidence checking.

    Args:
        query: The VLSI question to answer
        domain: Optional domain hint (rtl, timing, power, physical_design, verification, analog, device_physics)

    Returns:
        Retrieved evidence with source citations, ready for LLM synthesis.
    """
    try:
        kb = _get_kb()
        result = kb.answer(
            query=query,
            domain=domain if domain else None,
            top_k=6,
        )
        lines = [
            f"VLSI Answer (confidence: {result['confidence']}, grounded: {result.get('grounded', False)})",
            f"Domain: {result['domain']}",
            "",
        ]
        lines.append(result["answer"])
        lines.append("")
        lines.append("Sources:")
        for s in result.get("citations", result.get("sources", []))[:5]:
            lines.append(f"  - {s}")

        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"VLSI ask tool failed: {e}")
        return f"Unable to answer (error: {e})"


@tool("Expand VLSI Abbreviations")
def expand_abbr(text: str) -> str:
    """
    Expand VLSI abbreviations in a query to their full forms.
    Use this before searching to improve retrieval recall.

    Args:
        text: Text containing VLSI abbreviations (e.g., "How does DIBL affect FinFET Vth?")

    Returns:
        Text with abbreviations expanded.
    """
    try:
        from agentic.core.vlsi_rag import expand_abbreviations
        return expand_abbreviations(text)
    except Exception as e:
        logger.warning(f"Abbreviation expansion failed: {e}")
        return text
