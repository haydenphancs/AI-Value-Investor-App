"""EDGAR full-text search (`SECEdgarClient.full_text_search`) and the stake finder's larger
document cap (`get_filing_document(max_bytes=)`) in `app/integrations/sec_edgar.py` — hermetic,
over httpx.MockTransport, in the style of `test_sec_edgar_submissions.py`.

Pins:
* the request: efts.sec.gov/LATEST/search-index with httpx `params` (q, dateRange=custom,
  startdt, enddt; forms / ciks only when given, CIKs ten-digit; `from` only past page one),
  built from the stake finder's recorded query (MANIFEST.json) whose echo is in the fixture;
* every ValueError BEFORE any I/O — above all the two traps EFTS answers with 0 hits and no
  error: a parenthesis, and a "/A" form beside the root forms;
* the outcome split: 200 object → returned; 200 non-object → unavailable (no retry); a 5xx,
  408 or transport error → ONE retry, then unavailable; other 4xx (404 included) → refused;
* the circuits: a 429 / 403 opens ONLY full-text search's own circuit (the Learn reads and the
  buyback check keep working), while the shared circuit, once open, refuses searches too;
* the 4 MiB answer cap (declared, streamed, gzip-decoded); the query text only at DEBUG; the
  User-Agent never in a message;
* `get_filing_document`: `max_bytes` keyword-only in [1, 12 MiB] and used by BOTH size checks,
  None keeps the 3 MiB cap, and the document name must FULLY match (a trailing newline is
  refused).
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import time
from datetime import date, datetime
from pathlib import Path

import httpx
import pytest

from app.api.error_response import ErrorCode, classify_exception
from app.config import settings
from app.integrations import sec_edgar
from app.integrations.sec_edgar import (
    EFTS_CIRCUIT_SECONDS,
    EFTS_MAX_WINDOW,
    EFTS_PAGE_SIZE,
    MAX_DISCOVERY_DOCUMENT_BYTES,
    MAX_DOCUMENT_BYTES,
    MAX_EFTS_BYTES,
    SEC_EFTS_URL,
    SECConfigException,
    SECEdgarClient,
    SECEdgarDocumentTooLargeException,
    SECEdgarFullTextThrottled,
    SECException,
    SECRateLimitException,
    SECUnavailableException,
)

UA = "Caydex Test sec-contact-test@example.com"
CIK = "1045810"
ACC = "0001045810-25-000023"
DOC = "q4fy25pr.htm"
HTML = "<html><body><p>NVIDIA announced results.</p></body></html>"

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "trillion_club_stakes"
_MANIFEST = json.loads((_FIXTURES / "MANIFEST.json").read_text(encoding="utf-8"))
#: A real EFTS answer the stake finder's query got on 2026-10-28 (Meta's 424B5s among the hits).
RECORDED = json.loads((_FIXTURES / "json" / "efts_nvidia_2026_10.json").read_text(encoding="utf-8"))
_META = _MANIFEST["efts"]["efts_nvidia_2026_10"]
QUERY = _META["q"]
FORMS = tuple(_META["forms"].split(","))
START = date.fromisoformat(_META["start"])
END = date.fromisoformat(_META["end"])
SUBMISSIONS = {"name": "NVIDIA CORP", "filings": {"recent": {}}}


@pytest.fixture(autouse=True)
def _sec_state(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", UA)
    monkeypatch.setattr(settings, "SEC_EDGAR_BASE_URL", "https://data.sec.gov")
    monkeypatch.setattr(settings, "SEC_EDGAR_MAX_RPS", 1000.0)
    monkeypatch.setattr(sec_edgar, "_circuit_open_until", 0.0)
    monkeypatch.setattr(sec_edgar, "_efts_circuit_open_until", 0.0)
    monkeypatch.setattr(sec_edgar, "_next_request_at", 0.0)
    monkeypatch.setattr(sec_edgar, "_EFTS_RETRY_DELAY", 0.0)


class _Recorder:
    def __init__(self, respond):
        self.respond = respond
        self.requests: list[httpx.Request] = []
        self.times: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.times.append(time.monotonic())
        return self.respond(request)


async def _run(monkeypatch, respond, call):
    rec = _Recorder(respond)
    async with httpx.AsyncClient(transport=httpx.MockTransport(rec)) as http:
        monkeypatch.setattr(sec_edgar, "_client", http)
        try:
            return await call(SECEdgarClient()), rec
        except Exception as exc:  # returned so each test asserts on the request count too
            return exc, rec


def _search(query=QUERY, **kw):
    kw.setdefault("start", START)
    kw.setdefault("end", END)
    kw.setdefault("forms", FORMS)
    return lambda c: c.full_text_search(query, **kw)


def _document(cik=CIK, acc=ACC, doc=DOC, **kw):
    return lambda c: c.get_filing_document(cik, acc, doc, **kw)


def _ok(request):
    return httpx.Response(200, json=RECORDED)


def _sequence(*answers):
    """A responder answering each request with the next item: a status, a Response, or an
    exception to raise (a transport error)."""
    queue = list(answers)

    def respond(request):
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, httpx.Response):
            return item
        if item == 200:
            return httpx.Response(200, json=RECORDED)
        return httpx.Response(item, text=f"status {item}")

    return respond


def _by_host(efts, other):
    """EFTS answered by `efts`, every other SEC host by `other`."""
    return lambda r: efts(r) if r.url.host == "efts.sec.gov" else other(r)


def _efts_circuit() -> float:
    return sec_edgar._efts_circuit_remaining()


def _shared_circuit() -> float:
    return sec_edgar._circuit_remaining()


# ── The request ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_url_params_and_headers_from_the_recorded_query(monkeypatch):
    result, rec = await _run(monkeypatch, _ok, _search())
    assert result == RECORDED
    assert len(rec.requests) == 1
    req = rec.requests[0]
    assert req.method == "GET"
    assert (req.url.scheme, req.url.host, req.url.path) == ("https", "efts.sec.gov",
                                                             "/LATEST/search-index")
    assert str(req.url).startswith(SEC_EFTS_URL + "?")
    assert sorted(req.url.params.keys()) == ["dateRange", "enddt", "forms", "q", "startdt"]
    assert req.url.params["q"] == QUERY
    assert req.url.params["dateRange"] == "custom"
    assert req.url.params["startdt"] == "2026-10-01"
    assert req.url.params["enddt"] == "2026-10-28"
    assert req.url.params["forms"] == "8-K,6-K,424B3,424B4,424B5,424B7,S-1,F-1"
    assert req.headers["user-agent"] == UA
    assert "gzip" in req.headers["accept-encoding"]
    assert req.headers["accept"] == "application/json"
    # The recorded answer was produced by exactly these parameters: its echo filters on the
    # same root forms and dates, and the query has no parenthesis for EFTS to read literally.
    bool_q = RECORDED["query"]["query"]["bool"]
    assert bool_q["filter"][0]["terms"]["root_forms"] == list(FORMS)
    assert bool_q["filter"][1]["range"]["file_date"] == {"gte": "2026-10-01", "lte": "2026-10-28"}
    assert "(" not in QUERY and ")" not in QUERY


@pytest.mark.asyncio
async def test_ciks_are_sent_ten_digit_whatever_form_they_came_in(monkeypatch):
    _, rec = await _run(monkeypatch, _ok,
                        _search(ciks=[1326801, "0001045810", " 320193 ", "2488"]))
    assert rec.requests[0].url.params["ciks"] == "0001326801,0001045810,0000320193,0000002488"


@pytest.mark.asyncio
@pytest.mark.parametrize("offset, sent", [(0, None), (EFTS_PAGE_SIZE, "100"), (500, "500"),
                                          (EFTS_MAX_WINDOW - EFTS_PAGE_SIZE, "9900")])
async def test_offset_is_sent_as_from_only_past_the_first_page(monkeypatch, offset, sent):
    result, rec = await _run(monkeypatch, _ok, _search(offset=offset))
    assert result == RECORDED
    assert rec.requests[0].url.params.get("from") == sent


@pytest.mark.asyncio
async def test_no_forms_and_no_ciks_leave_both_parameters_out(monkeypatch):
    _, rec = await _run(monkeypatch, _ok, _search(forms=(), ciks=[]))
    assert sorted(rec.requests[0].url.params.keys()) == ["dateRange", "enddt", "q", "startdt"]


@pytest.mark.asyncio
async def test_edges_that_are_valid(monkeypatch):
    # One-day window; a form with a space; a query of exactly 1000 characters; one character.
    day = date(2026, 10, 26)
    long_query = '"NVIDIA Corporation" ' + "x" * (1000 - 21)
    assert len(long_query) == 1000
    for call in (_search(start=day, end=day, forms=["SCHEDULE 13D", "8-K"]),
                 _search(query=long_query), _search(query="x"), _search(forms=("424B5",))):
        result, rec = await _run(monkeypatch, _ok, call)
        assert result == RECORDED and len(rec.requests) == 1
    _, rec = await _run(monkeypatch, _ok, _search(start=day, end=day, forms=["SCHEDULE 13D"]))
    params = rec.requests[0].url.params
    assert params["forms"] == "SCHEDULE 13D"
    assert params["startdt"] == params["enddt"] == "2026-10-26"


@pytest.mark.asyncio
async def test_a_gzip_answer_is_decompressed(monkeypatch):
    body = gzip.compress(json.dumps(RECORDED).encode())
    result, _ = await _run(
        monkeypatch,
        lambda r: httpx.Response(200, content=body, headers={"Content-Encoding": "gzip"}),
        _search(),
    )
    assert result == RECORDED


@pytest.mark.asyncio
async def test_a_utf8_bom_before_the_json_is_not_an_outage(monkeypatch):
    body = b"\xef\xbb\xbf" + json.dumps(RECORDED).encode()
    result, _ = await _run(monkeypatch, lambda r: httpx.Response(200, content=body), _search())
    assert result == RECORDED


# ── Every argument is validated before any I/O ──────────────────────────


_BAD_ARGUMENTS = [
    # the query
    {"query": None}, {"query": 5}, {"query": b'"NVIDIA Corporation"'},
    {"query": ""}, {"query": "   "}, {"query": "x" * 1001},
    {"query": '"NVIDIA Corporation" — "investment agreement"'},   # non-ASCII (em dash)
    {"query": '"NVIDIA Corporation"\n"investment agreement"'},    # control character
    {"query": '"NVIDIA Corporation"\t"investment agreement"'},
    {"query": '"NVIDIA Corporation" ("investment agreement" OR "subscription agreement")'},
    {"query": '"NVIDIA Corporation" "investment agreement")'},
    {"query": '"NVIDIA Corporation" "investment agreement'},       # unclosed phrase
    {"query": '"NVIDIA Corporation" ""'},                          # empty phrase
    {"query": '"NVIDIA Corporation" "   " OR "x"'},
    # the dates
    {"start": datetime(2026, 10, 1)}, {"end": datetime(2026, 10, 28, 23, 59)},
    {"start": "2026-10-01"}, {"end": None}, {"start": 20261001},
    {"start": date(2026, 10, 29), "end": date(2026, 10, 28)},
    # the forms
    # A bare str is a Sequence too: "424B5" would go out as the forms 4, 2, 4, B, 5 (each one
    # passes the form rule) and "1326811" as seven one-digit CIKs — silently 0 hits.
    {"forms": "424B5"}, {"forms": "8-K"},
    {"forms": b"8-K"}, {"forms": {"8-K"}}, {"forms": None},
    {"forms": ["8-K", "8-K/A"]},            # an amendment beside its root: 0 hits, no error
    {"forms": ["SC 13D/A"]}, {"forms": ["8-k"]}, {"forms": ["8-K,6-K"]}, {"forms": [" 8-K"]},
    {"forms": ["8-K "]}, {"forms": ["SC  13D"]}, {"forms": ["8-"]},   # EFTS matches exactly
    {"forms": [""]}, {"forms": [None]}, {"forms": [8]},
    {"forms": ["X" * 21]}, {"forms": ["8-K\n"]}, {"forms": ("8-K",) * 51},
    # the CIKs
    {"ciks": "1326811"}, {"ciks": "1326801"}, {"ciks": [0]}, {"ciks": ["abc"]}, {"ciks": [True]},
    {"ciks": [12345678901]}, {"ciks": [None]}, {"ciks": ["CIK0001326801"]},
    {"ciks": [1326801] * 101},
    # the offset
    {"offset": -100}, {"offset": 50}, {"offset": EFTS_MAX_WINDOW},
    {"offset": EFTS_MAX_WINDOW + EFTS_PAGE_SIZE}, {"offset": True}, {"offset": "100"},
    {"offset": 100.0}, {"offset": None},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", _BAD_ARGUMENTS, ids=[repr(b)[:60] for b in _BAD_ARGUMENTS])
async def test_an_invalid_argument_is_a_value_error_with_no_request(monkeypatch, bad):
    kw = dict(bad)
    query = kw.pop("query", QUERY)
    result, rec = await _run(monkeypatch, _ok, _search(query=query, **kw))
    assert type(result) is ValueError, result
    assert rec.requests == []
    assert _efts_circuit() <= 0 and _shared_circuit() <= 0


@pytest.mark.asyncio
async def test_validation_comes_before_the_config_and_circuit_refusals(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "")
    sec_edgar._open_efts_circuit()
    result, rec = await _run(monkeypatch, _ok, _search(query='"NVIDIA Corporation" (x)'))
    assert type(result) is ValueError and rec.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "   ", "Caydex — sec-contact-test@example.com",
                                 "Caydex\nsec-contact-test@example.com"])
async def test_an_unusable_user_agent_refuses_before_any_request_and_before_the_circuit(
        monkeypatch, bad):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", bad)
    sec_edgar._open_efts_circuit()
    result, rec = await _run(monkeypatch, _ok, _search())
    assert type(result) is SECConfigException and rec.requests == []
    assert "sec-contact-test@example.com" not in str(result)


# ── Outcomes ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    httpx.Response(200, text="<html>maintenance</html>"),
    httpx.Response(200, text=""),
    httpx.Response(200, json=[1, 2]),
    httpx.Response(200, json="a string"),
    httpx.Response(200, json=None),
    httpx.Response(200, content=b"\xff\xfe not utf-8"),
    httpx.Response(200, content=b"[" * 100_000 + b"]" * 100_000),
])
async def test_a_200_that_is_not_a_json_object_is_unavailable_and_not_retried(monkeypatch,
                                                                               response):
    result, rec = await _run(monkeypatch, lambda r: response, _search())
    assert type(result) is SECUnavailableException and result.status_code == 200
    assert len(rec.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 404, 405, 410, 413, 414])
async def test_other_4xx_are_a_plain_refusal_with_no_retry_and_no_circuit(monkeypatch, status):
    result, rec = await _run(monkeypatch, lambda r: httpx.Response(status, text="bad"),
                             _search())
    assert type(result) is SECException and result.status_code == status
    assert len(rec.requests) == 1
    assert _efts_circuit() <= 0 and _shared_circuit() <= 0


@pytest.mark.asyncio
async def test_an_unexpected_status_is_unavailable_and_not_retried(monkeypatch):
    result, rec = await _run(
        monkeypatch, lambda r: httpx.Response(302, headers={"Location": "https://x"}), _search()
    )
    assert type(result) is SECUnavailableException and result.status_code == 302
    assert len(rec.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("first", [500, 502, 503, 504, 408, httpx.ReadTimeout("t"),
                                   httpx.ConnectError("refused"),
                                   httpx.RemoteProtocolError("peer closed")])
async def test_one_transient_failure_is_retried_once_and_the_retry_answers(monkeypatch, caplog,
                                                                         first):
    caplog.set_level(logging.WARNING, logger="app.integrations.sec_edgar")
    result, rec = await _run(monkeypatch, _sequence(first, 200), _search())
    assert result == RECORDED
    assert len(rec.requests) == 2
    assert rec.requests[0].url == rec.requests[1].url, "the retry is the same request"
    assert any("[sec-efts-retry]" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("answers, status", [
    ((500, 500), 500), ((503, 502), 502), ((408, 408), 408), ((500, 408), 408),
    ((httpx.ConnectError("refused"), httpx.ReadTimeout("t")), None),
    ((503, httpx.ConnectError("refused")), None),
    ((httpx.ReadTimeout("t"), 503), 503),
])
async def test_two_transient_failures_are_unavailable_after_exactly_one_retry(monkeypatch,
                                                                            answers, status):
    result, rec = await _run(monkeypatch, _sequence(*answers), _search())
    assert type(result) is SECUnavailableException, result
    assert result.status_code == status
    assert len(rec.requests) == 2, "one retry, never more"
    assert "after one retry" in str(result)
    assert _efts_circuit() <= 0 and _shared_circuit() <= 0, "a 5xx is not throttling"


@pytest.mark.asyncio
async def test_a_transport_failure_after_the_retry_keeps_its_cause(monkeypatch):
    result, _ = await _run(
        monkeypatch, _sequence(httpx.ConnectError("a"), httpx.ConnectError("b")), _search()
    )
    assert type(result) is SECUnavailableException
    assert isinstance(result.__cause__, httpx.ConnectError)


@pytest.mark.asyncio
async def test_the_retry_answer_decides_when_it_is_not_transient(monkeypatch):
    throttled, rec = await _run(monkeypatch, _sequence(500, 429), _search())
    assert type(throttled) is SECEdgarFullTextThrottled and len(rec.requests) == 2
    assert _efts_circuit() > 0 and _shared_circuit() <= 0

    monkeypatch.setattr(sec_edgar, "_efts_circuit_open_until", 0.0)
    refused, rec = await _run(monkeypatch, _sequence(503, 400), _search())
    assert type(refused) is SECException and refused.status_code == 400
    assert len(rec.requests) == 2

    junk, rec = await _run(monkeypatch, _sequence(503, httpx.Response(200, text="oops")),
                           _search())
    assert type(junk) is SECUnavailableException and junk.status_code == 200
    assert len(rec.requests) == 2


@pytest.mark.asyncio
async def test_the_retry_waits_the_retry_delay(monkeypatch):
    monkeypatch.setattr(sec_edgar, "_EFTS_RETRY_DELAY", 0.06)
    result, rec = await _run(monkeypatch, _sequence(503, 200), _search())
    assert result == RECORDED
    assert rec.times[1] - rec.times[0] >= 0.055, rec.times


@pytest.mark.asyncio
async def test_the_retry_refuses_when_a_circuit_opened_while_it_waited(monkeypatch):
    def efts_then_throttled_elsewhere(request):
        # Another search got a 429 while this one was failing: its retry must not go out.
        sec_edgar._open_efts_circuit()
        return httpx.Response(503, text="busy")

    result, rec = await _run(monkeypatch, efts_then_throttled_elsewhere, _search())
    assert type(result) is SECEdgarFullTextThrottled and result.status_code is None
    assert len(rec.requests) == 1

    monkeypatch.setattr(sec_edgar, "_efts_circuit_open_until", 0.0)

    def shared_throttled_elsewhere(request):
        sec_edgar._open_circuit()   # a Learn read was refused meanwhile: every SEC read stops
        return httpx.Response(503, text="busy")

    result, rec = await _run(monkeypatch, shared_throttled_elsewhere, _search())
    assert type(result) is SECRateLimitException and len(rec.requests) == 1


# ── The circuits ────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 403])
async def test_a_search_throttle_opens_only_the_full_text_circuit(monkeypatch, caplog, status):
    caplog.set_level(logging.WARNING, logger="app.integrations.sec_edgar")
    result, rec = await _run(
        monkeypatch,
        lambda r: httpx.Response(status, text="slow down", headers={"Retry-After": "30"}),
        _search(),
    )
    assert type(result) is SECEdgarFullTextThrottled
    assert isinstance(result, SECRateLimitException) and isinstance(result, SECException)
    assert result.status_code == status and result.retry_after == 30.0
    assert len(rec.requests) == 1, "a throttle is never retried"
    assert _efts_circuit() > EFTS_CIRCUIT_SECONDS - 5
    assert _shared_circuit() <= 0, "the Learn reads and the buyback check stay open"
    assert any("[sec-efts-throttled]" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)

    # Every later search refuses with no network I/O ...
    for _ in range(2):
        again, rec2 = await _run(monkeypatch, _ok, _search())
        assert type(again) is SECEdgarFullTextThrottled
        assert again.status_code is None and 0 < again.retry_after <= EFTS_CIRCUIT_SECONDS
        assert rec2.requests == []

    # ... while every other SEC read still goes out and answers.
    other = _by_host(_ok, lambda r: httpx.Response(200, json=SUBMISSIONS)
                     if "submissions" in r.url.path else
                     httpx.Response(200, json={"units": {}}) if "companyconcept" in r.url.path
                     else httpx.Response(200, text=HTML))
    for call, expected in ((lambda c: c.get_submissions(CIK), SUBMISSIONS),
                           (lambda c: c.get_company_concept(CIK, "Revenues"), {"units": {}}),
                           (_document(), HTML)):
        ok, rec3 = await _run(monkeypatch, other, call)
        assert ok == expected and len(rec3.requests) == 1

    # ... until the search circuit expires.
    monkeypatch.setattr(sec_edgar, "_efts_circuit_open_until", time.monotonic() - 1)
    back, rec4 = await _run(monkeypatch, _ok, _search())
    assert back == RECORDED and len(rec4.requests) == 1


@pytest.mark.asyncio
async def test_a_throttle_without_a_numeric_retry_after_still_opens_the_search_circuit(
        monkeypatch):
    result, _ = await _run(
        monkeypatch,
        lambda r: httpx.Response(429, headers={"Retry-After": "Wed, 28 Oct 2026 07:28:00 GMT"}),
        _search(),
    )
    assert type(result) is SECEdgarFullTextThrottled and result.retry_after is None
    assert _efts_circuit() > EFTS_CIRCUIT_SECONDS - 5 and _shared_circuit() <= 0


@pytest.mark.asyncio
async def test_the_shared_circuit_refuses_searches_too(monkeypatch):
    monkeypatch.setattr(sec_edgar, "_circuit_open_until", time.monotonic() + 120)
    result, rec = await _run(monkeypatch, _ok, _search())
    # A plain SECRateLimitException: every SEC read is off, not only search.
    assert type(result) is SECRateLimitException
    assert 0 < result.retry_after <= 120
    assert rec.requests == []
    assert _efts_circuit() <= 0, "a shared refusal does not open the search circuit"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 403])
async def test_a_learn_or_buyback_throttle_closes_search(monkeypatch, status):
    result, _ = await _run(monkeypatch, lambda r: httpx.Response(status),
                           lambda c: c.get_company_concept(CIK, "Revenues"))
    assert type(result) is SECRateLimitException
    again, rec = await _run(monkeypatch, _ok, _search())
    assert type(again) is SECRateLimitException and rec.requests == []


# ── The answer cap ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_answer_declared_too_large_is_refused_without_a_retry(monkeypatch):
    declared = MAX_EFTS_BYTES + 1
    result, rec = await _run(
        monkeypatch,
        lambda r: httpx.Response(200, content=b"{}", headers={"Content-Length": str(declared)}),
        _search(),
    )
    assert type(result) is SECEdgarDocumentTooLargeException
    assert result.limit_bytes == MAX_EFTS_BYTES and result.size_bytes == declared
    assert len(rec.requests) == 1
    assert _efts_circuit() <= 0 and _shared_circuit() <= 0


@pytest.mark.asyncio
async def test_an_answer_streamed_past_the_cap_is_abandoned(monkeypatch):
    chunk = b" " * 65536
    yielded = []

    async def body():
        yield b'{"pad": "'
        for _ in range(MAX_EFTS_BYTES // len(chunk) + 50):
            yielded.append(1)
            yield chunk

    result, rec = await _run(monkeypatch, lambda r: httpx.Response(200, content=body()),
                             _search())
    assert type(result) is SECEdgarDocumentTooLargeException
    assert result.size_bytes is None and result.limit_bytes == MAX_EFTS_BYTES
    assert len(yielded) <= MAX_EFTS_BYTES // len(chunk) + 2, "abandoned at the cap"
    assert len(rec.requests) == 1


@pytest.mark.asyncio
async def test_a_gzip_bomb_answer_is_measured_decoded(monkeypatch):
    bomb = gzip.compress(b'{"pad": "' + b" " * (MAX_EFTS_BYTES + 1024) + b'"}')
    assert len(bomb) < 100_000
    result, _ = await _run(
        monkeypatch,
        lambda r: httpx.Response(200, content=bomb, headers={"Content-Encoding": "gzip"}),
        _search(),
    )
    assert type(result) is SECEdgarDocumentTooLargeException


@pytest.mark.asyncio
async def test_an_answer_exactly_at_the_cap_is_read(monkeypatch):
    head, tail = b'{"pad": "', b'"}'
    body = head + b"x" * (MAX_EFTS_BYTES - len(head) - len(tail)) + tail
    assert len(body) == MAX_EFTS_BYTES

    async def streamed():  # no Content-Length: only the streaming count decides
        for i in range(0, len(body), 1 << 20):
            yield body[i:i + (1 << 20)]

    for content in (body, streamed()):
        result, _ = await _run(monkeypatch, lambda r, c=content: httpx.Response(200, content=c),
                               _search())
        assert isinstance(result, dict) and len(result["pad"]) == MAX_EFTS_BYTES - 11


# ── Hygiene: the query text, the User-Agent, classification ─────────────


@pytest.mark.asyncio
async def test_the_query_text_reaches_debug_logs_only(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="app.integrations.sec_edgar")
    marker = "Zyxquorbleplex"
    query = f'"{marker} Holdings Inc" "investment agreement"'
    shapes = [
        _sequence(503, 503),                                   # retry WARNING + message
        _sequence(429),                                        # throttled WARNING + message
        _sequence(400),
        _sequence(httpx.Response(200, text="not json")),
        _sequence(httpx.Response(200, content=b"{}", headers={"Content-Length": "999999999"})),
        _sequence(httpx.ConnectError(f"refused for {marker}?"), 503),
    ]
    messages = []
    for respond in shapes:
        monkeypatch.setattr(sec_edgar, "_efts_circuit_open_until", 0.0)
        result, _ = await _run(monkeypatch, respond, _search(query=query))
        assert isinstance(result, SECException), result
        messages.append(str(result))
    sec_edgar._open_efts_circuit()
    circuit, rec = await _run(monkeypatch, _ok, _search(query=query))
    assert type(circuit) is SECEdgarFullTextThrottled and rec.requests == []
    messages.append(str(circuit))

    for message in messages:
        assert "Holdings Inc" not in message, message
        assert "q#" in message, "the search is still named, by its digest"
    loud = [r.getMessage() for r in caplog.records if r.levelno >= logging.INFO]
    assert loud and not any("Holdings Inc" in m for m in loud), loud
    quiet = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any(marker in m for m in quiet), "anti-vacuity: the DEBUG line carries the query"


@pytest.mark.asyncio
async def test_the_digest_names_one_query_and_differs_between_queries(monkeypatch):
    names = []
    for query in (QUERY, QUERY, '"Eli Lilly and Company" "investment agreement"'):
        result, _ = await _run(monkeypatch, lambda r: httpx.Response(400), _search(query=query))
        names.append(str(result).split("q#", 1)[1].split(" ", 1)[0])
    assert names[0] == names[1] != names[2]
    assert all(len(n) == 12 for n in names)


@pytest.mark.asyncio
@pytest.mark.parametrize("status, expected", [(500, SECUnavailableException), (400, SECException)])
async def test_the_user_agent_never_reaches_a_message_and_the_excerpt_is_bounded(
        monkeypatch, caplog, status, expected):
    caplog.set_level(logging.DEBUG, logger="app.integrations.sec_edgar")
    echo = f"refused for {UA} " + "z" * 100_000
    result, _ = await _run(monkeypatch, lambda r: httpx.Response(status, text=echo), _search())
    assert type(result) is expected
    assert UA not in str(result) and "sec-contact-test@example.com" not in str(result)
    assert "<user-agent>" in str(result), "scrubbed, not dropped"
    assert len(str(result)) < 700
    for record in caplog.records:
        assert "sec-contact-test@example.com" not in record.getMessage()


def test_the_throttle_exception_is_typed_and_classifies_as_a_data_source_outage():
    exc = SECEdgarFullTextThrottled("SEC EDGAR: HTTP 429 for EFTS search", status_code=429,
                                    retry_after=30.0)
    assert isinstance(exc, SECRateLimitException) and isinstance(exc, SECException)
    assert exc.status_code == 429 and exc.retry_after == 30.0
    # The "SECEdgar" prefix routes it by name, ahead of the "HTTP 429" message heuristics.
    assert classify_exception(exc) == (ErrorCode.FMP_UNAVAILABLE, 502)
    assert "SECEdgarFullTextThrottled" in sec_edgar.__all__


@pytest.mark.asyncio
async def test_the_limiter_spaces_searches_with_the_other_reads(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_MAX_RPS", 20.0)  # 50 ms apart
    rec = _Recorder(_by_host(_ok, lambda r: httpx.Response(404)))
    async with httpx.AsyncClient(transport=httpx.MockTransport(rec)) as http:
        monkeypatch.setattr(sec_edgar, "_client", http)
        client = SECEdgarClient()
        await asyncio.gather(
            client.full_text_search(QUERY, start=START, end=END, forms=FORMS),
            client.get_submissions(CIK),
            client.get_company_concept(CIK, "Revenues"),
        )
    gaps = [b - a for a, b in zip(sorted(rec.times), sorted(rec.times)[1:])]
    assert len(gaps) == 2 and min(gaps) >= 0.045, gaps


@pytest.mark.asyncio
async def test_the_retry_takes_its_own_limiter_slot(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_MAX_RPS", 20.0)
    result, rec = await _run(monkeypatch, _sequence(503, 200), _search())
    assert result == RECORDED
    assert rec.times[1] - rec.times[0] >= 0.045, rec.times


# ── get_filing_document(max_bytes=) ─────────────────────────────────────


def _streamed(size, chunk=65536):
    async def body():
        left = size
        while left > 0:
            n = min(chunk, left)
            left -= n
            yield b"y" * n
    return body()


@pytest.mark.asyncio
async def test_without_max_bytes_the_document_cap_is_unchanged(monkeypatch):
    result, _ = await _run(monkeypatch,
                           lambda r: httpx.Response(200, content=_streamed(MAX_DOCUMENT_BYTES + 1)),
                           _document())
    assert type(result) is SECEdgarDocumentTooLargeException
    assert result.limit_bytes == MAX_DOCUMENT_BYTES and result.size_bytes is None
    ok, _ = await _run(monkeypatch,
                       lambda r: httpx.Response(200, content=_streamed(MAX_DOCUMENT_BYTES)),
                       _document(max_bytes=None))
    assert isinstance(ok, str) and len(ok) == MAX_DOCUMENT_BYTES


@pytest.mark.asyncio
async def test_the_default_cap_is_read_at_call_time(monkeypatch):
    monkeypatch.setattr(sec_edgar, "MAX_DOCUMENT_BYTES", 10)
    result, _ = await _run(monkeypatch, lambda r: httpx.Response(200, content=b"x" * 11),
                           _document())
    assert type(result) is SECEdgarDocumentTooLargeException and result.limit_bytes == 10


@pytest.mark.asyncio
async def test_a_raised_cap_reads_a_prospectus_past_the_learn_cap(monkeypatch):
    size = 5 * 1024 * 1024
    assert MAX_DOCUMENT_BYTES < size < MAX_DISCOVERY_DOCUMENT_BYTES
    result, rec = await _run(monkeypatch, lambda r: httpx.Response(200, content=_streamed(size)),
                             _document(doc="d94886d424b5.htm",
                                       max_bytes=MAX_DISCOVERY_DOCUMENT_BYTES))
    assert isinstance(result, str) and len(result) == size
    assert str(rec.requests[0].url).endswith("/000104581025000023/d94886d424b5.htm")


@pytest.mark.asyncio
async def test_max_bytes_drives_the_declared_size_check(monkeypatch):
    result, _ = await _run(
        monkeypatch,
        lambda r: httpx.Response(200, content=b"x", headers={"Content-Length": "1001"}),
        _document(max_bytes=1000),
    )
    assert type(result) is SECEdgarDocumentTooLargeException
    assert result.limit_bytes == 1000 and result.size_bytes == 1001
    assert "1000-byte cap" in str(result)
    big, _ = await _run(
        monkeypatch,
        lambda r: httpx.Response(
            200, content=b"x", headers={"Content-Length": str(MAX_DISCOVERY_DOCUMENT_BYTES + 1)}),
        _document(max_bytes=MAX_DISCOVERY_DOCUMENT_BYTES),
    )
    assert type(big) is SECEdgarDocumentTooLargeException
    assert big.limit_bytes == MAX_DISCOVERY_DOCUMENT_BYTES


@pytest.mark.asyncio
async def test_max_bytes_drives_the_streaming_check(monkeypatch):
    over, _ = await _run(monkeypatch, lambda r: httpx.Response(200, content=_streamed(1001, 100)),
                         _document(max_bytes=1000))
    assert type(over) is SECEdgarDocumentTooLargeException
    assert over.limit_bytes == 1000 and over.size_bytes is None
    at, _ = await _run(monkeypatch, lambda r: httpx.Response(200, content=_streamed(1000, 100)),
                       _document(max_bytes=1000))
    assert at == "y" * 1000
    one, _ = await _run(monkeypatch, lambda r: httpx.Response(200, content=b"z"),
                        _document(max_bytes=1))
    assert one == "z"
    two, _ = await _run(monkeypatch, lambda r: httpx.Response(200, content=_streamed(2, 1)),
                        _document(max_bytes=1))
    assert type(two) is SECEdgarDocumentTooLargeException


@pytest.mark.asyncio
@pytest.mark.parametrize("max_bytes", [0, -1, True, False, 1.5, "1000", 1e6,
                                       MAX_DISCOVERY_DOCUMENT_BYTES + 1, [1000]])
async def test_an_invalid_max_bytes_is_a_value_error_with_no_request(monkeypatch, max_bytes):
    result, rec = await _run(monkeypatch, lambda r: httpx.Response(200, text=HTML),
                             _document(max_bytes=max_bytes))
    assert type(result) is ValueError, result
    assert rec.requests == []


@pytest.mark.asyncio
async def test_max_bytes_is_keyword_only(monkeypatch):
    result, rec = await _run(monkeypatch, lambda r: httpx.Response(200, text=HTML),
                             lambda c: c.get_filing_document(CIK, ACC, DOC, 1000))
    assert type(result) is TypeError and rec.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("doc", ["q4fy25pr.htm\n", "primary_doc.xml\n", "a.htm\n\n", "\nq.htm",
                                 "xslSCHEDULE_13G_X01/primary_doc.xml"])
async def test_the_document_name_must_match_whole(monkeypatch, doc):
    # `$` also matches before ONE trailing newline: `.match` let "q4fy25pr.htm\n" into the URL.
    result, rec = await _run(monkeypatch, lambda r: httpx.Response(200, text=HTML),
                             _document(doc=doc))
    assert type(result) is ValueError, result
    assert rec.requests == []


@pytest.mark.asyncio
async def test_a_schedule_xml_document_name_is_accepted(monkeypatch):
    result, rec = await _run(monkeypatch, lambda r: httpx.Response(200, text="<x/>"),
                             _document(doc="primary_doc.xml", max_bytes=2 * 1024 * 1024))
    assert result == "<x/>"
    assert str(rec.requests[0].url).endswith("/000104581025000023/primary_doc.xml")


# ── Partial answers (review round 1, 2026-10-28) ───────────────────────────────────────────
# Elasticsearch answers 200 with only the surviving shards' hits by default; such a page is
# not "no new filings", so it is unavailable and retried like a 5xx.

def _partial(**extra):
    return httpx.Response(200, json={**RECORDED, **extra})


_PARTIALS = [
    {"timed_out": True},
    {"_shards": {"total": 5, "successful": 3, "skipped": 0, "failed": 2}},
    {"_shards": {"total": 5, "successful": 4, "skipped": 0, "failed": 0}},   # one unallocated
]

#: The search backend's own failure envelope, answered with HTTP 200 (seen live 2026-10-29).
_ERROR_BODY = {"errorType": "ConnectionError",
               "errorMessage": "getaddrinfo EBUSY vpc-efts-prod-opensearchservice-x.us-east-1.es.amazonaws.com",
               "trace": ["ConnectionError: getaddrinfo EBUSY …", "    at ClientRequest.onError (…)"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", _PARTIALS)
async def test_a_partial_answer_is_retried_once_and_the_retry_answers(monkeypatch, caplog, extra):
    caplog.set_level(logging.WARNING, logger="app.integrations.sec_edgar")
    result, rec = await _run(monkeypatch, _sequence(_partial(**extra), 200), _search())
    assert result == RECORDED
    assert len(rec.requests) == 2
    assert any("[sec-efts-retry]" in r.getMessage() and "partial" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", _PARTIALS)
async def test_two_partial_answers_are_unavailable_after_exactly_one_retry(monkeypatch, extra):
    result, rec = await _run(monkeypatch, _sequence(_partial(**extra), _partial(**extra)), _search())
    assert type(result) is SECUnavailableException, result
    assert result.status_code == 200 and "after one retry" in str(result) and "partial" in str(result)
    assert len(rec.requests) == 2
    assert _efts_circuit() <= 0 and _shared_circuit() <= 0, "a partial answer is not throttling"


@pytest.mark.asyncio
async def test_an_error_envelope_answered_200_is_retried_and_never_read_as_an_answer(monkeypatch, caplog):
    """Live 2026-10-29: EFTS answered HTTP 200 with {"errorType": "ConnectionError", …} for one alias.
    The finder used to see "no query echo"; it is SEC unavailable, retried once like a 5xx."""
    caplog.set_level(logging.WARNING, logger="app.integrations.sec_edgar")
    result, rec = await _run(monkeypatch, _sequence(httpx.Response(200, json=_ERROR_BODY), 200), _search())
    assert result == RECORDED and len(rec.requests) == 2
    assert any("[sec-efts-retry]" in r.getMessage() and "ConnectionError" in r.getMessage() for r in caplog.records)
    result, rec = await _run(monkeypatch, _sequence(httpx.Response(200, json=_ERROR_BODY),
                                                    httpx.Response(200, json=_ERROR_BODY)), _search())
    assert type(result) is SECUnavailableException and result.status_code == 200, result
    assert "after one retry" in str(result) and "error body: ConnectionError" in str(result)
    assert _efts_circuit() <= 0 and _shared_circuit() <= 0, "an upstream failure is not throttling"


@pytest.mark.asyncio
async def test_a_real_answer_that_happens_to_carry_an_error_key_is_still_an_answer(monkeypatch):
    body = {**RECORDED, "errorMessage": "ignored: the hits are here"}
    result, rec = await _run(monkeypatch, lambda r: httpx.Response(200, json=body), _search())
    assert result == body and len(rec.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {},                                                                      # the recorded shape
    {"timed_out": False, "_shards": {"total": 5, "successful": 5, "skipped": 0, "failed": 0}},
    {"_shards": {"total": 5, "successful": 5, "skipped": 2, "failed": 0}},   # skipped count as ok
    {"_shards": {"total": True, "successful": 0, "failed": False}},          # bools are not counts
    {"_shards": "n/a"},
    {"timed_out": "true"},                                                   # only the JSON true
])
async def test_a_complete_or_unreadable_shard_block_is_returned(monkeypatch, extra):
    body = {**RECORDED, **extra}
    result, rec = await _run(monkeypatch, lambda r: httpx.Response(200, json=body), _search())
    assert result == body and len(rec.requests) == 1
