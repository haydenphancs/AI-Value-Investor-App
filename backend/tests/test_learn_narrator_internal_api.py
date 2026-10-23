"""`/api/v1/internal/learn/*` — the narrator worker API's auth contract and its wire behaviour
(plan "Wiser weekly content engine" §1.8; `app/api/v1/endpoints/learn_internal.py`).

The gate is declared ONCE on the router (`.claude/rules/auth.md` §1 reasoning), so the source scan at
the bottom pins it: a route added to the module without touching the router is gated by default.

auth.md §2/§3: a MISSING credential is 401 AUTH_REQUIRED, never 403; a wrong one, or a server with no
secret configured, is 403 AUTH_FORBIDDEN — both on the `{error_code, …}` contract. A missing or
malformed `X-Learn-Claim` is 422 LEARN_NARRATOR_REQUEST_INVALID; a well-formed claim that does not hold
the job is 409 LEARN_NARRATOR_JOB_NOT_HELD.

Hermetic: the routes run the REAL narration service and the REAL ledger against the in-memory
PostgREST + Storage fakes of `test_learn_weekly_narration.py` (which builds on the ledger's fake).
"""

from __future__ import annotations

import copy
import json
import logging
import re
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import test_learn_weekly_narration as nt
from app.api.v1.endpoints import learn_internal as mod
from app.config import settings
from app.main import app
from app.services.learn_weekly import contracts as c
from app.services.learn_weekly import ledger, narration

_BASE = "/api/v1/internal/learn"
_SRC = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints" / "learn_internal.py"
_TOKEN = "s3cret-narrator-token"
_JOB = "00000000-0000-0000-0000-000000000000"
_NONCE = "0123456789abcdef"


@pytest.fixture
def client():
    # NOT `with TestClient(app)`: the context manager runs the lifespan, whose jobs reach Supabase.
    yield TestClient(app)


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(settings, "LEARN_NARRATOR_TOKEN", _TOKEN)
    monkeypatch.setattr(mod, "_unset_warned", False)
    return _TOKEN


@pytest.fixture
def fake(monkeypatch):
    client = nt.FakeClient()
    monkeypatch.setattr(ledger, "get_supabase", lambda: client)
    monkeypatch.setattr(narration, "get_supabase", lambda: client)
    monkeypatch.setattr(settings, "SUPABASE_URL", nt.SUPABASE_URL)
    monkeypatch.setattr(settings, "LEARN_NARRATOR_ENGINE", "chatterbox")
    return client


@pytest.fixture
def clock(monkeypatch):
    """Pin the service clock (the routes pass no `now`)."""
    monkeypatch.setattr(narration, "_now", lambda: nt.NOW)
    return nt.NOW


def _h(claim: str = None, tok: str = _TOKEN):
    headers = {"X-Learn-Narrator-Token": tok}
    if claim is not None:
        headers["X-Learn-Claim"] = claim
    return headers


def _claim_job(client, engines=("chatterbox",)):
    r = client.post(f"{_BASE}/jobs/claim", headers=_h(), json={"engines": list(engines), "worker_version": "w/1"})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    return job, f"{job['attempts']}.{job['claim_nonce']}"


def _complete_body(job, *, path=None, size=None, mutate=None):
    blocks = narration.blocks_from_script(narration.script_json(nt._blocks()))
    t, end = nt._timings(blocks)
    if mutate:
        mutate(t)
    sha = "ef" * 32
    path = path or f"audio/weekly/{nt.SLUG}-{sha[:12]}.m4a"
    return {"path": path, "bytes": size or int(end * 12_000), "duration_seconds": end, "engine": job["engine"],
            "pipeline_version": job["pipeline_version"], "blocks": t, "checks": {"coverage": 0.99}}


def _error(r, code: str, status: int):
    assert r.status_code == status, r.text
    body = r.json()
    assert body["error_code"] == code, body
    assert set(body) >= {"error_code", "message", "user_message"}
    return body


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 1. The token gate (auth.md §2/§3)
# ═════════════════════════════════════════════════════════════════════════════════════════════


def _routes():
    return [(sorted(r.methods)[0], r.path) for r in mod.router.routes]


def test_the_router_has_exactly_the_five_contract_routes():
    assert sorted(_routes()) == sorted([
        ("POST", "/jobs/claim"), ("POST", "/jobs/{job_id}/heartbeat"), ("POST", "/jobs/{job_id}/upload"),
        ("POST", "/jobs/{job_id}/complete"), ("POST", "/jobs/{job_id}/fail"),
    ])


