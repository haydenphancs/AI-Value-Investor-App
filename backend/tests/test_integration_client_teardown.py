"""Every integration holding a persistent `httpx.AsyncClient` must be closed in the lifespan.

Four clients were leaking their connection pools at shutdown:

  * `openfda.py` and `uspto.py` each shipped a finished, docstring'd `close_*_client()` —
    `\"\"\"Tear-down hook for app.main lifespan.\"\"\"` — that **nothing ever imported**. The
    tear-down was written and simply never wired, which is invisible to any reviewer reading
    the integration file, because the file looks complete.
  * `finra_short_interest.py` holds **two** clients (`_http_client` for the Nasdaq path,
    `_finra_http_client` for the FINRA OAuth path) and had no hook at all.

These are reachable in production, not dormant: `ip_intel_service.py` drives openfda/uspto from
the `refresh_top_tickers` background task, `moat_scoring_service.py`, and
`ticker_report_data_collector.py`.

## Why this test enumerates rather than lists

Naming the four modules found by the audit would pin exactly the four already fixed and nothing
else — the same shape of guard that let `_inflight` cancellation hide in 25 services when an
audit had named 6. So the check DERIVES the set: any integration that constructs an
`httpx.AsyncClient` outside an `async with` is holding it past the call and must be torn down.
A new integration that adds one and forgets the hook fails the build.

`async with httpx.AsyncClient(...)` is the per-call form and needs no closer — that is what
`alternative_me`, `apewisdom`, `census` and `fred` use.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_INTEGRATIONS = _BACKEND / "app/integrations"
_MAIN = _BACKEND / "app/main.py"


def _modules() -> list[Path]:
    if not _INTEGRATIONS.is_dir():
        pytest.skip(f"{_INTEGRATIONS} not present")
    return sorted(p for p in _INTEGRATIONS.glob("*.py") if p.name != "__init__.py")


def _constructs_persistent_client(tree: ast.AST) -> bool:
    """True if `httpx.AsyncClient(...)` is built anywhere outside an `async with` header.

    A client created as an `async with` context manager is closed when the block exits. One
    created anywhere else outlives the call — it is being cached in a module global or on a
    singleton — and therefore needs an explicit closer.
    """
    managed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                for sub in ast.walk(item.context_expr):
                    managed.add(id(sub))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            f"{func.value.id}.{func.attr}"
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            else getattr(func, "id", "")
        )
        if name in ("httpx.AsyncClient", "AsyncClient") and id(node) not in managed:
            return True
    return False


def _close_functions(tree: ast.AST) -> list[str]:
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("close_")
    ]


def _needs_teardown() -> dict[str, list[str]]:
    """module stem → its `close_*` functions, for every module holding a persistent client."""
    out: dict[str, list[str]] = {}
    for path in _modules():
        tree = ast.parse(path.read_text())
        if _constructs_persistent_client(tree):
            out[path.stem] = _close_functions(tree)
    return out


def test_every_persistent_client_module_defines_a_closer():
    missing = {mod: fns for mod, fns in _needs_teardown().items() if not fns}
    assert not missing, (
        f"these integrations hold a persistent httpx.AsyncClient with no close_* function: "
        f"{sorted(missing)}"
    )


def test_every_closer_is_awaited_in_the_lifespan():
    """The openfda/uspto failure mode exactly: the hook exists, and nothing calls it."""
    main_src = _MAIN.read_text()
    unwired: dict[str, list[str]] = {}
    for mod, fns in _needs_teardown().items():
        if not any(re.search(rf"await\s+{re.escape(fn)}\s*\(", main_src) for fn in fns):
            unwired[mod] = fns
    assert not unwired, (
        f"these close_* hooks exist but are never awaited in app/main.py: {unwired}. "
        "An unimported tear-down hook is indistinguishable from a wired one when you are "
        "reading the integration file — which is how these survived."
    )


def test_every_closer_is_also_imported():
    """`await close_x()` on a name that was never imported is a NameError at shutdown, i.e.
    a leak plus a traceback. Cheap to assert, and the import list is the thing a reviewer
    actually scans."""
    main_src = _MAIN.read_text()
    for mod, fns in _needs_teardown().items():
        wired = [fn for fn in fns if re.search(rf"await\s+{re.escape(fn)}\s*\(", main_src)]
        for fn in wired:
            assert re.search(
                rf"from app\.integrations\.{re.escape(mod)} import [^\n]*{re.escape(fn)}",
                main_src,
            ), f"{fn} is awaited in main.py but not imported from app.integrations.{mod}"


def test_the_detector_is_not_vacuous():
    """Anti-vacuity, twice over.

    If `_constructs_persistent_client` silently stopped matching, every assertion above would
    pass on an empty set. Pin both directions against modules whose shape is known: fmp and
    coingecko are the canonical persistent-client integrations, and alternative_me uses the
    per-call `async with` form.
    """
    detected = _needs_teardown()
    assert "fmp" in detected and "coingecko" in detected, (
        f"detector missed the two canonical persistent clients; saw {sorted(detected)}"
    )
    assert "alternative_me" not in detected, (
        "alternative_me uses `async with httpx.AsyncClient(...)` — a per-call client that "
        "needs no closer. Flagging it means the detector cannot tell the two forms apart."
    )
    assert len(detected) >= 5, (
        f"expected at least fmp, coingecko, openfda, uspto and finra_short_interest; "
        f"saw {sorted(detected)}"
    )


@pytest.mark.asyncio
async def test_finra_closer_behaves_under_failure():
    """Behavioural, not source-shape. The source guards above cannot see any of this.

    A closer that propagates would abort the lifespan teardown at whichever `close_*` it
    happens to be, silently skipping every one registered after it in `main.py` — turning one
    integration's problem into four leaked pools. And a naive loop that stops at the first
    exception leaves the OTHER FINRA client open.
    """
    import httpx

    from app.integrations import finra_short_interest as f

    class _Boom(httpx.AsyncClient):
        async def aclose(self):
            raise RuntimeError("simulated close failure")

    # No-op when nothing was ever built (the common case: shutdown before first use).
    await f.close_finra_client()

    # Normal path: both clients closed, both globals reset so a later call rebuilds.
    a, b = await f._get_client(), await f._get_finra_client()
    await f.close_finra_client()
    assert a.is_closed and b.is_closed
    assert f._http_client is None and f._finra_http_client is None

    # One client fails to close — the other must still be closed, and both globals cleared.
    f._http_client = _Boom()
    healthy = await f._get_finra_client()
    await f.close_finra_client()
    assert healthy.is_closed, "a failure on the first client stranded the second"
    assert f._http_client is None and f._finra_http_client is None

    # And the closer itself never propagates.
    f._http_client, f._finra_http_client = _Boom(), _Boom()
    await f.close_finra_client()

    # Builders recover after a close, so nothing is wedged if a request races shutdown.
    revived = await f._get_client()
    assert not revived.is_closed
    await f.close_finra_client()


def test_finra_closes_both_of_its_clients():
    """`finra_short_interest` is the one module with TWO persistent clients. A closer that
    handles only `_http_client` would pass every test above while still leaking the OAuth
    path's pool."""
    src = (_INTEGRATIONS / "finra_short_interest.py").read_text()
    tree = ast.parse(src)
    closer = next(
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("close_")
    )
    body = ast.get_source_segment(src, closer) or ""
    for client in ("_http_client", "_finra_http_client"):
        assert client in body, f"{closer.name} does not close {client}"
