"""Log-only numeric grounding audit for Cay AI answers (`CHAT_GROUNDING`), 2026-10-08.

Why this exists
---------------
Nothing measured whether the figures in a chat answer come from Caydex's data. A model that
"corrects" an FMP figure from memory, or invents a margin, looked exactly like one that quoted
the tool result. Before any enforcement (an answer note on ungrounded figures — plan step A9,
gated on two weeks of this data and a hand-labelled precision check) we need a BASELINE, so this
module only counts and logs.

What it does
------------
Every number the answer states is looked up among the numbers the turn's EVIDENCE states:

* the system instruction actually used (Caydex's data blocks, the live quote line, the fenced
  screen context, the date line);
* the user's own turns (a user may state a number: "if I put $500 in");
* the non-web tool results (numeric JSON leaves are read as values, strings as text);
* prior assistant turns and the rolling summary — kept APART as `prior_answer`, so a repeated
  hallucination never counts as grounded.

Each answer number lands in exactly one bucket:

  exempt              a year, a plain integer of 12 or less, a day of month next to a month name
  grounded            equal to an evidence value within the ANSWER's own rounding
                      ("4.2B" matches 4,213,000,000; "23.4%" matches 23.41)
  scaled              equal after a restatement the answer's OWN WRITING allows (below)
  prior_answer_only   found only in an earlier answer or the summary
  ungrounded          found nowhere

"Scaled" is deliberately narrow, because every extra factor is another window an invented
figure can fall into (a 23.4% margin × 10^6 used to "match" a chart's 23,412,345-share volume):

* a percent may restate a RATIO: 45.1% ↔ an evidence 0.451 (×100) — only toward a unit-less
  value of magnitude ≤ 10 (a ratio); never the reverse (a "1.8%" is not a price of 180);
* a unit-less number may be a ratio of a written percent (0.45 ↔ "45%") or a percent of a
  unit-less ratio (45 ↔ 0.45), the same magnitude bound;
* ONLY a figure written WITH a scale suffix ("$4.2B", "4,213 million", "302.5K") may restate a
  figure kept in a smaller unit — evidence = answer ÷ 10^3/6/9/12, never past the suffix's own
  scale ("$4.2B" ↔ 4,213 in millions or 4.2 in billions; "$42M" never ↔ 0.042). A percent,
  a multiple or a fraction gets no unit scale at all, and neither does an unsuffixed figure
  (a table "in millions" lands in `ungrounded`; the A9 precision check measures that shape).

Two kinds of numeric tool leaf are told apart by their KEY, because a chart or a quote is the
densest evidence a turn carries:

* a COUNT (`volume`, `shares…`, `employees`, never a `…usd` / `…value` / `…cap` key): only a
  unit-less answer number may match it, so a "$123M" or a "4.2%" never grounds on a day's
  123,000,000-share volume;
* a PER-SHARE PRICE (exactly `open` / `high` / `low` / `close` / `price` / `dayHigh` / … ): it
  grounds a $ or unit-less figure as written, never a percent, and never through a unit scale
  (a "$181M" is not a $181.20 close restated in millions).

The tool evidence is what the MODEL SAW: the doors pass each result through the same structural
truncation the model's copy went through (`gemini.truncate_tool_result`, applied by the caller
so this module stays free of the client import) — a pruned list tail never grounds a number.

`shadow_enforce` counts the ungrounded currency / percent figures that sit in a sentence naming
a metric Caydex holds (revenue, EPS, margin, shares, dividend, P/E, free cash flow, debt …): what
an A9 note WOULD have flagged. Names are not numbers at all ("10-K", "Form 4", "52-week",
"S&P 500", "Q3", "FY2025", an ISO date "2026-10-08" are blanked before extraction). Signs are compared on absolute values
(the text parser never captures a minus sign; "a loss of $1.2B" is the evidence's -1.2e9).

Rules that are not negotiable
-----------------------------
* **LOG ONLY.** Nothing here may change an answer. The doors run it after the answer is final
  (stream: after `enforce_answer`, before `finalize_answer_notes`, so code-written notes are
  never audited) and a failure is a `skipped=error` line, never an exception
  (`tests/test_chat_grounding_hook.py`, `tests/test_chat_numeric_grounding.py`).
* **Never evaluate an AI against web results** (Brave Search API terms §3(b)(xiii)). A turn whose
  web results reached the model is skipped (`skipped=web_turn`, decided by the door, which owns
  `web_results_delivered`); a web-search tool result is never evidence; and this module must
  NOT import `chat_web_search_service` (`tests/test_brave_search_boundary.py` pins its importers).
* **Counts only in the log.** No answer text, no samples: an INFO line reaches the log drain and
  the error-triage digest, and answer text there would be user content in an ops channel.
* **Capped, linear, never raises.** It runs on the single uvicorn worker's thread pool
  (`asyncio.to_thread`); every input is length-capped before any regex, and every pattern is
  linear (bounded quantifiers only). The number parser is `marketing/numbers.extract_numbers`,
  which is stdlib-only and linear by design — reused, not copied, so chat and the marketing
  grounding validator read "4.2B" / "23%" / "50 bps" the same way.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import math
import numbers as _numbers
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.services.marketing.numbers import (
    CURRENCY,
    FOREIGN_CURRENCY,
    FRACTION,
    MULTIPLE,
    PERCENT,
    PLAIN,
    YEAR,
    NumberMention,
    extract_numbers,
)

logger = logging.getLogger(__name__)

# ── evidence sources ──────────────────────────────────────────────────────────

SRC_CAYDEX = "caydex"           # the system instruction (data blocks, screen context, quote line)
SRC_USER = "user"               # the user's own turns
SRC_TOOL = "tool"               # non-web tool results
SRC_PRIOR = "prior_answer"      # earlier assistant turns + the rolling summary
_SOURCES = (SRC_CAYDEX, SRC_USER, SRC_TOOL, SRC_PRIOR)
_PRIMARY_SOURCES = frozenset({SRC_CAYDEX, SRC_USER, SRC_TOOL})

# ── caps (every input is cut BEFORE any regex runs) ──────────────────────────

#: An answer longer than this is not a chat answer (the deep-dive ceiling is ~16k chars):
#: skipped as `too_long` rather than scanned.
ANSWER_MAX_CHARS = 200_000
#: The part of an answer that is audited; a longer one is audited up to here (`capped`).
ANSWER_AUDIT_CHARS = 16_000
#: At most this many answer numbers are classified (the rest are not counted).
MAX_ANSWER_NUMBERS = 400
#: Per-source character budgets for TEXT evidence, so a long system instruction or a long
#: history cannot starve the tool results that arrive later in the turn.
_SOURCE_CHAR_BUDGET = {SRC_CAYDEX: 80_000, SRC_USER: 20_000, SRC_TOOL: 120_000, SRC_PRIOR: 60_000}
#: One text item (a history turn, one tool result's strings) is cut here.
TEXT_ITEM_CHARS = 40_000
#: Numeric JSON leaves kept as values, across all tool results.
MAX_EVIDENCE_VALUES = 50_000
#: Nodes walked per tool result, and how deep.
TOOL_MAX_NODES = 5_000
TOOL_MAX_DEPTH = 12
#: Characters of string leaves kept per tool result.
TOOL_TEXT_CHARS = 20_000
#: How long the stream door waits for the audit task, AFTER its durable write and its `done`
#: frame (never in front of what the user sees), before the generator ends.
SETTLE_SECONDS = 2.0
#: The longest an INLINE audit (the send door, which audits inside `generate_response`, under the
#: door's `CHAT_SEND_BUDGET_SECONDS`) may hold a finished answer. The audit itself takes well
#: under 0.1 s at its caps; this bounds only the wait for a free worker thread, so a saturated
#: executor (every Supabase call shares it) can never push a paid answer past the budget.
INLINE_AUDIT_SECONDS = 1.0

# ── matching ──────────────────────────────────────────────────────────────────

_BUCKETS = (PLAIN, PERCENT, CURRENCY, FOREIGN_CURRENCY, MULTIPLE, FRACTION)
#: A tool leaf under a key that names a count of things (shares, volume, employees).
_COUNT = "count"
#: A tool leaf under an exact per-share price key (open / high / low / close / price …).
_PRICE = "price"
_ALL_BUCKETS = _BUCKETS + (_COUNT, _PRICE)
#: Run on a dict key cut to `_KEY_CHARS`: a count key, unless it also names money.
_COUNT_KEY_RE = re.compile(r"(?i)volume|shares|employees|headcount")
_MONEY_KEY_RE = re.compile(r"(?i)usd|dollar|value|amount|notional|cap|price|cost|proceeds")
_PRICE_KEY_RE = re.compile(
    r"(?i)(?:open|high|low|close|adj_?close|adjusted_?close|price|vwap|previous_?close"
    r"|day_?high|day_?low|year_?high|year_?low|last|bid|ask)"
)
_KEY_CHARS = 64
_INF = float("inf")
#: A ratio's largest plausible magnitude (1,000% growth = 10.0): the ratio ↔ percent factor is
#: tried only toward evidence this small, so "18,120" never "restates" a price of 181.20.
_RATIO_MAX = 10.0
#: Unit restatements, tried only for a figure written with a scale suffix, up to its own scale.
_UNIT_SCALES = (1e3, 1e6, 1e9, 1e12)
#: The classes a suffixed figure may restate from (a percent is never "in millions").
_UNIT_SCALE_BUCKETS = {
    CURRENCY: (CURRENCY, PLAIN),
    FOREIGN_CURRENCY: (FOREIGN_CURRENCY, PLAIN),
    PLAIN: (PLAIN, _COUNT, CURRENCY, FOREIGN_CURRENCY),
}
#: One lookup step: (factor, evidence buckets, the largest evidence magnitude it may reach).
#: The answer's value ÷ factor is looked up, within the answer's tolerance ÷ factor.
_Step = Tuple[float, Tuple[str, ...], float]
_SHADOW_UNITS = frozenset({CURRENCY, FOREIGN_CURRENCY, PERCENT})

#: Names that carry digits but are not quantities, beyond the ones `extract_numbers` already
#: blanks (`NAME_NUMBER_RE`: S&P 500, 10-K/10-Q, 13F/13D, Form N, Q3, FY2025, 5G …). Bounded
#: quantifiers only. Period labels are SINGULAR on purpose ("52-week high", "10-year yield",
#: "200-day average"): a plural ("over 90 days") is a stated window, still audited. An ISO date
#: or timestamp ("2026-01-27", "2026-01-27T14:05:00Z") is a name too: a chart's 260 dates would
#: otherwise hand the evidence every integer from 1 to 31 and ground any "27 stores".
_CHAT_NAME_RE = re.compile(
    r"\b(?:19|20)[0-9]{2}-[01][0-9]-[0-3][0-9]"
    r"(?:[T ][0-2][0-9]:[0-5][0-9](?::[0-5][0-9](?:\.[0-9]{1,9})?)?(?:Z|[+-][0-2][0-9]:?[0-5][0-9])?)?"
    r"|(?i:\b[0-9]{1,3}[- ](?:day|week|month|year|quarter|session)\b)"
    r"|\b(?:6|8|10|20|40)-[KFQ](?:/A)?\b"
    r"|\b[SF]-[0-9]{1,2}\b"
    r"|(?i:\bnasdaq[- ]?100\b|\brussell\s?(?:1000|2000|3000)\b|\bdow\s?(?:jones\s)?30\b"
    r"|\bs&p\s?(?:100|400|500|600|1500)\b)"
    r"|\b13[DFG](?:/A)?\b"
    r"|\bForm\s?[0-9]{1,3}[A-Z]?\b"
)
#: WHOLE month tokens only ("Oct", "Oct.", "October", "Sept") — never a word that merely starts
#: like one ("markets", "declined", "maybe", "junk", "separate").
_MONTHS = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
           r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)(?![a-z])\.?")
#: Run on a ≤ 16-character window just before / after the number, never on a whole text.
_MONTH_BEFORE_RE = re.compile(r"(?i)\b" + _MONTHS + r"\s{0,2}$")
_MONTH_AFTER_RE = re.compile(r"(?i)^(?:st|nd|rd|th)?\s{1,2}(?:of\s)?" + _MONTHS + r"(?![a-z])")
_MONTH_WINDOW = 16
#: The numeric core of a mention's raw text, for its rounding tolerance.
_CORE_RE = re.compile(r"[0-9][0-9,]*(?:\.([0-9]+))?|\.([0-9]+)")
#: A sentence ends at . ! ? followed by whitespace, or at a line break ("4.2B" is not an end).
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s)|\n")
#: A metric Caydex holds — the sentence test for `shadow_enforce`.
_METRIC_RE = re.compile(
    r"(?i)\b(?:revenue|revenues|sales|eps|earnings|net income|operating income|gross profit"
    r"|ebitda|margin|margins|shares|share count|dividend|dividends|payout|p/e|pe ratio"
    r"|price-to-earnings|free cash flow|fcf|cash flow|debt|book value|buyback|buybacks)\b"
)


@dataclass
class GroundingAudit:
    """The counts for one answer. `numbers` = exempt + grounded + scaled + prior + ungrounded."""

    numbers: int = 0
    exempt: int = 0
    grounded: int = 0
    scaled: int = 0
    prior_answer_only: int = 0
    ungrounded: int = 0
    shadow_enforce: int = 0
    evidence_values: int = 0
    answer_capped: bool = False
    evidence_capped: bool = False
    skipped: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── evidence ──────────────────────────────────────────────────────────────────


class GroundingEvidence:
    """What the turn's answer may be grounded on. Cheap to fill on the event loop (text is
    stored, tool results are walked once with a node cap); the regex work happens later, in
    `audit_answer`, on a worker thread. Every method is total: bad input is ignored."""

    def __init__(self) -> None:
        self._texts: List[Tuple[str, str]] = []
        self._values: List[Tuple[str, float, str]] = []
        self._chars: Dict[str, int] = {s: 0 for s in _SOURCES}
        self.capped = False
        #: A web-search result WITH results reached this collector: the turn is a web turn
        #: whatever the caller said, and is skipped (never evaluated against search results).
        self.web_seen = False

    @classmethod
    def from_seed(cls, seed: Any) -> "GroundingEvidence":
        """`seed` = `{"caydex": [...], "user": [...], "prior_answer": [...]}` lists of strings
        (what `ChatService._grounding_seed` builds). Anything else → empty evidence."""
        ev = cls()
        try:
            if isinstance(seed, dict):
                for source in (SRC_CAYDEX, SRC_USER, SRC_PRIOR):
                    items = seed.get(source)
                    if isinstance(items, str):
                        items = [items]
                    if isinstance(items, (list, tuple)):
                        for item in items:
                            ev.add_text(item, source)
        except Exception as e:  # noqa: BLE001 — evidence is best-effort; the audit says so
            logger.warning("CHAT_GROUNDING seed unreadable (%s: %s) — empty evidence",
                           type(e).__name__, e)
        return ev

    def add_text(self, text: Any, source: str = SRC_CAYDEX) -> None:
        try:
            if not isinstance(text, str) or not text:
                return
            src = source if source in _SOURCES else SRC_CAYDEX
            room = _SOURCE_CHAR_BUDGET[src] - self._chars[src]
            if room <= 0:
                self.capped = True
                return
            piece = text[:min(TEXT_ITEM_CHARS, room)]
            if len(piece) < len(text):
                self.capped = True
            self._texts.append((src, piece))
            self._chars[src] += len(piece)
        except Exception as e:  # noqa: BLE001
            logger.warning("CHAT_GROUNDING evidence text dropped (%s: %s)", type(e).__name__, e)

    @staticmethod
    def _web_result_kind(name: Any, result: Any) -> Optional[bool]:
        """None = not a web-search result; True = one that carried results; False = an empty,
        capped or failed one. Structural on purpose: importing the web-search service here would
        widen its importer list (`tests/test_brave_search_boundary.py`)."""
        is_web = (name == "web_search") or (
            isinstance(result, dict) and result.get("web_search") is True
        )
        if not is_web:
            return None
        rows = result.get("results") if isinstance(result, dict) else None
        return isinstance(rows, list) and len(rows) > 0

    def add_tool_result(self, name: Any, result: Any) -> None:
        """Walk one tool result: numeric leaves → values, string leaves → text. A web-search
        result is NEVER evidence. Bounded by `TOOL_MAX_NODES` / `TOOL_MAX_DEPTH`."""
        try:
            if result is None:
                return
            web = self._web_result_kind(name, result)
            if web is not None:
                if web:
                    self.web_seen = True
                return
            strings: List[str] = []
            str_chars = 0
            nodes = 0
            # (node, depth, leaf bucket) — a list under "volume" holds counts too.
            stack: List[Tuple[Any, int, str]] = [(result, 0, PLAIN)]
            while stack:
                if nodes >= TOOL_MAX_NODES:
                    self.capped = True
                    break
                obj, depth, kind = stack.pop()
                nodes += 1
                if obj is None or isinstance(obj, (bool, complex)):
                    continue
                if isinstance(obj, _numbers.Number):
                    if len(self._values) >= MAX_EVIDENCE_VALUES:
                        self.capped = True
                        continue
                    try:
                        v = float(obj)
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if math.isfinite(v):
                        self._values.append((SRC_TOOL, abs(v), kind))
                elif isinstance(obj, str):
                    if obj and str_chars < TOOL_TEXT_CHARS:
                        piece = obj[:TOOL_TEXT_CHARS - str_chars]
                        strings.append(piece)
                        str_chars += len(piece)
                elif isinstance(obj, (dict, list, tuple)):
                    if depth >= TOOL_MAX_DEPTH:
                        self.capped = True
                        continue
                    children: Iterable[Tuple[Any, str]] = (
                        ((v, _leaf_kind(k, kind)) for k, v in obj.items())
                        if isinstance(obj, dict) else ((v, kind) for v in obj)
                    )
                    budget = TOOL_MAX_NODES - nodes - len(stack)
                    for child, child_kind in children:
                        if budget <= 0:
                            self.capped = True
                            break
                        stack.append((child, depth + 1, child_kind))
                        budget -= 1
            if strings:
                # Newline-joined so two adjacent leaves never read as one number.
                self.add_text("\n".join(strings), SRC_TOOL)
        except Exception as e:  # noqa: BLE001
            logger.warning("CHAT_GROUNDING tool result %r dropped from evidence (%s: %s)",
                           str(name)[:40], type(e).__name__, e)

    def _indexes(self) -> Tuple["_Index", "_Index", int]:
        primary, prior = _Index(), _Index()
        for src, value, bucket in self._values:
            (primary if src in _PRIMARY_SOURCES else prior).add(value, bucket)
        for src, text in self._texts:
            index = primary if src in _PRIMARY_SOURCES else prior
            for m in _numbers_in(text):
                index.add(m.value, m.unit)
        primary.freeze()
        prior.freeze()
        return primary, prior, primary.size + prior.size


def _leaf_kind(key: Any, inherited: str) -> str:
    """The bucket of the leaves under `key`: COUNT for a key naming a count of things
    ("volume", "sharesOutstanding", "employees") and no money ("dollarVolume" is not one), PRICE
    for an exact per-share price key ("close", "dayHigh"), else what the parent carried (a list
    under "volume" holds counts). Never raises."""
    if not isinstance(key, str) or not key:
        return inherited
    k = key[:_KEY_CHARS]
    if _COUNT_KEY_RE.search(k) and not _MONEY_KEY_RE.search(k):
        return _COUNT
    if _PRICE_KEY_RE.fullmatch(k):
        return _PRICE
    return inherited


class _Index:
    """Sorted absolute values per unit bucket; a lookup is a bisect per (factor, bucket)."""

    __slots__ = ("_buckets", "size")

    def __init__(self) -> None:
        self._buckets: Dict[str, List[float]] = {u: [] for u in _ALL_BUCKETS}
        self.size = 0

    def add(self, value: float, unit: str) -> None:
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return
        bucket = unit if unit in self._buckets else PLAIN   # YEAR and unknown → PLAIN
        self._buckets[bucket].append(abs(float(value)))
        self.size += 1

    def freeze(self) -> None:
        for values in self._buckets.values():
            values.sort()

    def find(self, value: float, tol: float, plan: Sequence[_Step]) -> bool:
        target_abs = abs(value)
        for f, buckets, max_abs in plan:
            target = target_abs / f
            t = tol / f
            lo, hi = target - t, min(target + t, max_abs)
            if lo > hi:
                continue
            for name in buckets:
                values = self._buckets.get(name) or []
                i = bisect.bisect_left(values, lo)
                if i < len(values) and values[i] <= hi:
                    return True
        return False


def _direct_plan(unit: str) -> Tuple[_Step, ...]:
    """A unit-less answer number may match any evidence; a $ / % / x one only its own class or
    a unit-less evidence value (JSON leaves carry no unit) — and a money figure a per-share
    price leaf too (never a count leaf)."""
    if unit in (PLAIN, YEAR):
        return ((1.0, _ALL_BUCKETS, _INF),)
    if unit in (CURRENCY, FOREIGN_CURRENCY):
        return ((1.0, (unit, PLAIN, _PRICE), _INF),)
    return ((1.0, (unit, PLAIN), _INF),)


def _scaled_plan(m: NumberMention) -> Tuple[_Step, ...]:
    """The restatements THIS mention's writing allows (module docstring, "Scaled")."""
    steps: List[_Step] = []
    if m.unit == PERCENT:
        steps.append((100.0, (PLAIN,), _RATIO_MAX))            # 45.1% ↔ 0.451
    elif m.unit == PLAIN:
        steps.append((100.0, (PLAIN,), _RATIO_MAX))            # 45 ↔ 0.45
        steps.append((0.01, (PERCENT,), 100.0 * _RATIO_MAX))   # 0.45 ↔ "45%"
    unit_buckets = _UNIT_SCALE_BUCKETS.get(m.unit)
    if unit_buckets:
        suffix = _suffix_scale(m)
        for f in _UNIT_SCALES:
            if f <= suffix * (1 + 1e-9):
                steps.append((f, unit_buckets, _INF))
    return tuple(steps)


