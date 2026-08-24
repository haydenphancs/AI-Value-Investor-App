"""Backend↔iOS parity for the Performance card's benchmark block, plus the one rule the
iOS table's layout depends on.

THE RULE. `BenchmarkComparisonTable` prints ONE window per row, spanning both columns:

    5-year                8.7%     11.4%    −2.7
      since Aug 2021

That is only true because every service measures both sides over the window they share
and reports its start. If a service starts measuring its two sides differently, the table
is not "slightly imprecise" — it publishes a false claim about a number it did not
compute. That is exactly what shipped on the ETF screen, where the ETF's CAGR ran from
the ETF's first date and the S&P's from the S&P's, with the mismatch hidden because
`benchmark_since_date` was sent as `None`.

THE DECODER. Stock, ETF, commodity AND crypto all decode ONE Swift struct,
`BenchmarkSummaryDTO` in `CryptoAPIModels.swift`. So a field added to any of the three
Pydantic copies has to reach that single struct, and the usual nullability predicate
applies (see `test_ios_response_schema_parity.py`): an `Optional[...]` backend field
mapped to a non-`Optional` Swift property is a decode crash on the whole detail screen.

These are SOURCE SCANS. Per `.claude/rules/testing.md` §3 they go vacuous easily, so each
one is brace-bounded, comment-stripped, and backed by an explicit existence assertion —
a `pytest.skip` on a moved file is how a guard silently turns green.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.services.benchmark_math import overlapping_cagrs
from app.services.commodity_service import CommodityService
from app.services.crypto_service import CryptoService  # noqa: F401  (import-time guard)
from app.services.etf_service import ETFService
from app.services.stock_overview_service import StockOverviewService

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_DTO_FILE = _IOS / "Models" / "CryptoAPIModels.swift"
_TABLE_FILE = _IOS / "Views" / "Molecules" / "BenchmarkComparisonTable.swift"
_SECTION_FILE = _IOS / "Views" / "Organisms" / "TickerDetailPerformanceSection.swift"

_SCHEMA_MODULES = ("app.schemas.etf", "app.schemas.crypto", "app.schemas.commodity")


# ── source helpers ───────────────────────────────────────────────────────────

def _code(path: Path) -> str:
    """File contents with `//` comments and block comments stripped.

    Without this every scan below passes on the PROSE next to a fix: the explanatory
    comment reliably contains every token the test greps for, so the guard stays green
    after the code is reverted.
    """
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(line.split("//")[0] for line in src.splitlines())


def _swift_struct(src: str, name: str) -> str:
    """The body of `struct <name>`, brace-matched.

    Brace-bounding matters: asserting against a whole FILE passes when the token lives in
    a different type in the same file, which is how a fix to a preview-only duplicate once
    looked like a fix to the live screen.
    """
    m = re.search(rf"struct\s+{re.escape(name)}\b[^{{]*\{{", src)
    assert m, f"struct {name} not found — update this test, do not delete the assertion"
    depth, start = 0, m.end() - 1
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces in struct {name}")


# ── anti-vacuity: the things every other test here reads must exist ──────────

def test_the_files_and_builders_these_scans_depend_on_exist():
    for path in (_DTO_FILE, _TABLE_FILE, _SECTION_FILE):
        assert path.exists(), f"{path} moved — repoint this test, never let it skip"
    for svc in (StockOverviewService, ETFService, CommodityService):
        assert hasattr(svc, "_build_benchmark_summary") or hasattr(svc, "_derive_from_history")


# ── schema shape ─────────────────────────────────────────────────────────────

def _model(module: str):
    import importlib
    return getattr(importlib.import_module(module), "BenchmarkSummaryResponse")


@pytest.mark.parametrize("module", _SCHEMA_MODULES)
def test_every_benchmark_schema_names_its_window_and_can_declare_absence(module):
    fields = _model(module).model_fields
    assert "window_label" in fields
    assert "benchmark_available" in fields


@pytest.mark.parametrize("module", _SCHEMA_MODULES)
def test_benchmark_available_is_a_defaulted_non_nullable_bool(module):
    """Deliberately NOT `Optional[float]` on `sp_benchmark`.

    The shipped iOS build decodes `sp_benchmark` as a non-optional `Double`, and the
    backend deploys before the client, so a null there would crash the Performance screen
    on every already-installed copy. A defaulted non-nullable bool is safe in both
    directions: an old client ignores the key, a new client reads it.
    """
    field = _model(module).model_fields["benchmark_available"]
    assert field.annotation is bool, "must not be Optional — a null is not a valid answer"
    assert field.default is True, "absent must mean 'available', matching the old behaviour"
    assert _model(module).model_fields["sp_benchmark"].annotation is float


@pytest.mark.parametrize("module", _SCHEMA_MODULES)
def test_benchmark_since_date_is_gone_from_the_wire(module):
    """It was identical to `since_date` (stock) or nil (everything else) at every call
    site. Rendering it is what put two identical "Since Aug 2021" labels on the card."""
    assert "benchmark_since_date" not in _model(module).model_fields


# ── the shared-window invariant, per service ─────────────────────────────────

def _series(start: date, end: date, n: int, first: float, last: float):
    span = (end - start).days
    ratio = (last / first) ** (1 / (n - 1))
    return [
        {"date": (start + timedelta(days=round(span * i / (n - 1)))).isoformat(),
         "close": first * (ratio ** i)}
        for i in range(n)
    ]


def test_etf_publishes_one_window_that_describes_both_columns():
    etf = _series(date(2014, 10, 31), date(2026, 8, 21), 2968, 20.38, 86.21)
    spy = _series(date(2006, 10, 5), date(2026, 8, 21), 5000, 135.18, 765.72)

    out = ETFService.__new__(ETFService)._build_benchmark_summary(etf, spy, symbol="ARKK")

    _, _, shared = overlapping_cagrs(etf, spy)
    assert shared == "2014-10-31"
    assert out.since_date is not None and "2014" in out.since_date, (
        "the published date must be the SHARED window's start, not one column's"
    )
    assert out.window_label in {"5-year", "All-time"}


def test_stock_publishes_one_window_that_describes_both_columns():
    today = date.today()
    stock = _series(today - timedelta(days=365 * 12), today, 3000, 50.0, 300.0)
    spy = _series(today - timedelta(days=365 * 12), today, 3000, 300.0, 500.0)

    out = StockOverviewService.__new__(StockOverviewService)._build_benchmark_summary(
        stock, spy, ticker="TEST"
    )
    assert out.window_label in {"5-year", "All-time"}
    assert out.since_date
    # The secondary row, when present, must cover a DIFFERENT window from the primary.
    if out.alltime_since_date is not None:
        assert out.alltime_since_date != out.since_date


def test_commodity_publishes_one_window_that_describes_both_columns():
    gold = _series(date(2007, 5, 29), date(2026, 8, 21), 4800, 657.2, 4680.6)
    spy = _series(date(2006, 10, 5), date(2026, 8, 21), 5000, 135.18, 765.72)

    derived = CommodityService.__new__(CommodityService)._derive_from_history(gold, spy)
    assert derived["bench_since"] == derived["bench_base"]["date"], (
        "the commodity's baseline close must be taken AT the shared window's start"
    )


@pytest.mark.parametrize("service_file", [
    "commodity_service.py", "etf_service.py", "stock_overview_service.py", "crypto_service.py",
])
def test_no_service_passes_a_literal_as_the_benchmark_return(service_file):
    """`sp_benchmark=10.5` shipped on the commodity screen: a literal compared against
    whatever window the commodity had, under a label naming that window — with a verdict
    badge computed from it.

    Parsed with `ast`, not grepped. A textual scan matches the PROSE explaining the fix —
    this very docstring names the constant — so the first draft of this test failed on
    its own comment. Stripping `#` comments is not enough; docstrings are code.
    """
    import ast

    src = (Path(__file__).resolve().parents[1] / "app" / "services" / service_file).read_text()
    offenders = [
        node.lineno
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "sp_benchmark" and isinstance(kw.value, ast.Constant)
    ]
    assert not offenders, (
        f"{service_file} passes a literal benchmark return at line(s) {offenders} — "
        "it must be measured over the asset's own window"
    )


# ── Swift decoder parity ─────────────────────────────────────────────────────

def test_the_swift_dto_decodes_every_new_backend_field():
    dto = _swift_struct(_code(_DTO_FILE), "BenchmarkSummaryDTO")
    for swift_name, wire_name in (
        ("windowLabel", "window_label"),
        ("benchmarkAvailable", "benchmark_available"),
    ):
        assert f"let {swift_name}" in dto, f"{swift_name} missing from BenchmarkSummaryDTO"
        assert f'case {swift_name} = "{wire_name}"' in dto, f"no CodingKey for {wire_name}"


def test_optional_backend_fields_are_optional_in_swift():
    """The predicate that matters: `Optional[...]` backend -> non-`Optional` Swift is a
    single null away from crashing the decode of the WHOLE detail response."""
    dto = _swift_struct(_code(_DTO_FILE), "BenchmarkSummaryDTO")
    nullable = {
        name for name, f in _model("app.schemas.etf").model_fields.items()
        if type(None) in getattr(f.annotation, "__args__", ())
    }
    swift_types = dict(re.findall(r"let\s+(\w+)\s*:\s*([\w?]+)", dto))
    camel = {
        "benchmark_name": "benchmarkName", "since_date": "sinceDate",
        "badge_threshold": "badgeThreshold", "window_label": "windowLabel",
        "alltime_annual_return": "alltimeAnnualReturn",
        "alltime_benchmark": "alltimeBenchmark", "alltime_since_date": "alltimeSinceDate",
    }
    assert nullable, "no nullable fields found — the reflection broke, not the schema"
    for wire in nullable:
        swift = camel.get(wire)
        if swift is None or swift not in swift_types:
            continue
        assert swift_types[swift].endswith("?"), (
            f"{wire} is Optional on the backend but {swift} is non-Optional in Swift"
        )


def test_the_swift_dto_no_longer_carries_the_duplicate_since_date():
    dto = _swift_struct(_code(_DTO_FILE), "BenchmarkSummaryDTO")
    assert "benchmarkSinceDate" not in dto


def test_the_summary_model_exposes_the_window_the_table_prints():
    section = _code(_SECTION_FILE)
    summary = _swift_struct(section, "PerformanceBenchmarkSummary")
    assert "let windowLabel" in summary
    assert "let benchmarkAvailable" in summary
    assert "benchmarkSinceDate" not in summary
    # The rows the table draws are built HERE, so the "same window" rule has one home.
    assert "var rows: [BenchmarkComparisonRow]" in summary


def test_an_unmeasurable_benchmark_is_never_rendered_as_a_number():
    """`sp_benchmark` still carries a placeholder 0.0 on the wire. If the row builder
    stops gating on `benchmarkAvailable`, that placeholder renders as "S&P 500 0.0%" —
    with a verdict badge beside it — exactly as it did before the flag existed."""
    summary = _swift_struct(_code(_SECTION_FILE), "PerformanceBenchmarkSummary")
    assert "benchmarkValue: benchmarkAvailable ? spBenchmark : nil" in summary
    assert "benchmarkAvailable &&" in summary, "the verdict badge must be gated too"


def test_the_verdict_badge_names_the_window_it_describes():
    """Ungated, the badge reads "Underperforming" directly above an all-time row where the
    asset is AHEAD — 5-year 8.7 vs 11.4 over all-time 10.0 vs 9.1, which is the
    contradiction in the reported screenshot."""
    summary = _swift_struct(_code(_SECTION_FILE), "PerformanceBenchmarkSummary")
    m = re.search(r"var badgeLabel: String \{(.+?)\n    \}", summary, flags=re.S)
    assert m, "badgeLabel not found"
    assert "windowLabel" in m.group(1)


def test_the_table_uses_data_tier_fonts_and_a_real_divider_token():
    """Two of the four reasons the block was hard to read, pinned.

    `bodySmallEmphasis` is a READING token with proportional digits, so 8.7 and 11.4 do
    not line up down a column. `cardBackgroundLight` is a SURFACE token (#EDF0F5 on a
    #FFFFFF card), which is why "add lines?" was asked about a line that already existed.
    """
    table = _code(_TABLE_FILE)
    assert "AppTypography.dataMedium" in table
    assert "AppColors.divider" in table
    assert "AppColors.cardBackgroundLight" not in table
    # `Divider()` paints UIColor.separator over whatever colour it is given (theme-lint #3).
    assert "Divider()" not in table
    # Chart-only graphic tokens must not escape into text (ios-swiftui.md role table).
    assert "Graphic" not in table


def test_the_section_no_longer_separates_with_a_surface_token():
    section = _code(_SECTION_FILE)
    assert "AppColors.cardBackgroundLight" not in section
