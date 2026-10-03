"""
Shared helpers for insider (Form 4) data: transaction classification AND
name normalization.

Both ``holders_service`` (per-ticker detail view) and ``tracking_service``
(watchlist-wide alerts) need to agree on:
  * which FMP transactionType strings map to buys vs sells
  * which trades carry real signal ("Informative") vs mechanical compensation
    noise like option exercises and tax withholding ("Uninformative")
  * how to render insider names — FMP returns them in messy 'LAST FIRST
    MIDDLE' uppercase form ("ELLISON LAWRENCE JOSEPH"); both surfaces
    should display the natural 'First Middle Last' shape.

Keeping these in one place prevents the alert card, the Holders tab, and
the ticker report's "Insider & Management" section from disagreeing about
the same underlying Form 4 row.

The per-fetch row rules live here too (2026-10-03, NYAX): which security lines are
equity (`is_equity_line`), which rows belong to the issuer at all (`filter_issuer_rows`),
how a Form 4/A replaces the Form 4 it amends (`supersede_form4_amendments`), and the
365-day window (`insider_window_cutoff`). Each surface used to carry its own copy: the
report and the Holders tab kept a row only when its security name contained "common
stock", so every "Ordinary Shares" filer (NYAX, STX, TSM, ...) showed Buys 0 beside a Home
CEO Buys card listing the CEO's purchases.
"""

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple


def normalize_insider_name(raw: Optional[str]) -> str:
    """Convert an FMP-style insider name into a natural 'First Middle Last'.

    FMP's `reportingName` comes in two messy shapes:
      - 'ELLISON LAWRENCE JOSEPH'   (uppercase, space-separated, last-first)
      - 'Ellison, Lawrence Joseph'  (mixed case with a comma)

    Both collapse to 'Lawrence Joseph Ellison'. Single-letter middle tokens
    get a period appended ('Sicilia Michael D' → 'Michael D. Sicilia').
    Falls back to 'Insider' for empty/None so callers can render without a
    None-check.

    Compound last names ('VAN DER BERG ALICE') are not detected — the first
    space-delimited token is treated as the surname. Rare enough to skip the
    extra heuristic.
    """
    if not raw or not raw.strip():
        return "Insider"
    s = raw.strip().rstrip(".")

    def _tok(t: str) -> str:
        t = t.strip(". ,")
        if not t:
            return ""
        if len(t) == 1:
            return t.upper() + "."
        # title() lowercases letters after the first; for "MC"/"MAC"
        # prefixes this gives "Mcdonald"/"Macarthur" — acceptable
        # without a special lookup table.
        return t[0].upper() + t[1:].lower()

    if "," in s:
        last_part, rest = s.split(",", 1)
        first_middle_part = rest
    else:
        parts = s.split()
        if len(parts) < 2:
            return _tok(s) or "Insider"
        last_part = parts[0]
        first_middle_part = " ".join(parts[1:])

    last_titled = _tok(last_part)
    first_middle_titled = " ".join(
        _tok(t) for t in first_middle_part.split() if _tok(t)
    )
    return f"{first_middle_titled} {last_titled}".strip() or "Insider"


def classify_insider_transaction(tx_type: str) -> str:
    """Classify a FMP ``transactionType`` string into one of four labels.

    Only open-market purchases (P) and pure sales (S) are informative.
    Composite sale types (S-Sale+OE, S-Sale+DIS) indicate option exercises
    or RSU dispositions paired with sales — these are uninformative because
    they reflect compensation mechanics, not insider sentiment.

      - P-Purchase           → Informative Buy
      - S-Sale (pure)        → Informative Sell
      - S-Sale+OE / +DIS     → Uninformative Sell
      - A-*/M-*/G-*          → Uninformative Buy (awards, exercises, gifts)
      - F-*/D-*              → Uninformative Sell (tax withholding, disposition)
    """
    tx = (tx_type or "").strip().upper()

    if tx.startswith("P"):
        return "Informative Buy"

    if tx.startswith("S"):
        if "+OE" in tx or "+DIS" in tx or "EXEMPT" in tx:
            return "Uninformative Sell"
        return "Informative Sell"

    if tx.startswith(("A", "M", "G")):
        return "Uninformative Buy"

    if tx.startswith(("F", "D")):
        return "Uninformative Sell"

    return "Uninformative Sell"


