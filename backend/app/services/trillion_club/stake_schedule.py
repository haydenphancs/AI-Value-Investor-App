"""Schedule 13D / 13G (structured XML) — the stake finder's S1 source, a member's OWN filing.

Pure: stdlib ``xml.etree.ElementTree`` + ``decimal`` over ONE decoded string. No I/O, no LLM,
no FMP. Two steps, kept apart on purpose:

* ``parse_schedule_xml(text)`` reads the document into a ``ScheduleFiling``, or raises
  ``ScheduleParseError`` (``.code`` ``schedule_unparseable`` / ``legacy_schedule``). It
  decides nothing about any member.
* ``select_member_position(filing, member)`` decides whether the filing is that member's own
  plain-share position. Its ``PositionResult.reason`` is a ``stake_contracts.REASONS`` code
  whenever it is not.

What the live filings taught (recorded under ``tests/fixtures/trillion_club_stakes/xml``,
read 2026-10-28):

* Two schemas spell everything differently. 13G: ``issuerCik``, ``classPercent``,
  ``reportingPersonBeneficiallyOwnedAggregateNumberOfShares``, ``comments``. 13D:
  ``issuerCIK``, ``percentOfClass``, ``aggregateAmountOwned``, ``commentContent``. The CUSIP
  sits in ``issuerCusips/issuerCusipNumber`` (schema X0202, once with an EMPTY second
  element — TRex) or in a bare ``issuerCusip`` / ``issuerCUSIP`` (older files — CoreWeave,
  Aktis).
* Namespaces vary: ``sch:``-prefixed (NVIDIA's Workiva files) or a default namespace.
  Elements are found by LOCAL name along fixed paths, never by a document-wide search: the
  signature block repeats ``reportingPersonName``.
* A joint filing lists the member's whole chain (Google LLC, XXVI Holdings Inc. and
  Alphabet Inc., each with the same 551,189,500 SpaceX shares, all of it SHARED power). The
  numbers come from ONE line, never a sum and never the largest. The chain is the member's
  own names, its listed affiliates' and its reviewed ``MemberConfig.intermediates``
  (XXVI Holdings Inc.). Every other reporting person is OUTSIDE it: power shared with one
  (a fund, a co-investor) means the block is not simply the member's own, and a line that
  can vote the block but not sell (all of) it (an acquirer's support agreements) does not
  hold it. Both go to the owner. So does a chain of ONE entity (the member, or one affiliate,
  alone) that reports shared power, or does not say: no co-reporting line of its chain is the
  one it shares with, so the sharer is someone the filing does not name — an acquirer's
  support-agreement 13D filed without its merger sub looks exactly like that (review A).
* A member can report a block it says is not its own: "disclaims beneficial ownership of
  these shares", "may be deemed to own them solely because of the Support Agreements". Its
  position texts (the ones the composition scan reads) are scanned for that, positionally
  (``_scan_disclaimer``): a disclaimer of group MEMBERSHIP never counts; one qualified by
  "except to the extent of its pecuniary interest therein", or naming only shares held inside
  the member's own reviewed chain (Google LLC for Alphabet), is chain boilerplate; one naming
  any other holder's shares fails closed, because nothing in the text says whether those
  shares are inside the reported count.
* "Exercise" and "option" are not always a derivative: Lilly's Aktis and Scribe 13Ds compute
  the percent "after giving effect to the full exercise of the underwriters' option". Only
  narrow, POSITIONAL phrases like that are exempt (``_EXEMPT_RES``). Any other mention of a
  warrant, option, convertible or conversion, preferred stock, RSUs, an exchangeable
  interest or shares received "upon (the) (redemption or) exchange of" one, a right to
  acquire / purchase / receive / subscribe for shares, or underlying shares sends the
  position to the owner. Bare "exchange" never counts: nearly every filing names the
  Securities and Exchange Commission. A new honest phrase that trips the scan costs the
  owner a tap, never a wrong row: add it as one more exemption with a must-reject twin in
  the tests.
* The cover's percent is the one that counts: Ionetix's Item 4 says 7.98% beside a cover 7.9.
* An all-zero CUSIP (Ionetix ``000000000``) passes the check digit. It is no identity key,
  and the position is still read (``cusip`` None).

Security: the text is refused BEFORE parsing when it carries any markup declaration
(``<!DOCTYPE``, ``<!ENTITY`` …, any case) — with no DTD there are no entities to expand and
nothing external to fetch — or a NUL character, which is how a UTF-16 document decoded as
single-byte text arrives (its ``<!DOCTYPE`` would otherwise hide behind the NULs). The parse
itself is a pull parse that stops past ``MAX_XML_DEPTH`` levels or ``MAX_XML_ELEMENTS``
elements.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.services.trillion_club.stake_contracts import (
    AFFILIATE_EXTRA_TYPES,
    AUTO_REPORTING_TYPES,
    REASONS,
    MemberConfig,
)
from app.services.trillion_club.stake_names import class_basis, cusip_identity, fold_entity_name

# ── Codes (each one a stake_contracts.REASONS key; a test pins that) ─────────────────────

CODE_UNPARSEABLE = "schedule_unparseable"
CODE_LEGACY = "legacy_schedule"
PARSE_ERROR_CODES: Tuple[str, ...] = (CODE_UNPARSEABLE, CODE_LEGACY)

REASON_ABOUT_MEMBER = "about_member"
REASON_LINE_MISSING = "member_line_missing"
REASON_LINES_DISAGREE = "member_lines_disagree"
REASON_REPORTING_TYPE = "reporting_type"
REASON_GROUP_MEMBER = "group_member"
REASON_CLASS_UNMAPPED = "class_unmapped"
REASON_EXIT = "exit_filing"
REASON_ABOVE_50 = "percent_above_50"
REASON_DERIVATIVE = "derivative_position"
REASON_SHARED_POWER = "shared_power"
REASON_NO_DISPOSITIVE = "no_dispositive_power"
REASON_DISCLAIMED = "ownership_disclaimed"
#: Every reason ``select_member_position`` can return (the check order is in its docstring).
POSITION_REASONS: Tuple[str, ...] = (
    REASON_ABOUT_MEMBER, CODE_UNPARSEABLE, REASON_LINE_MISSING, REASON_LINES_DISAGREE,
    REASON_REPORTING_TYPE, REASON_GROUP_MEMBER, REASON_CLASS_UNMAPPED, REASON_EXIT,
    REASON_ABOVE_50, REASON_DERIVATIVE, REASON_SHARED_POWER, REASON_NO_DISPOSITIVE,
    REASON_DISCLAIMED,
)

# ── Limits ───────────────────────────────────────────────────────────────────────────────

#: The decoded document, in characters. A real 13D/13G is 4-20 KB.
MAX_SCHEDULE_XML_CHARS = 2 * 1024 * 1024
#: Nesting and element caps of the pull parse (a real one: depth ~7, ~100-500 elements).
MAX_XML_DEPTH = 40
MAX_XML_ELEMENTS = 20_000
_FEED_CHUNK_CHARS = 64 * 1024
#: How much of the head is sniffed for HTML / SGML / plain text.
_SNIFF_CHARS = 8 * 1024
#: The filer's sentence quoted when the warrant shares cannot be counted (or when the
#: position text disclaims the block).
MAX_QUOTE_CHARS = 300
#: "disclaim" words read in one position's texts. A real filing has one to three; past this
#: the scan stops and fails closed (``ownership_disclaimed``), never reading on unbounded.
MAX_DISCLAIMER_HITS = 20
#: The S1 percent band: below it is an exit, above it a likely controlled company.
MIN_PERCENT = Decimal("5")
MAX_PERCENT = Decimal("50")

SCHEMA_13G = "13G"
SCHEMA_13D = "13D"
SUBMISSION_SCHEMAS: Dict[str, str] = {
    "SCHEDULE 13G": SCHEMA_13G,
    "SCHEDULE 13G/A": SCHEMA_13G,
    "SCHEDULE 13D": SCHEMA_13D,
    "SCHEDULE 13D/A": SCHEMA_13D,
}


# ── Results ──────────────────────────────────────────────────────────────────────────────

class ScheduleParseError(ValueError):
    """The text cannot be read as a structured Schedule 13D/13G.

    ``code`` is a ``stake_contracts.REASONS`` key: ``legacy_schedule`` for an old-format
    (HTML / plain-text) schedule, ``schedule_unparseable`` for everything else.
    """

    def __init__(self, code: str, message: str) -> None:
        if code not in PARSE_ERROR_CODES:
            raise ValueError(f"unknown schedule parse error code {code!r}")
        super().__init__(code, message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class ReportingPerson:
    """One cover-page line (rows 1-14 of the schedule) as filed. A number that is missing or
    does not parse strictly is None; ``type`` is the comma-joined type codes ("CO",
    "CO,HC"); ``member_of_group`` is the raw group-box value ("a", "b") or None."""

    name: str
    type: str
    shares: Optional[Decimal]
    percent: Optional[Decimal]
    comments: str
    member_of_group: Optional[str]
    sole_voting: Optional[Decimal]
    shared_voting: Optional[Decimal]
    sole_dispositive: Optional[Decimal]
    shared_dispositive: Optional[Decimal]


@dataclass(frozen=True)
class ScheduleFiling:
    """What ``parse_schedule_xml`` read. ``item4_text`` is the position text the composition
    and disclaimer scans read: the 13G's Item 4 (Ownership), or the 13D's Item 5(a)-(b) —
    never the Item 3 / 4 / 6 history. ``five_percent_or_less`` is the 13G's Item 5 box, or
    for a 13D whether Item 5(e) gives the date the person stopped holding more than 5%."""

    schema: str                      # "13G" | "13D"
    submission_type: str             # "SCHEDULE 13G/A" …
    issuer_cik: Optional[str]        # 10 digits
    issuer_name: Optional[str]
    cusips: Tuple[str, ...]          # every non-empty CUSIP element, as filed (upper-cased)
    class_title: Optional[str]
    event_date: Optional[date]
    persons: Tuple[ReportingPerson, ...]
    item4_text: str
    five_percent_or_less: Optional[bool]
    filer_ciks: Tuple[str, ...]      # the header's filer CIKs, 10 digits


@dataclass(frozen=True)
class PositionResult:
    """The member's position in one filing.

    ``ok`` is True only when ``reason`` is None. ``person`` / ``shares`` / ``percent`` /
    ``basis`` / ``cusip`` are filled as far as the checks got, so an exit or a proposal can
    name its numbers. ``derivative_note`` ("incl. 21.1M warrant shares", for the basis) or —
    when the warrant shares cannot be counted — ``derivative_quote`` (the filer's sentence,
    at most ``MAX_QUOTE_CHARS``) is set whenever the composition scan found a derivative,
    WHATEVER the final reason: a proposal for another reason must still name the warrants.
    ``disclaimer_quote`` is the filer's sentence (at most ``MAX_QUOTE_CHARS``) whenever the
    disclaimer scan found the block disclaimed — whatever the final reason too, so it is never
    set on an ``ok`` result. It is the text behind ``ownership_disclaimed``.
    """

    ok: bool
    reason: Optional[str]
    person: Optional[ReportingPerson]
    shares: Optional[int]
    percent: Optional[Decimal]
    basis: Optional[str]
    cusip: Optional[str]
    derivative_note: Optional[str]
    derivative_quote: Optional[str]
    disclaimer_quote: Optional[str] = None

    def __post_init__(self) -> None:
        if self.ok != (self.reason is None):
            raise ValueError(f"PositionResult: ok={self.ok} with reason {self.reason!r}")
        if self.reason is not None and self.reason not in REASONS:
            raise ValueError(f"PositionResult: {self.reason!r} is not a REASONS code")
        if self.ok and self.disclaimer_quote is not None:
            raise ValueError("PositionResult: an ok position whose text disclaims the block")


# ── Before the parse: refuse what must never reach the XML parser ────────────────────────

_ENTITY_RE = re.compile(r"<!\s{0,10}entity", re.IGNORECASE)
_DTD_RE = re.compile(r"<!\s{0,10}(?:doctype|entity)", re.IGNORECASE)
# Any markup declaration (DOCTYPE, ENTITY, ELEMENT, ATTLIST, NOTATION — any case); comments
# and CDATA sections are not declarations.
_MARKUP_DECL_RE = re.compile(r"<!(?!--|\[CDATA\[)")
_HTML_DECL_RE = re.compile(r"<!doctype\s{1,10}html\b|<html(?=[\s>/])", re.IGNORECASE)
# HTML / EDGAR-SGML tags no schedule XML element is named (``<title>`` IS one: the signature).
# The tag must END there, so a namespace prefix (``<p:foo>``) is never taken for one.
_HTML_TAG_RE = re.compile(
    r"<(?:body|head|div|table|font|p|br|center|pre|sec-document|sec-header|document|type)(?=[\s>/])",
    re.IGNORECASE,
)
_TEXT_SCHEDULE_RE = re.compile(r"\b(?:schedule|sc)\s{1,5}13[dg]\b", re.IGNORECASE)


def _refuse(code: str, message: str) -> ScheduleParseError:
    return ScheduleParseError(code, message)


def _sniff(text: Any) -> str:
    """The text the XML parser may see, or ``ScheduleParseError``."""
    if not isinstance(text, str):
        raise _refuse(CODE_UNPARSEABLE, f"expected the decoded document as str, got {type(text).__name__}")
    if len(text) > MAX_SCHEDULE_XML_CHARS:
        raise _refuse(
            CODE_UNPARSEABLE,
            f"{len(text)} characters, over the {MAX_SCHEDULE_XML_CHARS}-character cap",
        )
    body = text.lstrip("\ufeff \t\r\n")
    if not body:
        raise _refuse(CODE_UNPARSEABLE, "empty document")
    if "\x00" in body:
        # NUL is never legal XML: this is a UTF-16/32 document decoded as single-byte text.
        flat = body.replace("\x00", "")
        what = "a markup declaration (<!DOCTYPE/<!ENTITY)" if _DTD_RE.search(flat) else "NUL characters"
        raise _refuse(CODE_UNPARSEABLE, f"the text holds {what} — a UTF-16/32 document decoded as bytes?")
    if _ENTITY_RE.search(body):
        raise _refuse(CODE_UNPARSEABLE, "an entity declaration (<!ENTITY) — refused before parsing")
    head = body[:_SNIFF_CHARS]
    if _HTML_DECL_RE.search(head):
        raise _refuse(CODE_LEGACY, "an HTML document, not a structured schedule")
    if not body.startswith("<?xml"):
        if not body.startswith("<"):
            if _TEXT_SCHEDULE_RE.search(head):
                raise _refuse(CODE_LEGACY, "a plain-text schedule, not a structured one")
            raise _refuse(CODE_UNPARSEABLE, "not XML (no leading '<')")
        if _HTML_TAG_RE.search(head):
            raise _refuse(CODE_LEGACY, "HTML or EDGAR SGML markup, not a structured schedule")
    if _MARKUP_DECL_RE.search(body):
        raise _refuse(CODE_UNPARSEABLE, "a markup declaration (<!DOCTYPE …) — refused before parsing")
    return body


def _pull_parse(body: str) -> ET.Element:
    """The root element, built by a pull parse that stops past the depth / element caps."""
    parser = ET.XMLPullParser(events=("start", "end"))
    root: Optional[ET.Element] = None
    depth = 0
    count = 0

    def drain() -> None:
        nonlocal root, depth, count
        for event, element in parser.read_events():
            if event == "start":
                count += 1
                depth += 1
                if root is None:
                    root = element
                if depth > MAX_XML_DEPTH:
                    raise _refuse(CODE_UNPARSEABLE, f"nested deeper than {MAX_XML_DEPTH} levels")
                if count > MAX_XML_ELEMENTS:
                    raise _refuse(CODE_UNPARSEABLE, f"more than {MAX_XML_ELEMENTS} elements")
            else:
                depth -= 1

    try:
        for i in range(0, len(body), _FEED_CHUNK_CHARS):
            parser.feed(body[i:i + _FEED_CHUNK_CHARS])
            drain()
        parser.close()
        drain()
    except ScheduleParseError:
        raise
    except ET.ParseError as exc:
        raise _refuse(CODE_UNPARSEABLE, f"not well-formed XML ({exc})") from exc
    except (ValueError, UnicodeError) as exc:
        # A lone surrogate cannot be encoded for expat; nothing else here raises these.
        raise _refuse(CODE_UNPARSEABLE, f"unreadable text ({type(exc).__name__}: {exc})") from exc
    if root is None:
        raise _refuse(CODE_UNPARSEABLE, "no root element")
    return root


# ── Element access by LOCAL name (namespace-agnostic) ────────────────────────────────────

def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _children(parent: Optional[ET.Element], *names: str) -> List[ET.Element]:
    if parent is None:
        return []
    return [child for child in parent if _local(child.tag) in names]


def _child(parent: Optional[ET.Element], *names: str) -> Optional[ET.Element]:
    found = _children(parent, *names)
    return found[0] if found else None


def _raw_text(element: Optional[ET.Element]) -> str:
    """The element's text with its line breaks (comments, Item 4), stripped."""
    return "".join(element.itertext()).strip() if element is not None else ""


