from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from contracts import (  # noqa: E402
    build_semantic_windows, calculate_composite, seconds_to_frame,
    stable_candidate_id, temporal_overlap_ratio, validate_word_transcript,
)


class ContractTests(unittest.TestCase):
    def test_frame_conversion_is_explicit(self) -> None:
        self.assertEqual(seconds_to_frame(1.5, 30), 45)
        self.assertEqual(seconds_to_frame(1.01, 30, "floor"), 30)
        self.assertEqual(seconds_to_frame(1.01, 30, "ceil"), 31)

    def test_transcript_rejects_non_monotonic_timestamps(self) -> None:
        errors = validate_word_transcript([
            {"word": "one", "start": 1.0, "end": 2.0},
            {"word": "two", "start": 0.5, "end": 2.5},
        ])
        self.assertTrue(any("not monotonic" in error for error in errors))

    def test_windows_preserve_word_indexes(self) -> None:
        words = [{"word": f"w{i}" + ("." if i == 9 else ""), "start": i, "end": i + 0.8} for i in range(15)]
        windows = build_semantic_windows(words)
        self.assertEqual(windows[0]["startWordIndex"], 0)
        self.assertEqual(windows[0]["endWordIndexExclusive"], 10)
        self.assertEqual(windows[-1]["endWordIndexExclusive"], 15)

    def test_composite_uses_declared_weights(self) -> None:
        score = calculate_composite({"hook": 10, "selfContainedness": 8, "emotion": 6, "payoffDensity": 9, "retention": 7})
        self.assertEqual(score, 8.35)

    def test_overlap_uses_shorter_range(self) -> None:
        ratio = temporal_overlap_ratio(
            {"startFrame": 0, "endFrameExclusive": 100},
            {"startFrame": 50, "endFrameExclusive": 150},
        )
        self.assertEqual(ratio, 0.5)

    def test_stable_ids_depend_on_source_and_range(self) -> None:
        first = stable_candidate_id("abc", 10, 20)
        self.assertEqual(first, stable_candidate_id("abc", 10, 20))
        self.assertNotEqual(first, stable_candidate_id("abc", 10, 21))


if __name__ == "__main__":
    unittest.main()