# ── Form 4 row predicates (CEO Buys signal, home E2 2026-09-23) ────────
#
# FMP folds every role a reporting person holds into ONE `typeOfOwner` string, officer
# title last: "director, officer: Chief Executive Officer", "director, 10 percent owner,
# officer: President and CEO", "officer: CEO", "10 percent owner". There is no separate
# title field. `ticker_report_data_collector._role_rank` is NOT reused here: it ranks
# "President and CEO" as a president, and importing the agents collector is heavy.

_CEO_RE = re.compile(r"\bceo\b|chief\s+executive", re.I)
# Not the SITTING CEO: a former/retired/incoming one still files Form 4s for a while.
_NOT_SITTING_RE = re.compile(
    r"\b(?:former|retired|previous|past|emeritus|outgoing|elect)\b|\bex[-\s]?(?:ceo|chief)\b", re.I
)
# A title that CONTAINS "CEO" but is someone else's: "Chief of Staff to the CEO", "EVP, Office
# of the CEO", "Deputy CEO", "Spouse of CEO". Adjacency on purpose: "Vice Chairman and CEO"
# IS the CEO, "Vice CEO" is not.
_SUBORDINATE_RE = re.compile(
    r"\b(?:deputy|vice|assistant|associate|regional|divisional|division|segment)[\s-]+"
    r"(?:ceo|chief\s+executive)\b"
    r"|\b(?:to|of)\s+the\s+(?:ceo|chief\s+executive)\b|\bspouse\b",
    re.I,
)
# The CEO of a SEGMENT or a subsidiary, not of the issuer: "CEO, Consumer & Community Banking",
# "President & CEO of Subsidiary Bank", "Chief Executive Officer - Europe". "of the Company"
# stays the company. A comma alone is fine ("CEO, President and Director").
_SEGMENT_RE = re.compile(
    r"(?:\bceo\b|chief\s+executive(?:\s+officer)?)\s*"
    r"(?:[-–—:]\s*\w"
    r"|,\s*(?:consumer|commercial|corporate|global|international|north\s+america|americas|europe"
    r"|emea|asia|apac|wealth|retail|investment|banking|operations|division|segment|group|unit"
    r"|business|subsidiary)\b"
    r"|\s+of\s+(?!the\s+company\b|company\b)\w)",
    re.I,
)
# A CEO title whose last words are a REGION is a segment CEO with no punctuation for
# _SEGMENT_RE to key on: NYAX's "CEO NAYX North America". At most one word may sit between
# the CEO token and the region, and the region must END the title, so a company name that
# merely contains a place ("President & CEO ASRV & Bank", "Chief Executive Officer and
# Director USA Compression", "CEO, Marriott International") is still the issuer's CEO.
# `international` and `usa` are deliberately absent: both are common in company names.
_REGION_TAIL_RE = re.compile(
    r"(?:\bceo\b|chief\s+executive(?:\s+officer)?)\s+(?:[\w.&'-]+\s+)?"
    r"(?:north\s+america|americas|europe|emea|asia(?:\s+pacific)?|apac|latam|latin\s+america"
    r"|china|japan|india|uk)[\s.,;]*$",
    re.I,
)
# Equity share lines, tuned on 11,000 live Form 4 rows (2026-10-03): "Common Stock",
# "Common Shares", "Ordinary Shares", "Class A Ordinary Stock", Alphabet's "Class C Capital
# Stock", REIT/trust "Shares of Beneficial Interest", Canadian "Subordinate Voting Shares".
_COMMON_RE = re.compile(
    r"\bcommon\b|\bordinary\s+(?:shares?|stock)\b|\bcapital\s+stock\b"
    r"|\b(?:shares?|units?)\s+of\s+beneficial\s+interest\b"
    r"|\b(?:subordinate|limited)\s+voting\s+shares?\b",
    re.I,
)
# Not the stock itself, even when the label names it: derivatives, debt, preferred, unit
# awards and deferred-comp equivalents.
# Debt "notes" by their debt shape, not the bare word: "Common Stock (Note 1)" is the stock.
_NON_COMMON_RE = re.compile(
    r"preferred|preference|warrant|debenture|\boptions?\b|\brights?\b"
    r"|\b(?:senior|convertible|subordinated|promissory|exchangeable|secured|unsecured)\s+(?:\w+\s+)?notes?\b"
    r"|\bnotes?\s+(?:due|convertible|exchangeable|payable)\b|^\W*notes?\b"
    r"|restricted stock unit|\bstock\s+units?\b|\brsus?\b|phantom|equivalent|swap"
    r"|\bltips?\b|profits?\s+interests?",
    re.I,
)
# American Depositary Shares/Receipts ARE the stock, but the share-COUNTING surfaces exclude
# them on purpose (owner decision 2026-10-03): one ADS is several ordinary shares (TSM 1:5,
# BABA 1:8), so counting both lines would mix units in the share-denominated insider chart.
# The dollar-denominated alerts (`is_equity_line(strict=False)`) keep them.
_DEPOSITARY_RE = re.compile(r"deposit[ao]ry|\bads\b|\badss\b|\badrs?\b", re.I)
# "Common Stock and associated Preferred Stock Purchase Rights" is the common stock with
# its poison-pill rights attached: that tail must not trip the derivative test above. Only a
# tail ENDING in "rights" is stripped, and BOTH tests then read what is left, so "Common Stock
# with attached Warrants" or "LTIP Units and related Common Stock" are not laundered by it.
_ATTACHED_RIGHTS_TAIL_RE = re.compile(
    r"\s*\b(?:and|with|including)\b[^,;]*?\brights?\b[\s.)]*$", re.I
)
_ROLE_LABEL_MAX = 60


