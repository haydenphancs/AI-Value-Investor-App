"""`scripts/learn_weekly_renarrate.py` — re-narrating a PUBLISHED weekly piece at the corrected pace.

The narrator's tempo pass counted whitespace tokens, so W44's figures ("$57.0B" is six spoken words) made
its native pace look slow and the file was sped up 1.188×. The worker now counts spoken words (pipeline
v2, tests/test_learn_narrator_worker.py); this script re-renders a live text through a CATALOG job keyed
by the piece's slug (a published piece's own job would be cancelled at claim) and swaps the result onto
the live `money_move_articles` row. W44 itself stays on the hand-slowed clip B the owner approved: a v2
render would be shorter and faster than it, which is exactly what the swap's length guard refuses.

It WRITES PRODUCTION, so the guards are the point: nothing is written without `--yes`; the enqueue
refuses an unpublished / rehearsal piece, a foreign live row, an open job, a READY render of the same
text by the current pipeline, a weekly build in flight and the Deep Dive window; the swap refuses a render
whose text no longer matches the live article, a missing / different object, timings that do not merge,
the catalog's pause / seconds-per-word gates, the SPOKEN-word pace band (a v1 render of W44 fails it)
and, without `--allow-shorter`, a render shorter than the live clip. The live UPDATE changes only the
narration fields and is fenced; the piece mirror is fenced too; a rerun is idempotent. Plus the server
half: the catalog public URL and the weekly READY / FAILED lines.

The jobs here carry `checks` in the shape the real writers store; `test_the_readers_follow_what_the_real
_worker_and_complete_store` runs a real worker tick through the real `narration.complete` to keep it so.

Hermetic: Supabase and Storage go through the script's I/O seams or the ledger test's FakeSupabase.
"""

from __future__ import annotations

import asyncio
import builtins
import copy
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import test_learn_narrator_worker as wt
import test_learn_weekly_ledger as ledger_fake
import test_learn_weekly_narration as nt
from app.config import settings
from app.services import learn_audio_urls
from app.services.learn_weekly import contracts as C
from app.services.learn_weekly import ledger, narration, publisher
from app.services.learn_weekly.errors import LearnNarratorLedgerError
from app.services.learn_weekly.narration import WorkerClaim
from learn_narrator import main as worker_main
from learn_narrator import timings as tm
from learn_narrator.engines import chatterbox as cb

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "learn_weekly_renarrate.py"
UTC = timezone.utc
SLUG = "semiconductor-giants-and-ai-infrastructure-2026-10-30"
PERIOD = "2026-W44:deep_dive"
SB_URL = "https://x.supabase.co"
SAT = datetime(2026, 11, 7, 15, 0, tzinfo=UTC)               # Sat 10:00 ET: outside the Deep Dive window
FRI = datetime(2026, 11, 6, 13, 0, tzinfo=UTC)               # Fri 08:00 ET: inside it
OLD_URL = f"{SB_URL}/storage/v1/object/public/money-moves-media/audio/weekly/{SLUG}-34a8d0184084.m4a"
HERO = f"{SB_URL}/storage/v1/object/public/money-moves-images/articles/{SLUG}.hero.jpg"
LIVE_TS = "2026-11-01T18:00:00.123456+00:00"
#: The live clip's length in these fixtures, a little under `_ready_job`'s 155-wpm render of `_content()`.
LIVE_SECONDS = 37
PIECE_ID = "9a8b7c6d-0000-4000-8000-000000000044"
OLD_JOB_ID = "078b6690-0000-4000-8000-000000000001"


