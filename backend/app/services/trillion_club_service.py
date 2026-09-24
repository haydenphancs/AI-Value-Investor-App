"""
Trillion-Dollar Club Bets — the READ path: the Home section and the per-company detail.

What the companies worth $1 trillion or more own in other companies. Two kinds of data,
kept apart all the way to the wire (see ``app/schemas/trillion_club.py``):

* **13F holdings** — built by the daily job (``app/services/trillion_club/``) into
  ``trillion_club_filings``: one snapshot per (CIK, quarter) with the holdings JSON and the
  quarter-over-quarter SHARE changes. Only companies the owner opted in (``use_13f``).
* **Stakes** — hand-kept rows in ``trillion_club_stakes`` (private, non-U.S., warrants,
  commitments), each with a primary source, the date it describes and when it was checked.

This module NEVER calls FMP and never writes. It reads Supabase only (service role, via
``sb_exec`` so no statement runs on the event loop) and assembles the response.

CACHING (CLAUDE.md invariant 4, the Themes variant)
---------------------------------------------------
Tier 1 is a 10-minute in-memory cache for the group and for each detail, with shielded
``_inflight`` joins settled on every exit (model: ``HomeDashboardService.get_themes``).
Tier 2 IS the job's persisted output — the request path has nothing expensive upstream to
protect, so there is no separate ``*_cache`` table. A Supabase read error RAISES and is
never cached as an empty section; ``get_group_guarded`` then serves the last good group.

WHAT MAKES THE SECTION EMPTY (iOS hides an empty section)
---------------------------------------------------------
* ``settings.TRILLION_CLUB_ENABLED`` is False — no database read at all;
* membership is STALE: the newest ``membership_checked_at`` among the FMP-sized companies is
  older than 7 days (or missing; a stamp in the future does not count). A dead job must not
  keep a company that fell far below $1T on Home because the stored row still says
  "member". Logged at WARNING;
* nothing is published / no member has anything to show.

ONE member is left out (the rest still show) when its OWN evidence is stale: an FMP-sized
member's ``membership_checked_at`` older than 7 days / missing / in the future (the job
fails closed per company and does not advance it), or a hand-sized member's
``manual_cap_as_of`` older than 45 days / missing / in the future.

READ-TIME DECISIONS (deliberately not stored)
---------------------------------------------
* ``club_member_slug`` — whether a holding or stake is itself a club member — is computed
  here from the CURRENT registry (``cap_symbol`` + ``symbol_aliases`` + ``detail_symbol``).
  A 13F snapshot is rebuilt only when its hash changes (usually once a quarter), so a tag
  baked into it would go stale the day AMD joins or LLY leaves.
* Membership honours the owner's override immediately: ``force_in`` / ``force_out`` win over
  the job-written ``is_member`` (the job only re-evaluates FMP-sized companies, so a
  hand-sized one such as Aramco would otherwise never become a member).
* A stake is re-validated on every read: a row missing its source (or with a hostless
  link) or dates, carrying a banned word, or with a forecast in its background, is DROPPED
  with a WARNING rather than shown (the migration's CHECKs cover some of this; Studio edits
  and future columns are why it is checked again here). The copy rules are the seed
  script's own (``app/services/trillion_club/copy_rules.py``). A malformed stake symbol
  keeps the stake and loses only its link.

COPY
----
Nothing in this section is generated text; every string is hand-kept or comes from an SEC
filing. The data legitimately names Alphabet, OpenAI and Anthropic, so vendor regexes are
NOT applied to it, and it is not fed into chat.

GATING
------
Cards are free. The detail's depth is Pro: ``redact_trillion_club_detail`` is a pure,
copy-on-read redactor applied per request by the endpoint (the cached object is shared by
every caller, so it must never be edited in place — same rule as ``redact_signals``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from pydantic import PrivateAttr

from app.config import settings
from app.database import get_supabase
from app.schemas.trillion_club import (
    CARD_KINDS,
    CARD_THIRTEEN_F,
    CARD_WHALE_LINK,
    CHANGE_CORPORATE_ACTION,
    CHANGE_DECREASED,
    CHANGE_INCREASED,
    CHANGE_KINDS,
    CHANGE_NEWLY_REPORTED,
    CHANGE_NO_LONGER_REPORTED,
    CHANGE_UNCHANGED,
    COMPARISON_FIRST_FILING,
    COMPARISON_GAP,
    COMPARISON_QUARTER,
    NOTICE_AMENDED,
    NOTICE_FIRST_FILING,
    NOTICE_LATEST_NOT_IN,
    NOTICE_NO_NEWER_FILING,
    STAKE_KINDS,
    VALUE_BASES,
    ClubChangeCountsResponse,
    ClubChangeResponse,
    ClubHistoryPointResponse,
    ClubHoldingResponse,
    ClubMemberBriefResponse,
    ClubStakeResponse,
    TrillionClubCompanyResponse,
    TrillionClubDetailResponse,
    TrillionClubGroupResponse,
)
# ONE copy of the copy rules, shared with scripts/seed_trillion_club.py (they had drifted:
# this path used to serve "bought", "worth", "endorsed" and forecasts a seed run refuses).
# `contains_banned_copy` is re-exported here for existing callers.
from app.services.trillion_club.copy_rules import (
    STAKE_TEXT_FIELDS as _STAKE_TEXT_FIELDS,
    contains_banned_copy,
    contains_forecast,
)
from app.utils.market_hours import ET as _ET
from app.utils.supabase_async import sb_exec

logger = logging.getLogger(__name__)

# ── Tables ───────────────────────────────────────────────────────────────────────────
_COMPANIES_TABLE = "trillion_club_companies"
_STAKES_TABLE = "trillion_club_stakes"
_FILINGS_TABLE = "trillion_club_filings"
_WHALES_TABLE = "whales"

# Row ceilings. The registry is ~20 rows and a company ~10 stakes; these only stop a bad
# Studio bulk edit from turning one Home request into a multi-megabyte read.
_MAX_COMPANY_ROWS = 200
_MAX_STAKE_ROWS = 2000
_MAX_FILING_INDEX_ROWS = 500
_MAX_WHALE_ROWS = 50

# ── Cache / guard knobs ──────────────────────────────────────────────────────────────
_GROUP_KEY = "group"
_CACHE_TTL_SECONDS = 600               # 10 min, group and each detail (plan M4)
_GROUP_TIMEOUT_SECONDS = 6.0           # the Home branch's hard ceiling
# How old a last-good group may be and still stand in for a failed / slow rebuild. Past
# this the section is hidden instead: a long database outage must not keep serving a
# membership picture the 7-day staleness rule would already have withdrawn.
_GROUP_FALLBACK_MAX_AGE_SECONDS = 24 * 3600

# ── Content rules ────────────────────────────────────────────────────────────────────
_MEMBERSHIP_STALE_AFTER = timedelta(days=7)
# A `membership_checked_at` further ahead of this host's clock than this is a typo (a Studio
# edit of 2031 for 2026), not skew: it must not vouch for anything, or it would keep a dead
# job looking fresh for years.
_MEMBERSHIP_CLOCK_SKEW = timedelta(minutes=5)
# A hand-sized member (cap_source manual — Aramco, Samsung) has no FMP series for the job to
# check; its evidence is the owner's dated figure. Same horizon as the job's re-check WARNING
# (jobs.MANUAL_CAP_WARN_DAYS, pinned equal by test_trillion_club_service.py).
_MANUAL_CAP_STALE_AFTER_DAYS = 45
_STAKE_STALE_AFTER_DAYS = 120          # verified_on older than this → is_stale
_TOP_HOLDINGS = 3                      # the Home card
_FREE_HOLDINGS = 3                     # the detail, for a locked (Free) caller
_MAX_HISTORY_POINTS = 12
_BACKGROUND_MAX_CHARS = 90
_INVESTEE_NAME_MAX_CHARS = 60
_SOURCE_TITLE_MAX_CHARS = 120

_FMP_CAP_SOURCES = frozenset({"fmp_us", "fmp_adr"})
_CAP_SOURCES = _FMP_CAP_SOURCES | {"manual"}
_MEMBERSHIP_MODES = frozenset({"auto", "force_in", "force_out"})
_COMPARISONS = frozenset({COMPARISON_QUARTER, COMPARISON_FIRST_FILING, COMPARISON_GAP})

# fullmatch only — `re.match(r"^...$")` accepts a trailing "\n" (`$` matches before it).
_SLUG_RE = re.compile(r"[a-z0-9-]{1,40}")
_PERIOD_RE = re.compile(r"(\d{4})-Q([1-4])")
_CIK_RE = re.compile(r"\d{10}")
# A hand-kept stake symbol becomes a tappable chip that opens the stock screen at
# /stocks/{symbol}: "N/A", "BRK B" or a pasted URL must not. Ticker shape, share class allowed.
_STAKE_SYMBOL_RE = re.compile(r"[A-Z][A-Z0-9]{0,5}(?:[.-][A-Z0-9]{1,2})?")
# A count iOS decodes as `Int`; JSONB numbers are arbitrary precision, and one that does not
# fit would fail the whole Home payload's decode, not just this card.
_MAX_COUNT = 2 ** 53 - 1

_STAKE_ON_13F_NOTE = "on_13f_note"

# Display order of a detail's change rows (never `unchanged`, which is only counted).
_CHANGE_ORDER = {
    CHANGE_NEWLY_REPORTED: 0,
    CHANGE_INCREASED: 1,
    CHANGE_DECREASED: 2,
    CHANGE_CORPORATE_ACTION: 3,
    CHANGE_NO_LONGER_REPORTED: 4,
}

# Banned copy (plan §Copy) lives in app/services/trillion_club/copy_rules.py — imported above,
# never re-declared here. "Bet(s)" is allowed only in the section TITLE, which iOS owns.


class TrillionClubBuildCancelled(RuntimeError):
    """Handed to joiners when the leader's build was cancelled (a client disconnect or a
    shutdown), so they fail fast through their own error path instead of inheriting a
    CancelledError they never asked for."""


# ── Clock seams (monkeypatched in tests) ─────────────────────────────────────────────


def _today_et() -> date:
    return datetime.now(_ET).date()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _rules():
    """The pure 13F calendar rules (``app.services.trillion_club.rules``).

    Imported lazily and only where a notice is computed: a defect in that module must cost
    a card its ``next_due`` / notice, never the whole Home screen's import."""
    from app.services.trillion_club import rules

    return rules


