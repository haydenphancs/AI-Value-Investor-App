"""Regression guard for the LLM↔database boundary (OWASP LLM06 — Excessive Agency).

The security audit's strongest finding was a POSITIVE: every function-calling tool the
chat/research agent can invoke resolves to a read-only FMP/cache call — the model has NO
path to Supabase, raw SQL, the filesystem, or user data. This test PINS that invariant so a
future edit can't quietly hand the LLM a database (or shell/filesystem) path.

Two assertions:
  1. Static — the tool-handler modules never reference a DB/shell/filesystem primitive.
  2. Behavioral — the built handler set is EXACTLY the read-only allowlist (no surprise tool).
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from app.services.agents import chat_tools, fmp_tools


# Tokens that would indicate a tool can reach the database, a shell, or the filesystem.
_FORBIDDEN = (
    "supabase", "get_supabase", ".rpc(", ".table(", ".execute(",
    "psycopg", "sqlalchemy", "asyncpg",
    "subprocess", "import os", "os.system", "os.environ", "os.popen",
    "open(", "eval(", "exec(",
)


def _assert_no_forbidden(module):
    src = inspect.getsource(module).lower()
    hits = [tok for tok in _FORBIDDEN if tok.lower() in src]
    assert not hits, (
        f"{module.__name__} references DB/shell/filesystem primitive(s) {hits} — "
        "an LLM tool must never reach the database or the host. If this is intentional, "
        "the security boundary changed and this test must be reconsidered deliberately."
    )


def test_chat_tools_module_has_no_db_or_shell_path():
    _assert_no_forbidden(chat_tools)


def test_fmp_tools_module_has_no_db_or_shell_path():
    _assert_no_forbidden(fmp_tools)


def test_chat_tool_handler_set_is_exactly_the_readonly_allowlist():
    handlers = chat_tools.build_chat_tool_handlers(MagicMock())
    assert set(handlers.keys()) == {
        "get_stock_chart_data",
        "get_analyst_analysis",
        "get_sentiment_analysis",
        "get_market_overview",
    }


def test_research_tool_handler_set_is_exactly_the_readonly_allowlist():
    handlers = fmp_tools.build_tool_handlers(MagicMock())
    assert set(handlers.keys()) == {
        "fetch_quarterly_financials",
        "fetch_dividend_history",
        "fetch_sector_performance",
        "fetch_more_news",
        "fetch_extended_financials",
        "research_complete",
    }
