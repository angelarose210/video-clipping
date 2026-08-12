# Quality Check

Check every rendered clip before delivering it. The checks below need only ffmpeg, ffprobe, and the clip's own artifacts — no API key, no upload, no external service.

Run `scripts/qc.py` for the mechanical checks, then watch the clip. The script catches corruption, silence, loudness problems, and spec mismatches. It cannot tell you whether the hook lands or a caption covers a face.

## Run it

On a final render, against the contract's delivery dimensions:

```powershell
py scripts/qc.py --video 'clips\01-abc\out\clip.mp4' --contract 'clips\01-abc\CLIP_CONTRACT.json'
```

On a freshly materialized clip, before any editing:

```powershell
py scripts/qc.py --stage source --video 'clips\01-abc\public\videos\source.mp4' --contract 'clips\01-abc\CLIP_CONTRACT.json'
```

`--stage` matters. `materialize` cuts the clip at its native resolution rather than upscaling it, so a 1152×2048 source stays 1152×2048 while the contract's `timeline` records the 1080×1920 delivery target. `--stage source` checks against `timeline.materialized`; the default `final` checks against the delivery dimensions. Using the wrong one reports a dimension failure that is not real.

Without a contract:

```powershell
py scripts/qc.py --video 'C:\path\clip.mp4' --expect-width 1080 --expect-height 1920 --expect-fps 30
```

Exit code is 0 when every check passes, 1 on any failure. Findings are `pass`, `warn`, or `fail` with the measured value alongside the expected one.

## What the script checks

**Spec** — width, height, fps, duration, and codecs against the contract or the `--expect-*` flags. A vertical delivery that renders 1920×1080 is the single most common mistake and the cheapest to catch.

**Decode integrity** — decodes every video and audio frame and counts errors. Catches truncated files and corrupt frames that a metadata probe reports as healthy.

**Audio presence** — fails when the audio stream is missing entirely, and fails when it decodes to near-silence. A silent short is a wasted upload.

**Loudness** — measures integrated LUFS and true peak with `loudnorm`. Warns outside −16 to −8 LUFS, which covers what TikTok, Reels, and Shorts normalise toward. Fails when true peak exceeds −0.1 dBTP, which clips on playback.

**Black and frozen frames** — reports black frames via `blackdetect` and frozen stretches via `freezedetect`. A black frame at the head is dead air where the hook should be.

**Cut boundaries** — measures the first and last 0.15 s and compares them against the clip's own mean level. A boundary sitting at speech level warns; one at least 8 dB below it passes as room tone.

The comparison is relative on purpose. An absolute dB threshold cannot separate quiet speech from a loud room — two clean cuts in the same test source measured −44 dB and −24 dB, so any fixed number either misses one or warns on the other.

This check earns its place. On a real test clip it warned on the head boundary, and the transcript confirmed a 20 ms handle before the first word where [selection-workflow.md](selection-workflow.md) asks for 100–160 ms. It is the one check that catches a clipped syllable before a viewer hears it.

## Watch it anyway

Automated checks pass a clip that opens on a half-finished sentence. Before delivering, watch each clip start to finish once and confirm:

1. The first two seconds give a reason to keep watching.
2. No sentence is clipped at either end. Listen specifically at the boundaries.
3. Captions match the spoken words and sit clear of the face and hands.
4. The payoff actually lands inside the clip. A clip that sets up a question and cuts before the answer will underperform no matter how clean the render.
5. Nothing important sits outside the vertical crop.
6. Audio holds a consistent level; no jarring jump at any join.

## When a clip fails

Fix and rerender, then rerun the check. Keep the clip's rank while it is being fixed — rank came from the transcript-level selection scores and a render defect does not change how good the moment is.

Report waivers explicitly. "QC passed except loudness, waived at −7.2 LUFS" is a usable delivery note. "QC passed" when a check was skipped is not.
