"""`scripts/learn_catalog_narrate.py` and `scripts/learn_catalog_fetch.py` — the owner's two commands for
rendering a hand-written Money Moves catalog article on the Railway narrator (owner 2026-11-01, "Yes,
build it"; migration 207).

The narrate script WRITES PRODUCTION, so its guards matter: nothing is written without `--yes`; a job of
the slug still open, a READY round of the same text, a weekly piece mid-build and the Deep Dive window
(Thursday 18:00 ET → Saturday 00:00 ET) all refuse; what it queues is the catalog voice and passes the
REAL ledger's validators. The fetch script writes the owner's local files, so its guards are about
correctness: the newest READY round, byte-exact audio (size + the sha its path names + ffprobe), a
transcript that still matches the audio, the catalog suite's pause / pace / encode bands, no weekly
`audioUrl`, and no overwrite of a different clip without `--force`.

Hermetic: Supabase, Storage and ffprobe are replaced through each script's I/O seams; files go to tmp_path.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import test_learn_weekly_ledger as ledger_fake
import test_learn_weekly_narration as nt
from app.services.learn_weekly import contracts as C
from app.services.learn_weekly import ledger, narration

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_TESTS = Path(__file__).resolve().parent
SLUG = "coca-cola-vs-pepsico"
UTC = timezone.utc
SAT = datetime(2026, 11, 7, 15, 0, tzinfo=UTC)               # Sat 10:00 ET (EST, after DST ended Nov 1)


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


N = _load("learn_catalog_narrate")
F = _load("learn_catalog_fetch")


def article(**over):
    a = copy.deepcopy(nt.ARTICLE)
    a["slug"] = SLUG
    for k in ("isFeatured", "hasAudioVersion", "audioUrl"):     # a hand-written draft carries none of these
        a.pop(k, None)
    a.update(over)
    return a


def _job_row(art=None, *, status=C.JOB_READY, round=1, spw=0.28, bytes_per_second=12_600, data=None):
    """A READY catalog job as the ledger would hold it after `narration.complete`, plus its object bytes."""
    art = art or article()
    blocks = narration.narration_blocks(art)
    t, end = nt._timings(blocks, spw=spw)
    norm = narration.validate_timings(blocks, t, duration_seconds=end)
    data = data if data is not None else bytes(range(256)) * (int(end * bytes_per_second) // 256 + 1)
    data = data[: int(end * bytes_per_second)] if len(data) > int(end * bytes_per_second) else data
    sha = hashlib.sha256(data).hexdigest()
    return {
        "id": f"00000000-0000-0000-0000-00000000000{round}", "status": status, "piece_id": None,
        "catalog_slug": SLUG, "round": round, "script": narration.script_json(blocks),
        "script_sha256": narration.script_sha256(blocks), "engine": "chatterbox",
        "audio_path": narration.catalog_audio_path(SLUG, sha), "audio_bytes": len(data),
        "duration_seconds": end, "timings": norm, "error": None,
    }, data


# ═════════════════════════════════════════════════════════════════════════════════════════════
# learn_catalog_narrate.py
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_load_article_reads_the_draft_then_the_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(N, "DRAFTS_DIR", tmp_path / "drafts")
    monkeypatch.setattr(N, "BUNDLE", tmp_path / "bundle.json")
    with pytest.raises(SystemExit, match="no article"):
        N.load_article(SLUG)
    (tmp_path / "bundle.json").write_text(json.dumps({"articles": [article(), article(slug="other")]}))
    got, src, from_bundle = N.load_article(SLUG)
    assert got["slug"] == SLUG and src == tmp_path / "bundle.json" and from_bundle is True
    (tmp_path / "drafts").mkdir()
    (tmp_path / "drafts" / f"{SLUG}.json").write_text(json.dumps(article(title="Draft")))
    got, src, from_bundle = N.load_article(SLUG)
    assert got["title"] == "Draft" and from_bundle is False
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps(article(slug="kodak")))
    with pytest.raises(SystemExit, match="not 'coca-cola-vs-pepsico'"):
        N.load_article(SLUG, wrong)
    junk = tmp_path / "junk.json"
    junk.write_text("{nope")
    with pytest.raises(SystemExit, match="unreadable JSON"):
        N.load_article(SLUG, junk)
    with pytest.raises(SystemExit, match="holds 0 articles"):
        N.load_article(SLUG, _bundle_without(tmp_path))


def _bundle_without(tmp_path):
    path = tmp_path / "other_bundle.json"
    path.write_text(json.dumps({"articles": [article(slug="kodak")]}))
    return path


def test_the_two_scripts_read_articles_by_the_same_rules(tmp_path, monkeypatch):
    for mod in (N, F):
        monkeypatch.setattr(mod, "DRAFTS_DIR", tmp_path / "none")
        monkeypatch.setattr(mod, "BUNDLE", tmp_path / "bundle.json")
    (tmp_path / "bundle.json").write_text(json.dumps({"articles": [article()]}))
    assert N.load_article(SLUG) == F.load_article(SLUG)
    with pytest.raises(SystemExit):
        F.load_article("kodak")


def test_the_plan_is_the_catalog_voice_and_the_weekly_script_shape():
    plan = N.plan_enqueue(article(), SLUG, jobs=[], busy_pieces=[], now=SAT)
    blocks = narration.narration_blocks(article())
    assert plan["round"] == 1 and plan["engine"] == C.ENGINE_CHATTERBOX
    assert narration.parse_voice_key(plan["voice"]) == narration.CHATTERBOX_VOICE
    assert plan["script"] == narration.script_json(blocks)
    assert plan["script_sha256"] == narration.script_sha256(blocks)
    assert plan["pipeline_version"] == narration.PIPELINE_VERSION
    assert plan["blocks"] == len(blocks) and plan["words"] > 0 and plan["render_minutes"] >= 0


def test_the_catalog_voice_is_chatterbox_whatever_the_weekly_engine_setting(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "LEARN_NARRATOR_ENGINE", "kokoro")
    assert N.plan_enqueue(article(), SLUG, jobs=[], busy_pieces=[], now=SAT)["engine"] == "chatterbox"


def test_the_next_round_follows_the_highest_one():
    jobs = [{"round": 1, "status": C.JOB_FAILED, "script_sha256": "a" * 64},
            {"round": 3, "status": C.JOB_READY, "script_sha256": "b" * 64},
            {"round": True, "status": C.JOB_FAILED}]
    assert N.plan_enqueue(article(), SLUG, jobs=jobs, busy_pieces=[], now=SAT)["round"] == 4


@pytest.mark.parametrize("status", [C.JOB_QUEUED, C.JOB_IN_PROGRESS])
def test_an_open_job_of_the_slug_refuses_even_with_force(status):
    jobs = [{"id": "j1", "round": 1, "status": status}]
    with pytest.raises(SystemExit, match=f"still {status}"):
        N.plan_enqueue(article(), SLUG, jobs=jobs, busy_pieces=[], now=SAT, force=True)


def test_a_ready_round_of_the_same_text_refuses_unless_forced():
    sha = narration.script_sha256(narration.narration_blocks(article()))
    jobs = [{"round": 2, "status": C.JOB_READY, "script_sha256": sha}]
    with pytest.raises(SystemExit, match="already READY for this exact text"):
        N.plan_enqueue(article(), SLUG, jobs=jobs, busy_pieces=[], now=SAT)
    assert N.plan_enqueue(article(), SLUG, jobs=jobs, busy_pieces=[], now=SAT, force=True)["round"] == 3


@pytest.mark.parametrize("status", [C.BUILDING, C.DRAFTED, C.MEDIA_PENDING])
def test_a_weekly_piece_mid_build_refuses_unless_forced(status):
    busy = [{"period_key": "2026-W45:deep_dive", "status": status}]
    with pytest.raises(SystemExit, match="being built or narrated"):
        N.plan_enqueue(article(), SLUG, jobs=[], busy_pieces=busy, now=SAT)
    N.plan_enqueue(article(), SLUG, jobs=[], busy_pieces=busy, now=SAT, force=True)


@pytest.mark.parametrize("when, inside", [
    (datetime(2026, 11, 5, 22, 59, tzinfo=UTC), False),       # Thu 17:59 EST
    (datetime(2026, 11, 5, 23, 0, tzinfo=UTC), True),         # Thu 18:00 EST
    (datetime(2026, 11, 6, 13, 0, tzinfo=UTC), True),         # Fri 08:00 EST — the build
    (datetime(2026, 11, 7, 4, 59, tzinfo=UTC), True),         # Fri 23:59 EST
    (datetime(2026, 11, 7, 5, 0, tzinfo=UTC), False),         # Sat 00:00 EST
    (datetime(2026, 10, 29, 22, 0, tzinfo=UTC), True),        # Thu 18:00 EDT (before DST ended)
    (datetime(2026, 10, 29, 21, 59, tzinfo=UTC), False),      # Thu 17:59 EDT
    (datetime(2026, 11, 4, 23, 30, tzinfo=UTC), False),       # Wednesday evening
])
def test_the_deep_dive_window_is_thursday_18_et_to_saturday(when, inside):
    assert N.in_deep_dive_window(when) is inside
    if inside:
        with pytest.raises(SystemExit, match="Deep Dive window"):
            N.plan_enqueue(article(), SLUG, jobs=[], busy_pieces=[], now=when)
        N.plan_enqueue(article(), SLUG, jobs=[], busy_pieces=[], now=when, force=True)
    else:
        N.plan_enqueue(article(), SLUG, jobs=[], busy_pieces=[], now=when)


def test_a_naive_clock_is_refused():
    with pytest.raises(ValueError):
        N.in_deep_dive_window(datetime(2026, 11, 7, 12, 0))


@pytest.mark.parametrize("mutate, why", [
    (lambda a: a["sections"][0]["content"][0].update(text=" ".join(["word"] * 76) + "."), "76 words"),
    (lambda a: a["sections"][0]["content"][0].update(text="   "), "no speakable text"),
    (lambda a: a.update(title=None, subtitle=None, sections=[]), "nothing to narrate"),
    (lambda a: a["sections"][0]["content"][0].update(text=42), "must be a string"),
    (lambda a: a.update(slug="kodak"), "not 'coca-cola-vs-pepsico'"),
])
def test_an_article_that_cannot_be_narrated_is_refused(mutate, why):
    a = article()
    mutate(a)
    with pytest.raises(SystemExit, match=why):
        N.plan_enqueue(a, SLUG, jobs=[], busy_pieces=[], now=SAT)


@pytest.mark.parametrize("slug", ["Coca-Cola", "coca_cola", "x" * 161, "coca-cola\n"])
def test_a_bad_slug_is_refused(slug):
    with pytest.raises(SystemExit):
        N.plan_enqueue(article(slug=slug), slug, jobs=[], busy_pieces=[], now=SAT)


def _narrate_seams(monkeypatch, tmp_path, *, jobs=(), busy=(), enqueue=None):
    drafts = tmp_path / "drafts"
    drafts.mkdir(exist_ok=True)
    (drafts / f"{SLUG}.json").write_text(json.dumps(article()))
    monkeypatch.setattr(N, "DRAFTS_DIR", drafts)
    monkeypatch.setattr(N, "_jobs", lambda slug: list(jobs))
    monkeypatch.setattr(N, "_busy_pieces", lambda: list(busy))
    calls = []
    monkeypatch.setattr(N, "_enqueue", enqueue or (lambda plan: calls.append(plan) or {"id": "j", "status": "queued"}))
    monkeypatch.setattr(N, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: SAT)}))
    return calls


def test_main_writes_nothing_without_yes(monkeypatch, tmp_path, capsys):
    calls = _narrate_seams(monkeypatch, tmp_path)
    assert N.main(["--slug", SLUG]) == 0
    out = capsys.readouterr().out
    assert calls == [] and "dry run — nothing written" in out and "round 1" in out


def test_main_with_yes_queues_through_the_real_ledger(monkeypatch, tmp_path, capsys):
    fake = ledger_fake.FakeSupabase()
    real = lambda plan: ledger.enqueue_catalog_job_sync(  # noqa: E731 — the REAL validators, unmocked
        plan["slug"], round=plan["round"], script=plan["script"], script_sha256=plan["script_sha256"],
        engine=plan["engine"], voice=plan["voice"], pipeline_version=plan["pipeline_version"], sb=fake)
    _narrate_seams(monkeypatch, tmp_path, enqueue=real)
    assert N.main(["--slug", SLUG, "--yes"]) == 0
    (row,) = fake.tables[ledger.JOBS].rows
    assert row["catalog_slug"] == SLUG and row["piece_id"] is None and row["round"] == 1
    assert row["engine"] == "chatterbox" and narration.parse_voice_key(row["voice"]) == narration.CHATTERBOX_VOICE
    assert "queued narration job" in capsys.readouterr().out
    # The worker can render what was queued: the claim's wire view is the article's narration.
    blocks = narration.blocks_from_script(row["script"], expected_sha256=row["script_sha256"])
    assert [b.text for b in blocks] == [b.text for b in narration.narration_blocks(article())]


def test_main_reports_a_lost_round(monkeypatch, tmp_path, capsys):
    _narrate_seams(monkeypatch, tmp_path, enqueue=lambda plan: None)
    assert N.main(["--slug", SLUG, "--yes"]) == 1
    assert "nothing queued" in capsys.readouterr().out


def test_main_names_migration_207_when_the_ledger_refuses(monkeypatch, tmp_path):
    """Before 207 the read of `catalog_slug` (or the upsert) fails: a legible exit, not a traceback."""
    def boom(*_a, **_k):
        raise ledger.LearnLedgerError("list_catalog_jobs failed: APIError: column catalog_slug does not exist")

    calls = _narrate_seams(monkeypatch, tmp_path)
    monkeypatch.setattr(N, "_jobs", boom)
    with pytest.raises(SystemExit, match="is migration 207 applied"):
        N.main(["--slug", SLUG])
    _narrate_seams(monkeypatch, tmp_path, enqueue=boom)
    with pytest.raises(SystemExit, match="is migration 207 applied"):
        N.main(["--slug", SLUG, "--yes"])
    assert calls == []


def test_main_refuses_in_the_window_before_any_write(monkeypatch, tmp_path):
    calls = _narrate_seams(monkeypatch, tmp_path)
    monkeypatch.setattr(N, "datetime", type("D", (), {"now": staticmethod(
        lambda tz=None: datetime(2026, 11, 6, 13, 0, tzinfo=UTC))}))
    with pytest.raises(SystemExit, match="Deep Dive window"):
        N.main(["--slug", SLUG, "--yes"])
    assert calls == []


# ═════════════════════════════════════════════════════════════════════════════════════════════
# learn_catalog_fetch.py
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_pick_job_takes_the_newest_ready_round_and_warns_about_later_ones():
    r1, _ = _job_row(round=1)
    r2 = {**r1, "round": 2, "status": C.JOB_READY, "id": "r2"}
    r3 = {**r1, "round": 3, "status": C.JOB_FAILED, "id": "r3", "error": "render_oom: killed"}
    weekly = {**r1, "round": 9, "piece_id": "p", "catalog_slug": None}
    job, warnings = F.pick_job([r1, r3, r2, weekly], SLUG)
    assert job["id"] == "r2" and warnings == ["round 3 is failed — fetching the newest READY round 2"]
    with pytest.raises(SystemExit, match=r"round 3: failed \(render_oom: killed\)"):
        F.pick_job([r3], SLUG)
    with pytest.raises(SystemExit, match="no job at all"):
        F.pick_job([], SLUG)


def test_the_plan_merges_the_catalog_shape_with_no_weekly_audio_url():
    job, _ = _job_row()
    plan = F.plan_fetch(article(), job, SLUG)
    merged = plan["article"]
    assert merged == narration.merge_timings(article(), narration.blocks_from_script(job["script"]), job["timings"],
                                             duration_seconds=job["duration_seconds"])
    assert merged["hasAudioVersion"] is True and merged["audioDurationSeconds"] == round(job["duration_seconds"])
    assert "audioUrl" not in merged
    assert set(merged) - set(article()) == {"audioDurationSeconds", "hasAudioVersion"}
    assert merged["sections"][0]["content"][0]["readAlong"][0]["text"].startswith("In its quarterly filing")
    items = merged["sections"][1]["content"][0]["itemsReadAlong"]
    assert len(items) == 3 and items[1] == [], "an empty bullet keeps its slot, as the catalog aligner writes"
    assert plan["path"] == job["audio_path"] and plan["bytes"] == job["audio_bytes"]


def test_a_bundle_article_keeps_its_null_audio_url_and_a_narrator_url_is_dropped():
    job, _ = _job_row()
    assert F.plan_fetch(article(audioUrl=None, hasAudioVersion=False), job, SLUG)["article"]["audioUrl"] is None
    stale = f"https://x.supabase.co/storage/v1/object/public/money-moves-media/{job['audio_path']}"
    assert F.plan_fetch(article(audioUrl=stale), job, SLUG)["article"]["audioUrl"] is None


def test_an_edited_article_is_refused_the_audio_would_not_match():
    job, _ = _job_row()
    edited = article()
    edited["sections"][0]["content"][2]["text"] = "A data centre is a power plant with racks attached."
    with pytest.raises(SystemExit, match="text changed after round 1"):
        F.plan_fetch(edited, job, SLUG)
    # Only spoken text counts: an edit to a non-narrated field is fine.
    F.plan_fetch(article(cardSubtitle="New card line."), job, SLUG)


@pytest.mark.parametrize("over, why", [
    ({"audio_path": f"audio/weekly/{SLUG}-{'a' * 12}.m4a"}, "not a catalog object"),
    ({"audio_path": f"audio/catalog/kodak-{'a' * 12}.m4a"}, "not a catalog object"),
    ({"status": C.JOB_FAILED}, "not a READY catalog job"),
    ({"piece_id": "p-1"}, "not a READY catalog job"),
    ({"catalog_slug": "kodak"}, "not a READY catalog job"),
    ({"audio_bytes": 0}, "audio_bytes"),
    ({"duration_seconds": None}, "duration_seconds"),
    ({"script_sha256": "f" * 64}, "does not hash"),
])
def test_a_job_that_is_not_this_articles_render_is_refused(over, why):
    job, _ = _job_row()
    job.update(over)
    with pytest.raises(SystemExit, match=why):
        F.plan_fetch(article(), job, SLUG)


def test_the_merge_reruns_the_servers_timing_gate():
    job, _ = _job_row()
    job["timings"][3]["sentences"][0]["end"] = job["timings"][3]["sentences"][0]["start"] + 0.01
    with pytest.raises(SystemExit, match="do not merge"):
        F.plan_fetch(article(), job, SLUG)


def _with_pause(job, gap):
    """Stretch the pause between the first paragraph's first two sentences to `gap` seconds."""
    t = copy.deepcopy(job["timings"])
    s0, s1 = t[3]["sentences"][0], t[3]["sentences"][1]
    shift = gap - (s1["start"] - s0["end"])
    for k, entry in enumerate(t):
        if k == 3:
            for s in entry["sentences"][1:]:
                s["start"] += shift
                s["end"] += shift
            entry["end"] += shift
        elif k > 3:
            entry["start"] += shift
            entry["end"] += shift
            for s in entry["sentences"]:
                s["start"] += shift
                s["end"] += shift
    return {**job, "timings": t, "duration_seconds": job["duration_seconds"] + shift,
            "audio_bytes": int((job["duration_seconds"] + shift) * 12_600)}


