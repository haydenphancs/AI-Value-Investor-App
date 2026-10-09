"""`scripts/measure_13f_quarter_gaps.py` — the read-only scope measurement for the 13F
quarter-gap finding (2026-10-09: Norges Bank's 2026-Q2 diffed with 2025-Q4 because FMP lists
no 2026-Q1). Hermetic: invented dates lists, a fake FMP, the Supabase tripwire armed."""
from __future__ import annotations

import ast
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import scripts.measure_13f_quarter_gaps as m

_BACKEND = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)      # newest quarter past its deadline: 2026-Q2
_ENDS = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}


def _dates(*yqs):
    return [{"date": f"{y}-{_ENDS[q]}", "year": y, "quarter": q} for (y, q) in yqs]


def _c(*yqs, **kw):
    return m.classify(_dates(*yqs), now=NOW, **kw)


# ── classification ─────────────────────────────────────────────────────────────────────


def test_the_adjacent_quarter_listed_is_a_real_quarter():
    r = _c((2026, 2), (2026, 1), (2025, 4))
    assert (r["status"], r["latest"], r["compared_with"], r["missing_quarters"]) == \
        ("adjacent", "2026-Q2", "2026-Q1", [])
    assert r["notes"] == [] and r["history_holes"] == []


def test_the_norges_shape_is_a_gap_and_is_not_compared():
    r = _c((2026, 2), (2025, 4), (2025, 3))
    assert (r["status"], r["compared_with"], r["missing_quarters"]) == ("gap", None, ["2026-Q1"])
    assert r["adjacent"] == "2026-Q1"


def test_a_gap_across_the_year_boundary_and_a_two_quarter_gap():
    assert _c((2026, 1), (2025, 3))["missing_quarters"] == ["2025-Q4"]
    assert _c((2026, 2), (2025, 3))["missing_quarters"] == ["2025-Q4", "2026-Q1"]


def test_a_lone_filing_is_a_first_filing_and_nothing_usable_is_no_filing():
    r = _c((2026, 2))
    assert (r["status"], r["compared_with"]) == ("first_filing", None)
    assert m.classify([], now=NOW)["status"] == "no_filing"
    bad = m.classify({"error": "x"}, now=NOW)
    assert bad["status"] == "no_filing" and "dates is dict, not a list" in bad["notes"]


def test_an_unsorted_list_is_classified_by_the_newest_quarter():
    # The writers used to take the FIRST earlier entry in list order (2025-Q3 here). The
    # production selector reads the newest quarter whatever the order: a gap at 2026-Q1.
    r = _c((2026, 2), (2025, 3), (2025, 4))
    assert (r["status"], r["missing_quarters"]) == ("gap", ["2026-Q1"])
    assert "order: the list is not newest-first" in r["notes"]


def test_dates0_that_is_not_the_newest_quarter_is_reported():
    r = _c((2026, 1), (2026, 2), (2025, 4))
    assert (r["latest"], r["status"], r["compared_with"]) == ("2026-Q2", "adjacent", "2026-Q1")
    assert any(n.startswith("order: dates[0] is 2026-Q1") for n in r["notes"])


def test_malformed_and_duplicate_rows_are_counted_never_raised():
    rows = _dates((2026, 2), (2026, 1)) + [
        {"date": "2025-12-31", "year": 2025, "quarter": None},   # quarter from its date
        "junk",
        {"year": True, "quarter": 1},                              # a bool is not a year
    ] + _dates((2026, 1))
    r = m.classify(rows, now=NOW)
    assert r["status"] == "adjacent" and r["listed_count"] == 3
    assert r["malformed_rows"] == 2 and r["duplicate_rows"] == 1


def test_a_stale_latest_quarter_and_holes_in_recent_history():
    r = _c((2025, 4), (2025, 3), (2025, 1), (2024, 3))
    assert any(n.startswith("stale: newest listed 2025-Q4 < 2026-Q2") for n in r["notes"])
    assert r["history_holes"] == ["2024-Q4", "2025-Q2"]
    # The window bounds the scan: an old hole outside --history is not counted.
    assert _c((2026, 2), (2026, 1), (2025, 4), (2022, 1), history=3)["history_holes"] == []


def test_the_classifier_is_the_production_selector():
    # The measurement reports what the writers do: the SAME selector and row parser.
    from app.services import _whale_common as wc
    assert m.select_13f_comparison is wc.select_13f_comparison
    assert m.thirteen_f_dates_row_quarter is wc.thirteen_f_dates_row_quarter


