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
CLI = SKILL / "scripts" / "qc.py"

spec = importlib.util.spec_from_file_location("_qc", CLI)
qc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qc)

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_clip(
    destination: Path, width: int = 1080, height: int = 1920, seconds: int = 3,
    fps: int = 30, audio: bool = True, volume: float = 0.2,
) -> Path:
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps}:duration={seconds}",
    ]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}"]
    command += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        command += ["-c:a", "aac", "-af", f"volume={volume}", "-shortest"]
    else:
        command += ["-an"]
    command.append(str(destination))
    subprocess.run(command, check=True)
    return destination


class BoundaryThresholdTests(unittest.TestCase):
    """The boundary check is relative to the clip's own level.

    An absolute dB threshold cannot tell quiet speech from a loud room: two
    clean cuts in the same source measured -44 dB and -24 dB.
    """

    def test_audio_check_returns_the_clip_mean(self) -> None:
        signature = qc.check_audio.__annotations__.get("return", "")
        self.assertIn("tuple", str(signature).lower())

    def test_a_quiet_boundary_passes_in_a_quiet_clip(self) -> None:
        original = qc.measure_window
        qc.measure_window = lambda video, start, duration: -44.0
        try:
            findings = qc.check_boundaries(Path("unused.mp4"), 10.0, clip_mean_db=-23.0)
        finally:
            qc.measure_window = original
        self.assertTrue(all(item["status"] == "pass" for item in findings), findings)

    def test_the_same_level_warns_in_a_quieter_clip(self) -> None:
        """-44 dB is room tone next to speech at -23 dB, but not next to -46 dB."""
        original = qc.measure_window
        qc.measure_window = lambda video, start, duration: -44.0
        try:
            findings = qc.check_boundaries(Path("unused.mp4"), 10.0, clip_mean_db=-46.0)
        finally:
            qc.measure_window = original
        self.assertTrue(all(item["status"] == "warn" for item in findings), findings)

    def test_unknown_clip_level_does_not_guess(self) -> None:
        findings = qc.check_boundaries(Path("unused.mp4"), 10.0, clip_mean_db=None)
        self.assertEqual(findings[0]["status"], "warn")
        self.assertIn("cannot judge", findings[0]["detail"])

    def test_a_very_short_clip_is_not_measured(self) -> None:
        findings = qc.check_boundaries(Path("unused.mp4"), 0.2, clip_mean_db=-20.0)
        self.assertEqual(findings[0]["check"], "boundaries")
        self.assertEqual(findings[0]["status"], "warn")

    def test_warning_text_is_grammatical(self) -> None:
        original = qc.measure_window
        qc.measure_window = lambda video, start, duration: -10.0
        try:
            findings = qc.check_boundaries(Path("unused.mp4"), 10.0, clip_mean_db=-11.0)
        finally:
            qc.measure_window = original
        details = " ".join(item["detail"] for item in findings)
        self.assertIn("may start mid-syllable", details)
        self.assertIn("may end mid-syllable", details)
        self.assertNotIn("may starts", details)
        self.assertNotIn("may ends", details)


