# Changelog

## 1.0.0 - first public release

Runs with Python 3.10+ and ffmpeg. Nothing else is required, no GPU is needed
anywhere, and no step calls an external service.

### Included

- `scripts/clipping_pipeline.py` - run setup, semantic windows, deterministic
  ranking with temporal suppression and topic diversity, frame-accurate
  materialization into isolated child projects, and run validation.
- `scripts/transcribe.py` - word-level Whisper transcription. Supports
  `faster-whisper` (CPU-friendly, no PyTorch) and `openai-whisper`, picks
  whichever is installed, and forces word timestamps to advance monotonically so
  downstream frame maths cannot go backwards.
- `scripts/reframe.py` - 9:16 subject framing with four tiers: `static`,
  `sampled` (default), `tracked`, and `external`. The detector chain falls back
  YOLO -> OpenCV HOG -> OpenCV Haar -> frame centre, so it degrades instead of
  failing, and reports the confidence it actually achieved.
- `scripts/qc.py` - local quality checks using only ffmpeg and ffprobe. Covers
  spec, decode integrity, audio presence and level, loudness and true peak,
  black and frozen frames, and cut boundaries.
- `scripts/preflight.py` - reports what is installed, what is missing, and what
  each missing piece would unlock.
- `references/` - selection workflow, artifact contracts, reframing,
  transcription, Remotion editing rules, and quality checking.
- `tests/` - 71 tests covering ranking, materialization, contracts, reframing,
  QC, and transcription. One skips when no transcription backend is installed;
  the ffmpeg-dependent tests skip themselves when ffmpeg is absent.

### Design decisions worth knowing

**Sampled detection is the default, not per-frame tracking.** The GPU path saved
4 seconds out of 143 on a 4K test clip, because decoding dominates inference,
and its crop path differed from the sampled path by a mean of 4.3 px (0.36%).
Per-frame tracking is still available as `--tier tracked`. It is just no longer
the default, and a weak GPU is not a reason to avoid this skill.

**`CLIP_CONTRACT.json` carries `timeline.materialized` alongside
`timeline.width`/`height`.** `materialize` cuts at native resolution rather than
upscaling, so the cut file and the delivery target legitimately differ. QC takes
`--stage source|final` to check against the right one. Without this split, QC
reports a dimension failure on every clip whose source is not already at the
target resolution.

**Boundary checking is relative, not absolute.** Each cut is compared against
the clip's own mean level with 8 dB of headroom. Two clean cuts in the same test
source measured -44 dB and -24 dB, so no fixed threshold can separate quiet
speech from a loud room.