# ── read-only guarantees ───────────────────────────────────────────────────────────────


def test_the_tripwire_refuses_every_supabase_use(monkeypatch):
    import app.database as database

    monkeypatch.setattr(database, "_supabase_client", None)
    m.install_supabase_tripwire()
    for name in ("table", "rpc", "storage", "from_", "auth"):
        with pytest.raises(m.SupabaseRefused):
            getattr(database._supabase_client, name)


def test_the_script_has_no_write_path():
    tree = ast.parse((_BACKEND / "scripts" / "measure_13f_quarter_gaps.py").read_text())
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    # Every Supabase read or write starts at `.table(` / `.rpc(` / `.storage` / `.from_(`
    # (a bare `.update` would also match the script's `dict.update`).
    assert not attrs & {"table", "rpc", "storage", "from_"}
    assert not names & {"get_supabase", "sb_exec", "retry_idempotent_sync", "fetch_all_rows"}
    assert {"get_institutional_filing_dates", "get_institutional_holdings"} <= attrs   # anti-vacuity


# ── live mode against a fake FMP ───────────────────────────────────────────────────────


class _FakeFMP:
    def __init__(self, dates_by_cik, extracts=None, fail=None):
        self.dates_by_cik, self.extracts, self.fail = dates_by_cik, extracts or {}, fail or {}
        self.calls = []

    async def get_institutional_filing_dates(self, cik, *, strict=False):
        self.calls.append(("dates", cik, strict))
        if cik in self.fail:
            raise self.fail[cik]
        return self.dates_by_cik[cik]

    async def get_institutional_holdings(self, cik, year, quarter, *, strict=False):
        self.calls.append(("extract", cik, year, quarter, strict))
        return self.extracts.get((cik, year, quarter), [])


def test_the_probe_runs_for_gap_filers_only_and_strictly():
    fmp = _FakeFMP(
        {"A": _dates((2026, 2), (2025, 4)), "B": _dates((2026, 2), (2026, 1))},
        extracts={("A", 2026, 1): [{"symbol": "X"}] * 3},
    )
    filers = [{"cik": "A", "name": "gap"}, {"cik": "B", "name": "adjacent"}]
    out = asyncio.run(m.measure_live(fmp, filers, now=NOW, probe=True, history=12, secret=None))
    assert [r["status"] for r in out] == ["gap", "adjacent"]
    assert out[0]["probe_rows"] == 3 and "probe_rows" not in out[1]
    assert ("extract", "A", 2026, 1, True) in fmp.calls
    assert not any(c[0] == "extract" and c[1] == "B" for c in fmp.calls)
    assert all(c[-1] is True for c in fmp.calls)                  # strict: a failure is not "[]"


def test_a_dates_failure_is_an_error_entry_and_the_key_is_scrubbed():
    key = "live-test-key-0123456789"
    fmp = _FakeFMP({}, fail={"A": RuntimeError(f"GET /stable/x?apikey={key} -> 500 ({key})")})
    out = asyncio.run(m.measure_live(fmp, [{"cik": "A", "name": "n"}], now=NOW, probe=False,
                                     history=12, secret=key))
    assert out[0]["error"].startswith("dates: RuntimeError") and key not in out[0]["error"]
    assert key not in m.render(out)


def test_fixture_mode_end_to_end(tmp_path, monkeypatch, capsys):
    import app.database as database

    monkeypatch.setattr(database, "_supabase_client", None)
    monkeypatch.setattr(m, "configure_logging", lambda secret: None)
    fixture = tmp_path / "f.json"
    fixture.write_text(json.dumps([
        {"cik": "0001374170", "name": "Norges-like", "dates": _dates((2026, 2), (2025, 4)), "probe": 0},
        {"cik": "0000000002", "name": "Normal", "dates": _dates((2026, 2), (2026, 1))},
        {"cik": "0000000003", "name": "Newcomer", "dates": _dates((2026, 2))},
    ]))
    out = tmp_path / "o.json"
    code = asyncio.run(m.main(["--fixture", str(fixture), "--json", str(out)]))
    assert code == 0
    assert isinstance(database._supabase_client, m._SupabaseTripwire)
    text = capsys.readouterr().out
    assert "3 filer(s): 1 adjacent, 1 gap, 1 first_filing, 0 no_filing" in text
    assert "probe: FMP serves 0 extract row(s) for unlisted 2026-Q1" in text
    assert text.index("Norges-like") < text.index("Normal")       # gaps lead the table
    assert [r["status"] for r in json.loads(out.read_text())] == ["gap", "adjacent", "first_filing"]
