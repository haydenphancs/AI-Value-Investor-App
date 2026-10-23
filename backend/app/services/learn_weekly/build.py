"""The BUILD step: one piece with a chosen topic → a validated draft → a queued narration job
(plan "Wiser weekly content engine" §1.4 / §1.6 / §1.7; contract 4 of wave B).

`build_piece(piece, now=)` runs once per scheduler tick for each piece whose topic is chosen and
whose build time has come. Exactly-once is the piece LEASE, not a day claim (Regenerate / Next
topic must be able to rebuild the same day): `ledger.acquire_lease` moves `planned → building` in
one compare-and-swap and hands this run a fresh `generation_id`; every later write is fenced on
that generation, so a run that lost its lease can never overwrite the one that took it over
(first write wins).

    planned ─acquire─▶ building ─┬─ accepted ──────────▶ drafted ─enqueue─▶ media_pending
                                 │                         └─ enqueue keeps failing ─▶ failed + alert
                                 │                            (MAX_ENQUEUE_ATTEMPTS, or past the narration cut-off)
                                 ├─ content_rejected ──▶ retry (cap LEARN_MAX_CONTENT_REJECTIONS → skipped + alert)
                                 ├─ writer_failed ─────▶ retry (cap LEARN_MAX_WRITER_FAILURES → failed + alert)
                                 ├─ thin fact sheet ───▶ the NEXT candidate + alert (none left → skipped + alert)
                                 ├─ fact sheet / catalog unavailable ─▶ retry, no cap (the catch-up cut-off decides)
                                 └─ lease_lost ────────▶ stop (the new owner carries on)

A thin sheet is told apart from an unavailable one: `FactSheetShortfall` means every SEC read
ANSWERED and the candidate still has fewer than `MIN_SHEET_FACTS` usable facts (no 10-K yet, a
subject SEC does not know) — retrying it for two days would only lose the week, so the build moves the
topic on. An SEC read that failed (even one tag of one company) is an outage and retries.

Title / slug uniqueness is read from the table the unique indexes guard (`money_move_articles`, every
row — the served catalog keeps only the newest weekly pieces) plus every piece still on its way there
(`IN_FLIGHT_STATUSES`). The served catalog still feeds the related-article tiles.

The fact sheet (plan §1.6) is assembled here, from three sources only — the licence boundary:
`sec_facts.company_summary` + `annual_facts` for each subject CIK (SEC EDGAR, public domain), the
chosen candidate's verbatim 8-K peg facts, and the verified case library's primers for the theme
(`fact_sheet.build_fact_sheet` vets, renumbers and caps them). Nothing FMP-derived reaches it.

RETRY, and a known conflict with the ledger (reported, not papered over): contract 4 sends a
retryable outcome "back to planned", but the wave-A ledger only lets a TERMINAL piece re-enter
`planned` (`ledger._ONLY_FROM[planned]`, pinned by its own test). Until that rule admits `building →
planned`, a retry STAYS `building` with its lease cleared — `build_piece` accepts such a piece
(`BUILD_FROM_STATUSES`), but the scheduler only offers `planned` pieces, so it would wait for its
hold to expire. That case is logged at ERROR every time. The moment the ledger allows the move,
`_requeue` takes it (it asks `ledger.transition_problem` first) and the flow matches contract 4.

Never raises for an expected failure (it is recorded on the piece and logged with its ids); a
ledger error that leaves nothing written is logged and returns None (the lease then simply runs
out). CancelledError propagates.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import logging
import re
import uuid
from datetime import date, datetime, timezone
from types import ModuleType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.config import settings
from app.database import get_supabase
from app.integrations.sec_edgar import SECConfigException, get_sec_edgar_client
from app.utils.postgrest_paging import fetch_all_rows
from app.services.learn_weekly import article_schema as A
from app.services.learn_weekly import case_library
from app.services.learn_weekly import contracts as C
from app.services.learn_weekly import fact_sheet as fs
from app.services.learn_weekly import ledger
from app.services.learn_weekly import prompts as P
from app.services.learn_weekly import schedule, sec_facts
from app.services.learn_weekly import writer as W
from app.services.learn_weekly.errors import LearnWeeklyError

logger = logging.getLogger(__name__)

#: The build lease. Refreshed before EVERY model call (`writer.write_article`'s `before_call`), so
#: it must outlast one call plus the ledger round trips around it — not the whole generation.
LEASE_SECONDS = 15 * 60
#: Statuses `build_piece` writes from. `building` is admitted for a piece whose previous run
#: crashed or retried (its lease dead or cleared) — `acquire_lease` refuses one whose lease is live.
BUILD_FROM_STATUSES: Tuple[str, ...] = (C.PLANNED, C.BUILDING)
#: A sheet with fewer usable facts than this cannot carry 1,400+ grounded words: retry (SEC may be
#: partly down) until the catch-up cut-off, never write from it.
MIN_SHEET_FACTS = 5
#: The main subjects a candidate may name (contract 1).
MAX_SUBJECTS = 3
#: `progress` schema version (the resume key also pins the prompt, the sheet and the review round).
PROGRESS_VERSION = 1
STAGE = "build"
WAITING_FOR_TOPIC = "waiting_for_topic"
#: Failed narration-enqueue attempts of ONE draft before the piece ends `failed` (counted durably in
#: `build_attempts`, which every new draft resets to 0). A deterministic refusal — an engine or voice
#: the narrator does not know, a block it cannot voice — never heals on its own, and the scheduler
#: re-offers a `drafted` piece every 5-minute tick until its hold ends (~750 ticks); six ticks are
#: enough to ride out a ledger blip.
MAX_ENQUEUE_ATTEMPTS = 6
#: Pieces whose title / slug is not in `money_move_articles` yet but will be: everything between the
#: build queue and the publish. `published` is read from the table itself (the unique indexes live
#: there, and an unbounded oldest-first ledger read of years of published pieces would starve this
#: week's), terminal ones never reach it.
IN_FLIGHT_STATUSES: Tuple[str, ...] = tuple(
    s for s in C.PIECE_STATUSES if s not in C.TERMINAL_STATUSES and s != C.PUBLISHED)
#: The in-flight read's size. A handful of pieces are in flight at once (each expires at its hold), so
#: a full read means something is badly wrong — and a truncated list could let a duplicate through:
#: fail closed (`catalog_unavailable`) rather than guess.
_IN_FLIGHT_LIMIT = 1000
ARTICLES_TABLE = "money_move_articles"
#: Pieces whose enqueue failure was alerted while its `last_error` could NOT be stored (the ledger
#: write raised): without this, the stored-value comparison would alert again on every tick.
_ENQUEUE_ALERTED: Dict[str, str] = {}
_ENQUEUE_ALERTED_MAX = 256
#: The most `topic.build_drops` entries kept (newest last).
_BUILD_DROPS_MAX = 20

_CIK_RE = re.compile(r"[0-9]{1,10}")
_SUBJECT_NAME_MAX = 120
_TICKER_RE = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")


class FactSheetUnavailable(LearnWeeklyError):
    """The fact sheet could not be assembled now (SEC down or unconfigured, or a thin sheet while some
    SEC read failed). Retryable until the catch-up cut-off; never a content verdict, never counted
    against a cap."""


class FactSheetShortfall(FactSheetUnavailable):
    """Every SEC read ANSWERED and the sheet still has fewer than `MIN_SHEET_FACTS` usable facts: a
    property of the candidate (no 10-K yet, a subject SEC does not know), not an outage. The build
    moves the topic to the next candidate (`_HeldBuild.thin_sheet`) instead of retrying a sheet that
    will be just as thin at the cut-off. A subclass, so a caller that only knows `FactSheetUnavailable`
    (the preview script) still catches it."""

    def __init__(self, message: Any = "", *, usable: int = 0, **ids: Any) -> None:
        super().__init__(message, **ids)
        self.usable = usable


class _SECReadRecorder:
    """The SEC client for ONE fact-sheet assembly, recording every read that raised.

    `sec_facts.annual_facts` swallows a PARTIAL tag failure (it drops that metric, logs a WARNING and
    does not cache), so a thin sheet from a partial SEC outage looks exactly like a thin sheet from a
    company with no 10-K. This wrapper, passed through the public `client=` door, is how the build
    tells them apart without reaching into sec_facts' privates. Every coroutine method is recorded,
    not just the two sec_facts uses today: an unrecorded swallowed failure would be read as a
    deterministic shortfall (fail open).

    Zero recorded reads means the answer came from sec_facts' cache, which only holds reads where
    every tag answered — or from joining a concurrent load of the same CIK, which only another build
    could start, and the scheduler builds one piece at a time. The real client is created lazily, so a
    fully cached assembly never touches it."""

    def __init__(self) -> None:
        self._client: Any = None
        self.reads = 0
        self.failures: List[str] = []

    def _real(self) -> Any:
        if self._client is None:
            self._client = get_sec_edgar_client()
        return self._client

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            # Never delegate privates: an instance whose __init__ did not run (a copy) would
            # otherwise recurse through `self._client` forever.
            raise AttributeError(name)
        attr = getattr(self._real(), name)
        if not inspect.iscoroutinefunction(attr):
            return attr

        async def recorded(*args: Any, **kwargs: Any) -> Any:
            self.reads += 1
            try:
                return await attr(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.failures.append(f"{name}: {type(e).__name__}")
                raise

        return recorded


# ── the two collaborators built in parallel (stubbed in tests through these getters) ─────────


def _narration() -> ModuleType:
    from app.services.learn_weekly import narration

    return narration


def _alerts() -> ModuleType:
    from app.services.learn_weekly import alerts

    return alerts


async def _notify_terminal(piece: Mapping[str, Any], *, status: str, reason: str,
                           stage: str = STAGE) -> None:
    try:
        await _alerts().notify_terminal(piece, status=status, reason=reason, stage=stage)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # alerts never raise by contract; this is the belt to that brace
        logger.error("learn build: alerting %s/%s for piece %s failed (%s: %s)", status, reason,
                     piece.get("id"), type(e).__name__, e, exc_info=True)


async def _notify(text: str) -> None:
    try:
        await _alerts().notify(text, loud=True)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("learn build: alert failed (%s: %s): %s", type(e).__name__, e, text,
                     exc_info=True)


# ── the chosen topic (contract 1) ────────────────────────────────────────────────────────────


def chosen_candidate(topic: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(the chosen candidate, None), (None, WAITING_FOR_TOPIC) while no pick exists yet, or
    (None, why the stored topic is unusable). Reads only the fields the build needs."""
    if topic is None:
        return None, WAITING_FOR_TOPIC
    if not isinstance(topic, Mapping):
        return None, f"topic is {type(topic).__name__}, not an object"
    if topic.get("version") != 1:
        return None, f"topic version {topic.get('version')!r} is not 1"
    idx = topic.get("chosen_index")
    if idx is None:
        return None, WAITING_FOR_TOPIC
    if isinstance(idx, bool) or not isinstance(idx, int):
        return None, f"chosen_index {idx!r} is not an integer"
    candidates = topic.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None, "topic has no candidates"
    if not 0 <= idx < len(candidates):
        return None, f"chosen_index {idx} outside the {len(candidates)} candidates"
    cand = candidates[idx]
    if not isinstance(cand, Mapping):
        return None, f"candidate {idx} is not an object"
    if cand.get("category") not in C.CATEGORIES:
        return None, f"candidate {idx} category {cand.get('category')!r} not in {C.CATEGORIES}"
    if not isinstance(cand.get("subjects", []), list) or not isinstance(cand.get("peg_facts", []), list):
        return None, f"candidate {idx} subjects / peg_facts are not lists"
    return dict(cand), None


