#!/usr/bin/env python
"""Re-narrate a PUBLISHED Wiser weekly piece on the Railway narrator and swap the new audio onto its
live Money Moves article.

⚠️ WRITES PRODUCTION (`learn_narration_jobs`, the live `money_move_articles` row, `learn_weekly_pieces`)
with the service-role key in backend/.env. Without `--yes` it only reads and prints what it would do.

    cd backend && ./venv/bin/python scripts/learn_weekly_renarrate.py --period <period key> --enqueue
    cd backend && ./venv/bin/python scripts/learn_weekly_renarrate.py --period <period key> --enqueue --yes
    #   … the narrator renders it; Telegram: "🎙 Weekly re-narration READY: <slug> …"
    cd backend && ./venv/bin/python scripts/learn_weekly_renarrate.py --slug <slug> --swap
    cd backend && ./venv/bin/python scripts/learn_weekly_renarrate.py --slug <slug> --swap --yes

(`--period <key>` and `--slug <slug>` name the same piece; the Telegram line carries the slug.)

WHY IT EXISTS
-------------
The narrator's tempo pass used to count WHITESPACE tokens, so a figure-dense piece looked slow and was
sped up: W44's 161 figure tokens ("$57.0B", "12.3%") are several spoken words each, and its render
recorded atempo 1.188 (`checks.worker.worker.atempo`). The narrator now counts SPOKEN words
(`learn_narrator/engines/chatterbox.py`, pipeline v2) and caps the tempo at 0.80–1.10. This tool re-renders
a PUBLISHED piece's live text on Railway and swaps the result in, for a piece that needs it.

⛔ NOT W44 — "No W44 re-render (owner approved clip B)". Its live clip was slowed by hand on 2026-11-01
(atempo 0.772, read-along ×1.295, 931 s; approved by ear). A v2 render lands ≈ 871 s, about 6% FASTER
than that clip, so `--swap` refuses it (see --swap step 2). The 2026-11-01 "Yes, re-render it" that
started this tool was overtaken by that approval; the tool stays for a future piece.

A published piece cannot get a new round of its OWN job: `narration.claim_for_worker` cancels every
job whose piece is published (`_NARRATION_DEAD`). So this script uses the CATALOG job path (migration
207: a job with no piece, keyed by `catalog_slug`) with the piece's slug: the worker renders it like any
job and uploads to `audio/catalog/<slug>-<sha12>.m4a`; the server's READY line names `--swap`
(`narration.is_weekly_renarration`).

--enqueue (pure checks first, then ONE write with --yes)
---------
1. The piece is `published` and live (dry_run false); its `money_move_articles` row is ours (source
   weekly, id = uuid5 of the slug) with readable content and `updated_at`.
2. The narration = `narration.narration_blocks(<the LIVE content>)` — what listeners read (byline, art
   and tagLabel are not narrated); a blank block or one over the Chatterbox block cap is refused
   (`check_narratable`, as a weekly enqueue refuses it).
3. Refused: while a job of the slug is queued / in_progress; when a READY round already narrates this
   exact text (`--swap` it, or `--force`); while a weekly piece is building / drafted / media_pending,
   or between Thursday 18:00 ET and Saturday 00:00 ET (the narrator renders one job at a time and must
   never delay the Friday Deep Dive) — those two unless `--force`.
4. `ledger.enqueue_catalog_job_sync` — round = 1 + the slug's highest round, the Chatterbox voice
   (`narration.CHATTERBOX_VOICE`), the frozen script / sha and `narration.PIPELINE_VERSION`.
   It prints the expected new length, worked out from the OLD RENDER's own numbers
   (`checks.worker.file.duration`, `checks.worker.worker.atempo`), and that length against the live clip
   separately: the live file may not be that render (a promote trims it, a hand fix re-times it).

--swap (pure checks first, then the writes with --yes)
------
1. The newest READY catalog job of the slug; the LIVE content must still narrate exactly its script
   (`script_sha256` — the text was not edited since the enqueue: a changed transcript drifts the
   highlight); its object is in the bucket with the registered size and type.
2. `narration.merge_timings(live content, the job's script, its timings, duration, audio_url=<the
   catalog object's PUBLIC-form URL>)` — the server's own timing gate runs again; then the catalog
   gates of `learn_catalog_fetch.py` (every in-block pause < 3.0 s, every sentence's seconds per word in
   the server's band, the file's bytes/s and SPOKEN-word pace in the catalog voice bands). A render more
   than 2% SHORTER than the live clip is refused unless `--allow-shorter`: the text is the same, so
   shorter means faster, and the clip being replaced may be one the owner approved by ear. Only the
   narration fields may change (readAlong / itemsReadAlong, audioUrl, audioDurationSeconds,
   hasAudioVersion): art, tagLabel, byline, text — everything else stays byte for byte.
3. ONE conditional UPDATE of the live row (`audio_url`, `audio_duration_seconds`, `content`,
   `updated_at`), fenced on its slug, `source = 'weekly'`, its id and the `updated_at` read in step 1.
   The old audio object is KEPT (a phone that cached it, or a rollback, still finds it).
4. Best effort: the same merge onto the piece's own draft (published → published, fields draft /
   draft_sha256 / last_stage / validation.renarrate), fenced on the draft_sha256 and updated_at it
   read, so the ledger matches the live row. A rerun of `--swap` is idempotent: an already-swapped
   live row is left alone and only a missing mirror is written.

The app shows the new audio once the Money Moves cache refreshes (its TTL, ≤ 1 h, or a restart):
then probe with `scripts/check_money_moves_published.py`.

ORDER: migration 207 applied → the web deployed (the catalog branch of `learn_weekly/narration.py` and
its weekly READY line) → the narrator redeployed with chatterbox pipeline v2 (never while a job
renders) → `--enqueue --yes` → Telegram READY → `--swap --yes`. A narrator still on v1 renders the SAME
too-fast file: the web refuses it at `complete` on the spoken-word pace band, and `--swap` would too.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

#: The fields of a live article a re-narration may change (top level) — everything else is refused.
NARRATION_TOP_KEYS = frozenset({"audioUrl", "audioDurationSeconds", "hasAudioVersion"})
#: … and inside each content block.
NARRATION_BLOCK_KEYS = frozenset({"readAlong", "itemsReadAlong"})
#: What `--swap` writes into the piece's `last_stage`.
LAST_STAGE = "renarrate"
#: How much shorter than the live clip a render may land before `--swap` wants `--allow-shorter`: the
#: text is identical, so anything past rounding and a different pause layout is simply faster speech.
SHORTER_TOLERANCE = 0.02
ALREADY_MIRRORED = "the piece's draft already carries this narration"
_SIBLINGS: Dict[str, ModuleType] = {}


def _sibling(name: str) -> ModuleType:
    """A sibling script loaded by path (`scripts/` is not a package): the catalog scripts' window and
    gates, used as they are — never copied — so the two paths cannot drift."""
    if name not in _SIBLINGS:
        path = Path(__file__).resolve().with_name(f"{name}.py")
        spec = importlib.util.spec_from_file_location(f"_renarrate_{name}", path)
        if spec is None or spec.loader is None:
            raise SystemExit(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _SIBLINGS[name] = module
    return _SIBLINGS[name]


def in_deep_dive_window(now: datetime) -> bool:
    """Thursday 18:00 ET ≤ now < Saturday 00:00 ET — `learn_catalog_narrate.in_deep_dive_window`."""
    return bool(_sibling("learn_catalog_narrate").in_deep_dive_window(now))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


# ── pure checks ──────────────────────────────────────────────────────────────────────────────


def check_piece(piece: Optional[Mapping[str, Any]], who: str) -> str:
    """The piece's slug when it is a LIVE published weekly piece whose slug a re-narration can key on;
    SystemExit naming why not."""
    from app.services.learn_weekly import contracts as C
    from app.services.learn_weekly import ledger, narration

    if piece is None:
        raise SystemExit(f"no learn_weekly_pieces row for {who}")
    key, status = piece.get("period_key"), piece.get("status")
    if status == C.PUBLISHING:
        raise SystemExit(f"{key} is being published right now — rerun in a minute")
    if status != C.PUBLISHED:
        raise SystemExit(f"{key} is {status!r} — only a PUBLISHED piece is re-narrated here (a piece in "
                         "review gets its narration from the build; Retry narration is the Telegram button)")
    if piece.get("dry_run") is not False:
        raise SystemExit(f"{key} has dry_run={piece.get('dry_run')!r} — a rehearsal is never live")
    slug = piece.get("slug")
    try:
        ledger.validate_catalog_slug(slug)
    except ValueError as e:
        raise SystemExit(f"{key}: {e}")
    if not narration.is_weekly_renarration({"piece_id": None, "catalog_slug": slug}):
        raise SystemExit(f"{key}: slug {slug!r} does not end in a publish date — not a weekly piece's slug")
    return str(slug)


def check_live(piece: Mapping[str, Any], live: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The live row's content when the row is this piece's own weekly row, readable and fenceable;
    SystemExit otherwise (never an unfenced write, never a write over unreadable content)."""
    from app.services.learn_weekly import publisher

    key, slug = piece.get("period_key"), piece.get("slug")
    if not live or live.get("slug") != slug or live.get("source") != publisher.SOURCE_WEEKLY or \
            str(live.get("id")) != publisher.article_id(str(slug)):
        raise SystemExit(f"{key} is published but money_move_articles holds no weekly row of its own for {slug!r}")
    content = live.get("content")
    if not isinstance(content, Mapping) or not isinstance(content.get("sections"), list) or not live.get("updated_at"):
        raise SystemExit(f"{key}: the live row's content or updated_at is unreadable — nothing done")
    return copy.deepcopy(dict(content))


