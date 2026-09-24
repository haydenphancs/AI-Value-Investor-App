"""Learn content must not carry a famous misattribution (found 2026-09-23).

Found while the brand cover's weekly quotes were being checked against primary sources
(`test_ios_weekly_investor_quotes.py`, whose BANNED_PHRASES explain each case). Three had already
shipped in Learn:

1. Journey › Compound Interest, card description: "Discover why Einstein called it the eighth
   wonder of the world." There is no evidence Einstein ever said it.
2. Money Moves › "Warren Buffett's Early Days", quote block: "In the short run the market is a
   voting machine, but in the long run it is a weighing machine." credited to Benjamin Graham.
   That wording is Buffett's, from his 1987 Berkshire letter ("As Ben said: 'In the short run,
   the market is a voting machine but in the long run it is a weighing machine.'"). Graham's
   IDEA, Buffett's WORDS, so the block has to name both.
3. Wiser › Journey quote card (`InvestorQuote.buffettQuote`): "The stock market is a device for
   transferring money from the impatient to the patient." No primary source exists. It is now
   the verbatim 1991-letter sentence, which is also week 34 of the cover rotation.

This mirrors the cover's BANNED_PHRASES approach, with one difference. Learn has NARRATED prose,
and some wording the cover bans outright is genuine when credited correctly. So there are two
lists:

* BANNED_PHRASES: wording whose only popular form is a misattribution or unsourced lore.
  Scanned in every string of the Journey and Money Moves JSON (bundle AND the vendored copies
  `seed_*.py` falls back to) and every comment-stripped line of Swift in the app.
  Phrases from the cover's list that are ordinary English ("never lose money", "this time is
  different", "single income", "buy the rumor") are left OUT. In narrated prose they would be
  false positives ("no strategy can promise you'll never lose money").
* ATTRIBUTION_REQUIRED: wording that is genuine, but only as a particular person's. A content
  block (a Money Moves block, a Journey card or a lesson) that uses one must name every listed
  person in its own strings (text + attribution + headline ...). This rule is NOT applied to
  Swift: the Books prose legitimately paraphrases "price is what you pay" without a byline.

Read-along timings (`readAlong`, `itemsReadAlong`, `readAlongWords`) are skipped by the
attribution rule. They are sentence/word splits of their block's own `text`, pinned to it by the
alignment/schema parity tests, and a split sentence carries no attribution of its own. The
banned-phrase scan still reads them.

⚠️ Editing a narrated `text` to satisfy this test means re-voicing that clip (see
.claude/rules/learn-content.md). A block's `attribution` and a lesson's `description` are not
narrated, so changing them needs only a reseed.

Category 1 (pure): no network, no Supabase.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
IOS = REPO / "frontend/ios/ios"

LEARN_JSON = {
    "journey (bundle)": IOS / "Resources/Journey/journey_lessons.json",
    "journey (vendored)": BACKEND / "data/journey_lessons.json",
    "money moves (bundle)": IOS / "Resources/MoneyMoves/money_moves.json",
    "money moves (vendored)": BACKEND / "data/money_moves.json",
}
WEEKLY_QUOTES_JSON = IOS / "Resources/InvestorQuotes/weekly_investor_quotes.json"
PATH_MODELS = IOS / "Models/InvestorPathModels.swift"
QUOTE_CARD = IOS / "Views/Molecules/InvestorQuoteCard.swift"
PATH_VIEW_MODEL = IOS / "ViewModels/InvestorPathViewModel.swift"

# Wording whose only popular form is a misattribution or unsourced lore (normalised substrings).
BANNED_PHRASES = [
    "eighth wonder",                                    # not Einstein (shipped: Journey description)
    "einstein",                                         # in finance copy, only ever that myth
    "compound interest is the most powerful",           # not Einstein
    "most powerful force in the universe",              # not Einstein
    "impatient to the patient",                         # no primary source (shipped: Journey card)
    "irrational longer than you can remain solvent",    # not Keynes
    "when the facts change",                            # not Keynes
    "blood in the streets",                             # apocryphal Rothschild
    "investment in knowledge pays",                     # not Franklin
    "roughly right than precisely wrong",               # Carveth Read, pinned on Keynes/Buffett
    "sells to optimists",                               # Jason Zweig's 2003 commentary, not Graham
    "four most dangerous words",                        # Templeton via secondary sources only
    "rolls royce",                                      # unsourced Buffett lore
    "sitting in the shade",                             # unsourced Buffett lore
    "make money while you sleep",                       # unsourced Buffett lore
    "spend what is left after saving",                  # unsourced Buffett lore
    "protection against ignorance",                     # unsourced Buffett lore
    "best time to plant a tree",                        # misattributed proverb
]

# Genuine wording, but only as these people's: the block using it must name all of them.
ATTRIBUTION_REQUIRED = {
    # Buffett's rendering of Graham (1987 and 1993 Berkshire letters: "As Ben said: ...").
    # Shipped credited to Graham alone.
    "short run the market is a voting machine": ("buffett", "graham"),
    # Buffett crediting Graham (2008 letter: "Long ago, Ben Graham taught me that ...").
    "price is what you pay": ("buffett", "graham"),
}

# Keys whose values mirror a block's `text` sentence-by-sentence / word-by-word.
READ_ALONG_KEYS = {"readAlong", "itemsReadAlong", "readAlongWords"}

# Everything else in this test depends on the Journey card still being the verbatim letter text.
VERIFIED_1991 = (
    "Our stay-put behavior reflects our view that the stock market serves as a relocation center "
    "at which money is moved from the active to the patient."
)


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Casefold, straighten apostrophes, drop punctuation (same as the cover's test), so
    "short-run," and "short run" match alike."""
    text = unicodedata.normalize("NFKC", text).casefold().replace("’", "'").replace("'", "")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_comments(src: str) -> str:
    """Drop `/* */` blocks, whole-line `//` comments and trailing `//` tails.

    Load-bearing both ways: the comment beside a fix may name the wording it removed (an
    un-stripped ban scan would fail on that prose), and it usually names the code it added (an
    un-stripped presence scan would pass on the comment after the code was reverted).
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        m = re.search(r"\s//", line)
        if m and line[: m.start()].count('"') % 2 == 0:
            line = line[: m.start()]
        out.append(line)
    return "\n".join(out)


def _all_strings(node, path: str = "$"):
    """Every string value in a JSON document, with its path."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _all_strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _all_strings(value, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def _content_blocks(node, path: str = "$"):
    """Every dict that is a unit of content (article, section, block, lesson, card), except
    those under a read-along key. Yields (path, own_strings): the dict's direct string values
    plus the strings in its direct string lists (a bulletList's `items`)."""
    if isinstance(node, dict):
        own = []
        for key, value in node.items():
            if isinstance(value, str):
                own.append(value)
            elif isinstance(value, list) and value and all(isinstance(v, str) for v in value):
                own.extend(value)
        yield path, own
        for key, value in node.items():
            if key in READ_ALONG_KEYS:
                continue
            if isinstance(value, (dict, list)):
                yield from _content_blocks(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _content_blocks(value, f"{path}[{i}]")


def banned_phrase_violations(doc) -> list[str]:
    out = []
    for path, value in _all_strings(doc):
        text = _norm(value)
        for phrase in BANNED_PHRASES:
            if _norm(phrase) in text:
                out.append(f"{path}: {phrase!r} in {value[:120]!r}")
    return out


def attribution_violations(doc) -> list[str]:
    out = []
    for path, own in _content_blocks(doc):
        context = _norm(" ".join(own))
        for phrase, names in ATTRIBUTION_REQUIRED.items():
            if _norm(phrase) not in context:
                continue
            missing = [n for n in names if n not in context]
            if missing:
                out.append(f"{path}: {phrase!r} must credit {names}, missing {missing}: {own[:3]!r}")
    return out


def _load(path: Path):
    assert path.exists(), f"{path} is missing, so every assertion below would be vacuous"
    return json.loads(path.read_text(encoding="utf-8"))


def _swift_call(src: str, anchor: str) -> str:
    """The paren-balanced argument list that opens at the first `(` after `anchor`."""
    at = src.find(anchor)
    assert at >= 0, f"`{anchor}` not found"
    open_at = src.index("(", at)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[open_at + 1 : i]
    pytest.fail(f"unbalanced parentheses after `{anchor}`")


def _swift_string_arg(args: str, label: str) -> str | None:
    m = re.search(rf'\b{label}:\s*"((?:[^"\\]|\\.)*)"', args)
    return m.group(1).replace('\\"', '"').replace("\\\\", "\\") if m else None


def _block_after(src: str, anchor: str) -> str:
    at = src.find(anchor)
    assert at >= 0, f"`{anchor}` not found"
    open_at = src.index("{", at)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at : i + 1]
    pytest.fail(f"unbalanced braces after `{anchor}`")


