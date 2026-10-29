"""`app/utils/filing_text.py` — the filing-text lift out of `learn_weekly/sec_facts.py`.

`html_to_text` and `verify_peg_span` moved to a stdlib-only module so the Trillion Club stake
finder can use them; the Learn engine keeps importing them from `sec_facts`, which re-exports
the SAME objects. A quote check that drifts in a refactor puts a false quote in an article or a
stake row, so this file pins:

* identity: every re-exported name in `sec_facts` IS the `filing_text` object, and
  `sec_facts.__all__` still lists them;
* purity: the module imports only logging, re, html.parser and typing (AST), and loads no
  other `app` module (fresh interpreter);
* `html_to_text`: the default cap and log tags unchanged, `max_chars=None` reads everything,
  a caller's `log_tag`, and refusals of bad arguments;
* `verify_peg_span`: a DIFFERENTIAL test against the pre-lift code (transcribed below as
  `_pre_*`, verbatim but for names) on thousands of random digit-heavy spans, with and without
  `normalized=`; the window-edge trap (a span that ends inside a number at a window's edge looks
  whole in the window and must be refused against the full text); `normalized=` never
  re-normalises the document; and a CPU bound that a per-call scan of the whole text would fail;
* `sec_facts` itself: document names must match WHOLE (a trailing newline is refused).
"""

from __future__ import annotations

import ast
import logging
import random
import re
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, List, Optional, Tuple

import pytest

from app.config import settings
from app.services.learn_weekly import sec_facts
from app.utils import filing_text as ft
from app.utils.filing_text import (
    FILING_TEXT_MAX_CHARS,
    PEG_SPAN_MAX_CHARS,
    PEG_SPAN_MIN_CHARS,
    html_to_text,
    normalize_peg_text,
    verify_peg_span,
)

_BACKEND = Path(__file__).resolve().parents[1]
_MODULE = _BACKEND / "app" / "utils" / "filing_text.py"


# ── The pre-lift code (sec_facts.py at a5ce471c), transcribed as the oracle ──────────────
# Verbatim but for the `_pre_` names and the invisible characters written as escapes (their
# code points are pinned below). Never "fix" this copy: it is what the lift must agree with.

_PRE_PEG_SPAN_MIN_CHARS = 40
_PRE_PEG_SPAN_MAX_CHARS = 400
_PRE_FILING_TEXT_MAX_CHARS = 200_000
_PRE_SKIP_TAGS = frozenset({"script", "style", "head", "title", "noscript", "template", "ix:header"})
_PRE_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "caption", "center", "dd", "div", "dl",
    "dt", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot", "th",
    "thead", "tr", "ul",
})
_PRE_HTML_SUFFIXES = (".htm", ".html", ".xhtml", ".xml")
_PRE_HTML_SNIFF_RE = re.compile(r"<\s*/?\s*(?:html|body|div|p|table|font|span|br|td)\b", re.I)
_PRE_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
_PRE_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "«": '"', "»": '"',
})
_PRE_DIGIT_RUN_RE = re.compile(r"[0-9]+")
_PRE_NUMBER_TOKEN_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*")
_PRE_EDGE_WINDOW = 64


class _PreTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "body":
            self._skip = 0
        if tag in _PRE_SKIP_TAGS:
            self._skip += 1
        elif tag in _PRE_BLOCK_TAGS:
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        if tag in _PRE_BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _PRE_SKIP_TAGS:
            if self._skip:
                self._skip -= 1
        elif tag in _PRE_BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _pre_collapse(text: str) -> str:
    return " ".join(text.translate(_PRE_ZERO_WIDTH).split())