def _cik10(value: Any) -> Optional[str]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        value = str(value)
    if isinstance(value, str) and _CIK_RE.fullmatch(value.strip()) and int(value) > 0:
        return value.strip().zfill(10)
    return None


def candidate_subjects(candidate: Mapping[str, Any]) -> List[C.Subject]:
    """The candidate's main subjects (≤ `MAX_SUBJECTS`), cleaned: a name is required (SEC's name,
    ≤ 120 chars), a CIK is kept only when it is a real one (zero-padded), a ticker only when it
    looks like one; duplicates (by CIK, else by name) are dropped. Every drop is logged."""
    out: List[C.Subject] = []
    seen = set()
    for k, raw in enumerate(candidate.get("subjects") or []):
        if not isinstance(raw, Mapping):
            logger.warning("learn build: subject %d is not an object — dropped", k)
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > _SUBJECT_NAME_MAX:
            logger.warning("learn build: subject %d has no usable name — dropped", k)
            continue
        cik = _cik10(raw.get("cik"))
        if raw.get("cik") is not None and cik is None:
            logger.warning("learn build: subject %r has an unusable CIK %r — kept without it",
                           name[:60], raw.get("cik"))
        ticker = raw.get("ticker")
        ticker = ticker.strip().upper() if isinstance(ticker, str) else None
        if ticker is not None and not _TICKER_RE.fullmatch(ticker):
            ticker = None
        key = cik or name.strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(C.Subject(name=" ".join(name.split()), cik=cik, ticker=ticker))
    if len(out) > MAX_SUBJECTS:
        logger.warning("learn build: %d subjects — keeping the first %d", len(out), MAX_SUBJECTS)
        out = out[:MAX_SUBJECTS]
    return out


_FACT_KEYS = ("id", "text", "origin", "url", "accession", "entry_id")


