"""Guard the unified credit pricing scale (approved 2026-07-26, migration 100).

These pin the per-action credit costs so a stray edit can't silently drift the
scale (e.g. resurrect the old report cost of 5) or unhook the report cost from
its config home. Pure constants — no Supabase, no network.
"""

from app.config import settings
from app.services.credit_service import CreditService


def test_action_credit_costs_are_the_approved_scale():
    # 1 chat turn == 1 credit; a full report == 20 credits (~20x token/$ weight).
    assert settings.CHAT_CREDIT_COST == 1
    assert settings.REPORT_CREDIT_COST == 20


def test_report_cost_is_sourced_from_config():
    # DEEP_RESEARCH_COST must track the single config source, not a stale literal.
    assert CreditService.DEEP_RESEARCH_COST == settings.REPORT_CREDIT_COST == 20


def test_report_is_a_positive_integer_multiple_of_chat():
    # The unified pool relies on report cost being a clean multiple of the atomic
    # chat unit so one credit maps to a roughly constant COGS however it is spent.
    assert isinstance(CreditService.DEEP_RESEARCH_COST, int)
    assert CreditService.DEEP_RESEARCH_COST % settings.CHAT_CREDIT_COST == 0
    assert CreditService.DEEP_RESEARCH_COST > settings.CHAT_CREDIT_COST