# ── the audit ─────────────────────────────────────────────────────────────────


def _blank_names(text: str) -> str:
    return _CHAT_NAME_RE.sub(lambda m: " " * len(m.group(0)), text)


def _numbers_in(text: str) -> List[NumberMention]:
    """Digit numbers in `text`, chat name-numbers blanked first (indices are preserved)."""
    if not text:
        return []
    return extract_numbers(_blank_names(text))


def _core(m: NumberMention) -> Optional[Tuple[int, float]]:
    """(decimals written, value ÷ written digits) — the scale a suffix or a unit applied
    ("4.2B" → (1, 1e9), "50 bps" → (0, 0.01), "302,526" → (0, 1.0)). None when unreadable."""
    core = _CORE_RE.search(m.raw or "")
    if not core:
        return None
    decimals = len(core.group(1) or core.group(2) or "")
    try:
        base = float(core.group(0).replace(",", ""))
    except ValueError:
        return None
    if not base or not math.isfinite(base) or not math.isfinite(m.value):
        return decimals, 1.0
    return decimals, abs(m.value / base)


def _suffix_scale(m: NumberMention) -> float:
    """The power of ten a k/M/B/T (or thousand/million/billion/trillion) suffix applied to the
    written digits; 1.0 when the mention carries none."""
    info = _core(m)
    if info is None or info[1] < 999.0:
        return 1.0
    return 10.0 ** round(math.log10(info[1]))