@pytest.fixture(autouse=True)
def _supabase_url(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", SB_URL)


def _load():
    spec = importlib.util.spec_from_file_location("learn_weekly_renarrate_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = _load()


def _content(**over) -> Dict[str, Any]:
    """The LIVE row's content, as `publisher.build_article_row` wrote it (W44-like: figure-dense)."""
    c = {
        "slug": SLUG, "title": "Semiconductor Giants and AI Infrastructure",
        "subtitle": "How chip makers supply the data centre build-out.",
        "category": "battles", "isFeatured": True, "sortOrder": -202644, "publishedAt": "2026-11-01T17:45:00Z",
        "readTimeMinutes": 12, "viewCount": "", "tagLabel": "THE SUPPLIERS",
        "author": {"name": "Caydex Research", "title": "Editorial", "isVerified": False},
        "imageUrl": HERO, "imageCardUrl": HERO.replace(".hero.", ".card."),
        "audioUrl": OLD_URL, "hasAudioVersion": True, "audioDurationSeconds": LIVE_SECONDS,
        "sections": [
            {"title": "The Numbers", "icon": "chart.bar.fill", "content": [
                {"type": "paragraph",
                 "text": "Revenue rose 12.3% to $57.0B in fiscal 2025, while gross margin reached 74.6%. "
                         "Data center sales were $41.1B, up 56% from $26.3B a year earlier.",
                 "readAlong": [{"text": "old", "start": 0.0, "end": 1.0}]},
                {"type": "callout", "style": "info", "text": "Capital spending hit $15.2B in 2024 and is guided "
                                                            "to $18.5B, roughly 21% of sales."},
            ]},
            {"title": "Who Buys", "icon": "person.3.fill", "content": [
                {"type": "bulletList", "items": ["Its three biggest customers bought 36%, 19% and 12% of output.",
                                                 "Inventory climbed to $8.9B, about 117 days of supply."]},
                {"type": "paragraph", "text": "Long agreements steady revenue. Short ones expose it to every cycle "
                                              "the industry has seen since 2001."},
            ]},
        ],
    }
    c.update(over)
    return c


def _draft() -> Dict[str, Any]:
    """The piece's own ledger draft: the live content minus what the publisher overlays."""
    d = _content()
    for k in ("sortOrder", "publishedAt"):
        d.pop(k)
    return d


def _piece(**over) -> Dict[str, Any]:
    draft = _draft()
    p = {"id": PIECE_ID, "period_key": PERIOD, "kind": "deep_dive", "status": C.PUBLISHED, "dry_run": False,
         "slug": SLUG, "title": draft["title"], "draft": draft, "draft_sha256": narration.draft_sha256(draft),
         "narration_job_id": OLD_JOB_ID, "updated_at": "2026-11-01T17:46:00+00:00", "validation": {"x": 1}}
    p.update(over)
    return p


def _live(content=None, **over) -> Dict[str, Any]:
    row = {"id": publisher.article_id(SLUG), "slug": SLUG, "source": "weekly", "audio_url": OLD_URL,
           "audio_duration_seconds": LIVE_SECONDS, "content": content if content is not None else _content(),
           "updated_at": LIVE_TS}
    row.update(over)
    return row


def _stored_checks(*, atempo: Any, pipeline: Any, duration: Any, clamped: bool = False,
                   max_spw: Any = None) -> Dict[str, Any]:
    """`checks` as the ledger holds it: the worker's payload (through its real `bounded_checks`) inside the
    {"worker", "server"} that `narration.complete` writes. So the render's numbers are at
    `checks.worker.worker` and the file's at `checks.worker.file`."""
    payload = worker_main.bounded_checks({
        "blocks": [], "file": {"f0_hz": 134.0, "wpm": 157.0, "bytes_per_sec": 12_600, "lufs": -16.0,
                               "duration": duration},
        "summary": {"blocks": 0},
        "worker": {"engine_pipeline": pipeline, "atempo": atempo, "atempo_wanted": atempo,
                   "atempo_clamped": clamped, "tempo_words": "spoken"}})
    return {"worker": narration._checks(payload), "server": {"max_spw": max_spw}}


def _old_job(content=None, *, atempo=1.188, seconds=None, max_spw=0.62) -> Dict[str, Any]:
    """The piece's published round: v1 sped it up `atempo`× (job 078b6690… checks.worker.worker.atempo).
    `seconds` = the file it rendered; by default what v1's tempo pass made of `content`: speech at 165
    TOKENS a minute, plus the block pauses."""
    blocks = narration.narration_blocks(content if content is not None else _content())
    if seconds is None:
        seconds = sum(len(b.text.split()) for b in blocks) / cb.TARGET_WPM * 60 + cb.BLOCK_PAUSE * (len(blocks) - 1)
    return {"id": OLD_JOB_ID, "piece_id": PIECE_ID, "status": C.JOB_READY, "round": 1,
            "script": narration.script_json(blocks), "script_sha256": narration.script_sha256(blocks),
            "checks": _stored_checks(atempo=atempo, pipeline="chatterbox-0.1.7/block/aac96k/v1", duration=seconds,
                                     max_spw=max_spw)}


def _timings(blocks, *, wpm: float = 155.0, start=0.4, gap=0.6, pause=0.2, extra=None):
    """A valid timing table whose sentences take time by SPOKEN words, sized so the whole file lands at
    `wpm` spoken words per minute; `extra[(block i, sentence k)]` = a longer pause before that sentence.
    Returns (table, end)."""
    extra = extra or {}
    sents = [(b, s) for b in blocks for s in narration.sentence_split(b.text)]
    words = sum(tm.spoken_word_count(s) for _, s in sents)
    pauses = start + gap * (len(blocks) - 1) + pause * (len(sents) - len(blocks)) + \
        sum(v - pause for v in extra.values())
    per = (words * 60.0 / wpm - pauses) / words
    t, out = start, []
    for b in blocks:
        b0, spans = t, []
        for k, s in enumerate(narration.sentence_split(b.text)):
            if k and (b.i, k) in extra:
                t += extra[(b.i, k)] - pause
            d = tm.spoken_word_count(s) * per
            spans.append({"text": s, "start": round(t, 3), "end": round(t + d, 3)})
            t += d + pause
        out.append({"i": b.i, "start": round(b0, 3), "end": round(t - pause, 3), "sentences": spans})
        t += gap
    return out, round(t - gap, 3)


def _ready_job(content=None, *, round=1, wpm=155.0, bps=12_600, pipeline=None, extra=None, **over):
    """A READY re-narration job as the ledger holds it after `narration.complete` (timings normalised)."""
    content = content if content is not None else _content()
    blocks = narration.narration_blocks(content)
    t, end = _timings(blocks, wpm=wpm, extra=extra)
    norm = narration.validate_timings(blocks, t, duration_seconds=end)
    size = int(builtins.round(end) * bps)              # `round` is the job's round here
    job = {"id": f"00000000-0000-4000-8000-0000000000{round:02d}", "status": C.JOB_READY, "piece_id": None,
           "catalog_slug": SLUG, "round": round, "script": narration.script_json(blocks),
           "script_sha256": narration.script_sha256(blocks), "engine": "chatterbox",
           "audio_path": narration.catalog_audio_path(SLUG, f"{round:02d}" * 32), "audio_bytes": size,
           "duration_seconds": end, "timings": norm, "error": None,
           "checks": _stored_checks(atempo=0.93, pipeline=pipeline or cb.PIPELINE_VERSION, duration=end)}
    job.update(over)
    return job


def _stat(job) -> Dict[str, Any]:
    return {"size": job["audio_bytes"], "mimetype": "audio/mp4"}


# ═════════════════════════════════════════════════════════════════════════════════════════════
# --enqueue: the plan
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_the_plan_queues_the_live_text_in_the_catalog_voice():
    plan = R.plan_enqueue(_piece(), _live(), jobs=[], busy_pieces=[], now=SAT, old_job=_old_job())
    blocks = narration.narration_blocks(_content())
    assert plan["slug"] == SLUG and plan["round"] == 1 and plan["engine"] == C.ENGINE_CHATTERBOX
    assert narration.parse_voice_key(plan["voice"]) == narration.CHATTERBOX_VOICE
    assert plan["script"] == narration.script_json(blocks)
    assert plan["script_sha256"] == narration.script_sha256(blocks)
    assert plan["pipeline_version"] == narration.PIPELINE_VERSION
    assert plan["spoken_words"] > plan["words"] == sum(len(b.text.split()) for b in blocks)
    assert plan["current_seconds"] == LIVE_SECONDS and plan["old_atempo"] == 1.188


def test_byline_art_and_tag_are_not_narrated():
    """The script is the live article's TEXT: a different byline, cover or tag narrates the same."""
    plan = R.plan_enqueue(_piece(), _live(), jobs=[], busy_pieces=[], now=SAT)
    texts = " ".join(b["text"] for b in plan["script"])
    assert "THE SUPPLIERS" not in texts and "Caydex Research" not in texts and "http" not in texts
    other = _content(tagLabel="CASE STUDY", author={"name": "X", "title": "Y", "isVerified": True},
                     imageUrl=None, imageCardUrl=None)
    assert R.plan_enqueue(_piece(), _live(other), jobs=[], busy_pieces=[], now=SAT)["script_sha256"] == \
        plan["script_sha256"]


def test_the_expected_length_comes_from_the_old_renders_own_numbers():
    """The same raw speech, now at atempo ≈ v1 × tokens/spoken (never 1.188); the pauses keep their length."""
    blocks = narration.narration_blocks(_content())
    written, spoken = R.word_counts(blocks)
    old = _old_job()
    plan = R.plan_enqueue(_piece(), _live(), jobs=[], busy_pieces=[], now=SAT, old_job=old)
    new = max(cb.ATEMPO_RANGE[0], min(cb.ATEMPO_RANGE[1], 1.188 * written / spoken))
    pauses = cb.BLOCK_PAUSE * (len(blocks) - 1)
    raw = (R.render_seconds(old) - pauses) * 1.188
    assert plan["render_seconds"] == R.render_seconds(old)
    assert plan["new_atempo"] == pytest.approx(new, rel=1e-6)
    assert plan["expected_seconds"] == pytest.approx(raw / new + pauses, abs=0.1)
    assert plan["expected_seconds"] > R.render_seconds(old) * 1.05
    assert plan["projected_max_spw"] == pytest.approx(0.62 * 1.188 / new, abs=1e-3)


def test_a_v2_render_estimates_its_own_tempo_not_a_token_correction():
    """The estimate works from raw speech, so a round already on spoken words re-renders to itself; a token
    ratio (v1's correction) would wrongly slow it a second time."""
    blocks = narration.narration_blocks(_content())
    written, spoken = R.word_counts(blocks)
    pauses = cb.BLOCK_PAUSE * (len(blocks) - 1)
    seconds = spoken / cb.TARGET_WPM * 60 + pauses                 # a v2 file: speech at 165 spoken wpm
    got = R.estimate_render(_old_job(atempo=0.93, seconds=seconds), blocks)
    assert got["atempo"] == pytest.approx(0.93, rel=1e-6) != pytest.approx(0.93 * written / spoken, rel=1e-3)
    assert got["seconds"] == pytest.approx(seconds, abs=0.1)


def _w44_old_job() -> Dict[str, Any]:
    """W44's real round: the 65-block script (the old closing included), 727.494 s at atempo 1.188."""
    return _old_job(nt.w44_article(), atempo=nt.W44["recorded_atempo"], seconds=nt.W44["original_duration_seconds"])


@pytest.mark.parametrize("live_seconds", [931, 719, 400])
def test_the_estimate_never_reads_the_live_rows_length(live_seconds):
    """The live file need not be the render that recorded the atempo: W44's clip B (931 s) was re-timed by
    hand, its promote cut the closing (719 s). Pairing either with 1.188 said ~1133 s for a render that
    really lands near 871 s, the opposite direction of what a swap would do."""
    article = nt.w44_article(closing=False)
    content = {**_content(), **{k: article[k] for k in ("title", "subtitle", "sections")}}
    plan = R.plan_enqueue(_piece(), _live(content, audio_duration_seconds=live_seconds), jobs=[], busy_pieces=[],
                          now=SAT, old_job=_w44_old_job())
    assert plan["render_seconds"] == nt.W44["original_duration_seconds"]
    assert plan["expected_seconds"] == pytest.approx(871.3, abs=2.0)
    assert plan["new_atempo"] == pytest.approx(0.971, abs=0.003)
    assert plan["current_seconds"] == live_seconds


def test_a_trimmed_live_text_keeps_only_its_share_of_the_speech():
    """The old script's closing is not in the live text: its speech and its pause leave the estimate."""
    old = _w44_old_job()
    full = R.estimate_render(old, narration.narration_blocks(nt.w44_article()))
    live = R.estimate_render(old, narration.narration_blocks(nt.w44_article(closing=False)))
    assert full["seconds"] == pytest.approx(881.7, abs=2.0) and live["seconds"] < full["seconds"] - 5
    assert full["atempo"] == pytest.approx(live["atempo"], rel=1e-6)


@pytest.mark.parametrize("job", [
    None,
    {},
    {"checks": None},
    {"checks": {"worker": {"file": {"duration": 700.0}}}},                          # no atempo
    {"checks": _stored_checks(atempo=1.188, pipeline="x", duration=None)},          # no render length
    {"checks": _stored_checks(atempo=1.188, pipeline="x", duration=0)},
    {"checks": _stored_checks(atempo=0, pipeline="x", duration=700.0)},
    {"checks": _stored_checks(atempo=1.188, pipeline="x", duration=700.0), "script": "junk"},
])
def test_no_estimate_without_the_old_renders_own_numbers(job):
    blocks = narration.narration_blocks(_content())
    assert R.estimate_render(job, blocks) is None and R.projected_max_spw(job, blocks) is None


def test_no_estimate_from_a_render_shorter_than_its_own_pauses():
    blocks = narration.narration_blocks(_content())
    assert R.estimate_render(_old_job(seconds=cb.BLOCK_PAUSE), blocks) is None


def test_an_unreadable_old_job_still_plans_and_prints_no_estimate(monkeypatch, capsys):
    _seams(monkeypatch, old=None)
    assert R.main(["--period", PERIOD, "--enqueue"]) == 0
    out = capsys.readouterr().out
    assert "no estimate of the new length" in out and "expected after the re-render" not in out


@pytest.mark.parametrize("piece_over, live, why", [
    ({"status": C.APPROVED}, None, "only a PUBLISHED piece"),
    ({"status": C.UNPUBLISHED}, None, "only a PUBLISHED piece"),
    ({"status": C.PUBLISHING}, None, "being published right now"),
    ({"dry_run": True}, None, "rehearsal"),
    ({"slug": "how-amazon-built-its-moat"}, None, "does not end in a publish date"),
    ({"slug": "Bad Slug"}, None, "kebab-case"),
    ({}, "missing", "no weekly row of its own"),
    ({}, {"source": "bundle"}, "no weekly row of its own"),
    ({}, {"id": "00000000-0000-0000-0000-000000000000"}, "no weekly row of its own"),
    ({}, {"content": "junk"}, "unreadable"),
    ({}, {"updated_at": None}, "unreadable"),
])
def test_the_enqueue_refuses_what_is_not_a_live_weekly_article(piece_over, live, why):
    row = None if live == "missing" else _live(**(live or {}))
    with pytest.raises(SystemExit, match=why):
        R.plan_enqueue(_piece(**piece_over), row, jobs=[], busy_pieces=[], now=SAT)


def test_the_enqueue_refuses_text_the_narrator_cannot_render():
    long = _content()
    long["sections"][1]["content"][1]["text"] = " ".join(["word"] * (C.MAX_PARAGRAPH_WORDS + 1)) + "."
    with pytest.raises(SystemExit, match="block cap"):
        R.plan_enqueue(_piece(), _live(long), jobs=[], busy_pieces=[], now=SAT)
    blank = _content()
    blank["sections"][0]["content"][1]["text"] = "   "
    with pytest.raises(SystemExit, match="no speakable text"):
        R.plan_enqueue(_piece(), _live(blank), jobs=[], busy_pieces=[], now=SAT)


def test_the_enqueue_refuses_while_a_job_of_the_slug_is_open():
    for status in (C.JOB_QUEUED, C.JOB_IN_PROGRESS):
        with pytest.raises(SystemExit, match=f"still {status}"):
            R.plan_enqueue(_piece(), _live(), jobs=[_ready_job(status=status)], busy_pieces=[], now=SAT)


def test_a_ready_render_of_this_text_by_the_current_pipeline_is_swapped_not_rerendered():
    with pytest.raises(SystemExit, match="already READY .* run --swap"):
        R.plan_enqueue(_piece(), _live(), jobs=[_ready_job()], busy_pieces=[], now=SAT)
    assert R.plan_enqueue(_piece(), _live(), jobs=[_ready_job()], busy_pieces=[], now=SAT, force=True)["round"] == 2


def test_the_readers_follow_what_the_real_worker_and_complete_store(tmp_path, monkeypatch):
    """No hand-typed shape: a REAL worker tick's payload through the REAL `narration.complete`. The script
    must then read the render's numbers one level down (`checks.worker.worker`), where they really are;
    reading one level up left the duplicate-render guard dead and every printed estimate blank."""
    code, api, _ = wt._tick(tmp_path, monkeypatch, wt._raw_job())
    assert code == 0
    reported = next(c for c in api.calls if c[0] == "complete")[2]["checks"]
    client = nt.FakeClient()
    monkeypatch.setattr(ledger, "get_supabase", lambda: client)
    monkeypatch.setattr(narration, "get_supabase", lambda: client)
    monkeypatch.setattr(settings, "LEARN_NARRATOR_ENGINE", "chatterbox")

    async def flow():
        nt._media_pending(client)
        wire, claim, kw = await nt._rendered(client)
        await narration.complete(wire["id"], claim, now=nt.NOW, **{**kw, "checks": reported})
        return wire["id"]

    stored = client.row(ledger.JOBS, asyncio.run(flow()))
    assert set(stored["checks"]) == {"worker", "server"}
    assert R._old_atempo(stored) == 1.0 == reported["worker"]["atempo"]
    assert R.engine_pipeline(stored) == "test" == reported["worker"]["engine_pipeline"]
    assert R.render_seconds(stored) == reported["file"]["duration"] > 0
    assert R._worker_checks(stored)["atempo_clamped"] is False
    # …and this file's fixture is that shape, key for key.
    fixture = _stored_checks(atempo=1.0, pipeline="test", duration=1.0)
    assert set(fixture) == set(stored["checks"])
    assert set(fixture["worker"]) == set(stored["checks"]["worker"])
    assert set(fixture["worker"]["worker"]) <= set(stored["checks"]["worker"]["worker"])
    assert set(fixture["worker"]["file"]) == set(stored["checks"]["worker"]["file"])


def test_a_second_enqueue_of_a_ready_render_is_refused_on_the_stored_shape():
    """The guard that keeps a repeated --yes from queueing another ~1 h render on the one-job narrator."""
    job = _ready_job()
    assert R.engine_pipeline(job) == cb.PIPELINE_VERSION
    assert job["checks"]["worker"]["worker"]["engine_pipeline"] == cb.PIPELINE_VERSION
    assert "engine_pipeline" not in job["checks"]["worker"]
    with pytest.raises(SystemExit, match=f"rendered by {cb.PIPELINE_VERSION}"):
        R.plan_enqueue(_piece(), _live(), jobs=[job], busy_pieces=[], now=SAT)


def test_a_ready_render_by_an_older_pipeline_is_rendered_again():
    """A narrator not yet redeployed with v2 renders the same too-fast file: that round is what must be
    redone, so it does not block the enqueue."""
    old = _ready_job(pipeline="chatterbox-0.1.7/block/aac96k/v1")
    assert R.plan_enqueue(_piece(), _live(), jobs=[old], busy_pieces=[], now=SAT)["round"] == 2


def test_the_round_is_one_past_the_slugs_highest():
    jobs = [_ready_job(round=1, status=C.JOB_FAILED), _ready_job(round=3, status=C.JOB_CANCELLED),
            {**_ready_job(round=9), "catalog_slug": "kodak", "status": C.JOB_QUEUED},    # another slug
            {**_ready_job(round=7), "piece_id": PIECE_ID, "status": C.JOB_QUEUED}]       # a weekly job
    assert R.plan_enqueue(_piece(), _live(), jobs=jobs, busy_pieces=[], now=SAT)["round"] == 4


def test_the_enqueue_waits_for_a_weekly_build_and_the_deep_dive_window():
    busy = [{"period_key": "2026-W45:deep_dive", "status": C.MEDIA_PENDING}]
    with pytest.raises(SystemExit, match="being built or narrated"):
        R.plan_enqueue(_piece(), _live(), jobs=[], busy_pieces=busy, now=SAT)
    with pytest.raises(SystemExit, match="Deep Dive window"):
        R.plan_enqueue(_piece(), _live(), jobs=[], busy_pieces=[], now=FRI)
    assert R.plan_enqueue(_piece(), _live(), jobs=[], busy_pieces=busy, now=FRI, force=True)["round"] == 1
    assert R.plan_enqueue(_piece(), _live(), jobs=[], busy_pieces=[{"status": C.IN_REVIEW}], now=SAT)


def test_the_window_is_the_catalog_scripts_own():
    narrate = R._sibling("learn_catalog_narrate")
    for hour in range(0, 24 * 7, 5):
        when = datetime(2026, 11, 2, tzinfo=UTC) + timedelta(hours=hour)
        assert R.in_deep_dive_window(when) == narrate.in_deep_dive_window(when)


# ── --enqueue: main ─────────────────────────────────────────────────────────────────────────


def _seams(monkeypatch, *, piece=None, live="default", jobs=None, busy=None, old=None, enqueue=None,
           stat=None, update=None, transition=None, now=SAT):
    calls: Dict[str, List[Any]] = {"enqueue": [], "update": [], "transition": []}
    piece = piece or _piece()
    monkeypatch.setattr(R, "_piece_by_period", lambda period: piece if period == piece["period_key"] else None)
    monkeypatch.setattr(R, "_piece_by_slug", lambda slug: piece if slug == piece["slug"] else None)
    monkeypatch.setattr(R, "_read_live", lambda slug: _live() if live == "default" else live)
    monkeypatch.setattr(R, "_jobs", lambda slug: list(jobs or []))
    monkeypatch.setattr(R, "_busy_pieces", lambda: list(busy or []))
    monkeypatch.setattr(R, "_old_job", lambda p: old)
    monkeypatch.setattr(R, "_enqueue", lambda plan: calls["enqueue"].append(plan) or (
        enqueue(plan) if enqueue else {"id": "new-job", "status": C.JOB_QUEUED}))
    monkeypatch.setattr(R, "_stat", stat or (lambda path: None))
    monkeypatch.setattr(R, "_update_live", lambda slug, **kw: calls["update"].append((slug, kw)) or (
        update(slug, **kw) if update else {"slug": slug}))
    monkeypatch.setattr(R, "_transition", lambda pid, **kw: calls["transition"].append((pid, kw)) or (
        transition(pid, **kw) if transition else {"id": pid}))
    monkeypatch.setattr(R, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: now)}))
    return calls


