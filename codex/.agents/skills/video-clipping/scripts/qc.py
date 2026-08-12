#!/usr/bin/env python3
"""Local quality checks for a rendered short clip.

Needs only ffmpeg and ffprobe. No API key, no upload, no external service.

Catches the mechanical failures: wrong dimensions, corrupt frames, missing or
silent audio, clipping, black frames, frozen stretches, and clip boundaries
that land mid-syllable. It cannot judge whether the hook works — watch the
clip as well.

Exit code 0 when every check passes, 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Platform normalisation targets cluster in this range.
LOUDNESS_MINIMUM = -16.0
LOUDNESS_MAXIMUM = -8.0
TRUE_PEAK_CEILING = -0.1
# A boundary is judged against the clip's own average level, not an absolute dB
# value. Absolute thresholds cannot separate quiet speech from a loud room: two
# clean cuts in the same test source measured -44 dB and -24 dB. A boundary this
# many dB below the clip mean is room tone.
BOUNDARY_HEADROOM_DB = 8.0
SILENCE_DB = -60.0


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def probe(video: Path) -> dict[str, Any]:
    completed = run([
        "ffprobe", "-v", "error", "-count_frames",
        "-show_entries",
        "stream=index,codec_type,codec_name,width,height,avg_frame_rate,nb_read_frames,duration,channels,sample_rate"
        ":format=duration,size,format_name",
        "-of", "json", str(video),
    ])
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {completed.stderr.strip()}")
    return json.loads(completed.stdout)


def parse_rate(value: str | None) -> float:
    if not value or "/" not in value:
        return 0.0
    numerator, denominator = value.split("/")
    try:
        return int(numerator) / int(denominator) if int(denominator) else 0.0
    except ValueError:
        return 0.0


def finding(name: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"check": name, "status": status, "detail": detail, **extra}


def check_spec(
    data: dict[str, Any], expect_width: int | None, expect_height: int | None,
    expect_fps: float | None, expect_frames: int | None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    video_streams = [s for s in data["streams"] if s.get("codec_type") == "video"]
    if not video_streams:
        return [finding("video-stream", "fail", "no video stream found")]
    stream = video_streams[0]
    width, height = int(stream["width"]), int(stream["height"])
    fps = parse_rate(stream.get("avg_frame_rate"))

    if expect_width and expect_height:
        if width == expect_width and height == expect_height:
            findings.append(finding("dimensions", "pass", f"{width}x{height}"))
        else:
            findings.append(finding(
                "dimensions", "fail",
                f"expected {expect_width}x{expect_height}, rendered {width}x{height}",
            ))
    else:
        findings.append(finding("dimensions", "pass", f"{width}x{height} (no expectation supplied)"))

    if height > width:
        findings.append(finding("orientation", "pass", "vertical"))
    else:
        findings.append(finding(
            "orientation", "warn",
            f"not vertical ({width}x{height}); short-form platforms expect 9:16",
        ))

    if expect_fps:
        if abs(fps - expect_fps) < 0.05:
            findings.append(finding("fps", "pass", f"{fps:.3f}"))
        else:
            findings.append(finding("fps", "fail", f"expected {expect_fps}, rendered {fps:.3f}"))
    else:
        findings.append(finding("fps", "pass", f"{fps:.3f} (no expectation supplied)"))

    frames = int(stream.get("nb_read_frames") or 0)
    if expect_frames:
        # One frame of slack absorbs container rounding.
        if abs(frames - expect_frames) <= 1:
            findings.append(finding("frame-count", "pass", f"{frames}"))
        else:
            findings.append(finding(
                "frame-count", "fail",
                f"expected {expect_frames}, rendered {frames}",
            ))
    else:
        findings.append(finding("frame-count", "pass", f"{frames}"))

    duration = float(data.get("format", {}).get("duration") or stream.get("duration") or 0)
    findings.append(finding("duration", "pass", f"{duration:.3f}s", seconds=round(duration, 3)))
    codecs = ", ".join(f"{s['codec_type']}:{s.get('codec_name')}" for s in data["streams"])
    findings.append(finding("codecs", "pass", codecs))
    return findings


def check_decode(video: Path) -> list[dict[str, Any]]:
    completed = run([
        "ffmpeg", "-v", "error", "-xerror", "-i", str(video),
        "-f", "null", "-" if sys.platform != "win32" else "NUL",
    ])
    errors = [line for line in completed.stderr.splitlines() if line.strip()]
    if completed.returncode == 0 and not errors:
        return [finding("decode", "pass", "every frame decoded cleanly")]
    return [finding(
        "decode", "fail",
        f"{len(errors)} decode error(s); file may be truncated or corrupt",
        sample=errors[:5],
    )]


def check_audio(video: Path, data: dict[str, Any]) -> tuple[list[dict[str, Any]], float | None]:
    """Return the findings plus the clip's mean level for the boundary check."""
    audio_streams = [s for s in data["streams"] if s.get("codec_type") == "audio"]
    if not audio_streams:
        return [finding("audio-present", "fail", "no audio stream; a silent short is a wasted upload")], None
    findings = [finding(
        "audio-present", "pass",
        f"{audio_streams[0].get('codec_name')} {audio_streams[0].get('channels')}ch "
        f"{audio_streams[0].get('sample_rate')}Hz",
    )]
    completed = run([
        "ffmpeg", "-v", "info", "-i", str(video), "-map", "0:a:0",
        "-af", "volumedetect", "-f", "null", "-" if sys.platform != "win32" else "NUL",
    ])
    match = re.search(r"mean_volume:\s*(-?[\d.]+) dB", completed.stderr)
    if not match:
        findings.append(finding("audio-level", "warn", "could not measure mean volume"))
        return findings, None
    mean = float(match.group(1))
    if mean <= SILENCE_DB:
        findings.append(finding("audio-level", "fail", f"audio is effectively silent ({mean:.1f} dB mean)", meanDb=mean))
    else:
        findings.append(finding("audio-level", "pass", f"{mean:.1f} dB mean", meanDb=mean))
    return findings, mean


