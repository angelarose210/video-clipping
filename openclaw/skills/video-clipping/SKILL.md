---
name: video-clipping
description: "Use when turning a long-form video into short-form clips for TikTok, Instagram Reels, or YouTube Shorts - requests like "clip this podcast", "find the best moments", "make five shorts", or "turn this long video into reels". Performs word-level transcription, transcript-first candidate selection, deterministic weighted scoring with temporal deduplication and topic diversity, frame-accurate cutting, CPU-friendly 9:16 subject-aware reframing, and local ffmpeg quality checks. Needs only Python 3.10+ and ffmpeg; no GPU and no API keys anywhere."
metadata:
  openclaw:
    emoji: "✂️"
    requires:
      bins: ["ffmpeg", "ffprobe", "python3"]
    install:
      - id: brew
        kind: brew
        formula: ffmpeg
        bins: ["ffmpeg", "ffprobe"]
        label: "Install ffmpeg (brew)"
      - id: apt
        kind: apt
        package: ffmpeg
        bins: ["ffmpeg", "ffprobe"]
        label: "Install ffmpeg (apt)"
      - id: winget
        kind: winget
        package: Gyan.FFmpeg
        bins: ["ffmpeg", "ffprobe"]
        label: "Install ffmpeg (winget)"
---


# Video Clipping

Turn one long-form source into actual short-video files. Do not stop at clip recommendations.

This skill is self-contained. It needs Python 3.10+ and ffmpeg. Everything else is optional and it tells you what you are missing. See [INSTALL.md](INSTALL.md) if a command reports a missing dependency.

## Defaults

Five clips, 20–60 seconds each, 1080×1920 at 30 fps, unless the user says otherwise. All ranges are half-open `[startFrame, endFrameExclusive)`.

## Workflow

Run `preflight` first; it reports what is installed and what each missing piece would cost.

```powershell
py scripts/preflight.py --source 'C:\path\long.mp4'
```

1. **Transcribe** unless the user supplied a word-level transcript. See [transcription.md](references/transcription.md).

   ```powershell
   py scripts/transcribe.py --source 'C:\path\long.mp4' --output 'C:\path\transcript.json' --model base
   ```

   Scan the result for mis-spelled names before selecting; Whisper gets proper nouns wrong.

2. **Set up the run.** `preflight` probes the source, fingerprints it, and writes `SHORTS_REQUEST.json` as immutable run intent.

3. **Build windows**, then read [selection-workflow.md](references/selection-workflow.md) and write `analysis/candidates.raw.json` from the full transcript. Generate broadly and let `rank` do the pruning. Do not pre-delete overlapping candidates.

4. **Rank.** `analysis/ranked-shorts.json` is canonical. Never hand-edit its composite scores or suppression decisions, and never promote a rejected candidate by editing the file.

5. **Materialize.** One isolated child project per selected clip, each with a frame-accurate `source.mp4`, a rebased `transcript.json`, and a `CLIP_CONTRACT.json` binding it to the parent range.

6. **Reframe** only when a fixed centre crop would lose the subject or action. Read [reframing.md](references/reframing.md). The default tier samples a few frames per second and needs no GPU. Target selection stays a human decision when more than one person is plausibly the subject.

7. **Edit** in Remotion when a clip needs captions, overlays, or zoom cuts. Read [remotion-editing.md](references/remotion-editing.md). Skip this when a plain vertical cut is the deliverable — `materialize` already produced one.

8. **Check every clip.** Read [quality-check.md](references/quality-check.md).

   ```powershell
   py scripts/qc.py --stage source --video 'clips\01-abc\public\videos\source.mp4' --contract 'clips\01-abc\CLIP_CONTRACT.json'
   py scripts/qc.py --video 'clips\01-abc\out\clip.mp4' --contract 'clips\01-abc\CLIP_CONTRACT.json'
   ```

   Use `--stage source` on a materialized clip and the default on a final render; `materialize` cuts at native resolution, so the two have different expected dimensions.

   The script catches corrupt frames, silent or clipping audio, wrong dimensions, black frames, and cut boundaries that land mid-syllable. It cannot tell you whether the hook lands. Watch each clip too.

9. **Validate and deliver.** Report clips in rank order with source range, score, title, output path, QC verdict, and every waiver.

Read [artifact-contracts.md](references/artifact-contracts.md) when creating or validating any JSON in the run.

## Commands

```powershell
py scripts/preflight.py --source 'C:\path\long.mp4'
py scripts/transcribe.py --source 'C:\path\long.mp4' --output 'C:\path\transcript.json' --model base
py scripts/clipping_pipeline.py preflight --source 'C:\path\long.mp4' --run-root 'C:\path\shorts-run' --transcript 'C:\path\transcript.json'
py scripts/clipping_pipeline.py windows --request 'C:\path\shorts-run\SHORTS_REQUEST.json' --transcript 'C:\path\transcript.json'
py scripts/clipping_pipeline.py rank --request 'C:\path\shorts-run\SHORTS_REQUEST.json' --candidates 'C:\path\shorts-run\analysis\candidates.raw.json'
py scripts/clipping_pipeline.py materialize --run-root 'C:\path\shorts-run'
py scripts/clipping_pipeline.py validate --run-root 'C:\path\shorts-run'
```

Reframing, when a static crop will not do:

```powershell
py scripts/reframe.py preflight --source 'C:\path\clip.mp4'
py scripts/reframe.py plan --source 'C:\path\clip.mp4' --workspace 'C:\path\clip-reframe' --tier sampled
py scripts/reframe.py approve --manifest 'C:\path\clip-reframe\reframe.manifest.json' --reviewed-by 'Name'
py scripts/reframe.py publish --manifest 'C:\path\clip-reframe\reframe.manifest.json' --project 'C:\path\clips\01-abc' --id hero
```

On macOS and Linux use `python3` in place of `py`, and forward slashes.

## Rules

Resume only when the request, source, and artifact hashes still match. Never overwrite a non-matching child project without explicit replacement.

If fewer valid candidates exist than requested, render the valid set and report the shortfall. Do not quietly lower the quality thresholds to hit a number.

A failed clip keeps its rank while it is being fixed. Rank came from transcript-level scoring, and a render defect does not change how good the moment was.

State every waiver in the same sentence as the delivery claim. "Five clips delivered, clip 3 waived on loudness at −7.2 LUFS" is usable. "Five clips delivered" when a check was skipped is not.

## What Is Not Included

Deliberate gaps, so you know where the skill stops:

**Visual description.** The scoring contract accepts visual evidence — "emphatic gesture at 00:43" — but nothing here generates it. Selection works from the transcript alone, which is enough for talking-head and instructional footage. If you want visual signals, describe the footage yourself or plug in your own vision model and pass the timestamps as evidence strings.

**Caption styling.** [remotion-editing.md](references/remotion-editing.md) covers the correctness rules that break renders. It does not ship a caption design. Bring your own typography.

**Publishing.** No upload, no scheduling, no platform API. The skill delivers files.

**Hook judgement.** `qc.py` measures dimensions, decode integrity, loudness, black frames, and cut boundaries. It cannot tell whether the first two seconds earn the next thirty. Watch every clip before you post it.

**Multi-speaker diarization.** Transcription produces words with timestamps, not speaker labels. Interview footage still clips fine; you just do not get "who said it" for free.
