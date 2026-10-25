"""Learn weekly: the human-verified case library (`backend/data/learn_case_library.json`).

Evergreen and historical facts — how a data centre is financed, what a company's business is,
a resolved past case — come from this file, never from the model's memory (plan §1.6). Claude
drafts entries from primary sources (SEC filings, court and regulator documents); a NAMED person
verifies each one. Only a USABLE entry ever reaches a fact sheet:

    usable  ⇔  verified_by and verified_at are set  AND  llm_eligible is true  AND  ≥ 1 fact

`llm_eligible: false` is how a misconduct-centred case stays out of model hands (the repo already
records that an LLM retelling adds claims, `marketing/content_pool.py`); `status` carries
"resolved" or null and is kept for the scheduler's case rules.

Loading is fail-closed and loud:
  * the file ABSENT (another session is still drafting it) → an empty library + a WARNING;
  * the file unreadable, not JSON, or a `version` other than 1 → an empty library + an ERROR;
  * a malformed ENTRY → that entry skipped with an ERROR naming its id; the rest load.
An entry is malformed when a required field is missing or of the wrong type, a CIK is set but
invalid (a typo would make a public filer look private), a fact's source URL is not https, a fact
id repeats, or a `quotes` key appears anywhere in it — weekly pieces carry no quotes at all, so a
quote in the library is refused rather than silently ignored.

Pure apart from reading one small local file (cached per path + mtime + size, so an edited file is
picked up without a restart).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

from app.integrations.sec_edgar import normalize_cik
from app.services.learn_weekly.contracts import ORIGIN_LIBRARY, Fact

logger = logging.getLogger(__name__)

__all__ = [
    "LIBRARY_PATH",
    "LIBRARY_VERSION",
    "ENTRY_KINDS",
    "PRIMER_KINDS",
    "LibrarySource",
    "LibraryFact",
    "LibraryCompany",
    "LibraryEntry",
    "CaseLibrary",
    "parse_library",
    "load_case_library",
    "get_case_library",
    "usable_entries",
    "primer_count",
    "KIND_COMPANY",
    "company_primer_ciks",
    "facts_for",
    "library_fact_id",
]

LIBRARY_PATH = Path(__file__).resolve().parents[3] / "data" / "learn_case_library.json"
LIBRARY_VERSION = 1
ENTRY_KINDS: Tuple[str, ...] = ("mechanics", "company", "case")
KIND_COMPANY = "company"
#: The kinds that count toward a theme's eligibility (≥ 2 verified primers, plan §1.5).
PRIMER_KINDS: Tuple[str, ...] = ("mechanics", "company")

# Ids end up inside fact ids ("L:<entry>:<fid>"), so no ":" and nothing odd.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")
_MAX_FACT_CHARS = 1000
_MAX_TEXT_CHARS = 300


class _Malformed(ValueError):
    """One entry failed validation; the message says which field."""


# ── Types ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LibrarySource:
    title: str
    url: str                      # https only
    publisher: Optional[str] = None
    date: Optional[str] = None
    type: Optional[str] = None


@dataclass(frozen=True)
class LibraryFact:
    id: str
    text: str
    source: LibrarySource


@dataclass(frozen=True)
class LibraryCompany:
    name: str
    cik: Optional[str] = None     # 10-digit; None = no SEC filer (private / foreign)
    ticker: Optional[str] = None


@dataclass(frozen=True)
class LibraryEntry:
    id: str
    kind: str
    title: str
    themes: Tuple[str, ...]
    category_fit: Tuple[str, ...]
    companies: Tuple[LibraryCompany, ...]
    sic: Optional[str]
    era: Optional[str]
    status: Optional[str]
    llm_eligible: bool
    facts: Tuple[LibraryFact, ...]
    verified_by: Optional[str]
    verified_at: Optional[str]
    echo_tags: Tuple[str, ...] = ()
    banned_claims: Tuple[str, ...] = ()
    catalog_subjects: Tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return bool(self.verified_by and self.verified_at and self.llm_eligible and self.facts)


@dataclass(frozen=True)
class CaseLibrary:
    version: int
    entries: Tuple[LibraryEntry, ...]

    def entry(self, entry_id: str) -> Optional[LibraryEntry]:
        for e in self.entries:
            if e.id == entry_id:
                return e
        return None

    def usable_entries(
        self, theme: Optional[str] = None, kinds: Optional[Iterable[str]] = None
    ) -> List[LibraryEntry]:
        """Usable entries, file order, optionally for one theme slug and/or some kinds."""
        wanted = None if kinds is None else (
            {kinds} if isinstance(kinds, str) else set(kinds)
        )
        return [
            e for e in self.entries
            if e.usable
            and (theme is None or theme in e.themes)
            and (wanted is None or e.kind in wanted)
        ]

    def primer_count(self, theme: str) -> int:
        """Usable mechanics + company entries for ``theme`` — a theme is eligible for a Deep
        Dive at ≥ 2 (plan §1.5)."""
        return len(self.usable_entries(theme=theme, kinds=PRIMER_KINDS))

    def company_primer_ciks(self) -> FrozenSet[str]:
        """The CIKs of the companies with a usable COMPANY primer: the only companies a
        single-company Deep Dive may be about (owner decision 2026-10-24: "Skip single-company
        topics that have no verified case-library entry"). Without one, a lone company's sheet
        held only its filing figures and the writer filled its history from memory — the 3M
        drafts said "four business segments" (it has had three since the 2024 Solventum spin-off).
        A mechanics or case entry that merely lists the company does not count."""
        return frozenset(co.cik for e in self.usable_entries(kinds=(KIND_COMPANY,))
                         for co in e.companies if co.cik)

    def facts_for(self, entry: LibraryEntry) -> List[Fact]:
        return facts_for(entry)


def library_fact_id(entry_id: str, fact_id: str) -> str:
    return f"L:{entry_id}:{fact_id}"


def facts_for(entry: LibraryEntry) -> List[Fact]:
    """The entry's facts as `contracts.Fact` (origin library, id ``L:<entry>:<fid>``, url the
    source). An entry that is not usable yields NOTHING (WARNING): an unverified or
    misconduct-centred fact never reaches a sheet, whoever asks."""
    if not isinstance(entry, LibraryEntry):
        raise TypeError(f"facts_for needs a LibraryEntry, got {type(entry).__name__}")
    if not entry.usable:
        logger.warning(
            "[learn-library-unusable] entry %s is not usable (verified_by=%s, verified_at=%s, "
            "llm_eligible=%s, facts=%d) — no facts given",
            entry.id, bool(entry.verified_by), bool(entry.verified_at), entry.llm_eligible,
            len(entry.facts),
        )
        return []
    return [
        Fact(
            id=library_fact_id(entry.id, f.id),
            text=f.text,
            origin=ORIGIN_LIBRARY,
            url=f.source.url,
            entry_id=entry.id,
        )
        for f in entry.facts
    ]


# ── Parsing ────────────────────────────────────────────────────────────


def _text(value: Any, field: str, *, required: bool = True, max_len: int = _MAX_TEXT_CHARS
          ) -> Optional[str]:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        if required:
            raise _Malformed(f"{field} must be a non-empty string")
        return None
    text = " ".join(value.split())
    if len(text) > max_len:
        raise _Malformed(f"{field} is {len(text)} characters (max {max_len})")
    return text


def _str_list(value: Any, field: str, *, slug: bool = False) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _Malformed(f"{field} must be a list")
    out: List[str] = []
    for i, item in enumerate(value):
        text = _text(item, f"{field}[{i}]")
        if slug and not _SLUG_RE.match(text):
            raise _Malformed(f"{field}[{i}] {text!r} is not a lower-case slug")
        if text not in out:
            out.append(text)
    return tuple(out)


def _https_url(value: Any, field: str) -> str:
    url = _text(value, field, max_len=2000)
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or " " in url:
        raise _Malformed(f"{field} must be an https URL, got {url[:80]!r}")
    return url


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, Mapping):
        return key in value or any(_contains_key(v, key) for v in value.values())
    if isinstance(value, list):
        return any(_contains_key(v, key) for v in value)
    return False


def _parse_company(raw: Any, field: str) -> LibraryCompany:
    if not isinstance(raw, Mapping):
        raise _Malformed(f"{field} must be an object")
    name = _text(raw.get("name"), f"{field}.name", max_len=120)
    cik_raw = raw.get("cik")
    cik = None
    if cik_raw not in (None, ""):
        cik = normalize_cik(cik_raw)
        if cik is None:
            # Never quietly "private": a typo'd CIK would strip a public filer's figures.
            raise _Malformed(f"{field}.cik {cik_raw!r} is not a valid CIK")
    ticker = _text(raw.get("ticker"), f"{field}.ticker", required=False, max_len=12)
    return LibraryCompany(name=name, cik=cik, ticker=ticker.upper() if ticker else None)


def _parse_fact(raw: Any, field: str) -> LibraryFact:
    if not isinstance(raw, Mapping):
        raise _Malformed(f"{field} must be an object")
    fid = raw.get("id")
    if not isinstance(fid, str) or not _ID_RE.match(fid):
        raise _Malformed(f"{field}.id {fid!r} is not a valid id")
    text = _text(raw.get("text"), f"{field}.text", max_len=_MAX_FACT_CHARS)
    source = raw.get("source")
    if not isinstance(source, Mapping):
        raise _Malformed(f"{field}.source must be an object (every fact is sourced)")
    return LibraryFact(
        id=fid,
        text=text,
        source=LibrarySource(
            title=_text(source.get("title"), f"{field}.source.title"),
            url=_https_url(source.get("url"), f"{field}.source.url"),
            publisher=_text(source.get("publisher"), f"{field}.source.publisher", required=False),
            date=_text(source.get("date"), f"{field}.source.date", required=False, max_len=40),
            type=_text(source.get("type"), f"{field}.source.type", required=False, max_len=40),
        ),
    )


def _parse_entry(raw: Mapping[str, Any]) -> LibraryEntry:
    entry_id = raw.get("id")
    if not isinstance(entry_id, str) or not _ID_RE.match(entry_id):
        raise _Malformed(f"id {entry_id!r} is not a valid id")
    if _contains_key(raw, "quotes"):
        raise _Malformed("has a 'quotes' key — weekly pieces carry no quotes")
    kind = raw.get("kind")
    if kind not in ENTRY_KINDS:
        raise _Malformed(f"kind {kind!r} is not one of {ENTRY_KINDS}")
    llm_eligible = raw.get("llm_eligible")
    if not isinstance(llm_eligible, bool):
        raise _Malformed(f"llm_eligible must be true or false, got {llm_eligible!r}")
    status = raw.get("status")
    if status not in (None, "resolved"):
        raise _Malformed(f"status {status!r} must be \"resolved\" or null")

    companies_raw = raw.get("companies")
    if companies_raw is not None and not isinstance(companies_raw, list):
        raise _Malformed("companies must be a list")
    companies = tuple(
        _parse_company(c, f"companies[{i}]") for i, c in enumerate(companies_raw or ())
    )

    facts_raw = raw.get("facts")
    if not isinstance(facts_raw, list):
        raise _Malformed("facts must be a list")
    facts = tuple(_parse_fact(f, f"facts[{i}]") for i, f in enumerate(facts_raw))
    seen: set = set()
    for f in facts:
        if f.id in seen:
            raise _Malformed(f"fact id {f.id!r} repeats")
        seen.add(f.id)

    verified_by = _text(raw.get("verified_by"), "verified_by", required=False, max_len=120)
    verified_at = _text(raw.get("verified_at"), "verified_at", required=False, max_len=40)
    if verified_at is not None:
        try:
            date.fromisoformat(verified_at[:10])
        except ValueError:
            raise _Malformed(f"verified_at {verified_at!r} is not an ISO date") from None

    sic = raw.get("sic")
    if isinstance(sic, int) and not isinstance(sic, bool):
        sic = str(sic)
    return LibraryEntry(
        id=entry_id,
        kind=kind,
        title=_text(raw.get("title"), "title"),
        themes=_str_list(raw.get("themes"), "themes", slug=True),
        category_fit=_str_list(raw.get("category_fit"), "category_fit"),
        companies=companies,
        sic=_text(sic, "sic", required=False, max_len=8),
        era=_text(raw.get("era"), "era", required=False, max_len=60),
        status=status,
        llm_eligible=llm_eligible,
        facts=facts,
        verified_by=verified_by,
        verified_at=verified_at,
        echo_tags=_str_list(raw.get("echo_tags"), "echo_tags"),
        banned_claims=_str_list(raw.get("banned_claims"), "banned_claims"),
        catalog_subjects=_str_list(raw.get("catalog_subjects"), "catalog_subjects"),
    )


def parse_library(data: Any, *, source: str = "<memory>") -> CaseLibrary:
    """Validate a decoded library document. Never raises: a bad document is an empty library
    (ERROR) and a bad entry is skipped (ERROR naming its id)."""
    if not isinstance(data, Mapping):
        logger.error(
            "[learn-library-invalid] %s: top level is a %s, not an object — empty library",
            source, type(data).__name__,
        )
        return CaseLibrary(version=LIBRARY_VERSION, entries=())
    version = data.get("version")
    if version != LIBRARY_VERSION or isinstance(version, bool):
        # A new version may change what a field MEANS (llm_eligible, status): fail closed.
        logger.error(
            "[learn-library-version] %s: version %r is not %d — empty library",
            source, version, LIBRARY_VERSION,
        )
        return CaseLibrary(version=LIBRARY_VERSION, entries=())
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        logger.error("[learn-library-invalid] %s: 'entries' is not a list — empty library", source)
        return CaseLibrary(version=LIBRARY_VERSION, entries=())

    entries: List[LibraryEntry] = []
    seen: set = set()
    for i, raw in enumerate(raw_entries):
        label = raw.get("id") if isinstance(raw, Mapping) else None
        label = label if isinstance(label, str) else f"#{i}"
        if not isinstance(raw, Mapping):
            logger.error("[learn-library-entry] %s entry %s is not an object — skipped",
                         source, label)
            continue
        try:
            entry = _parse_entry(raw)
        except _Malformed as exc:
            logger.error("[learn-library-entry] %s entry %s skipped: %s", source, label, exc)
            continue
        if entry.id in seen:
            logger.error("[learn-library-entry] %s entry %s repeats an id — the later copy "
                         "is skipped", source, entry.id)
            continue
        seen.add(entry.id)
        entries.append(entry)
    usable = sum(1 for e in entries if e.usable)
    logger.info(
        "[learn-library-loaded] %s: %d entries (%d usable, %d malformed skipped)",
        source, len(entries), usable, len(raw_entries) - len(entries),
    )
    return CaseLibrary(version=LIBRARY_VERSION, entries=tuple(entries))


def load_case_library(path: Optional[os.PathLike] = None) -> CaseLibrary:
    """Read and validate the library file (default ``LIBRARY_PATH``). Never raises."""
    target = Path(path) if path is not None else LIBRARY_PATH
    try:
        raw = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(
            "[learn-library-absent] %s does not exist yet — empty library (no theme is "
            "eligible until verified primers land)",
            target,
        )
        return CaseLibrary(version=LIBRARY_VERSION, entries=())
    except OSError as exc:
        logger.error("[learn-library-unreadable] %s: %s: %s — empty library",
                     target, type(exc).__name__, exc)
        return CaseLibrary(version=LIBRARY_VERSION, entries=())
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        logger.error("[learn-library-invalid] %s is not JSON: %s: %s — empty library",
                     target, type(exc).__name__, exc)
        return CaseLibrary(version=LIBRARY_VERSION, entries=())
    return parse_library(data, source=str(target))


_cached: Dict[str, Tuple[Tuple[int, int], CaseLibrary]] = {}
_cache_lock = threading.Lock()


def get_case_library(path: Optional[os.PathLike] = None) -> CaseLibrary:
    """The library, re-read only when the file's mtime or size changes (one ``stat`` per call).

    Synchronous on purpose: the file is small and local, and the stat is what keeps an edited
    library live without a restart. Callers on the event loop that load it for the FIRST time
    may wrap it in ``asyncio.to_thread``.
    """
    target = Path(path) if path is not None else LIBRARY_PATH
    try:
        st = target.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = (-1, -1)  # absent: cached as such until it appears
    key = str(target)
    with _cache_lock:
        hit = _cached.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
    library = load_case_library(target)
    with _cache_lock:
        _cached[key] = (stamp, library)
    return library


def usable_entries(
    theme: Optional[str] = None, kinds: Optional[Iterable[str]] = None
) -> List[LibraryEntry]:
    """``get_case_library().usable_entries(...)`` — the default file."""
    return get_case_library().usable_entries(theme=theme, kinds=kinds)


def primer_count(theme: str) -> int:
    """``get_case_library().primer_count(theme)`` — the default file."""
    return get_case_library().primer_count(theme)


def company_primer_ciks() -> FrozenSet[str]:
    """``get_case_library().company_primer_ciks()`` — the default file."""
    return get_case_library().company_primer_ciks()
