"""iOS half of round 4 (2026-10-08): the report's Profitability drill-down names the peer
group of the line ON SCREEN — per metric AND per tab.

Before: `toMarginSeries()` stamped ONE `peerGroupLevel` on all four margin series, ROE/ROA
took `sectorAnnualLevel ?? sectorQuarterlyLevel`, and `ProfitabilityChartSheet.peerWord`
never read `selectedPeriod`. The backend picks every metric's annual and quarterly line
separately, so the FCF chip (or any Quarterly tab) could read "Industry Average … vs
industry" over a SECTOR median (IOS-R3-1, PP-LEVEL-1/2, RPT3-4, R3-CONTRACT-4).

Now `ProfitabilityMetricSeries` carries `annualPeerLevel` / `quarterlyPeerLevel`; margins
read their level through `ProfitPowerSectionData.lineLevel` — in a per-line (v7) map ONLY
`peer_group_levels["<period>.<metric>"]` (a missing key = an undrawn line = no level, and the
series' period-agnostic `peerLevel` is nil too: round 6, PP4-LVL-FALLBACK); in an older map
the tab's key, else `peerGroupLevel`. ROE/ROA read the card metric's `sectorAnnualLevel` /
`sectorQuarterlyLevel`; and `peerWord` picks by `selectedPeriod`, then the series' level,
then the card's. The v7-specific cases (live service, frozen-report narrowing, the live
card's `peerWord(for:)`) are in tests/test_pp_levels_round6_ios.py, which reuses these ports.

There is no XCTest target and this session may not compile Swift, so (testing.md §3):
comments are stripped before every assertion, every check is brace-bound to ONE
declaration, and the decision logic is PORTED from the Swift text and run against a case
table — and against what the REAL backend service emits — so the table fails when the Swift
changes, not only when this copy does. Every construction site of the changed struct is
checked against its memberwise initializer, since a missed one would be a build break.

Mutation reasoning (each checked by hand on a mutated COPY, via the path constants):
* `peerWord` back to `series(selectedMetric)?.peerLevel ?? …` → the port refuses it;
* annual/quarterly swapped in the ternary → the Quarterly-tab rows fail;
* `toMarginSeries` reading only `levels["annual"]` → the FCF-at-sector row fails;
* `lineLevel`'s per-line branch falling back (`levels[own] ?? levels[tab]`, or `?? legacyLevel`)
  → the "v7, own key absent" rows fail; dropping its `?? legacyLevel` → the v6/empty rows fail;
* `hasPerLineLevels` testing anything but a "." in a key (`isEmpty`, a negation) → its port
  refuses the body; `fallbackLevel`'s ternary arms swapped → the peerLevel rows fail;
* `toMarginSeries` passing `peerLevel: peerGroupLevel` again, or a second direct `levels[...]`
  read → the wiring port refuses it;
* a margin's `key:` typo ("fcf" for "fcf_margin") → the key-parity test fails;
* ROE back to the period-blind `sectorAnnualLevel ?? sectorQuarterlyLevel` → fails;
* a construction site passing a label out of declaration order → the init test fails.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"
_SERIES_MODELS = _IOS / "Models" / "ProfitabilityChartModels.swift"
_SHEET = _IOS / "Views" / "Molecules" / "ProfitabilityChartSheet.swift"
_PP_MODELS = _IOS / "Models" / "ProfitPowerModels.swift"
_PP_SERVICE = _REPO / "backend" / "app" / "services" / "profit_power_service.py"


# ── helpers (same contract as test_ios_benchmark_wording_2026_10_07.py) ──────────


def _strip_swift_comments(src: str) -> str:
    """Drop `/* */` blocks and `//` line comments; a `//` inside a string literal is kept."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out_lines = []
    for line in src.splitlines():
        in_str, cut, i = False, len(line), 0
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


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _series_struct() -> str:
    return _decl(_code(_SERIES_MODELS), r"\bstruct\s+ProfitabilityMetricSeries\s*:\s*Identifiable\s*")


def _margin_mapper() -> str:
    ext = _decl(_code(_SERIES_MODELS), r"\bextension\s+ProfitPowerResponseDTO\s*")
    return _decl(ext, r"\bfunc\s+toMarginSeries\s*\(\s*\)\s*->\s*\[ProfitabilityMetricSeries\]")


