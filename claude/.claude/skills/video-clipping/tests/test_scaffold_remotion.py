from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
CLI = SKILL / "scripts" / "scaffold_remotion.py"

spec = importlib.util.spec_from_file_location("_scaffold", CLI)
scaffold = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scaffold)


def make_words(text: str, start: float = 0.4, gap: float = 0.05) -> list[dict]:
    words, clock = [], start
    for index, token in enumerate(text.split()):
        duration = 0.28 + (index % 3) * 0.06
        words.append({"word": token, "start": round(clock, 3), "end": round(clock + duration, 3)})
        clock += duration + (0.42 if token.endswith((".", ":", "!", "?")) else gap)
    return words


SAMPLE = (
    "Almost everyone gets this step wrong. It costs them the whole result, and "
    "nobody notices until later. Here is the fix: slow down at the start."
)


def make_project(
    root: Path, fps: float = 30, width: int = 1080, height: int = 1920,
    frames: int = 900, words: list[dict] | None = None,
    title: str | None = "The Mistake Almost Everyone Makes",
) -> Path:
    (root / "public" / "videos").mkdir(parents=True, exist_ok=True)
    selection = {"composite": 8.4, "topicKey": "common-mistake",
                 "hookText": "Almost everyone gets this step wrong, and it costs them dearly."}
    if title is not None:
        selection["suggestedTitle"] = title
    (root / "CLIP_CONTRACT.json").write_text(json.dumps({
        "schemaVersion": 1, "id": "01-a1b2c3d4", "rank": 1,
        "timeline": {"startFrame": 0, "endFrameExclusive": frames, "fps": fps,
                     "width": width, "height": height},
        "selection": selection,
    }), encoding="utf-8")
    (root / "public" / "transcript.json").write_text(
        json.dumps(words if words is not None else make_words(SAMPLE)), encoding="utf-8"
    )
    return root


class CueGroupingTests(unittest.TestCase):
    def test_sentences_break_at_punctuation(self) -> None:
        cues = scaffold.group_cues(make_words(SAMPLE), 30.0, 900)
        self.assertGreaterEqual(len(cues), 3)
        self.assertTrue(cues[0]["text"].endswith("."), cues[0])

    def test_cues_never_invert_or_collapse(self) -> None:
        cues = scaffold.group_cues(make_words(SAMPLE), 30.0, 900)
        for cue in cues:
            self.assertGreater(cue["end"], cue["start"], cue)

    def test_cues_do_not_overlap(self) -> None:
        """Two cues on screen at once would stack and overprint."""
        cues = scaffold.group_cues(make_words(SAMPLE), 30.0, 900)
        for earlier, later in zip(cues, cues[1:]):
            self.assertLessEqual(earlier["end"], later["start"], (earlier, later))

    def test_no_cue_runs_past_the_composition(self) -> None:
        cues = scaffold.group_cues(make_words(SAMPLE), 30.0, 120)
        for cue in cues:
            self.assertLessEqual(cue["end"], 120, cue)

    def test_a_long_pause_breaks_a_cue(self) -> None:
        words = [
            {"word": "before", "start": 0.0, "end": 0.4},
            {"word": "after", "start": 2.0, "end": 2.4},
        ]
        cues = scaffold.group_cues(words, 30.0, 300)
        self.assertEqual(len(cues), 2, cues)

    def test_a_long_clause_breaks_on_length(self) -> None:
        words = make_words(" ".join(["word"] * 40), gap=0.01)
        cues = scaffold.group_cues(words, 30.0, 2000)
        self.assertGreater(len(cues), 1)
        for cue in cues:
            self.assertLessEqual(len(cue["text"]), scaffold.MAX_CHARS + 12, cue)

    def test_blank_words_do_not_create_empty_cues(self) -> None:
        words = [
            {"word": "   ", "start": 0.0, "end": 0.2},
            {"word": "real.", "start": 0.3, "end": 0.7},
        ]
        cues = scaffold.group_cues(words, 30.0, 300)
        for cue in cues:
            self.assertTrue(cue["text"].strip(), cue)


