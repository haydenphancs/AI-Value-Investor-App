"""`scripts/learn_weekly_promote.py` — publishing the W44 rehearsal (owner decision 2026-11-01).

The script strips the pre-2026-11-01 "AI-assisted" label from a rehearsed piece's text AND its
narration (the closing callout is spoken), then promotes it through `ledger.promote_rehearsed_sync`.
Everything here is offline: the ledger, Storage, ffmpeg and Telegram are stubbed."""

from __future__ import annotations

import copy
import importlib.util
import inspect
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.services.learn_weekly import article_schema as A
from app.services.learn_weekly import build, ledger, narration, publisher
from app.services.learn_weekly import contracts as C

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "learn_weekly_promote.py"
LEGACY = C.LEGACY_DISCLOSURE_TEXTS[0]
NOW = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
OLD_PATH = "audio/weekly/semiconductor-giants-and-ai-infrastructure-2026-10-30-34a8d0184084.m4a"


def _load():
    spec = importlib.util.spec_from_file_location("learn_weekly_promote", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["learn_weekly_promote"] = mod
    spec.loader.exec_module(mod)
    return mod


P = _load()


def _draft() -> Dict[str, Any]:
    return {
        "slug": "semiconductor-giants-and-ai-infrastructure-2026-10-30",
        "title": "Semiconductor Giants and AI Infrastructure",
        "subtitle": "How chip makers supply data centers.",
        "category": "battles", "isFeatured": True, "readTimeMinutes": 12, "viewCount": "",
        "author": {"name": "Caydex Research", "title": "AI-assisted · Editor-reviewed", "isVerified": False},
        "audioUrl": None, "hasAudioVersion": True, "audioDurationSeconds": 727,
        "sections": [
            {"title": "The Peg", "icon": "bolt.fill", "content": [
                {"type": "paragraph", "text": "Three companies filed reports. Each one matters."}]},
            {"title": "Lessons", "icon": "lightbulb.fill", "content": [
                {"type": "paragraph", "text": "Long agreements steady revenue."},
                {"type": "callout", "style": "info", "icon": "info.circle.fill", "text": LEGACY}]},
        ],
    }


def _timings(draft) -> List[Dict[str, Any]]:
    """One entry per narrated block, 2 s each with a 1.2 s gap before the closing."""
    out, t = [], 0.0
    blocks = narration.narration_blocks(draft)
    for b in blocks:
        if b.i == blocks[-1].i:
            t += 1.2
        sentences = narration.sentence_split(b.text) or [b.text]
        start = t
        sent = []
        for s in sentences:
            sent.append({"text": s, "start": round(t, 3), "end": round(t + 2.0, 3)})
            t += 2.0
        out.append({"i": b.i, "start": round(start, 3), "end": round(t, 3), "sentences": sent})
    return out


def _piece_job(**piece_over):
    draft = _draft()
    pid, jid = str(uuid.uuid4()), str(uuid.uuid4())
    piece = {"id": pid, "period_key": "2026-W44:deep_dive", "kind": "deep_dive", "status": C.REHEARSED,
             "dry_run": True, "narration_job_id": jid, "draft": draft, "draft_sha256": narration.draft_sha256(draft),
             "review_round": 0, "updated_at": "2026-10-31T03:00:36.658374+00:00", "slug": draft["slug"],
             "title": draft["title"], "category": "battles", "validation": {"outcome": "accepted"}}
    piece.update(piece_over)
    timings = _timings(draft)
    job = {"id": jid, "piece_id": pid, "status": C.JOB_READY, "engine": C.ENGINE_CHATTERBOX,
           "audio_path": OLD_PATH, "audio_bytes": 9_375_595, "duration_seconds": timings[-1]["end"],
           "timings": timings, "script": narration.script_json(narration.narration_blocks(draft)), "checks": {}}
    return piece, job


# ── plan_promote ─────────────────────────────────────────────────────────────────────────────


def test_plan_promote_accepts_a_rehearsal_and_derives_the_hold():
    piece, job = _piece_job()
    plan = P.plan_promote(piece, job, now=NOW, publish_at=NOW + timedelta(minutes=15), hold_hours=48)
    assert plan["hold_until"] == NOW + timedelta(minutes=15, hours=48) and plan["legacy_text"] == LEGACY


def _bad_cases():
    def last_text(text):
        piece, job = _piece_job()
        piece["draft"]["sections"][-1]["content"][-1]["text"] = text
        return piece, job

    def subtitle_says_it():
        piece, job = _piece_job()
        piece["draft"]["subtitle"] = "An AI-assisted look at chips."
        return piece, job

    def job_edit(**kw):
        piece, job = _piece_job()
        job.update(kw)
        return piece, job

    return [
        ("status", lambda: _piece_job(status=C.APPROVED), "only a rehearsed piece"),
        ("live", lambda: _piece_job(dry_run=False), "dry_run"),
        ("no job", lambda: (_piece_job()[0], None), "was not found"),
        ("other job", lambda: (_piece_job()[0], _piece_job()[1]), "was not found"),
        ("not ready", lambda: job_edit(status=C.JOB_QUEUED), "not this piece's ready audio"),
        ("other piece", lambda: job_edit(piece_id=str(uuid.uuid4())), "not this piece's ready audio"),
        ("bad path", lambda: job_edit(audio_path="audio/x.m4a"), "audio_path"),
        ("already stripped", lambda: last_text(C.DISCLOSURE_TEXT), "legacy AI disclosure"),
        ("label elsewhere", subtitle_says_it, "outside the byline"),
    ]


@pytest.mark.parametrize("name,make,match", _bad_cases(), ids=[c[0] for c in _bad_cases()])
def test_plan_promote_refuses(name, make, match):
    piece, job = make()
    with pytest.raises(SystemExit, match=match):
        P.plan_promote(piece, job, now=NOW, publish_at=NOW + timedelta(minutes=15), hold_hours=48)


def test_plan_promote_refuses_a_publish_time_too_close():
    piece, job = _piece_job()
    with pytest.raises(SystemExit, match="at least 10 min"):
        P.plan_promote(piece, job, now=NOW, publish_at=NOW + timedelta(minutes=9), hold_hours=48)


# ── plan_cut ─────────────────────────────────────────────────────────────────────────────────


def test_plan_cut_lands_in_the_silence_before_the_disclosure():
    piece, job = _piece_job()
    cut = P.plan_cut(job["timings"], piece["draft"], pad=0.45)
    prev, last = job["timings"][-2], job["timings"][-1]
    assert cut["prev_end"] == prev["end"] and cut["disclosure_start"] == last["start"]
    assert cut["cut"] == round(prev["end"] + 0.45, 3) < last["start"]


def test_plan_cut_never_cuts_into_the_disclosure_when_the_gap_is_small():
    piece, job = _piece_job()
    t = copy.deepcopy(job["timings"])
    t[-1]["start"] = t[-1]["sentences"][0]["start"] = t[-2]["end"] + 0.2
    cut = P.plan_cut(t, piece["draft"], pad=0.45)
    assert cut["cut"] == round(t[-1]["start"] - P.DISCLOSURE_GUARD, 3)


def test_plan_cut_refuses_overlap_a_foreign_table_and_a_negative_pad():
    piece, job = _piece_job()
    overlap = copy.deepcopy(job["timings"])
    overlap[-1]["start"] = overlap[-1]["sentences"][0]["start"] = overlap[-2]["end"]
    with pytest.raises(SystemExit, match="no gap"):
        P.plan_cut(overlap, piece["draft"])
    with pytest.raises(SystemExit, match="ends on blocks"):
        P.plan_cut(job["timings"][:-1], piece["draft"])
    with pytest.raises(SystemExit, match="unreadable"):
        P.plan_cut("junk", piece["draft"])
    with pytest.raises(SystemExit, match="pad"):
        P.plan_cut(job["timings"], piece["draft"], pad=float("nan"))


# ── promoted_draft ───────────────────────────────────────────────────────────────────────────


def test_promoted_draft_is_the_audio_minus_the_closing_with_the_catalog_byline():
    piece, job = _piece_job()
    url = publisher.audio_public_url(OLD_PATH)
    new = P.promoted_draft(piece["draft"], job["script"], audio_url=url, duration_seconds=711.6)
    assert new["author"] == C.AUTHOR
    assert new["sections"][-1]["content"][-1]["text"] == "Long agreements steady revenue."
    assert new["audioUrl"] == url and new["audioDurationSeconds"] == 712 and new["hasAudioVersion"] is True
    assert "ai-assisted" not in A.canonical_json(new).casefold()
    assert narration.script_json(narration.narration_blocks(new)) == job["script"][:-1]
    assert piece["draft"]["sections"][-1]["content"][-1]["text"] == LEGACY, "the input is not mutated"


def test_promoted_draft_refuses_text_that_drifted_from_the_audio_or_an_empty_section():
    piece, job = _piece_job()
    drifted = copy.deepcopy(job["script"])
    drifted[0]["text"] = "Another title"
    with pytest.raises(SystemExit, match="would\nnot match|not match"):
        P.promoted_draft(piece["draft"], drifted, audio_url="u", duration_seconds=10)
    lone = _draft()
    lone["sections"][-1]["content"] = [lone["sections"][-1]["content"][-1]]
    with pytest.raises(SystemExit, match="empty"):
        P.promoted_draft(lone, job["script"], audio_url="u", duration_seconds=10)


def test_the_publisher_accepts_the_promoted_piece():
    piece, job = _piece_job()
    new = P.promoted_draft(piece["draft"], job["script"], audio_url=publisher.audio_public_url(OLD_PATH),
                           duration_seconds=700.0)
    row, problem = publisher.build_article_row(dict(piece, draft=new, status=C.APPROVED, dry_run=False),
                                               dict(job, duration_seconds=700.0), now=NOW)
    assert problem is None and row["author_name"] == "Caydex Research"
    assert row["content"]["author"] == C.AUTHOR and row["audio_duration_seconds"] == 700


def test_trim_is_a_stream_copy_that_stops_at_the_cut():
    argv = P.trim_argv("in.m4a", "out.m4a", 718.883)
    assert argv[0] == "ffmpeg" and ["-c", "copy"] == argv[argv.index("-c"):argv.index("-c") + 2]
    assert argv[argv.index("-t") + 1] == "718.883" and "+bitexact" in argv
    assert "-shortest" not in argv and "-loop" not in argv and argv[-1] == "out.m4a"


# ── main: the write order ────────────────────────────────────────────────────────────────────


_REAL_REPOINT = ledger.repoint_ready_audio_sync
_REAL_PROMOTE = ledger.promote_rehearsed_sync


class _World:
    """Stubs for every I/O seam; records the order of writes."""

    def __init__(self, monkeypatch, piece, job, *, promote_ok=True):
        self.piece, self.job, self.log = piece, job, []
        self.bucket: Dict[str, int] = {OLD_PATH: job["audio_bytes"]}
        self.duration = job["timings"][-2]["end"] + 0.4
        self.size = int(12_500 * self.duration)
        self.promote_ok = promote_ok
        monkeypatch.setattr(ledger, "get_piece_sync", lambda key: self.piece)
        monkeypatch.setattr(ledger, "get_job_sync", lambda jid: self.job)
        monkeypatch.setattr(ledger, "list_pieces_sync", lambda **kw: [self.piece])
        monkeypatch.setattr(build, "_article_keys_sync", lambda: [{"title": "Visa vs. Mastercard", "slug": "visa"}])
        monkeypatch.setattr(ledger, "repoint_ready_audio_sync", self.repoint)
        monkeypatch.setattr(ledger, "promote_rehearsed_sync", self.promote)
        monkeypatch.setattr(P, "_download", self.download)
        monkeypatch.setattr(P, "_upload", self.upload)
        monkeypatch.setattr(P, "_stat", self.stat)
        monkeypatch.setattr(P, "_run", self.run)
        monkeypatch.setattr(P, "deploy_problem", lambda: self.deploy)
        monkeypatch.setattr(P, "_bot_configured", lambda: self.bot)
        self.deploy, self.bot, self.notified, self.repoint_ok = None, True, True, True

        async def _notify(row, when):
            self.log.append("notify")
            return self.notified
        monkeypatch.setattr(P, "_notify", _notify)

    def download(self, bucket, path):
        self.log.append(("download", path))
        return b"\0" * 1000

    def run(self, argv):
        if argv[0] == "ffmpeg":
            self.log.append("trim")
            Path(argv[-1]).write_bytes(b"\1" * self.size)
            return ""
        self.log.append("probe")
        return f"{self.duration:.6f}\n"

    def stat(self, bucket, path):
        size = self.bucket.get(path)
        return None if size is None else {"size": size, "mimetype": "audio/mp4"}

    def upload(self, bucket, path, data, content_type):
        self.log.append(("upload", path, content_type))
        self.bucket[path] = len(data)

    def repoint(self, job_id, **kw):
        # Bind against the REAL ledger signature, so a renamed keyword fails here, not in production.
        inspect.signature(_REAL_REPOINT).bind(job_id, **kw)
        self.log.append(("repoint", kw["old_audio_path"], kw["new_audio_path"]))
        if not self.repoint_ok:
            return None
        self.job = dict(self.job, audio_path=kw["new_audio_path"], audio_bytes=kw["new_audio_bytes"],
                        duration_seconds=kw["new_duration_seconds"], checks={"promote": dict(kw["note"])})
        return self.job

    def promote(self, piece_id, **kw):
        inspect.signature(_REAL_PROMOTE).bind(piece_id, **kw)
        # The real function's argument checks, run with no client (they raise before any I/O).
        assert kw["draft_sha256"] != kw["old_draft_sha256"] and kw["scheduled_publish_at"] > kw["now"]
        ledger._validate_piece_fields({"draft": kw["draft"], "draft_sha256": kw["draft_sha256"],
                                       "scheduled_publish_at": kw["scheduled_publish_at"],
                                       "hold_until": kw["hold_until"], "validation": kw["validation"]})
        self.log.append(("promote", kw["draft"]["audioUrl"], kw["draft_sha256"]))
        self.promoted = kw
        return dict(self.piece, status=C.APPROVED, dry_run=False) if self.promote_ok else None


def _when():
    return (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()


def test_a_dry_run_writes_nothing(monkeypatch, capsys):
    piece, job = _piece_job()
    world = _World(monkeypatch, piece, job)
    assert P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when()]) == 0
    assert world.log == []
    assert "dry run — nothing written" in capsys.readouterr().out