def _roe_mapper() -> str:
    ext = _decl(_code(_SERIES_MODELS), r"\bextension\s+DeepDiveMetric\s*")
    return _decl(ext, r"\bfunc\s+toProfitabilitySeries\s*\(")


def _sheet() -> str:
    return _decl(_code(_SHEET), r"\bstruct\s+ProfitabilityChartSheet\s*:\s*View\s*")


# ── the struct: two per-tab levels that every existing construction still compiles with ─


def _stored_properties(struct_body: str) -> List[tuple]:
    """[(name, is_let, has_default)] for the struct's depth-1 STORED properties, in order."""
    inner = struct_body[1:-1]
    depth, line_start, out = 0, 0, []
    for i, ch in enumerate(inner + "\n"):
        if ch == "\n":
            line = inner[line_start:i]
            if depth == 0:
                m = re.match(r"\s*(let|var)\s+(\w+)\s*:\s*([^={]+?)\s*(=\s*[^{]+)?\s*$", line)
                if m:
                    out.append((m.group(2), m.group(1) == "let", m.group(4) is not None))
            line_start = i + 1
        elif ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
    return out


def test_the_series_carries_a_level_per_tab_with_nil_defaults():
    props = _stored_properties(_series_struct())
    assert props == [
        ("metric", True, False), ("annual", True, False), ("quarterly", True, False),
        ("peerLevel", False, True), ("annualPeerLevel", False, True), ("quarterlyPeerLevel", False, True),
    ], props
    body = _series_struct()
    for name in ("peerLevel", "annualPeerLevel", "quarterlyPeerLevel"):
        assert re.search(rf"\bvar\s+{name}\s*:\s*String\?\s*=\s*nil\b", body), name


def _call_labels(src: str, open_paren: int) -> List[Optional[str]]:
    """Top-level argument labels of the call whose `(` is at `open_paren` (None = unlabelled)."""
    depth, i, args, cur = 0, open_paren, [], ""
    in_str = False
    while True:
        ch = src[i]
        if in_str:
            if ch == "\\":
                cur += src[i: i + 2]
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "([{":
            depth += 1
            if depth == 1:
                i += 1
                continue
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                args.append(cur)
                break
        elif ch == "," and depth == 1:
            args.append(cur)
            cur = ""
            i += 1
            continue
        cur += ch
        i += 1
    labels = []
    for a in args:
        m = re.match(r"\s*(\w+)\s*:(?!:)", a)
        labels.append(m.group(1) if m else None)
    return labels


def _all_swift_files() -> List[Path]:
    return sorted(p for p in (_REPO / "frontend" / "ios").rglob("*.swift") if ".build" not in p.parts)


def test_every_construction_site_matches_the_memberwise_initializer():
    """No compiler here: a call whose labels are not an in-order subsequence of the stored
    properties (with every `let` present) is a build break. Scans the WHOLE iOS tree,
    #Preview blocks included."""
    props = _stored_properties(_series_struct())
    order = [p[0] for p in props]
    required = {p[0] for p in props if p[1] and not p[2]}
    sites = 0
    for path in _all_swift_files():
        src = _code(path)
        for m in re.finditer(r"(?<![\w.])ProfitabilityMetricSeries\s*\(", src):
            labels = _call_labels(src, m.end() - 1)
            sites += 1
            assert None not in labels, (path.name, labels)
            assert required <= set(labels), (path.name, labels)
            idx = [order.index(lab) for lab in labels]
            assert idx == sorted(idx) and len(set(idx)) == len(idx), (path.name, labels)
    assert sites >= 2, f"found {sites} construction sites — the scan drifted"


def test_the_roe_mapper_keeps_its_signature_for_its_callers():
    sig = _code(_SERIES_MODELS)
    m = re.search(r"func\s+toProfitabilitySeries\s*\(\s*_\s+metric:\s*ProfitabilityMetricType\s*,"
                  r"\s*peerLevel:\s*String\?\s*=\s*nil\s*\)", sig)
    assert m, "toProfitabilitySeries' signature changed — re-check every caller"
    calls = 0
    for path in _all_swift_files():
        src = _code(path)
        for c in re.finditer(r"\.toProfitabilitySeries\s*\(", src):
            labels = _call_labels(src, c.end() - 1)
            calls += 1
            assert labels[0] is None and set(labels[1:]) <= {"peerLevel"}, (path.name, labels)
    assert calls == 2, calls


