"""
The video half of the `rendered` stage (Phase 4, SYSTEM_DESIGN_GUIDELINES §12.8): a PURE timeline
and ffmpeg argv builder, a runner with a timeout and a heartbeat, and an ffprobe gate the output
must pass before a single byte is uploaded. Stdlib only (ffmpeg/ffprobe are subprocesses); nothing
here imports app.* (tests/test_marketing_worker.py scans it).

The video: N card PNGs (1080×1920 — the content cards, the first of them on screen from frame 0
through the hook (drop 1: no brand card opens a video any more), the LAST one the disclaimer
card), the narration m4a (AAC 48 kHz stereo) of duration D, and
captions.ass timed on the narration's absolute clock. The output lasts D + tail: the disclaimer
card fills [D, D + tail] and the audio is PADDED with silence (`apad`) to the same length.

Why each piece is shaped the way it is:

* **Never `-shortest`.** The narration ends at D; `-shortest` would end the file there and cut
  the disclaimer card, which is legally required and plays AFTER the narration. `apad` pads the
  audio to the full length and `-t` bounds the output exactly (a test pins both, and the smoke
  render asserts the tail is really in the file).
* **Never `-loop 1`.** It re-decodes the PNG for every frame (measured 33 s of decoding for
  1,800 frames). Each card is decoded ONCE (`-framerate 30 -i card.png`), converted once, and
  repeated by reference with the `loop` filter to an exact frame count.
* **Exact frames, CFR 30.** Every boundary is quantised to the 30 fps grid once, here, so the
  frame counts, the xfade offsets and the total all agree; `setpts=N/(30*TB)` + `fps=30` make
  every stream constant-rate with the same time base (xfade refuses anything else).
* **The xfade arithmetic.** `xfade` passes its first input through until `offset`, blends for
  `duration`, then continues with its second input, whose LOCAL time 0 lands at `offset`; its
  output lasts offset + len(second). So with offset = the boundary b(i+1), segment i+1 appears
  exactly at its boundary on the absolute clock, and the accumulated stream feeding that xfade
  must last b(i+1) + XFADE_SECONDS (it does: every non-last stream carries its own frames plus
  XFADE_FRAMES more, which the fade consumes). The last stream carries exactly its own frames, so
  the video ends at the (frame-quantised) total. The fade therefore runs over the first
  XFADE_SECONDS of each incoming segment — including the disclaimer's first 0.3 s.
* **BT.709.** The RGB cards are converted with the BT.709 matrix (limited range) and the stream
  is tagged bt709: an untagged HD stream is decoded as BT.709 by every player, so a BT.601
  conversion would shift the brand colours. (libass blends the caption colours with BT.601
  coefficients on ffmpeg ≤ 7.0 — a small shift of the highlight blue only; white and the dark
  outline are unaffected.)
* **B-frames stay on (`-bf 3`), on purpose.** With no edit list (`-use_editlist 0`, which
  Instagram requires), the mp4 muxer starts the track with the EARLIEST timestamp at 0 and
  stretches the FIRST sample of every other track to absorb the difference. x264's B-frame delay
  (2 frames = 66.7 ms) is earlier than the AAC priming (1024 samples = 21.3 ms), so the AUDIO's
  first packet is the one stretched and the video stays exactly CFR 30/1 (measured 2026-09-29 on
  ffmpeg 7.0: with `-bf 0` the video's first frame was stretched instead, and avg_frame_rate
  became 172800/5801 — which the gate below refuses). The cost: the container runs ~2 frames
  past the total (`CONTAINER_SLACK_SECONDS`).
* **Bit-exact and deterministic ON ONE HOST**: `+bitexact` everywhere, a bit-exact swscale
  conversion, no metadata but the pipeline version, and a FIXED thread count (x264's bytes depend
  on it) — so a re-render on the same machine produces the same bytes and content address. NOT
  across hosts: the AAC decode/re-encode follows the CPU's SIMD path (measured 2026-09-29 with
  `-cpuflags 0`), so a re-claimed attempt on another machine whose first upload never completed
  mints a second object (orphaned, never posted; a Phase-7 sweep's job). A `ready` video is reused
  by `render_key` regardless of host.
* **Relative names only.** ffmpeg runs with cwd = the job's workdir and every file argument is a
  plain relative name from a small allow-listed alphabet, so no filtergraph escaping is ever
  needed (`:`, `,`, `;`, `[`, `]`, quotes and backslashes are filtergraph syntax).
* **The gate** (`check_probe` + `moov_before_mdat`): 1080×1920, yuv420p, h264 High, CFR 30/1
  (r AND avg frame rate), AAC 48 kHz stereo, the expected duration, under the caps, moov before
  mdat (faststart — the platforms fetch by URL and Instagram refuses a trailing moov). A file that
  fails it is never returned.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("marketing.video")

FPS = 30
SIZE = (1080, 1920)
XFADE_SECONDS = 0.3
#: Frames of one crossfade on the 30 fps grid (9).
XFADE_FRAMES = int(round(XFADE_SECONDS * FPS))
#: The shortest a card stays on screen (except when the whole narration is shorter — see
#: `timeline`): long enough to be read as a card, not a flash.
MIN_SEGMENT_SECONDS = 1.2
#: How far past the narration a word's time may run before the table is refused as not being
#: this narration's (voice.clamp_to_duration keeps a real table inside it).
WORD_OVERRUN_SECONDS = 0.5
RENDER_TIMEOUT_SECONDS = 300
PROBE_TIMEOUT_SECONDS = 30
DOWNLOAD_TIMEOUT_SECONDS = 60
HEARTBEAT_SECONDS = 60
#: Card rendering (Pillow, ≤ ~12 cards) + the caption file, budgeted generously for a slow vCPU.
CARD_RENDER_SECONDS = 30
#: Bump on ANY argv or encoding change: part of the render's reuse key and written into the file
#: (`comment`), so a new pipeline is a new object, never a silent mismatch.
VIDEO_RENDER_VERSION = "x264-high-crf20-bf3-bt709/aac160k-48k/xfade0.3/v1"
#: |probed duration − expected| allowed: the B-frame start (2 frames), frame quantisation
#: (≤ half a frame) and the AAC tail frame, with room to spare.
DURATION_TOLERANCE_SECONDS = 0.25
#: What the container may run past `max_seconds` for the same reasons (≈ 0.083 s measured worst
#: case: 2 frames of B-frame delay + half a frame of quantisation). The narration budget
#: (voice.video_budget_seconds) is what keeps the CONTENT inside the cap; this is only container
#: overhead, so a narration that lands exactly on the budget is not refused for 67 ms of it.
CONTAINER_SLACK_SECONDS = 0.15
DEFAULT_MAX_BYTES = 250 * 1024 * 1024
DEFAULT_THREADS = 2
MAX_THREADS = 4
#: How often the runner wakes to check the clock and heartbeat.
_POLL_SECONDS = 5.0
#: Top-level boxes `moov_before_mdat` will walk before giving up (a real file has < 10).
_MAX_BOXES = 4096

_SAFE_PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z")

# Indirections so tests can replace the process and the clock at the binding this module uses.
_popen = subprocess.Popen
_monotonic = time.monotonic


# ── errors ────────────────────────────────────────────────────────────────────


class RenderFailed(RuntimeError):
    """ffmpeg (or ffprobe) could not produce/read the video: missing tool or input, non-zero
    exit, unreadable probe. Retryable on the next tick."""


class RenderTimeout(RenderFailed):
    """ffmpeg outran RENDER_TIMEOUT_SECONDS and was killed."""


class RenderOOM(RenderFailed):
    """ffmpeg was SIGKILLed (exit -9 / 137) — almost always the container's memory limit."""