def _pre_html_to_text(raw: str, *, document: Optional[str] = None) -> str:
    if not isinstance(raw, str):
        return ""
    is_html = (
        isinstance(document, str) and document.lower().endswith(_PRE_HTML_SUFFIXES)
    ) or bool(_PRE_HTML_SNIFF_RE.search(raw[:65536]))
    if is_html:
        parser = _PreTextExtractor()
        try:
            parser.feed(raw)
            parser.close()
            text = "".join(parser.parts)
        except Exception:
            text = re.sub(r"<[^>]{0,2000}>", " ", raw)
    else:
        text = raw
    text = _pre_collapse(text)
    if len(text) > _PRE_FILING_TEXT_MAX_CHARS:
        text = text[:_PRE_FILING_TEXT_MAX_CHARS]
    return text


def _pre_normalise_for_match(text: str) -> str:
    return _pre_collapse(text.translate(_PRE_QUOTES))


def _pre_verify(span: Any, text: Any) -> Tuple[bool, str]:
    """The pre-lift `verify_peg_span`, returning which step decided (for coverage counts)."""
    if not isinstance(span, str) or not isinstance(text, str):
        return False, "type"
    if len(span) > _PRE_PEG_SPAN_MAX_CHARS * 4:
        return False, "length"
    needle = _pre_normalise_for_match(span)
    if not _PRE_PEG_SPAN_MIN_CHARS <= len(needle) <= _PRE_PEG_SPAN_MAX_CHARS:
        return False, "length"
    if any(ch.isdigit() and not ("0" <= ch <= "9") for ch in needle):
        return False, "foreign_digit"
    haystack = _pre_normalise_for_match(text)
    runs_in_text = set(_PRE_DIGIT_RUN_RE.findall(haystack))
    if any(run not in runs_in_text for run in _PRE_DIGIT_RUN_RE.findall(needle)):
        return False, "digit_run"          # the pre-check the lift dropped
    start = haystack.find(needle)
    while start != -1:
        if _pre_cuts_no_number(haystack, start, start + len(needle)):
            return True, "match"
        start = haystack.find(needle, start + 1)
    return False, "no_whole_match"


def _pre_cuts_no_number(haystack: str, start: int, end: int) -> bool:
    lo = max(0, start - _PRE_EDGE_WINDOW)
    window = haystack[lo:min(len(haystack), end + _PRE_EDGE_WINDOW)]
    for m in _PRE_NUMBER_TOKEN_RE.finditer(window):
        a, b = m.start() + lo, m.end() + lo
        overlaps = a < end and start < b
        inside = start <= a and b <= end
        if overlaps and not inside:
            return False
    return True


# ── Identity and purity ────────────────────────────────────────────────


_REEXPORTED = ("FILING_TEXT_MAX_CHARS", "PEG_SPAN_MIN_CHARS", "PEG_SPAN_MAX_CHARS",
               "html_to_text", "verify_peg_span", "normalize_peg_text")


@pytest.mark.parametrize("name", _REEXPORTED)
def test_sec_facts_re_exports_the_same_object(name):
    assert getattr(sec_facts, name) is getattr(ft, name)
    assert name in sec_facts.__all__ and name in ft.__all__


def test_sec_facts_all_still_lists_its_names_and_each_exists():
    for name in ("CompanySummary", "Filing8K", "METRIC_KEYS", "FILING_TEXT_MAX_CHARS",
                 "PEG_SPAN_MIN_CHARS", "PEG_SPAN_MAX_CHARS", "company_summary", "annual_facts",
                 "filing_text", "html_to_text", "verify_peg_span", "format_usd", "clear_caches"):
        assert name in sec_facts.__all__, name
    for name in sec_facts.__all__:
        assert hasattr(sec_facts, name), name
    assert set(ft.__all__) == set(_REEXPORTED)


def test_the_constants_are_unchanged():
    assert (FILING_TEXT_MAX_CHARS, PEG_SPAN_MIN_CHARS, PEG_SPAN_MAX_CHARS) == (200_000, 40, 400)


