"""
Schema-parity test: the GET /stocks/{ticker}/overview snapshot contract.

Pins the contract between the backend Pydantic snapshot models in
app/schemas/stock_overview.py (`SnapshotItemResponse` / `SnapshotMetricResponse`)
and the iOS Swift Codable DTOs in
frontend/ios/ios/Models/StockOverviewResponseModels.swift
(`SnapshotItemDTO` lines 79-89 / `SnapshotMetricDTO` lines 74-77).

Why this exists
---------------
The overview endpoint serializes with `response_model=StockOverviewResponse`
(app/api/v1/endpoints/stocks.py:376). `SnapshotItemResponse` recently gained
`weighted_score`, and `SnapshotMetricResponse` gained `metric_key` + `score` —
all `Optional[...] = None` — to feed the deterministic card-verdict generator
(`card_verdict.generate_card_verdict`) on the *report* path.

The iOS `SnapshotItemDTO` / `SnapshotMetricDTO` do NOT declare those three keys.
That is SAFE today only because `APIClient` (Core/Services/APIClient.swift:56)
decodes with a plain `JSONDecoder()`, and Swift's `Decodable` silently ignores
unknown keys (no `.convertFromSnakeCase`, no unknown-key rejection). No test
pinned this, so a backend rename/drop of an iOS-facing key would ship straight
to a JSONDecoder crash on the stock detail screen.

This test pins both halves of the invariant, serializing through
`model_dump(mode="json")` — the exact encode the FastAPI `response_model` +
the iOS `JSONDecoder` roundtrip performs:
  1. The keys iOS DOES decode are present at the right level
     (item: category / rating / metrics / full_report_available;
      metric: name / value).
  2. The three backend-only extras ARE present in the serialized JSON, so this
     test documents them as fields iOS intentionally ignores — and trips the day
     someone renames/drops one the report path depends on.

If iOS ever adds a Snapshot DTO field mirroring `score` / `metric_key` /
`weighted_score`, it MUST be declared `Optional`: the overview service never
populates these — it builds every snapshot with only category/rating/metrics
(see stock_overview_service.py `_build_*_snapshot`) — so the overview path emits
`null` for all three (verified by test_overview_path_snapshot_emits_none_extras
below). A non-optional iOS field would crash on that null.
"""

from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse


# Keys the iOS SnapshotItemDTO / SnapshotMetricDTO actually decode. Mirrors
# StockOverviewResponseModels.swift. Kept literal so a backend rename trips this
# test before the iOS decoder crashes in production.
_IOS_ITEM_KEYS = {"category", "rating", "metrics", "full_report_available"}
_IOS_METRIC_KEYS = {"name", "value"}

# Backend-only extras the iOS DTOs intentionally do NOT declare (silently ignored
# by the plain JSONDecoder). They feed the report-path card-verdict generator.
_BACKEND_ONLY_ITEM_KEYS = {"weighted_score"}
_BACKEND_ONLY_METRIC_KEYS = {"metric_key", "score"}


def test_overview_snapshot_serializes_ios_keys_and_backend_extras():
    """A fully-populated snapshot (all extras set) serializes the iOS-facing keys
    at the right level AND carries the three backend-only extras — documenting
    them as fields iOS ignores, not a contract iOS must decode."""
    item = SnapshotItemResponse(
        category="Profitability",
        rating=4,
        metrics=[
            # Scored metric — what the report path feeds to card_verdict.
            SnapshotMetricResponse(
                name="Gross Margin",
                value="46.0%",
                metric_key="gross_margin",
                score=4,
            ),
            # Informational metric — extras stay None.
            SnapshotMetricResponse(name="Net Margin", value="24.0%"),
        ],
        full_report_available=True,
        weighted_score=4.2,
    )

    # The exact encode FastAPI's response_model performs before the iOS
    # JSONDecoder reads it.
    dumped = item.model_dump(mode="json")

    # 1. iOS-facing item keys present at the item level.
    assert _IOS_ITEM_KEYS <= set(dumped.keys()), (
        f"missing iOS item keys: {_IOS_ITEM_KEYS - set(dumped.keys())}"
    )
    assert dumped["category"] == "Profitability"
    assert dumped["rating"] == 4
    assert dumped["full_report_available"] is True

    # iOS-facing metric keys present at the metric level.
    for m in dumped["metrics"]:
        assert _IOS_METRIC_KEYS <= set(m.keys()), (
            f"missing iOS metric keys: {_IOS_METRIC_KEYS - set(m.keys())}"
        )
    assert dumped["metrics"][0]["name"] == "Gross Margin"
    assert dumped["metrics"][0]["value"] == "46.0%"

    # 2. Backend-only extras present in the serialized JSON (iOS ignores them).
    assert _BACKEND_ONLY_ITEM_KEYS <= set(dumped.keys())
    assert dumped["weighted_score"] == 4.2
    assert _BACKEND_ONLY_METRIC_KEYS <= set(dumped["metrics"][0].keys())
    assert dumped["metrics"][0]["metric_key"] == "gross_margin"
    assert dumped["metrics"][0]["score"] == 4

    # The informational metric carries the extras as None (present, not omitted).
    assert dumped["metrics"][1]["metric_key"] is None
    assert dumped["metrics"][1]["score"] is None


def test_overview_path_snapshot_emits_none_extras():
    """Mirror how stock_overview_service builds snapshots — only category/rating/
    metrics (no extras) — and assert the three extras serialize as `null`, NOT
    omitted. This is why any future iOS Snapshot DTO mirroring `score` /
    `metric_key` / `weighted_score` MUST be Optional: the overview path always
    sends null for them."""
    item = SnapshotItemResponse(
        category="Financial Health",
        rating=3,
        metrics=[SnapshotMetricResponse(name="Altman Z-Score", value="2.10")],
    )

    dumped = item.model_dump(mode="json")

    # full_report_available defaults True (iOS decodes a non-optional Bool, so it
    # must always be present on the wire).
    assert dumped["full_report_available"] is True
    # The extras are present-with-null on the overview path — never absent.
    assert "weighted_score" in dumped and dumped["weighted_score"] is None
    metric = dumped["metrics"][0]
    assert "metric_key" in metric and metric["metric_key"] is None
    assert "score" in metric and metric["score"] is None
