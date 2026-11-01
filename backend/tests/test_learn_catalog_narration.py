"""Catalog narration jobs on the narrator's internal API (migration 207; owner 2026-11-01, "Yes, build
it": the Railway learn-narrator renders two hand-written Money Moves CATALOG articles so nothing heavy
runs on the 16 GB Mac).

A catalog job has no piece (`piece_id` NULL, `catalog_slug` set). The worker cannot tell it from a weekly
job — it claims, renders, uploads to the path the server names and reports timings — so every difference
lives in `app/services/learn_weekly/narration.py`, and this file pins it:
  * the claim hands the worker the same wire view and never looks a piece up (an unreadable catalog job is
    failed, with the owner's loud line, instead of sitting claimed forever);
  * the upload target is `audio/catalog/<slug>-<sha12>.m4a` in the same private bucket, under the same
    exists / size / type rules, and `complete` accepts only that path for the job's own slug — and a weekly
    job never accepts a catalog path;
  * `complete` runs the same timing gate, object check and bytes/s band, marks the job ready, moves NO
    piece, and sends the owner one line naming `scripts/learn_catalog_fetch.py`; a replay sends nothing;
  * a failed catalog job (the worker's cap, a deterministic code, an asset mismatch at the cap, an
    unreadable script, the lease re-claim cap) touches no piece and alerts loudly with the slug, the error
    and round + 1 — exactly ONCE: a /fail the worker retries after a lost response is a replay and sends
    nothing, and a job the ledger closes at its lease re-claim cap is reported by the claim whose fenced
    close won (the scheduler's sweep finds only pieces);
  * every refusal of a catalog `complete` names the article (`catalog_slug` is an errors.CONTEXT_KEYS key);
  * an alert that cannot be sent never raises.
The weekly flows are pinned unchanged by `test_learn_weekly_narration.py` / `test_learn_narrator_internal_api.py`.

Hermetic: the REAL ledger and narration service on the in-memory PostgREST + Storage fakes of
`test_learn_weekly_narration.py`; the alerts module is replaced in sys.modules.
"""

from __future__ import annotations

import copy
import logging
import sys
import types
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import test_learn_weekly_narration as nt
from app.config import settings
from app.services.learn_weekly import contracts as c
from app.services.learn_weekly import ledger, narration
from app.services.learn_weekly.errors import (
    LearnNarratorAssetMismatch,
    LearnNarratorRequestInvalid,
)
from app.services.learn_weekly.narration import WorkerClaim

SLUG = "coca-cola-vs-pepsico"
NOW = nt.NOW
_WORKER = Path(__file__).resolve().parents[1] / "learn_narrator"


@pytest.fixture
def fake(monkeypatch):
    client = nt.FakeClient()
    monkeypatch.setattr(ledger, "get_supabase", lambda: client)
    monkeypatch.setattr(narration, "get_supabase", lambda: client)
    monkeypatch.setattr(settings, "SUPABASE_URL", nt.SUPABASE_URL)
    monkeypatch.setattr(settings, "LEARN_NARRATOR_ENGINE", "chatterbox")
    return client


@pytest.fixture
def alerts(monkeypatch):
    """`alerts.notify` / `notify_terminal`, recorded (narration imports the module at call time)."""
    mod = types.ModuleType("app.services.learn_weekly.alerts")
    mod.lines = []
    mod.terminal = []
    mod.fail_with = None

    async def notify(text, *, loud=True):
        if mod.fail_with is not None:
            raise mod.fail_with
        mod.lines.append((text, loud))
        return True

    async def notify_terminal(piece, *, status, reason, stage=None):
        mod.terminal.append((piece.get("id"), status, reason))

    mod.notify = notify
    mod.notify_terminal = notify_terminal
    monkeypatch.setitem(sys.modules, "app.services.learn_weekly.alerts", mod)
    return mod


def _catalog_job(fake, *, slug=SLUG, content=None, round=1):
    """What `scripts/learn_catalog_narrate.py --yes` enqueues, synchronously."""
    blocks = narration.narration_blocks(content or nt.ARTICLE)
    job = ledger.enqueue_catalog_job_sync(slug, round=round, script=narration.script_json(blocks),
                                          script_sha256=narration.script_sha256(blocks), engine="chatterbox",
                                          voice=narration.voice_key(narration.voice_spec("chatterbox")),
                                          pipeline_version=narration.PIPELINE_VERSION, sb=fake)
    assert job is not None
    return job