def _flat_text(element: Optional[ET.Element]) -> str:
    """The element's text on one line (names, titles, numbers)."""
    return " ".join(_raw_text(element).split())


# ── Values ───────────────────────────────────────────────────────────────────────────────

_CIK_RE = re.compile(r"[0-9]{1,10}")
_SHARES_RE = re.compile(r"(?:[0-9]{1,15}|[0-9]{1,3}(?:,[0-9]{3}){1,4})(?:\.[0-9]{1,6})?")
_PERCENT_RE = re.compile(r"([0-9]{1,3}(?:\.[0-9]{1,6})?)\s{0,2}%?")
_DATE_RE = re.compile(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})")
_NOT_APPLICABLE_RE = re.compile(r"(?:n/?a|not\s{1,3}applicable|none)\.?", re.IGNORECASE)
_ANY_DATE_RE = re.compile(
    r"\b[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}\b|\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b|"
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]{0,6}\.?\s{1,3}[0-9]{1,2},?\s{1,3}[0-9]{4}\b",
    re.IGNORECASE,
)
_TYPE_SPLIT_RE = re.compile(r"[\s,;/]+")
_CUSIP_MAX_CHARS = 20


def _cik10(raw: str, what: str) -> Optional[str]:
    """``"1513845"`` → ``"0001513845"``; None when empty; a malformed or zero CIK raises."""
    text = raw.strip()
    if not text:
        return None
    if not _CIK_RE.fullmatch(text) or int(text) == 0:
        raise _refuse(CODE_UNPARSEABLE, f"{what} {text[:40]!r} is not a CIK")
    return text.zfill(10)