def test_an_in_block_pause_the_catalog_suite_would_refuse_is_refused():
    job, _ = _job_row(spw=0.25)                 # fast enough that the stretched file keeps the pace band
    F.plan_fetch(article(), _with_pause(job, 2.9), SLUG)
    with pytest.raises(SystemExit, match=r"3\.20 s pause"):
        F.plan_fetch(article(), _with_pause(job, 3.2), SLUG)      # the server's own gate allows up to 6 s


def test_read_along_problems_reads_the_rounded_spans():
    a = {"sections": [{"content": [{"type": "paragraph", "readAlong": [
        {"text": "One two three four.", "start": 0.0, "end": 0.5},          # 0.125 s/word: a dropped sentence
        {"text": "Five.", "start": 0.5, "end": 0.9}]}]}]}
    (problem,) = F.read_along_problems(a)
    assert "0.125 s/word" in problem


@pytest.mark.parametrize("bytes_per_second, spw, why", [
    (9_700, 0.28, "B/s outside"),            # the Gemini-path encode
    (16_000, 0.28, "B/s outside"),           # a 128k / stereo encode
    (12_600, 0.36, "WPM outside"),           # a dragged render (~123 WPM)
])
def test_the_catalog_voice_bands_are_checked_before_anything_is_written(bytes_per_second, spw, why):
    job, _ = _job_row(bytes_per_second=bytes_per_second, spw=spw)
    with pytest.raises(SystemExit, match=why):
        F.plan_fetch(article(), job, SLUG)