def check_loudness(video: Path, data: dict[str, Any]) -> list[dict[str, Any]]:
    if not any(s.get("codec_type") == "audio" for s in data["streams"]):
        return []
    completed = run([
        "ffmpeg", "-v", "info", "-i", str(video), "-map", "0:a:0",
        "-af", "loudnorm=print_format=json", "-f", "null",
        "-" if sys.platform != "win32" else "NUL",
    ])
    blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", completed.stderr, re.DOTALL)
    if not blocks:
        return [finding("loudness", "warn", "could not measure loudness")]
    try:
        measured = json.loads(blocks[-1])
    except json.JSONDecodeError:
        return [finding("loudness", "warn", "loudness output was not parseable")]
    findings: list[dict[str, Any]] = []
    integrated = float(measured.get("input_i", 0))
    peak = float(measured.get("input_tp", 0))
    if LOUDNESS_MINIMUM <= integrated <= LOUDNESS_MAXIMUM:
        findings.append(finding("loudness", "pass", f"{integrated:.1f} LUFS integrated", lufs=integrated))
    else:
        findings.append(finding(
            "loudness", "warn",
            f"{integrated:.1f} LUFS is outside the {LOUDNESS_MINIMUM} to {LOUDNESS_MAXIMUM} range "
            f"platforms normalise toward",
            lufs=integrated,
        ))
    if peak > TRUE_PEAK_CEILING:
        findings.append(finding(
            "true-peak", "fail",
            f"{peak:.2f} dBTP exceeds {TRUE_PEAK_CEILING} dBTP and will clip on playback",
            truePeak=peak,
        ))
    else:
        findings.append(finding("true-peak", "pass", f"{peak:.2f} dBTP", truePeak=peak))
    return findings


def check_black_and_freeze(video: Path, duration: float) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    completed = run([
        "ffmpeg", "-v", "info", "-i", str(video),
        "-vf", "blackdetect=d=0.1:pic_th=0.98", "-an", "-f", "null",
        "-" if sys.platform != "win32" else "NUL",
    ])
    blacks = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", completed.stderr)
    if not blacks:
        findings.append(finding("black-frames", "pass", "none detected"))
    else:
        opening = [pair for pair in blacks if float(pair[0]) < 0.5]
        status = "fail" if opening else "warn"
        note = "black frames at the head where the hook should be" if opening else "black frames mid-clip"
        findings.append(finding(
            "black-frames", status, f"{note}: {len(blacks)} range(s)",
            ranges=[{"start": float(a), "end": float(b)} for a, b in blacks[:5]],
        ))

    completed = run([
        "ffmpeg", "-v", "info", "-i", str(video),
        "-vf", "freezedetect=n=-60dB:d=1.0", "-an", "-f", "null",
        "-" if sys.platform != "win32" else "NUL",
    ])
    freezes = re.findall(r"freeze_start:\s*([\d.]+)", completed.stderr)
    if freezes:
        findings.append(finding(
            "frozen-frames", "warn",
            f"{len(freezes)} frozen stretch(es) of 1s or longer",
            starts=[float(value) for value in freezes[:5]],
        ))
    else:
        findings.append(finding("frozen-frames", "pass", "none detected"))
    return findings


def measure_window(video: Path, start: float, duration: float) -> float | None:
    completed = run([
        "ffmpeg", "-v", "info", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
        "-i", str(video), "-map", "0:a:0", "-af", "volumedetect",
        "-f", "null", "-" if sys.platform != "win32" else "NUL",
    ])
    match = re.search(r"mean_volume:\s*(-?[\d.]+) dB", completed.stderr)
    return float(match.group(1)) if match else None


