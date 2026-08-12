"""Deterministic contracts and selection helpers for long-form video clipping."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
RANGE_SEMANTICS = "[startFrame,endFrameExclusive)"
DEFAULT_WEIGHTS = {
    "hook": Decimal("0.30"),
    "selfContainedness": Decimal("0.20"),
    "emotion": Decimal("0.15"),
    "payoffDensity": Decimal("0.20"),
    "retention": Decimal("0.15"),
}


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
    stat = source.stat()
    return {"path": str(source), "sha256": digest.hexdigest(), "sizeBytes": stat.st_size}


def probe_video(path: str | Path) -> dict[str, Any]:
    source = Path(path).resolve()
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,nb_frames,nb_read_frames,duration:format=duration",
        "-of", "json", str(source),
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    data = json.loads(completed.stdout)
    if not data.get("streams"):
        raise ValueError(f"No video stream found in {source}")
    stream = data["streams"][0]
    rate = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
    numerator, denominator = (int(part) for part in rate.split("/"))
    fps = numerator / denominator
    duration = float(stream.get("duration") or data.get("format", {}).get("duration") or 0)
    frames = int(stream.get("nb_read_frames") or stream.get("nb_frames") or round(duration * fps))
    return {
        "width": int(stream["width"]), "height": int(stream["height"]),
        "fps": fps, "fpsRational": rate, "durationSeconds": duration,
        "totalFrames": frames,
    }


def seconds_to_frame(seconds: float, fps: float, mode: str = "nearest") -> int:
    value = Decimal(str(seconds)) * Decimal(str(fps))
    if mode == "floor":
        return int(math.floor(value))
    if mode == "ceil":
        return int(math.ceil(value))
    if mode != "nearest":
        raise ValueError("mode must be nearest, floor, or ceil")
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def validate_word_transcript(words: list[dict[str, Any]], duration: float | None = None) -> list[str]:
    errors: list[str] = []
    previous_start = -1.0
    previous_end = -1.0
    for index, word in enumerate(words):
        text = word.get("word")
        start, end = word.get("start"), word.get("end")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"word {index}: word must be non-empty text")
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (start, end)):
            errors.append(f"word {index}: timestamps must be finite numbers")
            continue
        if start < 0 or end <= start:
            errors.append(f"word {index}: invalid half-open time range")
        if start < previous_start or end < previous_end:
            errors.append(f"word {index}: timestamps are not monotonic")
        if duration is not None and end > duration + 0.25:
            errors.append(f"word {index}: end exceeds source duration")
        previous_start, previous_end = start, end
    return errors


def build_semantic_windows(
    words: list[dict[str, Any]], target_seconds: float = 10.0,
    minimum_seconds: float = 8.0, maximum_seconds: float = 12.0,
    strong_pause_seconds: float = 1.0,
) -> list[dict[str, Any]]:
    if not words:
        return []
    windows: list[dict[str, Any]] = []
    start_index = 0
    for index, word in enumerate(words):
        start = float(words[start_index]["start"])
        end = float(word["end"])
        elapsed = end - start
        next_gap = 0.0 if index + 1 == len(words) else float(words[index + 1]["start"]) - end
        sentence_end = str(word["word"]).rstrip().endswith((".", "?", "!"))
        should_close = (
            elapsed >= maximum_seconds
            or (elapsed >= minimum_seconds and (sentence_end or next_gap >= strong_pause_seconds))
            or (elapsed >= target_seconds and next_gap >= 0.35)
        )
        if should_close or index + 1 == len(words):
            segment = words[start_index:index + 1]
            windows.append({
                "id": f"window-{len(windows) + 1:04d}",
                "startWordIndex": start_index, "endWordIndexExclusive": index + 1,
                "startSeconds": float(segment[0]["start"]), "endSeconds": float(segment[-1]["end"]),
                "text": " ".join(str(item["word"]).strip() for item in segment),
            })
            start_index = index + 1
    return windows


def calculate_composite(scores: dict[str, Any], weights: dict[str, Any] | None = None) -> float:
    resolved = {key: Decimal(str(value)) for key, value in (weights or DEFAULT_WEIGHTS).items()}
    if sum(resolved.values()) != Decimal("1"):
        raise ValueError("score weights must total 1")
    total = Decimal("0")
    for key, weight in resolved.items():
        value = Decimal(str(scores.get(key)))
        if value < 0 or value > 10:
            raise ValueError(f"score {key} must be between 0 and 10")
        total += value * weight
    return float(total.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def temporal_overlap_ratio(left: dict[str, Any], right: dict[str, Any]) -> float:
    start = max(int(left["startFrame"]), int(right["startFrame"]))
    end = min(int(left["endFrameExclusive"]), int(right["endFrameExclusive"]))
    intersection = max(0, end - start)
    shorter = min(
        int(left["endFrameExclusive"]) - int(left["startFrame"]),
        int(right["endFrameExclusive"]) - int(right["startFrame"]),
    )
    return intersection / shorter if shorter > 0 else 0.0


def stable_candidate_id(source_sha256: str, start_frame: int, end_frame_exclusive: int) -> str:
    value = f"{source_sha256}:{start_frame}:{end_frame_exclusive}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:12]


def validate_candidate(candidate: dict[str, Any], request: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    start = candidate.get("startFrame")
    end = candidate.get("endFrameExclusive")
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
        errors.append("invalid half-open frame range")
        return errors
    fps = float(request["sourceVideo"]["fps"])
    duration = (end - start) / fps
    if duration < float(request["target"]["minimumDurationSeconds"]):
        errors.append("below minimum duration")
    if duration > float(request["target"]["maximumDurationSeconds"]):
        errors.append("above maximum duration")
    flags = set(candidate.get("rejectionFlags") or [])
    blocking = flags.intersection({"midSentence", "missingPayoff", "requiresPriorContext"})
    if blocking:
        errors.append("blocking flags: " + ", ".join(sorted(blocking)))
    try:
        calculate_composite(candidate.get("scores") or {}, request["selection"]["weights"])
    except (ValueError, TypeError, ArithmeticError) as error:
        errors.append(str(error))
    if not str(candidate.get("topicKey") or "").strip():
        errors.append("topicKey is required")
    return errors


def rank_candidates(request: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    source_sha = request["sourceFingerprint"]["sha256"]
    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for original in candidates:
        candidate = dict(original)
        errors = validate_candidate(candidate, request)
        candidate["id"] = candidate.get("id") or stable_candidate_id(source_sha, candidate.get("startFrame", -1), candidate.get("endFrameExclusive", -1))
        if errors:
            candidate["selection"] = {"status": "rejected", "reasons": errors}
            rejected.append(candidate)
            continue
        candidate["composite"] = calculate_composite(candidate["scores"], request["selection"]["weights"])
        valid.append(candidate)
    valid.sort(key=lambda item: (-item["composite"], item["startFrame"], item["id"]))

    kept: list[dict[str, Any]] = []
    threshold = float(request["selection"]["temporalOverlapThreshold"])
    for candidate in valid:
        suppressor = next((item for item in kept if temporal_overlap_ratio(candidate, item) >= threshold), None)
        if suppressor:
            candidate["selection"] = {"status": "rejected", "reasons": [f"overlaps {suppressor['id']}"]}
            rejected.append(candidate)
        else:
            kept.append(candidate)

    count = int(request["target"]["count"])
    selected: list[dict[str, Any]] = []
    if request["selection"].get("topicDiversity", True):
        seen: set[str] = set()
        for candidate in kept:
            if candidate["topicKey"] not in seen and len(selected) < count:
                selected.append(candidate)
                seen.add(candidate["topicKey"])
    for candidate in kept:
        if candidate not in selected and len(selected) < count:
            selected.append(candidate)
    for rank, candidate in enumerate(selected, start=1):
        candidate["rank"] = rank
        candidate["selection"] = {"status": "selected", "reasons": ["ranked by composite and diversity"]}
    for candidate in kept:
        if candidate not in selected:
            candidate["selection"] = {"status": "rejected", "reasons": ["outside requested count"]}
            rejected.append(candidate)
    rejected.sort(key=lambda item: (item.get("startFrame", -1), item["id"]))
    return {
        "schemaVersion": SCHEMA_VERSION, "rangeSemantics": RANGE_SEMANTICS,
        "requestedCount": count, "selectedCount": len(selected),
        "selected": selected, "rejected": rejected,
    }