def test_sec_facts_no_longer_defines_its_own_copy():
    # The identity test above cannot see a re-definition of a SMALL int (CPython caches -5..256,
    # so a second `PEG_SPAN_MIN_CHARS = 40` is still `is`-equal); this AST check can.
    src = (_BACKEND / "app" / "services" / "learn_weekly" / "sec_facts.py").read_text("utf-8")
    tree = ast.parse(src)
    defined = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            defined |= {t.id for t in targets if isinstance(t, ast.Name)}
    lifted = set(_REEXPORTED) | {
        "_TextExtractor", "_collapse", "_cuts_no_number", "_normalise_for_match", "_ZERO_WIDTH",
        "_QUOTES", "_SKIP_TAGS", "_BLOCK_TAGS", "_HTML_SUFFIXES", "_HTML_SNIFF_RE",
        "_DIGIT_RUN_RE", "_NUMBER_TOKEN_RE", "_EDGE_WINDOW", "_TAG_STRIP_RE",
    }
    twice = sorted(lifted & defined)
    assert not twice, f"sec_facts.py defines {twice} again: two copies can drift"


def test_the_module_imports_the_stdlib_only():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative imports"
            imported.add(node.module or "")
        elif isinstance(node, ast.Call):
            called = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if called in ("import_module", "__import__"):
                pytest.fail(f"dynamic import at line {node.lineno}")
    assert imported == {"__future__", "logging", "re", "html.parser", "typing"}, imported


def test_importing_it_loads_no_other_app_module():
    probe = ("import sys; import app.utils.filing_text; "
             "print(sorted(m for m in sys.modules if m == 'app' or m.startswith('app.')))")
    out = subprocess.run([sys.executable, "-c", probe], cwd=_BACKEND, capture_output=True,
                         text=True, timeout=120, check=True)
    assert out.stdout.strip() == "['app', 'app.utils', 'app.utils.filing_text']", out


def test_the_character_tables_are_the_pre_lift_ones():
    assert set(ft._ZERO_WIDTH) == {0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF}
    assert all(v is None for v in ft._ZERO_WIDTH.values())
    assert ft._ZERO_WIDTH == _PRE_ZERO_WIDTH
    assert ft._QUOTES == _PRE_QUOTES
    assert {chr(k): v for k, v in ft._QUOTES.items()} == {
        "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
        "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"', "«": '"', "»": '"',
    }


# ── html_to_text ───────────────────────────────────────────────────────

IXBRL = """<html><head><title>8-K</title><style>.x{color:red}</style>
<script>var secret = "1,234";</script></head><body>
<div style="display:none"><ix:header><ix:hidden>2099-01-01 999,999</ix:hidden></ix:header></div>
<p>NVIDIA&nbsp;Corporation announced revenue of $39.3&#160;billion &amp; record data center
revenue.</p><table><tr><td>Revenue</td><td>$39,331</td></tr></table>
<p>It&#8217;s   the\u200b company&rsquo;s view.</p></body></html>"""


def test_the_default_cap_and_conversion_are_unchanged():
    assert html_to_text(IXBRL, document="q4fy25pr.htm") == _pre_html_to_text(
        IXBRL, document="q4fy25pr.htm")
    long_doc = "<p>" + "word " * 100_000 + "</p>"
    capped = html_to_text(long_doc, document="a.htm")
    assert len(capped) == FILING_TEXT_MAX_CHARS
    assert capped == _pre_html_to_text(long_doc, document="a.htm")


def test_max_chars_none_reads_the_whole_document():
    words = [f"w{i}" for i in range(150_000)]
    doc = "<div>" + " ".join(words) + "</div>"
    full = html_to_text(doc, document="a.htm", max_chars=None)
    assert full == " ".join(words)
    assert len(full) > FILING_TEXT_MAX_CHARS * 5
    assert full.endswith("w149999"), "the tail past the Learn cap is kept"
    # a caller's own cap, at the boundary and one under it
    assert html_to_text(doc, document="a.htm", max_chars=len(full)) == full
    assert html_to_text(doc, document="a.htm", max_chars=len(full) - 1) == full[:-1]
    assert html_to_text(doc, document="a.htm", max_chars=1) == "w"