# ── Pure coercion helpers ────────────────────────────────────────────────────────────


def _finite(value: Any) -> Optional[float]:
    """A finite float, or None for None / bool / NaN / ±inf / non-numeric.

    OverflowError too: JSONB numbers are arbitrary precision, so ``json.loads`` hands back an
    int like ``10**400`` that ``float()`` cannot hold. Uncaught, one such value in ONE
    company's holdings failed the whole group build and hid the section for everyone."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _positive(value: Any) -> Optional[float]:
    out = _finite(value)
    return out if out is not None and out > 0 else None


def _non_negative(value: Any) -> Optional[float]:
    out = _finite(value)
    return out if out is not None and out >= 0 else None


def _count(value: Any) -> Optional[int]:
    """A non-negative integer count iOS can decode as ``Int``, or None (bools, fractional
    floats and anything above ``_MAX_COUNT`` refused)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= _MAX_COUNT else None
    if isinstance(value, float) and math.isfinite(value) and value.is_integer() and 0 <= value <= _MAX_COUNT:
        return int(value)
    return None


def _text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _iso_date(value: Any) -> Optional[str]:
    """``YYYY-MM-DD`` from a date / datetime / ISO string, or None when unparseable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        head = value.strip()[:10]
        try:
            return date.fromisoformat(head).isoformat()
        except ValueError:
            return None
    return None


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """An aware datetime from a TIMESTAMPTZ string (naive is read as UTC), or None."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _norm_symbol(value: Any) -> Optional[str]:
    """Upper-case, and one separator for share classes (``BRK.B`` ≡ ``BRK-B``)."""
    text = _text(value)
    if text is None:
        return None
    return text.upper().replace(".", "-").replace("/", "-")


def _period_key(period: Any) -> Optional[Tuple[int, int]]:
    if not isinstance(period, str):
        return None
    match = _PERIOD_RE.fullmatch(period.strip())
    return (int(match.group(1)), int(match.group(2))) if match else None


def _next_quarter(year: int, quarter: int) -> Tuple[int, int]:
    return (year + 1, 1) if quarter == 4 else (year, quarter + 1)


def _json(value: Any, expected: type, *, what: str) -> Any:
    """A JSONB column as the Python type it must be; a wrong shape degrades to empty, loudly."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            logger.warning("trillion club: %s is not valid JSON (%s: %s)", what, type(exc).__name__, exc)
            return expected()
    if value is None:
        return expected()
    if not isinstance(value, expected):
        logger.warning(
            "trillion club: %s is a %s, expected %s — ignored", what,
            type(value).__name__, expected.__name__,
        )
        return expected()
    return value


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [s for s in (_text(v) for v in value) if s]


def _normalise_cik(value: Any) -> Optional[str]:
    """A 10-digit zero-padded CIK. `whales.cik` is padded today; a bare one is accepted too."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and not 0 <= value < 10 ** 10:
        return None                    # also keeps str() off a >4300-digit int (ValueError)
    text = str(value) if isinstance(value, int) else _text(value)
    if text is None or not text.isascii() or not text.isdigit() or len(text) > 10:
        return None
    return text.zfill(10)


# ── Parsed rows ──────────────────────────────────────────────────────────────────────


@dataclass
class _Company:
    slug: str
    name: str
    card_kind: str
    use_13f: bool
    ciks: List[str]
    symbols: List[str]                # normalised cap_symbol + aliases + detail_symbol
    detail_symbol: Optional[str]
    logo_symbol: Optional[str]
    cap_source: str
    market_cap: Optional[float]
    market_cap_as_of: Optional[str]
    cap_is_manual: bool
    is_member: bool                   # EFFECTIVE membership (force modes applied)
    checked_at: Optional[datetime]
    link_whale: bool
    reviewed_on: Optional[str]

    @property
    def cap_sort_key(self) -> Tuple[float, str]:
        # Largest first; a missing cap sinks; slug breaks ties so the order is stable.
        return (-(self.market_cap or 0.0), self.slug)


class _ClubStake(ClubStakeResponse):
    """The wire's ``ClubStakeResponse``, plus ONE fact that never reaches the wire: which 13F
    holding an ``on_13f_note`` names, as that holding's ``(name, symbol)`` on the wire.

    The link is resolved at build time by CUSIP or symbol — neither of which the wire's
    holdings carry reliably (no CUSIP at all; no symbol when not routable) — and the Free
    redaction needs it to drop a note about a holding it withholds. A private attribute is
    not a field: ``model_dump`` / the response never see it, and ``model_copy`` (deep or
    not) keeps it."""

    _names_holding: Optional[Tuple[str, Optional[str]]] = PrivateAttr(default=None)


@dataclass
class _Stake:
    company_slug: str
    material: bool
    sort_key: Tuple[int, str]
    raw_symbol: Optional[str]          # shape-checked; None when missing or malformed
    cusip: Optional[str]               # investee_cusip, upper-cased (links an on_13f_note)
    response: _ClubStake


@dataclass
class _Filing:
    cik: str
    period: str
    period_end: str
    filed_on: Optional[str]
    amended_on: Optional[str]
    accessions: List[str]
    total_value: Optional[float]
    position_count: Optional[int]
    holdings: List[Dict[str, Any]] = field(default_factory=list)
    changes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _IndexRow:
    cik: str
    period: str
    key: Tuple[int, int]
    period_end: Optional[str]
    filed_on: Optional[str]
    total_value: Optional[float]
    position_count: Optional[int]


