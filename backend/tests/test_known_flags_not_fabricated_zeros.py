"""Three more non-Optional wire floats now carry a companion `*_known` bool.

The rebuild's invariant is "an unknown number is None, never 0.0" — but three floats iOS
decodes as plain `Double` (a shipped build cannot decode a null) kept publishing 0.0 for
an unknown, and the client rendered it as a fact:

  * `SectorIndustryResponse.sector_performance` — "Sector Performance +0.00%" in green
    when the sector had no matching row (< 5 members, or a name that does not normalise);
  * `TrackedAssetResponse.change_percent` / `.price` — a Tracking row at "+0.00%" (green)
    beside Home hiding the same tile, when `price_service` said the change was unknown;
  * `IndexDetailResponse.price_change_percent` (and the core/quote slices) — "+0.00%"
    under a live badge when the proxy quote carried no change.

Same pattern as `pe_known` / `dividend_yield_known`: the float stays, the bool says
whether it means anything, iOS renders "—" when False. Pinned at the BUILDERS.
"""
from __future__ import annotations

import pytest

from app.services.stock_overview_service import StockOverviewService
from app.schemas.stock_overview import SectorIndustryResponse
from app.schemas.tracking import TrackedAssetResponse
from app.schemas.index import IndexCoreResponse, IndexDetailResponse, IndexQuoteResponse


# ── sector performance ───────────────────────────────────────────────────────

def _sector(profile_sector="Technology", rows=None, industry_rows=None):
    svc = object.__new__(StockOverviewService)
    return svc._build_sector_industry(
        {"sector": profile_sector, "industry": "Software"}, rows or [], industry_rows or []
    )


def test_an_unmatched_sector_is_marked_unknown_not_a_flat_day():
    out = _sector(rows=[{"sector": "Energy", "changesPercentage": 1.4}])
    assert out.sector_performance == 0.0
    assert out.sector_performance_known is False


def test_an_empty_snapshot_is_unknown():
    assert _sector(rows=[]).sector_performance_known is False


def test_a_matched_sector_is_known_even_when_genuinely_flat():
    out = _sector(rows=[{"sector": "Technology", "changesPercentage": 0.0}])
    assert out.sector_performance_known is True and out.sector_performance == 0.0


def test_a_matched_sector_carries_its_value():
    out = _sector(rows=[{"sector": "Technology", "changesPercentage": -1.23}])
    assert (out.sector_performance, out.sector_performance_known) == (-1.23, True)


def test_the_flag_defaults_true_for_older_cached_shapes():
    assert SectorIndustryResponse(sector="x", industry="y", sector_performance=1.0,
                                  industry_rank="--").sector_performance_known is True


# ── tracking ─────────────────────────────────────────────────────────────────

def test_tracked_asset_flags_default_true_and_are_settable():
    a = TrackedAssetResponse(ticker="AAPL", company_name="Apple", price=1.0, change_percent=0.5)
    assert a.price_known is True and a.change_known is True
    b = TrackedAssetResponse(ticker="AAPL", company_name="Apple", price_known=False, change_known=False)
    assert b.price == 0.0 and b.change_percent == 0.0
    assert b.price_known is False and b.change_known is False


def test_the_tracking_builder_marks_an_unknown_change():
    """Brace-bound: the flags are computed from the same Nones the floats fold."""
    import inspect
    from app.services import tracking_service as ts
    src = inspect.getsource(ts.TrackingService)
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    i = code.index("price_known = price_f is not None")
    assert "change_known = change_pct is not None" in code[i:i + 200]
    j = code.index("TrackedAssetResponse(", i)
    assert "price_known=price_known" in code[j:j + 900] and "change_known=change_known" in code[j:j + 900]
    k = code.index("TrackedAssetResponse(", j + 10)          # the exception arm
    assert "price_known=False" in code[k:k + 400] and "change_known=False" in code[k:k + 400]


