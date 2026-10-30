"""Narration of a Wiser weekly piece: the script the worker speaks, and the server half of the
narrator's internal API (plan "Wiser weekly content engine", 2026-10-22, §1.8).

WHAT THIS MODULE OWNS
---------------------
1. **The narration script.** `narration_blocks(content)` turns a piece's iOS-shaped draft into the
   ordered blocks the `learn_narrator` worker renders, in EXACTLY the order the hand-made catalog
   was voiced by `scripts/clone_learn_audio.narration_blocks` (title, subtitle, then per section its
   title and its paragraph / subheading / quote / callout text, one block per bulletList item). The
   order is not cosmetic: the catalog's read-along, the aligner and every listener's sense of where
   the voice is all assume it. `tests/test_learn_weekly_narration.py` AST-extracts the script's own
   function and pins equality over every bundled article — the scripts tree imports torch at module
   top, so it is never imported here or at runtime.
   Each block keeps a server-side `dest` (contracts.NarrationBlock) saying where its timings merge
   back; the worker only ever sees `{i, text, role}` (`NarrationBlock.wire()`).
2. **The sentence split** the read-along is keyed on: `sentence_split` is `_forced_align.split_sentences
   (strip_markup(text))` — the same regex, copied and pinned by the same AST test, because the
   worker's sentence texts must equal the server's split exactly (a mismatch is refused).
3. **The timing gate** (`validate_timings`) — the server's last line against a bad render. CTC forced
   alignment ALWAYS returns spans, even for words that were never spoken (`fill_gaps` densifies the
   holes), so a block where the voice stopped early or skipped a sentence still produces monotonic,
   correctly-texted timings. Its one tell is time: a dropped sentence collapses to almost nothing.
   So every sentence's seconds-per-word must sit in SPW_BAND, and a sentence outside it refuses the
   whole round (review finding F5; "a silent article never ships").
4. **The worker-facing flows** behind `/api/v1/internal/learn/*` (`learn_internal.py` is the thin
   HTTP surface): claim, heartbeat, upload target, complete, fail — and what each does to the piece.

THE AUDIO OBJECT
----------------
Audio goes to the PRIVATE `money-moves-media` bucket (migration 128: every Learn narration bucket is
private; iOS gets a short-lived signed URL per request from `learn_audio_urls.sign_money_moves`) at
`audio/weekly/<slug>-<sha256[:12]>.m4a`: content-addressed, so a re-render of different bytes never
overwrites a file a published article (or a phone's LearnAudioCache, which keys on the full path)
still points at. The worker PUTs the bytes itself through a signed upload URL this module mints per
object — nothing large transits the web process, and the worker holds no Supabase key. `complete`
then verifies the object the way marketing's `run_service._verify_object` does (present, registered
size, registered content type; a mismatch deletes the object and fails the round; an UNKNOWN size or
type deletes nothing and is retried), with the bucket as a parameter, plus a bytes-per-second band
per engine that a truncated or silent encode falls outside of.

The draft that reaches the owner carries `audioUrl` in the PUBLIC form (the same form
`seed_money_moves.py` stores — `audio_public_url`), which the content service signs per request.

I/O: the Supabase SDK is synchronous, so every Storage call is a plain `def` run through
`asyncio.to_thread` (never called directly from an `async def` —
`tests/test_crud_paths_off_the_event_loop.py`); every ledger call goes through the ledger's own
async wrappers. Nothing here imports FMP or Gemini.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import importlib
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.config import settings
from app.database import get_supabase
from app.services.learn_audio_urls import MONEY_MOVES_BUCKET
from app.services.learn_weekly import contracts as c
from app.services.learn_weekly import ledger, schedule
from app.services.learn_weekly.errors import (
    LearnNarratorAssetMismatch,
    LearnNarratorAssetMissing,
    LearnNarratorJobNotHeld,
    LearnNarratorLedgerError,
    LearnNarratorNotFound,
    LearnNarratorRequestInvalid,
)

logger = logging.getLogger(__name__)

# ── constants ────────────────────────────────────────────────────────────────────────────────

#: Frozen into every job (the ledger refuses to adopt a round whose pipeline differs). Bump it with
#: any change to what the worker is asked to produce: the block order, the split, the timing shape.
PIPELINE_VERSION = "learn-narration/v1"

#: The private Learn narration bucket (migration 128) — the same one the catalog uses, so
#: `sign_money_moves` signs a weekly piece's audio with no new code path.
AUDIO_BUCKET = MONEY_MOVES_BUCKET
AUDIO_CONTENT_TYPE = "audio/mp4"
WEEKLY_AUDIO_PREFIX = "audio/weekly"
#: The worker's upload cap. A 12-minute Deep Dive at 96 kbps is ≈ 8.6 MB; 60 MB is a runaway.
MAX_AUDIO_UPLOAD_BYTES = 60 * 1024 * 1024
#: The ledger's own upper bound on a job's duration (`ledger.MAX_AUDIO_SECONDS`, 4 h).
MAX_AUDIO_SECONDS = ledger.MAX_AUDIO_SECONDS

_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_NONCE_RE = re.compile(r"[0-9a-f]{16}")       # what `ledger.claim_job` mints (secrets.token_hex(8))
_FAIL_CODE_RE = re.compile(r"[a-z0-9][a-z0-9_.\-]{0,63}")
_WEEKLY_AUDIO_RE = re.compile(r"audio/weekly/([a-z0-9]+(?:-[a-z0-9]+)*)-([0-9a-f]{12})\.m4a")

#: The engine's voice, frozen into the job. Chatterbox = the catalog clone, at the settings
#: `scripts/clone_learn_audio.py` voiced it with (approved 2026-06-24) — the worker AST-pins its own
#: constants to that script too. Kokoro's voice is read from settings at ENQUEUE time.
CHATTERBOX_VOICE: Mapping[str, Any] = {
    "exaggeration": 0.65, "cfg_weight": 0.40, "target_wpm": 165, "ref": "caydex_voice_achird_v2",
}

#: Bytes per second of the final m4a, by engine. Chatterbox = `verify_learn_voice`'s band (A4:
#: 11.5–14 kB/s for 96 kbps AAC-LC + MP4 overhead). Its ceiling was 12.8 kB/s, measured on local
#: ffmpeg 7.0 encodes (~12.1k); the narrator image's ffmpeg 5.1 overshoots the 96k target (12,629 B/s
#: on 2026-10-24, 12,888 on 2026-10-30, refused twice — a deterministic render, so every retry failed
#: the same way). The FLOOR is what separates the clone encode from the Gemini path (~9.7k). Kokoro
#: is encoded at the same 96 kbps but its band is UNMEASURED, so its floor is looser (10–14 kB/s ≈
#: 80–112 kbps): still narrow enough that a truncated stream, a silent track (AAC spends far fewer
#: bytes on silence) or a stereo/160 kbps mistake falls outside. Tighten it once the narrator's
#: first Kokoro renders are measured.
BYTES_PER_SECOND_BAND: Mapping[str, Tuple[float, float]] = {
    c.ENGINE_CHATTERBOX: (11_500.0, 14_000.0),
    c.ENGINE_KOKORO: (10_000.0, 14_000.0),
}

#: Seconds per spoken word, per SENTENCE, in the final (tempo-normalised) audio. 165 wpm ≈ 0.36 s/w.
#: The floor (0.15) is the dropped-sentence tell: CTC squeezes the tokens of words that were never
#: voiced into a few frames. The ceiling (1.2) catches a sentence stretched over silence or a
#: hallucinated run-on. Words are counted by `spoken_words` (letter/digit runs, so "Mark-to-Market"
#: is the three words the voice says, not one whitespace token). Calibrated on the catalog's own
#: aligned narration (854 sentences, 23 articles): every one sits in 0.18–0.64 s/word —
#: `test_the_catalogs_own_narration_passes_the_band` keeps that true if the band is ever tightened.
SPW_BAND: Tuple[float, float] = (0.15, 1.2)
#: Timings may end this far past the reported duration (encoder priming / rounding).
DURATION_SLACK_SECONDS = 0.5
#: The longest stretch of the audio no sentence may cover: from 0 to the first sentence, between
#: two consecutive sentences (in a block or across blocks), and from the last sentence to the end.
#: Every spoken block — context included — is in the timing table, so a long uncovered stretch is
#: audio nobody wrote: a hallucinated run-on after a block, a silent hole, a tail of garbage. The
#: catalog's longest pause between two sentences of one block is 2.16 s (block pauses are 0.6 s);
#: 6 s is ~3× that.
MAX_SILENCE_SECONDS = 6.0
#: Order checks tolerate this much overlap: the worker's offsets go through raw-offset ÷ atempo and
#: a 2-decimal rounding, which can overlap the previous block by a hair without anything being wrong.
ORDER_EPSILON_SECONDS = 0.02

#: Failed narration rounds per piece after which an automatic re-round stops (the piece fails with
#: an alert). The catch-up cut-off bounds rounds in TIME; this bounds them in COUNT, because a
#: deterministic failure (a block the engine cannot render) would otherwise re-render every cron
#: tick until the cut-off — ≈ 2 failures × 20 min per round for up to ~9 h.
MAX_AUTO_FAILED_ROUNDS = 3

#: Piece statuses after which no narration can ever be wanted: a job of such a piece is cancelled at
#: claim time instead of being rendered for nothing. Deliberately NOT building / drafted /
#: ready_for_review / in_review / approved — the builder may enqueue just before it moves the piece
#: to media_pending, and a Retry-narration round is enqueued before the piece moves back.
_NARRATION_DEAD = frozenset(c.TERMINAL_STATUSES | {c.PUBLISHING, c.PUBLISHED})

_BODY_TYPES = ("paragraph", "subheading", "quote", "callout")
_TEXT_EXCERPT = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _excerpt(text: Any) -> str:
    t = " ".join(str(text).split())
    return t if len(t) <= _TEXT_EXCERPT else t[: _TEXT_EXCERPT - 1] + "…"


# ── the script ───────────────────────────────────────────────────────────────────────────────


def strip_markup(s: Any) -> str:
    """`**bold**` markers removed — the text the voice speaks and iOS displays. A copy of
    `_forced_align.strip_markup` / `clone_learn_audio.strip_markup` (AST-pinned equal)."""
    return re.sub(r"\*\*", "", s or "")


# Split on whitespace that follows a sentence terminator (. ! ?), optionally followed by a closing
# quote — the quote stays in the LEFT sentence. Copied from `scripts/_forced_align._SENT` (the
# catalog's read-along split); `tests/test_learn_weekly_narration.py` pins the pattern string and
# the behaviour equal to the script's, AST-extracted.
_SENT = re.compile(r'(?:(?<=[.!?])|(?<=[.!?]["”’]))\s+(?=["“‘A-Z0-9])')


def _split(text: str) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    return [s.strip() for s in _SENT.split(text) if s.strip()]


def sentence_split(text: str) -> List[str]:
    """The server's sentence split of one narration block: `split_sentences(strip_markup(text))`,
    exactly what `align_money_moves_audio.collect_units` registers per block."""
    return _split(strip_markup(text))


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"narration: {where} must be a string, got {type(value).__name__}")
    return value


def narration_blocks(content: Mapping[str, Any]) -> List[c.NarrationBlock]:
    """The ordered narration of an iOS-shaped article. Order and filtering are EXACTLY
    `clone_learn_audio.narration_blocks` (a falsy text/item is skipped, then markup is stripped), so
    a weekly piece is voiced the way every catalog article was. `dest` tells `merge_timings` where
    each block's sentences go; a bulletList item keeps its index in `items` (a skipped empty item
    keeps its slot in `itemsReadAlong`, as the aligner does).

    ValueError on a non-string title / text / item: the draft is malformed (the script would raise a
    TypeError on the same input)."""
    if not isinstance(content, Mapping):
        raise ValueError(f"narration: content must be a mapping, got {type(content).__name__}")
    raw: List[Tuple[str, str, Tuple[Any, ...]]] = []
    if content.get("title"):
        raw.append((_text(content["title"], "title"), c.ROLE_CONTEXT, ("title",)))
    if content.get("subtitle"):
        raw.append((_text(content["subtitle"], "subtitle"), c.ROLE_CONTEXT, ("subtitle",)))
    sections = content.get("sections") or []
    if not isinstance(sections, list):
        raise ValueError("narration: sections must be a list")
    for s, section in enumerate(sections):
        if not isinstance(section, Mapping):
            raise ValueError(f"narration: section {s} is not an object")
        if section.get("title"):
            raw.append((_text(section["title"], f"section {s} title"), c.ROLE_CONTEXT, ("section_title", s)))
        blocks = section.get("content") or []
        if not isinstance(blocks, list):
            raise ValueError(f"narration: section {s} content must be a list")
        for b, block in enumerate(blocks):
            if not isinstance(block, Mapping):
                raise ValueError(f"narration: section {s} block {b} is not an object")
            kind = block.get("type")
            if kind in _BODY_TYPES and block.get("text"):
                raw.append((_text(block["text"], f"section {s} block {b} text"), c.ROLE_BODY, ("block", s, b)))
            elif kind == "bulletList":
                items = block.get("items") or []
                if not isinstance(items, list):
                    raise ValueError(f"narration: section {s} block {b} items must be a list")
                for k, item in enumerate(items):
                    if item:
                        raw.append((_text(item, f"section {s} block {b} item {k}"), c.ROLE_ITEM,
                                    ("item", s, b, k)))
    return [c.NarrationBlock(i=i, text=strip_markup(text), role=role, dest=dest)
            for i, (text, role, dest) in enumerate(raw)]


def script_json(blocks: Sequence[c.NarrationBlock]) -> List[Dict[str, Any]]:
    """What the job row's `script` column stores: every block WITH its dest (the worker never sees
    it — `NarrationBlock.wire()`)."""
    return [{"i": b.i, "text": b.text, "role": b.role, "dest": list(b.dest)} for b in blocks]


def _canonical(value: Any) -> str:
    # The FactSheet.canonical_json form: sorted keys, compact, UTF-8 (not \u-escaped).
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def script_sha256(blocks: Sequence[c.NarrationBlock]) -> str:
    """sha256 of the stored script (text, role AND dest): a job is the narration of one exact draft
    shape, and a draft whose structure moved under the same words must not adopt its timings."""
    return hashlib.sha256(_canonical(script_json(blocks)).encode("utf-8")).hexdigest()


def draft_sha256(draft: Mapping[str, Any]) -> str:
    """sha256 of a piece's draft (the canonical JSON form above). It fences the review buttons, and
    it changes when narration merges its timings — the owner approves exactly what they heard."""
    return hashlib.sha256(_canonical(draft).encode("utf-8")).hexdigest()


def blocks_from_script(script: Any, *, expected_sha256: Optional[str] = None) -> List[c.NarrationBlock]:
    """The job's stored script back as NarrationBlocks (dest lists → tuples). ValueError when it is
    unreadable, out of order, or (with `expected_sha256`) not the script the job was enqueued with."""
    if not isinstance(script, list) or not script:
        raise ValueError("narration: the job's script is not a non-empty list")
    out: List[c.NarrationBlock] = []
    for pos, entry in enumerate(script):
        if not isinstance(entry, Mapping):
            raise ValueError(f"narration: script entry {pos} is not an object")
        i, text, role, dest = entry.get("i"), entry.get("text"), entry.get("role"), entry.get("dest")
        if isinstance(i, bool) or i != pos:
            raise ValueError(f"narration: script entry {pos} has i={i!r}")
        if not isinstance(text, str) or not text.strip() or role not in c.ROLES:
            raise ValueError(f"narration: script entry {pos} has no text or an unknown role {role!r}")
        if not isinstance(dest, list) or not dest or not _dest_ok(tuple(dest)):
            raise ValueError(f"narration: script entry {pos} has an unreadable dest {dest!r}")
        out.append(c.NarrationBlock(i=pos, text=text, role=role, dest=tuple(dest)))
    if expected_sha256 is not None and script_sha256(out) != expected_sha256:
        raise ValueError("narration: the job's script does not hash to its script_sha256")
    return out


def _dest_ok(dest: Tuple[Any, ...]) -> bool:
    kind, idx = dest[0], dest[1:]
    arity = {"title": 0, "subtitle": 0, "section_title": 1, "block": 2, "item": 3}.get(kind)
    return arity is not None and len(idx) == arity and all(
        isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in idx)


# ── voice, paths ─────────────────────────────────────────────────────────────────────────────


def voice_spec(engine: str) -> Dict[str, Any]:
    """The voice parameters frozen into a new job of `engine` (Kokoro's voice read at call time)."""
    if engine == c.ENGINE_CHATTERBOX:
        return dict(CHATTERBOX_VOICE)
    if engine == c.ENGINE_KOKORO:
        voice = settings.LEARN_NARRATOR_KOKORO_VOICE
        if not (isinstance(voice, str) and re.fullmatch(r"[a-z]{2}_[a-z0-9]{1,40}", voice)):
            raise ValueError(f"LEARN_NARRATOR_KOKORO_VOICE {voice!r} is not a Kokoro voice id (e.g. am_michael)")
        return {"voice": voice}
    raise ValueError(f"narration: unknown engine {engine!r} (known: {c.ENGINES})")


def voice_key(spec: Mapping[str, Any]) -> str:
    """The job row's `voice` column (TEXT, 1-80 chars — a JSON object of the Chatterbox params is 85):
    `k=v` pairs sorted by key, `;`-joined. `parse_voice_key` reverses it for the worker."""
    parts = []
    for k in sorted(spec):
        v = spec[k]
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise ValueError(f"narration: voice parameter {k!r} must be a number or a string")
        rendered = repr(v) if isinstance(v, float) else str(v)
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", rendered) or not re.fullmatch(r"[a-z_]+", k):
            raise ValueError(f"narration: voice parameter {k}={rendered!r} cannot be keyed")
        parts.append(f"{k}={rendered}")
    key = ";".join(parts)
    if not 0 < len(key) <= 80:
        raise ValueError(f"narration: the voice key is {len(key)} characters (the column holds 1-80)")
    return key


def parse_voice_key(key: Any) -> Dict[str, Any]:
    """`voice_key` reversed: ints stay ints, floats floats, anything else a string."""
    if not isinstance(key, str) or not key:
        raise ValueError(f"narration: unreadable voice key {key!r}")
    out: Dict[str, Any] = {}
    for part in key.split(";"):
        name, sep, raw = part.partition("=")
        if not sep or not name or not raw:
            raise ValueError(f"narration: unreadable voice key part {part!r}")
        value: Any = raw
        if re.fullmatch(r"-?[0-9]+", raw):
            value = int(raw)
        elif re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?(?:e[-+]?[0-9]+)?", raw):
            value = float(raw)   # `repr(float)` — "0.4", "1e-05"
        out[name] = value
    return out


def current_engine(fallback: Optional[str] = None) -> str:
    """`LEARN_NARRATOR_ENGINE` as a known engine; junk → `fallback` (or Chatterbox) with an ERROR."""
    engine = settings.LEARN_NARRATOR_ENGINE
    if engine in c.ENGINES:
        return engine
    chosen = fallback if fallback in c.ENGINES else c.ENGINE_CHATTERBOX
    logger.error("learn narration: LEARN_NARRATOR_ENGINE=%r is not one of %s — using %s", engine, c.ENGINES, chosen)
    return chosen


def weekly_audio_path(slug: str, sha256: str) -> str:
    """`audio/weekly/<slug>-<sha256[:12]>.m4a` — content-addressed, never overwritten."""
    if not (isinstance(slug, str) and len(slug) <= 160 and _SLUG_RE.fullmatch(slug)):
        raise ValueError(f"narration: slug {slug!r} is not lowercase kebab-case")
    if not (isinstance(sha256, str) and _SHA256_RE.fullmatch(sha256)):
        raise ValueError("narration: sha256 must be 64 lowercase hex characters")
    return f"{WEEKLY_AUDIO_PREFIX}/{slug}-{sha256[:12]}.m4a"


def audio_public_url(path: str) -> str:
    """The PUBLIC-form URL of a weekly narration object — the form `seed_money_moves.py` stores in
    `audioUrl` (`storage.get_public_url`, minus its bare `?`). The bucket is private: this URL is not
    fetchable as is, it is what `learn_audio_urls.parse_storage_url` reads to sign per request. Pure
    (no client, no I/O), so the publisher can call it anywhere."""
    if not (isinstance(path, str) and _WEEKLY_AUDIO_RE.fullmatch(path)):
        raise ValueError(f"narration: {path!r} is not a weekly narration path")
    return f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/public/{AUDIO_BUCKET}/{path}"


# ── the timing gate ──────────────────────────────────────────────────────────────────────────


_SPOKEN_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)?")