def _officer_title(type_of_owner: str) -> str:
    """The reporting person's OFFICER title, or ``""`` when they are not filing as an officer.

    Only this text may make someone a CEO — never the free-text ``other:`` field on its own
    ("director, other: Retired CEO" and "director, other: Spouse of CEO" are not the CEO).
    Two live shapes (2026-09-23, 3,000 P rows): ``"…officer: <title>"`` for 498 of the 499
    CEO-matching rows, and ``"director, officer, other: President & CEO"`` — the bare officer
    FLAG with its title carried in ``other:`` — which is accepted.
    """
    low = type_of_owner.lower()
    at = low.rfind("officer:")
    if at >= 0:
        title = type_of_owner[at + len("officer:"):]
        cut = title.lower().find("other:")
        if cut >= 0:
            title = title[:cut]
        return " ".join(title.split()).strip(" ,;")
    roles = [part.strip() for part in low.split(",")]
    if "officer" in roles:
        other_at = low.rfind("other:")
        if other_at >= 0:
            return " ".join(type_of_owner[other_at + len("other:"):].split()).strip(" ,;")
    return ""


def is_ceo_role(type_of_owner: object) -> bool:
    """True when a Form 4 ``typeOfOwner`` names the issuer's SITTING CEO / co-CEO.

    Strings only — FMP has sent ``None`` and numbers here, and neither is a role. The match
    runs on the officer title alone (see ``_officer_title``) and rejects former/incoming CEOs,
    someone else's title that merely mentions the CEO, and segment/subsidiary CEOs.
    """
    if not isinstance(type_of_owner, str):
        return False
    title = _officer_title(type_of_owner)
    return (
        bool(title)
        and bool(_CEO_RE.search(title))
        and not _NOT_SITTING_RE.search(title)
        and not _SUBORDINATE_RE.search(title)
        and not _SEGMENT_RE.search(title)
        and not _REGION_TAIL_RE.search(title)
    )


def ceo_role_label(type_of_owner: object) -> str:
    """The officer title to show under a CEO's name ("Chief Executive Officer", "President
    and CEO"), else ``"CEO"``."""
    if isinstance(type_of_owner, str):
        title = _officer_title(type_of_owner)
        if title and _CEO_RE.search(title):
            return title[:_ROLE_LABEL_MAX].rstrip()
    return "CEO"


def is_common_stock(security_name: object) -> bool:
    """True for a common / ordinary share line — not preferred, warrants, notes,
    options, rights or RSUs, which a "CEO bought the stock" headline must not count.
    A blank or missing name is False: the CEO headline never counts an unlabeled row
    (the per-ticker surfaces use `is_equity_line`, which keeps them)."""
    if not isinstance(security_name, str):
        return False
    head = _ATTACHED_RIGHTS_TAIL_RE.sub("", security_name[:_SECURITY_NAME_MAX])
    return (
        bool(_COMMON_RE.search(head))
        and not _NON_COMMON_RE.search(head)
        and not _DEPOSITARY_RE.search(head)
    )


