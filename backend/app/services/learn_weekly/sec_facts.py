"""Learn weekly: the SEC EDGAR reads behind a fact sheet (plan §1.5 peg facts, §1.6 fact sheet).

Every figure FROZEN into a Learn article (its text and its cached narration) comes from SEC
EDGAR, which is public domain — never from FMP, whose Order Form forbids downloadable display.
This module is the Learn-side reader of three SEC surfaces, through the thin client
`app/integrations/sec_edgar.py` (its User-Agent rule, limiter and 429/403 circuit apply):

* `company_summary(cik)` — the submissions record trimmed to what the topic step needs: the
  display name, tickers, SIC, a foreign-filer flag and the recent 8-Ks with their item codes.
* `annual_facts(cik, name)` — the latest two fiscal years of seven us-gaap metrics, each
  rendered by CODE into one sentence (`contracts.Fact`, origin `sec_xbrl`). The model never
  writes a figure; it may only restate these sentences.
* `filing_text(...)` + `verify_peg_span(...)` — the plain text of one filing document and the
  check that a quoted "why now" span is verbatim in it (the model proposes spans, code decides).
  The text conversion and the check live in `app/utils/filing_text.py` (shared with the
  Trillion Club stake finder since 2026-10-28) and are re-exported here as the SAME objects
  under the same names: `html_to_text`, `verify_peg_span`, `normalize_peg_text` and the
  three length constants.

Why this does NOT reuse `services/sec_concept_service.py`: that service trims each row to
(start, end, val, filed, form) — no accession number, no fiscal-year fields — and its forms list
and Supabase cache rows are pinned to the buyback check by a test. A Learn fact must carry its
accession (the citation), so it keeps its own trim and its own cache.

Rules that shape the code (each is a way a wrong number could reach a published article):

* **Missing is never zero.** A 404 tag, a tag with no annual USD row, or a malformed value
  (bool, NaN, a string, over $1 quadrillion) gives NO fact for that metric — never a "$0".
* **The newest tag wins the fallback chain.** Filers switched revenue tags around 2018 (ASC 606):
  an old `Revenues` or `SalesRevenueNet` keeps its last pre-switch years forever, so taking the
  first tag that answers would print a 2017 figure as "the latest". Every tag of a chain is read
  and the one with the newest annual period wins; a tie keeps the chain order.
* **One row per period, the latest filed.** A 10-K/A restatement or the next year's comparative
  replaces the first filing's figure; two DIFFERENT values filed on the same day for one period
  are a conflict, and that period is skipped (WARNING) rather than guessed.
* **Periods come from dates, never from `fy`.** SEC's `fy` is the fiscal year of the FILING (a
  10-K's comparative row for the prior year carries the new year's `fy`), so the sentence says
  "the fiscal year ended January 26, 2025", built from `end`.
* **Annual = roughly twelve months.** A 10-K also tags quarters (Q4 alone) and multi-year spans;
  only a 350-380-day duration counts (52- and 53-week years included). Cash and debt are
  instants (no `start`).
* **A failed read is not an absence.** If any tag of a metric's chain FAILED (throttled, 5xx),
  the metric is skipped with a WARNING — the failed tag might have held the newer year — and the
  partial answer is not cached. If every read failed, the first exception is raised.

Caching: SEC is free, so there is no Supabase tier (plan §1.2 dropped the SEC cache table) — a
process-local TTL cache plus `_inflight` dedup per CIK. A blank `SEC_EDGAR_USER_AGENT` refuses
BEFORE any cache read, so turning SEC off never keeps serving old answers.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Hashable,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
)

from app.integrations.sec_edgar import (
    SECConfigException,
    SECEdgarClient,
    SECException,
    SECUnavailableException,
    filing_index_url,
    get_sec_edgar_client,
    normalize_accession,
    normalize_cik,
    user_agent_configured,
)
from app.services.learn_weekly.contracts import ORIGIN_SEC_XBRL, Fact
# Re-exported as the SAME objects (tests/test_filing_text_utils.py pins the identity): the
# Learn pipeline and its tests keep reading them from here.
from app.utils.filing_text import (
    FILING_TEXT_MAX_CHARS,
    PEG_SPAN_MAX_CHARS,
    PEG_SPAN_MIN_CHARS,
    html_to_text,
    normalize_peg_text,
    verify_peg_span,
)
from app.utils.inflight import fail_shared_future

logger = logging.getLogger(__name__)

__all__ = [
    "CompanySummary",
    "Filing8K",
    "METRIC_KEYS",
    "FILING_TEXT_MAX_CHARS",
    "PEG_SPAN_MIN_CHARS",
    "PEG_SPAN_MAX_CHARS",
    "company_summary",
    "annual_facts",
    "filing_text",
    "html_to_text",
    "verify_peg_span",
    "normalize_peg_text",
    "format_usd",
    "clear_caches",
]

_T = TypeVar("_T")


# ── Types ──────────────────────────────────────────────────────────────


class Filing8K(NamedTuple):
    """One recent 8-K: what the topic step counts and where a peg fact is read from."""

    filing_date: str            # ISO date
    accession: str              # dashed form
    primary_document: str       # "" when SEC's value was unusable (counted, but not readable)
    items: Tuple[str, ...]      # 8-K item codes ("2.02", "9.01"), malformed codes dropped


@dataclass(frozen=True)
class CompanySummary:
    name: str                               # SEC's conformed name ("NVIDIA CORP")
    cik: str                                # 10 digits, zero-padded
    tickers: Tuple[str, ...]
    exchanges: Tuple[Optional[str], ...]    # PARALLEL to `tickers` (None where SEC has null)
    sic: Optional[str]
    sic_description: Optional[str]
    foreign_filer: bool                     # any 20-F / 40-F / 6-K among the recent forms
    recent_8k: Tuple[Filing8K, ...]         # newest first, at most RECENT_8K_MAX


# ── Constants ──────────────────────────────────────────────────────────

_SUMMARY_TTL_SECONDS = 3600.0       # 8-Ks are the point of a summary: an hour, not a day
_FACTS_TTL_SECONDS = 24 * 3600.0    # annual XBRL facts change once a year (plus amendments)
_TEXT_TTL_SECONDS = 24 * 3600.0     # a filed document never changes
_SUMMARY_CACHE_MAX = 512
_FACTS_CACHE_MAX = 256
_TEXT_CACHE_MAX = 16                # up to 200k chars each

#: 8-Ks kept per summary. The topic step reads ~9 weeks; 100 covers that for the busiest filer.
RECENT_8K_MAX = 100
_FOREIGN_FORMS = frozenset({"20-F", "20-F/A", "40-F", "40-F/A", "6-K", "6-K/A"})
_ITEM_RE = re.compile(r"^[0-9]{1,2}\.[0-9]{2}$")
_DOCUMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")

_ANNUAL_FORMS = frozenset({"10-K", "10-K/A"})
_KEPT_KEYS = ("accn", "form", "filed", "start", "end", "val", "fy", "fp", "frame")
_ANNUAL_MIN_DAYS = 350              # end − start of a 52-week year is 363 days
_ANNUAL_MAX_DAYS = 380              # ... and of a 53-week year 370
_YEARS_KEPT = 2
#: Larger than any company's revenue or balance sheet by ~200×: a value past it is corrupt.
_MAX_ABS_VALUE = 1e15
_NAME_MAX_CHARS = 120
# FILING_TEXT_MAX_CHARS, PEG_SPAN_MIN_CHARS and PEG_SPAN_MAX_CHARS: app/utils/filing_text.py.


@dataclass(frozen=True)
class _Metric:
    key: str
    tags: Tuple[str, ...]           # the fallback chain, preferred first
    instant: bool                   # a balance (as of a date) rather than a year's flow
    noun: str                       # "revenue" → "reported revenue of $X"
    loss_noun: Optional[str] = None  # set ⇒ a negative value is a loss, phrased honestly
    tag_nouns: Tuple[Tuple[str, str], ...] = ()  # a tag whose meaning differs names itself

    def noun_for(self, tag: str) -> str:
        return dict(self.tag_nouns).get(tag, self.noun)


_METRICS: Tuple[_Metric, ...] = (
    _Metric(
        "revenue",
        ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"),
        instant=False,
        noun="revenue",
    ),
    _Metric("net_income", ("NetIncomeLoss",), instant=False, noun="net income",
            loss_noun="a net loss"),
    _Metric("operating_income", ("OperatingIncomeLoss",), instant=False,
            noun="operating income", loss_noun="an operating loss"),
    _Metric("research_and_development", ("ResearchAndDevelopmentExpense",), instant=False,
            noun="research and development expense"),
    _Metric("capital_expenditures",
            ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
            instant=False,
            noun="capital expenditures (purchases of property, plant and equipment)",
            # NVIDIA left the first tag after fiscal 2012 and files capex under the second, which
            # also counts intangible assets — the sentence must say so (2026-10-23 preview).
            tag_nouns=(
                ("PaymentsToAcquireProductiveAssets",
                 "capital expenditures (purchases of property, equipment and intangible assets)"),
            )),
    _Metric("cash", ("CashAndCashEquivalentsAtCarryingValue",), instant=True,
            noun="cash and cash equivalents"),
    _Metric(
        "long_term_debt",
        ("LongTermDebtNoncurrent", "LongTermDebt"),
        instant=True,
        noun="long-term debt",
        # The two tags measure different things; the sentence must say which one it is.
        tag_nouns=(
            ("LongTermDebtNoncurrent", "long-term debt (excluding the current portion)"),
            ("LongTermDebt", "long-term debt (including the current portion)"),
        ),
    ),
)
METRIC_KEYS: Tuple[str, ...] = tuple(m.key for m in _METRICS)


# ── Process-local caches + in-flight dedup ─────────────────────────────

_summary_cache: Dict[str, Tuple[float, Optional[CompanySummary]]] = {}
_facts_cache: Dict[str, Tuple[float, Tuple["_Selected", ...]]] = {}
_text_cache: Dict[Tuple[str, str, str], Tuple[float, Optional[str]]] = {}
_inflight: Dict[Hashable, asyncio.Future] = {}


def clear_caches() -> None:
    """Drop every cached answer (tests; a preview run that wants fresh reads)."""
    _summary_cache.clear()
    _facts_cache.clear()
    _text_cache.clear()
    _inflight.clear()


def _cache_get(cache: Dict[Any, Tuple[float, Any]], key: Any, ttl: float) -> Tuple[bool, Any]:
    entry = cache.get(key)
    if entry is None:
        return False, None
    stored_at, value = entry
    if time.monotonic() - stored_at > ttl:
        cache.pop(key, None)
        return False, None
    return True, value


def _cache_put(cache: Dict[Any, Tuple[float, Any]], key: Any, value: Any, cap: int) -> None:
    cache.pop(key, None)
    cache[key] = (time.monotonic(), value)
    if len(cache) > cap:
        for old in list(cache.keys())[: len(cache) - cap]:
            cache.pop(old, None)


async def _deduped(key: Hashable, what: str, factory: Callable[[], Awaitable[_T]]) -> _T:
    """Run ``factory`` once per ``key`` at a time; concurrent callers join the leader."""
    existing = _inflight.get(key)
    if existing is not None:
        logger.info("[learn-sec] in-flight JOIN for %s", what)
        return await asyncio.shield(existing)
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _inflight[key] = future
    try:
        result = await factory()
    except asyncio.CancelledError:
        # A BaseException: it skips `except Exception`, and an unresolved future would hang
        # every joiner for the life of the process.
        fail_shared_future(future, RuntimeError(f"in-flight {what} was cancelled"))
        raise
    except Exception as exc:
        fail_shared_future(future, exc)
        raise
    else:
        if not future.done():
            future.set_result(result)
        return result
    finally:
        if _inflight.get(key) is future:
            _inflight.pop(key, None)


def _require_user_agent(what: str) -> None:
    # FIRST, before any cache read: the setting off must never keep serving old answers.
    if not user_agent_configured():
        raise SECConfigException(
            "SEC EDGAR: SEC_EDGAR_USER_AGENT is not set or is not printable ASCII — no "
            f"{what}"
        )


def _require_cik(cik: Any) -> str:
    cik10 = normalize_cik(cik)
    if cik10 is None:
        raise ValueError(f"invalid CIK: {cik!r}")
    return cik10


# ── Small parsers ──────────────────────────────────────────────────────


def _parse_day(value: Any) -> Optional[date]:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _usable_value(value: Any) -> Optional[float]:
    """A reported number, or None for anything that must not become a figure."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if abs(value) > _MAX_ABS_VALUE:
        return None
    return value


