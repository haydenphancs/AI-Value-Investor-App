"""An index screen must not publish numbers it failed to compute.

Two defects, same screen, same class as the fabricated BTC 52-week low:

1. **`Open 0.00 / Day High 0.00 / Day Low 0.00` shipped as facts.** `PriceService._shape`
   emits a FIXED key set and has no `open` / `dayHigh` / `dayLow` — `/stable/quote`, which
   carried them, is a blocked path. So `_q("open")` returned its `0` default on EVERY index
   request, and the module `_fmt` renders `0.0` as `"0.00"` (it only em-dashes `None`).
   `commodity_service` has always had this right; this is the index twin.

2. **An uncomputable P/E rendered as a green "Bargain".** `ValuationLevel.from(pe:)` begins
   `case ..<18: return .bargain`, so the 0 sentinel produced a Bargain badge, a gauge marker
   pinned hard-left, and prose reading "trading at 0.0x earnings, which is considered
   Bargain — suggesting potential value". The backend already computed `val_label = "Unknown"`
   for this case; it simply never put it on the wire.
"""
from __future__ import annotations

import pytest

from app.schemas.index import ValuationSnapshotResponse
from app.services.index_service import _fmt


# ── 1. the formatter contract these rows depend on ───────────────────────────

def test_fmt_em_dashes_none_but_prints_a_measured_zero():
    """Both halves matter. `None` is unknown; a real 0 (e.g. a genuinely zero volume) is a
    measurement and must still render as a number."""
    assert _fmt(None) == "—"
    assert _fmt(0.0) == "0.00"


@pytest.mark.parametrize("key", ["open", "dayHigh", "dayLow", "yearHigh", "yearLow",
                                 "volume", "avgVolume", "previousClose"])
def test_the_display_only_stats_read_through_the_optional_helper(key):
    """Source-scan, docstring- and comment-stripped: these must use `_q_opt`, not `_q`.

    `_q` defaults to 0 and is still correct for values that feed arithmetic; `_q_opt` keeps
    "absent" distinguishable so `_fmt` can em-dash it. Stripping matters both ways here —
    the explanatory docstring names `_q("open")` as the retired pattern.
    """
    import ast
    import inspect

    from app.services.index_service import IndexService

    src = inspect.getsource(IndexService._build_index_detail)
    tree = ast.parse(inspect.cleandoc(src))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body = node.body[1:] or [ast.Pass()]
    body = ast.unparse(tree)
    assert f"_q_opt('{key}')" in body, f"{key} still defaults to 0 and will render 0.00"
    assert f"_q('{key}')" not in body


# ── 2. the companion bool ────────────────────────────────────────────────────

def test_the_schema_carries_pe_known():
    """`pe_ratio` cannot become Optional — shipped iOS builds decode it as a plain `Double`,
    so a null crashes the decode. "Unknown" therefore has to travel as a flag beside it."""
    fields = ValuationSnapshotResponse.model_fields
    assert "pe_known" in fields
    assert fields["pe_ratio"].annotation is float, (
        "pe_ratio must stay a non-Optional float for wire compatibility"
    )


def test_pe_known_defaults_true_so_an_older_payload_is_unchanged():
    v = ValuationSnapshotResponse(
        pe_ratio=22.0, forward_pe=19.0, earnings_yield=4.5,
        historical_avg_pe=21.0, historical_period="10-year", story_template="x",
    )
    assert v.pe_known is True


@pytest.mark.parametrize("pe,expected", [(0, False), (0.0, False), (None, False),
                                         (22.0, True), (0.4, True)])
@pytest.mark.asyncio
async def test_pe_known_tracks_whether_the_value_was_computed(pe, expected, monkeypatch):
    """Pinned at the BUILDER, not on a literal the test evaluates itself — the earlier
    form (`assert bool(pe and pe > 0) is expected`) never touched production code and
    stayed green with `pe_known=True` hard-coded. Note 0.4 is `True`: a small P/E is
    still a measurement, and only 0/None mean "not computed"."""
    from app.services.index_service import IndexService

    svc = IndexService.__new__(IndexService)

    async def _no_ai(*a, **k):
        return "valuation story", "sector story", "macro story", []

    monkeypatch.setattr(svc, "_generate_ai_stories", _no_ai, raising=False)
    out = await svc._build_snapshots(
        symbol="^GSPC", pe=pe, forward_pe=0.0, earnings_yield=0.0,
        historical_avg_pe=21.0, historical_period="10-yr", sector_raw=[],
        index_name="SPDR S&P 500 ETF Trust",
    )
    assert out.valuation.pe_known is expected


def test_an_unreadable_sector_row_is_dropped_not_zeroed(monkeypatch):
    """A sector row whose change is None / a non-numeric string must not become a
    0.00% entry the story template narrates as flat."""
    import asyncio
    from app.services.index_service import IndexService

    svc = IndexService.__new__(IndexService)

    async def _no_ai(*a, **k):
        return "valuation story", "sector story", "macro story", []

    monkeypatch.setattr(svc, "_generate_ai_stories", _no_ai, raising=False)
    out = asyncio.run(svc._build_snapshots(
        symbol="^GSPC", pe=20.0, forward_pe=0.0, earnings_yield=0.0,
        historical_avg_pe=21.0, historical_period="10-yr",
        sector_raw=[{"sector": "Energy", "changesPercentage": None},
                    {"sector": "Utilities", "changesPercentage": "n/a"},
                    {"sector": "Tech", "changesPercentage": "1.5%"},
                    {"sector": "Health", "changesPercentage": -0.4}],
        index_name="SPDR S&P 500 ETF Trust",
    ))
    got = {s.sector: s.change_percent for s in out.sector_performance.sectors}
    assert got == {"Tech": 1.5, "Health": -0.4}