# ---------------------------------------------------------------------------------------------
# A. The Learn JSON (what the seeders publish and the app falls back to)
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(LEARN_JSON))
def test_no_banned_misattribution_in_learn_json(name):
    bad = banned_phrase_violations(_load(LEARN_JSON[name]))
    assert not bad, f"{name}: famous misattribution / unsourced lore:\n  " + "\n  ".join(bad)


@pytest.mark.parametrize("name", sorted(LEARN_JSON))
def test_attributed_wording_credits_the_right_people(name):
    bad = attribution_violations(_load(LEARN_JSON[name]))
    assert not bad, f"{name}:\n  " + "\n  ".join(bad)


@pytest.mark.parametrize("name", sorted(LEARN_JSON))
def test_the_scan_reads_the_whole_document(name):
    """Anti-vacuity: a schema change that moved the content under a key the walker cannot reach
    would pass every test above with nothing scanned."""
    doc = _load(LEARN_JSON[name])
    strings = sum(1 for _ in _all_strings(doc))
    blocks = sum(1 for _ in _content_blocks(doc))
    assert strings > 1000 and blocks > 200, (name, strings, blocks)


# ---------------------------------------------------------------------------------------------
# B. The Swift sources (offline sample data, previews, the Journey quote card)
# ---------------------------------------------------------------------------------------------