def _parse_company(row: Mapping[str, Any]) -> Optional[_Company]:
    """One registry row, or None (with a WARNING naming the slug and the reason)."""
    slug = row.get("slug")
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        logger.warning("trillion club: company row with invalid slug %r skipped", slug)
        return None
    name = _text(row.get("display_name"))
    card_kind = row.get("card_kind")
    cap_source = row.get("cap_source")
    mode = row.get("membership_mode") or "auto"
    problem = None
    # isinstance first: a set/dict membership test raises TypeError on an unhashable value,
    # which would fail the whole group build instead of skipping this one row.
    if name is None:
        problem = "no display_name"
    elif not isinstance(card_kind, str) or card_kind not in CARD_KINDS:
        problem = f"unknown card_kind {card_kind!r}"
    elif not isinstance(cap_source, str) or cap_source not in _CAP_SOURCES:
        problem = f"unknown cap_source {cap_source!r}"
    elif not isinstance(mode, str) or mode not in _MEMBERSHIP_MODES:
        problem = f"unknown membership_mode {mode!r}"
    if problem:
        logger.warning("trillion club: company %s skipped — %s", slug, problem)
        return None

    if mode == "force_in":
        is_member = True
    elif mode == "force_out":
        is_member = False
    else:
        is_member = row.get("is_member") is True

    if cap_source == "manual":
        cap = _positive(row.get("manual_cap_usd"))
        cap_as_of = _iso_date(row.get("manual_cap_as_of"))
    else:
        cap = _positive(row.get("last_market_cap"))
        cap_as_of = _iso_date(row.get("last_cap_date"))

    ciks = []
    for raw in _string_list(row.get("ciks")):
        cik = raw if _CIK_RE.fullmatch(raw) else None
        if cik is None:
            logger.warning("trillion club: company %s has a malformed CIK %r — ignored", slug, raw)
            continue
        if cik not in ciks:
            ciks.append(cik)

    detail_symbol = _text(row.get("detail_symbol"))
    symbols: List[str] = []
    for candidate in [row.get("cap_symbol"), detail_symbol, *_string_list(row.get("symbol_aliases"))]:
        norm = _norm_symbol(candidate)
        if norm and norm not in symbols:
            symbols.append(norm)

    return _Company(
        slug=slug,
        name=name,
        card_kind=card_kind,
        use_13f=row.get("use_13f") is True,
        ciks=ciks,
        symbols=symbols,
        detail_symbol=detail_symbol,
        logo_symbol=_text(row.get("logo_symbol")),
        cap_source=cap_source,
        market_cap=cap,
        market_cap_as_of=cap_as_of,
        cap_is_manual=cap_source == "manual",
        is_member=is_member,
        checked_at=_parse_timestamp(row.get("membership_checked_at")),
        link_whale=row.get("link_whale") is True,
        reviewed_on=_iso_date(row.get("reviewed_on")),
    )


def _membership_problem(companies: Sequence[_Company], now: datetime) -> Optional[str]:
    """Why the section must be hidden for stale membership, or None when it is fresh.

    Section level: the NEWEST stamp among the FMP-sized companies says whether the job is
    alive at all. A stamp ahead of ``now`` by more than the clock skew is ignored (logged):
    one typo'd year would otherwise vouch for a dead job for years. Each member is then
    judged on its OWN evidence by ``_member_problem``."""
    fmp_sized = [c for c in companies if c.cap_source in _FMP_CAP_SOURCES]
    if not fmp_sized:
        return "no published FMP-sized company to vouch for the membership job"
    horizon = now + _MEMBERSHIP_CLOCK_SKEW
    checked = []
    for company in fmp_sized:
        if company.checked_at is None:
            continue
        if company.checked_at > horizon:
            logger.warning(
                "trillion club: %s membership_checked_at %s is in the future — it cannot vouch "
                "for the membership job", company.slug, company.checked_at.isoformat(),
            )
            continue
        checked.append(company.checked_at)
    if not checked:
        return "membership has never been checked (membership_checked_at is empty or in the future)"
    newest = max(checked)
    if now - newest > _MEMBERSHIP_STALE_AFTER:
        return f"membership last checked {newest.isoformat()} (more than 7 days ago)"
    return None


def _member_problem(company: _Company, now: datetime, today: date) -> Optional[str]:
    """Why this MEMBER must be left out on its own evidence, or None when it may show.

    The job fails CLOSED per company (a renamed or stalled cap symbol keeps the stored
    ``is_member`` and does NOT advance ``membership_checked_at``), so one company can sit
    frozen while every other keeps the section fresh — after 30 days its 20-close leave
    rule could have fired unseen. So:

    * FMP-sized (any mode): its own ``membership_checked_at`` must exist, be at most 7 days
      old, and not be ahead of this clock by more than the skew;
    * hand-sized (``cap_source`` manual — the job has no series to check, and stamps a
      forced row whatever the data): its ``manual_cap_as_of`` must exist, be at most
      ``_MANUAL_CAP_STALE_AFTER_DAYS`` old, and not be dated after tomorrow (ET vs UTC)."""
    if company.cap_is_manual:
        if company.market_cap_as_of is None:
            return "hand-sized, with no manual_cap_as_of"
        as_of = date.fromisoformat(company.market_cap_as_of)
        if as_of > today + timedelta(days=1):
            return f"manual_cap_as_of {as_of.isoformat()} is in the future"
        if (today - as_of).days > _MANUAL_CAP_STALE_AFTER_DAYS:
            return (
                f"manual_cap_as_of {as_of.isoformat()} is more than "
                f"{_MANUAL_CAP_STALE_AFTER_DAYS} days old"
            )
        return None
    checked = company.checked_at
    if checked is None:
        return "its membership has never been checked"
    if checked > now + _MEMBERSHIP_CLOCK_SKEW:
        return f"its membership_checked_at {checked.isoformat()} is in the future"
    if now - checked > _MEMBERSHIP_STALE_AFTER:
        return f"its membership was last checked {checked.isoformat()} (more than 7 days ago)"
    return None


def _member_tag_map(members: Iterable[_Company]) -> Dict[str, str]:
    """normalised symbol → member slug, from the CURRENT registry (read time)."""
    tags: Dict[str, str] = {}
    for company in sorted(members, key=lambda c: c.slug):
        for symbol in company.symbols:
            owner = tags.get(symbol)
            if owner is not None and owner != company.slug:
                logger.warning(
                    "trillion club: symbol %s claimed by both %s and %s — keeping %s",
                    symbol, owner, company.slug, owner,
                )
                continue
            tags[symbol] = company.slug
    return tags


def _tag_for(symbol: Any, tags: Mapping[str, str], own_slug: str) -> Optional[str]:
    norm = _norm_symbol(symbol)
    if norm is None:
        return None
    slug = tags.get(norm)
    # A company is never tagged as a member inside its own card.
    return slug if slug and slug != own_slug else None


# ── Stakes: the runtime validator ────────────────────────────────────────────────────


def _source_url_problem(url: Optional[str]) -> Optional[str]:
    """Why a stake's source link is unusable, or None. A stake is shown as a SOURCED fact:
    ``https://``, ``https:///x`` or ``https://?q=1`` open nothing, so they are no source."""
    if url is None or not url.startswith("https://") or any(ch.isspace() for ch in url):
        return "source_url missing or not https"
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:                 # e.g. an unbalanced "[" in the host
        return "source_url is not a valid URL"
    if parts.scheme != "https" or "@" in parts.netloc or not host or "." not in host.strip("."):
        return "source_url has no host"
    return None


def stake_problem(row: Mapping[str, Any], today: Optional[date] = None) -> Optional[str]:
    """Pure: why a stake row must NOT be shown, or None when it may be.

    Belt and braces over migration 175's CHECKs: Studio edits, a future column and a
    manual SQL fix all reach this path without passing the seed's review, and a stake is
    shown to users as a sourced fact. With ``today``, a date after tomorrow is refused too:
    a typo'd year would otherwise make ``verified_on`` read as "never stale"."""
    if row.get("published") is not True:
        return "not published"
    if row.get("source_confidence") != "primary":
        return f"source_confidence {row.get('source_confidence')!r} is not primary"
    kind = row.get("kind")
    if kind not in STAKE_KINDS:
        return f"unknown kind {kind!r}"
    name = _text(row.get("investee_name"))
    if name is None or len(name) > _INVESTEE_NAME_MAX_CHARS:
        return "investee_name missing or too long"
    url_problem = _source_url_problem(_text(row.get("source_url")))
    if url_problem is not None:
        return url_problem
    title = _text(row.get("source_title"))
    if title is None or len(title) > _SOURCE_TITLE_MAX_CHARS:
        return "source_title missing or too long"
    if _iso_date(row.get("as_of")) is None:
        return "as_of missing or not a date"
    if _iso_date(row.get("verified_on")) is None:
        return "verified_on missing or not a date"
    if today is not None:
        latest_ok = today + timedelta(days=1)   # ET vs UTC: a row checked "tomorrow" in UTC
        for column in ("as_of", "verified_on"):
            if date.fromisoformat(_iso_date(row.get(column))) > latest_ok:
                return f"{column} is in the future"
    if row.get("listed_since") is not None and _iso_date(row.get("listed_since")) is None:
        return "listed_since is not a date"
    basis = row.get("value_basis")
    if basis is not None and basis not in VALUE_BASES:
        return f"unknown value_basis {basis!r}"
    if kind == "commitment" and basis not in (None, "committed_up_to"):
        return "a commitment's value must be committed_up_to"
    value = row.get("disclosed_value_usd")
    if value is not None:
        if _positive(value) is None:
            return "disclosed_value_usd is not a positive finite number"
        if basis is None:
            return "disclosed_value_usd without a value_basis"
    pct = row.get("ownership_pct")
    if pct is not None:
        parsed = _finite(pct)
        if parsed is None or not 0 < parsed <= 100:
            return "ownership_pct outside (0, 100]"
    background = row.get("background")
    if background is not None and (not isinstance(background, str) or len(background) > _BACKGROUND_MAX_CHARS):
        return "background longer than 90 characters"
    for column in _STAKE_TEXT_FIELDS:
        if contains_banned_copy(row.get(column)):
            return f"banned wording in {column}"
    # A background states what happened (past tense); a forecast or motive is not a fact a
    # filing can support. Same rule, same field, as the seed script.
    if contains_forecast(background):
        return "forecast wording in background"
    return None


