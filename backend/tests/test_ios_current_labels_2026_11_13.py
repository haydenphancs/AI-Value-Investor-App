"""Two "current"-label fixes on iOS (owner decisions, 2026-11-13).

"Current" means NOW only; a closed period is always named by its period.

1. **Whale profile 13F picks.** The paid holdings section of a 13F filer was titled
   "Current Picks", both on the locked card and on the unlocked section header. A 13F is a
   quarter-end snapshot filed up to 45 days later, so the owner chose ("Whale 'Current
   Picks'") to name the filing quarter: "Picks · Q3 2026 13F". The quarter is
   `WhaleProfile.portfolioAsOf`, which the backend builds from the SAME snapshot as the
   holdings (`whale_service._stat_disclosure` -> `filing_period_display`) and keeps when the
   profile is locked (`redact_whale_profile`). With no quarter the title still names the
   source ("Picks · Latest 13F") instead of calling the picks current. The congressional
   "Recently Traded" wording is unchanged.

2. **Technical tab, Volume Analysis.** The percent under "Current Volume" is the latest daily
   bar against the bar before it (`technical_analysis_service._compute_volume_analysis`,
   `vol_change`, fed `df_daily`), not against the 30-day average printed in the card beside
   it. The owner chose ("Volume % comparator") to name it: "+12% vs prior day".

There is no XCTest target, so the iOS half reads the Swift source (testing.md §3): comments
are stripped before every assertion, every check is brace-bound to the ONE declaration it is
about, and the two label functions are PORTED from the Swift text (their literals are read
out of the source) and run against a case table, so the table fails when the Swift changes.

Every Swift guard is a `_check_*` function over raw source text, and
`test_each_guard_fails_on_its_mutation` applies one mutation per guard to an in-memory copy
of the real source and asserts the guard then fails — the Swift files on disk are never
edited.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import pytest

from app.schemas.whale import WhaleBehaviorSummaryResponse, WhaleProfileResponse
from app.services.technical_analysis_service import TechnicalAnalysisService
from app.services.whale_service import WhaleService, redact_whale_profile

_REPO = Path(__file__).resolve().parents[2]
_IOS_ROOT = _REPO / "frontend" / "ios"
_IOS = _IOS_ROOT / "ios"
_SERVICES = _REPO / "backend" / "app" / "services"

_WHALE_VIEW = _IOS / "Views" / "Screens" / "WhaleProfileView.swift"
_WHALE_DTOS = _IOS / "Models" / "WhaleDTOs.swift"
_WHALE_MODELS = _IOS / "Models" / "WhaleProfileModels.swift"
_DETAIL_MODELS = _IOS / "Models" / "TickerDetailModels.swift"
_TECH_SECTIONS = _IOS / "Views" / "Molecules" / "TechnicalDetailSections.swift"
_WHALE_SERVICE = _SERVICES / "whale_service.py"
_TA_SERVICE = _SERVICES / "technical_analysis_service.py"


# ── helpers ──────────────────────────────────────────────────────────────────


def _strip_swift_comments(src: str) -> str:
    """Drop `/* */` blocks and `//` line comments. A `//` inside a string literal is kept:
    the scan walks each line and ignores `//` while inside quotes."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out_lines = []
    for line in src.splitlines():
        in_str = False
        cut = len(line)
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "\\" and in_str:
                i += 2
                continue
            if ch == '"':
                in_str = not in_str
            elif not in_str and line.startswith("//", i):
                cut = i
                break
            i += 1
        out_lines.append(line[:cut])
    return "\n".join(out_lines)


def _raw(path: Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved; update this guard, do not delete it"
    return path.read_text(encoding="utf-8")


def _block_from(src: str, open_brace: int) -> str:
    """The text from the `{` at `open_brace` through its matching `}`."""
    assert src[open_brace] == "{", src[open_brace: open_brace + 40]
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace: i + 1]
    raise AssertionError("unbalanced braces")


def _decl(src: str, decl_regex: str) -> str:
    """Brace-bound body of the ONE declaration matching `decl_regex`."""
    matches = list(re.finditer(decl_regex, src))
    assert len(matches) == 1, f"{decl_regex!r} matched {len(matches)} times"
    return _block_from(src, src.index("{", matches[0].end()))


