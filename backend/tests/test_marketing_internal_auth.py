"""
`/api/v1/internal/marketing/*` — the worker API's auth contract and its wire shape.

The gate is declared ONCE on the router (`.claude/rules/auth.md` §1 reasoning), so the
source-scan at the bottom pins that: a route added to this module without touching the
router is gated by default, and a `router = APIRouter()` regression would open all of them.

auth.md §2/§3: a MISSING credential is 401 AUTH_REQUIRED, never 403; a wrong one, or a server
with no secret configured, is 403 AUTH_FORBIDDEN. Both bodies carry the `{error_code, …}`
contract, not a bare `{"detail": …}`.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import marketing_internal as mod
from app.config import settings
from app.main import app

_BASE = "/api/v1/internal/marketing"
# The claim window is today/yesterday ET (`claim_window_ok`). The endpoint recomputes the ET date
# per request, so the clock it reads is PINNED below (autouse) — sampling it at import made the
# suite fail whenever a run crossed midnight ET between collection and the request. Auth-failure
# tests may keep any date — the router gate answers first.
_TODAY = date(2026, 9, 17)
_YESTERDAY = _TODAY - timedelta(days=1)
_SRC = Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints" / "marketing_internal.py"
_TOKEN = "s3cret-worker-token"


@pytest.fixture(autouse=True)
def pinned_clock(monkeypatch):
    # The binding the endpoint calls (module-level `from … import run_date_et`), not run_service's.
    monkeypatch.setattr(mod, "run_date_et", lambda now=None: _TODAY)
    return _TODAY


@pytest.fixture
def client():
    # NOT `with TestClient(app)`: the context manager runs the lifespan, whose jobs reach
    # Supabase, and conftest blocks that.
    logging.disable(logging.CRITICAL)
    try:
        yield TestClient(app)
    finally:
        logging.disable(logging.NOTSET)


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_WORKER_TOKEN", _TOKEN)
    monkeypatch.setattr(mod, "_unset_warned", False)
    return _TOKEN


class _FakeService:
    """Stands in for the ledger so the route layer is tested in isolation."""

    def __init__(self):
        self.calls = []

    async def claim_run(self, run_date, *, worker_version, dry_run, now=None, claim_nonce=None, resume_only=False):
        self.calls.append(("claim", run_date, worker_version, dry_run))
        if resume_only:
            return None, "no_run"
        return {
            "id": "11111111-1111-1111-1111-111111111111", "run_date": run_date.isoformat(),
            "status": "in_progress", "stage": "planned", "content_class": "A", "dry_run": dry_run,
            "attempts": 1, "timings": {}, "metadata": {}, "an_extra_column": 1,
        }, "claimed"

    async def update_run(self, run_id, **fields):
        self.calls.append(("update", run_id, fields))
        return {"id": run_id, "run_date": "2026-09-17", "status": fields.get("status") or "in_progress",
                "stage": fields.get("stage") or "planned", "content_class": "A"}

    async def register_asset(self, run_id, **fields):
        self.calls.append(("register", run_id, fields))
        asset = {"id": "a1", "run_id": run_id, "kind": fields["kind"], "storage_path": "p", "content_type": "application/json",
                 "sha256": fields["sha256"], "status": "pending_upload"}
        return asset, {"method": "PUT", "url": "https://x/y?token=t", "token": "t", "bucket": "marketing-media",
                       "path": "p", "content_type": "application/json"}

    async def complete_asset(self, asset_id):
        self.calls.append(("complete", asset_id))
        return {"id": asset_id, "run_id": "r", "kind": "manifest", "storage_path": "p", "content_type": "application/json",
                "sha256": "a" * 64, "status": "ready"}

    async def create_posts(self, run_id, specs):
        self.calls.append(("posts", run_id, specs))
        return [{"id": f"p{i}", "run_id": run_id, "platform": s["platform"], "format": s["format"],
                 "status": "pending_review", "idempotency_key": f"k{i}"} for i, s in enumerate(specs)]


@pytest.fixture
def fake_service(monkeypatch):
    svc = _FakeService()
    monkeypatch.setattr(mod, "get_marketing_run_service", lambda: svc)
    return svc


# ── auth ──────────────────────────────────────────────────────────────────────


def test_missing_header_is_401_AUTH_REQUIRED(client, token):
    r = client.post(f"{_BASE}/runs/claim", json={"run_date": "2026-09-17", "worker_version": "t"})
    assert r.status_code == 401, r.text
    body = r.json()
    assert body["error_code"] == "AUTH_REQUIRED"
    assert "user_message" in body and "detail" not in body
    assert r.headers.get("www-authenticate", "").lower().startswith("bearer")


def test_wrong_token_is_403_AUTH_FORBIDDEN(client, token):
    r = client.post(f"{_BASE}/runs/claim", json={"run_date": "2026-09-17", "worker_version": "t"},
                    headers={"X-Marketing-Worker-Token": "nope"})
    assert r.status_code == 403, r.text
    assert r.json()["error_code"] == "AUTH_FORBIDDEN"


@pytest.mark.parametrize("near_miss", [
    _TOKEN[:1],                 # a prefix (kills a startswith / truncated compare)
    _TOKEN[:-1],                # the secret minus its last character
    _TOKEN + "x",               # the secret plus one (kills `header in expected`)
    _TOKEN.upper(),             # case
    _TOKEN[:-1] + "X",          # same length, one character off (kills a length-only check)
])
def test_a_near_miss_token_is_403_and_reaches_nothing(client, token, fake_service, near_miss):
    """'nope' alone would pass a comparator that accepts any PREFIX of the secret — and a
    one-character header would then reach kick_script's paid generation."""
    assert near_miss != _TOKEN
    r = client.post(f"{_BASE}/runs/claim", json={"run_date": _TODAY.isoformat(), "worker_version": "t"},
                    headers={"X-Marketing-Worker-Token": near_miss})
    assert r.status_code == 403, (near_miss, r.text)
    assert r.json()["error_code"] == "AUTH_FORBIDDEN"
    assert fake_service.calls == []


