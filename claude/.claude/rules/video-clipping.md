# Video Clipping

Applies when turning a long-form video into short-form clips for TikTok, Instagram Reels, or YouTube Shorts.

The full skill, including all scripts, lives at `.claude/skills/video-clipping/`. This rule is the condensed always-loaded version; run the scripts from that directory.

Deliver actual short-video files. Do not stop at clip recommendations.

## Defaults

Five clips, 20–60 seconds each, 1080×1920 at 30 fps, unless the user says otherwise. All frame ranges are half-open `[startFrame, endFrameExclusive)`.

## Workflow

Run preflight first. It reports what is installed and what each missing piece would cost.

```powershell
py .claude/skills/video-clipping/scripts/preflight.py --source 'C:\path\long.mp4'
```

1. **Transcribe** unless the user supplied a word-level transcript.

   ```powershell
   py .claude/skills/video-clipping/scripts/transcribe.py --source 'C:\path\long.mp4' --output 'C:\path\transcript.json' --model base
   ```

   Scan the output for mis-spelled proper nouns before selecting. Whisper gets names wrong.

2. **Set up the run.** `clipping_pipeline.py preflight` probes and fingerprints the source and writes `SHORTS_REQUEST.json` as immutable run intent.

3. **Build windows**, then write `analysis/candidates.raw.json` from the full transcript. Generate broadly. Do not pre-delete overlapping candidates — `rank` handles suppression.

4. **Rank.** `analysis/ranked-shorts.json` is canonical. Never hand-edit its composite scores or suppression decisions. Never promote a rejected candidate by editing the file.

5. **Materialize.** One isolated child project per clip: frame-accurate `source.mp4`, rebased `transcript.json`, and a `CLIP_CONTRACT.json` binding it to the parent range.

6. **Reframe** only when a fixed centre crop would lose the subject. The default `sampled` tier needs no GPU. When two people are plausibly the subject, the choice is the user's, not yours.

7. **Edit** in Remotion only when the clip needs captions, overlays, or zoom cuts. A plain vertical cut already exists after step 5.

   ```powershell
   py .claude/skills/video-clipping/scripts/scaffold_remotion.py scaffold --project 'clips\01-abc' --caption-style word
   ```

   Writes a project that renders as-is: fps, dimensions, and duration from the contract, captions from the clip's transcript. Use `--caption-style cue` for sentence-shaped cues with no `@remotion/captions` dependency. Render one still before a full render.

8. **Check every clip.**

   ```powershell
   py .claude/skills/video-clipping/scripts/qc.py --stage source --video 'clips\01-abc\public\videos\source.mp4' --contract 'clips\01-abc\CLIP_CONTRACT.json'
   py .claude/skills/video-clipping/scripts/qc.py --video 'clips\01-abc\out\clip.mp4' --contract 'clips\01-abc\CLIP_CONTRACT.json'
   ```

   `--stage source` on a materialized clip, default on a final render. `materialize` cuts at native resolution, so the two expect different dimensions.

9. **Deliver.** Report clips in rank order with source range, score, title, output path, QC verdict, and every waiver.

## Rules

Resume only when the request, source, and artifact hashes still match. Never overwrite a non-matching child project without explicit replacement.

If fewer valid candidates exist than requested, render the valid set and report the shortfall. Never lower the quality thresholds to hit a number.

A failed clip keeps its rank while it is being fixed. Rank came from transcript-level scoring; a render defect does not change how good the moment was.

State every waiver in the same sentence as the delivery claim. "Five clips delivered, clip 3 waived on loudness at −7.2 LUFS" is usable. "Five clips delivered" with a skipped check is not.

Automated QC cannot judge whether the hook lands. Watch each clip before delivering it.

## Detail

@.claude/skills/video-clipping/references/selection-workflow.md
@.claude/skills/video-clipping/references/artifact-contracts.md
@.claude/skills/video-clipping/references/reframing.md
@.claude/skills/video-clipping/references/quality-check.md
@.claude/skills/video-clipping/references/transcription.md
@.claude/skills/video-clipping/references/remotion-editing.md