# ── margins: per metric, per tab ──────────────────────────────────────────────────


def _make_calls() -> Dict[str, Dict[str, str]]:
    flat = _flat(_margin_mapper())
    calls = re.findall(
        r'make\(\.(\w+), key: "(\w+)", company: \{ \$0\.(\w+) \}, sector: \{ \$0\.(\w+) \}\)', flat,
    )
    assert len(calls) == 4, calls
    return {key: {"case": case, "company": comp, "sector": sec} for case, key, comp, sec in calls}


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(w[:1].upper() + w[1:] for w in rest)


def test_each_margins_key_is_the_backend_metric_its_closures_read():
    calls = _make_calls()
    src = _PP_SERVICE.read_text()
    m = re.search(r"_MARGIN_BENCHMARK_METRICS\s*=\s*\[([^\]]*)\]", src)
    assert m, "the backend margin list moved"
    backend = set(re.findall(r'"(\w+)"', m.group(1)))
    assert set(calls) == backend, (set(calls), backend)
    for key, c in calls.items():
        assert c["company"] == _camel(key), (key, c)                       # gross_margin → grossMargin
        assert c["sector"] == "sectorAverage" + _camel(key)[:1].upper() + _camel(key)[1:], (key, c)
        assert c["case"] == _camel(key), (key, c)                          # .grossMargin


def _pp_section_data() -> str:
    return _decl(_code(_PP_MODELS), r"\bstruct\s+ProfitPowerSectionData\s*")


def _norm_parens(flat: str) -> str:
    """`f(\n a, b\n)` flattens to `f( a, b )`: drop the space just inside the parens."""
    return re.sub(r"\s+\)", ")", re.sub(r"\(\s+", "(", flat))


def _swift_pp_key_of() -> Dict[str, str]:
    """`ProfitPowerSectionData.peerLevelKey(for:)` — period → backend tab key."""
    keys = _decl(_pp_section_data(), r"\bstatic\s+func\s+peerLevelKey\s*\(\s*for\s+period:\s*ProfitPowerPeriodType\s*\)")
    key_of = dict(re.findall(r'case\s+\.(\w+)\s*:\s*return\s+"(\w+)"', keys))
    assert key_of == {"annual": "annual", "quarterly": "quarterly"}, key_of
    return key_of


def _swift_has_per_line_levels():
    """Port of `ProfitPowerSectionData.hasPerLineLevels(_:)`: any key holding the separator."""
    body = _flat(_decl(_pp_section_data(),
                       r"\bstatic\s+func\s+hasPerLineLevels\s*\(\s*_\s+levels:\s*\[String:\s*String\]\s*\)\s*->\s*Bool"))
    m = re.fullmatch(r'\{ levels\.keys\.contains\(where: \{ \$0\.contains\("(.)"\) \}\) \}', body)
    assert m, f"hasPerLineLevels changed shape — re-port it: {body}"
    sep = m.group(1)
    return lambda levels: any(sep in k for k in levels)


def _swift_line_level():
    """Port of `ProfitPowerSectionData.lineLevel(in:period:metric:legacyLevel:)`."""
    body = _flat(_decl(
        _pp_section_data(),
        r"\bstatic\s+func\s+lineLevel\s*\(\s*in\s+levels:\s*\[String:\s*String\]\s*,\s*period:\s*ProfitPowerPeriodType\s*,"
        r"\s*metric:\s*String\s*,\s*legacyLevel:\s*String\?\s*\)\s*->\s*String\?",
    ))
    m = re.fullmatch(
        r'\{ let tab: String = peerLevelKey\(for: period\) '
        r'if hasPerLineLevels\(levels\) \{ let own: String = tab \+ "(.)" \+ metric return (.+?) \} '
        r'return (.+?) \}',
        body,
    )
    assert m, f"lineLevel changed shape — re-port it: {body}"
    sep = m.group(1)

    def terms(chain: str) -> List[str]:
        out = [t.strip() for t in chain.split("??")]
        for t in out:
            assert t in ("levels[own]", "levels[tab]", "legacyLevel"), f"a term this port does not know: {t!r}"
        return out

    per_line_terms, older_terms = terms(m.group(2)), terms(m.group(3))
    key_of, per_line = _swift_pp_key_of(), _swift_has_per_line_levels()

    def line_level(levels: Dict[str, str], period: str, metric: str, legacy: Optional[str]) -> Optional[str]:
        tab = key_of[period]
        values = {"levels[own]": levels.get(tab + sep + metric), "levels[tab]": levels.get(tab), "legacyLevel": legacy}
        for t in (per_line_terms if per_line(levels) else older_terms):
            if values[t] is not None:
                return values[t]
        return None
    return line_level


