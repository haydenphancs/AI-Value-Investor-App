"""The chart's session axis must match the session the bars actually cover.

Before Phase 4 a commodity screen charted `GCUSD` futures, which trade nearly 24 hours, so
`ChartCoordinateSystem.window(for: .commodity)` was `.roundTheClock` and the backend fetched
the intraday series with `extended_hours=True`. Phase 4 moved every surviving commodity
screen onto either a NYSE Arca ETF (GLD/SLV/PPLT/PALL — equity hours) or an EIA daily spot
print (no intraday bars at all). Nothing round-the-clock is left on that screen, but both
sides kept the old window: GLD's 04:00–20:00 ET bars were drawn on a 00:00–24:00 axis, so
the 1D chart compressed into two-thirds of the width and the "now" cursor sat wrong.

Both halves are pinned here because they must move together — the iOS window and the
backend `extended_hours` flag describe the same session, and `asset_class._ROUND_THE_CLOCK`
is the backend's own statement of which classes trade around the clock.

Source-scan guards go vacuous easily (`.claude/rules/testing.md` §3): comment-stripped
(`//` and `/* */`), brace-bounded, mutation-tested by hand.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import re

from app.services import asset_class
from app.services import commodity_service as cs

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_COORDS = _IOS / "Views" / "Molecules" / "Chart" / "ChartCoordinateSystem.swift"


def _strip_swift_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for raw in src.splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"//.*$", "", raw))
    return "\n".join(out)


def _block(src: str, header: str) -> str:
    i = src.find(header)
    assert i != -1, f"guard is stale — {header!r} not found"
    start = src.find("{", i)
    depth, j = 0, start
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start: j + 1]
        j += 1
    raise AssertionError(f"unbalanced braces after {header!r}")


def _window_body() -> str:
    assert _COORDS.exists(), "guard is stale — ChartCoordinateSystem.swift moved"
    src = _strip_swift_comments(_COORDS.read_text(encoding="utf-8"))
    return _block(src, "static func window(for context: ChartAssetContext)")


# ── iOS ──────────────────────────────────────────────────────────────────────

def test_only_crypto_is_round_the_clock():
    body = _window_body()
    arms = re.findall(r"case\s+([^:]+):\s*return\s+\.(\w+)", body)
    assert arms, "no switch arms found — the guard is bound to the wrong declaration"
    by_window: dict[str, set[str]] = {}
    for cases, window in arms:
        for c in cases.split(","):
            by_window.setdefault(window, set()).add(c.strip())
    assert by_window.get("roundTheClock") == {".crypto"}, by_window
    assert ".commodity" in by_window.get("regular", set()), by_window


def test_the_switch_stays_exhaustive_with_no_default_arm():
    """A new asset class must be a compile error, not an assumed window."""
    assert "default:" not in _window_body()


# ── backend, the other half of the same session ──────────────────────────────

def test_backend_round_the_clock_set_is_crypto_only():
    assert set(asset_class._ROUND_THE_CLOCK) == {"crypto"}
    assert asset_class.trades_extended_hours("commodity") is False
    assert asset_class.symbol_trades_extended_hours("GCUSD") is False
    assert asset_class.symbol_trades_extended_hours("BTCUSD") is True


def test_the_commodity_chart_is_fetched_on_the_equity_session():
    """`_get_chart` only reaches `fetch_chart_data` for ETF-backed refs (FRED returns []
    above it), and those funds trade the regular session."""
    src = inspect.getsource(cs.CommodityService._get_chart)
    tree = ast.parse(src.lstrip() if not src.startswith("def") else src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "fetch_chart_data"]
    assert calls, "guard is stale — `_get_chart` no longer calls fetch_chart_data"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "extended_hours" in kw, "extended_hours must be passed explicitly"
        assert isinstance(kw["extended_hours"], ast.Constant) and kw["extended_hours"].value is False
