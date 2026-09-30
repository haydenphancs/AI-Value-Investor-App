"""
Phase 4 — the video half of the render (worker side: marketing/video.py, SYSTEM_DESIGN_GUIDELINES
§12.8). Hermetic: the timeline, the argv and the ffprobe gate are pure; the runner is driven by a
fake process on a fake clock; ONE real smoke render runs through the local ffmpeg (skipped when
ffmpeg/ffprobe are missing) and proves the things only a real encode can: the disclaimer tail is
really in the file (never `-shortest`), the stream is CFR 30/1 with the B-frame/no-edit-list
arrangement, moov comes first, and two renders are byte-identical.
"""

from __future__ import annotations

import json
import logging
import math
import random
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

import pytest

from marketing import captions as cap
from marketing import video as vid

_FONT = Path(__file__).resolve().parents[1] / "marketing" / "assets" / "fonts" / "Inter-Bold.ttf"
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
S = vid.Segment


def W(w: str, s: float, e: float, line: int) -> dict:
    return {"w": w, "s": s, "e": e, "line": line}


def _table(line_starts: List[float], *, word_len: float = 0.3) -> List[dict]:
    """One two-word line per start (index = line number)."""
    out = []
    for line, s in enumerate(line_starts):
        out += [W("a", s, s + word_len, line), W("b", s + word_len, s + 2 * word_len, line)]
    return out


def _assert_well_formed(segs: List[vid.Segment], n_cards: int, narration: float, tail: float) -> None:
    assert segs[0].start == 0.0
    for a, b in zip(segs, segs[1:]):
        assert a.end == b.start, (a, b)                    # exactly contiguous, not approximately
        assert a.end > a.start
    assert segs[-1] == S(n_cards - 1, narration, narration + tail)
    shown = [s.card for s in segs[:-1]]
    assert shown == list(range(len(shown)))                # brand, then text cards 1..k in order
    assert len(set(s.card for s in segs)) == len(segs)     # no card twice
    assert all(0 <= s.card < n_cards for s in segs)
    for s in segs[:-1]:
        if narration >= 2 * vid.MIN_SEGMENT_SECONDS:
            assert s.end - s.start >= vid.MIN_SEGMENT_SECONDS - 1e-9, segs


# ── timeline ─────────────────────────────────────────────────────────────────


def test_no_text_cards_brand_covers_the_narration_then_the_disclaimer():
    segs = vid.timeline(_table([0.0, 2.5, 5.0]), 2, 8.0, 4.0)
    assert segs == [S(0, 0.0, 8.0), S(1, 8.0, 12.0)]


def test_one_text_card_starts_at_the_first_script_line():
    segs = vid.timeline(_table([0.0, 2.5, 5.0, 7.0]), 3, 9.0, 4.0)
    assert segs == [S(0, 0.0, 2.5), S(1, 2.5, 9.0), S(2, 9.0, 13.0)]


def test_four_text_cards_over_eight_lines_switch_on_every_second_line():
    starts = [0.0] + [2.0 + 3.0 * i for i in range(8)]     # hook + lines 1..8
    segs = vid.timeline(_table(starts), 6, 27.0, 4.0)
    assert [s.start for s in segs[1:5]] == [2.0, 8.0, 14.0, 20.0]   # lines 1, 3, 5, 7
    _assert_well_formed(segs, 6, 27.0, 4.0)


def test_uneven_line_counts_give_the_extra_lines_to_the_first_groups():
    starts = [0.0] + [2.0 + 3.0 * i for i in range(5)]     # lines 1..5 at 2, 5, 8, 11, 14
    segs = vid.timeline(_table(starts), 6, 17.0, 4.0)
    # 5 lines / 4 cards → groups of 2,1,1,1 → lines 1, 3, 4, 5
    assert [s.start for s in segs[1:5]] == [2.0, 8.0, 11.0, 14.0]


def test_more_cards_than_lines_split_time_and_every_card_gets_the_minimum():
    segs = vid.timeline(_table([0.0, 2.0, 4.0]), 7, 20.0, 4.0)   # 5 text cards, 2 lines
    _assert_well_formed(segs, 7, 20.0, 4.0)
    assert len(segs) == 7                                   # all five text cards shown
    starts = [s.start for s in segs[1:-1]]
    assert starts[0] == 2.0 and 4.0 in starts               # every line still opens a card
    assert starts == sorted(starts)
    # the long last line (4 → 20 s) takes the extra cards, split into equal slices
    assert starts[1] == 4.0 and all(b - a == pytest.approx(4.0) for a, b in zip(starts[1:], starts[2:]))


def test_a_narration_shorter_than_two_minimum_segments_is_the_brand_card_alone():
    segs = vid.timeline(_table([0.0, 0.5]), 5, 1.0, 4.0)
    assert segs == [S(0, 0.0, 1.0), S(4, 1.0, 5.0)]          # 1.0 s < MIN: documented exception
    segs = vid.timeline(_table([0.0, 1.3]), 5, 2.0, 4.0)
    assert segs == [S(0, 0.0, 2.0), S(4, 2.0, 6.0)]          # 2 × 1.2 > 2.0: the text card drops


def test_a_short_hook_pushes_the_first_switch_to_the_minimum():
    segs = vid.timeline(_table([0.0, 0.3, 5.0]), 4, 8.0, 4.0)
    assert segs[0] == S(0, 0.0, vid.MIN_SEGMENT_SECONDS)
    assert segs[1].start == pytest.approx(1.2) and segs[2].start == 5.0


def test_a_late_last_line_is_pulled_back_instead_of_dropping_its_card():
    segs = vid.timeline(_table([0.0, 1.6, 3.0, 5.0]), 5, 6.0, 4.0)
    assert [s.card for s in segs] == [0, 1, 2, 3, 4]
    assert segs[3].start == pytest.approx(4.8)               # 5.0 would leave only 1.0 s
    _assert_well_formed(segs, 5, 6.0, 4.0)


def test_too_many_cards_for_the_time_drop_the_trailing_ones_deterministically():
    starts = [0.0] + [1.0 + 0.5 * i for i in range(10)]     # ten 0.5 s lines
    a = vid.timeline(_table(starts, word_len=0.2), 12, 6.0, 4.0)
    b = vid.timeline(_table(starts, word_len=0.2), 12, 6.0, 4.0)
    assert a == b
    # ⌊6.0 / 1.2⌋ − 1 = 4 text cards fit; cards 5..10 are never referenced
    assert [s.card for s in a] == [0, 1, 2, 3, 4, 11]
    _assert_well_formed(a, 12, 6.0, 4.0)