def _piece_reads(fake):
    return [call for call in fake.calls if call[0] == ledger.PIECES]


async def _claimed(fake):
    wire = await narration.claim_for_worker(["chatterbox"], worker_version="test/1", now=NOW)
    assert wire is not None
    return wire, WorkerClaim(wire["attempts"], wire["claim_nonce"])


async def _rendered(fake, *, spw=0.36, sha="ef" * 32, slug=SLUG, bytes_per_second=12_600):
    wire, claim = await _claimed(fake)
    t, end = nt._timings(nt._blocks(), spw=spw)
    path = f"audio/catalog/{slug}-{sha[:12]}.m4a"
    size = int(end * bytes_per_second)
    fake.storage.put(path, size)
    kwargs = dict(path=path, size_bytes=size, duration_seconds=end, engine="chatterbox",
                  pipeline_version=narration.PIPELINE_VERSION, timings=t, checks={"coverage": 0.99})
    return wire, claim, kwargs


# ── paths ────────────────────────────────────────────────────────────────────────────────────


def test_the_catalog_path_is_content_addressed_under_its_own_prefix():
    sha = "0123456789abcdef" * 4
    assert narration.catalog_audio_path(SLUG, sha) == f"audio/catalog/{SLUG}-0123456789ab.m4a"
    assert narration.is_catalog_audio_path(f"audio/catalog/{SLUG}-0123456789ab.m4a", SLUG)
    assert not narration.is_catalog_audio_path(f"audio/catalog/{SLUG}-0123456789ab.m4a", "kodak")
    assert not narration.is_catalog_audio_path(f"audio/weekly/{SLUG}-0123456789ab.m4a")
    assert not narration.is_catalog_audio_path(f"audio/catalog/{SLUG}-0123456789ab.m4a\n")
    for bad_slug in ("Coca-Cola", "a/b", "../x", "", None):
        with pytest.raises(ValueError):
            narration.catalog_audio_path(bad_slug, sha)
    with pytest.raises(ValueError):
        narration.catalog_audio_path(SLUG, "AB" * 32)
    # Never a weekly object: the publisher and the promote script read only audio/weekly/.
    assert not narration._WEEKLY_AUDIO_RE.fullmatch(narration.catalog_audio_path(SLUG, sha))


# ── claim ────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_catalog_job_is_handed_out_like_a_weekly_one_with_no_piece_lookup(fake):
    job = _catalog_job(fake)
    wire, claim = await _claimed(fake)
    assert wire["id"] == job["id"] and wire["round"] == 1 and wire["engine"] == "chatterbox"
    assert wire["voice"] == narration.CHATTERBOX_VOICE
    assert wire["blocks"] == [b.wire() for b in nt._blocks()]
    assert set(wire) == {"id", "round", "engine", "voice", "pipeline_version", "attempts", "claim_nonce",
                         "lease_until", "blocks"}, "the worker's wire view is unchanged"
    assert _piece_reads(fake) == [], "a catalog job never reads learn_weekly_pieces"
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_IN_PROGRESS


@pytest.mark.asyncio
async def test_a_catalog_job_with_an_unreadable_script_is_failed_with_the_owner_alert(fake, alerts):
    job = _catalog_job(fake)
    fake.row(ledger.JOBS, job["id"])["script"] = [{"i": 0, "text": "x", "role": "body", "dest": ["title"]}]
    assert await narration.claim_for_worker(["chatterbox"], worker_version="t", now=NOW) is None
    row = fake.row(ledger.JOBS, job["id"])
    assert row["status"] == c.JOB_FAILED and "script_unreadable" in row["error"]
    assert _piece_reads(fake) == [] and alerts.terminal == []
    (text, loud), = alerts.lines
    assert loud is True and "Catalog narration FAILED" in text and SLUG in text
    assert "round 2" in text and "learn_catalog_narrate.py --slug coca-cola-vs-pepsico" in text


