"""
VLSI RAG Retrieval & Answer Quality Benchmark.

Measures hit rate, MRR, precision@k, and answer quality
across a labeled dataset of VLSI queries spanning all domains.
"""
import sys, os, time, json
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.agentic.core.vlsi_rag import VLSIKnowledgeBase


@dataclass
class BenchmarkQuery:
    query: str
    domain: str
    expected_keywords: List[str]
    expected_source_pattern: str = ""
    min_chunks: int = 1
    pdk: str = ""
    node: str = "general"
    must_be_grounded: bool = True
    must_refuse: bool = False


BENCHMARK = [
    # ── device_physics (5) ──
    BenchmarkQuery(
        query="How does drain-induced barrier lowering affect threshold voltage in short-channel MOSFETs?",
        domain="device_physics",
        expected_keywords=["dibl", "threshold", "short-channel"],
        expected_source_pattern="book2.pdf",
    ),
    BenchmarkQuery(
        query="What is the gate oxide thickness for sky130 high voltage devices?",
        domain="device_physics",
        expected_keywords=["oxide", "thickness", "sky130"],
        expected_source_pattern="hv.rst",
        pdk="sky130",
    ),
    BenchmarkQuery(
        query="Explain the body effect: how does substrate bias change Vth?",
        domain="device_physics",
        expected_keywords=["body effect", "threshold", "substrate"],
        expected_source_pattern="book1.pdf",
    ),
    BenchmarkQuery(
        query="What is velocity saturation and how does it affect MOSFET current?",
        domain="device_physics",
        expected_keywords=["velocity saturation", "mobility", "current"],
        expected_source_pattern="book",
    ),
    BenchmarkQuery(
        query="Describe the FinFET structure and its advantages over planar MOSFETs",
        domain="device_physics",
        expected_keywords=["finfet", "gate", "channel"],
        expected_source_pattern="asap7",
    ),

    # ── timing (5) ──
    BenchmarkQuery(
        query="How does clock tree synthesis reduce clock skew in digital designs?",
        domain="timing",
        expected_keywords=["clock", "skew", "synthesis"],
        expected_source_pattern="builtin",
    ),
    BenchmarkQuery(
        query="What is the difference between setup time and hold time constraints?",
        domain="timing",
        expected_keywords=["setup", "hold", "timing"],
        expected_source_pattern="book3.pdf",
    ),
    BenchmarkQuery(
        query="How is slack calculated in static timing analysis?",
        domain="timing",
        expected_keywords=["slack", "timing", "arrival"],
        expected_source_pattern="book3.pdf",
    ),
    BenchmarkQuery(
        query="What is on-chip variation and how does it affect timing closure?",
        domain="timing",
        expected_keywords=["ocv", "variation", "timing"],
        expected_source_pattern="book3.pdf",
    ),
    BenchmarkQuery(
        query="Explain the CPPR (common path pessimism removal) technique in STA",
        domain="timing",
        expected_keywords=["cppr", "pessimism", "common path"],
        expected_source_pattern="book",
    ),

    # ── physical_design (4) ──
    BenchmarkQuery(
        query="What are the metal layer design rules for GF180?",
        domain="physical_design",
        expected_keywords=["metal", "gf180", "rule"],
        expected_source_pattern="gf180mcu",
        pdk="gf180mcu",
    ),
    BenchmarkQuery(
        query="How does antenna effect occur during plasma etching and how is it fixed?",
        domain="physical_design",
        expected_keywords=["antenna", "etching", "diode"],
        expected_source_pattern="antenna.rst",
    ),
    BenchmarkQuery(
        query="What are the standard cell row taping rules in sky130?",
        domain="physical_design",
        expected_keywords=["tap", "cell", "well"],
        expected_source_pattern="skywater",
        pdk="sky130",
    ),
    BenchmarkQuery(
        query="Describe the ASAP7 metal stack and via layers",
        domain="physical_design",
        expected_keywords=["metal", "via", "asap7"],
        expected_source_pattern="asap7",
        pdk="asap7",
    ),

    # ── power (2) ──
    BenchmarkQuery(
        query="What is the difference between dynamic power and leakage power in CMOS circuits?",
        domain="power",
        expected_keywords=["dynamic power", "leakage", "switching"],
    ),
    BenchmarkQuery(
        query="How does clock gating reduce dynamic power consumption?",
        domain="power",
        expected_keywords=["clock gating", "power", "dynamic"],
    ),

    # ── rtl (2) ──
    BenchmarkQuery(
        query="What is the difference between blocking and non-blocking assignments in Verilog?",
        domain="rtl",
        expected_keywords=["blocking", "non-blocking", "verilog"],
    ),
    BenchmarkQuery(
        query="How does a finite state machine synthesis work in RTL design?",
        domain="rtl",
        expected_keywords=["state machine", "fsm", "synthesis"],
    ),

    # ── analog (1) ──
    BenchmarkQuery(
        query="What are the key design considerations for a two-stage operational amplifier?",
        domain="analog",
        expected_keywords=["operational amplifier", "opamp", "gain", "compensation"],
    ),

    # ── verification (1) ──
    BenchmarkQuery(
        query="What is function coverage in SystemVerilog and how is it used in verification?",
        domain="verification",
        expected_keywords=["coverage", "verification", "systemverilog"],
    ),

    # ── cross-domain (2) ──
    BenchmarkQuery(
        query="How does IR drop affect clock tree timing and what design fixes mitigate it?",
        domain="timing",
        expected_keywords=["ir drop", "clock", "power"],
        must_be_grounded=True,
    ),
    BenchmarkQuery(
        query="Explain the trade-off between power, performance, and area in advanced CMOS nodes",
        domain="general",
        expected_keywords=["power", "performance", "area", "trade-off"],
    ),

    # ── unanswerable / proprietary (1) ──
    BenchmarkQuery(
        query="What is the exact metal via resistance value for TSMC N3?",
        domain="physical_design",
        expected_keywords=[""],
        must_be_grounded=False,
        must_refuse=True,
    ),
]