def test_only_lines_with_words_are_grouped():
    words = _table([0.0, 2.0]) + [W("x", 8.0, 8.4, 3)]      # line 2 has no words
    segs = vid.timeline(words, 4, 12.0, 4.0)
    assert [s.start for s in segs] == [0.0, 2.0, 8.0, 12.0]


def test_no_words_or_only_a_hook_is_the_brand_card_alone():
    assert vid.timeline([], 6, 6.0, 4.0) == [S(0, 0.0, 6.0), S(5, 6.0, 10.0)]
    assert vid.timeline(_table([0.0]), 6, 6.0, 4.0) == [S(0, 0.0, 6.0), S(5, 6.0, 10.0)]


def test_word_order_inside_a_line_and_duplicate_words_do_not_matter():
    words = _table([0.0, 2.0, 6.0])
    shuffled = list(reversed(words)) + [dict(words[2]), dict(words[3])]
    assert vid.timeline(shuffled, 4, 10.0, 4.0) == vid.timeline(words, 4, 10.0, 4.0)


def test_lines_out_of_order_are_refused():
    words = [W("a", 0.0, 0.4, 0), W("b", 5.0, 5.4, 1), W("c", 2.0, 2.4, 2)]
    with pytest.raises(ValueError, match="line order"):
        vid.timeline(words, 4, 8.0, 4.0)
    with pytest.raises(ValueError, match="line order"):         # two lines, one start
        vid.timeline([W("a", 1.0, 1.4, 1), W("b", 1.0, 1.4, 2)], 4, 8.0, 4.0)
    with pytest.raises(ValueError, match="line order"):         # hook after line 1
        vid.timeline([W("a", 3.0, 3.4, 0), W("b", 1.0, 1.4, 1)], 4, 8.0, 4.0)


@pytest.mark.parametrize("n_cards,narration,tail", [
    (1, 5.0, 4.0), (0, 5.0, 4.0), (True, 5.0, 4.0), (3.0, 5.0, 4.0),
    (3, 0.0, 4.0), (3, -1.0, 4.0), (3, float("nan"), 4.0), (3, float("inf"), 4.0),
    (3, 5.0, 0.0), (3, 5.0, -4.0), (3, 5.0, float("nan")), (3, "5", 4.0),
])
def test_bad_arguments_are_refused(n_cards, narration, tail):
    with pytest.raises(ValueError):
        vid.timeline(_table([0.0, 2.0]), n_cards, narration, tail)


@pytest.mark.parametrize("word", [
    W("late", 5.6, 5.8, 1),                  # past D + 0.5
    W("late", 4.0, 5.6, 1),                  # ends past D + 0.5
    W("neg", -0.1, 0.2, 1),
    W("rev", 2.0, 1.0, 1),
    {"w": "x", "s": 1.0, "e": 1.2},          # no line
    {"w": "x", "e": 1.2, "line": 1},         # no start
    W("x", "1.0", 1.2, 1),                   # a string time
    W("x", float("nan"), 1.2, 1),
    W("x", 1.0, 1.2, True),                  # a bool line
    W("x", 1.0, 1.2, -1),
    W("x", 1.0, 1.2, 1.0),                   # a float line
    "not a mapping",
])
def test_malformed_words_are_refused(word):
    with pytest.raises(ValueError):
        vid.timeline([W("ok", 0.0, 0.4, 0), word], 4, 5.0, 4.0)


@pytest.mark.parametrize("words", [None, "words", {"w": "a", "s": 0, "e": 1, "line": 0}])
def test_a_word_table_that_is_not_a_sequence_is_refused(words):
    with pytest.raises(ValueError, match="sequence"):
        vid.timeline(words, 4, 5.0, 4.0)


def test_a_word_just_inside_the_overrun_allowance_is_accepted():
    segs = vid.timeline([W("a", 0.0, 0.4, 0), W("b", 3.0, 5.5, 1)], 3, 5.0, 4.0)
    assert segs[-1] == S(2, 5.0, 9.0)


def test_an_exact_narration_is_kept_exact_and_internal_bounds_are_whole_milliseconds():
    d = 17.123456789
    segs = vid.timeline(_table([0.0, 2.0004, 7.3337]), 4, d, 4.0)
    assert segs[-1].start == d and segs[-1].end == d + 4.0
    for s in segs[1:-1]:
        assert s.start * 1000 == pytest.approx(round(s.start * 1000), abs=1e-6)


def test_many_cards_are_fast_and_capped_by_time():
    segs = vid.timeline(_table([0.0] + [1.0 + i for i in range(60)]), 5000, 62.0, 4.0)
    assert len(segs) - 2 == math.floor(62.0 / vid.MIN_SEGMENT_SECONDS) - 1
    _assert_well_formed(segs, 5000, 62.0, 4.0)