def test_yes_trims_uploads_repoints_then_promotes_in_that_order(monkeypatch):
    piece, job = _piece_job()
    world = _World(monkeypatch, piece, job)
    assert P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"]) == 0
    kinds = [e if isinstance(e, str) else e[0] for e in world.log]
    assert kinds == ["download", "trim", "probe", "upload", "repoint", "promote", "notify"]
    new_path = world.log[3][1]
    assert world.log[3][2] == "audio/mp4" and new_path != OLD_PATH and narration._WEEKLY_AUDIO_RE.fullmatch(new_path)
    assert world.log[4] == ("repoint", OLD_PATH, new_path)
    assert world.log[5][1] == publisher.audio_public_url(new_path)
    sent = world.promoted["draft"]
    assert sent["author"] == C.AUTHOR and world.promoted["draft_sha256"] == narration.draft_sha256(sent)
    assert world.promoted["old_draft_sha256"] == piece["draft_sha256"]
    assert world.promoted["observed_updated_at"] == piece["updated_at"]
    assert world.promoted["validation"]["outcome"] == "accepted" and "promote" in world.promoted["validation"]


def test_a_lost_promote_after_the_repoint_exits_1_and_a_rerun_skips_straight_to_the_promote(monkeypatch):
    piece, job = _piece_job()
    world = _World(monkeypatch, piece, job, promote_ok=False)
    assert P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"]) == 1
    assert "notify" not in world.log
    trimmed = world.job["audio_path"]
    # Rerun: the job already points at the trimmed file — no download, trim or upload, no second repoint.
    world.log.clear()
    world.promote_ok = True
    assert P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"]) == 0
    assert [e if isinstance(e, str) else e[0] for e in world.log] == ["promote", "notify"]
    assert world.promoted["narration_job_id"] == job["id"]
    assert world.promoted["draft"]["audioUrl"] == publisher.audio_public_url(trimmed)


