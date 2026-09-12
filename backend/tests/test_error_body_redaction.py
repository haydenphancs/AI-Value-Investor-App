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
