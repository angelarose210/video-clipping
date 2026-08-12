#!/usr/bin/env python3
"""Reframe a horizontal clip to vertical without requiring a GPU.

Four tiers, cheapest first. Every tier writes the same manifest shape, so
Remotion and the QC checks never need to know which tier produced it.

  static    Fixed crop from a hand-supplied or auto-detected centre. No model.
  sampled   DEFAULT. Detect the subject on a few sampled frames, interpolate
            between them, then smooth. CPU only. Seconds, not minutes.
  tracked   Per-frame detection on every frame. CPU works but is slow; a GPU
            makes it practical. Use when the subject moves fast or unusually.
  external  Import a manifest produced by another tool.

Detectors, in preference order, all optional:
  ultralytics YOLO   best quality, pip install ultralytics
  OpenCV HOG people  bundled with opencv-python, no download
  OpenCV Haar face   bundled with opencv-python, no download
  centre fallback    always available, honest about being a guess

Ranges are half-open: [startFrame, endFrameExclusive).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_NAME = "reframe.manifest.json"
SCHEMA_VERSION = 1
RANGE_SEMANTICS = "[startFrame,endFrameExclusive)"
DETECTORS = ("auto", "yolo", "hog", "haar", "center")
TIERS = ("static", "sampled", "tracked", "external")


# ---------------------------------------------------------------- utilities


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_write_json(path: str | Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, destination)


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fingerprint(path: str | Path) -> dict[str, Any]:
    source = Path(path).resolve()
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return {"path": str(source), "sha256": digest.hexdigest(), "sizeBytes": source.stat().st_size}


def probe_video(path: str | Path) -> dict[str, Any]:
    source = Path(path).resolve()
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries",
        "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,nb_read_frames,duration:format=duration",
        "-of", "json", str(source),
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    data = json.loads(completed.stdout)
    if not data.get("streams"):
        raise ValueError(f"No video stream found in {source}")
    stream = data["streams"][0]
    rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
    numerator, denominator = (int(part) for part in rate.split("/"))
    fps = numerator / denominator if denominator else 0.0
    duration = float(stream.get("duration") or data.get("format", {}).get("duration") or 0)
    frames = int(stream.get("nb_read_frames") or stream.get("nb_frames") or round(duration * fps))
    return {
        "width": int(stream["width"]), "height": int(stream["height"]),
        "fps": fps, "fpsRational": rate, "durationSeconds": duration, "totalFrames": frames,
    }


def native_crop_size(source_width: int, source_height: int, output_width: int, output_height: int) -> tuple[float, float]:
    """Largest crop of the target aspect ratio that fits inside the source."""
    target_aspect = output_width / output_height
    source_aspect = source_width / source_height
    if source_aspect >= target_aspect:
        crop_height = float(source_height)
        crop_width = crop_height * target_aspect
    else:
        crop_width = float(source_width)
        crop_height = crop_width / target_aspect
    return crop_width, crop_height


# ---------------------------------------------------------------- detectors


class CenterDetector:
    """Always available. Returns the frame centre and says so honestly."""

    name = "center"
    confident = False

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height

    def detect(self, frame: Any) -> dict[str, Any] | None:
        return {
            "box": [self.width * 0.3, 0.0, self.width * 0.7, float(self.height)],
            "confidence": 0.0,
            "detector": self.name,
        }


class HaarFaceDetector:
    """Bundled with opencv-python. Good on frontal talking-head footage."""

    name = "haar"
    confident = True

    def __init__(self) -> None:
        import cv2

        self.cv2 = cv2
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        self.cascade = cv2.CascadeClassifier(str(cascade_path))
        if self.cascade.empty():
            raise RuntimeError("Could not load the bundled Haar face cascade")

    def detect(self, frame: Any) -> dict[str, Any] | None:
        cv2 = self.cv2
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = self.cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6, minSize=(48, 48))
        if len(faces) == 0:
            return None
        x, y, w, h = max(faces, key=lambda box: int(box[2]) * int(box[3]))
        # Expand a face box into a torso-ish box so the crop is not chin-tight.
        centre_x = x + w / 2
        box_width = w * 3.0
        return {
            "box": [centre_x - box_width / 2, float(y - h * 0.6), centre_x + box_width / 2, float(y + h * 5.0)],
            "confidence": 0.6,
            "detector": self.name,
        }


class HogPeopleDetector:
    """Bundled with opencv-python. Works when the whole body is visible."""

    name = "hog"
    confident = True

    def __init__(self) -> None:
        import cv2

        self.cv2 = cv2
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(self, frame: Any) -> dict[str, Any] | None:
        cv2 = self.cv2
        height = frame.shape[0]
        scale = 480 / height if height > 480 else 1.0
        small = cv2.resize(frame, None, fx=scale, fy=scale) if scale != 1.0 else frame
        boxes, weights = self.hog.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.05)
        if len(boxes) == 0:
            return None
        index = int(max(range(len(boxes)), key=lambda i: float(weights[i])))
        x, y, w, h = (value / scale for value in boxes[index])
        return {
            "box": [float(x), float(y), float(x + w), float(y + h)],
            "confidence": min(1.0, max(0.0, float(weights[index]) / 2.0)),
            "detector": self.name,
        }


class YoloDetector:
    """Best quality when ultralytics is installed. Runs on CPU or CUDA."""

    name = "yolo"
    confident = True

    def __init__(self, weights: str, device: str) -> None:
        from ultralytics import YOLO

        self.model = YOLO(weights)
        self.device = device

    def detect(self, frame: Any) -> dict[str, Any] | None:
        results = self.model.predict(frame, classes=[0], device=self.device, verbose=False)
        if not results:
            return None
        boxes = results[0].boxes
        if boxes is None or len(boxes) == 0:
            return None
        best_index, best_area = None, -1.0
        for index in range(len(boxes)):
            x1, y1, x2, y2 = (float(value) for value in boxes.xyxy[index].tolist())
            area = (x2 - x1) * (y2 - y1)
            if area > best_area:
                best_index, best_area = index, area
        x1, y1, x2, y2 = (float(value) for value in boxes.xyxy[best_index].tolist())
        return {
            "box": [x1, y1, x2, y2],
            "confidence": float(boxes.conf[best_index]) if boxes.conf is not None else 0.5,
            "detector": self.name,
        }


def build_detector(requested: str, width: int, height: int, weights: str, device: str) -> Any:
    """Return the best available detector at or below the requested tier."""
    order = [requested] if requested != "auto" else ["yolo", "hog", "haar", "center"]
    failures: list[str] = []
    for name in order:
        try:
            if name == "yolo":
                if not module_available("ultralytics"):
                    failures.append("yolo: ultralytics not installed")
                    continue
                return YoloDetector(weights, device)
            if name == "hog":
                return HogPeopleDetector()
            if name == "haar":
                return HaarFaceDetector()
            if name == "center":
                return CenterDetector(width, height)
        except (ImportError, RuntimeError, OSError) as error:
            failures.append(f"{name}: {error}")
    raise RuntimeError("No detector available. " + "; ".join(failures))


# ---------------------------------------------------------------- sampling


def sample_indices(total_frames: int, every: int) -> list[int]:
    if total_frames <= 0:
        return []
    every = max(1, every)
    indices = list(range(0, total_frames, every))
    if indices[-1] != total_frames - 1:
        indices.append(total_frames - 1)
    return indices


def detect_scene_cuts(video_path: str | Path, fps: float, threshold: float) -> list[int]:
    """Cut list via ffmpeg's scene score. Cheap, and it needs no model."""
    null_target = "NUL" if os.name == "nt" else "/dev/null"
    command = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(Path(video_path).resolve()),
        "-vf", f"scale=480:-2,select='gt(scene,{threshold})',metadata=print:file=-",
        "-an", "-f", "null", null_target,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    text = (completed.stdout or "") + "\n" + (completed.stderr or "")
    cuts: list[int] = []
    pending: float | None = None
    for line in text.splitlines():
        if "pts_time:" in line:
            try:
                pending = float(line.split("pts_time:")[1].split()[0])
            except (IndexError, ValueError):
                pending = None
        elif pending is not None and "scene_score" in line:
            cuts.append(int(round(pending * fps)))
            pending = None
    return sorted({frame for frame in cuts if frame > 0})


