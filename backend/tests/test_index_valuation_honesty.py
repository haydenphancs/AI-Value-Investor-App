"""The index valuation snapshot must not invent numbers, and must average the right rows.

Two defects confirmed 2026-09-12 (W1/W2 pass, findings F29 + F27):

F29 (P1) — `forward_pe = pe * 0.85`: a flat 15% haircut on the trailing figure, with no
forward-earnings input anywhere. It shipped as a "Fwd P/E" pill AND the valuation story
narrated it as "suggesting analysts expect earnings to catch up" — a fabricated claim about
analyst expectations, on every index, every day. Forward estimates are the unpurchased FMP
"Analyst Estimates" package, so there is no honest source.

F27 — `_compute_index_pe_from_sectors` promised "the simple average across all sectors" but
never filtered `industry = ''`. `sector_benchmarks` is ONE table where `industry=''` IS the
sector row (migration 072) and the quarterly job writes 153 INDUSTRY rows against 11 sector
rows, so the published index P/E was an unweighted mean of industry medians — and the read
was unpaged against PostgREST's 1,000-row clamp.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import app.services.index_service as idx


# ── F29: no fabricated forward P/E, and nothing narrates one ────────────────────────


def test_forward_pe_is_never_derived_from_the_trailing_pe():
    src = Path(idx.__file__).read_text(encoding="utf-8")
    body = re.sub(r"#.*$", "", src, flags=re.M)          # strip comments: the fix EXPLAINS the old formula
    assert "pe * 0.85" not in body, "the fabricated forward-P/E haircut is back"
    assert re.search(r"^\s*forward_pe\s*=\s*0\.0\s*$", body, re.M), \
        "forward_pe must be the explicit unknown sentinel, not a computed value"


def test_no_story_template_asserts_an_analyst_expectation():
    src = Path(idx.__file__).read_text(encoding="utf-8")
    body = re.sub(r"#.*$", "", src, flags=re.M)
    for phrase in ("analysts expect", "{FORWARD_PE}", "forward P/E of"):
        assert phrase not in body, (
            f"the valuation narrative still says {phrase!r} about a number nothing measures"
        )


def test_the_wire_sentinel_is_the_one_shipped_builds_already_read_as_unknown():
    """0 — NOT a new Optional field. `IndexDetailModels.swift` gates on `forwardPE > 0`
    and renders '—', so no client change is needed; making the shipped non-Optional Double
    Optional would crash every build in the field."""
    ios = (Path(idx.__file__).resolve().parents[3] / "frontend" / "ios" / "ios"
           / "Models" / "IndexDetailModels.swift").read_text(encoding="utf-8")
    assert "let forwardPE: Double" in ios, "the field must stay a non-Optional Double"
    assert 'forwardPE > 0 ? String(format: "%.1fx", forwardPE) : "—"' in ios


def test_every_surface_that_renders_forward_pe_reads_0_as_unknown():
    """A sentinel is only honest where EVERY reader knows it is one.

    `forward_pe` has two consumers, and the honesty fix landed on one. `chat_service`
    copies the same value into the Cay AI market-overview widget, whose pill formatted it
    unconditionally — so "Fwd P/E 0.0x" was printed beside a real "P/E (TTM) 24.3x",
    asserting a market forward earnings multiple of zero. Same class as the `*_known` flag
    with a reader that has no neutral state.
    """
    import re

    ios_root = Path(idx.__file__).resolve().parents[3] / "frontend" / "ios" / "ios"
    readers = [
        ios_root / "Models" / "IndexDetailModels.swift",
        ios_root / "Views" / "Molecules" / "ChatMarketOverviewWidget.swift",
    ]
    missing = []
    for path in readers:
        src = path.read_text(encoding="utf-8")
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        src = "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())
        # Every line that FORMATS the value must be guarded by a positivity test.
        for line_no, line in enumerate(src.splitlines(), 1):
            if not re.search(r"forwardPe|forwardPE", line):
                continue
            if "%.1fx" not in line and "format:" not in line:
                continue
            guarded = re.search(r"forwardPe\s*>\s*0|forwardPE\s*>\s*0", src)
            if not guarded:
                missing.append(f"{path.name}:{line_no}")
    assert not missing, (
        "a forward-P/E sentinel of 0 is rendered as a real multiple at: "
        + ", ".join(missing)
    )
    chat = (ios_root / "Views" / "Molecules" / "ChatMarketOverviewWidget.swift").read_text()
    assert "data.forwardPe > 0" in chat, (
        "the Cay AI market-overview pill prints \"Fwd P/E 0.0x\" for an unknown multiple"
    )


# ── F27: sector-aggregate rows only, paged ──────────────────────────────────────────


class _Recorder:
    """Chainable PostgREST stub that records filters and serves 1,000-row pages.

    ⚠️ It SHUFFLES each page by the recorded `order` column, so an un-ordered read is
    visibly wrong here rather than silently fine. The original stub sliced a stable Python
    list, which made `.range()` without `.order()` look correct — the very defect this
    file's paging test claims to pin.
    """

    def __init__(self, rows):
        self.rows, self.eq_calls, self.ranges = rows, [], []
        self.orders = []

    def table(self, _n):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.eq_calls.append((col, val))
        return self

    def order(self, col, desc=False):
        self.orders.append((col, desc))
        self._order = (col, desc)
        return self

    def range(self, a, b):
        self.ranges.append((a, b))
        self._slice = (a, b)
        return self

    def execute(self):
        rows = [r for r in self.rows
                if all(r.get(c) == v for c, v in self.eq_calls if c in r)]
        order = getattr(self, "_order", None)
        if order:
            col, desc = order
            missing = [r for r in rows if r.get(col) is None]
            assert not missing, (
                f"paged read ordered on {col!r}, which the rows do not carry — "
                "an ORDER BY on a missing column is not a total order"
            )
            rows = sorted(rows, key=lambda r: r.get(col), reverse=desc)
        a, b = getattr(self, "_slice", (0, 99999))
        return type("R", (), {"data": rows[a:b + 1]})()


def _with_ids(rows):
    """Stamp the bigint identity PK the pager orders on."""
    for n, r in enumerate(rows):
        r["id"] = n
    return rows


def _rows():
    """11 sector rows at 22.0 for the newest label, buried under 1,500 industry rows at 45.0."""
    out = []
    for q in ("Q1'26", "Q2'26"):
        for i in range(11):
            out.append({"sector": f"S{i}", "industry": "", "period_type": "quarterly",
                        "period_label": q, "median_value": 22.0})
    for i in range(1500):
        out.append({"sector": f"S{i % 11}", "industry": f"Ind{i}", "period_type": "quarterly",
                    "period_label": "Q2'26", "median_value": 45.0})
    return _with_ids(out)


def test_only_sector_aggregate_rows_are_averaged(monkeypatch):
    rec = _Recorder(_rows())
    monkeypatch.setattr(idx, "get_supabase", lambda: rec)
    value = idx._compute_index_pe_from_sectors()
    assert ("industry", "") in rec.eq_calls, "the sector-aggregate filter is missing"
    assert value == 22.0, f"industry rows contaminated the mean: {value}"


def test_the_read_is_paged_past_the_postgrest_clamp(monkeypatch):
    """The newest period is deliberately placed PAST row 1,000.

    Rows come back in heap order, so the newest quarter is not guaranteed to be early —
    and an unpaged read returns an arbitrary first 1,000. Here the 11 newest sector rows
    are the LAST rows in the table: only a paged read can see them.
    """
    rows = [{"sector": f"S{i}", "industry": "", "period_type": "quarterly",
             "period_label": f"Q{(q % 4) + 1}'{10 + q // 16:02d}", "median_value": 30.0}
            for q in range(110) for i in range(11)]          # 1,210 old rows, years '10-'16
    rows += [{"sector": f"S{i}", "industry": "", "period_type": "quarterly",
              "period_label": "Q4'26", "median_value": 99.0} for i in range(11)]
    _with_ids(rows)
    assert len(rows) > 1000
    rec = _Recorder(rows)
    monkeypatch.setattr(idx, "get_supabase", lambda: rec)
    value = idx._compute_index_pe_from_sectors()
    assert len(rec.ranges) > 1, "a single un-paged read silently truncates at 1,000 rows"
    assert value == 99.0, f"the newest period sat past the clamp and was lost: {value}"


def test_an_empty_table_is_none_not_zero(monkeypatch):
    monkeypatch.setattr(idx, "get_supabase", lambda: _Recorder([]))
    assert idx._compute_index_pe_from_sectors() is None


def test_a_period_with_too_few_sectors_is_skipped(monkeypatch):
    rows = [{"sector": f"S{i}", "industry": "", "period_type": "quarterly",
             "period_label": "Q2'26", "median_value": 30.0} for i in range(7)]
    rows += [{"sector": f"S{i}", "industry": "", "period_type": "quarterly",
              "period_label": "Q1'26", "median_value": 20.0} for i in range(9)]
    _with_ids(rows)
    monkeypatch.setattr(idx, "get_supabase", lambda: _Recorder(rows))
    # Q2'26 is newer but has 7 < 8 sectors, so Q1'26 wins — the existing contract.
    assert idx._compute_index_pe_from_sectors() == 20.0


def test_the_query_shape_is_not_asserted_from_prose():
    """Anti-vacuity: the assertions above ride on a stub, so pin that the real call chain
    carries the sector-aggregate filter and a complete read.

    ⚠️ COMMENT-STRIPPED. This asserted `".range(" in body` over the RAW source — and once
    the loop moved to `fetch_all_rows`, the sentence explaining why (".. a hand-rolled
    `.range()` loop ..") satisfied it on its own. The canonical vacuity of
    `.claude/rules/testing.md` §3, reproduced inside the very file that pins this read.
    """
    import re

    src = Path(idx.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_compute_index_pe_from_sectors")
    body = ast.get_source_segment(src, fn)
    code = "\n".join(re.sub(r"#.*$", "", line) for line in body.splitlines())
    assert '.eq("industry", "")' in code, "the sector-aggregate filter is gone"
    assert "fetch_all_rows(" in code, "the read is no longer paged to completion"


def test_the_paged_read_goes_through_the_shared_helper(monkeypatch):
    """Hand-rolled paging is how BOTH defects got in: no `ORDER BY`, and a silent stop at
    20 pages. `fetch_all_rows` makes the order column a required argument and WARNs when it
    hits its own backstop instead of truncating quietly.
    """
    import ast
    import inspect
    import re

    src = inspect.getsource(idx)
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_compute_index_pe_from_sectors")
    body = ast.get_source_segment(src, fn)
    code = "\n".join(re.sub(r"#.*$", "", line) for line in body.splitlines())
    assert "fetch_all_rows(" in code, "the sector-P/E read hand-rolls its paging again"
    assert ".range(" not in code, "a hand-rolled `.range()` loop is back"
    assert 'order_by="id"' in code


def test_the_pager_actually_orders_the_read(monkeypatch):
    """Behavioural twin of the source scan: the stub refuses an unordered page."""
    rec = _Recorder(_rows())
    monkeypatch.setattr(idx, "get_supabase", lambda: rec)
    idx._compute_index_pe_from_sectors()
    assert rec.orders, (
        "the paged read issued no ORDER BY — OFFSET paging without a total order can "
        "return a row on two pages or on neither"
    )
    assert all(col == "id" for col, _desc in rec.orders), rec.orders