def keyword_relevance(text: str, keywords: List[str]) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def compute_metrics(
    results: List[Tuple[List[str], List[float], bool]],
    expected_keywords_list: List[List[str]],
) -> Dict[str, float]:
    hit_rate_1 = 0
    hit_rate_3 = 0
    hit_rate_5 = 0
    reciprocal_ranks = []
    precisions_5 = []

    for (texts, scores, is_relevant), keywords in zip(results, expected_keywords_list):
        if not keywords:
            continue
        hit_1 = False
        hit_3 = False
        hit_5 = False
        first_rel_rank = None

        for rank, text in enumerate(texts, 1):
            relevant = keyword_relevance(text, keywords)
            if relevant:
                if rank == 1:
                    hit_1 = True
                if rank <= 3:
                    hit_3 = True
                if rank <= 5:
                    hit_5 = True
                if first_rel_rank is None:
                    first_rel_rank = rank

        if hit_1:
            hit_rate_1 += 1
        if hit_3:
            hit_rate_3 += 1
        if hit_5:
            hit_rate_5 += 1

        if first_rel_rank is not None:
            reciprocal_ranks.append(1.0 / first_rel_rank)
        else:
            reciprocal_ranks.append(0.0)

        top5 = texts[:5]
        if top5:
            relevant_top5 = sum(1 for t in top5 if keyword_relevance(t, keywords))
            precisions_5.append(relevant_top5 / len(top5))

    n = len(results)
    return {
        "hit_rate_1": hit_rate_1 / n,
        "hit_rate_3": hit_rate_3 / n,
        "hit_rate_5": hit_rate_5 / n,
        "mrr": sum(reciprocal_ranks) / n,
        "avg_precision_5": sum(precisions_5) / len(precisions_5) if precisions_5 else 0,
        "total_queries": n,
    }


