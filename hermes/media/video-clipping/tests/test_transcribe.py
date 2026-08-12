from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
CLI = SKILL / "scripts" / "transcribe.py"

spec = importlib.util.spec_from_file_location("_transcribe", CLI)
transcribe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transcribe)

SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
from contracts import validate_word_transcript  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg"))
HAS_BACKEND = (
    importlib.util.find_spec("faster_whisper") is not None
    or importlib.util.find_spec("whisper") is not None
)


class MonotonicTests(unittest.TestCase):
    def test_overlapping_words_are_pushed_forward(self) -> None:
        words = [
            {"word": "one", "start": 0.0, "end": 1.0},
            {"word": "two", "start": 0.5, "end": 1.5},
        ]
        cleaned = transcribe.enforce_monotonic(words)
        self.assertGreaterEqual(cleaned[1]["start"], cleaned[0]["end"])
        self.assertEqual(validate_word_transcript(cleaned), [])

    def test_zero_length_words_gain_a_duration(self) -> None:
        cleaned = transcribe.enforce_monotonic([{"word": "hi", "start": 1.0, "end": 1.0}])
        self.assertGreater(cleaned[0]["end"], cleaned[0]["start"])
        self.assertEqual(validate_word_transcript(cleaned), [])

    def test_output_satisfies_the_pipeline_contract(self) -> None:
        """The whole point of this cleanup: contracts.py must accept the result."""
        words = [
            {"word": "a", "start": 0.0, "end": 0.4},
            {"word": "b", "start": 0.2, "end": 0.3},
            {"word": "c", "start": 0.9, "end": 0.9},
            {"word": "d", "start": 0.5, "end": 2.0},
        ]
        self.assertNotEqual(validate_word_transcript(words), [])
        self.assertEqual(validate_word_transcript(transcribe.enforce_monotonic(words)), [])

    def test_already_clean_input_is_preserved(self) -> None:
        words = [
            {"word": "one", "start": 0.0, "end": 0.5},
            {"word": "two", "start": 0.6, "end": 1.1},
        ]
        cleaned = transcribe.enforce_monotonic(words)
        self.assertEqual([item["start"] for item in cleaned], [0.0, 0.6])
        self.assertEqual([item["end"] for item in cleaned], [0.5, 1.1])

    def test_empty_input_is_handled(self) -> None:
        self.assertEqual(transcribe.enforce_monotonic([]), [])


class BackendResolutionTests(unittest.TestCase):
    def test_an_explicit_backend_is_returned_verbatim(self) -> None:
        self.assertEqual(transcribe.resolve_backend("faster-whisper"), "faster-whisper")
        self.assertEqual(transcribe.resolve_backend("openai-whisper"), "openai-whisper")

    @unittest.skipUnless(HAS_BACKEND, "a Whisper backend is required")
    def test_auto_finds_an_installed_backend(self) -> None:
        self.assertIn(transcribe.resolve_backend("auto"), {"faster-whisper", "openai-whisper"})

    @unittest.skipIf(HAS_BACKEND, "only meaningful with no backend installed")
    def test_auto_explains_how_to_install(self) -> None:
        with self.assertRaises(RuntimeError) as caught:
            transcribe.resolve_backend("auto")
        self.assertIn("pip install", str(caught.exception))


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg is required")
class AudioExtractionTests(unittest.TestCase):
    def test_extraction_produces_16k_mono_pcm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
            ], check=True)
            audio = transcribe.extract_audio(source, root / "out.wav")
            self.assertTrue(audio.is_file())
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=sample_rate,channels,codec_name",
                "-of", "json", str(audio),
            ], check=True, capture_output=True, text=True)
            stream = json.loads(probe.stdout)["streams"][0]
            self.assertEqual(stream["sample_rate"], "16000")
            self.assertEqual(stream["channels"], 1)
            self.assertEqual(stream["codec_name"], "pcm_s16le")


class CliTests(unittest.TestCase):
    def test_a_missing_source_errors_as_json(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(CLI), "--source", "nope.mp4", "--output", "out.json"],
            capture_output=True, text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("error", json.loads(completed.stdout))


if __name__ == "__main__":
    unittest.main()