_MONTHS = (
    "January", "February", "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December",
)


def _long_date(day: date) -> str:
    return f"{_MONTHS[day.month - 1]} {day.day}, {day.year}"


def _plain_number(value: float) -> str:
    text = f"{value:,.1f}"
    return text[:-2] if text.endswith(".0") else text


_SCALES: Tuple[Tuple[float, str], ...] = ((1e12, "trillion"), (1e9, "billion"), (1e6, "million"))


def format_usd(value: float) -> str:
    """``$130.5 billion`` / ``$512.3 million`` / ``$84,000`` for a NON-NEGATIVE amount (the
    caller phrases the sign). One decimal at a scale; a value whose rounding would print
    "1,000 million" moves up a scale ("$1 billion")."""
    amount = abs(float(value))
    for i, (scale, word) in enumerate(_SCALES):
        lower = _SCALES[i + 1][0] if i + 1 < len(_SCALES) else None
        promotes = (
            round(amount / lower, 1) >= 1000 if lower is not None else round(amount) >= scale
        )
        if amount >= scale or promotes:
            return f"${_plain_number(round(amount / scale, 1))} {word}"
    return f"${int(round(amount)):,}"


# ── company_summary ────────────────────────────────────────────────────


async def company_summary(
    cik: Any, *, client: Optional[SECEdgarClient] = None
) -> Optional[CompanySummary]:
    """The company's SEC submissions record, trimmed. None when SEC has no such CIK (404).

    Raises:
        ValueError: ``cik`` is not a valid CIK.
        SECConfigException: ``SEC_EDGAR_USER_AGENT`` is unusable (before any cache read).
        SECRateLimitException / SECUnavailableException / SECException: the read failed, or
            the record is malformed (no name, no filings block → SECUnavailableException).
            Never cached.
    """
    cik10 = _require_cik(cik)
    _require_user_agent(f"submissions for CIK{cik10}")
    hit, cached = _cache_get(_summary_cache, cik10, _SUMMARY_TTL_SECONDS)
    if hit:
        return cached

    async def load() -> Optional[CompanySummary]:
        sec = client if client is not None else get_sec_edgar_client()
        data = await sec.get_submissions(cik10)
        summary = None if data is None else _parse_submissions(data, cik10)
        if summary is None:
            logger.info("[learn-sec-summary] CIK%s: SEC has no submissions record (404)", cik10)
        _cache_put(_summary_cache, cik10, summary, _SUMMARY_CACHE_MAX)
        return summary

    return await _deduped(("summary", cik10), f"SEC submissions CIK{cik10}", load)