def candidate_peg_facts(candidate: Mapping[str, Any]) -> List[C.Fact]:
    """The candidate's verbatim peg facts as `contracts.Fact` (origin `sec_peg` only; anything
    else is dropped with a WARNING — a peg is a filed span, never a library or model sentence).
    `fact_sheet.build_fact_sheet` vets the citation and renumbers them P1…"""
    out: List[C.Fact] = []
    for k, raw in enumerate(candidate.get("peg_facts") or []):
        if not isinstance(raw, Mapping) or raw.get("origin") != C.ORIGIN_SEC_PEG:
            logger.warning("learn build: peg fact %d is not a sec_peg fact object — dropped", k)
            continue
        fields = {key: raw.get(key) for key in _FACT_KEYS}
        if not isinstance(fields["id"], str) or not isinstance(fields["text"], str):
            logger.warning("learn build: peg fact %d has no id/text — dropped", k)
            continue
        out.append(C.Fact(**fields))
    return out


def candidate_theme(candidate: Mapping[str, Any]) -> Optional[str]:
    key = candidate.get("key")
    if candidate.get("kind") == "theme" and isinstance(key, str) and key.strip():
        return key.strip()
    return None


def candidate_label(candidate: Mapping[str, Any]) -> str:
    for key in ("label", "title", "key"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:200]
    return ""


def _cand_key(candidate: Any) -> str:
    """`kind:key` of a candidate (what the topic step's `drops` are keyed by), for the build's drops."""
    if not isinstance(candidate, Mapping):
        return "?"
    return f"{candidate.get('kind')}:{candidate.get('key')}"[:120]


# ── the fact sheet (plan §1.6) ───────────────────────────────────────────────────────────────


async def _subject_facts(subject: C.Subject, sec: _SECReadRecorder) -> List[C.Fact]:
    summary = await sec_facts.company_summary(subject.cik, client=sec)
    if summary is None:
        logger.warning("learn build: SEC has no record of CIK%s (%s) — no filing facts for it",
                       subject.cik, subject.name[:60])
        return []
    return await sec_facts.annual_facts(subject.cik, subject.name or summary.name, client=sec)


def _library_entries(lib: case_library.CaseLibrary, theme: Optional[str],
                     ciks: Sequence[str]) -> List[case_library.LibraryEntry]:
    """The theme's verified PRIMERS plus any primer about one of the subjects, file order, once."""
    wanted = set(ciks)
    out: List[case_library.LibraryEntry] = []
    seen = set()
    for entry in lib.usable_entries(kinds=case_library.PRIMER_KINDS):
        on_theme = theme is not None and theme in entry.themes
        on_subject = any(co.cik in wanted for co in entry.companies if co.cik)
        if (on_theme or on_subject) and entry.id not in seen:
            seen.add(entry.id)
            out.append(entry)
    return out


async def assemble_fact_sheet(kind: str, candidate: Mapping[str, Any], *,
                              library: Optional[case_library.CaseLibrary] = None) -> C.FactSheet:
    """The capped, cited fact sheet for `candidate` (module docstring). Raises
    `FactSheetUnavailable` when any subject's SEC read fails (a sheet missing the main subject's
    figures is a worse article, not a smaller one), or when fewer than `MIN_SHEET_FACTS` survive
    while some SEC read failed; `FactSheetShortfall` when fewer survive and every read answered."""
    subjects = candidate_subjects(candidate)
    peg = candidate_peg_facts(candidate)
    theme = candidate_theme(candidate)
    filers = [s for s in subjects if s.cik]
    sec = _SECReadRecorder()
    results = await asyncio.gather(*(_subject_facts(s, sec) for s in filers), return_exceptions=True)
    xbrl: List[C.Fact] = []
    for subject, result in zip(filers, results):
        if isinstance(result, BaseException):
            if not isinstance(result, Exception):
                raise result               # CancelledError / KeyboardInterrupt: never swallowed
            log = logger.error if isinstance(result, SECConfigException) else logger.warning
            log("learn build: SEC facts for CIK%s (%s) failed: %s: %s", subject.cik,
                subject.name[:60], type(result).__name__, result)
            raise FactSheetUnavailable(
                f"SEC read for CIK{subject.cik} failed: {type(result).__name__}: {result}",
                stage=STAGE) from result
        xbrl.extend(result)
    lib = library if library is not None else await asyncio.to_thread(case_library.get_case_library)
    entries = _library_entries(lib, theme, [s.cik for s in filers if s.cik])
    library_facts = [f for e in entries for f in lib.facts_for(e)]
    sheet = fs.build_fact_sheet(kind, candidate_label(candidate), theme, subjects, peg, xbrl,
                                library_facts, library=lib)
    if len(sheet.facts) < MIN_SHEET_FACTS:
        detail = (f"only {len(sheet.facts)} usable facts (peg {len(peg)}, filings {len(xbrl)}, "
                  f"library {len(library_facts)}; need {MIN_SHEET_FACTS})")
        if sec.failures:
            # annual_facts dropped the metrics whose tag read failed: the sheet may be thin BECAUSE
            # of the outage, so it is retried, never read as the candidate's own shortfall.
            logger.warning("learn build: thin sheet with %d failed SEC reads (%s) — an outage, not a "
                           "shortfall", len(sec.failures), ", ".join(sec.failures[:5]))
            raise FactSheetUnavailable(
                f"{detail}; {len(sec.failures)} SEC reads failed ({', '.join(sec.failures[:3])})",
                stage=STAGE)
        raise FactSheetShortfall(detail, usable=len(sheet.facts), stage=STAGE)
    return sheet


def sheet_sha256(sheet: C.FactSheet) -> str:
    return hashlib.sha256(sheet.canonical_json().encode("utf-8")).hexdigest()


# ── context reads ────────────────────────────────────────────────────────────────────────────


async def _served_catalog() -> List[Dict[str, Any]]:
    """The served Money Moves catalog (the live content service: bundle + published weekly)."""
    from app.services.money_moves_content_service import get_money_moves_content_service

    response = await get_money_moves_content_service().get_money_moves()
    return [a for a in response.articles if isinstance(a, dict)]


def _article_keys_sync() -> List[Dict[str, Any]]:
    """Every `money_move_articles` row's title and slug — paged, because PostgREST clamps one
    response to ~1,000 rows whatever the request asks for, and a silently short list would let a
    duplicate through to the publish."""
    return fetch_all_rows(lambda: get_supabase().table(ARTICLES_TABLE).select("title,slug"),
                          order_by="id", what="learn build: money_move_articles titles")