def test_enqueue_writes_nothing_without_yes(monkeypatch, capsys):
    calls = _seams(monkeypatch, old=_old_job())
    assert R.main(["--period", PERIOD, "--enqueue"]) == 0
    out = capsys.readouterr().out
    assert calls["enqueue"] == [] and "dry run — nothing written" in out
    assert "re-narration round 1" in out and "atempo 1.188" in out and "expected after the re-render" in out
    assert "inside the server's band" in out and "against the live clip" in out


def test_enqueue_warns_when_the_re_render_would_be_shorter_than_the_live_clip(monkeypatch, capsys):
    """W44 as it stands: the dry run tells the owner the swap would be refused, before a ~1 h render."""
    article = nt.w44_article(closing=False)
    content = {**_content(), **{k: article[k] for k in ("title", "subtitle", "sections")}}
    _seams(monkeypatch, old=_w44_old_job(), live=_live(content, audio_duration_seconds=931))
    assert R.main(["--period", PERIOD, "--enqueue"]) == 0
    out = capsys.readouterr().out
    assert "live audio now 931 s" in out and "the old render: 727 s at atempo 1.188" in out
    assert "expected after the re-render ≈ 871 s" in out and "against the live clip: -6.4%" in out
    assert "SHORTER than the live clip" in out and "--swap will refuse it without --allow-shorter" in out


