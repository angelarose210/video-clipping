# Changelog

## 1.1.1

### Fixed

Two word-style caption defects, both found by reading a rendered still rather
than by a test:

- A caption page was capped at `SWITCH_MS`, the grouping window. That constant
  controls which words land on the same page, not how long a page may stay up,
  so a four-word page spanning more than one window blanked while its own words
  were still being spoken. A page now ends at its last spoken token plus a short
  tail.
- The active-word highlight keyed off `page.startMs`, which stops matching once
  the hook gate clamps a page to start later than it was spoken. The highlight
  ran behind by exactly the amount the gate held it back -- sitting on word 6
  while word 9 was audible. It now derives from the Sequence's real start frame.

Both are covered by tests that assert on the generated template, so neither can
return silently.

## 1.1.0

### Added

- `scripts/scaffold_remotion.py` - writes a Remotion project into a materialized
  clip that renders on the first try. Composition settings come from
  `CLIP_CONTRACT.json`; captions come from the clip's own rebased transcript.
  Two caption styles, both verified end to end at 1080x1920: `word` uses
  `@remotion/captions` for a per-word highlight, and `cue` generates
  sentence-shaped cues into `src/cues.json` with no extra dependency. The `cues`
  subcommand regenerates them after a transcript edit.
- 26 tests covering cue grouping, template substitution, and the CLI.

### Changed

- `references/remotion-editing.md` had the project layout wrong. It showed
  Remotion nested in a `remotion/` subdirectory, which puts `public/` out of
  `staticFile()`'s reach and fails every asset path. The scaffold and the
  document now both put `package.json` and `src/` at the clip root, beside
  `public/`.
- Three production rules added, each from a defect caught in a real render: a
  missing `fontFamily` silently falls back to a serif; a hook card repeating the
  opening spoken line prints those words twice; a card and the first caption page
  overlap unless captions are gated behind it.

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
