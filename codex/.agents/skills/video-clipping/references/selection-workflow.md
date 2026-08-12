# Selection Workflow

Use the complete word-level transcript, not isolated keyword matches.

## 1. Detect candidate hooks

Inspect every semantic window and adjacent context. Mark questions, bold claims, pattern interrupts, stories, demonstrations, mistakes followed by corrections, emotional shifts, and topic transitions. For instructional content, prefer `mistake → correction` and `demonstration → explanation` arcs.

## 2. Construct complete arcs

Start semantically at or immediately before the hook and walk forward to the natural payoff. Then place the actual media start and end in adjacent silence or stable room tone when available. Transcript word timestamps are approximate alignment evidence, not acoustically safe trim points. As initial handles, retain roughly 100–160 ms before the first spoken onset and 100–140 ms after the final completed phrase, adjusting for the actual waveform and cadence. Extend backward by one sentence only when required for comprehension and still within duration limits. Reject candidates that require prior context, start mid-sentence, or end before the payoff. Never truncate a resolution merely to satisfy the maximum length.

All candidate frame ranges use the parent source FPS from `SHORTS_REQUEST.json.sourceVideo.fps`, with inclusive `startFrame` and exclusive `endFrameExclusive`.

## 3. Score with evidence

Assign each signal a 0–10 score and cite transcript word indexes/timestamps or visual-description timestamps:

- `hook` (30%): curiosity gap or immediate relevance in the first 1–3 seconds.
- `selfContainedness` (20%): understandable setup and payoff without the source video.
- `emotion` (15%): laughter, surprise, conviction, controversy, or an energy shift.
- `payoffDensity` (20%): useful revelation, demonstration, punchline, or action per second.
- `retention` (15%): open loop, escalation, and delayed but complete resolution.

Visual/audio evidence may adjust these five scores. Do not create a hidden sixth score. Do not infer vocal excitement from punctuation alone when the transcript is machine-generated; state the evidence limitation.

## 4. Write candidates

Write `analysis/candidates.raw.json`:

```json
{
  "schemaVersion": 1,
  "candidates": [{
    "startFrame": 900,
    "endFrameExclusive": 2250,
    "hookText": "Almost everyone gets this step wrong...",
    "hookType": "pattern_interrupt",
    "topicKey": "common-mistake",
    "topicLabel": "The most common mistake",
    "scores": {
      "hook": 8.5,
      "selfContainedness": 9,
      "emotion": 6,
      "payoffDensity": 9,
      "retention": 8
    },
    "evidence": {
      "hook": ["words 181–190, 00:30.0–00:33.2"],
      "selfContainedness": ["setup at 00:30; correction demonstrated by 00:58"],
      "emotion": ["visual description: emphatic gesture at 00:43"],
      "payoffDensity": ["three correction cues in 45 seconds"],
      "retention": ["mistake revealed first; fix lands at 00:55"]
    },
    "suggestedTitle": "The Mistake Almost Everyone Makes",
    "suggestedCaption": "One habit is undoing the rest of your work.",
    "rejectionFlags": []
  }]
}
```

Generate broadly; let the deterministic `rank` command compute composites, temporal suppression, and topic diversity. Do not pre-delete overlapping candidates.
