"""Build the broad benchmark universe for INDUSTRY + SECTOR medians.

Per FMP industry, pulls every actively-traded, US-LISTED operating company ABOVE a
market-cap floor (default $500M — excludes micro/penny-stock noise while keeping
small+mid+large caps, so medians are fair to small-cap companies). Groups by industry +
modal parent sector, capturing each ticker's market cap.

US-listed operating companies only (owner decision 2026-10-07). The 2026-06-24 file sent
none of the filters below and carried ~1,275 open-end mutual funds / ETFs, 615 `.TO`
listings (147 of them a second copy of a US ticker), and an 'Asset Management' list cut
at exactly the 1,000-row `limit` (TROW, BEN, IVZ… missing). So:

  * The screener is asked for `isEtf=false`, `isFund=false`, `isActivelyTrading=true` and
    `exchange=NYSE,NASDAQ,AMEX` — the US-listing filter `price_service`'s universe sweep
    and `theme_rotation` already send. `exchange`, NOT `country=US`: `country` is the
    issuer's domicile, so it would drop US-listed foreign issuers (TSM, ASML, NVO — real
    NYSE/NASDAQ listings) while saying nothing about WHERE a row trades.
  * Every row is re-checked here, because a filter FMP silently ignores looks exactly like
    a clean answer. Dropped and counted: a row flagged `isEtf` / `isFund`; a dotted
    foreign-exchange suffix (`SHOP.TO`, `VOD.L` — FMP spells US share classes with a
    DASH, `BRK-B` / `BF-B`, and the 2026-06-24 file held no dotted US symbol); a non-US
    exchange; a cap that is missing, non-finite or under the floor. A fund / ETF / foreign
    drop means a server-side filter was not honoured, so it is logged at WARNING; a few rows
    just under the floor are FMP's own cap drift (INFO) unless they are over 10% of all rows.
  * Only operating companies' COMMON shares vote (`not_common_share`, INFO — the screener
    cannot filter these). FMP lists an issuer's preferreds, notes, warrants, units and
    when-issued rows under the issuer's name and prices them with the issuer's share count
    (SOJC, a Southern Co. note: $20.7B; TBB, an AT&T note: $126B; EP-PC / MER-PK: $112B /
    $192B), so each would vote again in the issuer's median with junk ratios. The June file
    held ~15 of the 52 Regulated Electric names this way. Caught with the ticker-search
    grammar (`stock_search_service`), on symbol AND `companyName`: dash and NASDAQ
    5th-letter preferreds (EP-PC, FCNCN, AGNCP), notes named as notes (SOJC "… JR 2017B NT
    77", TBB "… 5.35% GLB NTS 66"), one-letter dash actions (ABC-U / -R / -W), a NASDAQ
    5th-letter W/U/R/V whose name says warrant / unit / right / when-issued, and twins of a
    base listing (SOMN beside SO, PMTU beside PMT, HONIV beside HON, CCXIW beside CCXI).
    Plus three debt words the search does not carry (`_BUILDER_DEBT_NAME_RE`): "bonds",
    "first mortgage" and "collateral trust" — a utility SUBSIDIARY's mortgage bonds
    ("Entergy Arkansas, LLC First Mortgage Bonds", EAI) carry the subsidiary's name, so no
    twin rule can pair them with the parent.
  * The twin rules read a MARKET-WIDE directory (every industry's common rows, collected
    before any filtering): FMP files a note under another industry than its issuer (CGABL in
    Financial - Credit Services beside CG in Asset Management; HONAV in Aerospace & Defense
    beside HON in Conglomerates). The issuer-name equality still guards distinct companies;
    every twin of a listing in ANOTHER industry is named at WARNING.
  * An ADR / GDS is common equity: "American Depositary / Depository Shares" and "Global
    Depositary / Depository Shares" never drop a row on their own — only beside a coupon,
    notes or preferred marker. A "Series B / A / L" beside it is KEPT: it names a Mexican
    or Chilean issuer's ordinary class (PAC, AMX, SQM, KOF, FMX).
  * A name that says Fund / ETF / ETN is dropped (`fund_name`, INFO): FMP's `isFund` marks
    open-end mutual funds only, so closed-end funds ("… Income Fund") pass the server filter.
    A trust that only reads like a fund is kept and NAMED at WARNING — "Trust" also names
    operating REITs and royalty trusts. A business development company is an operating
    company (it files a 10-K) and votes (ARCC, OBDC) — except one whose name says Fund
    (MSDL, BXSL "… Lending Fund"), which the name rule drops: it cannot tell a BDC from a
    closed-end fund. A closed-end fund whose name says neither Fund nor Trust
    (Tri-Continental Corporation) is neither dropped nor named.
  * A kept row trading under 0.02% of its reported cap a day (price × avgVolume) is NAMED
    at WARNING: the signature of a note FMP prices with the issuer's share count that no
    name rule could read (FMP cuts these names at ~31 characters: "Entergy Louisiana, LLC
    Collater", "PPL Capital Funding, Inc. 2007"). A thinly traded ADR can show it too, so
    it is a list to read, not a drop.
  * One vote per issuer (`same_issuer`, INFO): GOOG + GOOGL, BRK-A + BRK-B, FOX + FOXA each
    carry the issuer's fundamentals, so only the most liquid class of each is kept.
  * An industry is PAGED until a short page (the screener paginates — see
    `FMPClient.get_company_screener`). One still full after `_SCREENER_MAX_PAGES`, or a
    full page that adds no new symbol (FMP ignoring `page`), FAILS the build instead of
    being truncated.
  * Any failed request (a 429 is retried with backoff first) FAILS the build: exit 1,
    nothing written. A universe missing an industry empties that industry's peer
    medians until the next build — worse than keeping the previous file.
  * The build is compared with the file it replaces and REFUSED (exit 3, nothing
    written) when:
      - the ticker count drops by more than 10%. `--allow-shrink PCT` raises that bar to
        PCT% (50 when the flag is given bare) — never further: an override that let any
        drop through would also wave through the soft failure it exists to catch;
      - an industry that held at least 20 operating tickers (no dotted suffix, not a
        5-letter X fund symbol) has none now, unless it is named with `--allow-missing`.
        A screener that answers a real industry with an empty page looks exactly like an
        industry that left, and its peer medians would be empty until the next build.

This is a SEPARATE file from `industry_universe.json` (which has NO floor and feeds the
moat/dossier jobs) — do not conflate them. Output: backend/data/benchmark_universe.json,
format unchanged (read by `industry_benchmark_service._load_universe` through
`universe_data.load_universe`). It is NOT in git (FMP ToS §2.6.1): after a build, upload
it to the private `universe-data` Supabase Storage bucket.
~160 FMP calls (1 available-industries + ~159 screener calls), ~30s.

Usage (from backend/):
    ./venv/bin/python -m scripts.build_benchmark_universe                    # $500M floor
    ./venv/bin/python -m scripts.build_benchmark_universe --floor 1000000000 # $1B floor

    # The FIRST US-only regeneration (2026-10) shrinks the file by far more than 10%: the
    # June file held 615 dotted rows, 1,292 five-letter-X funds and ~800 ETF-like rows in
    # the 'Asset Management*' industries, so about 5,704 → ~3,100 (~45%). It needs the
    # override — once. Industries that only held funds leave with them: the refusal names
    # each one and the exact flag to add, after you have checked it really left:
    ./venv/bin/python -m scripts.build_benchmark_universe --allow-shrink 50 \
        --allow-missing "Asset Management - Bonds" --allow-missing "Asset Management - Global"

The shrink guard compares with the CURRENT local file. The live copy is the one in the
bucket: download it to backend/data/ first, or the guard has no baseline (WARNING, the
build proceeds).

Exit codes: 0 written · 1 a request failed, an industry would be truncated, or nothing
usable came back (nothing written) · 3 shrink refused or an industry went missing
(nothing written).

Idempotent — re-running overwrites the file with a fresh snapshot (written atomically).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, NamedTuple, Optional, Tuple, Union

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.integrations.fmp import (  # noqa: E402
    FMPClient,
    FMPException,
    FMPRateLimitException,
)
from app.log_redaction import SecretRedactingFilter, redact_secrets  # noqa: E402
from app.schemas.stock import StockSearchResult  # noqa: E402
# The ticker-search listing grammar, REUSED rather than re-derived: it was built from a live
# sweep of FMP's own rows for exactly these families (dash and NASDAQ 5th-letter preferreds,
# notes on bare tickers, corporate-action and same-issuer twins) and is pinned row by row by
# tests/test_stock_search_listing_rules.py.
from app.services.stock_search_service import (  # noqa: E402
    _DASH_ACTION_SUFFIX_RE,
    _DEBT_PREF_NAME_RE,
    _MAX_NAME_CHARS,
    _dedupe_secondary_listings,
    _grammar_drop_reason,
    _is_root_twin,
    _is_root_twin_candidate,
    _issuer_key,
    _secondary_base_symbol,
)

logger = logging.getLogger(__name__)
# FMP puts `apikey=` in the query string and a raw httpx error repeats the whole URL. Every
# record THIS module logs is scrubbed here — message and traceback — whoever configured
# logging (a test, another script calling `main()`); `__main__` also filters the root
# handlers, as app/main.py does, for the other modules' records.
logger.addFilter(SecretRedactingFilter())

_OUTPUT_PATH = _REPO_ROOT / "data" / "benchmark_universe.json"
_DEFAULT_FLOOR = 500_000_000  # $500M — small-cap inclusive, micro/penny excluded

# US listing venues, as the screener's `exchange` filter spells them.
_US_EXCHANGES: Tuple[str, ...] = ("NYSE", "NASDAQ", "AMEX")
# A row's own exchange is accepted when it IS one of these or starts with one plus a space
# ("NASDAQ Global Select", "NYSE American") — the long form some rows carry in `exchange`.
_US_EXCHANGE_PREFIXES: Tuple[str, ...] = _US_EXCHANGES + ("NEW YORK STOCK EXCHANGE",)

# Today's per-call limit (the screener caps a call at 10,000 rows whatever `limit` says).
# A US-only, fund-free industry above $500M is a few hundred rows, so a second page is
# unusual — it is logged when it happens.
_SCREENER_PAGE_LIMIT = 1000
_SCREENER_MAX_PAGES = 5

# A 429 is backpressure, not an answer: retried on this schedule (or the server's own
# Retry-After, capped) before the industry counts as failed.
_RATE_LIMIT_BACKOFF_SECONDS: Tuple[float, ...] = (5.0, 15.0, 45.0)
_MAX_RETRY_AFTER_SECONDS = 120.0

_CONCURRENCY = 8

# The build is refused when it would drop MORE than this share of the previous tickers.
_MAX_SHRINK_PERCENT = 10
# `--allow-shrink` given with no value. Bounded on purpose: the first US-only build is
# expected to drop ~45%, and a bar any larger would also pass an industry that FMP
# soft-failed with an empty page on that same run.
_DEFAULT_ALLOW_SHRINK_PERCENT = 50
# A previous industry with at least this many operating tickers (no dotted suffix, not a
# 5-letter X fund symbol) that has none now refuses the build unless it is named with
# `--allow-missing`. Smaller ones only get the 'no constituents now' WARNING.
_MISSING_INDUSTRY_MIN_TICKERS = 20

EXIT_OK = 0
EXIT_BUILD_FAILED = 1
EXIT_SHRINK_REFUSED = 3

# The SHAPE of a US common-share symbol: 1-5 letters, optionally a one-letter class after a
# dash (BRK-B, BF-B, MKC-V). Only a first gate — the dash preferreds (EP-PC) and the
# two-letter dash actions (-WT, -UN, -RI) fail it, but notes on bare tickers (SOJC, TBB),
# NASDAQ 5th-letter preferreds (FCNCN, AGNCP), warrants (CCXIW), when-issued rows (HONIV)
# and one-letter dash actions (ABC-U) all match it. `_listing_reason` and the twin rules in
# `_filter_rows` catch those.
_COMMON_SHARE_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(?:-[A-Z])?$")

# The search grammar's verdicts that are NOT a drop here. Its "mutual_fund" (a NASDAQ
# 5-letter X) is left to FMP's `isFund` flag, this builder's authority for funds; an
# unflagged one is kept and named (`_log_fund_like_symbols`).
_SEARCH_GRAMMAR_KEPT = frozenset({"mutual_fund"})

# NASDAQ's fifth-letter codes for a warrant (W), unit (U), right (R) and when-issued (V)
# listing. A symbol of this shape is dropped only with proof: a same-issuer base row (the
# twin rules) or a name that says what it is. One kept without either is named at WARNING.
_NASDAQ_ACTION_SYMBOL_RE = re.compile(r"^[A-Z]{4}[WURV]$")
# Consulted ONLY for that symbol shape: "Unit Corporation" is an operating company.
_ACTION_NAME_RE = re.compile(r"\b(?:warrants?|wts?|units?|rights?|when[\s-]?issued)\b",
                             re.IGNORECASE)

# A name that SAYS fund / ETF / ETN: the same two patterns `stocks._get_asset_type` lets win
# outright (copied, not imported, so this script does not load the endpoint layer;
# tests/test_benchmark_universe_builder_round2_listings.py pins them equal). FMP's `isFund`
# marks open-end mutual funds; closed-end funds ("Eaton Vance Limited Duration Income Fund",
# "CBRE Global Real Estate Income Fund") come back isFund=false and would vote in the
# 'Asset Management - *' medians and the Financial Services sector median. "Trust" alone is
# NOT a fund word — Federal Realty Investment Trust, Sabine Royalty Trust and Northern Trust
# operate — so a trust that only READS like a fund is named, not dropped (`_log_suspect_rows`).
_FUND_NAME_RE = re.compile(r"\bfunds?\b", re.IGNORECASE)
_ETF_NAME_RE = re.compile(r"\b(?:etfs?|etns?|adrhedged)\b", re.IGNORECASE)
_TRUST_RE = re.compile(r"\btrust\b", re.IGNORECASE)
# Read only beside "Trust". The second line (round 3) adds the closed-end and physical-metal
# trust forms the first missed: "Royce Value Trust", "The Gabelli Equity Trust", "BlackRock
# Science and Technology Trust", "BlackRock Capital Allocation Term Trust", "Sprott Physical
# Gold and Silver Trust", "Royce Micro-Cap Trust" — none of them is an operating REIT's or a
# royalty trust's word.
_FUND_STYLE_WORD_RE = re.compile(
    r"\b(?:income|municipal|muni|bonds?|dividend|premium|yield|credit|opportunit(?:y|ies)"
    r"|strateg(?:y|ies|ic)|buy[-\s]?write|tax[-\s](?:advantaged|exempt|free|managed)"
    r"|value|equity|physical|term|allocation|science|technology|(?:micro|small|mid)[-\s]?cap)\b",
    re.IGNORECASE,
)

# Debt words the shared search grammar does not carry — applied HERE only (the search keeps
# its own vocabulary). A utility subsidiary's mortgage bonds list under the SUBSIDIARY's name
# ("Entergy Arkansas, LLC First Mortgage Bonds", EAI $0.94B; "Entergy Louisiana, LLC
# Collateral Trust Mortgage Bonds", ELC $37.75B in the June file), so no twin rule can pair
# them with the parent (ETR). Plural "bonds" only: "BlackRock Taxable Municipal Bond Trust"
# is a closed-end fund named at WARNING, not a note. "Debentures" is already in the shared
# `_DEBT_PREF_NAME_RE`.
_BUILDER_DEBT_NAME_RE = re.compile(
    r"\bbonds\b|\bfirst\s+mortgage\b|\bcollateral\s+trust\b", re.IGNORECASE,
)

# A depositary receipt over a foreign issuer's ORDINARY shares is that issuer's US common
# listing (ARM, TSM, NVO). The shared grammar spares only the exact "American Depositary
# Shares" spelling (a look-behind); "American Depository Shares" and "Global Depositary /
# Depository Shares" hit its depositary-share branches, which exist for PREFERRED
# depositary shares (RILYL "… Depositary Shares … Preferred Stock", GOOGN "Depository Shs
# Repr 1/20th Conv Pfd"). So the ADR / GDS phrase is cut out of the name before the debt /
# preferred rules read it: a coupon, "notes", "pfd" or "preferred stock" left over still
# drops the row, and so does a PREFERRED marker beside the ADR phrase (`_ADR_NON_COMMON_RE`:
# "%", "pfd", "non-cum", "pref", "preferred", "preference shares" — the first three are the
# shared rule's too, restated so the ADR vocabulary reads in one place; bare "pref" /
# "preferred" and "preference shares" are this rule's alone).
# Never "series" (round 5, B4-1): a Mexican or Chilean issuer's ORDINARY voting class is
# named "Series B" / "Series A" / "Series L" ("Grupo Aeroportuario del Pacífico, S.A.B. de
# C.V. American Depositary Shares, each representing 10 Series B shares" — PAC, ASR, OMAB,
# AMX, SQM, KOF, FMX), so a series word beside an ADR phrase is that issuer's common.
_ADR_PHRASE_RE = re.compile(
    r"\b(?:american|global)\s+deposit(?:a|o)ry\s+(?:shares?|shs|receipts?)\b",
    re.IGNORECASE,
)
_ADR_NON_COMMON_RE = re.compile(
    r"%|\bpfd\b|\bnon[-\s]?cum|\bpref(?:erred)?\b|\bpreference\s+(?:shares?|shs|stock)\b",
    re.IGNORECASE,
)

# A kept row whose daily dollar volume (price × avgVolume) is under this share of its
# reported cap is named at WARNING. A note priced with the issuer's share count trades a
# sliver of that cap: SOJC $0.84M a day against $20.7B (0.004%); a common trades far more
# (DUK ~0.38%).
_THIN_DAILY_TURNOVER = 0.0002   # 0.02%

# A row dropped ONLY for its cap is still a live US common listing, so it can be the base
# that proves another row a twin (a SPAC's unit can sit over the floor while its common is
# just under it).
_TWIN_BASE_REASONS = frozenset({"below_floor", "bad_market_cap"})
_SUSPECTS_NAMED = 30

# Drop reasons that mean a SERVER-side filter was not honoured (WARNING), as opposed to
# what the screener cannot filter at all or a known drift (INFO, with the note below).
_UNEXPECTED_DROP_REASONS = frozenset({
    "etf", "fund", "inactive", "foreign_suffix", "non_us_exchange",
    "bad_market_cap", "malformed",
})
_EXPECTED_DROP_NOTES = {
    "not_common_share": (
        "preferreds, notes, warrants, units, rights and when-issued listings (by symbol, "
        "name, or as a same-issuer twin), which the screener cannot filter"
    ),
    "fund_name": (
        "named as a fund / ETF / ETN — FMP's isFund flags open-end funds only, so "
        "closed-end funds pass the server filter"
    ),
    "same_issuer": (
        "another share class of an issuer already counted (one vote per issuer: the most "
        "liquid class is kept)"
    ),
    # The 2026-06-24 file held 13 such rows ($364M-$499M under a $500M floor): FMP filters
    # on one cap and reports another, a few percent apart.
    "below_floor": "FMP's floor filter and the marketCap it reports drift apart",
}
# Sub-floor rows above this share of every row returned are not drift: the
# `marketCapMoreThan` filter itself was ignored.
_BELOW_FLOOR_ALARM_PERCENT = 10
_DROP_EXAMPLES = 5

Sleep = Callable[[float], Awaitable[Any]]


class UniverseBuildError(Exception):
    """An FMP answer the build cannot use. Fails the whole build (exit 1)."""


class UnusableAnswerError(UniverseBuildError):
    """FMP answered, but not with a list of rows (or with no industry at all)."""


class TruncatedIndustryError(UniverseBuildError):
    """An industry the screener cannot return completely — never written truncated."""


# Failures whose message says it all; anything else is a bug and gets its stack.
_EXPECTED_FAILURES = (FMPException, httpx.HTTPError, UniverseBuildError)


def _describe(exc: BaseException) -> str:
    """`Type: message` with secrets scrubbed — the ONLY form an exception takes in this
    script's log lines and failure summary. `FMPClient` re-raises a raw
    `httpx.HTTPStatusError` for a 400 / 403 / 404 / Cloudflare 52x, and its message is the
    request URL, `apikey=<key>` included."""
    return redact_secrets(f"{type(exc).__name__}: {exc}")


def _retry_delay(exc: BaseException, default: float) -> float:
    """The server's Retry-After in seconds when it is a usable number, else `default`."""
    raw = getattr(exc, "retry_after", None)
    try:
        secs = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(secs) or secs <= 0:
        return default
    return min(secs, _MAX_RETRY_AFTER_SECONDS)