def test_enqueue_warns_when_the_slowest_sentence_would_break_the_band(monkeypatch, capsys):
    _seams(monkeypatch, old=_old_job(max_spw=1.05))
    assert R.main(["--period", PERIOD, "--enqueue"]) == 0
    assert "OVER the server's ceiling" in capsys.readouterr().out


def test_enqueue_with_yes_queues_through_the_real_ledger(monkeypatch, capsys):
    fake = ledger_fake.FakeSupabase()
    real = lambda plan: ledger.enqueue_catalog_job_sync(  # noqa: E731 — the REAL validators, unmocked
        plan["slug"], round=plan["round"], script=plan["script"], script_sha256=plan["script_sha256"],
        engine=plan["engine"], voice=plan["voice"], pipeline_version=plan["pipeline_version"], sb=fake)
    _seams(monkeypatch, enqueue=real)
    assert R.main(["--slug", SLUG, "--enqueue", "--yes"]) == 0
    (row,) = fake.tables[ledger.JOBS].rows
    assert row["catalog_slug"] == SLUG and row["piece_id"] is None and row["round"] == 1
    assert row["engine"] == "chatterbox" and row["pipeline_version"] == narration.PIPELINE_VERSION
    assert narration.is_weekly_renarration(row)
    blocks = narration.blocks_from_script(row["script"], expected_sha256=row["script_sha256"])
    assert [b.text for b in blocks] == [b.text for b in narration.narration_blocks(_content())]
    out = capsys.readouterr().out
    assert "queued narration job" in out and f"--slug {SLUG} --swap" in out