@pytest.mark.asyncio
async def test_a_catalog_job_with_an_unreadable_slug_is_failed_never_rendered(fake, alerts):
    """Defence in depth: 207's CHECK refuses such a row, so the fake's CHECKs are switched off to model
    one anyway — a slug the server cannot read never becomes a Storage path."""
    job = _catalog_job(fake)
    fake.tables[ledger.JOBS].check = lambda row: []
    fake.row(ledger.JOBS, job["id"])["catalog_slug"] = "../../etc"
    assert await narration.claim_for_worker(["chatterbox"], worker_version="t", now=NOW) is None
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_FAILED
    assert len(alerts.lines) == 1


# ── upload ───────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_names_the_catalog_path_and_follows_the_same_rules(fake, caplog):
    _catalog_job(fake)
    wire, claim = await _claimed(fake)
    sha = "cd" * 32
    with caplog.at_level("INFO"):
        out = await narration.upload_target(wire["id"], claim, content_type="audio/mp4", size_bytes=1000, sha256=sha)
    path = f"audio/catalog/{SLUG}-{sha[:12]}.m4a"
    assert out["path"] == path and out["already_uploaded"] is False
    assert ("sign", narration.AUDIO_BUCKET, path) in fake.storage.calls
    assert "SECRET-TOKEN" not in caplog.text and f"catalog {SLUG}" in caplog.text
    # The same object already there: no URL. Another object there: refused and left in place.
    fake.storage.put(path, 1000)
    again = await narration.upload_target(wire["id"], claim, content_type="audio/mp4", size_bytes=1000, sha256=sha)
    assert again == {"upload_url": None, "path": path, "content_type": "audio/mp4", "already_uploaded": True}
    fake.storage.put(path, 999)
    with pytest.raises(LearnNarratorAssetMismatch):
        await narration.upload_target(wire["id"], claim, content_type="audio/mp4", size_bytes=1000, sha256=sha)
    assert (narration.AUDIO_BUCKET, path) in fake.storage.objects
    assert _piece_reads(fake) == []


# ── complete ─────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_complete_marks_the_job_ready_moves_no_piece_and_tells_the_owner(fake, alerts):
    job = _catalog_job(fake)
    wire, claim, kw = await _rendered(fake)
    out = await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert out == {"ok": True, "status": c.JOB_READY, "replay": False, "piece_updated": False}
    row = fake.row(ledger.JOBS, job["id"])
    assert row["status"] == c.JOB_READY and row["audio_path"] == kw["path"] and row["piece_id"] is None
    assert row["timings"] == narration.validate_timings(nt._blocks(), kw["timings"],
                                                        duration_seconds=kw["duration_seconds"])
    assert row["checks"]["server"]["sentences"] > 5 and row["checks"]["worker"]["coverage"] == 0.99
    assert _piece_reads(fake) == [] and fake.tables[ledger.PIECES].rows == []
    (text, loud), = alerts.lines
    assert loud is True and text.startswith("🎙 Catalog narration READY: coca-cola-vs-pepsico (")
    assert f"({kw['duration_seconds']:.0f}s, round 1)" in text
    assert text.endswith("run scripts/learn_catalog_fetch.py --slug coca-cola-vs-pepsico")
    assert "http" not in text, "no URL in an alert line"


@pytest.mark.asyncio
async def test_a_replayed_catalog_complete_writes_and_sends_nothing(fake, alerts):
    _catalog_job(fake)
    wire, claim, kw = await _rendered(fake)
    await narration.complete(wire["id"], claim, now=NOW, **kw)
    before = copy.deepcopy({k: v.rows for k, v in fake.tables.items()})
    writes = sum(1 for _, op in fake.calls if op in ("update", "insert", "upsert"))
    out = await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert out == {"ok": True, "status": c.JOB_READY, "replay": True, "piece_updated": False}
    assert sum(1 for _, op in fake.calls if op in ("update", "insert", "upsert")) == writes
    assert before == {k: v.rows for k, v in fake.tables.items()}
    assert len(alerts.lines) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("path, why", [
    (f"audio/weekly/{SLUG}-{'ef' * 6}.m4a", "not a catalog narration path"),        # the weekly prefix
    (f"audio/catalog/kodak-{'ef' * 6}.m4a", "not a catalog narration path"),         # another slug
    (f"audio/catalog/{SLUG}-{'ef' * 5}.m4a", "not a catalog narration path"),        # a short sha
    (f"audio/{SLUG}.m4a", "not a catalog narration path"),                           # the seed's object
])
async def test_complete_accepts_only_the_jobs_own_catalog_path(fake, alerts, path, why):
    job = _catalog_job(fake)
    wire, claim, kw = await _rendered(fake)
    fake.storage.put(path, kw["size_bytes"])
    kw["path"] = path
    with pytest.raises(LearnNarratorRequestInvalid) as e:
        await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert why in str(e.value) and SLUG in str(e.value)
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_IN_PROGRESS
    assert not any(call[0] == "remove" for call in fake.storage.calls) and alerts.lines == []


