"""Pure helpers for the stake finder: entity names, share classes, listings, identity, kind.

No I/O. Every rule here was pinned against live SEC data on 2026-10-28 (the recorded
fixtures under ``tests/fixtures/trillion_club_stakes``):

* SEC "conformed" names are often upper-case and abbreviated (``ELI LILLY & Co``,
  ``NVIDIA CORP``), while a filing's reporting person says ``Eli Lilly and Company``. Names
  are compared FOLDED (``fold_entity_name``), never as typed.
* An issuer name as filed carries an EDGAR state tag (``Ionetix Corp / DE /``) and a legal
  suffix, often comma-led (``Aktis Oncology, Inc.``). ``clean_investee_name`` strips the tag
  and ONE suffix, never re-cases, and refuses an all-caps name rather than "prettify" it.
* 13D/13G class titles are free text (8 spellings for 3 bases in 9 filings).
* SEC's ticker list mixes exchanges (``OTC`` never counts) and lists warrant tickers beside
  the common one (``CLSK`` / ``CLSKW``).
* An all-zero CUSIP (``000000000``, Ionetix) PASSES the check digit — it is a placeholder,
  never an identity key.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from app.services.trillion_club import rules
from app.services.trillion_club.stake_contracts import AUTO_TIER_TOKENS, DEFERRED_TIER_TOKENS, TIER_13DG

logger = logging.getLogger(__name__)

#: migration 175: investee_name is 1-60 characters. A longer name is never truncated.
INVESTEE_NAME_MAX = 60

# ── Entity names ─────────────────────────────────────────────────────────────────────────

_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)
#: Legal-form tokens folded to one spelling, so "CORP" == "Corporation" and "Co" == "Company".
_FOLD_TOKENS: Dict[str, str] = {
    "co": "company",
    "corp": "corporation",
    "inc": "incorporated",
    "ltd": "limited",
}


def fold_entity_name(name: Any) -> str:
    """A comparison key for a company name: casefolded, ``&`` → and, punctuation removed,
    runs of single letters joined (``N.V.`` → ``nv``) and legal-form tokens unified.

    ``fold_entity_name("ELI LILLY & Co") == fold_entity_name("Eli Lilly and Company")``.
    Returns "" for a non-string or a name with no letters or digits.
    """
    if not isinstance(name, str):
        return ""
    text = _NON_WORD_RE.sub(" ", name.casefold().replace("&", " and "))
    tokens: List[str] = []
    run: List[str] = []
    for tok in text.split():
        if len(tok) == 1 and tok.isalpha():
            run.append(tok)
            continue
        if run:
            tokens.append("".join(run))
            run = []
        tokens.append(tok)
    if run:
        tokens.append("".join(run))
    return " ".join(_FOLD_TOKENS.get(t, t) for t in tokens)


def names_match(a: Any, b: Any) -> bool:
    """True when two company names fold to the same non-empty key."""
    fa = fold_entity_name(a)
    return bool(fa) and fa == fold_entity_name(b)


# "Ionetix Corp / DE /", "Foo Inc /MD", "Bar Corp/NV/" — EDGAR's state-of-incorporation tag.
_STATE_TAG_RE = re.compile(r"\s*/\s*[A-Za-z]{2}\s*/?\s*$")
# ONE legal suffix at the end, comma-led or not.
_LEGAL_SUFFIX_RE = re.compile(
    r"(?:\s*,\s*|\s+)(?:inc\.?|incorporated|corp\.?|corporation|co\.?|company|n\.\s?v\.?|nv|"
    r"s\.\s?a\.?|sa|ag|se|plc|ltd\.?|limited|l\.l\.c\.?|llc|l\.p\.?|lp|oyj|ab|asa|a/s|gmbh|"
    r"s\.p\.a\.|pbc|public\s+benefit\s+corporation|k\.\s?k\.?|b\.\s?v\.?|bv|"
    r"co\.?,?\s+ltd\.?|pte\.?\s+ltd\.?|pty\.?\s+ltd\.?)$",
    re.IGNORECASE,
)
_DANGLING_RE = re.compile(r"(?:&|\band|,|-|/)\s*$", re.IGNORECASE)


def strip_state_tag(name: str) -> str:
    return _STATE_TAG_RE.sub("", name).strip()


def is_all_caps(name: str) -> bool:
    """True when the name's letters are all upper-case and there are more than three of
    them — EDGAR's conformed style (``INTEL CORP``), which is never shown as a name.
    Short acronyms (``AMD``, ``IBM``) are allowed."""
    letters = [c for c in name if c.isalpha()]
    return len(letters) > 3 and all(c.isupper() for c in letters)


def clean_investee_name(raw: Any) -> Optional[str]:
    """The display name for a non-club investee, or None when it cannot be used as written.

    Strips the EDGAR state tag and ONE trailing legal suffix (comma-led included), unless
    stripping would leave a dangling ``&`` / ``and`` / comma (``Eli Lilly and Company`` stays
    whole). Never re-cases. None for a non-string, an empty or all-caps result, a result
    over ``INVESTEE_NAME_MAX`` characters (never truncated) or one that ends in punctuation.
    """
    if not isinstance(raw, str):
        return None
    name = " ".join(strip_state_tag(raw.strip()).split())
    m = _LEGAL_SUFFIX_RE.search(name)
    if m:
        candidate = name[: m.start()].rstrip(" ,")
        if candidate and not _DANGLING_RE.search(candidate):
            name = candidate
    if not name or len(name) > INVESTEE_NAME_MAX or is_all_caps(name):
        return None
    if _DANGLING_RE.search(name) or not name[-1].isalnum() and name[-1] not in ")":
        return None
    if not name.isprintable() or not significant_tokens(name):
        return None  # "The Company" → "The": nothing left that says which company it is
    return name


#: Words that say nothing about WHICH company a row is (placeholders and legal forms).
_STOP_TOKENS: FrozenSet[str] = frozenset({
    "the", "and", "of", "a", "an", "company", "corporation", "incorporated", "limited",
    "llc", "plc", "nv", "sa", "ag", "se", "lp", "group", "holdings", "holding",
    # legal forms (stake_templates._KEY_SUFFIXES treats the same ones as forms, not names)
    "pbc", "spa", "gmbh", "oyj", "oy", "ab", "asa", "as", "kk", "bv", "sas", "sarl", "pte",
    "pty", "llp",
    "private", "companies", "not", "named", "unnamed", "other", "others", "commitment",
    "commitments", "investment", "investments", "stake", "stakes", "joint", "venture",
})
_PAREN_RE = re.compile(r"\([^)]*\)")
_MULTIWORD_FORM_RE = re.compile(r"\bpublic\s+benefit\s+corporation\b", re.IGNORECASE)


def significant_tokens(name: Any) -> FrozenSet[str]:
    """The folded tokens that identify a company, without parentheticals ("(warrant)"),
    legal forms or placeholder words. Empty for a placeholder row ("Private companies
    (not named)"), so such a row never name-matches anything."""
    if not isinstance(name, str):
        return frozenset()
    folded = fold_entity_name(_MULTIWORD_FORM_RE.sub(" ", _PAREN_RE.sub(" ", name)))
    return frozenset(t for t in folded.split() if t not in _STOP_TOKENS)


# ── Share class (13D/13G securitiesClassTitle) ───────────────────────────────────────────

_PAR_RE = re.compile(
    r"(?:[,;]?\s*(?:\$\s*[0-9][0-9.,]*\s*)?(?:no\s+par\s+value|par\s+value|nominal\s+value)"
    r"[^,;]{0,80})",
    re.IGNORECASE,
)
_CLASS_LETTER_RE = re.compile(r"\bclass\s+([a-z])\b")
#: "Series B Common Stock" (tracking / dual-series issuers): the cover percent is of that one
#: series, which "of common stock" would misstate — never mapped.
_SERIES_RE = re.compile(r"\bseries\s+[a-z0-9]{1,3}\b")
_NOT_PLAIN_RE = re.compile(
    r"\b(?:preferred|preference|depositary|ads|adss|adr|adrs|units?|warrants?|rights?|"
    r"notes?|debentures?|convertible|options?|safe|limited partnership|membership interests?)\b"
)


def class_basis(title: Any) -> Optional[str]:
    """``ownership_basis`` for a 13D/13G class title, or None (→ proposal) when the class is
    not plain common / ordinary / Class A stock.

    Par- and nominal-value clauses are ignored. Class A wins over "ordinary shares" (the
    owner's Nebius row reads "of Class A" for "Class A Ordinary Shares"); any other class
    letter, preferred, depositary shares, units, warrants, rights, notes or convertibles → None.
    """
    if not isinstance(title, str) or not title.strip():
        return None
    raw = " ".join(title.casefold().split())
    if _NOT_PLAIN_RE.search(raw) or _SERIES_RE.search(raw):
        return None  # read BEFORE the par strip: its tail can swallow "and Series A Preferred …"
    t = " ".join(_PAR_RE.sub(" ", raw).split())
    letters = {m.group(1) for m in _CLASS_LETTER_RE.finditer(raw)}
    if len(letters) > 1:
        return None  # "Class A Common Stock and Class C Capital Stock": one percent, two classes
    if letters:
        return "of Class A" if letters == {"a"} else None
    if re.search(r"\bcommon\s+(?:stock|shares)\b", t):
        return "of common stock"
    if re.search(r"\bordinary\s+shares\b", t):
        return "of ordinary shares"
    return None


# ── Symbols, CUSIPs and listings ─────────────────────────────────────────────────────────

def normalize_symbol(value: Any) -> Optional[str]:
    """The read path's symbol rule (``trillion_club_service._norm_symbol``, pinned equal by a
    test): trimmed, upper-case, ``.`` and ``/`` → ``-`` (``BRK.B`` == ``BRK-B``)."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text.upper().replace(".", "-").replace("/", "-")


def cusip_identity(value: Any) -> Optional[str]:
    """A CUSIP usable as an identity key, or None: 9 characters, a valid check digit, and
    not the all-zero placeholder (which PASSES the check digit)."""
    cusip = rules.normalize_cusip(value)
    if cusip is None or cusip[:8] == "00000000":
        return None
    if not cusip[8].isdigit() or int(cusip[8]) != rules.cusip_check_digit(cusip[:8]):
        return None
    return cusip


def cusip6(value: Any) -> Optional[str]:
    """The issuer part (first 6 characters) of a usable CUSIP, else None."""
    cusip = cusip_identity(value)
    return cusip[:6] if cusip else None


#: SEC's exchange labels that count as a U.S. listing. OTC never counts.
US_EXCHANGES: FrozenSet[str] = frozenset({"nasdaq", "nyse", "cboe"})
_DERIVATIVE_SUFFIXES: FrozenSet[str] = frozenset({"W", "WS", "WT", "U", "UN", "R", "RT"})


_PREFERRED_SERIES_RE = re.compile(r"P[A-Z]{0,2}")


def _is_derivative_of(sym: str, other: str) -> bool:
    """True when ``sym`` is a warrant / unit / right ticker (``CLSKW``) or a dashed preferred
    series (``BAC-PB``) of the same issuer's ``other`` ticker."""
    if other == sym or not sym.startswith(other):
        return False
    rest = sym[len(other):]
    if rest.startswith("-"):
        rest = rest[1:]
        return rest in _DERIVATIVE_SUFFIXES or bool(_PREFERRED_SERIES_RE.fullmatch(rest))
    return rest in _DERIVATIVE_SUFFIXES


def choose_us_common_ticker(
    tickers: Any, exchanges: Any
) -> Tuple[Optional[str], Optional[str]]:
    """``(symbol, None)`` for the issuer's single U.S.-exchange common ticker, else
    ``(None, reason)`` with reason ``no_us_listing`` or ``ambiguous_listing``.

    ``tickers`` / ``exchanges`` are SEC submissions' parallel arrays. Warrant, unit and right
    tickers and dashed preferred series (``BAC-PB``) listed beside a common ticker on ANY
    exchange (``CLSK`` / ``CLSKW``) are ignored. Two plain
    tickers (``GOOGL`` / ``GOOG``) are ambiguous: the finder never guesses a class.
    """
    if not isinstance(tickers, list) or not isinstance(exchanges, list):
        return None, "no_us_listing"
    every = [s for s in (normalize_symbol(t) for t in tickers) if s]
    us: List[str] = []
    for tick, exch in zip(tickers, exchanges):
        sym = normalize_symbol(tick)
        if sym and isinstance(exch, str) and exch.strip().casefold() in US_EXCHANGES and sym not in us:
            us.append(sym)
    if not us:
        return None, "no_us_listing"
    base = [s for s in us if not any(_is_derivative_of(s, o) for o in every)]
    if not base:
        return None, "no_us_listing"  # only a warrant of an OTC common trades on an exchange
    if len(base) == 1:
        return base[0], None
    return None, "ambiguous_listing"


# ── Kind ─────────────────────────────────────────────────────────────────────────────────

KIND_ON_13F_NOTE = "on_13f_note"
KIND_US_LISTED_OFF_13F = "us_listed_off_13f"
KIND_PRIVATE = "private"
KIND_COMMITMENT = "commitment"


def decide_kind(*, use_13f: bool, on_latest_13f: bool, symbol: Optional[str]) -> Optional[str]:
    """The stake kind, decided once at creation (plan v3, gate 12):

    * ``on_13f_note`` only when the member's 13F is built (``use_13f``) AND its latest
      COMPLETE build lists the holding — a note is about a 13F line, so it must have one;
    * else ``us_listed_off_13f`` when there is a single U.S. symbol;
    * else None — the caller proposes kind ``private`` (never auto).
    """
    if use_13f and on_latest_13f and symbol:
        return KIND_ON_13F_NOTE
    if symbol:
        return KIND_US_LISTED_OFF_13F
    return None


# ── Settings parsed at call time (a bad value never fails the deploy) ────────────────────

def parse_auto_tiers(raw: Any) -> FrozenSet[str]:
    """``TRILLION_CLUB_STAKE_AUTO_TIERS`` → the enabled auto tiers. Unknown and deferred
    tokens are dropped with ONE ERROR naming them (fail closed: no auto for them)."""
    if not isinstance(raw, str) or not raw.strip():
        return frozenset()
    tokens = [t.strip().lower() for t in re.split(r"[,\s]+", raw) if t.strip()]
    ok = [t for t in tokens if t in AUTO_TIER_TOKENS]
    bad = [t for t in tokens if t not in AUTO_TIER_TOKENS]
    if bad:
        deferred = [t for t in bad if t in DEFERRED_TIER_TOKENS]
        logger.error(
            "[tc-stakes-config] TRILLION_CLUB_STAKE_AUTO_TIERS: ignoring %s (deferred to a "
            "follow-up: %s; accepted: %s)", bad, deferred or "none", list(AUTO_TIER_TOKENS),
        )
    return frozenset(ok)


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_auto_since(raw: Any) -> Optional[date]:
    """``TRILLION_CLUB_STAKE_AUTO_SINCE`` → a date, or None (no auto-publish). Blank means
    None; a malformed value logs ONE ERROR and also means None — never a boot failure."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if _ISO_DATE_RE.match(text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    logger.error(
        "[tc-stakes-config] TRILLION_CLUB_STAKE_AUTO_SINCE=%r is not YYYY-MM-DD — "
        "auto-publish stays off", text[:40],
    )
    return None


def auto_tier_gate(filed_on: Optional[date], *, tiers: FrozenSet[str], since: Optional[date]) -> Optional[str]:
    """Gate 10, pure: None when a member's own plain-share 13D/13G may go live under these switches;
    otherwise the REASONS code that makes it a proposal — ``tier_off`` (13dg not in AUTO_TIERS) or
    ``before_auto_since`` (no AUTO_SINCE, a filing before it, or no filing date: fail closed).
    The finder's run and the read-only preview both call it, so the two never disagree."""
    if TIER_13DG not in tiers:
        return "tier_off"
    if since is None or filed_on is None or filed_on < since:
        return "before_auto_since"
    return None


# ── Source titles and URLs (the seed's own style) ────────────────────────────────────────

_SCHEDULE_LABELS: Dict[str, str] = {
    "SCHEDULE 13G": "Schedule 13G",
    "SCHEDULE 13G/A": "Schedule 13G/A",
    "SCHEDULE 13D": "Schedule 13D",
    "SCHEDULE 13D/A": "Schedule 13D/A",
}


def _short_date(day: date) -> str:
    return f"{day:%b} {day.day}, {day.year}"


def schedule_source_title(source_name: str, submission_type: str, filed_on: date) -> Optional[str]:
    """``"NVIDIA Schedule 13G (Jul 20, 2026)"`` — the seed's own spelling. None for an
    unknown submission type."""
    label = _SCHEDULE_LABELS.get((submission_type or "").strip().upper())
    if label is None:
        return None
    return f"{source_name} {label} ({_short_date(filed_on)})"


def document_source_title(investee_name: str, form: str, filed_on: date) -> str:
    """``"Intel 8-K (Dec 29, 2025)"`` — the seed's own spelling."""
    return f"{investee_name} {form.strip()} ({_short_date(filed_on)})"


_XSL_FOLDER_RE = re.compile(r"xsl[A-Za-z0-9_]{1,40}")  # fullmatch only
_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


def schedule_document_paths(primary_document: Any) -> Optional[Tuple[str, Optional[str]]]:
    """``("primary_doc.xml", "xslSCHEDULE_13G_X01")`` from submissions' ``primaryDocument``
    (the raw XML to parse, and the XSL folder of the rendered page to cite), or None when
    the value is anything else."""
    if not isinstance(primary_document, str):
        return None
    parts = primary_document.strip().split("/")
    if parts == ["primary_doc.xml"]:
        return "primary_doc.xml", None
    if len(parts) == 2 and parts[1] == "primary_doc.xml" and _XSL_FOLDER_RE.fullmatch(parts[0]):
        return "primary_doc.xml", parts[0]
    return None


def archive_url(cik: str, accession: str, document: str, folder: Optional[str] = None) -> str:
    """``https://www.sec.gov/Archives/edgar/data/<int cik>/<acc no dashes>/[folder/]document``."""
    acc = accession.replace("-", "")
    path = f"{folder}/{document}" if folder else document
    return f"{_ARCHIVES}/{int(cik)}/{acc}/{path}"


# ── Identity: is this investee already on file for the member? ───────────────────────────

@dataclass(frozen=True)
class IdentityMatch:
    """An existing stake row the candidate is (``by`` != "name") or may be ("name")."""

    row: Mapping[str, Any]
    by: str  # "cik" | "cusip" | "symbol" | "name"


class IdentityIndex:
    """Every stake row of ONE member (any origin, kind or published state), indexed by
    investee CIK, CUSIP-6, normalised symbol (plus the club's symbol aliases) and
    significant name tokens.

    Identifiers decide; a name match counts only against a row that carries none of the
    identifiers the candidate has (a private "Nscale" row vs an IPO'd Nscale), and is a
    POSSIBLE match the owner confirms — never an automatic one. Equal significant-token sets
    win; failing that, one set inside the other ("Anthropic" vs "Anthropic Labs") is still a
    possible match: over-matching costs one heads-up, under-matching a duplicate public row.
    """

    def __init__(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        symbol_aliases: Optional[Mapping[str, Iterable[str]]] = None,
    ) -> None:
        self._alias: Dict[str, str] = {}
        for canonical, aliases in (symbol_aliases or {}).items():
            c = normalize_symbol(canonical)
            if not c:
                continue
            self._alias[c] = c
            for a in aliases:
                na = normalize_symbol(a)
                if na:
                    self._alias[na] = c
        self._rows: List[Mapping[str, Any]] = [r for r in rows if isinstance(r, Mapping)]

    def _canon(self, symbol: Any) -> Optional[str]:
        s = normalize_symbol(symbol)
        return self._alias.get(s, s) if s else None

    def match(
        self,
        *,
        investee_cik: Optional[str] = None,
        cusip: Optional[str] = None,
        symbol: Optional[str] = None,
        name: Optional[str] = None,
    ) -> Optional[IdentityMatch]:
        c6 = cusip6(cusip)
        sym = self._canon(symbol)
        tokens = significant_tokens(name)
        possible: Optional[IdentityMatch] = None
        partial: Optional[IdentityMatch] = None
        for row in self._rows:
            row_cik = row.get("investee_cik")
            if investee_cik and isinstance(row_cik, str) and row_cik == investee_cik:
                return IdentityMatch(row, "cik")
            row_c6 = cusip6(row.get("investee_cusip"))
            if c6 and row_c6 and row_c6 == c6:
                return IdentityMatch(row, "cusip")
            row_sym = self._canon(row.get("investee_us_symbol"))
            if sym and row_sym and row_sym == sym:
                return IdentityMatch(row, "symbol")
            comparable = (investee_cik and row_cik) or (c6 and row_c6) or (sym and row_sym)
            if possible is None and not comparable and tokens:
                row_tokens = significant_tokens(row.get("investee_name"))
                if row_tokens and row_tokens == tokens:
                    possible = IdentityMatch(row, "name")
                elif partial is None and row_tokens and (row_tokens <= tokens or tokens <= row_tokens):
                    # "Anthropic" vs "Anthropic Labs": still only a possible match, never a row.
                    partial = IdentityMatch(row, "name")
        return possible or partial


def unique_preserving(values: Iterable[Any]) -> List[Any]:
    seen: Set[Any] = set()
    out: List[Any] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


__all__: Sequence[str] = (
    "INVESTEE_NAME_MAX", "fold_entity_name", "names_match", "strip_state_tag", "is_all_caps",
    "clean_investee_name", "significant_tokens", "class_basis", "normalize_symbol",
    "cusip_identity", "cusip6", "US_EXCHANGES", "choose_us_common_ticker", "KIND_ON_13F_NOTE",
    "KIND_US_LISTED_OFF_13F", "KIND_PRIVATE", "KIND_COMMITMENT", "decide_kind",
    "parse_auto_tiers", "parse_auto_since", "auto_tier_gate", "schedule_source_title", "document_source_title",
    "schedule_document_paths", "archive_url", "IdentityMatch", "IdentityIndex",
    "unique_preserving",
)