def test_the_fetch_bands_mirror_the_catalog_suite_and_the_narrators_gate():
    src = (_TESTS / "test_money_moves_catalog_parity.py").read_text(encoding="utf-8")
    bps = re.search(r"^_CLONE_BYTES_PER_SEC = \(([\d_]+), ([\d_]+)\)", src, re.M)
    wpm = re.search(r"^_CLONE_WPM = \(([\d.]+), ([\d.]+)\)", src, re.M)
    assert (int(bps.group(1)), int(bps.group(2))) == F.CLONE_BYTES_PER_SEC == (11_500, 14_000)
    assert (float(wpm.group(1)), float(wpm.group(2))) == F.CLONE_WPM == (144.0, 166.0)
    for path in (_SCRIPTS / "verify_learn_voice.py", _SCRIPTS.parent / "learn_narrator" / "verify.py"):
        text = path.read_text(encoding="utf-8")
        assert re.search(r"^WPM = \(144\.0, 166\.0\)", text, re.M), path
        assert re.search(r"^BYTES_PER_SEC = \(11_500, 14_000\)", text, re.M), path
    assert F.MAX_IN_BLOCK_PAUSE_SECONDS == narration.MAX_SILENCE_SECONDS / 2


def test_check_download_wants_the_registered_size_and_the_paths_sha():
    job, data = _job_row()
    plan = F.plan_fetch(article(), job, SLUG)
    F.check_download(data, plan)
    with pytest.raises(SystemExit, match="registered"):
        F.check_download(data[:-1], plan)
    with pytest.raises(SystemExit, match="hashes to"):
        F.check_download(bytes([data[0] ^ 1]) + data[1:], plan)
    F.check_duration(plan["duration"] + 0.4, plan)
    with pytest.raises(SystemExit, match="ffprobe reads"):
        F.check_duration(plan["duration"] + 0.6, plan)