def _swift_margin_series():
    """Port of `toMarginSeries`' level wiring: (levels, legacy, key) → the three level fields
    of that margin's `ProfitabilityMetricSeries`."""
    flat = _norm_parens(_flat(_margin_mapper()))
    assert "let levels: [String: String] = peerGroupLevels ?? [:]" in flat
    fb = re.search(r"let fallbackLevel: String\? = ProfitPowerSectionData\.hasPerLineLevels\(levels\) "
                   r"\? (\w+) : (\w+) func make\(", flat)
    assert fb, f"`fallbackLevel` moved or changed shape: {flat}"
    calls = {
        m.group(1): (m.group(2), m.group(3), m.group(4))
        for m in re.finditer(
            r"let (\w+): String\? = ProfitPowerSectionData\.lineLevel\(in: levels, period: \.(\w+), "
            r"metric: (\w+), legacyLevel: (\w+)\) (?=let |return )", flat)
    }
    assert set(calls) == {"annualLevel", "quarterlyLevel"}, calls
    # Every level comes through `lineLevel`: no second, direct read of the map.
    assert not re.search(r"\blevels\[", flat), "a direct `levels[...]` read is back in toMarginSeries"
    ctor = re.search(r"return ProfitabilityMetricSeries\(metric: metric, annual: pts\(annual\), "
                     r"quarterly: pts\(quarterly\), peerLevel: (\w+), annualPeerLevel: (\w+), "
                     r"quarterlyPeerLevel: (\w+)\)", flat)
    assert ctor, f"the series construction changed shape: {flat}"
    per_line, line_level = _swift_has_per_line_levels(), _swift_line_level()

    def series(levels: Dict[str, str], legacy: Optional[str], key: str) -> Dict[str, Optional[str]]:
        names: Dict[str, Optional[str]] = {"peerGroupLevel": legacy, "nil": None}
        names["fallbackLevel"] = names[fb.group(1)] if per_line(levels) else names[fb.group(2)]
        for var, (period, metric_arg, legacy_arg) in calls.items():
            assert metric_arg == "key", (var, metric_arg)
            names[var] = line_level(levels, period, key, names[legacy_arg])
        return {"peerLevel": names[ctor.group(1)], "annualPeerLevel": names[ctor.group(2)],
                "quarterlyPeerLevel": names[ctor.group(3)]}
    return series


def _swift_margin_level(levels: Dict[str, str], legacy: Optional[str], key: str, period: str) -> Optional[str]:
    """The margin's level on `period`'s tab ("annual" / "quarterly")."""
    return _swift_margin_series()(levels, legacy, key)[f"{period}PeerLevel"]


@pytest.mark.parametrize("levels, legacy, key, period, expected", [
    # 2026-10-08 (v7) payload: FCF's own line is the sector's while the tab (net) is industry.
    ({"annual": "industry", "annual.fcf_margin": "sector", "annual.net_margin": "industry"},
     "industry", "fcf_margin", "annual", "sector"),
    ({"annual": "industry", "annual.fcf_margin": "sector", "annual.net_margin": "industry"},
     "industry", "net_margin", "annual", "industry"),
    # Per tab: the quarterly key is read on the Quarterly tab, never the annual one.
    ({"annual.gross_margin": "industry", "quarterly.gross_margin": "sector"},
     "industry", "gross_margin", "quarterly", "sector"),
    # v7 with the line's own key ABSENT: that line draws no peer point, so it gets NO level —
    # never the tab's (the net line's) nor the payload's (PP4-LVL-FALLBACK).
    ({"annual": "industry", "annual.net_margin": "industry"}, "industry", "fcf_margin", "annual", None),
    ({"annual": "industry", "annual.net_margin": "industry"}, "industry", "net_margin", "quarterly", None),
    ({"quarterly.fcf_margin": "sector"}, "industry", "fcf_margin", "annual", None),
    # An older (v6) payload: only per-tab keys, then the payload-wide level.
    ({"annual": "industry", "quarterly": "sector"}, "industry", "operating_margin", "quarterly", "sector"),
    ({"annual": "industry"}, "sector", "operating_margin", "quarterly", "sector"),
    # A pre-2026-10-07 report: no map at all.
    ({}, "industry", "fcf_margin", "quarterly", "industry"),
    ({}, None, "fcf_margin", "annual", None),
])
def test_the_margin_level_chain(levels, legacy, key, period, expected):
    assert _swift_margin_level(levels, legacy, key, period) == expected