def _parse_stake(row: Mapping[str, Any], today: date) -> Optional[_Stake]:
    slug = row.get("company_slug")
    problem = stake_problem(row, today)
    if problem is not None:
        logger.warning(
            "trillion club: stake dropped (company=%s investee=%r id=%s): %s",
            slug, row.get("investee_name"), row.get("id"), problem,
        )
        return None
    verified_on = _iso_date(row.get("verified_on"))
    is_stale = (today - date.fromisoformat(verified_on)).days > _STAKE_STALE_AFTER_DAYS
    raw_symbol = _text(row.get("investee_us_symbol"))
    if raw_symbol is not None and not (
        raw_symbol.isascii() and _STAKE_SYMBOL_RE.fullmatch(raw_symbol.upper())
    ):
        # The stake is still a sourced fact; only its link to the stock screen goes.
        logger.warning(
            "trillion club: stake (company=%s investee=%r id=%s) has a malformed "
            "investee_us_symbol %r — shown without a symbol",
            slug, row.get("investee_name"), row.get("id"), raw_symbol[:40],
        )
        raw_symbol = None
    cusip = _text(row.get("investee_cusip"))
    sort_order = row.get("sort_order")
    sort_order = sort_order if isinstance(sort_order, int) and not isinstance(sort_order, bool) else 0
    investee = _text(row.get("investee_name"))
    return _Stake(
        company_slug=slug,
        material=row.get("material") is True,
        sort_key=(sort_order, investee.casefold()),
        raw_symbol=raw_symbol,
        cusip=cusip.upper() if cusip else None,
        response=_ClubStake(
            investee_name=investee,
            kind=row.get("kind"),
            symbol=raw_symbol.upper() if raw_symbol else None,
            local_listing=_text(row.get("local_listing")),
            ownership_pct=_finite(row.get("ownership_pct")),
            ownership_basis=_text(row.get("ownership_basis")),
            disclosed_value=_positive(row.get("disclosed_value_usd")),
            value_basis=row.get("value_basis"),
            as_of=_iso_date(row.get("as_of")),
            source_title=_text(row.get("source_title")),
            source_url=_text(row.get("source_url")),
            tied_to_deal=row.get("tied_to_deal") is True,
            listed_since=_iso_date(row.get("listed_since")),
            background=_text(row.get("background")),
            verified_on=verified_on,
            is_stale=is_stale,
        ),
    )


def _tagged_stakes(stakes: Sequence[_Stake], tags: Mapping[str, str], own_slug: str) -> List[ClubStakeResponse]:
    out = []
    for stake in sorted(stakes, key=lambda s: s.sort_key):
        response = stake.response.model_copy()
        response.club_member_slug = _tag_for(stake.raw_symbol, tags, own_slug)
        out.append(response)
    return out


# ── Filings: holdings + changes ──────────────────────────────────────────────────────


def _parse_index_row(row: Mapping[str, Any]) -> Optional[_IndexRow]:
    cik = row.get("cik")
    key = _period_key(row.get("period"))
    if not isinstance(cik, str) or not _CIK_RE.fullmatch(cik) or key is None:
        logger.warning(
            "trillion club: filing row skipped (cik=%r period=%r) — malformed key",
            cik, row.get("period"),
        )
        return None
    return _IndexRow(
        cik=cik,
        period=row["period"].strip(),
        key=key,
        period_end=_iso_date(row.get("period_end")),
        filed_on=_iso_date(row.get("filed_on")),
        total_value=_non_negative(row.get("total_value")),
        position_count=_count(row.get("position_count")),
    )


def _latest_index_row(rows: Iterable[_IndexRow], ciks: Sequence[str]) -> Optional[_IndexRow]:
    wanted = set(ciks)
    candidates = [r for r in rows if r.cik in wanted]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (r.key, r.filed_on or ""))


def _previous_index_row(rows: Iterable[_IndexRow], ciks: Sequence[str], latest: _IndexRow) -> Optional[_IndexRow]:
    """The stored row of the quarter ADJACENT before ``latest`` (same CIK preferred), or None."""
    year, quarter = latest.key
    wanted_key = (year - 1, 4) if quarter == 1 else (year, quarter - 1)
    wanted = set(ciks)
    candidates = [r for r in rows if r.cik in wanted and r.key == wanted_key]
    if not candidates:
        return None
    return max(candidates, key=lambda r: (r.cik == latest.cik, r.filed_on or ""))


def _parse_filing(row: Mapping[str, Any]) -> Optional[_Filing]:
    index = _parse_index_row(row)
    if index is None:
        return None
    if index.period_end is None:
        logger.warning("trillion club: filing %s %s has no period_end — skipped", index.cik, index.period)
        return None
    what = f"filing {index.cik} {index.period}"
    return _Filing(
        cik=index.cik,
        period=index.period,
        period_end=index.period_end,
        filed_on=index.filed_on,
        amended_on=_iso_date(row.get("amended_on")),
        accessions=_string_list(row.get("accessions")),
        total_value=index.total_value,
        position_count=index.position_count,
        holdings=[h for h in _json(row.get("holdings"), list, what=f"{what} holdings") if isinstance(h, dict)],
        changes=_json(row.get("changes"), dict, what=f"{what} changes"),
    )


def _row_key(item: Mapping[str, Any]) -> Optional[str]:
    """CUSIP first (the builder keys by it); the symbol only when a row has no CUSIP."""
    return _text(item.get("cusip")) or _norm_symbol(item.get("symbol"))


def _comparison(filing: _Filing) -> Optional[str]:
    # isinstance first: `["quarter"] in frozenset` raises TypeError (unhashable), and one
    # malformed JSONB field must cost this card its comparison, not hide the section.
    value = filing.changes.get("comparison")
    return value if isinstance(value, str) and value in _COMPARISONS else None


def _compared(filing: _Filing) -> bool:
    """Did this quarter's build actually compare share counts with the previous quarter?

    Only ``quarter`` did. A ``gap`` (the previous filing on file is not the adjacent
    quarter) is NOT diffed by the builder — rows [] and every count 0 — so, exactly like a
    first filing, its holdings carry no change and its counts are unknown. Reading it as a
    comparison labelled every holding "unchanged" and told iOS "no share-count changes"."""
    return _comparison(filing) == COMPARISON_QUARTER


def _change_rows(filing: _Filing) -> List[Dict[str, Any]]:
    rows = filing.changes.get("rows")
    if rows is None:
        return []
    if not isinstance(rows, list):
        logger.warning("trillion club: filing %s %s changes.rows is not a list", filing.cik, filing.period)
        return []
    return [r for r in rows if isinstance(r, dict)]


@dataclass
class _Holding:
    key: Optional[str]
    routable_symbol: Optional[str]
    response: ClubHoldingResponse
    cusip: Optional[str] = None        # upper-cased; links an on_13f_note stake
    norm_symbol: Optional[str] = None  # the filed symbol, routable or not (same link)


