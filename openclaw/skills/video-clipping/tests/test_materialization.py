from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
CLI = SKILL / "scripts" / "clipping_pipeline.py"


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class MaterializationSmokeTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> dict:
        completed = subprocess.run(
            [sys.executable, str(CLI), *arguments], capture_output=True, text=True
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        return json.loads(completed.stdout)

    def test_source_range_materializes_cfr_child_with_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            transcript = root / "transcript.json"
            run_root = root / "run"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=60:duration=4",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-shortest", str(source),
            ], check=True)
            words = [
                {"word": f"word-{index}", "start": index * 0.5, "end": index * 0.5 + 0.4}
                for index in range(8)
            ]
            transcript.write_text(json.dumps(words), encoding="utf-8")
            self.run_cli(
                "preflight", "--source", str(source), "--run-root", str(run_root),
                "--transcript", str(transcript), "--count", "1",
                "--minimum-duration", "2", "--maximum-duration", "4",
                "--fps", "30",
            )
            candidates = {
                "schemaVersion": 1,
                "candidates": [{
                    "startFrame": 60, "endFrameExclusive": 180,
                    "topicKey": "fixture", "hookText": "word-2", "hookType": "question",
                    "scores": {
                        "hook": 9, "selfContainedness": 8, "emotion": 6,
                        "payoffDensity": 8, "retention": 8,
                    },
                    "evidence": {}, "rejectionFlags": [],
                }],
            }
            candidates_path = run_root / "analysis" / "candidates.raw.json"
            candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
            self.run_cli(
                "rank", "--request", str(run_root / "SHORTS_REQUEST.json"),
                "--candidates", str(candidates_path),
            )
            result = self.run_cli("materialize", "--run-root", str(run_root))
            self.assertEqual(result["count"], 1)
            project = Path(result["clips"][0]["project"])
            contract = json.loads((project / "CLIP_CONTRACT.json").read_text(encoding="utf-8"))
            self.assertEqual(contract["source"]["sourceRange"], {
                "startFrame": 60, "endFrameExclusive": 180,
            })
            self.assertEqual(contract["timeline"]["fps"], 30.0)
            self.assertGreaterEqual(contract["timeline"]["endFrameExclusive"], 59)
            self.assertLessEqual(contract["timeline"]["endFrameExclusive"], 61)
            rebased = json.loads((project / "public" / "transcript.json").read_text(encoding="utf-8"))
            self.assertEqual(rebased[0]["parentWordIndex"], 2)
            self.assertAlmostEqual(rebased[0]["start"], 0.0)
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                "-of", "json", str(project / "public" / "videos" / "source.mp4"),
            ], check=True, capture_output=True, text=True)
            stream_types = {stream["codec_type"] for stream in json.loads(probe.stdout)["streams"]}
            self.assertEqual(stream_types, {"video", "audio"})
            validated = self.run_cli("validate", "--run-root", str(run_root))
            self.assertTrue(validated["ready"], validated["errors"])


if __name__ == "__main__":
    unittest.main()