def word_counts(blocks: Sequence[Any]) -> Tuple[int, int]:
    """(whitespace tokens — narrator v1's tempo basis, words the voice says — v2's) over the blocks."""
    from learn_narrator import timings as tm

    return sum(len(b.text.split()) for b in blocks), sum(tm.spoken_word_count(b.text) for b in blocks)


def estimate_render(old_job: Optional[Mapping[str, Any]], blocks: Sequence[Any]) -> Optional[Dict[str, float]]:
    """{"atempo", "seconds"} of a v2 render of `blocks`, or None when the old render's numbers are
    unreadable. The raw speech is the old render's OWN: its file length (less the block pauses, which the
    tempo pass keeps at their length) × its atempo — never the live row's length, because the live file
    may not be that render (a promote trims it, a hand fix re-times it: W44's live 931 s is not the 727 s
    render that recorded 1.188). Every block keeps its seed, so the speech is the same; a live text
    shorter than the old script (the trimmed closing) keeps its share of it, by spoken words. An
    estimate: printed, never a check."""
    from app.services.learn_weekly import narration
    from learn_narrator.engines import chatterbox as cb

    old_atempo, rendered = _old_atempo(old_job), render_seconds(old_job)
    if old_atempo is None or old_atempo <= 0 or rendered is None or not blocks:
        return None
    try:
        old_blocks = narration.blocks_from_script(old_job.get("script") if old_job else None)
    except ValueError:
        return None
    old_spoken, spoken = word_counts(old_blocks)[1], word_counts(blocks)[1]
    if old_spoken <= 0 or spoken <= 0:
        return None
    raw = (rendered - cb.BLOCK_PAUSE * (len(old_blocks) - 1)) * old_atempo * spoken / old_spoken
    if raw <= 0:
        return None
    atempo = max(cb.ATEMPO_RANGE[0], min(cb.ATEMPO_RANGE[1], cb.TARGET_WPM * raw / (spoken * 60)))
    return {"atempo": atempo, "seconds": round(raw / atempo + cb.BLOCK_PAUSE * (len(blocks) - 1), 1)}