def build_shots(cuts: list[int], total_frames: int, minimum_shot_frames: int) -> list[dict[str, Any]]:
    accepted: list[int] = []
    for frame in cuts:
        if frame >= total_frames:
            continue
        if frame - (accepted[-1] if accepted else 0) >= minimum_shot_frames:
            accepted.append(frame)
    if accepted and total_frames - accepted[-1] < minimum_shot_frames:
        accepted.pop()
    points = [0, *accepted, total_frames]
    return [
        {"id": f"shot-{index:03d}", "startFrame": start, "endFrameExclusive": end}
        for index, (start, end) in enumerate(zip(points, points[1:]), start=1)
    ]


def read_frames_at(video_path: str | Path, indices: list[int]) -> dict[int, Any]:
    """Read only the frames we need. Sequential decode, seek only forward."""
    import cv2

    wanted = sorted(set(indices))
    capture = cv2.VideoCapture(str(video_path))
    frames: dict[int, Any] = {}
    try:
        for index in wanted:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if ok:
                frames[index] = frame
    finally:
        capture.release()
    return frames


def detect_on_samples(
    video_path: str | Path, detector: Any, indices: list[int], shots: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    frames = read_frames_at(video_path, indices)
    shot_for = {}
    for shot in shots:
        for frame in range(shot["startFrame"], shot["endFrameExclusive"]):
            shot_for[frame] = shot["id"]
    records: list[dict[str, Any]] = []
    for index in indices:
        frame = frames.get(index)
        detection = detector.detect(frame) if frame is not None else None
        records.append({
            "frame": index,
            "shotId": shot_for.get(index, "shot-001"),
            "detection": detection,
        })
    return records


def interpolate_samples(
    samples: list[dict[str, Any]], shots: list[dict[str, Any]], width: int, height: int
) -> list[dict[str, Any]]:
    """Expand sampled detections into one record per frame, per shot.

    Interpolation stays inside a shot. Across a cut the subject can jump, so
    each shot interpolates only from its own samples and falls back to the
    frame centre when a shot has no confident sample at all.
    """
    by_shot: dict[str, list[dict[str, Any]]] = {shot["id"]: [] for shot in shots}
    for sample in samples:
        by_shot.setdefault(sample["shotId"], []).append(sample)

    records: list[dict[str, Any]] = []
    for shot in shots:
        shot_samples = [item for item in by_shot.get(shot["id"], []) if item["detection"]]
        anchors: list[tuple[int, float, float, float, float, str]] = []
        for item in shot_samples:
            box = item["detection"]["box"]
            centre_x = (box[0] + box[2]) / 2
            centre_y = (box[1] + box[3]) / 2
            anchors.append((
                item["frame"], centre_x, centre_y,
                float(box[3] - box[1]), float(item["detection"]["confidence"]),
                str(item["detection"]["detector"]),
            ))
        for frame in range(shot["startFrame"], shot["endFrameExclusive"]):
            if not anchors:
                records.append({
                    "frame": frame, "shotId": shot["id"],
                    "subjectCenter": [width / 2, height / 2], "subjectBox": None,
                    "confidence": 0.0, "flags": ["no-detection"], "detector": "center",
                })
                continue
            if len(anchors) == 1 or frame <= anchors[0][0]:
                anchor = anchors[0]
                centre_x, centre_y, box_height, confidence, detector = anchor[1], anchor[2], anchor[3], anchor[4], anchor[5]
            elif frame >= anchors[-1][0]:
                anchor = anchors[-1]
                centre_x, centre_y, box_height, confidence, detector = anchor[1], anchor[2], anchor[3], anchor[4], anchor[5]
            else:
                later = next(index for index, item in enumerate(anchors) if item[0] >= frame)
                left, right = anchors[later - 1], anchors[later]
                span = right[0] - left[0]
                ratio = 0.0 if span == 0 else (frame - left[0]) / span
                centre_x = left[1] + (right[1] - left[1]) * ratio
                centre_y = left[2] + (right[2] - left[2]) * ratio
                box_height = left[3] + (right[3] - left[3]) * ratio
                confidence = min(left[4], right[4])
                detector = left[5]
            records.append({
                "frame": frame, "shotId": shot["id"],
                "subjectCenter": [centre_x, centre_y],
                "subjectBox": [centre_x - box_height * 0.25, centre_y - box_height / 2,
                               centre_x + box_height * 0.25, centre_y + box_height / 2],
                "confidence": round(float(confidence), 4),
                "flags": [] if confidence > 0 else ["no-detection"],
                "detector": detector,
            })
    records.sort(key=lambda item: item["frame"])
    return records


def static_records(
    total_frames: int, centre_x: float, centre_y: float, width: int, height: int
) -> list[dict[str, Any]]:
    return [
        {
            "frame": frame, "shotId": "shot-001",
            "subjectCenter": [centre_x, centre_y], "subjectBox": None,
            "confidence": 1.0, "flags": [], "detector": "static",
        }
        for frame in range(total_frames)
    ]


# ---------------------------------------------------------------- framing


def load_framing():
    """Load the sibling framing module by path, not by name.

    A bare `import framing` would pick up any other module of that name that
    happens to be importable first.
    """
    import importlib.util

    path = Path(__file__).resolve().parent / "framing.py"
    spec = importlib.util.spec_from_file_location("_clipping_framing", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load framing module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.generate_shot_frames


def frames_for_records(
    records: list[dict[str, Any]], video: dict[str, Any], output_width: int, output_height: int,
    smooth_seconds: float, dead_zone: float, zoom: bool, min_zoom: float, max_zoom: float,
) -> list[dict[str, Any]]:
    generate_shot_frames = load_framing()
    output: list[dict[str, Any]] = []
    shot_ids: list[str] = []
    for record in records:
        if record["shotId"] not in shot_ids:
            shot_ids.append(record["shotId"])
    for shot_id in shot_ids:
        shot_records = [record for record in records if record["shotId"] == shot_id]
        output.extend(generate_shot_frames(
            shot_records,
            source_width=video["width"], source_height=video["height"], fps=video["fps"],
            output_width=output_width, output_height=output_height,
            smooth_seconds=smooth_seconds, dead_zone_pixels=dead_zone,
            zoom_enabled=zoom, min_zoom=min_zoom, max_zoom=max_zoom,
        ))
    output.sort(key=lambda item: item["frame"])
    return output


# ---------------------------------------------------------------- validation


def validate_frames(
    frames: list[dict[str, Any]], video: dict[str, Any], output_width: int, output_height: int
) -> dict[str, Any]:
    errors: list[str] = []
    if not frames:
        return {"passed": False, "errors": ["no frame records"], "checkedAt": utc_now()}
    expected_aspect = output_width / output_height
    indexes = [frame["frame"] for frame in frames]
    if indexes != list(range(indexes[0], indexes[0] + len(indexes))):
        errors.append("frame records are not continuous")
    if indexes[0] != 0:
        errors.append("frame records must start at 0")
    if len(frames) != video["totalFrames"]:
        errors.append(f"expected {video['totalFrames']} records, found {len(frames)}")
    for frame in frames:
        crop = frame["crop"]
        if crop["width"] <= 0 or crop["height"] <= 0:
            errors.append(f"frame {frame['frame']}: non-positive crop")
            break
        if crop["x"] < -0.5 or crop["y"] < -0.5:
            errors.append(f"frame {frame['frame']}: crop origin outside source")
            break
        if crop["x"] + crop["width"] > video["width"] + 0.5 or crop["y"] + crop["height"] > video["height"] + 0.5:
            errors.append(f"frame {frame['frame']}: crop exceeds source bounds")
            break
        if abs(crop["width"] / crop["height"] - expected_aspect) > 0.01:
            errors.append(f"frame {frame['frame']}: crop aspect does not match output")
            break
    return {"passed": not errors, "errors": errors, "checkedAt": utc_now()}


def uncertainty_ranges(records: list[dict[str, Any]], minimum_confidence: float) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    open_start: int | None = None
    for record in records:
        weak = float(record.get("confidence", 0.0)) < minimum_confidence or bool(record.get("flags"))
        if weak and open_start is None:
            open_start = record["frame"]
        elif not weak and open_start is not None:
            ranges.append({"startFrame": open_start, "endFrameExclusive": record["frame"], "reason": "low confidence"})
            open_start = None
    if open_start is not None:
        ranges.append({
            "startFrame": open_start,
            "endFrameExclusive": records[-1]["frame"] + 1,
            "reason": "low confidence",
        })
    return ranges


# ---------------------------------------------------------------- previews


def render_preview(
    video_path: str | Path, frames: list[dict[str, Any]], destination: Path,
    fps: float, output_width: int, output_height: int, proxy_height: int = 960,
) -> Path:
    """Burn the crop into a small mp4 so a human can approve it in one watch."""
    import cv2

    destination.parent.mkdir(parents=True, exist_ok=True)
    silent = destination.with_suffix(".silent.mp4")
    aspect = output_width / output_height
    preview_height = proxy_height
    preview_width = max(2, int(round(proxy_height * aspect)))
    preview_width += preview_width % 2
    preview_height += preview_height % 2
    capture = cv2.VideoCapture(str(video_path))
    writer = cv2.VideoWriter(str(silent), cv2.VideoWriter_fourcc(*"mp4v"), fps, (preview_width, preview_height))
    try:
        for entry in frames:
            ok, frame = capture.read()
            if not ok:
                break
            crop = entry["crop"]
            x1, y1 = int(round(crop["x"])), int(round(crop["y"]))
            x2 = int(round(crop["x"] + crop["width"]))
            y2 = int(round(crop["y"] + crop["height"]))
            cropped = frame[max(0, y1):y2, max(0, x1):x2]
            if cropped.size == 0:
                continue
            writer.write(cv2.resize(cropped, (preview_width, preview_height), interpolation=cv2.INTER_AREA))
    finally:
        capture.release()
        writer.release()
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(silent),
        "-i", str(Path(video_path).resolve()), "-map", "0:v:0", "-map", "1:a?", "-shortest",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(destination),
    ], check=True)
    silent.unlink(missing_ok=True)
    return destination


