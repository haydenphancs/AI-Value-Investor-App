"""`ticker_news_cache.source_name` has TWO writers, and they must agree.

`NewsCacheService._build_and_cache_rows` owns the table; `SentimentService.
_persist_articles` upserts into it on the SAME `(ticker, external_id)` conflict
key. A disagreement there does not merely differ — it clobbers, and the winner is
whichever path ran last.

They did disagree. The cache service wrote `publisher or site` (an outlet name);
sentiment wrote `site or source` (a hosting domain) and never looked at
`publisher`. Production held the same outlet under both spellings — "The Motley
Fool" and "fool.com", "CNBC Television" and "youtube.com" — with the domain form
winning, which is how a Bloomberg TV segment came to be cited as youtube.com on
the Insights card.

`sentiment_service` already documents in-line that it must not clobber
`sentiment`/`sentiment_confidence`. `source_name` was simply missed, so this is
pinned from outside rather than trusted to the comment.

Also pinned: `get_cached_bulk` must SELECT the column. The Insights corpus is read
through it, and a column left out of that string degrades silently — every
publisher becomes None and the client falls back to the host with nothing failing.
"""

import re
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parents[1] / "app"
_CACHE = _APP / "services/news_cache_service.py"
_SENTIMENT = _APP / "services/sentiment_service.py"


def _strip_comments(src: str) -> str:
    """Drop `#` comment lines and trailing tails.

    Load-bearing: the comments added beside each of these fixes quote
    `source_name`, `publisher` and `youtube.com` verbatim, so an un-stripped scan
    would pass on the explanation after the code was reverted.
    """
    out = []
    for line in src.splitlines():
        if line.strip().startswith("#"):
            continue
        out.append(re.sub(r"\s+#.*$", "", line))
    return "\n".join(out)


def _assignments(path: Path, key: str) -> list:
    """Every right-hand side assigned to `"<key>":` in a dict literal."""
    return re.findall(
        rf'"{re.escape(key)}"\s*:\s*(.+)', _strip_comments(path.read_text())
    )


def _feed_derivations(path: Path) -> list:
    """The `source_name` writes that derive from a RAW FEED ROW.

    Narrower than every `"source_name":` in the file on purpose: these modules
    also carry pass-through reads (`"source_name": row["source_name"]`) in
    response builders, which have no publisher to prefer and are not what this
    invariant is about. A raw-feed derivation is the one that reaches for
    ``site`` — exactly the branch that used to win.
    """
    return [r for r in _assignments(path, "source_name") if '"site"' in r]


def _func_block(src: str, header: str) -> str:
    """The indented body of a `def`, comments stripped."""
    src = _strip_comments(src)
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    lines = src[start:].splitlines()
    base = len(lines[0]) - len(lines[0].lstrip())
    body = [lines[0]]
    for line in lines[1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= base:
            break
        body.append(line)
    return "\n".join(body)


def test_both_writers_prefer_the_publisher_over_the_domain():
    writers = _feed_derivations(_CACHE) + _feed_derivations(_SENTIMENT)
    assert len(writers) >= 2, (
        f"expected a feed-derived source_name write in BOTH services, found "
        f"{len(writers)} — this scan has drifted"
    )
    for rhs in writers:
        assert "publisher" in rhs, (
            f"a source_name writer ignores `publisher`: {rhs.strip()!r}. Both writers "
            "upsert on (ticker, external_id), so the domain-first one clobbers the "
            "outlet name and broadcast news is attributed to its video host."
        )
        assert rhs.index("publisher") < rhs.index('"site"'), (
            f"site is preferred over publisher: {rhs.strip()!r}"
        )


def test_each_service_contributes_one_of_those_writers():
    """Both halves, not two hits in one file."""
    assert _feed_derivations(_CACHE), "news_cache_service lost its source_name write"
    assert _feed_derivations(_SENTIMENT), "sentiment_service lost its source_name write"


def test_the_sentiment_writer_still_leaves_the_ai_enrichment_alone():
    """The pre-existing invariant next door — asserted so this fix cannot be
    'tidied' into overwriting Gemini's sentiment, which credits paid for."""
    body = _func_block(_SENTIMENT.read_text(), "def _persist_articles")
    assert '"source_name":' in body, "wrong block — this scan has drifted"
    assert '"sentiment":' not in body
    assert '"sentiment_confidence":' not in body


def test_get_cached_bulk_selects_source_name():
    """Read path. Omitting it makes every publisher None with nothing failing."""
    src = _strip_comments(_CACHE.read_text())
    m = re.search(r"columns\s*=\s*\(([^)]*)\)", src)
    assert m, "the get_cached_bulk column list moved — this scan has drifted"
    assert "source_name" in m.group(1), (
        "get_cached_bulk stopped selecting source_name; the Insights card silently "
        "loses every publisher and falls back to the URL host."
    )


def test_the_scanners_are_not_vacuous():
    assert _feed_derivations(_CACHE) and _feed_derivations(_SENTIMENT)

    # Comment stripping bites — the fix comments quote the reverted code verbatim.
    assert "source_name" not in _strip_comments('# "source_name": a.get("site")\nx = 1\n')

    # The narrowing bites: a pass-through read is not mistaken for a derivation.
    assert _assignments(_CACHE, "source_name") != _feed_derivations(_CACHE)

    # A key that does not exist yields nothing rather than a false pass.
    assert _assignments(_CACHE, "definitely_not_a_column") == []

    # `_func_block` stops at the next def instead of swallowing the file.
    sample = "def a():\n    x = 1\ndef b():\n    y = 2\n"
    assert "y = 2" not in _func_block(sample, "def a(")

    with pytest.raises(AssertionError):
        _func_block("def a():\n    pass\n", "def not_here")
