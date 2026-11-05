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
        return "sector story", "macro story", []

    monkeypatch.setattr(svc, "_generate_ai_stories", _no_ai, raising=False)
    out = await svc._build_snapshots(
        symbol="^GSPC", pe=pe, forward_pe=0.0, earnings_yield=0.0,
        sector_raw=[],
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
        return "sector story", "macro story", []

    monkeypatch.setattr(svc, "_generate_ai_stories", _no_ai, raising=False)
    out = asyncio.run(svc._build_snapshots(
        symbol="^GSPC", pe=20.0, forward_pe=0.0, earnings_yield=0.0,
        sector_raw=[{"sector": "Energy", "changesPercentage": None},
                    {"sector": "Utilities", "changesPercentage": "n/a"},
                    {"sector": "Tech", "changesPercentage": "1.5%"},
                    {"sector": "Health", "changesPercentage": -0.4}],
        index_name="SPDR S&P 500 ETF Trust",
    ))
    got = {s.sector: s.change_percent for s in out.sector_performance.sectors}
    assert got == {"Tech": 1.5, "Health": -0.4}


# ── 3. the story must not characterise an unknown P/E ────────────────────────

def _story(pe, history=None):
    """The valuation sentence is code-owned since 2026-10-21 (`_valuation_story`)."""
    from app.services.index_service import _valuation_story

    return _valuation_story(pe=pe, pe_sectors=11, pe_history=history)


@pytest.mark.parametrize("history", [None, {"avg": 22.1}])
def test_an_unknown_pe_produces_no_valuation_claim(history):
    story = _story(0, history)
    assert "{VALUATION_LABEL}" not in story
    assert "{PE_RATIO}" not in story
    assert "{HISTORICAL_AVG_PE}" not in story
    for banned in ("Bargain", "potential value", "premium", "above", "below", "in line"):
        assert banned not in story, f"the story still characterises an uncomputed P/E: {story!r}"
    assert "isn't available" in story


def test_a_real_pe_still_produces_the_full_story():
    """The refusal must not disarm the feature it protects."""
    story = _story(28.5, {"avg": 22.1})
    assert "{PE_RATIO}" in story and "{HISTORICAL_AVG_PE}" in story


def test_the_deterministic_story_still_returns_all_three_parts():
    """`_build_story_templates` feeds a 3-tuple unpack at its call site (sector, macro,
    indicators); an early return with the wrong arity would be a runtime TypeError on every
    index request."""
    from app.services.index_service import IndexService

    svc = IndexService.__new__(IndexService)
    out = svc._build_story_templates(sectors=[], macro_cached=None)
    assert len(out) == 3


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


# ── "Updated on <date> ET": the snapshot is dated on New York's calendar ─────────────
#
# iOS prints `generated_date` as "Updated on <date> ET" (IndexDetailSnapshotsSection). It
# was the UTC calendar day, which turns at 8 PM ET (7 PM while New York is on EST) — a
# snapshot built at 9 PM ET on Oct 29 said Oct 30.

from datetime import datetime, timedelta, timezone  # noqa: E402

_UTC = timezone.utc


@pytest.mark.parametrize("now,expected", [
    # The pinned boundary: 21:00 ET on Oct 29 (EDT) is 01:00 UTC on Oct 30.
    (datetime(2026, 10, 30, 1, 0, tzinfo=_UTC), "2026-10-29"),
    # 8 PM ET exactly is midnight UTC; one minute before it is still the same day in both.
    (datetime(2026, 10, 30, 0, 0, tzinfo=_UTC), "2026-10-29"),
    (datetime(2026, 10, 29, 23, 59, tzinfo=_UTC), "2026-10-29"),
    # Midnight ET (04:00 UTC in EDT) is the next New York day.
    (datetime(2026, 10, 30, 4, 0, tzinfo=_UTC), "2026-10-30"),
    (datetime(2026, 10, 30, 3, 59, tzinfo=_UTC), "2026-10-29"),
    # The EST side (from 2026-11-01): the UTC day turns at 7 PM ET, New York's at 05:00 UTC.
    (datetime(2026, 11, 3, 0, 30, tzinfo=_UTC), "2026-11-02"),
    (datetime(2026, 11, 3, 4, 59, tzinfo=_UTC), "2026-11-02"),
    (datetime(2026, 11, 3, 5, 0, tzinfo=_UTC), "2026-11-03"),
    # The year end: still Dec 31 in New York while UTC is already in the new year.
    (datetime(2027, 1, 1, 2, 0, tzinfo=_UTC), "2026-12-31"),
    # Another zone's aware instant (Tokyo) is converted, never read off its own calendar.
    (datetime(2026, 10, 30, 10, 0, tzinfo=timezone(timedelta(hours=9))), "2026-10-29"),
    # A naive value is UTC.
    (datetime(2026, 10, 30, 1, 0), "2026-10-29"),
])
def test_the_snapshot_date_is_the_new_york_calendar_day(now, expected):
    from app.services.index_service import _snapshot_generated_date

    assert _snapshot_generated_date(now) == expected


def _freeze_index_clock(monkeypatch, instant):
    """`index_service`'s `datetime.now` frozen at the aware `instant`. Production passes no
    clock, and the UTC and New York days differ only from 20:00 ET (19:00 in EST) to midnight,
    so a live clock checked the None branch only when the suite happened to run then."""
    from app.services import index_service

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz is not None else instant.astimezone(_UTC).replace(tzinfo=None)

    monkeypatch.setattr(index_service, "datetime", _Clock)


@pytest.mark.parametrize("instant,expected", [
    # 9 PM EDT on Oct 29: UTC already says Oct 30.
    (datetime(2026, 10, 30, 1, 0, tzinfo=_UTC), "2026-10-29"),
    # 9 PM EST on New Year's Eve: UTC is already in 2027.
    (datetime(2027, 1, 1, 2, 0, tzinfo=_UTC), "2026-12-31"),
    # 04:30 UTC: a fixed −5 h would still say Oct 29 in EDT, a fixed −4 h already Jan 1 in EST.
    (datetime(2026, 10, 30, 4, 30, tzinfo=_UTC), "2026-10-30"),
    (datetime(2027, 1, 1, 4, 30, tzinfo=_UTC), "2026-12-31"),
])
def test_the_snapshot_date_defaults_to_now_in_new_york(monkeypatch, instant, expected):
    from app.services.index_service import _snapshot_generated_date

    _freeze_index_clock(monkeypatch, instant)
    assert _snapshot_generated_date() == expected


@pytest.mark.asyncio
async def test_the_builder_dates_the_snapshot_at_the_et_boundary(monkeypatch):
    """Through the real `_build_snapshots`: 21:00 ET on Oct 29 is Oct 30 in UTC and must
    stamp Oct 29. Mutation: the builder back on `datetime.now(tz=timezone.utc)`."""
    from app.services.index_service import IndexService

    svc = IndexService.__new__(IndexService)

    async def _no_ai(*a, **k):
        return "sector story", "macro story", []

    monkeypatch.setattr(svc, "_generate_ai_stories", _no_ai)
    # The second instant (New Year's Eve in New York, already 2027 in UTC) is far from any
    # run date, so a builder that ignored `now` and read the wall clock could not pass by
    # coincidence on the day this was written.
    for now, expected in ((datetime(2026, 10, 30, 1, 0, tzinfo=_UTC), "2026-10-29"),
                          (datetime(2027, 1, 1, 2, 0, tzinfo=_UTC), "2026-12-31")):
        out = await svc._build_snapshots(
            symbol="^GSPC", pe=20.0, forward_pe=0.0, earnings_yield=0.0, sector_raw=[],
            index_name="SPDR S&P 500 ETF Trust", now=now,
        )
        assert out.generated_date == expected, now
    # The production call passes no clock: New York's day, on a frozen clock rather than
    # whenever the suite runs. 9 PM EDT tells it from the UTC day; 04:30 UTC tells it from a
    # FIXED offset (−5 h in EDT is still Oct 29; −4 h in EST is already Jan 1).
    for instant, expected in ((datetime(2026, 10, 30, 1, 0, tzinfo=_UTC), "2026-10-29"),
                              (datetime(2026, 10, 30, 4, 30, tzinfo=_UTC), "2026-10-30"),
                              (datetime(2027, 1, 1, 4, 30, tzinfo=_UTC), "2026-12-31")):
        _freeze_index_clock(monkeypatch, instant)
        live = await svc._build_snapshots(
            symbol="^GSPC", pe=20.0, forward_pe=0.0, earnings_yield=0.0, sector_raw=[],
            index_name="SPDR S&P 500 ETF Trust",
        )
        assert live.generated_date == expected, instant