# Every regex here reads at most this much of a label (Form 4 security titles are short).
_SECURITY_NAME_MAX = 200


def _is_non_equity(security_name: str) -> bool:
    head = _ATTACHED_RIGHTS_TAIL_RE.sub("", security_name[:_SECURITY_NAME_MAX])
    return bool(_NON_COMMON_RE.search(head))


def is_equity_line(security_name: object, *, strict: bool = True) -> bool:
    """Whether a Form 4 row's security line is the issuer's stock, for the per-ticker
    surfaces (the report's Insider table, the Holders chart and list) and the alerts.

    * blank, ``None`` or a non-string → True: an unlabeled row was always counted on these
      surfaces (Form 3 holdings rows arrive blank), and a number must not crash ``.lower()``;
    * ``strict`` (the default; report + Holders) → ``is_common_stock``, the SAME rule as the
      Home CEO Buys card, so the three surfaces cannot disagree about one row again;
    * ``strict=False`` (watchlist alerts and pushes) → reject only lines that are clearly
      NOT the stock (derivatives, debt, preferred, unit awards). An allow-list there would
      silently drop real alerts on labels like "Class A Limited Voting Shares" or
      "Exchangeable Shares". ADS lines count there: an alert is in dollars, so the
      ADS-to-ordinary unit ratio that keeps them off the share charts does not matter.
    """
    if not isinstance(security_name, str) or not security_name.strip():
        return True
    if strict:
        return is_common_stock(security_name)
    return not _is_non_equity(security_name)


def is_informative(classification: str) -> bool:
    """True when the classification carries real insider-sentiment signal."""
    return classification in ("Informative Buy", "Informative Sell")


def action_word(classification: str) -> str:
    """Return ``"bought"`` or ``"sold"`` from a classification label."""
    return "bought" if "Buy" in classification else "sold"


def classify_for_alerts(tx_type: str) -> Tuple[str, bool]:
    """Convenience for the alerts pipeline.

    Returns ``(action_word, is_informative)`` where ``action_word`` is
    ``"bought"`` or ``"sold"``. Callers that want only real signals can
    gate on the second element.
    """
    classification = classify_insider_transaction(tx_type)
    return action_word(classification), is_informative(classification)


# ── Thesis-bullet self-labeling ───────────────────────────────────────
#
# Bull/Bear thesis bullets on the Ticker Report render with NO section header, so
# each must name its own signal. A bullet like "55 sells ($1.9B) vs 1 buy ($112K)
# in 12 months" is unreadable out of context — the reader can't tell it describes
# INSIDER activity (vs institutions, congress, or analysts, which also have
# buyers/sellers). The synthesis prompt asks the model to write "55 insider
# sells…", but the model doesn't reliably comply, and a leading "Insider:" prefix
# can't survive because narrative_prompts._post_process strips leading "Word:"
# labels. So the label is enforced deterministically, inlined before the sell word.

_THESIS_SELL_RE = re.compile(r"\b(?:sell|sale)\w*", re.I)
_THESIS_BUY_RE = re.compile(r"\bbuy\w*", re.I)
# Other "buyers vs sellers" sources — never relabel one of these as insider.
_THESIS_OTHER_SOURCE_RE = re.compile(
    r"congress|senat|repres|\bhouse\b|institution|hedge|analyst|\bfund", re.I
)


def ensure_insider_label(point: str) -> str:
    """Inject "insider" into a thesis bullet that describes insider buy/sell
    activity but never says so, so the bullet stands on its own.

    Conservative — acts ONLY when the bullet pairs a sell-count with a buy-count
    and a number, isn't already labeled "insider", and doesn't name a different
    source (congress / institutions / hedge funds / analysts). Otherwise the
    bullet is returned unchanged. The label is inlined before the sell word
    ("55 sells…" → "55 insider sells…") rather than prefixed, because a leading
    "Insider:" would be stripped by _post_process downstream.
    """
    if not isinstance(point, str) or not point or "insider" in point.lower():
        return point
    if not (
        _THESIS_SELL_RE.search(point)
        and _THESIS_BUY_RE.search(point)
        and any(ch.isdigit() for ch in point)
    ):
        return point
    if _THESIS_OTHER_SOURCE_RE.search(point):
        return point
    m = _THESIS_SELL_RE.search(point)
    if m.start() == 0:
        # Rare: bullet leads with the sell word — prefix instead of inlining.
        return "Insider " + point[0].lower() + point[1:]
    return f"{point[:m.start()]}insider {point[m.start():]}"