def test_plain_text_and_degenerate_inputs_are_unchanged():
    assert html_to_text("Margin was < 5% \n\n and  rising.", document="ex99.txt",
                        max_chars=None) == "Margin was < 5% and rising."
    for raw in ("", None, 5, "<html></html>", b"<p>x</p>"):
        assert html_to_text(raw, document="a.htm") == ""
        assert html_to_text(raw, document="a.htm", max_chars=None) == ""


def test_the_capped_log_line_keeps_its_tag_or_takes_the_callers(caplog):
    caplog.set_level(logging.INFO, logger="app.utils.filing_text")
    doc = "<p>" + "word " * 50_000 + "</p>"
    html_to_text(doc, document="a.htm")
    html_to_text(doc, document="b.htm", log_tag="tc-stakes")
    html_to_text(doc, document="c.htm", max_chars=None, log_tag="tc-stakes")
    lines = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[learn-sec-text-capped] a.htm:") for m in lines), lines
    assert any(m.startswith("[tc-stakes-text-capped] b.htm:") for m in lines), lines
    assert not any("c.htm" in m for m in lines), "an uncapped read never says it was capped"


def test_a_parser_failure_falls_back_to_a_tag_strip_with_a_tagged_warning(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="app.utils.filing_text")

    def boom(self, data):
        raise AssertionError("parser exploded")

    monkeypatch.setattr(ft._TextExtractor, "feed", boom)
    text = html_to_text("<p>Kept <b>bold</b> text</p>", document="x.htm", log_tag="tc-stakes")
    assert text == "Kept bold text"
    assert any(r.getMessage().startswith("[tc-stakes-html-parse] x.htm: AssertionError")
               for r in caplog.records)
    html_to_text("<p>a</p>", document="y.htm")
    assert any(r.getMessage().startswith("[learn-sec-html-parse] y.htm:") for r in caplog.records)


@pytest.mark.parametrize("kw", [
    {"max_chars": 0}, {"max_chars": -5}, {"max_chars": True}, {"max_chars": False},
    {"max_chars": 1.5}, {"max_chars": "100"},
    {"log_tag": ""}, {"log_tag": "Learn-Sec"}, {"log_tag": "learn sec"}, {"log_tag": "x\n"},
    {"log_tag": "-x"}, {"log_tag": None}, {"log_tag": 5}, {"log_tag": "x" * 41},
])
def test_bad_arguments_are_refused_loudly(kw):
    with pytest.raises(ValueError):
        html_to_text("<p>x</p>", document="a.htm", **kw)


def test_html_to_text_differs_from_the_pre_lift_code_nowhere_on_random_documents():
    rng = random.Random(20261028)
    tags = ["p", "div", "td", "tr", "b", "span", "br", "script", "style", "head", "title",
            "ix:header", "body", "font", "table"]
    for i in range(300):
        parts = []
        for _ in range(rng.randint(1, 60)):
            r = rng.random()
            if r < 0.3:
                tag = rng.choice(tags)
                parts.append(rng.choice([f"<{tag}>", f"</{tag}>", f"<{tag}/>"]))
            elif r < 0.4:
                parts.append(rng.choice(["&amp;", "&nbsp;", "&#8217;", "&rsquo;", "&lt;", "<", ">"]))
            else:
                parts.append(rng.choice(["NVIDIA", "  ", "\n", "$1,234.5", "\u200b", "“q”", "x"]))
        raw = "".join(parts)
        for doc in ("a.htm", "a.txt", None):
            assert html_to_text(raw, document=doc) == _pre_html_to_text(raw, document=doc), raw


# ── normalize_peg_text ─────────────────────────────────────────────────