def _clean_str(value: Any, max_len: int = 200) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text[:max_len] if text else None


def _parse_submissions(data: Mapping[str, Any], cik10: str) -> CompanySummary:
    name = _clean_str(data.get("name"), _NAME_MAX_CHARS)
    filings = data.get("filings")
    recent = filings.get("recent") if isinstance(filings, Mapping) else None
    if name is None or not isinstance(recent, Mapping):
        # A real submissions record always has both. Raised, never read as "no 8-Ks" / "not
        # foreign": a malformed record must not rank as a quiet domestic company.
        raise SECUnavailableException(
            f"SEC EDGAR: submissions for CIK{cik10} lack "
            f"{'a name' if name is None else 'filings.recent'} — malformed record",
            status_code=200,
        )

    raw_tickers = data.get("tickers") if isinstance(data.get("tickers"), list) else []
    raw_exchanges = data.get("exchanges") if isinstance(data.get("exchanges"), list) else []
    tickers: List[str] = []
    exchanges: List[Optional[str]] = []
    for i, raw in enumerate(raw_tickers):
        ticker = raw.strip().upper() if isinstance(raw, str) else ""
        if not _TICKER_RE.match(ticker) or ticker in tickers:
            continue
        tickers.append(ticker)
        exchanges.append(_clean_str(raw_exchanges[i], 40) if i < len(raw_exchanges) else None)

    sic_raw = data.get("sic")
    if isinstance(sic_raw, int) and not isinstance(sic_raw, bool):
        sic_raw = str(sic_raw)
    sic = sic_raw.strip() if isinstance(sic_raw, str) and sic_raw.strip().isdigit() else None

    forms = recent.get("form") if isinstance(recent.get("form"), list) else []
    foreign = any(isinstance(f, str) and f.strip() in _FOREIGN_FORMS for f in forms)

    columns = {
        key: recent.get(key) if isinstance(recent.get(key), list) else []
        for key in ("accessionNumber", "filingDate", "primaryDocument", "items")
    }
    lengths = {len(forms), *(len(col) for col in columns.values())}
    n = min(lengths)
    if len(lengths) > 1:
        # The arrays are parallel; a short one would shift every later row onto another
        # filing. Rows past the shortest array are dropped, never re-aligned by guess.
        logger.warning(
            "[learn-sec-summary-ragged] CIK%s: filings.recent arrays differ in length %s — "
            "reading only the first %d rows",
            cik10, sorted(lengths), n,
        )
    eight_ks: List[Filing8K] = []
    skipped = 0
    for i in range(n):
        form = forms[i]
        if not isinstance(form, str) or form.strip() != "8-K":
            continue
        accession = normalize_accession(columns["accessionNumber"][i])
        filed = _parse_day(columns["filingDate"][i])
        if accession is None or filed is None:
            skipped += 1
            continue
        document = columns["primaryDocument"][i]
        # fullmatch: `$` also matches before a trailing newline, which would reach the URL.
        if not (isinstance(document, str) and _DOCUMENT_RE.fullmatch(document)):
            document = ""
        raw_items = columns["items"][i]
        parts = raw_items.split(",") if isinstance(raw_items, str) else []
        items = tuple(code for code in (p.strip() for p in parts) if _ITEM_RE.match(code))
        eight_ks.append(Filing8K(filed.isoformat(), accession, document, items))
    if skipped:
        logger.warning(
            "[learn-sec-summary-bad-rows] CIK%s: %d 8-K rows without a valid accession or "
            "filing date were skipped",
            cik10, skipped,
        )
    eight_ks.sort(key=lambda f: (f.filing_date, f.accession), reverse=True)

    return CompanySummary(
        name=name,
        cik=cik10,
        tickers=tuple(tickers),
        exchanges=tuple(exchanges),
        sic=sic,
        sic_description=_clean_str(data.get("sicDescription")),
        foreign_filer=foreign,
        recent_8k=tuple(eight_ks[:RECENT_8K_MAX]),
    )