@pytest.mark.parametrize("levels, legacy, expected", [
    # A per-line map names every drawn line itself: no period-agnostic word (the sheet reads it
    # when a tab's own level is nil, and hands it to ROE/ROA as their fallback).
    ({"annual": "industry", "annual.net_margin": "industry"}, "industry", None),
    # An older payload keeps its one word for every line.
    ({"annual": "industry", "quarterly": "sector"}, "industry", "industry"),
    ({}, "sector", "sector"),
    ({}, None, None),
])
def test_the_series_fallback_level_is_for_an_older_payload_only(levels, legacy, expected):
    for key in _make_calls():
        assert _swift_margin_series()(levels, legacy, key)["peerLevel"] == expected, key


# ── the sheet's word follows the selected tab ─────────────────────────────────────


def _swift_peer_word():
    word = _flat(_decl(_sheet(), r"\bprivate\s+var\s+peerWord\s*:\s*String\s*"))
    assert "let line: ProfitabilityMetricSeries? = series(selectedMetric)" in word, word
    own = re.search(r"let own: String\? = selectedPeriod == \.(\w+) \? line\?\.(\w+) : line\?\.(\w+)", word)
    assert own, f"peerWord no longer picks by selectedPeriod: {word}"
    chain = re.search(r"let level: String\? = (.+?) return", word)
    assert chain, word
    terms = [t.strip() for t in chain.group(1).split("??")]
    ret = re.search(r'return level == "(\w+)" \? "(\w+)" : "(\w+)"', word)
    assert ret, word

    def peer_word(series: Optional[Dict[str, Any]], card_level: Optional[str], period: str) -> str:
        def field(name):
            return None if series is None else series.get(name)

        values = {
            "own": field(own.group(2)) if period == own.group(1) else field(own.group(3)),
            "line?.peerLevel": field("peerLevel"),
            "card.peerGroupLevel": card_level,
        }
        level = None
        for t in terms:
            assert t in values, f"a term this port does not know: {t!r}"
            if values[t] is not None:
                level = values[t]
                break
        return ret.group(2) if level == ret.group(1) else ret.group(3)
    return peer_word


@pytest.mark.parametrize("series, card, period, word", [
    # The bug: an industry Annual line and a sector Quarterly line.
    ({"annualPeerLevel": "industry", "quarterlyPeerLevel": "sector"}, "industry", "annual", "Industry"),
    ({"annualPeerLevel": "industry", "quarterlyPeerLevel": "sector"}, "industry", "quarterly", "Sector"),
    ({"annualPeerLevel": "sector", "quarterlyPeerLevel": "industry"}, "sector", "quarterly", "Industry"),
    # An older report: no per-tab level → the series' level → the card's.
    ({"peerLevel": "industry"}, "sector", "quarterly", "Industry"),
    ({}, "industry", "annual", "Industry"),
    (None, None, "annual", "Sector"),
    # A per-tab level wins over the fallbacks, and an unknown word is never "Industry".
    ({"annualPeerLevel": "sector", "peerLevel": "industry"}, "industry", "annual", "Sector"),
    ({"annualPeerLevel": "market"}, "industry", "annual", "Sector"),
])
def test_the_sheets_word_follows_the_tab_on_screen(series, card, period, word):
    assert _swift_peer_word()(series, card, period) == word


def test_the_legend_and_the_delta_line_both_use_that_word():
    sheet = _sheet()
    assert 'Text("\\(peerWord) Average")' in _decl(sheet, r"\bprivate\s+var\s+legendAndDelta\s*:\s*some\s+View\s*")
    delta = _decl(sheet, r"\bprivate\s+func\s+deltaText\s*\(")
    assert re.search(r"let\s+peer\s*=\s*peerWord\b", delta)
    # No second, period-blind reading of a series level anywhere in the sheet.
    assert len(re.findall(r"\.peerLevel\b", sheet)) == 2, re.findall(r".{40}\.peerLevel\b", sheet)


