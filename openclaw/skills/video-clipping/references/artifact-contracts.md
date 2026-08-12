# Artifact Contracts

All documents use `schemaVersion: 1`, UTF-8 JSON, and half-open ranges `[startFrame,endFrameExclusive)`. Parent-source ranges use `sourceVideo.fps`; child timelines use `target.fps`.

## `SHORTS_REQUEST.json`

Treat this as immutable run intent. It records the absolute source path and fingerprint, probed source metadata, optional transcript/visual-description paths, target count/platform/dimensions/FPS/duration, five declared score weights, overlap threshold, and topic-diversity setting.

## `analysis/transcript.windows.json`

Each window records source-relative seconds, source word indexes, and transcript text. Windows are analysis aids, not clip boundaries.

## `analysis/candidates.raw.json`

Each candidate requires `startFrame`, `endFrameExclusive`, `hookText`, `hookType`, `topicKey`, the five score fields, evidence for each field, and `rejectionFlags`. IDs may be omitted; ranking derives a stable ID from source hash and source range.

Blocking flags are `midSentence`, `missingPayoff`, and `requiresPriorContext`.

## `analysis/ranked-shorts.json`

The CLI recomputes the weighted composite, rejects invalid durations/flags, applies temporal NMS, then topic diversity. It retains selected and rejected candidates with reasons. Never promote a rejected candidate by editing this file.

Overlap is intersection divided by the shorter candidate duration. At or above the configured threshold, the lower-ranked candidate is suppressed. Sort ties by source start frame then stable ID.

## `clips/<rank>-<id>/CLIP_CONTRACT.json`

The contract binds:

- immutable parent source path/hash and source-frame range;
- normalized child source path/hash;
- child frame-zero timeline, target FPS, and target dimensions;
- `timeline.materialized`, the child file's actual width, height, FPS, and frame count;
- score/topic/hook/title metadata;
- rebased transcript and optional visual-description evidence paths.

`timeline.width`/`height` are the delivery target. `timeline.materialized` is what the cut file really is. They differ whenever the source resolution is not the target resolution, because `materialize` cuts at native resolution rather than upscaling — scaling belongs to whatever renders the final clip. Check a materialized file against `timeline.materialized` and a final render against `timeline.width`/`height`. `qc.py --stage source` selects the former.

Downstream skills may add artifacts but must not change parent provenance or the materialized source mapping.

## `SHORTS_RUN_MANIFEST.json`

Track `discovered → analyzed → ranked → selected → materialized → directed → handoffs-ready → built → rendered → qc-passed → delivered`. Valid non-success states are `pending`, `blocked`, `failed`, `waived`, `replaced`, and `stale`, each with a reason. If an input hash changes, mark the affected stage and every descendant stale.
