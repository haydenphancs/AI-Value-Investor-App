"""A secret must never reach the CLIENT's error body — or a persisted report error.

FMP puts the API key in the query string (`fmp.py` appends `apikey=` last), and
`_make_request` re-raises the ORIGINAL httpx exception for any status outside the four it
types (401/402/429/5xx). `str()` of that exception is
`Client error '403 Forbidden' for url 'https://…/search-symbol?query=AAPL&apikey=<key>'`.

`error_response_from_exception` serialised that into `details.underlying` and `message`
verbatim, and `error_body_from_exception` writes the same blob into
`research_reports.error_message` in Supabase — where it is durable and re-served on every
later fetch of that report. The repo's `redact_secrets` was wired only into logging and
Sentry (`log_redaction.py`), never into the response path (found 2026-09-12).

Reachable path, verified by the skeptic: `GET /stocks/search` → `fmp.search_stocks` →
`_make_request("search-symbol", …)`, which is NOT inside a try, so a 403/404 propagates to
`stocks.py`'s `except Exception` → `upstream_error_response(e, …)`.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.api.error_response import (
    ErrorCode,
    classify_exception,
    error_body_from_exception,
    error_response_from_exception,
)

_KEY = "d41d8cd98f00b204e9800998ecf8427e"


def _fmp_403() -> httpx.HTTPStatusError:
    url = ("https://financialmodelingprep.com/stable/search-symbol"
           f"?query=AAPL&limit=30&apikey={_KEY}")
    resp = httpx.Response(403, request=httpx.Request("GET", url))
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        return e
    raise AssertionError("raise_for_status did not raise")


def test_the_leak_path_is_actually_reachable():
    """Anti-vacuity: this exception really does build a body, not a bare 500."""
    code, _status = classify_exception(_fmp_403())
    assert code is ErrorCode.FMP_UNAVAILABLE
    assert _KEY in str(_fmp_403()), "the fixture no longer carries a key — test is vacuous"


def test_the_api_key_never_reaches_the_client_body():
    body = json.loads(error_response_from_exception(
        _fmp_403(), ticker="AAPL", step="stock_search").body)
    blob = json.dumps(body)
    assert _KEY not in blob, f"the FMP key was serialised to the client: {blob[:300]}"
    assert "apikey=***" in body["details"]["underlying"]
    assert "apikey=***" in body["message"]


def test_the_api_key_never_reaches_the_persisted_report_error():
    blob = json.dumps(error_body_from_exception(_fmp_403(), ticker="AAPL", step="stage_a"))
    assert _KEY not in blob, "the key would be durable in research_reports.error_message"
    assert "apikey=***" in blob


def test_redaction_happens_before_truncation():
    """A `[:200]` cut applied first leaves a usable key prefix in the body."""
    pad = "x" * 150
    url = (f"https://financialmodelingprep.com/stable/{pad}?apikey={_KEY}")
    resp = httpx.Response(403, request=httpx.Request("GET", url))
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        body = json.loads(error_response_from_exception(e).body)
    blob = json.dumps(body)
    assert _KEY not in blob
    for n in (8, 12, 16):
        assert _KEY[:n] not in blob, f"a {n}-char key prefix survived truncation"


def test_an_ordinary_message_is_not_mangled():
    """Control: redaction must not eat the diagnostic text the builders exist to carry."""
    body = json.loads(error_response_from_exception(
        ValueError("No company profile found for ticker: ^GSPC"), ticker="^GSPC").body)
    assert "No company profile found for ticker: ^GSPC" in body["message"]
    assert body["details"]["ticker"] == "^GSPC"


# ── E2: the failure stamp `generate_report` writes on its own except path ─────────
#
# `_run_research_task`'s terminal CAS wrote the structured, redacted blob — but the
# service's own `except Exception` (E2) wrote `f"Research failed: {str(e)[:400]}"` FIRST,
# and the 3 s `/status` poll served that verbatim until the CAS overwrote it (or forever
# if the row was claimed away by the sweep or a delete in that window). Same FMP shape:
# the key in the URL, the vendor's class name in a Gemini failure.


class _RecordingSupabase:
    """Records every `research_reports` update payload; answers nothing else."""

    def __init__(self):
        self.updates: list = []

    def table(self, name):
        sb = self

        class _Q:
            def __init__(self):
                self.payload = None

            def update(self, payload):
                self.payload = payload
                return self

            def select(self, *a, **k):
                return self

            def eq(self, *a):
                return self

            def in_(self, *a):
                return self

            def is_(self, *a):
                return self

            def limit(self, *a):
                return self

            def execute(self):
                if self.payload is not None:
                    sb.updates.append(dict(self.payload))

                class _R:
                    data = []
                return _R()

        return _Q()


@pytest.mark.asyncio
async def test_the_e2_failure_stamp_is_structured_and_redacted(monkeypatch):
    from app.services import research_service as rs

    sb = _RecordingSupabase()
    svc = rs.ResearchService.__new__(rs.ResearchService)
    svc.supabase = sb
    svc.gemini = object()
    svc.fmp = object()

    async def _no_cache(ticker, persona_key):
        return None

    async def _boom(ticker, persona_key, run, on_started=None, before_run=None, member_id=None):
        raise _fmp_403()

    monkeypatch.setattr(svc, "_lookup_shared_cache", _no_cache)
    monkeypatch.setattr(rs, "_run_agent_deduped", _boom)

    with pytest.raises(httpx.HTTPStatusError):
        await svc.generate_report("rid-1", "AAPL", "warren_buffett", "uid-1")

    failed = [u for u in sb.updates if u.get("status") == "failed"]
    assert failed, f"no failed stamp was written: {sb.updates}"
    raw = failed[-1]["error_message"]
    assert _KEY not in raw, f"the FMP key reached research_reports.error_message: {raw[:200]}"
    assert "apikey=***" in raw
    # Structured: the splitter serves a code + user message, never the httpx string.
    from app.api.v1.endpoints.research import _split_structured_error
    code, msg = _split_structured_error(raw)
    assert code == "FMP_UNAVAILABLE", (code, msg)
    assert msg and _KEY not in msg and "Forbidden" not in msg
    body = json.loads(raw)
    assert body["details"]["ticker"] == "AAPL"
    assert body["details"]["persona"] == "warren_buffett"
    assert body["details"]["step"] == "generate_report"


@pytest.mark.asyncio
async def test_a_gemini_failure_never_names_the_vendor_on_the_status_surface(monkeypatch):
    """CLAUDE.md invariant #7 on the one surface the string builder skipped."""
    from app.services import research_service as rs
    from app.integrations.gemini import GeminiQuotaError

    sb = _RecordingSupabase()
    svc = rs.ResearchService.__new__(rs.ResearchService)
    svc.supabase = sb
    svc.gemini = object()
    svc.fmp = object()

    async def _no_cache(ticker, persona_key):
        return None

    async def _boom(ticker, persona_key, run, on_started=None, before_run=None, member_id=None):
        raise GeminiQuotaError("429 RESOURCE_EXHAUSTED: Gemini quota exceeded")

    monkeypatch.setattr(svc, "_lookup_shared_cache", _no_cache)
    monkeypatch.setattr(rs, "_run_agent_deduped", _boom)
    with pytest.raises(GeminiQuotaError):
        await svc.generate_report("rid-2", "AAPL", "warren_buffett", "uid-1")

    raw = [u for u in sb.updates if u.get("status") == "failed"][-1]["error_message"]
    from app.api.v1.endpoints.research import _split_structured_error
    code, msg = _split_structured_error(raw)
    assert code and msg
    for word in ("Gemini", "Google", "GeminiQuotaError"):
        assert word not in msg, (word, msg)


def test_update_status_redacts_at_the_choke_point():
    """Any future caller that hands `_update_status` a raw string is still safe."""
    from app.services import research_service as rs

    sb = _RecordingSupabase()
    svc = rs.ResearchService.__new__(rs.ResearchService)
    svc.supabase = sb
    svc._update_status("rid", "failed", 0, error_message=f"Research failed: {_fmp_403()}")
    assert _KEY not in sb.updates[-1]["error_message"]
    assert "apikey=***" in sb.updates[-1]["error_message"]


def test_the_status_splitter_redacts_a_legacy_plain_string():
    """Rows written before the writers redacted are served through this hop."""
    from app.api.v1.endpoints.research import _split_structured_error

    code, msg = _split_structured_error(f"Research failed: {_fmp_403()}")
    assert code is None and _KEY not in msg and "apikey=***" in msg
    # And a structured blob whose user_message somehow carried one.
    code, msg = _split_structured_error(json.dumps(
        {"error_code": "FMP_UNAVAILABLE", "user_message": f"see {_fmp_403()}"}))
    assert code == "FMP_UNAVAILABLE" and _KEY not in msg
