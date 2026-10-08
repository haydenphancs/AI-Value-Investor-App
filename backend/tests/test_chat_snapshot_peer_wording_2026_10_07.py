"""Cay AI's snapshot block names an INDUSTRY median as the industry's (2026-10-07).

Snapshot metric names keep the words "sector avg" / "vs sector" on the wire (shipped iOS
builds strip the suffix by the word "sector"), and `peer_level` says whose median it is.
`chat_service` passes the names to the model verbatim, so an industry median was called a
"sector avg" while the 1.1 app's own context said "industry". Hermetic: pure formatting.
"""

from __future__ import annotations

import pytest

from app.schemas.stock_overview import SnapshotMetricResponse
from app.services.chat_service import _peer_worded_metric_name


@pytest.mark.parametrize("name, level, expected", [
    ("P/E (1.30x sector avg 22.4)", "industry", "P/E (1.30x industry avg 22.4)"),
    ("P/E (1.30x sector avg 22.4)", "sector", "P/E (1.30x sector avg 22.4)"),
    ("P/E (1.30x sector avg 22.4)", None, "P/E (1.30x sector avg 22.4)"),
    ("Current Ratio (vs sector 1.62)", "industry", "Current Ratio (vs industry 1.62)"),
    ("Net Margin (1.2x sector average 9.8%)", "industry", "Net Margin (1.2x industry average 9.8%)"),
    ("Return on Equity (ROE)", "industry", "Return on Equity (ROE)"),
    ("", "industry", ""),
])
def test_an_industry_median_is_named_as_the_industrys(name, level, expected):
    metric = SnapshotMetricResponse(name=name, value="1", peer_level=level)
    assert _peer_worded_metric_name(metric) == expected


def test_an_object_without_the_field_keeps_its_name():
    class _Old:
        name = "P/S (0.78x sector avg 0.32)"
    assert _peer_worded_metric_name(_Old()) == "P/S (0.78x sector avg 0.32)"


# ── An unrated card is "not rated", never "0/5" (round-3 review, 2026-10-07) ─────

from types import SimpleNamespace  # noqa: E402

from app.services import chat_service as cs  # noqa: E402


def _snap(category, rating, *metrics):
    return SimpleNamespace(category=category, rating=rating, metrics=[
        SnapshotMetricResponse(name=n, value=v, peer_level=lvl) for n, v, lvl in metrics
    ])


@pytest.mark.asyncio
async def test_an_unrated_bank_health_card_is_not_rated_and_still_counts_as_data(monkeypatch):
    snaps = {
        "profitability": _snap("Profitability", 4, ("ROE", "12%", None)),
        "growth": None,
        "valuation": None,
        "health": _snap("Financial Health", 0, ("Debt to Equity (vs sector 1.20)", "0.95", "industry")),
        "ownership": None,
    }

    def _svc(key, method):
        async def _get(_t):
            return snaps[key]
        return lambda: SimpleNamespace(**{method: _get})

    import app.services.growth_snapshot_service as g
    import app.services.health_snapshot_service as h
    import app.services.ownership_snapshot_service as o
    import app.services.profitability_snapshot_service as p
    import app.services.valuation_snapshot_service as v
    monkeypatch.setattr(p, "get_profitability_snapshot_service", _svc("profitability", "get_profitability_snapshot"))
    monkeypatch.setattr(g, "get_growth_snapshot_service", _svc("growth", "get_growth_snapshot"))
    monkeypatch.setattr(v, "get_valuation_snapshot_service", _svc("valuation", "get_valuation_snapshot"))
    monkeypatch.setattr(h, "get_health_snapshot_service", _svc("health", "get_health_snapshot"))
    monkeypatch.setattr(o, "get_ownership_snapshot_service", _svc("ownership", "get_ownership_snapshot"))
    svc = cs.ChatService.__new__(cs.ChatService)
    out = await svc._get_snapshot_summary("JPM")
    assert "Financial Health: not rated (too few comparable metrics)." in out
    assert "(0/5)" not in out and "Unknown" not in out
    assert "Debt to Equity (vs industry 1.20): 0.95" in out
    assert "Profitability: Solid (4/5)." in out


def test_a_summary_with_only_unrated_categories_is_still_data():
    only_unrated = "Snapshots for JPM: Financial Health: not rated (too few comparable metrics). D/E: 0.95."
    assert cs.ChatService._snapshot_summary_has_data(only_unrated)
    assert not cs.ChatService._snapshot_summary_has_data("Snapshots for JPM: none available.")