def _shares(element: Optional[ET.Element]) -> Optional[Decimal]:
    """A share count: plain or comma-grouped digits, decimals allowed only when integral
    (``22256412.00`` is 22,256,412). None for anything else ("N/A", "100.5", "-3")."""
    text = _flat_text(element)
    if not text or not _SHARES_RE.fullmatch(text):
        return None
    value = Decimal(text.replace(",", ""))
    if value != value.to_integral_value():
        return None
    return Decimal(int(value))


def _percent(element: Optional[ET.Element]) -> Optional[Decimal]:
    """A cover percent, strictly: ``9.3`` or ``9.3%``, at most 100. None for anything else
    ("Less than 5%", "N/A", "7.98% of …")."""
    m = _PERCENT_RE.fullmatch(_flat_text(element))
    if not m:
        return None
    value = Decimal(m.group(1))
    return value if value <= 100 else None


def _event_date(element: Optional[ET.Element]) -> Optional[date]:
    """MM/DD/YYYY → a date; None when absent; anything else present raises."""
    text = _flat_text(element)
    if not text:
        return None
    m = _DATE_RE.fullmatch(text)
    if not m:
        raise _refuse(CODE_UNPARSEABLE, f"event date {text[:40]!r} is not MM/DD/YYYY")
    month, day, year = (int(g) for g in m.groups())
    if not 1900 <= year <= 2200:
        raise _refuse(CODE_UNPARSEABLE, f"event date {text!r} is out of range")
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise _refuse(CODE_UNPARSEABLE, f"event date {text!r} is not a calendar date") from exc


def _yes_no(element: Optional[ET.Element]) -> Optional[bool]:
    text = _flat_text(element).upper()
    if text in ("Y", "YES", "TRUE"):
        return True
    if text in ("N", "NO", "FALSE"):
        return False
    return None


def _ceased_five_percent(element: Optional[ET.Element]) -> Optional[bool]:
    """13D Item 5(e): a date means the person stopped holding more than 5% (True); "N/A"
    means it did not (False); anything else is unknown (None) and the percent decides."""
    text = _flat_text(element)
    if not text:
        return None
    if _NOT_APPLICABLE_RE.fullmatch(text):
        return False
    return True if _ANY_DATE_RE.search(text) else None


def _type_codes(person: ET.Element) -> str:
    codes: List[str] = []
    for element in _children(person, "typeOfReportingPerson"):
        for token in _TYPE_SPLIT_RE.split(_flat_text(element).upper()):
            if token and token not in codes:
                codes.append(token)
    return ",".join(codes)


def _group_value(person: ET.Element) -> Optional[str]:
    """Row 2's group box. None of the recorded filings fills it, and the schemas' spelling
    (``memberOfGroup`` / ``memberGroup``) is not pinned by any of them, so the first element
    of the line whose local name holds "group" is read — never a box silently missed."""
    for element in person.iter():
        if element is not person and "group" in _local(element.tag).casefold():
            return _flat_text(element) or None
    return None


def _group_checked(value: Optional[str]) -> bool:
    """Row 2(a) — "a member of a group", affirmed. 2(b) (a disclaimer) and an empty box are
    not; any value this code does not know counts as checked (fail closed)."""
    if value is None:
        return False
    v = value.strip().strip("()[]").strip().casefold()
    return v not in ("", "b", "n", "no", "false", "0", "none", "n/a")


def _cusips(issuer: Optional[ET.Element]) -> Tuple[str, ...]:
    elements = _children(_child(issuer, "issuerCusips"), "issuerCusipNumber")
    elements += _children(issuer, "issuerCusip", "issuerCUSIP")
    out: List[str] = []
    for element in elements:
        value = _flat_text(element).upper()
        # An empty element (TRex's second issuerCusipNumber) is skipped; so is a value far
        # too long to be a CUSIP — neither is ever an identity key.
        if value and len(value) <= _CUSIP_MAX_CHARS and value not in out:
            out.append(value)
    return tuple(out)


def _person_13g(element: ET.Element) -> ReportingPerson:
    powers = _child(element, "reportingPersonBeneficiallyOwnedNumberOfShares")
    return ReportingPerson(
        name=_flat_text(_child(element, "reportingPersonName")),
        type=_type_codes(element),
        shares=_shares(_child(element, "reportingPersonBeneficiallyOwnedAggregateNumberOfShares")),
        percent=_percent(_child(element, "classPercent")),
        comments=_raw_text(_child(element, "comments", "commentContent")),
        member_of_group=_group_value(element),
        sole_voting=_shares(_child(powers, "soleVotingPower")),
        shared_voting=_shares(_child(powers, "sharedVotingPower")),
        sole_dispositive=_shares(_child(powers, "soleDispositivePower")),
        shared_dispositive=_shares(_child(powers, "sharedDispositivePower")),
    )


def _person_13d(element: ET.Element) -> ReportingPerson:
    return ReportingPerson(
        name=_flat_text(_child(element, "reportingPersonName")),
        type=_type_codes(element),
        shares=_shares(_child(element, "aggregateAmountOwned")),
        percent=_percent(_child(element, "percentOfClass")),
        comments=_raw_text(_child(element, "commentContent", "comments")),
        member_of_group=_group_value(element),
        sole_voting=_shares(_child(element, "soleVotingPower")),
        shared_voting=_shares(_child(element, "sharedVotingPower")),
        sole_dispositive=_shares(_child(element, "soleDispositivePower")),
        shared_dispositive=_shares(_child(element, "sharedDispositivePower")),
    )


def _namespace_schema(tag: str) -> Optional[str]:
    if not tag.startswith("{"):
        return None
    uri = tag[1:].split("}", 1)[0].casefold().rstrip("/")
    if uri.endswith("/schedule13g"):
        return SCHEMA_13G
    if uri.endswith("/schedule13d"):
        return SCHEMA_13D
    return None


def _joined(parts: Iterable[str]) -> str:
    return "\n\n".join(p for p in (s.strip() for s in parts) if p)


