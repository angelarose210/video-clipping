#!/usr/bin/env python3
"""Report what this skill can do on this machine, and what is missing.

Everything except Python and ffmpeg is optional. This tells you which parts of
the workflow are available now, what each missing piece would add, and the exact
command to install it.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def probe_source(source: Path) -> dict[str, Any]:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate,duration:format=duration,size",
        "-of", "json", str(source),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return {"error": completed.stderr.strip()[:200]}
    data = json.loads(completed.stdout)
    stream = (data.get("streams") or [{}])[0]
    rate = stream.get("avg_frame_rate", "0/1")
    numerator, denominator = (rate.split("/") + ["1"])[:2]
    fps = int(numerator) / int(denominator) if int(denominator or 0) else 0.0
    duration = float(data.get("format", {}).get("duration") or stream.get("duration") or 0)
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    return {
        "width": width, "height": height, "fps": round(fps, 3),
        "durationSeconds": round(duration, 2),
        "durationMinutes": round(duration / 60, 1),
        "orientation": "vertical" if height > width else "horizontal" if width > height else "square",
        "needsReframing": width >= height,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Report available capabilities for the video-clipping skill.")
    parser.add_argument("--source", help="Optional source video to probe")
    args = parser.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    has_cv2 = module_available("cv2")
    has_faster = module_available("faster_whisper")
    has_whisper = module_available("whisper")
    has_yolo = module_available("ultralytics")

    cuda = False
    cuda_device = None
    if module_available("torch"):
        try:
            import torch

            cuda = bool(torch.cuda.is_available())
            if cuda:
                cuda_device = torch.cuda.get_device_name(0)
        except (ImportError, RuntimeError):
            cuda = False

    report: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "required": {
            "ffmpeg": ffmpeg,
            "ffprobe": ffprobe,
        },
        "optional": {
            "faster-whisper": has_faster,
            "openai-whisper": has_whisper,
            "opencv-python": has_cv2,
            "ultralytics": has_yolo,
            "cuda": cuda,
            "cudaDevice": cuda_device,
        },
    }

    capabilities = {
        "select-and-clip": bool(ffmpeg and ffprobe),
        "transcribe": has_faster or has_whisper,
        "reframe-static": bool(ffmpeg and ffprobe),
        "reframe-sampled": has_cv2,
        "reframe-tracked": has_cv2,
        "quality-check": bool(ffmpeg and ffprobe),
    }
    report["capabilities"] = capabilities

    missing: list[dict[str, str]] = []
    if not (ffmpeg and ffprobe):
        missing.append({
            "what": "ffmpeg",
            "unlocks": "everything — this skill cannot run without it",
            "install": "choco install ffmpeg  (Windows) | brew install ffmpeg  (macOS) | apt install ffmpeg",
        })
    if not (has_faster or has_whisper):
        missing.append({
            "what": "a Whisper backend",
            "unlocks": "word-level transcription, which selection needs",
            "install": "pip install faster-whisper",
        })
    if not has_cv2:
        missing.append({
            "what": "opencv-python",
            "unlocks": "subject-aware reframing; without it only a fixed centre crop is available",
            "install": "pip install opencv-python",
        })
    if not has_yolo:
        missing.append({
            "what": "ultralytics",
            "unlocks": "the most accurate subject detector; the bundled OpenCV detectors work without it",
            "install": "pip install ultralytics",
        })
    report["missing"] = missing

    if has_yolo:
        detector = "yolo"
    elif has_cv2:
        detector = "hog or haar"
    else:
        detector = "center only"
    report["recommended"] = {
        "transcribeBackend": "faster-whisper" if has_faster else ("openai-whisper" if has_whisper else None),
        "reframeTier": "sampled" if has_cv2 else "static",
        "reframeDetector": detector,
        "device": "cuda" if cuda else "cpu",
    }

    if args.source:
        source = Path(args.source).resolve()
        report["source"] = {"path": str(source), "exists": source.is_file()}
        if source.is_file() and ffprobe:
            probed = probe_source(source)
            report["source"].update(probed)
            notes: list[str] = []
            minutes = probed.get("durationMinutes") or 0
            if minutes:
                notes.append(
                    f"Transcription with the base model on CPU: roughly "
                    f"{max(1, round(minutes * 0.15))}-{max(2, round(minutes * 0.25))} minutes."
                )
            if probed.get("needsReframing"):
                notes.append("Horizontal source: clips will need reframing to 9:16.")
            else:
                notes.append("Already vertical: no reframing needed.")
            report["source"]["notes"] = notes

    report["ready"] = capabilities["select-and-clip"] and capabilities["transcribe"]
    if not report["ready"] and capabilities["select-and-clip"]:
        report["note"] = "Clipping works, but you need a Whisper backend or a pre-made word-level transcript."
    print(json.dumps(report, indent=2))
    return 0 if capabilities["select-and-clip"] else 1


if __name__ == "__main__":
    sys.exit(main())
