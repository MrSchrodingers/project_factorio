import unittest

from factorio_ai_lab.learning.knowledge import (
    flatten_numeric_facts,
    verify_generated_knowledge,
)


class KnowledgeVerificationTests(unittest.TestCase):
    def test_flattens_nested_numeric_facts(self) -> None:
        flattened = flatten_numeric_facts(
            {"output": 20, "nested": {"rate_per_s": 1.25}, "accepted": True}
        )
        self.assertEqual(flattened["output"], 20.0)
        self.assertEqual(flattened["nested.rate_per_s"], 1.25)
        self.assertNotIn("accepted", flattened)

    def test_rejects_unsupported_number(self) -> None:
        result = verify_generated_knowledge(
            lesson="Copper output was 20 items.",
            hypothesis="Try 99 furnaces.",
            facts={"output": 20},
            evidence_keys=["output"],
        )
        self.assertFalse(result.verified)
        self.assertIn(99.0, result.unsupported_numbers)

    def test_rejects_rate_claim_without_rate_fact(self) -> None:
        result = verify_generated_knowledge(
            lesson="Copper produced 20 per second.",
            hypothesis="Keep the same design.",
            facts={"output": 20, "duration_s": 24},
            evidence_keys=["output", "duration_s"],
        )
        self.assertFalse(result.verified)
        self.assertTrue(result.rate_claim_without_rate_fact)

    def test_accepts_rate_when_fact_exists(self) -> None:
        result = verify_generated_knowledge(
            lesson="Measured rate was 1.5/s.",
            hypothesis="Preserve the measured rate.",
            facts={"rate_per_s": 1.5},
            evidence_keys=["rate_per_s"],
        )
        self.assertTrue(result.verified)

    def test_rejects_unknown_evidence_key(self) -> None:
        result = verify_generated_knowledge(
            lesson="The buffer remained stable.",
            hypothesis="Retain it.",
            facts={"buffer": 8},
            evidence_keys=["missing"],
        )
        self.assertFalse(result.verified)


if __name__ == "__main__":
    unittest.main()