def parse_schedule_xml(text: str) -> ScheduleFiling:
    """Read one structured Schedule 13D/13G (``primary_doc.xml``, already decoded).

    Raises ``ScheduleParseError``: ``legacy_schedule`` for an HTML / plain-text / SGML
    schedule (or a structured file whose type says "SC 13…"), ``schedule_unparseable`` for
    a non-str, an oversized or empty text, a markup declaration, NUL characters, malformed
    XML, a root that is not ``edgarSubmission``, an unknown submission type, a namespace
    that contradicts the type, no reporting person, or a malformed CIK / event date.
    A malformed NUMBER on a line is not an error here: it reads as None, and
    ``select_member_position`` decides whether that line matters.
    """
    root = _pull_parse(_sniff(text))
    if _local(root.tag) == "html":
        raise _refuse(CODE_LEGACY, "an XHTML document, not a structured schedule")
    if _local(root.tag) != "edgarSubmission":
        raise _refuse(CODE_UNPARSEABLE, f"root element {_local(root.tag)[:40]!r}, not edgarSubmission")
    header = _child(root, "headerData")
    form = _child(root, "formData")
    if header is None or form is None:
        raise _refuse(CODE_UNPARSEABLE, "no headerData / formData")

    submission_type = " ".join(_flat_text(_child(header, "submissionType")).upper().split())
    schema = SUBMISSION_SCHEMAS.get(submission_type)
    if schema is None:
        if submission_type.startswith("SC 13"):
            raise _refuse(CODE_LEGACY, f"submission type {submission_type!r}")
        raise _refuse(CODE_UNPARSEABLE, f"submission type {submission_type[:40]!r} is not a 13D/13G")
    declared = _namespace_schema(root.tag)
    if declared is not None and declared != schema:
        raise _refuse(CODE_UNPARSEABLE, f"a {submission_type} in the Schedule {declared} namespace")

    cover = _child(form, "coverPageHeader")
    if cover is None:
        raise _refuse(CODE_UNPARSEABLE, "no coverPageHeader")
    issuer = _child(cover, "issuerInfo")
    filer_ciks: List[str] = []
    for filer in _children(_child(header, "filerInfo"), "filer"):
        cik = _cik10(_flat_text(_child(_child(filer, "filerCredentials"), "cik")), "filer CIK")
        if cik and cik not in filer_ciks:
            filer_ciks.append(cik)

    if schema == SCHEMA_13G:
        persons = tuple(_person_13g(p) for p in _children(form, "coverPageHeaderReportingPersonDetails"))
        items = _child(form, "items")
        item4 = _child(items, "item4")
        item4_text = _joined(item4.itertext()) if item4 is not None else ""
        five_or_less = _yes_no(_child(_child(items, "item5"), "classOwnership5PercentOrLess"))
    else:
        persons = tuple(_person_13d(p) for p in _children(_child(form, "reportingPersons"), "reportingPersonInfo"))
        item5 = _child(_child(form, "items1To7"), "item5")
        item4_text = _joined(
            _raw_text(_child(item5, name)) for name in ("percentageOfClassSecurities", "numberOfShares")
        )
        five_or_less = _ceased_five_percent(_child(item5, "date5PercentOwnership"))
    if not persons:
        raise _refuse(CODE_UNPARSEABLE, f"a {submission_type} with no reporting person")

    return ScheduleFiling(
        schema=schema,
        submission_type=submission_type,
        issuer_cik=_cik10(_flat_text(_child(issuer, "issuerCik", "issuerCIK")), "issuer CIK"),
        issuer_name=_flat_text(_child(issuer, "issuerName")) or None,
        cusips=_cusips(issuer),
        class_title=_flat_text(_child(cover, "securitiesClassTitle")) or None,
        event_date=_event_date(_child(cover, "eventDateRequiresFilingThisStatement", "dateOfEvent")),
        persons=persons,
        item4_text=item4_text,
        five_percent_or_less=five_or_less,
        filer_ciks=tuple(filer_ciks),
    )


# ── Composition: does the position include warrants, options or convertibles? ───────────

# Every word that can make a reported position more than plain shares. "As-converted" is
# caught by "converted". Bare "exchange" is never one (nearly every filing names the
# Securities and Exchange Commission or the Exchange Act): only an exchangeable interest,
# or shares received "upon (the) (redemption or) exchange of" something, counts.
_DERIVATIVE_RE = re.compile(
    r"\b(?:pre-?\s?funded|warrants?|exercis[a-z]{0,8}|issuable|convertibles?|"
    r"conver(?:t|ts|ted|ting|sion|sions)|options?|preferred|rsus?|"
    r"restricted\s{1,3}(?:stock|share)\s{1,3}units?|exchangeable|"
    r"upon\s{1,3}(?:the\s{1,3})?(?:redemption\s{1,3}or\s{1,3})?exchange\s{1,3}of|"
    r"rights?\s{1,3}to\s{1,3}(?:acquire|purchase|receive|subscribe)|underlying)\b",
    re.IGNORECASE,
)
#: The trigger words a warrant position is written with ("21,065,936 shares issuable upon
#: exercise of a pre-funded warrant"). The basis may name a parsed warrant count only when
#: EVERY trigger hit is one of these (``fullmatch``): any other — an option, a conversion,
#: preferred stock, RSUs, an exchange, a right to buy — means the warrant count alone does
#: not describe the position, so the filer is quoted instead.
_WARRANT_WORD_RE = re.compile(r"pre-?\s?funded|warrants?|exercis[a-z]{0,8}|issuable|underlying", re.IGNORECASE)
# Words that, beside a warrant, mean the warrant count alone does not describe the position:
# the basis would name the warrants and silently hide the rest, so the filer is quoted.
_NOTE_BLOCKER_RE = re.compile(
    r"\b(?:convertibles?|conver(?:t|ts|ted|ting|sion|sions)|options?|rights?\s{1,3}to\s{1,3}acquire|"
    r"preferred|notes?|debentures?|units?|rsus?|restricted\s{1,3}stock|stock\s{1,3}appreciation)\b",
    re.IGNORECASE,
)

_APOS = "['\u2019]"
_UNDERWRITERS = r"underwriter(?:s" + _APOS + r"?|" + _APOS + r"s)?"
_ALLOTMENT = r"(?:(?:over-?\s?allotment|overallotment|greenshoe)\s{1,3})?"
_IN_FULL = r"(?:in\s{1,3}(?:full|part)\s{1,3})?"
_EXERCISE = r"exercis(?:e|es|ed|ing)"
_POWER = r"(?:(?:sole|shared)\s{1,3})?(?:voting|dispositive|investment)"
#: POSITIONAL exemptions: a derivative word is ignored only when it lies wholly inside one of
#: these spans. Each describes the issuer's IPO share count or the holder's voting power,
#: never a derivative the member holds.
_EXEMPT_RES: Tuple["re.Pattern[str]", ...] = (
    # "the underwriters' option", "the underwriters’ over-allotment option"
    re.compile(r"\b" + _UNDERWRITERS + r"\s{1,3}" + _ALLOTMENT + r"options?\b", re.IGNORECASE),
    # "the over-allotment option" / "greenshoe option": always the underwriters'
    re.compile(r"\b(?:over-?\s?allotment|overallotment|greenshoe)\s{1,3}options?\b", re.IGNORECASE),
    # "(the full) exercise (in full) of the underwriters' (over-allotment) option"
    re.compile(
        r"\b" + _EXERCISE + r"\s{1,3}" + _IN_FULL + r"of\s{1,3}(?:the|their)\s{1,3}" + _UNDERWRITERS
        + r"\s{1,3}" + _ALLOTMENT + r"options?\b",
        re.IGNORECASE,
    ),
    # "exercise of the over-allotment option" (an over-allotment option is the underwriters')
    re.compile(
        r"\b" + _EXERCISE + r"\s{1,3}" + _IN_FULL + r"of\s{1,3}(?:the|their|its)\s{1,3}"
        r"(?:over-?\s?allotment|overallotment|greenshoe)\s{1,3}options?\b",
        re.IGNORECASE,
    ),
    # "exercise (in full) by the underwriters of their option"
    re.compile(
        r"\b" + _EXERCISE + r"\s{1,3}" + _IN_FULL + r"by\s{1,3}the\s{1,3}underwriters?\s{1,3}"
        r"(?:in\s{1,3}full\s{1,3})?of\s{1,3}(?:their|its|the)\s{1,3}" + _ALLOTMENT + r"options?\b",
        re.IGNORECASE,
    ),
    # "the underwriters (have) exercised (in full) their option"
    re.compile(
        r"\bunderwriters?\s{1,3}(?:(?:have|has|had)\s{1,3})?(?:(?:fully|partially)\s{1,3})?"
        r"exercised\s{1,3}(?:in\s{1,3}full\s{1,3})?(?:their|its|the)\s{1,3}" + _ALLOTMENT + r"options?\b",
        re.IGNORECASE,
    ),
    # "the underwriters' (full) exercise (in full) of their option"
    re.compile(
        r"\b" + _UNDERWRITERS + r"\s{1,3}(?:(?:full|partial)\s{1,3})?exercise\s{1,3}" + _IN_FULL
        + r"of\s{1,3}(?:their|its|the)\s{1,3}" + _ALLOTMENT + r"options?\b",
        re.IGNORECASE,
    ),
    # "exercises sole voting and dispositive power" — power over shares, not a derivative
    re.compile(
        r"\b" + _EXERCISE + r"\s{1,3}(?:of\s{1,3})?" + _POWER + r"\s{1,3}(?:(?:and|or|and/or)\s{1,3}"
        + _POWER + r"\s{1,3})?(?:power|control|authority)\b",
        re.IGNORECASE,
    ),
    # "the right to receive (, or the power to direct the receipt of,) dividends" — the
    # schedules' own Item 6 / Item 5(d) wording about who is paid the dividends on shares
    # already held, never a right to acquire shares
    re.compile(
        r"\brights?\s{1,3}to\s{1,3}receive,?\s{1,3}(?:or\s{1,3}(?:the\s{1,3})?power\s{1,3}to\s{1,3}"
        r"direct\s{1,3}the\s{1,3}receipt\s{1,3}of,?\s{1,3})?(?:the\s{1,3})?dividends\b",
        re.IGNORECASE,
    ),
)

