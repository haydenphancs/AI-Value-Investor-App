"""
Phase 3 — voice + word-timed captions (worker side: marketing/timings.py, captions.py, voice.py;
SYSTEM_DESIGN_GUIDELINES §12.7). No torch here: the Kokoro child is faked at its process boundary,
and the token shapes below are the REAL ones kokoro 0.9.4 / misaki 0.9.4 produced in the
2026-09-26 spike (`$` untimed, punctuation as its own token, quotes attached, "3%"/"U.S." whole).

The table these modules produce is checked against the SERVER's own validators
(`app.schemas.marketing.validate_audio_words`, `run_service.narration_words`), so the two halves
of the contract cannot drift apart silently.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import List

import pytest

from app.schemas import marketing as sch
from app.services.marketing import run_service as mrs
from marketing import captions as cap
from marketing import timings as tm
from marketing import voice as vc

_PKG = Path(__file__).resolve().parents[1] / "marketing"
T = tm.Token


def _toks(*spec) -> List[tm.Token]:
    """("text", "ws", start|None, end|None) tuples → Token list."""
    return [T(*s) for s in spec]


# The spike's real shapes.
DOLLAR_LINE = "NVIDIA paid roughly $7.5 billion in 2019."
DOLLAR_TOKENS = _toks(("NVIDIA", " ", 0.3, 0.9), ("paid", " ", 0.91, 1.19), ("roughly", " ", 1.2, 1.56),
                      ("$", "", None, None), ("7.5", " ", 1.57, 2.58), ("billion", " ", 2.59, 3.1),
                      ("in", " ", 3.12, 3.25), ("2019", "", 3.3, 4.2), (".", "", 4.2, 4.4))
QUOTE_LINE = "“It’s a snapshot,” not a movie — one day."
QUOTE_TOKENS = _toks(("“", "", 0.23, 0.28), ("It’s", " ", 0.28, 0.41), ("a", " ", 0.41, 0.5),
                     ("snapshot", "", 0.5, 1.23), (",", "", 1.23, 1.27), ("”", " ", 1.27, 1.31),
                     ("not", " ", 1.31, 1.46), ("a", " ", 1.46, 1.52), ("movie", " ", 1.52, 1.89),
                     ("—", " ", 1.89, 1.95), ("one", " ", 1.95, 2.17), ("day", "", 2.17, 2.42),
                     (".", "", 2.42, 2.6))


# ── timings ──────────────────────────────────────────────────────────────────


def test_tokens_group_into_the_scripts_own_words():
    groups = tm.group_tokens(DOLLAR_TOKENS)
    assert [g[0] for g in groups] == ["NVIDIA", "paid", "roughly", "$7.5", "billion", "in", "2019."]
    # the untimed "$" borrows nothing: the group's start is its first KNOWN time
    assert groups[3][1] == 1.57


def test_an_exact_line_keeps_the_display_words_and_the_engines_times():
    timed, exact = tm.align_line(DOLLAR_LINE, DOLLAR_TOKENS, 4.5)
    assert exact and [w for w, _s, _e in timed] == DOLLAR_LINE.split()
    assert timed[3] == ("$7.5", 1.57, 2.58)
    timed, exact = tm.align_line(QUOTE_LINE, QUOTE_TOKENS, 2.7)
    assert exact and [w for w, _s, _e in timed] == QUOTE_LINE.split()
    assert timed[0][1] == 0.23 and timed[2][2] == 1.31      # "“It’s" starts at the quote; "snapshot,”" ends after it


def test_a_misaligned_line_falls_back_to_proportional_inside_the_timed_span():
    tokens = _toks(("Hello", " ", 0.2, 0.5), ("wor", "", 0.5, 0.7), ("ld", " ", 0.7, 0.9))
    timed, exact = tm.align_line("Hello big world", tokens, 1.0)
    assert not exact and [w for w, *_ in timed] == ["Hello", "big", "world"]
    assert timed[0][1] == pytest.approx(0.2) and timed[-1][2] == pytest.approx(0.9)
    assert all(s < e for _w, s, e in timed)


def test_a_line_with_no_timed_token_is_spread_over_its_audio():
    tokens = _toks(("Hi", " ", None, None), ("there", "", None, None))
    timed, exact = tm.align_line("Hi there", tokens, 1.2)
    assert not exact and timed[0][1] == 0.0 and timed[-1][2] == pytest.approx(1.2)


@pytest.mark.parametrize("line", ["", "   "])
def test_an_empty_line_has_no_words(line):
    assert tm.align_line(line, [], 0.5) == ([], True)


def test_a_single_word_line_and_an_untimed_middle_group():
    tokens = _toks(("Go", "", 0.1, 0.4))
    assert tm.align_line("Go", tokens, 0.5) == ([("Go", 0.1, 0.4)], True)
    tokens = _toks(("a", " ", 0.0, 0.2), ("$", " ", None, None), ("b", "", 0.6, 0.8))
    timed, exact = tm.align_line("a $ b", tokens, 1.0)
    assert exact and timed[1] == ("$", 0.2, 0.6)           # borrows the neighbours' edges


def test_assemble_offsets_every_line_by_the_audio_before_it_plus_a_pause():
    words = tm.assemble(["a b", "c"], [([("a", 0.0, 0.3), ("b", 0.3, 0.6)], 1.0), ([("c", 0.1, 0.4)], 0.5)])
    assert [(w.w, round(w.s, 3), w.line) for w in words] == [("a", 0.0, 0), ("b", 0.3, 0),
                                                             ("c", round(1.0 + tm.LINE_PAUSE_SECONDS + 0.1, 3), 1)]


def test_order_is_enforced_and_every_word_is_readable_where_the_timeline_allows():
    words = [tm.Word("a", 0.0, 0.05, 0), tm.Word("b", 0.02, 0.5, 0), tm.Word("c", 0.4, 0.3, 0),
             tm.Word("d", 0.6, 0.61, 0)]
    out = tm.enforce_order(words, total=2.0)
    for prev, nxt in zip(out, out[1:]):
        assert prev.e <= nxt.s + 1e-9
    assert all(w.e > w.s for w in out)
    assert out[-1].e - out[-1].s >= tm.MIN_WORD_SECONDS - 1e-9      # room after it: extended


def test_duplicate_and_crowded_stamps_never_produce_an_invalid_table():
    words = [tm.Word(f"w{i}", 1.0, 1.0, 0) for i in range(30)]
    table = tm.as_table(tm.enforce_order(words, total=1.0))
    sch.validate_audio_words(table, duration_seconds=1.0 + len(table) * 0.001)


def test_the_table_passes_the_servers_schema_and_word_check():
    script = {"hook": "Meet Mr. Market.", "video_script": [DOLLAR_LINE, QUOTE_LINE]}
    lines = tm.narrated_lines(script)
    per_line = [(tm.align_line(lines[0], _toks(("Meet", " ", 0.1, 0.4), ("Mr.", " ", 0.4, 0.7),
                                                ("Market", "", 0.7, 1.1), (".", "", 1.1, 1.2)), 1.3)[0], 1.3),
                (tm.align_line(lines[1], DOLLAR_TOKENS, 4.5)[0], 4.5),
                (tm.align_line(lines[2], QUOTE_TOKENS, 2.7)[0], 2.7)]
    table = tm.as_table(tm.assemble(lines, per_line))
    total = 1.3 + 4.5 + 2.7 + 2 * tm.LINE_PAUSE_SECONDS
    sch.validate_audio_words(table, duration_seconds=total)
    got = [w for entry in table for w in mrs.spoken_words(entry["w"])]
    assert got == mrs.narration_words(script)


def test_narrated_lines_skip_blanks_and_keep_order():
    assert tm.narrated_lines({"hook": " H ", "video_script": ["a", "  ", "b"]}) == ["H", "a", "b"]
    assert tm.narrated_lines({"video_script": []}) == []


# ── captions ─────────────────────────────────────────────────────────────────


def _timed(text: str, line: int = 0, step: float = 0.3) -> List[dict]:
    return [{"w": w, "s": round(i * step, 3), "e": round(i * step + step - 0.02, 3), "line": line}
            for i, w in enumerate(text.split())]


@pytest.mark.parametrize("raw, safe", [
    ("{\\p1}m 0 0 l 100 0{\\p0}", "(/p1)m 0 0 l 100 0(/p0)"),
    ("a\\Nb", "a/Nb"),
    ("tab\there\u0007bell", "tab here bell" if False else "tabherebell"),
    ("  spaced   out  ", "spaced out"),
    ("zero​width", "zerowidth"),
])
def test_model_text_cannot_carry_ass_overrides_or_control_characters(raw, safe):
    out = cap.sanitize(raw)
    assert "{" not in out and "}" not in out and "\\" not in out
    assert out == safe


def test_the_ass_file_has_the_frame_font_and_one_event_per_readable_word():
    text = cap.build_ass(_timed("Meet your moody business partner."))
    assert "PlayResX: 1080" in text and "PlayResY: 1920" in text and "WrapStyle: 2" in text
    assert "YCbCr Matrix: None" in text and f"Style: Caption,{cap.FONT_NAME}," in text
    events = [ln for ln in text.splitlines() if ln.startswith("Dialogue:")]
    assert len(events) == 5
    assert all(cap.HIGHLIGHT_C in e for e in events)
    assert cap.HIGHLIGHT_C == "&HFAA560&"                   # #60A5FA in BGR


def test_events_are_monotonic_and_contiguous_inside_a_phrase():
    evs = cap.events(_timed("Every day he offers a price for your share of the business."))
    for a, b in zip(evs, evs[1:]):
        assert a.start < a.end <= b.start + 1e-9
    assert all(e.end - e.start > 0 for e in evs)


_FONT = str(_PKG / "assets" / "fonts" / "Inter-Bold.ttf")


def test_phrases_respect_lines_sentences_abbreviations_and_function_words():
    words = _timed("Meet Mr. Market today.") + _timed("He waits.", line=1)
    groups = [" ".join(w["w"] for w in g) for g in cap.phrases(words, cap.font_measurer(_FONT))]
    assert groups == ["Meet Mr. Market today.", "He waits."]
    groups = [" ".join(w["w"] for w in g) for g in cap.phrases(_timed("one two three four a five six"))]
    assert not any(g.split()[-1] in ("a", "the", "of") for g in groups if len(g.split()) > 1)


def test_a_word_wider_than_the_frame_is_shrunk_not_wrapped():
    evs = cap.events(_timed("Supercalifragilisticexpialidociousnesses"))
    assert evs and "\\fscx" in evs[0].text


def test_short_windows_fold_into_the_next_instead_of_flashing():
    words = [{"w": "a", "s": 0.0, "e": 0.05, "line": 0}, {"w": "b", "s": 0.05, "e": 0.5, "line": 0},
             {"w": "c", "s": 0.5, "e": 0.9, "line": 0}]
    evs = cap.events(words)
    assert evs[0].start == 0.0 and all(e.end - e.start >= cap.MIN_EVENT_SECONDS for e in evs[:-1])


def test_the_caption_file_is_deterministic():
    words = _timed("Meet your moody business partner.")
    assert cap.build_ass(words) == cap.build_ass(words)


def test_ass_time_format():
    assert cap.ts(0) == "0:00:00.00" and cap.ts(61.234) == "0:01:01.23" and cap.ts(3600) == "1:00:00.00"


def test_the_vendored_font_draws_the_brand_copy():
    font = str(_PKG / "assets" / "fonts" / "Inter-Bold.ttf")
    assert cap.missing_glyphs(font, ["Meet “Mr. Market” — it’s 7.5% of S&P 500!"]) == []
    assert (_PKG / "assets" / "fonts" / "OFL.txt").read_text().startswith("Copyright")


# ── the voice stage (the Kokoro child faked at its boundary) ─────────────────


class _Api:
    def __init__(self, assets=None):
        self.assets = assets or []
        self.calls: List[tuple] = []

    def list_assets(self, run_id):
        self.calls.append(("list", run_id))
        return {"voice_asset_id": None, "assets": self.assets}

    def register_asset(self, run_id, **fields):
        self.calls.append(("register", fields))
        return {"asset": {"id": "audio-1", "storage_path": "p"}, "upload": {"url": "u"}}

    def complete_asset(self, asset_id):
        self.calls.append(("complete", asset_id))

    def update_run(self, run_id, **fields):
        self.calls.append(("patch", fields))


class _Skip(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


SCRIPT = {"hook": "Meet Mr. Market.", "video_script": ["He names a price.", "You may say no."]}


def _runner(durations):
    calls = []

    def run(lines, *, voice, speed, out_dir, heartbeat=None):
        calls.append(speed)
        d = durations[len(calls) - 1]
        per_line = [(tm.proportional(line.split(), 0.0, 1.0), 1.0) for line in lines]
        return vc.Narration(Path(out_dir) / "n.wav", tm.as_table(tm.assemble(lines, per_line)), d, speed, len(lines))
    return run, calls


def _stage(api, runner, uploads=None):
    uploads = uploads if uploads is not None else []
    return vc.stage_voice(api, {"id": "run-1"}, {"script": SCRIPT}, skip=_Skip,
                          uploader=lambda up, data, apikey=None: uploads.append(len(data)),
                          hasher=lambda b: "f" * 64, runner=runner,
                          encoder=lambda wav, out: (b"m4a-bytes", 3.0 + 2 * tm.LINE_PAUSE_SECONDS))


def test_the_stage_registers_uploads_completes_and_returns_the_pointer():
    api, (run, calls), uploads = _Api(), _runner([3.0]), []
    out = _stage(api, run, uploads)
    assert out == {"voice_asset_id": "audio-1"} and calls == [1.0] and uploads == [9]
    (reg,) = [f for k, f in api.calls if k == "register"]
    md = reg["metadata"]
    assert reg["kind"] == "audio" and reg["ext"] == "m4a"
    assert reg["duration_seconds"] == round(3.0 + 2 * tm.LINE_PAUSE_SECONDS, 3)
    assert md["pipeline_version"] == vc.PIPELINE_VERSION and md["script_sha256"] == vc.script_sha256(SCRIPT)
    assert [w for e in md["words"] for w in mrs.spoken_words(e["w"])] == mrs.narration_words(SCRIPT)
    sch.AssetRegisterRequest(kind="audio", ext="m4a", sha256="f" * 64, bytes=9,
                             duration_seconds=reg["duration_seconds"], metadata=md)   # the server accepts it


def test_a_word_past_the_encoded_audio_is_clamped_not_refused():
    words = [{"w": "a", "s": 0.0, "e": 1.0, "line": 0}, {"w": "b", "s": 1.0, "e": 2.4, "line": 0},
             {"w": "c", "s": 2.4, "e": 2.6, "line": 0}]
    out = vc.clamp_to_duration(words, 2.0)
    sch.validate_audio_words(out, duration_seconds=2.0)
    assert all(w["e"] <= 2.0 for w in out) and [w["w"] for w in out] == ["a", "b", "c"]
    assert vc.clamp_to_duration(words, 3.0) == words          # nothing past the audio: untouched


def _squeezed(at: float, overrun: float) -> list:
    """A 1 ms punctuation word ("—") butted against its successor — what `enforce_order` emits
    when the engine gives the next word no gap — in a 60 s table whose last word overruns."""
    return [{"w": "a", "s": 0.0, "e": at, "line": 0},
            {"w": "\u2014", "s": at, "e": round(at + 0.001, 3), "line": 0},
            {"w": "c", "s": round(at + 0.001, 3), "e": 40.0, "line": 0},
            {"w": "z", "s": 40.0, "e": round(60.0 + overrun, 3), "line": 1}]


def test_clamping_never_makes_a_word_start_before_the_previous_end():
    """Review 2026-09-26: rounding s and e separately after scaling collapsed the 1 ms word and
    let the next one start before its bumped end — refused by the server on every retry."""
    out = vc.clamp_to_duration(_squeezed(2.145, 0.07), 60.0)
    sch.validate_audio_words(out, duration_seconds=60.0)
    # A deterministic sweep over the placements that used to fail (75 of 56,700 in review).
    for step in range(0, 3000):
        at = round(1.0 + step * 0.001, 3)
        for overrun in (0.01, 0.07, 0.12, 0.3):
            out = vc.clamp_to_duration(_squeezed(at, overrun), 60.0)
            sch.validate_audio_words(out, duration_seconds=60.0)
            assert all(w["s"] < w["e"] for w in out)
            assert all(b["s"] >= a["e"] for a, b in zip(out, out[1:]))
            assert out[-1]["e"] <= 60.0 + 0.004, out[-1]


def test_a_matching_ready_narration_is_reused_without_synthesis():
    ready = {"id": "audio-9", "kind": "audio", "status": "ready",
             "metadata": {"script_sha256": vc.script_sha256(SCRIPT), "pipeline_version": vc.PIPELINE_VERSION,
                          "voice": vc.DEFAULT_VOICE, "words": [{"w": "x", "s": 0, "e": 1}]}}
    api, (run, calls) = _Api([ready]), _runner([3.0])
    assert _stage(api, run) == {"voice_asset_id": "audio-9"} and calls == []
    for stale in ({"pipeline_version": "old"}, {"voice": "other"}, {"script_sha256": "0" * 64}):
        api, (run, calls) = _Api([{**ready, "metadata": {**ready["metadata"], **stale}}]), _runner([3.0])
        _stage(api, run)
        assert calls == [1.0], stale


def test_an_overlong_narration_is_re_synthesised_once_faster_then_skipped(monkeypatch):
    monkeypatch.setenv("MARKETING_MAX_VIDEO_SECONDS", "20")
    budget = vc.video_budget_seconds()
    api, (run, calls) = _Api(), _runner([budget + 1.0, budget - 0.5])
    assert _stage(api, run) == {"voice_asset_id": "audio-1"}
    assert calls[0] == 1.0 and 1.0 < calls[1] <= vc.MAX_SPEED
    api, (run, calls) = _Api(), _runner([budget * 2, budget * 1.5])
    with pytest.raises(_Skip) as info:
        _stage(api, run)
    assert info.value.reason == "narration_too_long" and len(calls) == 2
    assert not [k for k, _f in api.calls if k == "register"]


def test_speed_and_budget_come_from_the_workers_environment(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("MARKETING_MAX_VIDEO_SECONDS", raising=False)
    # The worker mirrors the web setting of the same name; the defaults must agree.
    assert vc.DEFAULT_MAX_VIDEO_SECONDS == Settings.model_fields["MARKETING_MAX_VIDEO_SECONDS"].default
    assert vc.video_budget_seconds() == vc.DEFAULT_MAX_VIDEO_SECONDS - vc.DISCLAIMER_CARD_SECONDS
    monkeypatch.setenv("MARKETING_MAX_VIDEO_SECONDS", "not a number")
    assert vc.video_budget_seconds() == vc.DEFAULT_MAX_VIDEO_SECONDS - vc.DISCLAIMER_CARD_SECONDS


def test_torch_threads_prefer_the_override_then_the_cgroup(monkeypatch, tmp_path):
    monkeypatch.setenv("MARKETING_TTS_THREADS", "3")
    assert vc.torch_threads() == 3
    monkeypatch.delenv("MARKETING_TTS_THREADS")
    real = Path.read_text

    def fake_read(self, *a, **k):
        if str(self) == "/sys/fs/cgroup/cpu.max":
            return "150000 100000\n"
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", fake_read)
    assert vc.torch_threads() == 2


class _Proc:
    def __init__(self, code, waits=0):
        self.returncode, self.waits, self.killed = code, waits, False
        self.stdout = None

    def wait(self, timeout=None):
        if self.waits > 0 and timeout is not None:
            self.waits -= 1
            raise subprocess.TimeoutExpired("x", timeout)
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


@pytest.mark.parametrize("code, exc", [(-9, vc.VoiceOOM), (137, vc.VoiceOOM), (1, vc.VoiceFailed)])
def test_a_dead_child_is_a_typed_failure(monkeypatch, tmp_path, code, exc):
    monkeypatch.setattr(vc.subprocess, "Popen", lambda *a, **k: _Proc(code))
    with pytest.raises(exc):
        vc.run_child(["a"], voice="af_heart", speed=1.0, out_dir=tmp_path)


def test_a_slow_child_heartbeats_and_a_wedged_one_is_killed(monkeypatch, tmp_path):
    clock = {"t": 0.0}
    monkeypatch.setattr(vc.time, "monotonic", lambda: clock.__setitem__("t", clock["t"] + 40) or clock["t"])
    proc = _Proc(0, waits=10**6)
    monkeypatch.setattr(vc.subprocess, "Popen", lambda *a, **k: proc)
    beats = []
    with pytest.raises(vc.VoiceFailed, match="timed out"):
        vc.run_child(["a"], voice="af_heart", speed=1.0, out_dir=tmp_path, heartbeat=lambda: beats.append(1),
                     timeout=600)
    assert proc.killed and len(beats) >= 3


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")
def test_the_encode_is_bit_exact_and_the_right_shape(tmp_path):
    wav = tmp_path / "in.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(bytes(2 * 24000))
    a, da = vc.encode_m4a(wav, tmp_path / "a.m4a")
    b, db = vc.encode_m4a(wav, tmp_path / "b.m4a")
    assert a == b and abs(da - 1.0) < 0.1
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate,channels",
                            "-of", "csv=p=0", str(tmp_path / "a.m4a")], capture_output=True, text=True)
    assert probe.stdout.strip() == "aac,48000,2"


# ── licences on the media path (rules marketing.md §5) ───────────────────────


def _code_references(path: Path) -> List[str]:
    """Names, attributes, imports and STRING constants in code — not comments or docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            out.append(n.id)
        elif isinstance(n, ast.Attribute):
            out.append(n.attr)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            out += [a.name for a in n.names] + ([n.module] if isinstance(n, ast.ImportFrom) and n.module else [])
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings:
            out.append(n.value)
    return out