class RenderRejected(RuntimeError):
    """ffmpeg produced a file, but it failed the output gate (every problem is in the message)."""


# ── the timeline (pure) ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Segment:
    """Card `card` is on screen over [start, end) on the output's absolute clock (seconds)."""
    card: int
    start: float
    end: float


def _finite(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {type(value).__name__}")
    v = float(value)
    if not math.isfinite(v):
        raise ValueError(f"{name} must be finite, got {v}")
    return v


def _ms(seconds: float) -> int:
    return int(math.floor(seconds * 1000.0 + 0.5))


def _line_starts(words: Sequence[Mapping[str, Any]], narration: float) -> Dict[int, int]:
    """{line: first word start in ms}. Word order inside a line does not matter (the minimum
    start is taken, duplicates are harmless); the LINES must start in line order."""
    if words is None or isinstance(words, (str, bytes, Mapping)):
        raise ValueError(f"words must be a sequence of word mappings, got {type(words).__name__}")
    limit = narration + WORD_OVERRUN_SECONDS
    starts: Dict[int, int] = {}
    for i, w in enumerate(words):
        if not isinstance(w, Mapping):
            raise ValueError(f"word {i} is {type(w).__name__}, not a mapping")
        try:
            raw_s, raw_e, line = w["s"], w["e"], w["line"]
        except KeyError as e:
            raise ValueError(f"word {i} has no {e.args[0]!r}") from None
        s = _finite(f"word {i} start", raw_s)
        e = _finite(f"word {i} end", raw_e)
        if isinstance(line, bool) or not isinstance(line, int) or line < 0:
            raise ValueError(f"word {i} line must be a non-negative int, got {line!r}")
        if s < 0 or e < s:
            raise ValueError(f"word {i} has an invalid window [{s}, {e}]")
        if e > limit:
            raise ValueError(f"word {i} ends at {e:.3f}s, past the {narration:.3f}s narration "
                             f"(+{WORD_OVERRUN_SECONDS}s) — not this narration's word table")
        ms = _ms(s)
        if line not in starts or ms < starts[line]:
            starts[line] = ms
    ordered = sorted(starts)
    for a, b in zip(ordered, ordered[1:]):
        if starts[b] <= starts[a]:
            raise ValueError(f"line {b} starts at {starts[b]} ms, not after line {a} "
                             f"({starts[a]} ms) — the word table is not in line order")
    return starts


def _even_group_starts(lines: List[int], starts: Dict[int, int], k: int) -> List[int]:
    """k ≤ len(lines): contiguous groups as even as possible by line count (the first
    len % k groups carry one extra line); each group starts at its first line's first word."""
    n = len(lines)
    base, extra = divmod(n, k)
    out, idx = [], 0
    for g in range(k):
        out.append(starts[lines[idx]])
        idx += base + (1 if g < extra else 0)
    return out


def _split_line_starts(lines: List[int], starts: Dict[int, int], k: int, end_ms: int) -> List[int]:
    """k > len(lines): every line opens a card, and the k − L extra cards go to the lines in
    proportion to their spoken span (largest remainder, ties to the earlier line); a line with c
    cards is split into c equal time slices."""
    spans = []
    for j, line in enumerate(lines):
        nxt = starts[lines[j + 1]] if j + 1 < len(lines) else end_ms
        spans.append(max(nxt - starts[line], 0))
    extra = k - len(lines)
    counts = [1] * len(lines)
    total = sum(spans)
    if total <= 0:
        for j in range(extra):
            counts[j % len(lines)] += 1
    else:
        quotas = [extra * s / total for s in spans]
        floors = [int(math.floor(q)) for q in quotas]
        for j, f in enumerate(floors):
            counts[j] += f
        left = extra - sum(floors)
        order = sorted(range(len(lines)), key=lambda j: (-(quotas[j] - floors[j]), j))
        for j in order[:left]:
            counts[j] += 1
    out = []
    for j, line in enumerate(lines):
        for i in range(counts[j]):
            out.append(starts[line] + (spans[j] * i) // counts[j])
    return out


def timeline(words: Sequence[Mapping[str, Any]], n_cards: int, narration_seconds: float,
             tail_seconds: float, *, hook_card: bool = True) -> List[Segment]:
    """Which card is on screen when. PURE.

    `n_cards` INCLUDES the disclaimer card (index n_cards − 1). `words` is the narration's word
    table (`{w, s, e, line}`, line 0 = the hook, 1 … L = the script lines). Two openings:

    * `hook_card=False` — what the render uses since drop 1 (2026-10-09: "videos open on the
      content, never on our logo"): there is NO hook card. Cards 0 … n_cards − 2 are the content
      cards; card 0 opens at 0 and stays through the hook AND its own group's lines, so the first
      frame is content. With 6 lines and 3 cards a card still switches on lines 3 and 5 (the
      prompt's "lines 1-2, 3-4 and 5-6"; tests/test_marketing_video.py pins it).
    * `hook_card=True` — the pre-drop-1 shape, kept for callers that still model it: card 0 is a
      card shown over the hook alone (it was the brand card), [0, first word of the first script
      line), and the text cards are 1 … n_cards − 2 (there may be none).

    Either way:

    * The cards after the opening one split the script lines that HAVE words into contiguous
      groups, as evenly as possible by line count (the first len % k groups carry one extra
      line); a card switches at the first word of its group's first line. With more cards than
      lines, every line opens a card and the extra cards split the lines' time (in proportion to
      each line's span). When no script line has words, card 0 covers [0, D] alone.
    * Every segment lasts at least MIN_SEGMENT_SECONDS: a boundary too close to the one before is
      pushed later, then one too close to the next (or to D) is pulled earlier — each by as
      little as needed, so a switch can land a little off its line's first word. When the cards
      cannot ALL get the minimum inside D, the TRAILING cards are dropped — at most ⌊D / MIN⌋
      cards before the disclaimer — and the lines are regrouped over the cards left.
      Deterministic; a dropped card appears in no segment; card 0 and the disclaimer card are
      never dropped. The one exception to the minimum: a narration shorter than 2·MIN shows card 0
      alone over [0, D] (shorter than MIN when D is).
    * The disclaimer segment is exactly [D, D + tail] — `tail` is the caller's (the disclaimer
      card's screen time); `build_argv` needs it ≥ XFADE_SECONDS.

    Segments are contiguous (each end IS the next start), strictly increasing, and cover
    [0, D + tail] exactly. Internal boundaries are whole milliseconds; D is kept exact.

    ValueError: n_cards < 2, D ≤ 0, tail ≤ 0 (or non-finite), a malformed word, a word past
    D + WORD_OVERRUN_SECONDS, or lines that do not start in line order."""
    if isinstance(n_cards, bool) or not isinstance(n_cards, int) or n_cards < 2:
        raise ValueError(f"n_cards must be an int >= 2 (an opening card + the disclaimer), got {n_cards!r}")
    if not isinstance(hook_card, bool):
        raise ValueError(f"hook_card must be a bool, got {hook_card!r}")
    narration = _finite("narration_seconds", narration_seconds)
    tail = _finite("tail_seconds", tail_seconds)
    if narration <= 0:
        raise ValueError(f"narration_seconds must be > 0, got {narration}")
    if tail <= 0:
        raise ValueError(f"tail_seconds must be > 0, got {tail}")
    starts = _line_starts(words, narration)
    lines = [ln for ln in sorted(starts) if ln >= 1]
    min_ms = _ms(MIN_SEGMENT_SECONDS)
    end_ms = int(math.floor(narration * 1000.0 + 1e-6))        # never past D
    # `chosen` = the switch times after card 0 (card i+1 starts at chosen[i]). Every shown card
    # needs ≥ MIN inside D — that is the ONLY reason a card is dropped; the two clamps below
    # always succeed within it.
    chosen: List[int] = []
    if hook_card:
        n_grouped = n_cards - 2               # the text cards; the hook card is card 0
        # k text cards + the hook card: (k + 1)·MIN ≤ D.
        k = max(min(n_grouped, end_ms // min_ms - 1), 0) if lines else 0
        if k:
            chosen = (_even_group_starts(lines, starts, k) if k <= len(lines)
                      else _split_line_starts(lines, starts, k, end_ms))
        shown_grouped = len(chosen)
    else:
        n_grouped = n_cards - 1               # every card before the disclaimer; card 0 opens at 0
        # k cards: k·MIN ≤ D (card 0 is always shown).
        k = max(min(n_grouped, end_ms // min_ms), 1) if lines else 1
        if k > 1:
            groups = (_even_group_starts(lines, starts, k) if k <= len(lines)
                      else _split_line_starts(lines, starts, k, end_ms))
            chosen = groups[1:]               # group 0 starts at 0: the hook is card 0's too
        shown_grouped = len(chosen) + 1
    if chosen:
        prev = 0                              # forward: each ≥ MIN after the previous (card 0 ≥ MIN)
        for i, b in enumerate(chosen):
            chosen[i] = prev = max(b, prev + min_ms)
        nxt = end_ms                          # backward: each ≥ MIN before the next (last ≤ D − MIN)
        for i in range(len(chosen) - 1, -1, -1):
            chosen[i] = nxt = min(chosen[i], nxt - min_ms)
    if n_grouped and shown_grouped < n_grouped:
        logger.info("timeline: %d of %d %s cards fit %.3fs of narration over %d timed lines "
                    "(trailing cards dropped)", shown_grouped, n_grouped,
                    "text" if hook_card else "content", narration, len(lines))
    segments: List[Segment] = []
    prev_t = 0.0
    for card, b in enumerate(chosen):
        t = b / 1000.0
        segments.append(Segment(card, prev_t, t))
        prev_t = t
    segments.append(Segment(len(chosen), prev_t, narration))
    segments.append(Segment(n_cards - 1, narration, narration + tail))
    return segments


# ── the ffmpeg command (pure) ─────────────────────────────────────────────────


def _check_name(label: str, name: Any) -> str:
    """A relative file name ffmpeg can take verbatim, in argv AND inside the filtergraph: path
    parts of [A-Za-z0-9_.-] (not starting with '.' or '-'), joined by '/'. That excludes every
    filtergraph metacharacter (: , ; [ ] ' \\ =), whitespace, '%' (an image2 sequence pattern),
    absolute paths and '..'."""
    if not isinstance(name, str) or not name:
        raise ValueError(f"{label} must be a non-empty relative name, got {name!r}")
    parts = name.split("/")
    if not all(_SAFE_PART.match(p) for p in parts):
        raise ValueError(f"{label} {name!r} is not a plain relative name (allowed: letters, digits, "
                         f"'_', '.', '-' in parts joined by '/'; no leading '.', '-' or '/')")
    return name


def _frame(t: float) -> int:
    return int(math.floor(t * FPS + 0.5))


def _fmt(t: float) -> str:
    return f"{t:.3f}"


def _check_segments(segments: Sequence[Segment]) -> List[int]:
    """Validate a timeline for rendering; return each segment's start frame plus the total frame
    count (len(segments) + 1 values)."""
    if not segments:
        raise ValueError("no segments")
    for i, seg in enumerate(segments):
        if not isinstance(seg, Segment):
            raise ValueError(f"segment {i} is {type(seg).__name__}, not a Segment")
        _finite(f"segment {i} start", seg.start)
        _finite(f"segment {i} end", seg.end)
        if seg.end <= seg.start:
            raise ValueError(f"segment {i} [{seg.start}, {seg.end}] is empty or reversed")
    if abs(segments[0].start) > 1e-9:
        raise ValueError(f"the timeline starts at {segments[0].start}, not 0")
    for i in range(len(segments) - 1):
        if abs(segments[i].end - segments[i + 1].start) > 1e-6:
            raise ValueError(f"segments {i} and {i + 1} are not contiguous "
                             f"({segments[i].end} vs {segments[i + 1].start})")
    frames = [_frame(s.start) for s in segments] + [_frame(segments[-1].end)]
    for i in range(len(segments)):
        own = frames[i + 1] - frames[i]
        if own < 1:
            raise ValueError(f"segment {i} ({segments[i].end - segments[i].start:.4f}s) is shorter "
                             f"than one frame at {FPS} fps")
        if i > 0 and own < XFADE_FRAMES:
            raise ValueError(f"segment {i} ({own} frames) is shorter than its {XFADE_FRAMES}-frame "
                             f"fade-in")
    return frames


def build_argv(*, ffmpeg: str, card_files: Sequence[str], segments: Sequence[Segment],
               audio_file: str, ass_file: str, fonts_dir: str, out_file: str,
               threads: int) -> List[str]:
    """The ONE ffmpeg command for the video. PURE and deterministic.

    `card_files[i]` is the PNG shown in `segments[i]` (parallel lists; one `-i` per segment, so a
    card used twice is simply decoded twice). All file arguments are names RELATIVE to the
    directory ffmpeg runs in (`run_ffmpeg(cwd=…)`). ValueError on any contract breach: a name
    outside the allow-list, a non-contiguous timeline, a segment under one frame, a non-first
    segment shorter than its fade-in, a bad thread count, or an output that is also an input."""
    if not isinstance(ffmpeg, str) or not ffmpeg:
        raise ValueError("ffmpeg must be the executable's path")
    if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
        raise ValueError(f"threads must be an int >= 1, got {threads!r}")
    if len(card_files) != len(segments):
        raise ValueError(f"{len(card_files)} card files for {len(segments)} segments "
                         f"(card_files[i] is segment i's card)")
    cards = [_check_name(f"card_files[{i}]", c) for i, c in enumerate(card_files)]
    audio = _check_name("audio_file", audio_file)
    ass = _check_name("ass_file", ass_file)
    fonts = _check_name("fonts_dir", fonts_dir)
    out = _check_name("out_file", out_file)
    if out in set(cards) | {audio, ass}:
        raise ValueError(f"out_file {out!r} is also an input")
    frames = _check_segments(segments)
    n = len(segments)
    total = segments[-1].end

    prep = ("setsar=1,scale=flags=bicubic+accurate_rnd+full_chroma_int+bitexact"
            ":out_color_matrix=bt709:out_range=tv,format=yuv420p")
    chains: List[str] = []
    for i in range(n):
        own = frames[i + 1] - frames[i]
        count = own + (XFADE_FRAMES if i + 1 < n else 0)
        chains.append(f"[{i}:v]{prep},loop=loop={count - 1}:size=1:start=0,"
                      f"setpts=N/({FPS}*TB),fps={FPS}[v{i}]")
    acc = "v0"
    for i in range(1, n):
        chains.append(f"[{acc}][v{i}]xfade=transition=fade:duration={_fmt(XFADE_SECONDS)}"
                      f":offset={_fmt(frames[i] / FPS)}[x{i}]")
        acc = f"x{i}"
    chains.append(f"[{acc}]ass=filename={ass}:fontsdir={fonts}[vout]")
    chains.append(f"[{n}:a]apad=whole_dur={_fmt(total)}[aout]")

    argv = [ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-loglevel", "error", "-y",
            "-filter_complex_threads", str(threads)]
    for card in cards:
        argv += ["-framerate", str(FPS), "-i", card]
    argv += ["-i", audio,
             "-filter_complex", ";".join(chains),
             "-map", "[vout]", "-map", "[aout]",
             "-c:v", "libx264", "-profile:v", "high", "-preset", "veryfast", "-crf", "20",
             "-bf", "3", "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", str(2 * FPS),
             "-sc_threshold", "0",
             "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
             "-color_range", "tv",
             "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
             "-threads", str(threads),
             "-t", _fmt(total),
             "-map_metadata", "-1", "-metadata", f"comment={VIDEO_RENDER_VERSION}",
             "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
             "-movflags", "+faststart", "-use_editlist", "0",
             "-f", "mp4", out]
    return argv


def threads_for(cpu_quota: Optional[int]) -> int:
    """x264 threads for a CPU quota (whole CPUs): the quota, capped at MAX_THREADS; DEFAULT_THREADS
    when unknown. The result is part of the reuse key — x264's bytes depend on it."""
    if isinstance(cpu_quota, bool) or not isinstance(cpu_quota, int) or cpu_quota < 1:
        return DEFAULT_THREADS
    return min(cpu_quota, MAX_THREADS)


def worst_case_seconds(upload_timeout: float) -> float:
    """The longest the rendered stage can run: the narration download, card rendering, the
    narration pre-probe, the ffmpeg run, the output probe and one signed-upload PUT (the worker's
    timeout, passed in — this module never imports main.py). tests/test_marketing_worker.py pins
    it inside STAGE_START_MARGIN_SECONDS."""
    return (RENDER_TIMEOUT_SECONDS + 2 * PROBE_TIMEOUT_SECONDS + DOWNLOAD_TIMEOUT_SECONDS
            + float(upload_timeout) + CARD_RENDER_SECONDS)


# ── ffprobe (parse + gate: pure) ──────────────────────────────────────────────


@dataclass
class ProbeResult:
    width: Optional[int] = None
    height: Optional[int] = None
    pix_fmt: Optional[str] = None
    video_codec: Optional[str] = None
    profile: Optional[str] = None
    r_frame_rate: Optional[str] = None
    avg_frame_rate: Optional[str] = None
    audio_codec: Optional[str] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    duration: Optional[float] = None
    size: Optional[int] = None


def probe_argv(ffprobe: str, path: str) -> List[str]:
    return [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
            "-i", path]


def _as_int(v: Any) -> Optional[int]:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if math.isfinite(v) and v == int(v) else None
    if isinstance(v, str):
        try:
            return int(v.strip())
        except ValueError:
            return None
    return None


def _as_float(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _as_str(v: Any) -> Optional[str]:
    return v if isinstance(v, str) and v else None


def parse_ffprobe(payload: Any) -> ProbeResult:
    """`ffprobe -print_format json -show_format -show_streams` → ProbeResult. Tolerant: a missing
    stream, section or field is None, never a KeyError (the gate reports it). The first video
    stream that is not cover art, and the first audio stream, are read."""
    payload = payload if isinstance(payload, dict) else {}
    raw_streams = payload.get("streams")
    streams = [s for s in raw_streams if isinstance(s, dict)] if isinstance(raw_streams, list) else []
    fmt = payload.get("format") if isinstance(payload.get("format"), dict) else {}

    def first(kind: str) -> Dict[str, Any]:
        for s in streams:
            disp = s.get("disposition") if isinstance(s.get("disposition"), dict) else {}
            if s.get("codec_type") == kind and not disp.get("attached_pic"):
                return s
        return {}

    v, a = first("video"), first("audio")
    duration = _as_float(fmt.get("duration"))
    if duration is None:
        known = [d for d in (_as_float(s.get("duration")) for s in (v, a) if s) if d is not None]
        duration = max(known) if known else None
    return ProbeResult(
        width=_as_int(v.get("width")), height=_as_int(v.get("height")),
        pix_fmt=_as_str(v.get("pix_fmt")), video_codec=_as_str(v.get("codec_name")),
        profile=_as_str(v.get("profile")), r_frame_rate=_as_str(v.get("r_frame_rate")),
        avg_frame_rate=_as_str(v.get("avg_frame_rate")), audio_codec=_as_str(a.get("codec_name")),
        sample_rate=_as_int(a.get("sample_rate")), channels=_as_int(a.get("channels")),
        duration=duration, size=_as_int(fmt.get("size")),
    )


def _rate(text: Optional[str]) -> Optional[Fraction]:
    if not text:
        return None
    try:
        num, _, den = text.partition("/")
        r = Fraction(int(num), int(den or "1"))
    except (ValueError, ZeroDivisionError):
        return None
    return r


def check_probe(p: ProbeResult, *, expected_seconds: float, max_seconds: float, max_bytes: int) -> None:
    """The output gate. RenderRejected listing EVERY problem (so one log line says all of it).
    A cap that is not a positive finite number is itself a problem (a NaN cap would otherwise
    compare False and wave everything through)."""
    problems: List[str] = []
    for label, cap in (("expected_seconds", expected_seconds), ("max_seconds", max_seconds),
                       ("max_bytes", max_bytes)):
        if (isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(cap)
                or cap <= 0):
            problems.append(f"invalid {label} {cap!r}")
    if problems:
        raise RenderRejected("; ".join(problems))
    if p.video_codec is None and p.width is None:
        problems.append("no video stream")
    else:
        if (p.width, p.height) != SIZE:
            problems.append(f"size {p.width}x{p.height} != {SIZE[0]}x{SIZE[1]}")
        if p.pix_fmt != "yuv420p":
            problems.append(f"pix_fmt {p.pix_fmt} != yuv420p")
        if p.video_codec != "h264":
            problems.append(f"video codec {p.video_codec} != h264")
        if p.profile != "High":
            problems.append(f"h264 profile {p.profile} != High")
        for label, text in (("r_frame_rate", p.r_frame_rate), ("avg_frame_rate", p.avg_frame_rate)):
            if _rate(text) != FPS:
                problems.append(f"{label} {text} != {FPS}/1 (constant frame rate required)")
    if p.audio_codec is None:
        problems.append("no audio stream")
    else:
        if p.audio_codec != "aac":
            problems.append(f"audio codec {p.audio_codec} != aac")
        if p.sample_rate != 48000:
            problems.append(f"sample rate {p.sample_rate} != 48000")
        if p.channels != 2:
            problems.append(f"channels {p.channels} != 2")
    if p.duration is None:
        problems.append("no duration")
    else:
        if abs(p.duration - expected_seconds) > DURATION_TOLERANCE_SECONDS:
            problems.append(f"duration {p.duration:.3f}s is not the expected {expected_seconds:.3f}s "
                            f"(±{DURATION_TOLERANCE_SECONDS}s) — a cut tail or a runaway stream")
        if p.duration > max_seconds + CONTAINER_SLACK_SECONDS:
            problems.append(f"duration {p.duration:.3f}s is over the {max_seconds:.3f}s cap")
    if p.size is None or p.size <= 0:
        problems.append(f"size {p.size} bytes")
    elif p.size > max_bytes:
        problems.append(f"size {p.size} bytes is over the {max_bytes}-byte cap")
    if problems:
        raise RenderRejected("; ".join(problems))


def moov_before_mdat(path: str) -> bool:
    """Walk the TOP-LEVEL MP4 boxes (header reads only — never the whole file): True iff a
    complete `moov` box comes before an `mdat` box. Any malformed or truncated structure (a box
    size < its header, a box running past EOF, an unreadable file) is False, never an exception."""
    try:
        total = os.path.getsize(path)
        with open(path, "rb") as f:
            pos, seen_moov = 0, False
            for _ in range(_MAX_BOXES):
                if pos + 8 > total:
                    return False
                f.seek(pos)
                head = f.read(8)
                if len(head) < 8:
                    return False
                size, kind = struct.unpack(">I4s", head)
                header = 8
                if size == 1:
                    ext = f.read(8)
                    if len(ext) < 8:
                        return False
                    size = struct.unpack(">Q", ext)[0]
                    header = 16
                elif size == 0:
                    size = total - pos
                if size < header or pos + size > total:
                    return False
                if kind == b"mdat":
                    return seen_moov
                if kind == b"moov":
                    seen_moov = True
                pos += size
            return False
    except OSError:
        return False


# ── running ffmpeg / ffprobe ──────────────────────────────────────────────────


def _stderr_tail(fh: Any, limit: int = 600) -> str:
    try:
        fh.flush()
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 4096))
        text = fh.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return ""
    return text.strip()[-limit:]


def run_ffmpeg(argv: Sequence[str], *, cwd: str, timeout: float = RENDER_TIMEOUT_SECONDS,
               heartbeat: Optional[Callable[[], None]] = None, run_id: str = "?") -> None:
    """Run ffmpeg in `cwd`; heartbeat every HEARTBEAT_SECONDS while it works. stderr goes to a
    temporary FILE (a pipe nobody drains can deadlock a chatty child). Typed failures carry the
    run id and the stderr tail: RenderTimeout (killed at `timeout`), RenderOOM (exit -9 / 137),
    RenderFailed (could not start, or any other non-zero exit). A failing heartbeat is logged and
    the render goes on; the child never outlives this call."""
    if not argv:
        raise ValueError("empty argv")
    t0 = _monotonic()
    logger.info("render ffmpeg START run_id=%s args=%d timeout=%.0fs", run_id, len(argv), timeout)
    logger.debug("render ffmpeg argv run_id=%s: %s", run_id, list(argv))
    with tempfile.TemporaryFile() as err:
        try:
            proc = _popen(list(argv), cwd=cwd, stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=err)
        except OSError as e:
            raise RenderFailed(f"run {run_id}: could not start ffmpeg: {type(e).__name__}: {e}") from e
        timed_out = False
        try:
            last_beat = t0
            while True:
                # The child's own state FIRST: a heartbeat can block (a slow backend), and a
                # render that finished during it used to be reported as a timeout and thrown
                # away (review 2026-09-29).
                if proc.poll() is not None:
                    break
                remaining = timeout - (_monotonic() - t0)
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    proc.wait(timeout=min(_POLL_SECONDS, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pass
                now = _monotonic()
                if heartbeat is not None and now - last_beat >= HEARTBEAT_SECONDS:
                    last_beat = now
                    try:
                        heartbeat()
                    except Exception as e:  # noqa: BLE001 — a missed beat must not kill the render
                        logger.warning("render heartbeat failed run_id=%s: %s: %s",
                                       run_id, type(e).__name__, e)
        finally:
            if proc.poll() is None:            # timed out, or interrupted by a BaseException
                proc.kill()
                proc.wait()
        rc = proc.returncode
        tail = _stderr_tail(err)
    wall = _monotonic() - t0
    if timed_out:
        logger.error("render ffmpeg TIMEOUT run_id=%s after %.0fs: %s", run_id, wall, tail)
        raise RenderTimeout(f"run {run_id}: ffmpeg timed out after {timeout:.0f}s and was killed: {tail}")
    if rc in (-9, 137):
        logger.error("render ffmpeg KILLED run_id=%s exit=%s wall=%.1fs: %s", run_id, rc, wall, tail)
        raise RenderOOM(f"run {run_id}: ffmpeg was killed (exit {rc}) — the memory limit? {tail}")
    if rc != 0:
        logger.error("render ffmpeg FAILED run_id=%s exit=%s wall=%.1fs: %s", run_id, rc, wall, tail)
        raise RenderFailed(f"run {run_id}: ffmpeg exited {rc}: {tail}")
    logger.info("render ffmpeg DONE run_id=%s wall=%.1fs", run_id, wall)


def _probe(ffprobe: str, path: str, *, cwd: str, run_id: str) -> ProbeResult:
    try:
        res = subprocess.run(probe_argv(ffprobe, path), cwd=cwd, stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise RenderFailed(f"run {run_id}: ffprobe timed out after {PROBE_TIMEOUT_SECONDS}s "
                           f"on {path}") from None
    except OSError as e:
        raise RenderFailed(f"run {run_id}: could not start ffprobe: {type(e).__name__}: {e}") from e
    if res.returncode != 0:
        raise RenderFailed(f"run {run_id}: ffprobe exited {res.returncode} on {path}: "
                           f"{(res.stderr or '').strip()[-300:]}")
    try:
        payload = json.loads(res.stdout or "")
    except ValueError as e:
        raise RenderFailed(f"run {run_id}: ffprobe printed no JSON for {path}: {e}") from e
    return parse_ffprobe(payload)


def _tool(name: str, given: Optional[str], run_id: str) -> str:
    exe = given or shutil.which(name)
    if not exe or not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
        raise RenderFailed(f"run {run_id}: {name} not found ({given or 'not on PATH'})")
    return exe


def _png_size(path: str) -> Optional[Tuple[int, int]]:
    """(width, height) from a PNG's IHDR, or None when the file is not a PNG."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def render_video(*, workdir: str, card_files: Sequence[str], segments: Sequence[Segment],
                 audio_file: str, ass_file: str, fonts_dir: str, out_file: str = "video.mp4",
                 threads: int, ffmpeg: Optional[str] = None, ffprobe: Optional[str] = None,
                 heartbeat: Optional[Callable[[], None]] = None, run_id: str = "?",
                 max_seconds: float, max_bytes: int = DEFAULT_MAX_BYTES) -> Tuple[bytes, ProbeResult]:
    """Render, probe, gate; return (mp4 bytes, probe). Every file name is relative to `workdir`.

    Before ffmpeg runs: the tools exist, every input is there (each card a 1080×1920 PNG, the
    fonts dir holds a font — libass would otherwise fall back to a system face without a word),
    and the narration's probed length matches the timeline (it must end where the last segment —
    the disclaimer card — begins, ± DURATION_TOLERANCE_SECONDS). After: the ffprobe gate
    (expected duration = segments[-1].end) and moov-before-mdat, else RenderRejected.
    ValueError (from build_argv) means the caller broke the contract."""
    t0 = _monotonic()
    ffmpeg_exe = _tool("ffmpeg", ffmpeg, run_id)
    ffprobe_exe = _tool("ffprobe", ffprobe, run_id)
    argv = build_argv(ffmpeg=ffmpeg_exe, card_files=card_files, segments=segments,
                      audio_file=audio_file, ass_file=ass_file, fonts_dir=fonts_dir,
                      out_file=out_file, threads=threads)

    def at(name: str) -> str:
        return os.path.join(workdir, name)

    for name in sorted(set(card_files)):
        dims = _png_size(at(name))
        if dims is None:
            raise RenderFailed(f"run {run_id}: card {name} is missing or not a PNG")
        if dims != SIZE:
            raise RenderFailed(f"run {run_id}: card {name} is {dims[0]}x{dims[1]}, "
                               f"not {SIZE[0]}x{SIZE[1]}")
    for label, name in (("narration", audio_file), ("captions", ass_file)):
        if not os.path.isfile(at(name)):
            raise RenderFailed(f"run {run_id}: {label} file {name} is missing")
    try:
        faces = [f for f in os.listdir(at(fonts_dir)) if f.lower().endswith((".ttf", ".otf"))]
    except OSError as e:
        raise RenderFailed(f"run {run_id}: fonts dir {fonts_dir} unreadable: "
                           f"{type(e).__name__}: {e}") from e
    if not faces:
        raise RenderFailed(f"run {run_id}: fonts dir {fonts_dir} holds no .ttf/.otf face")

    narration = _probe(ffprobe_exe, audio_file, cwd=workdir, run_id=run_id)
    speech_end = segments[-1].start
    if narration.audio_codec is None or narration.duration is None:
        raise RenderFailed(f"run {run_id}: narration {audio_file} has no readable audio stream")
    if abs(narration.duration - speech_end) > DURATION_TOLERANCE_SECONDS:
        raise RenderFailed(f"run {run_id}: narration is {narration.duration:.3f}s but the timeline "
                           f"starts the closing card at {speech_end:.3f}s — not this narration's "
                           f"timeline")

    out_path = at(out_file)
    try:
        os.remove(out_path)                   # never probe a stale file from an earlier attempt
    except FileNotFoundError:
        pass
    except OSError as e:
        raise RenderFailed(f"run {run_id}: cannot clear {out_file}: {type(e).__name__}: {e}") from e
    run_ffmpeg(argv, cwd=workdir, timeout=RENDER_TIMEOUT_SECONDS, heartbeat=heartbeat, run_id=run_id)
    if not os.path.isfile(out_path):
        raise RenderFailed(f"run {run_id}: ffmpeg exited 0 but wrote no {out_file}")
    probe = _probe(ffprobe_exe, out_file, cwd=workdir, run_id=run_id)
    probe.size = os.path.getsize(out_path)   # the bytes that will be uploaded are the authority
    expected = segments[-1].end
    try:
        check_probe(probe, expected_seconds=expected, max_seconds=max_seconds, max_bytes=max_bytes)
    except RenderRejected as e:
        logger.error("render REJECTED run_id=%s: %s", run_id, e)
        raise RenderRejected(f"run {run_id}: {e}") from None
    if not moov_before_mdat(out_path):
        logger.error("render REJECTED run_id=%s: moov is not before mdat", run_id)
        raise RenderRejected(f"run {run_id}: moov is not before mdat (faststart failed)")
    with open(out_path, "rb") as f:
        data = f.read()
    logger.info("render video OK run_id=%s duration=%.3fs expected=%.3fs bytes=%d segments=%d "
                "threads=%d wall=%.1fs", run_id, probe.duration or 0.0, expected, len(data),
                len(segments), threads, _monotonic() - t0)
    return data, probe