def test_the_article_is_written_in_the_drafts_own_form(tmp_path, monkeypatch):
    a = article(title="Coca-Cola vs. PepsiCo — “quotes”")
    text = F.dump_article(a)
    assert text == json.dumps(a, indent=1, ensure_ascii=False) and not text.endswith("\n")
    assert "“quotes”" in text and json.loads(text) == a
    monkeypatch.setattr(F, "DRAFTS_DIR", tmp_path)
    assert F.article_out_path(tmp_path / "x.json", False, SLUG) == tmp_path / "x.json"
    assert F.article_out_path(tmp_path / "bundle.json", True, SLUG) == tmp_path / f"{SLUG}.json"
    (tmp_path / f"{SLUG}.json").write_text("{}")
    with pytest.raises(SystemExit, match="pass --article"):
        F.article_out_path(tmp_path / "bundle.json", True, SLUG)


def _fetch_seams(monkeypatch, tmp_path, job, data, *, probe=None):
    drafts, audio = tmp_path / "drafts", tmp_path / "audio"
    drafts.mkdir(exist_ok=True)
    draft = drafts / f"{SLUG}.json"
    draft.write_text(json.dumps(article(), indent=1, ensure_ascii=False))
    monkeypatch.setattr(F, "DRAFTS_DIR", drafts)
    monkeypatch.setattr(F, "AUDIO_DIR", audio)
    monkeypatch.setattr(F, "_jobs", lambda slug: [job])
    downloads = []
    monkeypatch.setattr(F, "_download", lambda path: downloads.append(path) or data)
    monkeypatch.setattr(F, "_probe_duration", probe or (lambda path: job["duration_seconds"] + 0.05))
    return draft, audio / f"{SLUG}.m4a", downloads