# ── 3. the story must not characterise an unknown P/E ────────────────────────

def _story(pe, forward_pe=0.0, historical_avg_pe=21.0):
    from app.services.index_service import IndexService

    svc = IndexService.__new__(IndexService)
    valuation, _sector, _macro, _ind = svc._build_story_templates(
        pe=pe, forward_pe=forward_pe, historical_avg_pe=historical_avg_pe,
        sectors=[], macro_cached=None,
    )
    return valuation


def test_an_unknown_pe_produces_no_valuation_claim():
    story = _story(0)
    assert "{VALUATION_LABEL}" not in story
    assert "{PE_RATIO}" not in story
    for banned in ("Bargain", "potential value", "premium"):
        assert banned not in story, f"the story still characterises an uncomputed P/E: {story!r}"
    assert "isn't available" in story


def test_a_real_pe_still_produces_the_full_story():
    """The refusal must not disarm the feature it protects."""
    story = _story(28.5, forward_pe=22.1)
    assert "{PE_RATIO}" in story and "{VALUATION_LABEL}" in story


def test_the_deterministic_story_still_returns_all_four_parts():
    """`_build_story_templates` feeds a 4-tuple unpack at its call site; an early return
    with the wrong arity would be a runtime TypeError on every index request."""
    from app.services.index_service import IndexService

    svc = IndexService.__new__(IndexService)
    out = svc._build_story_templates(
        pe=0, forward_pe=0, historical_avg_pe=21.0, sectors=[], macro_cached=None,
    )
    assert len(out) == 4


# ── 4. the iOS half — a wire flag nothing reads is worthless ─────────────────
#
# `money_flow_index_known` taught this: the backend flag was correct and the screen still
# fabricated, because the DTO, the display model and the view all had to change too.

def _swift(path: str) -> str:
    """File contents with `//` comments stripped.

    Every fix below is annotated with prose naming the retired pattern, so an unstripped
    scan passes on the explanation after a revert — and a NEGATIVE assertion fails on it
    even when the code is correct. This suite has tripped over its own commentary before.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
    text = (root / path).read_text(encoding="utf-8")
    out = []
    for line in text.splitlines():
        # not a real Swift parser, but these files have no `//` inside string literals
        # on the lines we assert about.
        out.append(re.sub(r"//.*$", "", line))
    return "\n".join(out)


def test_the_dto_decodes_pe_known():
    src = _swift("Models/IndexDetailResponseModels.swift")
    assert "let peKnown: Bool?" in src, "the flag never reaches the client"
    assert 'case peKnown = "pe_known"' in src
    assert "peKnown: snapshotsData.valuation.peKnown ?? true" in src, (
        "an older backend must decode as 'assume known', not crash or default false"
    )


def _swift_computed_body(src: str, decl: str) -> str:
    """The BRACE-BOUNDED body of one computed property.

    ⚠️ Not optional rigour: `level` and `gaugePosition` carry the IDENTICAL guard line, so a
    whole-file `in src` assertion passes when the guard is deleted from one of them. That is
    exactly how a mutation removing it from `level` — the property that produces the
    "Bargain" badge — stayed green.
    """
    i = src.index(decl)
    i = src.index("{", i)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError(f"unbalanced braces after {decl!r}")


def test_the_level_is_optional_and_gated():
    src = _swift("Models/IndexDetailModels.swift")
    assert "var level: ValuationLevel?" in src, (
        "level is still non-Optional — a 0 P/E resolves to .bargain"
    )
    assert "var gaugePosition: Double?" in src

    # Each property must carry its OWN guard — see `_swift_computed_body`.
    for decl in ("var level: ValuationLevel?", "var gaugePosition: Double?"):
        body = _swift_computed_body(src, decl)
        assert "guard peKnown" in body, f"{decl} no longer gates on peKnown"
        assert "return nil" in body, decl


def test_the_view_omits_the_badge_and_gauge_when_unknown():
    src = _swift("Views/Organisms/IndexDetailSnapshotsSection.swift")
    assert "if let level = valuation.level {" in src, "the badge is still unconditional"
    assert "if let gauge = valuation.gaugePosition {" in src, "the tier bar is still unconditional"
    # and the metric pills must not print "0.0x"
    assert "valuation.peDisplay" in src
    assert 'String(format: "%.1fx", valuation.peRatio)' not in src


def test_no_swift_file_force_reads_the_level_off_the_sentinel():
    """`.level.rawValue` on the non-Optional would not compile now, but `.level!` would."""
    for path in ("Models/IndexDetailModels.swift",
                 "Views/Organisms/IndexDetailSnapshotsSection.swift",
                 "ViewModels/IndexDetailViewModel.swift"):
        src = _swift(path)
        assert "valuation.level!" not in src, path
        assert ".level!.rawValue" not in src, path