def test_normalize_peg_text_is_the_matching_normalisation():
    assert normalize_peg_text("  It’s “done”,\n\tNVIDIA\u200b\u2060Corp  ") == (
        'It\'s "done", NVIDIACorp')
    assert normalize_peg_text("«x» ‘y’ „z‟ ′″") == '"x" \'y\' "z" \'"'
    for bad in (None, 5, b"x", ["x"]):
        assert normalize_peg_text(bad) == ""
    rng = random.Random(7)
    for _ in range(200):
        text = "".join(rng.choice("ab 1\n\t\u200b’“.") for _ in range(rng.randint(0, 80)))
        assert normalize_peg_text(text) == _pre_normalise_for_match(text)
        assert len(normalize_peg_text(text)) <= len(text)


# ── verify_peg_span: the differential test against the pre-lift code ───

_WORDS = ("the", "Company", "aggregate", "purchase", "price", "of", "shares", "NVIDIA",
          "Corporation", "agreement", "issued", "sold", "Class", "A", "common", "stock", "per",
          "share", "in", "cash", "billion", "million", "approximately", "closing", "and", "to")


def _random_number(rng: random.Random) -> str:
    kind = rng.random()
    if kind < 0.15:
        return str(rng.randint(0, 9))
    if kind < 0.3:
        return str(rng.randint(10, 9999))
    if kind < 0.6:
        return f"{rng.randint(1000, 99_999_999):,}"
    if kind < 0.8:
        return f"{rng.randint(1, 9_999_999):,}.{rng.randint(0, 99):02d}"
    return f"{rng.randint(0, 999)}.{rng.randint(0, 9)}"


def _random_filing(rng: random.Random) -> str:
    out = []
    for _ in range(rng.randint(40, 160)):
        r = rng.random()
        if r < 0.33:
            out.append(rng.choice(["$", "", "", "("]) + _random_number(rng)
                       + rng.choice(["", "", "%", ",", ".", ")", " billion"]))
        elif r < 0.43:
            out.append(rng.choice("“”‘’\"'«»") + rng.choice(_WORDS) + rng.choice("“”‘’\"'«» "))
        elif r < 0.47:
            out.append(rng.choice(["\u200b", "\ufeff", "\u2060", "\u200d"]).join(rng.choice(_WORDS)))
        elif r < 0.475:
            out.append(rng.choice(["٣٩", "３９", "x²", "1٣2", "²3"]))
        else:
            out.append(rng.choice(_WORDS))
    return "".join(w + rng.choice([" ", " ", "  ", "\n", "\t", " \n "]) for w in out)


def _random_spans(rng: random.Random, text: str, n: int) -> List[str]:
    norm = _pre_normalise_for_match(text)
    spans: List[str] = []
    for _ in range(n):
        r = rng.random()
        length = rng.randint(30, 430)
        a = rng.randint(0, max(0, len(norm) - 1))
        span = norm[a:a + length]
        if r < 0.15:                                   # whitespace and quotes re-inflated
            span = span.replace(" ", rng.choice(["  ", "\n", " \t"]), rng.randint(1, 5))
            span = span.replace('"', "“", 1).replace("'", "’", 1)
        elif r < 0.25:                                 # one digit changed
            digits = [i for i, ch in enumerate(span) if "0" <= ch <= "9"]
            if digits:
                i = rng.choice(digits)
                span = span[:i] + str((int(span[i]) + 1) % 10) + span[i + 1:]
        elif r < 0.32:                                 # a digit glued on either end
            span = rng.choice(["7" + span, span + "7"])
        elif r < 0.4:                                  # taken from the RAW text
            raw_a = rng.randint(0, max(0, len(text) - 1))
            span = text[raw_a:raw_a + length]
        elif r < 0.45:                                 # from another document
            span = _pre_normalise_for_match(_random_filing(rng))[:length]
        spans.append(span)
    return spans