async def _article_keys() -> Tuple[List[str], List[str]]:
    """(titles, slugs) of EVERY `money_move_articles` row — bundle and weekly, served or held back by
    the archive cap: the table the `lower(title)` and `slug` unique indexes guard. The served catalog
    keeps only the newest `LEARN_SERVED_WEEKLY_MAX` weekly pieces, so after a year or so an older
    title would pass the writer's check and then expire at publish on the index. Uncached, so this
    morning's publish is in it too. Raises on a failed read (the caller fails closed)."""
    rows = await asyncio.to_thread(_article_keys_sync)
    titles = [r["title"] for r in rows if isinstance(r.get("title"), str) and r["title"].strip()]
    slugs = [r["slug"] for r in rows if isinstance(r.get("slug"), str) and r["slug"].strip()]
    if not rows:
        logger.warning("learn build: money_move_articles returned no rows — the title check rests on "
                       "the served catalog and the in-flight pieces alone")
    return titles, slugs


def _merged(*lists: Iterable[str], key=lambda s: s) -> List[str]:
    """The lists concatenated in order, each value once (by `key`)."""
    seen = set()
    out: List[str] = []
    for values in lists:
        for v in values:
            k = key(v)
            if k not in seen:
                seen.add(k)
                out.append(v)
    return out


def _title_fold(title: str) -> str:
    return " ".join(title.split()).casefold()


async def _in_flight(piece_id: Any) -> Optional[Tuple[List[str], List[str]]]:
    """(titles, slugs) of every OTHER piece on its way to `money_move_articles`
    (`IN_FLIGHT_STATUSES`), or None when the read may be incomplete (it filled its limit; logged at
    ERROR — the caller waits rather than guess). Published pieces are not read here: they are in the
    table `_article_keys` reads, and years of them would crowd this week's out of an oldest-first
    read."""
    rows = await ledger.list_pieces(statuses=list(IN_FLIGHT_STATUSES), limit=_IN_FLIGHT_LIMIT)
    if len(rows) >= _IN_FLIGHT_LIMIT:
        logger.error("learn build: %d pieces are in flight (the read's limit) — the title check "
                     "cannot see them all; not building until that is resolved", len(rows))
        return None
    titles: List[str] = []
    slugs: List[str] = []
    for row in rows:
        if str(row.get("id")) == str(piece_id):
            continue
        draft = row.get("draft") if isinstance(row.get("draft"), Mapping) else {}
        title = row.get("title") or draft.get("title")
        slug = row.get("slug") or draft.get("slug")
        if isinstance(title, str) and title.strip():
            titles.append(title)
        if isinstance(slug, str) and slug.strip():
            slugs.append(slug)
    return titles, slugs


def _utcnow() -> datetime:
    """The wall clock (patched in tests). The retry decision reads it, not the tick's `now`: a
    generation runs for minutes, and the tick's instant would make the cut-off check at the end of a
    build the same check `build_piece` already made at its start."""
    return datetime.now(timezone.utc)