def _if_then_else(body: str, condition_regex: str) -> Tuple[str, Optional[str]]:
    """(then-block, else-block or None) of the FIRST `if <condition> {` in `body`."""
    m = re.search(rf"\bif\s+{condition_regex}\s*\{{", body)
    assert m, f"no `if {condition_regex}` block"
    then_block = _block_from(body, m.end() - 1)
    rest = body[m.end() - 1 + len(then_block):]
    em = re.match(r"\s*else\s*\{", rest)
    else_block = _block_from(rest, em.end() - 1) if em else None
    return then_block, else_block


def _call_args(src: str, callee_regex: str) -> List[str]:
    """The parenthesised argument text of every call to `callee_regex` in `src`."""
    out = []
    for m in re.finditer(rf"{callee_regex}\s*\(", src):
        start = m.end() - 1
        depth = 0
        for i in range(start, len(src)):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    out.append(src[start: i + 1])
                    break
        else:  # pragma: no cover - a malformed file
            raise AssertionError(f"unbalanced parens after {callee_regex}")
    return out


def test_the_comment_stripper_ignores_prose_and_keeps_strings():
    sample = (
        '// Text("Current Picks")\n'
        '/* title: "Current Picks", */\n'
        'let a = "Picks · \\(quarter) 13F" // was "Current Picks"\n'
    )
    out = _strip_swift_comments(sample)
    assert "Current Picks" not in out
    assert '"Picks · \\(quarter) 13F"' in out


def test_the_brace_bound_scan_does_not_leak_into_the_next_declaration():
    src = 'struct A { var x: Int { 1 } }\nstruct B { let y = "Current Picks" }'
    assert _decl(src, r"\bstruct\s+A\b") == "{ var x: Int { 1 } }"
    assert "Current Picks" not in _decl(src, r"\bstruct\s+A\b")


# ── 1. Whale profile: the 13F picks are named by their filing quarter ────────

_PICKS_SECTION_DECL = r"\bstruct\s+WhaleCurrentPicksSection\s*:\s*View\s*"
_PICKS_TITLE_DECL = (
    r"\bstatic\s+func\s+picksTitle\s*\(\s*filingQuarter\s*:\s*String\?\s*\)\s*->\s*String\s*"
)


def _picks_title_port(raw: str) -> Callable[[Optional[str]], str]:
    """Python port of `WhaleCurrentPicksSection.picksTitle(filingQuarter:)`, built from the
    literals in the Swift source so a change to either string changes the port."""
    code = _strip_swift_comments(raw)
    fn = _decl(_decl(code, _PICKS_SECTION_DECL), _PICKS_TITLE_DECL)
    assert re.search(
        r"\blet\s+quarter\s*=\s*\(\s*filingQuarter\s*\?\?\s*\"\"\s*\)"
        r"\s*\.trimmingCharacters\(\s*in\s*:\s*\.whitespacesAndNewlines\s*\)",
        fn,
    ), "picksTitle must trim the quarter (nil and blank both fall back)"
    fallback = re.search(
        r"\bguard\s+!quarter\.isEmpty\s+else\s*\{\s*return\s+\"((?:[^\"\\]|\\.)*)\"\s*\}", fn
    )
    assert fallback, "picksTitle must fall back when the quarter is blank"
    returns = re.findall(r"\breturn\s+\"((?:[^\"\\]|\\.)*)\"", fn)
    assert len(returns) == 2, f"expected the fallback and the titled return, got {returns!r}"
    template = returns[1]
    assert template.count("\\(quarter)") == 1, template
    assert "\\(" not in template.replace("\\(quarter)", ""), template

    def port(filing_quarter: Optional[str]) -> str:
        quarter = (filing_quarter or "").strip()
        if not quarter:
            return fallback.group(1)
        return template.replace("\\(quarter)", quarter)

    return port


_PICKS_TITLE_CASES = [
    ("Q3 2026", "Picks · Q3 2026 13F"),
    ("Q4 2025", "Picks · Q4 2025 13F"),
    (" Q3 2026\n", "Picks · Q3 2026 13F"),
    (None, "Picks · Latest 13F"),
    ("", "Picks · Latest 13F"),
    ("   ", "Picks · Latest 13F"),
]


def _check_picks_title(raw: str) -> None:
    port = _picks_title_port(raw)
    for quarter, expected in _PICKS_TITLE_CASES:
        got = port(quarter)
        assert got == expected, f"picksTitle({quarter!r}) = {got!r}, want {expected!r}"
        # A 13F is never "current" — with or without a quarter.
        assert "Current" not in got and "13F" in got, got