def _holdings(filing: _Filing, tags: Mapping[str, str], own_slug: str) -> List[_Holding]:
    """Every holding of the filing, largest value first, with its read-time club tag and
    its share change (``unchanged`` when the quarter compares and no change row names it;
    None on a first filing or a gap, which compared nothing)."""
    compared = _compared(filing)
    changes_by_key: Dict[str, Dict[str, Any]] = {}
    for row in _change_rows(filing):
        key = _row_key(row)
        if key is not None:
            changes_by_key.setdefault(key, row)

    out: List[_Holding] = []
    seen: set = set()
    for item in filing.holdings:
        key = _row_key(item)
        if key is not None and key in seen:
            logger.warning("trillion club: filing %s %s repeats holding %s — first kept", filing.cik, filing.period, key)
            continue
        raw_symbol = _text(item.get("symbol"))
        routable = raw_symbol.upper() if raw_symbol and item.get("routable") is True else None
        name = _text(item.get("name")) or (raw_symbol.upper() if raw_symbol else None) or key
        if name is None:
            logger.warning("trillion club: filing %s %s has a holding with no name, symbol or CUSIP — skipped", filing.cik, filing.period)
            continue
        if key is not None:
            seen.add(key)
        weight = _finite(item.get("weight"))
        if weight is not None and not 0 <= weight <= 1:
            logger.warning("trillion club: filing %s %s holding %s weight %r outside [0, 1] — dropped", filing.cik, filing.period, key, weight)
            weight = None
        change: Optional[str] = None
        newly_listed = False
        if compared:
            row = changes_by_key.get(key) if key is not None else None
            if row is None:
                change = CHANGE_UNCHANGED
            elif isinstance(row.get("change"), str) and row.get("change") in CHANGE_KINDS:
                change = row.get("change")
                newly_listed = row.get("newly_listed") is True
        is_small = item.get("is_small")
        cusip = _text(item.get("cusip"))
        out.append(_Holding(
            key=key,
            cusip=cusip.upper() if cusip else None,
            norm_symbol=_norm_symbol(raw_symbol),
            routable_symbol=routable,
            response=ClubHoldingResponse(
                name=name,
                symbol=routable,
                weight=weight,
                shares=_non_negative(item.get("shares")),
                value=_non_negative(item.get("value")),
                change=change,
                newly_listed=newly_listed,
                is_small=is_small if isinstance(is_small, bool) else (weight is not None and weight < 0.01),
                club_member_slug=_tag_for(raw_symbol, tags, own_slug),
                sector=_text(item.get("sector")),
            ),
        ))
    out.sort(key=lambda h: (
        h.response.value is None,
        -(h.response.value or 0.0),
        -(h.response.weight or 0.0),
        h.response.name.casefold(),
    ))
    return out


def _previous_routability(prev: Optional[_Filing]) -> Dict[str, Optional[str]]:
    """row key → the routable symbol the PREVIOUS quarter's own build stored for it (None
    when that build marked it not routable). Only these may vouch for a row with no
    current holding."""
    out: Dict[str, Optional[str]] = {}
    if prev is None:
        return out
    for item in prev.holdings:
        key = _row_key(item)
        if key is None or key in out:
            continue
        raw_symbol = _text(item.get("symbol"))
        out[key] = raw_symbol.upper() if raw_symbol and item.get("routable") is True else None
    return out


def _changes(
    filing: _Filing,
    holdings: Sequence[_Holding],
    previous: Optional[Mapping[str, Optional[str]]] = None,
) -> List[ClubChangeResponse]:
    """The quarter's changed rows (never ``unchanged``), in a stable display order.

    A row for a CURRENT holding carries that holding's routable symbol (None when it is
    not routable, same as the holdings list). A row with no current holding — a
    no-longer-reported one, usually a merger or a delisting — carries a symbol only when
    something VOUCHES that it routes: a ``routable`` flag on the row itself, else the
    previous quarter's stored holding (``previous``, from ``_previous_routability``).
    Nothing vouching means None: iOS makes every change row with a symbol a button into the
    stock screen, and a dead or recycled ticker there opens the wrong company."""
    if not _compared(filing):
        return []
    previous = previous or {}
    current = {h.key: h for h in holdings if h.key is not None}
    out: List[ClubChangeResponse] = []
    for row in _change_rows(filing):
        change = row.get("change")
        # isinstance first: a list/dict `change` would raise TypeError in the dict lookup and
        # 503 the whole detail instead of dropping this one row.
        if not isinstance(change, str) or change not in _CHANGE_ORDER:
            if change != CHANGE_UNCHANGED:
                logger.warning(
                    "trillion club: filing %s %s change row %s has unknown change %r — dropped",
                    filing.cik, filing.period, _row_key(row), change,
                )
            continue
        key = _row_key(row)
        holding = current.get(key) if key is not None else None
        raw_symbol = _text(row.get("symbol"))
        stamped = row.get("routable")
        if holding is not None:
            symbol = holding.routable_symbol
        elif isinstance(stamped, bool):
            symbol = raw_symbol.upper() if stamped and raw_symbol else None
        else:
            symbol = previous.get(key) if key is not None else None
        name = (
            _text(row.get("name"))
            or (holding.response.name if holding else None)
            or (raw_symbol.upper() if raw_symbol else None)    # a label, never a link
            or key
        )
        if name is None:
            continue
        weight = _finite(row.get("weight"))
        out.append(ClubChangeResponse(
            name=name,
            symbol=symbol,
            change=change,
            newly_listed=row.get("newly_listed") is True,
            shares=_non_negative(row.get("shares")),
            prev_shares=_non_negative(row.get("prev_shares")),
            share_change=_finite(row.get("share_change")),
            value=_non_negative(row.get("value")),
            weight=weight if weight is not None and 0 <= weight <= 1 else None,
        ))
    out.sort(key=lambda c: (_CHANGE_ORDER[c.change], -(c.value or 0.0), c.name.casefold()))
    return out


def _change_counts(filing: _Filing) -> Optional[ClubChangeCountsResponse]:
    """The per-outcome counts, or None when unknown. Missing counts are NOT zeros: "0 newly
    reported" is a claim about the filing, and iOS hides the change line on None. Only a
    ``quarter`` comparison counted anything — a first filing's or a gap's all-zero counts
    are a comparison that never happened."""
    if not _compared(filing):
        return None
    counts = filing.changes.get("counts")
    if not isinstance(counts, dict) or not counts:
        logger.warning(
            "trillion club: filing %s %s has a comparison but no counts — change line hidden",
            filing.cik, filing.period,
        )
        return None
    return ClubChangeCountsResponse(**{
        name: _count(counts.get(name)) or 0 for name in ClubChangeCountsResponse.model_fields
    })


def _next_due_on_or_after(rules: Any, key: Tuple[int, int], today: date) -> Optional[date]:
    """The due date of the quarter right AFTER ``key`` — only while it is still ahead.

    iOS prints it as "Next 13F due by <date>" and may not read the device clock, so a date
    already past is never sent. It is also never rolled forward to a LATER quarter: while
    the Q3 filing is overdue, "Next 13F due by Feb 16" (Q4's deadline) would read as
    "nothing is due before February". Once the deadline passes the card shows no due date;
    the overdue filing is the notice's job (latest_not_in after the 5-business-day grace,
    then no_newer_filing), not this line's."""
    year, quarter = _next_quarter(*key)
    due = rules.sec_13f_due_date(year, quarter)
    return due if due >= today else None


def _filing_notice(company: _Company, filing: _Filing, today: date) -> Tuple[Optional[str], Optional[str]]:
    """(next_due, notice) for a 13F card. Staleness notices outrank the others.

    The calendar comes from the core rules module; if it fails, the card keeps its data and
    loses only these two fields (logged), and the amended / first-filing notices — which
    need no calendar — still apply."""
    next_due: Optional[str] = None
    notice: Optional[str] = None
    key = _period_key(filing.period)
    try:
        rules = _rules()
        if key is not None:
            due = _next_due_on_or_after(rules, key, today)
            next_due = due.isoformat() if due is not None else None
            if rules.no_newer_filing(filing.period, today):
                notice = NOTICE_NO_NEWER_FILING
            elif rules.latest_not_in_due(filing.period, today):
                notice = NOTICE_LATEST_NOT_IN
    except Exception as exc:  # noqa: BLE001 — a calendar bug costs two fields, not the card
        logger.warning(
            "trillion club: 13F calendar unavailable for %s %s: %s: %s",
            company.slug, filing.period, type(exc).__name__, exc,
        )
        next_due = None
    if notice is None:
        amended = len(filing.accessions) > 1 or (
            filing.amended_on is not None and filing.amended_on != filing.filed_on
        )
        if amended:
            notice = NOTICE_AMENDED
        elif _comparison(filing) == COMPARISON_FIRST_FILING:
            notice = NOTICE_FIRST_FILING
    return next_due, notice


# ── Card assembly ────────────────────────────────────────────────────────────────────