def test_no_banned_misattribution_in_swift():
    files = sorted(IOS.rglob("*.swift"))
    assert len(files) > 400, f"only {len(files)} Swift files found, so the scan would be vacuous"
    bad = []
    for path in files:
        for no, line in enumerate(_strip_comments(path.read_text(errors="ignore")).splitlines(), 1):
            text = _norm(line)
            for phrase in BANNED_PHRASES:
                if _norm(phrase) in text:
                    bad.append(f"{path.relative_to(IOS)} (stripped line {no}): {phrase!r}")
    assert not bad, "famous misattribution / unsourced lore in Swift:\n  " + "\n  ".join(bad)


def test_journey_quote_card_is_a_verified_weekly_quote():
    """The Journey card is hardcoded Swift, outside the cover's contract. Tie it to the
    verified list: the same text must exist there with the same author, source and year, so it
    inherits that entry's primary-source citation."""
    args = _swift_call(_strip_comments(PATH_MODELS.read_text()), "static let buffettQuote = InvestorQuote")
    text = _swift_string_arg(args, "text")
    author = _swift_string_arg(args, "author")
    source = _swift_string_arg(args, "source")
    year_m = re.search(r"\byear:\s*(\d{4})\b", args)
    assert text and author, args
    assert source and year_m, "the Journey quote must carry its primary source (source: + year:)"

    weekly = {q["text"]: q for q in _load(WEEKLY_QUOTES_JSON)["quotes"]}
    assert text in weekly, (
        f"`.buffettQuote` is not one of the verified weekly quotes: {text!r}. Pick a quote from "
        f"weekly_investor_quotes.json (each carries a primary source), or add it there first.")
    entry = weekly[text]
    got = (author, source, int(year_m.group(1)))
    want = (entry["author"], entry["source"], entry["year"])
    assert got == want, f"`.buffettQuote` (author, source, year) {got} disagrees with week {entry['week']}: {want}"
    assert text == VERIFIED_1991