def _check_picks_header(raw: str) -> None:
    """The unlocked section header uses the shared title; congressional wording unchanged."""
    section = _decl(_strip_swift_comments(raw), _PICKS_SECTION_DECL)
    assert re.search(r"\bvar\s+filingQuarter\s*:\s*String\?\s*=\s*nil\b", section), (
        "WhaleCurrentPicksSection must take the filing quarter"
    )
    assert re.search(
        r"\bText\(\s*isCongressional\s*\?\s*\"Recently Traded\"\s*:\s*"
        r"Self\.picksTitle\(\s*filingQuarter\s*:\s*filingQuarter\s*\)\s*\)",
        section,
    ), "the header must read picksTitle(filingQuarter:) for a 13F and 'Recently Traded' otherwise"
    assert "Current Picks" not in section


def _check_profile_wiring(raw: str) -> None:
    """The 13F branch: the locked card AND the unlocked section are titled by the quarter."""
    code = _strip_swift_comments(raw)
    view = _decl(code, r"\bstruct\s+WhaleProfileView\s*:\s*View\s*")
    thirteen_f, _ = _if_then_else(view, r"!profile\.isCongressional")
    locked, unlocked = _if_then_else(thirteen_f, r"profile\.isLocked")
    assert unlocked is not None, "the 13F picks block lost its unlocked branch"

    locked_calls = _call_args(locked, r"\blockedSection")
    assert len(locked_calls) == 1, locked_calls
    assert re.search(
        r"\btitle\s*:\s*WhaleCurrentPicksSection\.picksTitle\(\s*filingQuarter\s*:\s*"
        r"profile\.portfolioAsOf\s*\)",
        locked_calls[0],
    ), "the locked 13F card must be titled by the filing quarter"

    section_calls = _call_args(unlocked, r"\bWhaleCurrentPicksSection")
    assert len(section_calls) == 1, section_calls
    assert re.search(
        r"\bfilingQuarter\s*:\s*profile\.portfolioAsOf\b", section_calls[0]
    ), "the unlocked 13F section must be given the filing quarter"

    # The congressional wording outside the 13F block is unchanged.
    assert re.search(
        r"\btitle\s*:\s*profile\.isCongressional\s*\?\s*\"Recently Traded\"\s*:\s*\"Recent Trades\"",
        view,
    ), "the Recent Trades locked title (congressional 'Recently Traded') must not change"

    # No "Current Picks" string literal survives anywhere in the file's code.
    assert not re.search(r"\"[^\"\n]*Current Picks[^\"\n]*\"", code)


def test_picks_title_names_the_filing_quarter():
    _check_picks_title(_raw(_WHALE_VIEW))


def test_picks_section_header_uses_the_quarter_title():
    _check_picks_header(_raw(_WHALE_VIEW))


def test_profile_passes_the_quarter_to_both_the_locked_card_and_the_section():
    _check_profile_wiring(_raw(_WHALE_VIEW))


def test_portfolio_as_of_travels_from_the_wire_to_the_model():
    """`portfolio_as_of` is decoded and mapped onto `WhaleProfile.portfolioAsOf`."""
    assert "portfolio_as_of" in WhaleProfileResponse.model_fields
    dtos = _strip_swift_comments(_raw(_WHALE_DTOS))
    dto = _decl(dtos, r"\bstruct\s+WhaleProfileDTO\s*:\s*Codable\s*")
    assert re.search(r"\blet\s+portfolioAsOf\s*:\s*String\?", dto)
    assert re.search(r"\bcase\s+portfolioAsOf\s*=\s*\"portfolio_as_of\"", dto)
    mapper = _decl(dto, r"\bfunc\s+toWhaleProfile\s*\(\s*\)\s*->\s*WhaleProfile\s*")
    assert re.search(r"\bportfolioAsOf\s*:\s*portfolioAsOf\b", mapper)
    model = _decl(
        _strip_swift_comments(_raw(_WHALE_MODELS)),
        r"\bstruct\s+WhaleProfile\s*:\s*Identifiable\s*,\s*Codable\s*",
    )
    assert re.search(r"\bvar\s+portfolioAsOf\s*:\s*String\?\s*=\s*nil", model)