def test_a_trimmed_file_outside_the_window_or_the_voice_band_is_never_uploaded(monkeypatch):
    piece, job = _piece_job()
    world = _World(monkeypatch, piece, job)
    world.duration = job["timings"][-1]["start"] + 1.0          # the cut did not take: it still speaks
    with pytest.raises(SystemExit, match="outside"):
        P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"])
    assert not any(isinstance(e, tuple) and e[0] in ("upload", "repoint", "promote") for e in world.log)
    world = _World(monkeypatch, piece, job)
    world.size = 10                                              # far below the bytes-per-second band
    with pytest.raises(SystemExit, match="voice band"):
        P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"])
    assert not any(isinstance(e, tuple) and e[0] in ("upload", "repoint", "promote") for e in world.log)


def test_a_taken_title_or_missing_audio_refuses_before_anything(monkeypatch):
    piece, job = _piece_job()
    world = _World(monkeypatch, piece, job)
    monkeypatch.setattr(build, "_article_keys_sync", lambda: [{"title": piece["title"].upper(), "slug": "x"}])
    with pytest.raises(SystemExit, match="already has the title"):
        P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"])
    monkeypatch.setattr(build, "_article_keys_sync", lambda: [])
    world.bucket.clear()
    with pytest.raises(SystemExit, match="not in the bucket"):
        P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"])
    assert world.log == []


