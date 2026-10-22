"""SEC XBRL concept facts per company, behind a two-tier cache-aside.

Feeds ``services/_buyback_verification.py``: FMP's ``commonStockRepurchased`` is sometimes
employee tax withholding or a preferred redemption (INTC's $181M and $617M are RSU
withholding), and the company's own filing says which. The rule needs up to four us-gaap
concepts per company (``SEC_BUYBACK_TAGS``); this service fetches them from SEC EDGAR
(``integrations/sec_edgar.py``) and caches them.

Tiers (template: ``profit_power_service.py``):
  1. In memory, key ``(cik, tag)``, ``_MEMORY_TTL_SECONDS`` (5 min), bounded.
  2. Supabase ``public.sec_concept_cache`` (migration 202), PK ``(cik, concept)``, one
     query per call. A row is FRESH when it was fetched less than 24 h ago AND it is not a
     coverage miss: with ``newest_period_end`` given, a ``found`` row whose ``newest_end``
     trails it by more than 7 days, fetched more than 1 h ago, is refetched (a 10-Q was
     probably filed since). A fresh ``found = false`` row is a fresh "never used".
  3. SEC, only for the tags with no fresh entry, concurrently. A success is written to both
     tiers, the payload TRIMMED to the 10-Q / 10-K USD rows and their start, end, val,
     filed, form keys (what ``_buyback_verification.facts_from_concept`` reads). A 404 is
     ``found = false``, facts NULL — "the company never used the concept", never a zero.

When a tag's fetch FAILS, its Supabase row is served if it is at most 30 days old and the
answer is marked ``stale``; with no such row the tag's SEC exception is re-raised. A blank
``SEC_EDGAR_USER_AGENT`` raises ``SECConfigException`` before ANY cache read, so turning the
setting off never quietly keeps serving old rows.

Supabase is synchronous: every call runs in a worker thread, never on the event loop. Writes
are best-effort — a missing table (202 not applied yet) only logs.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from app.database import get_supabase
from app.integrations.sec_edgar import (
    SECConfigException,
    SECEdgarClient,
    SECException,
    get_sec_edgar_client,
    normalize_cik,
    user_agent_configured,
)
from app.utils.inflight import fail_shared_future

logger = logging.getLogger(__name__)

__all__ = ["SecConcepts", "SecConceptService", "get_sec_concept_service"]

_TABLE = "sec_concept_cache"

#: The forms ``_buyback_verification`` reads (pinned equal by the tests). An 8-K or a 20-F
#: fact is not a statement FMP's quarterly row is built from.
_STATEMENT_FORMS = frozenset({"10-Q", "10-K", "10-Q/A", "10-K/A", "10-QT", "10-KT"})
_KEPT_KEYS = ("start", "end", "val", "filed", "form")

_MEMORY_TTL_SECONDS = 300
#: A stale answer (SEC failing, a Supabase row served) is held in memory only this long:
#: enough to stop every request of an outage re-reading Supabase and re-trying SEC, short
#: enough that a recovered SEC is asked again within a minute.
_STALE_MEMORY_TTL_SECONDS = 60
_CACHE_MAX_ENTRIES = 1024

FRESH_FOR = timedelta(hours=24)
STALE_SERVE_MAX_AGE = timedelta(days=30)
COVERAGE_GAP = timedelta(days=7)
COVERAGE_RECHECK_AFTER = timedelta(hours=1)
#: A ``fetched_at`` further in the future than this is a corrupt row, never "fresh forever".
_CLOCK_SKEW = timedelta(minutes=5)

# An XBRL element name. It goes into the SEC URL path, so nothing else may.
_TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,199}$")


@dataclass(frozen=True)
class SecConcepts:
    """One company's answer. ``concepts`` maps every requested tag to its trimmed
    ``{"units": {"USD": [rows]}}`` payload, or None when the company never used the tag (a
    404). Payloads are shared with the cache: treat them as read-only."""

    cik: str
    concepts: Dict[str, Optional[Dict[str, Any]]]
    # True when at least one tag came from a Supabase row past its freshness because SEC
    # failed.
    stale: bool


@dataclass(frozen=True)
class _Fact:
    """One (cik, tag) answer, as held in either tier."""

    found: bool
    facts: Optional[Dict[str, Any]]
    newest_end: Optional[date]
    fetched_at: datetime


# ── Tier 1: in memory ──────────────────────────────────────────────────
# (cik, tag) → (monotonic stored-at, fact, stale)
_cache: Dict[Tuple[str, str], Tuple[float, _Fact, bool]] = {}

# ── In-flight dedup: (cik, sorted tags being loaded) → shared future ───
_inflight: Dict[Tuple[str, Tuple[str, ...]], asyncio.Future] = {}

# Best-effort Supabase writes still running (kept referenced so none is collected early).
_pending_writes: Set["asyncio.Future[None]"] = set()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _cache_set(cik: str, tag: str, fact: _Fact, *, stale: bool = False) -> None:
    key = (cik, tag)
    _cache.pop(key, None)
    _cache[key] = (time.monotonic(), fact, stale)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(old, None)


def _cache_get(
    cik: str, tag: str, now: datetime, period_end: Optional[date]
) -> Optional[Tuple[_Fact, bool]]:
    entry = _cache.get((cik, tag))
    if entry is None:
        return None
    stored_at, fact, stale = entry
    ttl = _STALE_MEMORY_TTL_SECONDS if stale else _MEMORY_TTL_SECONDS
    if time.monotonic() - stored_at > ttl:
        _cache.pop((cik, tag), None)
        return None
    if not stale and not _is_fresh(fact, now, period_end):
        return None
    return fact, stale


# ── Pure helpers ───────────────────────────────────────────────────────


def _parse_day(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_fresh(fact: _Fact, now: datetime, period_end: Optional[date]) -> bool:
    age = now - fact.fetched_at
    if age >= FRESH_FOR or age < -_CLOCK_SKEW:
        return False
    if (
        period_end is not None
        and fact.found
        # No statement row at all trails every period: refetch it like an old one.
        and (fact.newest_end is None or fact.newest_end < period_end - COVERAGE_GAP)
        and age > COVERAGE_RECHECK_AFTER
    ):
        return False
    return True


def _servable_when_stale(fact: _Fact, now: datetime) -> bool:
    age = now - fact.fetched_at
    return -_CLOCK_SKEW <= age <= STALE_SERVE_MAX_AGE


def _trim_payload(payload: Mapping[str, Any]) -> Tuple[Dict[str, Any], Optional[date]]:
    """``payload`` cut to ``{"units": {"USD": [statement rows, five keys each]}}`` and the
    latest period end among those rows. A payload with no USD list keeps no USD key, so
    ``facts_from_concept`` still reads it as "no USD unit" (None), not as an empty list."""
    units = payload.get("units")
    usd = units.get("USD") if isinstance(units, Mapping) else None
    if not isinstance(usd, list):
        return {"units": {}}, None
    rows: List[Dict[str, Any]] = []
    newest: Optional[date] = None
    for row in usd:
        if not isinstance(row, Mapping) or row.get("form") not in _STATEMENT_FORMS:
            continue
        val = row.get("val")
        if isinstance(val, float) and not math.isfinite(val):
            continue  # JSONB refuses NaN / inf, and the rule drops them anyway
        rows.append({k: row[k] for k in _KEPT_KEYS if k in row})
        end = _parse_day(row.get("end"))
        if end is not None and (newest is None or end > newest):
            newest = end
    return {"units": {"USD": rows}}, newest


def _validate_tags(tags: Iterable[str]) -> List[str]:
    if isinstance(tags, (str, bytes)):
        raise ValueError(f"tags must be a sequence of concept names, not a string: {tags!r}")
    out: List[str] = []
    for tag in tags:
        if not isinstance(tag, str) or not _TAG_RE.match(tag):
            raise ValueError(f"invalid XBRL concept name: {tag!r}")
        if tag not in out:
            out.append(tag)
    if not out:
        raise ValueError("no XBRL concept names requested")
    return out


def _parse_period_end(value: Any) -> Optional[date]:
    if value is None:
        return None
    parsed = _parse_day(value)
    if parsed is None:
        logger.warning(
            "[sec-concept-bad-period-end] newest_period_end=%r is not a date — the coverage "
            "check is skipped for this call",
            value,
        )
    return parsed


def _user_agent_set() -> bool:
    # The client's own predicate, so the two can never disagree: a value the client would
    # refuse (blank, or not printable ASCII) turns the check off here too, before any read.
    return user_agent_configured()


# ── Service ────────────────────────────────────────────────────────────


class SecConceptService:
    def __init__(
        self, *, client: Optional[SECEdgarClient] = None, supabase: Any = None
    ) -> None:
        # Both resolved lazily: building the singleton must never touch Supabase or SEC.
        self._client = client
        self._supabase = supabase

    def _sec(self) -> SECEdgarClient:
        return self._client if self._client is not None else get_sec_edgar_client()

    def _db(self) -> Any:
        return self._supabase if self._supabase is not None else get_supabase()

    async def get_concepts(
        self,
        cik: str,
        tags: Sequence[str],
        *,
        newest_period_end: Optional[str] = None,
    ) -> SecConcepts:
        """``tags`` for the company ``cik`` (10 digits; normalised again here).

        Raises:
            ValueError: an invalid CIK or concept name, or no tags.
            SECConfigException: ``SEC_EDGAR_USER_AGENT`` is blank (before any cache read).
            SECRateLimitException / SECUnavailableException / SECException: a tag's fetch
                failed and it has no Supabase row of at most 30 days to fall back on.
        """
        cik10 = normalize_cik(cik)
        if cik10 is None:
            raise ValueError(f"invalid CIK: {cik!r}")
        wanted = _validate_tags(tags)
        # FIRST, before any cache read: a blank setting turns the check off, and must never
        # keep serving rows written while it was on.
        if not _user_agent_set():
            raise SECConfigException(
                "SEC EDGAR: SEC_EDGAR_USER_AGENT is not set or is not printable ASCII — no "
                f"concepts for CIK{cik10}"
            )
        period_end = _parse_period_end(newest_period_end)
        now = _utcnow()

        answers: Dict[str, Tuple[_Fact, bool]] = {}
        missing: List[str] = []
        for tag in wanted:
            hit = _cache_get(cik10, tag, now, period_end)
            if hit is None:
                missing.append(tag)
            else:
                answers[tag] = hit

        if missing:
            key = (cik10, tuple(sorted(missing)))
            inflight = _inflight.get(key)
            if inflight is not None:
                logger.info("SEC concepts in-flight JOIN for CIK%s %s", cik10, list(key[1]))
                loaded = await asyncio.shield(inflight)
            else:
                loop = asyncio.get_running_loop()
                future: asyncio.Future = loop.create_future()
                _inflight[key] = future
                try:
                    loaded = await self._load(cik10, missing, period_end)
                    if not future.done():
                        future.set_result(loaded)
                except asyncio.CancelledError:
                    # A BaseException: it skips `except Exception`, and an unresolved future
                    # would hang every joiner for the life of the process.
                    fail_shared_future(
                        future, RuntimeError("in-flight SEC concept fetch was cancelled")
                    )
                    raise
                except Exception as e:
                    fail_shared_future(future, e)
                    raise
                finally:
                    if _inflight.get(key) is future:
                        _inflight.pop(key, None)
            answers.update(loaded)

        return SecConcepts(
            cik=cik10,
            concepts={
                tag: (answers[tag][0].facts if answers[tag][0].found else None)
                for tag in wanted
            },
            stale=any(answers[tag][1] for tag in wanted),
        )

    async def get_cached_concepts(
        self, cik: str, tags: Sequence[str]
    ) -> Optional[SecConcepts]:
        """What the two tiers hold for ``tags`` — never a call to SEC.

        For a caller whose wait for `get_concepts` ran out (SEC slow, not failed: the
        stale fallback there only runs once a fetch has FAILED, which a 20 s read timeout
        reaches after the caller's own bound). Every tag needs an answer at most 30 days
        old (`STALE_SERVE_MAX_AGE`), else None. Always ``stale=True``: the rows may predate
        the newest filing, and the caller decides what that leaves unchecked.

        Raises:
            ValueError: an invalid CIK or concept name, or no tags.
            SECConfigException: ``SEC_EDGAR_USER_AGENT`` is unusable (before any read).
        """
        cik10 = normalize_cik(cik)
        if cik10 is None:
            raise ValueError(f"invalid CIK: {cik!r}")
        wanted = _validate_tags(tags)
        if not _user_agent_set():
            raise SECConfigException(
                f"SEC EDGAR: SEC_EDGAR_USER_AGENT is not set — no concepts for CIK{cik10}"
            )
        now = _utcnow()
        answers: Dict[str, _Fact] = {}
        for tag in wanted:
            entry = _cache.get((cik10, tag))
            if entry is None:
                continue
            stored_at, fact, stale = entry
            ttl = _STALE_MEMORY_TTL_SECONDS if stale else _MEMORY_TTL_SECONDS
            if time.monotonic() - stored_at <= ttl and _servable_when_stale(fact, now):
                answers[tag] = fact
        missing = [tag for tag in wanted if tag not in answers]
        if missing:
            rows = await asyncio.to_thread(self._read_rows, cik10, missing)
            for tag in missing:
                row = rows.get(tag)
                if row is not None and _servable_when_stale(row, now):
                    answers[tag] = row
        if len(answers) != len(wanted):
            logger.info(
                "[sec-concept-cache-only-miss] CIK%s: no cached answer of at most %d days "
                "for %s",
                cik10, STALE_SERVE_MAX_AGE.days,
                [tag for tag in wanted if tag not in answers],
            )
            return None
        return SecConcepts(
            cik=cik10,
            concepts={
                tag: (answers[tag].facts if answers[tag].found else None) for tag in wanted
            },
            stale=True,
        )

    async def _load(
        self, cik: str, tags: List[str], period_end: Optional[date]
    ) -> Dict[str, Tuple[_Fact, bool]]:
        """The leader's work for ``tags``: tier 2, then SEC for whatever is not fresh."""
        now = _utcnow()
        rows = await asyncio.to_thread(self._read_rows, cik, tags)

        out: Dict[str, Tuple[_Fact, bool]] = {}
        to_fetch: List[str] = []
        for tag in tags:
            row = rows.get(tag)
            if row is not None and _is_fresh(row, now, period_end):
                out[tag] = (row, False)
                _cache_set(cik, tag, row)
            else:
                to_fetch.append(tag)
        logger.info(
            "SEC concepts CIK%s: %d fresh from Supabase, fetching %s",
            cik, len(out), to_fetch or "nothing",
        )
        if not to_fetch:
            return out

        client = self._sec()
        results = await asyncio.gather(
            *(client.get_company_concept(cik, tag) for tag in to_fetch),
            return_exceptions=True,
        )
        fetched_at = _utcnow()
        fetched: Dict[str, _Fact] = {}
        failures: List[Tuple[str, Exception]] = []
        for tag, result in zip(to_fetch, results):
            if isinstance(result, BaseException) and not isinstance(result, Exception):
                raise result  # a cancelled child: the leader's cancel arm settles joiners
            if isinstance(result, Exception):
                failures.append((tag, result))
                continue
            if result is None:
                fact = _Fact(found=False, facts=None, newest_end=None, fetched_at=fetched_at)
            elif isinstance(result, Mapping):
                trimmed, newest_end = _trim_payload(result)
                fact = _Fact(True, trimmed, newest_end, fetched_at)
            else:  # the client returns a dict or None; anything else is a contract break
                failures.append((tag, TypeError(
                    f"SEC client returned {type(result).__name__} for CIK{cik} {tag}"
                )))
                continue
            fetched[tag] = fact
            out[tag] = (fact, False)
            _cache_set(cik, tag, fact)

        if fetched:
            self._spawn_write(cik, fetched)

        unresolved: List[Tuple[str, Exception]] = []
        for tag, exc in failures:
            if not isinstance(exc, SECException):
                # Not an SEC outcome — a bug or a contract break. Loud, with the stack; the
                # stale fallback below still applies, so the chart keeps a verified answer.
                logger.error(
                    "[sec-concept-unexpected] CIK%s %s: %s: %s",
                    cik, tag, type(exc).__name__, exc, exc_info=exc,
                )
            row = rows.get(tag)
            if row is not None and _servable_when_stale(row, now):
                logger.warning(
                    "[sec-concept-stale-served] CIK%s %s: SEC failed (%s: %s) — serving the "
                    "Supabase row fetched %s",
                    cik, tag, type(exc).__name__, exc, row.fetched_at.isoformat(),
                )
                out[tag] = (row, True)
                _cache_set(cik, tag, row, stale=True)
            else:
                unresolved.append((tag, exc))

        if unresolved:
            for tag, exc in unresolved:
                logger.warning(
                    "[sec-concept-fetch-failed] CIK%s %s: no Supabase row of at most %d days "
                    "to fall back on — %s: %s",
                    cik, tag, STALE_SERVE_MAX_AGE.days, type(exc).__name__, exc,
                )
            raise unresolved[0][1]
        return out

    # ── Supabase (synchronous: always called through a worker thread) ──────

    def _read_rows(self, cik: str, tags: List[str]) -> Dict[str, _Fact]:
        """The usable rows for ``tags``, fresh or not (the stale fallback needs the old
        ones). A failed read is logged and treated as "no rows"."""
        try:
            resp = (
                self._db()
                .table(_TABLE)
                .select("concept, found, facts, newest_end, fetched_at")
                .eq("cik", cik)
                .in_("concept", list(tags))
                .execute()
            )
        except Exception as e:
            logger.warning(
                "[sec-concept-cache-read-failed] CIK%s %s: %s: %s",
                cik, ",".join(tags), type(e).__name__, e,
            )
            return {}

        out: Dict[str, _Fact] = {}
        for row in getattr(resp, "data", None) or []:
            if not isinstance(row, Mapping):
                continue
            tag = row.get("concept")
            if tag not in tags:
                continue
            found = row.get("found")
            facts = row.get("facts")
            fetched_at = _parse_timestamp(row.get("fetched_at"))
            if (
                not isinstance(found, bool)
                or fetched_at is None
                or (found and not isinstance(facts, Mapping))
            ):
                logger.warning(
                    "[sec-concept-cache-row-invalid] CIK%s %s: found=%r facts=%s "
                    "fetched_at=%r — ignored",
                    cik, tag, found, type(facts).__name__, row.get("fetched_at"),
                )
                continue
            out[tag] = _Fact(
                found=found,
                facts=dict(facts) if found else None,
                newest_end=_parse_day(row.get("newest_end")) if found else None,
                fetched_at=fetched_at,
            )
        return out

    def _write_rows_safe(self, cik: str, rows: List[Dict[str, Any]]) -> None:
        tags = ",".join(str(r.get("concept")) for r in rows)
        try:
            self._db().table(_TABLE).upsert(rows, on_conflict="cik,concept").execute()
        except Exception as e:
            logger.warning(
                "[sec-concept-cache-write-failed] CIK%s %s: %s: %s",
                cik, tags, type(e).__name__, e,
            )

    def _spawn_write(self, cik: str, facts: Dict[str, _Fact]) -> None:
        """Best-effort write-through, off the event loop and off the caller's critical
        path. The future is kept referenced until done."""
        rows = [
            {
                "cik": cik,
                "concept": tag,
                "found": fact.found,
                "facts": fact.facts if fact.found else None,
                "newest_end": fact.newest_end.isoformat() if fact.newest_end else None,
                "fetched_at": fact.fetched_at.isoformat(),
            }
            for tag, fact in facts.items()
        ]
        fut = asyncio.get_running_loop().run_in_executor(
            None, self._write_rows_safe, cik, rows
        )
        _pending_writes.add(fut)
        fut.add_done_callback(_pending_writes.discard)


# ── Singleton ──────────────────────────────────────────────────────────

_service: Optional[SecConceptService] = None


def get_sec_concept_service() -> SecConceptService:
    global _service
    if _service is None:
        _service = SecConceptService()
    return _service