def test_fetch_main_dry_run_downloads_and_writes_nothing(monkeypatch, tmp_path, capsys):
    job, data = _job_row()
    draft, clip, downloads = _fetch_seams(monkeypatch, tmp_path, job, data)
    before = draft.read_text()
    assert F.main(["--slug", SLUG]) == 0
    assert downloads == [] and not clip.exists() and draft.read_text() == before
    assert "dry run — nothing downloaded or written" in capsys.readouterr().out


def test_fetch_main_writes_the_exact_bytes_and_the_merged_article(monkeypatch, tmp_path):
    job, data = _job_row()
    draft, clip, downloads = _fetch_seams(monkeypatch, tmp_path, job, data)
    assert F.main(["--slug", SLUG, "--yes"]) == 0
    assert downloads == [job["audio_path"]]
    assert clip.read_bytes() == data, "byte for byte — never re-encoded"
    written = draft.read_text(encoding="utf-8")
    merged = json.loads(written)
    assert written == F.dump_article(merged)
    assert merged == F.plan_fetch(article(), job, SLUG)["article"]
    assert merged["hasAudioVersion"] is True and "audioUrl" not in merged
    # A rerun finds the same bytes: nothing is rewritten, and the merged article merges again cleanly.
    mtime = clip.stat().st_mtime_ns
    assert F.main(["--slug", SLUG, "--yes"]) == 0
    assert clip.stat().st_mtime_ns == mtime and json.loads(draft.read_text()) == merged