def render_contact_sheet(
    video_path: str | Path, frames: list[dict[str, Any]], shots: list[dict[str, Any]], destination_dir: Path
) -> list[dict[str, Any]]:
    import cv2

    destination_dir.mkdir(parents=True, exist_ok=True)
    by_frame = {entry["frame"]: entry for entry in frames}
    wanted: list[tuple[str, str, int]] = []
    for shot in shots:
        start, end = shot["startFrame"], shot["endFrameExclusive"]
        for role, index in (("first", start), ("middle", start + (end - start - 1) // 2), ("last", end - 1)):
            if index in by_frame:
                wanted.append((shot["id"], role, index))
    images = read_frames_at(video_path, [index for _, _, index in wanted])
    stills: list[dict[str, Any]] = []
    for shot_id, role, index in wanted:
        frame = images.get(index)
        if frame is None:
            continue
        crop = by_frame[index]["crop"]
        x1, y1 = int(round(crop["x"])), int(round(crop["y"]))
        x2 = int(round(crop["x"] + crop["width"]))
        y2 = int(round(crop["y"] + crop["height"]))
        annotated = frame.copy()
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 220, 255), 6)
        label = f"{shot_id} {role} f={index} conf={by_frame[index].get('confidence', 0):.2f}"
        cv2.putText(annotated, label, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 6, cv2.LINE_AA)
        cv2.putText(annotated, label, (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2, cv2.LINE_AA)
        path = destination_dir / f"{shot_id}-{role}-{index:06d}.jpg"
        cv2.imwrite(str(path), annotated, [cv2.IMWRITE_JPEG_QUALITY, 88])
        stills.append({"shotId": shot_id, "role": role, "frame": index, "path": str(path)})
    return stills


# ---------------------------------------------------------------- commands


def command_preflight(args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "dependencies": {name: module_available(name) for name in ("cv2", "numpy", "ultralytics", "torch")},
        "executables": {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")},
        "cuda": False,
    }
    if module_available("torch"):
        try:
            import torch

            report["cuda"] = bool(torch.cuda.is_available())
            if report["cuda"]:
                report["cudaDevice"] = torch.cuda.get_device_name(0)
        except (ImportError, RuntimeError) as error:
            report["cudaError"] = str(error)
    available = [
        name for name, ok in (
            ("yolo", report["dependencies"]["ultralytics"]),
            ("hog", report["dependencies"]["cv2"]),
            ("haar", report["dependencies"]["cv2"]),
            ("center", True),
        ) if ok
    ]
    report["detectorsAvailable"] = available
    report["recommendedTier"] = "sampled" if report["dependencies"]["cv2"] else "static"
    report["recommendedDetector"] = available[0] if available else "center"
    if args.source:
        source = Path(args.source).resolve()
        report["source"] = {"path": str(source), "exists": source.is_file()}
        if source.is_file() and report["executables"]["ffprobe"]:
            report["video"] = probe_video(source)
    report["ready"] = bool(report["executables"]["ffmpeg"] and report["executables"]["ffprobe"])
    if not report["dependencies"]["cv2"]:
        report["note"] = "Install opencv-python for the sampled tier; static tier works with ffmpeg alone."
    return report


def command_plan(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.source).resolve()
    if not source.is_file():
        raise ValueError(f"Source does not exist: {source}")
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    video = probe_video(source)
    if video["totalFrames"] <= 0:
        raise ValueError("Could not determine the source frame count")

    tier = args.tier
    if tier == "external":
        raise ValueError("Use the import command for an externally produced manifest")

    crop_width, crop_height = native_crop_size(video["width"], video["height"], args.output_width, args.output_height)
    if tier == "static":
        centre_x = args.center_x if args.center_x is not None else video["width"] / 2
        centre_y = args.center_y if args.center_y is not None else video["height"] / 2
        records = static_records(video["totalFrames"], centre_x, centre_y, video["width"], video["height"])
        shots = [{"id": "shot-001", "startFrame": 0, "endFrameExclusive": video["totalFrames"]}]
        detector_name = "static"
        sampled = 0
    else:
        detector = build_detector(args.detector, video["width"], video["height"], args.weights, args.device)
        detector_name = detector.name
        cuts = [] if args.no_scene_detect else detect_scene_cuts(source, video["fps"], args.scene_threshold)
        shots = build_shots(cuts, video["totalFrames"], args.minimum_shot_frames)
        if tier == "tracked":
            indices = list(range(video["totalFrames"]))
        else:
            every = args.sample_every if args.sample_every else max(1, int(round(video["fps"] * args.sample_seconds)))
            indices = sample_indices(video["totalFrames"], every)
            for shot in shots:
                # Guarantee at least one sample right after every cut.
                if shot["startFrame"] not in indices:
                    indices.append(shot["startFrame"])
            indices = sorted(set(indices))
        samples = detect_on_samples(source, detector, indices, shots)
        sampled = len(indices)
        records = interpolate_samples(samples, shots, video["width"], video["height"])

    frames = frames_for_records(
        records, video, args.output_width, args.output_height,
        args.smooth_seconds, args.dead_zone, args.zoom, args.min_zoom, args.max_zoom,
    )
    validation = validate_frames(frames, video, args.output_width, args.output_height)
    unresolved = uncertainty_ranges(records, args.minimum_confidence)

    previews: dict[str, Any] = {"video": None, "stills": []}
    if not args.no_preview and module_available("cv2"):
        previews["stills"] = render_contact_sheet(source, frames, shots, workspace / "stills")
        try:
            previews["video"] = str(render_preview(
                source, frames, workspace / "preview.mp4",
                video["fps"], args.output_width, args.output_height,
            ))
        except (subprocess.CalledProcessError, OSError, ValueError) as error:
            previews["videoError"] = str(error)

    status = "review-pending" if validation["passed"] else "failed"
    if validation["passed"] and unresolved:
        status = "uncertain"
    manifest = {
        "schemaVersion": SCHEMA_VERSION, "rangeSemantics": RANGE_SEMANTICS,
        "tier": tier, "detector": detector_name,
        "source": fingerprint(source), "video": video,
        "output": {"width": args.output_width, "height": args.output_height,
                   "aspectRatio": args.output_width / args.output_height},
        "settings": {
            "sampleEvery": args.sample_every, "sampleSeconds": args.sample_seconds,
            "sampledFrames": sampled, "sceneThreshold": args.scene_threshold,
            "sceneDetect": not args.no_scene_detect, "minimumShotFrames": args.minimum_shot_frames,
            "smoothSeconds": args.smooth_seconds, "deadZonePixels": args.dead_zone,
            "zoomEnabled": args.zoom, "minZoom": args.min_zoom, "maxZoom": args.max_zoom,
            "minimumConfidence": args.minimum_confidence, "device": args.device,
            "nativeCrop": {"width": round(crop_width, 3), "height": round(crop_height, 3)},
        },
        "shots": shots, "frames": frames,
        "uncertainty": {"unresolvedRanges": unresolved, "accepted": []},
        "validation": validation,
        "previews": previews,
        "review": {"decision": "pending", "reviewedBy": None, "note": None, "reviewedAt": None},
        "status": status,
        "createdAt": utc_now(), "updatedAt": utc_now(),
    }
    manifest_path = workspace / MANIFEST_NAME
    atomic_write_json(manifest_path, manifest)
    return {
        "manifest": str(manifest_path), "tier": tier, "detector": detector_name,
        "sampledFrames": sampled, "frameCount": len(frames), "shotCount": len(shots),
        "status": status, "validationPassed": validation["passed"],
        "validationErrors": validation["errors"],
        "unresolvedRanges": len(unresolved),
        "preview": previews.get("video"),
        "ready": validation["passed"],
        "nextStep": "Watch the preview, then run accept-uncertainty if needed, then approve.",
    }


def command_accept_uncertainty(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest).resolve()
    manifest = read_json(manifest_path)
    ranges = manifest["uncertainty"]["unresolvedRanges"]
    if not ranges:
        return {"manifest": str(manifest_path), "accepted": 0, "status": manifest["status"], "ready": True}
    manifest["uncertainty"]["accepted"].extend([
        {**item, "acceptedBy": args.reviewed_by, "note": args.note, "acceptedAt": utc_now()}
        for item in ranges
    ])
    manifest["uncertainty"]["unresolvedRanges"] = []
    manifest["status"] = "review-pending" if manifest["validation"]["passed"] else manifest["status"]
    manifest["updatedAt"] = utc_now()
    atomic_write_json(manifest_path, manifest)
    return {"manifest": str(manifest_path), "accepted": len(ranges), "status": manifest["status"], "ready": True}


def command_approve(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest).resolve()
    manifest = read_json(manifest_path)
    blockers: list[str] = []
    if not manifest["validation"]["passed"]:
        blockers.extend(manifest["validation"]["errors"] or ["validation failed"])
    if manifest["uncertainty"]["unresolvedRanges"]:
        blockers.append(f"{len(manifest['uncertainty']['unresolvedRanges'])} unresolved uncertainty ranges")
    current = fingerprint(manifest["source"]["path"])
    if current["sha256"] != manifest["source"]["sha256"]:
        blockers.append("source file changed since planning")
    if blockers:
        return {"manifest": str(manifest_path), "ready": False, "blockers": blockers}
    manifest["review"] = {
        "decision": "approved", "reviewedBy": args.reviewed_by,
        "note": args.note, "reviewedAt": utc_now(),
    }
    manifest["status"] = "ready"
    manifest["updatedAt"] = utc_now()
    atomic_write_json(manifest_path, manifest)
    return {"manifest": str(manifest_path), "status": "ready", "ready": True}


def command_publish(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = Path(args.manifest).resolve()
    manifest = read_json(manifest_path)
    if manifest["status"] != "ready" or manifest["review"]["decision"] != "approved":
        raise ValueError("Refusing to publish a manifest that is not approved and ready")
    project = Path(args.project).resolve()
    destination = project / "public" / "reframing" / args.id
    destination.mkdir(parents=True, exist_ok=True)
    source = Path(manifest["source"]["path"])
    project_source = project / "public" / "videos" / source.name
    project_source.parent.mkdir(parents=True, exist_ok=True)
    if not project_source.exists() or fingerprint(project_source)["sha256"] != manifest["source"]["sha256"]:
        shutil.copy2(source, project_source)
    published = dict(manifest)
    published["source"] = {**manifest["source"], "projectPath": f"public/videos/{source.name}"}
    published["publishedAt"] = utc_now()
    published_path = destination / MANIFEST_NAME
    atomic_write_json(published_path, published)
    return {
        "manifest": str(published_path),
        "sourcePath": f"public/videos/{source.name}",
        "manifestPath": f"public/reframing/{args.id}/{MANIFEST_NAME}",
        "ready": True,
    }


def command_render(args: argparse.Namespace) -> dict[str, Any]:
    """Optional escape hatch: bake the crop with ffmpeg instead of Remotion."""
    manifest = read_json(args.manifest)
    if manifest["status"] != "ready" and not args.allow_unapproved:
        raise ValueError("Manifest is not ready; pass --allow-unapproved for a scratch render")
    frames = manifest["frames"]
    if not frames:
        raise ValueError("Manifest has no frame records")
    destination = Path(args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    commands_path = destination.with_suffix(".sendcmd.txt")
    fps = float(manifest["video"]["fps"])
    lines = []
    for entry in frames:
        crop = entry["crop"]
        lines.append(
            f"{entry['frame'] / fps:.9f} crop w {crop['width']:.3f}, crop h {crop['height']:.3f}, "
            f"crop x {crop['x']:.3f}, crop y {crop['y']:.3f};"
        )
    commands_path.write_text("\n".join(lines), encoding="utf-8")
    escaped = str(commands_path).replace("\\", "/").replace(":", "\\:")
    first = frames[0]["crop"]
    filter_graph = (
        f"sendcmd=f='{escaped}',"
        f"crop@crop={first['width']:.3f}:{first['height']:.3f}:{first['x']:.3f}:{first['y']:.3f},"
        f"scale={manifest['output']['width']}:{manifest['output']['height']}:flags=lanczos"
    )
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(Path(manifest["source"]["path"]).resolve()),
        "-map", "0:v:0", "-map", "0:a?", "-vf", filter_graph,
        "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(destination),
    ], check=True)
    commands_path.unlink(missing_ok=True)
    return {"output": str(destination), "ready": True}


def command_import(args: argparse.Namespace) -> dict[str, Any]:
    manifest = read_json(args.manifest)
    video = probe_video(manifest["source"]["path"])
    validation = validate_frames(
        manifest.get("frames", []), video,
        manifest["output"]["width"], manifest["output"]["height"],
    )
    manifest["validation"] = validation
    manifest["tier"] = "external"
    manifest["status"] = "review-pending" if validation["passed"] else "failed"
    manifest["updatedAt"] = utc_now()
    atomic_write_json(args.manifest, manifest)
    return {
        "manifest": str(Path(args.manifest).resolve()),
        "validationPassed": validation["passed"], "errors": validation["errors"],
        "ready": validation["passed"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CPU-first vertical reframing for short clips.")
    sub = parser.add_subparsers(dest="command", required=True)

    command = sub.add_parser("preflight", help="Report available detectors and recommend a tier")
    command.add_argument("--source")

    command = sub.add_parser("plan", help="Detect the subject and write a crop manifest")
    command.add_argument("--source", required=True)
    command.add_argument("--workspace", required=True)
    command.add_argument("--tier", default="sampled", choices=[tier for tier in TIERS if tier != "external"])
    command.add_argument("--detector", default="auto", choices=list(DETECTORS))
    command.add_argument("--output-width", type=int, default=1080)
    command.add_argument("--output-height", type=int, default=1920)
    command.add_argument("--sample-seconds", type=float, default=0.5,
                         help="Sampled tier: seconds between detections (default 0.5)")
    command.add_argument("--sample-every", type=int, default=None,
                         help="Sampled tier: frames between detections; overrides --sample-seconds")
    command.add_argument("--center-x", type=float, default=None, help="Static tier: subject centre x in source pixels")
    command.add_argument("--center-y", type=float, default=None, help="Static tier: subject centre y in source pixels")
    command.add_argument("--scene-threshold", type=float, default=0.25)
    command.add_argument("--no-scene-detect", action="store_true")
    command.add_argument("--minimum-shot-frames", type=int, default=12)
    command.add_argument("--smooth-seconds", type=float, default=0.35)
    command.add_argument("--dead-zone", type=float, default=45.0)
    command.add_argument("--zoom", action="store_true")
    command.add_argument("--min-zoom", type=float, default=1.0)
    command.add_argument("--max-zoom", type=float, default=1.25)
    command.add_argument("--minimum-confidence", type=float, default=0.25)
    command.add_argument("--weights", default="yolov8n.pt")
    command.add_argument("--device", default="cpu")
    command.add_argument("--no-preview", action="store_true")

    command = sub.add_parser("accept-uncertainty", help="Record explicit acceptance of low-confidence ranges")
    command.add_argument("--manifest", required=True)
    command.add_argument("--reviewed-by", required=True)
    command.add_argument("--note", required=True)

    command = sub.add_parser("approve", help="Record named approval after watching the preview")
    command.add_argument("--manifest", required=True)
    command.add_argument("--reviewed-by", required=True)
    command.add_argument("--note", default=None)

    command = sub.add_parser("publish", help="Copy source and manifest into a Remotion project")
    command.add_argument("--manifest", required=True)
    command.add_argument("--project", required=True)
    command.add_argument("--id", required=True)

    command = sub.add_parser("render", help="Bake the crop with ffmpeg instead of Remotion")
    command.add_argument("--manifest", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--allow-unapproved", action="store_true")

    command = sub.add_parser("import", help="Validate a manifest produced by another tool")
    command.add_argument("--manifest", required=True)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    handlers = {
        "preflight": command_preflight, "plan": command_plan,
        "accept-uncertainty": command_accept_uncertainty, "approve": command_approve,
        "publish": command_publish, "render": command_render, "import": command_import,
    }
    real_stdout = sys.stdout
    try:
        # Model loaders (ultralytics especially) print progress bars and download
        # notices to stdout. Send anything they emit to stderr so this command's
        # only stdout output is the JSON result contract.
        with contextlib.redirect_stdout(sys.stderr):
            result = handlers[args.command](args)
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as error:
        print(json.dumps({"error": str(error), "command": args.command}, indent=2), file=real_stdout)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True), file=real_stdout)
    return 0 if result.get("ready", True) else 1


if __name__ == "__main__":
    sys.exit(main())