def spoken_words(text: str) -> int:
    """Words the voice says in `text`, for the seconds-per-word band: letter/digit runs (an
    apostrophe keeps "don't" whole), so a hyphenated compound counts per part — the catalog's
    "Mark-to-Market" heading is 1.32 s for one whitespace token but 0.44 s/word spoken. Never 0."""
    return max(1, len(_SPOKEN_WORD.findall(text or "")))


def _num(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LearnNarratorRequestInvalid(f"timings: {where} is not a number ({type(value).__name__})")
    v = float(value)
    if not math.isfinite(v):
        raise LearnNarratorRequestInvalid(f"timings: {where} is {value!r}, not a finite number")
    if v < 0:
        raise LearnNarratorRequestInvalid(f"timings: {where} is negative ({v})")
    return v


def _dump(entry: Any) -> Any:
    """Accept a pydantic model or a mapping (the API passes models, tests and replays pass dicts)."""
    dump = getattr(entry, "model_dump", None)
    return dump() if callable(dump) else entry


def validate_timings(blocks: Sequence[c.NarrationBlock], timings: Any, *,
                     duration_seconds: Optional[float] = None) -> List[Dict[str, Any]]:
    """The worker's timing table, checked against the script; returns it normalised
    (`[{i, start, end, sentences: [{text, start, end}]}]`, floats).

    Refused with LearnNarratorRequestInvalid (422, naming the block and sentence):
      * not exactly one entry per block, in block order (`timings[k].i == k`) — a missing block, an
        extra block, a duplicate;
      * a value that is not a finite number ≥ 0 (NaN, ±inf, a bool, a string, negative);
      * start > end; a block that starts before the previous one ended; a sentence outside its
        block's window or before the previous sentence ended (ORDER_EPSILON_SECONDS of slack);
      * anything ending past `duration_seconds` + DURATION_SLACK_SECONDS;
      * sentence texts that are not exactly `sentence_split(block.text)`;
      * a sentence whose seconds-per-word is outside SPW_BAND — the dropped-sentence tell;
      * more than MAX_SILENCE_SECONDS of audio no sentence covers (before the first sentence,
        between two sentences, or after the last one up to `duration_seconds`).
    """
    blocks = list(blocks)
    if not blocks:
        raise LearnNarratorRequestInvalid("timings: the script has no blocks")
    if not isinstance(timings, (list, tuple)):
        raise LearnNarratorRequestInvalid(f"timings: expected a list of blocks, got {type(timings).__name__}")
    entries = [_dump(e) for e in timings]
    limit: Optional[float] = None
    if duration_seconds is not None:
        limit = _num(duration_seconds, "duration_seconds") + DURATION_SLACK_SECONDS
    seen = {}
    for pos, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise LearnNarratorRequestInvalid(f"timings: entry {pos} is not an object")
        i = entry.get("i")
        if isinstance(i, bool) or not isinstance(i, int):
            raise LearnNarratorRequestInvalid(f"timings: entry {pos} has no integer block index (i={i!r})")
        if i in seen:
            raise LearnNarratorRequestInvalid(f"timings: block {i} appears twice (entries {seen[i]} and {pos})")
        seen[i] = pos
    unknown = sorted(i for i in seen if not 0 <= i < len(blocks))
    if unknown:
        raise LearnNarratorRequestInvalid(
            f"timings: extra block(s) {unknown[:5]} — the script has {len(blocks)} blocks (0..{len(blocks) - 1})")
    missing = [b.i for b in blocks if b.i not in seen]
    if missing:
        raise LearnNarratorRequestInvalid(
            f"timings: block(s) {missing[:5]} missing ({len(missing)} of {len(blocks)}) — every block needs "
            "exactly one entry")
    out: List[Dict[str, Any]] = []
    prev_end = 0.0
    spoken_until = 0.0          # end of the last sentence, across blocks (the silence check)
    for pos, entry in enumerate(entries):
        block = blocks[pos]
        if entry["i"] != pos:
            raise LearnNarratorRequestInvalid(
                f"timings: entry {pos} is block {entry['i']} — entries must be in block order")
        start = _num(entry.get("start"), f"block {pos} start")
        end = _num(entry.get("end"), f"block {pos} end")
        if start > end:
            raise LearnNarratorRequestInvalid(f"timings: block {pos} starts at {start:.2f} s after it ends ({end:.2f} s)")
        if start + ORDER_EPSILON_SECONDS < prev_end:
            raise LearnNarratorRequestInvalid(
                f"timings: block {pos} starts at {start:.2f} s, before block {pos - 1} ends ({prev_end:.2f} s) — "
                "not monotonic")
        if limit is not None and end > limit:
            raise LearnNarratorRequestInvalid(
                f"timings: block {pos} ends at {end:.2f} s, past the audio's {duration_seconds:.2f} s "
                f"(+{DURATION_SLACK_SECONDS} s)")
        sents = entry.get("sentences")
        if not isinstance(sents, (list, tuple)):
            raise LearnNarratorRequestInvalid(f"timings: block {pos} has no sentence list")
        sents = [_dump(s) for s in sents]
        expected = sentence_split(block.text)
        if len(sents) != len(expected):
            raise LearnNarratorRequestInvalid(
                f"timings: block {pos} has {len(sents)} sentence(s); the server's split of "
                f"'{_excerpt(block.text)}' has {len(expected)}")
        s_prev = start
        norm_sents = []
        for k, (sent, text) in enumerate(zip(sents, expected)):
            where = f"block {pos} sentence {k}"
            if not isinstance(sent, Mapping):
                raise LearnNarratorRequestInvalid(f"timings: {where} is not an object")
            if sent.get("text") != text:
                raise LearnNarratorRequestInvalid(
                    f"timings: {where} text '{_excerpt(sent.get('text'))}' is not the server's "
                    f"'{_excerpt(text)}'")
            s0 = _num(sent.get("start"), f"{where} start")
            s1 = _num(sent.get("end"), f"{where} end")
            if s0 > s1:
                raise LearnNarratorRequestInvalid(f"timings: {where} starts at {s0:.2f} s after it ends ({s1:.2f} s)")
            if s0 + ORDER_EPSILON_SECONDS < s_prev:
                raise LearnNarratorRequestInvalid(
                    f"timings: {where} starts at {s0:.2f} s, before the previous one ends ({s_prev:.2f} s) — "
                    "not monotonic")
            if s1 > end + ORDER_EPSILON_SECONDS:
                raise LearnNarratorRequestInvalid(
                    f"timings: {where} ends at {s1:.2f} s, after its block ends ({end:.2f} s)")
            words = spoken_words(text)
            spw = (s1 - s0) / words
            lo, hi = SPW_BAND
            if not lo <= spw <= hi:
                raise LearnNarratorRequestInvalid(
                    f"timings: {where} '{_excerpt(text)}' spans {s1 - s0:.2f} s for {words} word(s) "
                    f"({spw:.3f} s/word, band {lo}–{hi}) — "
                    + ("a dropped or truncated sentence" if spw < lo else "a stretched sentence (silence or a run-on)"))
            if s0 - spoken_until > MAX_SILENCE_SECONDS:
                raise LearnNarratorRequestInvalid(
                    f"timings: {s0 - spoken_until:.1f} s of audio before {where} is covered by no sentence "
                    f"(cap {MAX_SILENCE_SECONDS} s) — unwritten audio or a silent hole")
            norm_sents.append({"text": text, "start": s0, "end": s1})
            s_prev = s1
            spoken_until = s1
        out.append({"i": pos, "start": start, "end": end, "sentences": norm_sents})
        prev_end = end
    if duration_seconds is not None and float(duration_seconds) - spoken_until > MAX_SILENCE_SECONDS:
        raise LearnNarratorRequestInvalid(
            f"timings: the audio runs {float(duration_seconds) - spoken_until:.1f} s past the last sentence "
            f"(cap {MAX_SILENCE_SECONDS} s) — an unwritten tail")
    return out


def timing_stats(timings: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Server-side summary stored in the job's `checks.server` (the review header reads it)."""
    spws = []
    for b in timings:
        for s in b.get("sentences") or []:
            words = spoken_words(str(s.get("text", "")))
            spws.append(((float(s["end"]) - float(s["start"])) / words, b.get("i")))
    if not spws:
        return {"blocks": len(timings), "sentences": 0}
    lo = min(spws, key=lambda t: t[0])
    hi = max(spws, key=lambda t: t[0])
    return {"blocks": len(timings), "sentences": len(spws),
            "min_spw": round(lo[0], 3), "min_spw_block": lo[1],
            "max_spw": round(hi[0], 3), "max_spw_block": hi[1]}


def merge_timings(content: Mapping[str, Any], blocks: Sequence[c.NarrationBlock], timings: Any, *,
                  duration_seconds: Optional[float] = None, audio_url: Optional[str] = None) -> Dict[str, Any]:
    """A NEW content dict with the narration's read-along merged in, the shape the catalog aligner
    writes (`align_money_moves_audio.align_article`): `readAlong = [{text, start, end}]` on every
    paragraph / subheading / quote / callout block, `itemsReadAlong = [[…] per item]` on every
    bulletList (an empty item keeps an empty slot), times rounded to 2 decimals;
    `audioDurationSeconds` (int seconds) and `hasAudioVersion = True`; `audioUrl` when given (the
    PUBLIC form — `audio_public_url`). Title / subtitle / section titles are spoken context and are
    not highlighted, as in the catalog.

    The timings are validated first (`validate_timings`: LearnNarratorRequestInvalid on any defect).
    `duration_seconds` None → the last block's end (and no past-the-end check). ValueError when
    `content` is not the article `blocks` were built from — the draft changed under the job."""
    norm = validate_timings(blocks, timings, duration_seconds=duration_seconds)
    current = narration_blocks(content)
    if [(b.text, b.role, b.dest) for b in current] != [(b.text, b.role, b.dest) for b in blocks]:
        raise ValueError("narration: the content is not the article the narration script was built from")
    new = copy.deepcopy(dict(content))
    for section in new.get("sections") or []:
        for block in section.get("content") or []:
            block.pop("readAlong", None)
            block.pop("itemsReadAlong", None)
    for block, timing in zip(blocks, norm):
        spans = [{"text": s["text"], "start": round(s["start"], 2), "end": round(s["end"], 2)}
                 for s in timing["sentences"]]
        kind, idx = block.dest[0], block.dest[1:]
        if kind == "block":
            s, b = idx
            new["sections"][s]["content"][b]["readAlong"] = spans
        elif kind == "item":
            s, b, k = idx
            target = new["sections"][s]["content"][b]
            slots = target.get("itemsReadAlong")
            if slots is None:
                slots = [[] for _ in target.get("items") or []]
                target["itemsReadAlong"] = slots
            slots[k] = spans
    total = float(duration_seconds) if duration_seconds is not None else max(t["end"] for t in norm)
    new["audioDurationSeconds"] = int(round(total))
    new["hasAudioVersion"] = True
    if audio_url is not None:
        new["audioUrl"] = audio_url
    return new


# ── enqueue (called by the build step) ───────────────────────────────────────────────────────


async def enqueue_for_piece(piece: Mapping[str, Any], content: Dict[str, Any], *, engine: str,
                            round: int) -> Optional[Dict[str, Any]]:
    """Queue the narration of `content` (the piece's iOS-shaped draft) as round `round` of `piece`,
    frozen to `engine` and its voice. Returns the job row (`ledger.enqueue_job`: first write wins on
    (piece, round); a retried tick adopts its own job), or None when that round already narrates a
    DIFFERENT script (logged ERROR by the ledger — open round max + 1 instead).

    ValueError before any I/O on a draft that cannot be narrated: no blocks, a blank block, a block
    over MAX_PARAGRAPH_WORDS (Chatterbox truncates a block at 40 s raw, and the worker would fail
    the round after rendering it), a non-string text, an unknown engine."""
    if not isinstance(piece, Mapping) or not piece.get("id"):
        raise ValueError("narration: enqueue_for_piece needs the piece row (with its id)")
    if engine not in c.ENGINES:
        raise ValueError(f"narration: unknown engine {engine!r} (known: {c.ENGINES})")
    blocks = narration_blocks(content)
    if not blocks:
        raise ValueError(f"narration: piece {piece.get('id')} has nothing to narrate")
    for b in blocks:
        if not b.text.strip() or not sentence_split(b.text):
            raise ValueError(f"narration: block {b.i} ({b.dest}) has no speakable text")
        words = len(b.text.split())
        if words > c.MAX_PARAGRAPH_WORDS:
            raise ValueError(f"narration: block {b.i} ({b.dest}) has {words} words — over the "
                             f"{c.MAX_PARAGRAPH_WORDS}-word block cap (Chatterbox truncates at 40 s)")
    sha = script_sha256(blocks)
    voice = voice_key(voice_spec(engine))
    job = await ledger.enqueue_job(piece["id"], round=round, script=script_json(blocks), script_sha256=sha,
                                   engine=engine, voice=voice, pipeline_version=PIPELINE_VERSION)
    if job is not None:
        logger.info("learn narration: piece %s (%s) round %d → job %s (%s, %d blocks, %d words, script %s)",
                    piece.get("id"), piece.get("period_key"), round, job.get("id"), engine, len(blocks),
                    sum(len(b.text.split()) for b in blocks), sha[:12])
    return job


# ── the worker's claim ───────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkerClaim:
    """`X-Learn-Claim: <attempts>.<nonce>` — the claim the worker got from `/jobs/claim` (the job's
    `attempts` after the claim and the `claim_nonce` it minted). Every later call presents it and
    every ledger write is fenced on both (a re-claimed zombie writes nothing)."""
    attempts: int
    nonce: str

    @classmethod
    def parse(cls, header: Optional[str]) -> "WorkerClaim":
        if not header:
            raise ValueError("missing claim")
        head, sep, nonce = header.strip().partition(".")
        if not sep or not head.isascii() or not head.isdigit() or len(head) > 6:
            raise ValueError("malformed claim (expected <attempts>.<nonce>)")
        attempts = int(head)
        if attempts < 1 or not _NONCE_RE.fullmatch(nonce):
            raise ValueError("malformed claim (attempts ≥ 1, nonce = 16 lowercase hex)")
        return cls(attempts, nonce)

    def holds(self, row: Mapping[str, Any]) -> bool:
        stored = row.get("claim_nonce")
        attempts = row.get("attempts")
        return (not isinstance(attempts, bool) and isinstance(attempts, int) and attempts == self.attempts
                and isinstance(stored, str) and hmac.compare_digest(stored, self.nonce))


async def _job_or_404(job_id: Any, *, op: str) -> Dict[str, Any]:
    job = await ledger.get_job(job_id)
    if job is None:
        raise LearnNarratorNotFound(f"{op}: no narration job {str(job_id)[:40]!r}", job_id=job_id, op=op)
    return job


def _not_held(job: Mapping[str, Any], claim: WorkerClaim, *, op: str) -> LearnNarratorJobNotHeld:
    why = ("it was cancelled (the piece closed or moved on)" if job.get("status") == c.JOB_CANCELLED
           else f"it is {job.get('status')} at claim {job.get('attempts')}")
    return LearnNarratorJobNotHeld(f"{op}: claim {claim.attempts} no longer holds the job — {why}",
                                   job_id=job.get("id"), op=op, status=job.get("status"))


async def _held_job(job_id: Any, claim: WorkerClaim, *, op: str) -> Dict[str, Any]:
    """The job row when `claim` is its current in_progress claim; else 404 (unknown id) / 409."""
    row = await ledger.job_claim_ok(job_id, claim.attempts, claim.nonce)
    if row is not None:
        return row
    raise _not_held(await _job_or_404(job_id, op=op), claim, op=op)


async def claim_for_worker(engines: Sequence[str], *, worker_version: str,
                           now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """`POST /jobs/claim`: the oldest job whose FROZEN engine the worker declares (`ledger.claim_job`
    — the engine is the job's, never the worker's environment), as the worker's view of it, or None
    (204). A job whose piece can no longer want narration (terminal or live) is cancelled instead of
    handed out; a job whose stored script cannot be read is failed (it would fail every render)."""
    at = now or _now()
    for _ in range(3):  # a cancelled or unreadable job does not cost the worker its cron tick
        job = await ledger.claim_job(list(engines), now=at)
        if job is None:
            logger.info("learn narration: nothing to claim for engines %s (worker %s)", list(engines),
                        _excerpt(worker_version))
            return None
        claim = WorkerClaim(int(job["attempts"]), str(job["claim_nonce"]))
        piece = await ledger.get_piece_by_id(job.get("piece_id"))
        if piece is None or piece.get("status") in _NARRATION_DEAD:
            logger.warning("learn narration: job %s (round %s) belongs to piece %s which is %s — cancelling "
                           "its open jobs instead of rendering", job.get("id"), job.get("round"),
                           job.get("piece_id"), None if piece is None else piece.get("status"))
            await ledger.cancel_open_jobs(job.get("piece_id"), reason="piece_closed_before_render", now=at)
            continue
        try:
            blocks = blocks_from_script(job.get("script"), expected_sha256=job.get("script_sha256"))
            voice = parse_voice_key(job.get("voice"))
        except ValueError as e:
            logger.error("learn narration: job %s (piece %s round %s) has an unreadable script or voice — "
                         "failing it rather than rendering: %s", job.get("id"), job.get("piece_id"),
                         job.get("round"), e)
            failed = await ledger.fail_job(job["id"], claim.attempts, claim.nonce,
                                           error=f"script_unreadable: {e}", now=at)
            if failed is not None and failed.get("status") == c.JOB_FAILED:
                await after_job_failed(failed, now=at)
            continue
        logger.info("learn narration: worker %s claimed job %s (piece %s round %s, %s, %d blocks, attempt %d)",
                    _excerpt(worker_version), job.get("id"), job.get("piece_id"), job.get("round"),
                    job.get("engine"), len(blocks), claim.attempts)
        return {
            "id": str(job["id"]), "round": int(job["round"]), "engine": job["engine"], "voice": voice,
            "pipeline_version": job["pipeline_version"], "attempts": claim.attempts,
            "claim_nonce": claim.nonce, "lease_until": job.get("lease_until"),
            "blocks": [b.wire() for b in blocks],
        }
    return None


async def heartbeat(job_id: Any, claim: WorkerClaim, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """`POST /jobs/{id}/heartbeat`: extend the lease. 409 once the job was cancelled / re-claimed —
    the worker must stop rendering."""
    row = await ledger.heartbeat_job(job_id, claim.attempts, claim.nonce, now=now or _now())
    if row is not None:
        return {"ok": True, "lease_until": row.get("lease_until")}
    raise _not_held(await _job_or_404(job_id, op="heartbeat"), claim, op="heartbeat")


# ── Storage (sync bodies, always run through asyncio.to_thread) ──────────────────────────────


def _object_stat_sync(bucket: str, path: str) -> Optional[Dict[str, Any]]:
    """`{"size", "mimetype"}` of `bucket/path` as Storage recorded the upload, or None when ABSENT.
    Read from a prefix LIST (a listing carries each object's metadata; a HEAD does not) — the port of
    `run_service._object_stat` with the bucket as a parameter. An outage raises (→ 503, retried),
    never "absent"."""
    prefix, _, name = path.rpartition("/")
    try:
        listing = get_supabase().storage.from_(bucket).list(prefix, {"limit": 100, "search": name})
    except Exception as e:
        raise LearnNarratorLedgerError(f"storage LIST failed for {bucket}/{path}: {type(e).__name__}: {e}",
                                       op="object_stat") from e
    for item in listing or []:
        if isinstance(item, dict) and item.get("name") == name:
            md = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            return {"size": md.get("size", md.get("contentLength")), "mimetype": md.get("mimetype")}
    return None


def _remove_object_sync(bucket: str, path: str) -> None:
    try:
        get_supabase().storage.from_(bucket).remove([path])
    except Exception as e:
        raise LearnNarratorLedgerError(f"storage DELETE failed for {bucket}/{path}: {type(e).__name__}: {e}",
                                       op="remove_object") from e


def _signed_upload_url_sync(bucket: str, path: str) -> str:
    """A signed upload URL for `bucket/path` (a write credential: never logged)."""
    try:
        signed = get_supabase().storage.from_(bucket).create_signed_upload_url(path)
    except Exception as e:
        raise LearnNarratorLedgerError(f"signed upload URL failed for {bucket}/{path}: {type(e).__name__}",
                                       op="signed_upload") from e
    url = (signed or {}).get("signed_url") or (signed or {}).get("signedUrl")
    if not isinstance(url, str) or not url:
        raise LearnNarratorLedgerError(f"signed upload URL for {bucket}/{path} came back empty", op="signed_upload")
    return url


def object_problems(stat: Mapping[str, Any], *, want_size: int, want_type: str, path: str) -> List[str]:
    """What is wrong with an object against what was registered ([] = it matches). Size and content
    type as `run_service._verify_object` compares them (`; charset` parameters ignored). An UNKNOWN
    size or type is not a mismatch: LearnNarratorLedgerError (503, retried, nothing deleted)."""
    size, mimetype = stat.get("size"), stat.get("mimetype")
    if size is None or not mimetype:
        raise LearnNarratorLedgerError(f"storage reported no size/content type for {path} (size={size!r}, "
                                       f"mimetype={mimetype!r}); not accepting it yet", op="verify_object")
    try:
        got_size = int(size)
    except (TypeError, ValueError):
        raise LearnNarratorLedgerError(f"storage reported a non-numeric size {size!r} for {path}",
                                       op="verify_object") from None
    got_type = str(mimetype).split(";", 1)[0].strip().lower()
    problems = []
    if got_size != int(want_size):
        problems.append(f"size {got_size} != registered {want_size}")
    if got_type != want_type:
        problems.append(f"content type {got_type!r} != registered {want_type!r}")
    return problems


def bytes_per_second_problem(engine: str, size_bytes: int, duration_seconds: float) -> Optional[str]:
    lo, hi = BYTES_PER_SECOND_BAND.get(engine, BYTES_PER_SECOND_BAND[c.ENGINE_CHATTERBOX])
    rate = size_bytes / duration_seconds
    if lo <= rate <= hi:
        return None
    return (f"{rate:,.0f} bytes/s is outside the {engine} band {lo:,.0f}–{hi:,.0f} "
            f"({size_bytes} bytes over {duration_seconds:.1f} s)")


# ── upload / complete / fail ─────────────────────────────────────────────────────────────────


def _slug_of(piece: Mapping[str, Any], *, op: str) -> str:
    slug = piece.get("slug")
    if not (isinstance(slug, str) and _SLUG_RE.fullmatch(slug)):
        raise LearnNarratorRequestInvalid(f"{op}: piece {piece.get('id')} has no slug yet — it cannot hold audio",
                                          piece_id=piece.get("id"), op=op)
    return slug


async def _piece_or_404(job: Mapping[str, Any], *, op: str) -> Dict[str, Any]:
    piece = await ledger.get_piece_by_id(job.get("piece_id"))
    if piece is None:
        raise LearnNarratorNotFound(f"{op}: the piece of job {job.get('id')} does not exist",
                                    job_id=job.get("id"), piece_id=job.get("piece_id"), op=op)
    return piece


def _int_in(value: Any, lo: int, hi: int, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise LearnNarratorRequestInvalid(f"{where} must be an integer in [{lo}, {hi}], got {value!r}")
    return value


async def upload_target(job_id: Any, claim: WorkerClaim, *, content_type: Any, size_bytes: Any,
                        sha256: Any) -> Dict[str, Any]:
    """`POST /jobs/{id}/upload`: where the worker PUTs the audio. A signed upload URL for
    `audio/weekly/<slug>-<sha12>.m4a` in the private bucket — or, when that object is already there
    with the same size and type (a retried tick after its PUT landed), the path with
    `upload_url: None`. An object of ANOTHER size at the content-addressed path is refused and left
    in place (409): it is not this upload, and deleting by a worker's word could remove audio a
    published article plays."""
    if content_type != AUDIO_CONTENT_TYPE:
        raise LearnNarratorRequestInvalid(f"upload: content_type must be {AUDIO_CONTENT_TYPE!r}, got "
                                          f"{_excerpt(content_type)!r}", job_id=job_id, op="upload")
    size = _int_in(size_bytes, 1, MAX_AUDIO_UPLOAD_BYTES, "upload: bytes")
    if not (isinstance(sha256, str) and _SHA256_RE.fullmatch(sha256)):
        raise LearnNarratorRequestInvalid("upload: sha256 must be 64 lowercase hex characters",
                                          job_id=job_id, op="upload")
    job = await _held_job(job_id, claim, op="upload")
    piece = await _piece_or_404(job, op="upload")
    path = weekly_audio_path(_slug_of(piece, op="upload"), sha256)
    stat = await asyncio.to_thread(_object_stat_sync, AUDIO_BUCKET, path)
    if stat is not None:
        problems = object_problems(stat, want_size=size, want_type=AUDIO_CONTENT_TYPE, path=path)
        if problems:
            logger.error("learn narration upload: %s already holds ANOTHER object (%s) — job %s (piece %s) "
                         "refused, object left in place", path, "; ".join(problems), job.get("id"), piece.get("id"))
            raise LearnNarratorAssetMismatch(f"upload: {path} already holds another object ({'; '.join(problems)})",
                                             job_id=job.get("id"), op="upload")
        logger.info("learn narration upload: %s already in the bucket with the same size — no re-upload "
                    "(job %s)", path, job.get("id"))
        return {"upload_url": None, "path": path, "content_type": AUDIO_CONTENT_TYPE, "already_uploaded": True}
    url = await asyncio.to_thread(_signed_upload_url_sync, AUDIO_BUCKET, path)
    logger.info("learn narration upload: signed an upload of %d bytes to %s/%s for job %s (piece %s)",
                size, AUDIO_BUCKET, path, job.get("id"), piece.get("id"))
    return {"upload_url": url, "path": path, "content_type": AUDIO_CONTENT_TYPE, "already_uploaded": False}


def _checks(checks: Any) -> Dict[str, Any]:
    if checks is None:
        return {}
    if not isinstance(checks, Mapping):
        raise LearnNarratorRequestInvalid(f"complete: checks must be an object, got {type(checks).__name__}")
    try:
        text = json.dumps(checks, allow_nan=False, default=str)
    except ValueError as e:
        raise LearnNarratorRequestInvalid(f"complete: checks must be JSON without NaN/inf ({e})") from None
    if len(text) > 64_000:
        raise LearnNarratorRequestInvalid(f"complete: checks is {len(text)} characters (cap 64,000)")
    return json.loads(text)


async def complete(job_id: Any, claim: WorkerClaim, *, path: Any, size_bytes: Any, duration_seconds: Any,
                   engine: Any, pipeline_version: Any, timings: Any, checks: Any = None,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """`POST /jobs/{id}/complete`. In order:
      1. a CANCELLED job, or a replay of a completion that already landed under this claim, answers
         200 and writes nothing (a replay only re-attempts the fenced piece write when the piece is
         still waiting on this very job — the crash-between-writes recovery);
      2. the claim must hold the job (409), and the request must name the job's frozen engine and
         pipeline and a path of this piece's slug (422);
      3. the timing table must pass `validate_timings` (422) — before any Storage I/O;
      4. the object must be in the bucket (409 ASSET_MISSING), with the registered size and type and a
         bytes/s inside the engine's band — else it is DELETED and the round failed (409
         ASSET_MISMATCH); an unknown size/type deletes nothing (503, retried);
      5. `ledger.complete_job`, then the piece `media_pending → ready_for_review` with the merged
         draft and its new draft_sha256 — fenced on status, `narration_job_id` and the draft it read.
    """
    at = now or _now()
    op = "complete"
    job = await _job_or_404(job_id, op=op)
    status = job.get("status")
    if status == c.JOB_CANCELLED:
        logger.info("learn narration complete: job %s was cancelled — the render is discarded, nothing "
                    "written (piece %s)", job.get("id"), job.get("piece_id"))
        return {"ok": True, "status": c.JOB_CANCELLED, "replay": False, "piece_updated": False}
    if status == c.JOB_READY and claim.holds(job):
        logger.info("learn narration complete: job %s is already ready under claim %d — a replay",
                    job.get("id"), claim.attempts)
        updated = await _finish_piece(job, op="complete_replay")
        return {"ok": True, "status": c.JOB_READY, "replay": True, "piece_updated": updated}
    if status != c.JOB_IN_PROGRESS or not claim.holds(job):
        raise _not_held(job, claim, op=op)
    ids = {"job_id": job.get("id"), "piece_id": job.get("piece_id"), "op": op}
    if engine != job.get("engine"):
        raise LearnNarratorRequestInvalid(f"complete: engine {_excerpt(engine)!r} is not the job's frozen "
                                          f"{job.get('engine')!r}", **ids)
    if pipeline_version != job.get("pipeline_version"):
        raise LearnNarratorRequestInvalid(f"complete: pipeline_version {_excerpt(pipeline_version)!r} is not the "
                                          f"job's {job.get('pipeline_version')!r}", **ids)
    try:
        blocks = blocks_from_script(job.get("script"), expected_sha256=job.get("script_sha256"))
    except ValueError as e:
        raise LearnNarratorRequestInvalid(f"complete: the job's stored script is unusable ({e})", **ids) from None
    piece = await _piece_or_404(job, op=op)
    slug = _slug_of(piece, op=op)
    m = _WEEKLY_AUDIO_RE.fullmatch(path) if isinstance(path, str) else None
    if m is None or m.group(1) != slug:
        raise LearnNarratorRequestInvalid(f"complete: path {_excerpt(path)!r} is not a weekly narration path of "
                                          f"slug {slug!r}", **ids)
    size = _int_in(size_bytes, 1, MAX_AUDIO_UPLOAD_BYTES, "complete: bytes")
    if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)) \
            or not math.isfinite(float(duration_seconds)) or not 0 < float(duration_seconds) <= MAX_AUDIO_SECONDS:
        raise LearnNarratorRequestInvalid(f"complete: duration_seconds must be a finite number in (0, "
                                          f"{MAX_AUDIO_SECONDS:.0f}], got {duration_seconds!r}", **ids)
    duration = float(duration_seconds)
    norm = validate_timings(blocks, timings, duration_seconds=duration)
    worker_checks = _checks(checks)

    stat = await asyncio.to_thread(_object_stat_sync, AUDIO_BUCKET, path)
    if stat is None:
        raise LearnNarratorAssetMissing(f"complete: {AUDIO_BUCKET}/{path} is not in the bucket — upload it first",
                                        **ids)
    problems = object_problems(stat, want_size=size, want_type=AUDIO_CONTENT_TYPE, path=path)
    if not problems:
        rate = bytes_per_second_problem(str(job.get("engine")), size, duration)
        if rate:
            problems.append(rate)
    if problems:
        detail = "; ".join(problems)
        logger.error("learn narration complete: job %s (piece %s round %s) audio MISMATCH at %s: %s — deleting "
                     "the object and failing the round", job.get("id"), job.get("piece_id"), job.get("round"),
                     path, detail)
        await asyncio.to_thread(_remove_object_sync, AUDIO_BUCKET, path)
        failed = await ledger.fail_job(job["id"], claim.attempts, claim.nonce,
                                       error=f"asset_mismatch: {detail}", now=at)
        if failed is not None and failed.get("status") == c.JOB_FAILED:
            await after_job_failed(failed, now=at)
        raise LearnNarratorAssetMismatch(f"complete: {path} is not what the job registered ({detail}); object "
                                         "deleted, round failed", **ids)

    server_checks = {**timing_stats(norm), "bytes_per_second": round(size / duration, 1),
                     "pipeline_version": PIPELINE_VERSION}
    done = await ledger.complete_job(job["id"], claim.attempts, claim.nonce, audio_path=path, audio_bytes=size,
                                     duration_seconds=duration, timings=norm,
                                     checks={"worker": worker_checks, "server": server_checks}, now=at)
    if done is None:
        cur = await _job_or_404(job_id, op=op)
        if cur.get("status") == c.JOB_CANCELLED:
            logger.info("learn narration complete: job %s was cancelled while completing — nothing written",
                        cur.get("id"))
            return {"ok": True, "status": c.JOB_CANCELLED, "replay": False, "piece_updated": False}
        raise _not_held(cur, claim, op=op)
    updated = await _finish_piece(done, op=op)
    return {"ok": True, "status": c.JOB_READY, "replay": False, "piece_updated": updated}


async def finish_ready_job(job: Mapping[str, Any]) -> bool:
    """The scheduler's sweep for a READY job whose piece never moved: `complete` commits the job
    before it writes the piece, so a Supabase failure between the two writes (or a `/fail` the
    ledger refused for a READY job) would otherwise leave the piece `media_pending` until its hold
    ends. Same fenced, idempotent merge as `complete`."""
    return await _finish_piece(job, op="sweep")


async def _finish_piece(job: Mapping[str, Any], *, op: str) -> bool:
    """Merge a READY job's timings into its piece and move it `media_pending → ready_for_review`.
    Fenced on the status, `narration_job_id` = this job and the draft_sha256 it read, so a piece that
    moved on (regenerated, expired, another round) is never written. True when the piece moved."""
    piece = await ledger.get_piece_by_id(job.get("piece_id"))
    if piece is None:
        logger.warning("learn narration %s: job %s is ready but its piece %s no longer exists", op,
                       job.get("id"), job.get("piece_id"))
        return False
    if piece.get("status") != c.MEDIA_PENDING or str(piece.get("narration_job_id")) != str(job.get("id")):
        logger.info("learn narration %s: job %s is ready; piece %s is %s waiting on job %s — not updated", op,
                    job.get("id"), piece.get("id"), piece.get("status"), piece.get("narration_job_id"))
        return False
    draft = piece.get("draft")
    try:
        blocks = blocks_from_script(job.get("script"), expected_sha256=job.get("script_sha256"))
        if not isinstance(draft, dict) or script_sha256(narration_blocks(draft)) != job.get("script_sha256"):
            logger.error("learn narration %s: piece %s's draft is no longer the article job %s narrated — the "
                         "audio is kept on the job but NOT merged; the piece stays media_pending", op,
                         piece.get("id"), job.get("id"))
            return False
        merged = merge_timings(draft, blocks, job.get("timings"), duration_seconds=job.get("duration_seconds"),
                               audio_url=audio_public_url(str(job.get("audio_path"))))
    except Exception as e:  # a stored job that no longer validates is a bug: loud, never a half write
        logger.error("learn narration %s: could not merge job %s into piece %s: %s: %s", op, job.get("id"),
                     piece.get("id"), type(e).__name__, e, exc_info=True)
        return False
    row = await ledger.transition(piece["id"], from_statuses=(c.MEDIA_PENDING,), to_status=c.READY_FOR_REVIEW,
                                  fields={"draft": merged, "draft_sha256": draft_sha256(merged),
                                          "last_stage": "narration"},
                                  fence={"narration_job_id": str(job.get("id")),
                                         "draft_sha256": piece.get("draft_sha256")})
    if row is None:
        logger.info("learn narration %s: piece %s moved on before job %s's audio was merged", op,
                    piece.get("id"), job.get("id"))
        return False
    logger.info("learn narration: piece %s (%s) READY FOR REVIEW with job %s's audio (%s s)", piece.get("id"),
                piece.get("period_key"), job.get("id"), job.get("duration_seconds"))
    return True


async def fail(job_id: Any, claim: WorkerClaim, *, code: Any, detail: Any = "",
               now: Optional[datetime] = None) -> Dict[str, Any]:
    """`POST /jobs/{id}/fail`: the worker gives up on this render. `worker_failures + 1`; the job is
    re-queued while under MAX_JOB_WORKER_FAILURES and failed at it, and a failed job moves its piece
    (`after_job_failed`). A cancelled job answers 200 and writes nothing; a replay of a recorded
    failure re-runs only the fenced piece step."""
    if not (isinstance(code, str) and _FAIL_CODE_RE.fullmatch(code)):
        raise LearnNarratorRequestInvalid(f"fail: code must match {_FAIL_CODE_RE.pattern}, got {_excerpt(code)!r}",
                                          job_id=job_id, op="fail")
    if not isinstance(detail, str):
        raise LearnNarratorRequestInvalid("fail: detail must be a string", job_id=job_id, op="fail")
    at = now or _now()
    row = await ledger.fail_job(job_id, claim.attempts, claim.nonce, error=f"{code}: {detail}", now=at)
    if row is None:
        cur = await _job_or_404(job_id, op="fail")
        if cur.get("status") == c.JOB_CANCELLED:
            logger.info("learn narration fail: job %s was already cancelled — nothing written", cur.get("id"))
            return {"ok": True, "status": c.JOB_CANCELLED, "worker_failures": cur.get("worker_failures")}
        raise _not_held(cur, claim, op="fail")
    if row.get("status") == c.JOB_FAILED:
        await after_job_failed(row, now=at)
    return {"ok": True, "status": row.get("status"), "worker_failures": row.get("worker_failures")}


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def after_job_failed(job: Mapping[str, Any], *, now: Optional[datetime] = None) -> None:
    """A narration job ended `failed` (the worker's cap, an asset mismatch at the cap, an unreadable
    script). If its piece is still `media_pending` on THIS job: while inside the engine's catch-up
    cut-off (`schedule.catch_up_cutoff`, no build time — the draft exists) and under
    MAX_AUTO_FAILED_ROUNDS failed rounds, queue the next round (max round + 1, the CURRENT
    LEARN_NARRATOR_ENGINE) and point the piece at it; otherwise the piece is `failed` and the owner
    gets the loud alert. Every write is fenced on `narration_job_id` = this job, so a replay — or the
    scheduler calling this for a job the ledger closed at its re-claim cap — is idempotent. Public for
    that sweep: `ledger.claim_job` closes an exhausted job without touching its piece."""
    at = now or _now()
    piece = await ledger.get_piece_by_id(job.get("piece_id"))
    if piece is None:
        logger.warning("learn narration: failed job %s has no piece %s", job.get("id"), job.get("piece_id"))
        return
    if piece.get("status") != c.MEDIA_PENDING or str(piece.get("narration_job_id")) != str(job.get("id")):
        logger.info("learn narration: failed job %s — piece %s is %s on job %s; nothing to move", job.get("id"),
                    piece.get("id"), piece.get("status"), piece.get("narration_job_id"))
        return
    error = " ".join(str(job.get("error") or "unspecified").split())[:300]
    engine = current_engine(fallback=job.get("engine"))
    reason: Optional[str] = None
    try:
        blocks = blocks_from_script(job.get("script"))
        words = sum(len(b.text.split()) for b in blocks)
    except ValueError:
        words = None   # catch_up_cutoff assumes the longest Deep Dive (fail-safe) for junk
    hold = _parse_ts(piece.get("hold_until"))
    jobs = await ledger.list_jobs(piece["id"])
    failed_rounds = sum(1 for j in jobs if j.get("status") == c.JOB_FAILED)
    if hold is None:
        reason = "narration_failed_hold_unreadable"
    elif failed_rounds >= MAX_AUTO_FAILED_ROUNDS:
        reason = "narration_rounds_exhausted"
    elif at >= schedule.catch_up_cutoff(hold, engine, words, build_seconds=0):
        reason = "narration_cutoff_passed"
    if reason is None:
        next_round = max([int(j.get("round") or 0) for j in jobs] + [int(job.get("round") or 0)]) + 1
        new_job = None
        try:
            new_job = await enqueue_for_piece(piece, piece.get("draft"), engine=engine, round=next_round)
        except ValueError as e:
            logger.error("learn narration: piece %s cannot be re-narrated: %s", piece.get("id"), e)
            reason = "narration_draft_unnarratable"
        if new_job is not None:
            row = await ledger.transition(piece["id"], from_statuses=(c.MEDIA_PENDING,), to_status=c.MEDIA_PENDING,
                                          fields={"narration_job_id": str(new_job["id"]),
                                                  "last_error": f"narration round {job.get('round')} failed: {error}"},
                                          fence={"narration_job_id": str(job.get("id"))})
            if row is not None:
                logger.warning("learn narration: piece %s (%s) round %s FAILED (%s) — round %d queued as job %s "
                               "(%s); %d failed round(s) so far", piece.get("id"), piece.get("period_key"),
                               job.get("round"), error, next_round, new_job.get("id"), engine, failed_rounds)
            else:
                logger.info("learn narration: piece %s moved on while round %d was queued", piece.get("id"),
                            next_round)
            return
        if reason is None:
            reason = "narration_requeue_refused"
    row = await ledger.transition(piece["id"], from_statuses=(c.MEDIA_PENDING,), to_status=c.FAILED,
                                  fields={"last_error": f"{reason}: round {job.get('round')}: {error}",
                                          "last_stage": "narration"},
                                  fence={"narration_job_id": str(job.get("id"))})
    if row is None:
        logger.info("learn narration: piece %s moved on before it could be failed (%s)", piece.get("id"), reason)
        return
    logger.error("learn narration: piece %s (%s) FAILED at narration — %s (round %s: %s)", piece.get("id"),
                 piece.get("period_key"), reason, job.get("round"), error)
    await _notify_terminal(row, status=c.FAILED, reason=reason, stage="narration")


async def _notify_terminal(piece: Mapping[str, Any], *, status: str, reason: str, stage: Optional[str]) -> None:
    """`alerts.notify_terminal` (the review module's loud owner alert), imported at call time so this
    module never depends on the alert module's import graph. Never raises: an alert that cannot be
    sent is logged at ERROR — the piece's own failure is already recorded."""
    try:
        alerts = importlib.import_module("app.services.learn_weekly.alerts")
        await alerts.notify_terminal(piece, status=status, reason=reason, stage=stage)
    except Exception as e:
        logger.error("learn narration: the %s alert for piece %s (%s, %s) could not be sent: %s: %s", status,
                     piece.get("id"), piece.get("period_key"), reason, type(e).__name__, e, exc_info=True)
