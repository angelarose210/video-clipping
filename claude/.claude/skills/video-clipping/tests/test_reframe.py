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
CLI = SKILL / "scripts" / "reframe.py"

spec = importlib.util.spec_from_file_location("_reframe", SKILL / "scripts" / "reframe.py")
reframe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reframe)

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
HAS_CV2 = importlib.util.find_spec("cv2") is not None


def make_source(destination: Path, width: int = 640, height: int = 360, seconds: int = 2, fps: int = 30) -> Path:
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate={fps}:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(destination),
    ], check=True)
    return destination


class PureHelperTests(unittest.TestCase):
    def test_native_crop_preserves_target_aspect(self) -> None:
        width, height = reframe.native_crop_size(1920, 1080, 1080, 1920)
        self.assertAlmostEqual(width / height, 1080 / 1920, places=6)
        self.assertEqual(height, 1080.0)
        self.assertLessEqual(width, 1920)

    def test_native_crop_handles_vertical_source(self) -> None:
        width, height = reframe.native_crop_size(1080, 1920, 1080, 1920)
        self.assertEqual((width, height), (1080.0, 1920.0))

    def test_sample_indices_always_include_last_frame(self) -> None:
        indices = reframe.sample_indices(100, 15)
        self.assertEqual(indices[0], 0)
        self.assertEqual(indices[-1], 99)
        self.assertLess(len(indices), 100)

    def test_sample_indices_tolerate_zero_step(self) -> None:
        self.assertEqual(reframe.sample_indices(3, 0), [0, 1, 2])

    def test_build_shots_covers_source_exactly_once(self) -> None:
        shots = reframe.build_shots([50, 120], 200, minimum_shot_frames=12)
        self.assertEqual(shots[0]["startFrame"], 0)
        self.assertEqual(shots[-1]["endFrameExclusive"], 200)
        for left, right in zip(shots, shots[1:]):
            self.assertEqual(left["endFrameExclusive"], right["startFrame"])

    def test_build_shots_drops_cuts_that_make_tiny_shots(self) -> None:
        shots = reframe.build_shots([2, 4, 6], 100, minimum_shot_frames=12)
        self.assertEqual(len(shots), 1)

    def test_interpolation_moves_between_anchors(self) -> None:
        shots = [{"id": "shot-001", "startFrame": 0, "endFrameExclusive": 11}]
        samples = [
            {"frame": 0, "shotId": "shot-001",
             "detection": {"box": [0, 0, 100, 200], "confidence": 0.9, "detector": "test"}},
            {"frame": 10, "shotId": "shot-001",
             "detection": {"box": [200, 0, 300, 200], "confidence": 0.9, "detector": "test"}},
        ]
        records = reframe.interpolate_samples(samples, shots, 640, 360)
        self.assertEqual(len(records), 11)
        self.assertAlmostEqual(records[0]["subjectCenter"][0], 50.0)
        self.assertAlmostEqual(records[10]["subjectCenter"][0], 250.0)
        self.assertAlmostEqual(records[5]["subjectCenter"][0], 150.0)

    def test_interpolation_does_not_cross_a_cut(self) -> None:
        shots = [
            {"id": "shot-001", "startFrame": 0, "endFrameExclusive": 5},
            {"id": "shot-002", "startFrame": 5, "endFrameExclusive": 10},
        ]
        samples = [
            {"frame": 0, "shotId": "shot-001",
             "detection": {"box": [0, 0, 100, 200], "confidence": 0.9, "detector": "test"}},
            {"frame": 5, "shotId": "shot-002",
             "detection": {"box": [500, 0, 600, 200], "confidence": 0.9, "detector": "test"}},
        ]
        records = reframe.interpolate_samples(samples, shots, 640, 360)
        # Every frame of shot one holds its own anchor; no drift toward shot two.
        self.assertEqual({record["subjectCenter"][0] for record in records[:5]}, {50.0})
        self.assertEqual({record["subjectCenter"][0] for record in records[5:]}, {550.0})

    def test_missing_detection_falls_back_to_centre_and_flags(self) -> None:
        shots = [{"id": "shot-001", "startFrame": 0, "endFrameExclusive": 3}]
        records = reframe.interpolate_samples([], shots, 640, 360)
        self.assertEqual(len(records), 3)
        for record in records:
            self.assertEqual(record["subjectCenter"], [320.0, 180.0])
            self.assertIn("no-detection", record["flags"])
            self.assertEqual(record["confidence"], 0.0)

    def test_uncertainty_ranges_are_half_open(self) -> None:
        records = [
            {"frame": 0, "confidence": 0.9, "flags": []},
            {"frame": 1, "confidence": 0.0, "flags": ["no-detection"]},
            {"frame": 2, "confidence": 0.0, "flags": ["no-detection"]},
            {"frame": 3, "confidence": 0.9, "flags": []},
        ]
        ranges = reframe.uncertainty_ranges(records, 0.25)
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0]["startFrame"], 1)
        self.assertEqual(ranges[0]["endFrameExclusive"], 3)

    def test_uncertainty_range_closes_at_the_final_frame(self) -> None:
        records = [
            {"frame": 0, "confidence": 0.9, "flags": []},
            {"frame": 1, "confidence": 0.0, "flags": ["no-detection"]},
        ]
        ranges = reframe.uncertainty_ranges(records, 0.25)
        self.assertEqual(ranges[0]["endFrameExclusive"], 2)

    def test_validation_rejects_a_gap_in_frame_records(self) -> None:
        video = {"width": 1920, "height": 1080, "totalFrames": 3}
        frames = [
            {"frame": 0, "crop": {"x": 0, "y": 0, "width": 607.5, "height": 1080}},
            {"frame": 2, "crop": {"x": 0, "y": 0, "width": 607.5, "height": 1080}},
        ]
        result = reframe.validate_frames(frames, video, 1080, 1920)
        self.assertFalse(result["passed"])

    def test_validation_rejects_a_crop_outside_source_bounds(self) -> None:
        video = {"width": 1920, "height": 1080, "totalFrames": 1}
        frames = [{"frame": 0, "crop": {"x": 1800, "y": 0, "width": 607.5, "height": 1080}}]
        result = reframe.validate_frames(frames, video, 1080, 1920)
        self.assertFalse(result["passed"])
        self.assertTrue(any("bounds" in error for error in result["errors"]))

    def test_validation_rejects_a_wrong_aspect_crop(self) -> None:
        video = {"width": 1920, "height": 1080, "totalFrames": 1}
        frames = [{"frame": 0, "crop": {"x": 0, "y": 0, "width": 1000, "height": 1080}}]
        result = reframe.validate_frames(frames, video, 1080, 1920)
        self.assertFalse(result["passed"])
        self.assertTrue(any("aspect" in error for error in result["errors"]))

    def test_validation_accepts_a_correct_manifest(self) -> None:
        video = {"width": 1920, "height": 1080, "totalFrames": 2}
        frames = [
            {"frame": 0, "crop": {"x": 0, "y": 0, "width": 607.5, "height": 1080}},
            {"frame": 1, "crop": {"x": 10, "y": 0, "width": 607.5, "height": 1080}},
        ]
        result = reframe.validate_frames(frames, video, 1080, 1920)
        self.assertTrue(result["passed"], result["errors"])


