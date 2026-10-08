"""iOS half of the 2026-10-07 peer-benchmark rework (ships in 1.1).

Since 2026-10-07 every peer median the backend serves is the INDUSTRY's when that group
has >= 20 companies for the period, else the SAME period's SECTOR median
(`sector_benchmark_lookup.merge_peer_cells`), and the response says which:

* `SnapshotMetricResponse.peer_level` — the Overview snapshot / Valuation Meter rows. The
  metric NAME keeps the literal words "sector avg" / "vs sector" on the wire (shipped builds
  strip `\\s*\\([^)]*sector[^)]*\\)` from report labels), so a 1.1 build renames them to
  "industry avg" / "vs industry" at DISPLAY time when `peer_level == "industry"`.
* `HealthCheckMetricSchema.peer_level` — the Health Check "vs X" label names its group.
* `ProfitPowerResponse.peer_group_levels` — per period ("annual" / "quarterly": the NET-margin
  line's group since 2026-10-08), so the legend follows the tab on screen; the per-margin
  "<period>.<metric>" keys feed the report drill-down (tests/test_pp_levels_round4_*.py).
* `SnapshotItemResponse.computed_at` — the Snapshots header said "Updated on <TODAY>"
  whatever the cache age; it now shows the OLDEST build time, or no date at all.
* `degraded` containing "benchmarks" (the peer lookup raised) — the card says so in one
  muted line instead of looking like a company with no peers, but only over a tab that
  draws no peer line (the flag covers EITHER period's failed read; 2026-10-08).
* Cay AI's peer net-margin line was labelled with the company's fiscal year ("Industry Avg
  Net Margin (FY2026)"); it now mirrors the backend's own grounding line exactly.

There is no XCTest target, so these read the Swift source (testing.md §3): comments are
stripped before every assertion, every check is brace-bound to the ONE declaration it is
about, and the string logic (the industry substitution, the comparison prefix, the legend
word, the report-label strip) is PORTED from the Swift text and run against a case table,
so the table fails when the Swift changes, not only when this copy does.

Every guard was mutation-tested once by hand (a mutated COPY of the Swift file, pointed at
by the module's path constant): each failed on its mutation and passed on the real source.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.schemas.health_check import HealthCheckMetricSchema
from app.schemas.profit_power import ProfitPowerDataPointSchema, ProfitPowerResponse
from app.schemas.stock_overview import (
    SnapshotItemResponse,
    SnapshotMetricResponse,
    snapshot_build_time,
)

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"
_SERVICES = _REPO / "backend" / "app" / "services"

_OVERVIEW_DTO = _IOS / "Models" / "StockOverviewResponseModels.swift"
_DETAIL_MODELS = _IOS / "Models" / "TickerDetailModels.swift"
_REPOSITORY = _IOS / "Core" / "Repositories" / "StockRepository.swift"
_PP_MODELS = _IOS / "Models" / "ProfitPowerModels.swift"
_HC_MODELS = _IOS / "Models" / "HealthCheckModels.swift"
_REPORT_MODELS = _IOS / "Models" / "TickerReportModels.swift"
_SNAPSHOT_CARD = _IOS / "Views" / "Molecules" / "SnapshotCard.swift"
_NOTE = _IOS / "Views" / "Molecules" / "PeerComparisonUnavailableNote.swift"
_VALUATION_SECTION = _IOS / "Views" / "Organisms" / "ValuationMeterSection.swift"
_SNAPSHOTS_SECTION = _IOS / "Views" / "Organisms" / "TickerDetailSnapshotsSection.swift"
_PP_CARD = _IOS / "Views" / "Organisms" / "ProfitPowerSectionCard.swift"
_GROWTH_CARD = _IOS / "Views" / "Organisms" / "GrowthSectionCard.swift"
_HC_CARD = _IOS / "Views" / "Organisms" / "HealthCheckSectionCard.swift"
_FINANCIALS = _IOS / "Views" / "Organisms" / "TickerFinancialsContent.swift"
_DETAIL_VIEW = _IOS / "Views" / "Screens" / "TickerDetailView.swift"
_VM = _IOS / "ViewModels" / "TickerDetailViewModel.swift"
_CHAT_SERVICE = _SERVICES / "chat_service.py"


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


def _code(path: Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved; update this guard, do not delete it"
    return _strip_swift_comments(path.read_text(encoding="utf-8"))


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


def _if_block(body: str, condition_regex: str) -> str:
    m = re.search(rf"\bif\s+{condition_regex}\s*\{{", body)
    assert m, f"no `if {condition_regex}` block"
    return _block_from(body, m.end() - 1)


def _coding_keys(struct_body: str) -> dict:
    """{swift property: wire key} from the struct's `enum CodingKeys` — `case a, b` maps a
    name to itself, `case a = "x"` maps it to "x"."""
    keys = _decl(struct_body, r"\benum\s+CodingKeys\s*:\s*String\s*,\s*CodingKey\s*")
    mapping = {}
    for stmt in re.findall(r"\bcase\s+([^\n]+)", keys):
        for part in stmt.split(","):
            part = part.strip()
            if not part:
                continue
            m = re.fullmatch(r'(\w+)\s*=\s*"([^"]+)"', part)
            if m:
                mapping[m.group(1)] = m.group(2)
            elif re.fullmatch(r"\w+", part):
                mapping[part] = part
    return mapping


def test_the_comment_stripper_ignores_prose_and_keeps_strings():
    sample = (
        "// Text(metric.displayName)\n"
        "/* if peerComparisonUnavailable { PeerComparisonUnavailableNote() } */\n"
        'let a = "https://x.test" // trailing Date()\n'
        'let b = "\\(comparisonPrefix) \\(String(format: "%.2f", c))" // vs sector\n'
    )
    out = _strip_swift_comments(sample)
    assert "displayName" not in out and "PeerComparisonUnavailableNote" not in out
    assert '"https://x.test"' in out and "Date()" not in out
    assert "comparisonPrefix" in out and "vs sector" not in out


def test_the_brace_bound_scan_does_not_leak_into_the_next_declaration():
    src = "struct A { var x: Int { 1 } }\nstruct B { let y = Date() }"
    assert _decl(src, r"\bstruct\s+A\b") == "{ var x: Int { 1 } }"
    assert "Date()" not in _decl(src, r"\bstruct\s+A\b")


def test_the_coding_keys_parser_reads_both_forms():
    body = _decl(_code(_REPOSITORY), r"\bstruct\s+ProfitPowerResponseDTO\s*:")
    keys = _coding_keys(body)
    assert keys["symbol"] == "symbol" and keys["annual"] == "annual"
    assert keys["peerGroupLevel"] == "peer_group_level"


# ── (1) the DTOs decode every new field as Optional, under the backend's key ─────────

# (Pydantic model, wire key, Swift file, Swift DTO, Swift property, Swift wrapped type)
_NEW_FIELDS = [
    (SnapshotMetricResponse, "peer_level", _OVERVIEW_DTO, "SnapshotMetricDTO", "peerLevel", "String"),
    (SnapshotItemResponse, "computed_at", _OVERVIEW_DTO, "SnapshotItemDTO", "computedAt", "String"),
    (HealthCheckMetricSchema, "peer_level", _REPOSITORY, "HealthCheckMetricDTO", "peerLevel", "String"),
    (ProfitPowerResponse, "peer_group_levels", _REPOSITORY, "ProfitPowerResponseDTO",
     "peerGroupLevels", "[String: String]"),
]


def _dto(path: Path, name: str) -> str:
    return _decl(_code(path), rf"\bstruct\s+{re.escape(name)}\s*:")


@pytest.mark.parametrize("model,key,path,struct,prop,swift_type", _NEW_FIELDS)
def test_the_backend_field_exists_and_is_additive(model, key, path, struct, prop, swift_type):
    field = model.model_fields.get(key)
    assert field is not None, f"{model.__name__}.{key} is gone — iOS 1.1 decodes it"
    assert not field.is_required(), f"{model.__name__}.{key} must have a default (additive)"


@pytest.mark.parametrize("model,key,path,struct,prop,swift_type", _NEW_FIELDS)
def test_the_swift_dto_decodes_the_key_as_optional(model, key, path, struct, prop, swift_type):
    body = _dto(path, struct)
    decl = re.search(rf"\blet\s+{prop}\s*:\s*([^\n=]+?)\s*$", body, re.MULTILINE)
    assert decl, f"{struct} does not declare `{prop}`"
    assert decl.group(1).replace(" ", "") == swift_type.replace(" ", "") + "?", (
        f"{struct}.{prop} is `{decl.group(1)}` — it must be `{swift_type}?`, or a payload "
        "without the key (an older backend, a cached DTO, a stored report) fails to decode"
    )
    assert _coding_keys(body).get(prop) == key, f"{struct}.CodingKeys does not map `{prop}` to {key!r}"


@pytest.mark.parametrize("model,struct,path", [
    (SnapshotMetricResponse, "SnapshotMetricDTO", _OVERVIEW_DTO),
    (SnapshotItemResponse, "SnapshotItemDTO", _OVERVIEW_DTO),
    (HealthCheckMetricSchema, "HealthCheckMetricDTO", _REPOSITORY),
    (ProfitPowerResponse, "ProfitPowerResponseDTO", _REPOSITORY),
])
def test_every_key_the_dto_decodes_is_one_the_backend_serves(model, struct, path):
    """The explicit CodingKeys `SnapshotMetricDTO` gained must still name `name` / `value`
    — a typo there fails every Overview decode."""
    keys = _coding_keys(_dto(path, struct))
    assert keys, f"{struct} has no CodingKeys"
    unknown = set(keys.values()) - set(model.model_fields)
    assert not unknown, f"{struct} decodes keys {model.__name__} never serves: {unknown}"


def test_the_overview_mapper_carries_the_level_and_the_build_time():
    mapper = _decl(_code(_OVERVIEW_DTO), r"\bfunc\s+toDisplayModel\s*\(\s*\)\s*->\s*TickerDetailData")
    flat = re.sub(r"\s+", " ", mapper)
    # The wire name is passed through untouched — the substitution is display-time only.
    assert "SnapshotMetric(name: $0.name, value: $0.value, peerLevel: $0.peerLevel)" in flat
    assert "let computedAt = SnapshotItem.parseComputedAt(dto.computedAt)" in flat
    assert re.search(r"computedAt:\s*computedAt\s*\)", flat), "the item drops its build time"
    # An unreadable stamp is logged, never silently turned into "now".
    assert "computed_at unreadable" in flat
    assert "Date()" not in mapper.split("let snapshotItems")[1].split("let sectorInfo")[0]


def test_the_health_check_mapper_carries_the_level():
    dto = _dto(_REPOSITORY, "HealthCheckResponseDTO")
    mapper = _decl(dto, r"\bfunc\s+toDisplayModel\s*\(\s*\)\s*->\s*HealthCheckSectionData")
    assert re.search(r"return\s+HealthCheckMetric\((?:(?!\)\s*\n\s*\}).)*peerLevel:\s*dto\.peerLevel",
                     mapper, re.S)


def test_the_profit_power_mapper_carries_the_per_period_levels():
    dto = _dto(_REPOSITORY, "ProfitPowerResponseDTO")
    mapper = _decl(dto, r"\bfunc\s+toDisplayModel\s*\(\s*\)\s*->\s*ProfitPowerSectionData")
    assert re.search(r"section\.peerGroupLevels\s*=\s*peerGroupLevels\s*\?\?\s*\[:\]", mapper)
    assert re.search(r"peerGroupLevel:\s*peerGroupLevel\b", mapper), "the legacy level was dropped"
    assert re.search(r"return\s+section\b", mapper)


# ── (2) "industry avg" on the Overview snapshot cards and the Valuation Meter ─────────


def _snapshot_metric() -> str:
    return _decl(_code(_DETAIL_MODELS), r"\bstruct\s+SnapshotMetric\s*:\s*Identifiable\s*")


def _swift_peer_wording():
    """`SnapshotMetric.peerWording`, rebuilt from the guard and the regex pairs IN THE SWIFT."""
    body = _decl(_snapshot_metric(), r"\bstatic\s+func\s+peerWording\s*\(")
    guard = re.search(r'guard\s+peerLevel\s*==\s*"(\w+)"\s+else\s*\{\s*return\s+name\s*\}', body)
    assert guard, f"peerWording lost its level guard: {body}"
    pairs = re.findall(r'of:\s*#"(.*?)"#\s*,\s*with:\s*"([^"]*)"\s*,\s*options:\s*\.regularExpression', body)
    assert len(pairs) == 2, f"expected the 'sector avg' and 'vs sector' rewrites, found {pairs}"

    def wording(name: str, level):
        if level != guard.group(1):
            return name
        for pattern, template in pairs:
            name = re.sub(pattern, re.sub(r"\$(\d)", r"\\\1", template), name)
        return name
    return wording


def test_the_metric_carries_an_optional_level_and_a_display_name():
    metric = _snapshot_metric()
    assert re.search(r"\bvar\s+peerLevel\s*:\s*String\?\s*=\s*nil", metric)
    assert re.search(r"\bvar\s+displayName\s*:\s*String\s*\{\s*Self\.peerWording\(\s*name\s*,\s*peerLevel:\s*peerLevel\s*\)\s*\}",
                     metric)
    # `id` stays the wire name, so a level change never re-identifies a row.
    assert re.search(r"\bvar\s+id\s*:\s*String\s*\{\s*name\s*\}", metric)


@pytest.mark.parametrize("name, level, shown", [
    # The valuation snapshot's two shapes and the health snapshot's.
    ("P/E (1.30x sector avg 22.4)", "industry", "P/E (1.30x industry avg 22.4)"),
    ("P/S (7.50x sector avg 0.98)", "industry", "P/S (7.50x industry avg 0.98)"),
    ("FCF Yield (sector avg 3.10%)", "industry", "FCF Yield (industry avg 3.10%)"),
    ("Current Ratio (vs sector 1.23)", "industry", "Current Ratio (vs industry 1.23)"),
    ("EV/EBITDA (1.33x the sector average 18)", "industry", "EV/EBITDA (1.33x the industry average 18)"),
    # A sector median, no level, an unknown future level: byte-identical.
    ("P/E (1.30x sector avg 22.4)", "sector", "P/E (1.30x sector avg 22.4)"),
    ("P/E (1.30x sector avg 22.4)", None, "P/E (1.30x sector avg 22.4)"),
    ("P/E (1.30x sector avg 22.4)", "market", "P/E (1.30x sector avg 22.4)"),
    ("P/E (1.30x sector avg 22.4)", "Industry", "P/E (1.30x sector avg 22.4)"),
    # No peer phrase at all, and a near-miss that is not one.
    ("Gross Margin", "industry", "Gross Margin"),
    ("Intersector avg spread", "industry", "Intersector avg spread"),
    ("Sector Rotation", "industry", "Sector Rotation"),
    ("", "industry", ""),
])
def test_the_industry_substitution_is_keyed_on_the_level(name, level, shown):
    assert _swift_peer_wording()(name, level) == shown


def test_the_overview_card_shows_the_display_name():
    card = _decl(_code(_SNAPSHOT_CARD), r"\bstruct\s+SnapshotCard\s*:\s*View\s*")
    assert "Text(metric.displayName)" in card
    assert "Text(metric.name)" not in card


def test_the_valuation_meter_rows_show_the_display_name():
    section = _decl(_code(_VALUATION_SECTION), r"\bstruct\s+ValuationMeterSection\s*:\s*View\s*")
    rows = _decl(section, r"\bprivate\s+var\s+multiplesRows\s*:\s*some\s+View\s*")
    assert "Text(metric.displayName)" in rows
    assert "Text(metric.name)" not in rows


@pytest.mark.parametrize("context", ["overviewContext", "analysisContext"])
def test_cay_ai_is_told_the_same_peer_group_the_card_shows(context):
    body = _decl(_code(_VM), rf"\bprivate\s+var\s+{context}\s*:\s*String\?\s*")
    assert r"\($0.displayName): \($0.value)" in body
    assert r"\($0.name): \($0.value)" not in body
    if context == "analysisContext":
        assert "vs sector peers" not in body, "the valuation line names one group for both"


# ── (2) Health Check names its comparison group ─────────────────────────────────────


def _hc_metric() -> str:
    return _decl(_code(_HC_MODELS), r"\bstruct\s+HealthCheckMetric\s*:\s*Identifiable\s*")


def _swift_comparison_prefix():
    body = _decl(_hc_metric(), r"\bvar\s+comparisonPrefix\s*:\s*String\s*")
    arms = dict(re.findall(r'if\s+peerLevel\s*==\s*"(\w+)"\s*\{\s*return\s+"([^"]+)"\s*\}', body))
    default = re.findall(r'\n\s*return\s+"([^"]+)"\s*\n', body)
    assert arms and len(default) == 1, f"comparisonPrefix drifted: {body}"
    return lambda level: arms.get(level, default[0]) if level is not None else default[0]


def test_the_health_metric_carries_an_optional_level():
    assert re.search(r"\bvar\s+peerLevel\s*:\s*String\?\s*=\s*nil", _hc_metric())


@pytest.mark.parametrize("level, prefix", [
    ("industry", "vs industry"),
    ("sector", "vs sector"),
    (None, "vs"),          # an older backend: today's label, never a guessed group
    ("market", "vs"),      # an unknown future level
])
def test_the_comparison_label_names_industry_or_sector_by_level(level, prefix):
    assert _swift_comparison_prefix()(level) == prefix


def test_every_peer_comparison_row_uses_the_prefix():
    body = _decl(_hc_metric(), r"\bvar\s+formattedComparison\s*:\s*String\?\s*")
    after_guard = body[body.index("guard let comparison = comparisonValue"):]
    returns = re.findall(r'return\s+"([^"\n]*(?:"[^"\n]*"[^"\n]*)*)"', after_guard)
    peer_returns = [r for r in returns if "comparison" in r]
    assert len(peer_returns) == 3, peer_returns
    for r in peer_returns:
        assert r.startswith(r"\(comparisonPrefix) "), r
    assert not re.search(r'return\s+"vs \\\(', after_guard), "a row still prints a bare 'vs'"


# ── (2) Profit Power legend follows the tab on screen ───────────────────────────────


def _pp_section_data() -> str:
    return _decl(_code(_PP_MODELS), r"\bstruct\s+ProfitPowerSectionData\s*")


def _swift_pp_peer_word():
    data = _pp_section_data()
    keys = _decl(data, r"\bstatic\s+func\s+peerLevelKey\s*\(\s*for\s+period:\s*ProfitPowerPeriodType\s*\)")
    key_of = dict(re.findall(r'case\s+\.(\w+)\s*:\s*return\s+"(\w+)"', keys))
    body = _decl(data, r"\bfunc\s+peerWord\s*\(\s*for\s+period:\s*ProfitPowerPeriodType\s*\)\s*->\s*String")
    flat = re.sub(r"\s+", " ", body)
    assert "let level = peerGroupLevels[Self.peerLevelKey(for: period)] ?? peerGroupLevel" in flat, flat
    m = re.search(r'return level == "(\w+)" \? "(\w+)" : "(\w+)"', flat)
    assert m, flat

    def word(levels: dict, legacy, period: str) -> str:
        level = levels.get(key_of[period], legacy)
        return m.group(2) if level == m.group(1) else m.group(3)
    return key_of, word


def test_the_per_period_keys_are_the_ones_the_backend_writes():
    key_of, _ = _swift_pp_peer_word()
    assert key_of == {"annual": "annual", "quarterly": "quarterly"}
    src = (_SERVICES / "profit_power_service.py").read_text()
    for key in key_of.values():
        assert re.search(rf'\(\s*"{key}"\s*,\s*{key}_points', src), f"the backend no longer keys {key!r}"
    assert re.search(r"\bvar\s+peerGroupLevels\s*:\s*\[String\s*:\s*String\]\s*=\s*\[:\]", _pp_section_data())


@pytest.mark.parametrize("levels, legacy, period, word", [
    ({"annual": "industry", "quarterly": "sector"}, "industry", "annual", "Industry"),
    ({"annual": "industry", "quarterly": "sector"}, "industry", "quarterly", "Sector"),
    # A series with no drawn peer line has no key: the response-wide level.
    ({"annual": "industry"}, "industry", "quarterly", "Industry"),
    ({"annual": "sector"}, "sector", "quarterly", "Sector"),
    # An older payload: no per-period map at all.
    ({}, "industry", "annual", "Industry"),
    ({}, None, "quarterly", "Sector"),
    ({"annual": "market"}, "industry", "annual", "Sector"),
])
def test_the_legend_word_follows_the_period(levels, legacy, period, word):
    _, swift_word = _swift_pp_peer_word()
    assert swift_word(levels, legacy, period) == word


def test_the_card_passes_the_on_screen_periods_word_to_chart_and_legend():
    card = _decl(_code(_PP_CARD), r"\bstruct\s+ProfitPowerSectionCard\s*:\s*View\s*")
    word = _decl(card, r"\bprivate\s+var\s+peerWord\s*:\s*String\s*")
    assert "profitPowerData.peerWord(for: displayedPeriod)" in word
    chart = re.search(r"ProfitPowerChartView\((.*?)\)\s*\.padding", card, re.S)
    legend = re.search(r"ProfitPowerLegendView\((.*?)\)\s*\.frame", card, re.S)
    assert chart and legend
    for call in (chart.group(1), legend.group(1)):
        assert re.search(r"peerWord:\s*peerWord\s*,", call), call
        assert "profitPowerData.peerWord," not in call


# ── (3) Cay AI's peer net-margin line mirrors the backend ───────────────────────────


def _margins_block() -> str:
    ctx = _decl(_code(_VM), r"\bprivate\s+var\s+financialsContext\s*:\s*String\?\s*")
    m = re.search(r"\bif\s+let\s+pp\s*=\s*profitPowerData\s*,\s*let\s+latest\s*=\s*pp\.annualData\.last\s*\{", ctx)
    assert m, "financialsContext no longer reads the latest annual Profit Power point"
    return _block_from(ctx, m.end() - 1)


def _peer_line_literal() -> str:
    block = _margins_block()
    lines = [ln.strip() for ln in block.splitlines() if "peer-group median net margin" in ln]
    assert len(lines) == 1, lines
    m = re.fullmatch(r'parts\.append\("(.*)"\)', lines[0])
    assert m, lines[0]
    return m.group(1)


def test_the_peer_line_is_never_dated_with_the_companys_fiscal_year():
    block = _margins_block()
    assert "Avg Net Margin (" not in block, "the FY label put a full-year claim on the peer median"
    literal = _peer_line_literal()
    assert "periodLabel" not in literal and "FY" not in literal
    peer = _if_block(block, r"let\s+peerNet\s*=\s*latest\.sectorAverageNetMargin\s*,\s*peerNet\.isFinite")
    assert "peer-group median net margin" in peer


def _backend_peer_phrase() -> str:
    tree = ast.parse(_CHAT_SERVICE.read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_format_profit_summary")
    values = [n.value.value for n in ast.walk(fn)
              if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "peer_year" for t in n.targets)
              and isinstance(n.value, ast.Constant)]
    assert len(values) == 1, values
    return values[0]


def test_the_ios_line_renders_exactly_the_backends_grounding_fragment():
    """Rebuild the iOS line from its Swift literal and compare it with what the backend's
    own `_format_profit_summary` writes for the same numbers."""
    from app.services.chat_service import ChatService

    literal = _peer_line_literal()
    assert _backend_peer_phrase() in literal

    def render(word: str, value: float) -> str:
        out = literal.replace(r"\(pp.peerWord(for: .annual))", word)
        out = out.replace(r'\(String(format: "%.1f", peerNet))', f"{value:.1f}")
        assert "\\(" not in out, f"an interpolation this port does not know: {out}"
        return out

    for level, word in (("industry", "Industry"), ("sector", "Sector")):
        data = ProfitPowerResponse(
            symbol="XYZ", quarterly=[], peer_group_level=level,
            peer_group_levels={"annual": level},
            annual=[ProfitPowerDataPointSchema(period="2026", gross_margin=60.0,
                                               operating_margin=30.0, net_margin=20.0,
                                               fcf_margin=18.0, sector_average_net_margin=12.34)],
        )
        backend = ChatService._format_profit_summary("XYZ", data)
        assert render(word, 12.34) in backend, (render(word, 12.34), backend)


def test_the_peer_line_reads_the_annual_series_level():
    assert r"\(pp.peerWord(for: .annual))" in _peer_line_literal()


# ── (4) "Peer comparison temporarily unavailable." on a benchmarks-degraded card ─────


def _vm() -> str:
    return _code(_VM)


def test_the_peer_lookup_reason_is_one_the_backend_emits_and_never_a_retry():
    vm = _vm()
    m = re.search(r'private\s+static\s+let\s+peerLookupReason\s*=\s*"(\w+)"', vm)
    assert m and m.group(1) == "benchmarks"
    for svc in ("growth_service.py", "profit_power_service.py", "health_check_service.py"):
        assert '"benchmarks"' in (_SERVICES / svc).read_text(), f"{svc} no longer emits 'benchmarks'"
    reasons = re.search(r"static\s+let\s+nonDataLegReasons\s*:\s*Set<String>\s*=\s*\[(.*?)\]", vm, re.S)
    assert reasons and '"benchmarks"' in reasons.group(1), "a peer-only gap must never offer a retry"
    body = _decl(vm, r"\bprivate\s+static\s+func\s+peerLookupFailed\s*\(")
    assert re.search(r"\(\s*degraded\s*\?\?\s*\[\]\s*\)\.contains\(\s*peerLookupReason\s*\)", body)


@pytest.mark.parametrize("fetcher, flag", [
    ("fetchGrowth", "growthPeerUnavailable"),
    ("fetchProfitPower", "profitPowerPeerUnavailable"),
    ("fetchHealthCheck", "healthCheckPeerUnavailable"),
])
def test_each_fetcher_sets_its_flag_from_degraded_and_clears_it_on_failure(fetcher, flag):
    vm = _vm()
    assert re.search(rf"@Published\s+private\(set\)\s+var\s+{flag}\s*:\s*Bool\s*=\s*false", vm)
    body = _decl(vm, rf"\bprivate\s+func\s+{fetcher}\s*\(")
    catch_at = re.search(r"\}\s*catch\s*\{", body)
    assert catch_at
    success, catch = body[:catch_at.start()], _block_from(body, catch_at.end() - 1)
    assert re.search(rf"self\.{flag}\s*=\s*Self\.peerLookupFailed\(\s*dto\.degraded\s*\)", success)
    assert re.search(rf"self\.{flag}\s*=\s*false", catch), "a failed fetch must not keep the note"
    assert not re.search(rf"self\.{flag}\s*=\s*Self\.peerLookupFailed", catch)


def test_the_note_is_one_muted_line_in_the_shared_words():
    note = _decl(_code(_NOTE), r"\bstruct\s+PeerComparisonUnavailableNote\s*:\s*View\s*")
    assert 'static let message = "Peer comparison temporarily unavailable."' in note
    body = _decl(note, r"\bvar\s+body\s*:\s*some\s+View\s*")
    assert "Text(Self.message)" in body
    assert ".font(AppTypography.caption)" in body and ".foregroundColor(AppColors.textSecondary)" in body
    assert "Button" not in body and "onRetry" not in body, "a peer-only gap is not a retry"


# (Swift file, struct, flag declaration, the Bool property that says the TAB ON SCREEN draws
# a dashed median — None where the card has no per-tab peer line to contradict).
_NOTE_CARDS = [
    (_GROWTH_CARD, "GrowthSectionCard", r"private\(set\)\s+var", "showsPeerLine"),
    (_PP_CARD, "ProfitPowerSectionCard", r"var", "drawsPeerLine"),
    (_HC_CARD, "HealthCheckSectionCard", r"var", None),
]


def _note_condition(card: str) -> str:
    """The `if` condition guarding the ONE `PeerComparisonUnavailableNote()`."""
    # The note is the FIRST statement of the block, so it is inside this `if` (a modifier
    # chain after it is fine); `card.count(...) == 1` rules out a second, unguarded copy.
    m = re.search(r"\bif\s+([^{}\n]+?)\s*\{\s*PeerComparisonUnavailableNote\(\)", card)
    assert m, "the note is no longer drawn inside a plain `if <condition> { … }`"
    return m.group(1).strip()


def _eval_condition(cond: str, env: dict) -> bool:
    """Run a Swift Bool condition made only of identifiers, `&&`, `||`, `!` and parentheses."""
    assert re.fullmatch(r"[\w\s&|!()]+", cond), f"a condition this port does not know: {cond}"
    names = set(re.findall(r"[A-Za-z_]\w*", cond))
    assert names <= set(env), f"the condition reads {names - set(env)}, which this port does not model"
    py = re.sub(r"!(?!=)", " not ", cond.replace("&&", " and ").replace("||", " or "))
    return bool(eval(py, {"__builtins__": {}}, dict(env)))  # noqa: S307 - whitelisted above


@pytest.mark.parametrize("path, struct, decl, drawn", _NOTE_CARDS)
def test_each_card_draws_the_note_only_when_told(path, struct, decl, drawn):
    card = _decl(_code(path), rf"\bstruct\s+{struct}\s*:\s*View\s*")
    assert re.search(rf"(?<![\w.]){decl}\s+peerComparisonUnavailable\s*:\s*Bool\s*=\s*false", card)
    assert card.count("PeerComparisonUnavailableNote()") == 1, "the note is drawn outside its condition"
    cond = _note_condition(card)
    # The truth table, run on the Swift condition itself (IOS-R3-3, 2026-10-08): the backend
    # flags "benchmarks" when EITHER period's peer read failed and still draws the period
    # that succeeded, so the note must never sit under a drawn median.
    for flag in (False, True):
        for line_drawn in (False, True):
            env = {"peerComparisonUnavailable": flag}
            if drawn is not None:
                env[drawn] = line_drawn
            shown = _eval_condition(cond, env)
            expected = flag and (drawn is None or not line_drawn)
            assert shown == expected, (struct, cond, env)


@pytest.mark.parametrize("path, struct, prop, field", [
    (_GROWTH_CARD, "GrowthSectionCard", "showsPeerLine", "sectorAverageYoY"),
    (_PP_CARD, "ProfitPowerSectionCard", "drawsPeerLine", "sectorAverageNetMargin"),
])
def test_the_notes_guard_reads_the_series_on_screen(path, struct, prop, field):
    """The note's 'is a peer line drawn' test is over the DISPLAYED period's (and, for
    Growth, metric's) points, on the same field the dashed line and the legend use — a
    guard over the other tab, or over a field the chart never draws, would be green while
    the note contradicts the screen."""
    card = _decl(_code(path), rf"\bstruct\s+{struct}\s*:\s*View\s*")
    body = re.sub(r"\s+", " ", _decl(card, rf"\bprivate\s+var\s+{prop}\s*:\s*Bool\s*"))
    assert re.fullmatch(
        rf"\{{ currentDataPoints\.contains(?:\(where: | )\{{ \$0\.{field} != nil \}}\)? \}}", body,
    ), body
    points = _decl(card, r"\bprivate\s+var\s+currentDataPoints\s*:\s*\[\w+\]\s*")
    assert "displayedPeriod" in points and "selectedPeriod" not in points, points
    # The legend's entry and the note agree on what "drawn" means.
    call = re.search(r"\w+LegendView\((.*?)\)\s*\.frame", card, re.S)
    assert call, "the legend call moved; update this guard"
    legend = re.search(r"showsPeerLine:\s*([^\n]+)", call.group(1))
    assert legend, "the legend no longer takes showsPeerLine"
    arg = legend.group(1).strip().rstrip(",").strip()
    norm = re.sub(r"\s+", " ", arg).replace("contains(where: {", "contains {").replace("})", "}")
    assert norm in (prop, f"currentDataPoints.contains {{ $0.{field} != nil }}"), arg


def test_the_growth_delegating_init_takes_the_flag():
    card = _decl(_code(_GROWTH_CARD), r"\bstruct\s+GrowthSectionCard\s*:\s*View\s*")
    init = _decl(card, r"\binit\s*\(\s*growthData:\s*GrowthSectionData\s*,\s*isDegraded:\s*Bool\s*,")
    assert re.search(r"self\.peerComparisonUnavailable\s*=\s*peerComparisonUnavailable", init)


@pytest.mark.parametrize("card_call, flag", [
    ("GrowthSectionCard(", "growthPeerUnavailable"),
    ("ProfitPowerSectionCard(", "profitPowerPeerUnavailable"),
    ("HealthCheckSectionCard(", "healthCheckPeerUnavailable"),
])
def test_the_financials_tab_wires_each_flag_from_the_view_model(card_call, flag):
    content = _decl(_code(_FINANCIALS), r"\bstruct\s+TickerFinancialsContent\s*:\s*View\s*")
    assert re.search(rf"\bvar\s+{flag}\s*:\s*Bool\s*=\s*false", content)
    body = _decl(content, r"\bvar\s+body\s*:\s*some\s+View\s*")
    at = body.index(card_call)
    call = body[at: body.index("\n            }\n", at)]
    assert re.search(rf"peerComparisonUnavailable:\s*{flag}\b", call), call
    view = _code(_DETAIL_VIEW)
    financials = view[view.index("case .financials:"): view.index("case .holders:")]
    assert re.search(rf"{flag}:\s*viewModel\.{flag}\b", financials)


# ── (5) the Snapshots header dates the OLDEST build, never "today" ──────────────────


def _snapshots_section() -> str:
    return _decl(_code(_SNAPSHOTS_SECTION), r"\bstruct\s+TickerDetailSnapshotsSection\s*:\s*View\s*")


def test_the_header_never_prints_the_devices_today():
    section = _snapshots_section()
    assert "Date()" not in section, "the header claims the device's today again"
    assert "formattedDate" not in section


def test_the_header_shows_the_oldest_build_time_or_nothing():
    section = _snapshots_section()
    text = _decl(section, r"\bprivate\s+var\s+updatedOnText\s*:\s*String\?\s*")
    assert re.search(r"guard\s+let\s+oldest\s*=\s*snapshots\.compactMap\(\\\.computedAt\)\.min\(\)\s*else\s*\{\s*return\s+nil\s*\}", text)
    assert ".max()" not in text
    assert re.search(r'return\s+"Updated on \\\(Self\.dateFormatter\.string\(from:\s*oldest\)\) ET"', text)
    body = _decl(section, r"\bvar\s+body\s*:\s*some\s+View\s*")
    shown = _if_block(body, r"let\s+updatedOnText")
    assert "Text(updatedOnText)" in shown
    assert body.count("Updated on") == 0, "a second, unconditional date line"


def test_the_build_time_parser_accepts_the_backends_wire_format_and_refuses_junk():
    item = _decl(_code(_DETAIL_MODELS), r"\bstruct\s+SnapshotItem\s*:\s*Identifiable\s*")
    assert re.search(r"\bvar\s+computedAt\s*:\s*Date\?\s*=\s*nil", item)
    parse = _decl(item, r"\bstatic\s+func\s+parseComputedAt\s*\(")
    flat = re.sub(r"\s+", " ", parse)
    assert "!raw.isEmpty else { return nil }" in flat
    assert "isoFractional.date(from: raw) ?? isoPlain.date(from: raw)" in flat
    assert "return date >= earliestPlausibleComputedAt ? date : nil" in flat
    assert "Date()" not in parse, "an unreadable stamp must be nil, never now"
    plain = _decl(item, r"\bprivate\s+static\s+let\s+isoPlain\s*:\s*ISO8601DateFormatter\s*=")
    assert "f.formatOptions = [.withInternetDateTime]" in plain
    # What the backend writes is exactly RFC 3339 to the second with "Z" — the shape
    # `.withInternetDateTime` parses.
    from datetime import datetime, timezone
    for moment in (datetime(2026, 10, 7, 14, 3, 22, 123456, tzinfo=timezone.utc),
                   datetime(2026, 10, 7, 23, 59, 59)):
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", snapshot_build_time(moment))


# ── the report's label strip also takes an "industry" suffix ───────────────────────


def _report_strip_patterns() -> list[str]:
    metric = _code(_REPORT_MODELS)
    found = []
    for prop in ("historyTitle", "displayLabel"):
        body = _decl(metric, rf"\bvar\s+{prop}\s*:\s*String\s*")
        pats = re.findall(r'of:\s*#"(.*?)"#', body)
        peer = [p for p in pats if "sector" in p]
        assert len(peer) == 1, (prop, pats)
        found.append(peer[0])
    return found


@pytest.mark.parametrize("label, stripped", [
    ("P/E (0.98x sector avg 27)", "P/E"),
    ("P/E (0.98x industry avg 27)", "P/E"),
    ("Current Ratio (vs sector 4.5)", "Current Ratio"),
    ("Current Ratio (vs industry 4.5)", "Current Ratio"),
    ("Revenue Growth", "Revenue Growth"),
    ("Return on Equity (ROE)", "Return on Equity (ROE)"),
])
def test_the_report_label_strip_takes_sector_or_industry(label, stripped):
    for pattern in _report_strip_patterns():
        assert re.sub(pattern, "", label).strip() == stripped, (pattern, label)