async def _request(
    fmp: Any, endpoint: str, params: Optional[Dict[str, Any]], *, sleep: Sleep, context: str,
) -> Any:
    """One FMP call, a 429 retried on `_RATE_LIMIT_BACKOFF_SECONDS`; anything else raises."""
    backoff = _RATE_LIMIT_BACKOFF_SECONDS
    for attempt in range(len(backoff) + 1):
        try:
            # A fresh dict per attempt: the client adds `apikey` to the one it is given.
            return await fmp._make_request(
                endpoint, params=dict(params) if params is not None else None,
            )
        except FMPRateLimitException as exc:
            if attempt >= len(backoff):
                raise
            delay = _retry_delay(exc, backoff[attempt])
            logger.warning(
                "benchmark universe: %s rate-limited (429) — retry %d/%d in %.0fs",
                context, attempt + 1, len(backoff), delay,
            )
            await sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover — the loop returns or raises


async def _list_industries(fmp: Any, *, sleep: Sleep = asyncio.sleep) -> List[str]:
    rows = await _request(fmp, "available-industries", None, sleep=sleep,
                          context="available-industries")
    if not isinstance(rows, list):
        raise UnusableAnswerError(
            f"available-industries answered {type(rows).__name__}, expected a list"
        )
    out: List[str] = []
    for row in rows:
        name = (row.get("industry") if isinstance(row, dict) else "") or ""
        name = name.strip() if isinstance(name, str) else ""
        if name:
            out.append(name)
    if not out:
        # FMP classifies every ticker into ~159 industries; none at all is FMP failing.
        raise UnusableAnswerError(
            f"available-industries returned no industry names ({len(rows)} rows)"
        )
    return sorted(set(out))