# A warrant count: "21,065,936 Class A ordinary shares issuable upon exercise of a
# pre-funded Class A ordinary share purchase warrant" (Nebius), "41,011,803 shares of Common
# Stock that are issuable upon the exercise of warrants" (TeraWulf), "… shares underlying
# the Warrant", "a warrant (the "Warrant") to purchase up to 58,970,907 shares". The words
# between "exercise of" and "warrant" may not hold a number or another instrument.
_COUNT = r"(?<![0-9,.$])(?P<n>[0-9]{1,3}(?:,[0-9]{3}){1,4}|[0-9]{1,15})(?![0-9])"
_SHARE_NOUN = (
    r"(?:(?:class\s{1,3}[a-z]\s{1,3})?(?:ordinary|common)\s{1,3}(?:shares?|stock)"
    r"|shares?(?:\s{1,3}of\s{1,3}(?:the\s{1,3}issuer" + _APOS + r"s\s{1,3})?(?:class\s{1,3}[a-z]\s{1,3})?"
    r"(?:common|ordinary)\s{1,3}(?:stock|shares))?)"
)
_GAP_WORD = (
    r"(?:(?!(?:and|or|options?|notes?|rights?|units?|preferred|convertibles?|debentures?)\b)"
    r"[a-z\"\u201c\u201d'\u2019()-]{1,40}\s{1,3})"
)
_WARRANT_COUNT_RES: Tuple["re.Pattern[str]", ...] = (
    re.compile(
        _COUNT + r"\s{1,3}" + _SHARE_NOUN + r"\s{0,3},?\s{1,3}(?:(?:that|which)\s{1,3}are\s{1,3})?"
        r"(?:currently\s{1,3})?issuable\s{1,3}upon\s{1,3}(?:the\s{1,3})?(?:full\s{1,3})?exercise\s{1,3}"
        r"of\s{1,3}" + _GAP_WORD + r"{0,10}?warrants?\b",
        re.IGNORECASE,
    ),
    re.compile(
        _COUNT + r"\s{1,3}" + _SHARE_NOUN + r"\s{1,3}underlying\s{1,3}" + _GAP_WORD + r"{0,10}?warrants?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwarrants?\s{1,3}" + _GAP_WORD + r"{0,6}?(?:to\s{1,3}(?:purchase|acquire)|"
        r"(?:exercisable\s{1,3})?(?:for|into))\s{1,3}(?:up\s{1,3}to\s{1,3})?(?:an\s{1,3}aggregate\s{1,3}of\s{1,3})?"
        + _COUNT + r"\s{1,3}" + _SHARE_NOUN,
        re.IGNORECASE,
    ),
)
# A count in a sentence that excludes or disclaims shares may not be the member's own.
_EXCLUSION_RE = re.compile(
    r"\b(?:exclud[a-z]{0,6}|do(?:es)?\s{1,3}not\s{1,3}include|not\s{1,3}including|other\s{1,3}than|"
    r"disclaim[a-z]{0,6})\b",
    re.IGNORECASE,
)
_MAX_COUNT_MATCHES = 50

#: Sentence ends are searched at most this far from a hit (abbreviations never end one).
_SENTENCE_SPAN = 600
_ABBREVIATIONS = frozenset({
    "inc", "corp", "co", "ltd", "llc", "lp", "plc", "no", "nos", "mr", "ms", "mrs", "dr", "st",
    "jr", "sr", "vs", "approx", "etc", "u.s", "n.v", "l.p", "l.l.c", "s.a", "b.v", "s.e",
})


def _is_abbreviation(text: str, dot: int) -> bool:
    k = dot
    while k > 0 and dot - k < 12 and (text[k - 1].isalpha() or text[k - 1] == "."):
        k -= 1
    token = text[k:dot].casefold().strip(".")
    return len(token.replace(".", "")) == 1 or token in _ABBREVIATIONS


def _sentence_bounds(text: str, start: int, end: int) -> Tuple[int, int]:
    """The sentence holding ``text[start:end]``: from after the previous end (". ", "? ",
    "! " or a blank line) to the next, at most ``_SENTENCE_SPAN`` characters away."""
    lo, hi = max(0, start - _SENTENCE_SPAN), min(len(text), end + _SENTENCE_SPAN)
    s = lo
    for i in range(start - 1, lo - 1, -1):
        ch = text[i]
        if ch == "\n" and i > 0 and text[i - 1] == "\n":
            s = i + 1
            break
        if ch in ".?!" and i + 1 < len(text) and text[i + 1].isspace() and not _is_abbreviation(text, i):
            s = i + 1
            break
    e = hi
    for j in range(end, hi):
        ch = text[j]
        if ch == "\n" and j + 1 < len(text) and text[j + 1] == "\n":
            e = j
            break
        if ch in ".?!" and (j + 1 == len(text) or text[j + 1].isspace()) and not _is_abbreviation(text, j):
            e = j + 1
            break
    return s, e


def _cut_left(s: str, room: int) -> str:
    if len(s) <= room:
        return s
    if room < 2:
        return ""
    tail = s[len(s) - (room - 1):]
    space = tail.find(" ")
    return "\u2026" + (tail[space + 1:] if space >= 0 else "")


def _cut_right(s: str, room: int) -> str:
    if len(s) <= room:
        return s
    if room < 2:
        return ""
    head = s[:room - 1]
    space = head.rfind(" ")
    return (head[:space] if space >= 0 else "") + "\u2026"


