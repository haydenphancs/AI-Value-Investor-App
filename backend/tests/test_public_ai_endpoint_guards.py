"""Every unauthenticated endpoint that can trigger a PAID upstream call must be
rate-limited.

Denial-of-wallet (OWASP LLM10). These five news-enrichment routes took no
`Depends` of any kind — an anonymous caller could POST up to
MAX_ENRICH_ARTICLE_IDS article ids per request, each one a Gemini call, in a
loop. Only `/updates/news/enrich` was guarded; the other five were not.

Signature-level assertions rather than live HTTP: the guard is a FastAPI
dependency, so its presence in the resolved dependant is exactly the property
that matters, and it can't drift without this failing.
"""

import inspect

import pytest

from app.api.v1.endpoints import (
    commodities,
    crypto,
    etfs,
    indices,
    stocks,
    updates,
)

# (module, handler name) for every route that spends Gemini for an anonymous caller.
ENRICH_ROUTES = [
    (stocks, "enrich_stock_news"),
    (crypto, "enrich_crypto_news"),
    (etfs, "enrich_etf_news"),
    (indices, "enrich_index_news"),
    (commodities, "enrich_commodity_news"),
    (updates, "enrich_updates_news"),
]


def _has_rate_limit_dependency(func) -> bool:
    """True when any parameter's default is a FastAPI Depends wrapping one of the
    rate-limit checkers."""
    from app.dependencies import IdentityRateLimitChecker, RateLimitChecker

    for param in inspect.signature(func).parameters.values():
        dep = getattr(param.default, "dependency", None)
        if isinstance(dep, (RateLimitChecker, IdentityRateLimitChecker)):
            return True
    return False


@pytest.mark.parametrize(
    "module,handler",
    ENRICH_ROUTES,
    ids=[f"{m.__name__.rsplit('.', 1)[-1]}.{h}" for m, h in ENRICH_ROUTES],
)
def test_enrich_endpoints_are_rate_limited(module, handler):
    func = getattr(module, handler, None)
    assert func is not None, f"{module.__name__}.{handler} no longer exists"
    assert _has_rate_limit_dependency(func), (
        f"{module.__name__}.{handler} lost its rate limit — an anonymous caller "
        f"can now trigger unbounded paid Gemini enrichment."
    )


def test_report_endpoint_is_rate_limited():
    """The single most expensive unauthenticated surface: ~17 Gemini + ~20 FMP
    calls per cache miss, keyed (ticker, persona) so a ticker loop never hits
    cache."""
    from app.api.v1.endpoints import ticker_report

    assert _has_rate_limit_dependency(ticker_report.get_ticker_report), (
        "GET /stocks/{ticker}/report lost its rate limit"
    )


def test_admin_auth_debug_oracle_stays_removed():
    """It was public and returned `match: server_token == header_token` plus the
    server token's exact length — an unthrottled equality oracle on ADMIN_TOKEN.
    Re-adding it in any form is a regression."""
    from app.api.v1.endpoints import admin

    assert not hasattr(admin, "auth_debug")
    for route in admin.router.routes:
        assert "auth-debug" not in getattr(route, "path", ""), (
            "the public admin-token oracle came back"
        )