def test_roe_and_roa_take_each_tabs_level_from_the_card_metric():
    roe = _flat(_roe_mapper())
    assert re.search(r"peerLevel: peerLevel, annualPeerLevel: sectorAnnualLevel, "
                     r"quarterlyPeerLevel: sectorQuarterlyLevel \)", roe), roe
    init = _flat(_decl(_sheet(), r"\binit\s*\(\s*card:\s*DeepDiveMetricCard\s*,"))
    assert "let fallbackLevel: String? = marginSeries.first?.peerLevel" in init
    for m in ("roe", "roa"):
        assert f"{m}.toProfitabilitySeries(.{m}, peerLevel: fallbackLevel)" in init, init
    assert not re.search(r"sectorAnnualLevel\s*\?\?|sectorQuarterlyLevel\s*\?\?", init), (
        "one level for both tabs is the RPT3-4 defect"
    )


# ── end to end: what the REAL backend emits, read through the Swift ports ─────────


class _FakeFMP:
    def __init__(self, **answers: Any) -> None:
        self._answers = answers

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            key = f"{name}:{kwargs['period']}" if "period" in kwargs else name
            return self._answers.get(key, self._answers.get(name, []))
        return _call


class _FakeLookup:
    def __init__(self, by_type: Dict[str, Any]):
        self._by_type = by_type

    def get_benchmark_series(self, industry, sector, metrics, period_type):
        src = self._by_type.get(period_type, {})
        return {m: {k: dict(v) for k, v in src.get(m, {}).items()} for m in metrics}


def _line(level, labels):
    return {lab: {"value": 0.1, "n": 40, "level": level, "peer_group_name": level} for lab in labels}


@pytest.mark.asyncio
async def test_the_backends_keys_name_the_fcf_and_quarterly_lines_on_screen(monkeypatch):
    """Net/gross/operating are industry lines on Annual; FCF is the sector's; every
    Quarterly line is the sector's. Each chip × tab must name its own line."""
    from app.services import profit_power_service as pp
    from app.services.sector_benchmark_lookup import CALENDAR_QUARTER_PERIOD_TYPE as CQ

    years, quarters = ("2024", "2025"), ("Q4'25", "Q1'26")
    annual = {m: _line("industry", years) for m in ("net_margin", "gross_margin", "operating_margin")}
    annual["fcf_margin"] = _line("sector", years)
    quarterly = {m: _line("sector", quarters)
                 for m in ("net_margin", "gross_margin", "operating_margin", "fcf_margin")}
    monkeypatch.setattr(pp, "get_sector_benchmark_lookup",
                        lambda: _FakeLookup({"annual": annual, CQ: quarterly}))
    row = {"period": "FY", "revenue": 100.0, "grossProfit": 50.0, "operatingIncome": 20.0, "netIncome": 10.0}
    svc = pp.ProfitPowerService.__new__(pp.ProfitPowerService)
    svc.fmp = _FakeFMP(**{
        "get_company_profile": {"symbol": "ZZZ", "sector": "Technology", "industry": "Solar"},
        "get_income_statement:annual": [{**row, "date": "2024-12-31", "fiscalYear": "2024"},
                                        {**row, "date": "2025-12-31", "fiscalYear": "2025"}],
        "get_income_statement:quarter": [{**row, "period": "Q4", "date": "2025-12-31", "fiscalYear": "2025"},
                                         {**row, "period": "Q1", "date": "2026-03-31", "fiscalYear": "2026"}],
    })
    svc.supabase = None
    resp, _n, degraded = await svc._build_profit_power("ZZZ")
    assert degraded == []
    wire = resp.model_dump(mode="json")
    levels, legacy = wire["peer_group_levels"], wire["peer_group_level"]

    word = _swift_peer_word()
    margin_series = _swift_margin_series()
    for key, c in _make_calls().items():
        series = margin_series(levels, legacy, key)
        assert series["peerLevel"] is None, "a v7 payload carries no period-agnostic word"
        want_annual = "Sector" if key == "fcf_margin" else "Industry"
        assert word(series, legacy, "annual") == want_annual, (key, levels)
        assert word(series, legacy, "quarterly") == "Sector", (key, levels)
        # And the drawn line on that tab really is that group's (every point non-null here).
        field = "sector_average_" + key
        assert all(p[field] is not None for p in wire["annual"] + wire["quarterly"]), key
