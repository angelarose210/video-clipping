#!/usr/bin/env python3
"""Scaffold a working Remotion project inside a materialized clip.

Reads CLIP_CONTRACT.json and writes a project that renders on the first try:
correct fps, dimensions, and duration, with captions driven by the clip's own
rebased transcript.

Two caption styles:

  word  Per-word highlight via @remotion/captions. Suits fast talking-head
        footage where the moving highlight carries the energy.

  cue   Sentence-shaped cues generated from the transcript, no extra
        dependency. Suits instructional pacing where a whole clause needs to
        sit still long enough to read.

The scaffold is deliberately unbranded. It gives you a composition that runs;
typography, colour, and end cards are yours.

Writes JSON on stdout. Exit code 0 on success, 1 on any error.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from pathlib import Path
from typing import Any

# Pinned so a scaffold generated today still installs in six months. Bump
# deliberately, not incidentally.
REMOTION_VERSION = "4.0.489"
REACT_VERSION = "19.1.0"

# Cue grouping. A cue longer than MAX_CHARS wraps to a third line at the font
# sizes below, and one shorter than MIN_FRAMES flashes past unread.
MAX_CHARS = 62
PAUSE_BREAK_SECONDS = 0.35
MAX_CUE_SECONDS = 4.0
MIN_CUE_FRAMES = 8
SENTENCE_END = re.compile(r"[.!?]['\"\u201d\u2019)]*$")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def fill(template: str, **values: Any) -> str:
    """Substitute __TOKEN__ placeholders.

    Deliberately not str.format: JSX and CSS are full of braces, and escaping
    every one of them is how a template acquires a bug you only see at render.
    """
    for key, value in values.items():
        template = template.replace(f"__{key}__", str(value))
    if "__" in re.sub(r"__[a-z]", "", template):
        leftover = re.findall(r"__[A-Z_]+__", template)
        if leftover:
            raise ValueError(f"unsubstituted placeholders: {sorted(set(leftover))}")
    return template


def group_cues(words: list[dict[str, Any]], fps: float, total_frames: int) -> list[dict[str, Any]]:
    """Group word timings into readable cues, in clip-local frames.

    Break priority: sentence-final punctuation, then a pause, then length. That
    order keeps cues aligned to how someone actually speaks. Splitting purely on
    length puts breaks mid-clause, which reads worse than a slightly long cue.
    """
    cues: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def flush() -> None:
        if not current:
            return
        text = " ".join(item["word"].strip() for item in current if item["word"].strip())
        if not text:
            current.clear()
            return
        start = int(round(float(current[0]["start"]) * fps))
        end = int(round(float(current[-1]["end"]) * fps))
        cues.append({"start": max(0, start), "end": min(total_frames, end), "text": text})
        current.clear()

    for index, word in enumerate(words):
        current.append(word)
        text_so_far = " ".join(item["word"].strip() for item in current)
        following = words[index + 1] if index + 1 < len(words) else None
        gap = float(following["start"]) - float(word["end"]) if following else 0.0
        span = float(word["end"]) - float(current[0]["start"])

        if SENTENCE_END.search(word["word"].strip()):
            flush()
        elif following is not None and gap >= PAUSE_BREAK_SECONDS:
            flush()
        elif len(text_so_far) >= MAX_CHARS or span >= MAX_CUE_SECONDS:
            flush()
    flush()

    # A cue that ends before the next begins reads as a gap; extend each to its
    # neighbour so captions do not blink between clauses. Half-open ranges.
    cleaned: list[dict[str, Any]] = []
    for index, cue in enumerate(cues):
        if cue["start"] >= total_frames:
            # Wholly past the composition. A cue here would extend the render or
            # be clipped to zero duration; dropping it is the honest outcome.
            continue
        following = cues[index + 1] if index + 1 < len(cues) else None
        end = following["start"] if following else cue["end"] + int(round(0.4 * fps))
        # The composition bound wins over the minimum duration. Applying the
        # minimum last lets a late cue extend past the last frame.
        cue["end"] = min(max(cue["start"] + MIN_CUE_FRAMES, end), total_frames)
        if cue["end"] > cue["start"]:
            cleaned.append(cue)
    return cleaned


PACKAGE_JSON_WORD = """{
  "name": "__SLUG__",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "studio": "remotion studio src/index.ts",
    "typecheck": "tsc --noEmit",
    "still": "remotion still src/index.ts __COMPOSITION__ out/still.png --frame=__MID_FRAME__ --overwrite",
    "render": "remotion render src/index.ts __COMPOSITION__ out/clip.mp4 --codec=h264 --concurrency=1 --overwrite"
  },
  "dependencies": {
    "@remotion/captions": "__REMOTION__",
    "@remotion/cli": "__REMOTION__",
    "@remotion/media": "__REMOTION__",
    "react": "__REACT__",
    "react-dom": "__REACT__",
    "remotion": "__REMOTION__"
  },
  "devDependencies": {
    "@types/react": "19.1.8",
    "@types/react-dom": "19.1.6",
    "typescript": "5.8.3"
  }
}
"""

PACKAGE_JSON_CUE = """{
  "name": "__SLUG__",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "studio": "remotion studio src/index.ts",
    "typecheck": "tsc --noEmit",
    "still": "remotion still src/index.ts __COMPOSITION__ out/still.png --frame=__MID_FRAME__ --overwrite",
    "render": "remotion render src/index.ts __COMPOSITION__ out/clip.mp4 --codec=h264 --concurrency=1 --overwrite"
  },
  "dependencies": {
    "@remotion/cli": "__REMOTION__",
    "@remotion/media": "__REMOTION__",
    "react": "__REACT__",
    "react-dom": "__REACT__",
    "remotion": "__REMOTION__"
  },
  "devDependencies": {
    "@types/react": "19.1.8",
    "@types/react-dom": "19.1.6",
    "typescript": "5.8.3"
  }
}
"""

TSCONFIG = """{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["DOM", "DOM.Iterable", "ES2022"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "resolveJsonModule": true,
    "esModuleInterop": true,
    "allowSyntheticDefaultImports": true,
    "strict": true,
    "forceConsistentCasingInFileNames": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "skipLibCheck": true
  },
  "include": ["src", "remotion.config.ts"]
}
"""

REMOTION_CONFIG = """import {Config} from '@remotion/cli/config';