def _row_symbol(row: Any) -> Optional[str]:
    if not isinstance(row, dict):
        return None
    sym = row.get("symbol")
    if not isinstance(sym, str) or not sym.strip():
        return None
    return sym.strip().upper()


async def _screener_for_industry(
    fmp: Any,
    industry: str,
    floor: int,
    *,
    page_limit: int = _SCREENER_PAGE_LIMIT,
    max_pages: int = _SCREENER_MAX_PAGES,
    sleep: Sleep = asyncio.sleep,
) -> List[Any]:
    """Every row the screener holds for one industry, paged until a short page.

    Raises (never returns a partial list): `TruncatedIndustryError` when the industry is
    still a full page after `max_pages`, `UnusableAnswerError` for a non-list answer or a
    full page that adds no new symbol (FMP ignoring `page` would otherwise loop to the cap
    and look complete), and whatever the request raised.
    """
    rows: List[Any] = []
    seen: set = set()
    for page in range(max_pages):
        params: Dict[str, Any] = {
            "industry": industry,
            "exchange": ",".join(_US_EXCHANGES),
            "isEtf": "false",
            "isFund": "false",
            "isActivelyTrading": "true",
            "marketCapMoreThan": str(floor),
            "limit": str(page_limit),
        }
        if page:
            params["page"] = str(page)
        batch = await _request(fmp, "company-screener", params, sleep=sleep,
                               context=f"industry={industry!r} page {page}")
        if not isinstance(batch, list):
            raise UnusableAnswerError(
                f"industry={industry!r} page {page}: company-screener answered "
                f"{type(batch).__name__}, expected a list"
            )
        new_symbols = {s for s in (_row_symbol(r) for r in batch) if s} - seen
        full = len(batch) >= page_limit
        if page and full and not new_symbols:
            raise UnusableAnswerError(
                f"industry={industry!r}: page {page} is a full {len(batch)}-row page of "
                f"symbols already seen — the screener is not honouring `page`, so this "
                f"industry cannot be read completely"
            )
        seen |= new_symbols
        rows.extend(batch)
        if not full:
            if page:
                logger.warning(
                    "benchmark universe: industry=%r needed %d screener pages (%d rows) — "
                    "unusual for a US-only universe; a page boundary can shift between "
                    "calls, so check its count", industry, page + 1, len(rows),
                )
            return rows
    raise TruncatedIndustryError(
        f"industry={industry!r}: still a full {page_limit}-row page after {max_pages} "
        f"pages ({len(rows)} rows) — it would be truncated; raise _SCREENER_MAX_PAGES"
    )


