from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
CLI = SKILL / "scripts" / "clipping_pipeline.py"


class CliTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(CLI), *arguments], capture_output=True, text=True)

    def test_windows_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = root / "SHORTS_REQUEST.json"
            transcript = root / "transcript.json"
            output = root / "windows.json"
            request.write_text(json.dumps({"sourceVideo": {"durationSeconds": 30}, "sourceFingerprint": {"sha256": "abc"}}), encoding="utf-8")
            transcript.write_text(json.dumps([
                {"word": f"word{i}" + ("." if i == 9 else ""), "start": i, "end": i + 0.8}
                for i in range(15)
            ]), encoding="utf-8")
            completed = self.run_cli("windows", "--request", str(request), "--transcript", str(transcript), "--output", str(output))
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertTrue(output.is_file())
            self.assertEqual(json.loads(completed.stdout)["windowCount"], 2)

    def test_invalid_transcript_returns_json_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = root / "SHORTS_REQUEST.json"
            transcript = root / "transcript.json"
            request.write_text(json.dumps({"sourceVideo": {"durationSeconds": 30}, "sourceFingerprint": {"sha256": "abc"}}), encoding="utf-8")
            transcript.write_text(json.dumps([{"word": "bad", "start": 2, "end": 1}]), encoding="utf-8")
            completed = self.run_cli("windows", "--request", str(request), "--transcript", str(transcript))
            self.assertEqual(completed.returncode, 1)
            self.assertIn("error", json.loads(completed.stdout))


if __name__ == "__main__":
    unittest.main()