def _parse_instant(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def catch_up_cutoff_for(piece: Mapping[str, Any], *,
                        build_seconds: Optional[float] = None) -> Optional[datetime]:
    """The piece's engine cut-off (`schedule.catch_up_cutoff`, worst-case words for its kind) from
    its OWN frozen hold; None when the hold is unreadable (logged — the scheduler's expiry still
    closes such a piece). The engine is `narration.current_engine()` — the one the enqueue will
    really use (a junk setting falls back to a known engine there, so the cut-off must match it).
    `build_seconds` overrides the build budget (0 = the cut-off for a piece already drafted)."""
    hold = _parse_instant(piece.get("hold_until"))
    if hold is None:
        logger.error("learn build: piece %s has an unreadable hold_until %r", piece.get("id"),
                     piece.get("hold_until"))
        return None
    words = C.DEEP_DIVE_WORDS[1] if piece.get("kind") == C.KIND_DEEP_DIVE else C.SLOT_WORDS[1]
    engine = _narration().current_engine()
    if build_seconds is None:
        return schedule.catch_up_cutoff(hold, engine, words)
    return schedule.catch_up_cutoff(hold, engine, words, build_seconds=build_seconds)


def enqueue_cutoff_for(piece: Mapping[str, Any]) -> Optional[datetime]:
    """The last instant a drafted piece's narration may still be queued and reach the owner before
    its hold ends (the catch-up cut-off with no build left to do)."""
    return catch_up_cutoff_for(piece, build_seconds=0)


def published_date_for(piece: Mapping[str, Any]) -> Optional[date]:
    """The ET date the piece publishes on: its frozen `scheduled_publish_at` (the row wins), else
    the date its period key implies. None when neither is readable."""
    at = _parse_instant(piece.get("scheduled_publish_at"))
    if at is not None:
        return schedule.et_date(at)
    try:
        return schedule.et_date(schedule.times_for_period_key(str(piece.get("period_key"))).publish_at)
    except (TypeError, ValueError):
        return None


def _progress_key(sheet_sha: str, review_round: int) -> Dict[str, Any]:
    return {"v": PROGRESS_VERSION, "prompt_version": P.PROMPT_VERSION,
            "fact_sheet_sha256": sheet_sha, "review_round": review_round}


def resume_from(progress: Any, sheet_sha: str, review_round: int) -> Optional[Dict[str, Any]]:
    """The writer's `resume` from a stored `progress`, only when it was written under the same
    progress version, prompt version, fact sheet and review round (a Regenerate bumps the round, so
    an owner-rejected draft is never resumed)."""
    if not isinstance(progress, Mapping) or not progress:
        return None
    key = _progress_key(sheet_sha, review_round)
    if any(progress.get(k) != v for k, v in key.items()):
        logger.info("learn build: stored progress does not match this build (%s) — starting fresh",
                    sorted(k for k, v in key.items() if progress.get(k) != v))
        return None
    return {"outline": progress.get("outline"), "sections": progress.get("sections") or []}


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


# ── one held build ───────────────────────────────────────────────────────────────────────────


class _HeldBuild:
    """One build run that holds the lease of `generation_id` on the piece."""

    def __init__(self, row: Mapping[str, Any], gen: str, now: datetime) -> None:
        self.row = dict(row)
        self.pid = str(row.get("id"))
        self.key = row.get("period_key")
        self.gen = gen
        self.now = now
        self.held = True          # False once a write cleared the lease (or we know we lost it)

    @property
    def fence(self) -> Dict[str, Any]:
        return {"generation_id": self.gen}

    def _tokens(self, spent: int) -> int:
        return _int(self.row.get("tokens_used")) + max(0, int(spent or 0))

    async def write(self, to_status: str, fields: Dict[str, Any],
                    from_statuses: Sequence[str] = (C.BUILDING,)) -> Optional[Dict[str, Any]]:
        row = await ledger.transition(self.pid, from_statuses=list(from_statuses),
                                      to_status=to_status, fields=fields, fence=self.fence)
        if row is None:
            logger.warning("learn build: piece %s (%s) generation %s lost the piece before → %s",
                           self.pid, self.key, self.gen, to_status)
            self.held = False
            return None
        self.row = dict(row)
        if fields.get("lease_until", "kept") is None:
            self.held = False
        return row

    async def terminal(self, status: str, reason: str, *, last_error: str, spent: int = 0,
                       validation: Optional[Dict[str, Any]] = None,
                       extra: Optional[Mapping[str, Any]] = None) -> Optional[Dict[str, Any]]:
        fields: Dict[str, Any] = {"last_error": last_error, "last_stage": STAGE, "lease_until": None,
                                  "tokens_used": self._tokens(spent)}
        if validation is not None:
            fields["validation"] = validation
        if extra:
            fields.update(extra)
        row = await self.write(status, fields)
        if row is not None:
            await _notify_terminal(row, status=status, reason=reason)
        return row

    async def requeue(self, reason: str, *, spent: int = 0, clear_progress: bool = False,
                      validation: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Hand the piece back for the next tick — or skip it when the catch-up cut-off has passed
        (building it later could not leave the owner a review window before the hold ends)."""
        cutoff = catch_up_cutoff_for(self.row)
        if cutoff is not None and max(self.now, _utcnow()) >= cutoff:
            return await self.terminal(C.SKIPPED, "catch_up_cutoff_passed", spent=spent,
                                       last_error=f"catch_up_cutoff_passed after {reason}"[:900],
                                       validation=validation)
        fields: Dict[str, Any] = {"last_error": reason[:900], "last_stage": STAGE, "lease_until": None,
                                  "tokens_used": self._tokens(spent)}
        if clear_progress:
            fields["progress"] = {}
        if validation is not None:
            fields["validation"] = validation
        target = self._back_target(reason)
        row = await self.write(target, fields)
        if row is not None:
            logger.warning("learn build: piece %s (%s) → %s for a retry: %s", self.pid, self.key,
                           target, reason[:300])
        return row

    def _back_target(self, reason: str) -> str:
        """`planned` (contract 4) — or `building` with the lease cleared while the ledger still refuses
        `building → planned` (module docstring), logged at ERROR every time."""
        if ledger.transition_problem([C.BUILDING], C.PLANNED) is None:
            return C.PLANNED
        logger.error(
            "learn build: piece %s (%s) should go back to planned (%s), but the ledger refuses "
            "building → planned; it stays building with its lease cleared. build_piece accepts "
            "it, but the scheduler only offers planned pieces — it will wait for its hold to "
            "expire unless the ledger rule or the scheduler query changes (contract 4)",
            self.pid, self.key, reason[:200])
        return C.BUILDING

    async def thin_sheet(self, shortfall: FactSheetShortfall) -> Optional[Dict[str, Any]]:
        """The chosen candidate's sheet is deterministically too thin (`FactSheetShortfall`): move the
        topic to the next usable candidate and hand the piece back to the build queue with ONE loud
        alert — or end `skipped` (one terminal alert) when no candidate is left. Never a retry of the
        same candidate: it would be just as thin at the cut-off two days later, and the owner has no
        button to switch topics on a `planned` piece. The drop is recorded in the topic JSON
        (`build_drops`) and in `last_error`; the pick stays consistent with `topic.apply_pick` — the
        build's switch is an automatic pick (`auto_picked` true), the owner's own tap (`owner_pick`)
        is kept as the record of what they chose."""
        topic = self.row.get("topic")
        current, problem = chosen_candidate(topic)
        if current is None:
            # The fresh row's topic no longer reads (it did a moment ago): let the next build say so.
            return await self.requeue(f"fact_sheet_unavailable: {shortfall} (topic unreadable: "
                                      f"{problem})")
        idx = topic["chosen_index"]
        candidates = topic["candidates"]
        why = f"thin_fact_sheet: #{idx + 1} {candidate_label(current)!r}: {shortfall}"
        raw_drops = topic.get("build_drops")
        drops = list(raw_drops) if isinstance(raw_drops, list) else []
        drops.append({"index": idx, "key": _cand_key(current), "reason": str(shortfall)[:300]})
        nxt: Optional[Tuple[int, Dict[str, Any]]] = None
        for j in range(idx + 1, len(candidates)):
            cand, bad = chosen_candidate({**topic, "chosen_index": j})
            if cand is not None:
                nxt = (j, cand)
                break
            drops.append({"index": j, "key": _cand_key(candidates[j]),
                          "reason": f"topic_invalid: {bad}"[:300]})
        recorded = copy.deepcopy(dict(topic))
        # Bounded: a topic reopened by rebuilds keeps appending, and the column is one JSONB row.
        recorded["build_drops"] = drops[-_BUILD_DROPS_MAX:]
        logger.warning("learn build: piece %s (%s) %s — %s", self.pid, self.key, why[:300],
                       f"moving to #{nxt[0] + 1}" if nxt else "no candidate left")
        if nxt is None:
            return await self.terminal(
                C.SKIPPED, "thin_fact_sheet", extra={"topic": recorded},
                last_error=f"{why}; no candidate left after #{idx + 1}"[:900])
        cutoff = catch_up_cutoff_for(self.row)
        if cutoff is not None and max(self.now, _utcnow()) >= cutoff:
            return await self.terminal(
                C.SKIPPED, "catch_up_cutoff_passed", extra={"topic": recorded},
                last_error=f"catch_up_cutoff_passed after {why}"[:900])
        j, cand = nxt
        recorded["chosen_index"] = j
        recorded["auto_picked"] = True
        fields: Dict[str, Any] = {
            "topic": recorded, "category": cand["category"],
            "subjects": ledger.subjects_json(candidate_subjects(cand)),
            "last_error": f"{why}; moved to #{j + 1} {candidate_label(cand)!r}"[:900],
            "last_stage": STAGE, "lease_until": None, "progress": {},
        }
        row = await self.write(self._back_target(why), fields)
        if row is not None:
            await _notify(f"🟠 Learn {self.key}: topic #{idx + 1} “{candidate_label(current)[:80]}” "
                          f"has only {shortfall.usable} usable facts in its filings (need "
                          f"{MIN_SHEET_FACTS}) — switched to #{j + 1} "
                          f"“{candidate_label(cand)[:80]}”; it builds on the next tick.")
        return row

    async def save_progress(self, sheet_sha: str, review_round: int,
                            payload: Mapping[str, Any]) -> None:
        progress = {**_progress_key(sheet_sha, review_round), **dict(payload)}
        await self.write(C.BUILDING, {"progress": progress})

    # ── outcomes ─────────────────────────────────────────────────────────────────────────────

    async def capped(self, column: str, cap: int, status: str, reason: str, detail: str, *,
                     spent: int, clear_progress: bool, validation: Optional[Dict[str, Any]]
                     ) -> Optional[Dict[str, Any]]:
        """Count one more `column` (content_rejections / writer_failures), then retry under the
        cap or close the piece at it."""
        bumped = await ledger.bump(self.pid, column, fence=self.fence, statuses=[C.BUILDING])
        if bumped is None:
            logger.warning("learn build: piece %s (%s) generation %s could not count %s — the "
                           "piece moved on", self.pid, self.key, self.gen, column)
            self.held = False
            return None
        self.row = dict(bumped)
        count = _int(bumped.get(column))
        if count >= cap:
            logger.error("learn build: piece %s (%s) reached %s=%d (cap %d) — %s: %s", self.pid,
                         self.key, column, count, cap, status, detail[:300])
            return await self.terminal(status, reason, last_error=f"{reason}: {detail}"[:900],
                                       spent=spent, validation=validation)
        return await self.requeue(f"{column} {count}/{cap}: {detail}", spent=spent,
                                  clear_progress=clear_progress, validation=validation)

    async def accepted(self, outcome: W.WriterOutcome, sheet: C.FactSheet, sheet_sha: str,
                       category: str) -> Optional[Dict[str, Any]]:
        content = outcome.content or {}
        fields = {
            "draft": content,
            "draft_sha256": A.content_sha256(content),
            "fact_sheet": sheet.to_json(),
            "fact_sheet_sha256": sheet_sha,
            "subjects": ledger.subjects_json(sheet.subjects),
            "validation": outcome.validation_record(),
            "tokens_used": self._tokens(outcome.tokens_used),
            "slug": content.get("slug"),
            "title": content.get("title"),
            "category": category,
            "last_error": None,
            "last_stage": "drafted",
            "progress": {},
            "lease_until": None,
            # A NEW draft starts its narration-enqueue count afresh (`MAX_ENQUEUE_ATTEMPTS`): the
            # failures of an earlier round's draft are not this draft's.
            "build_attempts": 0,
        }
        try:
            drafted = await self.write(C.DRAFTED, fields)
        except ledger.LearnLedgerConflict as e:
            # The slug or title is held by another piece: a uniqueness miss the in-flight read
            # could not see (a race). A content problem — the next generation picks another title.
            logger.warning("learn build: piece %s (%s) draft collides with another piece (%s)",
                           self.pid, self.key, e)
            return await self.capped(
                "content_rejections", int(settings.LEARN_MAX_CONTENT_REJECTIONS), C.SKIPPED,
                "content_rejections_exhausted", "slug_collision: the draft's slug or title is "
                "another piece's", spent=outcome.tokens_used, clear_progress=True,
                validation=outcome.validation_record())
        if drafted is None:
            return None
        logger.info("learn build: piece %s (%s) DRAFTED %r (%s, %d calls, %d tokens)", self.pid,
                    self.key, content.get("title"), content.get("slug"), outcome.calls,
                    outcome.tokens_used)
        return await enqueue_narration(drafted, content, now=self.now)


def _remember_alert(pid: str, reason: Optional[str]) -> None:
    _ENQUEUE_ALERTED.pop(pid, None)
    if reason is None:
        return
    _ENQUEUE_ALERTED[pid] = reason
    while len(_ENQUEUE_ALERTED) > _ENQUEUE_ALERTED_MAX:
        _ENQUEUE_ALERTED.pop(next(iter(_ENQUEUE_ALERTED)), None)


async def enqueue_narration(drafted: Mapping[str, Any], content: Mapping[str, Any], *,
                            now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Queue the narration of a DRAFTED piece and move it to `media_pending` (fenced on its
    generation and draft hash). Round = `review_round or 1`; a round already narrating a
    different script (the ledger refuses it) opens round max + 1. The engine is
    `narration.current_engine()` — a junk `LEARN_NARRATOR_ENGINE` falls back to a known engine
    (ERROR) instead of failing every enqueue, the same rule Retry narration and the next-round
    path follow. If nothing could be queued the piece STAYS `drafted` (`_enqueue_failed`: counted,
    alerted once per distinct reason, `failed` at the cap or past the cut-off) — calling this again
    for the same piece is safe (the job insert is first-write-wins on (piece, round))."""
    pid, key = drafted.get("id"), drafted.get("period_key")
    narration = _narration()
    engine = narration.current_engine()
    round_no = _int(drafted.get("review_round")) or 1
    job: Optional[Mapping[str, Any]] = None
    problem: Optional[str] = None
    try:
        job = await narration.enqueue_for_piece(drafted, dict(content), engine=engine, round=round_no)
        if job is None:
            jobs = await ledger.list_jobs(pid)
            rounds = [_int(j.get("round")) for j in jobs]
            next_round = max(rounds + [round_no]) + 1
            logger.warning("learn build: piece %s (%s) round %d narrates another script — opening "
                           "round %d", pid, key, round_no, next_round)
            job = await narration.enqueue_for_piece(drafted, dict(content), engine=engine,
                                                    round=next_round)
            if job is None:
                problem = f"round {next_round} was refused too"
    except asyncio.CancelledError:
        raise
    except Exception as e:
        problem = f"{type(e).__name__}: {e}"
        logger.error("learn build: queueing the narration of piece %s (%s) failed: %s", pid, key,
                     problem, exc_info=not isinstance(e, ValueError))
    if job is None or not job.get("id"):
        problem = problem or "the ledger returned no job"
        logger.error("learn build: piece %s (%s) is DRAFTED but its narration is not queued: %s",
                     pid, key, problem)
        return await _enqueue_failed(drafted, problem, now=now)
    _remember_alert(str(pid), None)
    media = await ledger.transition(
        pid, from_statuses=[C.DRAFTED], to_status=C.MEDIA_PENDING,
        fields={"narration_job_id": job["id"], "last_stage": "narration_queued", "last_error": None},
        fence={"generation_id": drafted.get("generation_id"),
               "draft_sha256": drafted.get("draft_sha256")})
    if media is None:
        logger.warning("learn build: piece %s (%s) moved on before media_pending (job %s queued)",
                       pid, key, job.get("id"))
        return dict(drafted)
    logger.info("learn build: piece %s (%s) → media_pending (job %s, round %s, %s)", pid, key,
                job.get("id"), job.get("round"), engine)
    return media


async def _enqueue_failed(drafted: Mapping[str, Any], problem: str, *,
                          now: Optional[datetime]) -> Dict[str, Any]:
    """Record one failed enqueue of a drafted piece. The scheduler re-offers a `drafted` piece every
    5-minute tick until its hold ends, so this is what bounds a deterministic failure:

    * the attempt is COUNTED durably (`build_attempts`, compare-and-swapped on the observed value
      and the draft hash, so two overlapping retries cannot both count or both alert);
    * the loud "could not be queued" alert goes out only when the stored `last_error` CHANGES — the
      same refusal on every tick is one alert, not ~750;
    * at `MAX_ENQUEUE_ATTEMPTS`, or once the narration cut-off (`enqueue_cutoff_for`) has passed —
      a later enqueue could not reach the owner before the hold — the piece ends `failed` with ONE
      terminal alert (the CAS makes it one even across overlapping ticks).

    Returns the row this call wrote, else `drafted` as it came in."""
    pid, key = str(drafted.get("id")), drafted.get("period_key")
    reason = f"narration_enqueue_failed: {problem}"[:900]
    observed = drafted.get("build_attempts")
    counted = isinstance(observed, int) and not isinstance(observed, bool) and observed >= 0
    attempts = (observed if counted else 0) + 1
    fence: Dict[str, Any] = {"draft_sha256": drafted.get("draft_sha256")}
    if counted:
        fence["build_attempts"] = observed
    else:
        logger.warning("learn build: piece %s (%s) has an unreadable build_attempts %r — counting "
                       "this failure as attempt 1", pid, key, observed)
    cutoff = enqueue_cutoff_for(drafted)
    at = max(now, _utcnow()) if isinstance(now, datetime) and now.tzinfo else _utcnow()
    late = cutoff is not None and at >= cutoff
    if late or attempts >= MAX_ENQUEUE_ATTEMPTS:
        why = (f"past the narration cut-off {cutoff.isoformat()}" if late and cutoff is not None
               else f"{attempts}/{MAX_ENQUEUE_ATTEMPTS} attempts")
        try:
            row = await ledger.transition(
                pid, from_statuses=[C.DRAFTED], to_status=C.FAILED, fence=fence,
                fields={"last_error": f"narration_enqueue_exhausted ({why}): {problem}"[:900],
                        "build_attempts": attempts})
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — nothing written: the next tick decides again
            logger.error("learn build: closing piece %s (%s) after its enqueue failures failed "
                         "(%s: %s)", pid, key, type(e).__name__, e, exc_info=True)
            return dict(drafted)
        if row is None:
            logger.warning("learn build: piece %s (%s) moved on before its enqueue failures closed "
                           "it — its new owner decides", pid, key)
            return dict(drafted)
        _remember_alert(pid, None)
        logger.error("learn build: piece %s (%s) → failed: narration never queued (%s)", pid, key,
                     why)
        await _notify_terminal(row, status=C.FAILED, reason="narration_enqueue_failed",
                               stage="drafted")
        return row
    stored_failure = True
    try:
        row = await ledger.transition(pid, from_statuses=[C.DRAFTED], to_status=C.DRAFTED,
                                      fields={"last_error": reason, "build_attempts": attempts},
                                      fence=fence)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — the alert below still reports the stuck draft
        logger.error("learn build: recording the enqueue failure of %s failed (%s: %s)", pid,
                     type(e).__name__, e)
        row, stored_failure = None, False
    if stored_failure and row is None:
        # A Regenerate, the expiry or an overlapping retry (which counted this failure first) moved
        # the piece: whoever did owns the outcome and its alert.
        logger.warning("learn build: piece %s (%s) moved on before its enqueue failure was recorded "
                       "— no alert from this run", pid, key)
        return dict(drafted)
    # The durable rule is the stored value. When nothing could be stored, the next tick compares
    # against the same old value, so the process also remembers what it already said — otherwise a
    # ledger that refuses writes turns into an alert per tick.
    changed = drafted.get("last_error") != reason and _ENQUEUE_ALERTED.get(pid) != reason
    if changed:
        _remember_alert(pid, reason)
        await _notify(f"🔴 Learn {key}: the draft is written but its narration could not be "
                      f"queued ({problem[:200]}). It waits in drafted (attempt {attempts}/"
                      f"{MAX_ENQUEUE_ATTEMPTS}); it fails if this keeps happening.")
    else:
        logger.info("learn build: piece %s (%s) enqueue failed again with the same reason "
                    "(attempt %d/%d) — already alerted", pid, key, attempts, MAX_ENQUEUE_ATTEMPTS)
    return dict(row) if row is not None else dict(drafted)


async def _skip_late(piece: Mapping[str, Any], now: datetime) -> Optional[Dict[str, Any]]:
    """Past the catch-up cut-off before building: skip loudly. A `building` piece whose lease is
    still live is someone else's build — left alone."""
    status = piece.get("status")
    fence: Dict[str, Any] = {}
    if status == C.BUILDING:
        lease = _parse_instant(piece.get("lease_until"))
        if lease is not None and lease > now:
            return None
        fence = {"generation_id": piece.get("generation_id"), "lease_until": piece.get("lease_until")}
    row = await ledger.transition(piece.get("id"), from_statuses=[status], to_status=C.SKIPPED,
                                  fields={"last_error": "catch_up_cutoff_passed", "last_stage": STAGE,
                                          "lease_until": None},
                                  fence=fence)
    if row is not None:
        await _notify_terminal(row, status=C.SKIPPED, reason="catch_up_cutoff_passed")
    return row


async def _build_held(build: _HeldBuild, candidate: Optional[Dict[str, Any]],
                      topic_problem: Optional[str]) -> Optional[Dict[str, Any]]:
    row = build.row
    kind = row.get("kind")
    if topic_problem is not None or candidate is None or kind not in C.KINDS:
        why = topic_problem or f"unknown piece kind {kind!r}"
        logger.error("learn build: piece %s (%s) cannot be built: %s", build.pid, build.key, why)
        return await build.terminal(C.FAILED, "topic_invalid", last_error=f"topic_invalid: {why}"[:900])
    published = published_date_for(row)
    if published is None:
        return await build.terminal(C.FAILED, "schedule_invalid",
                                    last_error="schedule_invalid: no readable publish date")
    category = candidate["category"]

    try:
        sheet = await assemble_fact_sheet(kind, candidate)
    except FactSheetShortfall as e:
        return await build.thin_sheet(e)
    except FactSheetUnavailable as e:
        return await build.requeue(f"fact_sheet_unavailable: {e}")
    try:
        catalog = await _served_catalog()
    except Exception as e:  # the service degrades to [] itself; anything else is a bug here
        logger.error("learn build: reading the served catalog failed (%s: %s)", type(e).__name__, e,
                     exc_info=True)
        catalog = []
    if not catalog:
        # An empty catalog would let a duplicate title through (fail OPEN): wait for the real one.
        return await build.requeue("catalog_unavailable: the served Money Moves catalog is empty")
    try:
        table_titles, table_slugs = await _article_keys()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # Fail closed for the same reason: without the table the unique indexes guard, a repeat of
        # an archived title would be narrated, approved and then refused at publish.
        logger.error("learn build: reading the money_move_articles titles failed (%s: %s)",
                     type(e).__name__, e, exc_info=True)
        return await build.requeue(f"catalog_unavailable: the money_move_articles title read failed "
                                   f"({type(e).__name__}: {e})")
    in_flight = await _in_flight(build.pid)
    if in_flight is None:
        return await build.requeue("catalog_unavailable: the in-flight pieces could not all be read")
    titles, slugs = in_flight
    # The served catalog's titles first (the prompt shows a capped list, newest pieces first is the
    # useful order), then every other title the table holds.
    served_titles = _merged([a["title"] for a in catalog if isinstance(a.get("title"), str)],
                            table_titles, key=_title_fold)
    served_slugs = _merged([a["slug"] for a in catalog if isinstance(a.get("slug"), str)],
                           table_slugs, slugs)

    sha = sheet_sha256(sheet)
    review_round = _int(row.get("review_round"))

    async def before_call() -> bool:
        try:
            refreshed = await ledger.refresh_lease(build.pid, generation_id=build.gen,
                                                   lease_seconds=LEASE_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("learn build: refreshing the lease of piece %s (generation %s) failed "
                         "(%s: %s) — stopping the generation", build.pid, build.gen,
                         type(e).__name__, e)
            return False
        return refreshed is not None

    async def on_progress(payload: Dict[str, Any]) -> None:
        await build.save_progress(sha, review_round, payload)

    outcome = await W.write_article(
        sheet=sheet, kind=kind, category=category, topic_label=candidate_label(candidate),
        served_titles=served_titles, in_flight_titles=titles, generation_id=build.gen,
        before_call=before_call, reject_reason=row.get("reject_reason"),
        published_date=published, catalog=catalog, served_slugs=served_slugs,
        resume=resume_from(row.get("progress"), sha, review_round), on_progress=on_progress)

    if outcome.status == W.STATUS_ACCEPTED:
        return await build.accepted(outcome, sheet, sha, category)
    if outcome.status == W.STATUS_CONTENT_REJECTED:
        return await build.capped(
            "content_rejections", int(settings.LEARN_MAX_CONTENT_REJECTIONS), C.SKIPPED,
            "content_rejections_exhausted", f"content_rejected {outcome.issue_codes}",
            spent=outcome.tokens_used, clear_progress=True, validation=outcome.validation_record())
    if outcome.status == W.STATUS_WRITER_FAILED:
        return await build.capped(
            "writer_failures", int(settings.LEARN_MAX_WRITER_FAILURES), C.FAILED,
            "writer_failures_exhausted", f"writer_failed: {outcome.error or 'no detail'}",
            spent=outcome.tokens_used, clear_progress=False, validation=outcome.validation_record())
    # lease_lost: whoever holds the piece now carries on. If the refresh only FAILED (the lease may
    # still be ours), hand the piece back for the next tick — the write is fenced, so it lands only
    # if we really still hold it.
    logger.warning("learn build: piece %s (%s) generation %s stopped: %s", build.pid, build.key,
                   build.gen, outcome.error)
    return await build.requeue(f"lease_lost: {outcome.error or 'no detail'}", spent=outcome.tokens_used)


async def build_piece(piece: Mapping[str, Any], *, now: datetime) -> Optional[Dict[str, Any]]:
    """Build one piece (module docstring). Returns the piece row as this run last wrote it, or
    None when nothing was written (not buildable yet, waiting for its topic, held by another
    generation, or a lost race). `now` (timezone-aware) is the tick's instant: it dates the lease
    and the catch-up decision. A `drafted` piece (its narration could not be queued earlier) only
    retries the enqueue."""
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("build_piece needs a timezone-aware now")
    if not isinstance(piece, Mapping) or not piece.get("id"):
        logger.error("learn build: build_piece got %r, not a piece row", type(piece).__name__)
        return None
    pid, key, status = piece.get("id"), piece.get("period_key"), piece.get("status")
    if status == C.DRAFTED:
        draft = piece.get("draft")
        if not isinstance(draft, Mapping):
            logger.error("learn build: drafted piece %s (%s) has no draft", pid, key)
            return None
        return await enqueue_narration(piece, draft, now=now)
    if status not in BUILD_FROM_STATUSES:
        logger.info("learn build: piece %s (%s) is %s — nothing to build", pid, key, status)
        return None
    candidate, problem = chosen_candidate(piece.get("topic"))
    if problem == WAITING_FOR_TOPIC:
        logger.info("learn build: piece %s (%s) has no chosen topic yet", pid, key)
        return None
    cutoff = catch_up_cutoff_for(piece)
    if cutoff is not None and now >= cutoff:
        return await _skip_late(piece, now)

    gen = str(uuid.uuid4())
    row = await ledger.acquire_lease(pid, generation_id=gen, lease_seconds=LEASE_SECONDS,
                                     from_statuses=list(BUILD_FROM_STATUSES), to_status=C.BUILDING,
                                     now=now)
    if row is None:
        return None
    build = _HeldBuild(row, gen, now)
    logger.info("learn build: generation %s took piece %s (%s, %s, generation #%s)", gen, pid, key,
                row.get("kind"), row.get("generations"))
    try:
        return await _build_held(build, candidate, problem)
    except asyncio.CancelledError:
        raise
    except ledger.LearnLedgerError as e:
        logger.error("learn build: a ledger write for piece %s (%s) generation %s failed (%s: %s) — "
                     "the lease will run out", pid, key, gen, type(e).__name__, e)
        return None
    except Exception as e:
        # A bug: count it as a writer failure so a crash loop ends at the cap with an alert,
        # instead of rebuilding forever in silence.
        logger.exception("learn build: piece %s (%s) generation %s crashed (%s: %s)", pid, key, gen,
                         type(e).__name__, e)
        try:
            return await build.capped("writer_failures", int(settings.LEARN_MAX_WRITER_FAILURES),
                                      C.FAILED, "writer_failures_exhausted",
                                      f"crash: {type(e).__name__}: {e}", spent=0,
                                      clear_progress=False, validation=None)
        except Exception as e2:  # noqa: BLE001 — the ledger is down too; the lease runs out
            logger.error("learn build: recording the crash of piece %s failed (%s: %s)", pid,
                         type(e2).__name__, e2)
            return None
    finally:
        if build.held:
            try:
                await ledger.release_lease(pid, generation_id=gen)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — the lease simply runs out
                logger.warning("learn build: releasing piece %s generation %s failed (%s: %s)",
                               pid, gen, type(e).__name__, e)


__all__ = [
    "BUILD_FROM_STATUSES", "FactSheetShortfall", "FactSheetUnavailable", "IN_FLIGHT_STATUSES",
    "LEASE_SECONDS", "MAX_ENQUEUE_ATTEMPTS", "MIN_SHEET_FACTS", "WAITING_FOR_TOPIC",
    "assemble_fact_sheet", "build_piece", "candidate_label", "candidate_peg_facts",
    "candidate_subjects", "candidate_theme", "catch_up_cutoff_for", "chosen_candidate",
    "enqueue_cutoff_for", "enqueue_narration", "published_date_for", "resume_from", "sheet_sha256",
]