# ── annual_facts ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Selected:
    metric: str
    tag: str
    rows: Tuple[Mapping[str, Any], ...]     # newest first, ≤ _YEARS_KEPT, trimmed


def select_annual_rows(
    payload: Any, *, instant: bool, what: str = "concept"
) -> List[Dict[str, Any]]:
    """The latest two fiscal-year rows of one companyconcept payload, newest first.

    USD only; forms 10-K / 10-K/A; a duration of 350-380 days (or, ``instant``, no start);
    one row per (start, end) — the latest filed; one row per period end. Rows are trimmed to
    ``_KEPT_KEYS`` with ``accn`` in dashed form and ``start``/``end`` as ISO dates.
    """
    units = payload.get("units") if isinstance(payload, Mapping) else None
    usd = units.get("USD") if isinstance(units, Mapping) else None
    if not isinstance(usd, list):
        return []

    periods: Dict[Tuple[Optional[str], str], List[Tuple[date, str, Dict[str, Any]]]] = {}
    malformed = 0
    for row in usd:
        form = row.get("form") if isinstance(row, Mapping) else None
        # isinstance first: a list or dict `form` is unhashable and would raise in the set test.
        if not isinstance(form, str) or form not in _ANNUAL_FORMS:
            continue
        value = _usable_value(row.get("val"))
        end = _parse_day(row.get("end"))
        filed = _parse_day(row.get("filed"))
        accession = normalize_accession(row.get("accn"))
        if value is None or end is None or filed is None or accession is None:
            malformed += 1
            continue
        if instant:
            if row.get("start") is not None:
                continue
            start = None
        else:
            start = _parse_day(row.get("start"))
            if start is None:
                continue
            if not _ANNUAL_MIN_DAYS <= (end - start).days <= _ANNUAL_MAX_DAYS:
                continue
        trimmed = {k: row[k] for k in _KEPT_KEYS if k in row}
        trimmed.update(
            accn=accession,
            val=value,
            end=end.isoformat(),
            filed=filed.isoformat(),
        )
        if start is not None:
            trimmed["start"] = start.isoformat()
        periods.setdefault((trimmed.get("start"), trimmed["end"]), []).append(
            (filed, accession, trimmed)
        )

    by_end: Dict[str, Tuple[date, str, Dict[str, Any]]] = {}
    for (start_s, end_s), candidates in periods.items():
        latest = max(c[0] for c in candidates)
        top = [c for c in candidates if c[0] == latest]
        if len({float(c[2]["val"]) for c in top}) > 1:
            logger.warning(
                "[learn-sec-conflict] %s: two different values filed on %s for the period "
                "%s..%s (%s) — that period is skipped, never guessed",
                what, latest.isoformat(), start_s, end_s, sorted({c[1] for c in top}),
            )
            continue
        chosen = max(top, key=lambda c: c[1])
        # Two annual starts for one end (a 52/53-week overlap): the latest filed wins.
        previous = by_end.get(end_s)
        if previous is None or (chosen[0], chosen[1]) > (previous[0], previous[1]):
            by_end[end_s] = chosen

    if malformed:
        # Skipped, never read as zero — but said out loud: a feed that starts sending strings
        # or NaN would otherwise look exactly like a company that stopped reporting.
        logger.warning(
            "[learn-sec-malformed-rows] %s: %d 10-K rows with an unusable value, date or "
            "accession were skipped",
            what, malformed,
        )
    ordered = sorted(by_end.values(), key=lambda c: c[2]["end"], reverse=True)
    return [c[2] for c in ordered[:_YEARS_KEPT]]


