# Editing a Clip in Remotion

Optional. `materialize` already produces a frame-accurate vertical-ready clip with audio, and `reframe.py render` can bake a crop straight to a file. Reach for Remotion when a clip needs captions, overlays, or zoom cuts.

Each materialized clip is a self-contained project. Never share one Remotion project across clips — a shared project means one clip's assets and composition length leak into another.

## Project layout

Keep everything under the clip directory `materialize` created:

```
clips/01-a1b2c3d4e5f6/
  CLIP_CONTRACT.json        <- immutable provenance; never edit
  public/
    videos/source.mp4       <- frame-zero edit source
    transcript.json         <- word timestamps, rebased to this clip
    reframing/<id>/         <- approved crop manifest, when used
  remotion/
    package.json
    src/
  out/
```

Paths stay project-relative so the folder can be zipped or moved without breaking.

## Set up

```powershell
cd 'clips\01-a1b2c3d4e5f6'
npx create-video@latest remotion --blank
cd remotion
npx remotion add @remotion/captions
```

Read `CLIP_CONTRACT.json` and take these as fixed:

- `timeline.fps`, `timeline.width`, `timeline.height` — composition settings.
- `timeline.endFrameExclusive` — `durationInFrames`. Do not extend past the materialized source.
- `selection.suggestedTitle` and `selection.hookText` — starting copy for an opening caption.

Frame 0 of `public/videos/source.mp4` is frame 0 of the timeline. Parent-source frames in `source.sourceRange` are provenance only; never use them as composition frames.

## Captions

`public/transcript.json` is already rebased so its first word starts near 0. Convert to the `@remotion/captions` shape and page it:

```tsx
import { createTikTokStyleCaptions } from "@remotion/captions";

const captions = words.map((word) => ({
  text: word.word,
  startMs: word.start * 1000,
  endMs: word.end * 1000,
  timestampMs: (word.start + word.end) / 2 * 1000,
  confidence: null,
}));

const { pages } = createTikTokStyleCaptions({
  captions,
  combineTokensWithinMilliseconds: 1200,
});
```

Sync any overlay to a spoken word the same way every time:

```
absoluteStartFrame = Math.round(word.start * fps)
```

Inside `<Sequence from={X}>`, subtract `X`. Use half-open ranges: `[125, 200)` holds frames 125 through 199, a duration of 75.

## Consuming a reframe manifest

Preconditions, checked before rendering. Fail closed; do not fall back to a centre crop.

- `status` is `ready` and `review.decision` is `approved`.
- Manifest `source.sha256`, dimensions, fps, and frame count match the project-local file.
- `validation.passed` is true and `uncertainty.unresolvedRanges` is empty.
- Every crop shares the aspect ratio of `output.width / output.height`.

Load and index once, never per frame:

```tsx
const record = framesBySourceFrame.get(sourceFrame);
if (!record) throw new Error(`Missing crop for source frame ${sourceFrame}`);

const scaleX = width / record.crop.width;
const scaleY = height / record.crop.height;
if (Math.abs(scaleX - scaleY) > 0.001) {
  throw new Error("Crop aspect does not match the canvas");
}

const style: React.CSSProperties = {
  position: "absolute",
  width: sourceWidth,
  height: sourceHeight,
  transformOrigin: "top left",
  transform: `translate(${-record.crop.x * scaleX}px, ${-record.crop.y * scaleY}px) scale(${scaleX})`,
};
```

Use the manifest record for every frame. Do not interpolate between records or across shot boundaries — smoothing is already baked in, and the reset at each cut is intentional. Do not render the review boxes or warning labels from the preview.

## Production rules

Learned from broken renders. Each one prevents a specific failure.

**Audio**
1. Every `<Audio>` gets `showInTimeline={false}`, or Remotion Studio throws waveform errors.
2. Keep audio outside visual `<Sequence>` blocks; give it one dedicated timeline.
3. Never let program audio play twice. Mute the source video when voiceover comes from a separate `<Audio>`.

**Overlays**
4. No `boxShadow` on a transparent PNG. Use `filter: drop-shadow()` so the alpha is respected.
5. Use `<AbsoluteFill>` with flexbox to centre. `left: 50%` breaks at other resolutions.
6. Hide or move captions whenever an overlay occupies the caption band. Merge blackout windows that sit a frame or two apart, or you get a visible flash.
7. Inspect the actual crop for face and hand no-go zones. Vertical talking-head footage usually blocks the upper middle; demonstration footage does not.

**Rendering**
8. Run `npx tsc --noEmit` before previewing. It catches unused imports and type errors that surface as confusing render failures.
9. Check free temp space first. Budget 25 GB, or 40–50 GB when the source is over 1 GB, per concurrent render.
10. On Windows with the project on a non-system drive, set `TEMP`/`TMP` in the same command that launches Remotion. The assignment does not persist between shells, and Node fails with `ENOENT` rather than creating the directory:

```powershell
$env:TEMP='D:\remotion-temp'; $env:TMP='D:\remotion-temp'; npx remotion render MyComp out/video.mp4
```

11. One render at a time per project. Each invocation stages its own full copy of the sources. Clean your own temp root after a failed render before retrying, and never sweep a shared temp directory by name pattern.

## Rendering

```powershell
npx remotion render MyComp out/clip.mp4 --codec=h264
```

Add `--concurrency=1` when a render is non-deterministic or the media path demands single-threaded output.