@pytest.mark.parametrize("method, path", _routes())
def test_a_missing_token_is_401_auth_required_on_every_route(client, token, method, path):
    r = client.request(method, _BASE + path.replace("{job_id}", _JOB), json={})
    _error(r, "AUTH_REQUIRED", 401)


@pytest.mark.parametrize("method, path", _routes())
def test_a_wrong_token_is_403_and_reaches_nothing(client, token, fake, method, path):
    for bad in ("nope", _TOKEN + "x", _TOKEN[:-1], " " + _TOKEN):
        r = client.request(method, _BASE + path.replace("{job_id}", _JOB), json={},
                           headers=_h(f"1.{_NONCE}", tok=bad))
        _error(r, "AUTH_FORBIDDEN", 403)
    assert fake.calls == [] and fake.storage.calls == []


def test_an_unset_server_secret_fails_closed_with_403_and_one_error_log(client, monkeypatch, caplog):
    monkeypatch.setattr(settings, "LEARN_NARRATOR_TOKEN", None)
    monkeypatch.setattr(mod, "_unset_warned", False)
    with caplog.at_level(logging.ERROR, logger=mod.__name__):
        for _ in range(3):
            r = client.post(f"{_BASE}/jobs/claim", headers=_h(), json={"engines": ["kokoro"], "worker_version": "w"})
            _error(r, "AUTH_FORBIDDEN", 403)
    assert caplog.text.count("LEARN_NARRATOR_TOKEN is not set") == 1
    monkeypatch.setattr(settings, "LEARN_NARRATOR_TOKEN", "")
    _error(client.post(f"{_BASE}/jobs/claim", headers=_h(tok=""), json={}), "AUTH_REQUIRED", 401)


def test_a_non_ascii_token_is_a_mismatch_not_a_500(client, token):
    r = client.post(f"{_BASE}/jobs/claim", headers={"X-Learn-Narrator-Token": "s3cret-é".encode("latin-1")},
                    json={"engines": ["kokoro"], "worker_version": "w"})
    _error(r, "AUTH_FORBIDDEN", 403)


def test_the_marketing_token_does_not_open_the_learn_router(client, token, monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_WORKER_TOKEN", "marketing-token")
    r = client.post(f"{_BASE}/jobs/claim", headers={"X-Marketing-Worker-Token": "marketing-token"},
                    json={"engines": ["kokoro"], "worker_version": "w"})
    _error(r, "AUTH_REQUIRED", 401)


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 2. The claim header
# ═════════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("path", ["/jobs/{job_id}/heartbeat", "/jobs/{job_id}/upload", "/jobs/{job_id}/complete",
                                  "/jobs/{job_id}/fail"])
@pytest.mark.parametrize("claim", [None, "", "1", "1.", ".0123456789abcdef", "0.0123456789abcdef",
                                   "x.0123456789abcdef", "1.0123456789ABCDEF", "1.0123456789abcde",
                                   "1.0123456789abcdef0", "1234567.0123456789abcdef", "-1.0123456789abcdef",
                                   " 1 .0123456789abcdef"])
def test_every_route_but_the_claim_needs_a_well_formed_claim(client, token, fake, path, claim):
    r = client.post(_BASE + path.replace("{job_id}", _JOB), headers=_h(claim), json={})
    body = _error(r, "LEARN_NARRATOR_REQUEST_INVALID", 422)
    assert "X-Learn-Claim" in body["message"]
    assert fake.calls == []


@pytest.mark.parametrize("header", ["１.0123456789abcdef", "\u0661.0123456789abcdef", "1.0123456789abcdeg"])
def test_a_claim_with_non_ascii_digits_or_non_hex_is_malformed(header):
    """`str.isdigit()` is True for '１' and '١' — the parse must not take them as attempts (the header
    itself arrives latin-1-decoded, so this is checked on the parser directly)."""
    with pytest.raises(ValueError):
        narration.WorkerClaim.parse(header)
    assert narration.WorkerClaim.parse(" 12.0123456789abcdef ") == narration.WorkerClaim(12, "0123456789abcdef")


def test_the_claim_route_needs_no_claim_header(client, token, fake):
    r = client.post(f"{_BASE}/jobs/claim", headers=_h(), json={"engines": ["chatterbox"], "worker_version": "w"})
    assert r.status_code == 204 and r.content == b""