async def annual_facts(
    cik: Any, name: str, *, client: Optional[SECEdgarClient] = None
) -> List[Fact]:
    """The company's latest two fiscal years of the seven metrics, one code-rendered sentence
    per fact: every metric's newest year first (metric order), then the prior years.

    Ids are ``S1``…``Sn`` in that order; ``fact_sheet.build_fact_sheet`` renumbers across
    companies. ``url`` is the filing's EDGAR index page; ``accession`` is the citation and is
    never in the text.

    Raises:
        ValueError: ``cik`` is not a valid CIK, or ``name`` is blank.
        SECConfigException: ``SEC_EDGAR_USER_AGENT`` is unusable (before any cache read).
        SECException (or the first non-SEC error): EVERY tag read failed. A partial failure
            only drops the affected metrics (WARNING) and is not cached.
    """
    cik10 = _require_cik(cik)
    display = _clean_str(name, _NAME_MAX_CHARS)
    if display is None:
        raise ValueError(f"annual_facts needs a display name for CIK{cik10}, got {name!r}")
    _require_user_agent(f"annual facts for CIK{cik10}")

    hit, cached = _cache_get(_facts_cache, cik10, _FACTS_TTL_SECONDS)
    if hit:
        selected = cached
    else:
        selected = await _deduped(
            ("facts", cik10), f"SEC annual facts CIK{cik10}", lambda: _load_selected(cik10, client)
        )
    return _render_facts(cik10, display, selected)