class CenterDetectorTests(unittest.TestCase):
    def test_center_detector_reports_zero_confidence(self) -> None:
        detector = reframe.CenterDetector(640, 360)
        detection = detector.detect(None)
        self.assertEqual(detection["confidence"], 0.0)
        self.assertEqual(detection["detector"], "center")


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg is required")
class CliTests(unittest.TestCase):
    def run_cli(self, *arguments: str, expect: int = 0) -> dict:
        completed = subprocess.run(
            [sys.executable, str(CLI), *arguments], capture_output=True, text=True
        )
        self.assertEqual(completed.returncode, expect, completed.stdout + completed.stderr)
        return json.loads(completed.stdout)

    def test_preflight_reports_a_recommendation(self) -> None:
        report = self.run_cli("preflight")
        self.assertIn(report["recommendedTier"], {"static", "sampled"})
        self.assertIn("center", report["detectorsAvailable"])

    def test_stdout_is_only_json(self) -> None:
        """Model loaders print to stdout; that must not corrupt the contract."""
        completed = subprocess.run(
            [sys.executable, str(CLI), "preflight"], capture_output=True, text=True
        )
        json.loads(completed.stdout)

    def test_static_tier_needs_no_detector(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_source(root / "source.mp4", 640, 360, 2, 30)
            result = self.run_cli(
                "plan", "--source", str(source), "--workspace", str(root / "ws"),
                "--tier", "static", "--output-width", "1080", "--output-height", "1920",
                "--no-preview",
            )
            self.assertTrue(result["validationPassed"], result["validationErrors"])
            self.assertEqual(result["frameCount"], 60)
            manifest = json.loads((root / "ws" / "reframe.manifest.json").read_text(encoding="utf-8"))
            first = manifest["frames"][0]["crop"]
            self.assertAlmostEqual(first["width"] / first["height"], 1080 / 1920, places=4)

    def test_plan_never_self_approves(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_source(root / "source.mp4", 640, 360, 2, 30)
            self.run_cli(
                "plan", "--source", str(source), "--workspace", str(root / "ws"),
                "--tier", "static", "--no-preview",
            )
            manifest = json.loads((root / "ws" / "reframe.manifest.json").read_text(encoding="utf-8"))
            self.assertNotEqual(manifest["status"], "ready")
            self.assertEqual(manifest["review"]["decision"], "pending")

    def test_publish_refuses_an_unapproved_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_source(root / "source.mp4", 640, 360, 2, 30)
            self.run_cli(
                "plan", "--source", str(source), "--workspace", str(root / "ws"),
                "--tier", "static", "--no-preview",
            )
            result = self.run_cli(
                "publish", "--manifest", str(root / "ws" / "reframe.manifest.json"),
                "--project", str(root / "project"), "--id", "hero", expect=1,
            )
            self.assertIn("error", result)

    def test_approve_then_publish_writes_project_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_source(root / "source.mp4", 640, 360, 2, 30)
            manifest_path = root / "ws" / "reframe.manifest.json"
            self.run_cli(
                "plan", "--source", str(source), "--workspace", str(root / "ws"),
                "--tier", "static", "--no-preview",
            )
            self.run_cli("approve", "--manifest", str(manifest_path), "--reviewed-by", "Tester")
            result = self.run_cli(
                "publish", "--manifest", str(manifest_path),
                "--project", str(root / "project"), "--id", "hero",
            )
            self.assertEqual(result["manifestPath"], "public/reframing/hero/reframe.manifest.json")
            self.assertTrue((root / "project" / "public" / "videos" / "source.mp4").is_file())
            self.assertTrue((root / "project" / "public" / "reframing" / "hero" / "reframe.manifest.json").is_file())

    def test_approve_refuses_when_the_source_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_source(root / "source.mp4", 640, 360, 2, 30)
            manifest_path = root / "ws" / "reframe.manifest.json"
            self.run_cli(
                "plan", "--source", str(source), "--workspace", str(root / "ws"),
                "--tier", "static", "--no-preview",
            )
            make_source(source, 640, 360, 3, 30)
            result = self.run_cli("approve", "--manifest", str(manifest_path), "--reviewed-by", "Tester", expect=1)
            self.assertFalse(result["ready"])
            self.assertTrue(any("source" in blocker for blocker in result["blockers"]))

    def test_accept_uncertainty_unblocks_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_source(root / "source.mp4", 640, 360, 2, 30)
            manifest_path = root / "ws" / "reframe.manifest.json"
            planned = self.run_cli(
                "plan", "--source", str(source), "--workspace", str(root / "ws"),
                "--tier", "sampled", "--detector", "center", "--no-preview",
            )
            self.assertGreater(planned["unresolvedRanges"], 0)
            self.run_cli("approve", "--manifest", str(manifest_path), "--reviewed-by", "Tester", expect=1)
            self.run_cli(
                "accept-uncertainty", "--manifest", str(manifest_path),
                "--reviewed-by", "Tester", "--note", "synthetic fixture",
            )
            approved = self.run_cli("approve", "--manifest", str(manifest_path), "--reviewed-by", "Tester")
            self.assertEqual(approved["status"], "ready")

    def test_render_bakes_the_crop_at_target_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_source(root / "source.mp4", 640, 360, 2, 30)
            manifest_path = root / "ws" / "reframe.manifest.json"
            self.run_cli(
                "plan", "--source", str(source), "--workspace", str(root / "ws"),
                "--tier", "static", "--output-width", "1080", "--output-height", "1920",
                "--no-preview",
            )
            self.run_cli("approve", "--manifest", str(manifest_path), "--reviewed-by", "Tester")
            output = root / "vertical.mp4"
            self.run_cli("render", "--manifest", str(manifest_path), "--output", str(output))
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
                "-of", "json", str(output),
            ], check=True, capture_output=True, text=True)
            streams = json.loads(probe.stdout)["streams"]
            video = next(stream for stream in streams if stream["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), (1080, 1920))
            self.assertIn("audio", {stream["codec_type"] for stream in streams})

    def test_import_revalidates_a_foreign_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_source(root / "source.mp4", 640, 360, 1, 30)
            manifest_path = root / "foreign.json"
            manifest_path.write_text(json.dumps({
                "schemaVersion": 1,
                "source": reframe.fingerprint(source),
                "output": {"width": 1080, "height": 1920},
                "status": "ready",
                "frames": [
                    {"frame": index, "crop": {"x": 0, "y": 0, "width": 202.5, "height": 360}}
                    for index in range(30)
                ],
            }), encoding="utf-8")
            result = self.run_cli("import", "--manifest", str(manifest_path))
            self.assertTrue(result["validationPassed"], result["errors"])
            reloaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            # An incoming "ready" claim is not trusted.
            self.assertEqual(reloaded["status"], "review-pending")

    def test_import_rejects_a_short_frame_array(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_source(root / "source.mp4", 640, 360, 1, 30)
            manifest_path = root / "foreign.json"
            manifest_path.write_text(json.dumps({
                "schemaVersion": 1,
                "source": reframe.fingerprint(source),
                "output": {"width": 1080, "height": 1920},
                "status": "ready",
                "frames": [{"frame": 0, "crop": {"x": 0, "y": 0, "width": 202.5, "height": 360}}],
            }), encoding="utf-8")
            result = self.run_cli("import", "--manifest", str(manifest_path), expect=1)
            self.assertFalse(result["validationPassed"])


@unittest.skipUnless(HAS_FFMPEG and HAS_CV2, "ffmpeg and opencv-python are required")
class SampledTierTests(unittest.TestCase):
    def test_sampled_tier_covers_every_frame_from_few_detections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_source(root / "source.mp4", 640, 360, 4, 30)
            completed = subprocess.run([
                sys.executable, str(CLI), "plan", "--source", str(source),
                "--workspace", str(root / "ws"), "--tier", "sampled",
                "--detector", "center", "--sample-seconds", "0.5", "--no-preview",
            ], capture_output=True, text=True)
            result = json.loads(completed.stdout)
            self.assertEqual(result["frameCount"], 120)
            self.assertLess(result["sampledFrames"], 20)
            self.assertTrue(result["validationPassed"], result["validationErrors"])


if __name__ == "__main__":
    unittest.main()
