"""Company facts — who a listed company is, from the licensed company profile.

WHY THIS EXISTS (2026-10-08). Ask Cay AI answered "who is the CEO", "how many employees",
"where is it headquartered" and "when did it list" from model memory, because the only
profile it ever saw was the cached row for the stock on screen — read with a blocking
Supabase call, with no write-back on a miss, and nothing at all for any other ticker. This
module is the one accessor for those facts, for any ticker, with the repo's cache-aside
shape (CLAUDE.md invariant 4):

  1. process memory, `_MEM_TTL` (5 min), keyed by the symbol;
  2. `company_profile_cache` through the Overview's OWN read helper
     (`StockOverviewService.get_cached_company_profile`, 24 h), off the event loop;
  3. FMP `profile` (+ `key-executives`), one fetch per symbol at a time (`_inflight`), then a
     READ-MERGE write-back of ONE superset row, off the loop and best effort.

TWO MODES (2026-10-09). ``get_company_facts(t)`` is the FULL read the profile tool makes
(company name, exchange, trading currency, executives). ``get_company_facts(t,
need_executives=False)`` is the PROFILE-ONLY read (CEO, sector, industry, head count,
headquarters, IPO date, description — what the chat's STOCK profile line shows): it never
calls key-executives, and ANY usable cached row answers it — a ``facts`` row, a raw profile, or
the Overview's own formatted row — so opening a ticker and then asking Cay AI costs no upstream
call. The full read is answered by a ``facts`` row (block younger than `_FACTS_FRESH_SECONDS`)
or a raw profile; on the Overview's formatted row, which has no company name, exchange or
trading currency, it refreshes the profile (one call, together with the executives when they
are due) and, if that refresh fails, still answers from the row's own fields — fresh, not
stale. In an outage, an OLDER row of any of the three shapes is the stale fallback, dated by
its facts block's stamp or else by the row's real `cached_at`.

THE SHARED ROW. `company_profile_cache.profile_json` (JSONB) has two other writers: the
Overview writes a formatted dict (description / ceo / founded / employees / headquarters /
website / sector / industry / sector_performance / industry_rank + fund flags + country /
is_adr) and, since 2026-10-09, MERGES it in (below); `whale_service` still replaces the row
WHOLE with the raw FMP profile (`upsert(on_conflict="ticker")`). This writer never drops a key
either of them wrote: it re-reads the row at write time and merges, writing the Overview's own
keys in the Overview's own format
(`StockOverviewService._build_company_profile`, called — not copied) and adding two blocks of
its own:

  * ``facts`` — the price-free fields the Overview's dict lacks (company name, city, state,
    exchange, trading currency, the raw IPO date), versioned by ``v``;
  * ``key_executives`` — ``{fetched_at, rows}``, fresh for `_EXECUTIVES_FRESH_SECONDS`
    (7 days) by its own stamp, so executives have a durable tier with no migration.

What the merge refuses to carry onto a re-stamped row, because a fresh `cached_at` would
re-date it: the Overview's DAILY keys (`sector_performance`, `industry_rank`) when the base row
is past its 24 h; the raw profile's price fields (whale's raw row carries a `price`); and the
raw profile's IDENTITY fields that the Overview's keys or the ``facts`` block duplicate (head
count, IPO date, city / state, exchange, currency, ADR flag — `_RAW_IDENTITY_KEYS`). Kept, those
made the merged row read as a raw profile, so whale's week-old head count and name beat the
Overview's fresh ones under a "cached within the last 24 hours" note (2026-10-09 review).
Whale's ``companyName`` and ``image`` stay for its holdings, and both re-stamping writers carry
today's values of them. A FAILED read of the row skips the write (logged) — a blind write would
drop the very blocks the merge keeps. An executives-only update keeps the base row's own
`cached_at`, so the profile fields beside it are never made to look younger than they are.

The Overview's writer merges too (2026-10-09, `merge_profile_row` — the same rules, called
from `StockOverviewService._upsert_company_profile_db` with its own client), so a detail view
no longer drops ``facts`` and ``key_executives``. Each block keeps its OWN stamp across those
re-stamps, which is why a ``facts`` block older than `_FACTS_FRESH_SECONDS` stops counting as
fresh for the full read (a renamed company is re-read, never served from a year-old block).
Read-merge-write is not atomic: a write landing between another writer's read and write is
lost, and the next read simply re-fetches what it needs.

Every value is cleaned at the boundary: placeholders ("N/A", "--", "No description
available.", 0 employees) are DROPPED, never shown; NaN / inf / bool are never numbers. The
description is returned RAW (bounded length) — the caller fences it as untrusted vendor text.
`get_company_facts` never raises.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import math
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.utils.currency import currency_code

logger = logging.getLogger(__name__)

_TABLE = "company_profile_cache"
FACTS_KEY = "facts"
FACTS_VERSION = 1
EXECUTIVES_KEY = "key_executives"

_MEM_TTL = 300                      # 5 min, the hot tier
_NOT_FOUND_TTL = 300                # "FMP has no profile for this symbol"
_FAILURE_TTL = 30                   # herd guard during an upstream outage — never longer
_MEM_MAX = 512
_ROW_FRESH_SECONDS = 24 * 3600      # the Overview helper's own TTL, for the merge rules
_EXECUTIVES_FRESH_SECONDS = 7 * 24 * 3600
#: How long a ``facts`` block's OWN fields (name, city, state, exchange, currency, IPO date) count
#: as fresh for the full read. The row around it is re-stamped by every Overview write, so the
#: block needs its own clock or a company renamed a year ago would keep its old name.
_FACTS_FRESH_SECONDS = 30 * 24 * 3600
_DESCRIPTION_MAX = 5000
_EXECUTIVES_MAX = 15
_TEXT_MAX = 120
#: The one outage sentence a caller (and so the model) sees. Fixed on purpose: an exception's
#: class or message names the vendor ("FMPRateLimitException") — that stays in the log.
_UPSTREAM_ERROR = "company profile could not be loaded right now"

#: The Overview's per-session keys. Carried onto a merged row only while the base row is
#: within its own 24 h: re-stamping them would re-date one day's sector move as today's.
_DAILY_KEYS = ("sector_performance", "industry_rank")
#: Price fields of a RAW FMP profile (whale_service's row). Never re-dated by a re-stamp.
_RAW_PRICE_KEYS = (
    "price", "marketCap", "mktCap", "beta", "lastDividend", "lastDiv", "range", "change",
    "changes", "changePercentage", "changesPercentage", "volume", "averageVolume", "volAvg",
    "dcf", "dcfDiff",
)
#: The identity fields of a RAW FMP profile that the Overview's keys or this module's ``facts``
#: block duplicate (head count, IPO date, city / state, exchange, trading currency, ADR flag)
#: plus the raw-only ones nothing reads. Never carried by a re-stamp: kept, they would make the
#: merged row read as a raw profile (`_looks_raw_profile`, and the chat's own copy of that
#: test), whose OLD head count and name would beat the Overview's fresh ones under a fresh
#: ``cached_at`` (2026-10-09 review). ``companyName`` and ``image`` are NOT here — whale_service
#: reads them for its holdings' names and logos, and both re-stamping writers carry today's
#: values (`stock_overview_service.profile_display_fields`); ``country`` and the fund flags are
#: the Overview's own keys too.
_RAW_IDENTITY_KEYS = (
    "symbol", "fullTimeEmployees", "ipoDate", "city", "state", "zip", "address", "phone",
    "exchange", "exchangeShortName", "exchangeFullName", "currency", "isAdr", "cik", "isin",
    "cusip", "defaultImage", "isActivelyTrading",
)

#: The keys the Overview's formatted writer stores (`StockOverviewService.get_overview`).
_OVERVIEW_PROFILE_KEYS = ("description", "ceo", "founded", "employees", "headquarters",
                          "website", "sector", "industry")
#: An Overview row answers only when it carries at least one of these (its writer prints
#: "N/A" in every field when its own profile read failed — that row is a miss).
_OVERVIEW_CORE_FIELDS = ("ceo", "sector", "industry", "employees", "ipo_date")
#: The Overview's keys a raw FMP profile never has. A row carrying one of them next to raw keys
#: was merged by a re-stamping writer, so it is read as the Overview's row, never as raw.
_OVERVIEW_ONLY_KEYS = ("founded", "employees", "headquarters")

_PLACEHOLDERS = frozenset({
    "", "n/a", "na", "n.a.", "--", "-", "—", "–", "none", "null", "nil", "unknown",
    "no description available.", "no description available", "not available",
})
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_DOTTED_CLASS_RE = re.compile(r"^([A-Z]{1,6})\.([A-Z])$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: sym -> (stored_at, ttl, value, with_executives). A profile-only result never answers a full
#: read (it has no executives); a full result answers both.
_mem: Dict[str, Tuple[float, float, Dict[str, Any], bool]] = {}
#: sym -> the running FULL load (with executives), and sym -> the running PROFILE-ONLY load.
#: A profile-only caller may join a full load; a full caller never joins a profile-only one
#: (it would come back without executives).
_inflight: Dict[str, "asyncio.Task"] = {}
_profile_inflight: Dict[str, "asyncio.Task"] = {}
#: Strong references to best-effort write-backs still running (a bare task is only weakly
#: held by the loop). Tests await them.
_pending_writes: set = set()


# ── small pure helpers ────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_text(value: Any, cap: int = _TEXT_MAX) -> Optional[str]:
    """A real, bounded string — or None for a placeholder, a non-string or a blank."""
    if not isinstance(value, str):
        return None
    text = _CONTROL_RE.sub("", value).strip()
    if text.lower() in _PLACEHOLDERS:
        return None
    if len(text) > cap:
        text = text[: cap - 1].rstrip() + "…"
    return text or None


def _clean_count(value: Any) -> Optional[int]:
    """A positive head count, or None. Never a bool, NaN, inf, 0 or a negative."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)) or value <= 0 or value > 1e8:
            return None
        return int(value)
    if isinstance(value, str):
        digits = value.replace(",", "").strip()
        if digits.isdigit():
            n = int(digits)
            return n if 0 < n <= 100_000_000 else None
    return None