async def _load_selected(
    cik10: str, client: Optional[SECEdgarClient]
) -> Tuple[_Selected, ...]:
    sec = client if client is not None else get_sec_edgar_client()
    tags: List[str] = []
    for metric in _METRICS:
        for tag in metric.tags:
            if tag not in tags:
                tags.append(tag)
    results = await asyncio.gather(
        *(sec.get_company_concept(cik10, tag) for tag in tags), return_exceptions=True
    )
    by_tag: Dict[str, Any] = dict(zip(tags, results))

    failures = [(tag, r) for tag, r in by_tag.items() if isinstance(r, BaseException)]
    for tag, err in failures:
        if isinstance(err, SECConfigException):
            raise err
        if not isinstance(err, SECException):
            # Not an SEC answer at all: a bug or an unexpected library error. Loud, with the stack.
            logger.error(
                "[learn-sec-unexpected] CIK%s %s: %s: %s",
                cik10, tag, type(err).__name__, err, exc_info=err,
            )
    if failures and len(failures) == len(tags):
        first = failures[0][1]
        logger.warning(
            "[learn-sec-facts-unavailable] CIK%s: all %d tag reads failed (first: %s: %s)",
            cik10, len(tags), type(first).__name__, first,
        )
        raise first

    selected: List[_Selected] = []
    for metric in _METRICS:
        failed = [tag for tag in metric.tags if isinstance(by_tag[tag], BaseException)]
        if failed:
            logger.warning(
                "[learn-sec-metric-skipped] CIK%s %s: read failed for %s — the metric is "
                "left out (a failed tag may hold the newer year)",
                cik10, metric.key, failed,
            )
            continue
        best: Optional[_Selected] = None
        for tag in metric.tags:
            payload = by_tag[tag]
            if payload is None:
                continue  # 404: the company never used this tag
            rows = select_annual_rows(
                payload, instant=metric.instant, what=f"CIK{cik10} {metric.key} {tag}"
            )
            if not rows:
                continue
            if best is None or rows[0]["end"] > best.rows[0]["end"]:
                best = _Selected(metric.key, tag, tuple(rows))
        if best is None:
            logger.info(
                "[learn-sec-metric-absent] CIK%s %s: no annual USD 10-K row under %s",
                cik10, metric.key, list(metric.tags),
            )
            continue
        selected.append(best)

    selected = _drop_stale(cik10, selected)
    out = tuple(selected)
    if failures:
        logger.warning(
            "[learn-sec-facts-partial] CIK%s: %d of %d tag reads failed — %d metrics kept, "
            "not cached",
            cik10, len(failures), len(tags), len(out),
        )
    else:
        _cache_put(_facts_cache, cik10, out, _FACTS_CACHE_MAX)
    return out