def test_the_claim_window_is_evaluated_on_the_endpoints_clock(client, token, fake_service):
    h = {"X-Marketing-Worker-Token": token}
    for ok in (_TODAY, _YESTERDAY):
        r = client.post(f"{_BASE}/runs/claim", json={"run_date": ok.isoformat(), "worker_version": "t"}, headers=h)
        assert r.status_code == 200, (ok, r.text)
    for bad in (_TODAY + timedelta(days=1), _TODAY - timedelta(days=2)):
        r = client.post(f"{_BASE}/runs/claim", json={"run_date": bad.isoformat(), "worker_version": "t"}, headers=h)
        assert r.status_code == 422 and r.json()["error_code"] == "INVALID_INPUT", bad


def test_unset_server_secret_fails_closed_with_403(client, monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_WORKER_TOKEN", None)
    monkeypatch.setattr(mod, "_unset_warned", False)
    r = client.post(f"{_BASE}/runs/claim", json={"run_date": "2026-09-17", "worker_version": "t"},
                    headers={"X-Marketing-Worker-Token": "anything"})
    assert r.status_code == 403, r.text
    assert r.json()["error_code"] == "AUTH_FORBIDDEN"


def test_non_ascii_header_is_a_mismatch_not_a_500(client, token):
    r = client.post(f"{_BASE}/runs/claim", json={"run_date": "2026-09-17", "worker_version": "t"},
                    headers={b"X-Marketing-Worker-Token": "s\xe9cret".encode("latin-1")})
    # Starlette decodes header bytes as latin-1 → a non-ASCII str; `compare_digest` on str
    # would raise TypeError and 500. The gate compares bytes, so it is a plain mismatch.
    assert r.status_code == 403, r.text


def test_every_route_in_the_module_is_gated(client, token, fake_service):
    """The gate lives on the router; nothing here answers without the header. The probes are
    DERIVED from the router (they were a hand-written list, so a new route was unguarded by
    this test until someone remembered to add it)."""
    probes = []
    for route in mod.router.routes:
        path = re.sub(r"\{[^}]+\}", "p1", route.path)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            probes.append((method, f"{_BASE}{path}"))
    assert len(probes) >= 6, probes  # anti-vacuity: claim, patch, assets, complete, script, posts
    assert ("POST", f"{_BASE}/runs/p1/script") in probes
    for method, path in probes:
        r = client.request(method, path, json={})
        assert r.status_code == 401, (method, path, r.status_code, r.text)
    assert fake_service.calls == []


# ── wire shape ────────────────────────────────────────────────────────────────


def test_claim_round_trip_and_extra_columns_are_ignored(client, token, fake_service):
    r = client.post(f"{_BASE}/runs/claim",
                    json={"run_date": _TODAY.isoformat(), "worker_version": "phase1", "dry_run": False},
                    headers={"X-Marketing-Worker-Token": token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["claimed"] is True and body["reason"] == "claimed"
    assert body["run"]["run_date"] == _TODAY.isoformat() and body["run"]["dry_run"] is False
    assert "an_extra_column" not in body["run"]
    assert fake_service.calls == [("claim", _TODAY, "phase1", False)]


@pytest.mark.parametrize("bad", ["2026/09/17", "17-09-2026", "2026-13-01", "today", ""])
def test_claim_rejects_a_malformed_date_with_422(client, token, fake_service, bad):
    r = client.post(f"{_BASE}/runs/claim", json={"run_date": bad, "worker_version": "t"},
                    headers={"X-Marketing-Worker-Token": token})
    assert r.status_code == 422, r.text
    assert fake_service.calls == []


def test_update_validates_stage_and_status_before_touching_the_ledger(client, token, fake_service):
    h = {"X-Marketing-Worker-Token": token}
    assert client.patch(f"{_BASE}/runs/r1", json={"stage": "teleported"}, headers=h).status_code == 422
    assert client.patch(f"{_BASE}/runs/r1", json={"status": "gone"}, headers=h).status_code == 422
    assert client.patch(f"{_BASE}/runs/r1", json={"content_class": "B"}, headers=h).status_code == 422
    assert fake_service.calls == []
    r = client.patch(f"{_BASE}/runs/r1", json={"stage": "selected", "timings": {"select_s": 1.2}}, headers=h)
    assert r.status_code == 200, r.text
    assert fake_service.calls[-1][2]["stage"] == "selected"
    # the route always writes AS THE WORKER: fenced on its own in_progress run
    assert fake_service.calls[-1][2]["worker"] is True


def test_register_asset_validates_kind_ext_and_sha(client, token, fake_service):
    h = {"X-Marketing-Worker-Token": token}
    ok = {"kind": "manifest", "ext": ".JSON", "sha256": "A" * 64, "bytes": 10}
    for field, bad in [("kind", "selfie"), ("ext", "exe"), ("sha256", "abc"), ("bytes", -1)]:
        r = client.post(f"{_BASE}/runs/r1/assets", json={**ok, field: bad}, headers=h)
        assert r.status_code == 422, (field, r.text)
    r = client.post(f"{_BASE}/runs/r1/assets", json=ok, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["upload"]["method"] == "PUT"
    # normalised before reaching the ledger
    reg = fake_service.calls[-1][2]
    assert reg["ext"] == "json" and reg["sha256"] == "a" * 64


def test_posts_validates_platform_and_format_and_caps_batch(client, token, fake_service):
    h = {"X-Marketing-Worker-Token": token}
    r = client.post(f"{_BASE}/runs/r1/posts", json={"posts": [{"platform": "myspace", "format": "text"}]}, headers=h)
    assert r.status_code == 422
    r = client.post(f"{_BASE}/runs/r1/posts", json={"posts": []}, headers=h)
    assert r.status_code == 422
    r = client.post(f"{_BASE}/runs/r1/posts",
                    json={"posts": [{"platform": "x", "format": "text", "caption": "a"},
                                    {"platform": "tiktok", "format": "video", "asset_ids": ["not-a-uuid"]}]}, headers=h)
    assert r.status_code == 422, "asset_ids must be UUIDs (the column is UUID[])"
    r = client.post(f"{_BASE}/runs/r1/posts",
                    json={"posts": [{"platform": "x", "format": "text", "caption": "a"},
                                    {"platform": "tiktok", "format": "video",
                                     "asset_ids": ["11111111-1111-1111-1111-111111111111"]}]}, headers=h)
    assert r.status_code == 200, r.text
    assert [p["status"] for p in r.json()["posts"]] == ["pending_review", "pending_review"]


def test_resume_only_claim_with_nothing_to_resume_returns_no_run(client, token, fake_service):
    r = client.post(f"{_BASE}/runs/claim",
                    json={"run_date": _YESTERDAY.isoformat(), "worker_version": "t", "resume_only": True},
                    headers={"X-Marketing-Worker-Token": token})
    assert r.status_code == 200, r.text
    assert r.json() == {"claimed": False, "reason": "no_run", "run": None}


def test_known_ledger_failures_log_at_warning_not_error(client, token, monkeypatch, caplog):
    """A Supabase blip must not page Sentry three times per stage (the worker retries 5xx)."""
    import logging as _logging

    class Blip:
        async def update_run(self, run_id, **fields):
            from app.services.marketing.run_service import MarketingRunError
            raise MarketingRunError("update_run failed (run_id=r1): APIError: 520")

    monkeypatch.setattr(mod, "get_marketing_run_service", lambda: Blip())
    _logging.disable(_logging.NOTSET)
    with caplog.at_level(_logging.WARNING, logger=mod.logger.name):
        r = client.patch(f"{_BASE}/runs/r1", json={"stage": "selected"}, headers={"X-Marketing-Worker-Token": token})
    assert r.status_code == 503 and r.json()["error_code"] == "MARKETING_LEDGER_ERROR"
    levels = {rec.levelno for rec in caplog.records if "update_run" in rec.getMessage()}
    assert levels == {_logging.WARNING}


def test_ledger_errors_surface_as_the_error_contract_not_a_bare_500(client, token, monkeypatch):
    class Boom:
        async def complete_asset(self, asset_id):
            from app.services.marketing.run_service import MarketingAssetMissingInStorage
            raise MarketingAssetMissingInStorage("asset a1 at p is not in bucket marketing-media")

    monkeypatch.setattr(mod, "get_marketing_run_service", lambda: Boom())
    r = client.post(f"{_BASE}/assets/a1/complete", headers={"X-Marketing-Worker-Token": token})
    # 409, terminal for the stage: the worker must NOT retry the same call — it fails the
    # run and the next hourly tick re-registers + re-uploads.
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error_code"] == "MARKETING_ASSET_MISSING" and "user_message" in body
    assert body.get("details", {}).get("step") == "marketing_complete_asset"


def test_ledger_exceptions_map_to_the_marketing_codes_not_the_report_default():
    """The classifier's generic tail answers REPORT_GENERATION_FAILED; a ledger failure
    arriving under that name points the on-call at the wrong subsystem."""
    import json

    from app.api.error_response import error_response_from_exception
    from app.services.marketing import run_service as mrs

    cases = {
        mrs.MarketingRunError("insert failed"): ("MARKETING_LEDGER_ERROR", 503),
        mrs.MarketingRunNotFound("run x"): ("MARKETING_NOT_FOUND", 404),
        mrs.MarketingAssetNotFound("asset y"): ("MARKETING_NOT_FOUND", 404),
        mrs.MarketingAssetMissingInStorage("z"): ("MARKETING_ASSET_MISSING", 409),
        mrs.MarketingScriptNotReady("s"): ("MARKETING_SCRIPT_NOT_READY", 409),
        mrs.MarketingRunNotHeld("run r1 is 'skipped'"): ("MARKETING_RUN_NOT_HELD", 409),
        mrs.MarketingRequestInvalid("x/video"): ("MARKETING_REQUEST_INVALID", 422),
    }
    for exc, (code, status) in cases.items():
        r = error_response_from_exception(exc, step="t")
        assert (json.loads(r.body)["error_code"], r.status_code) == (code, status), type(exc).__name__


# ── source scan: the gate is on the router ────────────────────────────────────


def _stripped_source() -> str:
    src = _SRC.read_text()
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(re.sub(r"#.*$", "", l) for l in src.splitlines())


def test_the_gate_is_declared_on_the_router_not_per_route():
    src = _stripped_source()
    m = re.search(r"router\s*=\s*APIRouter\((.*?)\)\n", src, re.S)
    assert m, "router declaration not found"
    assert "Depends(require_marketing_worker)" in m.group(1), (
        "the worker gate must be a ROUTER-level dependency so a new route is gated by default"
    )
    # and no route takes an iOS auth dependency — this surface is not for the app
    assert "get_current_user" not in src and "get_identity_only_user" not in src


def test_the_module_touches_no_fmp_client():
    """auth.md §1a: nothing on the marketing path may relay FMP data; the worker API is a
    ledger, not a data source."""
    src = _stripped_source()
    assert "integrations.fmp" not in src and "fmp_client" not in src.lower()