# ── Per-fetch row rules (report + Holders + alerts) ───────────────────
#
# Applied ONCE to the rows a service fetched, in this order, before any builder sees them:
#   filter_issuer_rows → equity lines (is_equity_line) → supersede_form4_amendments
# so the report's table, the Holders chart and the Holders list count the same rows. The
# watchlist alerts run the last two steps (lenient equity mode) but NOT the issuer filter:
# they have no issuer CIK on that path (a recorded gap — a holding company's 10%-owner
# purchases elsewhere can still alert). Pure: no I/O, no logging (callers log what they drop).

INSIDER_WINDOW_DAYS = 365
# Per-symbol insider window fetch (`FMPClient.get_insider_trades_since(symbol=...)`), shared by
# the report collector and the Holders tab. FMP honours limit=1000 per symbol (probed
# 2026-10-03: one page covers 3+ years for AMZN and 4+ for JPM), so 5 pages bound a year
# generously — the same page shape as the CEO drill-down.
INSIDER_PAGE_SIZE = 1000
INSIDER_MAX_PAGES = 5


def insider_window_cutoff(now: Optional[datetime] = None) -> str:
    """The inclusive ``YYYY-MM-DD`` lower bound of the trailing-365-day insider window.

    Every window site compares this STRING against a row's ``insider_row_date``. Two sites
    used to compare a datetime that carried the current time of day, so a trade dated on
    the cutoff day was in the list and the Holders summary but not in the bars or the
    report's table.
    """
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=INSIDER_WINDOW_DAYS)).strftime("%Y-%m-%d")


def insider_row_date(row: Dict[str, Any]) -> str:
    """A row's trade date (``YYYY-MM-DD``), else its filing date, else ``""``. FMP's
    ``filingDate`` can carry a time ("2026-09-22 16:05:00"), so only the date is kept."""
    for key in ("transactionDate", "filingDate"):
        raw = row.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()[:10]
    return ""


def _normalize_cik(value: object) -> Optional[str]:
    """CIK digits without leading zeros ("0001901279" → "1901279"), else None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value <= 0 or value != int(value):
            return None
        return str(int(value))
    text = str(value).strip()
    if not text.isdigit():
        return None
    text = text.lstrip("0")
    return text or None


def filter_issuer_rows(
    rows: Any, issuer_cik: object
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Keep only the rows about THIS issuer's stock.

    FMP's per-symbol insider feed also carries filings the company made as a 10% OWNER of
    OTHER issuers (it keys the symbol on the filing's EDGAR folder): BRK-B's feed holds
    Berkshire's purchases of other companies' "Class A/B Common Stock", which turned the
    BRK-B report into "Net Buying $212.9M" when its own insiders bought $0.5M and sold $20M.
    Those rows carry the other issuer's ``companyCik``.

    Returns ``(kept, dropped)``, ``dropped`` mapping each foreign CIK to its row count.
    A row with no ``companyCik`` is kept (nothing proves it foreign), and an unknown issuer
    CIK keeps every row. A holding-company reorganisation that changed the issuer's CIK
    drops the pre-reorganisation rows; callers log the drop so that stays visible.
    """
    if not isinstance(rows, list):
        return [], {}
    issuer = _normalize_cik(issuer_cik)
    dict_rows = [r for r in rows if isinstance(r, dict)]
    if issuer is None:
        return dict_rows, {}
    kept: List[Dict[str, Any]] = []
    dropped: Dict[str, int] = {}
    for r in dict_rows:
        cik = _normalize_cik(r.get("companyCik"))
        if cik is None or cik == issuer:
            kept.append(r)
        else:
            dropped[cik] = dropped.get(cik, 0) + 1
    return kept, dropped