@pytest.mark.asyncio
@pytest.mark.parametrize("breaks", ["engine", "pipeline", "timings", "mismatch"])
async def test_every_catalog_complete_refusal_names_the_article(fake, alerts, caplog, breaks):
    """`catalog_slug` is an errors.CONTEXT_KEYS identifier: each refusal's message (the log line and the
    worker's error body) ends `[catalog_slug=… job_id=… op=complete]`, with no "not in CONTEXT_KEYS"
    WARNING and no empty piece_id."""
    job = _catalog_job(fake)
    wire, claim, kw = await _rendered(fake, bytes_per_second=9_700 if breaks == "mismatch" else 12_600)
    if breaks == "engine":
        kw["engine"] = "kokoro"
    elif breaks == "pipeline":
        kw["pipeline_version"] = "learn-narration/v0"
    elif breaks == "timings":
        kw["timings"][3]["sentences"][0]["end"] = kw["timings"][3]["sentences"][0]["start"] + 0.01
    with caplog.at_level(logging.WARNING, logger="app.services.learn_weekly.errors"):
        with pytest.raises((LearnNarratorRequestInvalid, LearnNarratorAssetMismatch)) as e:
            await narration.complete(wire["id"], claim, now=NOW, **kw)
    msg = str(e.value)
    assert msg.endswith(f"[catalog_slug={SLUG} job_id={job['id']} op=complete]"), msg
    assert e.value.ids == {"catalog_slug": SLUG, "job_id": job["id"], "op": "complete"}
    assert "not in CONTEXT_KEYS" not in caplog.text and "piece_id" not in msg


def test_catalog_slug_is_a_rendered_identifier():
    from app.services.learn_weekly import errors
    assert "catalog_slug" in errors.CONTEXT_KEYS
    err = LearnNarratorRequestInvalid("complete: bad", job_id="j1", piece_id=None, op="complete",
                                      catalog_slug="coca-cola-vs-pepsico")
    assert str(err) == "complete: bad [catalog_slug=coca-cola-vs-pepsico job_id=j1 op=complete]"


@pytest.mark.asyncio
async def test_a_weekly_job_never_accepts_a_catalog_path(fake):
    piece, job = nt._media_pending(fake)
    wire, claim = await _claimed(fake)
    t, end = nt._timings(nt._blocks())
    path = f"audio/catalog/{nt.SLUG}-{'ef' * 6}.m4a"
    fake.storage.put(path, int(end * 12_600))
    with pytest.raises(LearnNarratorRequestInvalid) as e:
        await narration.complete(wire["id"], claim, path=path, size_bytes=int(end * 12_600), duration_seconds=end,
                                 engine="chatterbox", pipeline_version=narration.PIPELINE_VERSION, timings=t, now=NOW)
    assert "not a weekly narration path" in str(e.value)
    assert fake.row(ledger.PIECES, piece["id"])["status"] == c.MEDIA_PENDING


@pytest.mark.asyncio
async def test_the_catalog_timing_gate_is_the_weekly_one(fake, alerts):
    """A dropped sentence (seconds-per-word under the floor) is refused before any Storage I/O."""
    _catalog_job(fake)
    wire, claim, kw = await _rendered(fake)
    kw["timings"][3]["sentences"][0]["end"] = kw["timings"][3]["sentences"][0]["start"] + 0.01
    calls = len(fake.storage.calls)
    with pytest.raises(LearnNarratorRequestInvalid) as e:
        await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert "s/word" in str(e.value)
    assert len(fake.storage.calls) == calls and alerts.lines == []


