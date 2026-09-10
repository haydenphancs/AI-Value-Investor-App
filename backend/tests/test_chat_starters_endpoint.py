"""`GET /api/v1/chat/starters` — auth, shape, and the impersonality ban.

The two source scans at the bottom are the important half. This route is globally
cached — ONE entry serves every caller — so an innocuous-looking "personalise it" edit
inside the service would hand one user's data to whoever asks next. That cannot be
caught by exercising the endpoint, because it would look correct for the caller who
triggered the build.
"""

import ast
import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_chat_identity
from app.main import app
from app.services.chat_starters_service import ChatStartersService

_SERVICE_SRC = (
    Path(__file__).resolve().parents[1] / "app" / "services" / "chat_starters_service.py"
)
_PATH = "/api/v1/chat/starters"


@pytest.fixture
def client():
    # NOT `with TestClient(app)` — the context manager runs the lifespan, whose startup
    # jobs reach Supabase, and conftest blocks that.
    logging.disable(logging.CRITICAL)
    try:
        yield TestClient(app)
    finally:
        logging.disable(logging.NOTSET)


@pytest.fixture(autouse=True)
def _reset_caches():
    ChatStartersService._pool_cache = None
    ChatStartersService._response_cache = None
    ChatStartersService._pool_inflight = None
    ChatStartersService._response_inflight = None
    yield
    ChatStartersService._pool_cache = None
    ChatStartersService._response_cache = None
    ChatStartersService._pool_inflight = None
    ChatStartersService._response_inflight = None


# ── auth ─────────────────────────────────────────────────────────────────────


def test_an_unauthenticated_caller_is_refused(client):
    """FMP End-User Display Rights permit their data only through an authenticated
    platform, and the live slots name real tickers (`.claude/rules/auth.md` §1a)."""
    response = client.get(_PATH)
    assert response.status_code == 401, response.text


def test_the_route_declares_its_own_dependency():
    """`chat.py`'s router carries no blanket dependency, so an author who forgets one
    ships an open FMP-derived route. Fail-closed is not automatic in this module."""
    route = next(r for r in app.routes if getattr(r, "path", None) == _PATH)
    names = {d.call.__name__ for d in route.dependant.dependencies if getattr(d, "call", None)}
    assert "get_chat_identity" in names, f"dependencies were {names}"


# ── shape ────────────────────────────────────────────────────────────────────


def test_an_authenticated_caller_gets_a_full_row(client, monkeypatch):
    import app.integrations.apewisdom as apewisdom
    import app.services.chat_starters_service as svc_mod
    import app.services.market_movers_service as movers_mod
    import app.services.push_dispatch_service as push_mod

    class _Movers:
        async def get_scanner_inputs(self):
            return {}, {}

        async def get_sector_performance(self):
            return []

    async def _mentions():
        return {}

    def _no_db():
        raise RuntimeError("no database in tests")

    monkeypatch.setattr(movers_mod, "get_market_movers_service", lambda: _Movers())
    monkeypatch.setattr(apewisdom, "get_all_mentions", _mentions)
    monkeypatch.setattr(push_mod, "trading_date_et", lambda: "2026-09-10")
    monkeypatch.setattr(svc_mod, "get_supabase", _no_db)

    app.dependency_overrides[get_chat_identity] = lambda: {"id": "u1", "is_guest": False}
    try:
        response = client.get(_PATH)
    finally:
        app.dependency_overrides.pop(get_chat_identity, None)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["trading_date"] == "2026-09-10"
    # Degrades to evergreen-only with every live source empty and no database — and is
    # still a complete row. That is the contract.
    assert len(body["global_starters"]) == 8
    texts = [c["text"] for c in body["global_starters"]]
    assert "What tickers are hot today?" in texts
    assert len(texts) == len(set(texts)), "duplicate chips collapse in the iOS ForEach"
    for scope in ("ticker", "etf", "crypto", "commodity", "index"):
        assert len(body["detail_starters"][scope]) == 4
        assert all("{symbol}" in t for t in body["detail_starters"][scope])


# ── the impersonality ban ────────────────────────────────────────────────────


def _service_ast() -> ast.Module:
    return ast.parse(_SERVICE_SRC.read_text(encoding="utf-8"))


def test_the_service_never_reads_the_caller():
    """One cache entry serves everyone, so a per-user input is a cross-user leak.

    Checked on the AST rather than the raw text so the prose above — which necessarily
    uses the words "watchlist" and "tier" to explain the ban — cannot make this pass
    vacuously. Only real identifiers and attribute/subscript names count.
    """
    banned = {"user", "user_id", "watchlist", "portfolio", "holdings", "tier", "credits"}
    hits: set[str] = set()
    for node in ast.walk(_service_ast()):
        if isinstance(node, ast.Name) and node.id in banned:
            hits.add(node.id)
        elif isinstance(node, ast.arg) and node.arg in banned:
            hits.add(node.arg)
        elif isinstance(node, ast.Attribute) and node.attr in banned:
            hits.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A table/column name reached through supabase.table("watchlist_items")
            if node.value in banned or node.value.startswith(("watchlist", "portfolio")):
                hits.add(node.value)
    assert not hits, (
        f"chat_starters_service reads per-caller data: {sorted(hits)}. This response is "
        "cached ONCE for every caller — a per-user slot would be served to whoever asks "
        "next. Personalisation here needs a separate per-user-keyed cache."
    )


def test_the_service_never_touches_the_pro_gated_signals():
    """`signals_v3` tickers are masked PER REQUEST by `redact_signals()`.

    A globally cached body carrying one would show a Free user the ticker the paywall
    hides — the redaction happens after the gather in `get_dashboard`, never inside the
    cache, so reading the cache directly yields UNREDACTED symbols.
    """
    imported: set[str] = set()
    for node in ast.walk(_service_ast()):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
    offenders = {name for name in imported if "signals" in name or "redact" in name}
    assert not offenders, f"signals must stay out of the globally cached starters: {offenders}"


def test_the_impersonality_scans_are_not_vacuous():
    """Both scans above must fail on a real violation, not just on an empty file."""
    source = _SERVICE_SRC.read_text(encoding="utf-8")
    assert len(source) > 4000, "service unexpectedly small — the scans may be trivial"

    # The banned words DO appear in this module's prose. If the scan matched raw text
    # instead of the AST it would already be red, so this proves the AST walk is what is
    # doing the work.
    assert re.search(r"\bwatchlist\b", source), (
        "the ban's rationale should be written down in the service; if this ever stops "
        "being true, re-check that the scan is still AST-based and not text-based"
    )

    # And prove the walk actually detects an assignment it should reject.
    tree = ast.parse("def f(user_id):\n    return user_id\n")
    found = {n.arg for n in ast.walk(tree) if isinstance(n, ast.arg)}
    assert "user_id" in found
