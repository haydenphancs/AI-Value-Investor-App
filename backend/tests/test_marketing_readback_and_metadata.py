"""
Phase 3 prerequisites on the web side (rules marketing.md §2, SYSTEM_DESIGN_GUIDELINES §12.2):

* `GET /runs/{id}/assets` read-back — a resumed or re-claimed stage re-derives its media from
  the server's READY rows; the narration pointer (`metadata.voice_asset_id`) is returned only if
  it names a ready `audio` asset of the same run;
* metadata caps — the least-trusted process writes two JSONB columns; both are bounded;
* the audio timing table — shape validated at the schema, and the timed words must be EXACTLY
  the accepted script's hook + lines (the first server-side check of what the video says).

Category 1 (pure) + the in-memory PostgREST fake from test_marketing_run_service.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas import marketing as sch
from app.services.marketing import run_service as mrs
from test_marketing_run_service import FakeSupabase

NONCE = "abcdef0123456789abcdef0123456789"
SHA = "b" * 64
NOW = datetime(2026, 9, 17, 21, 0, tzinfo=timezone.utc)


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(mrs.settings, "MARKETING_RUN_STALE_SECONDS", 3600)
    monkeypatch.setattr(mrs.settings, "MARKETING_MAX_RUN_ATTEMPTS", 6)
    service = mrs.MarketingRunService(supabase=FakeSupabase())
    service.fake = service.sb  # type: ignore[attr-defined]
    return service


async def _claim(svc) -> dict:
    row, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW,
                                      claim_nonce=NONCE)
    assert reason == mrs.CLAIMED
    return row


def _holder(row) -> mrs.CallerClaim:
    return mrs.CallerClaim(int(row["attempts"]), row["metadata"]["claim_nonce"])


async def _accept(svc, run_id: str, hook: str, lines) -> None:
    await svc.insert_script({"run_id": run_id, "status": "accepted", "source_ref": "journey:x",
                             "template_id": "checklist", "generation_id": "g",
                             "output": {"hook": hook, "video_script": list(lines), "posts": {}}})


def _words(text: str, step: float = 0.3):
    out, t = [], 0.0
    for w in text.split():
        out.append({"w": w, "s": round(t, 3), "e": round(t + step - 0.05, 3)})
        t += step
    return out


# ── the claim header value ───────────────────────────────────────────────────


@pytest.mark.parametrize("raw, ok", [
    (f"1.{NONCE}", True), (f"6.{NONCE.upper()}", True), (f"12.{'a' * 16}", True),
    (None, False), ("", False), (NONCE, False), (f"0.{NONCE}", False), (f"-1.{NONCE}", False),
    (f"1.{'a' * 15}", False), (f"1.{'a' * 65}", False), (f"1.{'g' * 32}", False),
    (f"1234567.{NONCE}", False), (f"1 .{NONCE}", False),
])
def test_the_claim_header_parses_strictly(raw, ok):
    if ok:
        c = mrs.CallerClaim.parse(raw)
        assert c.header() == raw.lower() or c.header() == f"{c.attempts}.{c.nonce}"
    else:
        with pytest.raises(ValueError):
            mrs.CallerClaim.parse(raw)


def test_claim_problem_needs_both_attempts_and_nonce():
    run = {"attempts": 2, "metadata": {"claim_nonce": NONCE}}
    assert mrs.claim_problem(run, mrs.CallerClaim(2, NONCE)) is None
    assert mrs.claim_problem(run, mrs.CallerClaim(1, NONCE))
    assert mrs.claim_problem(run, mrs.CallerClaim(2, "f" * 32))
    assert mrs.claim_problem({"attempts": 2, "metadata": {}}, mrs.CallerClaim(2, NONCE))
    assert mrs.claim_problem({"attempts": 2, "metadata": "junk"}, mrs.CallerClaim(2, NONCE))


# ── read-back ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_back_lists_only_ready_assets_with_public_urls(svc, monkeypatch):
    monkeypatch.setattr(mrs.settings, "SUPABASE_URL", "https://proj.supabase.co/")
    run = await _claim(svc)
    a, _ = await svc.register_asset(run["id"], kind="audio", ext="m4a", sha256=SHA, size_bytes=1,
                                    claim=_holder(run))
    b, _ = await svc.register_asset(run["id"], kind="card", ext="png", sha256="c" * 64, size_bytes=1,
                                    claim=_holder(run))
    svc.fake.objects.add(a["storage_path"])
    await svc.complete_asset(a["id"], claim=_holder(run))
    voice, rows = await svc.list_ready_assets(run["id"], claim=_holder(run))
    assert voice is None and [r["id"] for r in rows] == [a["id"]]      # b never landed
    assert rows[0]["public_url"] == ("https://proj.supabase.co/storage/v1/object/public/"
                                     f"{mrs.settings.MARKETING_MEDIA_BUCKET}/{a['storage_path']}")


@pytest.mark.asyncio
async def test_the_read_back_carries_the_runs_lane(svc, monkeypatch):
    """Migration 210: the read-back echoes the run's lane (main for a row with no lane column) — what a resumed stage
    may check its own lane against — and a spotlight run's ready objects live under `<run_date>/spotlight/`."""
    run = await _claim(svc)
    assert (await svc.read_back(run["id"], claim=_holder(run)))["lane"] == "main"
    # the insider spotlight listed: that Thursday the main lane's chain holds Money Map, so with the 2a series alone the
    # spotlight would hold no series and not be scheduled (C0, 2026-11-16)
    for name, value in (("MARKETING_SPOTLIGHT_ENABLED", True), ("MARKETING_SPOTLIGHT_WEEKDAYS", "thu"),
                        ("MARKETING_CONTENT_CLASSES", "A,C,F"), ("MARKETING_SPOTLIGHT_HOUR_ET", 10),
                        ("MARKETING_RUN_HOUR_ET", 16),
                        ("MARKETING_NEWS_SERIES", "ceo_buys,insider_buys,thirteen_f,money_map,insider_spotlight")):
        monkeypatch.setattr(mrs.settings, name, value)
    spot, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW,
                                       claim_nonce="0" * 31 + "1", lane="spotlight")
    assert reason == mrs.CLAIMED
    a, _ = await svc.register_asset(spot["id"], kind="audio", ext="m4a", sha256=SHA, size_bytes=1,
                                    claim=_holder(spot))
    svc.fake.objects.add(a["storage_path"])
    await svc.complete_asset(a["id"], claim=_holder(spot))
    back = await svc.read_back(spot["id"], claim=_holder(spot))
    assert back["lane"] == "spotlight" and [r["storage_path"] for r in back["assets"]] == [
        f"2026-09-17/spotlight/audio-{SHA[:16]}.m4a"]
    assert sch.RunAssetsResponse().lane == "main"                           # an older service: the main lane
    assert sch.RunAssetsResponse.model_validate({"lane": "spotlight"}).lane == "spotlight"