def test_yes_refuses_before_the_web_deploy_and_without_the_review_bot(monkeypatch):
    piece, job = _piece_job()
    world = _World(monkeypatch, piece, job)
    world.deploy = "the live Terms page still promises an \"AI-assisted\" label — deploy the web first"
    with pytest.raises(SystemExit, match="deploy the web first"):
        P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"])
    assert world.log == []
    assert P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes",
                   "--allow-before-deploy"]) == 0, "the owner's explicit override"
    world = _World(monkeypatch, piece, job)
    world.bot = False
    with pytest.raises(SystemExit, match="withdraw button"):
        P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"])
    assert world.log == []
    # The explicit override promotes, sends nothing and says so.
    assert P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes",
                   "--no-withdraw-button"]) == 0
    assert "notify" not in world.log and any(isinstance(e, tuple) and e[0] == "promote" for e in world.log)


def test_deploy_problem_reads_the_live_terms_and_fails_closed(monkeypatch):
    monkeypatch.setattr(P, "_live_terms_text", lambda: "help write some Learn articles (marked &ldquo;AI-assisted&rdquo;")
    assert "deploy the web first" in P.deploy_problem()
    monkeypatch.setattr(P, "_live_terms_text", lambda: "help write some Learn articles (each reviewed by Caydex")
    assert P.deploy_problem() is None

    def boom():
        raise OSError("dns")
    monkeypatch.setattr(P, "_live_terms_text", boom)
    assert "could not read the live Terms page" in P.deploy_problem()