def _flag_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _flag_false(value: Any) -> bool:
    return value is False or (isinstance(value, str) and value.strip().lower() == "false")


def _row_exchange(row: Dict[str, Any]) -> str:
    for key in ("exchangeShortName", "exchange"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return ""


def _is_us_exchange(exchange: str) -> bool:
    if not exchange:
        # No exchange on the row: the server-side `exchange` filter is the gate, and the
        # symbol checks still catch a foreign listing.
        return True
    return any(exchange == p or exchange.startswith(p + " ") for p in _US_EXCHANGE_PREFIXES)


def _positive_finite(value: Any) -> Optional[float]:
    """A real, finite, positive number, else None (a bool, a string, NaN and inf are not)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    if not math.isfinite(out) or out <= 0:
        return None
    return out


def _market_cap(row: Dict[str, Any]) -> Optional[float]:
    return _positive_finite(row.get("marketCap"))


def _row_name(row: Dict[str, Any]) -> str:
    """The row's `companyName`, length-capped BEFORE any regex sees it and folded onto one
    line, or "" when FMP sent none (or not a string)."""
    name = row.get("companyName")
    if not isinstance(name, str):
        return ""
    return " ".join(name[:_MAX_NAME_CHARS].split())


def _as_search_row(
    row: Dict[str, Any], sym: str, name: Optional[str] = None,
) -> StockSearchResult:
    """The row in the shape the ticker-search grammar reads (`name` overrides the row's own
    name). Every screener row here is a non-ETF, non-fund listing by FMP's flags, i.e. the
    search's type "stock"."""
    return StockSearchResult(symbol=sym, name=_row_name(row) if name is None else name,
                             exchange_short_name=_row_exchange(row) or None, type="stock")


def _without_adr_phrase(name: str) -> Tuple[str, bool]:
    """(the name with every ADR / GDS phrase cut out, whether one was there)."""
    cut, n = _ADR_PHRASE_RE.subn(" ", name)
    return (" ".join(cut.split()), True) if n else (name, False)


def _listing_reason(row: Dict[str, Any], sym: str) -> Optional[str]:
    """`fund_name` or `not_common_share` from the row's OWN symbol and name, or None.

    The rules that need the other rows (a twin of a base listing, a second share class of
    one issuer) run in `_filter_market`.
    """
    name = _row_name(row)
    if _FUND_NAME_RE.search(name) or _ETF_NAME_RE.search(name):
        return "fund_name"
    # ABC-U / ABC-R / ABC-W: unit, right, warrant. Class letters (BRK-B, MKC-V) are not
    # action suffixes, and the two-letter forms already failed the symbol shape.
    if _DASH_ACTION_SUFFIX_RE.search(sym):
        return "not_common_share"
    # The debt / preferred rules read the name WITHOUT its ADR / GDS phrase (an ADR is the
    # foreign issuer's common); what is left must not name a preferred either. A "Series B"
    # there is a Latin American issuer's ordinary class, not a preferred (B4-1).
    debt_name, is_adr = _without_adr_phrase(name)
    if is_adr and _ADR_NON_COMMON_RE.search(debt_name):
        return "not_common_share"
    # Dash and NASDAQ 5th-letter (P/O/N/M) preferreds, and the gated debt-name rule.
    verdict = _grammar_drop_reason(_as_search_row(row, sym, debt_name), sym)
    if verdict not in (None, *_SEARCH_GRAMMAR_KEPT):
        return "not_common_share"
    # The same debt / preferred NAME rule WITHOUT the search's issuer gate. The search needs
    # the gate so stock-typed ETF products ("Corgi U.S. Equities 30% Structu") stay
    # findable; this universe wants none of those, and the gate let a trust's notes through —
    # "PennyMac Mortgage Investment Trust 8.50% Senior Notes due 2028" (PMTU) names no
    # Inc/Corp. Plus this builder's own debt words.
    if _DEBT_PREF_NAME_RE.search(debt_name) or _BUILDER_DEBT_NAME_RE.search(debt_name):
        return "not_common_share"
    if _NASDAQ_ACTION_SYMBOL_RE.match(sym) and _ACTION_NAME_RE.search(name):
        return "not_common_share"
    return None


def _drop_reason(row: Any, floor: int) -> Optional[str]:
    """Why a screener row is not a US-listed operating company above the floor, or None.

    Judged on the row ALONE; `_filter_rows` adds the twin and one-vote-per-issuer rules.
    """
    sym = _row_symbol(row)
    if sym is None:
        return "malformed"
    if _flag_true(row.get("isEtf")):
        return "etf"
    if _flag_true(row.get("isFund")):
        return "fund"
    if _flag_false(row.get("isActivelyTrading")):
        return "inactive"
    if "." in sym:
        return "foreign_suffix"
    if not _is_us_exchange(_row_exchange(row)):
        return "non_us_exchange"
    if not _COMMON_SHARE_SYMBOL_RE.match(sym):
        return "not_common_share"
    listing = _listing_reason(row, sym)
    if listing is not None:
        return listing
    cap = _market_cap(row)
    if cap is None:
        return "bad_market_cap"
    if cap < floor:
        return "below_floor"
    return None


def _drop_label(row: Any, reason: str) -> str:
    label = _row_symbol(row) or repr(row)[:40]
    if reason == "non_us_exchange":
        return f"{label} [{_row_exchange(row)}]"
    if reason in ("not_common_share", "fund_name") and isinstance(row, dict):
        name = _row_name(row)
        if name:
            return f'{label} "{name[:60]}"'
    return label


def _listing_twins(
    kept: Dict[str, Dict[str, Any]], bases: Dict[str, Dict[str, Any]],
) -> Dict[str, str]:
    """{symbol: its base} for each kept row that is another listing of a live common — the
    search's two twin rules, run with `kept` + `bases` as the directory (the whole market in
    `_filter_market`, so a note filed under another industry than its issuer is still
    paired):

      * corporate-action twins: a …W/U/R/V or dash-action symbol whose base (the symbol minus
        the suffix) carries the same name — CCXIW / CCXIU beside CCXI, NOVTU beside NOVT;
      * root twins: the issuer's ticker plus letters under the issuer's own name — SOMN and
        SOJE beside SO, PMTU beside PMT, HONIV beside HON. A single class letter A/B/C/J/K
        (FOXA, LILAK) and GOOGL are exempt, exactly as in the search.

    A base is any live US common listing the screener returned, whatever its cap
    (`_TWIN_BASE_REASONS`). Distinct companies stay apart because both rules also need the
    two names to be one issuer's.
    """
    page = {sym: _as_search_row(r, sym) for sym, r in {**bases, **kept}.items()}
    directory = {sym: sr.name for sym, sr in page.items()}
    twins: Dict[str, str] = {}
    survivors = {sr.symbol for sr in _dedupe_secondary_listings(list(page.values()))}
    for sym in kept:
        if sym not in survivors:
            twins[sym] = _secondary_base_symbol(sym) or "?"
    for sym in kept:
        if sym in twins:
            continue
        sr = page[sym]
        if not _is_root_twin_candidate(sr, sym, ""):
            continue
        key = _issuer_key(sr.name)
        if key and _is_root_twin(sr, sym, key, page, directory):
            twins[sym] = next(
                (sym[:i] for i in range(1, len(sym))
                 if sym[:i] in directory and _issuer_key(directory[sym[:i]]) == key),
                "?",
            )
    return twins


def _share_class_siblings(a: str, b: str) -> bool:
    """Do two symbols read as classes of ONE listing? BRK-A/BRK-B and PBR/PBR-A (one dash
    root), GOOG/GOOGL, FOX/FOXA and Z/ZG (one letter added), BATRA/BATRK and FWONA/FWONK
    (only the last letter differs).

    The issuer name alone is not proof: First Bancorp (FBNC) and First BanCorp. (FBP) are
    two banks, both in Banks - Regional, with one normalised name.
    """
    ra, rb = a.split("-", 1)[0], b.split("-", 1)[0]
    if ra == rb:
        return True
    short, long_ = sorted((ra, rb), key=len)
    if len(long_) - len(short) == 1 and long_.startswith(short):
        return True
    return len(ra) == len(rb) >= 3 and ra[:-1] == rb[:-1]


def _liquidity(row: Dict[str, Any]) -> float:
    """Dollar volume (price × average, else last, volume); 0.0 when FMP sent no usable pair."""
    price = _positive_finite(row.get("price"))
    volume = _positive_finite(row.get("avgVolume")) or _positive_finite(row.get("volume"))
    if price is None or volume is None:
        return 0.0
    out = price * volume
    return out if math.isfinite(out) else 0.0


def _vote_order(row: Dict[str, Any]) -> Tuple[float, float, int, str]:
    """Sort key, best first: most liquid, then largest cap, then the shorter (then the
    alphabetically first) symbol — deterministic whatever order FMP listed the classes in.
    A thinly traded listing that slipped past every rule loses to the issuer's common even
    when FMP reports it a bigger cap (SOJC carried $20.7B)."""
    sym = row["symbol"]
    return (-_liquidity(row), -(_market_cap(row) or 0.0), len(sym), sym)


def _one_vote_per_issuer(
    rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Tuple[Dict[str, Any], Dict[str, Any]]]]:
    """(one row per issuer, [(dropped row, the row kept in its place)]).

    Each class of a dual-class issuer comes back with the issuer's fundamentals, so GOOG +
    GOOGL or BRK-A + BRK-B voted twice in the industry median — and four such issuers in a
    32-name industry (Entertainment: FOX/FOXA, NWS/NWSA, BATRA/BATRK, FWONA/FWONK) moved it.
    Grouped by the search's normalised issuer name AND symbols that read as share classes
    (`_share_class_siblings`); a name too short to prove one issuer ("3M Company") is never
    grouped.
    """
    out: List[Dict[str, Any]] = []
    by_issuer: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = _issuer_key(_row_name(row))
        if key:
            by_issuer.setdefault(key, []).append(row)
        else:
            out.append(row)
    duplicates: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for members in by_issuer.values():
        groups: List[List[Dict[str, Any]]] = []
        for row in members:
            joined = [g for g in groups
                      if any(_share_class_siblings(row["symbol"], m["symbol"]) for m in g)]
            merged = [row] + [m for g in joined for m in g]
            groups = [g for g in groups if not any(g is j for j in joined)] + [merged]
        for group in groups:
            ranked = sorted(group, key=_vote_order)
            out.append(ranked[0])
            duplicates.extend((m, ranked[0]) for m in ranked[1:])
    return out, duplicates