@pytest.mark.parametrize(
    "whale, snapshot, expected",
    [
        ({"data_source": "13f"}, {"filing_period": "2026-Q3"}, "Q3 2026"),
        ({"data_source": "13f"}, {"filing_period": " 2025-Q4 "}, "Q4 2025"),
        # No snapshot read (a degraded build) → no quarter → the "Latest 13F" fallback.
        ({"data_source": "13f"}, None, None),
        ({"data_source": "13f"}, {}, None),
        ({"data_source": "13f"}, {"filing_period": None}, None),
        # Malformed periods are never rendered as a quarter.
        ({"data_source": "13f"}, {"filing_period": "2026-Q5"}, None),
        ({"data_source": "13f"}, {"filing_period": "Q3 2026"}, None),
        # A congressional snapshot's monthly period is not a 13F quarter.
        ({"data_source": "congressional"}, {"filing_period": "2026-09"}, None),
    ],
)
def test_the_backend_quarter_is_a_13f_quarter_or_nothing(whale, snapshot, expected):
    got = WhaleService._stat_disclosure(whale, snapshot)["portfolio_as_of"]
    assert got == expected


def test_a_locked_profile_keeps_the_quarter_for_the_locked_card_title():
    profile = WhaleProfileResponse(
        id="w1",
        name="Filer",
        title="",
        description="",
        behavior_summary=WhaleBehaviorSummaryResponse(
            action="Buying", primary_focus="tech", secondary_action="Trimming",
            secondary_focus="energy",
        ),
        portfolio_as_of="Q3 2026",
    )
    locked = redact_whale_profile(profile, "pro")
    assert locked.is_locked is True
    assert locked.current_holdings == []
    assert locked.portfolio_as_of == "Q3 2026"


def _function_node(tree: ast.AST, name: str) -> ast.AST:
    found = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    ]
    assert len(found) == 1, f"{name}: {len(found)} definitions"
    return found[0]


def test_the_backend_reads_the_holdings_and_the_quarter_from_one_snapshot():
    """The title's quarter and the listed holdings describe the same filing: both come from
    the one `snapshot` the profile builder reads, after its last assignment."""
    fn = _function_node(ast.parse(_raw(_WHALE_SERVICE)), "_build_whale_profile")
    assigns = [
        n.lineno for n in ast.walk(fn)
        if isinstance(n, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(t, ast.Name) and t.id == "snapshot"
            or isinstance(t, ast.Tuple) and any(
                isinstance(e, ast.Name) and e.id == "snapshot" for e in t.elts
            )
            for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
        )
    ]
    holdings_reads = [
        n.lineno for n in ast.walk(fn)
        if isinstance(n, ast.Subscript)
        and isinstance(n.value, ast.Name) and n.value.id == "snapshot"
        and isinstance(n.slice, ast.Constant) and n.slice.value == "holdings_data"
    ]
    disclosure_calls = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute) and n.func.attr == "_stat_disclosure"
    ]
    assert assigns and holdings_reads and len(disclosure_calls) == 1
    args = disclosure_calls[0].args
    assert len(args) == 2 and isinstance(args[1], ast.Name) and args[1].id == "snapshot"
    assert max(assigns) < min(holdings_reads)
    assert max(assigns) < disclosure_calls[0].lineno
    response_kwargs = {
        kw.arg: kw.value for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "WhaleProfileResponse"
        for kw in n.keywords
    }
    src = ast.unparse(response_kwargs["portfolio_as_of"])
    assert src == "disclosure['portfolio_as_of']", src


# ── 2. Technical tab: the volume change names its comparator ─────────────────

_VOLUME_DATA_DECL = r"\bstruct\s+VolumeAnalysisData\s*"
_FORMATTED_DECL = r"\bvar\s+formattedVolumeChange\s*:\s*String\s*"