def test_verify_peg_span_agrees_with_the_pre_lift_code_on_random_spans():
    rng = random.Random(1028)
    reasons: dict = {}
    trues = 0
    cases = 0
    dropped_but_verbatim = 0
    for _ in range(250):
        text = _random_filing(rng)
        norm = normalize_peg_text(text)
        for span in _random_spans(rng, text, 60):
            expected, why = _pre_verify(span, text)
            reasons[why] = reasons.get(why, 0) + 1
            if why == "digit_run" and norm.find(normalize_peg_text(span)) != -1:
                # The case the lift changed: the old pre-check refused it, the new code finds
                # it verbatim and must refuse it on the cut rule instead.
                dropped_but_verbatim += 1
            got = verify_peg_span(span, text)
            got_norm = verify_peg_span(span, text, normalized=norm)
            assert got is expected, (why, span, text)
            assert got_norm is expected, (why, span, text)
            trues += expected
            cases += 1
    # Non-vacuous: both answers are common, and the step the lift dropped (a digit run that is
    # a whole number nowhere in the text) decided many of the cases now decided by the search.
    # (Seeded, so the counts are fixed: 15,000 cases, ~5,700 true, ~2,900 decided by the dropped
    # step; the floors are half of that, so only a weakened generator trips them.)
    assert cases == 15_000
    assert trues >= cases // 4, (trues, cases, reasons)
    assert cases - trues >= cases // 4, (trues, cases, reasons)
    assert reasons.get("digit_run", 0) >= 1500, reasons
    assert dropped_but_verbatim >= 500, dropped_but_verbatim   # ~1,000 with this seed
    assert reasons.get("no_whole_match", 0) >= 1500, reasons
    assert reasons.get("foreign_digit", 0) >= 500 and reasons.get("length", 0) >= 500, reasons


@pytest.mark.parametrize("span, text", [
    (None, "x" * 50), (5, "x" * 50), ("x" * 50, None), ("x" * 50, 5),
    ("x" * 5000, "x" * 5000), ("x" * 1601, "x" * 2000), ("x" * 39, "x" * 100),
])
def test_non_strings_and_absurd_lengths_are_refused_on_both_paths(span, text):
    assert verify_peg_span(span, text) is False
    norm = normalize_peg_text(text) if isinstance(text, str) else ""
    assert verify_peg_span(span, text, normalized=norm) is False
    assert _pre_verify(span, text)[0] is False


# ── The window-edge trap (why spans are verified against the FULL text) ───

FULL = ("Pursuant to the Purchase Agreement, NVIDIA Corporation purchased the Shares for an "
        "aggregate purchase price of $5,000,000,000.00 in cash, and the closing occurred on "
        "December 26, 2025.")


def test_a_span_ending_inside_a_number_at_a_window_edge_is_refused_against_the_full_text():
    cut = FULL.index("$5,000,") + len("$5,000")          # the window stops inside the amount
    window = FULL[:cut]
    span = window[-90:]
    assert span.endswith("aggregate purchase price of $5,000")
    assert verify_peg_span(span, window) is True, "the trap: inside the window it looks whole"
    assert verify_peg_span(span, FULL) is False
    assert verify_peg_span(span, FULL, normalized=normalize_peg_text(FULL)) is False
    # The whole amount verifies.
    whole = FULL[cut - 84:FULL.index(".00 in") + 3]
    assert whole.endswith("$5,000,000,000.00")
    assert verify_peg_span(whole, FULL, normalized=normalize_peg_text(FULL)) is True


def test_a_span_starting_inside_a_number_at_a_window_edge_is_refused_against_the_full_text():
    cut = FULL.index("000,000.00")                        # the window starts inside the amount
    window = FULL[cut:]
    span = window[:80]
    assert span.startswith("000,000.00 in cash")
    assert verify_peg_span(span, window) is True, "the trap: inside the window it looks whole"
    assert verify_peg_span(span, FULL) is False
    assert verify_peg_span(span, FULL, normalized=normalize_peg_text(FULL)) is False


# ── normalized= ─────────────────────────────────────────────────────────


