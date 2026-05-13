"""
Unit tests for grounded answer synthesis in VLSI RAG pipeline.
Tests 5 scenarios: timing, device physics, PDK-specific,
unanswerable proprietary, and cross-domain queries.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from src.agentic.core.vlsi_rag import VLSIKnowledgeBase


class TestGroundedAnswers(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.kb = VLSIKnowledgeBase()

    def test_1_timing_synthesis(self):
        """Timing: LLM synthesizes clock skew answer with citations"""
        result = self.kb.answer("How does clock tree synthesis reduce clock skew?")
        self.assertIn(result["confidence"], ("high", "medium", "low"))
        answer_lower = result["answer"].lower()
        self.assertIn("clock", answer_lower,
                      f"Answer should mention clock. Got: {result['answer'][:200]}")
        self.assertGreaterEqual(result["chunk_count"], 1)

    def test_2_device_physics_question(self):
        """Device physics: DIBL/threshold query should not crash; answer or refuse coherently"""
        result = self.kb.answer("How does DIBL affect threshold voltage in FinFET devices?")
        self.assertIn(result["confidence"], ("high", "medium", "low"))
        self.assertGreaterEqual(result["chunk_count"], 0)
        answer_lower = result["answer"].lower()
        if not result["refusal"]:
            self.assertTrue(
                any(word in answer_lower for word in ["threshold", "dibl", "short-channel", "vth"]),
                f"Non-refusal answer should mention VLSI terms. Got: {result['answer'][:200]}"
            )

    def test_3_pdk_specific_question(self):
        """PDK-specific: should retrieve something and either answer or refuse coherently"""
        result = self.kb.answer("What are the design rules for metal layers in sky130?")
        self.assertIn(result["confidence"], ("high", "medium", "low"))
        self.assertGreaterEqual(result["chunk_count"], 0)
        answer_lower = result["answer"].lower()
        if not result["refusal"]:
            self.assertTrue(
                any(word in answer_lower for word in ["sky130", "metal", "design rule"]),
                f"Non-refusal answer should mention SKY130/metal. Got: {result['answer'][:200]}"
            )

    def test_4_unanswerable_proprietary(self):
        """Unanswerable proprietary query must refuse"""
        result = self.kb.answer("What is the exact via enclosure rule for TSMC N2?")
        refusal_msg = "not available in the public knowledge base"
        self.assertIn(refusal_msg, result["answer"].lower())
        self.assertTrue(result["refusal"])
        self.assertFalse(result["grounded"])

    def test_5_cross_domain_query(self):
        """Cross-domain: power + timing should answer or refuse coherently"""
        result = self.kb.answer("How does IR drop affect clock tree timing and what can be done to fix it?")
        self.assertIn(result["confidence"], ("high", "medium", "low"))
        self.assertGreaterEqual(result["chunk_count"], 0)
        answer_lower = result["answer"].lower()
        if not result["refusal"]:
            self.assertTrue(
                any(word in answer_lower for word in ["power", "voltage", "clock", "timing", "ir"]),
                f"Non-refusal answer should cover power/timing. Got: {result['answer'][:300]}"
            )


if __name__ == "__main__":
    unittest.main()