def insider_reporter_key(row: Dict[str, Any]) -> str:
    """Stable per-person identity: the SEC reporting CIK, else the normalised name.
    ``""`` when neither identifies anyone (``normalize_insider_name`` answers "Insider")."""
    # Normalised, so 1903011, "0001903011" and 1903011.0 are one person, and an all-zero or
    # non-digit CIK falls back to the name instead of pooling unrelated reporters.
    cik = _normalize_cik(row.get("reportingCik"))
    if cik:
        return f"cik:{cik}"
    name = row.get("reportingName")
    if isinstance(name, str) and name.strip():
        normalized = normalize_insider_name(name).lower()
        if normalized and normalized != "insider":
            return f"name:{normalized}"
    return ""


_SHARE_CLASS_RE = re.compile(r"\bclass\s+[\"'“”]?([a-z0-9])\b", re.I)


def _share_class_token(security_name: object) -> str:
    """"a" for "Class A Common Stock", "" for an unclassed line. Grouping on the class, not
    the raw label, lets a 4/A that rewords the label ("Ordinary shares" for "Ordinary
    Shares") still replace its original, while BRK-A and BRK-B lines stay apart."""
    if not isinstance(security_name, str):
        return ""
    m = _SHARE_CLASS_RE.search(security_name)
    return m.group(1).lower() if m else ""


def _finite_number(value: object) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _form_type(row: Dict[str, Any]) -> str:
    form = row.get("formType")
    return str(form).strip().upper() if form not in (None, "") else "4"


def _filing_date(row: Dict[str, Any]) -> str:
    """A row's filing date (``YYYY-MM-DD``), the filing identity — the CEO card's too, so the
    two rules agree on the same rows. Two filings on one day read as one filing (identical
    lines on them are all kept, as separate fills); a row without one reads as one filing."""
    filed = row.get("filingDate")
    return str(filed)[:10] if isinstance(filed, str) else ""


def supersede_form4_amendments(rows: Any) -> List[Dict[str, Any]]:
    """Drop the Form 4 lines a later Form 4/A replaces, and the same line filed twice.

    FMP lists an amendment as new rows beside the original's, so every per-ticker total
    counted a restated trade twice: NYAX's CTO filed a 4/A on 2026-09-29 repeating three
    lines of his 09-24 Form 4, and a June 4/A corrected a 13,596-share sale to 12,180 while
    both stayed counted ($6.1M of NYAX insider selling shown against $3.8M filed).

    Generalised from ``signals_service._extract_ceo_buys`` (the CEO card), for every
    transaction type. Rows are grouped by (symbol, reporter, trade date, direct/indirect,
    raw ``transactionType``, share class). Within a group, the amendment that counts is
    every 4/A filed on the LATEST amendment date, and:

      * when it restates at least as many lines as the originals (a 4/A re-files the whole
        form: NYAX's CTO, JCTC's three new lines for two old ones) it replaces the group;
      * a PARTIAL 4/A (fewer lines) is matched ONE-TO-ONE against the originals: each of
        its lines consumes the identical original (a re-filed line), else the nearest
        original of the same size at a new price, or the same positive price at a new size
        (a correction), else nothing (an omitted line it adds). Every original it did not
        consume is kept. (The CEO card's rule removes EVERY original sharing a size or a
        price with any amended line — harmless on its CEO buys, wrong on a day of round-lot
        sales or $0 awards, so it is not copied: a $0 price matches nothing.)
      * the same (shares, price) line on two filing DATES counts once (the earliest);
        identical lines on one date are separate fills and all count.

    Passed through untouched: non-Form-4 rows (Form 3/3A/5 — a missing ``formType`` reads
    as "4"), rows with no reporter identity, and rows with no finite share count. Input
    order (FMP's newest-first) is preserved. Known limits — the 4/A lands in another group
    and both count: it corrects the trade DATE or the transaction code, or its label adds
    a share class the original lacked (a 4/A does not cite the filing it amends).
    """
    if not isinstance(rows, list):
        return []
    keep: set = set()
    groups: Dict[Tuple[str, ...], List[int]] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        reporter = insider_reporter_key(row)
        shares = _finite_number(row.get("securitiesTransacted"))
        if not _form_type(row).startswith("4") or not reporter or shares is None:
            keep.add(i)
            continue
        symbol = row.get("symbol")
        own = row.get("directOrIndirect")
        tx = row.get("transactionType")
        key = (
            symbol.strip().upper() if isinstance(symbol, str) else "",
            reporter,
            insider_row_date(row),
            own.strip().upper() if isinstance(own, str) else "",
            tx.strip().upper() if isinstance(tx, str) else "",
            _share_class_token(row.get("securityName")),
        )
        groups.setdefault(key, []).append(i)

    def _line(i: int) -> Tuple[float, float]:
        price = _finite_number(rows[i].get("price"))
        return (
            round(abs(_finite_number(rows[i].get("securitiesTransacted")) or 0.0), 4),
            round(price, 4) if price is not None else -1.0,
        )

    for members in groups.values():
        amendments = [i for i in members if "/A" in _form_type(rows[i])]
        if amendments:
            latest = max(_filing_date(rows[i]) for i in amendments)
            amended = [i for i in amendments if _filing_date(rows[i]) == latest]
            originals = [i for i in members if "/A" not in _form_type(rows[i])]
            if len(amended) >= len(originals):
                members = amended
            else:
                members = _match_partial_amendment(originals, amended, _line) + amended
        by_line: Dict[Tuple[float, float], List[int]] = {}
        for i in members:
            by_line.setdefault(_line(i), []).append(i)
        for line in by_line.values():
            first = min(_filing_date(rows[i]) for i in line)
            keep.update(i for i in line if _filing_date(rows[i]) == first)
    return [row for i, row in enumerate(rows) if i in keep]