def _quote(text: str, start: int, end: int) -> str:
    """The filer's sentence around ``text[start:end]`` on one line, at most
    ``MAX_QUOTE_CHARS``: cut at word boundaries with "…" when longer, the hit always kept."""
    s, e = _sentence_bounds(text, start, end)
    pre = " ".join(text[s:start].split())
    hit = " ".join(text[start:end].split())
    post = " ".join(text[end:e].split())
    left = pre + (" " if pre and text[start - 1].isspace() else "")
    right = (" " if post and end < len(text) and text[end].isspace() else "") + post
    budget = MAX_QUOTE_CHARS - len(hit)
    left = _cut_left(left, budget // 3) if len(left) + len(hit) + len(right) > MAX_QUOTE_CHARS else left
    right = _cut_right(right, budget - len(left))
    return (left + hit + right).strip()


def format_share_count(n: int) -> str:
    """``21065936`` → ``"21.1M"``, ``41011803`` → ``"41.0M"`` (the seed's "3.7M warrant
    shares" style), ``1234567890`` → ``"1.2B"``; under a million, the exact count
    (``"750,000"``)."""
    if n < 1_000_000:
        return f"{n:,}"
    millions = (Decimal(n) / Decimal(1_000_000)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if millions < 1000:
        return f"{millions}M"
    billions = (Decimal(n) / Decimal(1_000_000_000)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{billions}B"


@dataclass(frozen=True)
class _Composition:
    found: bool
    note: Optional[str]
    quote: Optional[str]


_NO_DERIVATIVE = _Composition(False, None, None)


class _Exempt:
    """The merged exempt spans of one text, for O(log n) "is this hit exempt?" checks."""

    def __init__(self, text: str) -> None:
        spans = sorted(m.span() for rx in _EXEMPT_RES for m in rx.finditer(text))
        merged: List[Tuple[int, int]] = []
        for a, b in spans:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        self._spans = merged
        self._starts = [a for a, _ in merged]

    def covers(self, start: int, end: int) -> bool:
        i = bisect_right(self._starts, start) - 1
        return i >= 0 and self._spans[i][1] >= end


def _scan_composition(sources: Sequence[str], shares: Optional[int]) -> _Composition:
    """The composition scan over the member's position texts (its cover comments, then
    Item 4 / 13D Item 5(a)-(b)). ``found`` when any non-exempt derivative word appears.
    ``note`` names the warrant shares only when exactly one warrant count appears, every
    non-exempt trigger word is a warrant word (``_WARRANT_WORD_RE``) and nothing else
    (notes, units, …) is mentioned, no sentence holding the count excludes or disclaims
    shares, and the count is at most the line's ``shares``; otherwise ``quote`` holds the
    filer's sentence around the first hit."""
    texts: List[str] = []
    for text in sources:
        if isinstance(text, str) and text.strip() and text not in texts:
            texts.append(text)
    first: Optional[Tuple[str, int, int]] = None
    # A trigger hit that is not a warrant word: the position holds more than warrants, so
    # no warrant count may describe it (never "incl. 2.0M warrant shares" beside a
    # convertible preferred, an exchangeable unit or a right to buy).
    mixed = False
    exempt: Dict[int, _Exempt] = {}
    for idx, text in enumerate(texts):
        exempt[idx] = _Exempt(text)
        for m in _DERIVATIVE_RE.finditer(text):
            if exempt[idx].covers(*m.span()):
                continue
            if first is None:
                first = (text, m.start(), m.end())
            if not _WARRANT_WORD_RE.fullmatch(m.group(0)):
                mixed = True
                break
        if mixed:
            break
    if first is None:
        return _NO_DERIVATIVE
    if mixed:
        return _Composition(True, None, _quote(*first))

    blocked = False
    unclear = False
    counts: Set[int] = set()
    seen = 0
    for idx, text in enumerate(texts):
        ex = exempt.get(idx) or _Exempt(text)
        if not blocked and any(not ex.covers(*m.span()) for m in _NOTE_BLOCKER_RE.finditer(text)):
            blocked = True
        for rx in _WARRANT_COUNT_RES:
            for m in rx.finditer(text):
                seen += 1
                if seen > _MAX_COUNT_MATCHES:
                    # Never read on without a bound: that many counts is not one warrant.
                    unclear = True
                    break
                s, e = _sentence_bounds(text, m.start(), m.end())
                if _EXCLUSION_RE.search(text, s, e):
                    unclear = True
                counts.add(int(m.group("n").replace(",", "")))
            if seen > _MAX_COUNT_MATCHES:
                break
        if seen > _MAX_COUNT_MATCHES:
            break
    note: Optional[str] = None
    if not blocked and not unclear and len(counts) == 1 and shares is not None:
        (count,) = tuple(counts)
        if 0 < count <= shares:
            note = f"incl. {format_share_count(count)} warrant shares"
    return _Composition(True, note, None if note else _quote(*first))


# ── Disclaimer: does the position text say the block is not the member's own? ────────────
#
# Review A (S1-LONE-SHARED-POWER): an acquirer's support-agreement 13D can report SHARED
# voting and dispositive power over the target's shares with its own line alone on the cover,
# saying it "may be deemed to beneficially own these shares solely because of the Support
# Agreements" and "expressly disclaims beneficial ownership of all of these shares". Read
# positionally over the same texts as the composition scan (cover comments, Item 4 / 13D
# Item 5(a)-(b)). Two shapes count:
#
# 1. A causal phrase whose object is an agreement ("solely because of / by reason of / as a
#    result of / by virtue of / solely due to / on account of … Agreement(s)"), inside one
#    phrase: no sentence, clause or comma break between them, at most 100 characters apart.
#    "pursuant to a Securities Purchase Agreement" (NVIDIA's Nebius 13G), "subject to a
#    lock-up agreement" and a bare "due to" (a percentage change "due to" a share issuance)
#    never count. Accepted over-block: any agreement in the phrase counts, so "the percentage
#    rose as a result of the Issuer's repurchases under the Repurchase Agreement" goes to the
#    owner too (a heads-up, never a wrong row).
# 2. A "disclaim" word whose own clause names ownership (beneficial ownership, being an
#    owner, a pecuniary, beneficial or economic interest) — not group membership. It is chain
#    boilerplate, never a hit, when its clause names only shares held inside the member's
#    reviewed chain ("held by Google LLC" for Alphabet; "held by the other Reporting Persons"
#    when every line is in the chain), or is qualified by "except to the extent of its
#    pecuniary interest therein" and names no holder. Any OTHER holder named anywhere in the
#    disclaimer's sentence ("held by the GV Funds", "held of record by the Supporting
#    Stockholders") is a hit whatever the qualifier says: the text cannot say whether those
#    shares are inside the reported count, so it fails closed. A self-reference ("held by
#    it", the member's own name) names no other holder; the qualifier then decides.

_DISCLAIM_RE = re.compile(r"\bdisclaim(?:s|ed|ing|er|ers)?\b", re.IGNORECASE)
#: What a disclaimer must be ABOUT to count ("membership in a group" names none of these).
_OWNERSHIP_WORD_RE = re.compile(
    r"\b(?:ownership|owners?|beneficially\s{1,3}own(?:s|ed|ing)?|(?:pecuniary|beneficial|economic)\s{1,3}interests?)\b",
    re.IGNORECASE,
)
#: The chain boilerplate's qualifier: "except to the extent of its pecuniary interest therein".
_PECUNIARY_RE = re.compile(
    r"\b(?:except|other\s{1,3}than)\s{1,3}to\s{1,3}the\s{1,3}extent\s{1,3}of\b[^.;:]{0,60}?"
    r"\bpecuniary\s{1,3}interests?\b",
    re.IGNORECASE,
)
#: A named holder of the shares: "held by", "owned directly by", "held of record by", "held through".
_HELD_BY_RE = re.compile(
    r"\b(?:held|owned)\s{1,3}(?:(?:directly\s{1,3}or\s{1,3}indirectly|directly|indirectly|beneficially|jointly|"
    r"of\s{1,3}record)\s{1,3})?(?:by|through)\s{1,3}",
    re.IGNORECASE,
)
#: Where a holder's name ends: punctuation, a period before a space (a legal suffix ends a
#: name), an appositive (", a Delaware …", ", which …") or a word that starts the next phrase.
_HOLDER_STOP_RE = re.compile(
    r"[;:()\[\]\"\u201c\u201d]|\.(?=\s|$)|"
    r",\s{0,3}(?:a|an|the|its|their|which|who|whose|as|being|each|except|other|save|for|in|pursuant|under|with)\b|"
    r"\s(?:except|other\s{1,3}than|save|for|as|in|pursuant|under|with|within|which|who|whose|is|are|was|were|"
    r"has|have|had|may|shall|will|that|to)\b",
    re.IGNORECASE,
)
#: Several holders in one phrase: "Google LLC and XXVI Holdings Inc.", "A, B and C".
_HOLDER_SPLIT_RE = re.compile(r",\s{0,3}(?:and|or)\s{1,3}|,\s{1,3}|\s{1,3}(?:and/or|and|or)\s{1,3}", re.IGNORECASE)
#: Shape 1: held because of an agreement — the causal phrase, then its object, one phrase.
_AGREEMENT_CAUSE_RE = re.compile(
    r"\b(?:solely\s{1,3}because(?:\s{1,3}of)?|because\s{1,3}of|by\s{1,3}(?:reason|virtue)\s{1,3}of|"
    r"as\s{1,3}a\s{1,3}(?:result|consequence)\s{1,3}of|solely\s{1,3}due\s{1,3}to|on\s{1,3}account\s{1,3}of)\b"
    r"[^.;:,()]{0,100}?\bagreements?\b",
    re.IGNORECASE,
)
#: Characters read around a disclaim word (its clause after it; for a passive "disclaimed",
#: before it too), and after "held by" for a holder's name.
_DISCLAIM_WINDOW = 300
_HOLDER_SPAN = 160
#: "held by" phrases read in one window; past this the window counts as naming an outside holder.
_MAX_HOLDERS = 8
#: Folded words dropped before a holder's name ("the", "each of the").
_HOLDER_LEAD_WORDS = frozenset({"the", "each", "any", "all", "of", "such", "its", "their", "his", "her", "both"})
#: References to the reporting person itself: they name no OTHER holder.
_SELF_HOLDERS = frozenset({"it", "itself", "reporting person", "reporting persons", "filing person",
                           "filing persons", "them", "themselves"})
#: References to the filing's other reporting persons: the chain, only when every line is in it.
_OTHER_HOLDERS = frozenset({"other reporting persons", "other reporting person", "another reporting person",
                            "remaining reporting persons", "remaining reporting person"})

_HOLDER_SELF = "self"
_HOLDER_CHAIN = "chain"
_HOLDER_OTHERS = "others"
_HOLDER_OUTSIDE = "outside"


def _holder_key(name: str) -> str:
    """A holder phrase folded, with its leading articles dropped."""
    tokens = fold_entity_name(name).split()
    while tokens and tokens[0] in _HOLDER_LEAD_WORDS:
        tokens.pop(0)
    return " ".join(tokens)


def _holder_kind(key: str, chain_keys: Set[str], self_keys: Set[str]) -> str:
    if key in self_keys or key in _SELF_HOLDERS:
        return _HOLDER_SELF
    if key in chain_keys:
        return _HOLDER_CHAIN
    if key in _OTHER_HOLDERS:
        return _HOLDER_OTHERS
    return _HOLDER_OUTSIDE


def _holder_kinds(text: str, lo: int, hi: int, chain_keys: Set[str], self_keys: Set[str]) -> Set[str]:
    """The kinds of holder ``text[lo:hi]`` names after "held by" / "owned by" (none: empty)."""
    kinds: Set[str] = set()
    for n, h in enumerate(_HELD_BY_RE.finditer(text, lo, hi)):
        if n >= _MAX_HOLDERS:
            kinds.add(_HOLDER_OUTSIDE)          # never read on without a bound: fail closed
            break
        seg = text[h.end():min(hi, h.end() + _HOLDER_SPAN)]
        stop = _HOLDER_STOP_RE.search(seg)
        if stop is not None:
            seg = seg[:stop.start()]
        whole = _holder_key(seg)
        if not whole:
            continue
        kind = _holder_kind(whole, chain_keys, self_keys)
        if kind != _HOLDER_OUTSIDE:              # "Eli Lilly and Company" is one name, not two
            kinds.add(kind)
            continue
        for part in _HOLDER_SPLIT_RE.split(seg):
            key = _holder_key(part)
            if key:
                kinds.add(_holder_kind(key, chain_keys, self_keys))
    return kinds


def _disclaims_ownership(text: str, hit: "re.Match[str]", chain_keys: Set[str], self_keys: Set[str],
                         others_are_chain: bool) -> bool:
    """True when the disclaim word ``hit`` disclaims the reported block (shape 2 above)."""
    s, e = _sentence_bounds(text, hit.start(), hit.end())
    fwd = min(e, hit.end() + _DISCLAIM_WINDOW)
    semi = text.find(";", hit.end(), fwd)
    clause_end = semi if semi >= 0 else fwd
    passive = hit.group(0).casefold() == "disclaimed"
    back = max(s, hit.start() - _DISCLAIM_WINDOW)
    if passive:                       # "… and such beneficial ownership is expressly disclaimed"
        semi_back = text.rfind(";", back, hit.start())
        clause_start = semi_back + 1 if semi_back >= 0 else back
        about = _OWNERSHIP_WORD_RE.search(text, clause_start, hit.start()) is not None
    else:
        clause_start = hit.end()
        about = False
    about = about or _OWNERSHIP_WORD_RE.search(text, hit.end(), clause_end) is not None
    if not about:
        return False                  # a group, an obligation …: not the ownership
    # Firing evidence is read over the disclaimer's whole sentence; the exemptions only inside
    # its own clause.
    kinds = _holder_kinds(text, back, fwd, chain_keys, self_keys)
    if _HOLDER_OUTSIDE in kinds or (_HOLDER_OTHERS in kinds and not others_are_chain):
        return True
    if _holder_kinds(text, clause_start, clause_end, chain_keys, self_keys) & {_HOLDER_CHAIN, _HOLDER_OTHERS}:
        return False                  # only shares held inside the member's own chain
    return _PECUNIARY_RE.search(text, clause_start, clause_end) is None


def _scan_disclaimer(sources: Sequence[str], chain_keys: Set[str], self_keys: Set[str],
                     others_are_chain: bool) -> Optional[str]:
    """The filer's sentence when the member's position texts say the block is not its own
    (either shape above), else None. ``chain_keys``: folded names of the member's whole
    reviewed chain; ``self_keys``: the chosen line's own; ``others_are_chain``: every line of
    the filing is in the chain and the chain has more than one entity."""
    texts: List[str] = []
    for text in sources:
        if isinstance(text, str) and text.strip() and text not in texts:
            texts.append(text)
    hits = 0
    for text in texts:
        cause = _AGREEMENT_CAUSE_RE.search(text)
        for d in _DISCLAIM_RE.finditer(text):
            if cause is not None and d.start() > cause.start():
                break
            hits += 1
            if hits > MAX_DISCLAIMER_HITS:
                return _quote(text, d.start(), d.end())
            if _disclaims_ownership(text, d, chain_keys, self_keys, others_are_chain):
                return _quote(text, d.start(), d.end())
        if cause is not None:
            return _quote(text, cause.start(), cause.end())
    return None


# ── Selection: the member's own line, and what it says ───────────────────────────────────

_TWO_CLASSES_RE = re.compile(r"\bclass\s{1,3}([a-z])\b")
#: A series designator ("Series B Common Stock", "… and Series A Convertible Preferred
#: Stock"): one series of a tracking-stock issuer, or a second class. The cover percent is
#: of that series, which "of common stock" would misstate — never mapped, whatever
#: ``stake_names.class_basis`` says (the same rule, kept here too).
_SERIES_RE = re.compile(r"\bseries\s{1,3}([a-z0-9]{1,3})\b")

#: Roles of a cover line. The member's CHAIN is every line with a role; only a "member" or
#: an "affiliate" line is ever the chosen one.
_ROLE_MEMBER = "member"
_ROLE_AFFILIATE = "affiliate"
_ROLE_INTERMEDIATE = "intermediate"
_PRINCIPAL_ROLES: Tuple[str, ...] = (_ROLE_MEMBER, _ROLE_AFFILIATE)


def _class_problem(title: Optional[str], basis: Optional[str]) -> bool:
    """True when the class is not one plain class: no basis, two class letters, or any
    series designator (two series included)."""
    t = " ".join((title or "").casefold().split())
    letters = set(_TWO_CLASSES_RE.findall(t))
    series = set(_SERIES_RE.findall(t))
    return basis is None or len(letters) > 1 or len(series) > 0


def _intermediate_names(member: MemberConfig, own: Set[str], affiliates: Dict[str, Set[str]]) -> Set[str]:
    """Folded names of the member's reviewed intermediates (``MemberConfig.intermediates``):
    holding companies INSIDE its chain that co-report its block. Only a tuple / list of
    strings is read; anything else reads as none, so every co-reporter is outside the chain
    (fail closed: a proposal, never a wider chain)."""
    raw = getattr(member, "intermediates", ())
    if not isinstance(raw, (tuple, list)):
        return set()
    out = {fold_entity_name(n) for n in raw if isinstance(n, str)}
    out.discard("")
    return out - own - set(affiliates)


def _role_of(key: str, own: Set[str], affiliates: Dict[str, Set[str]], intermediates: Set[str]) -> Optional[str]:
    if key in own:
        return _ROLE_MEMBER
    if key in affiliates:
        return _ROLE_AFFILIATE
    if key in intermediates:
        return _ROLE_INTERMEDIATE
    return None


def _may_share_power(person: ReportingPerson) -> bool:
    """True when the line reports shared voting or shared dispositive power (rows 6 / 8) —
    or does not say: a missing or unreadable number counts as shared (fail closed)."""
    return any(v is None or v > 0 for v in (person.shared_voting, person.shared_dispositive))


def _no_dispositive_power(person: ReportingPerson) -> bool:
    """True when the line cannot sell the block it reports: its sole + shared dispositive
    power (rows 7 + 8) is 0, neither number is readable, or the readable power is below the
    line's aggregate (row 9). Beneficial ownership is voting OR dispositive power, so an
    aggregate above the dispositive power is partly voting-only — an acquirer's toehold plus
    its support agreements. One unreadable number beside another that covers the aggregate
    is a power to sell; every recorded filing reports rows 7 + 8 equal to row 9."""
    sole, shared = person.sole_dispositive, person.shared_dispositive
    if sole is None and shared is None:
        return True
    zero = Decimal(0)
    total = (sole if sole is not None else zero) + (shared if shared is not None else zero)
    if total == zero:
        return True
    return person.shares is not None and total < person.shares


def _names_of(member: MemberConfig) -> Tuple[Set[str], Dict[str, Set[str]]]:
    """(folded names of the member's OWN line, {folded affiliate name: accepted types})."""
    own = {fold_entity_name(n) for n in member.names}
    own |= {fold_entity_name(n) for f in member.s1_filers if f.role == "member" for n in f.names}
    own.discard("")
    allowed_extra = set(AUTO_REPORTING_TYPES) | set(AFFILIATE_EXTRA_TYPES)
    affiliates: Dict[str, Set[str]] = {}
    for f in member.s1_filers:
        if f.role != "affiliate":
            continue
        for n in f.names:
            key = fold_entity_name(n)
            if key and key not in own:
                affiliates.setdefault(key, set()).update(set(f.types) & allowed_extra)
    return own, affiliates


def _result(reason: Optional[str], **fields: Any) -> PositionResult:
    base: Dict[str, Any] = dict(
        person=None, shares=None, percent=None, basis=None, cusip=None,
        derivative_note=None, derivative_quote=None, disclaimer_quote=None,
    )
    base.update(fields)
    return PositionResult(ok=reason is None, reason=reason, **base)


def select_member_position(filing: ScheduleFiling, member: MemberConfig) -> PositionResult:
    """The member's own plain-share position in ``filing``, or the reason it is not one.

    The member's CHAIN is every line whose folded name is the member's, a listed
    affiliate's or a reviewed intermediate's (``MemberConfig.intermediates``, e.g. XXVI
    Holdings Inc. between Google LLC and Alphabet Inc.). Any other line is OUTSIDE it.

    Checked in this order (the first failure is the reason):

    1. ``about_member`` — the issuer is the member, a listed affiliate or an intermediate
       (by CIK, or by name), so someone else filed ABOUT it. No issuer CIK and no name
       match → ``schedule_unparseable``.
    2. ``member_line_missing`` — no line's folded name is one of the member's or a listed
       affiliate's names. An intermediate's line alone is not the member's position.
    3. ``member_lines_disagree`` — the chain's lines do not all report the same shares AND
       percent (never summed, never the largest). The member's own line is chosen, else the
       first affiliate line — never an intermediate's. A chosen line without strict numbers
       → ``schedule_unparseable``, unless the 5%-or-less box is checked (an exit needs none).
    4. ``reporting_type`` — the line's type is not exactly one accepted code: ``CO`` for the
       member's own line, the affiliate entry's ``types`` (``CO`` / ``OO``) for an affiliate's.
    5. ``class_unmapped`` — the class is not plain common / ordinary / Class A
       (``stake_names.class_basis``), the title names two classes, or it names a series
       ("Series B Common Stock"; two series too).
    6. ``exit_filing`` — the percent is below 5, or the 5%-or-less box is checked. It runs
       after 4-5 (an exit must be the member's own common position: its own line's type,
       never a preferred class's) and before 7-11: a sub-5% position is an exit whatever it
       is made of and whoever co-reports it. A full sell-out reports zero shares AND zero
       power, which must read as an exit, never as 7.
    7. ``no_dispositive_power`` — the chosen line's sole + shared dispositive power (rows 7
       and 8) is 0, neither number is readable, or it is below the line's aggregate (row 9):
       the member can vote (part of) the block but not sell it (an acquirer's
       support-agreement 13D, with or without a toehold). There is no own holding to judge,
       so it runs before 8-12.
    8. ``ownership_disclaimed`` — the member's position texts (the ones the composition scan
       reads) disclaim beneficial ownership of the block, or say it is held because of an
       agreement (``_scan_disclaimer``: positional; group-membership disclaimers, the
       pecuniary-interest chain boilerplate and a disclaimer naming only the member's own
       chain never count). The text says the block is not the member's own, so no other
       check can make it a holding.
    9. ``group_member`` — a group box is checked (row 2(a)) and an outside line reports too.
    10. ``shared_power`` — right after the group box: an outside line reports shared voting
        or shared dispositive power, or the chosen line shares power while an outside line
        reports at all, or while its chain is ONE entity (the member's own lines, or one
        affiliate's, alone — review A: the sharer is then someone the filing does not name).
        A missing or unreadable power number counts as shared. Sharing between two entities
        of the chain is fine: Google LLC, XXVI Holdings Inc. and Alphabet Inc. share one
        SpaceX block.
    11. ``percent_above_50`` — likely a controlled company.
    12. ``derivative_position`` — the composition scan found warrants, options,
        convertibles / conversions, preferred stock, RSUs, an exchangeable interest or a
        right to buy shares.

    ``cusip`` is the first CUSIP that is a usable identity key, else None (allowed).
    """
    own, affiliates = _names_of(member)
    intermediates = _intermediate_names(member, own, affiliates)
    issuer_key = fold_entity_name(filing.issuer_name)
    if (filing.issuer_cik is not None and filing.issuer_cik in member.filer_ciks) or (
        issuer_key and (issuer_key in own or issuer_key in affiliates or issuer_key in intermediates)
    ):
        return _result(REASON_ABOUT_MEMBER)
    if filing.issuer_cik is None:
        return _result(CODE_UNPARSEABLE)

    roles = [
        (_role_of(key, own, affiliates, intermediates), key)
        for key in (fold_entity_name(p.name) for p in filing.persons)
    ]
    principal = [i for i, (role, _) in enumerate(roles) if role in _PRINCIPAL_ROLES]
    if not principal:
        return _result(REASON_LINE_MISSING)
    chain = [p for p, (role, _) in zip(filing.persons, roles) if role is not None]
    if len({(p.shares, p.percent) for p in chain}) > 1:
        return _result(REASON_LINES_DISAGREE)
    chosen_index = next((i for i in principal if roles[i][0] == _ROLE_MEMBER), principal[0])
    chosen = filing.persons[chosen_index]
    chosen_role, chosen_key = roles[chosen_index]
    outside = [p for p, (role, _) in zip(filing.persons, roles) if role is None]
    # The chain counted in ENTITIES: every line of the member itself is one (two spellings of
    # its name are still one company), each affiliate and intermediate another.
    entities = {_ROLE_MEMBER if role == _ROLE_MEMBER else (role, key) for role, key in roles if role is not None}
    lone = len(entities) == 1
    shares = int(chosen.shares) if chosen.shares is not None else None
    cusip = next((c for c in (cusip_identity(v) for v in filing.cusips) if c), None)
    basis = class_basis(filing.class_title)
    fields: Dict[str, Any] = dict(
        person=chosen, shares=shares, percent=chosen.percent, basis=basis, cusip=cusip,
    )
    flagged_exit = filing.five_percent_or_less is True
    if (chosen.shares is None or chosen.percent is None) and not flagged_exit:
        return _result(CODE_UNPARSEABLE, **fields)

    # The member's chain (every line with a role) and any outside line holding the very same
    # block describe one position: all their comments are read, with the position text.
    same_block = [
        p for p, (role, _) in zip(filing.persons, roles)
        if role is not None or (p.shares, p.percent) == (chosen.shares, chosen.percent)
    ]
    position_texts = [chosen.comments] + [p.comments for p in same_block if p is not chosen] + [filing.item4_text]
    composition = _scan_composition(position_texts, shares)
    disclaimer = _scan_disclaimer(
        position_texts,
        chain_keys=own | set(affiliates) | intermediates,
        self_keys=own if chosen_role == _ROLE_MEMBER else {chosen_key},
        others_are_chain=not outside and not lone,
    )
    fields.update(derivative_note=composition.note, derivative_quote=composition.quote, disclaimer_quote=disclaimer)

    codes = [c for c in chosen.type.split(",") if c]
    accepted = set(AUTO_REPORTING_TYPES) if chosen_role == _ROLE_MEMBER else affiliates.get(chosen_key, set())
    if len(codes) != 1 or codes[0] not in accepted:
        return _result(REASON_REPORTING_TYPE, **fields)
    if _class_problem(filing.class_title, basis):
        return _result(REASON_CLASS_UNMAPPED, **{**fields, "basis": None})
    if flagged_exit or (chosen.percent is not None and chosen.percent < MIN_PERCENT):
        return _result(REASON_EXIT, **fields)
    if _no_dispositive_power(chosen):
        return _result(REASON_NO_DISPOSITIVE, **fields)
    if disclaimer is not None:
        return _result(REASON_DISCLAIMED, **fields)
    if outside and any(_group_checked(p.member_of_group) for p in filing.persons):
        return _result(REASON_GROUP_MEMBER, **fields)
    if outside and (_may_share_power(chosen) or any(_may_share_power(p) for p in outside)):
        return _result(REASON_SHARED_POWER, **fields)
    if lone and _may_share_power(chosen):
        # Review A: a chain of one that shares power shares it with a holder the filing does
        # not name (an acquirer's support agreements, filed without its merger sub).
        return _result(REASON_SHARED_POWER, **fields)
    if chosen.percent is not None and chosen.percent > MAX_PERCENT:
        return _result(REASON_ABOVE_50, **fields)
    if composition.found:
        return _result(REASON_DERIVATIVE, **fields)
    return _result(None, **fields)


__all__: Sequence[str] = (
    "CODE_UNPARSEABLE", "CODE_LEGACY", "PARSE_ERROR_CODES", "POSITION_REASONS",
    "MAX_SCHEDULE_XML_CHARS", "MAX_XML_DEPTH", "MAX_XML_ELEMENTS", "MAX_QUOTE_CHARS", "MAX_DISCLAIMER_HITS",
    "MIN_PERCENT", "MAX_PERCENT", "SCHEMA_13G", "SCHEMA_13D", "SUBMISSION_SCHEMAS",
    "ScheduleParseError", "ReportingPerson", "ScheduleFiling", "PositionResult",
    "parse_schedule_xml", "select_member_position", "format_share_count",
)
