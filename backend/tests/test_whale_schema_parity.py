"""
Whale response ↔ iOS Codable contract — pins the JSON key sets.

Mirrors test_ticker_report_schema_parity.py: each test constructs a minimal
response, dumps it, and asserts the EXACT key set the iOS decoder maps via
CodingKeys (WhaleDTOs.swift / TrackingModels.swift). A failure here means iOS
either loses a field silently (extra key it never mapped) or worse — a rename
the decoder can't find. Fix the schema or the Swift DTO, never the assertion
alone.

Run via `python -m pytest` (no conftest — cwd must be backend/).
"""

from app.schemas.tracking import WhaleTradeItemResponse
from app.schemas.whale import (
    TrendingWhaleResponse,
    WhaleTradeGroupActivityResponse,
)


def test_trending_whale_response_keys_match_ios_dto():
    # iOS: TrendingWhaleDTO.CodingKeys (WhaleDTOs.swift)
    dumped = TrendingWhaleResponse(
        id="w1", name="Ray Dalio", category="investors"
    ).model_dump()
    assert set(dumped.keys()) == {
        "id",
        "name",
        "category",
        "avatar_url",
        "followers_count",
        "is_following",
        "title",
        "description",
        "recent_trade_count",
        "firm_name",
    }


def test_trending_whale_firm_name_optional_and_emitted():
    # Old rows (pre-migration-080 cache) lack firm_name → must default to None
    # but still EMIT the key so the iOS optional decodes deterministically.
    w = TrendingWhaleResponse.model_validate(
        {"id": "w1", "name": "Renaissance Technologies", "category": "institutions"}
    )
    assert w.firm_name is None
    assert "firm_name" in w.model_dump()


def test_activity_response_keys_match_ios_dto():
    # iOS: WhaleTradeGroupActivityDTO.CodingKeys (WhaleDTOs.swift)
    dumped = WhaleTradeGroupActivityResponse(
        id="tg1",
        entity_name="Ray Dalio",
        action="BOUGHT",
        trade_count=3,
        total_amount="$1.2B",
        date="2026-06-30",
    ).model_dump()
    assert set(dumped.keys()) == {
        "id",
        "whale_id",
        "entity_name",
        "entity_avatar_name",
        "entity_firm_name",
        "category",
        "action",
        "trade_count",
        "total_amount",
        "summary",
        "date",
    }


def test_whale_trade_item_keys_match_ios_dto():
    # iOS: WhaleTradeItemDTO.CodingKeys (TrackingModels.swift)
    dumped = WhaleTradeItemResponse(
        ticker="ORCL",
        company_name="Oracle",
        whale_count=1,
        amount="$2.4B",
        raw_amount=2_400_000_000.0,
    ).model_dump()
    assert set(dumped.keys()) == {
        "ticker",
        "company_name",
        "whale_count",
        "amount",
        "raw_amount",
        "raw_amount_low",
        "raw_amount_high",
        "is_congress",
        "lead_whale_id",
        "lead_whale_name",
        "lead_whale_avatar_name",
        "lead_whale_firm",
    }