def _card(
    company: _Company,
    *,
    filing: Optional[_Filing],
    holdings: Sequence[_Holding],
    stakes: Sequence[_Stake],
    whale_id: Optional[str],
    tags: Mapping[str, str],
    today: date,
) -> TrillionClubCompanyResponse:
    # The FREE Home card lists material stakes, but never an `on_13f_note`: a note is about
    # one 13F holding, and a note naming a holding outside the top 3 (NVIDIA's Nokia) would
    # hand the card's free reader a name the Free detail withholds. Notes live in the detail,
    # next to their holding (and the Free detail drops the ones naming withheld holdings).
    material = [s for s in stakes if s.material and s.response.kind != _STAKE_ON_13F_NOTE]
    card = TrillionClubCompanyResponse(
        slug=company.slug,
        name=company.name,
        card_kind=company.card_kind,
        logo_symbol=company.logo_symbol,
        detail_symbol=company.detail_symbol,
        market_cap=company.market_cap,
        market_cap_as_of=company.market_cap_as_of,
        cap_is_manual=company.cap_is_manual,
        stakes=_tagged_stakes(material, tags, company.slug),
        # Every published stake, material or not: the card lists only the material ones, and
        # "+N more in the details" must count what the detail actually lists.
        stake_count=len(stakes),
        whale_id=whale_id if company.card_kind == CARD_WHALE_LINK else None,
        reviewed_on=company.reviewed_on,
    )
    if filing is not None:
        next_due, notice = _filing_notice(company, filing, today)
        card.period = filing.period
        card.period_end = filing.period_end
        card.filed_on = filing.filed_on
        card.amended_on = filing.amended_on
        card.next_due = next_due
        card.position_count = filing.position_count if filing.position_count is not None else len(holdings)
        card.total_value = filing.total_value
        card.top_holdings = [h.response for h in holdings[:_TOP_HOLDINGS]]
        card.change_counts = _change_counts(filing)
        card.comparison = _comparison(filing)
        prev = filing.changes.get("prev_period")
        card.prev_period = prev.strip() if _period_key(prev) is not None else None
        card.notice = notice
    return card


def _has_item(card: TrillionClubCompanyResponse, filing: Optional[_Filing]) -> bool:
    """A member earns a Home card with ≥1 thing to show: a 13F filing, a material stake,
    or (Berkshire) a resolvable whale profile to link to."""
    return filing is not None or bool(card.stakes) or (
        card.card_kind == CARD_WHALE_LINK and card.whale_id is not None
    )


def _brief(company: _Company) -> ClubMemberBriefResponse:
    return ClubMemberBriefResponse(slug=company.slug, name=company.name)


def _link_notes(stakes: Sequence[_Stake], holdings: Sequence[_Holding]) -> None:
    """Record, off the wire, which holding each ``on_13f_note`` stake names — by CUSIP
    first, else by the filed symbol (routable or not). The Free redaction drops a note whose
    holding it withholds. Edits this build's own freshly parsed stakes only."""
    by_cusip: Dict[str, _Holding] = {}
    by_symbol: Dict[str, _Holding] = {}
    for holding in holdings:
        if holding.cusip:
            by_cusip.setdefault(holding.cusip, holding)
        if holding.norm_symbol:
            by_symbol.setdefault(holding.norm_symbol, holding)
    for stake in stakes:
        if stake.response.kind != _STAKE_ON_13F_NOTE:
            continue
        holding = by_cusip.get(stake.cusip) if stake.cusip else None
        if holding is None and stake.raw_symbol:
            holding = by_symbol.get(_norm_symbol(stake.raw_symbol))
        if holding is not None:
            stake.response._names_holding = (holding.response.name, holding.response.symbol)


# ── Redaction (pure, copy-on-read) ───────────────────────────────────────────────────

# Every field of TrillionClubDetailResponse and how a locked detail treats it. A field added
# to the model without a decision here fails test_trillion_club_service (the gate must be
# decided, not inherited by accident).
REDACTION_POLICY = {
    "company": "kept; its top_holdings cut to the free holdings, its stakes filtered like "
               "`stakes` and its stake_count recounted",
    "holdings": "top 3 only",
    "changes": "kept (changed rows only — never unchanged)",
    "stakes": "kept, except an on_13f_note that names a withheld holding",
    "history": "emptied",
    "is_locked": "True",
    "tier_required": "the plan that unlocks",
    "locked_holdings_count": "holdings withheld",
    "locked_history_count": "earlier quarters withheld (0: no History lock on iOS)",
    "other_members": "kept",
}

# The change kinds whose rows ship in `changes`: a holding with one of them is named on a
# locked payload anyway, so a note about it may stay.
_SHOWN_CHANGES = frozenset(_CHANGE_ORDER)


def _names_withheld_holding(
    stake: ClubStakeResponse,
    withheld_ids: set,
    withheld_symbols: set,
) -> bool:
    """Does this stake name a holding the locked payload withholds? Only an
    ``on_13f_note`` can (it is a note ABOUT a 13F holding): by the build-time link when the
    stake came from the service, else by its own symbol."""
    if stake.kind != _STAKE_ON_13F_NOTE:
        return False
    link = getattr(stake, "_names_holding", None)
    if link is not None and tuple(link) in withheld_ids:
        return True
    symbol = _norm_symbol(stake.symbol)
    return symbol is not None and symbol in withheld_symbols


def redact_trillion_club_detail(
    detail: TrillionClubDetailResponse, tier_required: str
) -> TrillionClubDetailResponse:
    """Return a NEW, locked detail for a Free caller. Never mutates ``detail``.

    ⚠️ ``detail`` is normally the object in the class-level cache, shared by every caller
    for 10 minutes: editing it in place would strip Pro users' holdings until the next
    rebuild. Everything is deep-copied first.

    Free keeps the top 3 holdings, the quarter's CHANGED rows (the owner's decision: the
    latest changes are free) and the stakes. What it cannot rebuild is the full list:
    ``unchanged`` rows are never sent, so a holding outside the top 3 that did not change
    appears nowhere in the locked payload — not as a holding, a change, a club tag, a
    top-holdings entry, or an ``on_13f_note`` stake naming it (by CUSIP or symbol; those
    notes are dropped from ``stakes`` AND ``company.stakes``, and ``company.stake_count``
    is recounted to what is left).

    The FREE Home card (the group) never lists an ``on_13f_note`` at all — see ``_card``.

    ``is_locked`` is True for EVERY locked-tier caller (it says "this caller is on Free");
    ``locked_holdings_count`` / ``locked_history_count`` say how much was withheld and are
    0 when nothing was — iOS keys the "+N more holdings" row and the History lock off the
    counts, not the flag, so a filer with no earlier quarter sells no empty History.
    """
    copy = detail.model_copy(deep=True)
    kept = copy.holdings[:_FREE_HOLDINGS]
    kept_ids = {(h.name, h.symbol) for h in kept}
    withheld = [
        h for h in copy.holdings[_FREE_HOLDINGS:]
        if (h.name, h.symbol) not in kept_ids and h.change not in _SHOWN_CHANGES
    ]
    withheld_ids = {(h.name, h.symbol) for h in withheld}
    withheld_symbols = {s for s in (_norm_symbol(h.symbol) for h in withheld) if s}

    def _free(stakes: Sequence[ClubStakeResponse]) -> List[ClubStakeResponse]:
        return [s for s in stakes if not _names_withheld_holding(s, withheld_ids, withheld_symbols)]

    stakes = _free(copy.stakes)
    company = copy.company
    company.top_holdings = [h for h in company.top_holdings if (h.name, h.symbol) in kept_ids]
    company.stakes = _free(company.stakes)
    if company.stake_count is not None:
        company.stake_count = len(stakes)
    return TrillionClubDetailResponse(
        company=company,
        holdings=kept,
        changes=[c for c in copy.changes if c.change != CHANGE_UNCHANGED],
        stakes=stakes,
        history=[],
        is_locked=True,
        tier_required=tier_required,
        locked_holdings_count=max(0, len(copy.holdings) - len(kept)),
        locked_history_count=len(copy.history),
        other_members=copy.other_members,
    )


# ── Service ──────────────────────────────────────────────────────────────────────────