class _MarketFilter(NamedTuple):
    kept: Dict[str, List[Dict[str, Any]]]       # industry → one row per symbol and issuer
    dropped: Counter                             # reason → count, all industries
    examples: Dict[str, List[str]]               # reason → a few labels
    drops_by_industry: Dict[str, int]
    cross_industry_twins: List[str]              # twins of a common in ANOTHER industry


def _filter_market(rows_by_industry: Dict[str, List[Any]], floor: int) -> _MarketFilter:
    """Filter every industry's screener answer against ONE market-wide directory.

    Per row first (`_drop_reason`); then the twin rules over the common rows of EVERY
    industry (FMP files CGABL under Financial - Credit Services and CG under Asset
    Management, HONAV under Aerospace & Defense and HON under Conglomerates — an
    industry-local directory never saw the base); then one vote per issuer inside each
    industry. Industries are walked in sorted order, so the result does not depend on the
    order the requests completed in.
    """
    kept_by: Dict[str, Dict[str, Dict[str, Any]]] = {}
    market_kept: Dict[str, Dict[str, Any]] = {}
    market_bases: Dict[str, Dict[str, Any]] = {}
    home: Dict[str, str] = {}              # symbol → the industry its directory row is from
    dropped: Counter = Counter()
    examples: Dict[str, List[str]] = {}
    drops_by_industry: Counter = Counter()
    cross: List[str] = []

    def _note(industry: str, reason: str, label: str) -> None:
        dropped[reason] += 1
        drops_by_industry[industry] += 1
        sample = examples.setdefault(reason, [])
        if len(sample) < _DROP_EXAMPLES:
            sample.append(label)

    for industry in sorted(rows_by_industry):
        kept = kept_by.setdefault(industry, {})
        for row in rows_by_industry[industry]:
            reason = _drop_reason(row, floor)
            if reason is None:
                sym = _row_symbol(row)
                if sym not in kept:          # a row a page boundary repeated: one vote
                    kept[sym] = {**row, "symbol": sym}
                    if sym not in market_kept:
                        market_kept[sym] = kept[sym]
                        home[sym] = industry
                continue
            if reason in _TWIN_BASE_REASONS:
                sym = _row_symbol(row)
                if sym not in market_bases:
                    market_bases[sym] = {**row, "symbol": sym}
                    home.setdefault(sym, industry)
            _note(industry, reason, _drop_label(row, reason))

    for sym, base in sorted(_listing_twins(market_kept, market_bases).items()):
        base_industry = home.get(base)
        for industry in sorted(kept_by):
            row = kept_by[industry].pop(sym, None)
            if row is None:
                continue
            if base_industry is not None and base_industry != industry:
                label = f"{sym} (twin of {base} in {base_industry})"
                cross.append(f'{sym} "{_row_name(row)[:60]}" [{industry}] → '
                             f'{base} [{base_industry}]')
            else:
                label = f"{sym} (twin of {base})"
            _note(industry, "not_common_share", label)

    out: Dict[str, List[Dict[str, Any]]] = {}
    for industry in sorted(kept_by):
        survivors, duplicates = _one_vote_per_issuer(list(kept_by[industry].values()))
        for row, winner in duplicates:
            _note(industry, "same_issuer", f"{row['symbol']} (kept {winner['symbol']})")
        out[industry] = survivors
    return _MarketFilter(out, dropped, examples, dict(drops_by_industry), cross)