def _volume_change_port(raw: str) -> Callable[[float], str]:
    """Python port of `VolumeAnalysisData.formattedVolumeChange`, read from the Swift text.
    Python's `%`-format and Swift's `String(format:)` share C printf rounding."""
    code = _strip_swift_comments(raw)
    getter = _decl(_decl(code, _VOLUME_DATA_DECL), _FORMATTED_DECL)
    assert re.search(
        r"\blet\s+sign\s*=\s*currentVolumeChange\s*>=\s*0\s*\?\s*\"\+\"\s*:\s*\"\"", getter
    ), "the sign rule changed: zero and gains print '+', losses print their own '-'"
    m = re.search(
        r"\breturn\s+\"\\\(sign\)\\\(String\(format:\s*\"(%[^\"]+)\"\s*,\s*currentVolumeChange\s*\)\)"
        r"((?:[^\"\\]|\\.)*)\"",
        getter,
    )
    assert m, "formattedVolumeChange's return shape changed"
    fmt, suffix = m.group(1), m.group(2)
    assert "\\(" not in suffix, suffix

    def port(change: float) -> str:
        sign = "+" if change >= 0 else ""
        return f"{sign}{fmt % change}{suffix}"

    return port


_VOLUME_CASES = [
    (12.0, "+12% vs prior day"),
    (12.4, "+12% vs prior day"),
    (-7.6, "-8% vs prior day"),
    (0.0, "+0% vs prior day"),
    (-100.0, "-100% vs prior day"),
    (250.0, "+250% vs prior day"),
    (12345.6, "+12346% vs prior day"),
]


def _check_volume_label(raw: str) -> None:
    port = _volume_change_port(raw)
    for change, expected in _VOLUME_CASES:
        got = port(change)
        assert got == expected, f"formattedVolumeChange({change}) = {got!r}, want {expected!r}"


def _swift_sources() -> Dict[Path, str]:
    return {p: p.read_text(encoding="utf-8") for p in sorted(_IOS_ROOT.rglob("*.swift"))}


def _check_volume_label_has_one_caller(sources: Dict[Path, str]) -> None:
    """The comparator text is right only under "Current Volume": that must stay its sole
    use, so no other surface inherits "vs prior day"."""
    uses = {}
    for path, raw in sources.items():
        n = len(re.findall(r"\bformattedVolumeChange\b", _strip_swift_comments(raw)))
        if n:
            uses[path.name] = n
    assert uses == {"TickerDetailModels.swift": 1, "TechnicalDetailSections.swift": 1}, uses

    sections = _strip_swift_comments(sources[_TECH_SECTIONS])
    volume_section = _decl(sections, r"\bstruct\s+VolumeAnalysisSection\s*:\s*View\s*")
    cards = _call_args(volume_section, r"\bVolumeCard")
    current = [c for c in cards if re.search(r"\btitle\s*:\s*\"Current Volume\"", c)]
    assert len(current) == 1, cards
    assert re.search(r"\bsubtitle\s*:\s*volumeData\.formattedVolumeChange\b", current[0]), current[0]


def test_volume_change_names_the_prior_day_comparator():
    _check_volume_label(_raw(_DETAIL_MODELS))


def test_the_volume_label_is_used_only_under_current_volume():
    _check_volume_label_has_one_caller(_swift_sources())


def _ohlcv(volumes: List[float]) -> pd.DataFrame:
    close = np.linspace(100.0, 120.0, len(volumes))
    return pd.DataFrame({
        "open": close - 0.5,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": np.array(volumes, dtype=float),
    })


def test_the_backend_change_is_against_the_prior_bar_not_the_average():
    # 38 quiet days, then 2.0M, then 2.24M: +12% on the prior bar, ~+107% on the 30-day mean.
    df = _ohlcv([1_000_000.0] * 38 + [2_000_000.0, 2_240_000.0])
    result = TechnicalAnalysisService._compute_volume_analysis(df)
    assert result.current_volume == 2_240_000.0
    assert result.current_volume_change == 12.0


def test_the_backend_change_is_signed_and_rounded_to_one_place():
    df = _ohlcv([1_000_000.0] * 38 + [3_000_000.0, 1_000_000.0])
    result = TechnicalAnalysisService._compute_volume_analysis(df)
    assert result.current_volume_change == -66.7


def test_the_volume_analysis_is_fed_the_daily_frame():
    """"prior day" holds only while the frame is one bar per day."""
    fn = _function_node(ast.parse(_raw(_TA_SERVICE)), "_build_analysis_detail")
    daily_assigns = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "df_daily" for t in n.targets)
    ]
    assert len(daily_assigns) == 1
    assert "_fetch_daily_ohlcv" in ast.unparse(daily_assigns[0].value)
    volume_kw = [
        kw for n in ast.walk(fn) if isinstance(n, ast.Call)
        for kw in n.keywords if kw.arg == "volume_analysis"
    ]
    assert len(volume_kw) == 1
    assert ast.unparse(volume_kw[0].value) == "self._compute_volume_analysis(df_daily)"