// One render at a time. Each invocation stages its own copy of the sources, so
// concurrent renders on one project fight over temp space.
Config.setConcurrency(1);
Config.setOverwriteOutput(true);
Config.setCodec('h264');
Config.setPixelFormat('yuv420p');
"""

INDEX_TS = """import {registerRoot} from 'remotion';
import {Root} from './Root';

registerRoot(Root);
"""

ROOT_TSX = """import {Composition} from 'remotion';
import {Short} from './Short';
import {CLIP} from './config';

export const Root = () => (
  <Composition
    id={CLIP.id}
    component={Short}
    durationInFrames={CLIP.durationInFrames}
    fps={CLIP.fps}
    width={CLIP.width}
    height={CLIP.height}
  />
);
"""

CONFIG_TS = """/**
 * Generated from CLIP_CONTRACT.json. The dimensions, fps, and duration are the
 * delivery target and the materialized source's frame count -- changing them
 * desynchronises captions from speech.
 *
 * hook starts from the selection's suggested title. Rewrite it. hookSub is
 * empty on purpose: the spoken opening line is already about to appear in the
 * captions, so printing it here says the same thing twice.
 *
 * Captions begin at hookHoldFrames, so the card and the first words never
 * share the screen. Set hookHoldFrames to 0 to drop the card and start
 * captions immediately.
 */