# ── index ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model", [IndexCoreResponse, IndexDetailResponse, IndexQuoteResponse])
def test_index_change_known_defaults_true(model):
    assert model.model_fields["change_known"].default is True


def test_index_core_builder_marks_an_unknown_change():
    import inspect
    from app.services import index_service as isv
    src = inspect.getsource(isv.IndexService)
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    core = code.index("return IndexCoreResponse(")
    assert "change_known = change_pct is not None" in code[core - 400:core]
    assert "change_known=change_known" in code[core:core + 400]
    detail = code.index("response = IndexDetailResponse(")
    assert "change_known = change_pct is not None" in code[detail - 6000:detail]
    assert "change_known=change_known" in code[detail:detail + 500]
    quote = code.index("return IndexQuoteResponse(")
    assert "change_known=full.change_known" in code[quote:quote + 500]


# ── iOS: the Tracking row honours `change_known` (source-scan, brace-bound) ─────
#
# `PriceChangeLabel` derives sign, colour and arrow from `changePercent`, so the row must
# hand the flag to the LABEL rather than pre-mangle the percent (the toggle guard in
# `test_ios_holdings_change_toggle.py` pins `changePercent: asset.changePercent` verbatim).
# `isKnown: false` must gate every reader — an unknown move renders as an em dash with no
# arrow in neutral colour, exactly like a NaN. Mutation-tested: dropping `isKnown &&`
# from `isFinite`, or the `isKnown:` argument from the row, each fails one assertion.

import re
from pathlib import Path

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"


def _strip_swift_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _swift_block(path: Path, header: str) -> str:
    src = path.read_text()
    start = src.find(header)
    assert start != -1, f"{header!r} not found in {path.name} — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return _strip_swift_comments(src[open_brace:i + 1])
    pytest.fail(f"unbalanced braces after {header!r}")


def test_the_label_gates_every_reader_on_is_known():
    body = _swift_block(_IOS / "Views/Atoms/PriceChangeLabel.swift", "struct PriceChangeLabel")
    assert re.search(r"var isKnown:\s*Bool\s*=\s*true", body), "isKnown must default true for the other call sites"
    m = re.search(r"var isFinite:\s*Bool\s*\{([^}]*)\}", body)
    assert m and "isKnown" in m.group(1), (
        "isFinite no longer reads isKnown — colour, arrow and text all key off isFinite"
    )
    # Every reader keys off isFinite (one gate), so an unknown move cannot leak through one.
    for reader in ("formattedChange", "color"):
        blk = re.search(rf"var {reader}:[^{{]*\{{(.*?)\n    \}}", body, re.S)
        assert blk and "guard isFinite" in blk.group(1), f"{reader} does not gate on isFinite"


def test_the_tracking_row_passes_the_flag_to_the_label():
    body = _swift_block(_IOS / "Views/Molecules/AssetRow.swift", "struct AssetRow")
    call = re.search(r"PriceChangeLabel\((.*?)\n\s*\)", body, re.S)
    assert call, "AssetRow no longer renders PriceChangeLabel"
    args = call.group(1)
    assert "changePercent: asset.changePercent" in args
    assert "isKnown: asset.changeKnown" in args, "the row renders +0.00% for an unknown move"
    assert ".nan" not in args, "pre-mangling the percent bypasses the label's own guard"


def test_the_tracked_asset_flags_decode_leniently_and_default_true():
    body = _swift_block(_IOS / "Models/TrackingModels.swift", "struct TrackedAssetDTO")
    assert re.search(r"let priceKnown:\s*Bool\?", body)
    assert re.search(r"let changeKnown:\s*Bool\?", body)
    assert 'case priceKnown = "price_known"' in body
    assert 'case changeKnown = "change_known"' in body
    assert "priceKnown ?? true" in body and "changeKnown ?? true" in body
    model = _swift_block(_IOS / "Models/TrackingModels.swift", "struct TrackedAsset:")
    assert "guard changeKnown" in model and "guard priceKnown" in model
