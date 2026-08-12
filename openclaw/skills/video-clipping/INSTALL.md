# Install

Two things are required: Python 3.10 or newer, and ffmpeg. Everything else is optional and the skill tells you what a missing piece would cost.

Check where you stand:

```powershell
py scripts/preflight.py
```

That prints which parts of the workflow work now, what each missing package unlocks, and the command to install it. Run it again after installing anything.

## Required

**Python 3.10+**

```powershell
py --version          # Windows
python3 --version     # macOS, Linux
```

**ffmpeg and ffprobe**, both on `PATH`.

```powershell
choco install ffmpeg              # Windows, Chocolatey
winget install Gyan.FFmpeg        # Windows, winget
brew install ffmpeg               # macOS
sudo apt install ffmpeg           # Debian, Ubuntu
```

Verify: `ffmpeg -version` and `ffprobe -version`.

With just these two you can rank candidates from a supplied transcript, cut frame-accurate clips, apply a fixed centre crop, and run quality checks.

## Transcription

Needed unless you bring your own word-level transcript. Pick one backend.

```powershell
pip install faster-whisper
```

Recommended. CTranslate2 build, fast on CPU, no PyTorch. The first run downloads the model (about 150 MB for `base`).

```powershell
pip install openai-whisper
```

Alternative. Pulls in PyTorch, which is a much larger download. Use it if `faster-whisper` will not build on your platform.

Neither needs a GPU. Full detail in [transcription.md](references/transcription.md).

## Subject-aware reframing

Only needed when a horizontal source has to become 9:16 and a fixed centre crop would cut the subject out.

```powershell
pip install opencv-python
```

This is the important one. It enables the default `sampled` tier and brings two detectors with it — HOG for full bodies and Haar for frontal faces — with no model download and no GPU.

```powershell
pip install ultralytics
```

Optional on top. Adds the YOLO detector, which is the most accurate of the three: 0.85 mean confidence against Haar's 0.60 and HOG's 0.17 on our 4K test clip. First run downloads weights (about 6 MB for `yolov8n.pt`).

Full detail in [reframing.md](references/reframing.md).

## GPU

Not required anywhere. The default reframing tier samples a few frames per second instead of processing every frame, which is why a weak GPU does not hold this skill back.

Measured on a 4K clip, 320 frames: the per-frame `tracked` tier took 143 s on CPU and 139 s on an RTX 3060. Decoding dominates, not inference. A GPU buys you almost nothing here.

If you do have CUDA and PyTorch installed, pass `--device cuda` to `transcribe.py` or `--device 0` to `reframe.py plan`.

## Remotion

Only needed for captions, overlays, or zoom cuts. Skip it for plain vertical cuts — `materialize` already produces those.

Needs Node.js 18+. The scaffold writes the project and its `package.json`, so `npm install` pulls what it needs:

```powershell
py scripts/scaffold_remotion.py scaffold --project 'clips\01-your-clip' --caption-style word
cd 'clips\01-your-clip'
npm install
npx tsc --noEmit
npm run render
```

`--caption-style cue` skips the `@remotion/captions` dependency and generates sentence-shaped cues from the transcript instead.

Full detail in [remotion-editing.md](references/remotion-editing.md).

## Everything at once

```powershell
pip install faster-whisper opencv-python ultralytics
```

Plus ffmpeg from the list above.

## Tests

```powershell
py -m pytest tests -q
```

Tests that need ffmpeg skip themselves when it is absent, so a partial install still gives a useful result.

## Troubleshooting

**`ffprobe` not found** — ffmpeg is installed but not on `PATH`. Open a new shell after installing; the installer does not update the current one.

**`No Whisper backend found`** — install `faster-whisper`, or pass a pre-made transcript to `clipping_pipeline.py preflight --transcript`.

**`No detector available`** — install `opencv-python`, or use `--tier static` with `--center-x` and `--center-y`.

**Transcript rejected as non-monotonic** — a word starts before the previous one ends. `transcribe.py` corrects this automatically; a hand-built transcript may need the same. The error names the exact word index.

**`ENOENT` during a Remotion render on Windows** — set `TEMP` and `TMP` in the same command that launches the render. See [remotion-editing.md](references/remotion-editing.md).

**Reframe crop looks wrong** — check `confidence` in the manifest before blaming the tier. Low confidence means the detector never found the subject and the crop fell back to centre. Try `--detector yolo`, or use `--tier static` with a hand-picked centre.