def test_a_lost_withdraw_button_exits_2_and_resend_only_notifies(monkeypatch, capsys):
    piece, job = _piece_job()
    world = _World(monkeypatch, piece, job)
    world.notified = False
    assert P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"]) == 2
    assert "--resend-withdraw" in capsys.readouterr().out
    # The piece is now promoted; --resend-withdraw only sends the button.
    world.piece = dict(piece, status=C.APPROVED, dry_run=False, validation={"promote": {"by": "owner"}},
                       scheduled_publish_at=_when())
    world.log.clear()
    world.notified = True
    assert P.main(["--period", "2026-W44:deep_dive", "--resend-withdraw"]) == 0
    assert world.log == ["notify"]
    world.piece = dict(piece)                                   # never promoted → refused
    with pytest.raises(SystemExit, match="not promoted by this script"):
        P.main(["--period", "2026-W44:deep_dive", "--resend-withdraw"])


def test_an_object_already_in_the_bucket_is_reused_and_a_lagging_listing_stops_before_the_repoint(monkeypatch):
    piece, job = _piece_job()
    world = _World(monkeypatch, piece, job)
    world.repoint_ok = False                                    # first run: dies at the repoint
    assert P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"]) == 1
    uploaded = [e for e in world.log if isinstance(e, tuple) and e[0] == "upload"]
    assert len(uploaded) == 1 and not any(isinstance(e, tuple) and e[0] == "promote" for e in world.log)
    world.log.clear()
    world.repoint_ok = True                                     # rerun: same bytes → same path → reused
    assert P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"]) == 0
    assert not any(isinstance(e, tuple) and e[0] == "upload" for e in world.log)
    assert [e[0] for e in world.log if isinstance(e, tuple)] == ["download", "repoint", "promote"]

    piece2, job2 = _piece_job()
    lag = _World(monkeypatch, piece2, job2)
    lag.upload = lambda bucket, path, data, ct: lag.log.append(("upload", path, ct))   # never listed
    monkeypatch.setattr(P, "_upload", lag.upload)
    with pytest.raises(SystemExit, match="does not list it"):
        P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"])
    assert not any(isinstance(e, tuple) and e[0] in ("repoint", "promote") for e in lag.log)


def test_a_promote_marker_on_untrimmed_audio_is_refused(monkeypatch):
    piece, job = _piece_job()
    job["checks"] = {"promote": {"from_path": OLD_PATH}}        # marker, but still the original file
    _World(monkeypatch, piece, job)
    with pytest.raises(SystemExit, match="not a trimmed file"):
        P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when()])


def test_a_naive_publish_time_is_refused(monkeypatch):
    piece, job = _piece_job()
    _World(monkeypatch, piece, job)
    with pytest.raises(SystemExit, match="timezone offset"):
        P.main(["--period", "2026-W44:deep_dive", "--publish-at", "2026-11-02T18:00:00"])