def _match_partial_amendment(originals: List[int], amended: List[int], line) -> List[int]:
    """The originals a partial 4/A leaves standing (see `supersede_form4_amendments`)."""
    remaining = list(originals)
    for a in amended:
        shares_a, price_a = line(a)
        exact = next((o for o in remaining if line(o) == (shares_a, price_a)), None)
        if exact is not None:
            remaining.remove(exact)
            continue
        candidates = []
        for o in remaining:
            shares_o, price_o = line(o)
            if shares_o == shares_a and price_a >= 0 and price_o >= 0:
                candidates.append((abs(price_o - price_a) / max(price_a, price_o, 1e-9), o))
            elif price_o == price_a and price_a > 0:
                candidates.append((abs(shares_o - shares_a) / max(shares_a, shares_o, 1e-9), o))
        if candidates:
            remaining.remove(min(candidates, key=lambda c: c[0])[1])
    return remaining


def prepare_insider_rows(
    rows: Any, issuer_cik: object
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """The per-fetch pipeline for the report and the Holders tab, in its one order:
    ``filter_issuer_rows`` → equity lines → ``supersede_form4_amendments``.

    Returns ``(rows, dropped_foreign_ciks)``. Equity filtering comes BEFORE supersession so
    a derivative line (an option exercise on the same day, same reporter) can never be
    taken for an amendment of a stock line.
    """
    kept, dropped = filter_issuer_rows(rows, issuer_cik)
    equity = [r for r in kept if is_equity_line(r.get("securityName"))]
    return supersede_form4_amendments(equity), dropped


def clean_role_title(title: Optional[str]) -> str:
    """Strip FMP's ``officer:`` tag from a ``typeOfOwner`` / roster title so it reads
    cleanly in the UI ("officer: Chief Executive Officer" → "Chief Executive Officer").
    Other tags (``director,``, ``10 percent owner,``) are kept. Blank → "Officer"."""
    if not isinstance(title, str) or not title.strip():
        return "Officer"
    cleaned = _OFFICER_PREFIX_RE.sub(r"\1 ", title[:_ROLE_TITLE_MAX])
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(",").strip()
    return cleaned or "Officer"


# Only FMP's role TAG (at the start or after a comma), so "Chief Operating Officer: Ops" keeps
# its word; read on a capped title (the old leading `\s*` scanned whitespace quadratically).
_OFFICER_PREFIX_RE = re.compile(r"(^|,)\s*officer:\s*", flags=re.IGNORECASE)
_ROLE_TITLE_MAX = 200


def issuer_roster(roster: Any, issuer_cik: object) -> List[Dict[str, Any]]:
    """The insider roster (``FMPClient.get_insider_roster``) with other issuers' "insiders"
    dropped — BRK-B's listed Berkshire itself — then de-duplicated by normalised name: the
    roster is keyed on (name, CIK), so one person can appear once per CIK before the filter."""
    kept, _ = filter_issuer_rows(roster if isinstance(roster, list) else [], issuer_cik)
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for r in kept:
        key = normalize_insider_name(r.get("owner")).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out
