# Transcription

Selection needs word-level timestamps. Sentence-level output is not enough: clip boundaries land between words, and a boundary placed on a guessed timestamp clips the first or last syllable.

No GPU is required. Both backends run on CPU.

## Install a backend

Pick one.

```powershell
# Recommended. CTranslate2 build, fast on CPU, no PyTorch.
pip install faster-whisper

# Alternative. Needs PyTorch, which is a much larger install.
pip install openai-whisper
```

Also required: ffmpeg on `PATH`, for extracting 16 kHz mono audio.

- Windows: `choco install ffmpeg` or `winget install Gyan.FFmpeg`
- macOS: `brew install ffmpeg`
- Debian/Ubuntu: `sudo apt install ffmpeg`

Check both at once:

```powershell
ffmpeg -version
py -c "import importlib.util as u; print('faster_whisper', bool(u.find_spec('faster_whisper')), '| whisper', bool(u.find_spec('whisper')))"
```

## Run it

```powershell
py scripts/transcribe.py --source 'C:\path\long.mp4' --output 'C:\path\transcript.json' --model base
```

The script extracts audio itself, picks whichever backend is installed, and writes the flat word list the rest of the skill expects:

```json
[
  { "word": "You're", "start": 0.0, "end": 0.74 },
  { "word": "ready", "start": 0.75, "end": 1.12 }
]
```

Flags:

- `--model` — `tiny`, `base`, `small`, `medium`, `large-v3`, `turbo`. `base` is a sound default; move to `small` or `medium` for accented speech or noisy audio.
- `--backend` — force `faster-whisper` or `openai-whisper` instead of auto-detecting.
- `--device cuda` — use a GPU if one exists. Optional; CPU is the default.
- `--language en` — skip auto-detection.
- `--keep-audio` — keep the extracted 16 kHz wav for waveform inspection at cut points.

## Model size and runtime

A 60-minute source on CPU with `faster-whisper`:

| Model | Rough runtime | Use when |
|-------|---------------|----------|
| `tiny` | 3–6 min | Rough pass, testing the pipeline |
| `base` | 6–12 min | Default; clear speech |
| `small` | 15–25 min | Accents, some background noise |
| `medium` | 40–60 min | Difficult audio, names that matter |

These scale with the machine. Start with `base`, and only move up if the transcript is too rough to select from.

## Review before selecting

Whisper mis-spells proper nouns with confidence. "Claude" becomes "Clawed", product names and player names come out wrong. Selection quotes `hookText` from the transcript and that text can end up in a title or caption, so scan the transcript for names before ranking.

Timestamp accuracy matters more than spelling. Word timestamps are alignment evidence, not acoustically safe cut points — always confirm boundaries against the waveform, per [selection-workflow.md](selection-workflow.md).

## Bringing your own transcript

Any word list matching the shape above works. Requirements enforced by `contracts.py`:

- Non-empty `word` text.
- Finite numeric `start` and `end`, with `end > start`.
- Monotonic across the list: no word starts before the previous word ends.
- Nothing ending more than 0.25 s past the probed source duration.

`transcribe.py` nudges non-monotonic output into range automatically. A hand-built or third-party transcript may need the same treatment. `windows` reports every violation with its word index and exits non-zero rather than proceeding on bad input.