@pytest.mark.asyncio
async def test_the_voice_pointer_is_returned_only_when_it_verifies(svc, caplog):
    run = await _claim(svc)
    claim = _holder(run)
    audio, _ = await svc.register_asset(run["id"], kind="audio", ext="m4a", sha256=SHA, size_bytes=1, claim=claim)
    card, _ = await svc.register_asset(run["id"], kind="card", ext="png", sha256="c" * 64, size_bytes=1, claim=claim)
    pending, _ = await svc.register_asset(run["id"], kind="audio", ext="mp3", sha256="d" * 64, size_bytes=1, claim=claim)
    for x in (audio, card):
        svc.fake.objects.add(x["storage_path"])
        await svc.complete_asset(x["id"], claim=claim)
    for pointer, expect in ((audio["id"], audio["id"]), (card["id"], None), (pending["id"], None),
                            ("not-an-asset", None)):
        await svc.update_run(run["id"], metadata={"voice_asset_id": pointer}, worker=True, claim=claim)
        voice, _rows = await svc.list_ready_assets(run["id"], claim=claim)
        assert voice == expect, pointer
    assert any("voice_asset_id" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_the_image_pointer_rides_the_workers_patch_and_is_returned_only_when_it_verifies(svc, caplog):
    """Drop 1: the worker names its post image in the `rendered` checkpoint PATCH
    (`metadata.image_asset_id` — worker-writable, not server-owned); the read-back returns it only
    when it names a READY `card` of this run whose image_role is the post image."""
    assert "image_asset_id" not in sch.SERVER_OWNED_RUN_METADATA
    run = await _claim(svc)
    claim = _holder(run)
    footer = "Not investment advice"      # news-v23: the lesson picture's short footer
    await svc.insert_script({"run_id": run["id"], "status": "accepted", "source_ref": "journey:x",
                             "template_id": "checklist", "generation_id": "g",
                             "output": {"hook": "h", "video_script": [], "posts": {},
                                        "post_formats": {"bluesky": "image"},
                                        "image_post": {"title": "T", "paragraphs": ["P1", "P2"]},
                                        "image_footer": footer}})
    image, _ = await svc.register_asset(run["id"], kind="card", ext="jpg", sha256=SHA, size_bytes=1, claim=claim,
                                        metadata={"image_role": "post_image",
                                                  "onscreen_text": ["T", "P1", "P2", footer]})
    plain, _ = await svc.register_asset(run["id"], kind="card", ext="png", sha256="c" * 64, size_bytes=1,
                                        claim=claim)
    pending, _ = await svc.register_asset(run["id"], kind="card", ext="jpg", sha256="d" * 64, size_bytes=1,
                                          claim=claim, metadata={"image_role": "post_image",
                                                                 "onscreen_text": [footer]})
    for x in (image, plain):
        svc.fake.objects.add(x["storage_path"])
        await svc.complete_asset(x["id"], claim=claim)
    for pointer, expect in ((image["id"], image["id"]), (plain["id"], None), (pending["id"], None),
                            ("not-an-asset", None)):
        await svc.update_run(run["id"], metadata={"image_asset_id": pointer}, worker=True, claim=claim)
        back = await svc.read_back(run["id"], claim=claim)
        assert back["image_asset_id"] == expect, pointer
        assert back["voice_asset_id"] is None and back["video_asset_id"] is None
    assert any("image_asset_id" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_read_back_is_for_the_holder_only(svc):
    run = await _claim(svc)
    with pytest.raises(mrs.MarketingRunNotHeld):
        await svc.list_ready_assets(run["id"], claim=mrs.CallerClaim(1, "f" * 32))


@pytest.mark.asyncio
async def test_completing_an_asset_is_for_the_holder_of_its_run_only(svc):
    run = await _claim(svc)
    a, _ = await svc.register_asset(run["id"], kind="audio", ext="m4a", sha256=SHA, size_bytes=1,
                                    claim=_holder(run))
    svc.fake.objects.add(a["storage_path"])
    with pytest.raises(mrs.MarketingRunNotHeld):
        await svc.complete_asset(a["id"], claim=mrs.CallerClaim(1, "f" * 32))
    assert svc.fake.tables[mrs.ASSETS].rows[0]["status"] == "pending_upload"


# ── metadata caps ────────────────────────────────────────────────────────────


def test_metadata_is_capped_on_both_worker_writes():
    big = {"x": "y" * (sch.METADATA_MAX_BYTES + 1)}
    with pytest.raises(ValidationError):
        sch.RunUpdateRequest(metadata=big)
    with pytest.raises(ValidationError):
        sch.AssetRegisterRequest(kind="manifest", ext="json", sha256=SHA, bytes=1, metadata=big)
    ok = {"x": "y" * 1000}
    assert sch.RunUpdateRequest(metadata=ok).metadata == ok
    with pytest.raises(ValidationError):
        sch.RunUpdateRequest(metadata={"n": float("nan")})


# ── the audio timing table ───────────────────────────────────────────────────


def _audio(words, duration=10.0):
    return sch.AssetRegisterRequest(kind="audio", ext="m4a", sha256=SHA, bytes=1,
                                    duration_seconds=duration, metadata={"words": words})


def test_a_well_formed_timing_table_is_accepted():
    assert _audio(_words("Every day he names a price.")).metadata["words"][0]["w"] == "Every"


@pytest.mark.parametrize("words", [
    [],
    "not a list",
    [{"w": "a", "s": 0.0}],                                   # no end
    [{"w": "", "s": 0.0, "e": 0.2}],                          # empty word
    [{"w": "a" * 49, "s": 0.0, "e": 0.2}],                    # over-long word
    [{"w": "a", "s": 0.5, "e": 0.5}],                         # zero length
    [{"w": "a", "s": -0.1, "e": 0.2}],                        # negative
    [{"w": "a", "s": 0.0, "e": 0.4}, {"w": "b", "s": 0.3, "e": 0.6}],   # overlap
    [{"w": "a", "s": 0.0, "e": 11.0}],                        # past the audio (+0.5 s slack)
    [{"w": "a", "s": 0.0, "e": 0.2, "extra": 1}],             # unknown key
    [{"w": "a", "s": True, "e": 0.2}],                        # bool is not a number
    [{"w": "a", "s": 0.0, "e": float("inf")}],
    [{"w": "a", "s": 0.0, "e": 0.2, "line": -1}],
    [{"w": "a", "s": i, "e": i + 0.5} for i in range(sch.AUDIO_WORDS_MAX + 1)],
])
def test_a_malformed_timing_table_is_a_422(words):
    with pytest.raises(ValidationError):
        _audio(words, duration=1000.0 if isinstance(words, list) and len(words) > 10 else 10.0)


def test_only_an_audio_asset_carries_timings():
    with pytest.raises(ValidationError):
        sch.AssetRegisterRequest(kind="card", ext="png", sha256=SHA, bytes=1,
                                 metadata={"words": _words("a b")})


@pytest.mark.asyncio
async def test_the_timed_words_must_be_the_accepted_script(svc):
    run = await _claim(svc)
    await _accept(svc, run["id"], "Meet Mr. Market.", ["He names a price every day.", "You may say no."])
    spoken = "Meet Mr. Market. He names a price every day. You may say no."
    ok = json.loads(json.dumps(_audio(_words(spoken)).metadata))
    row, _ = await svc.register_asset(run["id"], kind="audio", ext="m4a", sha256=SHA, size_bytes=1,
                                      metadata=ok, claim=_holder(run))
    assert row["metadata"]["words"][0]["w"] == "Meet"
    for wrong in ("Meet Mr. Market. He names a price every day. You must buy now.",   # a changed word
                  "Meet Mr. Market. He names a price every day.",                     # a dropped line
                  spoken + " Buy today."):                                            # an added one
        with pytest.raises(mrs.MarketingRequestInvalid):
            await svc.register_asset(run["id"], kind="audio", ext="m4a", sha256="e" * 64, size_bytes=1,
                                     metadata={"words": _words(wrong)}, claim=_holder(run))


@pytest.mark.asyncio
async def test_timed_words_without_an_accepted_script_are_refused(svc):
    run = await _claim(svc)
    with pytest.raises(mrs.MarketingScriptNotReady):
        await svc.register_asset(run["id"], kind="audio", ext="m4a", sha256=SHA, size_bytes=1,
                                 metadata={"words": _words("a b")}, claim=_holder(run))


# ── the script read-back's data-card slot (Batch 2 fix CARDS-B2-1, 2026-10-20) ─────


def test_the_script_read_back_declares_the_data_card_slot_as_an_optional_int():
    """The wire contract both halves match: `data_card_slot`, an int or None, None by default (an older web's
    read-back has no such key) — and declared, because Pydantic drops an undeclared key from the response."""
    from typing import Optional

    field = sch.WorkerScript.model_fields["data_card_slot"]
    assert field.annotation == Optional[int] and field.default is None
    base = {"hook": "h", "video_script": [], "cards": [], "carousel_slides": [], "disclaimer_card": "d"}
    assert sch.WorkerScript.model_validate(base).data_card_slot is None
    sent = sch.ScriptKickResponse.model_validate({"status": "accepted", "script": {
        **base, "video_features": ["data_card"], "data_card_slot": 3}})
    assert json.loads(sent.model_dump_json())["script"]["data_card_slot"] == 3


def test_spoken_words_fold_case_and_edge_punctuation_only():
    assert mrs.spoken_words("“Mr. Market,” he said — U.S. S&P 500!") == [
        "mr", "market", "he", "said", "u.s", "s&p", "500"]
    assert mrs.narration_words({"hook": "A b.", "video_script": ["C d?", "E"]}) == ["a", "b", "c", "d", "e"]


# ── per-post art (workstream C, slice C3, 2026-11-16): the wire contract ──────


_ART = "c" * 64


def _post_image_request(**meta):
    return sch.AssetRegisterRequest(kind="card", ext="jpg", sha256=SHA, bytes=1,
                                    metadata={"image_role": sch.IMAGE_ROLE_POST, "onscreen_text": ["Footer"], **meta})


def _video_request(**meta):
    return sch.AssetRegisterRequest(kind="video", ext="mp4", sha256=SHA, bytes=1,
                                    metadata={"onscreen_text": ["Kicker"], **meta})


def test_each_surface_declares_its_art_on_its_own_asset_kind_only():
    assert sch.ASSET_METADATA_ART_SHA == "art_sha256" and sch.ASSET_METADATA_OPENING_ART_SHA == "opening_art_sha256"
    assert _post_image_request(art_sha256=_ART).metadata["art_sha256"] == _ART
    assert _post_image_request(art_sha256=None).metadata["art_sha256"] is None       # "no art" said out loud
    assert _video_request(opening_art_sha256=_ART).metadata["opening_art_sha256"] == _ART
    assert _video_request(opening_art_sha256=None).metadata["opening_art_sha256"] is None
    # each key on the other surface, or on an asset that draws nothing, is a 422
    with pytest.raises(ValidationError, match="only a post image carries metadata.art_sha256"):
        _video_request(art_sha256=_ART)
    with pytest.raises(ValidationError, match="only a video carries metadata.opening_art_sha256"):
        _post_image_request(opening_art_sha256=_ART)
    for kind, ext in (("card", "png"), ("audio", "m4a"), ("manifest", "json")):
        for key in ("art_sha256", "opening_art_sha256"):
            with pytest.raises(ValidationError):
                sch.AssetRegisterRequest(kind=kind, ext=ext, sha256=SHA, bytes=1, metadata={key: _ART})


@pytest.mark.parametrize("bad", ["C" * 64, "c" * 63, "c" * 65, "g" * 64, 7, True, ["c" * 64], {"sha": _ART}, ""])
def test_an_art_declaration_is_null_or_64_lower_case_hex(bad):
    with pytest.raises(ValidationError, match="64 lower-case hex"):
        _post_image_request(art_sha256=bad)
    with pytest.raises(ValidationError, match="64 lower-case hex"):
        _video_request(opening_art_sha256=bad)


def test_the_script_read_back_declares_art_as_an_optional_object():
    """`WorkerScript.art` must be declared (Pydantic drops an undeclared key from the response) — None by default (a
    lesson, no art, the switch off, an older web) — and it crosses the wire as the five keys the web sent."""
    from typing import Any, Dict, Optional

    field = sch.WorkerScript.model_fields["art"]
    assert field.annotation == Optional[Dict[str, Any]] and field.default is None
    base = {"hook": "h", "video_script": [], "cards": [], "carousel_slides": [], "disclaimer_card": "d"}
    assert sch.WorkerScript.model_validate(base).art is None
    art = {"url": "https://sb.example/storage/v1/object/public/marketing-media/art/" + "c" * 32 + ".jpg",
           "sha256": _ART, "bytes": 480_000, "width": 1376, "height": 774}
    sent = sch.ScriptKickResponse.model_validate({"status": "accepted", "script": {**base, "art": art}})
    assert json.loads(sent.model_dump_json())["script"]["art"] == art
    assert json.loads(sch.ScriptKickResponse.model_validate(
        {"status": "accepted", "script": base}).model_dump_json())["script"]["art"] is None


def test_post_art_is_a_known_capability_appended_last_and_never_dropped(caplog):
    import logging

    assert sch.WORKER_CAPABILITY_POST_ART == "post_art" and sch.WORKER_CAPABILITIES[-3] == "post_art"
    assert sch.WORKER_CAPABILITIES[-2] == sch.WORKER_CAPABILITY_LOOK_V23 == "look_v23"   # news-v23, appended after it
    # news-v23 phase 3 (the weekly theme post's angles), appended after that
    assert sch.WORKER_CAPABILITIES[-1] == sch.WORKER_CAPABILITY_THEME_ANGLES == "theme_angles"
    assert sch.POST_METADATA_ART == "art"
    body = {"run_date": "2026-11-17", "worker_version": "c", "claim_nonce": "ab" * 16,
            "capabilities": list(sch.WORKER_CAPABILITIES)}
    with caplog.at_level(logging.WARNING):
        assert sch.RunClaimRequest.model_validate(body).capabilities == sorted(sch.WORKER_CAPABILITIES)
    assert not [r for r in caplog.records if "does not know" in r.getMessage()]
    # the claim's bound still holds every capability this web knows
    assert len(sch.WORKER_CAPABILITIES) <= sch.RunClaimRequest.model_fields["capabilities"].metadata[0].max_length