def projected_max_spw(old_job: Optional[Mapping[str, Any]], blocks: Sequence[Any]) -> Optional[float]:
    """The slowest sentence's seconds per word after the re-render: the old round's `checks.server.max_spw`
    stretched by old ÷ new atempo (every span scales with the tempo). Over the server's SPW_BAND ceiling,
    the re-render would fail `timings_invalid` — printed as a warning before the ~1 h render."""
    checks = old_job.get("checks") if isinstance(old_job, Mapping) else None
    server = checks.get("server") if isinstance(checks, Mapping) else None
    max_spw = server.get("max_spw") if isinstance(server, Mapping) else None
    old_atempo, estimate = _old_atempo(old_job), estimate_render(old_job, blocks)
    if not _finite(max_spw) or estimate is None or old_atempo is None:
        return None
    return round(float(max_spw) * old_atempo / estimate["atempo"], 3)


def _reported_checks(job: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The payload the worker sent with the render: `checks.worker`. `narration.complete` stores
    {"worker": <that payload>, "server": <its own>}, and the payload holds `blocks`, `file`, `summary`
    and its own `worker` block (`learn_narrator/main.py`)."""
    checks = job.get("checks") if isinstance(job, Mapping) else None
    reported = checks.get("worker") if isinstance(checks, Mapping) else None
    return reported if isinstance(reported, Mapping) else {}


def _worker_checks(job: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The render's own numbers: `checks.worker.worker` (atempo, atempo_clamped, engine_pipeline, …)."""
    worker = _reported_checks(job).get("worker")
    return worker if isinstance(worker, Mapping) else {}


def render_seconds(job: Optional[Mapping[str, Any]]) -> Optional[float]:
    """The length of the file the worker rendered (`checks.worker.file.duration`), or None. Not the job's
    `duration_seconds`: a promote or a hand fix repoints that at a later file, while `checks` still
    describes the render whose atempo it records."""
    file = _reported_checks(job).get("file")
    value = file.get("duration") if isinstance(file, Mapping) else None
    return float(value) if _finite(value) and value > 0 else None


def _old_atempo(job: Optional[Mapping[str, Any]]) -> Optional[float]:
    value = _worker_checks(job).get("atempo")
    return float(value) if _finite(value) else None


def engine_pipeline(job: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The narrator pipeline that rendered a READY job (`checks.worker.worker.engine_pipeline`), or None."""
    value = _worker_checks(job).get("engine_pipeline")
    return value if isinstance(value, str) and value else None


def current_pipeline() -> str:
    """The Chatterbox pipeline this checkout's narrator renders with (v2 counts spoken words)."""
    from learn_narrator.engines import chatterbox as cb

    return cb.PIPELINE_VERSION


def _rounds(jobs: Iterable[Mapping[str, Any]]) -> List[int]:
    return [j["round"] for j in jobs if _is_int(j.get("round"))]


def plan_enqueue(piece: Mapping[str, Any], live: Optional[Mapping[str, Any]], *, jobs: Iterable[Mapping[str, Any]],
                 busy_pieces: Iterable[Mapping[str, Any]], now: datetime, force: bool = False,
                 old_job: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Everything `--enqueue` writes, or SystemExit naming why it must not run. Pure."""
    from app.services.learn_weekly import contracts as C
    from app.services.learn_weekly import narration

    slug = check_piece(piece, str(piece.get("period_key")))
    content = check_live(piece, live)
    try:
        blocks = narration.narration_blocks(content)
        narration.check_narratable(blocks, subject=f"weekly piece {piece.get('period_key')}")
    except ValueError as e:
        raise SystemExit(f"{slug} cannot be narrated: {e}")
    sha = narration.script_sha256(blocks)
    jobs = [dict(j) for j in jobs if j.get("catalog_slug") == slug and j.get("piece_id") is None]
    open_jobs = [j for j in jobs if j.get("status") in (C.JOB_QUEUED, C.JOB_IN_PROGRESS)]
    if open_jobs:
        j = open_jobs[0]
        raise SystemExit(f"{slug}: job {j.get('id')} (round {j.get('round')}) is still {j.get('status')} — wait "
                         "for it (the narrator renders one job at a time)")
    # A READY round of this exact text by the CURRENT narrator pipeline needs no new render (swap it); one
    # rendered by an older pipeline (a narrator not yet redeployed with v2) is exactly what must be redone.
    ready_same = [j for j in jobs if j.get("status") == C.JOB_READY and j.get("script_sha256") == sha
                  and engine_pipeline(j) == current_pipeline()]
    if ready_same and not force:
        j = max(ready_same, key=lambda r: r["round"] if _is_int(r.get("round")) else -1)
        raise SystemExit(f"{slug}: round {j.get('round')} is already READY for this exact text (rendered by "
                         f"{engine_pipeline(j)}) — run --swap (or --force to render it again)")
    busy = [p for p in busy_pieces if p.get("status") in (C.BUILDING, C.DRAFTED, C.MEDIA_PENDING)]
    if busy and not force:
        names = ", ".join(f"{p.get('period_key')} ({p.get('status')})" for p in busy[:3])
        raise SystemExit(f"a weekly piece is being built or narrated: {names} — the narrator renders one job at a "
                         "time; enqueue after it reaches review (or --force)")
    if in_deep_dive_window(now) and not force:
        raise SystemExit("it is the Deep Dive window (Thursday 18:00 ET to Saturday 00:00 ET): a re-narration "
                         "must not delay the Friday Deep Dive — enqueue on Saturday (or --force)")
    engine = C.ENGINE_CHATTERBOX         # the catalog voice the piece was narrated in
    written, spoken = word_counts(blocks)
    current = live.get("audio_duration_seconds") if live else None
    estimate = estimate_render(old_job, blocks)
    return {
        "slug": slug, "round": max(_rounds(jobs), default=0) + 1, "script": narration.script_json(blocks),
        "script_sha256": sha, "engine": engine, "voice": narration.voice_key(narration.voice_spec(engine)),
        "pipeline_version": narration.PIPELINE_VERSION, "blocks": len(blocks), "words": written,
        "spoken_words": spoken, "current_seconds": current if _finite(current) else None,
        "render_seconds": render_seconds(old_job), "old_atempo": _old_atempo(old_job),
        "new_atempo": estimate["atempo"] if estimate else None,
        "expected_seconds": estimate["seconds"] if estimate else None,
        "projected_max_spw": projected_max_spw(old_job, blocks),
        "render_minutes": round(written / C.NARRATION_WPM * 60 * C.RENDER_FACTOR[engine] / 60),
    }


def pick_ready(jobs: Iterable[Mapping[str, Any]], slug: str) -> Tuple[Dict[str, Any], List[str]]:
    """(the newest READY catalog job of `slug`, warnings about later rounds). SystemExit when none is
    ready, naming every round."""
    from app.services.learn_weekly import contracts as C

    rows = [dict(j) for j in jobs if j.get("catalog_slug") == slug and j.get("piece_id") is None]

    def rnd(j: Mapping[str, Any]) -> int:
        return j["round"] if _is_int(j.get("round")) else -1

    ready = [j for j in rows if j.get("status") == C.JOB_READY]
    if not ready:
        seen = ", ".join(f"round {j.get('round')}: {j.get('status')}"
                         + (f" ({str(j.get('error'))[:120]})" if j.get("error") else "") for j in rows)
        raise SystemExit(f"{slug}: no READY re-narration job ({seen or 'no job at all'}) — --enqueue queues one")
    job = max(ready, key=rnd)
    warnings = [f"round {j.get('round')} is {j.get('status')} — swapping in the newest READY round {job.get('round')}"
                for j in rows if rnd(j) > rnd(job)]
    return job, warnings


def _without_narration(content: Mapping[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(dict(content))
    for key in NARRATION_TOP_KEYS:
        out.pop(key, None)
    for section in out.get("sections") or []:
        if isinstance(section, dict):
            for block in section.get("content") or []:
                if isinstance(block, dict):
                    for key in NARRATION_BLOCK_KEYS:
                        block.pop(key, None)
    return out


def only_narration_changed(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    """Do `before` and `after` differ in the narration fields alone?"""
    return _without_narration(before) == _without_narration(after)


def shorter_problem(live: Mapping[str, Any], seconds: float) -> Optional[str]:
    """Why a render of `seconds` must not replace the live clip without `--allow-shorter`, or None. The
    script hash proved the text is the same, so a shorter file is FASTER speech, and the clip it replaces
    may be one the owner approved by ear: W44's clip B (931 s) would have lost to a ≈ 871 s render that
    passed every band. No live audio → nothing to protect; a live clip whose length cannot be read cannot
    be compared, so it asks too."""
    if not live.get("audio_url"):
        return None
    current = live.get("audio_duration_seconds")
    if not _finite(current) or current <= 0:
        return f"the live clip's length is unreadable ({current!r}), so this render cannot be compared with it"
    if seconds >= float(current) * (1 - SHORTER_TOLERANCE):
        return None
    return (f"this render is {seconds:.0f} s, {1 - seconds / float(current):.1%} SHORTER than the live clip "
            f"({float(current):.0f} s): the same text, so faster speech")


def plan_swap(piece: Mapping[str, Any], live: Optional[Mapping[str, Any]], job: Mapping[str, Any],
              stat: Optional[Mapping[str, Any]], *, allow_shorter: bool = False) -> Dict[str, Any]:
    """{"content", "audio_url", "seconds", "duration", "bytes", "blocks", "live_done", "shorter"} — the
    live row as it must become — or SystemExit naming why the render must not go live. Pure (`stat` is
    the object's Storage listing, read by the caller)."""
    from app.services.learn_weekly import contracts as C
    from app.services.learn_weekly import narration
    from app.services.learn_weekly.errors import LearnNarratorLedgerError, LearnNarratorRequestInvalid

    slug = check_piece(piece, str(piece.get("period_key")))
    content = check_live(piece, live)
    jid = job.get("id")
    if job.get("status") != C.JOB_READY or job.get("piece_id") is not None or job.get("catalog_slug") != slug:
        raise SystemExit(f"job {jid} is not a READY re-narration job of {slug}")
    path = job.get("audio_path")
    if not narration.is_catalog_audio_path(path, slug):
        raise SystemExit(f"job {jid} has audio_path {path!r}, not a catalog narration object of {slug}")
    size, duration = job.get("audio_bytes"), job.get("duration_seconds")
    if not _is_int(size) or size <= 0:
        raise SystemExit(f"job {jid} has an unreadable audio_bytes {size!r}")
    if not _finite(duration) or not 0 < float(duration):
        raise SystemExit(f"job {jid} has an unreadable duration_seconds {duration!r}")
    try:
        blocks = narration.blocks_from_script(job.get("script"), expected_sha256=job.get("script_sha256"))
    except ValueError as e:
        raise SystemExit(f"job {jid}: {e}")
    try:
        current = narration.script_sha256(narration.narration_blocks(content))
    except ValueError as e:
        raise SystemExit(f"the live article cannot be narrated: {e}")
    if current != job.get("script_sha256"):
        raise SystemExit(f"the live article's text changed after round {job.get('round')} was enqueued (script "
                         f"{current[:12]} ≠ the job's {str(job.get('script_sha256'))[:12]}) — the audio would not "
                         f"match the transcript; re-narrate: --enqueue")
    if stat is None:
        raise SystemExit(f"job {jid}'s audio {path} is not in the bucket")
    try:
        mismatch = narration.object_problems(stat, want_size=size, want_type=narration.AUDIO_CONTENT_TYPE, path=path)
    except LearnNarratorLedgerError as e:
        raise SystemExit(f"Storage cannot vouch for {path} yet: {e}")
    if mismatch:
        raise SystemExit(f"{path} in the bucket is not the job's object: {'; '.join(mismatch)}")
    rate = narration.bytes_per_second_problem(str(job.get("engine") or C.ENGINE_CHATTERBOX), size, float(duration))
    if rate:
        raise SystemExit(f"the render fails the voice's encode band: {rate}")
    audio_url = narration.catalog_audio_public_url(path)
    try:
        merged = narration.merge_timings(content, blocks, job.get("timings"), duration_seconds=float(duration),
                                         audio_url=audio_url)
    except (LearnNarratorRequestInvalid, ValueError) as e:
        raise SystemExit(f"the job's timings do not merge: {type(e).__name__}: {e}")
    if not only_narration_changed(content, merged):
        raise SystemExit("the merge would change more than the narration fields — nothing done")
    fetch = _sibling("learn_catalog_fetch")
    problems = fetch.read_along_problems(merged) + fetch.voice_band_problems(merged, size)
    if problems:
        raise SystemExit("the render does not pass the catalog gates:\n  " + "\n  ".join(problems))
    seconds = merged["audioDurationSeconds"]
    live_done = (live.get("audio_url") == audio_url and live.get("content") == merged
                 and live.get("audio_duration_seconds") == seconds)
    shorter = None if live_done else shorter_problem(live, float(duration))
    if shorter and not allow_shorter:
        raise SystemExit(f"refusing the swap: {shorter}. If the live clip is the one you want, stop here; if you "
                         "really want this render, add --allow-shorter")
    return {"content": merged, "audio_url": audio_url, "seconds": seconds, "duration": float(duration),
            "bytes": size, "blocks": blocks, "live_done": live_done, "shorter": shorter}


def mirror_draft(piece: Mapping[str, Any], plan: Mapping[str, Any], job: Mapping[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """(the piece's draft with the same narration merged in, "") — or (None, why it is not written).
    Pure. Only the narration fields may change, as on the live row."""
    from app.services.learn_weekly import narration
    from app.services.learn_weekly.errors import LearnNarratorRequestInvalid

    draft = piece.get("draft")
    if not isinstance(draft, Mapping):
        return None, "the piece's draft is unreadable"
    try:
        merged = narration.merge_timings(draft, plan["blocks"], job.get("timings"), duration_seconds=plan["duration"],
                                         audio_url=plan["audio_url"])
    except (LearnNarratorRequestInvalid, ValueError) as e:
        return None, f"the piece's draft does not narrate the live text ({type(e).__name__}: {e})"
    if not only_narration_changed(draft, merged):
        return None, "the merge would change more than the draft's narration fields"
    if merged == draft:
        return None, ALREADY_MIRRORED
    return merged, ""


# ── I/O seams (tests replace these) ──────────────────────────────────────────────────────────


def _piece_by_period(period: str) -> Optional[Dict[str, Any]]:
    from app.services.learn_weekly import ledger

    try:
        return ledger.get_piece_sync(period)
    except ValueError as e:
        raise SystemExit(f"--period {period!r}: {e}")


def _piece_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    from app.services.learn_weekly import ledger

    rows = [p for p in ledger.list_pieces_sync(limit=1000) if p.get("slug") == slug]
    if len(rows) > 1:
        raise SystemExit(f"{len(rows)} pieces carry slug {slug!r} — use --period")
    return rows[0] if rows else None


def _read_live(slug: str) -> Optional[Dict[str, Any]]:
    from app.database import get_supabase
    from app.services.learn_weekly import publisher

    res = (get_supabase().table(publisher.ARTICLES)
           .select("id,slug,source,audio_url,audio_duration_seconds,content,updated_at")
           .eq("slug", slug).limit(1).execute())
    data = getattr(res, "data", None) or []
    if isinstance(data, dict):
        data = [data]
    return data[0] if data and isinstance(data[0], dict) else None


def _jobs(slug: str) -> List[Dict[str, Any]]:
    from app.services.learn_weekly import ledger

    return ledger.list_catalog_jobs_sync(slug)


def _busy_pieces() -> List[Dict[str, Any]]:
    from app.services.learn_weekly import contracts as C
    from app.services.learn_weekly import ledger

    return ledger.list_pieces_sync(statuses=[C.BUILDING, C.DRAFTED, C.MEDIA_PENDING], limit=50)


def _old_job(piece: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    from app.services.learn_weekly import ledger

    jid = piece.get("narration_job_id")
    return ledger.get_job_sync(jid) if jid else None


def _enqueue(plan: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    from app.services.learn_weekly import ledger

    return ledger.enqueue_catalog_job_sync(plan["slug"], round=plan["round"], script=plan["script"],
                                           script_sha256=plan["script_sha256"], engine=plan["engine"],
                                           voice=plan["voice"], pipeline_version=plan["pipeline_version"])


def _stat(path: str) -> Optional[Dict[str, Any]]:
    from app.services.learn_weekly import narration

    return narration._object_stat_sync(narration.AUDIO_BUCKET, path)


def _update_live(slug: str, *, audio_url: str, audio_duration_seconds: int, content: Mapping[str, Any],
                 observed_updated_at: str, now: datetime) -> Optional[Dict[str, Any]]:
    """One conditional UPDATE of the live weekly row: matched on its slug, `source = 'weekly'`, its own
    id and the `updated_at` read before (a concurrent change wins and this returns None)."""
    from app.database import get_supabase
    from app.services.learn_weekly import publisher

    res = (get_supabase().table(publisher.ARTICLES)
           .update({"audio_url": audio_url, "audio_duration_seconds": int(audio_duration_seconds),
                    "content": dict(content), "updated_at": publisher._iso(now)})
           .eq("slug", slug).eq("source", publisher.SOURCE_WEEKLY).eq("id", publisher.article_id(slug))
           .eq("updated_at", observed_updated_at).execute())
    data = getattr(res, "data", None) or []
    return data[0] if data and isinstance(data[0], dict) else None


def _transition(piece_id: Any, **kw: Any) -> Optional[Dict[str, Any]]:
    from app.services.learn_weekly import ledger

    return ledger.transition_sync(piece_id, **kw)


# ── the two steps ────────────────────────────────────────────────────────────────────────────


def _fmt_seconds(value: Any) -> str:
    return f"{float(value):.0f} s" if _finite(value) else "?"


def enqueue(piece: Mapping[str, Any], args: argparse.Namespace) -> int:
    from app.services.learn_weekly.ledger import LearnLedgerError

    slug = check_piece(piece, args.who)
    live = _read_live(slug)
    try:
        jobs, busy = _jobs(slug), _busy_pieces()
        old = _old_job(piece)
    except LearnLedgerError as e:
        raise SystemExit(f"could not read the narration ledger (is migration 207 applied?): {e}")
    plan = plan_enqueue(piece, live, jobs=jobs, busy_pieces=busy, now=datetime.now(timezone.utc), force=args.force,
                        old_job=old)
    ratio = plan["spoken_words"] / plan["words"] if plan["words"] else float("nan")
    print(f"{piece.get('period_key')} ({slug}): re-narration round {plan['round']} — {plan['blocks']} blocks, "
          f"{plan['words']} words written / {plan['spoken_words']} spoken ({ratio:.2f}×), render ≈"
          f"{plan['render_minutes']} min on the narrator")
    print(f"  engine {plan['engine']} · voice {plan['voice']} · {plan['pipeline_version']} · "
          f"script {plan['script_sha256'][:12]}")
    print(f"  live audio now {_fmt_seconds(plan['current_seconds'])}")
    if plan["expected_seconds"] is None:
        print("  the old render's length or atempo is not recorded — no estimate of the new length")
    else:
        print(f"  the old render: {_fmt_seconds(plan['render_seconds'])} at atempo {plan['old_atempo']:.3f}; expected "
              f"after the re-render ≈ {_fmt_seconds(plan['expected_seconds'])} at atempo ≈ {plan['new_atempo']:.3f} "
              "— the narrator's v2 tempo, counted on spoken words")
        shorter = shorter_problem(live or {}, float(plan["expected_seconds"]))
        if plan["current_seconds"]:
            print(f"  against the live clip: {plan['expected_seconds'] / float(plan['current_seconds']) - 1:+.1%}")
        if shorter:
            print(f"  ⚠️  {shorter}. --swap will refuse it without --allow-shorter — is a re-render wanted at all?")
    if plan["projected_max_spw"] is not None:
        from app.services.learn_weekly import narration

        hi = narration.SPW_BAND[1]
        flag = "⚠️  OVER the server's ceiling — the render would fail timings_invalid; split that sentence first" \
            if plan["projected_max_spw"] > hi else "inside the server's band"
        print(f"  slowest sentence after the re-render ≈ {plan['projected_max_spw']:.3f} s/word (ceiling {hi}): {flag}")
    if not args.yes:
        print("dry run — nothing written (add --yes)")
        return 0
    try:
        job = _enqueue(plan)
    except LearnLedgerError as e:
        raise SystemExit(f"the enqueue failed (is migration 207 applied?): {e}")
    if job is None:
        print(f"round {plan['round']} of {slug} already holds a job for ANOTHER script (see the ERROR log) — nothing "
              "queued; re-run to take the next round")
        return 1
    print(f"queued narration job {job.get('id')} ({job.get('status')}). The narrator claims it on its next tick "
          f"(a ~1 h render); when Telegram says READY: scripts/learn_weekly_renarrate.py --slug {slug} --swap")
    return 0


def swap(piece: Mapping[str, Any], args: argparse.Namespace) -> int:
    from app.services.learn_weekly import narration
    from app.services.learn_weekly.errors import LearnNarratorLedgerError
    from app.services.learn_weekly.ledger import LearnLedgerError

    slug = check_piece(piece, args.who)
    live = _read_live(slug)
    try:
        job, warnings = pick_ready(_jobs(slug), slug)
    except LearnLedgerError as e:
        raise SystemExit(f"could not read the narration ledger (is migration 207 applied?): {e}")
    for w in warnings:
        print(f"⚠️  {w}")
    path = str(job.get("audio_path"))
    try:
        stat = _stat(path) if narration.is_catalog_audio_path(path, slug) else None
    except LearnNarratorLedgerError as e:
        raise SystemExit(f"could not list {path} in Storage: {e}")
    plan = plan_swap(piece, live, job, stat, allow_shorter=args.allow_shorter)
    worker = _worker_checks(job)
    print(f"{piece.get('period_key')} ({slug}): round {job.get('round')} — {path} ({plan['bytes']} bytes, "
          f"{plan['duration']:.1f} s; live now {_fmt_seconds(live.get('audio_duration_seconds'))})")
    print(f"  rendered by {engine_pipeline(job) or '?'} at atempo {worker.get('atempo')!r}"
          + (" (CLAMPED — the pace band still passed)" if worker.get("atempo_clamped") else ""))
    if engine_pipeline(job) != current_pipeline():
        print(f"  ⚠️  not this checkout's narrator pipeline ({current_pipeline()}) — the gates above passed it anyway")
    if plan["shorter"]:
        print(f"  ⚠️  --allow-shorter: {plan['shorter']}")
    print(f"  live row: {'already points at this render' if plan['live_done'] else 'audio_url, audio_duration_seconds, content read-along'}")
    draft, why = mirror_draft(piece, plan, job)
    print(f"  piece draft: {'gains the same narration' if draft is not None else why}")
    if not args.yes:
        print("dry run — nothing written (add --yes)")
        return 0
    now = datetime.now(timezone.utc)
    if not plan["live_done"]:
        row = _update_live(slug, audio_url=plan["audio_url"], audio_duration_seconds=plan["seconds"],
                           content=plan["content"], observed_updated_at=str(live["updated_at"]), now=now)
        if row is None:
            print("the live row changed while this ran (lost compare-and-swap) — nothing written; rerun")
            return 1
        print(f"  live row now plays {path} ({plan['seconds']} s); the old audio "
              f"{live.get('audio_url') or '(none)'} is kept")
    if draft is None:
        done = why == ALREADY_MIRRORED
        print(f"{'' if done else '⚠️  '}SWAPPED: {slug} — the piece's ledger draft was not written ({why}). The app "
              "shows the new audio once the Money Moves cache refreshes (≤ 1 h, or a restart); then "
              "scripts/check_money_moves_published.py")
        return 0
    validation = dict(piece.get("validation") or {}) if isinstance(piece.get("validation"), Mapping) else {}
    # What the live row played before: unknown on a mirror-only rerun (an earlier run already swapped it).
    before = (None, None) if plan["live_done"] else (live.get("audio_url"), live.get("audio_duration_seconds"))
    validation["renarrate"] = {
        "at": now.isoformat(), "by": "owner (--swap --yes)", "job_id": str(job.get("id")),
        "round": job.get("round"), "audio_path": path, "duration_seconds": plan["duration"],
        "from_audio_url": before[0], "from_duration_seconds": before[1], "live_swapped_earlier": plan["live_done"],
        "engine_pipeline": worker.get("engine_pipeline"), "atempo": worker.get("atempo"),
        "allowed_shorter": bool(plan["shorter"]),
    }
    try:
        moved = _transition(piece["id"], from_statuses=[piece["status"]], to_status=piece["status"],
                            fields={"draft": draft, "draft_sha256": narration.draft_sha256(draft),
                                    "last_stage": LAST_STAGE, "validation": validation},
                            fence={"draft_sha256": piece.get("draft_sha256"), "updated_at": piece.get("updated_at")})
    except (LearnLedgerError, ValueError) as e:
        print(f"⚠️  the live row is swapped, but the piece's ledger draft was NOT updated ({type(e).__name__}: {e}) — "
              f"rerun --swap (it leaves the live row alone and writes only the mirror)")
        return 2
    if moved is None:
        print("⚠️  the live row is swapped, but the piece moved on while this ran (lost compare-and-swap) — its "
              "ledger draft is unchanged; rerun --swap to mirror it")
        return 2
    print(f"SWAPPED: {slug} — live row and ledger draft carry round {job.get('round')}'s narration. The app shows it "
          "once the Money Moves cache refreshes (≤ 1 h, or a restart); then scripts/check_money_moves_published.py")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Re-narrate a published weekly piece and swap its live audio "
                                            "(WRITES PRODUCTION).")
    who = p.add_mutually_exclusive_group(required=True)
    who.add_argument("--period", help="e.g. 2026-W44:deep_dive")
    who.add_argument("--slug", help="the piece's slug (the Telegram READY line names it)")
    step = p.add_mutually_exclusive_group(required=True)
    step.add_argument("--enqueue", action="store_true", help="queue the re-render on the narrator")
    step.add_argument("--swap", action="store_true", help="point the live article at the newest READY render")
    p.add_argument("--force", action="store_true",
                   help="--enqueue only: during the Deep Dive window, beside a weekly build, or over a READY round")
    p.add_argument("--allow-shorter", action="store_true",
                   help="--swap only: replace the live clip with a render more than 2%% shorter (faster speech)")
    p.add_argument("--yes", action="store_true", help="actually write (default: print only)")
    args = p.parse_args(argv)
    if args.force and args.swap:
        raise SystemExit("--force applies to --enqueue only")
    if args.allow_shorter and args.enqueue:
        raise SystemExit("--allow-shorter applies to --swap only")
    args.who = args.period or args.slug
    piece = _piece_by_period(args.period) if args.period else _piece_by_slug(args.slug)
    if piece is None:
        raise SystemExit(f"no learn_weekly_pieces row for {args.who}")
    if args.slug and piece.get("slug") != args.slug:
        raise SystemExit(f"the piece found for {args.slug!r} carries slug {piece.get('slug')!r}")
    return enqueue(piece, args) if args.enqueue else swap(piece, args)


if __name__ == "__main__":
    raise SystemExit(main())
