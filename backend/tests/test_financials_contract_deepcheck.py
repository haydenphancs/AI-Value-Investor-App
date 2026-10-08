"""
Financials-tab contract deep-check (W8): the six endpoints' error bodies, and backend <-> iOS
parity for every field the 2026-09-30 fix plan added to the Financials wire contract.

Two defects this pins:

1. **The six Financials handlers raised a bare-string 502** (finding #93). iOS cannot decode
   ``{"detail": "Earnings service unavailable for AVGO"}`` as ``APIErrorResponse``, so
   APIClient retried it blind as ``.serverError`` — each retry a full, uncached, cold FMP
   fan-out — and the tab could never say why. Every failure is now the typed body
   (invariant #3). An UNEXPECTED failure classifies as REPORT_GENERATION_FAILED, whose
   registered copy talks about a report, so its ``user_message`` is replaced with the
   section's own; a KNOWN upstream code keeps its registered copy. An invalid symbol stays
   a 400, now typed — and only for the services' own "Invalid ticker symbol" ValueError, so
   a stray ``float("n/a")`` inside a build is not blamed on the user's symbol.

2. **Every new wire field must be decodable by iOS as Optional.** A shipped build that
   meets a key it does not know ignores it; a NEW build that meets a payload WITHOUT the key
   (an older backend, a cached report) must not throw. So for each new Pydantic field the
   Swift DTO declares ``let <prop>: <Type>?`` and maps the exact snake_case key. The Swift
   scan strips comments and brace-bounds each DTO struct, so a field documented in a comment,
   or declared on a different struct, cannot satisfy it.

Hermetic: the handlers are called directly with their service getters monkeypatched on the
binding the endpoint module uses (``stocks.get_*_service``).
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

import app.api.v1.endpoints.stocks as stocks
from app.integrations.fmp import (
    FMPNotEntitledException,
    FMPRateLimitException,
    FMPUnavailableException,
)
from app.schemas.earnings import EarningsQuarterSchema, EarningsResponse
from app.schemas.growth import GrowthResponse
from app.schemas.health_check import HealthCheckMetricSchema, HealthCheckResponse
from app.schemas.profit_power import ProfitPowerResponse
from app.schemas.revenue_breakdown import RevenueBreakdownResponse
from app.schemas.signal_of_confidence import (
    DividendInfoSchema,
    SignalOfConfidenceDataPointSchema,
    SignalOfConfidenceResponse,
    SignalOfConfidenceSummarySchema,
)

_BACKEND = Path(__file__).resolve().parents[1]
_REPO = _BACKEND.parent
_STOCKS_PY = _BACKEND / "app/api/v1/endpoints/stocks.py"
_REPOSITORY = _REPO / "frontend/ios/ios/Core/Repositories/StockRepository.swift"

_APIERROR_KEYS = {"error_code", "message", "user_message", "action", "details"}


# ─────────────────────────────────────────────────────────────────────────────
# 1. The six handlers answer every failure with the APIErrorResponse shape
# ─────────────────────────────────────────────────────────────────────────────

class _Raising:
    """A stand-in service whose every method raises `exc`."""

    def __init__(self, exc: BaseException):
        self._exc = exc

    def __getattr__(self, name):
        async def _raise(*_a, **_k):
            raise self._exc
        return _raise


class _Returning:
    def __init__(self, value):
        self._value = value

    def __getattr__(self, name):
        async def _ret(*_a, **_k):
            return self._value
        return _ret


# (handler name, service getter bound in stocks.py, step, the section's words in the copy)
_ROUTES = [
    ("get_earnings", "get_earnings_service", "earnings", "earnings"),
    ("get_growth", "get_growth_service", "growth", "growth"),
    ("get_profit_power", "get_profit_power_service", "profit_power", "profit power"),
    ("get_health_check", "get_health_check_service", "health_check", "health check"),
    ("get_revenue_breakdown", "get_revenue_breakdown_service", "revenue_breakdown",
     "revenue breakdown"),
    ("get_signal_of_confidence", "get_signal_of_confidence_service",
     "signal_of_confidence", "signal of confidence"),
]


async def _call(handler_name: str, ticker: str = "avgo"):
    handler = getattr(stocks, handler_name)
    # The three fan-out routes take the rate-limit dependency as a keyword; it is inert here.
    if "_fanout" in handler.__code__.co_varnames[: handler.__code__.co_argcount]:
        return await handler(ticker, _fanout=None)
    return await handler(ticker)


def _body(resp) -> dict:
    return json.loads(resp.body)


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,getter,step,section", _ROUTES)
async def test_an_unexpected_failure_is_a_typed_502_with_the_sections_own_copy(
    monkeypatch, handler, getter, step, section
):
    monkeypatch.setattr(stocks, getter, lambda: _Raising(RuntimeError("non-dict price row")))
    resp = await _call(handler)

    assert resp.status_code == 502
    body = _body(resp)
    assert _APIERROR_KEYS <= set(body), f"{handler}: not the APIErrorResponse shape: {body}"
    assert body["error_code"] == "REPORT_GENERATION_FAILED"
    # The registered copy for that code names a REPORT; this is a Financials card.
    assert "report" not in body["user_message"].lower(), body["user_message"]
    assert section in body["user_message"], body["user_message"]
    assert body["details"]["step"] == step
    assert body["details"]["ticker"] == "AVGO"
    assert "RuntimeError" in body["details"]["underlying"]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,getter,step,section", _ROUTES)
@pytest.mark.parametrize("exc,code", [
    (FMPRateLimitException("429 on /stable/ratios"), "FMP_RATE_LIMITED"),
    (FMPUnavailableException("503 after retries"), "FMP_UNAVAILABLE"),
    (FMPNotEntitledException("not on the Order Form"), "FMP_NOT_ENTITLED"),
])
async def test_a_known_upstream_failure_keeps_its_typed_code_and_registered_copy(
    monkeypatch, handler, getter, step, section, exc, code
):
    monkeypatch.setattr(stocks, getter, lambda: _Raising(exc))
    resp = await _call(handler)
    body = _body(resp)

    assert _APIERROR_KEYS <= set(body)
    assert body["error_code"] == code
    assert body["details"]["step"] == step
    # Registered copy, not the per-section override (that is for the unknown case only).
    assert "We couldn't load" not in body["user_message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,getter,step,section", _ROUTES)
async def test_the_services_invalid_symbol_error_stays_a_400_now_typed(
    monkeypatch, handler, getter, step, section
):
    monkeypatch.setattr(
        stocks, getter, lambda: _Raising(ValueError("Invalid ticker symbol: '1234567'"))
    )
    resp = await _call(handler, "1234567")

    assert resp.status_code == 400
    body = _body(resp)
    assert _APIERROR_KEYS <= set(body)
    assert body["error_code"] == "TICKER_NOT_FOUND"
    assert body["details"]["step"] == step


@pytest.mark.asyncio
@pytest.mark.parametrize("handler,getter,step,section", _ROUTES)
async def test_an_unrelated_value_error_is_not_blamed_on_the_symbol(
    monkeypatch, handler, getter, step, section
):
    # `float("n/a")` deep inside a build: a bug in the build, not a bad ticker.
    monkeypatch.setattr(
        stocks, getter,
        lambda: _Raising(ValueError("could not convert string to float: 'n/a'")),
    )
    resp = await _call(handler)

    assert resp.status_code == 502
    body = _body(resp)
    assert body["error_code"] != "TICKER_NOT_FOUND"
    assert section in body["user_message"]


@pytest.mark.asyncio
async def test_the_error_body_never_carries_the_fmp_key(monkeypatch):
    # An httpx error re-raised raw carries the request URL, and FMP puts the key in it.
    secret = "abcdEFGH12345678secretkey"
    exc = RuntimeError(
        "Client error '403 Forbidden' for url "
        f"'https://financialmodelingprep.com/stable/ratios?symbol=AVGO&apikey={secret}'"
    )
    monkeypatch.setattr(stocks, "get_earnings_service", lambda: _Raising(exc))
    resp = await _call("get_earnings")
    assert secret not in resp.body.decode()


@pytest.mark.asyncio
async def test_an_http_exception_from_a_service_still_passes_through(monkeypatch):
    monkeypatch.setattr(
        stocks, "get_growth_service",
        lambda: _Raising(HTTPException(status_code=429, detail="slow down")),
    )
    with pytest.raises(HTTPException) as info:
        await _call("get_growth")
    assert info.value.status_code == 429


@pytest.mark.asyncio
async def test_a_successful_build_is_returned_untouched(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(stocks, "get_health_check_service", lambda: _Returning(sentinel))
    assert await _call("get_health_check") is sentinel


def _financials_handlers() -> dict:
    tree = ast.parse(_STOCKS_PY.read_text())
    wanted = {name for name, *_ in _ROUTES}
    found = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name in wanted
    }
    assert set(found) == wanted, f"handlers drifted: missing {wanted - set(found)}"
    return found


def test_no_financials_handler_raises_a_bare_string_http_exception():
    """AST, not a regex: a docstring or comment naming the old raise cannot trip it, and a
    reverted raise in any of the six handlers cannot hide behind a sibling's fix."""
    for name, fn in _financials_handlers().items():
        for node in ast.walk(fn):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                callee = node.exc.func
                callee_name = getattr(callee, "id", getattr(callee, "attr", ""))
                assert callee_name != "HTTPException", (
                    f"{name} raises HTTPException again — iOS cannot decode a string detail"
                )