def test_a_normalized_value_that_cannot_be_the_texts_is_refused(caplog):
    caplog.set_level(logging.WARNING, logger="app.utils.filing_text")
    span = FULL[:FULL.index(" for an")]
    assert verify_peg_span(span, FULL, normalized=normalize_peg_text(FULL)) is True
    assert verify_peg_span(span, FULL[:130], normalized=normalize_peg_text(FULL[:130])) is True
    # A window's text with the full document's normalisation: longer than the text → refused.
    assert verify_peg_span(span, FULL[:130], normalized=normalize_peg_text(FULL)) is False
    assert any("[filing-text-normalized]" in r.getMessage() for r in caplog.records)
    for bad in (5, b"x", ["x"]):
        assert verify_peg_span(span, FULL, normalized=bad) is False


def test_normalized_is_used_instead_of_re_normalising_the_document(monkeypatch):
    big = " ".join(f"word{i} ${i:,}.5" for i in range(40_000))
    norm = normalize_peg_text(big)
    span = norm[100_000:100_200].split(" ", 1)[1].rsplit(" ", 1)[0]
    seen: List[int] = []
    real = ft._collapse

    def spy(text):
        seen.append(len(text))
        return real(text)

    monkeypatch.setattr(ft, "_collapse", spy)
    assert verify_peg_span(span, big, normalized=norm) is True
    assert seen and max(seen) <= PEG_SPAN_MAX_CHARS * 4, "only the span was normalised"
    seen.clear()
    assert verify_peg_span(span, big) is True
    assert max(seen) == len(big), "anti-vacuity: without it the document IS normalised"


def test_many_spans_against_one_big_document_stay_cheap():
    # A ~4 MB digit-heavy prospectus. With `normalized=`, each check is a substring search: the
    # pre-lift per-call scan of every digit run in the text (~70 ms here, each time) is gone.
    rng = random.Random(4)
    words = []
    total = 0
    while total < 4_000_000:
        if rng.random() < 0.4:
            w = f"${rng.randint(1, 999):,}.{rng.randint(0, 99):02d}"
        else:
            w = rng.choice(_WORDS)
        words.append(w)
        total += len(w) + 1
    text = " ".join(words)
    norm = normalize_peg_text(text)
    spans = []
    for k in range(30):
        a = norm.index(" ", 100_000 * k + 7) + 1
        b = norm.index(" ", a + 150)
        spans.append(norm[a:b])
    t0 = time.process_time()
    results = [verify_peg_span(s, text, normalized=norm) for s in spans]
    elapsed = time.process_time() - t0
    assert all(results)
    assert elapsed < 1.0, f"30 checks took {elapsed:.2f}s of CPU"


# ── sec_facts after the lift: whole-name document checks ─────────────────

UA = "Caydex Test sec-contact-test@example.com"
CIK = "1045810"
ACC = "0001045810-25-000023"


class _NoSEC:
    def __init__(self):
        self.calls = []

    async def get_filing_document(self, cik, accession, document):
        self.calls.append(document)
        return "<p>never read</p>"


@pytest.mark.asyncio
@pytest.mark.parametrize("doc", ["q4fy25pr.htm\n", "a.htm\n\n", "../a.htm", "", None])
async def test_filing_text_refuses_a_document_name_that_does_not_match_whole(monkeypatch, doc):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", UA)
    sec_facts.clear_caches()
    sec = _NoSEC()
    with pytest.raises(ValueError):
        await sec_facts.filing_text(CIK, ACC, doc, client=sec)
    assert sec.calls == []


def test_a_primary_document_with_a_trailing_newline_is_counted_but_not_readable():
    recent = {
        "accessionNumber": [ACC, "0001045810-25-000024"],
        "filingDate": ["2025-02-26", "2025-02-27"],
        "form": ["8-K", "8-K"],
        "primaryDocument": ["nvda-8k.htm\n", "nvda-8k2.htm"],
        "items": ["2.02", "8.01"],
    }
    summary = sec_facts._parse_submissions({"name": "NVIDIA CORP", "filings": {"recent": recent}},
                                           "0001045810")
    docs = {f.accession: f.primary_document for f in summary.recent_8k}
    assert docs == {ACC: "", "0001045810-25-000024": "nvda-8k2.htm"}