def test_enqueue_reports_a_lost_round(monkeypatch, capsys):
    _seams(monkeypatch, enqueue=lambda plan: None)
    assert R.main(["--period", PERIOD, "--enqueue", "--yes"]) == 1
    assert "nothing queued" in capsys.readouterr().out


def test_enqueue_names_migration_207_when_the_ledger_refuses(monkeypatch):
    def boom(*_a, **_k):
        raise ledger.LearnLedgerError("list_catalog_jobs failed: column catalog_slug does not exist")

    calls = _seams(monkeypatch)
    monkeypatch.setattr(R, "_jobs", boom)
    with pytest.raises(SystemExit, match="is migration 207 applied"):
        R.main(["--period", PERIOD, "--enqueue", "--yes"])
    assert calls["enqueue"] == []


def test_the_command_line_names_one_piece_and_one_step(monkeypatch):
    _seams(monkeypatch)
    for argv in ([], ["--period", PERIOD], ["--period", PERIOD, "--slug", SLUG, "--swap"],
                 ["--period", PERIOD, "--enqueue", "--swap"]):
        with pytest.raises(SystemExit):
            R.main(argv)
    with pytest.raises(SystemExit, match="--enqueue only"):
        R.main(["--period", PERIOD, "--swap", "--force"])
    with pytest.raises(SystemExit, match="--swap only"):
        R.main(["--period", PERIOD, "--enqueue", "--allow-shorter"])
    with pytest.raises(SystemExit, match="no learn_weekly_pieces row"):
        R.main(["--slug", "other-2026-10-23", "--swap"])


# ═════════════════════════════════════════════════════════════════════════════════════════════
# --swap: the plan
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_the_swap_merges_only_the_narration_fields():
    job = _ready_job()
    plan = R.plan_swap(_piece(), _live(), job, _stat(job))
    before, after = _content(), plan["content"]
    assert R.only_narration_changed(before, after)
    url = f"{SB_URL}/storage/v1/object/public/money-moves-media/{job['audio_path']}"
    assert plan["audio_url"] == after["audioUrl"] == url
    assert plan["seconds"] == after["audioDurationSeconds"] == int(round(job["duration_seconds"]))
    for key in ("tagLabel", "author", "imageUrl", "imageCardUrl", "title", "subtitle", "sortOrder", "publishedAt"):
        assert after[key] == before[key], key
    para = after["sections"][0]["content"][0]
    assert para["text"] == before["sections"][0]["content"][0]["text"]
    assert [s["text"] for s in para["readAlong"]] == narration.sentence_split(para["text"])   # the old span is gone
    assert len(after["sections"][1]["content"][0]["itemsReadAlong"]) == 2
    assert plan["live_done"] is False


def test_the_catalog_url_is_signed_like_any_learn_audio():
    job = _ready_job()
    url = narration.catalog_audio_public_url(job["audio_path"])
    assert learn_audio_urls.parse_storage_url(url, allowed={learn_audio_urls.MONEY_MOVES_BUCKET}) == \
        (learn_audio_urls.MONEY_MOVES_BUCKET, job["audio_path"])
    for bad in (f"audio/weekly/{SLUG}-0123456789ab.m4a", "audio/catalog/../x-0123456789ab.m4a", None, ""):
        with pytest.raises(ValueError):
            narration.catalog_audio_public_url(bad)


def _edited():
    c = _content()
    c["sections"][1]["content"][1]["text"] = "Long agreements steady revenue."
    return c


@pytest.mark.parametrize("make, why", [
    (lambda: (_piece(status=C.UNPUBLISHED), _live(), _ready_job(), "stat"), "only a PUBLISHED piece"),
    (lambda: (_piece(), _live(_edited()), _ready_job(), "stat"), "text changed after round 1"),
    (lambda: (_piece(), _live(), _ready_job(), None), "not in the bucket"),
    (lambda: (_piece(), _live(), _ready_job(), {"size": 1, "mimetype": "audio/mp4"}), "not the job's object"),
    (lambda: (_piece(), _live(), _ready_job(), {"size": None, "mimetype": None}), "cannot vouch"),
    (lambda: (_piece(), _live(), _ready_job(bps=9_700), "stat"), "encode band"),
    (lambda: (_piece(), _live(), _ready_job(audio_path=f"audio/weekly/{SLUG}-0123456789ab.m4a"), "stat"),
     "not a catalog narration object"),
    (lambda: (_piece(), _live(), _ready_job(audio_bytes=0), "stat"), "audio_bytes"),
    (lambda: (_piece(), _live(), _ready_job(duration_seconds=float("nan")), "stat"), "duration_seconds"),
    (lambda: (_piece(), _live(), _ready_job(script_sha256="0" * 64), "stat"), "does not hash"),
    (lambda: (_piece(), _live(), _ready_job(status=C.JOB_FAILED), "stat"), "not a READY re-narration job"),
    (lambda: (_piece(), _live(), _ready_job(catalog_slug="kodak"), "stat"), "not a READY re-narration job"),
])
def test_the_swap_refuses_a_render_that_must_not_go_live(make, why):
    piece, live, job, stat = make()
    with pytest.raises(SystemExit, match=why):
        R.plan_swap(piece, live, job, _stat(job) if stat == "stat" else stat)


