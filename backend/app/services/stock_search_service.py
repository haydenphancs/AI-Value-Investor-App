"""
Ticker-search listing rules — which FMP search rows a person should actually see.

FMP's ``search-symbol`` / ``search-name`` return every security an issuer ever listed,
all under the issuer's own name, and the rows carry no type or activity flag. So one
company arrived as several identical-looking rows (reported: "avgo" → AVGO and AVGOP,
both "Broadcom Inc."). A read-only sweep of the live data (2026-09-25) found the whole
family, and each rule below exists for one of its members:

  • NYSE/AMEX dash preferreds ``BAC-PE``, ``F-PB`` (notes listed as preferreds), ``T-PA``
    — 325 active symbols, every one fixed-income, many with NO listed base (``BML-PH``).
  • NASDAQ 5th-letter preferreds P/O/N/M ``AVGOP``, ``AGNCP``…``AGNCM``, ``GOOGN`` — 186
    active symbols matching ``^[A-Z]{4}[PONM]$``, zero of them common stock.
  • Notes / baby bonds on bare tickers whose NAME says what they are: ``TBB`` "AT&T Inc.
    5.35% GLB NTS 66", ``DUKB`` "Duke Energy Corporation 5.625%", ``SOJC`` "… JR 2017B NT 77".
  • Same-issuer root twins with identical names and no grammar at all: ``AGNCL``, ``APOS``,
    ``SOMN``, ``METCI``, and padded-root warrants ``VFSWW`` / ``PCTTW`` / ``AUROW``.
  • DEAD listings — converted, delisted, renamed, expired SPACs: ``AVGOP``, ``FI`` beside
    ``FISV``, ``BRKS`` ranked first for "brk", ``TWTR``. In 32 replayed queries 51 of the 98
    non-crypto rows people actually saw were dead.
  • NASDAQ open-end mutual funds ``____X`` (``JMGRX``/``JDMRX``/``JAENX`` are all "Janus
    Henderson Enterprise Fund") — hidden by product decision (2026-09-25).

What must NEVER be hidden, and why each guard exists:
  • Dual-class commons — GOOG/GOOGL, BRK-A/BRK-B, FOX/FOXA, UA/UAA, METC/METCB. Different
    securities with different prices and votes; kept as two rows and ranked together.
    GOOGL is the one class share whose letter (L) is also a note letter (AGNCL), hence
    ``_KNOWN_SHARE_CLASS_SYMBOLS``.
  • The symbol the user typed EXACTLY is exempt from every drop — the same ``keep_symbol``
    rule ``_dedupe_secondary_listings`` has always had. It protects a same-day IPO FMP has
    not yet added to its active list, and FMP's rare wrong "inactive" flags (CWEN-A).
  • A renamed live ticker must not be hidden as the "twin" of its own dead old ticker
    (JBT → JBTM, both "JBT Marel Corporation"): a root-twin base must itself be LIVE.

Liveness comes from ``/stable/actively-trading-list`` (one call, ~70k ``{symbol, name}``
rows; agreed 14/14 with ``profile.isActivelyTrading``; carried 204 of 210 new listings
and same-day symbol changes). It is held IN MEMORY only, on purpose — see
``get_active_listings``. Every data-dependent rule FAILS OPEN: an outage can bring a dead
row back, never blank or 502 the search. The grammar rules need no data, so AVGOP and the
preferreds stay hidden even then.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import get_fmp_client
from app.schemas.stock import StockSearchResult

logger = logging.getLogger(__name__)

# Every regex below runs on a length-capped string (search runs on the single uvicorn
# worker, on every debounced keystroke).
_MAX_NAME_CHARS = 300


# ── Corporate-entity marker (shared with the endpoint's classifier) ──────
# Whole-word markers whose presence means the row is an operating company's security,
# not one of its funds. The endpoint's `_get_asset_type` uses it to rescue Invesco Ltd.
# (IVZ), Charles Schwab (SCHW) and Northern Trust (NTRS) from the issuer-brand / "trust"
# keywords; the debt-name rule below uses it to stay off ETF and fund rows.
_CORP_ENTITY_RE = re.compile(
    r"\b(?:inc|incorporated|corp|corporation|co|company|plc|ltd|limited|"
    r"bancorp|bancshares|holdings?|group|ag|se|sa|nv|llc|lp)\b",
    re.IGNORECASE,
)


# ── Secondary-listing de-duplication (moved unchanged from stocks.py, + "-RI") ──
# FMP search returns corporate-action securities alongside the primary listing,
# all sharing the issuer's company name — confusing identical rows, and (worse)
# they classify as "stock" so a user could pick a warrant/unit/right and run the
# company pipeline on it. Two encodings occur:
#   • NASDAQ 5th-letter: when-issued "V" (SNDK→SNDKV), warrant "W" (BGRY→BGRYW),
#     unit "U" (SVNA→SVNAU), right "R" (RFAC→RFACR).
#   • NYSE dash form: "-WT"/"-WS" warrant, "-UN"/"-U" unit, "-RT"/"-RI"/"-R" right,
#     "-WI" when-issued (APCA → APCA-WT / APCA-UN, JACS → JACS-RI).
# We drop such a row ONLY when its BASE symbol (the row minus the suffix) is ALSO
# present with the SAME normalized name + exchange + type — i.e. it's a redundant
# twin of a listing we already show. This never collapses legitimate dual-class
# shares: GOOGL/GOOG differ by "L" (not a suffix) and BRK-A/BRK-B by "-A"/"-B"
# (not an action suffix); Z/ZG carry distinct names. A standalone V/W/U/R ticker
# with no same-named base (Visa "V", Veritiv "VRTV", Nu "NU", Baidu "BIDU",
# Progressive "PGR") is untouched — its base is a DIFFERENT company.
_SECONDARY_SUFFIXES = ("V", "W", "U", "R")
_DASH_ACTION_SUFFIX_RE = re.compile(r"[.\-](?:wt|ws|un|u|rt|ri|r|wi|w)$", re.IGNORECASE)
# Security-class / corporate-action descriptors FMP appends INCONSISTENTLY across
# a company's securities — e.g. the common is "RF Acquisition Corp II Ordinary
# Shares" while its unit/right rows are just "RF Acquisition Corp II". Stripping
# these so both key to the same company is SAFE: a twin only collapses when it
# ALSO carries a V/W/U/R (or dash-action) suffix, which legitimate dual-class
# shares (GOOGL/GOOG, Zillow "Class A"/"Class C") never do — so the name key is
# only a confirmation, never the sole trigger.
_NAME_DESCRIPTOR_RE = re.compile(
    r"\b(?:units?|warrants?|rights?|when[\s-]?issued|wi"
    r"|ordinary\s+shares?|common\s+stock|common\s+shares?"
    r"|depositary\s+shares?|class\s+[a-z])\b",
    re.IGNORECASE,
)


def _normalize_company_name(name: Optional[str]) -> str:
    """Lowercase, drop trailing corporate-action descriptors + punctuation, and
    collapse whitespace — so a twin ("IB Acquisition Corp. Unit") keys to the
    same company as its base ("IB Acquisition Corp.")."""
    n = (name or "")[:_MAX_NAME_CHARS].lower()
    n = _NAME_DESCRIPTOR_RE.sub(" ", n)
    n = re.sub(r"[.,]", " ", n)
    return " ".join(n.split())


def _secondary_base_symbol(sym: str) -> Optional[str]:
    """The primary/base ticker a corporate-action symbol derives from, or None.

    ``APCA-WT``/``APCA-UN`` → ``APCA``; ``SNDKV``/``SVNAU``/``RFACR`` → drop the
    5th letter. Returns None for a symbol with no recognized action suffix.
    """
    m = _DASH_ACTION_SUFFIX_RE.search(sym)
    if m:
        return sym[:m.start()]
    if len(sym) >= 2 and sym[-1] in _SECONDARY_SUFFIXES:
        return sym[:-1]
    return None


def _dedupe_secondary_listings(
    results: List[StockSearchResult],
    keep_symbol: str = "",
) -> List[StockSearchResult]:
    """Remove when-issued / warrant / unit / right twins that duplicate a
    primary listing.

    ``keep_symbol`` (upper-case) is never dropped — protects a ticker the user
    typed verbatim.
    """
    keep = (keep_symbol or "").upper()
    present: Dict[tuple, set] = {}
    by_issuer: Dict[tuple, set] = {}
    for r in results:
        exchange = (r.exchange_short_name or "").upper()
        sym = (r.symbol or "").upper()
        key = (_normalize_company_name(r.name), exchange, r.type)
        present.setdefault(key, set()).add(sym)
        issuer = _issuer_key(r.name)
        if issuer:
            by_issuer.setdefault((issuer, exchange, r.type), set()).add(sym)

    deduped: List[StockSearchResult] = []
    for r in results:
        sym = (r.symbol or "").upper()
        base = _secondary_base_symbol(sym) if sym != keep else None
        if base:
            exchange = (r.exchange_short_name or "").upper()
            key = (_normalize_company_name(r.name), exchange, r.type)
            if base in present.get(key, ()):
                continue  # redundant secondary twin of a primary we're showing
            # A DASH action suffix (-WT, -UN, -RI …) is unambiguous, so its name only has
            # to name the same ISSUER: FMP writes "IonQ, Inc. WT" beside "IonQ, Inc." and
            # "Ares Acquisition Corporation III" beside "Ares Acquisition Corp. III Class
            # A", which the raw name key above never matched (IONQ-WT, BBAI-WT, AAC-UN,
            # SKYH-WT all showed as a second company row). Stock rows only, and never the
            # bare 5th-letter form — for ETFs that looser key would pair VOOV with VOO.
            if r.type == "stock" and _DASH_ACTION_SUFFIX_RE.search(sym):
                issuer = _issuer_key(r.name)
                if issuer and base in by_issuer.get((issuer, exchange, r.type), ()):
                    continue
        deduped.append(r)
    return deduped


# ── Grammar rules: no data source needed, so they hold during an FMP outage ──

# NYSE-group preferreds are "<root> PR<series>" on the tape; FMP spells them "BAC-PE".
# Anchored to "-P" + 0-2 letters so the class dashes (BRK-A, HEI-A, LEN-B, MKC-V,
# PBR-A, CIG-C) and the action dashes (-WT, -UN, -RI) can never match. No twin check:
# BML-PG/PH/PJ/PL and MER-PK are Bank of America preferreds with no BML/MER common,
# and PCG-P* carry a different name from PCG.
_DASH_PREFERRED_RE = re.compile(r"^[A-Z]{1,5}-P[A-Z]{0,2}$")
# NASDAQ reserves the 5th letter P/O/N/M for the 1st-4th preferred series. EXACTLY
# five letters: SNAP, RAMP, NTBP and every other 4-letter P/O/N/M ticker is a common.
# No twin check: the root is often not the common's ticker (VLYPP → VLY, WFCNP → WFC).
_NASDAQ_PREFERRED_RE = re.compile(r"^[A-Z]{4}[PONM]$")
# NASDAQ's 5th-letter X is the mutual-fund (MFQS) designator. FOXX, BIOX and TSMX are
# 4 letters and untouched; a 5-letter X on another exchange is left alone.
_NASDAQ_MUTUAL_FUND_RE = re.compile(r"^[A-Z]{4}X$")
# A name that states it is debt or a preferred. Only consulted for a "stock" row whose
# name carries a corporate-entity marker, so ETF/fund names ("Fidelity Asset Manager
# 20%") are never touched. Deliberately NOT here: bare "preferred" (Preferred Bank,
# PFBC), bare "senior" (Sonida Senior Living), "perpetual" (Perpetual Industries,
# PRPI), singular "note" (Blue Note Mining), and "American Depositary Shares" (ARM, PONY).
_DEBT_PREF_NAME_RE = re.compile(
    r"%"
    r"|\bdue\s+(?:19|20)\d\d\b"
    r"|\bnotes\b"
    r"|\b(?:sr|jr|glb|global|sub)\.?\s+nts?\b"
    r"|\bnts?\s+\d"
    r"|\bdebentures?\b"
    r"|\bsubordinated\b"
    r"|\bjr\.?\s*sub"
    r"|\bjrsub\b"
    r"|\bpfd\b"
    r"|\bpreferred\s+(?:stock|shares?|securities)\b"
    r"|\bnon[-\s]?cum"
    r"|(?<!american\s)\bdepositary\s+sh"
    r"|\bdepository\s+sh",
    re.IGNORECASE,
)
# The issuer gate R3 needs before it trusts a debt marker. It is the corporate-entity
# marker plus the two finance-vehicle forms that carry none of those words:
# "Brookfield Infrastructure Finance ULC 5 % Notes …" (BIPH) and "Dillards Capital Trust
# I CAP SECS 7.5%" (DDT). Not dropped altogether: stock-typed structured ETFs such as
# "Corgi U.S. Equities 30% Structu" carry a "%" and no issuer marker, and must stay.
_DEBT_ISSUER_GATE_RE = re.compile(
    _CORP_ENTITY_RE.pattern + r"|\bulc\b|\bcapital\s+trust\b",
    re.IGNORECASE,
)


def _grammar_drop_reason(row: StockSearchResult, sym: str) -> Optional[str]:
    """Why a row is a non-common listing by its symbol or name alone, or None."""
    if _DASH_PREFERRED_RE.match(sym):
        return "dash_preferred"
    if _NASDAQ_PREFERRED_RE.match(sym):
        return "nasdaq_preferred"
    if (_NASDAQ_MUTUAL_FUND_RE.match(sym)
            and (row.exchange_short_name or "").upper() == "NASDAQ"):
        return "mutual_fund"
    if row.type == "stock":
        name = (row.name or "")[:_MAX_NAME_CHARS]
        if _DEBT_ISSUER_GATE_RE.search(name) and _DEBT_PREF_NAME_RE.search(name):
            return "debt_or_preferred_name"
    return None


# ── Same-issuer root twins (R4) ───────────────────────────────────────────────
# A note, preferred or warrant listed as the issuer's ticker plus letters, under the
# issuer's exact name and with no grammar to key on: AGNCL, APOS, SOMN, SOJD, METCI,
# OPENL, and padded-root warrants VFSWW, PCTTW, AUROW, BIOTW. Replayed over every
# prefix pair in the 70,318-row active list (2026-09-25), the only exchange-listed
# commons this caught were GOOGL (Alphabet Class A) and BBDO (Bradesco common ADR),
# hence the allow-list. Class letters A/B/C/J/K are exempt because real classes use
# them without a dash (FOXA, NWSA, UAA, METCB, RDIB, LILAK, UONEK).
_ROOT_TWIN_SYMBOL_RE = re.compile(r"^[A-Z]{2,6}$")
_CLASS_LETTERS = frozenset("ABCJK")
_CLASS_NAME_RE = re.compile(r"\bclass\s+[a-z]\b", re.IGNORECASE)
_KNOWN_SHARE_CLASS_SYMBOLS = frozenset({"GOOGL", "BBDO"})
# Everything from the first of these on is a security description, not the issuer:
# "Southern Company (The) Series 2", "Duke Energy Corporation Units 1.08.29",
# "Duke Robotics Corp. C/wts Exp 06/05/2031".
_ISSUER_CUT_RE = re.compile(
    r"%|\s-\s|\s\d"
    r"|\b(?:series|ser|notes?|nts|jr|sub|subordinated|units?|pfd|pref|preferred"
    r"|depositary|depository|senior|fixed|perp|perpetual|cum|cumulative|conv"
    r"|convertible|class|warrants?|(?:c/)?wts?|rights?)\b",
    re.IGNORECASE,
)
_ISSUER_PUNCT_RE = re.compile(r"[.,&()'’/]")
_ISSUER_SUFFIX_WORDS = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "company", "plc", "ltd",
    "limited", "llc", "lp", "l", "p",
})
_MIN_ISSUER_KEY = 3


def _issuer_key(name: Optional[str]) -> str:
    """The issuer part of a security name, normalized for equality — "" when too short.

    "The Southern Company" and "Southern Company (The) Series 2" both give "southern";
    "Instinct Bio Technical Co. Holdings Inc." and "…Company Holdings Inc. Warrants"
    both give "instinct bio technical holdings". A key under 3 characters ("3M Company"
    → "3m") never matches anything: too little left to prove two rows are one issuer.
    """
    n = (name or "")[:_MAX_NAME_CHARS].lower().replace("(the)", " ").strip()
    if n.startswith("the "):
        n = n[4:]
    m = _ISSUER_CUT_RE.search(n)
    if m:
        n = n[:m.start()]
    n = _ISSUER_PUNCT_RE.sub(" ", n)
    key = " ".join(w for w in n.split() if w not in _ISSUER_SUFFIX_WORDS)
    return key if len(key) >= _MIN_ISSUER_KEY else ""


def _is_root_twin(
    row: StockSearchResult,
    sym: str,
    key: str,
    page: Dict[str, StockSearchResult],
    directory: Dict[str, str],
) -> bool:
    exchange = (row.exchange_short_name or "").upper()
    for i in range(1, len(sym)):
        base = sym[:i]
        # ⚠️ The base must be LIVE. JBT (dead) and JBTM (live) are both "JBT Marel
        # Corporation"; with a dead base allowed, typing "JBT" hid the company's only
        # live ticker — and during an outage every query did.
        if base not in directory:
            continue
        base_row = page.get(base)
        if base_row is not None and (base_row.exchange_short_name or "").upper() == exchange:
            base_name = base_row.name
        else:
            base_name = directory.get(base) or ""
        if _issuer_key(base_name) != key:
            continue
        extra = sym[i:]
        return not (len(extra) == 1 and extra in _CLASS_LETTERS)
    return False


def _is_root_twin_candidate(row: StockSearchResult, sym: str, query_upper: str) -> bool:
    return not (
        sym == query_upper
        or row.type != "stock"
        or not _ROOT_TWIN_SYMBOL_RE.match(sym)
        or sym in _KNOWN_SHARE_CLASS_SYMBOLS
        or _CLASS_NAME_RE.search((row.name or "")[:_MAX_NAME_CHARS])
    )


def _drop_root_twins(
    rows: List[StockSearchResult],
    query_upper: str,
    directory: Dict[str, str],
) -> List[StockSearchResult]:
    page: Dict[str, StockSearchResult] = {}
    for r in rows:
        page.setdefault((r.symbol or "").upper(), r)

    kept: List[StockSearchResult] = []
    for r in rows:
        sym = (r.symbol or "").upper()
        if _is_root_twin_candidate(r, sym, query_upper):
            key = _issuer_key(r.name)
            if key and _is_root_twin(r, sym, key, page, directory):
                continue
        kept.append(r)
    return kept


# ── Ranking ───────────────────────────────────────────────────────────────────

def _rank(rows: List[StockSearchResult], query_upper: str) -> List[StockSearchResult]:
    """Exact symbol first, then its siblings, then FMP's own order (stable sort).

    Siblings are rows whose dash root IS the query (BRK → BRK-B, BRK-A, which ranked
    7th and 9th behind the dead BRKS) or which share the exact row's issuer (UA → UAA,
    GOOG → GOOGL), so a dual-class pair always sits together.
    """
    exact = next((r for r in rows if (r.symbol or "").upper() == query_upper), None)
    exact_key = _issuer_key(exact.name) if exact is not None else ""

    def tier(r: StockSearchResult) -> int:
        sym = (r.symbol or "").upper()
        if sym == query_upper:
            return 0
        if query_upper and sym.split("-", 1)[0] == query_upper:
            return 1
        if exact_key and _issuer_key(r.name) == exact_key:
            return 1
        return 2

    return sorted(rows, key=tier)


def refine_listings(
    rows: List[StockSearchResult],
    query_upper: str,
    directory: Optional[Dict[str, str]],
) -> List[StockSearchResult]:
    """Apply every listing rule to the endpoint's non-crypto rows, in order.

    1. Grammar drops (dash preferred, NASDAQ preferred, NASDAQ mutual fund, debt name).
    2. Liveness: not in ``directory`` → drop. Skipped when ``directory`` is None.
    3. The corporate-action twin dedupe — AFTER liveness, so a twin only collapses
       onto a base that is still trading.
    4. Same-issuer root twins — only with a directory, because a base must be live.
    5. Rank.

    The symbol the user typed exactly is exempt from 1-4.
    """
    q = (query_upper or "").strip().upper()
    kept: List[StockSearchResult] = []
    dropped: Dict[str, int] = {}
    for r in rows:
        sym = (r.symbol or "").upper()
        if sym != q:
            reason = _grammar_drop_reason(r, sym)
            if reason is None and directory is not None and sym not in directory:
                reason = "inactive"
            if reason is not None:
                dropped[reason] = dropped.get(reason, 0) + 1
                continue
        kept.append(r)

    before = len(kept)
    kept = _dedupe_secondary_listings(kept, keep_symbol=q)
    if before != len(kept):
        dropped["corporate_action_twin"] = before - len(kept)

    if directory is not None:
        before = len(kept)
        kept = _drop_root_twins(kept, q, directory)
        if before != len(kept):
            dropped["root_twin"] = before - len(kept)

    if dropped:
        logger.debug(
            "stock_search q=%r: kept %d of %d rows, dropped %s (directory=%s)",
            q, len(kept), len(rows), dropped,
            "unavailable" if directory is None else len(directory),
        )
    return _rank(kept, q)


def would_keep(
    row: StockSearchResult,
    query_upper: str,
    directory: Optional[Dict[str, str]],
) -> bool:
    """Would ``refine_listings`` keep this row on its own? For the ticker-match check.

    ⚠️ The endpoint lets a prefix hit skip the name search only if the row SURVIVES the
    rules. It used to count any US row, so "visa" — whose only US prefix hit is the
    mutual fund VISAX — skipped the name search, VISAX was then hidden, and searching
    for Visa returned nothing (2026-09-25 review). Page-relative twin rules (R5, and R4
    against a base on the page) cannot be judged from one row; R4 is judged against the
    directory instead.
    """
    q = (query_upper or "").strip().upper()
    sym = (row.symbol or "").upper()
    if sym == q:
        return True
    if _grammar_drop_reason(row, sym) is not None:
        return False
    if directory is None:
        return True
    if sym not in directory:
        return False
    if _is_root_twin_candidate(row, sym, q):
        key = _issuer_key(row.name)
        if key and _is_root_twin(row, sym, key, {}, directory):
            return False
    return True


# The endpoint calls these. A bug in the rules must cost the refinement, never the search.
_REFINE_FAILURE_LOG_INTERVAL = 600.0
_refine_failures_logged_at: Dict[str, float] = {}


def _note_rule_failure(what: str, query_upper: str, e: Exception) -> None:
    """ERROR with the stack once per (step, exception type) per 10 min — the
    `FMPClient._note_unpredicted_402` idea: a deterministic bug would otherwise raise on
    every debounced keystroke and flood Sentry."""
    kind = f"{what}:{type(e).__name__}"
    now = time.time()
    if now - _refine_failures_logged_at.get(kind, 0.0) >= _REFINE_FAILURE_LOG_INTERVAL:
        _refine_failures_logged_at[kind] = now
        logger.error(
            "stock_search: %s failed for q=%r (%s: %s) — degrading; repeats are logged "
            "once per %ds",
            what, query_upper, type(e).__name__, e, int(_REFINE_FAILURE_LOG_INTERVAL),
            exc_info=True,
        )
    else:
        logger.debug("stock_search: %s failed again for q=%r (%s)", what, query_upper, kind)


def current_directory(query_upper: str = "") -> Optional[Dict[str, str]]:
    """``get_active_listings()`` for one request, never raising (None = rules that need
    it are skipped). Take it ONCE per request and pass it to both the ticker-match check
    and ``apply_listing_rules``, so the two can never disagree about liveness."""
    try:
        return get_active_listings()
    except Exception as e:
        _note_rule_failure("active-listing lookup", query_upper, e)
        return None


def apply_listing_rules(
    rows: List[StockSearchResult],
    query_upper: str,
    directory: Optional[Dict[str, str]],
) -> List[StockSearchResult]:
    """``refine_listings``, falling back to the corporate-action dedupe alone if
    anything in it raises."""
    try:
        return refine_listings(rows, query_upper, directory)
    except Exception as e:
        _note_rule_failure("listing rules", query_upper, e)
        return _dedupe_secondary_listings(rows, keep_symbol=query_upper)


# ── Active-listing directory ──────────────────────────────────────────────────
# IN MEMORY ONLY, a deliberate departure from the two-tier cache rule:
#   • it is one FMP call per refresh, so a persistent tier saves one call per restart;
#   • persisting a bulk FMP symbol list is the ToS 2.6.1 redistribution surface
#     migration 157 avoids;
#   • reloading ~27k rows over PostgREST (1000-row pages) is slower than the one fetch.
# Precedent: `price_service._get_universe` and `market_movers_service` closes.
#
# Measured 2026-09-25: 70,318 rows, 1.2 MB gzip / 5.45 MB JSON, ~0.8 s; json parse
# ~41 ms + build ~9 ms on the event loop (FMPClient parses there), i.e. a ~50 ms stall
# of the single worker per refresh. Accepted at a 2 h cadence.
_DIRECTORY_KEY = "active_listings"
_DEGRADED_KEY = "active_listings:degraded"
_FRESH_TTL = 2 * 3600.0
_MAX_STALE = 7 * 86400.0
_DEGRADED_TTL = 60.0
_REFRESH_TIMEOUT = 20.0
# Payload floors. Today's list is 70,318 rows / 27,273 dot-free symbols; a truncated or
# error body must never become the whitelist, or it would hide every live company.
_MIN_ROWS = 50_000
_MIN_US_SYMBOLS = 20_000
_MIN_FRACTION_OF_LAST_GOOD = 0.8
_FAILURES_BEFORE_ERROR = 3

_cache: Dict[str, Tuple[float, Any]] = {}
_inflight: Dict[str, "asyncio.Task[Optional[Dict[str, str]]]"] = {}
_consecutive_failures = 0


class ActiveListingsRefused(Exception):
    """The actively-trading payload failed a sanity floor and was not cached."""


def get_active_listings() -> Optional[Dict[str, str]]:
    """``{symbol: name}`` of every actively trading dot-free symbol, or None.

    NEVER awaits. A search is a keystroke; a cold or stale directory schedules one
    background refresh and this request is served from what is already held — the
    last good copy up to 7 days old, else None (callers then skip the rules that need
    it). The first search after a deploy therefore runs grammar rules only.
    """
    now = time.time()
    entry = _cache.get(_DIRECTORY_KEY)
    if entry is not None:
        ts, directory = entry
        age = now - ts
        if age <= _FRESH_TTL:
            return directory
        if age <= _MAX_STALE:
            _schedule_refresh(now)
            return directory
        logger.warning(
            "stock_search: active-listing directory is %.1f days old — dropping it; "
            "the liveness filter is off until a refresh succeeds",
            age / 86400,
        )
        _cache.pop(_DIRECTORY_KEY, None)
    _schedule_refresh(now)
    return None


def _schedule_refresh(now: float) -> None:
    if _DIRECTORY_KEY in _inflight:
        return
    degraded = _cache.get(_DEGRADED_KEY)
    if degraded is not None and now - degraded[0] < _DEGRADED_TTL:
        return  # an outage must not become one list fetch per keystroke
    try:
        task = asyncio.get_running_loop().create_task(
            refresh_active_listings(), name="stock-search-active-listings",
        )
    except RuntimeError:
        return  # no running loop (sync caller) — the next async caller schedules it
    _inflight[_DIRECTORY_KEY] = task
    task.add_done_callback(_on_refresh_done)


def _on_refresh_done(task: "asyncio.Task[Optional[Dict[str, str]]]") -> None:
    # Cleared from the task's OWN completion, so a cancelled caller can never remove a
    # still-running refresh and let the next keystroke start a duplicate.
    if _inflight.get(_DIRECTORY_KEY) is task:
        _inflight.pop(_DIRECTORY_KEY, None)
    if not task.cancelled():
        task.exception()  # retrieved: the coroutine logs its own failures


def _build_directory(rows: Any) -> Dict[str, str]:
    if not isinstance(rows, list):
        raise ActiveListingsRefused(f"expected a list, got {type(rows).__name__}")
    directory: Dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sym = row.get("symbol")
        if not isinstance(sym, str):
            continue
        sym = sym.strip().upper()
        if not sym or "." in sym:
            continue  # foreign listings — search never shows them
        name = row.get("name")
        directory[sym] = name if isinstance(name, str) else ""
    if len(rows) < _MIN_ROWS or len(directory) < _MIN_US_SYMBOLS:
        raise ActiveListingsRefused(
            f"payload too small: {len(rows)} rows / {len(directory)} dot-free symbols "
            f"(floors {_MIN_ROWS} / {_MIN_US_SYMBOLS})"
        )
    last = _cache.get(_DIRECTORY_KEY)
    # Relative floor only against a copy that is still usable — comparing with an
    # expired one would refuse a legitimately smaller list forever.
    if last is not None and time.time() - last[0] <= _MAX_STALE:
        floor = int(len(last[1]) * _MIN_FRACTION_OF_LAST_GOOD)
        if len(directory) < floor:
            raise ActiveListingsRefused(
                f"payload shrank: {len(directory)} dot-free symbols vs {len(last[1])} "
                f"last good (floor {floor})"
            )
    return directory


async def refresh_active_listings() -> Optional[Dict[str, str]]:
    """Fetch, validate and install the directory. Returns it, or None on failure.

    Never raises ``Exception``: a failure keeps the last good copy, writes a 60 s
    degraded memo and logs WARNING (ERROR once it has failed 3 times running, so a
    filter stuck off is visible in Sentry). ``CancelledError`` propagates.
    """
    global _consecutive_failures
    started = time.monotonic()
    try:
        rows = await asyncio.wait_for(
            get_fmp_client().get_actively_trading_list(), _REFRESH_TIMEOUT,
        )
        directory = _build_directory(rows)
    except Exception as e:
        _consecutive_failures += 1
        _cache[_DEGRADED_KEY] = (time.time(), True)
        n = _consecutive_failures
        level = (
            logging.ERROR
            if n == _FAILURES_BEFORE_ERROR or (n > _FAILURES_BEFORE_ERROR and n % 60 == 0)
            else logging.WARNING
        )
        logger.log(
            level,
            "stock_search: active-listing refresh failed (%s: %s), %d in a row — "
            "keeping the last good copy (%s)",
            type(e).__name__, e, n,
            "none" if _DIRECTORY_KEY not in _cache else "held",
        )
        return None
    _cache[_DIRECTORY_KEY] = (time.time(), directory)
    _cache.pop(_DEGRADED_KEY, None)
    _consecutive_failures = 0
    logger.info(
        "stock_search: active-listing directory refreshed — %d rows, %d dot-free "
        "symbols in %.2fs",
        len(rows), len(directory), time.monotonic() - started,
    )
    return directory