def test_the_worker_never_references_the_non_commercial_aligner_or_the_scripts_tree():
    files = [p for p in _PKG.rglob("*.py") if "out" not in p.relative_to(_PKG).parts[:1]]
    assert len(files) >= 5
    bad = []
    for f in files:
        for ref in _code_references(f):
            low = ref.lower()
            if "mms_fa" in low or "forced_align" in low or low == "scripts" or low.startswith("scripts."):
                bad.append((f.name, ref))
    assert bad == [], bad
    # Anti-vacuity: the scanner sees a real reference when there is one.
    probe = _PKG.parent / "tests" / "_mms_probe.py"
    try:
        probe.write_text("from torchaudio.pipelines import MMS_FA\n")
        assert "MMS_FA" in _code_references(probe)
    finally:
        probe.unlink(missing_ok=True)


# ── review fixes (2026-09-26, M9) ─────────────────────────────────────────────


@pytest.mark.parametrize("base", [1.0, 1.0375, 10.3025, 2.0005, 7.1235, 123.4565])
def test_a_crowded_table_never_rounds_a_word_to_zero_length(base):
    """Kokoro times sit on a 0.5 ms grid, so a float 1 ms sliver rounded to 3 decimals could
    tie to s == e; the pass now works in whole milliseconds."""
    words = [tm.Word(f"w{i}", base, base, 0) for i in range(40)]
    table = tm.as_table(tm.enforce_order(words, total=base))
    sch.validate_audio_words(table, duration_seconds=table[-1]["e"])
    assert all(w["e"] > w["s"] for w in table)