# ── mutation proof: each guard fails on a broken copy of the real source ─────


def _mutate(raw: str, old: str, new: str) -> str:
    assert old in raw, f"mutation target is stale: {old!r}"
    return raw.replace(old, new, 1)


_HEADER_LINE = 'Text(isCongressional ? "Recently Traded" : Self.picksTitle(filingQuarter: filingQuarter))'
_LOCKED_TITLE = "title: WhaleCurrentPicksSection.picksTitle(filingQuarter: profile.portfolioAsOf),"

_WHALE_MUTATIONS = [
    ("template says Current", _check_picks_title,
     'return "Picks · \\(quarter) 13F"', 'return "Current Picks · \\(quarter)"'),
    ("fallback says Current", _check_picks_title,
     'return "Picks · Latest 13F"', 'return "Current Picks"'),
    ("no trim", _check_picks_title,
     '(filingQuarter ?? "").trimmingCharacters(in: .whitespacesAndNewlines)', '(filingQuarter ?? "")'),
    ("header reverted", _check_picks_header,
     _HEADER_LINE, 'Text(isCongressional ? "Recently Traded" : "Current Picks")'),
    ("header reverted behind a comment", _check_picks_header,
     _HEADER_LINE, 'Text(isCongressional ? "Recently Traded" : "Current Picks") // ' + _HEADER_LINE),
    ("congressional header renamed", _check_picks_header,
     _HEADER_LINE, 'Text(isCongressional ? "Picks" : Self.picksTitle(filingQuarter: filingQuarter))'),
    ("locked title reverted", _check_profile_wiring,
     _LOCKED_TITLE, 'title: "Current Picks",'),
    ("quarter not passed to the section", _check_profile_wiring,
     "filingQuarter: profile.portfolioAsOf,", ""),
    ("congressional locked title renamed", _check_profile_wiring,
     'title: profile.isCongressional ? "Recently Traded" : "Recent Trades",',
     'title: profile.isCongressional ? "Picks" : "Recent Trades",'),
]

_VOLUME_MUTATIONS = [
    ("comparator dropped", '% vs prior day"', '%"'),
    ("wrong comparator", '% vs prior day"', '% vs 30d avg"'),
    ("zero loses its plus", "currentVolumeChange >= 0 ?", "currentVolumeChange > 0 ?"),
]


@pytest.mark.parametrize(
    "label, check, old, new", _WHALE_MUTATIONS, ids=[m[0] for m in _WHALE_MUTATIONS]
)
def test_each_whale_guard_fails_on_its_mutation(label, check, old, new):
    raw = _raw(_WHALE_VIEW)
    check(raw)  # green on the real source
    mutated = _mutate(raw, old, new)  # outside `raises`: a stale target must fail loudly
    with pytest.raises(AssertionError):
        check(mutated)


@pytest.mark.parametrize(
    "label, old, new", _VOLUME_MUTATIONS, ids=[m[0] for m in _VOLUME_MUTATIONS]
)
def test_each_volume_label_guard_fails_on_its_mutation(label, old, new):
    raw = _raw(_DETAIL_MODELS)
    # Mutate INSIDE the getter only: the doc comment above it quotes "+12% vs prior day",
    # and volumeChangeColor shares the `>= 0 ?` text.
    getter = _decl(_decl(raw, _VOLUME_DATA_DECL), _FORMATTED_DECL)
    _check_volume_label(raw)
    mutated = _mutate(raw, getter, _mutate(getter, old, new))
    with pytest.raises(AssertionError):
        _check_volume_label(mutated)


@pytest.mark.parametrize(
    "label, old, new",
    [
        ("a second caller",
         'subtitle: "Daily Average",', "subtitle: volumeData.formattedVolumeChange,"),
        ("moved off Current Volume",
         "subtitle: volumeData.formattedVolumeChange,", 'subtitle: "Today",'),
    ],
)
def test_the_single_caller_guard_fails_on_its_mutation(label, old, new):
    sources = _swift_sources()
    _check_volume_label_has_one_caller(sources)
    mutated = dict(sources)
    mutated[_TECH_SECTIONS] = _mutate(sources[_TECH_SECTIONS], old, new)
    with pytest.raises(AssertionError):
        _check_volume_label_has_one_caller(mutated)
