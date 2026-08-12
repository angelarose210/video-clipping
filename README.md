# Video Clipping

Turn one long video into finished vertical shorts. No GPU, no API keys, no cloud uploads.

Most clipping tools hand you timestamps and leave the work to you. This one transcribes the source, scores every candidate moment against five weighted signals, suppresses overlaps, cuts frame-accurate files, reframes to 9:16 around the subject, and checks each render before you post it.

## What It Does

- **Picks moments from the transcript, not from keyword hits.** Five weighted signals — hook, self-containedness, emotion, payoff density, retention — with cited evidence for each score. Ranking is deterministic: same input, same output, every time.
- **Deduplicates properly.** Temporal non-maximum suppression on overlap, then topic diversity, so you get five different clips instead of five cuts of the same 40 seconds.
- **Reframes without a GPU.** The default tier detects the subject on a few frames per second and interpolates between them. On a 4K test clip that ran in 13–19 s against 143 s for per-frame tracking, and the two crop paths differed by a mean of 4.3 px. An RTX 3060 saved 4 s of those 143 — decoding dominates, not inference.
- **Degrades instead of failing.** Detectors fall back YOLO → OpenCV HOG → OpenCV Haar → frame centre, and the manifest reports the confidence it actually achieved rather than pretending.
- **Checks its own output.** Local ffmpeg checks for dimensions, decode integrity, silent audio, loudness and true peak, black frames, frozen stretches, and cut boundaries that land mid-syllable.
- **Scaffolds captions that render.** One command writes a Remotion project from the clip contract — right fps, right dimensions, captions timed to the clip's own transcript. Two styles: per-word highlight, or sentence-shaped cues with no extra dependency.
- **Stops and asks when it should.** A crop plan is never auto-approved. An automatic reframe that quietly cuts off a head is worse than one that waits for a human.

## Quick Start

```
Use video-clipping to make 5 shorts from webinar.mp4
```

Or drive the scripts directly:

```bash
python3 scripts/preflight.py --source long.mp4
python3 scripts/transcribe.py --source long.mp4 --output transcript.json --model base
python3 scripts/clipping_pipeline.py preflight --source long.mp4 --run-root shorts-run --transcript transcript.json
python3 scripts/clipping_pipeline.py windows --request shorts-run/SHORTS_REQUEST.json --transcript transcript.json
# write analysis/candidates.raw.json from the transcript, then:
python3 scripts/clipping_pipeline.py rank --request shorts-run/SHORTS_REQUEST.json --candidates shorts-run/analysis/candidates.raw.json
python3 scripts/clipping_pipeline.py materialize --run-root shorts-run
python3 scripts/qc.py --stage source --video shorts-run/clips/01-a1b2c3/public/videos/source.mp4 --contract shorts-run/clips/01-a1b2c3/CLIP_CONTRACT.json
```

Captions and overlays, when a plain cut is not the deliverable:

```bash
python3 scripts/scaffold_remotion.py scaffold --project shorts-run/clips/01-a1b2c3 --caption-style word
cd shorts-run/clips/01-a1b2c3 && npm install && npx tsc --noEmit && npm run render
```

## Example

**Input:** a 48-minute talk, `--count 2`.

**Output:** two isolated clip projects, each with a frame-accurate cut, a rebased transcript, and a contract binding it to the parent range.

```
shorts-run/analysis/ranked-shorts.json
  selected:
    01-a1b2c3  frames 12420-13590  39.0s  composite 8.42  "The Mistake Almost Everyone Makes"
    02-d4e5f6  frames 31200-32010  27.0s  composite 7.85  "Why The Obvious Fix Backfires"
  rejected:
    overlap 0.71 with 01-a1b2c3 (temporal suppression)
    duplicate topicKey "common-mistake" (topic diversity)

shorts-run/clips/01-a1b2c3/
  CLIP_CONTRACT.json
  public/videos/source.mp4
  transcript.json
```

Then QC on the cut:

```json
{
  "verdict": "pass",
  "counts": { "pass": 12, "warn": 1, "fail": 0 },
  "findings": [
    { "check": "boundary-head", "status": "warn",
      "detail": "-21.4 dB in the first 0.15s sits at speech level for this clip (-23.1 dB mean); it may start mid-syllable - listen to it" }
  ]
}
```

That warning is the check earning its place. On a real run it flagged a 20 ms handle before the first spoken word, where the selection rules ask for 100–160 ms.

## Install

Requires **Python 3.10+** and **ffmpeg** on `PATH`. Nothing else is mandatory. `preflight.py` tells you what each optional package would unlock:

```bash
pip install faster-whisper    # transcription, CPU-friendly, no PyTorch
pip install opencv-python     # subject-aware reframing + two detectors
pip install ultralytics       # optional: adds the YOLO detector
```

Captions and overlays additionally need **Node.js 18+**. Plain vertical cuts do not.

Full detail, including per-platform ffmpeg commands and troubleshooting, is in `INSTALL.md` inside any format folder.

### Hermes

Copy `hermes/media/video-clipping/` to `~/.hermes/skills/media/`

### Codex CLI

Copy `codex/.agents/skills/video-clipping/` to `.agents/skills/` in your project

### OpenClaw

Copy `openclaw/skills/video-clipping/` to `.agents/skills/` in your project

### Claude Code

Copy `claude/.claude/skills/video-clipping/` to `.claude/skills/` in your project.

For an always-loaded condensed version, also copy `claude/.claude/rules/video-clipping.md` to `.claude/rules/`.

## Tests

```bash
python3 -m pytest tests -q
```

97 tests covering ranking, materialization, contracts, reframing, QC, transcription, and the Remotion scaffold. Tests needing ffmpeg skip themselves when it is absent, so a partial install still gives a useful signal.

## Not Included

**Visual description.** The scoring contract accepts visual evidence, but nothing here generates it. Selection works from the transcript, which is enough for talking-head and instructional footage. Bring your own vision model if you want visual signals.

**A caption design.** The scaffold writes working captions in two styles, but deliberately plain ones: a system font stack, white text, one highlight colour. No brand, no motion design, no end card. Typography and palette are yours.

**Publishing.** No upload, no scheduling, no platform API. The skill delivers files.

**Hook judgement.** QC measures the mechanical failures. It cannot tell whether the first two seconds earn the next thirty.

**Speaker diarization.** You get words with timestamps, not speaker labels. Interview footage still clips fine.

## License

MIT