@pytest.mark.asyncio
async def test_a_catalog_asset_mismatch_is_deleted_requeued_then_failed_loudly(fake, alerts):
    job = _catalog_job(fake)
    for attempt, expected in ((1, c.JOB_QUEUED), (2, c.JOB_FAILED)):
        wire, claim, kw = await _rendered(fake, bytes_per_second=9_700)     # the Gemini-path encode
        with pytest.raises(LearnNarratorAssetMismatch):
            await narration.complete(wire["id"], claim, now=NOW, **kw)
        assert (narration.AUDIO_BUCKET, kw["path"]) not in fake.storage.objects
        row = fake.row(ledger.JOBS, job["id"])
        assert row["status"] == expected and "asset_mismatch" in row["error"]
        assert len(alerts.lines) == (attempt - 1)
    (text, _), = alerts.lines
    assert "Catalog narration FAILED: coca-cola-vs-pepsico round 1" in text and "bytes/s" in text
    assert "(round 2)" in text and alerts.terminal == [] and _piece_reads(fake) == []


@pytest.mark.asyncio
async def test_the_narrator_images_encode_rate_is_accepted_for_a_catalog_job(fake, alerts):
    _catalog_job(fake)
    wire, claim, kw = await _rendered(fake, bytes_per_second=13_990)
    assert (await narration.complete(wire["id"], claim, now=NOW, **kw))["status"] == c.JOB_READY


# ── fail ─────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_catalog_failure_requeues_under_the_cap_and_alerts_at_it(fake, alerts):
    job = _catalog_job(fake, round=3)
    wire, claim = await _claimed(fake)
    out = await narration.fail(wire["id"], claim, code="render_oom", detail="killed", now=NOW)
    assert out["status"] == c.JOB_QUEUED and alerts.lines == []
    wire, claim = await _claimed(fake)
    out = await narration.fail(wire["id"], claim, code="render_oom", detail="killed again", now=NOW)
    assert out["status"] == c.JOB_FAILED
    (text, loud), = alerts.lines
    assert loud and "round 3 — render_oom: killed again" in text and "(round 4)" in text
    assert fake.row(ledger.JOBS, job["id"])["piece_id"] is None and _piece_reads(fake) == []


@pytest.mark.asyncio
async def test_a_deterministic_catalog_failure_alerts_at_the_first_report(fake, alerts):
    _catalog_job(fake)
    wire, claim = await _claimed(fake)
    out = await narration.fail(wire["id"], claim, code="voice_out_of_band", detail="F0 128 Hz", now=NOW)
    assert out["status"] == c.JOB_FAILED and len(alerts.lines) == 1
    assert "voice_out_of_band: F0 128 Hz" in alerts.lines[0][0]


@pytest.mark.asyncio
async def test_a_replayed_catalog_fail_sends_the_owner_no_second_alert(fake, alerts, caplog):
    """The worker retries /fail on a lost response (502/503/504, a transport error) and `fail` waits on
    the Telegram pacer before it answers. The retry is a replay: the ledger writes nothing, and the
    catalog alert — unfenced, unlike a piece step — must not go out a second time."""
    job = _catalog_job(fake)
    wire, claim = await _claimed(fake)
    first = await narration.fail(wire["id"], claim, code="voice_out_of_band", detail="F0 128 Hz", now=NOW)
    row = copy.deepcopy(fake.row(ledger.JOBS, job["id"]))
    with caplog.at_level("INFO"):
        again = await narration.fail(wire["id"], claim, code="voice_out_of_band", detail="F0 128 Hz",
                                     now=NOW + timedelta(seconds=31))
    assert first == again == {"ok": True, "status": c.JOB_FAILED, "worker_failures": 1}
    assert fake.row(ledger.JOBS, job["id"]) == row, "the replay wrote nothing"
    assert len(alerts.lines) == 1 and "Catalog narration FAILED" in alerts.lines[0][0]
    assert "a replay of a failure already recorded" in caplog.text
    # The same replay with the SAME timestamp (a test clock, or a fast retry) is still a replay.
    await narration.fail(wire["id"], claim, code="voice_out_of_band", detail="F0 128 Hz", now=NOW)
    assert len(alerts.lines) == 1


@pytest.mark.asyncio
async def test_a_replayed_catalog_fail_at_the_worker_cap_alerts_once(fake, alerts):
    _catalog_job(fake)
    wire, claim = await _claimed(fake)
    assert (await narration.fail(wire["id"], claim, code="render_oom", detail="1", now=NOW))["status"] \
        == c.JOB_QUEUED
    wire, claim = await _claimed(fake)
    for _ in range(3):  # the closing report, then two retries of it
        out = await narration.fail(wire["id"], claim, code="render_oom", detail="2", now=NOW)
        assert out["status"] == c.JOB_FAILED and out["worker_failures"] == 2
    assert len(alerts.lines) == 1


