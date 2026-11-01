"""`scripts/learn_weekly_promote.py` — publishing the W44 rehearsal (owner decision 2026-11-01).

The script strips the pre-2026-11-01 "AI-assisted" label from a rehearsed piece's text AND its
narration (the closing callout is spoken), then promotes it through `ledger.promote_rehearsed_sync`.
Everything here is offline: the ledger, Storage, ffmpeg and Telegram are stubbed."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import inspect
import io
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.config import settings
from app.services import money_moves_art_style as style
from app.services.learn_weekly import article_schema as A
from app.services.learn_weekly import build, cover_art, ledger, narration, publisher
from app.services.learn_weekly import contracts as C

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "learn_weekly_promote.py"
LEGACY = C.LEGACY_DISCLOSURE_TEXTS[0]
NOW = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
OLD_PATH = "audio/weekly/semiconductor-giants-and-ai-infrastructure-2026-10-30-34a8d0184084.m4a"
SLUG = "semiconductor-giants-and-ai-infrastructure-2026-10-30"
SB_URL = "https://x.supabase.co"
HERO_URL = f"{SB_URL}/storage/v1/object/public/money-moves-images/articles/{SLUG}.hero.jpg"
CARD_URL = f"{SB_URL}/storage/v1/object/public/money-moves-images/articles/{SLUG}.card.jpg"
#: W44 was written under the old prompt, whose example tagLabel was this.
OLD_TAG = "FEATURE DEEP DIVE"


@pytest.fixture(autouse=True)
def _supabase_url(monkeypatch):
    monkeypatch.setattr(settings, "SUPABASE_URL", SB_URL)


def _load():
    spec = importlib.util.spec_from_file_location("learn_weekly_promote", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["learn_weekly_promote"] = mod
    spec.loader.exec_module(mod)
    return mod


P = _load()


def _draft(tag: Any = None) -> Dict[str, Any]:
    d = {
        "slug": SLUG,
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
    if tag is not None:
        d["tagLabel"] = tag
    return d


def _plates(color=(20, 40, 70)) -> Dict[str, bytes]:
    """Real JPEGs at the shipping sizes (what the catalog script writes)."""
    from PIL import Image

    out = {}
    for name, (w, h) in style.DERIVATIVES.items():
        buf = io.BytesIO()
        Image.new("RGB", (w, h), color).save(buf, "JPEG", quality=84)
        out[name] = buf.getvalue()
    return out


def _local(plates=None, manifest: Any = "auto") -> Dict[str, Any]:
    plates = _plates() if plates is None else plates
    if manifest == "auto":
        manifest = {"slug": SLUG, "subject": "two identical stacked memory chips", "treatment": "blueprint_cyanotype",
                    "art_model": "image-model-x", "art_prompt_sha1": "6c34500af0c4",
                    "masters": {n: {"sha256": hashlib.sha256(b).hexdigest()} for n, b in plates.items()}}
    return {"plates": plates, "manifest": manifest, "dir": "/art"}


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


def _piece_job(tag: Any = None, **piece_over):
    draft = _draft(tag)
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


class _ImageStore:
    """The PUBLIC image bucket as `cover_art` uses it (list / upload / remove); uploads go to the log."""

    def __init__(self, log: List[Any]) -> None:
        self.objects: Dict[str, bytes] = {}
        self.opts: Dict[str, Dict[str, Any]] = {}
        self.log = log
        self.storage = self

    def from_(self, bucket):
        assert bucket == "money-moves-images", bucket
        return self

    def list(self, prefix, opts):
        return [{"name": path.rpartition("/")[2], "metadata": {"size": len(data), "mimetype": "image/jpeg"}}
                for path, data in self.objects.items()
                if path.rpartition("/")[0] == prefix and opts.get("search", "") in path.rpartition("/")[2]]

    def upload(self, path, data, opts):
        self.log.append(("art", path))
        if path in self.objects and opts.get("upsert") == "false":
            raise type("Dup", (Exception,), {"status": 409})("exists")
        self.objects[path], self.opts[path] = bytes(data), dict(opts)
        return {"Key": path}

    def remove(self, paths):
        self.log.append(("art_remove", list(paths)))
        for path in paths:
            self.objects.pop(path, None)


class _World:
    """Stubs for every I/O seam; records the order of writes."""

    def __init__(self, monkeypatch, piece, job, *, promote_ok=True):
        self.piece, self.job, self.log = piece, job, []
        self.images = _ImageStore(self.log)
        self.local = _local()
        monkeypatch.setattr(cover_art, "_sb", lambda: self.images)
        monkeypatch.setattr(P, "_read_local_plates", lambda art_dir, slug: self.local)
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
    # The cover art first (idempotent; a failure stops before any ledger write), then the audio.
    assert kinds == ["art", "art", "download", "trim", "probe", "upload", "repoint", "promote", "notify"]
    new_path = world.log[5][1]
    assert world.log[5][2] == "audio/mp4" and new_path != OLD_PATH and narration._WEEKLY_AUDIO_RE.fullmatch(new_path)
    assert world.log[6] == ("repoint", OLD_PATH, new_path)
    assert world.log[7][1] == publisher.audio_public_url(new_path)
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


# ── cover art + tagLabel (owner 2026-11-01: "W44 now + every weekly piece") ──────────────────


def test_fixed_tag_label_replaces_only_a_header_repeating_or_missing_tag():
    assert P.fixed_tag_label(_draft(OLD_TAG)) == P.DEFAULT_TAG_LABEL == "CASE STUDY"
    assert P.fixed_tag_label(_draft("Feature deep-dive")) == "CASE STUDY"
    assert P.fixed_tag_label(_draft("FEATURED")) == "CASE STUDY"
    assert P.fixed_tag_label(_draft(None)) == "CASE STUDY"
    assert P.fixed_tag_label(_draft("   ")) == "CASE STUDY"
    assert P.fixed_tag_label(_draft("THE BOTTLENECK")) is None              # a good tag is kept
    assert P.fixed_tag_label(_draft(OLD_TAG), " head  to head ") == "HEAD TO HEAD"
    assert P.fixed_tag_label(_draft("HEAD TO HEAD"), "head to head") is None


@pytest.mark.parametrize("override, match", [
    ("DEEP DIVE", "header"), ("Featured story", "header"), ("", "1-4 words"),
    ("ONE TWO THREE FOUR FIVE", "1-4 words"), ("X" * 33, "32 characters"), ("AI ASSISTED", "AI-assisted"),
])
def test_fixed_tag_label_refuses_a_bad_override(override, match):
    with pytest.raises(SystemExit, match=match):
        P.fixed_tag_label(_draft(OLD_TAG), override)


def test_with_cover_merges_only_the_two_urls_and_the_tag_and_never_mutates():
    d = _draft(OLD_TAG)
    new = P.with_cover(d, art={"imageUrl": HERO_URL, "imageCardUrl": CARD_URL}, tag_label="CASE STUDY")
    assert new["imageUrl"] == HERO_URL and new["imageCardUrl"] == CARD_URL and new["tagLabel"] == "CASE STUDY"
    assert d.get("imageUrl") is None and d["tagLabel"] == OLD_TAG
    assert narration.narration_blocks(new) == narration.narration_blocks(d), "neither field is narrated"
    for bad in ({"imageUrl": HERO_URL}, {"imageUrl": HERO_URL, "imageCardUrl": "javascript:x"},
                {"imageUrl": HERO_URL, "imageCardUrl": CARD_URL, "extra": "x"}, "junk"):
        with pytest.raises(SystemExit, match="cover art"):
            P.with_cover(d, art=bad)


def test_promoted_draft_with_art_and_tag_publishes_the_image_and_still_matches_the_audio():
    piece, job = _piece_job(tag=OLD_TAG)
    art = {"imageUrl": HERO_URL, "imageCardUrl": CARD_URL}
    with pytest.raises(SystemExit, match="repeats the app"):
        P.promoted_draft(piece["draft"], job["script"], audio_url="u", duration_seconds=10)
    new = P.promoted_draft(piece["draft"], job["script"], audio_url=publisher.audio_public_url(OLD_PATH),
                           duration_seconds=700.0, art=art, tag_label="CASE STUDY")
    row, problem = publisher.build_article_row(dict(piece, draft=new, status=C.APPROVED, dry_run=False),
                                               dict(job, duration_seconds=700.0), now=NOW)
    assert problem is None
    assert row["image_url"] == HERO_URL and row["content"]["imageUrl"] == HERO_URL
    assert row["content"]["imageCardUrl"] == CARD_URL and row["content"]["tagLabel"] == "CASE STUDY"


def test_local_plate_problems_checks_sizes_format_and_the_manifest():
    assert P.local_plate_problems(_local(), SLUG) == []
    assert P.local_plate_problems(_local(manifest=None), SLUG) == []
    swapped = _local()
    swapped["plates"]["hero"] = _plates(color=(200, 10, 10))["hero"]          # not the recorded plate
    assert any("sha256 differs" in p for p in P.local_plate_problems(swapped, SLUG))
    other = _local()
    other["manifest"]["slug"] = "another-piece-2026-10-23"
    assert any("not " + repr(SLUG) in p for p in P.local_plate_problems(other, SLUG))
    small = _local(plates={"hero": _plates()["card"], "card": _plates()["card"]}, manifest=None)
    assert any("want 1206x678" in p for p in P.local_plate_problems(small, SLUG))
    truncated = _local(plates={"hero": _plates()["hero"][:500], "card": _plates()["card"]}, manifest=None)
    assert any("not a readable image" in p for p in P.local_plate_problems(truncated, SLUG))


def test_read_local_plates_reads_the_pair_and_refuses_half_a_picture(tmp_path):
    assert P._read_local_plates(tmp_path, SLUG) is None
    plates = _plates()
    (tmp_path / f"{SLUG}.hero.jpg").write_bytes(plates["hero"])
    with pytest.raises(SystemExit, match="only the hero plate"):
        P._read_local_plates(tmp_path, SLUG)
    (tmp_path / f"{SLUG}.card.jpg").write_bytes(plates["card"])
    (tmp_path / f"{SLUG}.manifest.json").write_text(json.dumps({"slug": SLUG}))
    got = P._read_local_plates(tmp_path, SLUG)
    assert got["plates"] == plates and got["manifest"] == {"slug": SLUG} and got["dir"] == str(tmp_path)
    (tmp_path / f"{SLUG}.manifest.json").write_text("{not json")
    with pytest.raises(SystemExit, match="unreadable"):
        P._read_local_plates(tmp_path, SLUG)
    assert P.DEFAULT_ART_DIR == BACKEND / "data" / "learn_weekly_art"


def test_plan_art_order():
    both = {"hero": {"size": 1}, "card": {"size": 1}}
    assert P.plan_art(local=_local(), bucket=both, draw=True, no_art=False)["source"] == "local"
    assert P.plan_art(local=None, bucket=both, draw=True, no_art=False)["source"] == "bucket"
    assert P.plan_art(local=None, bucket={"hero": {"size": 1}, "card": None}, draw=True,
                      no_art=False)["source"] == "draw"
    assert P.plan_art(local=None, bucket={}, draw=False, no_art=False)["source"] == "missing"
    assert P.plan_art(local=_local(), bucket=both, draw=False, no_art=True)["source"] == "none"
    with pytest.raises(SystemExit, match="contradict"):
        P.plan_art(local=None, bucket={}, draw=True, no_art=True)


def test_yes_publishes_w44_with_its_cover_art_and_a_tag_that_does_not_repeat_the_header(monkeypatch):
    piece, job = _piece_job(tag=OLD_TAG)
    world = _World(monkeypatch, piece, job)
    assert P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"]) == 0
    hero, card = f"articles/{SLUG}.hero.jpg", f"articles/{SLUG}.card.jpg"
    assert world.images.objects == {hero: world.local["plates"]["hero"], card: world.local["plates"]["card"]}
    for path in (hero, card):
        assert world.images.opts[path]["content-type"] == "image/jpeg" and world.images.opts[path]["upsert"] == "false"
    sent = world.promoted["draft"]
    assert sent["imageUrl"] == HERO_URL and sent["imageCardUrl"] == CARD_URL and sent["tagLabel"] == "CASE STUDY"
    assert world.promoted["draft_sha256"] == narration.draft_sha256(sent), "the sha covers the art and the tag"
    v = world.promoted["validation"]
    assert v["cover_art"]["status"] == "ok" and v["cover_art"]["imageUrl"] == HERO_URL
    assert v["cover_art"]["subject"] == "two identical stacked memory chips"
    assert v["promote"]["cover_art"] == "local"
    assert v["promote"]["tag_label"] == {"from": OLD_TAG, "to": "CASE STUDY"}
    row, problem = publisher.build_article_row(dict(piece, draft=sent, status=C.APPROVED, dry_run=False),
                                               world.job, now=NOW)
    assert problem is None and row["image_url"] == HERO_URL and row["content"]["tagLabel"] == "CASE STUDY"


def test_a_tag_label_override_is_published(monkeypatch):
    piece, job = _piece_job(tag=OLD_TAG)
    world = _World(monkeypatch, piece, job)
    assert P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes",
                   "--tag-label", "head to head"]) == 0
    assert world.promoted["draft"]["tagLabel"] == "HEAD TO HEAD"


def test_without_any_art_source_yes_refuses_before_any_write_and_no_art_publishes_artless(monkeypatch, capsys):
    piece, job = _piece_job(tag=OLD_TAG)
    world = _World(monkeypatch, piece, job)
    world.local = None                                         # no plates on disk, none in the bucket
    assert P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when()]) == 0
    assert "--yes refuses" in capsys.readouterr().out and world.log == []
    with pytest.raises(SystemExit, match="no cover art"):
        P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"])
    assert world.log == []
    assert P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes", "--no-art"]) == 0
    assert world.images.objects == {} and "imageUrl" not in world.promoted["draft"]
    assert world.promoted["draft"]["tagLabel"] == "CASE STUDY", "the tag is fixed even without art"
    assert world.promoted["validation"]["cover_art"]["status"] == "skipped"


def test_a_pair_already_in_the_bucket_is_reused_with_no_upload(monkeypatch):
    piece, job = _piece_job(tag=OLD_TAG)
    world = _World(monkeypatch, piece, job)
    world.local = None
    plates = _plates()
    world.images.objects = {f"articles/{SLUG}.{n}.jpg": b for n, b in plates.items()}
    assert P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"]) == 0
    assert not any(isinstance(e, tuple) and e[0] == "art" for e in world.log)
    assert world.promoted["draft"]["imageUrl"] == HERO_URL
    assert world.promoted["validation"]["promote"]["cover_art"] == "bucket"


def test_draw_art_is_the_paid_fallback(monkeypatch):
    piece, job = _piece_job(tag=OLD_TAG)
    world = _World(monkeypatch, piece, job)
    world.local = None
    calls = []

    def draw(p, d):
        calls.append(d["slug"])
        return {"imageUrl": HERO_URL, "imageCardUrl": CARD_URL,
                "coverArt": {"status": "ok", "reused": False, "subject": "drawn"}}
    monkeypatch.setattr(P, "_draw_art", draw)
    with pytest.raises(SystemExit, match="no cover art"):          # never drawn without the flag
        P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"])
    assert calls == []
    assert P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes", "--draw-art"]) == 0
    assert calls == [SLUG] and world.promoted["draft"]["imageUrl"] == HERO_URL
    assert world.promoted["validation"]["cover_art"]["subject"] == "drawn"


def test_refused_plates_or_another_picture_in_the_bucket_stop_before_any_ledger_write(monkeypatch):
    piece, job = _piece_job(tag=OLD_TAG)
    world = _World(monkeypatch, piece, job)
    world.local["plates"]["card"] = _plates(color=(9, 9, 9))["card"]        # not the manifest's plate
    with pytest.raises(SystemExit, match="refused"):
        P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when()])
    assert world.log == []
    world = _World(monkeypatch, piece, job)
    world.images.objects = {f"articles/{SLUG}.hero.jpg": b"x" * 10, f"articles/{SLUG}.card.jpg": b"y" * 10}
    with pytest.raises(SystemExit, match="another picture"):
        P.main(["--period", "2026-W44:deep_dive", "--publish-at", _when(), "--yes"])
    assert not any(isinstance(e, tuple) and e[0] in ("download", "upload", "repoint", "promote") for e in world.log)


# ── --backfill-art: a piece that is already live ────────────────────────────────────────────


_REAL_TRANSITION = ledger.transition_sync


class _Live:
    """A promoted (approved / published) W44 with the art seams, the live row and the ledger write stubbed."""

    def __init__(self, monkeypatch, status, *, tag=OLD_TAG):
        base = _draft(tag)
        base["sections"][-1]["content"].pop()                   # the promoted text: no closing
        base["author"] = dict(C.AUTHOR)
        self.piece = {"id": str(uuid.uuid4()), "period_key": "2026-W44:deep_dive", "kind": "deep_dive",
                      "status": status, "dry_run": False, "draft": base,
                      "draft_sha256": narration.draft_sha256(base), "review_round": 0, "slug": SLUG,
                      "title": base["title"], "category": "battles",
                      "validation": {"outcome": "accepted", "promote": {"by": "owner"}}}
        self.log: List[Any] = []
        self.images = _ImageStore(self.log)
        self.local = _local()
        published = dict(base, isFeatured=True, sortOrder=-202644, publishedAt="2026-11-02T23:00:00+00:00")
        self.row = {"id": publisher.article_id(SLUG), "slug": SLUG, "source": "weekly", "image_url": None,
                    "content": published, "updated_at": "2026-11-02T23:00:00.123456+00:00"}
        self.row_ok = self.transition_ok = True
        self.transitions: List[Dict[str, Any]] = []
        monkeypatch.setattr(ledger, "get_piece_sync", lambda key: self.piece)
        monkeypatch.setattr(ledger, "transition_sync", self.transition)
        monkeypatch.setattr(cover_art, "_sb", lambda: self.images)
        monkeypatch.setattr(P, "_read_local_plates", lambda art_dir, slug: self.local)
        monkeypatch.setattr(P, "_read_live_article_sync", lambda slug: self.row)
        monkeypatch.setattr(P, "_update_live_article_sync", self.update_row)

        def never(*a, **kw):
            raise AssertionError("the backfill never trims audio, repoints or promotes")
        for name in ("_download", "_upload", "_run"):
            monkeypatch.setattr(P, name, never)
        monkeypatch.setattr(ledger, "promote_rehearsed_sync", never)
        monkeypatch.setattr(ledger, "repoint_ready_audio_sync", never)

    def update_row(self, slug, *, image_url, content, observed_updated_at, now):
        self.log.append(("row", slug, observed_updated_at))
        if not self.row_ok:
            return None
        self.row = dict(self.row, image_url=image_url, content=dict(content), updated_at=now.isoformat())
        return self.row

    def transition(self, piece_id, **kw):
        inspect.signature(_REAL_TRANSITION).bind(piece_id, **kw)
        # The real argument checks, run with no client (they raise before any I/O).
        assert ledger.transition_problem(kw["from_statuses"], kw["to_status"]) is None
        ledger._validate_piece_fields(kw["fields"])
        ledger._validate_fence(kw["fence"], ledger.PIECE_FENCEABLE, ledger.PIECES)
        self.log.append(("piece", kw["to_status"]))
        self.transitions.append(kw)
        if not self.transition_ok:
            return None
        self.piece = dict(self.piece, draft=kw["fields"]["draft"], draft_sha256=kw["fields"]["draft_sha256"],
                          validation=kw["fields"]["validation"])
        return self.piece


def test_backfill_a_published_w44_updates_the_live_row_then_the_piece(monkeypatch, capsys):
    live = _Live(monkeypatch, C.PUBLISHED)
    before = copy.deepcopy(live.row)
    old_draft, old_sha = copy.deepcopy(live.piece["draft"]), live.piece["draft_sha256"]
    assert P.main(["--period", "2026-W44:deep_dive", "--backfill-art"]) == 0      # dry run
    assert live.log == [] and "dry run" in capsys.readouterr().out
    assert P.main(["--period", "2026-W44:deep_dive", "--backfill-art", "--yes"]) == 0
    assert [e[0] for e in live.log] == ["art", "art", "row", "piece"]
    assert live.log[2][2] == before["updated_at"], "the row write is fenced on the updated_at read before"
    row = live.row
    assert row["image_url"] == HERO_URL and row["content"]["imageUrl"] == HERO_URL
    assert row["content"]["imageCardUrl"] == CARD_URL and row["content"]["tagLabel"] == "CASE STUDY"
    untouched = {k: v for k, v in row["content"].items() if k not in ("imageUrl", "imageCardUrl", "tagLabel")}
    assert untouched == {k: v for k, v in before["content"].items() if k != "tagLabel"}, \
        "the rest of the published content stays byte for byte"
    t = live.transitions[0]
    assert t["from_statuses"] == [C.PUBLISHED] and t["to_status"] == C.PUBLISHED
    assert t["fence"] == {"draft_sha256": old_sha, "review_round": 0, "dry_run": False}
    new = t["fields"]["draft"]
    assert new["imageUrl"] == HERO_URL and new["tagLabel"] == "CASE STUDY"
    assert t["fields"]["draft_sha256"] == narration.draft_sha256(new)
    assert narration.narration_blocks(new) == narration.narration_blocks(old_draft), "the audio still matches"
    assert t["fields"]["validation"]["cover_art"]["imageUrl"] == HERO_URL
    assert t["fields"]["validation"]["backfill_art"]["tag_label"] == {"from": OLD_TAG, "to": "CASE STUDY"}
    # A rerun finds nothing to do and writes nothing.
    live.log.clear()
    assert P.main(["--period", "2026-W44:deep_dive", "--backfill-art", "--yes"]) == 0
    assert live.log == []


def test_backfill_an_approved_piece_updates_only_its_draft_fenced_on_the_old_sha(monkeypatch):
    live = _Live(monkeypatch, C.APPROVED)
    old_sha = live.piece["draft_sha256"]
    assert P.main(["--period", "2026-W44:deep_dive", "--backfill-art", "--yes"]) == 0
    assert [e[0] for e in live.log] == ["art", "art", "piece"]
    t = live.transitions[0]
    assert t["from_statuses"] == [C.APPROVED] and t["to_status"] == C.APPROVED
    assert t["fence"] == {"draft_sha256": old_sha, "review_round": 0, "dry_run": False}
    assert live.piece["draft"]["imageCardUrl"] == CARD_URL


def test_backfill_refuses_a_rehearsal_or_a_dry_run_and_waits_out_publishing(monkeypatch):
    live = _Live(monkeypatch, C.REHEARSED)
    with pytest.raises(SystemExit, match="through the promote itself"):
        P.main(["--period", "2026-W44:deep_dive", "--backfill-art", "--yes"])
    live.piece = dict(live.piece, status=C.APPROVED, dry_run=True)
    with pytest.raises(SystemExit, match="dry_run=True"):
        P.main(["--period", "2026-W44:deep_dive", "--backfill-art", "--yes"])
    live.piece = dict(live.piece, status=C.PUBLISHING, dry_run=False)
    assert P.main(["--period", "2026-W44:deep_dive", "--backfill-art", "--yes"]) == 1
    assert live.log == []


def test_backfill_a_published_piece_without_its_own_live_row_or_after_a_lost_write(monkeypatch):
    live = _Live(monkeypatch, C.PUBLISHED)
    live.row = dict(live.row, source="bundle")
    with pytest.raises(SystemExit, match="no weekly row of its own"):
        P.main(["--period", "2026-W44:deep_dive", "--backfill-art", "--yes"])
    assert live.log == []
    live = _Live(monkeypatch, C.PUBLISHED)
    live.row = dict(live.row, content=None)                      # never overwrite unreadable content
    with pytest.raises(SystemExit, match="unreadable"):
        P.main(["--period", "2026-W44:deep_dive", "--backfill-art", "--yes"])
    assert live.log == []
    live = _Live(monkeypatch, C.PUBLISHED)
    live.row_ok = False                                          # someone changed the row in between
    assert P.main(["--period", "2026-W44:deep_dive", "--backfill-art", "--yes"]) == 1
    assert [e[0] for e in live.log] == ["art", "art", "row"], "the piece is not written after a lost row write"


def test_backfill_with_no_art_source_refuses_yes(monkeypatch):
    live = _Live(monkeypatch, C.PUBLISHED)
    live.local = None
    assert P.main(["--period", "2026-W44:deep_dive", "--backfill-art"]) == 0          # dry run: says so
    with pytest.raises(SystemExit, match="no cover art"):
        P.main(["--period", "2026-W44:deep_dive", "--backfill-art", "--yes"])
    assert live.log == []