def test_the_swap_refuses_a_render_shorter_than_the_live_clip():
    """The same text, so shorter = faster: W44's approved clip B (931 s) would have lost to a render 6%
    shorter that passes every band. Nothing but an explicit flag replaces it."""
    job = _ready_job()
    live = _live(audio_duration_seconds=round(job["duration_seconds"] / 0.936))    # W44's -6.4%
    with pytest.raises(SystemExit, match=r"6\.\d% SHORTER than the live clip .*--allow-shorter"):
        R.plan_swap(_piece(), live, job, _stat(job))
    plan = R.plan_swap(_piece(), live, job, _stat(job), allow_shorter=True)
    assert "SHORTER" in plan["shorter"] and plan["live_done"] is False


def test_the_swap_tolerates_a_rounding_sized_difference_and_any_longer_render():
    job = _ready_job()
    end = job["duration_seconds"]
    for live_seconds in (end * 0.5, end, end / (1 - R.SHORTER_TOLERANCE) - 0.01):
        plan = R.plan_swap(_piece(), _live(audio_duration_seconds=live_seconds), job, _stat(job))
        assert plan["shorter"] is None
    with pytest.raises(SystemExit, match="SHORTER"):
        R.plan_swap(_piece(), _live(audio_duration_seconds=end / (1 - R.SHORTER_TOLERANCE) + 0.5), job, _stat(job))


def test_a_live_clip_of_unknown_length_asks_and_no_live_clip_does_not():
    job = _ready_job()
    with pytest.raises(SystemExit, match="length is unreadable"):
        R.plan_swap(_piece(), _live(audio_duration_seconds=None), job, _stat(job))
    assert R.plan_swap(_piece(), _live(audio_duration_seconds=None), job, _stat(job), allow_shorter=True)["shorter"]
    assert R.plan_swap(_piece(), _live(audio_url=None, audio_duration_seconds=None), job, _stat(job))["shorter"] is None


def test_a_swap_already_done_is_never_refused_as_shorter(monkeypatch):
    """A rerun compares the render with itself: only the mirror is left to write."""
    job = _ready_job()
    plan = R.plan_swap(_piece(), _live(), job, _stat(job))
    done = _live(plan["content"], audio_url=plan["audio_url"], audio_duration_seconds=plan["seconds"])
    assert R.plan_swap(_piece(), done, job, _stat(job))["live_done"] is True


def test_swap_main_refuses_a_shorter_render_and_writes_nothing(monkeypatch):
    job = _ready_job()
    live = _live(audio_duration_seconds=round(job["duration_seconds"] * 1.5))
    calls = _seams(monkeypatch, jobs=[job], live=live, stat=lambda path: _stat(job))
    with pytest.raises(SystemExit, match="SHORTER"):
        R.main(["--period", PERIOD, "--swap", "--yes"])
    assert calls["update"] == [] and calls["transition"] == []


def test_allow_shorter_swaps_says_so_and_records_it(monkeypatch, capsys):
    job = _ready_job()
    live = _live(audio_duration_seconds=round(job["duration_seconds"] * 1.5))
    calls = _seams(monkeypatch, jobs=[job], live=live, stat=lambda path: _stat(job))
    assert R.main(["--period", PERIOD, "--swap", "--allow-shorter", "--yes"]) == 0
    assert len(calls["update"]) == 1
    assert "--allow-shorter: this render is" in capsys.readouterr().out
    (_, kw), = calls["transition"]
    assert kw["fields"]["validation"]["renarrate"]["allowed_shorter"] is True


def test_the_swap_prints_the_render_tempo_from_the_stored_shape(monkeypatch, capsys):
    job = _ready_job()
    job["checks"] = _stored_checks(atempo=0.8, pipeline=cb.PIPELINE_VERSION, duration=job["duration_seconds"],
                                   clamped=True)
    _seams(monkeypatch, jobs=[job], stat=lambda path: _stat(job))
    assert R.main(["--slug", SLUG, "--swap"]) == 0
    out = capsys.readouterr().out
    assert f"rendered by {cb.PIPELINE_VERSION} at atempo 0.8 (CLAMPED" in out
    assert "not this checkout's narrator pipeline" not in out


def test_the_swap_refuses_a_too_fast_render_on_spoken_words():
    """A narrator still on v1 renders W44 at 165 TOKENS/min again: the spoken-word pace band refuses it."""
    job = _ready_job(wpm=200.0)
    with pytest.raises(SystemExit, match="spoken WPM outside"):
        R.plan_swap(_piece(), _live(), job, _stat(job))
    slow = _ready_job(wpm=130.0)
    with pytest.raises(SystemExit, match="spoken WPM outside"):
        R.plan_swap(_piece(), _live(), slow, _stat(slow))


def test_the_swap_runs_the_catalog_pause_gate():
    """A 3.2 s pause inside a paragraph passes the server's 6 s silence check but not the catalog's 3.0 s
    in-block bound (learn_catalog_fetch's gate, run as it is)."""
    blocks = narration.narration_blocks(_content())
    para = next(b for b in blocks if b.dest == ("block", 0, 0))
    assert len(narration.sentence_split(para.text)) == 2
    job = _ready_job(extra={(para.i, 1): 3.2})
    with pytest.raises(SystemExit, match=r"3\.\d\d s pause before 'Data center"):
        R.plan_swap(_piece(), _live(), job, _stat(job))
    ok = _ready_job(extra={(para.i, 1): 2.5})
    assert R.plan_swap(_piece(), _live(), ok, _stat(ok))["seconds"] > 0


def test_the_swap_refuses_timings_that_do_not_merge():
    job = _ready_job()
    job["timings"] = job["timings"][:-1]
    with pytest.raises(SystemExit, match="do not merge"):
        R.plan_swap(_piece(), _live(), job, _stat(job))


