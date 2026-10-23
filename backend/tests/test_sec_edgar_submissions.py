"""The two Learn reads of the SEC EDGAR thin client — `get_submissions` and
`get_filing_document` (`app/integrations/sec_edgar.py`) — hermetic, over httpx.MockTransport.

Pins: the URLs (submissions on data.sec.gov, documents on www.sec.gov with the integer CIK and
the undashed accession), the User-Agent rule, the outcome split shared with
`get_company_concept` (200 / 404 None / 429+403 throttled with ONE process-wide circuit across
all three reads / 5xx, 408, timeout unavailable / other 4xx refused), input validation before any
I/O (no path traversal into the Archives), and the 3 MB document cap — refused by a declared
Content-Length or while streaming (a gzip bomb included), never held in memory.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import time

import httpx
import pytest

from app.api.error_response import ErrorCode, classify_exception
from app.config import settings
from app.integrations import sec_edgar
from app.integrations.sec_edgar import (
    MAX_DOCUMENT_BYTES,
    SECConfigException,
    SECEdgarClient,
    SECEdgarDocumentTooLargeException,
    SECException,
    SECRateLimitException,
    SECUnavailableException,
    filing_index_url,
    normalize_accession,
)

UA = "Caydex Test sec-contact-test@example.com"
CIK = "1045810"
ACC = "0001045810-25-000023"
DOC = "q4fy25pr.htm"
SUBMISSIONS = {
    "cik": "1045810",
    "name": "NVIDIA CORP",
    "tickers": ["NVDA"],
    "exchanges": ["Nasdaq"],
    "sic": "3674",
    "sicDescription": "Semiconductors & Related Devices",
    "filings": {"recent": {"accessionNumber": [ACC], "filingDate": ["2025-02-26"],
                           "form": ["8-K"], "primaryDocument": [DOC], "items": ["2.02,9.01"]}},
}
HTML = "<html><body><p>NVIDIA announced results.</p></body></html>"


@pytest.fixture(autouse=True)
def _sec_state(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", UA)
    monkeypatch.setattr(settings, "SEC_EDGAR_BASE_URL", "https://data.sec.gov")
    monkeypatch.setattr(settings, "SEC_EDGAR_MAX_RPS", 1000.0)
    monkeypatch.setattr(sec_edgar, "_circuit_open_until", 0.0)
    monkeypatch.setattr(sec_edgar, "_next_request_at", 0.0)


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


def _submissions(cik=CIK):
    return lambda c: c.get_submissions(cik)


def _document(cik=CIK, acc=ACC, doc=DOC):
    return lambda c: c.get_filing_document(cik, acc, doc)


def _concept(c):
    return c.get_company_concept(CIK, "Revenues")


# ── get_submissions ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submissions_url_headers_and_parse(monkeypatch):
    result, rec = await _run(monkeypatch, lambda r: httpx.Response(200, json=SUBMISSIONS),
                             _submissions())
    assert result == SUBMISSIONS
    assert len(rec.requests) == 1
    req = rec.requests[0]
    assert str(req.url) == "https://data.sec.gov/submissions/CIK0001045810.json"
    assert req.headers["user-agent"] == UA
    assert "gzip" in req.headers["accept-encoding"]


@pytest.mark.asyncio
async def test_submissions_int_and_padded_cik_build_the_same_url(monkeypatch):
    for cik in (1045810, "0001045810", " 1045810 "):
        _, rec = await _run(monkeypatch, lambda r: httpx.Response(200, json=SUBMISSIONS),
                            _submissions(cik))
        assert str(rec.requests[0].url).endswith("/submissions/CIK0001045810.json")


@pytest.mark.asyncio
async def test_submissions_404_is_none(monkeypatch):
    result, rec = await _run(monkeypatch, lambda r: httpx.Response(404, text="Not Found"),
                             _submissions())
    assert result is None and len(rec.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cik", [None, "", "abc", "0", "12345678901", True, -5, 3.0])
async def test_submissions_invalid_cik_is_a_value_error_with_no_request(monkeypatch, cik):
    result, rec = await _run(monkeypatch, lambda r: httpx.Response(200, json=SUBMISSIONS),
                             _submissions(cik))
    assert isinstance(result, ValueError) and rec.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    httpx.Response(200, text="<html>maintenance</html>"),
    httpx.Response(200, text=""),
    httpx.Response(200, json=[1, 2]),
    httpx.Response(200, json=None),
    httpx.Response(200, content=b"[" * 100_000 + b"]" * 100_000),
])
async def test_submissions_200_that_is_not_a_json_object_is_unavailable(monkeypatch, response):
    result, _ = await _run(monkeypatch, lambda r: response, _submissions())
    assert type(result) is SECUnavailableException and result.status_code == 200


@pytest.mark.asyncio
async def test_submissions_gzip_body_is_decompressed(monkeypatch):
    body = gzip.compress(json.dumps(SUBMISSIONS).encode())
    result, _ = await _run(
        monkeypatch,
        lambda r: httpx.Response(200, content=body, headers={"Content-Encoding": "gzip"}),
        _submissions(),
    )
    assert result == SUBMISSIONS


@pytest.mark.asyncio
@pytest.mark.parametrize("status, expected", [
    (500, SECUnavailableException), (503, SECUnavailableException),
    (408, SECUnavailableException), (400, SECException), (410, SECException),
])
async def test_submissions_status_split(monkeypatch, status, expected):
    result, _ = await _run(monkeypatch, lambda r: httpx.Response(status, text="x"),
                           _submissions())
    assert type(result) is expected and result.status_code == status
    assert sec_edgar._circuit_remaining() <= 0, "only 429 / 403 may open the circuit"


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [httpx.ReadTimeout("t"), httpx.ConnectError("refused")])
async def test_submissions_transport_errors_are_unavailable(monkeypatch, exc):
    def boom(request):
        raise exc

    result, _ = await _run(monkeypatch, boom, _submissions())
    assert type(result) is SECUnavailableException and result.status_code is None
    assert "CIK0001045810" in str(result)


# ── One circuit for all three reads ─────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 403])
@pytest.mark.parametrize("first", ["submissions", "document"])
async def test_a_throttle_on_either_read_opens_the_one_shared_circuit(monkeypatch, status, first):
    call = _submissions() if first == "submissions" else _document()
    result, rec = await _run(
        monkeypatch,
        lambda r: httpx.Response(status, text="slow down", headers={"Retry-After": "30"}),
        call,
    )
    assert type(result) is SECRateLimitException
    assert result.status_code == status and result.retry_after == 30.0
    assert len(rec.requests) == 1

    # Every read — the two new ones AND the buyback check's concept read — now refuses
    # without any network I/O.
    for other in (_submissions(), _document(), _concept):
        again, rec2 = await _run(monkeypatch, lambda r: httpx.Response(200, json={}), other)
        assert type(again) is SECRateLimitException, again
        assert again.status_code is None and 0 < again.retry_after <= sec_edgar.CIRCUIT_SECONDS
        assert rec2.requests == []

    monkeypatch.setattr(sec_edgar, "_circuit_open_until", time.monotonic() - 1)
    ok, rec3 = await _run(monkeypatch, lambda r: httpx.Response(200, json=SUBMISSIONS),
                          _submissions())
    assert ok == SUBMISSIONS and len(rec3.requests) == 1


@pytest.mark.asyncio
async def test_a_concept_throttle_closes_the_new_reads_too(monkeypatch):
    result, _ = await _run(monkeypatch, lambda r: httpx.Response(429), _concept)
    assert type(result) is SECRateLimitException
    for other in (_submissions(), _document()):
        again, rec = await _run(monkeypatch, lambda r: httpx.Response(200, json={}), other)
        assert type(again) is SECRateLimitException and rec.requests == []


# ── get_filing_document ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_document_url_is_on_www_sec_gov_with_int_cik_and_undashed_accession(monkeypatch):
    result, rec = await _run(monkeypatch, lambda r: httpx.Response(200, text=HTML), _document())
    assert result == HTML
    req = rec.requests[0]
    assert str(req.url) == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000023/q4fy25pr.htm"
    )
    assert req.headers["user-agent"] == UA


@pytest.mark.asyncio
async def test_document_accepts_an_undashed_accession_and_a_padded_cik(monkeypatch):
    _, rec = await _run(monkeypatch, lambda r: httpx.Response(200, text=HTML),
                        _document(cik="0001045810", acc="000104581025000023"))
    assert "/data/1045810/000104581025000023/q4fy25pr.htm" in str(rec.requests[0].url)


@pytest.mark.asyncio
async def test_document_404_is_none(monkeypatch):
    result, rec = await _run(monkeypatch, lambda r: httpx.Response(404, text="nope"),
                             _document())
    assert result is None and len(rec.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("acc", [None, "", "0001045810-25-00002", "abc", "0001045810_25_000023",
                                 "٠٠٠١٠٤٥٨١٠-٢٥-٠٠٠٠٢٣", 104581025000023])
async def test_document_invalid_accession_is_a_value_error_with_no_request(monkeypatch, acc):
    result, rec = await _run(monkeypatch, lambda r: httpx.Response(200, text=HTML),
                             _document(acc=acc))
    assert isinstance(result, ValueError) and rec.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("doc", ["", None, "../x.htm", "a/b.htm", ".hidden", "..", "x.htm?y=1",
                                 "x.htm#f", "x y.htm", "%2e%2e", "x" * 201, 5])
async def test_document_name_cannot_escape_the_filing_folder(monkeypatch, doc):
    result, rec = await _run(monkeypatch, lambda r: httpx.Response(200, text=HTML),
                             _document(doc=doc))
    assert isinstance(result, ValueError) and rec.requests == []


@pytest.mark.asyncio
async def test_document_declared_too_large_is_refused_by_its_content_length(monkeypatch):
    declared = MAX_DOCUMENT_BYTES + 1
    result, _ = await _run(
        monkeypatch,
        lambda r: httpx.Response(200, content=b"x" * 10,
                                 headers={"Content-Length": str(declared)}),
        _document(),
    )
    assert type(result) is SECEdgarDocumentTooLargeException
    assert result.size_bytes == declared and result.limit_bytes == MAX_DOCUMENT_BYTES
    assert sec_edgar._circuit_remaining() <= 0  # not throttling


@pytest.mark.asyncio
async def test_document_streamed_past_the_cap_is_abandoned(monkeypatch):
    chunk = b"y" * 65536
    yielded = []

    async def body():
        for _ in range(MAX_DOCUMENT_BYTES // len(chunk) + 50):
            yielded.append(1)
            yield chunk

    result, _ = await _run(monkeypatch, lambda r: httpx.Response(200, content=body()),
                           _document())
    assert type(result) is SECEdgarDocumentTooLargeException
    assert result.size_bytes is None
    # Abandoned at the cap: never read to the end.
    assert len(yielded) <= MAX_DOCUMENT_BYTES // len(chunk) + 2


@pytest.mark.asyncio
async def test_a_gzip_bomb_is_measured_decoded_not_compressed(monkeypatch):
    bomb = gzip.compress(b"\0" * (MAX_DOCUMENT_BYTES + 1024))
    assert len(bomb) < 100_000  # tiny on the wire
    result, _ = await _run(
        monkeypatch,
        lambda r: httpx.Response(200, content=bomb, headers={"Content-Encoding": "gzip"}),
        _document(),
    )
    assert type(result) is SECEdgarDocumentTooLargeException


@pytest.mark.asyncio
async def test_a_document_exactly_at_the_cap_is_read(monkeypatch):
    body = b"z" * MAX_DOCUMENT_BYTES
    result, _ = await _run(monkeypatch, lambda r: httpx.Response(200, content=body),
                           _document(doc="ex991.txt"))
    assert isinstance(result, str) and len(result) == MAX_DOCUMENT_BYTES


@pytest.mark.asyncio
async def test_document_decoding_honours_the_charset_then_utf8_then_cp1252(monkeypatch):
    # cp1252 curly apostrophe (0x92), no charset declared: not UTF-8, so the fallback reads it.
    result, _ = await _run(
        monkeypatch,
        lambda r: httpx.Response(200, content=b"NVIDIA\x92s results",
                                 headers={"Content-Type": "text/html"}),
        _document(),
    )
    assert result == "NVIDIA’s results"
    result, _ = await _run(
        monkeypatch,
        lambda r: httpx.Response(200, content="café".encode("latin-1"),
                                 headers={"Content-Type": "text/html; charset=ISO-8859-1"}),
        _document(),
    )
    assert result == "café"
    result, _ = await _run(
        monkeypatch,
        lambda r: httpx.Response(200, content="— dash".encode("utf-8"),
                                 headers={"Content-Type": "text/html; charset=bogus-xyz"}),
        _document(),
    )
    assert result == "— dash"


@pytest.mark.asyncio
async def test_an_empty_200_document_is_an_empty_string(monkeypatch):
    result, _ = await _run(monkeypatch, lambda r: httpx.Response(200, content=b""), _document())
    assert result == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("status, expected", [
    (500, SECUnavailableException), (408, SECUnavailableException), (401, SECException),
])
async def test_document_status_split_with_a_scrubbed_bounded_excerpt(monkeypatch, status,
                                                                   expected):
    echo = f"refused for {UA} " + "z" * 100_000
    result, _ = await _run(monkeypatch, lambda r: httpx.Response(status, text=echo),
                           _document())
    assert type(result) is expected and result.status_code == status
    assert UA not in str(result) and "sec-contact-test@example.com" not in str(result)
    assert len(str(result)) < 600


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [httpx.ReadTimeout("t"), httpx.RemoteProtocolError("closed")])
async def test_document_transport_errors_are_unavailable(monkeypatch, exc):
    def boom(request):
        raise exc

    result, _ = await _run(monkeypatch, boom, _document())
    assert type(result) is SECUnavailableException
    assert ACC in str(result)


# ── Config and the User-Agent ───────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", "   ", "Caydex — sec-contact-test@example.com",
                                 "Caydex\nsec-contact-test@example.com"])
async def test_an_unusable_user_agent_refuses_both_reads_before_any_request(monkeypatch, bad):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", bad)
    for call in (_submissions(), _document()):
        result, rec = await _run(monkeypatch, lambda r: httpx.Response(200, json={}), call)
        assert type(result) is SECConfigException and rec.requests == []
        assert "sec-contact-test@example.com" not in str(result)


@pytest.mark.asyncio
async def test_the_limiter_spaces_the_new_reads_with_the_old_one(monkeypatch):
    monkeypatch.setattr(settings, "SEC_EDGAR_MAX_RPS", 20.0)  # 50 ms apart
    rec = _Recorder(lambda r: httpx.Response(404))
    async with httpx.AsyncClient(transport=httpx.MockTransport(rec)) as http:
        monkeypatch.setattr(sec_edgar, "_client", http)
        client = SECEdgarClient()
        await asyncio.gather(
            client.get_submissions(CIK),
            client.get_filing_document(CIK, ACC, DOC),
            client.get_company_concept(CIK, "Revenues"),
        )
    gaps = [b - a for a, b in zip(sorted(rec.times), sorted(rec.times)[1:])]
    assert len(gaps) == 2 and min(gaps) >= 0.045, gaps


# ── Helpers and classification ──────────────────────────────────────────


@pytest.mark.parametrize("value, expected", [
    (ACC, ACC),
    ("000104581025000023", ACC),
    (f"  {ACC} ", ACC),
    ("0001045810-25000023", ACC),
    ("", None), (None, None), (True, None), (104581025000023, None),
    ("0001045810-25-0000231", None), ("0001045810-2-5000023", None),
    ("０００１０４５８１０-２５-０００００２３", None),
])
def test_normalize_accession(value, expected):
    assert normalize_accession(value) == expected


def test_filing_index_url():
    assert filing_index_url("0001045810", "000104581025000023") == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000023/"
        "0001045810-25-000023-index.htm"
    )
    assert filing_index_url("abc", ACC) is None
    assert filing_index_url(CIK, "bad") is None


def test_the_too_large_exception_is_typed_and_classifies_as_a_data_source_outage():
    exc = SECEdgarDocumentTooLargeException("too big", limit_bytes=3, size_bytes=9)
    assert isinstance(exc, SECException)
    assert classify_exception(exc) == (ErrorCode.FMP_UNAVAILABLE, 502)