class TemplateTests(unittest.TestCase):
    def test_fill_rejects_an_unsubstituted_placeholder(self) -> None:
        with self.assertRaises(ValueError) as caught:
            scaffold.fill("id: __COMPOSITION__ fps: __FPS__", COMPOSITION="X")
        self.assertIn("__FPS__", str(caught.exception))

    def test_fill_leaves_jsx_braces_alone(self) -> None:
        """A brace-formatting template would corrupt every style block."""
        result = scaffold.fill("style={{color: '#fff'}} id: __ID__", ID="X")
        self.assertIn("{{color: '#fff'}}", result)

    def test_every_template_declares_a_font_family(self) -> None:
        """Without one the browser falls back to a serif, which looks broken."""
        self.assertIn("fontFamily: FONT", scaffold.SHORT_TSX)
        self.assertIn("const FONT", scaffold.SHORT_TSX)

    def test_both_caption_tracks_accept_the_hook_gate(self) -> None:
        for template in (scaffold.CAPTIONS_WORD_TSX, scaffold.CAPTIONS_CUE_TSX):
            self.assertIn("showFromFrame", template)

    def test_the_highlight_timebase_survives_the_gate(self) -> None:
        """The active-word highlight must key off the Sequence's real start.

        The hook gate can clamp a page to start later than it was spoken. Keying
        the highlight off page.startMs then runs it behind by exactly the amount
        the gate held it back -- in a real render the highlight sat on word 6
        while word 9 was being spoken.
        """
        template = scaffold.CAPTIONS_WORD_TSX
        self.assertNotIn("page.startMs + (frame / fps)", template)
        self.assertIn("fromFrame", template)
        self.assertIn("fromFrame={visibleStart}", template)

    def test_a_word_page_is_not_capped_at_the_grouping_window(self) -> None:
        """SWITCH_MS groups tokens; it is not how long a page may stay up.

        createTikTokStyleCaptions puts words on one page when they fall within
        SWITCH_MS OF EACH OTHER, so a four-word page spans well over one window.
        Capping duration at SWITCH_MS blanked the last page of a real render
        while its words were still being spoken.
        """
        template = scaffold.CAPTIONS_WORD_TSX
        self.assertNotIn("start + Math.round((SWITCH_MS / 1000) * fps)", template)
        # The page must instead end from its own last spoken token.
        self.assertIn("page.tokens[page.tokens.length - 1]", template)
        self.assertIn("lastToken?.toMs", template)

    def test_word_style_depends_on_the_captions_package(self) -> None:
        self.assertIn("@remotion/captions", scaffold.PACKAGE_JSON_WORD)

    def test_cue_style_does_not(self) -> None:
        """The cue path exists so a project can skip that dependency."""
        self.assertNotIn("@remotion/captions", scaffold.PACKAGE_JSON_CUE)


class ScaffoldTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> tuple[dict, int]:
        completed = subprocess.run(
            [sys.executable, str(CLI), *arguments], capture_output=True, text=True
        )
        return json.loads(completed.stdout), completed.returncode

    def test_a_full_project_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = make_project(Path(temporary))
            report, code = self.run_cli("scaffold", "--project", str(root))
            self.assertEqual(code, 0, report)
            for relative in ("package.json", "tsconfig.json", "remotion.config.ts",
                             "src/index.ts", "src/Root.tsx", "src/config.ts",
                             "src/Captions.tsx", "src/Short.tsx"):
                self.assertTrue((root / relative).is_file(), relative)

    def test_the_contract_drives_the_composition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = make_project(Path(temporary), fps=60, width=720, height=1280, frames=480)
            report, code = self.run_cli("scaffold", "--project", str(root))
            self.assertEqual(code, 0, report)
            config = (root / "src" / "config.ts").read_text(encoding="utf-8")
            self.assertIn("fps: 60", config)
            self.assertIn("width: 720", config)
            self.assertIn("height: 1280", config)
            self.assertIn("durationInFrames: 480", config)

    def test_type_scales_with_the_canvas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            small = make_project(Path(temporary) / "small", height=1280, width=720)
            self.run_cli("scaffold", "--project", str(small))
            text = (small / "src" / "Captions.tsx").read_text(encoding="utf-8")
            # 70px at 1920 scales to 47px at 1280, not left at 70.
            self.assertIn("fontSize: 47", text)

    def test_the_hook_subtitle_does_not_repeat_the_captions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = make_project(Path(temporary))
            self.run_cli("scaffold", "--project", str(root))
            config = (root / "src" / "config.ts").read_text(encoding="utf-8")
            self.assertIn('hookSub: ""', config)

    def test_a_missing_title_falls_back_to_the_hook_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = make_project(Path(temporary), title=None)
            self.run_cli("scaffold", "--project", str(root))
            config = (root / "src" / "config.ts").read_text(encoding="utf-8")
            self.assertIn("hook: ", config)
            self.assertNotIn('hook: ""', config)

    def test_hook_frames_never_exceed_the_composition(self) -> None:
        """A 40-frame clip cannot hold a 75-frame card."""
        with tempfile.TemporaryDirectory() as temporary:
            root = make_project(Path(temporary), frames=40, words=make_words("Short clip here."))
            self.run_cli("scaffold", "--project", str(root))
            config = (root / "src" / "config.ts").read_text(encoding="utf-8")
            held = int(config.split("hookHoldFrames:")[1].split(",")[0])
            self.assertLess(held, 40, config)

    def test_cue_style_writes_cues_and_drops_the_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = make_project(Path(temporary))
            report, code = self.run_cli("scaffold", "--project", str(root), "--caption-style", "cue")
            self.assertEqual(code, 0, report)
            self.assertTrue((root / "src" / "cues.json").is_file())
            package = json.loads((root / "package.json").read_text(encoding="utf-8"))
            self.assertNotIn("@remotion/captions", package["dependencies"])

    def test_an_existing_project_is_not_clobbered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = make_project(Path(temporary))
            self.run_cli("scaffold", "--project", str(root))
            (root / "src" / "Short.tsx").write_text("// my edits", encoding="utf-8")
            report, code = self.run_cli("scaffold", "--project", str(root))
            self.assertEqual(code, 1)
            self.assertIn("refusing to overwrite", report["error"])
            self.assertEqual((root / "src" / "Short.tsx").read_text(encoding="utf-8"), "// my edits")

    def test_force_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = make_project(Path(temporary))
            self.run_cli("scaffold", "--project", str(root))
            report, code = self.run_cli("scaffold", "--project", str(root), "--force")
            self.assertEqual(code, 0, report)

    def test_a_missing_contract_errors_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report, code = self.run_cli("scaffold", "--project", temporary)
            self.assertEqual(code, 1)
            self.assertIn("CLIP_CONTRACT.json", report["error"])

    def test_an_empty_transcript_is_refused(self) -> None:
        """Captions over no words render a silent, empty overlay."""
        with tempfile.TemporaryDirectory() as temporary:
            root = make_project(Path(temporary), words=[])
            report, code = self.run_cli("scaffold", "--project", str(root))
            self.assertEqual(code, 1)
            self.assertIn("empty", report["error"])

    def test_stdout_stays_pure_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = make_project(Path(temporary))
            completed = subprocess.run(
                [sys.executable, str(CLI), "scaffold", "--project", str(root)],
                capture_output=True, text=True,
            )
            json.loads(completed.stdout)

    def test_cues_can_be_regenerated_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = make_project(Path(temporary))
            self.run_cli("scaffold", "--project", str(root), "--caption-style", "cue")
            report, code = self.run_cli("cues", "--project", str(root))
            self.assertEqual(code, 0, report)
            self.assertGreater(report["cueCount"], 0)


if __name__ == "__main__":
    unittest.main()
