"""The SEC EDGAR thin client (`app/integrations/sec_edgar.py`) — hermetic, over httpx.MockTransport.

Pins the contract `services/sec_concept_service.py` codes against: the URL and headers, the
outcome split (200 dict / 404 None / 429+403 throttled with a process-wide circuit / 5xx,
408, timeout, non-JSON unavailable / other 4xx refused), the blank-User-Agent refusal before
any I/O, the process-wide limiter, CIK normalisation, and that the User-Agent (it carries a
contact address) never reaches an exception message or a log line.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import pytest

from app.api.error_response import ErrorCode, classify_exception
from app.config import settings
from app.integrations import sec_edgar
from app.integrations.sec_edgar import (
    SECConfigException,
    SECEdgarClient,
    SECException,
    SECRateLimitException,
    SECUnavailableException,
    normalize_cik,
)

UA = "Caydex Test sec-contact-test@example.com"
TAG = "PaymentsForRepurchaseOfCommonStock"
CONCEPT = {
    "cik": 320193,
    "taxonomy": "us-gaap",
    "tag": TAG,
    "units": {"USD": [{"start": "2025-03-30", "end": "2025-06-28", "val": 1, "form": "10-Q",
                       "filed": "2025-08-01"}]},
}


@pytest.fixture(autouse=True)
def _sec_state(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", UA)
    monkeypatch.setattr(settings, "SEC_EDGAR_BASE_URL", "https://data.sec.gov")
    monkeypatch.setattr(settings, "SEC_EDGAR_MAX_RPS", 1000.0)
    monkeypatch.setattr(sec_edgar, "_circuit_open_until", 0.0)
    monkeypatch.setattr(sec_edgar, "_next_request_at", 0.0)


class _Recorder:
    """A MockTransport handler that records each request and answers from `respond`."""

    def __init__(self, respond):
        self.respond = respond
        self.requests: list[httpx.Request] = []
        self.times: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.times.append(time.monotonic())
        return self.respond(request)


async def _call(monkeypatch, respond, *, cik="320193", tag=TAG):
    rec = _Recorder(respond)
    async with httpx.AsyncClient(transport=httpx.MockTransport(rec)) as http:
        monkeypatch.setattr(sec_edgar, "_client", http)
        try:
            return await SECEdgarClient().get_company_concept(cik, tag), rec
        except Exception as exc:  # returned so each test asserts on the request count too
            return exc, rec


# ── Request shape ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sends_the_user_agent_and_pads_the_cik_into_the_url(monkeypatch):
    result, rec = await _call(monkeypatch, lambda r: httpx.Response(200, json=CONCEPT))
    assert result == CONCEPT
    assert len(rec.requests) == 1
    req = rec.requests[0]
    assert str(req.url) == (
        "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/"
        "PaymentsForRepurchaseOfCommonStock.json"
    )
    assert req.method == "GET"
    assert req.headers["user-agent"] == UA
    assert "gzip" in req.headers["accept-encoding"]


@pytest.mark.asyncio
async def test_a_gzipped_body_is_decompressed(monkeypatch):
    import gzip

    body = gzip.compress(json.dumps(CONCEPT).encode())
    result, _ = await _call(
        monkeypatch,
        lambda r: httpx.Response(200, content=body, headers={"Content-Encoding": "gzip"}),
    )
    assert result == CONCEPT


@pytest.mark.asyncio
async def test_an_already_padded_cik_and_an_int_cik_build_the_same_url(monkeypatch):
    for cik in ("0000320193", 320193):
        _, rec = await _call(monkeypatch, lambda r: httpx.Response(200, json=CONCEPT), cik=cik)
        assert "/CIK0000320193/" in str(rec.requests[0].url)


@pytest.mark.asyncio
@pytest.mark.parametrize("cik", [None, "", "abc", "0", "12345678901", True])
async def test_an_invalid_cik_is_a_value_error_with_no_request(monkeypatch, cik):
    result, rec = await _call(monkeypatch, lambda r: httpx.Response(200, json=CONCEPT), cik=cik)
    assert isinstance(result, ValueError)
    assert rec.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tag", ["", "../companyfacts", "Payments/x", "1Tag", "a b", None])
async def test_an_invalid_tag_never_reaches_the_url(monkeypatch, tag):
    result, rec = await _call(monkeypatch, lambda r: httpx.Response(200, json=CONCEPT), tag=tag)
    assert isinstance(result, ValueError)
    assert rec.requests == []


# ── Outcomes ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_404_is_none_the_company_never_used_the_concept(monkeypatch):
    result, rec = await _call(monkeypatch, lambda r: httpx.Response(404, text="Not Found"))
    assert result is None
    assert len(rec.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 403])
async def test_throttling_raises_and_opens_the_circuit_with_no_further_requests(monkeypatch, status):
    result, rec = await _call(
        monkeypatch,
        lambda r: httpx.Response(status, text="slow down", headers={"Retry-After": "30"}),
    )
    assert type(result) is SECRateLimitException
    assert result.status_code == status
    assert result.retry_after == 30.0
    assert len(rec.requests) == 1

    # The circuit: the next calls refuse without any network I/O.
    for _ in range(3):
        again, rec2 = await _call(monkeypatch, lambda r: httpx.Response(200, json=CONCEPT))
        assert type(again) is SECRateLimitException
        assert again.status_code is None
        assert 0 < again.retry_after <= sec_edgar.CIRCUIT_SECONDS
        assert rec2.requests == []

    # ... until it expires.
    monkeypatch.setattr(sec_edgar, "_circuit_open_until", time.monotonic() - 1)
    ok, rec3 = await _call(monkeypatch, lambda r: httpx.Response(200, json=CONCEPT))
    assert ok == CONCEPT and len(rec3.requests) == 1


@pytest.mark.asyncio
async def test_a_429_without_a_numeric_retry_after_still_opens_the_circuit(monkeypatch):
    result, _ = await _call(
        monkeypatch,
        lambda r: httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
    )
    assert type(result) is SECRateLimitException and result.retry_after is None
    assert sec_edgar._circuit_remaining() > sec_edgar.CIRCUIT_SECONDS - 5


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503, 504, 408])
async def test_5xx_and_408_are_unavailable(monkeypatch, status):
    result, _ = await _call(monkeypatch, lambda r: httpx.Response(status, text="upstream down"))
    assert type(result) is SECUnavailableException
    assert result.status_code == status
    assert sec_edgar._circuit_remaining() <= 0, "only 429 / 403 may open the circuit"


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    httpx.ReadTimeout("read timed out"),
    httpx.ConnectTimeout("connect timed out"),
    httpx.ConnectError("connection refused"),
    httpx.RemoteProtocolError("peer closed"),
])
async def test_timeouts_and_network_errors_are_unavailable(monkeypatch, exc):
    def boom(request):
        raise exc

    result, _ = await _call(monkeypatch, boom)
    assert type(result) is SECUnavailableException
    assert result.status_code is None
    assert TAG in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    httpx.Response(200, text="<html>maintenance</html>"),
    httpx.Response(200, text=""),
    httpx.Response(200, json=[1, 2, 3]),
    httpx.Response(200, json="a string"),
    httpx.Response(200, json=None),
])
async def test_a_200_whose_body_is_not_a_json_object_is_unavailable(monkeypatch, response):
    result, _ = await _call(monkeypatch, lambda r: response)
    assert type(result) is SECUnavailableException
    assert result.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 405, 410, 413])
async def test_other_4xx_are_a_plain_sec_exception(monkeypatch, status):
    result, _ = await _call(monkeypatch, lambda r: httpx.Response(status, text="bad"))
    assert type(result) is SECException
    assert result.status_code == status
    assert sec_edgar._circuit_remaining() <= 0


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", "   "])
async def test_a_blank_user_agent_refuses_before_any_request(monkeypatch, blank):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", blank)
    result, rec = await _call(monkeypatch, lambda r: httpx.Response(200, json=CONCEPT))
    assert type(result) is SECConfigException
    assert rec.requests == []


# A value httpx cannot send. The em dash made httpx raise an UNTYPED UnicodeEncodeError out of
# `.get()` (four `[sec-concept-unexpected]` ERRORs per build, the chart never persisted); a
# control character reached h11, whose error repr carried the contact address past `_scrub`.
_UNSENDABLE_USER_AGENTS = [
    "Caydex — sec-contact-test@example.com",   # em dash: non-ASCII
    "Caydex Test séc-contact-test@example.com",  # accented letter: non-ASCII
    "Caydex\nsec-contact-test@example.com",          # internal newline
    "Caydex\tsec-contact-test@example.com",          # tab: not printable
    "Caydex\x00sec-contact-test@example.com",        # NUL
    "Caydex\x7fsec-contact-test@example.com",        # DEL
]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", _UNSENDABLE_USER_AGENTS)
async def test_an_unsendable_user_agent_is_a_config_refusal_before_any_request(monkeypatch, bad):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", bad)
    result, rec = await _call(monkeypatch, lambda r: httpx.Response(200, json=CONCEPT))
    assert type(result) is SECConfigException, result
    assert rec.requests == []
    assert "sec-contact-test@example.com" not in str(result)
    assert sec_edgar._circuit_remaining() <= 0  # a config mistake is not throttling


@pytest.mark.parametrize("value, expected", [
    (UA, True),
    ("  " + UA + "  ", True),   # outer whitespace is stripped before sending
    ("Caydex/1.0 (ops@example.com)", True),
    ("", False),
    ("   ", False),
    (None, False),
    (123, False),
    *[(bad, False) for bad in _UNSENDABLE_USER_AGENTS],
])
def test_user_agent_configured_is_the_one_shared_predicate(monkeypatch, value, expected):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", value)
    assert sec_edgar.user_agent_configured() is expected


@pytest.mark.asyncio
async def test_a_200_nested_too_deep_to_parse_is_unavailable_not_a_recursion_error(monkeypatch):
    deep = b"[" * 100_000 + b"]" * 100_000
    result, rec = await _call(monkeypatch, lambda r: httpx.Response(200, content=deep))
    assert type(result) is SECUnavailableException, result
    assert result.status_code == 200
    assert isinstance(result.__cause__, RecursionError)
    assert len(rec.requests) == 1


@pytest.mark.asyncio
async def test_the_exception_hierarchy_is_typed():
    assert issubclass(SECRateLimitException, SECException)
    assert issubclass(SECUnavailableException, SECException)
    assert issubclass(SECConfigException, SECException)
    assert SECException("x").status_code is None
    assert SECException("x", status_code=418).status_code == 418


# ── The limiter ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_limiter_spaces_sequential_calls_by_one_over_rps(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_MAX_RPS", 20.0)  # 50 ms apart
    rec = _Recorder(lambda r: httpx.Response(200, json=CONCEPT))
    async with httpx.AsyncClient(transport=httpx.MockTransport(rec)) as http:
        monkeypatch.setattr(sec_edgar, "_client", http)
        client = SECEdgarClient()
        await client.get_company_concept("320193", TAG)
        await client.get_company_concept("320193", TAG)
    assert len(rec.times) == 2
    assert rec.times[1] - rec.times[0] >= 0.045, rec.times


@pytest.mark.asyncio
async def test_the_limiter_spaces_concurrent_calls_too(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_MAX_RPS", 20.0)
    rec = _Recorder(lambda r: httpx.Response(200, json=CONCEPT))
    async with httpx.AsyncClient(transport=httpx.MockTransport(rec)) as http:
        monkeypatch.setattr(sec_edgar, "_client", http)
        client = SECEdgarClient()
        await asyncio.gather(*(client.get_company_concept("320193", TAG) for _ in range(4)))
    gaps = [b - a for a, b in zip(sorted(rec.times), sorted(rec.times)[1:])]
    assert len(gaps) == 3 and min(gaps) >= 0.045, gaps


@pytest.mark.asyncio
async def test_the_limiter_is_process_wide_across_client_instances(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_MAX_RPS", 20.0)
    rec = _Recorder(lambda r: httpx.Response(404))
    async with httpx.AsyncClient(transport=httpx.MockTransport(rec)) as http:
        monkeypatch.setattr(sec_edgar, "_client", http)
        await asyncio.gather(
            SECEdgarClient().get_company_concept("320193", TAG),
            SECEdgarClient().get_company_concept("320193", TAG),
        )
    assert abs(rec.times[1] - rec.times[0]) >= 0.045, rec.times


# ── normalize_cik ───────────────────────────────────────────────────────


@pytest.mark.parametrize("value, expected", [
    ("320193", "0000320193"),
    ("0000320193", "0000320193"),
    (320193, "0000320193"),
    (50863, "0000050863"),
    ("  50863 ", "0000050863"),
    ("1", "0000000001"),
    ("9999999999", "9999999999"),
    (None, None),
    ("", None),
    ("   ", None),
    (True, None),
    (False, None),
    ("abc", None),
    ("CIK0000320193", None),
    ("32019a", None),
    ("320193.0", None),
    ("-320193", None),
    (-320193, None),
    (0, None),
    ("0", None),
    ("0000000000", None),
    ("12345678901", None),
    (12345678901, None),
    ("00000000320193", None),
    ("²³", None),           # Unicode digits pass str.isdigit; a CIK is ASCII
    (320193.0, None),
    ([320193], None),
])
def test_normalize_cik(value, expected):
    assert normalize_cik(value) == expected


# ── The User-Agent never leaks ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_user_agent_never_appears_in_an_exception_message_or_a_log(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="app.integrations.sec_edgar")
    echo = f"request from {UA} refused"

    def raise_with_ua(request):
        raise httpx.ConnectError(f"cannot connect for {UA}")

    cases = [
        lambda r: httpx.Response(400, text=echo),
        lambda r: httpx.Response(500, text=echo),
        lambda r: httpx.Response(200, text=echo),
        raise_with_ua,
        lambda r: httpx.Response(403, text=echo),
    ]
    messages = []
    for respond in cases:
        monkeypatch.setattr(sec_edgar, "_circuit_open_until", 0.0)
        result, _ = await _call(monkeypatch, respond)
        assert isinstance(result, SECException), result
        messages.append(str(result))
    # the circuit refusal (the last case left it open) and the config refusal
    circuit, _ = await _call(monkeypatch, lambda r: httpx.Response(200, json=CONCEPT))
    messages.append(str(circuit))
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", " ")
    config, _ = await _call(monkeypatch, lambda r: httpx.Response(200, json=CONCEPT))
    messages.append(str(config))

    for message in messages:
        assert UA not in message and "sec-contact-test@example.com" not in message, message
    assert any("<user-agent>" in m for m in messages), "the echoed body is scrubbed, not dropped"
    for record in caplog.records:
        text = record.getMessage()
        assert "sec-contact-test@example.com" not in text, text


# ── Teardown ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_sec_edgar_client_closes_and_resets_the_shared_client(monkeypatch):
    monkeypatch.setattr(sec_edgar, "_client", None)
    http = sec_edgar._get_http_client()
    assert sec_edgar._get_http_client() is http, "one shared client"
    await sec_edgar.close_sec_edgar_client()
    assert http.is_closed and sec_edgar._client is None
    await sec_edgar.close_sec_edgar_client()  # idempotent


def test_get_sec_edgar_client_is_a_singleton():
    assert sec_edgar.get_sec_edgar_client() is sec_edgar.get_sec_edgar_client()


# ── classify_exception ──────────────────────────────────────────────────


@pytest.mark.parametrize("exc", [
    SECException("SEC EDGAR: HTTP 400", status_code=400),
    SECRateLimitException("SEC EDGAR: HTTP 429 rate limit", status_code=429),
    SECUnavailableException("SEC EDGAR: request timed out"),
    SECConfigException("SEC EDGAR: SEC_EDGAR_USER_AGENT is not set"),
])
def test_every_sec_exception_classifies_as_a_data_source_outage(exc):
    # "HTTP 429" / "timed out" in the message must not send these to the FMP heuristics.
    assert classify_exception(exc) == (ErrorCode.FMP_UNAVAILABLE, 502)


def test_the_sec_branch_does_not_capture_unrelated_class_names():
    class SecurityException(Exception):
        pass

    class SECTORException(Exception):
        pass

    class SecretsException(Exception):
        pass

    for cls in (SecurityException, SECTORException, SecretsException):
        code, _ = classify_exception(cls("boom"))
        assert code == ErrorCode.REPORT_GENERATION_FAILED, (cls.__name__, code)