def test_an_untimed_last_word_keeps_the_rest_of_its_line():
    """Kokoro 0.9.4 leaves a line's last words untimed after a phoneme-less token (" - ")."""
    tokens = _toks(("Profit", " ", 0.2, 0.6), ("-", " ", None, None), ("not", " ", 0.7, 0.9),
                   ("revenue", " ", 0.9, 1.4), ("-", " ", None, None), ("matters", "", None, None),
                   (".", "", None, None))
    timed, exact = tm.align_line("Profit - not revenue - matters.", tokens, 2.4)
    assert exact
    last = timed[-1]
    assert last[0] == "matters." and last[2] == pytest.approx(2.4) and last[2] - last[1] > 0.5


def test_widths_are_measured_at_the_em_libass_draws():
    """libass sizes Inter so ascent+descent = Fontsize (1.21 em): the drawn em is ~0.83×."""
    ratio = cap.font_em_ratio(_FONT)
    assert ratio == pytest.approx(2048 / (1984 + 494), rel=1e-6)
    m = cap.font_measurer(_FONT)
    from PIL import ImageFont

    at_fontsize = ImageFont.truetype(_FONT, cap.FONT_SIZE).getlength("Every day he offers")
    assert m("Every day he offers") == pytest.approx(at_fontsize * ratio, rel=0.03)