def test_journey_view_model_still_shows_that_quote():
    assert "@Published var quote: InvestorQuote = .buffettQuote" in _strip_comments(PATH_VIEW_MODEL.read_text())


def test_quote_card_renders_the_citation_and_reads_as_one_element():
    card = _block_after(_strip_comments(QUOTE_CARD.read_text()), "struct InvestorQuoteCard: View")
    assert "if let citation = quote.citation" in card
    assert "Text(verbatim: citation)" in card, "a citation is data: render it verbatim, not as Markdown"
    assert ".accessibilityElement(children: .ignore)" in card
    assert ".accessibilityLabel(quote.accessibilityLabel)" in card


# ---------------------------------------------------------------------------------------------
# C. Guard-the-guard: the three shipped defects, replayed through the checkers
# ---------------------------------------------------------------------------------------------

def test_checker_flags_the_shipped_einstein_description():
    doc = {"lessons": [{"title": "Compound Interest",
                        "description": "Discover why Einstein called it the eighth wonder of the world.",
                        "cards": []}]}
    hits = banned_phrase_violations(doc)
    assert any("eighth wonder" in h for h in hits) and any("einstein" in h for h in hits), hits


def test_checker_flags_the_shipped_graham_attribution_and_accepts_the_fix():
    text = "In the short run the market is a voting machine, but in the long run it is a weighing machine."
    block = {"type": "quote", "text": text, "attribution": "Benjamin Graham",
             "readAlong": [{"text": text, "start": 124.66, "end": 129.66}]}
    doc = {"articles": [{"sections": [{"content": [block]}]}]}
    hits = attribution_violations(doc)
    assert len(hits) == 1 and "buffett" in hits[0], hits

    block["attribution"] = "Warren Buffett, paraphrasing Benjamin Graham"
    assert attribution_violations(doc) == []

    # Punctuation variants (Buffett's 1993 "short-run,") and a missing attribution are caught too.
    block["text"] = "In the short-run, the market is a voting machine."
    block.pop("attribution")
    assert attribution_violations(doc), "an unattributed quote block passed"


def test_read_along_splits_are_not_judged_on_their_own():
    """A split sentence carries no attribution; judging it alone would fail every correctly
    credited quote. Its parent block is what is judged."""
    text = "Price is what you pay; value is what you get."
    ok = {"type": "quote", "text": text, "attribution": "Warren Buffett, quoting Benjamin Graham",
          "readAlong": [{"text": text, "start": 0.0, "end": 3.0}]}
    assert attribution_violations({"content": [ok]}) == []
    bare = {"type": "paragraph", "text": text, "readAlong": [{"text": text, "start": 0.0, "end": 3.0}]}
    assert len(attribution_violations({"content": [bare]})) == 1


def test_checker_flags_the_shipped_journey_card_quote():
    swift = ('static let buffettQuote = InvestorQuote(\n'
             '    text: "The stock market is a device for transferring money from the impatient to the patient.",\n'
             '    author: "Warren Buffett"\n)')
    assert any(_norm(p) in _norm(line) for line in _strip_comments(swift).splitlines() for p in BANNED_PHRASES)


def test_comment_stripping_is_not_vacuous():
    src = '// the eighth wonder\nlet a = "x" // impatient to the patient\n/* Einstein */let b = 1\n'
    stripped = _strip_comments(src)
    assert "eighth" not in stripped and "impatient" not in stripped and "Einstein" not in stripped
    assert 'let a = "x"' in stripped and "let b = 1" in stripped


def test_every_rule_phrase_normalises_to_something():
    for phrase in list(BANNED_PHRASES) + list(ATTRIBUTION_REQUIRED):
        assert _norm(phrase) == phrase, f"write rule phrases pre-normalised: {phrase!r}"
    assert len(set(BANNED_PHRASES)) == len(BANNED_PHRASES)
