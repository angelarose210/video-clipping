# Reframing to Vertical

A 16:9 source cropped to 9:16 loses about 60% of the frame width. If the speaker sits in the middle and stays there, a fixed centre crop is correct and costs nothing. Reach for subject detection only when a fixed crop would cut the subject or the action out of frame.

All commands come from `scripts/reframe.py`. Ranges are half-open `[startFrame, endFrameExclusive)`.

## Pick a tier

Run this first. It reports what is installed and recommends a tier:

```powershell
py scripts/reframe.py preflight --source 'C:\path\clip.mp4'
```

| Tier | What it does | Needs | Speed |
|------|--------------|-------|-------|
| `static` | One fixed crop for the whole clip | ffmpeg only | Instant |
| `sampled` | **Default.** Detects the subject on sampled frames, interpolates between them, smooths the result | opencv-python | Seconds |
| `tracked` | Detects on every frame | opencv-python | Minutes, and a GPU barely helps |
| `external` | Validates a manifest another tool produced | — | Instant |

Measured on the same 4K clip, 320 frames, on a machine with an RTX 3060:

| Tier | Detections | Time |
|------|-----------|------|
| `sampled` | 12 | 13–19 s, whichever detector |
| `tracked` CPU | 320 | 143 s |
| `tracked` CUDA | 320 | 139 s |

The GPU saved 4 seconds out of 143. Decoding 320 frames of 4K dominates the cost, not inference, so a GPU does not rescue the tracked tier — using fewer frames does. That is why `sampled` is the default and why a weak GPU is not a reason to avoid this skill.

The two tiers also agree. Across those 320 frames the sampled and tracked crop paths differed by 4.3 px on average and 21 px at worst, on a 1215 px-wide crop — 0.36% mean, 1.75% peak. You are paying 8× the time for a difference you cannot see.

Move up to `tracked` only when the subject changes direction faster than your sample interval: fast sport, handheld whip pans, quick cuts between speakers. Try halving `--sample-seconds` first.

The sampled tier is the reason this skill does not need a GPU. Subjects in talking-head and demonstration footage move over hundreds of milliseconds, not single frames, so two detections a second describe the motion and interpolation fills the rest. Detecting all 30 frames of a second re-answers a question that has barely changed.

## Pick a detector

`--detector auto` walks this list and takes the first that loads. Name one explicitly to pin behaviour.

| Detector | Install | Best for |
|----------|---------|----------|
| `yolo` | `pip install ultralytics` | Anything. Highest confidence, needs a one-time weights download |
| `hog` | bundled with opencv-python | Full body visible, plain background |
| `haar` | bundled with opencv-python | Frontal talking heads. Expands the face box into a torso box |
| `center` | always | No subject found. Reports confidence 0 so the manifest flags itself |

On the same 4K test clip: YOLO averaged 0.85 confidence, Haar 0.60, HOG 0.17. HOG's weak result correctly landed the manifest in `uncertain` rather than passing a bad crop through. Trust the confidence over the tier name.

## Run it

Default path, no GPU, no model download:

```powershell
py scripts/reframe.py plan --source 'C:\path\clip.mp4' --workspace 'C:\path\clip-reframe' `
  --tier sampled --detector auto --output-width 1080 --output-height 1920
```

Fixed crop when the subject does not move:

```powershell
py scripts/reframe.py plan --source 'C:\path\clip.mp4' --workspace 'C:\path\clip-reframe' `
  --tier static --center-x 960 --center-y 540
```

Useful flags:

- `--sample-seconds 0.5` — seconds between detections. Lower it for faster motion, raise it for a locked-off shot.
- `--sample-every 15` — same idea in frames; overrides `--sample-seconds`.
- `--zoom --max-zoom 1.25` — let the crop push in when the subject is small in frame.
- `--smooth-seconds 0.35` and `--dead-zone 45` — how much drift is ignored before the crop moves. Raise the dead zone to stop small fidgets from moving the frame.
- `--no-scene-detect` — skip cut detection on single-shot footage.
- `--device 0` — send YOLO to CUDA device 0 when a GPU exists.

`plan` writes `reframe.manifest.json`, a `preview.mp4` with the crop burned in, and annotated stills under `stills/`.

## The approval gate

`plan` never produces a `ready` manifest. This is deliberate: an automatic crop that quietly cuts off a head is worse than one that stops and asks.

```powershell
# Only when the manifest reports unresolved low-confidence ranges
py scripts/reframe.py accept-uncertainty --manifest '<workspace>\reframe.manifest.json' `
  --reviewed-by 'Your name' --note 'Watched preview; subject stays in frame'

# After actually watching preview.mp4
py scripts/reframe.py approve --manifest '<workspace>\reframe.manifest.json' `
  --reviewed-by 'Your name' --note 'Crop approved'
```

`approve` refuses and lists blockers when validation failed, uncertainty is unresolved, or the source file changed since planning. Statuses are `review-pending`, `uncertain`, `failed`, and `ready`.

Never click through the target choice yourself when two plausible people are visible. That is a human decision, and a wrong guess reframes the whole clip onto the wrong person.

## Deliver the crop

Into a Remotion project:

```powershell
py scripts/reframe.py publish --manifest '<workspace>\reframe.manifest.json' `
  --project 'C:\path\clips\01-abc123' --id hero
```

This copies the source to `public/videos/` and the manifest to `public/reframing/<id>/`. Consume it per the rules in [remotion-editing.md](remotion-editing.md).

Straight to a baked file, skipping Remotion:

```powershell
py scripts/reframe.py render --manifest '<workspace>\reframe.manifest.json' --output 'C:\path\vertical.mp4'
```

`render` drives ffmpeg's `sendcmd` with one crop command per frame and keeps the audio. Verified output: 1080×1920, frame count preserved, audio intact. Use it when the clip needs no captions or overlays.

## Importing another tool's manifest

```powershell
py scripts/reframe.py import --manifest '<path>\reframe.manifest.json'
```

The manifest needs `source.path`, `output.width`, `output.height`, and a continuous `frames` array of `{frame, crop:{x,y,width,height}}` starting at frame 0. `import` re-probes the source and revalidates every crop; it does not trust the incoming status.

## What gets validated

`plan` and `import` both check that frame records are continuous and start at 0, that the count matches the probed frame count, that every crop is positive and inside the source bounds, and that every crop matches the output aspect ratio within 0.01. Any failure sets `status: failed` and blocks approval.