def test_fetch_main_refuses_to_overwrite_a_different_clip(monkeypatch, tmp_path):
    job, data = _job_row()
    draft, clip, _ = _fetch_seams(monkeypatch, tmp_path, job, data)
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"an older local render")
    before = draft.read_text()
    with pytest.raises(SystemExit, match="DIFFERENT bytes"):
        F.main(["--slug", SLUG, "--yes"])
    assert clip.read_bytes() == b"an older local render" and draft.read_text() == before
    assert F.main(["--slug", SLUG, "--yes", "--force"]) == 0
    assert clip.read_bytes() == data


@pytest.mark.parametrize("probe, data_mut, why", [
    (lambda path: 1.0, None, "ffprobe reads"),
    (None, lambda d: d[:-10], "registered"),
])
def test_fetch_main_writes_nothing_when_the_download_fails_a_check(monkeypatch, tmp_path, probe, data_mut, why):
    job, data = _job_row()
    draft, clip, _ = _fetch_seams(monkeypatch, tmp_path, job, data_mut(data) if data_mut else data, probe=probe)
    before = draft.read_text()
    with pytest.raises(SystemExit, match=why):
        F.main(["--slug", SLUG, "--yes"])
    assert not clip.exists() and draft.read_text() == before


def test_fetch_main_with_no_ready_round(monkeypatch, tmp_path):
    job, data = _job_row(status=C.JOB_IN_PROGRESS)
    _fetch_seams(monkeypatch, tmp_path, job, data)
    with pytest.raises(SystemExit, match="round 1: in_progress"):
        F.main(["--slug", SLUG, "--yes"])