def _tolerance(m: NumberMention) -> float:
    """Half a unit of the mention's last written digit, times its scale suffix: "4.2B" → 5e7,
    "23.4%" → 0.05, "302,526" → 0.5, "50 bps" (= 0.5%) → 0.005."""
    floor = max(abs(m.value) * 1e-9, 1e-12)
    if m.unit == FRACTION:
        return max(0.005, floor)
    info = _core(m)
    if info is None:
        return floor
    decimals, scale = info
    return max(0.5 * (10.0 ** -decimals) * scale, floor)


def _is_day_of_month(m: NumberMention, text: str) -> bool:
    if m.unit != PLAIN or m.value != int(m.value) or not 1 <= m.value <= 31:
        return False
    before = text[max(0, m.start - _MONTH_WINDOW):m.start]
    after = text[m.end:m.end + _MONTH_WINDOW]
    return bool(_MONTH_BEFORE_RE.search(before) or _MONTH_AFTER_RE.search(after))


def _is_exempt(m: NumberMention, text: str) -> bool:
    if m.unit == YEAR:
        return True
    if not math.isfinite(m.value):
        return False
    if (m.unit == PLAIN and "." not in (m.raw or "") and m.value == int(m.value)
            and abs(m.value) <= 12):
        return True
    return _is_day_of_month(m, text)