class TrillionClubService:
    """Reads the Trillion-Dollar Club tables and assembles the Home group + the detail."""

    # Class-level so every request shares them (one uvicorn worker on Railway).
    _group_cache: Dict[str, Tuple[float, TrillionClubGroupResponse]] = {}
    _detail_cache: Dict[str, Tuple[float, TrillionClubDetailResponse]] = {}
    _inflight: Dict[str, asyncio.Future] = {}
    # `invalidate()` stamps this instead of deleting the group: a rebuild that then times
    # out can still serve the previous group rather than an empty section.
    _invalidated_at: float = 0.0

    # ── Public API ──────────────────────────────────────────────────────────────────

    async def get_group_guarded(self) -> TrillionClubGroupResponse:
        """The Home branch. NEVER raises (the dashboard gather has no return_exceptions).

        Waits up to 6 s on the shared build; ``shield`` keeps a timeout from cancelling it
        (it finishes and warms the cache for the next request). On a timeout or a read
        error it serves the last good group (≤ 24 h old), else an empty one.

        The build is an explicit Task whose exception is ALWAYS retrieved by a done-callback:
        once ``wait_for`` gives up, ``shield`` drops the callback that would have read it,
        so a build that then failed (a slow Supabase that errors — an outage's usual shape)
        was logged by asyncio as "Task exception was never retrieved" at ERROR, per Home
        request. The late failure is logged once, at WARNING, instead."""
        try:
            if not settings.TRILLION_CLUB_ENABLED:
                return TrillionClubGroupResponse()
            build = asyncio.ensure_future(self.get_group())
            abandoned: List[bool] = []
            build.add_done_callback(lambda task: _settle_group_build(task, abandoned))
            try:
                return await asyncio.wait_for(asyncio.shield(build), _GROUP_TIMEOUT_SECONDS)
            except BaseException:
                if not build.done():
                    abandoned.append(True)     # nobody is waiting on it any more
                raise
        except Exception as exc:  # noqa: BLE001 — the section degrades, Home never fails
            cached = self._group_cache.get(_GROUP_KEY)
            if cached is not None and time.time() - cached[0] < _GROUP_FALLBACK_MAX_AGE_SECONDS:
                logger.warning(
                    "trillion club group unavailable (%s: %s); serving last good group (age=%.0fs)",
                    type(exc).__name__, exc, time.time() - cached[0],
                )
                return cached[1]
            logger.warning(
                "trillion club group unavailable and no recent good group (%s: %s); section hidden",
                type(exc).__name__, exc,
            )
            return TrillionClubGroupResponse()

    async def get_group(self) -> TrillionClubGroupResponse:
        """The Home group: cache-aside (10 min) + shielded in-flight dedup. RAISES on a read
        error (never cached); ``get_group_guarded`` is the caller that must not raise."""
        if not settings.TRILLION_CLUB_ENABLED:
            return TrillionClubGroupResponse()
        cached = self._group_cache.get(_GROUP_KEY)
        if cached is not None and self._is_fresh(cached[0]):
            return cached[1]
        return await self._dedup(_GROUP_KEY, self._build_group, self._group_cache)

    async def get_detail(self, slug: str) -> Optional[TrillionClubDetailResponse]:
        """A company's drill-down, UNREDACTED (the endpoint redacts per caller).

        None when the feature is off, the slug is malformed or unknown, the company is not
        a published member, or membership is stale (the section is hidden then, so there is
        nothing to drill into). None is not cached, so a just-published company appears on
        the next request. RAISES on a read error."""
        if not settings.TRILLION_CLUB_ENABLED:
            return None
        if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
            return None
        key = f"detail:{slug}"
        cached = self._detail_cache.get(key)
        if cached is not None and self._is_fresh(cached[0]):
            return cached[1]
        return await self._dedup(key, lambda: self._build_detail(slug), self._detail_cache)

    @classmethod
    def invalidate(cls) -> None:
        """Called by the jobs after they write. The group is marked stale (still usable as
        the guard's fallback); every detail is dropped. This process only — the other
        instances, if any, catch up within one TTL."""
        cls._invalidated_at = time.time()
        cls._detail_cache.clear()

    # ── Cache plumbing ──────────────────────────────────────────────────────────────

    def _is_fresh(self, stored_at: float) -> bool:
        return (time.time() - stored_at) < _CACHE_TTL_SECONDS and stored_at > self._invalidated_at

    async def _dedup(self, key, build, cache: Dict[str, Tuple[float, Any]]):
        """Run ``build`` once per key however many callers arrive; cache a non-None result.

        Joiners wait under ``asyncio.shield`` so a joiner that gives up (its own timeout, a
        disconnect) cannot cancel the shared future and make the leader's ``set_result``
        raise. The leader settles the future on EVERY exit — ``BaseException`` included,
        since CancelledError skips ``except Exception`` and would strand the joiners."""
        inflight = self._inflight.get(key)
        if inflight is not None:
            return await asyncio.shield(inflight)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[key] = fut
        # Stamped with the build's START: a build that read before a job's `invalidate()`
        # and finished after it must not be cached as fresh for another ten minutes.
        started = time.time()
        try:
            result = await build()
            if result is not None:
                cache[key] = (started, result)
            if not fut.done():
                fut.set_result(result)
            return result
        except BaseException as exc:
            if not fut.done():
                handed = (
                    TrillionClubBuildCancelled(f"trillion club build {key} was cancelled")
                    if isinstance(exc, asyncio.CancelledError) else exc
                )
                fut.set_exception(handed)
                # The leader re-raises and its caller logs; a future nobody joined must not
                # also print "exception was never retrieved" at garbage collection.
                fut.exception()
            raise
        finally:
            self._inflight.pop(key, None)

    # ── Reads (each RAISES on a Supabase error) ────────────────────────────────────

    async def _read_companies(self) -> List[_Company]:
        sb = get_supabase()
        rows = (await sb_exec(
            sb.table(_COMPANIES_TABLE).select("*").eq("published", True).limit(_MAX_COMPANY_ROWS)
        )).data or []
        companies: List[_Company] = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                logger.warning("trillion club: non-object company row %r skipped", type(row).__name__)
                continue
            company = _parse_company(row)
            if company is None or company.slug in seen:
                continue
            seen.add(company.slug)
            companies.append(company)
        return companies

    async def _read_stakes(self, slugs: Sequence[str], today: date) -> List[_Stake]:
        if not slugs:
            return []
        sb = get_supabase()
        rows = (await sb_exec(
            sb.table(_STAKES_TABLE)
            .select("*")
            .in_("company_slug", list(slugs))
            .eq("published", True)
            .eq("source_confidence", "primary")
            .order("sort_order")
            .limit(_MAX_STAKE_ROWS)
        )).data or []
        wanted = set(slugs)
        out = []
        for row in rows:
            if isinstance(row, dict) and row.get("company_slug") in wanted:
                stake = _parse_stake(row, today)
                if stake is not None:
                    out.append(stake)
        return out

    async def _read_filing_index(self, ciks: Sequence[str]) -> List[_IndexRow]:
        """Light columns of every stored quarter (latest selection + Pro history)."""
        if not ciks:
            return []
        sb = get_supabase()
        rows = (await sb_exec(
            sb.table(_FILINGS_TABLE)
            .select("cik,period,period_end,filed_on,total_value,position_count")
            .in_("cik", list(ciks))
            .order("period", desc=True)
            .limit(_MAX_FILING_INDEX_ROWS)
        )).data or []
        return [r for r in (_parse_index_row(row) for row in rows if isinstance(row, dict)) if r]

    async def _read_filings(self, pairs: Sequence[Tuple[str, str]]) -> Dict[Tuple[str, str], _Filing]:
        """The full rows for exactly these (cik, period) pairs — one round trip."""
        if not pairs:
            return {}
        ciks = sorted({c for c, _ in pairs})
        periods = sorted({p for _, p in pairs})
        sb = get_supabase()
        rows = (await sb_exec(
            sb.table(_FILINGS_TABLE)
            .select("*")
            .in_("cik", ciks)
            .in_("period", periods)
            # PK (cik, period): the cross product is an exact upper bound on the rows.
            .limit(len(ciks) * len(periods))
        )).data or []
        wanted = set(pairs)
        out: Dict[Tuple[str, str], _Filing] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            filing = _parse_filing(row)
            if filing is not None and (filing.cik, filing.period) in wanted:
                out[(filing.cik, filing.period)] = filing
        return out

    async def _read_whale_ids(self, ciks: Sequence[str]) -> Dict[str, str]:
        """padded CIK → whales.id (unique on cik: `uq_whales_cik`)."""
        if not ciks:
            return {}
        variants = sorted({v for c in ciks for v in (c, c.lstrip("0") or "0")})
        sb = get_supabase()
        rows = (await sb_exec(
            sb.table(_WHALES_TABLE).select("id,cik").in_("cik", variants).limit(_MAX_WHALE_ROWS)
        )).data or []
        out: Dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            cik = _normalise_cik(row.get("cik"))
            whale_id = row.get("id")
            if cik and whale_id is not None and str(whale_id).strip():
                out.setdefault(cik, str(whale_id))
        return out

    @staticmethod
    async def _all(*coros):
        """Run independent reads together; re-raise the first failure (never swallow one)."""
        results = await asyncio.gather(*coros, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return results

    # ── Builds ──────────────────────────────────────────────────────────────────────

    async def _members(self, today: date) -> Optional[List[_Company]]:
        """Published EFFECTIVE members, or None when membership is stale (section hidden).

        A member whose OWN evidence is stale (``_member_problem``) is left out entirely —
        no card, not in "Also in the club", no detail (404), no club tag — with a WARNING
        naming it; the rest of the section still shows."""
        companies = await self._read_companies()
        now = _now_utc()
        problem = _membership_problem(companies, now)
        if problem is not None:
            logger.warning("trillion club section hidden: %s", problem)
            return None
        members: List[_Company] = []
        for company in companies:
            if not company.is_member:
                continue
            stale = _member_problem(company, now, today)
            if stale is not None:
                logger.warning("trillion club: %s left out of the club — %s", company.slug, stale)
                continue
            members.append(company)
        return members

    @staticmethod
    def _filers(companies: Iterable[_Company]) -> List[_Company]:
        # `use_13f` is the owner's opt-in; joining the club never turns ingestion on.
        return [c for c in companies if c.use_13f and c.card_kind == CARD_THIRTEEN_F and c.ciks]

    @staticmethod
    def _links(companies: Iterable[_Company]) -> List[_Company]:
        return [c for c in companies if c.card_kind == CARD_WHALE_LINK and c.link_whale and c.ciks]

    async def _build_group(self) -> TrillionClubGroupResponse:
        today = _today_et()
        members = await self._members(today)
        if not members:
            return TrillionClubGroupResponse()
        tags = _member_tag_map(members)
        filers = self._filers(members)
        links = self._links(members)
        stakes, index, whale_ids = await self._all(
            self._read_stakes([m.slug for m in members], today),
            self._read_filing_index(sorted({cik for c in filers for cik in c.ciks})),
            self._read_whale_ids(sorted({cik for c in links for cik in c.ciks})),
        )
        latest = {c.slug: _latest_index_row(index, c.ciks) for c in filers}
        filings = await self._read_filings([(r.cik, r.period) for r in latest.values() if r])

        stakes_by_slug: Dict[str, List[_Stake]] = {}
        for stake in stakes:
            stakes_by_slug.setdefault(stake.company_slug, []).append(stake)

        cards: List[Tuple[_Company, TrillionClubCompanyResponse]] = []
        also: List[_Company] = []
        for company in members:
            row = latest.get(company.slug)
            filing = filings.get((row.cik, row.period)) if row else None
            if row is not None and filing is None:
                logger.warning(
                    "trillion club: %s latest filing %s %s listed but unreadable — card built without it",
                    company.slug, row.cik, row.period,
                )
            holdings = _holdings(filing, tags, company.slug) if filing else []
            card = _card(
                company,
                filing=filing,
                holdings=holdings,
                stakes=stakes_by_slug.get(company.slug, []),
                whale_id=_whale_id_for(company, whale_ids),
                tags=tags,
                today=today,
            )
            if _has_item(card, filing):
                cards.append((company, card))
            else:
                also.append(company)

        cards.sort(key=lambda pair: pair[0].cap_sort_key)
        also.sort(key=lambda c: c.cap_sort_key)
        return TrillionClubGroupResponse(
            companies=[card for _, card in cards],
            also_in_club=[_brief(c) for c in also],
        )

    async def _build_detail(self, slug: str) -> Optional[TrillionClubDetailResponse]:
        today = _today_et()
        members = await self._members(today)
        if not members:
            return None
        company = next((c for c in members if c.slug == slug), None)
        if company is None:
            return None
        tags = _member_tag_map(members)
        filer = bool(self._filers([company]))
        link = bool(self._links([company]))
        stakes, index, whale_ids = await self._all(
            self._read_stakes([company.slug], today),
            self._read_filing_index(company.ciks if filer else []),
            self._read_whale_ids(company.ciks if link else []),
        )
        latest = _latest_index_row(index, company.ciks) if filer else None
        # The adjacent previous quarter, when stored: its own build's `routable` is what may
        # vouch for a no-longer-reported row's symbol. Same round trip as the latest row.
        previous = _previous_index_row(index, company.ciks, latest) if latest else None
        filing = prev_filing = None
        if latest is not None:
            pairs = [(latest.cik, latest.period)] + ([(previous.cik, previous.period)] if previous else [])
            read = await self._read_filings(pairs)
            filing = read.get((latest.cik, latest.period))
            prev_filing = read.get((previous.cik, previous.period)) if previous else None
            if filing is None:
                logger.warning(
                    "trillion club: %s latest filing %s %s listed but unreadable — detail built without it",
                    slug, latest.cik, latest.period,
                )
        if filing is not None and prev_filing is not None:
            said = filing.changes.get("prev_period")
            if isinstance(said, str) and _period_key(said) is not None and said.strip() != prev_filing.period:
                logger.warning(
                    "trillion club: %s %s compares with %s, not the stored %s — previous quarter ignored",
                    slug, filing.period, said.strip(), prev_filing.period,
                )
                prev_filing = None
        holdings = _holdings(filing, tags, company.slug) if filing else []
        _link_notes(stakes, holdings)
        card = _card(
            company,
            filing=filing,
            holdings=holdings,
            stakes=stakes,
            whale_id=_whale_id_for(company, whale_ids),
            tags=tags,
            today=today,
        )
        return TrillionClubDetailResponse(
            company=card,
            holdings=[h.response for h in holdings],
            changes=_changes(filing, holdings, _previous_routability(prev_filing)) if filing else [],
            stakes=_tagged_stakes(stakes, tags, company.slug),
            history=_history(index, company.ciks, exclude=latest),
            # EVERY other published member, carded or not (the Home group's also_in_club is
            # the no-card subset). Deliberately so — do not narrow it here.
            other_members=[_brief(c) for c in sorted(members, key=lambda c: c.cap_sort_key) if c.slug != slug],
        )


def _settle_group_build(task: "asyncio.Future", abandoned: Sequence[bool]) -> None:
    """Done-callback of ``get_group_guarded``'s build: retrieve its exception, always.

    While the guard is still waiting, the exception reaches it through ``shield`` and is
    logged there — so it is only read here. Once the guard has stopped waiting
    (``abandoned``), this is the only place the failure can be reported."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None and abandoned:
        logger.warning(
            "trillion club group build failed after the Home guard stopped waiting "
            "(%s: %s) — the next request rebuilds",
            type(exc).__name__, exc,
        )


def _whale_id_for(company: _Company, whale_ids: Mapping[str, str]) -> Optional[str]:
    if company.card_kind != CARD_WHALE_LINK or not company.link_whale:
        return None
    for cik in company.ciks:
        if cik in whale_ids:
            return whale_ids[cik]
    # The card is still built when it has material stakes; whale_id goes out null, so iOS
    # shows neither the "Open profile" button nor the profile explainer.
    logger.warning(
        "trillion club: %s links to a whale profile but no whales row has CIK %s — "
        "whale_id sent as null (no profile link)",
        company.slug, ",".join(company.ciks) or "(none)",
    )
    return None


def _history(index: Sequence[_IndexRow], ciks: Sequence[str], *, exclude: Optional[_IndexRow]) -> List[ClubHistoryPointResponse]:
    """Earlier quarters, newest first, one per period (the latest one is the card itself)."""
    wanted = set(ciks)
    by_period: Dict[str, _IndexRow] = {}
    for row in sorted(index, key=lambda r: (r.key, r.filed_on or ""), reverse=True):
        if row.cik not in wanted or row.period_end is None:
            continue
        if exclude is not None and row.period == exclude.period:
            continue
        by_period.setdefault(row.period, row)
    points = sorted(by_period.values(), key=lambda r: r.key, reverse=True)[:_MAX_HISTORY_POINTS]
    return [
        ClubHistoryPointResponse(
            period=r.period,
            period_end=r.period_end,
            total_value=r.total_value,
            position_count=r.position_count,
        )
        for r in points
    ]


def invalidate() -> None:
    """Module-level alias for ``TrillionClubService.invalidate`` (what the jobs call)."""
    TrillionClubService.invalidate()


_service: Optional[TrillionClubService] = None


def get_trillion_club_service() -> TrillionClubService:
    global _service
    if _service is None:
        _service = TrillionClubService()
    return _service