def test_a_job_id_of_claim_does_not_borrow_the_claim_routes_exemption(client, token, fake):
    r = client.post(f"{_BASE}/jobs/claim/heartbeat", headers=_h(), json={})
    _error(r, "LEARN_NARRATOR_REQUEST_INVALID", 422)
    # The router dependency itself must PARSE (and refuse) the header here — not exempt the URL and
    # leave the handler-level fallback to say "missing" (mutation-tested: a URL-suffix exemption).
    r = client.post(f"{_BASE}/jobs/claim/heartbeat", headers=_h("1.not-hex-at-all!"), json={})
    assert "malformed claim" in _error(r, "LEARN_NARRATOR_REQUEST_INVALID", 422)["message"]


def test_the_exemption_is_the_matched_post_claim_route():
    src = _stripped_source()
    assert 'request.method == "POST"' in src and "_CLAIM_ROUTE_PATH" in src
    assert re.search(r'_CLAIM_ROUTE_PATH\s*=\s*"/jobs/claim"', src)


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 3. The claim → heartbeat → upload → complete round trip
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_a_full_round_trip_hands_the_piece_to_review(client, token, fake, clock):
    piece, job_row = nt._media_pending(fake)
    job, claim = _claim_job(client)
    assert job["id"] == job_row["id"] and job["engine"] == "chatterbox"
    assert job["voice"] == narration.CHATTERBOX_VOICE
    assert all(set(b) == {"i", "text", "role"} for b in job["blocks"]), "dest never reaches the worker"

    r = client.post(f"{_BASE}/jobs/{job['id']}/heartbeat", headers=_h(claim), json={})
    assert r.status_code == 200 and r.json()["ok"] is True

    sha = "ef" * 32
    body = _complete_body(job)
    r = client.post(f"{_BASE}/jobs/{job['id']}/upload", headers=_h(claim),
                    json={"content_type": "audio/mp4", "bytes": body["bytes"], "sha256": sha})
    assert r.status_code == 200, r.text
    up = r.json()
    assert up["path"] == f"audio/weekly/{nt.SLUG}-{sha[:12]}.m4a" and up["upload_url"] and not up["already_uploaded"]

    fake.storage.put(up["path"], body["bytes"])                       # the worker's PUT
    r = client.post(f"{_BASE}/jobs/{job['id']}/complete", headers=_h(claim), json=body)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "status": "ready", "replay": False, "piece_updated": True}
    prow = fake.row(ledger.PIECES, piece["id"])
    assert prow["status"] == c.READY_FOR_REVIEW and prow["draft"]["hasAudioVersion"] is True
    assert prow["draft"]["audioUrl"].endswith(up["path"])

    # the job is finished: its claim holds no open job any more (409); a replayed complete is 200
    # and writes nothing
    r = client.post(f"{_BASE}/jobs/{job['id']}/upload", headers=_h(claim),
                    json={"content_type": "audio/mp4", "bytes": body["bytes"], "sha256": sha})
    assert r.status_code == 409          # the job is ready now: its claim no longer holds an open job
    writes = len(fake.calls)
    r = client.post(f"{_BASE}/jobs/{job['id']}/complete", headers=_h(claim), json=body)
    assert r.status_code == 200 and r.json()["replay"] is True
    assert all(op == "select" for _, op in fake.calls[writes:])


def test_a_reupload_while_rendering_gets_the_path_without_a_url(client, token, fake, clock):
    nt._media_pending(fake)
    job, claim = _claim_job(client)
    sha = "ef" * 32
    path = f"audio/weekly/{nt.SLUG}-{sha[:12]}.m4a"
    fake.storage.put(path, 4321)
    r = client.post(f"{_BASE}/jobs/{job['id']}/upload", headers=_h(claim),
                    json={"content_type": "audio/mp4", "bytes": 4321, "sha256": sha})
    assert r.status_code == 200 and r.json() == {"upload_url": None, "path": path, "content_type": "audio/mp4",
                                                  "already_uploaded": True}


def test_the_claim_offers_only_declared_engines_and_204s_otherwise(client, token, fake, clock):
    nt._media_pending(fake, engine="chatterbox")
    r = client.post(f"{_BASE}/jobs/claim", headers=_h(), json={"engines": ["kokoro"], "worker_version": "k"})
    assert r.status_code == 204
    r = client.post(f"{_BASE}/jobs/claim", headers=_h(), json={"engines": ["espeak"], "worker_version": "k"})
    assert r.status_code == 204
    job, _ = _claim_job(client, engines=("kokoro", "chatterbox"))
    assert job["engine"] == "chatterbox"


