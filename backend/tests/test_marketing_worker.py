"""
`marketing/main.py` — the media worker: the ET gate, the manifest, and the whole
claim → register → signed PUT → complete → checkpoint conversation against a fake backend.

The worker package `backend/marketing/` is STANDALONE (imports nothing from app.*); this
test loads the entrypoint by path (a fresh module per test, so import-time side effects such
as logging setup are exercised) and scans EVERY file in the package for that property,
because importing app.config in that container would fail (no SUPABASE_URL) and importing
app.main would double-start every lifespan loop.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import httpx
import pytest

_PKG = Path(__file__).resolve().parents[1] / "marketing"
_SCRIPT = _PKG / "main.py"
ET = ZoneInfo("America/New_York")


def _load():
    spec = importlib.util.spec_from_file_location("marketing_daily_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture
def m():
    return _load()


# ── standalone-ness ───────────────────────────────────────────────────────────


def test_worker_package_imports_nothing_from_app():
    files = sorted(_PKG.rglob("*.py"))
    assert _SCRIPT in files and len(files) >= 2, files
    for py in files:
        src = re.sub(r'"""[\s\S]*?"""', "", py.read_text())
        src = "\n".join(re.sub(r"#.*$", "", l) for l in src.splitlines())
        assert not re.search(r"^\s*(from|import)\s+app[.\s]", src, re.M), (
            f"{py.name}: the worker must stay importable without app.config "
            "(no SUPABASE_* in its container)"
        )


def test_worker_package_is_self_contained_for_docker():
    """The Dockerfile COPYs only `marketing/`; the run command and the fonts path must agree."""
    docker = (_PKG / "Dockerfile").read_text()
    assert 'COPY marketing/ marketing/' in docker and '"-m", "marketing.main"' in docker
    copies = [l for l in docker.splitlines() if l.startswith("COPY ")]
    assert copies and all(l.split()[1].startswith("marketing/") for l in copies), copies
    toml = (_PKG / "railway.toml").read_text()
    assert 'dockerfilePath = "marketing/Dockerfile"' in toml
    assert 'startCommand = "python -m marketing.main"' in toml


def test_run_stages_mirror_the_schema(m):
    from app.schemas.marketing import RUN_STAGES

    assert m.RUN_STAGES == RUN_STAGES


# ── pure helpers ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "hour, run_hour, expected",
    [(15, 16, False), (16, 16, True), (23, 16, True), (0, 16, False), (0, 0, True), (7, 8, False)],
)
def test_should_run_now_gates_on_the_et_hour(m, hour, run_hour, expected):
    now = datetime(2026, 9, 17, hour, 30, tzinfo=ET)
    assert m.should_run_now(now, run_hour) is expected


def test_should_run_now_force_bypasses_and_bad_hour_raises(m):
    assert m.should_run_now(datetime(2026, 9, 17, 1, 0, tzinfo=ET), 16, force=True) is True
    with pytest.raises(ValueError):
        m.should_run_now(datetime(2026, 9, 17, 1, 0, tzinfo=ET), 24)


@pytest.mark.parametrize("raw, default, expected", [
    (None, True, True), ("", False, False), ("1", False, True), ("true", False, True),
    ("YES", False, True), ("0", True, False), ("false", True, False), ("nah", True, False),
])
def test_env_flag(m, monkeypatch, raw, default, expected):
    if raw is None:
        monkeypatch.delenv("MK_TEST_FLAG", raising=False)
    else:
        monkeypatch.setenv("MK_TEST_FLAG", raw)
    assert m.env_flag("MK_TEST_FLAG", default) is expected


def test_build_manifest_lists_fonts_and_tolerates_a_missing_dir(m, tmp_path):
    (tmp_path / "Inter-Bold.ttf").write_bytes(b"x")
    (tmp_path / "notes.md").write_bytes(b"x")
    (tmp_path / "Inter-Regular.TTF").write_bytes(b"x")
    man = m.build_manifest(worker_version="v", run_date=datetime(2026, 9, 17).date(), dry_run=True,
                           ffmpeg="ffmpeg version 5.1.9", fonts_dir=str(tmp_path))
    assert man["fonts"] == ["Inter-Bold.ttf", "Inter-Regular.TTF"]
    assert man["run_date"] == "2026-09-17" and man["ffmpeg"].startswith("ffmpeg")
    assert "platform" not in man  # the manifest lands in a PUBLIC bucket: no kernel/glibc fingerprint
    missing = m.build_manifest(worker_version="v", run_date=datetime(2026, 9, 17).date(), dry_run=True,
                               ffmpeg=None, fonts_dir=str(tmp_path / "nope"))
    assert missing["fonts"] == [] and missing["ffmpeg"] is None
    assert missing["fonts_error"] and "FileNotFoundError" in missing["fonts_error"]


def test_sha256_hex(m):
    assert m.sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


# ── the conversation with the backend ─────────────────────────────────────────


class FakeBackend:
    """Answers the internal API and the Storage signed-upload PUT; records every call."""

    def __init__(self, *, claim_reason="claimed", claim_status_codes=None, complete_status=200):
        self.calls: List[tuple] = []
        self.claim_reason = claim_reason
        self.claim_status_codes = list(claim_status_codes or [])
        self.complete_status = complete_status
        self.uploaded: Dict[str, bytes] = {}
        self.claim_bodies: List[Dict[str, Any]] = []
        self.run = {"id": "run-1", "run_date": "2026-09-17", "status": "in_progress", "stage": "planned",
                    "content_class": "A", "attempts": 1, "dry_run": True, "timings": {}, "metadata": {}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if request.method == "POST" and path.endswith("/runs/claim"):
            self.claim_bodies.append(json.loads(request.content))
        if request.url.host == "sb.example":
            assert request.method == "PUT"
            assert request.headers.get("x-upsert") == "false"
            body = request.read()
            assert b'name="file"' in body and b"Content-Type: application/json" in body
            self.uploaded[path] = body
            return httpx.Response(200, json={"Key": path})
        assert request.headers.get("x-marketing-worker-token") == "tok"
        if path.endswith("/runs/claim"):
            if self.claim_status_codes:
                return httpx.Response(self.claim_status_codes.pop(0), json={"error_code": "X", "message": "boom"})
            return httpx.Response(200, json={"claimed": self.claim_reason == "claimed",
                                             "reason": self.claim_reason, "run": self.run})
        if path.endswith("/assets") and request.method == "POST":
            body = json.loads(request.content)
            asset = {"id": "asset-1", "run_id": "run-1", "kind": body["kind"], "content_type": "application/json",
                     "storage_path": f"2026-09-17/{body['kind']}-{body['sha256'][:16]}.json",
                     "sha256": body["sha256"], "status": "pending_upload"}
            return httpx.Response(200, json={"asset": asset, "upload": {
                "method": "PUT", "url": f"https://sb.example/object/upload/sign/marketing-media/{asset['storage_path']}?token=t",
                "token": "t", "bucket": "marketing-media", "path": asset["storage_path"],
                "content_type": "application/json"}})
        if path.endswith("/complete"):
            if self.complete_status != 200:
                return httpx.Response(self.complete_status, json={"error_code": "MARKETING", "message": "not in bucket"})
            return httpx.Response(200, json={"asset": {"id": "asset-1", "run_id": "run-1", "kind": "manifest",
                                                       "storage_path": "p", "content_type": "application/json",
                                                       "sha256": "a" * 64, "status": "ready"}})
        if request.method == "PATCH":
            self.run.update({k: v for k, v in json.loads(request.content).items() if k in ("status", "stage")})
            return httpx.Response(200, json=self.run)
        return httpx.Response(404, json={"error_code": "NOT_FOUND", "message": path})


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("MARKETING_API_BASE_URL", "https://backend.example")
    monkeypatch.setenv("MARKETING_WORKER_TOKEN", "tok")
    monkeypatch.setenv("MARKETING_FORCE", "1")
    monkeypatch.setenv("MARKETING_RUN_DATE", "2026-09-17")
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)


def _wire(m, monkeypatch, backend: FakeBackend):
    transport = httpx.MockTransport(backend.handler)

    class Shim:
        Client = functools.partial(httpx.Client, transport=transport)
        TransportError = httpx.TransportError

    monkeypatch.setattr(m, "httpx", Shim)
    monkeypatch.setattr(m, "ffmpeg_version", lambda: "ffmpeg version 5.1.9 (fake)")
    monkeypatch.setattr(m, "time", _FastTime())


class _FastTime:
    """No real sleeping in the retry backoff."""
    monotonic = staticmethod(__import__("time").monotonic)

    @staticmethod
    def sleep(_):
        return None


def test_happy_path_claims_uploads_manifest_and_closes_the_run_as_skipped(m, monkeypatch, env):
    be = FakeBackend()
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    tails = [(meth, p.rsplit("/", 1)[-1]) for meth, p in be.calls]
    assert tails[:2] == [("POST", "claim"), ("POST", "assets")]
    assert tails[2][0] == "PUT" and tails[2][1].startswith("manifest-") and tails[2][1].endswith(".json")
    assert tails[3:] == [("POST", "complete"), ("PATCH", "run-1"), ("PATCH", "run-1")]
    assert be.run["status"] == "skipped"
    # The manifest that went up is real JSON describing the image.
    (body,) = be.uploaded.values()
    start, end = body.find(b"{"), body.rfind(b"}") + 1
    manifest = json.loads(body[start:end])
    assert manifest["worker_version"] == "phase1" and manifest["run_date"] == "2026-09-17"
    assert manifest["ffmpeg"].startswith("ffmpeg")


def test_not_claimed_exits_zero_without_touching_anything_else(m, monkeypatch, env):
    for reason in ("already_done", "in_progress", "media_ready"):
        be = FakeBackend(claim_reason=reason)
        _wire(m, monkeypatch, be)
        assert m.main() == 0
        assert [meth for meth, _ in be.calls] == ["POST"], reason


def test_before_the_window_only_tries_to_resume_yesterday_and_never_creates(m, monkeypatch, env):
    monkeypatch.delenv("MARKETING_FORCE")
    monkeypatch.delenv("MARKETING_RUN_DATE")
    monkeypatch.setenv("MARKETING_RUN_HOUR_ET", "16")

    class FrozenDT(m.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 17, 9, 0, tzinfo=ET)

    monkeypatch.setattr(m, "datetime", FrozenDT)
    be = FakeBackend(claim_reason="no_run")
    be.run = None  # type: ignore[assignment]
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert [meth for meth, _ in be.calls] == ["POST"]
    (body,) = be.claim_bodies
    assert body["resume_only"] is True and body["run_date"] == "2026-09-16"
    assert len(body["claim_nonce"]) == 32


def test_every_claim_carries_a_per_process_nonce(m, monkeypatch, env):
    be = FakeBackend()
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    (body,) = be.claim_bodies
    assert body["resume_only"] is False and len(body["claim_nonce"]) == 32


def test_httpx_request_lines_with_the_signed_token_are_not_logged(m, monkeypatch, env, caplog):
    """httpx logs every request URL at INFO; the signed-upload URL carries ?token=."""
    import logging as _logging

    be = FakeBackend()
    _wire(m, monkeypatch, be)
    with caplog.at_level(_logging.DEBUG):
        assert m.main() == 0
    assert not any("token=" in rec.getMessage() for rec in caplog.records)
    assert _logging.getLogger("httpx").level == _logging.WARNING


def test_lowercase_log_level_does_not_crash_the_worker(monkeypatch):
    monkeypatch.setenv("MARKETING_LOG_LEVEL", "debug")
    _load()  # module import applies basicConfig


def test_transient_5xx_on_claim_is_retried_then_succeeds(m, monkeypatch, env):
    be = FakeBackend(claim_status_codes=[503, 502])
    _wire(m, monkeypatch, be)
    assert m.main() == 0
    assert [p.rsplit("/", 1)[-1] for meth, p in be.calls][:3] == ["claim", "claim", "claim"]


def test_persistent_5xx_gives_up_with_exit_1(m, monkeypatch, env):
    be = FakeBackend(claim_status_codes=[503, 503, 503, 503])
    _wire(m, monkeypatch, be)
    assert m.main() == 1
    assert len(be.calls) == 3  # _HTTP_ATTEMPTS, then stop


def test_a_stage_failure_is_recorded_on_the_run_and_exits_1(m, monkeypatch, env):
    be = FakeBackend(complete_status=422)
    _wire(m, monkeypatch, be)
    assert m.main() == 1
    assert be.run["status"] == "failed"
    # the failure PATCH carried the error text
    assert [meth for meth, _ in be.calls][-1] == "PATCH"


def test_missing_env_is_exit_1_before_any_call(m, monkeypatch):
    monkeypatch.delenv("MARKETING_API_BASE_URL", raising=False)
    monkeypatch.delenv("MARKETING_WORKER_TOKEN", raising=False)
    assert m.main() == 1


def test_signed_upload_sends_apikey_only_when_configured(m, monkeypatch, env):
    seen: Dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["apikey"] = request.headers.get("apikey")
        seen["upsert"] = request.headers.get("x-upsert")
        return httpx.Response(200, json={"Key": "k"})

    class Shim:
        Client = functools.partial(httpx.Client, transport=httpx.MockTransport(handler))
        TransportError = httpx.TransportError

    monkeypatch.setattr(m, "httpx", Shim)
    up = {"url": "https://sb.example/object/upload/sign/marketing-media/x.json?token=t",
          "path": "x.json", "content_type": "application/json"}
    m.upload_signed(up, b"{}")
    assert seen == {"apikey": None, "upsert": "false"}
    m.upload_signed(up, b"{}", apikey="pk")
    assert seen["apikey"] == "pk"


def test_signed_upload_409_means_already_there_and_is_not_an_error(m, monkeypatch, env):
    class Shim:
        Client = functools.partial(httpx.Client, transport=httpx.MockTransport(
            lambda r: httpx.Response(409, json={"statusCode": "409", "error": "Duplicate"})))
        TransportError = httpx.TransportError

    monkeypatch.setattr(m, "httpx", Shim)
    m.upload_signed({"url": "https://sb.example/u?token=t", "path": "x.json",
                     "content_type": "application/json"}, b"{}")  # no raise


def test_signed_upload_failure_is_loud(m, monkeypatch, env):
    class Shim:
        Client = functools.partial(httpx.Client, transport=httpx.MockTransport(
            lambda r: httpx.Response(413, text="Payload too large")))
        TransportError = httpx.TransportError

    monkeypatch.setattr(m, "httpx", Shim)
    with pytest.raises(m.WorkerAPIError, match="413"):
        m.upload_signed({"url": "https://sb.example/u?token=t", "path": "x.json",
                         "content_type": "application/json"}, b"{}")


def test_railway_watch_patterns_are_repo_rooted():
    """Railway evaluates watchPatterns from the REPO root even with a Root Directory set, so
    an un-prefixed pattern never matches and every push is skipped as 'no changes'."""
    toml = (_PKG / "railway.toml").read_text()
    body = "\n".join(l for l in toml.splitlines() if not l.strip().startswith("#"))
    m_ = re.search(r"watchPatterns\s*=\s*\[(.*?)\]", body, re.S)
    assert m_, "watchPatterns missing"
    patterns = re.findall(r'"([^"]+)"', m_.group(1))
    assert patterns and all(p.startswith("/backend/") for p in patterns), patterns
    assert "cronSchedule" in body and "healthcheckPath" not in body