def test_fetch_main_names_migration_207_when_the_ledger_refuses(monkeypatch, tmp_path):
    job, data = _job_row()
    _fetch_seams(monkeypatch, tmp_path, job, data)

    def boom(slug):
        raise ledger.LearnLedgerError("list_catalog_jobs failed: APIError: 42703")

    monkeypatch.setattr(F, "_jobs", boom)
    with pytest.raises(SystemExit, match="is migration 207 applied"):
        F.main(["--slug", SLUG, "--yes"])


def test_fetch_main_refuses_a_bad_slug_before_any_read(monkeypatch, tmp_path):
    job, data = _job_row()
    _fetch_seams(monkeypatch, tmp_path, job, data)
    monkeypatch.setattr(F, "_jobs", lambda slug: pytest.fail("no read for a bad slug"))
    with pytest.raises(SystemExit, match="kebab"):
        F.main(["--slug", "Coca-Cola", "--yes"])


# ═════════════════════════════════════════════════════════════════════════════════════════════
# The two scripts end to end against the real ledger and narration service (fakes for I/O):
# narrate → claim → upload → complete → fetch.
# ═════════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_narrate_then_fetch_round_trips_through_the_real_server(monkeypatch, tmp_path):
    from app.config import settings
    import sys
    import types

    client = nt.FakeClient()
    monkeypatch.setattr(ledger, "get_supabase", lambda: client)
    monkeypatch.setattr(narration, "get_supabase", lambda: client)
    monkeypatch.setattr(settings, "SUPABASE_URL", nt.SUPABASE_URL)
    alerts = types.ModuleType("app.services.learn_weekly.alerts")
    lines = []

    async def notify(text, *, loud=True):
        lines.append(text)
        return True

    alerts.notify = notify
    monkeypatch.setitem(sys.modules, "app.services.learn_weekly.alerts", alerts)

    # 1. narrate (the script's REAL seams read and write the fake ledger)
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / f"{SLUG}.json").write_text(json.dumps(article(), indent=1, ensure_ascii=False))
    monkeypatch.setattr(N, "DRAFTS_DIR", drafts)
    monkeypatch.setattr(N, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: SAT)}))
    assert N.main(["--slug", SLUG, "--yes"]) == 0

    # 2. the worker: claim → upload → (PUT) → complete, on the real service
    wire = await narration.claim_for_worker(["chatterbox"], worker_version="t", now=SAT)
    claim = narration.WorkerClaim(wire["attempts"], wire["claim_nonce"])
    blocks = narration.narration_blocks(article())
    t, end = nt._timings(blocks, spw=0.28)
    data = bytes(range(256)) * (int(end * 12_600) // 256 + 1)
    data = data[: int(end * 12_600)]
    target = await narration.upload_target(wire["id"], claim, content_type="audio/mp4", size_bytes=len(data),
                                           sha256=hashlib.sha256(data).hexdigest())
    client.storage.put(target["path"], len(data))
    out = await narration.complete(wire["id"], claim, path=target["path"], size_bytes=len(data),
                                   duration_seconds=end, engine="chatterbox",
                                   pipeline_version=narration.PIPELINE_VERSION, timings=t, now=SAT)
    assert out["status"] == C.JOB_READY and len(lines) == 1 and "learn_catalog_fetch.py" in lines[0]

    # 3. fetch (the REAL job read; the download and ffprobe are stubbed)
    monkeypatch.setattr(F, "DRAFTS_DIR", drafts)
    monkeypatch.setattr(F, "AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr(F, "_download", lambda path: data)
    monkeypatch.setattr(F, "_probe_duration", lambda path: end)
    assert F.main(["--slug", SLUG, "--yes"]) == 0
    merged = json.loads((drafts / f"{SLUG}.json").read_text())
    assert (tmp_path / "audio" / f"{SLUG}.m4a").read_bytes() == data
    assert merged["sections"][0]["content"][0]["readAlong"] and merged["hasAudioVersion"] is True
    # The merged article narrates exactly the queued script (integrate.py may append it as is).
    (row,) = client.tables[ledger.JOBS].rows
    assert narration.script_sha256(narration.narration_blocks(merged)) == row["script_sha256"]
    # And queuing it again is refused: that exact text is already READY.
    with pytest.raises(SystemExit, match="already READY"):
        N.main(["--slug", SLUG, "--yes"])