def test_every_financials_handler_routes_its_failure_through_the_typed_helper():
    for name, fn in _financials_handlers().items():
        handlers = [
            h for node in ast.walk(fn) if isinstance(node, ast.Try) for h in node.handlers
        ]
        generic = [
            h for h in handlers
            if isinstance(h.type, ast.Name) and h.type.id == "Exception"
        ]
        assert generic, f"{name} has no `except Exception` arm"
        for h in generic:
            returns = [n for n in ast.walk(h) if isinstance(n, ast.Return)]
            assert any(
                isinstance(r.value, ast.Call)
                and getattr(r.value.func, "id", "") == "_financials_error_response"
                for r in returns
            ), f"{name}: `except Exception` does not return _financials_error_response(...)"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Backend <-> iOS parity for every new Financials wire field
# ─────────────────────────────────────────────────────────────────────────────

# (Pydantic model, snake_case key, Swift DTO struct, Swift property, Swift wrapped type)
_NEW_FIELDS = [
    (EarningsResponse, "degraded", "EarningsDTO", "degraded", "[String]"),
    (EarningsQuarterSchema, "has_estimate", "EarningsQuarterDTO", "hasEstimate", "Bool"),
    (GrowthResponse, "degraded", "GrowthResponseDTO", "degraded", "[String]"),
    (GrowthResponse, "peer_group_levels", "GrowthResponseDTO", "peerGroupLevels",
     "[String: String]"),
    (ProfitPowerResponse, "degraded", "ProfitPowerResponseDTO", "degraded", "[String]"),
    (HealthCheckResponse, "degraded", "HealthCheckResponseDTO", "degraded", "[String]"),
    (RevenueBreakdownResponse, "degraded", "RevenueBreakdownDTO", "degraded", "[String]"),
    (SignalOfConfidenceResponse, "degraded", "SignalOfConfidenceResponseDTO", "degraded",
     "[String]"),
    (SignalOfConfidenceSummarySchema, "share_count_change_known",
     "SignalOfConfidenceSummaryDTO", "shareCountChangeKnown", "Bool"),
    (DividendInfoSchema, "avg_yield_window", "DividendInfoDTO", "avgYieldWindow", "String"),
    # P19: False = no cash-flow row for the quarter (0.0 placeholders, not a measured $0).
    (SignalOfConfidenceDataPointSchema, "cash_flow_reported", "SignalOfConfidenceDataPointDTO",
     "cashFlowReported", "Bool"),
    # 2026-10-05: the yields' denominator (USD millions); the Capital view's scale floor.
    (SignalOfConfidenceDataPointSchema, "market_cap", "SignalOfConfidenceDataPointDTO",
     "marketCap", "Double"),
    # 2026-10-07: the peer group each comparison / drawn peer line comes from.
    (HealthCheckMetricSchema, "peer_level", "HealthCheckMetricDTO", "peerLevel", "String"),
    (ProfitPowerResponse, "peer_group_levels", "ProfitPowerResponseDTO", "peerGroupLevels",
     "[String: String]"),
]


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails (doc comments name every field we look for)."""
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _block(src: str, header_re: str) -> str:
    """Brace-balanced body of the first declaration matching `header_re` (comments stripped
    BEFORE matching, so a header quoted in a comment cannot be the one found)."""
    m = re.search(header_re, src)
    assert m, f"{header_re!r} not found — this scan has drifted"
    open_brace = src.index("{", m.end() - 1 if src[m.end() - 1] == "{" else m.end())
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace: i + 1]
    pytest.fail(f"unbalanced braces after {header_re!r}")


def _struct(name: str) -> str:
    src = _strip_comments(_REPOSITORY.read_text())
    return _block(src, rf"\bstruct\s+{re.escape(name)}\s*:[^{{]*\{{")


def _coding_keys(struct_body: str) -> dict:
    """{swift property: wire key} from the struct's `enum CodingKeys` — `case a, b` lines map
    a name to itself, `case a = "x"` maps it to "x"."""
    keys_block = _block(struct_body, r"enum\s+CodingKeys\s*:\s*String\s*,\s*CodingKey\s*\{")
    mapping = {}
    for stmt in re.findall(r"\bcase\s+([^\n]+)", keys_block):
        for part in stmt.split(","):
            part = part.strip()
            if not part:
                continue
            m = re.fullmatch(r'(\w+)\s*=\s*"([^"]+)"', part)
            if m:
                mapping[m.group(1)] = m.group(2)
            elif re.fullmatch(r"\w+", part):
                mapping[part] = part
    return mapping


@pytest.mark.parametrize("model,key,struct,prop,swift_type", _NEW_FIELDS)
def test_the_backend_field_exists_and_is_additive(model, key, struct, prop, swift_type):
    field = model.model_fields.get(key)
    assert field is not None, f"{model.__name__}.{key} is gone — iOS still decodes it"
    # Additive: a payload built before the field (a cached row, a stored report) still
    # validates, so the field must carry a default.
    assert not field.is_required(), f"{model.__name__}.{key} must have a default"


@pytest.mark.parametrize("model,key,struct,prop,swift_type", _NEW_FIELDS)
def test_the_swift_dto_decodes_the_key_as_optional(model, key, struct, prop, swift_type):
    body = _struct(struct)
    decl = re.search(rf"\blet\s+{prop}\s*:\s*([^\n=]+?)\s*$", body, re.MULTILINE)
    assert decl, f"{struct} does not declare `{prop}`"
    declared = decl.group(1).replace(" ", "")
    assert declared == swift_type.replace(" ", "") + "?", (
        f"{struct}.{prop} is `{decl.group(1)}` — it must be `{swift_type}?` so a payload "
        "without the key (older backend, cached report) still decodes"
    )
    assert _coding_keys(body).get(prop) == key, (
        f"{struct}.CodingKeys does not map `{prop}` to \"{key}\""
    )


def test_the_coding_keys_parser_is_not_vacuous():
    # A struct with a mixed `case a, b` line and `case x = "y"` lines parses both forms.
    keys = _coding_keys(_struct("ProfitPowerResponseDTO"))
    assert keys["symbol"] == "symbol" and keys["annual"] == "annual"
    assert keys["peerGroupLevel"] == "peer_group_level"
    # And a comment-only mention does not count.
    fake = 'struct X: Codable {\n    // let degraded: [String]?\n    let a: Int\n}'
    assert "degraded" not in _strip_comments(fake)
