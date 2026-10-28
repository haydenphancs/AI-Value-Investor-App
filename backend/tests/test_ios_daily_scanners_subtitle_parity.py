"""The Daily Scanners subtitle must state the floor the backend actually applies.

WHY THIS FILE EXISTS. Home's "Daily Scanners" header reads
"Big moves, heavy volume, crowded shorts · $250M+ caps". The "$250M" is a promise about
`_SCANNER_MIN_MARKET_CAP` in `home_dashboard_service.py`, the one market-cap floor all three
cards share (Top Movers and Heavy Traffic through `_is_quality_company`, Skeptical Money in
`_build_shorts`). The number lives in two languages, so a floor change on the backend would
silently leave the app claiming a wrong one. This pins them together.

It also refuses "penny stocks" wording: only Movers and Heavy Traffic apply the $5 price floor.
Skeptical Money's curated list holds sub-$5 names (AMC, PLUG, FCEL, OPEN), so that claim would
be false on one of the three cards.

Per `.claude/rules/testing.md` §3: Swift comments are stripped (the subtitle's own comment
quotes the floor), the scan is brace-bound to `DailyScannersSection`'s `body` (its preview host
has a `body` too), and it was mutation-tested by hand.
"""

import ast
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SERVICE = _REPO / "backend/app/services/home_dashboard_service.py"
_SECTION = _REPO / "frontend/ios/ios/Views/Organisms/DailyScannersSection.swift"

_MONEY = re.compile(r"\$(\d+(?:\.\d+)?)([MB])\+")


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of a declaration."""
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace : i + 1]
    pytest.fail(f"unbalanced braces after {header!r}")


def _backend_floor() -> int:
    """`_SCANNER_MIN_MARKET_CAP` read by AST, so the test needs no app settings to import."""
    tree = ast.parse(_read(_SERVICE))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_SCANNER_MIN_MARKET_CAP"
        ):
            value = ast.literal_eval(node.value)
            assert isinstance(value, int) and value > 0, f"unexpected floor {value!r}"
            return value
    pytest.fail("_SCANNER_MIN_MARKET_CAP not found in home_dashboard_service.py — scan drifted")


def _as_label(dollars: int) -> str:
    """250_000_000 → "$250M"; 1_500_000_000 → "$1.5B" (the subtitle's style)."""
    if dollars >= 1_000_000_000:
        n, unit = dollars / 1_000_000_000, "B"
    else:
        n, unit = dollars / 1_000_000, "M"
    return f"${n:g}{unit}"


def _subtitle() -> str:
    section = _decl_block(_strip_comments(_read(_SECTION)), "struct DailyScannersSection: View")
    body = _decl_block(section, "var body: some View")
    # Anti-vacuity: this must be the header of the live section, not some other block.
    assert 'Text("Daily Scanners")' in body, "the Daily Scanners title moved — scan drifted"
    texts = [t for t in re.findall(r'Text\("([^"]*)"\)', body) if _MONEY.search(t)]
    assert len(texts) == 1, (
        f"expected exactly one subtitle Text stating the market-cap floor, found {texts!r}"
    )
    return texts[0]


def test_subtitle_states_the_backend_market_cap_floor():
    subtitle = _subtitle()
    figures = _MONEY.findall(subtitle)
    assert len(figures) == 1, f"subtitle should state one floor, found {figures!r}"
    stated = "${}{}".format(*figures[0])
    expected = _as_label(_backend_floor())
    assert stated == expected, (
        f"Daily Scanners subtitle says {stated}+ but the backend floor "
        f"_SCANNER_MIN_MARKET_CAP is {expected}. Update the subtitle in "
        "DailyScannersSection.swift to match the backend (or the backend to match the copy)."
    )


def test_subtitle_does_not_claim_no_penny_stocks():
    subtitle = _subtitle()
    assert "penny" not in subtitle.lower(), (
        "The subtitle claims no penny stocks, but Skeptical Money (`_build_shorts`) applies no "
        "$5 price floor and its universe holds sub-$5 names. Add the floor there first, then "
        "relax this test."
    )


@pytest.mark.parametrize(
    "dollars, label",
    [(250_000_000, "$250M"), (300_000_000, "$300M"), (1_000_000_000, "$1B"),
     (1_500_000_000, "$1.5B"), (50_000_000, "$50M")],
)
def test_label_format(dollars, label):
    assert _as_label(dollars) == label