@pytest.mark.asyncio
async def test_a_replayed_weekly_fail_still_reruns_its_fenced_piece_step(fake, alerts, monkeypatch):
    """The weekly side of `_after_fail_recorded` is unchanged: a replay re-runs `after_job_failed` (fenced
    on narration_job_id), which heals a first call whose piece write was lost."""
    piece, job = nt._media_pending(fake)
    wire, claim = await _claimed(fake)
    await narration.fail(wire["id"], claim, code="voice_out_of_band", detail="x", now=NOW)
    seen = []
    real = narration.after_job_failed

    async def spy(row, *, now=None):
        seen.append(row["id"])
        await real(row, now=now)

    monkeypatch.setattr(narration, "after_job_failed", spy)
    await narration.fail(wire["id"], claim, code="voice_out_of_band", detail="x", now=NOW)
    assert seen == [job["id"]]
    assert alerts.lines == [], "a weekly piece reports through notify_terminal, never the catalog line"


# ── the lease re-claim cap (a render killed again and again) ─────────────────────────────────


async def _expire_to_the_cap(fake, *, start=NOW):
    """Claim the open job, then let its lease run out MAX_JOB_LEASE_RECLAIMS times (each re-claim
    through the worker's own claim route). Returns the time of the claim that will close it."""
    at = start
    for n in range(c.MAX_JOB_LEASE_RECLAIMS + 1):
        wire = await narration.claim_for_worker(["chatterbox"], worker_version="t", now=at)
        assert wire is not None and wire["attempts"] == n + 1
        at += timedelta(seconds=c.JOB_LEASE_SECONDS + 1)
    return at


@pytest.mark.asyncio
async def test_a_catalog_job_closed_at_the_lease_reclaim_cap_alerts_the_owner_once(fake, alerts):
    """`ledger.claim_job` closes a job whose lease ran out once past MAX_JOB_LEASE_RECLAIMS. A weekly job
    is then found by the scheduler's sweep through its piece; a catalog job has none, so the claim that
    closed it reports it — exactly once, because the close is fenced."""
    job = _catalog_job(fake, round=2)
    at = await _expire_to_the_cap(fake)
    assert alerts.lines == []
    assert await narration.claim_for_worker(["chatterbox"], worker_version="t", now=at) is None
    row = fake.row(ledger.JOBS, job["id"])
    assert row["status"] == c.JOB_FAILED and row["error"].startswith("lease_reclaims_exhausted")
    (text, loud), = alerts.lines
    assert loud is True and "Catalog narration FAILED: coca-cola-vs-pepsico round 2" in text
    assert "lease_reclaims_exhausted" in text and "(round 3)" in text
    assert alerts.terminal == [] and _piece_reads(fake) == []
    # Later claims see a failed job: nothing closed, nothing alerted again.
    assert await narration.claim_for_worker(["chatterbox"], worker_version="t",
                                            now=at + timedelta(hours=1)) is None
    assert len(alerts.lines) == 1


@pytest.mark.asyncio
async def test_the_claim_that_closes_a_capped_catalog_job_still_hands_out_the_next_one(fake, alerts):
    capped = _catalog_job(fake)
    at = await _expire_to_the_cap(fake)
    nxt = _catalog_job(fake, slug="kodak-inventing-the-future-it-couldnt-sell")
    wire = await narration.claim_for_worker(["chatterbox"], worker_version="t", now=at)
    assert wire is not None and wire["id"] == nxt["id"]
    assert fake.row(ledger.JOBS, capped["id"])["status"] == c.JOB_FAILED
    (text, _), = alerts.lines
    assert "coca-cola-vs-pepsico" in text and "kodak" not in text


@pytest.mark.asyncio
async def test_a_weekly_job_closed_at_the_cap_is_left_to_the_scheduler_sweep(fake, alerts):
    """Weekly behaviour is unchanged: the claim only closes it; `scheduler._narration_step` moves the
    piece (its own tests pin that), so the claim neither alerts nor touches the piece."""
    piece, job = nt._media_pending(fake)
    at = await _expire_to_the_cap(fake)
    assert await narration.claim_for_worker(["chatterbox"], worker_version="t", now=at) is None
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_FAILED
    assert fake.row(ledger.PIECES, piece["id"])["status"] == c.MEDIA_PENDING
    assert alerts.lines == [] and alerts.terminal == []


