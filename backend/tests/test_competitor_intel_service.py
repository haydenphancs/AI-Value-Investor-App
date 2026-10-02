"""
Unit tests for competitor_intel_service.

Covers the pure-helper math (ticker normalization, source-label
derivation, relationship filter, segment cleaner, version marker), the
FMP-validation pass, and the cache tiers end to end (memory → DB →
extraction → stale-on-error) — all with injected fake clients, since the
testing rule forbids hitting live FMP / Gemini / Supabase.

No network: Supabase is an in-memory fake patched over the module's
`get_supabase` binding.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

import app.services.competitor_intel_service as cis
from app.services.competitor_intel_service import (
    CACHE_MARKER,
    CACHE_MARKER_NODETAILS,
    COMPETITOR_INTEL_SCHEMA_FLOOR,
    SEGMENT_MAX_CHARS,
    CompetitorIntelService,
    _COMPETITOR_MAX_N,
    _NEGATIVE_TTL_SECONDS,
    _clean_segment,
    _coerce_details,
    _derive_source_label,
    _is_customer_or_partner,
    _marker_is_current,
    _normalize_relationship,
    _normalize_ticker,
)


@pytest.fixture(autouse=True)
def _isolated_tiers(monkeypatch):
    """Every test starts with empty module-level tiers and the AI switch on."""
    cis._mem_cache.clear()
    cis._inflight.clear()
    monkeypatch.setattr(cis.settings, "COMPETITOR_INTEL_AI_ENABLED", True)
    yield
    cis._mem_cache.clear()
    cis._inflight.clear()


# ── Ticker normalization ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("MSFT", "MSFT"),
        ("msft", "MSFT"),
        ("  MSFT  ", "MSFT"),
        ("$MSFT", "MSFT"),
        ("NASDAQ:MSFT", "MSFT"),
        ("NYSE:BRK.B", "BRK.B"),
        ("MSFT (Microsoft Corporation)", "MSFT"),
        ("BRK.B", "BRK.B"),
        ("BRK-B", "BRK-B"),
    ],
)
def test_normalize_ticker_accepts_common_decorations(raw, expected):
    assert _normalize_ticker(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "    ",
        "lowercase only",
        "TOOLONGTICKERNAME",
        "!!!",
        None,
        123,
    ],
)
def test_normalize_ticker_rejects_garbage(raw):
    assert _normalize_ticker(raw) == ""


# ── Source label derivation ─────────────────────────────────────────────


def test_derive_source_label_dedupes_and_capitalizes():
    sources = [
        {"publisher": "reuters"},
        {"publisher": "reuters"},  # dup
        {"publisher": "bloomberg"},
        {"publisher": "sec"},
    ]
    assert _derive_source_label(sources) == ["Reuters", "Bloomberg", "Sec"]


def test_derive_source_label_caps_at_four():
    sources = [{"publisher": f"pub{i}"} for i in range(10)]
    assert len(_derive_source_label(sources)) == 4


def test_derive_source_label_empty_input():
    assert _derive_source_label([]) == []
    assert _derive_source_label(None) == []  # type: ignore[arg-type]


def test_derive_source_label_skips_blanks_and_non_dicts():
    sources = [
        {"publisher": ""},
        {"publisher": "  "},
        "not a dict",
        {"publisher": "reuters"},
    ]
    assert _derive_source_label(sources) == ["Reuters"]  # type: ignore[arg-type]


# ── FMP validation pass ─────────────────────────────────────────────────


class _FakeFMP:
    """Stand-in for FMPClient.get_company_profiles_batch / get_company_profile.

    Initialize with a dict of ticker → profile dict; absent tickers
    return as missing (FMP doesn't recognize them).
    """

    def __init__(self, profiles: Dict[str, Dict[str, Any]]):
        self._profiles = profiles
        self.requested: List[str] = []   # every ticker a profile was asked for

    async def get_company_profiles_batch(
        self, tickers: List[str]
    ) -> List[Dict[str, Any]]:
        self.requested.extend(tickers)
        return [self._profiles[t] for t in tickers if t in self._profiles]

    async def get_company_profile(
        self, ticker: str
    ) -> Optional[Dict[str, Any]]:
        return self._profiles.get(ticker, {})


def _make_service_with_fake_fmp(profiles: Dict[str, Dict[str, Any]]) -> CompetitorIntelService:
    svc = CompetitorIntelService()
    svc._fmp = _FakeFMP(profiles)  # type: ignore[assignment]
    return svc


@pytest.mark.asyncio
async def test_fmp_validate_drops_unknown_tickers():
    svc = _make_service_with_fake_fmp({
        "MSFT": {"symbol": "MSFT", "mktCap": 3_000_000_000_000},
        # "FAKE1" intentionally absent
    })
    validated, rejected = await svc._fmp_validate(["MSFT", "FAKE1"], focal="ORCL")
    assert [v["ticker"] for v in validated] == ["MSFT"]
    assert {"ticker": "FAKE1", "reason": "rejected_unknown_ticker"} in rejected


@pytest.mark.asyncio
async def test_fmp_validate_drops_zero_or_null_mkt_cap():
    svc = _make_service_with_fake_fmp({
        "MSFT": {"symbol": "MSFT", "mktCap": 3_000_000_000_000},
        "ZERO": {"symbol": "ZERO", "mktCap": 0},
        "NULL": {"symbol": "NULL", "mktCap": None},
        "NEG":  {"symbol": "NEG",  "mktCap": -100},
    })
    validated, rejected = await svc._fmp_validate(
        ["MSFT", "ZERO", "NULL", "NEG"], focal="ORCL",
    )
    assert [v["ticker"] for v in validated] == ["MSFT"]
    reasons = {r["ticker"]: r["reason"] for r in rejected}
    assert reasons == {
        "ZERO": "rejected_no_mktcap",
        "NULL": "rejected_no_mktcap",
        "NEG": "rejected_no_mktcap",
    }


@pytest.mark.asyncio
async def test_fmp_validate_keeps_small_cap_survivors():
    """Phase 2 has NO $27.3B floor. A $10B niche rival with verifiable
    revenue overlap must survive (Snowflake-vs-Oracle scenario).
    """
    svc = _make_service_with_fake_fmp({
        "BIG":   {"symbol": "BIG",   "mktCap": 500_000_000_000},
        "SMALL": {"symbol": "SMALL", "mktCap":  10_000_000_000},
    })
    validated, rejected = await svc._fmp_validate(["BIG", "SMALL"], focal="X")
    assert [v["ticker"] for v in validated] == ["BIG", "SMALL"]
    assert rejected == []


# ── End-to-end extract+validate with fake Gemini ───────────────────────


class _FakeGemini:
    """Returns whatever response dict is given at construction (or raises
    `error`). Records every prompt and counts calls — the bill."""

    def __init__(self, response: Optional[Dict[str, Any]] = None,
                 error: Optional[BaseException] = None):
        self._response = response
        self._error = error
        self.calls = 0
        self.prompts: List[str] = []

    async def generate_grounded_research(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls += 1
        self.prompts.append(kwargs.get("prompt", ""))
        if self._error is not None:
            raise self._error
        return self._response


def _gemini_response_with_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """A synthetic grounded-research response whose JSON fence holds `rows`."""
    import json as _json
    text = (
        "Some intro prose about the competitors.\n\n"
        "```json\n"
        + _json.dumps({"competitors": rows, "confidence": "high"}, indent=2)
        + "\n```\n"
    )
    return {
        "text": text,
        "tokens_used": 1234,
        "grounding_sources": [
            {"publisher": "reuters", "title": "reuters.com", "uri": "https://reuters.com/x"},
        ],
        "search_queries": ["who competes with broadcom"],
        "model": "gemini-2.5-flash",
    }


def _gemini_response_with_competitors(tickers: List[str]) -> Dict[str, Any]:
    """Build a synthetic Gemini grounded-research response payload."""
    competitors_json = ",\n    ".join(
        '{{"ticker":"{t}","name":"{t} Inc","segment_overlap":"x","source_citation":"10-K"}}'.format(t=t)
        for t in tickers
    )
    text = (
        "Some intro prose about the competitors.\n\n"
        "```json\n"
        "{\n"
        f'  "competitors": [\n    {competitors_json}\n  ],\n'
        '  "confidence": "high"\n'
        "}\n"
        "```\n"
    )
    return {
        "text": text,
        "tokens_used": 1234,
        "grounding_sources": [
            {"publisher": "reuters", "title": "reuters.com", "uri": "https://reuters.com/x"},
        ],
        "search_queries": ["who competes with oracle"],
        "model": "gemini-2.5-flash",
    }


@pytest.mark.asyncio
async def test_extract_and_validate_returns_validated_list_under_7():
    """Gemini returns 5 → no trim, all 5 returned (no mkt-cap floor applied)."""
    svc = CompetitorIntelService()
    svc._gemini = _FakeGemini(  # type: ignore[assignment]
        _gemini_response_with_competitors(["MSFT", "AMZN", "CRM", "SAP", "IBM"])
    )
    svc._fmp = _FakeFMP({  # type: ignore[assignment]
        "MSFT": {"symbol": "MSFT", "mktCap": 3_000_000_000_000},
        "AMZN": {"symbol": "AMZN", "mktCap": 2_000_000_000_000},
        "CRM":  {"symbol": "CRM",  "mktCap":   300_000_000_000},
        "SAP":  {"symbol": "SAP",  "mktCap":   200_000_000_000},
        "IBM":  {"symbol": "IBM",  "mktCap":   180_000_000_000},
    })
    result = await svc._extract_and_validate(
        ticker="ORCL",
        profile={"companyName": "Oracle", "sector": "Tech", "industry": "Software"},
    )
    assert result.status == "applied"
    assert result.validated_tickers == ["MSFT", "AMZN", "CRM", "SAP", "IBM"]
    assert result.rejected == []


@pytest.mark.asyncio
async def test_extract_and_validate_trims_only_when_over_7_by_research_rank_not_cap():
    """Gemini returns 9 → keep the first 7 by RESEARCH RANK, drop the last
    2 to `rejected` as `trimmed_to_7_by_gemini_rank`.

    Market caps run the OTHER way (A smallest … I largest), so a trim by cap
    would keep C..I and drop A and B — the two most direct rivals. That is the
    AVGO defect in miniature (QCOM, rank 2, could never appear)."""
    nine = ["A", "B", "C", "D", "E", "F", "G", "H", "I"]
    svc = CompetitorIntelService()
    svc._gemini = _FakeGemini(_gemini_response_with_competitors(nine))  # type: ignore[assignment]
    profiles = {
        t: {"symbol": t, "mktCap": (i + 1) * 10_000_000_000}   # ascending: rank ≠ cap
        for i, t in enumerate(nine)
    }
    svc._fmp = _FakeFMP(profiles)  # type: ignore[assignment]

    result = await svc._extract_and_validate(
        ticker="X",
        profile={"companyName": "X Corp"},
    )
    assert result.status == "applied_with_rejections"
    assert result.validated_tickers == ["A", "B", "C", "D", "E", "F", "G"]
    assert len(result.validated_tickers) == _COMPETITOR_MAX_N
    trimmed = [r for r in result.rejected if "trimmed_to" in r["reason"]]
    assert {r["ticker"] for r in trimmed} == {"H", "I"}
    assert all(r["reason"] == f"trimmed_to_{_COMPETITOR_MAX_N}_by_gemini_rank" for r in trimmed)


@pytest.mark.asyncio
async def test_extract_and_validate_drops_focal_self_suggestion():
    """If Gemini accidentally lists the focal itself, it gets removed
    and tagged in the audit's rejected list (reason='is_focal').
    """
    svc = CompetitorIntelService()
    svc._gemini = _FakeGemini(  # type: ignore[assignment]
        _gemini_response_with_competitors(["ORCL", "MSFT", "AMZN"])
    )
    svc._fmp = _FakeFMP({  # type: ignore[assignment]
        "MSFT": {"symbol": "MSFT", "mktCap": 3_000_000_000_000},
        "AMZN": {"symbol": "AMZN", "mktCap": 2_000_000_000_000},
    })
    result = await svc._extract_and_validate(
        ticker="ORCL",
        profile={"companyName": "Oracle"},
    )
    assert "ORCL" not in result.validated_tickers
    assert result.validated_tickers == ["MSFT", "AMZN"]
    assert any(r["reason"] == "is_focal" and r["ticker"] == "ORCL"
               for r in result.rejected)


@pytest.mark.asyncio
async def test_extract_and_validate_handles_missing_json_fence():
    """Plain-text Gemini response with no ```json``` block → gemini_error
    status, no validated tickers, but raw_text preserved in audit.
    """
    svc = CompetitorIntelService()
    svc._gemini = _FakeGemini({  # type: ignore[assignment]
        "text": "no code fence here",
        "tokens_used": 50,
        "grounding_sources": [],
        "search_queries": [],
        "model": "gemini-2.5-flash",
    })
    svc._fmp = _FakeFMP({})  # type: ignore[assignment]
    result = await svc._extract_and_validate(
        ticker="X", profile={"companyName": "X"},
    )
    assert result.status == "gemini_error"
    assert result.validated_tickers == []
    assert "no ```json``` code fence" in result.raw_response.get("error", "")


@pytest.mark.asyncio
async def test_extract_and_validate_handles_all_rejected():
    """Gemini returns plausible-shape tickers but FMP doesn't recognize
    any → rejected_no_validated, no cache write should follow.
    """
    svc = CompetitorIntelService()
    svc._gemini = _FakeGemini(  # type: ignore[assignment]
        _gemini_response_with_competitors(["FAKE1", "FAKE2"])
    )
    svc._fmp = _FakeFMP({})  # empty: FMP recognizes nothing  # type: ignore[assignment]
    result = await svc._extract_and_validate(
        ticker="X", profile={"companyName": "X"},
    )
    assert result.status == "rejected_no_validated"
    assert result.validated_tickers == []
    rejected_reasons = {r["reason"] for r in result.rejected}
    assert "rejected_unknown_ticker" in rejected_reasons


@pytest.mark.asyncio
async def test_extract_and_validate_dedupes_repeated_suggestions():
    """Gemini sometimes lists the same ticker twice — silently dedupe
    before validation.
    """
    svc = CompetitorIntelService()
    svc._gemini = _FakeGemini(  # type: ignore[assignment]
        _gemini_response_with_competitors(["MSFT", "MSFT", "AMZN"])
    )
    svc._fmp = _FakeFMP({  # type: ignore[assignment]
        "MSFT": {"symbol": "MSFT", "mktCap": 3_000_000_000_000},
        "AMZN": {"symbol": "AMZN", "mktCap": 2_000_000_000_000},
    })
    result = await svc._extract_and_validate(
        ticker="X", profile={"companyName": "X"},
    )
    assert result.validated_tickers == ["MSFT", "AMZN"]


# ═══════════════════════════════════════════════════════════════════════
# 2026-10-01 — AVGO competitors (TestFlight #57): prompt, relationship
# filter, segment labels, version marker, stale-on-error.
# ═══════════════════════════════════════════════════════════════════════


_PG_UNKNOWN_COLUMN = "PGRST204"


class _PgErr(Exception):
    """Stand-in for postgrest.APIError: classifiers key on `.code` only."""

    def __init__(self, code: Any):
        super().__init__(f"pg error {code!r}")
        self.code = code


class _FakeQuery:
    def __init__(self, sb: "_FakeSB", table: str):
        self._sb = sb
        self._table = table
        self._op: Optional[str] = None
        self._payload: Optional[Dict[str, Any]] = None
        self._filters: Dict[str, Any] = {}

    def select(self, cols: str) -> "_FakeQuery":
        self._op = "select"
        self._sb.selects.append((self._table, cols))
        return self

    def eq(self, key: str, value: Any) -> "_FakeQuery":
        self._filters[key] = value
        return self

    def limit(self, _n: int) -> "_FakeQuery":
        return self

    def upsert(self, row: Dict[str, Any]) -> "_FakeQuery":
        self._op, self._payload = "upsert", dict(row)
        return self

    def insert(self, row: Dict[str, Any]) -> "_FakeQuery":
        self._op, self._payload = "insert", dict(row)
        return self

    def execute(self) -> SimpleNamespace:
        sb = self._sb
        if self._op == "select":
            if sb.read_error is not None:
                raise sb.read_error
            rows = [
                dict(r) for r in sb.tables.get(self._table, [])
                if all(r.get(k) == v for k, v in self._filters.items())
            ]
            return SimpleNamespace(data=rows)
        if self._op == "insert":
            sb.inserts.append((self._table, self._payload))
            sb.tables.setdefault(self._table, []).append(dict(self._payload))
            return SimpleNamespace(data=[self._payload])
        # upsert
        sb.upserts.append((self._table, dict(self._payload)))
        if sb.upsert_errors:
            err = sb.upsert_errors.pop(0)
            if err is not None:
                raise err
        row = dict(self._payload)
        if self._table == "competitor_intel_cache":
            if "competitor_details" in row and not sb.has_details_column:
                raise _PgErr(_PG_UNKNOWN_COLUMN)
            if sb.has_details_column:
                row.setdefault("competitor_details", {})   # the column's DEFAULT
        table = sb.tables.setdefault(self._table, [])
        table[:] = [r for r in table if r.get("ticker") != row.get("ticker")]
        table.append(row)
        return SimpleNamespace(data=[row])


class _FakeSB:
    """In-memory Supabase. `has_details_column=False` models the database BEFORE
    migration 186: a payload naming `competitor_details` fails with PGRST204, and
    `select("*")` rows carry no such key."""

    def __init__(self, *, has_details_column: bool = True):
        self.has_details_column = has_details_column
        self.tables: Dict[str, List[Dict[str, Any]]] = {}
        self.selects: List[Any] = []
        self.upserts: List[Any] = []
        self.inserts: List[Any] = []
        self.upsert_errors: List[Optional[BaseException]] = []
        self.read_error: Optional[BaseException] = None

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self, name)

    def seed_cache_row(self, ticker: str, tickers: List[str], *, model_version: Any,
                       details: Any = None, computed_at: Optional[datetime] = None,
                       expires_in_days: float = 50) -> None:
        now = datetime.now(timezone.utc)
        computed = computed_at or (now - timedelta(days=10))
        row: Dict[str, Any] = {
            "ticker": ticker,
            "competitor_tickers": tickers,
            "source_labels": ["Reuters"],
            "computed_at": computed.isoformat(),
            "expires_at": (now + timedelta(days=expires_in_days)).isoformat(),
            "model_version": model_version,
        }
        if self.has_details_column:
            row["competitor_details"] = details if details is not None else {}
        self.tables.setdefault("competitor_intel_cache", []).append(row)

    def cache_upserts(self) -> List[Dict[str, Any]]:
        return [p for t, p in self.upserts if t == "competitor_intel_cache"]

    def audits(self) -> List[Dict[str, Any]]:
        return [p for t, p in self.inserts if t == "competitor_intel_audit"]


def _use_fake_sb(monkeypatch, sb: _FakeSB) -> _FakeSB:
    # Module-level `from app.database import get_supabase` → patch the binding the
    # service actually calls (testing.md "patch the binding the caller uses").
    monkeypatch.setattr(cis, "get_supabase", lambda: sb)
    return sb


_AVGO_PROFILES = {
    t: {"symbol": t, "mktCap": cap}
    for t, cap in {
        "MRVL": 60e9, "QCOM": 170e9, "INTC": 90e9, "NVDA": 4.3e12,
        "MSFT": 3.8e12, "IBM": 250e9, "GOOGL": 2.9e12, "NTNX": 20e9,
    }.items()
}


def _row(t: str, rel: Any = "direct", seg: Any = "Custom AI accelerators", **extra: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {"ticker": t, "name": f"{t} Inc", "source_citation": "10-K"}
    if rel is not _MISSING:
        row["relationship"] = rel
    if seg is not _MISSING:
        row["segment"] = seg
    row.update(extra)
    return row


_MISSING = object()


def _svc(gemini: _FakeGemini, profiles: Optional[Dict[str, Dict[str, Any]]] = None) -> CompetitorIntelService:
    svc = CompetitorIntelService()
    svc._gemini = gemini  # type: ignore[assignment]
    svc._fmp = _FakeFMP(profiles if profiles is not None else _AVGO_PROFILES)  # type: ignore[assignment]
    return svc


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(t, str) for t in value)


# ── 1. The prompt ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_prompt_carries_the_date_the_order_rule_and_the_exclusion_rule():
    gem = _FakeGemini(_gemini_response_with_rows([_row("MRVL")]))
    svc = _svc(gem)
    await svc._extract_and_validate(
        "AVGO", {"companyName": "Broadcom Inc."}, today=date(2026, 10, 1),
    )
    prompt = gem.prompts[0]
    # Dated, and pinned to current sources (it once searched a "2023 10-K").
    assert "Today's date is 2026-10-01" in prompt
    assert "latest fiscal-year 10-K" in prompt
    assert "last two earnings calls" in prompt
    assert "past 12 months" in prompt
    # Order: most direct first, by share of revenue contested — not by size.
    assert "MOST DIRECT FIRST" in prompt
    assert "how much of AVGO's revenue each one contests" in prompt
    assert "not simply the largest" in prompt
    # Exclusion of customers / suppliers / design partners, with the carve-out.
    assert "EXCLUDE companies that are mainly AVGO's customers, suppliers or design partners" in prompt
    assert "unless they also sell a competing product to third parties" in prompt
    # The two new per-row fields.
    assert '"relationship": "direct" | "partial" | "customer_partner"' in prompt
    assert '"segment":' in prompt and "at most 48 characters" in prompt
    # Fully formatted, and no model / vendor identity in the model-facing text.
    assert "{today}" not in prompt and "{ticker}" not in prompt
    assert "gemini" not in prompt.lower()


@pytest.mark.asyncio
async def test_prompt_date_defaults_to_today_utc():
    gem = _FakeGemini(_gemini_response_with_rows([_row("MRVL")]))
    await _svc(gem)._extract_and_validate("AVGO", {"companyName": "Broadcom"})
    today = datetime.now(timezone.utc).date()
    # Either side of a UTC midnight that falls between the two reads.
    assert any(
        f"Today's date is {d.isoformat()}" in gem.prompts[0]
        for d in (today, today - timedelta(days=1))
    )


# ── 2. The relationship filter ──────────────────────────────────────────


@pytest.mark.parametrize(
    "label",
    [
        "customer_partner", "Customer/Partner", "customer or partner", "CUSTOMER",
        "partner", "supplier", "Supplier", "customer_or_partner", "design partner",
        "Key Supplier", "customers & partners", " customer-partner ",
    ],
)
@pytest.mark.asyncio
async def test_a_customer_or_partner_row_is_dropped_before_any_fmp_call(label):
    gem = _FakeGemini(_gemini_response_with_rows([
        _row("MRVL"), _row("GOOGL", rel=label, seg="Custom chip buyer"), _row("QCOM", rel="partial"),
    ]))
    svc = _svc(gem)
    result = await svc._extract_and_validate("AVGO", {"companyName": "Broadcom"})
    assert result.validated_tickers == ["MRVL", "QCOM"]
    assert {"ticker": "GOOGL", "reason": "customer_or_partner"} in result.rejected
    assert "GOOGL" not in result.suggested_tickers
    assert "GOOGL" not in svc._fmp.requested, "a dropped row must cost no FMP profile call"
    assert "GOOGL" not in result.details
    assert result.status == "applied_with_rejections"


@pytest.mark.parametrize(
    "label",
    ["direct", "Direct", "partial", "PARTIAL", "", "   ", None, 123, ["direct"],
     "partial_customer", "competitor and customer", "weird-label", _MISSING],
)
@pytest.mark.asyncio
async def test_a_missing_or_unrecognised_relationship_is_kept(label):
    gem = _FakeGemini(_gemini_response_with_rows([_row("MRVL", rel=label), _row("QCOM")]))
    result = await _svc(gem)._extract_and_validate("AVGO", {"companyName": "Broadcom"})
    assert result.validated_tickers == ["MRVL", "QCOM"]
    assert not any(r["reason"] == "customer_or_partner" for r in result.rejected)
    assert result.status == "applied"


@pytest.mark.asyncio
async def test_an_unrecognised_relationship_is_logged_at_info(caplog):
    gem = _FakeGemini(_gemini_response_with_rows([_row("MRVL", rel="frenemy")]))
    with caplog.at_level(logging.INFO, logger=cis.__name__):
        await _svc(gem)._extract_and_validate("AVGO", {"companyName": "Broadcom"})
    assert any(
        "unrecognised relationship" in r.getMessage() and "MRVL" in r.getMessage()
        and r.levelno == logging.INFO
        for r in caplog.records
    )


def test_relationship_normalisation_and_classification():
    assert _normalize_relationship("Customer / Partner") == "customer_partner"
    assert _normalize_relationship("  DIRECT  ") == "direct"
    assert _normalize_relationship(None) == ""
    assert _normalize_relationship(42) == ""
    assert _normalize_relationship("x" * 5000) == "x" * 64   # capped before the regex
    assert _is_customer_or_partner("customer_partner")
    assert _is_customer_or_partner("strategic_partner")
    # Any word that is not a role / filler means the company also competes.
    assert not _is_customer_or_partner("partial_customer")
    assert not _is_customer_or_partner("competitor_and_customer")
    assert not _is_customer_or_partner("direct")
    assert not _is_customer_or_partner("partial")
    # Filler alone names no role.
    assert not _is_customer_or_partner("and_or")


@pytest.mark.asyncio
async def test_every_row_dropped_is_rejected_no_validated_and_bills_no_fmp():
    gem = _FakeGemini(_gemini_response_with_rows([
        _row("GOOGL", rel="customer_partner"), _row("META", rel="customer"),
    ]))
    svc = _svc(gem)
    result = await svc._extract_and_validate("AVGO", {"companyName": "Broadcom"})
    assert result.status == "rejected_no_validated"   # within migration 054's CHECK
    assert result.validated_tickers == []
    assert {r["reason"] for r in result.rejected} == {"customer_or_partner"}
    assert svc._fmp.requested == []


@pytest.mark.parametrize(
    "rows, kept, dropped",
    [
        # First row decides: a later "direct" cannot resurrect a dropped customer …
        ([_row("MSFT", rel="customer_partner"), _row("MSFT", rel="direct"), _row("AMZN")],
         ["AMZN"], ["MSFT"]),
        # … and a later "customer" cannot drop a kept rival.
        ([_row("MSFT", rel="direct"), _row("MSFT", rel="customer_partner"), _row("AMZN")],
         ["MSFT", "AMZN"], []),
    ],
)
@pytest.mark.asyncio
async def test_a_duplicate_ticker_with_conflicting_labels_follows_its_first_row(rows, kept, dropped):
    profiles = {
        "MSFT": {"symbol": "MSFT", "mktCap": 3e12}, "AMZN": {"symbol": "AMZN", "mktCap": 2e12},
    }
    result = await _svc(_FakeGemini(_gemini_response_with_rows(rows)), profiles)._extract_and_validate(
        "ORCL", {"companyName": "Oracle"},
    )
    assert result.validated_tickers == kept
    assert [r["ticker"] for r in result.rejected if r["reason"] == "customer_or_partner"] == dropped
    # Never both suggested AND rejected in one audit row.
    assert not set(result.suggested_tickers) & {r["ticker"] for r in result.rejected}


@pytest.mark.asyncio
async def test_a_duplicate_keeps_the_first_rows_segment():
    rows = [_row("MRVL", seg="Custom AI accelerators"), _row("MRVL", seg="Something else")]
    result = await _svc(_FakeGemini(_gemini_response_with_rows(rows)))._extract_and_validate(
        "AVGO", {"companyName": "Broadcom"},
    )
    assert result.details == {"MRVL": {"segment": "Custom AI accelerators"}}


# ── 3. The segment cleaner ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Custom AI accelerators", "Custom AI accelerators"),
        ("Custom AI accelerators [1] & networking [2, 3]", "Custom AI accelerators & networking"),
        ("Ethernet switching [12-14]", "Ethernet switching"),
        ("Cloud infra (see https://example.com/a?b=c)", "Cloud infra (see)"),
        ("Cloud infra www.example.com", "Cloud infra"),
        ("[Enterprise software](https://x.y/z)", "Enterprise software"),
        ("**Virtualization** _software_", "Virtualization software"),
        ("`Storage` # networking ~~x~~", "Storage networking x"),
        ("<b>Broadband</b> chips", "Broadband chips"),
        ("Line one\nline two\ttabbed\x00nul", "Line one line two tabbed nul"),
        ("Zero​width ‮override", "Zerowidth override"),
        ("   - Enterprise databases.  ", "Enterprise databases"),
        ('"Quoted label"', "Quoted label"),
        ("Custom AI accelerators…", "Custom AI accelerators…"),
    ],
)
def test_segment_cleaner_cases(raw, expected):
    assert _clean_segment(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [None, 42, 4.2, ["x"], {"segment": "x"}, "", "   ", "[1][2]", "https://only.a/url",
     "**  **", "\x00\x01", "Per Gemini: networking", "As the prompt asks", "LLM output",
     "an llms answer", "large language model chips", "OpenAI partnership", "chatgpt"],
)
def test_segment_cleaner_rejects_junk_and_model_talk(raw):
    assert _clean_segment(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "Custom AI accelerators, Ethernet switching, and broadband access silicon",
        "Networking silicon (Ethernet switches and custom accelerators for hyperscalers)",
        "Supercalifragilisticexpialidocious-networking-silicon-and-more-stuff-here",
        "Ab (" + "x" * 60,
        "word " * 400,                    # 2 KB
        "x" * 2048,                       # 2 KB, no spaces at all
        "Networking for the largest cloud and the hyperscale and enterprise markets",
    ],
)
def test_segment_cleaner_caps_long_labels_on_a_word_boundary(raw):
    out = _clean_segment(raw)
    assert out is not None
    assert len(out) <= SEGMENT_MAX_CHARS
    assert _clean_segment(out) == out, "re-cleaning a stored label must not change it"
    assert len(raw.strip()) > SEGMENT_MAX_CHARS
    assert out.endswith("…") or out.endswith("…)")
    assert out.count("(") == out.count(")"), "never a dangling open parenthesis"
    assert not out.rstrip("…)").endswith((" and", " or", " the", ",")), (
        "a cut label must not end on a connector word"
    )


def test_segment_cleaner_exactly_at_the_cap_is_untouched():
    label = "A" * SEGMENT_MAX_CHARS
    assert _clean_segment(label) == label
    assert _clean_segment(label + "B") == "A" * (SEGMENT_MAX_CHARS - 1) + "…"


@pytest.mark.asyncio
async def test_segments_are_recorded_for_survivors_only_with_a_legacy_fallback():
    rows = [
        _row("MRVL", seg="Custom AI accelerators [3]"),
        _row("QCOM", seg=_MISSING, segment_overlap="Qualcomm competes in RF front-end modules."),
        _row("NOPE", seg="Unknown to FMP"),            # fails FMP validation
        _row("INTC", seg="https://only.example/url"),  # cleans to nothing
    ]
    result = await _svc(_FakeGemini(_gemini_response_with_rows(rows)))._extract_and_validate(
        "AVGO", {"companyName": "Broadcom"},
    )
    assert result.validated_tickers == ["MRVL", "QCOM", "INTC"]
    assert result.details == {
        "MRVL": {"segment": "Custom AI accelerators"},
        "QCOM": {"segment": "Qualcomm competes in RF front-end modules"},
    }


# ── 4/5. Version marker + DB read ──────────────────────────────────────


@pytest.mark.parametrize(
    "model_version, column_present, current",
    [
        (f"gemini-2.5-flash|{CACHE_MARKER}", True, True),
        (f"gemini-2.5-flash|{CACHE_MARKER}", False, True),
        (f"gemini-2.5-flash|{CACHE_MARKER_NODETAILS}", False, True),
        # Written through the fallback, and 186 has since been applied → stale.
        (f"gemini-2.5-flash|{CACHE_MARKER_NODETAILS}", True, False),
        ("gemini-2.5-flash", True, False),           # every row written before this change
        ("gemini-2.5-flash", False, False),
        ("gemini-2.5-flash|cip-v1", True, False),
        (f"gemini-2.5-flash|{CACHE_MARKER}x", True, False),
        (CACHE_MARKER, True, False),                 # no "|" separator
        (None, True, False),
        ("", True, False),
        (123, True, False),
    ],
)
def test_marker_currency(model_version, column_present, current):
    assert _marker_is_current(model_version, details_column_present=column_present) is current


def test_the_two_markers_cannot_be_confused():
    assert CACHE_MARKER != CACHE_MARKER_NODETAILS
    assert not f"m|{CACHE_MARKER_NODETAILS}".endswith(f"|{CACHE_MARKER}")


def test_constants_are_not_in_the_future():
    now = datetime.now(timezone.utc)
    assert COMPETITOR_INTEL_SCHEMA_FLOOR <= now, (
        "a floor in the future turns every cached row into a miss — every collection "
        "re-bills a grounded call until the date passes"
    )
    assert COMPETITOR_INTEL_SCHEMA_FLOOR.tzinfo is not None
    assert _NEGATIVE_TTL_SECONDS == 1800
    assert SEGMENT_MAX_CHARS == 48


def test_read_cache_flags_a_legacy_row_stale_and_keeps_it_usable(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row("AVGO", ["NVDA", "MRVL"], model_version="gemini-2.5-flash")
    row = CompetitorIntelService()._read_cache("AVGO")
    assert row is not None and row.stale is True
    assert row.tickers == ["NVDA", "MRVL"] and row.details == {}
    assert sb.selects == [("competitor_intel_cache", "*")], (
        "select('*') reads the same before and after 186 — a named column list would "
        "400 before it and miss the column after"
    )


def test_read_cache_current_row_carries_cleaned_details(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row(
        "AVGO", ["MRVL", "QCOM"], model_version=f"m|{CACHE_MARKER}",
        details={
            "MRVL": {"segment": "Custom AI accelerators"},
            "qcom": {"segment": "x" * 300},                 # lower-case key, overlong
            "GOOGL": {"segment": "Not in this row's list"},  # foreign key → dropped
            "NVDA": "not a dict",
        },
    )
    row = CompetitorIntelService()._read_cache("AVGO")
    assert row is not None and row.stale is False
    assert row.details["MRVL"] == {"segment": "Custom AI accelerators"}
    assert len(row.details["QCOM"]["segment"]) <= SEGMENT_MAX_CHARS
    assert set(row.details) == {"MRVL", "QCOM"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"expires_in_days": -1},                                              # expired
        {"computed_at": COMPETITOR_INTEL_SCHEMA_FLOOR - timedelta(days=1)},  # pre-floor
    ],
)
def test_read_cache_expired_or_pre_floor_rows_are_a_plain_miss(monkeypatch, kwargs):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row("AVGO", ["MRVL"], model_version=f"m|{CACHE_MARKER}", **kwargs)
    assert CompetitorIntelService()._read_cache("AVGO") is None


@pytest.mark.parametrize("tickers", [[], None, "MRVL", [None, "", "  ", 7]])
def test_read_cache_rows_without_usable_tickers_are_a_miss(monkeypatch, tickers):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row("AVGO", tickers, model_version=f"m|{CACHE_MARKER}")
    assert CompetitorIntelService()._read_cache("AVGO") is None


def test_read_cache_failure_is_a_logged_miss(monkeypatch, caplog):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.read_error = _PgErr(520)
    with caplog.at_level(logging.WARNING, logger=cis.__name__):
        assert CompetitorIntelService()._read_cache("AVGO") is None
    assert any("cache read failed for AVGO" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, {}), ([], {}), ("not json", {}), (42, {}),
        ('{"MRVL": {"segment": "Networking"}}', {"MRVL": {"segment": "Networking"}}),
        ({"MRVL": {"segment": None}}, {}),
        ({"MRVL": {"segment": "Gemini says so"}}, {}),
        ({7: {"segment": "x"}}, {}),
        ({" mrvl ": {"segment": "Networking", "extra": 1}}, {"MRVL": {"segment": "Networking"}}),
    ],
)
def test_coerce_details_degrades_malformed_shapes(raw, expected):
    assert _coerce_details(raw, ["MRVL"]) == expected


# ── 4. The write: full row, unknown-column fallback, other errors ──────


def test_write_cache_writes_the_full_row_with_the_current_marker(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    details = {"MRVL": {"segment": "Custom AI accelerators"}}
    CompetitorIntelService()._write_cache("AVGO", ["MRVL"], ["Reuters"], "gemini-x", details)
    [payload] = sb.cache_upserts()
    assert payload["model_version"] == f"gemini-x|{CACHE_MARKER}"
    assert payload["competitor_details"] == details
    assert payload["competitor_tickers"] == ["MRVL"]
    details["MRVL"]["segment"] = "mutated afterwards"
    assert payload["competitor_details"]["MRVL"]["segment"] == "Custom AI accelerators"


def test_write_cache_without_a_model_still_stamps_a_marker(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    CompetitorIntelService()._write_cache("AVGO", ["MRVL"], [], None)
    assert sb.cache_upserts()[0]["model_version"] == f"unknown|{CACHE_MARKER}"


def test_write_cache_before_migration_186_falls_back_to_tickers_only(monkeypatch, caplog):
    sb = _use_fake_sb(monkeypatch, _FakeSB(has_details_column=False))
    with caplog.at_level(logging.WARNING, logger=cis.__name__):
        CompetitorIntelService()._write_cache(
            "AVGO", ["MRVL", "QCOM"], ["Reuters"], "gemini-x",
            {"MRVL": {"segment": "Custom AI accelerators"}},
        )
    first, second = sb.cache_upserts()
    assert "competitor_details" in first
    assert "competitor_details" not in second
    assert second["model_version"] == f"gemini-x|{CACHE_MARKER_NODETAILS}"
    assert second["competitor_tickers"] == ["MRVL", "QCOM"]
    assert any("migration 186 not applied" in r.getMessage() for r in caplog.records)
    # Readable as CURRENT while the column is still missing (no re-extract loop) …
    row = CompetitorIntelService()._read_cache("AVGO")
    assert row is not None and row.stale is False and row.details == {}
    # … and STALE the moment 186 lands (the column appears, with its DEFAULT).
    sb.has_details_column = True
    sb.tables["competitor_intel_cache"][0]["competitor_details"] = {}
    row = CompetitorIntelService()._read_cache("AVGO")
    assert row is not None and row.stale is True


@pytest.mark.parametrize(
    "error",
    [_PgErr("23505"), _PgErr(520), _PgErr("PGRST116"), RuntimeError("boom"), _PgErr(None)],
)
def test_write_cache_other_errors_are_logged_and_not_retried(monkeypatch, caplog, error):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.upsert_errors = [error]
    with caplog.at_level(logging.WARNING, logger=cis.__name__):
        CompetitorIntelService()._write_cache(
            "AVGO", ["MRVL"], [], "m", {"MRVL": {"segment": "Networking"}},
        )
    assert len(sb.cache_upserts()) == 1, "only an unknown-column error earns the fallback"
    assert any("cache write failed for AVGO" in r.getMessage() for r in caplog.records)
    assert not any("migration 186" in r.getMessage() for r in caplog.records)


def test_write_cache_fallback_failure_is_logged_not_raised(monkeypatch, caplog):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.upsert_errors = [_PgErr("42703"), _PgErr(502)]
    with caplog.at_level(logging.WARNING, logger=cis.__name__):
        CompetitorIntelService()._write_cache("AVGO", ["MRVL"], [], "m", {})
    assert len(sb.cache_upserts()) == 2
    assert any("tickers-only cache write failed for AVGO" in r.getMessage() for r in caplog.records)


def test_write_cache_with_no_tickers_writes_nothing(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    CompetitorIntelService()._write_cache("AVGO", [], [], "m", {})
    assert sb.cache_upserts() == []


# ── 6. get_competitors end to end: tiers, stale-on-error, negative entry ─


@pytest.mark.asyncio
async def test_fresh_extraction_then_mem_both_return_a_plain_str_list(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    gem = _FakeGemini(_gemini_response_with_rows([
        _row("MRVL", seg="Custom AI accelerators"), _row("QCOM", seg="Networking"),
        _row("GOOGL", rel="customer_partner"),
    ]))
    svc = _svc(gem)
    first = await svc.get_competitors("AVGO", {"companyName": "Broadcom"})
    second = await svc.get_competitors("AVGO", {"companyName": "Broadcom"})
    assert first == second == ["MRVL", "QCOM"]
    assert _is_str_list(first) and _is_str_list(second)
    assert gem.calls == 1
    [payload] = sb.cache_upserts()
    assert payload["model_version"] == f"gemini-2.5-flash|{CACHE_MARKER}"
    assert payload["competitor_details"] == {
        "MRVL": {"segment": "Custom AI accelerators"}, "QCOM": {"segment": "Networking"},
    }
    assert await svc.get_competitor_details("AVGO") == payload["competitor_details"]
    [audit] = sb.audits()
    assert audit["model_version"] == f"gemini-2.5-flash|{CACHE_MARKER}"
    assert audit["status"] == "applied_with_rejections"


@pytest.mark.asyncio
async def test_a_current_db_row_is_served_without_extraction_as_a_str_list(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row(
        "AVGO", ["MRVL", "QCOM"], model_version=f"m|{CACHE_MARKER}",
        details={"MRVL": {"segment": "Custom AI accelerators"}},
    )
    gem = _FakeGemini(error=AssertionError("must not be called"))
    svc = _svc(gem)
    out = await svc.get_competitors("AVGO", {})
    assert out == ["MRVL", "QCOM"] and _is_str_list(out)
    again = await svc.get_competitors("AVGO", {})       # memory tier
    assert again == ["MRVL", "QCOM"] and _is_str_list(again)
    assert gem.calls == 0
    assert await svc.get_competitor_details("AVGO") == {"MRVL": {"segment": "Custom AI accelerators"}}


@pytest.mark.asyncio
async def test_a_stale_row_is_re_extracted_and_replaced(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row("AVGO", ["NVDA", "MRVL", "GOOGL"], model_version="gemini-2.5-flash")
    gem = _FakeGemini(_gemini_response_with_rows([_row("MRVL"), _row("QCOM")]))
    out = await _svc(gem).get_competitors("AVGO", {"companyName": "Broadcom"})
    assert out == ["MRVL", "QCOM"]
    assert gem.calls == 1
    assert sb.cache_upserts()[0]["model_version"].endswith(f"|{CACHE_MARKER}")


@pytest.mark.asyncio
async def test_a_nodetails_row_is_re_extracted_once_the_column_exists(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB(has_details_column=True))
    sb.seed_cache_row("AVGO", ["MRVL"], model_version=f"m|{CACHE_MARKER_NODETAILS}")
    gem = _FakeGemini(_gemini_response_with_rows([_row("MRVL", seg="Custom AI accelerators")]))
    svc = _svc(gem)
    assert await svc.get_competitors("AVGO", {}) == ["MRVL"]
    assert gem.calls == 1
    [payload] = sb.cache_upserts()
    assert payload["model_version"].endswith(f"|{CACHE_MARKER}")
    assert payload["competitor_details"] == {"MRVL": {"segment": "Custom AI accelerators"}}


@pytest.mark.asyncio
async def test_a_nodetails_row_is_not_re_extracted_while_the_column_is_missing(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB(has_details_column=False))
    sb.seed_cache_row("AVGO", ["MRVL"], model_version=f"m|{CACHE_MARKER_NODETAILS}")
    gem = _FakeGemini(error=AssertionError("must not be called"))
    assert await _svc(gem).get_competitors("AVGO", {}) == ["MRVL"]
    assert gem.calls == 0, "a pre-186 row must not re-bill on every collection"


@pytest.mark.parametrize(
    "gemini",
    [
        lambda: _FakeGemini(error=RuntimeError("quota")),                                   # gemini_error
        lambda: _FakeGemini({"text": "no fence", "model": "m"}),                             # no JSON
        lambda: _FakeGemini(_gemini_response_with_rows([_row("GOOGL", rel="customer")])),    # all dropped
        lambda: _FakeGemini(_gemini_response_with_rows([_row("FAKE1")])),                    # all unverifiable
    ],
)
@pytest.mark.asyncio
async def test_a_failed_re_extraction_serves_the_stale_list_and_stops_re_billing(
    monkeypatch, caplog, gemini,
):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row(
        "AVGO", ["NVDA", "MRVL"], model_version="gemini-2.5-flash",
        details={"MRVL": {"segment": "Old label"}},
    )
    gem = gemini()
    svc = _svc(gem)
    with caplog.at_level(logging.WARNING, logger=cis.__name__):
        first = await svc.get_competitors("AVGO", {"companyName": "Broadcom"})
    assert first == ["NVDA", "MRVL"] and _is_str_list(first)
    assert any("serving the stale cached list" in r.getMessage() for r in caplog.records)
    # The 30-min negative entry: the next pre-warm answers from memory, no new bill.
    second = await svc.get_competitors("AVGO", {"companyName": "Broadcom"})
    assert second == ["NVDA", "MRVL"] and _is_str_list(second)
    assert gem.calls == 1
    expires_at = cis._mem_cache["AVGO"][0]
    assert expires_at - time.time() > _NEGATIVE_TTL_SECONDS - 60
    # The stale row is left alone (nothing overwrote it with a failure).
    assert sb.cache_upserts() == []
    assert await svc.get_competitor_details("AVGO") == {"MRVL": {"segment": "Old label"}}
    assert len(sb.audits()) == 1


@pytest.mark.asyncio
async def test_a_cold_failure_leaves_a_negative_entry_and_answers_none(monkeypatch):
    _use_fake_sb(monkeypatch, _FakeSB())
    gem = _FakeGemini(error=RuntimeError("quota"))
    svc = _svc(gem)
    assert await svc.get_competitors("AVGO", {}) is None
    assert await svc.get_competitors("AVGO", {}) is None
    assert gem.calls == 1, "a failing ticker must not re-bill on every pre-warm"
    assert await svc.get_competitor_details("AVGO") == {}


@pytest.mark.asyncio
async def test_the_negative_entry_expires(monkeypatch):
    _use_fake_sb(monkeypatch, _FakeSB())
    gem = _FakeGemini(error=RuntimeError("quota"))
    svc = _svc(gem)
    assert await svc.get_competitors("AVGO", {}) is None
    t0 = time.time()
    monkeypatch.setattr(cis.time, "time", lambda: t0 + _NEGATIVE_TTL_SECONDS + 1)
    assert await svc.get_competitors("AVGO", {}) is None
    assert gem.calls == 2


@pytest.mark.asyncio
async def test_kill_switch_serves_the_stale_list_without_a_call(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row("AVGO", ["NVDA", "MRVL"], model_version="gemini-2.5-flash")
    monkeypatch.setattr(cis.settings, "COMPETITOR_INTEL_AI_ENABLED", False)
    gem = _FakeGemini(error=AssertionError("must not be called"))
    out = await _svc(gem).get_competitors("AVGO", {})
    assert out == ["NVDA", "MRVL"]
    assert gem.calls == 0
    [audit] = sb.audits()
    assert audit["status"] == "skipped_kill_switch" and audit["model_version"] is None


@pytest.mark.asyncio
async def test_kill_switch_with_nothing_cached_answers_none(monkeypatch):
    _use_fake_sb(monkeypatch, _FakeSB())
    monkeypatch.setattr(cis.settings, "COMPETITOR_INTEL_AI_ENABLED", False)
    gem = _FakeGemini(error=AssertionError("must not be called"))
    assert await _svc(gem).get_competitors("AVGO", {}) is None
    assert gem.calls == 0


@pytest.mark.asyncio
async def test_a_forced_failure_answers_none_and_leaves_memory_alone(monkeypatch):
    """The quarterly batch counts honestly (no stale inflation) and must not plant a
    negative entry that would hide the servable DB row from on-demand callers."""
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row("AVGO", ["MRVL"], model_version=f"m|{CACHE_MARKER}")
    gem = _FakeGemini(error=RuntimeError("quota"))
    svc = _svc(gem)
    assert await svc.get_competitors("AVGO", {}, force_refresh=True) is None
    assert "AVGO" not in cis._mem_cache
    assert await svc.get_competitors("AVGO", {}) == ["MRVL"]   # the DB row, still served
    assert gem.calls == 1


@pytest.mark.asyncio
async def test_a_joiner_with_a_stale_row_gets_it_when_a_forced_leader_fails(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row("AVGO", ["NVDA", "MRVL"], model_version="gemini-2.5-flash")
    release = asyncio.Event()
    entered = asyncio.Event()

    class _BlockingGemini(_FakeGemini):
        async def generate_grounded_research(self, **kwargs: Any) -> Dict[str, Any]:
            self.calls += 1
            entered.set()
            await release.wait()
            raise RuntimeError("quota")

    gem = _BlockingGemini()
    svc = _svc(gem)
    leader = asyncio.create_task(svc.get_competitors("AVGO", {}, force_refresh=True))
    await asyncio.wait_for(entered.wait(), timeout=2)
    joiner = asyncio.create_task(svc.get_competitors("AVGO", {}))
    for _ in range(20):
        await asyncio.sleep(0)
        if joiner.done():
            break
    release.set()
    assert await asyncio.wait_for(leader, timeout=2) is None
    assert await asyncio.wait_for(joiner, timeout=2) == ["NVDA", "MRVL"]
    assert gem.calls == 1


@pytest.mark.asyncio
async def test_invalid_ticker_answers_none_and_empty_details():
    svc = CompetitorIntelService()
    assert await svc.get_competitors("!!!", {}) is None
    assert await svc.get_competitor_details("!!!") == {}
    assert await svc.get_competitor_details(None) == {}  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_competitor_details_reads_the_db_when_memory_is_cold(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row(
        "AVGO", ["MRVL"], model_version=f"m|{CACHE_MARKER}",
        details={"MRVL": {"segment": "Custom AI accelerators"}},
    )
    svc = CompetitorIntelService()
    assert await svc.get_competitor_details("avgo") == {"MRVL": {"segment": "Custom AI accelerators"}}
    assert "AVGO" not in cis._mem_cache, "a details read must not short-circuit get_competitors"
    assert await svc.get_competitor_details("MSFT") == {}


@pytest.mark.asyncio
async def test_get_competitor_details_never_raises(monkeypatch, caplog):
    svc = CompetitorIntelService()

    def _boom(_ticker):
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "_read_cache", _boom)
    with caplog.at_level(logging.WARNING, logger=cis.__name__):
        assert await svc.get_competitor_details("AVGO") == {}
    assert any("get_competitor_details failed for AVGO" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_memory_hands_out_copies_not_its_own_lists(monkeypatch):
    _use_fake_sb(monkeypatch, _FakeSB())
    gem = _FakeGemini(_gemini_response_with_rows([_row("MRVL"), _row("QCOM")]))
    svc = _svc(gem)
    out = await svc.get_competitors("AVGO", {})
    out.append("JUNK")
    details = await svc.get_competitor_details("AVGO")
    details["MRVL"]["segment"] = "mutated"
    assert await svc.get_competitors("AVGO", {}) == ["MRVL", "QCOM"]
    assert (await svc.get_competitor_details("AVGO"))["MRVL"]["segment"] == "Custom AI accelerators"


# ── 8. Small fixes: nothing swallowed silently ─────────────────────────


@pytest.mark.asyncio
async def test_per_ticker_profile_fallback_failure_is_logged(caplog):
    class _FlakyFMP:
        async def get_company_profiles_batch(self, tickers):
            raise RuntimeError("batch down")

        async def get_company_profile(self, ticker):
            if ticker == "BAD":
                raise RuntimeError("profile down")
            return {"symbol": ticker, "mktCap": 1e9}

    svc = CompetitorIntelService()
    svc._fmp = _FlakyFMP()  # type: ignore[assignment]
    with caplog.at_level(logging.WARNING, logger=cis.__name__):
        validated, rejected = await svc._fmp_validate(["GOOD", "BAD"], focal="AVGO")
    assert [v["ticker"] for v in validated] == ["GOOD"]
    assert {"ticker": "BAD", "reason": "rejected_unknown_ticker"} in rejected
    msgs = [r.getMessage() for r in caplog.records]
    assert any("profile fetch for BAD" in m and "RuntimeError" in m for m in msgs)
    assert any("get_company_profiles_batch failed for AVGO" in m for m in msgs)


def test_sum_tokens_failure_is_logged(monkeypatch, caplog):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.read_error = _PgErr(520)
    with caplog.at_level(logging.WARNING, logger=cis.__name__):
        assert CompetitorIntelService()._sum_tokens_for_run("run-1") == 0
    assert any("token total read failed for run run-1" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_grounded_call_failure_is_logged_with_the_ticker(caplog):
    gem = _FakeGemini(error=RuntimeError("quota exceeded"))
    with caplog.at_level(logging.WARNING, logger=cis.__name__):
        result = await _svc(gem)._extract_and_validate("AVGO", {})
    assert result.status == "gemini_error"
    assert any(
        "grounded research call failed for AVGO" in r.getMessage() and "RuntimeError" in r.getMessage()
        for r in caplog.records
    )


def test_get_competitors_docstring_no_longer_claims_a_market_cap_sort():
    doc = CompetitorIntelService.get_competitors.__doc__ or ""
    assert "sorted by mktCap" not in doc
    assert "MOST DIRECT FIRST" in doc


# ── is_ranked_list: may the report call this list "most direct first"? ────────
#
# Only a list extracted under the CURRENT prompt (which asks for most-direct-first)
# is ranked. A stale fallback — an older prompt's list served after a failed
# re-extraction — is not, or the report would label it "Most direct first".


@pytest.mark.asyncio
async def test_a_freshly_extracted_list_is_ranked(monkeypatch):
    _use_fake_sb(monkeypatch, _FakeSB())
    gem = _FakeGemini(_gemini_response_with_rows([_row("MRVL"), _row("QCOM")]))
    svc = _svc(gem)
    assert await svc.get_competitors("AVGO", {}) == ["MRVL", "QCOM"]
    assert await svc.is_ranked_list("AVGO") is True


@pytest.mark.asyncio
async def test_a_current_db_row_is_ranked_with_or_without_memory(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row("AVGO", ["MRVL"], model_version=f"m|{CACHE_MARKER}")
    svc = _svc(_FakeGemini(error=AssertionError("must not be called")))
    assert await svc.is_ranked_list("AVGO") is True          # DB tier, cold memory
    assert await svc.get_competitors("AVGO", {}) == ["MRVL"]
    assert await svc.is_ranked_list("AVGO") is True          # memory tier


@pytest.mark.parametrize(
    "gemini",
    [
        lambda: _FakeGemini(error=RuntimeError("quota")),
        lambda: _FakeGemini(_gemini_response_with_rows([_row("GOOGL", rel="customer")])),
    ],
)
@pytest.mark.asyncio
async def test_a_stale_fallback_list_is_not_ranked(monkeypatch, gemini):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row("AVGO", ["NVDA", "MRVL"], model_version="gemini-2.5-flash")
    svc = _svc(gemini())
    assert await svc.get_competitors("AVGO", {}) == ["NVDA", "MRVL"]
    assert await svc.is_ranked_list("AVGO") is False
    # Still unranked while the 30-min stale entry is served from memory.
    assert await svc.get_competitors("AVGO", {}) == ["NVDA", "MRVL"]
    assert await svc.is_ranked_list("AVGO") is False


@pytest.mark.asyncio
async def test_a_stale_db_row_is_not_ranked_before_any_extraction(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row("AVGO", ["NVDA"], model_version="gemini-2.5-flash")
    assert await _svc(_FakeGemini(error=AssertionError("no call"))).is_ranked_list("AVGO") is False


@pytest.mark.asyncio
async def test_is_ranked_list_is_false_for_nothing_negative_garbage_and_errors(monkeypatch, caplog):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    svc = _svc(_FakeGemini(error=RuntimeError("quota")))
    assert await svc.is_ranked_list("AVGO") is False          # no row at all
    assert await svc.get_competitors("AVGO", {}) is None       # cold failure → negative entry
    assert await svc.is_ranked_list("AVGO") is False
    assert await svc.is_ranked_list("") is False
    assert await svc.is_ranked_list(None) is False  # type: ignore[arg-type]
    cis._mem_cache.clear()
    sb.read_error = RuntimeError("supabase down")
    with caplog.at_level(logging.INFO, logger=cis.__name__):
        assert await svc.is_ranked_list("MSFT") is False


# ═══════════════════════════════════════════════════════════════════════
# 2026-10-01 review round ("intel" F1-F5): every failure path is audited
# and negative-cached, a failed READ is not a miss, the batch never counts
# a joined stale fallback as a re-extraction, `ranked` is independent of
# `stale`, and the segment cleaner keeps C++ / C# / Disney+ / .NET and is
# idempotent.
# ═══════════════════════════════════════════════════════════════════════


def _fenced(payload_json: str) -> Dict[str, Any]:
    """A grounded answer whose json fence holds `payload_json` verbatim."""
    return {
        "text": f"Prose first.\n\n```json\n{payload_json}\n```\n",
        "tokens_used": 777,
        "grounding_sources": [{"publisher": "reuters"}],
        "search_queries": ["q"],
        "model": "gemini-2.5-flash",
    }


class _GatedGemini(_FakeGemini):
    """The FIRST call parks until `release` is set; every call answers the next item
    of `outcomes` (an exception instance is raised)."""

    def __init__(self, outcomes: List[Any]):
        super().__init__()
        self._outcomes = list(outcomes)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def generate_grounded_research(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            await self.release.wait()
        out = self._outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out


async def _let_joiners_park() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


# ── F1: a non-object payload, malformed answers, the unhandled path ────


@pytest.mark.parametrize(
    "payload_json, kind",
    [('[{"ticker": "MRVL"}]', "list"), ("[]", "list"), ('"MRVL"', "str"),
     ("42", "int"), ("null", "NoneType"), ("true", "bool")],
)
@pytest.mark.asyncio
async def test_a_json_payload_that_is_not_an_object_is_a_billed_gemini_error(payload_json, kind):
    svc = _svc(_FakeGemini(_fenced(payload_json)))
    result = await svc._extract_and_validate("AVGO", {"companyName": "Broadcom"})
    assert result.status == "gemini_error"
    assert result.tokens_used == 777 and result.model_version == "gemini-2.5-flash"
    assert result.raw_response["error"] == f"payload is a JSON {kind}, not an object"
    assert result.validated_tickers == []
    assert svc._fmp.requested == []


@pytest.mark.asyncio
async def test_a_non_object_payload_is_audited_once_and_not_re_billed(monkeypatch):
    """The model repeats the array at temperature 0: it must be one audited, billed
    failure plus a negative entry — not a re-bill on every collection."""
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    gem = _FakeGemini(_fenced('[{"ticker": "MRVL"}]'))
    svc = _svc(gem)
    for _ in range(3):
        assert await svc.get_competitors("AVGO", {}) is None
    assert gem.calls == 1
    [audit] = sb.audits()
    assert audit["status"] == "gemini_error" and audit["tokens_used"] == 777
    expires_at, tickers, _details, _ranked = cis._mem_cache["AVGO"]
    assert tickers is None and expires_at - time.time() > _NEGATIVE_TTL_SECONDS - 60


@pytest.mark.parametrize(
    "response, tokens",
    [
        (None, None),                                                    # not an object
        (["not", "a", "dict"], None),
        ({"text": 42, "tokens_used": 5, "model": "m"}, 5),              # non-str text
        ({"text": None, "tokens_used": 5, "model": "m"}, 5),            # no text at all
        # 100k nested arrays: json raises RecursionError, not JSONDecodeError.
        ({"text": "```json\n" + "[" * 100_000 + "\n```", "tokens_used": 5, "model": "m"}, 5),
    ],
)
@pytest.mark.asyncio
async def test_a_malformed_grounded_answer_never_raises_and_keeps_its_tokens(response, tokens):
    result = await _svc(_FakeGemini(response))._extract_and_validate("AVGO", {})
    assert result.status == "gemini_error"
    assert result.tokens_used == tokens
    assert result.validated_tickers == []


@pytest.mark.parametrize("sources", [42, "reuters", {"publisher": "reuters"}, None])
@pytest.mark.asyncio
async def test_malformed_grounding_sources_cost_the_labels_not_the_list(sources):
    response = _gemini_response_with_rows([_row("MRVL")])
    response["grounding_sources"] = sources
    result = await _svc(_FakeGemini(response))._extract_and_validate("AVGO", {})
    assert result.status == "applied" and result.validated_tickers == ["MRVL"]
    assert result.source_labels == []


@pytest.mark.asyncio
async def test_an_unexpected_raise_while_parsing_is_an_audited_error_with_tokens(monkeypatch, caplog):
    """The catch-all after the billed call: whatever escapes the parser still ends
    in a `gemini_error` result that carries the tokens (the call was paid for)."""
    svc = _svc(_FakeGemini(_gemini_response_with_rows([_row("MRVL")])))

    async def _validate_boom(*_a, **_k):
        raise RuntimeError("fmp shape")

    monkeypatch.setattr(svc, "_fmp_validate", _validate_boom)
    with caplog.at_level(logging.ERROR, logger=cis.__name__):
        result = await svc._extract_and_validate("AVGO", {})
    assert result.status == "gemini_error"
    assert result.tokens_used == 1234 and result.model_version == "gemini-2.5-flash"
    assert result.raw_response["error"] == "response handling: RuntimeError: fmp shape"
    assert any("handling the grounded answer for AVGO failed" in r.getMessage()
               for r in caplog.records)


@pytest.mark.parametrize(
    "profile", [None, [], "AVGO", 42, {"description": 123, "companyName": None}],
)
@pytest.mark.asyncio
async def test_a_malformed_profile_never_raises_and_still_prompts(profile):
    gem = _FakeGemini(_gemini_response_with_rows([_row("MRVL")]))
    result = await _svc(gem)._extract_and_validate("AVGO", profile)  # type: ignore[arg-type]
    assert result.validated_tickers == ["MRVL"]
    assert "COMPANY: AVGO (AVGO)" in gem.prompts[0]


@pytest.mark.parametrize("seed_stale", [True, False])
@pytest.mark.asyncio
async def test_an_unhandled_exception_is_audited_served_stale_and_negative_cached(
    monkeypatch, caplog, seed_stale,
):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    if seed_stale:
        sb.seed_cache_row("AVGO", ["NVDA", "MRVL"], model_version="gemini-2.5-flash")
    svc = _svc(_FakeGemini(error=AssertionError("not reached")))
    calls: List[str] = []

    async def _boom(ticker, profile, **_kw):
        calls.append(ticker)
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(svc, "_extract_and_validate", _boom)
    expected = ["NVDA", "MRVL"] if seed_stale else None
    with caplog.at_level(logging.WARNING, logger=cis.__name__):
        assert await svc.get_competitors("AVGO", {}) == expected
    assert any("unhandled error for AVGO" in r.getMessage() for r in caplog.records)
    # The negative entry: the next pre-warm answers from memory, nothing re-runs.
    assert await svc.get_competitors("AVGO", {}) == expected
    assert calls == ["AVGO"]
    assert cis._mem_cache["AVGO"][0] - time.time() > _NEGATIVE_TTL_SECONDS - 60
    [audit] = sb.audits()
    assert audit["status"] == "gemini_error"
    assert "unhandled RuntimeError: parser exploded" in audit["raw_response"]["error"]
    assert sb.cache_upserts() == []
    if seed_stale:
        assert await svc.is_ranked_list("AVGO") is False   # a legacy fallback stays unranked


@pytest.mark.asyncio
async def test_an_exception_after_the_audit_row_writes_no_second_one(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    svc = _svc(_FakeGemini(_gemini_response_with_rows([_row("MRVL")])))

    def _write_cache_boom(*_a, **_k):
        raise RuntimeError("thread pool gone")

    monkeypatch.setattr(svc, "_write_cache", _write_cache_boom)
    await svc.get_competitors("AVGO", {})          # must not raise
    assert [a["status"] for a in sb.audits()] == ["applied"]


@pytest.mark.asyncio
async def test_the_unhandled_path_never_raises_even_when_its_fallbacks_fail(monkeypatch, caplog):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row("AVGO", ["NVDA"], model_version="legacy-model")
    svc = _svc(_FakeGemini())

    async def _boom(*_a, **_k):
        raise RuntimeError("extract")

    def _boom_sync(*_a, **_k):
        raise RuntimeError("fallback")

    monkeypatch.setattr(svc, "_extract_and_validate", _boom)
    monkeypatch.setattr(svc, "_serve_after_failure", _boom_sync)
    monkeypatch.setattr(svc, "_write_audit_row", _boom_sync)
    with caplog.at_level(logging.WARNING, logger=cis.__name__):
        assert await svc.get_competitors("AVGO", {}) == ["NVDA"]
    msgs = [r.getMessage() for r in caplog.records]
    assert any("fallback after the unhandled error for AVGO failed too" in m for m in msgs)
    assert any("audit row for the unhandled error on AVGO not written" in m for m in msgs)
    assert "AVGO" not in cis._inflight


@pytest.mark.asyncio
async def test_a_joiner_of_a_leader_that_raises_is_settled_not_stranded(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row("AVGO", ["NVDA", "MRVL"], model_version="gemini-2.5-flash")
    svc = _svc(_FakeGemini())
    entered, release = asyncio.Event(), asyncio.Event()

    async def _slow_boom(*_a, **_k):
        entered.set()
        await release.wait()
        raise RuntimeError("late failure")

    monkeypatch.setattr(svc, "_extract_and_validate", _slow_boom)
    leader = asyncio.create_task(svc.get_competitors("AVGO", {}))
    await asyncio.wait_for(entered.wait(), timeout=2)
    joiner = asyncio.create_task(svc.get_competitors("AVGO", {}, force_refresh=True))
    await _let_joiners_park()
    release.set()
    assert await asyncio.wait_for(leader, timeout=2) == ["NVDA", "MRVL"]
    assert await asyncio.wait_for(joiner, timeout=2) is None
    assert "AVGO" not in cis._inflight


# ── F2: a failed cache READ is not a miss ──────────────────────────────


class _MalformedSB:
    """`execute()` answers with `data` that is not a list of rows."""

    def __init__(self, data: Any):
        self._data = data

    def table(self, _name: str) -> "_MalformedSB":
        return self

    def select(self, _cols: str) -> "_MalformedSB":
        return self

    def eq(self, _k: str, _v: Any) -> "_MalformedSB":
        return self

    def limit(self, _n: int) -> "_MalformedSB":
        return self

    def execute(self) -> SimpleNamespace:
        return SimpleNamespace(data=self._data)


def test_read_cache_checked_tells_a_failed_read_from_a_miss(monkeypatch, caplog):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    svc = CompetitorIntelService()
    assert svc._read_cache_checked("AVGO") == (None, True)          # no row: a MISS
    sb.seed_cache_row("AVGO", ["MRVL"], model_version=f"m|{CACHE_MARKER}", expires_in_days=-1)
    assert svc._read_cache_checked("AVGO") == (None, True)          # expired: still a MISS
    sb.tables.clear()
    sb.seed_cache_row("AVGO", ["MRVL"], model_version=f"m|{CACHE_MARKER}")
    row, ok = svc._read_cache_checked("AVGO")
    assert ok is True and row is not None and row.tickers == ["MRVL"]
    sb.read_error = _PgErr(520)
    assert svc._read_cache_checked("AVGO") == (None, False)         # the read FAILED
    assert svc._read_cache("AVGO") is None                            # the plain reader: None
    for data in ({"ticker": "AVGO"}, "rows", 42):
        monkeypatch.setattr(cis, "get_supabase", lambda d=data: _MalformedSB(d))
        with caplog.at_level(logging.WARNING, logger=cis.__name__):
            assert svc._read_cache_checked("AVGO") == (None, False)
    assert any("malformed response" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_failed_read_plus_a_failed_extraction_does_not_hide_the_row_for_30_min(
    monkeypatch, caplog,
):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row("AVGO", ["MRVL", "QCOM"], model_version=f"m|{CACHE_MARKER}")
    sb.read_error = _PgErr(520)                     # a 520 on the read …
    gem = _FakeGemini(error=RuntimeError("quota"))  # … while the grounded call fails too
    svc = _svc(gem)
    with caplog.at_level(logging.WARNING, logger=cis.__name__):
        assert await svc.get_competitors("AVGO", {}) is None
    assert any("cache read failed too" in r.getMessage() for r in caplog.records)
    ttl_left = cis._mem_cache["AVGO"][0] - time.time()
    assert 0 < ttl_left <= cis._READ_FAILED_NEGATIVE_TTL_SECONDS
    # Inside the short window a pre-warm still does not re-bill.
    assert await svc.get_competitors("AVGO", {}) is None
    assert gem.calls == 1
    # The database recovers: a minute later the current row is served, not re-extracted.
    sb.read_error = None
    t0 = time.time()
    monkeypatch.setattr(cis.time, "time", lambda: t0 + cis._READ_FAILED_NEGATIVE_TTL_SECONDS + 1)
    assert await svc.get_competitors("AVGO", {}) == ["MRVL", "QCOM"]
    assert gem.calls == 1
    assert await svc.is_ranked_list("AVGO") is True


@pytest.mark.asyncio
async def test_kill_switch_with_a_failed_read_leaves_only_the_short_entry(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.read_error = _PgErr(520)
    monkeypatch.setattr(cis.settings, "COMPETITOR_INTEL_AI_ENABLED", False)
    svc = _svc(_FakeGemini(error=AssertionError("must not be called")))
    assert await svc.get_competitors("AVGO", {}) is None
    assert cis._mem_cache["AVGO"][0] - time.time() <= cis._READ_FAILED_NEGATIVE_TTL_SECONDS


@pytest.mark.asyncio
async def test_a_healthy_miss_plus_a_failed_extraction_keeps_the_30_min_entry(monkeypatch):
    """The twin: a read that SUCCEEDED and found nothing keeps the full entry."""
    _use_fake_sb(monkeypatch, _FakeSB())
    assert await _svc(_FakeGemini(error=RuntimeError("quota"))).get_competitors("AVGO", {}) is None
    assert cis._mem_cache["AVGO"][0] - time.time() > _NEGATIVE_TTL_SECONDS - 60


def test_the_read_failure_entry_is_at_most_a_minute():
    assert 0 < cis._READ_FAILED_NEGATIVE_TTL_SECONDS <= 60 < _NEGATIVE_TTL_SECONDS


# ── F3: a force_refresh joiner counts only a FRESH extraction ──────────


_ORCL_PROFILES = {
    t: {"symbol": t, "mktCap": cap}
    for t, cap in {"MSFT": 3.8e12, "SAP": 3e11, "CRM": 2.5e11}.items()
}


@pytest.mark.asyncio
async def test_a_forced_joiner_of_an_on_demand_leader_that_served_stale_answers_none(monkeypatch):
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row("ORCL", ["MSFT", "SAP"], model_version="gemini-2.5-flash")  # legacy → stale
    gem = _GatedGemini([RuntimeError("quota")])
    svc = _svc(gem, _ORCL_PROFILES)
    leader = asyncio.create_task(svc.get_competitors("ORCL", {}))     # a detail-view collection
    await asyncio.wait_for(gem.entered.wait(), timeout=2)
    joiner = asyncio.create_task(svc.get_competitors("ORCL", {}, force_refresh=True))  # the batch
    await _let_joiners_park()
    gem.release.set()
    assert await asyncio.wait_for(leader, timeout=2) == ["MSFT", "SAP"]   # stale-on-error, as before
    assert await asyncio.wait_for(joiner, timeout=2) is None, (
        "a joined stale fallback is not a re-extraction — the batch must retry it"
    )
    assert gem.calls == 1


@pytest.mark.asyncio
async def test_forced_joiners_of_a_fresh_leader_count_it_and_get_their_own_copies(monkeypatch):
    _use_fake_sb(monkeypatch, _FakeSB())
    gem = _GatedGemini([_gemini_response_with_rows([_row("MRVL"), _row("QCOM")])])
    svc = _svc(gem)
    leader = asyncio.create_task(svc.get_competitors("AVGO", {}))
    await asyncio.wait_for(gem.entered.wait(), timeout=2)
    joiners = [
        asyncio.create_task(svc.get_competitors("AVGO", {}, force_refresh=True)) for _ in range(2)
    ]
    await _let_joiners_park()
    gem.release.set()
    led = await asyncio.wait_for(leader, timeout=2)
    a = await asyncio.wait_for(joiners[0], timeout=2)
    b = await asyncio.wait_for(joiners[1], timeout=2)
    assert led == a == b == ["MRVL", "QCOM"] and _is_str_list(a) and _is_str_list(b)
    a.append("JUNK")
    assert b == ["MRVL", "QCOM"] and led == ["MRVL", "QCOM"], "joiners must not share one list"
    assert gem.calls == 1


@pytest.mark.asyncio
async def test_the_batch_retries_a_ticker_whose_pass_1_joined_a_stale_fallback(monkeypatch):
    """End to end through `refresh_top_tickers`: pass 1 joins a failing on-demand
    extraction, so it must count 0 applied and pass 2 must re-extract the ticker."""
    sb = _use_fake_sb(monkeypatch, _FakeSB())
    sb.seed_cache_row("ORCL", ["MSFT", "SAP"], model_version="gemini-2.5-flash")
    gem = _GatedGemini([
        RuntimeError("quota"),
        _gemini_response_with_rows([_row("MSFT"), _row("CRM")]),
    ])
    svc = _svc(gem, _ORCL_PROFILES)
    batch_reached_join = asyncio.Event()

    async def _profile(_ticker):
        # `_run_one` goes from here straight into get_competitors' join, no await between.
        batch_reached_join.set()
        return {"companyName": "Oracle"}

    monkeypatch.setattr(svc, "_safe_fetch_profile", _profile)
    monkeypatch.setattr(svc, "_load_top_watchlist_tickers", lambda _n: ["ORCL"])
    leader = asyncio.create_task(svc.get_competitors("ORCL", {}))
    await asyncio.wait_for(gem.entered.wait(), timeout=2)
    batch = asyncio.create_task(svc.refresh_top_tickers(top_n=1))
    await asyncio.wait_for(batch_reached_join.wait(), timeout=2)
    await _let_joiners_park()
    gem.release.set()
    assert await asyncio.wait_for(leader, timeout=2) == ["MSFT", "SAP"]
    summary = await asyncio.wait_for(batch, timeout=5)
    assert summary["applied"] == 0, "pass 1 joined a stale fallback — that is not an extraction"
    assert summary["applied_after_retry"] == 1 and summary["still_failing"] == 0
    assert gem.calls == 2
    assert sb.cache_upserts()[-1]["competitor_tickers"] == ["MSFT", "CRM"]


# ── F4: `ranked` is a separate question from `stale` ───────────────────


@pytest.mark.parametrize(
    "model_version, ranked",
    [
        (f"m|{CACHE_MARKER}", True), (f"m|{CACHE_MARKER_NODETAILS}", True),
        ("gemini-2.5-flash", False), ("m|cip-v1", False), (CACHE_MARKER, False),
        (f"m|{CACHE_MARKER}x", False), (None, False), (123, False), ("", False),
    ],
)
def test_marker_ranked(model_version, ranked):
    assert cis._marker_is_ranked(model_version) is ranked


@pytest.mark.parametrize(
    "model_version, column_present, stale, ranked",
    [
        (f"m|{CACHE_MARKER}", True, False, True),
        (f"m|{CACHE_MARKER_NODETAILS}", False, False, True),
        (f"m|{CACHE_MARKER_NODETAILS}", True, True, True),   # stale (no labels), STILL ranked
        ("gemini-2.5-flash", True, True, False),             # the old prompt never asked for an order
        ("m|cip-v1", True, True, False),
        (None, True, True, False),
    ],
)
def test_read_cache_ranked_is_independent_of_stale(
    monkeypatch, model_version, column_present, stale, ranked,
):
    sb = _use_fake_sb(monkeypatch, _FakeSB(has_details_column=column_present))
    sb.seed_cache_row("NVDA", ["AMD", "AVGO"], model_version=model_version)
    row = CompetitorIntelService()._read_cache("NVDA")
    assert row is not None and (row.stale, row.ranked) == (stale, ranked)


@pytest.mark.parametrize(
    "gemini, ai_enabled",
    [
        (lambda: _FakeGemini(error=RuntimeError("quota")), True),
        (lambda: _FakeGemini(_gemini_response_with_rows([_row("GOOGL", rel="customer")])), True),
        (lambda: _FakeGemini(error=AssertionError("must not be called")), False),   # kill switch
    ],
)
@pytest.mark.asyncio
async def test_a_nodetails_row_whose_re_extraction_fails_stays_ranked(monkeypatch, gemini, ai_enabled):
    """Migration 186 applied after the batch wrote `-nodetails` rows: the row is stale
    (no labels) but its order IS the directness ranking, so a failed re-extraction must
    not demote it to "highest threat first"."""
    sb = _use_fake_sb(monkeypatch, _FakeSB(has_details_column=True))
    sb.seed_cache_row("NVDA", ["AMD", "AVGO"], model_version=f"m|{CACHE_MARKER_NODETAILS}")
    monkeypatch.setattr(cis.settings, "COMPETITOR_INTEL_AI_ENABLED", ai_enabled)
    svc = _svc(gemini())
    assert await svc.get_competitors("NVDA", {}) == ["AMD", "AVGO"]
    assert cis._mem_cache["NVDA"][3] is True
    assert await svc.is_ranked_list("NVDA") is True          # memory tier
    cis._mem_cache.clear()
    assert await svc.is_ranked_list("NVDA") is True          # DB tier


# ── F5: names survive the cleaner, and it is idempotent ────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Compilers for C++", "Compilers for C++"),
        ("Compilers for C++.", "Compilers for C++"),
        ("Streaming video vs Disney+", "Streaming video vs Disney+"),
        ("Streaming (Disney+, Apple TV+)", "Streaming (Disney+, Apple TV+)"),
        (".NET developer tools", ".NET developer tools"),
        ("- .NET tools -", ".NET tools"),
        ("Dev tools for C#", "Dev tools for C#"),
        ("Dev tools for C#.", "Dev tools for C#"),
        ("F# and C# tooling", "F# and C# tooling"),
        # Not names: a detached "+", a markdown heading, a leading ellipsis.
        ("Networking +", "Networking"),
        ("+ Networking", "Networking"),
        ("## Networking", "Networking"),
        ("...and more", "and more"),
        # Nested empty parentheses (a cited link, a citation) leave nothing behind.
        ("Cloud ((https://x.com/a))", "Cloud"),
        ("Networking (([3]))", "Networking"),
        ("Storage ( ( ( ) ) ) arrays", "Storage arrays"),
    ],
)
def test_segment_cleaner_keeps_names_and_drops_nested_empty_parens(raw, expected):
    out = _clean_segment(raw)
    assert out == expected
    assert _clean_segment(out) == out


@pytest.mark.parametrize(
    "raw, kept",
    [
        ("Toolchains and compilers for embedded C++ and Rust developers", "embedded C++…"),
        ("Enterprise IDEs for C# developers, F# developers and more", "F#…"),
        ("C++ and C# compilers for embedded systems, and real-time operating systems",
         "C++ and C# compilers"),
        ("Video streaming services such as Disney+ and Apple TV+ and Paramount+", "Disney+…"),
        ("Developer platforms for .NET, Java and JavaScript applications in the cloud",
         "for .NET, Java…"),
    ],
)
def test_a_truncated_label_keeps_its_names(raw, kept):
    out = _clean_segment(raw)
    assert out is not None and kept in out
    assert len(out) <= SEGMENT_MAX_CHARS and _clean_segment(out) == out


_FUZZ_ALPHABET = list("abcXYZ019 ()[]{}<>*_`~|\\#+.-–—:;,!?\"'“”‘’/&…\n\t\x00​") + [
    "http://", "https://x.y/z", "www.", "[1]", "[2, 3]", "<b>", "</b>", "](", "C++", "C#",
    ".NET", "Disney+", "((", "))", "( )", " and ", " the ", "...",
]


def test_segment_cleaner_is_idempotent_over_generated_punctuation_and_parentheses():
    """Property: cleaning a cleaned label changes nothing (`_read_cache` re-cleans
    stored rows, so a non-idempotent cleaner shows one label fresh and another on the
    next read). Seeded, so a failure reproduces; the pre-fix cleaner fails ~20 of these
    inputs, every one a nested "( ( ) )"."""
    rng = random.Random(20261001)
    checked = 0
    for _ in range(4000):
        size = rng.randint(0, rng.choice([3, 10, 30, 80]))
        raw = "".join(rng.choice(_FUZZ_ALPHABET) for _ in range(size))
        once = _clean_segment(raw)
        if once is None:
            continue
        checked += 1
        assert _clean_segment(once) == once, (
            f"not idempotent: {raw!r} -> {once!r} -> {_clean_segment(once)!r}"
        )
        assert len(once) <= SEGMENT_MAX_CHARS, raw
        assert "()" not in once and "( " not in once and " )" not in once, raw
        assert once == once.strip(), raw
    assert checked > 1000, "the generator must yield mostly usable labels, or this proves nothing"