def test_pick_ready_takes_the_newest_ready_round():
    r1, r2 = _ready_job(round=1), _ready_job(round=2)
    r3 = _ready_job(round=3, status=C.JOB_FAILED, error="voice_out_of_band: wpm 201 outside")
    job, warnings = R.pick_ready([r3, r1, r2, {**r1, "piece_id": PIECE_ID, "round": 9}], SLUG)
    assert job["round"] == 2 and warnings == ["round 3 is failed — swapping in the newest READY round 2"]
    with pytest.raises(SystemExit, match=r"round 3: failed \(voice_out_of_band"):
        R.pick_ready([r3], SLUG)
    with pytest.raises(SystemExit, match="no job at all"):
        R.pick_ready([], SLUG)


def test_the_mirror_merges_the_same_narration_into_the_draft():
    job = _ready_job()
    plan = R.plan_swap(_piece(), _live(), job, _stat(job))
    draft, why = R.mirror_draft(_piece(), plan, job)
    assert why == "" and R.only_narration_changed(_draft(), draft)
    assert draft["audioUrl"] == plan["audio_url"] and draft["audioDurationSeconds"] == plan["seconds"]
    assert draft["sections"] == plan["content"]["sections"]
    assert R.mirror_draft(_piece(draft=draft), plan, job) == (None, R.ALREADY_MIRRORED)
    other = _draft()
    other["title"] = "Another Title"
    got, why = R.mirror_draft(_piece(draft=other), plan, job)
    assert got is None and "does not narrate the live text" in why
    assert R.mirror_draft(_piece(draft=None), plan, job)[0] is None


# ── --swap: main ────────────────────────────────────────────────────────────────────────────


def test_swap_writes_nothing_without_yes(monkeypatch, capsys):
    job = _ready_job()
    calls = _seams(monkeypatch, jobs=[job], stat=lambda path: _stat(job))
    assert R.main(["--slug", SLUG, "--swap"]) == 0
    out = capsys.readouterr().out
    assert calls["update"] == [] and calls["transition"] == [] and "dry run — nothing written" in out
    assert cb.PIPELINE_VERSION in out and "gains the same narration" in out


class _LiveTable:
    """The live `money_move_articles` row and its fenced UPDATE (slug + updated_at)."""

    def __init__(self, row):
        self.row = copy.deepcopy(row)

    def read(self, slug):
        return copy.deepcopy(self.row) if slug == self.row["slug"] else None

    def update(self, slug, *, audio_url, audio_duration_seconds, content, observed_updated_at, now):
        if slug != self.row["slug"] or observed_updated_at != self.row["updated_at"]:
            return None
        self.row.update(audio_url=audio_url, audio_duration_seconds=audio_duration_seconds,
                        content=json.loads(json.dumps(content)), updated_at=now.isoformat())
        return copy.deepcopy(self.row)


def _fake_piece(fake) -> Dict[str, Any]:
    """The published piece in the ledger test's FakeSupabase (the REAL transition rules and fences)."""
    row = ledger.create_piece_sync(PERIOD, "deep_dive", scheduled_publish_at=ledger_fake.T0,
                                   hold_until=ledger_fake.HOLD, dry_run=False, sb=fake)
    draft = _draft()
    ledger_fake._walk(fake, row["id"], ledger_fake.MAIN_PATH + [C.PUBLISHING, C.PUBLISHED],
                      fields={"slug": SLUG, "title": draft["title"]})
    return ledger.transition_sync(row["id"], from_statuses=[C.PUBLISHED], to_status=C.PUBLISHED, sb=fake,
                                  fields={"draft": draft, "draft_sha256": narration.draft_sha256(draft),
                                          "validation": {"promote": {"by": "owner"}}})


def test_swap_with_yes_updates_the_live_row_then_mirrors_the_piece(monkeypatch, capsys):
    fake = ledger_fake.FakeSupabase()
    piece = _fake_piece(fake)
    job = _ready_job()
    table = _LiveTable(_live())
    calls = _seams(monkeypatch, piece=piece, live=None, jobs=[job], stat=lambda path: _stat(job),
                   update=table.update,
                   transition=lambda pid, **kw: ledger.transition_sync(pid, sb=fake, **kw))
    monkeypatch.setattr(R, "_read_live", table.read)
    assert R.main(["--period", PERIOD, "--swap", "--yes"]) == 0
    (slug, kw), = calls["update"]
    assert slug == SLUG and kw["observed_updated_at"] == LIVE_TS
    assert set(kw) == {"audio_url", "audio_duration_seconds", "content", "observed_updated_at", "now"}
    live = table.row
    assert live["audio_url"] == narration.catalog_audio_public_url(job["audio_path"]) == live["content"]["audioUrl"]
    assert live["audio_duration_seconds"] == int(round(job["duration_seconds"]))
    assert R.only_narration_changed(_content(), live["content"])
    (pid, tkw), = calls["transition"]
    assert tkw["fence"] == {"draft_sha256": piece["draft_sha256"], "updated_at": piece["updated_at"]}
    assert tkw["from_statuses"] == [C.PUBLISHED] and tkw["to_status"] == C.PUBLISHED
    stored = fake.row(ledger.PIECES, piece["id"])
    assert stored["status"] == C.PUBLISHED and stored["last_stage"] == R.LAST_STAGE
    assert stored["draft"]["audioUrl"] == live["audio_url"]
    assert stored["draft"]["sections"] == live["content"]["sections"]
    assert stored["draft_sha256"] == narration.draft_sha256(stored["draft"])
    note = stored["validation"]["renarrate"]
    assert note["job_id"] == job["id"] and note["from_audio_url"] == OLD_URL
    assert note["from_duration_seconds"] == LIVE_SECONDS and note["live_swapped_earlier"] is False
    assert note["engine_pipeline"] == cb.PIPELINE_VERSION and note["atempo"] == 0.93
    assert note["allowed_shorter"] is False
    assert stored["validation"]["promote"] == {"by": "owner"}          # earlier notes are kept
    assert "SWAPPED" in capsys.readouterr().out

    # A rerun is idempotent: the live row already plays it and the draft already carries it.
    calls2 = _seams(monkeypatch, piece=fake.row(ledger.PIECES, piece["id"]), live=None, jobs=[job],
                    stat=lambda path: _stat(job), update=table.update)
    monkeypatch.setattr(R, "_read_live", table.read)
    assert R.main(["--period", PERIOD, "--swap", "--yes"]) == 0
    assert calls2["update"] == [] and calls2["transition"] == []
    out = capsys.readouterr().out
    assert "already points at this render" in out and R.ALREADY_MIRRORED in out


def test_a_lost_live_compare_and_swap_writes_nothing_else(monkeypatch, capsys):
    job = _ready_job()
    calls = _seams(monkeypatch, jobs=[job], stat=lambda path: _stat(job), update=lambda slug, **kw: None)
    assert R.main(["--period", PERIOD, "--swap", "--yes"]) == 1
    assert calls["transition"] == [] and "lost compare-and-swap" in capsys.readouterr().out


