"""PDF report disclosure guards.

Two legal-facing invariants of the exported (shareable) PDF, plus the NaN guard
that both of them depend on:

1. The full disclaimer must render in the document body. The ``@page`` footer
   carries a one-line notice, but the PDF leaves the app via the share sheet with
   every verdict on it, so the complete text has to travel with it.
2. The "Fair Value" hero card must state which basis it actually used. It prefers
   the Wall Street analyst consensus target and falls back to our own DCF-derived
   estimate; labeling the latter as analyst consensus misattributes our own number
   to third parties.
3. ``_num`` must treat NaN/Inf as ABSENT. A non-finite value renders as "$nan" and
   silently defeats the margin-of-safety comparisons (NaN is truthy, and both
   ``nan >= 1`` and ``nan <= -1`` are False, so it would land on "Fairly Valued").

Pure render path — no network, no Supabase, no WeasyPrint (that import is lazy and
lives in ``render_pdf_bytes``, which these tests never call).
"""

import math

from app.services.pdf_report_service import build_context, render_html

_WALL_STREET_LABEL = "Per Wall Street consensus"

# Substrings that only appear if a non-finite number reached a formatter. A bare
# "nan"/"inf" search is useless here — "fiNANcial", "INFormation" etc. match.
_NONFINITE_RENDERS = ("$nan", "nan%", "$inf", "inf%", ">nan<", ">inf<",
                      "$-inf", "-inf%", "$nan.", "nan.0")


def _assert_no_nonfinite(html: str) -> None:
    low = html.lower()
    for bad in _NONFINITE_RENDERS:
        assert bad not in low, f"non-finite value reached the PDF as {bad!r}"


def _data(*, ws_target=None, current_price=100.0, **extra):
    """Minimal frozen-report dict. build_context is documented as tolerant of
    missing fields, so we supply only what these assertions exercise."""
    d = {
        "company_name": "Example Corp",
        "symbol": "EXMP",
        "quality_score": 70,
        "price_action": {"current_price": current_price},
        "wall_street_consensus": {"target_price": ws_target},
    }
    d.update(extra)
    return d


# ── 1. Disclaimer ─────────────────────────────────────────────────────────────

def test_disclaimer_default_text_renders_in_body():
    ctx = build_context(_data())
    assert ctx["disclaimer"], "context must always carry a disclaimer"
    html = render_html(ctx)
    assert "Important disclaimer" in html
    assert "not investment advice" in html.lower()


def test_disclaimer_uses_report_supplied_text_when_present():
    sentinel = "Educational use only. Consult a qualified financial advisor."
    ctx = build_context(_data(disclaimer_text=sentinel))
    assert ctx["disclaimer"] == sentinel
    assert sentinel in render_html(ctx)


# ── 2. Fair-value attribution ─────────────────────────────────────────────────

def test_basis_is_wall_street_when_analyst_target_present():
    ctx = build_context(_data(ws_target=150.0), fair_value_estimate=120.0)
    assert ctx["fair_value"] == 150.0, "analyst target must win over our estimate"
    assert ctx["fair_value_basis"] == _WALL_STREET_LABEL
    assert _WALL_STREET_LABEL in render_html(ctx)


def test_basis_credits_caydex_when_no_analyst_target():
    """The regression this test exists for: falling back to our own DCF while the
    card still claimed Wall Street consensus."""
    ctx = build_context(_data(ws_target=None), fair_value_estimate=120.0)
    assert ctx["fair_value"] == 120.0
    assert "Caydex" in ctx["fair_value_basis"]
    html = render_html(ctx)
    assert _WALL_STREET_LABEL not in html, (
        "our own estimate must never be attributed to Wall Street consensus"
    )


def test_basis_is_empty_when_no_value_available_at_all():
    ctx = build_context(_data(ws_target=None), fair_value_estimate=None)
    assert ctx["fair_value"] is None
    assert ctx["fair_value_basis"] == ""
    assert _WALL_STREET_LABEL not in render_html(ctx)


def test_zero_analyst_target_is_not_treated_as_a_target():
    """A $0 price target is nonsense data, not a valuation — fall through."""
    ctx = build_context(_data(ws_target=0.0), fair_value_estimate=120.0)
    assert ctx["fair_value"] == 120.0
    assert "Caydex" in ctx["fair_value_basis"]


# ── 3. Non-finite guards ──────────────────────────────────────────────────────

def test_nan_analyst_target_falls_back_instead_of_rendering_nan():
    ctx = build_context(_data(ws_target=float("nan")), fair_value_estimate=120.0)
    assert ctx["fair_value"] == 120.0
    assert "Caydex" in ctx["fair_value_basis"]
    _assert_no_nonfinite(render_html(ctx))


def test_nan_everywhere_degrades_honestly_not_to_fairly_valued():
    """The dangerous shape: NaN is truthy, so an unguarded NaN would produce a
    fair value of NaN AND a confident 'Fairly Valued' verdict."""
    ctx = build_context(
        _data(ws_target=float("nan"), current_price=float("nan")),
        fair_value_estimate=float("nan"),
    )
    assert ctx["fair_value"] is None
    assert ctx["current_price"] is None
    assert ctx["margin_of_safety_pct"] is None
    assert ctx["valuation_word"] == "—", "must not claim a valuation verdict"
    _assert_no_nonfinite(render_html(ctx))


def test_infinite_values_are_treated_as_absent():
    ctx = build_context(
        _data(ws_target=float("inf")), fair_value_estimate=float("-inf")
    )
    assert ctx["fair_value"] is None
    assert ctx["valuation_word"] == "—"
    _assert_no_nonfinite(render_html(ctx))


def test_num_coercion_edge_cases():
    from app.services.pdf_report_service import _num

    assert _num("123.45") == 123.45          # numeric strings still coerce
    assert _num(0) == 0.0                    # zero is a real value, not absent
    assert _num(-5) == -5.0
    assert _num(None) is None
    assert _num("") is None
    assert _num("n/a") is None
    assert _num(float("nan")) is None
    assert _num(float("inf")) is None
    assert _num(float("-inf")) is None
    assert _num(True) is None, "bool must not silently become 1.0"
    assert _num(False) is None
    assert _num([1]) is None
    assert _num({}) is None


def test_valuation_word_boundaries():
    """+/-1% is the dead band around fair value."""
    def word(fv, price):
        return build_context(_data(ws_target=fv, current_price=price))["valuation_word"]

    assert word(150.0, 100.0) == "Undervalued"
    assert word(50.0, 100.0) == "Overvalued"
    assert word(100.0, 100.0) == "Fairly Valued"
    assert word(100.5, 100.0) == "Fairly Valued"   # inside the band
    assert word(101.5, 100.0) == "Undervalued"     # outside it
    assert word(98.5, 100.0) == "Overvalued"


def test_zero_current_price_does_not_divide_by_zero():
    ctx = build_context(_data(ws_target=150.0, current_price=0.0))
    assert ctx["margin_of_safety_pct"] is None
    assert ctx["valuation_word"] == "—"


def test_render_survives_a_completely_empty_report():
    """Worst case: nothing but an empty dict. Must render, not raise."""
    html = render_html(build_context({}))
    assert "Important disclaimer" in html
    assert math.isfinite(1.0)  # sanity