@pytest.mark.parametrize("text, groups", [
    ("Call it “luck.” It is not.", ["Call it “luck.”", "It is not."]),
    ("Is it risky? No. It is simple.", ["Is it risky?", "No.", "It is simple."]),
    ("Meet Mr. Market in the U.S. today.", None),
])
def test_sentence_ends_behind_quotes_and_short_answers_end_a_phrase(text, groups):
    got = [" ".join(w["w"] for w in g) for g in cap.phrases(_timed(text), cap.font_measurer(_FONT))]
    if groups is not None:
        assert got == groups, got
    else:
        assert not any(g.endswith("Mr.") or g.endswith("U.S.") for g in got), got


def test_quantized_events_never_overlap_or_collapse():
    evs = [cap.Event(0.40, 0.41, "a"), cap.Event(0.40, 0.90, "b"), cap.Event(2.875, 2.885, "c"),
           cap.Event(2.88, 3.2, "d")]
    q = cap.quantize(evs)
    assert all(e.end > e.start for e in q)
    for a, b in zip(q, q[1:]):
        assert a.end <= b.start
    text = cap.build_ass([{"w": "calm,", "s": 0.0, "e": 0.004, "line": 0},
                          {"w": "then", "s": 0.004, "e": 0.3, "line": 0}])
    for ln in [x for x in text.splitlines() if x.startswith("Dialogue:")]:
        start, end = ln.split(",")[1:3]
        assert start != end, ln