export const CLIP = {
  id: '__COMPOSITION__',
  fps: __FPS__,
  width: __WIDTH__,
  height: __HEIGHT__,
  durationInFrames: __DURATION__,

  // Frames trimmed off the head of the materialized source. Raise this to drop
  // an inherited sign-off or a slow start; captions shift with it automatically.
  sourceTrimFrames: 0,

  // Opening hook card. Set holdFrames to 0 to remove it.
  hook: __HOOK__,
  hookSub: __HOOK_SUB__,
  hookHoldFrames: __HOOK_FRAMES__,
} as const;
"""

CAPTIONS_WORD_TSX = """import {createTikTokStyleCaptions, type Caption, type TikTokPage} from '@remotion/captions';
import {Sequence, useCurrentFrame, useVideoConfig} from 'remotion';
import transcript from '../public/transcript.json';
import {CLIP} from './config';

// How long one page of words stays up before the next replaces it.
const SWITCH_MS = 1150;

type Word = {word: string; start: number; end: number};

const captions: Caption[] = (transcript as Word[]).map((item, index) => ({
  text: `${index === 0 ? '' : ' '}${item.word}`,
  startMs: item.start * 1000,
  endMs: item.end * 1000,
  timestampMs: item.start * 1000,
  confidence: null,
}));

const {pages} = createTikTokStyleCaptions({
  captions,
  combineTokensWithinMilliseconds: SWITCH_MS,
});

const CaptionPage = ({page}: {page: TikTokPage}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  // Sequence-local frame back to absolute transcript time.
  const absoluteMs = page.startMs + (frame / fps) * 1000;

  return (
    <div
      style={{
        position: 'absolute',
        left: 54,
        right: 54,
        bottom: __CAPTION_BOTTOM__,
        display: 'flex',
        justifyContent: 'center',
        textAlign: 'center',
        fontSize: __CAPTION_SIZE__,
        lineHeight: 1.08,
        fontWeight: 800,
        textTransform: 'uppercase',
        color: '#fff',
        WebkitTextStroke: '2px rgba(0,0,0,0.85)',
        textShadow: '0 5px 18px #000, 0 2px 4px #000',
        whiteSpace: 'pre-wrap',
      }}
    >
      <div>
        {page.tokens.map((token) => {
          const active = token.fromMs <= absoluteMs && token.toMs > absoluteMs;
          return (
            <span
              key={`${token.fromMs}-${token.toMs}`}
              style={{color: active ? HIGHLIGHT : '#fff'}}
            >
              {token.text}
            </span>
          );
        })}
      </div>
    </div>
  );
};

// The one colour worth changing first.
const HIGHLIGHT = '#FFE066';

export const CaptionTrack = ({
  hideFromFrame,
  showFromFrame = 0,
}: {
  hideFromFrame: number;
  showFromFrame?: number;
}) => {
  const {fps} = useVideoConfig();
  return (
    <>
      {pages.map((page, index) => {
        const next = pages[index + 1];
        const start = Math.round((page.startMs / 1000) * fps) - CLIP.sourceTrimFrames;
        const naturalEnd = next
          ? Math.round((next.startMs / 1000) * fps) - CLIP.sourceTrimFrames
          : hideFromFrame;
        // Half-open: [start, end). Cap each page so a long trailing pause does
        // not leave the last words on screen.
        const end = Math.min(hideFromFrame, naturalEnd, start + Math.round((SWITCH_MS / 1000) * fps));
        // Clamp to the gate so a page that began under the hook card resumes
        // when the card clears instead of being dropped.
        const visibleStart = Math.max(start, showFromFrame);
        if (end <= visibleStart) return null;
        return (
          <Sequence
            key={`${page.startMs}-${index}`}
            from={visibleStart}
            durationInFrames={end - visibleStart}
            premountFor={Math.round(0.5 * fps)}
          >
            <CaptionPage page={page} />
          </Sequence>
        );
      })}
    </>
  );
};
"""

CAPTIONS_CUE_TSX = """import {Sequence, useVideoConfig} from 'remotion';
import cues from './cues.json';
import {CLIP} from './config';

/**
 * Cues were generated from public/transcript.json by
 * `scaffold_remotion.py cues`, breaking on sentence-final punctuation first,
 * then pauses, then length. Frames are clip-local and half-open: [start, end).
 *
 * Edit cues.json by hand when a break reads badly. Re-running the generator
 * overwrites it, so keep hand edits or re-apply them.
 */