def test_a_lost_mirror_leaves_the_swapped_live_row_and_says_rerun(monkeypatch, capsys):
    """The mirror is fenced on the draft it read: a piece that moved on is not overwritten (exit 2), and
    a rerun writes only the mirror."""
    fake = ledger_fake.FakeSupabase()
    piece = _fake_piece(fake)
    stale = {**piece, "draft_sha256": "f" * 64}
    job = _ready_job()
    table = _LiveTable(_live())
    calls = _seams(monkeypatch, piece=stale, live=None, jobs=[job], stat=lambda path: _stat(job),
                   update=table.update, transition=lambda pid, **kw: ledger.transition_sync(pid, sb=fake, **kw))
    monkeypatch.setattr(R, "_read_live", table.read)
    assert R.main(["--period", PERIOD, "--swap", "--yes"]) == 2
    assert len(calls["update"]) == 1 and len(calls["transition"]) == 1
    assert fake.row(ledger.PIECES, piece["id"])["draft"] == piece["draft"]          # untouched
    assert "rerun --swap" in capsys.readouterr().out
    calls2 = _seams(monkeypatch, piece=fake.row(ledger.PIECES, piece["id"]), live=None, jobs=[job],
                    stat=lambda path: _stat(job), update=table.update,
                    transition=lambda pid, **kw: ledger.transition_sync(pid, sb=fake, **kw))
    monkeypatch.setattr(R, "_read_live", table.read)
    assert R.main(["--period", PERIOD, "--swap", "--yes"]) == 0
    assert calls2["update"] == [] and len(calls2["transition"]) == 1
    stored = fake.row(ledger.PIECES, piece["id"])
    assert stored["draft"]["audioUrl"] == table.row["audio_url"]
    note = stored["validation"]["renarrate"]
    assert note["live_swapped_earlier"] is True and note["from_audio_url"] is None   # never the catalog URL


def test_a_mirror_that_raises_is_reported_never_swallowed(monkeypatch, capsys):
    def boom(pid, **kw):
        raise ledger.LearnLedgerError("transition failed: APIError: timeout")
    job = _ready_job()
    _seams(monkeypatch, jobs=[job], stat=lambda path: _stat(job), transition=boom)
    assert R.main(["--period", PERIOD, "--swap", "--yes"]) == 2
    out = capsys.readouterr().out
    assert "NOT updated" in out and "LearnLedgerError" in out


def test_an_unreadable_storage_listing_stops_the_swap(monkeypatch):
    def boom(path):
        raise LearnNarratorLedgerError("storage LIST failed", op="object_stat")
    job = _ready_job()
    calls = _seams(monkeypatch, jobs=[job], stat=boom)
    with pytest.raises(SystemExit, match="could not list"):
        R.main(["--period", PERIOD, "--swap", "--yes"])
    assert calls["update"] == []


class _Query:
    def __init__(self, log):
        self.log = log

    def update(self, payload):
        self.log.append(("update", payload))
        return self

    def eq(self, col, val):
        self.log.append(("eq", col, val))
        return self

    def execute(self):
        return type("Res", (), {"data": [{"ok": True}]})()


def test_the_live_update_is_one_fenced_write_of_the_narration_columns(monkeypatch):
    log: List[Any] = []
    client = type("SB", (), {"table": lambda self, name: log.append(("table", name)) or _Query(log)})()
    import app.database as db

    monkeypatch.setattr(db, "get_supabase", lambda: client)
    now = datetime(2026, 11, 7, 16, 0, tzinfo=UTC)
    assert R._update_live(SLUG, audio_url="u", audio_duration_seconds=930, content={"a": 1},
                          observed_updated_at=LIVE_TS, now=now) == {"ok": True}
    assert log[0] == ("table", publisher.ARTICLES)
    (_, payload), = [x for x in log if x[0] == "update"]
    assert set(payload) == {"audio_url", "audio_duration_seconds", "content", "updated_at"}
    assert payload["audio_duration_seconds"] == 930 and payload["content"] == {"a": 1}
    eqs = {(x[1], x[2]) for x in log if x[0] == "eq"}
    assert eqs == {("slug", SLUG), ("source", "weekly"), ("id", publisher.article_id(SLUG)), ("updated_at", LIVE_TS)}


# ═════════════════════════════════════════════════════════════════════════════════════════════
# The server half: the weekly READY / FAILED lines and a correctly paced figure-heavy render
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_a_weekly_slug_job_is_a_renarration_and_its_lines_name_this_script():
    job = {"catalog_slug": SLUG, "piece_id": None, "round": 2, "duration_seconds": 931.4,
           "error": "voice_out_of_band: wpm 201 outside (144.0, 166.0)"}
    assert narration.is_weekly_renarration(job)
    ready = narration.catalog_ready_text(job)
    assert "Weekly re-narration READY" in ready and f"learn_weekly_renarrate.py --slug {SLUG} --swap" in ready
    assert "931s" in ready and "learn_catalog_fetch" not in ready
    failed = narration.catalog_failed_text(job)
    assert "Weekly re-narration FAILED" in failed and "round 3" in failed
    assert f"learn_weekly_renarrate.py --slug {SLUG} --enqueue --yes" in failed and "keeps its current audio" in failed
    catalog = {**job, "catalog_slug": "coca-cola-vs-pepsico"}
    assert not narration.is_weekly_renarration(catalog)
    assert "learn_catalog_fetch.py --slug coca-cola-vs-pepsico" in narration.catalog_ready_text(catalog)
    assert "learn_catalog_narrate.py --slug coca-cola-vs-pepsico" in narration.catalog_failed_text(catalog)
    for slug in ("the-2008-crisis", "year-end-2026-13-01", None):
        assert not narration.is_weekly_renarration({**job, "catalog_slug": slug})
    assert not narration.is_weekly_renarration({**job, "piece_id": PIECE_ID})


@pytest.mark.parametrize("seconds_per_spoken_word", [60 / 165, 60 / 150])
def test_the_servers_timing_gate_accepts_a_correctly_paced_figure_heavy_render(seconds_per_spoken_word):
    """At the corrected pace (165 spoken wpm, and 10% slower) every figure-dense sentence stays inside the
    server's seconds-per-word band, counted on its own letter/digit runs — no band change is needed."""
    blocks = narration.narration_blocks(_content())
    t, start = [], 0.4
    for b in blocks:
        spans, b0 = [], start
        for s in narration.sentence_split(b.text):
            d = tm.spoken_word_count(s) * seconds_per_spoken_word
            spans.append({"text": s, "start": round(start, 3), "end": round(start + d, 3)})
            start += d + 0.2
        t.append({"i": b.i, "start": round(b0, 3), "end": round(start - 0.2, 3), "sentences": spans})
        start += 0.6
    norm = narration.validate_timings(blocks, t, duration_seconds=start)
    worst = max((s["end"] - s["start"]) / narration.spoken_words(s["text"]) for e in norm for s in e["sentences"])
    assert worst < narration.SPW_BAND[1]