def _filter_rows(
    rows: List[Any], floor: int,
) -> Tuple[List[Dict[str, Any]], Counter, Dict[str, List[str]]]:
    """(kept rows — one per symbol AND one per issuer; drop counts by reason; a few examples
    per reason) for ONE industry treated as the whole market — `_filter_market` with a
    single industry."""
    result = _filter_market({"": rows}, floor)
    return result.kept[""], result.dropped, result.examples


def _aggregate(by_industry: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Resolve (industry → screener rows) → {industry, sector(modal), tickers, market_caps}."""
    out: List[Dict[str, Any]] = []
    for industry, rows in by_industry.items():
        if not rows:
            continue
        sector_counts: Dict[str, int] = {}
        caps: Dict[str, float] = {}
        for r in rows:
            sym = _row_symbol(r)
            sec = r.get("sector")
            sec = sec.strip() if isinstance(sec, str) else ""
            cap = _market_cap(r)
            if sym and cap is not None:
                caps[sym] = cap
                if sec:
                    sector_counts[sec] = sector_counts.get(sec, 0) + 1
        if not caps:
            continue
        sector = (
            max(sector_counts.items(), key=lambda kv: kv[1])[0]
            if sector_counts else "Unknown"
        )
        out.append({
            "industry": industry,
            "sector": sector,
            "tickers": sorted(caps.keys()),
            "market_caps": caps,
        })
    out.sort(key=lambda d: (d["sector"], d["industry"]))
    return out


def _ticker_count(industries: List[Any]) -> int:
    return sum(
        len(e["tickers"]) for e in industries
        if isinstance(e, dict) and isinstance(e.get("tickers"), list)
    )


def _load_previous(path: Path) -> Optional[List[Any]]:
    """The `industries` list of the file this build replaces, or None (no baseline)."""
    if not path.exists():
        logger.warning(
            "benchmark universe: no previous file at %s — the shrink guard has no baseline. "
            "The live copy is in the 'universe-data' bucket; download it there first to "
            "compare.", path,
        )
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        industries = payload.get("industries") if isinstance(payload, dict) else None
        if not isinstance(industries, list):
            raise ValueError(f"'industries' is {type(industries).__name__}, expected list")
    except Exception as exc:
        logger.warning(
            "benchmark universe: previous file %s is unreadable (%s) — the shrink guard "
            "has no baseline", path, _describe(exc),
        )
        return None
    return industries


ShrinkOverride = Union[None, bool, int, float]


def _shrink_limit(allow_shrink: ShrinkOverride) -> Optional[float]:
    """The largest drop `--allow-shrink` permits, in percent, or None (no override).

    `True` is the bare flag (`_DEFAULT_ALLOW_SHRINK_PERCENT`); a number must be finite and in
    (0, 100]. Anything else is a caller bug and raises ValueError — never read as "any".
    """
    if allow_shrink is None or allow_shrink is False:
        return None
    if allow_shrink is True:
        return float(_DEFAULT_ALLOW_SHRINK_PERCENT)
    if (isinstance(allow_shrink, (int, float)) and math.isfinite(allow_shrink)
            and 0 < allow_shrink <= 100):
        return float(allow_shrink)
    raise ValueError(f"allow_shrink must be a percentage in (0, 100], got {allow_shrink!r}")


def _shrink_allowed(previous: int, new: int, allow_shrink: ShrinkOverride) -> bool:
    """False when `new` drops MORE than `_MAX_SHRINK_PERCENT` below `previous`, or — with
    `--allow-shrink PCT` — more than PCT (a PCT under the default bar changes nothing)."""
    if previous <= 0 or (previous - new) * 100 <= previous * _MAX_SHRINK_PERCENT:
        return True
    drop = 100.0 * (previous - new) / previous
    limit = _shrink_limit(allow_shrink)
    if limit is not None and (previous - new) * 100 <= previous * limit:
        logger.warning(
            "benchmark universe: ticker count drops %.1f%% (%d → %d) — allowed by "
            "--allow-shrink %g", drop, previous, new, limit,
        )
        return True
    if limit is not None:
        logger.error(
            "benchmark universe: REFUSED — ticker count drops %.1f%% (%d → %d), more than "
            "the %g%% --allow-shrink permits. Nothing written. Find out why before raising "
            "it: a drop this size is also what a screener answering real industries with "
            "empty pages looks like.", drop, previous, new, limit,
        )
        return False
    logger.error(
        "benchmark universe: REFUSED — ticker count drops %.1f%% (%d → %d), more than "
        "%d%%. Nothing written. If the drop is expected (e.g. the first US-only build), "
        "re-run with --allow-shrink PCT (bare: %d%%).", drop, previous, new,
        _MAX_SHRINK_PERCENT, _DEFAULT_ALLOW_SHRINK_PERCENT,
    )
    return False


def _fund_like_symbol(sym: str) -> bool:
    """NASDAQ's fifth-letter-X mutual-fund shape (`____X`)."""
    return len(sym) == 5 and sym.endswith("X")


def _operating_ticker_count(tickers: Any) -> int:
    """Tickers that read as a US operating listing: a string, no dotted foreign suffix, not
    a 5-letter X fund symbol. The previous file carries no names or flags, so an ETF with
    an ordinary symbol still counts."""
    if not isinstance(tickers, list):
        return 0
    return sum(
        1 for t in tickers
        if isinstance(t, str) and t.strip() and "." not in t
        and not _fund_like_symbol(t.strip().upper())
    )


def _missing_industries_allowed(
    previous: List[Any], new: List[Dict[str, Any]], allow_missing: Iterable[str] = (),
) -> bool:
    """False when an industry that held at least `_MISSING_INDUSTRY_MIN_TICKERS` operating
    tickers in the previous file has none now and is not named in `allow_missing`.

    The screener answering a real industry with `[]` is not a failed request — it reads as
    an industry with no constituents — and on a run that also needs `--allow-shrink` its
    single 'no constituents now' line is lost among the industries that really left.
    """
    allowed = {a.strip().casefold() for a in allow_missing
               if isinstance(a, str) and a.strip()}
    now = {e["industry"] for e in new}
    gone: Dict[str, int] = {}
    for e in previous:
        if not isinstance(e, dict):
            continue
        industry = e.get("industry")
        if not isinstance(industry, str) or not industry.strip() or industry in now:
            continue
        gone[industry] = max(gone.get(industry, 0), _operating_ticker_count(e.get("tickers")))

    named = {i for i in gone if i.strip().casefold() in allowed}
    blocking = sorted((i, n) for i, n in gone.items()
                      if n >= _MISSING_INDUSTRY_MIN_TICKERS and i not in named)
    for industry in sorted(named):
        logger.warning(
            "benchmark universe: industry=%r (%d operating tickers before) has none now — "
            "allowed by --allow-missing", industry, gone[industry],
        )
    unused = sorted(allowed - {i.strip().casefold() for i in gone})
    if unused:
        logger.warning(
            "benchmark universe: --allow-missing named %d industr%s that did not go missing "
            "(a typo, or it still has constituents): %s",
            len(unused), "y" if len(unused) == 1 else "ies", ", ".join(unused),
        )
    if not blocking:
        return True
    logger.error(
        "benchmark universe: REFUSED — %d industr%s with at least %d operating tickers in the "
        "previous file %s none now: %s. Nothing written. An empty screener answer for a real "
        "industry looks exactly like this. If each one really left, re-run with: %s",
        len(blocking), "y" if len(blocking) == 1 else "ies", _MISSING_INDUSTRY_MIN_TICKERS,
        "has" if len(blocking) == 1 else "have",
        ", ".join(f"{i} ({n})" for i, n in blocking),
        " ".join(f'--allow-missing "{i}"' for i, _ in blocking),
    )
    return False


def _log_comparison(previous: List[Any], new: List[Dict[str, Any]]) -> None:
    def by_sector(industries: List[Any]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for e in industries:
            if isinstance(e, dict) and isinstance(e.get("tickers"), list):
                sector = str(e.get("sector") or "Unknown")
                out[sector] = out.get(sector, 0) + len(e["tickers"])
        return out

    before, after = by_sector(previous), by_sector(new)
    for sector in sorted(set(before) | set(after)):
        logger.info("  %-25s %5d → %5d tickers", sector, before.get(sector, 0),
                    after.get(sector, 0))
    gone = sorted(
        {e["industry"] for e in previous
         if isinstance(e, dict) and isinstance(e.get("industry"), str) and e["industry"]}
        - {e["industry"] for e in new}
    )
    if gone:
        logger.warning(
            "benchmark universe: %d industries in the previous file have no constituents "
            "now: %s", len(gone), ", ".join(map(str, gone)),
        )


def _log_drops(dropped: Counter, examples: Dict[str, List[str]], rows_seen: int) -> None:
    for reason, n in sorted(dropped.items()):
        unexpected = reason in _UNEXPECTED_DROP_REASONS or (
            reason == "below_floor" and n * 100 > rows_seen * _BELOW_FLOOR_ALARM_PERCENT
        )
        note = ("a server-side screener filter was not honoured" if unexpected
                else _EXPECTED_DROP_NOTES.get(reason, "expected"))
        logger.log(
            logging.WARNING if unexpected else logging.INFO,
            "benchmark universe: dropped %d %s row(s) of %d, e.g. %s — %s",
            n, reason, rows_seen, ", ".join(examples.get(reason, [])), note,
        )


def _log_fund_like_symbols(industries: List[Dict[str, Any]]) -> None:
    """A 5-letter symbol ending in X is a mutual fund by NASDAQ's fifth-letter convention.

    Kept (FMP's `isFund` is the authority), but named, so a fund FMP failed to flag is
    seen before the file is uploaded.
    """
    suspects = sorted({t for e in industries for t in e["tickers"] if _fund_like_symbol(t)})
    if suspects:
        logger.warning(
            "benchmark universe: %d kept symbol(s) follow NASDAQ's fifth-letter-X mutual-fund "
            "convention but FMP did not flag them isFund — check before uploading: %s",
            len(suspects), ", ".join(suspects[:20]),
        )


def _named(entries: List[str]) -> str:
    """The first `_SUSPECTS_NAMED` entries, and how many more there are."""
    more = len(entries) - _SUSPECTS_NAMED
    return "; ".join(entries[:_SUSPECTS_NAMED]) + (f"; (+{more} more)" if more > 0 else "")


def _daily_turnover(row: Dict[str, Any]) -> Optional[float]:
    """price × avgVolume ÷ marketCap — the share of the reported cap traded a day — or None
    when any of the three is missing, non-numeric, non-finite or not positive."""
    price = _positive_finite(row.get("price"))
    avg_volume = _positive_finite(row.get("avgVolume"))
    cap = _market_cap(row)
    if price is None or avg_volume is None or cap is None:
        return None
    out = price * avg_volume / cap
    return out if math.isfinite(out) else None


def _log_cross_industry_twins(entries: List[str]) -> None:
    """Rows dropped as the twin of a common in ANOTHER industry, every one named: the
    market-wide directory is what lets a distinct company be mistaken for a note, so the
    owner checks the list before uploading."""
    if entries:
        logger.warning(
            "benchmark universe: %d row(s) dropped as twins of a common listed in ANOTHER "
            "industry (the issuer's ticker plus letters, under the issuer's own name) — "
            "check before uploading: %s", len(entries), _named(entries),
        )


def _log_suspect_rows(by_industry: Dict[str, List[Dict[str, Any]]]) -> None:
    """Kept rows no rule can settle on its own — named at WARNING, never dropped, so the
    owner reads them before uploading:

      * a NASDAQ fifth-letter W/U/R/V symbol (warrant / unit / right / when-issued) with no
        same-issuer base row and a name that does not say what it is;
      * a trust whose name reads like a closed-end fund ("BlackRock Taxable Municipal Bond
        Trust"). "Trust" alone also names operating REITs and royalty trusts, and this list
        will name some of them too (Universal Health Realty Income Trust) — which is why it
        is a list to read, not a drop;
      * a row trading under `_THIN_DAILY_TURNOVER` of its reported cap a day — a note FMP
        prices with the issuer's share count whose (often truncated) name says nothing, or
        a thinly traded ADR. Most suspicious first.
    """
    actions: List[str] = []
    trusts: List[str] = []
    thin: List[Tuple[float, str]] = []
    for industry, rows in sorted(by_industry.items()):
        for row in rows:
            sym, name = row["symbol"], _row_name(row)
            entry = f'{sym} "{name[:60]}" [{industry}]'
            if _NASDAQ_ACTION_SYMBOL_RE.match(sym):
                actions.append(entry)
            if _TRUST_RE.search(name) and _FUND_STYLE_WORD_RE.search(name):
                trusts.append(entry)
            turnover = _daily_turnover(row)
            if turnover is not None and turnover < _THIN_DAILY_TURNOVER:
                thin.append((turnover, f"{entry} {turnover * 100:.4f}%/day"))

    if thin:
        thin.sort()
        logger.warning(
            "benchmark universe: %d kept row(s) trade under %.2f%% of their reported market "
            "cap a day (price × avgVolume) — the mark of a note or preferred FMP prices with "
            "the issuer's share count, or of a thinly traded ADR — kept; check before "
            "uploading: %s", len(thin), _THIN_DAILY_TURNOVER * 100, _named([e for _, e in thin]),
        )
    if actions:
        logger.warning(
            "benchmark universe: %d kept symbol(s) carry NASDAQ's fifth-letter warrant / unit "
            "/ right / when-issued code (W/U/R/V) with no same-issuer base row and a plain "
            "name — kept; check before uploading: %s", len(actions), _named(actions),
        )
    if trusts:
        logger.warning(
            "benchmark universe: %d kept row(s) are trusts whose name reads like a closed-end "
            "fund (FMP flags only open-end funds isFund) — kept; check before uploading: %s",
            len(trusts), _named(trusts),
        )


def _write_atomically(path: Path, payload: Dict[str, Any]) -> None:
    """Via a `.part` file and a rename, so a crash never leaves a half-written universe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


async def main(
    floor: int = _DEFAULT_FLOOR,
    *,
    output: Path = _OUTPUT_PATH,
    allow_shrink: ShrinkOverride = None,
    allow_missing: Optional[Iterable[str]] = (),
    fmp: Any = None,
    sleep: Sleep = asyncio.sleep,
    page_limit: int = _SCREENER_PAGE_LIMIT,
    max_pages: int = _SCREENER_MAX_PAGES,
) -> int:
    """Build, check and write the universe. Returns the process exit code.

    `allow_shrink`: the largest drop to permit, in percent (`True` = the bare flag, 50);
    `allow_missing`: industries that may leave (see `_missing_industries_allowed`).
    """
    _shrink_limit(allow_shrink)          # a bad override fails here, before any FMP call
    # A bare string is one industry, never its characters; None is none.
    if allow_missing is None:
        allow_missing = []
    allow_missing = [allow_missing] if isinstance(allow_missing, str) else list(allow_missing)
    owns_client = fmp is None
    client = FMPClient() if owns_client else fmp
    try:
        try:
            industries = await _list_industries(client, sleep=sleep)
        except Exception as exc:
            logger.error(
                "benchmark universe: could not list FMP industries (%s) — nothing written",
                _describe(exc),
                exc_info=not isinstance(exc, _EXPECTED_FAILURES),
            )
            return EXIT_BUILD_FAILED
        logger.info("FMP available-industries: %d entries (floor=$%.0fM)",
                    len(industries), floor / 1e6)

        sem = asyncio.Semaphore(_CONCURRENCY)
        raw_by_industry: Dict[str, List[Any]] = {}
        failures: Dict[str, str] = {}
        rows_seen = 0

        async def _one(name: str) -> None:
            nonlocal rows_seen
            async with sem:
                try:
                    rows = await _screener_for_industry(
                        client, name, floor,
                        page_limit=page_limit, max_pages=max_pages, sleep=sleep,
                    )
                except Exception as exc:
                    # Scrubbed BEFORE it is stored: the summary below joins these strings.
                    failures[name] = _describe(exc)
                    logger.warning(
                        "benchmark universe: industry=%r FAILED (%s)", name, failures[name],
                        exc_info=not isinstance(exc, _EXPECTED_FAILURES),
                    )
                    return
            rows_seen += len(rows)
            raw_by_industry[name] = rows

        await asyncio.gather(*[_one(i) for i in industries])

        if failures:
            logger.error(
                "benchmark universe: %d of %d industries FAILED — nothing written (a universe "
                "missing an industry empties its peer medians until the next build): %s",
                len(failures), len(industries),
                "; ".join(f"{k} ({v})" for k, v in sorted(failures.items())),
            )
            return EXIT_BUILD_FAILED

        # Filtered only now that every industry is in: the twin rules read the whole market.
        filtered = _filter_market(raw_by_industry, floor)
        by_industry = filtered.kept
        for name, kept in by_industry.items():
            logger.info("  %-45s %d tickers (%d dropped)", name, len(kept),
                        filtered.drops_by_industry.get(name, 0))
        _log_drops(filtered.dropped, filtered.examples, rows_seen)
        _log_cross_industry_twins(filtered.cross_industry_twins)
        _log_suspect_rows(by_industry)
        aggregated = _aggregate(by_industry)
        total = _ticker_count(aggregated)
        logger.info("Industries with constituents: %d  Total tickers: %d", len(aggregated), total)
        if not aggregated:
            # Never written, even with --allow-shrink: an empty universe blanks every
            # industry and sector median.
            logger.error(
                "benchmark universe: no industry has a usable constituent (%d industries "
                "asked) — nothing written", len(industries),
            )
            return EXIT_BUILD_FAILED
        _log_fund_like_symbols(aggregated)

        previous = _load_previous(output)
        if previous is not None:
            _log_comparison(previous, aggregated)
            # Both checks run (and log) before either refuses, so one run names every problem.
            shrink_ok = _shrink_allowed(_ticker_count(previous), total, allow_shrink)
            missing_ok = _missing_industries_allowed(previous, aggregated, allow_missing)
            if not (shrink_ok and missing_ok):
                return EXIT_SHRINK_REFUSED
        elif allow_missing:
            logger.warning(
                "benchmark universe: --allow-missing given but there is no previous file to "
                "compare with — it changes nothing",
            )

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": (
                "fmp /stable/available-industries + /stable/company-screener "
                f"(marketCapMoreThan, exchange={','.join(_US_EXCHANGES)}, isEtf=false, "
                "isFund=false, isActivelyTrading=true)"
            ),
            "market_cap_floor": floor,
            "industry_count": len(aggregated),
            "ticker_count": total,
            "industries": aggregated,
        }
        _write_atomically(output, payload)
        logger.info("Wrote %s — upload it to the 'universe-data' bucket", output)

        per_sector: Dict[str, int] = {}
        for e in aggregated:
            per_sector[e["sector"]] = per_sector.get(e["sector"], 0) + 1
        print("\nIndustries per sector:")
        for sector, n in sorted(per_sector.items()):
            print(f"  {sector:<25} {n}")
        return EXIT_OK
    finally:
        if owns_client:
            await client.close()


def _shrink_percent_arg(text: str) -> int:
    """`--allow-shrink PCT`: a whole percentage, 1-100."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole percentage, got {text!r}")
    if not 1 <= value <= 100:
        raise argparse.ArgumentTypeError(f"expected 1-100, got {value}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the broad benchmark universe")
    parser.add_argument("--floor", type=int, default=_DEFAULT_FLOOR,
                        help="Market-cap floor in USD (default 500M)")
    parser.add_argument("--allow-shrink", nargs="?", type=_shrink_percent_arg, default=None,
                        const=_DEFAULT_ALLOW_SHRINK_PERCENT, metavar="PCT",
                        help=f"Write even when the ticker count drops more than "
                             f"{_MAX_SHRINK_PERCENT}%% below the previous file — up to PCT%% "
                             f"(default {_DEFAULT_ALLOW_SHRINK_PERCENT} when given bare)")
    parser.add_argument("--allow-missing", action="append", default=[], metavar="INDUSTRY",
                        help=f"An industry that held at least {_MISSING_INDUSTRY_MIN_TICKERS} "
                             f"operating tickers before and may have none now (repeatable; "
                             f"quote names with spaces)")
    parser.add_argument("--output", type=Path, default=_OUTPUT_PATH,
                        help="Where to write (default backend/data/benchmark_universe.json); "
                             "the shrink guard compares with this file")
    return parser


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    # As app/main.py does: every record that reaches the console, from ANY module (the FMP
    # client's own lines included), is scrubbed of `apikey=` and the other secrets.
    for _handler in logging.getLogger().handlers:
        _handler.addFilter(SecretRedactingFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = _build_parser().parse_args()
    sys.exit(asyncio.run(main(args.floor, output=args.output, allow_shrink=args.allow_shrink,
                              allow_missing=args.allow_missing)))
