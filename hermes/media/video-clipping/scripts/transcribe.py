#!/usr/bin/env python3
"""Word-level transcription for the video-clipping skill.

Produces the flat word list every other command in this skill expects:

    [{"word": "You're", "start": 0.0, "end": 0.74}, ...]

Backends, tried in this order unless --backend forces one:

1. faster-whisper (CTranslate2). Fast on CPU, no PyTorch needed.
2. openai-whisper. Needs PyTorch; simpler install story on some machines.

Neither backend requires a GPU. Both accept --device cuda when one exists.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

WORD_MODELS = ("tiny", "base", "small", "medium", "large-v3", "turbo")


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def atomic_write_json(path: str | Path, payload: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, destination)


def extract_audio(source: Path, destination: Path) -> Path:
    """Whisper wants 16 kHz mono PCM. Give it exactly that."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to extract audio; install it first")
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
            "-acodec", "pcm_s16le", str(destination),
        ],
        check=True,
    )
    return destination


def transcribe_faster_whisper(audio: Path, model_name: str, device: str, language: str | None) -> list[dict[str, Any]]:
    from faster_whisper import WhisperModel

    compute_type = "float16" if device == "cuda" else "int8"
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, _ = model.transcribe(
        str(audio),
        language=language,
        word_timestamps=True,
        vad_filter=False,
    )
    words: list[dict[str, Any]] = []
    for segment in segments:
        for word in segment.words or []:
            text = str(word.word)
            if not text.strip():
                continue
            words.append({
                "word": text.strip() if not text.startswith(" ") else text[1:] or text.strip(),
                "start": round(float(word.start), 3),
                "end": round(float(word.end), 3),
            })
    return words


def transcribe_openai_whisper(audio: Path, model_name: str, device: str, language: str | None) -> list[dict[str, Any]]:
    import whisper

    model = whisper.load_model(model_name, device=device)
    result = model.transcribe(str(audio), word_timestamps=True, language=language, verbose=False)
    words: list[dict[str, Any]] = []
    for segment in result.get("segments", []):
        for word in segment.get("words", []):
            text = str(word.get("word", "")).strip()
            if not text:
                continue
            words.append({
                "word": text,
                "start": round(float(word["start"]), 3),
                "end": round(float(word["end"]), 3),
            })
    return words


def enforce_monotonic(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Whisper occasionally emits a word whose start precedes the previous end.

    The clipping contracts reject non-monotonic transcripts, so nudge the
    minimum amount required rather than dropping evidence.
    """
    cleaned: list[dict[str, Any]] = []
    previous_end = 0.0
    for word in words:
        start = max(float(word["start"]), previous_end)
        end = max(float(word["end"]), start + 0.01)
        cleaned.append({"word": word["word"], "start": round(start, 3), "end": round(end, 3)})
        previous_end = end
    return cleaned


def resolve_backend(requested: str) -> str:
    if requested != "auto":
        return requested
    if module_available("faster_whisper"):
        return "faster-whisper"
    if module_available("whisper"):
        return "openai-whisper"
    raise RuntimeError(
        "No Whisper backend found. Install one:\n"
        "  pip install faster-whisper        # recommended, CPU-friendly\n"
        "  pip install openai-whisper        # needs PyTorch"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a word-level transcript for video clipping.")
    parser.add_argument("--source", required=True, help="Video or audio file")
    parser.add_argument("--output", required=True, help="Destination transcript.json")
    parser.add_argument("--model", default="base", help=f"Whisper model size ({', '.join(WORD_MODELS)})")
    parser.add_argument("--backend", default="auto", choices=["auto", "faster-whisper", "openai-whisper"])
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--language", default=None, help="Force a language code, e.g. en. Omit to auto-detect.")
    parser.add_argument("--keep-audio", action="store_true", help="Keep the extracted 16 kHz wav next to the output")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.is_file():
        print(json.dumps({"error": f"source does not exist: {source}"}, indent=2))
        return 1

    try:
        backend = resolve_backend(args.backend)
    except RuntimeError as error:
        print(json.dumps({"error": str(error)}, indent=2))
        return 1

    output = Path(args.output).resolve()
    temporary_dir = output.parent if args.keep_audio else Path(tempfile.mkdtemp(prefix="clip-transcribe-"))
    audio = temporary_dir / f"{source.stem}-16k.wav"
    try:
        if source.suffix.lower() in {".wav"} and not args.keep_audio:
            audio = source
        else:
            extract_audio(source, audio)
        if backend == "faster-whisper":
            words = transcribe_faster_whisper(audio, args.model, args.device, args.language)
        else:
            words = transcribe_openai_whisper(audio, args.model, args.device, args.language)
    except (RuntimeError, OSError, subprocess.CalledProcessError, ImportError) as error:
        print(json.dumps({"error": str(error), "backend": backend}, indent=2))
        return 1
    finally:
        if not args.keep_audio and audio.name.endswith("-16k.wav") and audio.exists():
            shutil.rmtree(audio.parent, ignore_errors=True)

    if not words:
        print(json.dumps({"error": "no words were transcribed", "backend": backend}, indent=2))
        return 1

    words = enforce_monotonic(words)
    atomic_write_json(output, words)
    print(json.dumps({
        "output": str(output),
        "backend": backend,
        "model": args.model,
        "device": args.device,
        "wordCount": len(words),
        "durationSeconds": words[-1]["end"],
        "reviewNote": "Check proper nouns; Whisper mis-spells names and product terms.",
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