def run_benchmark(top_k: int = 5) -> Dict[str, Any]:
    print("=" * 72)
    print("VLSI RAG Retrieval & Answer Quality Benchmark")
    print("=" * 72)

    kb = VLSIKnowledgeBase()

    # ── Phase 1: Retrieval Quality ──
    print("\n── Phase 1: Retrieval Quality ──\n")

    retrieval_results = []
    domain_stats: Dict[str, Dict] = {}
    total_retrieval_time = 0.0

    for q in BENCHMARK:
        t0 = time.time()
        hits = kb.retrieve(q.query, domain=q.domain if q.domain != "general" else None,
                           top_k=top_k)
        elapsed = time.time() - t0
        total_retrieval_time += elapsed

        rel_texts = [h.chunk.text for h in hits]
        hit_scores = [h.score for h in hits]

        is_relevant = any(
            keyword_relevance(t, q.expected_keywords)
            for t in rel_texts
        ) if q.expected_keywords else q.must_refuse

        retrieval_results.append((rel_texts, hit_scores, is_relevant))

        domain_key = q.domain
        if domain_key not in domain_stats:
            domain_stats[domain_key] = {"count": 0, "hit": 0}
        domain_stats[domain_key]["count"] += 1
        if is_relevant:
            domain_stats[domain_key]["hit"] += 1

        status = "✓" if is_relevant else "✗"
        print(f"  {status} [{q.domain:20s}] {q.query[:60]}...")
        print(f"      hits={len(hits):2d}  time={elapsed:.2f}s  "
              f"{'(refusal expected)' if q.must_refuse else ''}")

    metrics = compute_metrics(
        retrieval_results,
        [q.expected_keywords for q in BENCHMARK],
    )

    print(f"\n── Retrieval Metrics (top-{top_k}) ──")
    print(f"  Hit Rate@1:  {metrics['hit_rate_1']:.1%}")
    print(f"  Hit Rate@3:  {metrics['hit_rate_3']:.1%}")
    print(f"  Hit Rate@5:  {metrics['hit_rate_5']:.1%}")
    print(f"  MRR:         {metrics['mrr']:.3f}")
    print(f"  Avg Prec@5:  {metrics['avg_precision_5']:.1%}")
    print(f"  Total time:  {total_retrieval_time:.1f}s")

    print(f"\n── Per-Domain Hit Rate ──")
    for dom, stats in sorted(domain_stats.items()):
        hit_pct = stats["hit"] / stats["count"] * 100
        print(f"  {dom:20s}: {stats['hit']}/{stats['count']} ({hit_pct:.0f}%)")

    # ── Phase 2: Answer Quality ──
    print(f"\n── Phase 2: Answer Quality ──\n")

    answer_stats = {
        "total": 0,
        "grounded": 0,
        "refused_correctly": 0,
        "refused_incorrectly": 0,
        "high_conf": 0,
        "medium_conf": 0,
        "low_conf": 0,
        "total_time": 0.0,
    }

    for q in BENCHMARK:
        t0 = time.time()
        result = kb.answer(q.query, domain=q.domain if q.domain != "general" else None)
        elapsed = time.time() - t0
        answer_stats["total_time"] += elapsed
        answer_stats["total"] += 1

        is_refusal = result.get("refusal", False)
        is_grounded = result.get("grounded", False)
        confidence = result.get("confidence", "low")
        answer_text = result.get("answer", "")

        if q.must_refuse and is_refusal:
            answer_stats["refused_correctly"] += 1
            status = "✓"
        elif not q.must_refuse and not is_refusal and is_grounded:
            answer_stats["grounded"] += 1
            answer_stats["refused_incorrectly"] += 0
            status = "✓"
        elif not q.must_refuse and is_refusal:
            answer_stats["refused_incorrectly"] += 1
            status = "⚠"
        elif q.must_refuse and not is_refusal:
            status = "✗"
        else:
            status = "?"

        if confidence == "high":
            answer_stats["high_conf"] += 1
        elif confidence == "medium":
            answer_stats["medium_conf"] += 1
        else:
            answer_stats["low_conf"] += 1

        ans_short = answer_text.replace("\n", " ")[:80]
        print(f"  {status} [{q.domain:20s}] {q.query[:50]}...")
        print(f"      grounded={is_grounded} refusal={is_refusal} conf={confidence} "
              f"time={elapsed:.1f}s")
        print(f"      answer: {ans_short}...")

    print(f"\n── Answer Quality Summary ──")
    n = answer_stats["total"]
    print(f"  Grounded (non-refusal): {answer_stats['grounded']}/{n - 1} "
          f"({answer_stats['grounded']/(n-1)*100:.0f}% of answerable)")
    print(f"  Correctly refused:       {answer_stats['refused_correctly']}/1")
    print(f"  Incorrectly refused:     {answer_stats['refused_incorrectly']}")
    print(f"  Confidence high:         {answer_stats['high_conf']}/{n}")
    print(f"  Confidence medium:       {answer_stats['medium_conf']}/{n}")
    print(f"  Confidence low:          {answer_stats['low_conf']}/{n}")
    print(f"  Total answer time:       {answer_stats['total_time']:.1f}s")

    return {"retrieval": metrics, "answer": answer_stats}


if __name__ == "__main__":
    run_benchmark()