class PureHelperTests(unittest.TestCase):
    def test_parse_rate_handles_rational_and_junk(self) -> None:
        self.assertAlmostEqual(qc.parse_rate("30000/1001"), 29.97, places=2)
        self.assertEqual(qc.parse_rate("30/1"), 30.0)
        self.assertEqual(qc.parse_rate("0/0"), 0.0)
        self.assertEqual(qc.parse_rate(None), 0.0)
        self.assertEqual(qc.parse_rate("garbage"), 0.0)

    def test_spec_check_fails_on_wrong_dimensions(self) -> None:
        data = {
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                         "avg_frame_rate": "30/1", "nb_read_frames": "90"}],
            "format": {"duration": "3.0"},
        }
        findings = qc.check_spec(data, 1080, 1920, 30.0, 90)
        dimensions = next(item for item in findings if item["check"] == "dimensions")
        self.assertEqual(dimensions["status"], "fail")

    def test_spec_check_warns_when_not_vertical(self) -> None:
        data = {
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                         "avg_frame_rate": "30/1", "nb_read_frames": "90"}],
            "format": {"duration": "3.0"},
        }
        findings = qc.check_spec(data, None, None, None, None)
        orientation = next(item for item in findings if item["check"] == "orientation")
        self.assertEqual(orientation["status"], "warn")

    def test_spec_check_allows_one_frame_of_rounding(self) -> None:
        data = {
            "streams": [{"codec_type": "video", "width": 1080, "height": 1920,
                         "avg_frame_rate": "30/1", "nb_read_frames": "90"}],
            "format": {"duration": "3.0"},
        }
        findings = qc.check_spec(data, 1080, 1920, 30.0, 91)
        frames = next(item for item in findings if item["check"] == "frame-count")
        self.assertEqual(frames["status"], "pass")

    def test_spec_check_fails_a_real_frame_shortfall(self) -> None:
        data = {
            "streams": [{"codec_type": "video", "width": 1080, "height": 1920,
                         "avg_frame_rate": "30/1", "nb_read_frames": "60"}],
            "format": {"duration": "2.0"},
        }
        findings = qc.check_spec(data, 1080, 1920, 30.0, 90)
        frames = next(item for item in findings if item["check"] == "frame-count")
        self.assertEqual(frames["status"], "fail")

    def test_spec_check_fails_a_wrong_fps(self) -> None:
        data = {
            "streams": [{"codec_type": "video", "width": 1080, "height": 1920,
                         "avg_frame_rate": "25/1", "nb_read_frames": "75"}],
            "format": {"duration": "3.0"},
        }
        findings = qc.check_spec(data, 1080, 1920, 30.0, None)
        fps = next(item for item in findings if item["check"] == "fps")
        self.assertEqual(fps["status"], "fail")

    def test_missing_video_stream_is_a_failure(self) -> None:
        findings = qc.check_spec({"streams": [], "format": {}}, 1080, 1920, 30.0, 90)
        self.assertEqual(findings[0]["status"], "fail")


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg is required")
class CliTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> tuple[dict, int]:
        completed = subprocess.run(
            [sys.executable, str(CLI), *arguments], capture_output=True, text=True
        )
        return json.loads(completed.stdout), completed.returncode

    def test_a_clean_vertical_clip_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clip = make_clip(Path(temporary) / "clip.mp4")
            report, code = self.run_cli(
                "--video", str(clip), "--expect-width", "1080",
                "--expect-height", "1920", "--expect-fps", "30",
            )
            self.assertEqual(report["verdict"], "pass", report["findings"])
            self.assertEqual(code, 0)
            self.assertEqual(report["counts"]["fail"], 0)

    def test_every_audio_check_reports_a_measurement(self) -> None:
        """Guards a regression where volumedetect printed below the log level."""
        with tempfile.TemporaryDirectory() as temporary:
            clip = make_clip(Path(temporary) / "clip.mp4")
            report, _ = self.run_cli("--video", str(clip))
            level = next(item for item in report["findings"] if item["check"] == "audio-level")
            self.assertIn("meanDb", level, level)
            loudness = next(item for item in report["findings"] if item["check"] == "loudness")
            self.assertIn("lufs", loudness, loudness)

    def test_missing_audio_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            clip = make_clip(Path(temporary) / "silent.mp4", audio=False)
            report, code = self.run_cli("--video", str(clip))
            self.assertEqual(report["verdict"], "fail")
            self.assertEqual(code, 1)
            audio = next(item for item in report["findings"] if item["check"] == "audio-present")
            self.assertEqual(audio["status"], "fail")

    def test_wrong_dimensions_fail_against_a_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clip = make_clip(root / "clip.mp4", width=1920, height=1080)
            contract = root / "CLIP_CONTRACT.json"
            contract.write_text(json.dumps({
                "id": "01-test",
                "timeline": {"width": 1080, "height": 1920, "fps": 30, "endFrameExclusive": 90},
            }), encoding="utf-8")
            report, code = self.run_cli("--video", str(clip), "--contract", str(contract))
            self.assertEqual(report["verdict"], "fail")
            self.assertEqual(code, 1)
            self.assertEqual(report["clipId"], "01-test")

    def test_contract_supplies_expectations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clip = make_clip(root / "clip.mp4")
            contract = root / "CLIP_CONTRACT.json"
            contract.write_text(json.dumps({
                "id": "01-good",
                "timeline": {"width": 1080, "height": 1920, "fps": 30, "endFrameExclusive": 90},
            }), encoding="utf-8")
            report, code = self.run_cli("--video", str(clip), "--contract", str(contract))
            self.assertEqual(report["verdict"], "pass", report["findings"])
            self.assertEqual(code, 0)

    def test_a_missing_file_errors_cleanly(self) -> None:
        report, code = self.run_cli("--video", "does-not-exist.mp4")
        self.assertIn("error", report)
        self.assertEqual(code, 1)

    def test_black_opening_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            clip = root / "black.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:size=1080x1920:rate=30:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip),
            ], check=True)
            report, code = self.run_cli("--video", str(clip))
            black = next(item for item in report["findings"] if item["check"] == "black-frames")
            self.assertEqual(black["status"], "fail")
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