#: How far a metric's newest period may trail the company's newest annual period (any metric)
#: before it is treated as a chain the company stopped filing under. A year plus slack for a
#: 53-week year and a late balance-sheet instant.
_STALE_LAG_DAYS = 400


def _drop_stale(cik10: str, selected: Sequence[_Selected]) -> List[_Selected]:
    """Leave out a metric whose NEWEST row trails the company's newest annual row by more than
    `_STALE_LAG_DAYS`: every tag of its chain went quiet, so its "latest" is years old. NVIDIA's
    capex chain ended in fiscal 2012 while its revenue ran to 2026, and a live preview printed the
    2011 figure beside 2026 revenue as if current (2026-10-23). Relative to the company's own
    filings, so it needs no clock. WARNING per dropped metric."""
    newest = [date.fromisoformat(s.rows[0]["end"]) for s in selected if s.rows]
    if not newest:
        return list(selected)
    cutoff = max(newest) - timedelta(days=_STALE_LAG_DAYS)
    kept: List[_Selected] = []
    for s in selected:
        end = date.fromisoformat(s.rows[0]["end"])
        if end < cutoff:
            logger.warning(
                "[learn-sec-metric-stale] CIK%s %s (%s): newest period %s trails the company's "
                "newest %s by more than %d days — left out (the company stopped filing under it)",
                cik10, s.metric, s.tag, end.isoformat(), max(newest).isoformat(), _STALE_LAG_DAYS,
            )
            continue
        kept.append(s)
    return kept