class _Sentences:
    """Sentence spans of the audited text, and whether each names a Caydex metric (lazy)."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._ends = [m.end() for m in _SENTENCE_END_RE.finditer(text)]
        self._names_metric: Dict[int, bool] = {}

    def names_metric(self, pos: int) -> bool:
        i = bisect.bisect_right(self._ends, pos)
        if i not in self._names_metric:
            start = self._ends[i - 1] if i > 0 else 0
            end = self._ends[i] if i < len(self._ends) else len(self._text)
            self._names_metric[i] = bool(_METRIC_RE.search(self._text[start:end]))
        return self._names_metric[i]


def _audit(answer: Any, evidence: Any, web_turn: bool) -> GroundingAudit:
    if web_turn:
        return GroundingAudit(skipped="web_turn")
    if not isinstance(answer, str) or not answer.strip():
        return GroundingAudit(skipped="empty")
    if len(answer) > ANSWER_MAX_CHARS:
        return GroundingAudit(skipped="too_long")
    if not isinstance(evidence, GroundingEvidence):
        return GroundingAudit(skipped="no_evidence")
    if evidence.web_seen:
        return GroundingAudit(skipped="web_turn")

    audit = GroundingAudit(answer_capped=len(answer) > ANSWER_AUDIT_CHARS,
                           evidence_capped=evidence.capped)
    text = answer[:ANSWER_AUDIT_CHARS]
    primary, prior, audit.evidence_values = evidence._indexes()
    sentences = _Sentences(text)
    for m in _numbers_in(text)[:MAX_ANSWER_NUMBERS]:
        audit.numbers += 1
        if _is_exempt(m, text):
            audit.exempt += 1
            continue
        if math.isfinite(m.value):
            tol = _tolerance(m)
            direct, scaled = _direct_plan(m.unit), _scaled_plan(m)
            if primary.find(m.value, tol, direct):
                audit.grounded += 1
                continue
            if primary.find(m.value, tol, scaled):
                audit.scaled += 1
                continue
            if prior.find(m.value, tol, direct + scaled):
                audit.prior_answer_only += 1
                continue
        audit.ungrounded += 1
        if m.unit in _SHADOW_UNITS and sentences.names_metric(m.start):
            audit.shadow_enforce += 1
    return audit


def audit_answer(answer: Any, evidence: Any, *, web_turn: bool = False) -> GroundingAudit:
    """Classify every number in `answer` against `evidence`. Pure CPU work, capped; call it
    through `asyncio.to_thread`. Never raises: a failure is `skipped="error"`, logged."""
    try:
        return _audit(answer, evidence, bool(web_turn))
    except Exception as e:  # noqa: BLE001 — log-only audit: a bug here must never reach a user
        logger.warning("CHAT_GROUNDING audit failed (%s: %s) — skipped", type(e).__name__, e,
                       exc_info=True)
        return GroundingAudit(skipped="error")


async def audit_answer_bounded(
    answer: Any, evidence: Any, *, timeout: Optional[float] = None,
) -> GroundingAudit:
    """`audit_answer` off the event loop, waited on for at most `timeout` seconds (default
    `INLINE_AUDIT_SECONDS`, read at call time) — for a caller that must not let a log-only audit
    delay its answer. A timeout is `skipped="timeout"` (the worker-thread job finishes and is
    discarded); any other failure `skipped="error"`. Never raises, except the CALLER's own
    cancellation."""
    if timeout is None:
        timeout = INLINE_AUDIT_SECONDS
    try:
        return await asyncio.wait_for(asyncio.to_thread(audit_answer, answer, evidence),
                                      timeout=timeout)
    except asyncio.CancelledError:
        raise
    except (asyncio.TimeoutError, TimeoutError):
        logger.warning("CHAT_GROUNDING inline audit exceeded %.1fs (worker threads busy) — "
                       "skipped, the answer is unaffected", timeout)
        return GroundingAudit(skipped="timeout")
    except Exception as e:  # noqa: BLE001 — e.g. the executor is shutting down
        logger.warning("CHAT_GROUNDING inline audit did not run (%s: %s)", type(e).__name__, e)
        return GroundingAudit(skipped="error")


# ── logging (counts only) ─────────────────────────────────────────────────────

_LABEL_RE = re.compile(r"[^A-Za-z0-9_\-]")
_COUNT_KEYS = ("numbers", "exempt", "grounded", "scaled", "prior_answer_only", "ungrounded",
               "shadow_enforce", "evidence_values")


def _label(value: Any, limit: int = 40) -> str:
    """A log-safe token: client-controlled values (context type, session id) can carry
    newlines or prose, so only [A-Za-z0-9_-] survives, bounded."""
    if value is None:
        return "-"
    cleaned = _LABEL_RE.sub("", str(value))[:limit]
    return cleaned or "-"


def _count(d: Dict[str, Any], key: str) -> int:
    v = d.get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def log_grounding_audit(
    audit: Any, *, door: str, session_id: Any = None, asset_type: Any = None,
    context_type: Any = None, fallback: bool = False,
) -> None:
    """Emit ONE `CHAT_GROUNDING` INFO line with counts only. Never raises."""
    try:
        if isinstance(audit, GroundingAudit):
            d = audit.as_dict()
        elif isinstance(audit, dict):
            d = audit
        else:
            d = {"skipped": "missing"}
        counts = {k: _count(d, k) for k in _COUNT_KEYS}
        capped = bool(d.get("answer_capped")) or bool(d.get("evidence_capped"))
        logger.info(
            "CHAT_GROUNDING door=%s session=%s asset=%s ctx=%s fallback=%s numbers=%d exempt=%d "
            "grounded=%d scaled=%d prior=%d ungrounded=%d shadow_enforce=%d evidence=%d "
            "capped=%s skipped=%s",
            # The audit's own asset first: a fallback's audit names the class IT resolved.
            _label(door), _label(session_id, 64), _label(d.get("asset") or asset_type),
            _label(context_type), bool(fallback), counts["numbers"], counts["exempt"],
            counts["grounded"], counts["scaled"], counts["prior_answer_only"],
            counts["ungrounded"], counts["shadow_enforce"], counts["evidence_values"],
            capped, _label(d.get("skipped")),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("CHAT_GROUNDING log line failed (%s: %s)", type(e).__name__, e)


# ── the async door helpers ────────────────────────────────────────────────────

#: Strong references to running audit tasks: the event loop keeps only weak ones, and a door
#: never awaits its task past `SETTLE_SECONDS` (or at all, when it was cancelled).
_PENDING: Set["asyncio.Task[None]"] = set()


async def _audit_and_log(
    answer: Any, evidence: Any, *, door: str, session_id: Any, asset_type: Any,
    context_type: Any, precomputed: Any, web_turn: bool, replay: Optional[str],
    fallback: bool,
) -> None:
    try:
        if precomputed is not None:
            audit: Any = precomputed
        elif replay:
            audit = GroundingAudit(skipped=str(replay))
        elif web_turn:
            audit = GroundingAudit(skipped="web_turn")
        else:
            audit = await asyncio.to_thread(audit_answer, answer, evidence)
    except Exception as e:  # noqa: BLE001 — e.g. the executor is shutting down
        logger.warning("CHAT_GROUNDING audit did not run (%s: %s)", type(e).__name__, e)
        audit = GroundingAudit(skipped="error")
    log_grounding_audit(audit, door=door, session_id=session_id, asset_type=asset_type,
                        context_type=context_type, fallback=fallback)


def start_grounding_audit(
    answer: Any, evidence: Any, *, door: str, session_id: Any = None, asset_type: Any = None,
    context_type: Any = None, precomputed: Any = None, web_turn: bool = False,
    replay: Optional[str] = None, fallback: bool = False,
) -> "Optional[asyncio.Task[None]]":
    """Start the audit + its log line as a background task and return it (None if it could
    not start). `precomputed` (the fallback's own audit dict) is logged as is; `replay`
    (a cached / warmed answer) and `web_turn` log a skip. The task holds no reference to the
    caller, so a cancelled request still gets its line. Never raises."""
    coro = _audit_and_log(
        answer, evidence, door=door, session_id=session_id, asset_type=asset_type,
        context_type=context_type, precomputed=precomputed, web_turn=bool(web_turn),
        replay=replay, fallback=bool(fallback),
    )
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except Exception as e:  # noqa: BLE001 — no running loop
        coro.close()
        logger.warning("CHAT_GROUNDING audit not started (%s: %s)", type(e).__name__, e)
        return None
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)
    return task


async def settle_grounding_audit(task: Any, timeout: float = SETTLE_SECONDS) -> None:
    """Give the audit task up to `timeout` seconds. `asyncio.wait` never cancels the task —
    not on the timeout and not when the caller itself is cancelled — so the line is written
    either way. Never raises (cancellation of the CALLER still propagates)."""
    if task is None:
        return
    try:
        await asyncio.wait({task}, timeout=timeout)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("CHAT_GROUNDING settle failed (%s: %s)", type(e).__name__, e)
