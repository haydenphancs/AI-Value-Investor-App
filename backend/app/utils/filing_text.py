"""The plain text of one SEC filing document, and the check that a quote is verbatim in it.

Pure: stdlib only (logging, re, html.parser, typing), no I/O, no app imports. Two pipelines
quote SEC filings and must never quote what a filing does not say:

* the Learn weekly engine (``services/learn_weekly/sec_facts.py``, which re-exports every
  public name here as the SAME object): a "why now" span the model proposes is kept only
  when it is verbatim in the filing (plan "Wiser weekly content engine" §1.5);
* the Trillion Club stake finder: every sentence a template's facts come from is checked the
  same way, against the FULL document text (plan "automatic stake finder", Templates).

``html_to_text`` turns one document into collapsed plain text: tags, scripts, styles and the
hidden inline-XBRL header removed, entities unescaped. Learn caps it at
``FILING_TEXT_MAX_CHARS`` (the default); the stake finder reads the whole document
(``max_chars=None``) — a span past a cap could never verify, and a number cut at the cap
would look whole there.

``verify_peg_span`` is the check. ``normalize_peg_text`` is the exact normalisation it
matches against, exposed so a caller verifying many spans against one document normalises it
once and passes ``normalized=``.

Lifted from ``learn_weekly/sec_facts.py`` on 2026-10-28 with its answers unchanged —
``tests/test_filing_text_utils.py`` pins them against the pre-lift code. The log tags stay
``[learn-sec-…]`` unless the caller passes its own ``log_tag``.
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "FILING_TEXT_MAX_CHARS",
    "PEG_SPAN_MIN_CHARS",
    "PEG_SPAN_MAX_CHARS",
    "html_to_text",
    "normalize_peg_text",
    "verify_peg_span",
]

#: ``html_to_text``'s default cap (the Learn reader's), in characters of collapsed text.
FILING_TEXT_MAX_CHARS = 200_000
#: A verified span is 40-400 characters long after normalisation.
PEG_SPAN_MIN_CHARS = 40
PEG_SPAN_MAX_CHARS = 400

# The caller's log tag: "[<tag>-html-parse]", "[<tag>-text-capped]".
_LOG_TAG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


# ── html_to_text ───────────────────────────────────────────────────────

# Hidden or non-content elements. `ix:header` is inline XBRL's hidden block (contexts, units,
# hidden facts) — thousands of dates and numbers no reader sees; quoting from it would cite text
# that is not in the visible filing.
_SKIP_TAGS = frozenset({"script", "style", "head", "title", "noscript", "template", "ix:header"})
_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "caption", "center", "dd", "div", "dl",
    "dt", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot", "th",
    "thead", "tr", "ul",
})
_HTML_SUFFIXES = (".htm", ".html", ".xhtml", ".xml")
_HTML_SNIFF_RE = re.compile(r"<\s*/?\s*(?:html|body|div|p|table|font|span|br|td)\b", re.I)
# The last-resort tag strip (bounded: a stray "<" cannot make it scan the rest of the file).
_TAG_STRIP_RE = re.compile(r"<[^>]{0,2000}>")
# Zero-width characters EDGAR HTML scatters through words; they would break a verbatim match:
# zero width space, zero width non-joiner, zero width joiner, word joiner, BOM (ZWNBSP).
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # entities unescaped by the parser
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "body":
            self._skip = 0  # an unclosed <head> must not swallow the whole body
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        # <br/>, <hr/>: a separator. A self-closed skip tag opens nothing.
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            if self._skip:
                self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")  # "<td>Revenue</td><td>$1</td>" must not glue the cells

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _collapse(text: str) -> str:
    return " ".join(text.translate(_ZERO_WIDTH).split())


def html_to_text(
    raw: str,
    *,
    document: Optional[str] = None,
    max_chars: Optional[int] = FILING_TEXT_MAX_CHARS,
    log_tag: str = "learn-sec",
) -> str:
    """Plain text of one filing document, whitespace collapsed, at most ``max_chars``
    characters (``None``: the whole document). HTML when the document name says so (or the
    body looks like it); a plain-text exhibit is only collapsed — a literal "<" in it is text,
    not a tag. "" for a non-string ``raw``.

    ``log_tag`` names the caller in the two log lines (``[<tag>-html-parse]`` WARNING when the
    parser fails and a tag strip is used, ``[<tag>-text-capped]`` INFO when the cap cuts).

    Raises:
        ValueError: ``max_chars`` is not None or a positive int, or ``log_tag`` is not
            lower-case letters, digits and dashes (a caller's bug, never a document's).
    """
    if max_chars is not None and (
        isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1
    ):
        raise ValueError(
            f"html_to_text: max_chars must be a positive int or None, got {max_chars!r}"
        )
    if not isinstance(log_tag, str) or not _LOG_TAG_RE.fullmatch(log_tag):
        raise ValueError(
            f"html_to_text: log_tag must be lower-case words and dashes, got {log_tag!r}"
        )
    if not isinstance(raw, str):
        return ""
    is_html = (
        isinstance(document, str) and document.lower().endswith(_HTML_SUFFIXES)
    ) or bool(_HTML_SNIFF_RE.search(raw[:65536]))
    if is_html:
        parser = _TextExtractor()
        try:
            parser.feed(raw)
            parser.close()
            text = "".join(parser.parts)
        except Exception as exc:  # HTMLParser is lenient; this is a last resort, never silent
            logger.warning(
                "[%s-html-parse] %s: %s: %s — falling back to a tag strip",
                log_tag, document, type(exc).__name__, exc,
            )
            text = _TAG_STRIP_RE.sub(" ", raw)
    else:
        text = raw
    text = _collapse(text)
    if max_chars is not None and len(text) > max_chars:
        logger.info(
            "[%s-text-capped] %s: %d chars cut to %d — a span past the cap cannot verify",
            log_tag, document, len(text), max_chars,
        )
        text = text[:max_chars]
    return text


# ── verify_peg_span ────────────────────────────────────────────────────

# Typographic quotes → straight ones, one character for one (left/right single quote, low-9 and
# high-reversed-9 single quotes, prime; left/right double quote, low-9 and high-reversed-9
# double quotes, double prime; the two guillemets).
_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "″": '"',
    "«": '"', "»": '"',
})
# A number as written in a filing: "130,497", "2.02", "1,234.5".
_NUMBER_TOKEN_RE = re.compile(r"[0-9]+(?:[.,][0-9]+)*")
_EDGE_WINDOW = 64


def normalize_peg_text(text: str) -> str:
    """The text ``verify_peg_span`` matches in: zero-width characters removed, typographic
    quotes made straight, every whitespace run one space, no outer whitespace. "" for a
    non-string.

    Never longer than ``text``. A caller verifying many spans against ONE document computes
    this once and passes it as ``verify_peg_span(..., normalized=)``.
    """
    if not isinstance(text, str):
        return ""
    return _collapse(text.translate(_QUOTES))


def verify_peg_span(span: Any, text: Any, *, normalized: Optional[str] = None) -> bool:
    """True when ``span`` is a verbatim quote of ``text`` (the filing's plain text).

    Only whitespace runs, zero-width characters and curly vs straight quotes are normalised;
    everything else — case, dashes, digits — must match exactly. The span must be 40-400
    characters after normalisation, may hold only ASCII digits (a Unicode digit is a homoglyph
    the downstream number grounding cannot read), and must never cut a number: every digit
    run of the span is a whole number of the text at the matched position ("$13" quoted from
    "$130.5 billion" is refused).

    ``normalized`` is ``normalize_peg_text(text)``, computed once by a caller that verifies
    many spans against one document; the answer is the same with or without it. The span is
    matched in ``normalized`` when it is given, so it must be THIS text's normalisation and
    never a window of it: a window can cut a number at its edge, and the cut would look
    whole there. A value longer than ``text`` cannot be its normalisation and is refused.
    """
    if not isinstance(span, str) or not isinstance(text, str):
        return False
    if normalized is not None:
        if not isinstance(normalized, str):
            return False
        if len(normalized) > len(text):
            logger.warning(
                "[filing-text-normalized] verify_peg_span: `normalized` (%d chars) is longer "
                "than `text` (%d chars), so it is not that text's normalisation — span refused",
                len(normalized), len(text),
            )
            return False
    if len(span) > PEG_SPAN_MAX_CHARS * 4:  # cheap bound before any normalisation work
        return False
    needle = normalize_peg_text(span)
    if not PEG_SPAN_MIN_CHARS <= len(needle) <= PEG_SPAN_MAX_CHARS:
        return False
    if any(ch.isdigit() and not ("0" <= ch <= "9") for ch in needle):
        return False
    haystack = normalize_peg_text(text) if normalized is None else normalized
    # The pre-lift code also refused, before searching, a span holding a digit run that is a
    # whole number NOWHERE in the text — a set of every digit run of the haystack, rebuilt on
    # every call (~0.2 s for a 12 MB prospectus, which the stake finder checks many spans
    # against). It never changed an answer: where `_cuts_no_number` accepts a match, no number
    # crosses the span's ends, so each digit run of the span is a whole digit run of the text
    # right there. Dropped; the differential test pins every answer to the pre-lift code.
    start = haystack.find(needle)
    while start != -1:
        if _cuts_no_number(haystack, start, start + len(needle)):
            return True
        start = haystack.find(needle, start + 1)
    return False


def _cuts_no_number(haystack: str, start: int, end: int) -> bool:
    """No number token of ``haystack`` is partly inside [start, end) and partly outside."""
    lo = max(0, start - _EDGE_WINDOW)
    window = haystack[lo:min(len(haystack), end + _EDGE_WINDOW)]
    for m in _NUMBER_TOKEN_RE.finditer(window):
        a, b = m.start() + lo, m.end() + lo
        overlaps = a < end and start < b
        inside = start <= a and b <= end
        if overlaps and not inside:
            return False
    return True