def _render_facts(cik10: str, name: str, selected: Sequence[_Selected]) -> List[Fact]:
    metrics = {m.key: m for m in _METRICS}
    facts: List[Fact] = []
    for rank in range(_YEARS_KEPT):
        for chosen in selected:
            if rank >= len(chosen.rows):
                continue
            row = chosen.rows[rank]
            text = _render_sentence(metrics[chosen.metric], chosen.tag, name, row)
            if text is None:
                logger.warning(
                    "[learn-sec-implausible-sign] CIK%s %s (%s): %s for the period ending %s "
                    "is negative — a metric that cannot be negative is left out",
                    cik10, chosen.metric, chosen.tag, row.get("val"), row.get("end"),
                )
                continue
            facts.append(
                Fact(
                    id=f"S{len(facts) + 1}",
                    text=text,
                    origin=ORIGIN_SEC_XBRL,
                    url=filing_index_url(cik10, row["accn"]),
                    accession=row["accn"],
                )
            )
    return facts


def _render_sentence(
    metric: _Metric, tag: str, name: str, row: Mapping[str, Any]
) -> Optional[str]:
    value = float(row["val"])
    if value < 0 and metric.loss_noun is None:
        return None
    noun = metric.loss_noun if value < 0 else metric.noun_for(tag)
    end = date.fromisoformat(row["end"])
    period = f"as of {_long_date(end)}" if metric.instant else (
        f"for the fiscal year ended {_long_date(end)}"
    )
    return f"{name} reported {noun} of {format_usd(value)} {period} (Form {row['form']})."


# ── filing_text ────────────────────────────────────────────────────────


async def filing_text(
    cik: Any, accession: Any, document: Any, *, client: Optional[SECEdgarClient] = None
) -> Optional[str]:
    """One filing document as plain text (tags, scripts, styles and the hidden inline-XBRL
    header stripped, entities unescaped, whitespace collapsed), at most
    ``FILING_TEXT_MAX_CHARS``. None when SEC has no such document (404); "" when it holds no
    text.

    Raises:
        ValueError: an invalid CIK, accession or document name.
        SECConfigException: ``SEC_EDGAR_USER_AGENT`` is unusable (before any cache read).
        SECEdgarDocumentTooLargeException: over the client's 3 MB cap (skip the document).
        SECRateLimitException / SECUnavailableException / SECException: the read failed.
    """
    cik10 = _require_cik(cik)
    acc = normalize_accession(accession)
    if acc is None:
        raise ValueError(f"invalid accession number: {accession!r}")
    if not isinstance(document, str) or not _DOCUMENT_RE.fullmatch(document):
        raise ValueError(f"invalid filing document name: {document!r}")
    _require_user_agent(f"filing document {acc} {document}")
    key = (cik10, acc, document)
    hit, cached = _cache_get(_text_cache, key, _TEXT_TTL_SECONDS)
    if hit:
        return cached

    async def load() -> Optional[str]:
        sec = client if client is not None else get_sec_edgar_client()
        raw = await sec.get_filing_document(cik10, acc, document)
        text = None if raw is None else html_to_text(raw, document=document)
        if raw is None:
            logger.info("[learn-sec-document-missing] CIK%s %s %s: 404", cik10, acc, document)
        _cache_put(_text_cache, key, text, _TEXT_CACHE_MAX)
        return text

    return await _deduped(("text",) + key, f"SEC document CIK{cik10} {acc} {document}", load)


# html_to_text, verify_peg_span and normalize_peg_text: app/utils/filing_text.py (re-exported
# above). `filing_text` converts with the default 200k-character cap and `[learn-sec-…]` log tags.