def _clean_date(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not _DATE_RE.match(value.strip()):
        return None
    day = value.strip()[:10]
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return None
    return day


def _clean_bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _clean_website(value: Any) -> Optional[str]:
    text = _clean_text(value, 200)
    if not text:
        return None
    for scheme in ("https://", "http://"):
        if text.lower().startswith(scheme):
            text = text[len(scheme):]
    text = text.rstrip("/")
    if not text or " " in text or "." not in text:
        return None
    return text


def _clean_currency(value: Any) -> Optional[str]:
    """The trading currency, by the ONE shared rule (`app.utils.currency.currency_code`) — this
    copy used to upper-case before checking ASCII, so "ßU" read as the code "SSU"."""
    return currency_code(value)


def _parse_stamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def normalize_symbol(ticker: Any) -> Optional[str]:
    """Upper-cased and stripped, or None. Kept as typed: "SHOP.TO" and "VOD.L" are real
    listings, so a dot is never rewritten here (see `_dash_class_candidate`)."""
    if not isinstance(ticker, str):
        return None
    sym = ticker.strip().upper()
    if not sym or len(sym) > 16 or any(c.isspace() for c in sym):
        return None
    return sym


def _same_symbol(returned: Any, asked: str) -> bool:
    """The profile answers for the symbol asked (absent = trusted; "BRK-B" ≡ "BRK.B")."""
    if returned is None:
        return True
    if not isinstance(returned, str):
        return False
    return returned.strip().upper().replace(".", "-") == asked.replace(".", "-")


def _dash_class_candidate(sym: str) -> Optional[str]:
    """"BRK.B" → "BRK-B": the profile's spelling of a US share class, tried only after the
    dotted form found nothing (a one-letter suffix is also London's ".L" and Tokyo's ".T")."""
    m = _DOTTED_CLASS_RE.match(sym)
    return f"{m.group(1)}-{m.group(2)}" if m else None


# ── projections ───────────────────────────────────────────────────────────────

def _fields_from_raw(raw: Dict[str, Any]) -> Dict[str, Any]:
    """The facts a raw FMP profile carries, cleaned. Price-free by construction."""
    return {
        "name": _clean_text(raw.get("companyName"), 160),
        "ceo": _clean_text(raw.get("ceo")),
        "sector": _clean_text(raw.get("sector")),
        "industry": _clean_text(raw.get("industry")),
        "employees": _clean_count(raw.get("fullTimeEmployees")),
        "city": _clean_text(raw.get("city"), 80),
        "state": _clean_text(raw.get("state"), 80),
        "country": _clean_text(raw.get("country"), 60),
        "ipo_date": _clean_date(raw.get("ipoDate")),
        "website": _clean_website(raw.get("website")),
        "exchange": _clean_text(raw.get("exchange") or raw.get("exchangeShortName"), 40),
        "currency": _clean_currency(raw.get("currency")),
        "is_adr": _clean_bool(raw.get("isAdr")),
        "is_etf": _clean_bool(raw.get("isEtf")),
        "is_fund": _clean_bool(raw.get("isFund")),
        "description": raw.get("description"),
    }


def _looks_raw_profile(row: Dict[str, Any]) -> bool:
    """A raw FMP profile row (whale_service's write), not the Overview's formatted dict — and
    not a MERGED row that still carries raw keys beside the Overview's own (``founded`` /
    ``employees`` / ``headquarters``, which no raw profile has): that row's Overview fields are
    the fresh ones, its raw head count the old one, so it reads as the Overview's row
    (`_partial`, refreshed once by the full read). `_restamped` no longer builds such a row;
    this keeps one that exists from being read the wrong way."""
    if any(k in row for k in _OVERVIEW_ONLY_KEYS):
        return False
    return isinstance(row.get("companyName"), str) and (
        "symbol" in row or "ipoDate" in row or "fullTimeEmployees" in row)


def _fields_from_overview_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Facts from the Overview's FORMATTED row (no ``facts`` block, not a raw profile): the
    profile fields it carries — CEO, sector, industry, head count, headquarters (one string,
    "Cupertino, CA"), IPO date (its ``founded`` is the raw ``ipoDate``), website, description,
    country and the ADR / fund flags (``isEtf`` / ``isFund``, raw names). It has no company
    name, city / state split, exchange or trading currency, so ``_partial`` is set (`_load`).
    None when the row carries no core field: its writer prints "N/A" everywhere when its own
    profile read failed, and that row is a miss, never an empty answer. Pure."""
    if not any(k in row for k in _OVERVIEW_PROFILE_KEYS):
        return None
    country = _clean_text(row.get("country"), 60)
    place = _clean_text(row.get("headquarters"), 160)
    if place and country and place.lower() == country.lower():
        place = None        # the writer's country-only fallback: the country says it already
    fields: Dict[str, Any] = {
        "name": None,
        "ceo": _clean_text(row.get("ceo")),
        "sector": _clean_text(row.get("sector")),
        "industry": _clean_text(row.get("industry")),
        "employees": _clean_count(row.get("employees")),
        "city": None,
        "state": None,
        "location": place,
        "country": country,
        "ipo_date": _clean_date(row.get("founded")),
        "website": _clean_website(row.get("website")),
        "exchange": None,
        "currency": None,
        "is_adr": _clean_bool(row.get("is_adr")),
        "is_etf": _clean_bool(row.get("isEtf")),
        "is_fund": _clean_bool(row.get("isFund")),
        "description": row.get("description"),
        "_as_of": None,
        "_partial": True,
    }
    if not any(fields.get(k) for k in _OVERVIEW_CORE_FIELDS):
        return None
    return fields


def _facts_block_is_fresh(stamp: Optional[datetime], now: Optional[datetime] = None) -> bool:
    """A ``facts`` block's own fields count as current within `_FACTS_FRESH_SECONDS` of its
    stamp (5 minutes of clock skew tolerated). An unreadable stamp is not fresh."""
    if stamp is None:
        return False
    age = ((now or _now()) - stamp).total_seconds()
    return -300 <= age <= _FACTS_FRESH_SECONDS


def _fields_from_row(row: Any) -> Optional[Dict[str, Any]]:
    """Facts from a cached row, or None when the row cannot answer them.

    Three shapes, in order: a row with this module's ``facts`` block (``_partial`` when the
    block is older than `_FACTS_FRESH_SECONDS`), a raw profile (whale_service's write), the
    Overview's formatted dict (``_partial`` always: no company name, exchange or currency).
    ``_as_of`` is the block's own stamp, else None (the caller dates it)."""
    if not isinstance(row, dict):
        return None
    block = row.get(FACTS_KEY)
    if isinstance(block, dict) and block.get("v") == FACTS_VERSION:
        stamp = _parse_stamp(block.get("fetched_at"))
        return {
            "name": _clean_text(block.get("company_name"), 160),
            "ceo": _clean_text(row.get("ceo")),
            "sector": _clean_text(row.get("sector")),
            "industry": _clean_text(row.get("industry")),
            "employees": _clean_count(row.get("employees")),
            "city": _clean_text(block.get("city"), 80),
            "state": _clean_text(block.get("state"), 80),
            "country": _clean_text(block.get("country") or row.get("country"), 60),
            "ipo_date": _clean_date(block.get("ipo_date")),
            "website": _clean_website(row.get("website")),
            "exchange": _clean_text(block.get("exchange"), 40),
            "currency": _clean_currency(block.get("currency")),
            "is_adr": _clean_bool(block.get("is_adr")),
            "is_etf": _clean_bool(block.get("is_etf")),
            "is_fund": _clean_bool(block.get("is_fund")),
            "description": row.get("description"),
            "_as_of": stamp,
            "_partial": not _facts_block_is_fresh(stamp),
        }
    if _looks_raw_profile(row):
        fields = _fields_from_raw(row)
        fields["_as_of"] = None
        fields["_partial"] = False
        return fields
    return _fields_from_overview_row(row)


def _executive_rows(rows: Any) -> List[Dict[str, Any]]:
    """Cleaned executives: name and title required, deduped, current ones first, capped.
    Applied to FMP rows AND to stored rows (a stored row is re-cleaned, never trusted)."""
    if not isinstance(rows, list):
        return []
    this_year = _now().year
    seen = set()
    current: List[Dict[str, Any]] = []
    former: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = _clean_text(row.get("name"), 100)
        title = _clean_text(row.get("title"), 140)
        if not name or not title:
            continue
        key = (name.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)
        item: Dict[str, Any] = {"name": name, "title": title}
        year_born = row.get("year_born", row.get("yearBorn"))
        if (isinstance(year_born, (int, float)) and not isinstance(year_born, bool)
                and math.isfinite(float(year_born)) and 1900 <= int(year_born) <= this_year):
            item["year_born"] = int(year_born)
        since = row.get("since", row.get("titleSince"))
        if isinstance(since, (int, float)) and not isinstance(since, bool) \
                and math.isfinite(float(since)) and 1900 <= int(since) <= this_year:
            item["since"] = str(int(since))
        elif isinstance(since, str):
            day = _clean_date(since)
            if day:
                item["since"] = day
            elif since.strip().isdigit() and 1900 <= int(since.strip()) <= this_year:
                item["since"] = since.strip()
        active = row.get("active")
        if isinstance(active, bool):
            item["active"] = active
        (former if active is False else current).append(item)
    return (current + former)[:_EXECUTIVES_MAX]


def _fresh_executives(row: Any, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The row's executives block when its own stamp is within 7 days, else None."""
    if not isinstance(row, dict):
        return None
    block = row.get(EXECUTIVES_KEY)
    if not isinstance(block, dict):
        return None
    stamp = _parse_stamp(block.get("fetched_at"))
    if stamp is None:
        return None
    age = ((now or _now()) - stamp).total_seconds()
    if age < -300 or age > _EXECUTIVES_FRESH_SECONDS:
        return None
    if not isinstance(block.get("rows"), list):
        return None
    return {"fetched_at": _iso(stamp), "rows": _executive_rows(block.get("rows"))}


def _public(sym: str, fields: Dict[str, Any], executives: Optional[Dict[str, Any]], *,
            executives_status: str, as_of: Optional[datetime], stale: bool = False
            ) -> Dict[str, Any]:
    """The caller's dict. Absent fields are OMITTED (never "N/A", never 0)."""
    out: Dict[str, Any] = {"ticker": sym, "available": True}
    for key in ("name", "ceo", "sector", "industry", "employees", "website", "exchange",
                "currency"):
        if fields.get(key) is not None:
            out[key] = fields[key]
    # ``location`` is the Overview row's one-string headquarters ("Cupertino, CA"), present only
    # when the row has no city / state split.
    hq = {k: fields[k] for k in ("city", "state", "location", "country") if fields.get(k)}
    if hq:
        out["hq"] = hq
    if fields.get("ipo_date"):
        out["ipo_date"] = fields["ipo_date"]
        out["ipo_date_label"] = ("IPO or first listing date of this listing (for a spin-off, "
                                 "merger or re-listing it is when this listing began, not the "
                                 "company's founding)")
    for key in ("is_adr", "is_etf", "is_fund"):
        if isinstance(fields.get(key), bool):
            out[key] = fields[key]
    desc = fields.get("description")
    if isinstance(desc, str):
        desc = desc.strip()
        if desc and desc.lower() not in _PLACEHOLDERS:
            out["description"] = (desc if len(desc) <= _DESCRIPTION_MAX
                                  else desc[: _DESCRIPTION_MAX - 1].rstrip() + "…")
    if executives is not None:
        out["executives"] = executives.get("rows") or []
        out["executives_as_of"] = executives.get("fetched_at")
        if not out["executives"]:
            out["executives_note"] = ("No executive list is on file for this company; say "
                                      "so rather than naming anyone from memory.")
    else:
        out["executives_note"] = (
            "The executive list could not be loaded right now; say it was not loaded — never "
            "that the company has no executives." if executives_status == "failed" else
            "The executive list was not loaded in this answer.")
    if as_of is not None:
        out["as_of"] = _iso(as_of)
    elif not stale:
        # Only the 24 h tier reaches here undated: the Overview helper it read gates on that.
        out["as_of_note"] = "from the company profile cached within the last 24 hours"
    else:
        # An older row whose date cannot be read is never presented as recent.
        out["as_of_note"] = "from an older read of the company profile; its date is not known"
    if stale:
        out["stale_note"] = ("The profile could not be refreshed just now; these facts are "
                             "from an older read" + (" (see as_of)" if as_of is not None else "")
                             + " and may be out of date.")
    return out


def facts_as_profile_row(facts: Any) -> Optional[Dict[str, Any]]:
    """The shape `ChatService._format_company_profile` reads (description / ceo / sector /
    industry / employees / headquarters / country / founded), from `get_company_facts`'s
    dict — so a caller can switch its source without touching the formatter. Missing fields
    stay missing. Pure; None for an unavailable result."""
    if not isinstance(facts, dict) or not facts.get("available"):
        return None
    hq = facts.get("hq") if isinstance(facts.get("hq"), dict) else {}
    head = (", ".join(p for p in (hq.get("city"), hq.get("state")) if isinstance(p, str) and p)
            or (hq.get("location") if isinstance(hq.get("location"), str) else None) or None)
    return {
        "description": facts.get("description"),
        "ceo": facts.get("ceo"),
        "sector": facts.get("sector"),
        "industry": facts.get("industry"),
        "employees": facts.get("employees"),
        "headquarters": head,
        "country": hq.get("country"),
        "founded": facts.get("ipo_date"),
    }


# ── memory tier ───────────────────────────────────────────────────────────────

def _mem_live(sym: str) -> Optional[Tuple[float, float, Dict[str, Any], bool]]:
    entry = _mem.get(sym)
    if entry is None:
        return None
    if time.monotonic() - entry[0] > entry[1]:
        _mem.pop(sym, None)
        return None
    return entry


def _mem_get(sym: str, need_execs: bool = True) -> Optional[Dict[str, Any]]:
    """The live memo for `sym`, or None. A full read is never answered by a profile-only
    result (it has no executives) — but an UNAVAILABLE result (not found, an outage) answers
    both modes: the profile behind it is the same."""
    entry = _mem_live(sym)
    if entry is None:
        return None
    _stored, _ttl, value, with_execs = entry
    if need_execs and not with_execs and value.get("available"):
        return None
    return value


def _mem_set(sym: str, value: Dict[str, Any], ttl: float, *, with_execs: bool = True) -> None:
    current = _mem_live(sym)
    if (current is not None and current[3] and not with_execs and current[2].get("available")
            and value.get("available")):
        # A profile-only load finishing after a full one must not downgrade the memo to an
        # answer without executives (the next full read would re-load for nothing).
        return
    _mem.pop(sym, None)
    _mem[sym] = (time.monotonic(), ttl, value, with_execs)
    if len(_mem) > _MEM_MAX:
        for old in list(_mem.keys())[: len(_mem) - _MEM_MAX]:
            _mem.pop(old, None)


def clear_memory() -> None:
    """Test/ops hook: drop the hot tier (in-flight fetches are left to finish)."""
    _mem.clear()


# ── storage seams (patched in tests) ──────────────────────────────────────────

def _overview_service():
    from app.services.stock_overview_service import get_stock_overview_service

    return get_stock_overview_service()


def _db():
    from app.database import get_supabase

    return get_supabase()


def _fmp():
    from app.integrations.fmp import get_fmp_client

    return get_fmp_client()


def _read_row(sym: str) -> Tuple[Optional[Dict[str, Any]], Optional[datetime]]:
    """``(profile_json, cached_at)`` whatever its age. SYNC (to_thread). ``(None, None)`` ONLY
    when there is no usable row; a failed read RAISES, so the merge write can tell "no row"
    (write the update alone) from "could not read" (skip — `_merge_write`)."""
    res = (_db().table(_TABLE).select("profile_json, cached_at")
           .eq("ticker", sym).limit(1).execute())
    rows = getattr(res, "data", None) or []
    if not rows or not isinstance(rows[0], dict):
        return None, None
    blob = rows[0].get("profile_json")
    return (blob if isinstance(blob, dict) else None), _parse_stamp(rows[0].get("cached_at"))


def _read_row_any_age(sym: str) -> Tuple[Optional[Dict[str, Any]], Optional[datetime]]:
    """`_read_row` for the READ path — the 7-day executives tier and the outage fallback —
    where a failed read is simply a miss: ``(None, None)``, logged. Never raises."""
    try:
        return _read_row(sym)
    except Exception as e:  # noqa: BLE001 — best effort; a failed read is a miss
        logger.warning("company_facts: row read failed for %s: %s: %s", sym, type(e).__name__, e)
        return None, None


def _restamped(base: Optional[Dict[str, Any]], base_at: Optional[datetime],
               update: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """The ``profile_json`` a RE-STAMPING writer stores over `base` (written at `base_at`).
    Pure. Keeps every key `base` holds — another writer's blocks (``facts``,
    ``key_executives``, the fund flags, a raw profile's logo and name) included — except the
    kinds a fresh ``cached_at`` would re-date: the Overview's daily keys when `base` is past
    its 24 h (or undated), and a raw profile's price fields and duplicated identity fields
    (`_RAW_IDENTITY_KEYS`) always — so the result never reads as a raw profile. Then overlays
    `update`."""
    merged: Dict[str, Any] = dict(base) if isinstance(base, dict) else {}
    if base_at is None or (now - base_at).total_seconds() > _ROW_FRESH_SECONDS:
        for key in _DAILY_KEYS:
            merged.pop(key, None)
    for key in _RAW_PRICE_KEYS + _RAW_IDENTITY_KEYS:
        merged.pop(key, None)
    merged.update(update)
    return merged


def merge_profile_row(row: Any, update: Dict[str, Any],
                      *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The ``profile_json`` to store when a writer RE-STAMPS the shared row: `row` is the table
    row as read (``{"profile_json", "cached_at"}``; None or junk = no base), `update` the
    writer's own keys. The Overview's writer calls this (with its own client) so a detail view
    keeps the ``facts`` / ``key_executives`` blocks and the fund flags another writer stored.
    Pure; never raises for a malformed `row`."""
    base: Optional[Dict[str, Any]] = None
    base_at: Optional[datetime] = None
    if isinstance(row, dict):
        blob = row.get("profile_json")
        base = blob if isinstance(blob, dict) else None
        base_at = _parse_stamp(row.get("cached_at"))
    return _restamped(base, base_at, dict(update or {}), now or _now())


def _merge_write(sym: str, update: Dict[str, Any], *, restamp: bool) -> None:
    """READ-MERGE-WRITE of the shared row. SYNC (to_thread); best effort, never raises.

    Re-reads at write time (the Overview may have written since this call's read), keeps
    every key the base row holds, drops the daily keys of a base past its 24 h and the raw
    price and identity keys on a re-stamp (`_restamped`), and overlays `update`.
    `restamp=False` (an executives-only update) keeps the base row's own `cached_at`.

    A FAILED read skips the write (logged), exactly as the Overview's writer does: answering
    it like "no row" would upsert `update` alone over the row, dropping ``key_executives`` (a
    profile-only load never fetches them), whale's logo and name, and the fund flags — and
    the re-stamp would then hide the loss from whale's 7-day check."""
    kind = "profile" if restamp else "executives"
    try:
        base, base_at = _read_row(sym)
    except Exception as e:  # noqa: BLE001 — the answer is already in hand
        logger.warning("company_facts: %s write for %s SKIPPED — the row read failed (%s: %s); "
                       "a blind write would drop the blocks the other writers keep",
                       kind, sym, type(e).__name__, e)
        return
    try:
        now = _now()
        if restamp:
            merged = _restamped(base, base_at, update, now)
            stamp = now
        else:
            if base is None:
                logger.info("company_facts: no row for %s to attach executives to — skipped",
                            sym)
                return
            stamp = base_at or now
            merged = dict(base)
            merged.update(update)
        _db().table(_TABLE).upsert(
            {"ticker": sym, "profile_json": merged, "cached_at": _iso(stamp)},
            on_conflict="ticker",
        ).execute()
        logger.info("company_facts: %s row for %s written (%s)",
                    kind, sym, ", ".join(sorted(update)))
    except Exception as e:  # noqa: BLE001 — the answer is already in hand
        logger.warning("company_facts: write-back failed for %s (%s): %s: %s",
                       sym, kind, type(e).__name__, e)


def _schedule_write(sym: str, update: Dict[str, Any], *, restamp: bool) -> None:
    task = asyncio.ensure_future(asyncio.to_thread(_merge_write, sym, update, restamp=restamp))
    _pending_writes.add(task)

    def _done(t: "asyncio.Task") -> None:
        _pending_writes.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.warning("company_facts: write-back task failed for %s: %s",
                           sym, t.exception())

    task.add_done_callback(_done)


def _profile_update(service: Any, raw: Dict[str, Any], fields: Dict[str, Any],
                    executives: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The superset this writer contributes: the Overview's own keys in the Overview's own
    format (its builder, not a copy), the fund flags, country, company name and logo it copies
    from the raw profile, and the two blocks of this module."""
    from app.services.stock_overview_service import (
        fund_flags, profile_country_fields, profile_display_fields,
    )

    built = service._build_company_profile(raw)
    update: Dict[str, Any] = {
        "description": built.description,
        "ceo": built.ceo,
        "founded": built.founded,
        "employees": built.employees,
        "headquarters": built.headquarters,
        "website": built.website,
        "sector": built.sector,
        "industry": built.industry,
        **fund_flags(raw),
        **profile_country_fields(raw),
        # Whale's holdings name and logo, refreshed under this re-stamp (never kept stale).
        **profile_display_fields(raw),
        FACTS_KEY: {
            "v": FACTS_VERSION,
            "company_name": fields.get("name"),
            "city": fields.get("city"),
            "state": fields.get("state"),
            "country": fields.get("country"),
            "ipo_date": fields.get("ipo_date"),
            "exchange": fields.get("exchange"),
            "currency": fields.get("currency"),
            "is_adr": fields.get("is_adr"),
            "is_etf": fields.get("is_etf"),
            "is_fund": fields.get("is_fund"),
            "fetched_at": _iso(_now()),
        },
    }
    if executives is not None:
        update[EXECUTIVES_KEY] = executives
    return update


# ── the load ──────────────────────────────────────────────────────────────────

async def _none() -> None:
    return None


def _error_result(sym: str, error: str, **extra: Any) -> Dict[str, Any]:
    out = {"ticker": sym, "available": False, "error": error}
    out.update(extra)
    return out


async def _load(sym: str, need_execs: bool = True) -> Dict[str, Any]:
    """Cache → FMP for one symbol. Never raises; memoises what it returns.

    `need_execs=False` is the profile-only read: any usable row answers it and the
    key-executives call is never made (see the module docstring, TWO MODES)."""
    started = time.monotonic()
    service = None
    row: Optional[Dict[str, Any]] = None
    try:
        service = _overview_service()
        row = await asyncio.to_thread(service.get_cached_company_profile, sym)
    except Exception as e:  # noqa: BLE001 — a failed cache read is a miss
        logger.warning("company_facts: cache read failed for %s: %s: %s",
                       sym, type(e).__name__, e)
        row = None
    fields = _fields_from_row(row)
    executives = _fresh_executives(row) if need_execs else None
    # The full read needs what a `_partial` row lacks (the Overview's formatted row has no
    # company name, exchange or trading currency; a facts block past its 30 days is re-read).
    # The profile-only read is answered by any usable row.
    refresh = fields is not None and need_execs and bool(fields.get("_partial"))
    base_status = "ok" if need_execs else "skipped"

    if fields is not None and not refresh and (executives is not None or not need_execs):
        result = _public(sym, fields, executives, executives_status=base_status,
                         as_of=fields.get("_as_of"))
        _mem_set(sym, result, _MEM_TTL, with_execs=need_execs)
        logger.info("company_facts: cache HIT for %s%s (%.0f ms)", sym,
                    "" if need_execs else " (profile only)",
                    (time.monotonic() - started) * 1000)
        return result

    stale_row: Optional[Dict[str, Any]] = None
    stale_at: Optional[datetime] = None
    if fields is None:
        # The 24 h helper missed. An older row can still carry executives inside their own
        # 7 days, and is the fallback if the upstream fails — dated by its own stamp.
        stale_row, stale_at = await asyncio.to_thread(_read_row_any_age, sym)
        if need_execs and executives is None:
            executives = _fresh_executives(stale_row)

    fmp = _fmp()
    need_profile = fields is None or refresh
    call_execs = need_execs and executives is None
    profile_res, execs_res = await asyncio.gather(
        fmp.get_company_profile(sym) if need_profile else _none(),
        fmp.get_key_executives(sym) if call_execs else _none(),
        return_exceptions=True,
    )

    executives_status = base_status
    fetched_execs: Optional[Dict[str, Any]] = None
    if call_execs:
        if isinstance(execs_res, BaseException):
            executives_status = "failed"
            logger.warning("company_facts: key executives failed for %s: %s: %s",
                           sym, type(execs_res).__name__, execs_res)
        else:
            fetched_execs = {"fetched_at": _iso(_now()),
                             "rows": _executive_rows(execs_res if isinstance(execs_res, list)
                                                     else [])}
            executives = fetched_execs

    # A failed executives read is remembered only as long as any other failure: one blip
    # must not answer "the executive list could not be loaded" for the full hot-tier TTL.
    ok_ttl = _FAILURE_TTL if executives_status == "failed" else _MEM_TTL

    if not need_profile:
        # Profile from the fresh cached row; only the executives were fetched.
        result = _public(sym, fields, executives, executives_status=executives_status,
                         as_of=fields.get("_as_of"))
        _mem_set(sym, result, ok_ttl, with_execs=need_execs)
        if fetched_execs is not None:
            _schedule_write(sym, {EXECUTIVES_KEY: fetched_execs}, restamp=False)
        return result

    profile_ok = (isinstance(profile_res, dict) and bool(profile_res)
                  and _same_symbol(profile_res.get("symbol"), sym))

    if refresh and not profile_ok:
        # The refresh failed or found nothing, but the row is inside its 24 h: it still
        # answers its own fields — fresh, not stale — only without the ones it lacks. A
        # failed refresh is retried after the short failure TTL, never held for the hot tier.
        refresh_failed = isinstance(profile_res, BaseException)
        if refresh_failed:
            logger.warning("company_facts: profile refresh failed for %s (answering from the "
                           "cached row): %s: %s", sym, type(profile_res).__name__, profile_res)
        else:
            logger.warning("company_facts: profile refresh for %s found no matching profile "
                           "(%r) — answering from the cached row", sym,
                           profile_res.get("symbol") if isinstance(profile_res, dict) else None)
        result = _public(sym, fields, executives, executives_status=executives_status,
                         as_of=fields.get("_as_of"))
        _mem_set(sym, result, _FAILURE_TTL if refresh_failed else ok_ttl,
                 with_execs=need_execs)
        if fetched_execs is not None:
            _schedule_write(sym, {EXECUTIVES_KEY: fetched_execs}, restamp=False)
        return result

    if isinstance(profile_res, BaseException):
        logger.warning("company_facts: profile fetch failed for %s: %s: %s",
                       sym, type(profile_res).__name__, profile_res)
        stale_fields = _fields_from_row(stale_row)
        if stale_fields is not None:
            # Dated by the facts block's own stamp, else the row's `cached_at` — never left
            # undated next to a "24 hours" note it cannot honour. Any of the three row shapes
            # (facts, raw, the Overview's formatted row) is a fallback.
            result = _public(sym, stale_fields, executives,
                             executives_status=executives_status,
                             as_of=stale_fields.get("_as_of") or stale_at, stale=True)
            _mem_set(sym, result, _FAILURE_TTL, with_execs=need_execs)
            return result
        # The exception class (a vendor's name) stays in the log above, never in the result.
        result = _error_result(sym, _UPSTREAM_ERROR, upstream=True)
        _mem_set(sym, result, _FAILURE_TTL, with_execs=need_execs)
        return result

    if not profile_ok:
        if isinstance(profile_res, dict) and profile_res:
            logger.warning("company_facts: profile for %s came back as %r — refused",
                           sym, profile_res.get("symbol"))
        result = _error_result(sym, "no company profile on file for this symbol",
                               not_found=True)
        _mem_set(sym, result, _NOT_FOUND_TTL, with_execs=need_execs)
        logger.info("company_facts: no profile for %s", sym)
        return result

    fresh = _fields_from_raw(profile_res)
    result = _public(sym, fresh, executives, executives_status=executives_status,
                     as_of=_now())
    _mem_set(sym, result, ok_ttl, with_execs=need_execs)
    try:
        if service is None:
            service = _overview_service()
        # Executives ride along only when this load fetched them (`fetched_execs`) or read
        # them inside their 7 days; a profile-only load writes none, and the merge keeps the
        # row's own block.
        _schedule_write(sym, _profile_update(service, profile_res, fresh, executives),
                        restamp=True)
    except Exception as e:  # noqa: BLE001 — the answer stands without the write
        logger.warning("company_facts: write-back not scheduled for %s: %s: %s",
                       sym, type(e).__name__, e)
    logger.info("company_facts: FMP %s for %s (%.0f ms)",
                "profile+executives" if call_execs else "profile", sym,
                (time.monotonic() - started) * 1000)
    return result


def _on_load_done(table: Dict[str, "asyncio.Task"], sym: str, task: "asyncio.Task") -> None:
    if table.get(sym) is task:
        table.pop(sym, None)
    if not task.cancelled() and task.exception() is not None:
        logger.warning("company_facts: load for %s raised %s: %s", sym,
                       type(task.exception()).__name__, task.exception())


def _joinable(task: Optional["asyncio.Task"]) -> bool:
    """A load this event loop can await — never one a closed loop left behind (its result
    would raise "attached to a different loop", read as an outage)."""
    if task is None:
        return False
    try:
        return task.get_loop() is asyncio.get_running_loop()
    except RuntimeError:
        return False


async def _get_one(sym: str, need_execs: bool = True) -> Dict[str, Any]:
    hit = _mem_get(sym, need_execs)
    if hit is not None:
        return hit
    # A full load answers a profile-only caller too; never the reverse.
    task = _inflight.get(sym)
    if not _joinable(task) and not need_execs:
        task = _profile_inflight.get(sym)
    if not _joinable(task):
        table = _inflight if need_execs else _profile_inflight
        task = asyncio.ensure_future(_load(sym) if need_execs
                                     else _load(sym, need_execs=False))
        table[sym] = task
        task.add_done_callback(lambda t, tb=table, s=sym: _on_load_done(tb, s, t))
    # Shielded: a caller giving up (a tool timeout) never cancels the shared fetch — it
    # finishes and warms both tiers for the next question.
    return await asyncio.shield(task)


async def get_company_facts(ticker: str, *, need_executives: bool = True) -> Dict[str, Any]:
    """Facts for a listed company (or fund), for any ticker. Never raises.

    ``{"ticker", "available": True, "name", "ceo", "sector", "industry", "employees",
    "hq": {"city", "state", "location", "country"}, "ipo_date", "ipo_date_label", "website",
    "exchange", "currency" (trading), "is_adr", "is_etf", "is_fund", "description" (RAW,
    ≤5000 chars — fence it), "executives": [{"name", "title", "since"?, "year_born"?,
    "active"?}], "executives_as_of", "as_of" | "as_of_note", "stale_note"?}`` — absent
    fields are omitted, never placeholders (``hq.location`` is the Overview row's one-string
    headquarters, only when there is no city / state split). On failure: ``{"ticker",
    "available": False, "error", "upstream": True}`` (an outage) or ``{..., "not_found":
    True}`` (no profile).

    ``need_executives=False`` — the profile-only read (the chat's STOCK profile line): never
    calls key-executives, and any usable cached row (the Overview's own formatted row
    included) answers it with no upstream call. Its result carries no ``executives``.
    """
    sym = normalize_symbol(ticker)
    if sym is None:
        return {"available": False, "error": "no valid ticker supplied"}
    need_execs = bool(need_executives) if isinstance(need_executives, bool) else True
    try:
        result = await _get_one(sym, need_execs)
        if result.get("not_found"):
            alt = _dash_class_candidate(sym)
            if alt:
                alt_result = await _get_one(alt, need_execs)
                if alt_result.get("available"):
                    result = alt_result
        return copy.deepcopy(result)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — `_load` never raises; belt and braces
        logger.warning("company_facts: get_company_facts failed for %s: %s: %s",
                       sym, type(e).__name__, e, exc_info=True)
        return _error_result(sym, _UPSTREAM_ERROR, upstream=True)


__all__ = [
    "get_company_facts", "facts_as_profile_row", "merge_profile_row", "normalize_symbol",
    "clear_memory", "FACTS_KEY", "FACTS_VERSION", "EXECUTIVES_KEY",
]