def test_timeline_invariants_hold_over_random_tables():
    rng = random.Random(20260929)
    for _ in range(600):
        n_lines = rng.randint(0, 14)
        t, words = 0.0, []
        for line in range(n_lines + 1):
            if line == 0 and rng.random() < 0.2:
                continue                                    # sometimes no hook
            for _w in range(rng.randint(1, 6)):
                d = rng.uniform(0.12, 0.6)
                words.append(W("w", round(t, 3), round(t + d, 3), line))
                t += d
            t += 0.28
        narration = max(t - 0.28, 0.05) + rng.uniform(0, 0.3)
        n_cards = rng.randint(2, 16)
        segs = vid.timeline(words, n_cards, narration, 4.0)
        _assert_well_formed(segs, n_cards, narration, 4.0)
        lines = {w["line"] for w in words if w["line"] >= 1}
        expected_text = (max(min(n_cards - 2, math.floor(narration * 1000) // 1200 - 1), 0)
                         if lines else 0)
        assert len(segs) - 2 == expected_text
        if narration >= 1.0 / vid.FPS:
            vid.build_argv(ffmpeg="ffmpeg", card_files=[f"c{s.card}.png" for s in segs],
                           segments=segs, audio_file="a.m4a", ass_file="c.ass", fonts_dir="fonts",
                           out_file="v.mp4", threads=2)


# ── build_argv ───────────────────────────────────────────────────────────────


SEGS = [S(0, 0.0, 2.5), S(1, 2.5, 6.25), S(2, 6.25, 9.0), S(3, 9.0, 13.0)]


def _argv(segs=SEGS, **kw) -> List[str]:
    args = dict(ffmpeg="/usr/bin/ffmpeg", card_files=[f"card{s.card:02d}.png" for s in segs],
                segments=segs, audio_file="narration.m4a", ass_file="captions.ass", fonts_dir="fonts",
                out_file="video.mp4", threads=2)
    args.update(kw)
    return vid.build_argv(**args)


def _graph(argv: List[str]) -> str:
    return argv[argv.index("-filter_complex") + 1]


def _opt(argv: List[str], name: str) -> str:
    return argv[argv.index(name) + 1]


def test_argv_never_cuts_the_disclaimer_and_never_loops_the_png_input():
    argv = _argv()
    graph = _graph(argv)
    assert "-shortest" not in argv and "shortest" not in graph
    assert "-loop" not in argv and "-stream_loop" not in argv
    assert graph.count("loop=loop=") == len(SEGS) and "size=1" in graph
    assert "[4:a]apad=whole_dur=13.000[aout]" in graph        # the audio covers the tail
    assert _opt(argv, "-t") == "13.000"                        # an OUTPUT option: after every -i
    assert argv.index("-t") > max(i for i, a in enumerate(argv) if a == "-i")


def test_each_stream_has_exact_frames_and_xfade_offsets_are_the_boundaries():
    argv = _argv()
    graph = _graph(argv)
    loops = [int(x) + 1 for x in re.findall(r"loop=loop=(\d+):", graph)]
    frames = [round(s.start * 30) for s in SEGS] + [round(SEGS[-1].end * 30)]
    own = [b - a for a, b in zip(frames, frames[1:])]
    assert loops == [o + vid.XFADE_FRAMES for o in own[:-1]] + [own[-1]]
    assert sum(own) == round(13.0 * 30)
    offsets = [float(x) for x in re.findall(r"xfade=transition=fade:duration=0\.300:offset=([0-9.]+)", graph)]
    assert len(offsets) == len(SEGS) - 1
    for off, seg in zip(offsets, SEGS[1:]):
        assert abs(off - seg.start) <= 0.5 / vid.FPS + 1e-3
    # the accumulated stream feeding each xfade lasts boundary + XFADE (the arithmetic)
    for i, seg in enumerate(SEGS[:-1]):
        assert (frames[i] + loops[i]) / 30 == pytest.approx(SEGS[i + 1].start + vid.XFADE_SECONDS, abs=1 / 60)


def test_argv_carries_the_output_profile_and_the_bitexact_flags():
    argv = _argv(threads=3)
    joined = " ".join(argv)
    for flag in ("-c:v libx264", "-profile:v high", "-preset veryfast", "-crf 20", "-bf 3",
                 "-pix_fmt yuv420p", "-r 30", "-g 60", "-sc_threshold 0", "-c:a aac", "-b:a 160k",
                 "-ar 48000", "-ac 2", "-movflags +faststart", "-use_editlist 0", "-map_metadata -1",
                 "-fflags +bitexact", "-flags:v +bitexact", "-flags:a +bitexact", "-threads 3",
                 "-filter_complex_threads 3", "-nostdin", "-f mp4", "-colorspace bt709",
                 f"comment={vid.VIDEO_RENDER_VERSION}"):
        assert flag in joined, flag
    assert argv[-1] == "video.mp4"
    assert _graph(argv).endswith("[x3]ass=filename=captions.ass:fontsdir=fonts[vout];[4:a]apad=whole_dur=13.000[aout]")
    assert argv.count("-framerate") == len(SEGS) and _opt(argv, "-framerate") == "30"


def test_argv_is_deterministic_and_prints_fixed_precision_times():
    a, b = _argv(), _argv()
    assert a == b
    odd = [S(0, 0.0, 1.2345678), S(1, 1.2345678, 7.1111111), S(2, 7.1111111, 11.1111111)]
    graph = _graph(_argv(odd))
    for number in re.findall(r"(?:offset|whole_dur|duration)=([0-9.]+)", graph):
        assert re.fullmatch(r"\d+\.\d{3}", number), number


@pytest.mark.parametrize("field", ["card", "audio_file", "ass_file", "fonts_dir", "out_file"])
@pytest.mark.parametrize("bad", ["a:b.png", "a b.png", "a,b", "a'b", "a;b", "a[b", "a]b", "a\\b",
                                 "/abs/x.png", "../x.png", "x/../y", "-x.png", ".hidden", "a%d.png",
                                 "a=b", "", "tab\there", "ünï.png", "a//b"])
def test_only_plain_relative_names_reach_ffmpeg(field, bad):
    kw = {}
    if field == "card":
        kw["card_files"] = ["card00.png", bad, "card02.png", "card03.png"]
    else:
        kw[field] = bad
    with pytest.raises(ValueError):
        _argv(**kw)


def test_nested_relative_names_are_allowed():
    argv = _argv(card_files=["cards/c0.png", "cards/c1.png", "cards/c2.png", "cards/c3.png"],
                 fonts_dir="assets/fonts")
    assert "fontsdir=assets/fonts" in _graph(argv)


@pytest.mark.parametrize("segs,match", [
    ([], "no segments"),
    ([S(0, 0.5, 2.0), S(1, 2.0, 6.0)], "starts at"),
    ([S(0, 0.0, 2.0), S(1, 2.1, 6.0)], "contiguous"),
    ([S(0, 0.0, 2.0), S(1, 2.0, 2.0)], "empty"),
    ([S(0, 0.0, 2.0), S(1, 2.0, 1.0)], "empty"),
    ([S(0, 0.0, 0.01), S(1, 0.01, 4.0)], "one frame"),
    ([S(0, 0.0, 2.0), S(1, 2.0, 2.2)], "fade-in"),        # 6 frames < the 9-frame fade
    ([S(0, 0.0, float("inf")), S(1, float("inf"), 5.0)], "finite"),
])
def test_bad_timelines_are_refused(segs, match):
    with pytest.raises(ValueError, match=match):
        _argv(segs, card_files=[f"c{i}.png" for i in range(len(segs))])


@pytest.mark.parametrize("kw", [
    {"threads": 0}, {"threads": True}, {"threads": 1.5}, {"threads": -2},
    {"card_files": ["a.png"]},                             # not parallel to the segments
    {"out_file": "narration.m4a"},                         # would clobber an input
    {"out_file": "card01.png"},
    {"ffmpeg": ""},
])
def test_bad_arguments_to_build_argv_are_refused(kw):
    with pytest.raises(ValueError):
        _argv(**kw)


def test_a_card_shown_twice_is_simply_decoded_twice():
    segs = [S(0, 0.0, 2.0), S(1, 2.0, 4.0), S(0, 4.0, 6.0), S(3, 6.0, 10.0)]
    argv = _argv(segs, card_files=["a.png", "b.png", "a.png", "d.png"])
    assert argv.count("a.png") == 2 and "[4:a]apad" in _graph(argv)


def test_threads_for_and_the_stage_worst_case():
    assert vid.threads_for(None) == vid.DEFAULT_THREADS
    assert vid.threads_for(0) == vid.DEFAULT_THREADS and vid.threads_for(-3) == vid.DEFAULT_THREADS
    assert vid.threads_for(True) == vid.DEFAULT_THREADS
    assert vid.threads_for(1) == 1 and vid.threads_for(3) == 3
    assert vid.threads_for(64) == vid.MAX_THREADS
    worst = vid.worst_case_seconds(120.0)
    assert worst == (vid.RENDER_TIMEOUT_SECONDS + 2 * vid.PROBE_TIMEOUT_SECONDS
                     + vid.DOWNLOAD_TIMEOUT_SECONDS + 120.0 + vid.CARD_RENDER_SECONDS)
    # main.STAGE_START_MARGIN_SECONDS is 12 min (pinned against this in test_marketing_worker.py)
    assert worst <= 12 * 60


# ── ffprobe parse + gate ─────────────────────────────────────────────────────


def _payload(**over) -> dict:
    video = {"codec_type": "video", "codec_name": "h264", "profile": "High", "width": 1080,
             "height": 1920, "pix_fmt": "yuv420p", "r_frame_rate": "30/1", "avg_frame_rate": "30/1",
             "duration": "13.000000"}
    audio = {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2,
             "duration": "13.066667"}
    fmt = {"duration": "13.066667", "size": "4200000"}
    video.update(over.pop("video", {}))
    audio.update(over.pop("audio", {}))
    fmt.update(over.pop("format", {}))
    return {"streams": [video, audio], "format": fmt, **over}


GOOD = vid.parse_ffprobe(_payload())


def _gate(p: vid.ProbeResult, *, expected=13.0, max_seconds=75.0, max_bytes=250 * 1024 * 1024) -> None:
    vid.check_probe(p, expected_seconds=expected, max_seconds=max_seconds, max_bytes=max_bytes)


def test_a_good_probe_parses_and_passes():
    assert GOOD == vid.ProbeResult(1080, 1920, "yuv420p", "h264", "High", "30/1", "30/1", "aac",
                                   48000, 2, 13.066667, 4200000)
    _gate(GOOD)


def test_probe_argv_is_json_streams_and_format():
    argv = vid.probe_argv("/usr/bin/ffprobe", "video.mp4")
    assert argv[0] == "/usr/bin/ffprobe" and argv[-2:] == ["-i", "video.mp4"]
    assert "json" in argv and "-show_streams" in argv and "-show_format" in argv


@pytest.mark.parametrize("payload", [{}, None, [], "junk", {"streams": "nope"}, {"streams": [1, None]},
                                     {"format": []}, {"streams": [{}], "format": {"duration": "N/A"}}])
def test_parse_is_tolerant_of_any_shape(payload):
    p = vid.parse_ffprobe(payload)
    assert p.width is None and p.audio_codec is None and p.duration is None and p.size is None


def test_parse_skips_cover_art_and_falls_back_to_stream_durations():
    art = {"codec_type": "video", "codec_name": "png", "width": 600, "height": 600,
           "disposition": {"attached_pic": 1}}
    payload = _payload(format={"duration": "N/A"})
    payload["streams"].insert(0, art)
    p = vid.parse_ffprobe(payload)
    assert p.video_codec == "h264" and p.width == 1080
    assert p.duration == pytest.approx(13.066667)          # max of the stream durations


def test_parse_reads_odd_scalar_types_as_none_not_crashes():
    p = vid.parse_ffprobe(_payload(video={"width": "wide", "height": True},
                                   audio={"sample_rate": 48000.5, "channels": "2"},
                                   format={"size": "12.5", "duration": "inf"}))
    assert p.width is None and p.height is None and p.sample_rate is None and p.channels == 2
    assert p.size is None and p.duration == pytest.approx(13.066667)


@pytest.mark.parametrize("over,needle", [
    ({"streams": [{"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2}]},
     "no video stream"),
    ({"video": {"avg_frame_rate": "2997/100"}}, "avg_frame_rate 2997/100"),
    ({"video": {"r_frame_rate": "30000/1001", "avg_frame_rate": "30000/1001"}}, "r_frame_rate 30000/1001"),
    ({"video": {"avg_frame_rate": "172800/5801"}}, "avg_frame_rate"),   # a stretched first frame
    ({"video": {"avg_frame_rate": "0/0"}}, "avg_frame_rate"),
    ({"video": {"pix_fmt": "yuv444p"}}, "pix_fmt yuv444p"),
    ({"video": {"width": 1920, "height": 1080}}, "size 1920x1080"),
    ({"video": {"codec_name": "hevc"}}, "video codec hevc"),
    ({"video": {"profile": "Main"}}, "profile Main"),
    ({"audio": {"sample_rate": "44100"}}, "sample rate 44100"),
    ({"audio": {"channels": 1}}, "channels 1"),
    ({"audio": {"codec_name": "mp3"}}, "audio codec mp3"),
    ({"format": {"duration": "9.0"}}, "not the expected"),              # the tail was cut
    ({"format": {"duration": "13.3"}}, "not the expected"),             # off by > tolerance
    ({"format": {"size": "0"}}, "size 0"),
])
def test_the_gate_refuses_each_outlier(over, needle):
    payload = _payload(**{k: v for k, v in over.items() if k != "streams"})
    if "streams" in over:
        payload["streams"] = over["streams"]
    with pytest.raises(vid.RenderRejected, match=re.escape(needle)):
        _gate(vid.parse_ffprobe(payload))


def test_the_gate_refuses_no_audio_over_cap_and_oversize_and_lists_every_problem():
    p = vid.parse_ffprobe(_payload())
    p.audio_codec = None
    p.pix_fmt = "yuv444p"
    p.size = 300 * 1024 * 1024
    with pytest.raises(vid.RenderRejected) as e:
        _gate(p, max_bytes=250 * 1024 * 1024)
    msg = str(e.value)
    assert "no audio stream" in msg and "yuv444p" in msg and "over the" in msg and msg.count(";") >= 2
    long = vid.parse_ffprobe(_payload(format={"duration": "80.1"}))
    with pytest.raises(vid.RenderRejected, match="over the 75.000s cap"):
        _gate(long, expected=80.0, max_seconds=75.0)
    none = vid.parse_ffprobe(_payload(format={"duration": "N/A"}, video={"duration": None},
                                      audio={"duration": None}))
    with pytest.raises(vid.RenderRejected, match="no duration"):
        _gate(none)


def test_the_cap_allows_only_container_overhead():
    # a narration exactly on the budget: 71 s + 4 s → the container runs ~2 frames past 75 s
    _gate(vid.parse_ffprobe(_payload(format={"duration": "75.083333"})), expected=75.0, max_seconds=75.0)
    with pytest.raises(vid.RenderRejected, match="cap"):
        _gate(vid.parse_ffprobe(_payload(format={"duration": "75.2"})), expected=75.0, max_seconds=75.0)


@pytest.mark.parametrize("caps", [
    {"max_seconds": float("nan")}, {"max_seconds": 0}, {"max_seconds": -5.0}, {"max_seconds": True},
    {"expected": float("inf")}, {"max_bytes": 0}, {"max_bytes": None},
])
def test_an_invalid_cap_is_itself_a_rejection_never_a_pass(caps):
    with pytest.raises(vid.RenderRejected, match="invalid"):
        _gate(GOOD, **caps)


# ── moov_before_mdat ─────────────────────────────────────────────────────────


def _box(kind: bytes, payload: bytes = b"", size: Optional[int] = None) -> bytes:
    return struct.pack(">I4s", len(payload) + 8 if size is None else size, kind) + payload


def _write(tmp_path: Path, data: bytes) -> str:
    p = tmp_path / "x.mp4"
    p.write_bytes(data)
    return str(p)


def test_moov_first_and_mdat_first(tmp_path):
    ftyp = _box(b"ftyp", b"isom\x00\x00\x02\x00")
    assert vid.moov_before_mdat(_write(tmp_path, ftyp + _box(b"moov", b"m" * 40) + _box(b"mdat", b"d" * 100)))
    assert not vid.moov_before_mdat(_write(tmp_path, ftyp + _box(b"mdat", b"d" * 100) + _box(b"moov", b"m" * 40)))
    assert not vid.moov_before_mdat(_write(tmp_path, ftyp + _box(b"moov", b"m" * 40)))   # no mdat at all
    assert vid.moov_before_mdat(_write(tmp_path, ftyp + _box(b"free", b"") + _box(b"moov", b"m" * 8)
                                       + _box(b"mdat", b"d" * 3)))


def test_largesize_and_to_eof_boxes(tmp_path):
    ftyp = _box(b"ftyp", b"isom\x00\x00\x02\x00")
    big_mdat = struct.pack(">I4sQ", 1, b"mdat", 16 + 50) + b"d" * 50
    assert vid.moov_before_mdat(_write(tmp_path, ftyp + _box(b"moov", b"m" * 8) + big_mdat))
    big_moov = struct.pack(">I4sQ", 1, b"moov", 16 + 20) + b"m" * 20
    assert vid.moov_before_mdat(_write(tmp_path, ftyp + big_moov + _box(b"mdat", b"d" * 4)))
    to_eof = struct.pack(">I4s", 0, b"mdat") + b"d" * 64
    assert vid.moov_before_mdat(_write(tmp_path, ftyp + _box(b"moov", b"m" * 8) + to_eof))
    assert not vid.moov_before_mdat(_write(tmp_path, ftyp + to_eof))


@pytest.mark.parametrize("data", [
    b"",
    b"\x00\x00",
    _box(b"ftyp", b"isom") + b"\x00\x00\x00",                              # truncated header
    _box(b"ftyp", b"isom") + struct.pack(">I4s", 4, b"moov") + b"x" * 20,  # size < 8
    _box(b"ftyp", b"isom") + struct.pack(">I4s", 500, b"moov") + b"x" * 20,  # runs past EOF
    _box(b"ftyp", b"isom") + struct.pack(">I4s", 1, b"moov") + b"\x00\x00",  # truncated largesize
    _box(b"ftyp", b"isom") + struct.pack(">I4sQ", 1, b"moov", 8) + b"x" * 8,  # largesize < 16
    _box(b"ftyp", b"isom") + _box(b"moov", b"m" * 8) + struct.pack(">I4s", 400, b"mdat") + b"d",  # truncated mdat
    b"\xff" * 64,
])
def test_malformed_files_are_false_never_an_exception(tmp_path, data):
    assert vid.moov_before_mdat(_write(tmp_path, data)) is False


def test_a_missing_file_is_false(tmp_path):
    assert vid.moov_before_mdat(str(tmp_path / "nope.mp4")) is False
    assert vid.moov_before_mdat(str(tmp_path)) is False                    # a directory


def test_a_long_chain_of_boxes_is_bounded(tmp_path):
    data = _box(b"free") * (vid._MAX_BOXES + 10) + _box(b"moov") + _box(b"mdat")
    assert vid.moov_before_mdat(_write(tmp_path, data)) is False


# ── run_ffmpeg (fake process, fake clock) ────────────────────────────────────


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Proc:
    def __init__(self, clock: _Clock, *, finish_after: float, rc: int, stderr: bytes, fh) -> None:
        self.clock, self.rc = clock, rc
        self.done_at = clock.now + finish_after
        self.returncode: Optional[int] = None
        self.killed = False
        fh.write(stderr)
        fh.flush()

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        if timeout is None or self.clock.now + timeout >= self.done_at:
            self.clock.now = max(self.clock.now, self.done_at)
            self.returncode = self.rc
            return self.rc
        self.clock.now += timeout
        raise subprocess.TimeoutExpired("ffmpeg", timeout)

    def poll(self):
        # Like a real child: exited once its time has come, whether or not anyone waited.
        if self.returncode is None and self.clock.now >= self.done_at:
            self.returncode = self.rc
        return self.returncode

    def kill(self):
        self.killed = True
        if self.returncode is None:
            self.returncode = -9


def _fake(monkeypatch, *, finish_after=10.0, rc=0, stderr=b""):
    clock = _Clock()
    procs: List[_Proc] = []

    def popen(argv, **kw):
        assert kw["cwd"] == "/work" and kw["stdin"] == subprocess.DEVNULL
        procs.append(_Proc(clock, finish_after=finish_after, rc=rc, stderr=stderr, fh=kw["stderr"]))
        return procs[-1]

    monkeypatch.setattr(vid, "_popen", popen)
    monkeypatch.setattr(vid, "_monotonic", clock)
    return clock, procs


def test_a_clean_run_returns_and_beats_while_it_works(monkeypatch):
    _clock, procs = _fake(monkeypatch, finish_after=200.0)
    beats = []
    vid.run_ffmpeg(["ffmpeg", "-i", "x"], cwd="/work", heartbeat=lambda: beats.append(1), run_id="r1")
    assert len(beats) == 3 and not procs[0].killed          # at ~60, 120, 180 s


def test_a_render_that_finished_during_a_slow_heartbeat_is_not_a_timeout(monkeypatch):
    """ffmpeg exits 0 at 270 s while the heartbeat started at 240 s is stuck on a slow backend
    until 310 s — past the 300 s deadline. The finished render must be kept: it used to be
    reported as 'timed out and was killed' and thrown away (review 2026-09-29)."""
    clock, procs = _fake(monkeypatch, finish_after=270.0)
    beats = []

    def slow_beat():
        beats.append(clock.now)
        if len(beats) == 4:
            clock.now += 70.0                    # a backend retry chain

    vid.run_ffmpeg(["ffmpeg"], cwd="/work", timeout=300, heartbeat=slow_beat, run_id="r-9")
    assert not procs[0].killed and procs[0].returncode == 0


def test_a_hung_ffmpeg_is_killed_at_the_timeout(monkeypatch):
    clock, procs = _fake(monkeypatch, finish_after=10 ** 9, stderr=b"frame=  12 still going")
    beats = []
    start = clock.now
    with pytest.raises(vid.RenderTimeout, match="run r-7.*timed out after 300s.*still going") as e:
        vid.run_ffmpeg(["ffmpeg"], cwd="/work", timeout=300, heartbeat=lambda: beats.append(1), run_id="r-7")
    assert isinstance(e.value, vid.RenderFailed)
    assert procs[0].killed and clock.now - start == pytest.approx(300.0)
    assert len(beats) >= 4


@pytest.mark.parametrize("rc", [-9, 137])
def test_a_sigkilled_ffmpeg_is_oom(monkeypatch, rc):
    _fake(monkeypatch, rc=rc, stderr=b"Killed")
    with pytest.raises(vid.RenderOOM, match=f"run r2: ffmpeg was killed \\(exit {rc}\\)") as e:
        vid.run_ffmpeg(["ffmpeg"], cwd="/work", run_id="r2")
    assert isinstance(e.value, vid.RenderFailed)


def test_a_failed_ffmpeg_carries_the_stderr_tail_and_the_run_id(monkeypatch, caplog):
    _fake(monkeypatch, rc=1, stderr=("x" * 5000 + " Error applying option 'fontsdir' — the real cause").encode())
    with caplog.at_level(logging.ERROR, logger="marketing.video"):
        with pytest.raises(vid.RenderFailed) as e:
            vid.run_ffmpeg(["ffmpeg"], cwd="/work", run_id="run-42")
    msg = str(e.value)
    assert not isinstance(e.value, (vid.RenderOOM, vid.RenderTimeout))
    assert "run run-42" in msg and "exited 1" in msg and msg.endswith("the real cause")
    assert len(msg) < 700
    assert any("run-42" in r.getMessage() for r in caplog.records)


def test_another_signal_is_a_plain_failure(monkeypatch):
    _fake(monkeypatch, rc=-11, stderr=b"")
    with pytest.raises(vid.RenderFailed, match="exited -11") as e:
        vid.run_ffmpeg(["ffmpeg"], cwd="/work", run_id="r")
    assert not isinstance(e.value, vid.RenderOOM)


def test_undecodable_stderr_is_replaced_not_raised(monkeypatch):
    _fake(monkeypatch, rc=1, stderr=b"\xff\xfe bad bytes \x80")
    with pytest.raises(vid.RenderFailed, match="bad bytes"):
        vid.run_ffmpeg(["ffmpeg"], cwd="/work", run_id="r")


def test_a_failing_heartbeat_is_logged_and_the_render_goes_on(monkeypatch, caplog):
    _fake(monkeypatch, finish_after=130.0)

    def boom():
        raise RuntimeError("api down")

    with caplog.at_level(logging.WARNING, logger="marketing.video"):
        vid.run_ffmpeg(["ffmpeg"], cwd="/work", heartbeat=boom, run_id="r9")
    assert any("heartbeat failed run_id=r9" in r.getMessage() and "api down" in r.getMessage()
               for r in caplog.records)


def test_an_interrupt_never_leaves_ffmpeg_running(monkeypatch):
    _clock, procs = _fake(monkeypatch, finish_after=10 ** 9)

    def interrupt():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        vid.run_ffmpeg(["ffmpeg"], cwd="/work", heartbeat=interrupt, run_id="r")
    assert procs[0].killed


def test_ffmpeg_that_cannot_start_is_a_render_failure(monkeypatch):
    def popen(argv, **kw):
        raise FileNotFoundError(2, "No such file", argv[0])

    monkeypatch.setattr(vid, "_popen", popen)
    with pytest.raises(vid.RenderFailed, match="run r: could not start ffmpeg: FileNotFoundError"):
        vid.run_ffmpeg(["/nope/ffmpeg"], cwd="/work", run_id="r")
    with pytest.raises(ValueError):
        vid.run_ffmpeg([], cwd="/work")


# ── render_video around the runner (fakes) ───────────────────────────────────


def _png(path: Path, size=(1080, 1920), colour=(23, 27, 38)) -> None:
    from PIL import Image

    Image.new("RGB", size, colour).save(path)


def _workdir(tmp_path: Path, segs=None) -> dict:
    segs = segs or [S(0, 0.0, 2.0), S(1, 2.0, 6.0)]
    for s in segs:
        _png(tmp_path / f"card{s.card:02d}.png")
    (tmp_path / "narration.m4a").write_bytes(b"not really audio")
    (tmp_path / "captions.ass").write_text("[Script Info]\n", encoding="utf-8")
    (tmp_path / "fonts").mkdir()
    (tmp_path / "fonts" / "Inter-Bold.ttf").write_bytes(b"face")
    return dict(workdir=str(tmp_path), card_files=[f"card{s.card:02d}.png" for s in segs], segments=segs,
                audio_file="narration.m4a", ass_file="captions.ass", fonts_dir="fonts", threads=2,
                ffmpeg=sys.executable, ffprobe=sys.executable, run_id="rv", max_seconds=75.0)


_MOOV_FIRST = _box(b"ftyp", b"isom\x00\x00\x02\x00") + _box(b"moov", b"m" * 16) + _box(b"mdat", b"d" * 64)
_MDAT_FIRST = _box(b"ftyp", b"isom\x00\x00\x02\x00") + _box(b"mdat", b"d" * 64) + _box(b"moov", b"m" * 16)


def _fake_render(monkeypatch, tmp_path: Path, *, out_bytes: Optional[bytes], out_probe: vid.ProbeResult,
                 narration: Optional[vid.ProbeResult] = None) -> list:
    calls = []

    def probe(ffprobe, path, *, cwd, run_id):
        calls.append(path)
        if path == "narration.m4a":
            return narration or vid.ProbeResult(audio_codec="aac", sample_rate=48000, channels=2, duration=2.0)
        return out_probe

    def run(argv, *, cwd, timeout, heartbeat, run_id):
        calls.append("ffmpeg")
        if out_bytes is not None:
            (tmp_path / argv[-1]).write_bytes(out_bytes)

    monkeypatch.setattr(vid, "_probe", probe)
    monkeypatch.setattr(vid, "run_ffmpeg", run)
    return calls


def _good_probe(duration=6.0) -> vid.ProbeResult:
    return vid.ProbeResult(1080, 1920, "yuv420p", "h264", "High", "30/1", "30/1", "aac", 48000, 2,
                           duration, None)


def test_render_video_returns_the_bytes_and_the_probe(monkeypatch, tmp_path):
    kw = _workdir(tmp_path)
    calls = _fake_render(monkeypatch, tmp_path, out_bytes=_MOOV_FIRST, out_probe=_good_probe())
    data, probe = vid.render_video(**kw)
    assert data == _MOOV_FIRST and probe.size == len(_MOOV_FIRST)       # size = the file's
    assert calls == ["narration.m4a", "ffmpeg", "video.mp4"]


def test_render_video_rejects_a_trailing_moov(monkeypatch, tmp_path):
    kw = _workdir(tmp_path)
    _fake_render(monkeypatch, tmp_path, out_bytes=_MDAT_FIRST, out_probe=_good_probe())
    with pytest.raises(vid.RenderRejected, match="run rv: moov is not before mdat"):
        vid.render_video(**kw)


def test_render_video_rejects_a_cut_tail_with_the_run_id(monkeypatch, tmp_path):
    kw = _workdir(tmp_path)
    _fake_render(monkeypatch, tmp_path, out_bytes=_MOOV_FIRST, out_probe=_good_probe(duration=2.0))
    with pytest.raises(vid.RenderRejected, match="run rv: duration 2.000s is not the expected 6.000s"):
        vid.render_video(**kw)


def test_render_video_refuses_a_narration_that_is_not_the_timelines(monkeypatch, tmp_path):
    kw = _workdir(tmp_path)
    calls = _fake_render(monkeypatch, tmp_path, out_bytes=_MOOV_FIRST, out_probe=_good_probe(),
                         narration=vid.ProbeResult(audio_codec="aac", duration=5.0))
    with pytest.raises(vid.RenderFailed, match="narration is 5.000s .* 2.000s"):
        vid.render_video(**kw)
    assert "ffmpeg" not in calls                                        # refused BEFORE the encode
    _fake_render(monkeypatch, tmp_path, out_bytes=_MOOV_FIRST, out_probe=_good_probe(),
                 narration=vid.ProbeResult(video_codec="h264", duration=2.0))
    with pytest.raises(vid.RenderFailed, match="no readable audio stream"):
        vid.render_video(**kw)


def test_render_video_needs_the_tools_and_every_input(monkeypatch, tmp_path):
    kw = _workdir(tmp_path)
    _fake_render(monkeypatch, tmp_path, out_bytes=_MOOV_FIRST, out_probe=_good_probe())
    with pytest.raises(vid.RenderFailed, match="ffmpeg not found"):
        vid.render_video(**{**kw, "ffmpeg": str(tmp_path / "no-ffmpeg")})
    with pytest.raises(vid.RenderFailed, match="ffprobe not found"):
        vid.render_video(**{**kw, "ffprobe": str(tmp_path / "captions.ass")})   # not executable
    (tmp_path / "card01.png").unlink()
    with pytest.raises(vid.RenderFailed, match="card01.png is missing or not a PNG"):
        vid.render_video(**kw)
    _png(tmp_path / "card01.png", size=(1080, 1080))
    with pytest.raises(vid.RenderFailed, match="card01.png is 1080x1080"):
        vid.render_video(**kw)
    _png(tmp_path / "card01.png")
    (tmp_path / "captions.ass").unlink()
    with pytest.raises(vid.RenderFailed, match="captions file captions.ass is missing"):
        vid.render_video(**kw)
    (tmp_path / "captions.ass").write_text("x", encoding="utf-8")
    (tmp_path / "fonts" / "Inter-Bold.ttf").unlink()
    with pytest.raises(vid.RenderFailed, match="holds no .ttf/.otf face"):
        vid.render_video(**kw)
    shutil.rmtree(tmp_path / "fonts")
    with pytest.raises(vid.RenderFailed, match="fonts dir fonts unreadable"):
        vid.render_video(**kw)


def test_an_output_path_that_cannot_be_cleared_is_a_failure(monkeypatch, tmp_path):
    kw = _workdir(tmp_path)
    (tmp_path / "video.mp4").mkdir()
    calls = _fake_render(monkeypatch, tmp_path, out_bytes=_MOOV_FIRST, out_probe=_good_probe())
    with pytest.raises(vid.RenderFailed, match="cannot clear video.mp4"):
        vid.render_video(**kw)
    assert "ffmpeg" not in calls


def test_render_video_with_no_output_file_is_a_failure(monkeypatch, tmp_path):
    kw = _workdir(tmp_path)
    (tmp_path / "video.mp4").write_bytes(_MOOV_FIRST)                    # stale, from an earlier try
    _fake_render(monkeypatch, tmp_path, out_bytes=None, out_probe=_good_probe())
    with pytest.raises(vid.RenderFailed, match="wrote no video.mp4"):
        vid.render_video(**kw)


def test_probe_failures_are_typed(monkeypatch):
    def run(result=None, exc=None):
        def fake(*a, **k):
            if exc:
                raise exc
            return result
        return fake

    cp = subprocess.CompletedProcess
    monkeypatch.setattr(vid.subprocess, "run", run(exc=subprocess.TimeoutExpired("ffprobe", 30)))
    with pytest.raises(vid.RenderFailed, match="ffprobe timed out"):
        vid._probe("ffprobe", "v.mp4", cwd=".", run_id="p")
    monkeypatch.setattr(vid.subprocess, "run", run(cp([], 1, "", "moov atom not found")))
    with pytest.raises(vid.RenderFailed, match="ffprobe exited 1 on v.mp4: moov atom not found"):
        vid._probe("ffprobe", "v.mp4", cwd=".", run_id="p")
    monkeypatch.setattr(vid.subprocess, "run", run(cp([], 0, "{not json", "")))
    with pytest.raises(vid.RenderFailed, match="no JSON"):
        vid._probe("ffprobe", "v.mp4", cwd=".", run_id="p")
    monkeypatch.setattr(vid.subprocess, "run", run(cp([], 0, json.dumps(_payload()), "")))
    assert vid._probe("ffprobe", "v.mp4", cwd=".", run_id="p") == GOOD


# ── ONE real render ──────────────────────────────────────────────────────────


def _pixel(path: Path, at: float) -> bytes:
    """RGB of a 2×2 corner patch of the frame at `at` seconds (away from the captions)."""
    return subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{at:.3f}", "-i", str(path), "-frames:v", "1",
                           "-vf", "crop=2:2:20:20,scale=1:1,format=rgb24", "-f", "rawvideo", "-"],
                          capture_output=True, check=True, timeout=30).stdout[:3]


@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg + ffprobe")
def test_a_real_render_keeps_the_disclaimer_tail_and_is_bit_exact(tmp_path):
    colours = {0: (23, 27, 38), 1: (30, 35, 48), 2: (59, 130, 246)}       # brand, text, disclaimer
    for i, c in colours.items():
        _png(tmp_path / f"card{i:02d}.png", colour=c)
    narration = 2.6
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency=440:sample_rate=48000:duration={narration}", "-ac", "2", "-c:a", "aac",
                    "-b:a", "160k", "-fflags", "+bitexact", "-flags:a", "+bitexact",
                    str(tmp_path / "narration.m4a")], check=True, capture_output=True, timeout=30)
    words = [W("Money", 0.0, 0.4, 0), W("moves.", 0.4, 0.9, 0), W("Start", 1.3, 1.8, 1),
             W("small.", 1.8, 2.5, 1)]
    (tmp_path / "captions.ass").write_text(cap.build_ass(words), encoding="utf-8")
    (tmp_path / "fonts").mkdir()
    shutil.copyfile(_FONT, tmp_path / "fonts" / "Inter-Bold.ttf")
    tail = 1.0
    segs = vid.timeline(words, 3, narration, tail)
    assert segs == [S(0, 0.0, 1.3), S(1, 1.3, 2.6), S(2, 2.6, 3.6)]
    kw = dict(workdir=str(tmp_path), card_files=[f"card{s.card:02d}.png" for s in segs], segments=segs,
              audio_file="narration.m4a", ass_file="captions.ass", fonts_dir="fonts", threads=2,
              run_id="smoke", max_seconds=75.0)
    data, probe = vid.render_video(**kw)
    vid.check_probe(probe, expected_seconds=3.6, max_seconds=75.0, max_bytes=10 ** 9)
    assert probe.r_frame_rate == "30/1" and probe.avg_frame_rate == "30/1"   # CFR, no stretched frame
    # the tail is really there — the anti-`-shortest` assertion: 2.6 s of audio, 3.6 s of file
    assert probe.duration >= 3.6 - 1.0 / vid.FPS
    # …and the container overhead (B-frame start) fits the slack the cap allows
    assert probe.duration <= 3.6 + vid.CONTAINER_SLACK_SECONDS
    counts = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0",
                             str(tmp_path / "video.mp4")], capture_output=True, text=True, timeout=30)
    assert int(counts.stdout.strip()) == round(3.6 * vid.FPS)
    audio_end = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                                "stream=duration", "-of", "csv=p=0", str(tmp_path / "video.mp4")],
                               capture_output=True, text=True, timeout=30)
    assert float(audio_end.stdout.strip()) >= 3.6 - 0.05                   # padded, not cut
    assert vid.moov_before_mdat(str(tmp_path / "video.mp4"))
    # the disclaimer card fills the tail; the brand card the hook (BT.709 round-trip, ±6 per channel)
    for at, colour in ((0.5, colours[0]), (2.0, colours[1]), (3.4, colours[2])):
        got = _pixel(tmp_path / "video.mp4", at)
        assert all(abs(g - c) <= 6 for g, c in zip(got, colour)), (at, tuple(got), colour)
    again, _ = vid.render_video(**{**kw, "out_file": "video2.mp4"})
    assert again == data                                                    # bit-exact