@pytest.mark.parametrize("body", [{}, {"engines": []}, {"engines": ["chatterbox"]},
                                  {"engines": ["chatterbox"] * 9, "worker_version": "w"},
                                  {"engines": "chatterbox", "worker_version": "w"},
                                  {"engines": ["chatterbox"], "worker_version": ""}])
def test_a_malformed_claim_request_is_422_before_the_ledger(client, token, fake, body):
    r = client.post(f"{_BASE}/jobs/claim", headers=_h(), json=body)
    assert r.status_code == 422
    assert fake.calls == []


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 4. Claim fencing
# ═════════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("route, payload", [
    ("heartbeat", {}),
    ("upload", {"content_type": "audio/mp4", "bytes": 100, "sha256": "ab" * 32}),
    ("fail", {"code": "render_failed", "detail": "x"}),
])
def test_a_wrong_attempts_or_nonce_is_409_job_not_held(client, token, fake, clock, route, payload):
    nt._media_pending(fake)
    job, claim = _claim_job(client)
    attempts, nonce = claim.split(".")
    for bad in (f"{int(attempts) + 1}.{nonce}", f"{attempts}.{'0' * 16}"):
        r = client.post(f"{_BASE}/jobs/{job['id']}/{route}", headers=_h(bad), json=payload)
        _error(r, "LEARN_NARRATOR_JOB_NOT_HELD", 409)
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_IN_PROGRESS


def test_a_wrong_claim_on_complete_is_409_and_writes_nothing(client, token, fake, clock):
    nt._media_pending(fake)
    job, claim = _claim_job(client)
    body = _complete_body(job)
    fake.storage.put(body["path"], body["bytes"])
    before = copy.deepcopy({k: v.rows for k, v in fake.tables.items()})
    r = client.post(f"{_BASE}/jobs/{job['id']}/complete", headers=_h(f"{job['attempts']}.{'f' * 16}"), json=body)
    _error(r, "LEARN_NARRATOR_JOB_NOT_HELD", 409)
    assert before == {k: v.rows for k, v in fake.tables.items()}


def test_an_unknown_job_is_404(client, token, fake):
    for route in ("heartbeat", "fail"):
        payload = {} if route == "heartbeat" else {"code": "x", "detail": ""}
        r = client.post(f"{_BASE}/jobs/{_JOB}/{route}", headers=_h(f"1.{_NONCE}"), json=payload)
        _error(r, "LEARN_NARRATOR_NOT_FOUND", 404)
    r = client.post(f"{_BASE}/jobs/not-a-uuid/heartbeat", headers=_h(f"1.{_NONCE}"), json={})
    _error(r, "LEARN_NARRATOR_NOT_FOUND", 404)


def test_heartbeat_on_a_cancelled_job_is_409(client, token, fake, clock):
    piece, _ = nt._media_pending(fake)
    job, claim = _claim_job(client)
    ledger.cancel_open_jobs_sync(piece["id"], reason="owner regenerate", sb=fake)
    body = _error(client.post(f"{_BASE}/jobs/{job['id']}/heartbeat", headers=_h(claim), json={}),
                  "LEARN_NARRATOR_JOB_NOT_HELD", 409)
    assert "cancelled" in body["message"]


def test_a_cancelled_job_answers_complete_and_fail_with_200_and_writes_nothing(client, token, fake, clock):
    piece, _ = nt._media_pending(fake)
    job, claim = _claim_job(client)
    body = _complete_body(job)
    fake.storage.put(body["path"], body["bytes"])
    ledger.cancel_open_jobs_sync(piece["id"], reason="expired", sb=fake)
    before = copy.deepcopy({k: v.rows for k, v in fake.tables.items()})
    r = client.post(f"{_BASE}/jobs/{job['id']}/complete", headers=_h(claim), json=body)
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["piece_updated"] is False
    r = client.post(f"{_BASE}/jobs/{job['id']}/fail", headers=_h(claim), json={"code": "x", "detail": ""})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert before == {k: v.rows for k, v in fake.tables.items()}


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 5. Complete: object checks and the timing gate through HTTP
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_a_missing_object_is_409_asset_missing(client, token, fake, clock):
    nt._media_pending(fake)
    job, claim = _claim_job(client)
    r = client.post(f"{_BASE}/jobs/{job['id']}/complete", headers=_h(claim), json=_complete_body(job))
    _error(r, "LEARN_NARRATOR_ASSET_MISSING", 409)