type Cue = {start: number; end: number; text: string};

const CaptionCue = ({text}: {text: string}) => (
  <div
    style={{
      position: 'absolute',
      left: 64,
      right: 64,
      bottom: __CAPTION_BOTTOM__,
      textAlign: 'center',
      fontSize: __CUE_SIZE__,
      lineHeight: 1.18,
      fontWeight: 700,
      color: '#fff',
      textShadow: '0 4px 16px #000, 0 2px 3px #000',
      textWrap: 'balance',
    }}
  >
    {text}
  </div>
);

export const CaptionTrack = ({
  hideFromFrame,
  showFromFrame = 0,
}: {
  hideFromFrame: number;
  showFromFrame?: number;
}) => {
  const {fps} = useVideoConfig();
  return (
    <>
      {(cues as Cue[]).map((cue, index) => {
        const start = Math.max(cue.start - CLIP.sourceTrimFrames, showFromFrame);
        const end = Math.min(cue.end - CLIP.sourceTrimFrames, hideFromFrame);
        if (end <= start) return null;
        return (
          <Sequence
            key={`${cue.start}-${index}`}
            from={start}
            durationInFrames={end - start}
            premountFor={Math.round(0.5 * fps)}
          >
            <CaptionCue text={cue.text} />
          </Sequence>
        );
      })}
    </>
  );
};
"""

SHORT_TSX = """import {Video} from '@remotion/media';
import {
  AbsoluteFill,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import {CaptionTrack} from './Captions';
import {CLIP} from './config';

/**
 * The composition renders at CLIP.width x CLIP.height. The materialized source
 * keeps its native resolution, so objectFit: 'cover' does the scaling and the
 * crop -- a horizontal source will be centre-cropped here. When that loses the
 * subject, plan a crop with reframe.py and consume the manifest instead.
 */

const Hook = () => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const enter = spring({frame, fps, config: {damping: 18, stiffness: 220, mass: 0.75}});
  const opacity = interpolate(
    frame,
    [0, 8, CLIP.hookHoldFrames - 12, CLIP.hookHoldFrames],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  return (
    <div
      style={{
        position: 'absolute',
        top: __HOOK_TOP__,
        left: 48,
        right: 48,
        opacity,
        transform: `scale(${0.92 + enter * 0.08})`,
        transformOrigin: 'top center',
        textAlign: 'center',
      }}
    >
      <div
        style={{
          fontSize: __HOOK_SIZE__,
          lineHeight: 0.95,
          fontWeight: 800,
          textTransform: 'uppercase',
          color: '#fff',
          textShadow: '0 6px 22px #000',
        }}
      >
        {CLIP.hook}
      </div>
      {CLIP.hookSub ? (
        <div
          style={{
            marginTop: 14,
            fontSize: __HOOK_SUB_SIZE__,
            fontWeight: 700,
            color: '#FFE066',
            textShadow: '0 4px 15px #000',
          }}
        >
          {CLIP.hookSub}
        </div>
      ) : null}
    </div>
  );
};

const FONT = '"Inter", "Segoe UI Variable Display", "Segoe UI", system-ui, -apple-system, "Helvetica Neue", Arial, sans-serif';

export const Short = () => {
  return (
    <AbsoluteFill style={{background: '#000', overflow: 'hidden', fontFamily: FONT}}>
      <Video
        src={staticFile('videos/source.mp4')}
        trimBefore={CLIP.sourceTrimFrames}
        volume={1}
        objectFit="cover"
        style={{position: 'absolute', width: '100%', height: '100%'}}
      />

      {/* Legibility scrim. Captions sit over the bottom band. */}
      <AbsoluteFill
        style={{
          background:
            'linear-gradient(180deg, rgba(0,0,0,.40) 0%, transparent 22%, transparent 62%, rgba(0,0,0,.72) 100%)',
        }}
      />

      {CLIP.hookHoldFrames > 0 ? <Hook /> : null}
      <CaptionTrack
        hideFromFrame={CLIP.durationInFrames}
        showFromFrame={CLIP.hookHoldFrames}
      />
    </AbsoluteFill>
  );
};
"""

README_MD = """# __COMPOSITION__

Generated by `scaffold_remotion.py`. Renders as-is.

```bash
npm install
npx tsc --noEmit                 # run this first; type errors surface as confusing render failures
npm run studio                   # preview
npm run still                    # one frame to out/still.png
npm run render                   # out/clip.mp4
```

On Windows with this project on a non-system drive, set the temp directory in
the same command as the render. The assignment does not persist between shells,
and Node fails with `ENOENT` rather than creating the directory:

```powershell
$env:TEMP='D:\\remotion-temp'; $env:TMP='D:\\remotion-temp'; npm run render
```

## Layout

```
__SLUG__/
  CLIP_CONTRACT.json     immutable provenance; never edit
  public/
    videos/source.mp4    frame zero of the timeline
    transcript.json      word timestamps, rebased to this clip
  src/
    config.ts            __START_HERE__
    Captions.tsx         __CAPTION_NOTE__
    Short.tsx            composition
  out/
```

`public/` sits beside `src/`, which is what `staticFile()` expects. Moving it
breaks every asset path.

## What to change first

1. `src/config.ts` -- `hook` and `hookSub` start from the transcript's suggested
   title. Rewrite them. Set `hookHoldFrames: 0` to drop the card.
2. `sourceTrimFrames` in the same file -- raise it to cut a slow start.
   Captions shift with it.
3. Colour and type in `Captions.tsx` and `Short.tsx`. There is no brand here on
   purpose.

## What not to change

`fps`, `width`, `height`, and `durationInFrames` come from the contract. A
`durationInFrames` past the materialized source's frame count renders black
frames at the tail, and changing `fps` desynchronises captions from speech.
"""


def scaffold(args: argparse.Namespace) -> dict[str, Any]:
    project = Path(args.project).resolve()
    contract_path = project / "CLIP_CONTRACT.json"
    if not contract_path.is_file():
        raise ValueError(f"no CLIP_CONTRACT.json in {project}")

    contract = read_json(contract_path)
    timeline = contract["timeline"]
    selection = contract.get("selection") or {}
    fps = float(timeline["fps"])
    duration = int(timeline["endFrameExclusive"])
    width = int(timeline["width"])
    height = int(timeline["height"])

    transcript_path = project / "public" / "transcript.json"
    if not transcript_path.is_file():
        raise ValueError(f"no rebased transcript at {transcript_path}")
    words = read_json(transcript_path)
    if not words:
        raise ValueError("transcript is empty; captions would render nothing")

    clip_id = str(contract["id"])
    composition = "Clip" + re.sub(r"[^A-Za-z0-9]", "", clip_id.title())
    existing = [
        str(path.relative_to(project))
        for path in (project / "src", project / "package.json")
        if path.exists()
    ]
    if existing and not args.force:
        raise ValueError(f"refusing to overwrite existing project files: {existing} (pass --force)")

    # The card gets the title only. hookText is the opening spoken line, so
    # putting it in the subtitle prints the same words the captions are about to
    # speak -- twice on screen, once redundantly.
    hook = selection.get("suggestedTitle") or selection.get("hookText") or "YOUR HOOK HERE"
    if len(hook) > 60:
        hook = hook[:60].rsplit(" ", 1)[0].rstrip(" ,;:")
    hook_sub = ""

    # Type scales with the canvas so a 720x1280 scaffold is not unreadable and a
    # 1080x1920 one is not comically large.
    scale = height / 1920
    tokens = {
        "SLUG": clip_id,
        "COMPOSITION": composition,
        "REMOTION": REMOTION_VERSION,
        "REACT": REACT_VERSION,
        "FPS": fps if fps % 1 else int(fps),
        "WIDTH": width,
        "HEIGHT": height,
        "DURATION": duration,
        "MID_FRAME": duration // 2,
        "HOOK": json.dumps(hook.upper()),
        "HOOK_SUB": json.dumps(hook_sub),
        "HOOK_FRAMES": min(int(round(2.5 * fps)), max(0, duration - 1)),
        "HOOK_TOP": int(round(300 * scale)),
        "HOOK_SIZE": int(round(78 * scale)),
        "HOOK_SUB_SIZE": int(round(40 * scale)),
        "CAPTION_BOTTOM": int(round(300 * scale)),
        "CAPTION_SIZE": int(round(70 * scale)),
        "CUE_SIZE": int(round(52 * scale)),
    }

    written: list[str] = []

    def emit(relative: str, template: str, **extra: Any) -> None:
        write_text(project / relative, fill(template, **{**tokens, **extra}))
        written.append(relative)

    if args.caption_style == "word":
        emit("package.json", PACKAGE_JSON_WORD)
        emit("src/Captions.tsx", CAPTIONS_WORD_TSX)
        caption_note = "@remotion/captions, per-word highlight"
    else:
        emit("package.json", PACKAGE_JSON_CUE)
        cues = group_cues(words, fps, duration)
        if not cues:
            raise ValueError("no cues generated; check the transcript's word timings")
        write_text(project / "src" / "cues.json", json.dumps(cues, indent=1) + "\n")
        written.append("src/cues.json")
        emit("src/Captions.tsx", CAPTIONS_CUE_TSX)
        caption_note = f"{len(cues)} generated cues from cues.json"

    emit("tsconfig.json", TSCONFIG)
    emit("remotion.config.ts", REMOTION_CONFIG)
    emit("src/index.ts", INDEX_TS)
    emit("src/Root.tsx", ROOT_TSX)
    emit("src/config.ts", CONFIG_TS)
    emit("src/Short.tsx", SHORT_TSX)
    emit(
        "REMOTION.md",
        README_MD,
        START_HERE="hook text, trim, dimensions",
        CAPTION_NOTE=caption_note,
    )
    (project / "out").mkdir(exist_ok=True)

    return {
        "project": str(project),
        "composition": composition,
        "captionStyle": args.caption_style,
        "timeline": {"fps": fps, "width": width, "height": height, "durationInFrames": duration},
        "wordCount": len(words),
        "filesWritten": sorted(written),
        "nextSteps": [
            "npm install",
            "npx tsc --noEmit",
            f"npx remotion render src/index.ts {composition} out/clip.mp4 --codec=h264 --concurrency=1",
        ],
    }


def cues_only(args: argparse.Namespace) -> dict[str, Any]:
    """Regenerate src/cues.json after editing the transcript."""
    project = Path(args.project).resolve()
    contract = read_json(project / "CLIP_CONTRACT.json")
    words = read_json(project / "public" / "transcript.json")
    fps = float(contract["timeline"]["fps"])
    duration = int(contract["timeline"]["endFrameExclusive"])
    cues = group_cues(words, fps, duration)
    output = project / "src" / "cues.json"
    write_text(output, json.dumps(cues, indent=1) + "\n")
    longest = max((cue["text"] for cue in cues), key=len, default="")
    return {
        "output": str(output),
        "cueCount": len(cues),
        "longestCueChars": len(longest),
        "note": "Hand edits to cues.json are overwritten by this command.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("scaffold", help="write a full Remotion project into a clip directory")
    build.add_argument("--project", required=True, help="clip directory containing CLIP_CONTRACT.json")
    build.add_argument("--caption-style", default="word", choices=["word", "cue"])
    build.add_argument("--force", action="store_true", help="overwrite an existing src/ or package.json")

    regenerate = sub.add_parser("cues", help="regenerate src/cues.json from the transcript")
    regenerate.add_argument("--project", required=True)

    args = parser.parse_args()
    handlers = {"scaffold": scaffold, "cues": cues_only}

    real_stdout = sys.stdout
    try:
        # Keep stdout a clean JSON channel even if a future dependency prints.
        with contextlib.redirect_stdout(sys.stderr):
            result = handlers[args.command](args)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}, indent=2), file=real_stdout)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True), file=real_stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
