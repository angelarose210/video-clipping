from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from contracts import rank_candidates  # noqa: E402


def candidate(start: int, end: int, topic: str, hook: float) -> dict:
    return {
        "startFrame": start, "endFrameExclusive": end,
        "topicKey": topic, "hookText": topic, "hookType": "question",
        "scores": {"hook": hook, "selfContainedness": 8, "emotion": 6, "payoffDensity": 8, "retention": 8},
        "evidence": {}, "rejectionFlags": [],
    }


class RankingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = {
            "sourceFingerprint": {"sha256": "abc"},
            "sourceVideo": {"fps": 60},
            "target": {"count": 3, "fps": 30, "minimumDurationSeconds": 20, "maximumDurationSeconds": 60},
            "selection": {
                "weights": {"hook": 0.30, "selfContainedness": 0.20, "emotion": 0.15, "payoffDensity": 0.20, "retention": 0.15},
                "temporalOverlapThreshold": 0.5, "topicDiversity": True,
            },
        }

    def test_duration_uses_parent_source_fps(self) -> None:
        result = rank_candidates(self.request, [candidate(0, 2400, "a", 9)])
        self.assertEqual(result["selectedCount"], 1)

    def test_nms_keeps_higher_scoring_overlap(self) -> None:
        result = rank_candidates(self.request, [candidate(0, 1800, "a", 9), candidate(600, 2400, "b", 7)])
        self.assertEqual(result["selectedCount"], 1)
        self.assertTrue(any("overlaps" in item["selection"]["reasons"][0] for item in result["rejected"]))

    def test_diversity_selects_distinct_topics_first(self) -> None:
        result = rank_candidates(self.request, [
            candidate(0, 1200, "a", 10), candidate(1800, 3000, "a", 9),
            candidate(3600, 4800, "b", 8), candidate(5400, 6600, "c", 7),
        ])
        self.assertEqual([item["topicKey"] for item in result["selected"]], ["a", "b", "c"])

    def test_blocking_flag_is_rejected(self) -> None:
        item = candidate(0, 1200, "a", 9)
        item["rejectionFlags"] = ["missingPayoff"]
        result = rank_candidates(self.request, [item])
        self.assertEqual(result["selectedCount"], 0)
        self.assertIn("blocking flags", result["rejected"][0]["selection"]["reasons"][0])


if __name__ == "__main__":
    unittest.main()