def test_a_mismatched_object_is_deleted_and_409_asset_mismatch(client, token, fake, clock):
    nt._media_pending(fake)
    job, claim = _claim_job(client)
    body = _complete_body(job)
    fake.storage.put(body["path"], body["bytes"] + 1)
    _error(client.post(f"{_BASE}/jobs/{job['id']}/complete", headers=_h(claim), json=body),
           "LEARN_NARRATOR_ASSET_MISMATCH", 409)
    assert (narration.AUDIO_BUCKET, body["path"]) not in fake.storage.objects
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_QUEUED


def _raw_post(client, url, headers, payload):
    """JSON with NaN / Infinity literals, as Python's encoder (and a careless worker) writes them."""
    return client.post(url, headers={**headers, "content-type": "application/json"},
                       content=json.dumps(payload, allow_nan=True))


@pytest.mark.parametrize("mutate, reason", [
    (lambda t: t.pop(2), "missing"),
    (lambda t: t.append({**copy.deepcopy(t[-1]), "i": len(t)}), "extra block"),
    (lambda t: t[3].__setitem__("start", t[2]["end"] - 2.0), "not monotonic"),
    (lambda t: t[1].__setitem__("end", float("nan")), "finite"),
    (lambda t: t[1]["sentences"][0].__setitem__("start", float("-inf")), "finite"),
    (lambda t: t[0].__setitem__("start", -1.0), "negative"),
    (lambda t: t[-1].__setitem__("end", 99_999.0), "past the audio"),
    (lambda t: t[3]["sentences"][1].update({"start": t[3]["sentences"][0]["end"],
                                             "end": t[3]["sentences"][0]["end"] + 0.05}), "dropped"),
    (lambda t: t[3]["sentences"][0].__setitem__("text", "In its quarterly filing."), "not the server's"),
])
def test_a_bad_timing_table_is_422_with_its_reason(client, token, fake, clock, mutate, reason):
    nt._media_pending(fake)
    job, claim = _claim_job(client)
    body = _complete_body(job, mutate=mutate)
    fake.storage.put(body["path"], body["bytes"])
    r = _raw_post(client, f"{_BASE}/jobs/{job['id']}/complete", _h(claim), body)
    out = _error(r, "LEARN_NARRATOR_REQUEST_INVALID", 422)
    assert reason in out["message"], out["message"]
    assert fake.row(ledger.JOBS, job["id"])["status"] == c.JOB_IN_PROGRESS
    assert (narration.AUDIO_BUCKET, body["path"]) in fake.storage.objects


def test_a_storage_outage_is_503_ledger_error(client, token, fake, clock):
    nt._media_pending(fake)
    job, claim = _claim_job(client)
    fake.storage.failures["list"] = RuntimeError("storage unreachable")
    r = client.post(f"{_BASE}/jobs/{job['id']}/complete", headers=_h(claim), json=_complete_body(job))
    _error(r, "LEARN_NARRATOR_LEDGER_ERROR", 503)


def test_a_ledger_outage_is_503_not_a_bare_500(client, token, fake):
    fake.fail_next(ledger.JOBS, "select", RuntimeError("postgrest down"))
    r = client.post(f"{_BASE}/jobs/claim", headers=_h(), json={"engines": ["chatterbox"], "worker_version": "w"})
    _error(r, "LEARN_NARRATOR_LEDGER_ERROR", 503)


def test_the_signed_upload_url_never_reaches_a_log(client, token, fake, clock, caplog):
    nt._media_pending(fake)
    job, claim = _claim_job(client)
    with caplog.at_level(logging.DEBUG):
        r = client.post(f"{_BASE}/jobs/{job['id']}/upload", headers=_h(claim),
                        json={"content_type": "audio/mp4", "bytes": 5000, "sha256": "ab" * 32})
    assert "SECRET-TOKEN" in r.json()["upload_url"]
    assert "SECRET-TOKEN" not in caplog.text


