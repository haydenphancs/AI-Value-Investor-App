"""Learn weekly: assemble the fact sheet a piece is written from (plan §1.6).

The sheet is the ONLY source of figures and named facts the writer may restate: grounding
checks every number and proper noun of the draft against it. Three inputs, in priority order:

  1. peg facts (``sec_peg``) — verbatim spans of this week's 8-K / EX-99.1 (the "why now");
  2. XBRL facts (``sec_xbrl``) — code-rendered sentences from the filer's 10-K figures;
  3. library facts (``library``) — human-verified case-library facts (the evergreen part).

What this module guarantees, whatever the callers pass:

* **Every fact is cited.** An https URL on every fact, an accession number on every SEC fact;
  a fact without one is dropped (WARNING) — the review header must be able to show a source.
* **Every peg fact passes the peg screen again.** `topic.peg_problem` (no person, no forecast,
  no allegation or enforcement matter, no instruction, share price or puffery, every in-app block
  rule) runs on each ``sec_peg`` fact with the sheet's subjects; a refused one is dropped
  (WARNING). A peg is restated by design and grounding then treats the restatement as sourced,
  so a topic JSON stored before a rule existed — or edited by hand — must not carry a refused
  span onto the sheet.
* **Library facts are the library's.** A library fact must resolve to a USABLE entry of the
  case library and match its text exactly; an unverified, ineligible, unknown or edited fact is
  dropped (ERROR) — the sheet is never a side door around verification.
* **The counterparty rule.** A company with no CIK (a private or foreign counterparty, e.g. an
  AI lab) may appear only INSIDE a public filer's SEC-disclosed fact, never with its own figures.
  So a library fact that carries a figure (a digit, %, $, or a scale word) is dropped (WARNING)
  when its entry is about no-CIK companies only, or when it names a no-CIK company (of its entry
  or a no-CIK subject) without also naming a CIK filer of the entry and citing sec.gov.
* **Every peg fact carries its filing date.** `date_peg_facts` (called by the build with SEC's
  submissions record) opens each span with the code-owned sentence
  ``contracts.PEG_DATE_PREFIX`` and the 8-K's filing date, and drops a peg whose filing date SEC
  does not give (WARNING). The peg section must state the full date it restates, and grounding
  refuses any number the sheet does not hold. The peg screen reads the span without it.
* **Identical texts appear once** (case- and whitespace-insensitive), the higher-priority copy
  kept.
* **Stable ids.** Peg facts are numbered ``P1…``, XBRL facts ``S1…`` in input order (so facts
  from several companies' ``annual_facts`` never collide), library facts keep
  ``L:<entry>:<fid>``. The same inputs always give the same ids.
* **The cap.** ``DEEP_DIVE_SHEET_CAP`` / ``SLOT_SHEET_CAP`` characters of fact text
  (``FactSheet.char_count``): facts are dropped from the LOWEST priority end (the last library
  fact first, then the last XBRL fact, then the last peg fact) until the sheet fits, and every
  drop is logged with its id.

Pure: no I/O except resolving the default case library (a cached local file) when the caller
passes none. `topic` is imported lazily (inside `_peg_screen`): it loads the ledger and the SEC
client modules, which nothing else here needs, and a module-level import would tie this module's
import to the whole topic step.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import date
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit

from app.integrations.sec_edgar import normalize_accession
from app.services.learn_weekly import case_library as _case_library
from app.services.learn_weekly.case_library import CaseLibrary, LibraryEntry
from app.services.learn_weekly.contracts import (
    DEEP_DIVE_SHEET_CAP,
    KIND_DEEP_DIVE,
    KIND_SLOT,
    KINDS,
    ORIGIN_LIBRARY,
    ORIGIN_SEC_PEG,
    ORIGIN_SEC_XBRL,
    SLOT_SHEET_CAP,
    Fact,
    FactSheet,
    Subject,
    peg_date_text,
    split_peg_date,
)

logger = logging.getLogger(__name__)

__all__ = ["MAX_FACT_CHARS", "sheet_cap", "date_peg_facts", "build_fact_sheet"]

#: One fact is one sentence: a longer "fact" is a paste, not a fact (peg spans are ≤ 400).
MAX_FACT_CHARS = 1000
_TITLE_MAX_CHARS = 200


# A figure in any form a reader would take as a number about the company.
_FIGURE_RE = re.compile(
    r"\d|%|\$|\b(?:hundred|thousand|million|billion|trillion|percent)s?\b", re.IGNORECASE
)
# Corporate suffixes dropped to find the name as prose uses it ("OpenAI, Inc." → "OpenAI").
_SUFFIX_RE = re.compile(
    r"[,\s]+(?:inc|incorporated|corp|corporation|co|company|ltd|limited|llc|l\.l\.c|lp|l\.p|"
    r"plc|pbc|ag|sa|nv|se|gmbh|bv|b\.v|s\.a|holdings?)\.?$",
    re.IGNORECASE,
)


def sheet_cap(kind: str) -> int:
    if kind == KIND_DEEP_DIVE:
        return DEEP_DIVE_SHEET_CAP
    if kind == KIND_SLOT:
        return SLOT_SHEET_CAP
    raise ValueError(f"unknown piece kind {kind!r} (expected one of {KINDS})")


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _is_https(url: Any) -> bool:
    if not isinstance(url, str) or " " in url:
        return False
    parts = urlsplit(url)
    return parts.scheme == "https" and bool(parts.netloc)


def _is_sec_url(url: Optional[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower() if isinstance(url, str) else ""
    return host == "sec.gov" or host.endswith(".sec.gov")


def _name_variants(name: str) -> List[str]:
    full = _collapse(name)
    out = [full]
    stripped = full
    while True:
        shorter = _SUFFIX_RE.sub("", stripped).strip()
        if shorter == stripped or not shorter:
            break
        stripped = shorter
        out.append(stripped)
    return [v for v in out if v]


def _names(text: str, name: Optional[str]) -> bool:
    """``text`` mentions ``name`` (or its suffix-less form) as a whole word. Short names (≤ 3
    characters, e.g. "X") match case-sensitively so they do not fire on ordinary words."""
    if not name:
        return False
    for variant in _name_variants(name):
        flags = 0 if len(variant) <= 3 else re.IGNORECASE
        if re.search(rf"(?<!\w){re.escape(variant)}(?!\w)", text, flags):
            return True
    return False


def _counterparty_problem(
    fact: Fact, entry: LibraryEntry, private_subjects: Sequence[str]
) -> Optional[str]:
    """Why a library fact breaks the counterparty rule, or None when it may stay."""
    if not _FIGURE_RE.search(fact.text):
        return None
    with_cik = [c for c in entry.companies if c.cik]
    if entry.companies and not with_cik:
        return "its entry is about companies with no CIK and the fact carries a figure"
    private = [c.name for c in entry.companies if not c.cik] + list(private_subjects)
    named = [n for n in private if _names(fact.text, n)]
    if not named:
        return None
    filer_named = any(
        _names(fact.text, c.name)
        or (c.ticker and re.search(rf"(?<!\w){re.escape(c.ticker)}(?!\w)", fact.text))
        for c in with_cik
    )
    if filer_named and _is_sec_url(fact.url):
        return None  # a public filer's own SEC disclosure that names its counterparty
    return (
        f"it names {named[0]!r} (no CIK) with a figure outside a public filer's SEC disclosure"
    )


def _subjects(subjects: Optional[Iterable[Any]]) -> Tuple[Subject, ...]:
    out: List[Subject] = []
    seen: Set[str] = set()
    for s in subjects or ():
        if not isinstance(s, Subject) or not isinstance(s.name, str) or not s.name.strip():
            logger.error("[learn-sheet-subject] not a named Subject: %r — skipped", s)
            continue
        key = s.cik or s.name.strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return tuple(out)


def _filing_day(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def date_peg_facts(pegs: Iterable[Fact], filed: Mapping[str, str]) -> List[Fact]:
    """``pegs`` with SEC's filing date in front of each text (`contracts.peg_date_text`).
    ``filed`` maps an accession number to its ISO filing date (the filers' submissions records).
    A peg whose accession is not there, or whose date does not parse, is dropped (WARNING):
    undated, the peg section could not state the date of what it restates. A peg with no text
    passes through unchanged, so `build_fact_sheet` still drops it as empty."""
    days = {}
    for accession, iso in (filed or {}).items():
        key, day = normalize_accession(accession), _filing_day(iso)
        if key is not None and day is not None:
            days[key] = day
    out: List[Fact] = []
    for fact in pegs or ():
        if not isinstance(fact, Fact) or not isinstance(fact.text, str) or not fact.text.strip():
            out.append(fact)
            continue
        key = normalize_accession(fact.accession)
        day = days.get(key) if key is not None else None
        if day is None:
            logger.warning("[learn-sheet-peg-undated] %s: SEC gives no filing date for accession "
                           "%r — dropped", fact.id, fact.accession)
            continue
        out.append(replace(fact, text=peg_date_text(day) + fact.text.strip()))
    return out


def build_fact_sheet(
    kind: str,
    topic_title: str,
    theme_slug: Optional[str],
    subjects: Optional[Iterable[Subject]],
    peg_facts: Optional[Iterable[Fact]],
    xbrl_facts: Optional[Iterable[Fact]],
    library_facts: Optional[Iterable[Fact]],
    *,
    library: Optional[CaseLibrary] = None,
) -> FactSheet:
    """The capped, deduplicated, cited fact sheet for one piece (see the module docstring).

    ``library`` resolves library facts to their entries; None means the default case-library
    file (``case_library.get_case_library()``). Raises ValueError only for an unknown ``kind``.
    """
    cap = sheet_cap(kind)
    title = _collapse(topic_title)[:_TITLE_MAX_CHARS] if isinstance(topic_title, str) else ""
    slug = theme_slug if isinstance(theme_slug, str) and theme_slug.strip() else None
    subs = _subjects(subjects)
    private_subjects = [s.name for s in subs if not s.cik]
    subject_names = [x for s in subs for x in (s.name, s.ticker) if isinstance(x, str) and x]

    lib = library
    library_list = list(library_facts or ())
    if lib is None and library_list:
        lib = _case_library.get_case_library()

    accepted: List[Tuple[str, Fact]] = []     # (origin, fact), priority order
    for origin, group in (
        (ORIGIN_SEC_PEG, peg_facts),
        (ORIGIN_SEC_XBRL, xbrl_facts),
        (ORIGIN_LIBRARY, library_list),
    ):
        for fact in group or ():
            kept = _vet(fact, origin, lib, private_subjects, subject_names)
            if kept is not None:
                accepted.append((origin, kept))

    # Identical texts once (higher priority kept); library ids once.
    unique: List[Tuple[str, Fact]] = []
    seen_text: Set[str] = set()
    seen_ids: Set[str] = set()
    for origin, fact in accepted:
        key = fact.text.casefold()
        if key in seen_text:
            logger.info("[learn-sheet-duplicate] %s %s repeats an earlier fact — dropped",
                        origin, fact.id)
            continue
        if origin == ORIGIN_LIBRARY and fact.id in seen_ids:
            logger.warning("[learn-sheet-duplicate-id] library fact %s given twice — dropped",
                           fact.id)
            continue
        seen_text.add(key)
        seen_ids.add(fact.id)
        unique.append((origin, fact))

    # Stable ids: P1…, S1… by input order; library ids are already stable.
    numbered: List[Fact] = []
    counters = {ORIGIN_SEC_PEG: 0, ORIGIN_SEC_XBRL: 0}
    for origin, fact in unique:
        if origin in counters:
            counters[origin] += 1
            prefix = "P" if origin == ORIGIN_SEC_PEG else "S"
            fact = Fact(
                id=f"{prefix}{counters[origin]}",
                text=fact.text,
                origin=fact.origin,
                url=fact.url,
                accession=fact.accession,
                entry_id=fact.entry_id,
            )
        numbered.append(fact)

    # The cap: drop from the lowest-priority end until the text fits.
    total = sum(len(f.text) for f in numbered)
    dropped: List[str] = []
    while numbered and total > cap:
        last = numbered.pop()
        total -= len(last.text)
        dropped.append(last.id)
    if dropped:
        logger.warning(
            "[learn-sheet-cap] %s %r: %d facts dropped to fit the %d-character cap "
            "(lowest priority first): %s",
            kind, title, len(dropped), cap, dropped,
        )

    sheet = FactSheet(
        kind=kind, topic_title=title, theme_slug=slug, subjects=subs, facts=tuple(numbered)
    )
    logger.info(
        "[learn-sheet-built] %s %r: %d facts (%d peg, %d xbrl, %d library), %d characters",
        kind, title, len(sheet.facts),
        sum(1 for f in sheet.facts if f.origin == ORIGIN_SEC_PEG),
        sum(1 for f in sheet.facts if f.origin == ORIGIN_SEC_XBRL),
        sum(1 for f in sheet.facts if f.origin == ORIGIN_LIBRARY),
        sheet.char_count(),
    )
    return sheet


def _peg_screen(fact_id: Any, text: str, subject_names: Sequence[str]) -> Optional[str]:
    """`topic.peg_problem` for one stored peg fact (None = it may stay). Never raises: the screen
    is total and fails closed on its own errors."""
    # Lazy: see the module docstring (topic loads the ledger and SEC client modules).
    from app.services.learn_weekly.topic import peg_problem

    problem = peg_problem(text, subject_names)
    if problem is not None:
        logger.warning("[learn-sheet-peg-screen] peg fact %s dropped: %s — %.80r",
                       fact_id, problem, text)
    return problem


def _vet(
    fact: Any, origin: str, library: Optional[CaseLibrary], private_subjects: Sequence[str],
    subject_names: Sequence[str] = (),
) -> Optional[Fact]:
    """The fact, normalised, or None (logged) when it may not enter the sheet."""
    if not isinstance(fact, Fact):
        logger.error("[learn-sheet-fact] %s input is a %s, not a Fact — dropped",
                     origin, type(fact).__name__)
        return None
    if fact.origin != origin:
        # A fact in the wrong pipe would skip that pipe's rules (e.g. a "library" fact with no
        # entry posing as XBRL).
        logger.error("[learn-sheet-fact] %s given as %s, but its origin is %r — dropped",
                     fact.id, origin, fact.origin)
        return None
    text = _collapse(fact.text) if isinstance(fact.text, str) else ""
    if not text or len(text) > MAX_FACT_CHARS:
        logger.warning("[learn-sheet-fact] %s %s: text is %s — dropped", origin, fact.id,
                       "empty" if not text else f"{len(text)} characters (max {MAX_FACT_CHARS})")
        return None
    if not _is_https(fact.url):
        logger.warning("[learn-sheet-uncited] %s %s has no https source URL — dropped",
                       origin, fact.id)
        return None

    accession = fact.accession
    if origin in (ORIGIN_SEC_PEG, ORIGIN_SEC_XBRL):
        accession = normalize_accession(fact.accession)
        if accession is None:
            logger.warning("[learn-sheet-uncited] %s %s has no valid accession number — "
                           "dropped", origin, fact.id)
            return None
        # The screen reads the FILED span: the code-owned date sentence says "SEC", which the
        # legal-matter rule pairs with a commercial "settle" ("notes that settle in shares").
        if (origin == ORIGIN_SEC_PEG
                and _peg_screen(fact.id, split_peg_date(text)[1], subject_names) is not None):
            return None
    else:
        problem = _library_problem(fact, text, library, private_subjects)
        if problem is not None:
            return None
    return Fact(id=fact.id, text=text, origin=origin, url=fact.url, accession=accession,
                entry_id=fact.entry_id)


def _library_problem(
    fact: Fact, text: str, library: Optional[CaseLibrary], private_subjects: Sequence[str]
) -> Optional[str]:
    entry = library.entry(fact.entry_id) if (library is not None and fact.entry_id) else None
    if entry is None or not entry.usable:
        logger.error(
            "[learn-sheet-library] %s: entry %r is %s — dropped (only verified, eligible "
            "entries feed a sheet)",
            fact.id, fact.entry_id, "unknown" if entry is None else "not usable",
        )
        return "unverified"
    source = next(
        (f for f in entry.facts
         if _case_library.library_fact_id(entry.id, f.id) == fact.id),
        None,
    )
    if source is None or _collapse(source.text) != text or source.source.url != fact.url:
        logger.error(
            "[learn-sheet-library] %s does not match a fact of entry %s (id, text or source "
            "differ) — dropped",
            fact.id, entry.id,
        )
        return "altered"
    problem = _counterparty_problem(
        Fact(id=fact.id, text=text, origin=fact.origin, url=fact.url, entry_id=fact.entry_id),
        entry,
        private_subjects,
    )
    if problem is not None:
        logger.warning("[learn-sheet-counterparty] library fact %s dropped: %s",
                       fact.id, problem)
        return "counterparty"
    return None