def check_boundaries(
    video: Path, duration: float, clip_mean_db: float | None, window: float = 0.15
) -> list[dict[str, Any]]:
    """A clipped syllable lives in the first or last fraction of a second.

    The window is deliberately narrow: measuring half a second catches ordinary
    speech that starts just after a clean cut, which would warn on every clip
    and teach you to ignore the warning.

    The comparison is relative to the clip's own mean level. A boundary at
    speech level is suspicious; one well below it is room tone.
    """
    if duration <= window * 4:
        return [finding("boundaries", "warn", "clip too short to measure boundaries")]
    if clip_mean_db is None:
        return [finding("boundaries", "warn", "clip level unknown; cannot judge boundaries")]
    threshold = clip_mean_db - BOUNDARY_HEADROOM_DB
    findings: list[dict[str, Any]] = []
    head = measure_window(video, 0.0, window)
    tail = measure_window(video, max(0.0, duration - window), window)
    for name, value in (("head", head), ("tail", tail)):
        present = "starts" if name == "head" else "ends"
        bare = "start" if name == "head" else "end"
        position = "first" if name == "head" else "last"
        if value is None:
            findings.append(finding(f"boundary-{name}", "warn", "could not measure"))
        elif value <= threshold:
            findings.append(finding(
                f"boundary-{name}", "pass",
                f"{value:.1f} dB against a {clip_mean_db:.1f} dB clip mean - {present} below speech level",
                meanDb=value, thresholdDb=round(threshold, 1),
            ))
        else:
            findings.append(finding(
                f"boundary-{name}", "warn",
                f"{value:.1f} dB in the {position} {window}s sits at speech level for this clip "
                f"({clip_mean_db:.1f} dB mean); it may {bare} mid-syllable - listen to it",
                meanDb=value, thresholdDb=round(threshold, 1),
            ))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Local quality checks for a rendered short clip.")
    parser.add_argument("--video", required=True)
    parser.add_argument("--contract", help="CLIP_CONTRACT.json to read expected specs from")
    parser.add_argument(
        "--stage", default="final", choices=["final", "source"],
        help="final (default): check against the contract's delivery dimensions. "
             "source: check a materialized clip against its own native dimensions.",
    )
    parser.add_argument("--expect-width", type=int)
    parser.add_argument("--expect-height", type=int)
    parser.add_argument("--expect-fps", type=float)
    parser.add_argument("--expect-frames", type=int)
    parser.add_argument("--skip-decode", action="store_true", help="Skip the full decode pass on very long files")
    args = parser.parse_args()

    for executable in ("ffmpeg", "ffprobe"):
        if not shutil.which(executable):
            print(json.dumps({"error": f"{executable} is required and was not found on PATH"}, indent=2))
            return 1

    video = Path(args.video).resolve()
    if not video.is_file():
        print(json.dumps({"error": f"video does not exist: {video}"}, indent=2))
        return 1

    width, height, fps, frames = args.expect_width, args.expect_height, args.expect_fps, args.expect_frames
    contract_id = None
    if args.contract:
        contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
        timeline = contract.get("timeline", {})
        # A materialized clip keeps its source resolution; only the final render
        # is expected at the contract's delivery dimensions.
        expected = timeline
        if args.stage == "source":
            expected = timeline.get("materialized") or timeline
        width = width or expected.get("width")
        height = height or expected.get("height")
        fps = fps or expected.get("fps")
        frames = frames or expected.get("totalFrames") or timeline.get("endFrameExclusive")
        contract_id = contract.get("id")

    try:
        data = probe(video)
    except (RuntimeError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1

    duration = float(data.get("format", {}).get("duration") or 0)
    findings = check_spec(data, width, height, fps, frames)
    if not args.skip_decode:
        findings.extend(check_decode(video))
    audio_findings, clip_mean_db = check_audio(video, data)
    findings.extend(audio_findings)
    findings.extend(check_loudness(video, data))
    findings.extend(check_black_and_freeze(video, duration))
    if clip_mean_db is not None:
        findings.extend(check_boundaries(video, duration, clip_mean_db))

    failures = [item for item in findings if item["status"] == "fail"]
    warnings = [item for item in findings if item["status"] == "warn"]
    report = {
        "video": str(video),
        "clipId": contract_id,
        "verdict": "fail" if failures else "pass",
        "counts": {
            "pass": len(findings) - len(failures) - len(warnings),
            "warn": len(warnings),
            "fail": len(failures),
        },
        "findings": findings,
        "note": "Automated checks cannot judge whether the hook lands. Watch the clip as well.",
    }
    print(json.dumps(report, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