@pytest.mark.parametrize("payload", [
    {"content_type": "audio/mpeg", "bytes": 100, "sha256": "ab" * 32},
    {"content_type": "audio/mp4", "bytes": 60 * 1024 * 1024 + 1, "sha256": "ab" * 32},
    {"content_type": "audio/mp4", "bytes": 0, "sha256": "ab" * 32},
    {"content_type": "audio/mp4", "bytes": 100, "sha256": "zz" * 32},
])
def test_upload_refuses_a_contract_breach_with_422(client, token, fake, clock, payload):
    nt._media_pending(fake)
    job, claim = _claim_job(client)
    r = client.post(f"{_BASE}/jobs/{job['id']}/upload", headers=_h(claim), json=payload)
    _error(r, "LEARN_NARRATOR_REQUEST_INVALID", 422)
    assert fake.storage.calls == []


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 6. Fail
# ═════════════════════════════════════════════════════════════════════════════════════════════


def test_fail_requeues_then_fails_and_opens_the_next_round(client, token, fake, clock, monkeypatch):
    piece, _ = nt._media_pending(fake)
    alerts = []

    async def _alert(piece_row, *, status, reason, stage):
        alerts.append(reason)

    monkeypatch.setattr(narration, "_notify_terminal", _alert)
    job, claim = _claim_job(client)
    r = client.post(f"{_BASE}/jobs/{job['id']}/fail", headers=_h(claim), json={"code": "render_failed", "detail": "x"})
    assert r.status_code == 200 and r.json() == {"ok": True, "status": "queued", "worker_failures": 1}
    job, claim = _claim_job(client)
    r = client.post(f"{_BASE}/jobs/{job['id']}/fail", headers=_h(claim), json={"code": "render_failed", "detail": "x"})
    assert r.json()["status"] == "failed"
    jobs = ledger.list_jobs_sync(piece["id"], sb=fake)
    assert [j["round"] for j in jobs] == [1, 2] and alerts == []
    assert fake.row(ledger.PIECES, piece["id"])["narration_job_id"] == jobs[1]["id"]


def test_fail_past_the_cutoff_fails_the_piece_with_one_alert(client, token, fake, monkeypatch):
    piece, _ = nt._media_pending(fake)
    late = nt.HOLD - timedelta(minutes=30)
    monkeypatch.setattr(narration, "_now", lambda: late)
    alerts = []

    async def _alert(piece_row, *, status, reason, stage):
        alerts.append((piece_row["id"], status, reason, stage))

    monkeypatch.setattr(narration, "_notify_terminal", _alert)
    for _ in range(c.MAX_JOB_WORKER_FAILURES):
        job, claim = _claim_job(client)
        client.post(f"{_BASE}/jobs/{job['id']}/fail", headers=_h(claim), json={"code": "oom", "detail": "killed"})
    assert fake.row(ledger.PIECES, piece["id"])["status"] == c.FAILED
    assert alerts == [(piece["id"], c.FAILED, "narration_cutoff_passed", "narration")]


@pytest.mark.parametrize("payload", [{"code": "Bad Code"}, {"code": ""}, {"code": "x" * 65}, {"detail": "no code"}])
def test_fail_refuses_a_malformed_code(client, token, fake, clock, payload):
    nt._media_pending(fake)
    job, claim = _claim_job(client)
    r = client.post(f"{_BASE}/jobs/{job['id']}/fail", headers=_h(claim), json=payload)
    assert r.status_code == 422
    assert fake.row(ledger.JOBS, job["id"])["worker_failures"] == 0


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 7. Source guards (comments and docstrings stripped — testing.md §3)
# ═════════════════════════════════════════════════════════════════════════════════════════════


def _stripped_source() -> str:
    src = _SRC.read_text(encoding="utf-8")
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(re.sub(r"#.*$", "", line) for line in src.splitlines())


def test_the_gates_are_declared_on_the_router_not_per_route():
    src = _stripped_source()
    m = re.search(r"router\s*=\s*APIRouter\((.*?)\)\n", src, re.S)
    assert m, "router declaration not found"
    assert "Depends(require_learn_narrator)" in m.group(1) and "Depends(require_job_claim)" in m.group(1)
    assert src.count("APIRouter(") == 1
    assert "get_current_user" not in src and "get_identity_only_user" not in src


def test_the_module_touches_no_fmp_client_and_no_supabase():
    src = _stripped_source()
    assert "fmp" not in src.lower()
    assert "get_supabase" not in src and ".table(" not in src and ".storage" not in src


def test_the_token_compare_is_constant_time():
    src = _stripped_source()
    assert "secrets.compare_digest(" in src
    assert not re.search(r"x_learn_narrator_token\s*[!=]=\s*expected", src)