@pytest.mark.asyncio
async def test_after_job_failed_on_a_catalog_job_only_alerts(fake, alerts):
    job = _catalog_job(fake)
    failed = {**fake.row(ledger.JOBS, job["id"]), "status": c.JOB_FAILED, "error": "lease_reclaims_exhausted: x"}
    calls = len(fake.calls)
    await narration.after_job_failed(failed, now=NOW)
    assert len(fake.calls) == calls, "no ledger read or write for a catalog job"
    assert "lease_reclaims_exhausted" in alerts.lines[0][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("boom", [RuntimeError("telegram down"), ValueError("bad")])
async def test_an_alert_that_cannot_be_sent_never_raises(fake, alerts, boom, caplog):
    _catalog_job(fake)
    alerts.fail_with = boom
    wire, claim, kw = await _rendered(fake)
    with caplog.at_level("ERROR"):
        out = await narration.complete(wire["id"], claim, now=NOW, **kw)
    assert out["status"] == c.JOB_READY
    assert "could not be sent" in caplog.text and "learn_catalog_fetch.py" in caplog.text


def test_the_alert_texts_survive_junk_fields():
    assert "?s, round ?" in narration.catalog_ready_text({"catalog_slug": SLUG, "duration_seconds": float("nan"),
                                                          "round": True})
    text = narration.catalog_failed_text({"catalog_slug": SLUG, "round": None, "error": "a\nb" * 400})
    assert "\n" not in text and "(round max + 1)" in text and len(text) < 600


# ── the HTTP surface: a catalog job end to end through the worker's own routes ────────────────


def test_a_catalog_job_runs_end_to_end_through_the_internal_api(fake, alerts, monkeypatch):
    from app.api.v1.endpoints import learn_internal as mod
    from app.main import app

    monkeypatch.setattr(settings, "LEARN_NARRATOR_TOKEN", "tok-catalog")
    monkeypatch.setattr(mod, "_unset_warned", False)
    monkeypatch.setattr(narration, "_now", lambda: NOW)
    job = _catalog_job(fake)
    client = TestClient(app)        # NOT `with`: the lifespan's jobs would reach Supabase
    base, h = "/api/v1/internal/learn", {"X-Learn-Narrator-Token": "tok-catalog"}
    r = client.post(f"{base}/jobs/claim", headers=h, json={"engines": ["chatterbox"], "worker_version": "w/1"})
    assert r.status_code == 200, r.text
    wire = r.json()["job"]
    hc = {**h, "X-Learn-Claim": f"{wire['attempts']}.{wire['claim_nonce']}"}
    t, end = nt._timings(nt._blocks())
    size, sha = int(end * 12_600), "ef" * 32
    r = client.post(f"{base}/jobs/{wire['id']}/upload", headers=hc,
                    json={"content_type": "audio/mp4", "bytes": size, "sha256": sha})
    assert r.status_code == 200, r.text
    path = r.json()["path"]
    assert path == f"audio/catalog/{SLUG}-{sha[:12]}.m4a"
    fake.storage.put(path, size)                               # the worker's signed PUT
    r = client.post(f"{base}/jobs/{wire['id']}/complete", headers=hc,
                    json={"path": path, "bytes": size, "duration_seconds": end, "engine": "chatterbox",
                          "pipeline_version": narration.PIPELINE_VERSION, "blocks": t, "checks": {}})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == c.JOB_READY and r.json()["piece_updated"] is False
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_READY and len(alerts.lines) == 1


# ── the worker stays subject-agnostic (so the Railway narrator image is not rebuilt) ──────────


def test_the_worker_never_learns_whether_a_job_is_a_piece_or_a_catalog_article():
    """The worker takes the upload path from the server and reports timings for the blocks it was
    handed; nothing in it may branch on the subject — a catalog job must run on the deployed image."""
    for f in sorted(_WORKER.glob("*.py")) + sorted((_WORKER / "engines").glob("*.py")):
        src = f.read_text(encoding="utf-8")
        for token in ("piece_id", "catalog_slug", "audio/weekly", "audio/catalog", "learn_weekly_pieces"):
            assert token not in src, f"{f.name} mentions {token!r}"
