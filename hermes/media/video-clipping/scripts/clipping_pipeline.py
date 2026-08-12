#!/usr/bin/env python3
"""CLI for deterministic long-form-to-shorts selection and materialization."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from contracts import (
    DEFAULT_WEIGHTS, RANGE_SEMANTICS, SCHEMA_VERSION, atomic_write_json,
    build_semantic_windows, fingerprint, probe_video, rank_candidates, read_json,
    seconds_to_frame, validate_word_transcript,
)

STAGES = ["discovered", "analyzed", "ranked", "selected", "materialized", "directed", "handoffs-ready", "built", "rendered", "qc-passed", "delivered"]


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def request_path(run_root: Path) -> Path:
    return run_root / "SHORTS_REQUEST.json"


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    source = Path(args.source).resolve()
    if not source.is_file():
        raise ValueError(f"Source does not exist: {source}")
    root = Path(args.run_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "analysis").mkdir(exist_ok=True)
    (root / "clips").mkdir(exist_ok=True)
    video = probe_video(source)
    request = {
        "schemaVersion": SCHEMA_VERSION, "rangeSemantics": RANGE_SEMANTICS,
        "sourcePath": str(source), "sourceFingerprint": fingerprint(source), "sourceVideo": video,
        "inputs": {
            "transcriptPath": str(Path(args.transcript).resolve()) if args.transcript else None,
            "visualDescriptionPath": str(Path(args.visual_description).resolve()) if args.visual_description else None,
        },
        "target": {
            "count": args.count, "platforms": args.platform,
            "width": args.width, "height": args.height, "fps": args.fps,
            "minimumDurationSeconds": args.minimum_duration,
            "maximumDurationSeconds": args.maximum_duration,
        },
        "selection": {
            "weights": {key: float(value) for key, value in DEFAULT_WEIGHTS.items()},
            "temporalOverlapThreshold": args.overlap_threshold, "topicDiversity": not args.no_topic_diversity,
        },
    }
    existing = request_path(root)
    if existing.exists() and read_json(existing) != request and not args.replace:
        raise ValueError("A different request already exists; pass --replace to replace it")
    atomic_write_json(existing, request)
    manifest = {
        "schemaVersion": SCHEMA_VERSION, "requestPath": "SHORTS_REQUEST.json",
        "sourceSha256": request["sourceFingerprint"]["sha256"],
        "stages": {stage: {"status": "complete" if stage == "discovered" else "pending"} for stage in STAGES},
        "clips": [],
    }
    atomic_write_json(root / "SHORTS_RUN_MANIFEST.json", manifest)
    return {"request": str(existing), "manifest": str(root / "SHORTS_RUN_MANIFEST.json"), "ready": True}


def windows(args: argparse.Namespace) -> dict[str, Any]:
    request = read_json(args.request)
    transcript = read_json(args.transcript)
    errors = validate_word_transcript(transcript, request["sourceVideo"]["durationSeconds"])
    if errors:
        raise ValueError("; ".join(errors[:20]))
    output = Path(args.output or Path(args.request).parent / "analysis" / "transcript.windows.json")
    payload = {
        "schemaVersion": SCHEMA_VERSION, "rangeSemantics": RANGE_SEMANTICS,
        "sourceSha256": request["sourceFingerprint"]["sha256"],
        "windows": build_semantic_windows(transcript),
    }
    atomic_write_json(output, payload)
    return {"output": str(output.resolve()), "windowCount": len(payload["windows"])}


def rank(args: argparse.Namespace) -> dict[str, Any]:
    request = read_json(args.request)
    raw = read_json(args.candidates)
    candidates = raw.get("candidates", raw) if isinstance(raw, dict) else raw
    result = rank_candidates(request, candidates)
    output = Path(args.output or Path(args.request).parent / "analysis" / "ranked-shorts.json")
    atomic_write_json(output, result)
    return {"output": str(output.resolve()), "selectedCount": result["selectedCount"], "requestedCount": result["requestedCount"]}


def rebase_transcript(words: list[dict[str, Any]], start_seconds: float, end_seconds: float) -> list[dict[str, Any]]:
    rebased = []
    for index, word in enumerate(words):
        if float(word["end"]) <= start_seconds or float(word["start"]) >= end_seconds:
            continue
        item = dict(word)
        item["parentWordIndex"] = index
        item["parentStart"] = word["start"]
        item["parentEnd"] = word["end"]
        item["start"] = round(max(0.0, float(word["start"]) - start_seconds), 6)
        item["end"] = round(min(end_seconds, float(word["end"])) - start_seconds, 6)
        rebased.append(item)
    return rebased


def materialize(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.run_root).resolve()
    request = read_json(request_path(root))
    ranked = read_json(root / "analysis" / "ranked-shorts.json")
    transcript_path = request.get("inputs", {}).get("transcriptPath")
    if not transcript_path or not Path(transcript_path).is_file():
        raise ValueError("A word-level transcriptPath is required before materialization")
    transcript = read_json(transcript_path)
    source = request["sourcePath"]
    source_fps = float(request["sourceVideo"]["fps"])
    target_fps = float(request["target"]["fps"])
    clips = []
    for candidate in ranked["selected"]:
        clip_id = f"{candidate['rank']:02d}-{candidate['id']}"
        project = root / "clips" / clip_id
        contract_path = project / "CLIP_CONTRACT.json"
        expected_parent = request["sourceFingerprint"]["sha256"]
        if contract_path.exists() and read_json(contract_path).get("source", {}).get("parentSha256") != expected_parent and not args.replace:
            raise ValueError(f"Refusing to overwrite nonmatching child project: {project}")
        if project.exists() and args.replace:
            shutil.rmtree(project)
        video_dir = project / "public" / "videos"
        video_dir.mkdir(parents=True, exist_ok=True)
        (project / "public" / "reframing").mkdir(parents=True, exist_ok=True)
        (project / "out").mkdir(parents=True, exist_ok=True)
        output_video = video_dir / "source.mp4"
        start_seconds = candidate["startFrame"] / source_fps
        end_seconds = candidate["endFrameExclusive"] / source_fps
        duration = end_seconds - start_seconds
        command = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", source,
            "-ss", f"{start_seconds:.9f}", "-t", f"{duration:.9f}",
            "-map", "0:v:0", "-map", "0:a?", "-vf", f"fps={target_fps}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output_video),
        ]
        subprocess.run(command, check=True)
        child_video = probe_video(output_video)
        rebased = rebase_transcript(transcript, start_seconds, end_seconds)
        atomic_write_json(project / "public" / "transcript.json", rebased)
        visual = request.get("inputs", {}).get("visualDescriptionPath")
        if visual and Path(visual).is_file():
            shutil.copy2(visual, project / "public" / "source_visual_description.txt")
        contract = {
            "schemaVersion": SCHEMA_VERSION, "rangeSemantics": RANGE_SEMANTICS,
            "id": clip_id, "rank": candidate["rank"],
            "source": {
                "parentPath": source, "parentSha256": expected_parent,
                "sourceRange": {"startFrame": candidate["startFrame"], "endFrameExclusive": candidate["endFrameExclusive"]},
                "materializedPath": "public/videos/source.mp4", "materializedFingerprint": fingerprint(output_video),
            },
            "timeline": {
                "startFrame": 0, "endFrameExclusive": child_video["totalFrames"], "fps": target_fps,
                # Delivery target. The materialized file below keeps its native
                # resolution so editing does not work from an upscale; whatever
                # renders the final clip is what scales to these dimensions.
                "width": request["target"]["width"], "height": request["target"]["height"],
                "materialized": {
                    "width": child_video["width"], "height": child_video["height"],
                    "fps": child_video["fps"], "totalFrames": child_video["totalFrames"],
                },
            },
            "selection": {
                "scores": candidate["scores"], "composite": candidate["composite"],
                "topicKey": candidate["topicKey"], "hookText": candidate.get("hookText"),
                "suggestedTitle": candidate.get("suggestedTitle"),
            },
            "evidence": {"transcriptPath": "public/transcript.json", "visualDescriptionPath": "public/source_visual_description.txt" if visual else None},
        }
        atomic_write_json(contract_path, contract)
        clips.append({"id": clip_id, "project": str(project), "contract": str(contract_path), "output": str(output_video)})
    manifest_path = root / "SHORTS_RUN_MANIFEST.json"
    manifest = read_json(manifest_path)
    manifest["clips"] = clips
    manifest["stages"]["materialized"] = {"status": "complete", "count": len(clips)}
    atomic_write_json(manifest_path, manifest)
    return {"clips": clips, "count": len(clips)}


def validate(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.run_root).resolve()
    errors: list[str] = []
    request = read_json(request_path(root))
    current = fingerprint(request["sourcePath"])
    if current["sha256"] != request["sourceFingerprint"]["sha256"]:
        errors.append("source fingerprint changed")
    ranked_path = root / "analysis" / "ranked-shorts.json"
    if not ranked_path.is_file():
        errors.append("ranked-shorts.json is missing")
    manifest = read_json(root / "SHORTS_RUN_MANIFEST.json")
    for clip in manifest.get("clips", []):
        contract_path = Path(clip["contract"])
        if not contract_path.is_file():
            errors.append(f"missing contract: {contract_path}")
            continue
        contract = read_json(contract_path)
        materialized = contract_path.parent / contract["source"]["materializedPath"]
        if not materialized.is_file():
            errors.append(f"missing materialized video: {materialized}")
        elif fingerprint(materialized)["sha256"] != contract["source"]["materializedFingerprint"]["sha256"]:
            errors.append(f"stale materialized video: {materialized}")
    return {"ready": not errors, "errors": errors, "clipCount": len(manifest.get("clips", []))}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze, rank, and materialize short clips from long-form video.")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("preflight")
    command.add_argument("--source", required=True); command.add_argument("--run-root", required=True)
    command.add_argument("--transcript"); command.add_argument("--visual-description")
    command.add_argument("--count", type=int, default=5); command.add_argument("--platform", action="append", default=["tiktok", "reels", "youtube-shorts"])
    command.add_argument("--width", type=int, default=1080); command.add_argument("--height", type=int, default=1920); command.add_argument("--fps", type=float, default=30)
    command.add_argument("--minimum-duration", type=float, default=20); command.add_argument("--maximum-duration", type=float, default=60)
    command.add_argument("--overlap-threshold", type=float, default=0.5); command.add_argument("--no-topic-diversity", action="store_true"); command.add_argument("--replace", action="store_true")
    command = sub.add_parser("windows"); command.add_argument("--request", required=True); command.add_argument("--transcript", required=True); command.add_argument("--output")
    command = sub.add_parser("rank"); command.add_argument("--request", required=True); command.add_argument("--candidates", required=True); command.add_argument("--output")
    command = sub.add_parser("materialize"); command.add_argument("--run-root", required=True); command.add_argument("--replace", action="store_true")
    command = sub.add_parser("validate"); command.add_argument("--run-root", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = {"preflight": preflight, "windows": windows, "rank": rank, "materialize": materialize, "validate": validate}[args.command](args)
        print_json(result)
        return 0 if result.get("ready", True) else 1
    except (ValueError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print_json({"error": str(error), "command": args.command})
        return 1


if __name__ == "__main__":
    sys.exit(main())
