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


def test_basis_labels_a_bare_estimate_as_a_model_value():
    """The regression this test exists for: falling back to a DCF while the card still
    claimed Wall Street consensus. A bare `fair_value_estimate` (no Caydex block) is FMP's
    model — it used to be labelled "Caydex estimate", which misattributed it too."""
    ctx = build_context(_data(ws_target=None), fair_value_estimate=120.0)
    assert ctx["fair_value"] == 120.0
    assert ctx["fair_value_basis"] == "DCF model value · not a price target"
    assert "Caydex" not in ctx["fair_value_basis"]
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
    assert "not a price target" in ctx["fair_value_basis"]


# ── 3. Non-finite guards ──────────────────────────────────────────────────────

def test_nan_analyst_target_falls_back_instead_of_rendering_nan():
    ctx = build_context(_data(ws_target=float("nan")), fair_value_estimate=120.0)
    assert ctx["fair_value"] == 120.0
    assert "not a price target" in ctx["fair_value_basis"]
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
    """The gap is PRICE vs fair value, in neutral words, with a ±0.5 % "in line" band. It was
    Undervalued / Overvalued / Fairly Valued at ±1 % until 2026-09-25 (hard rule 4)."""
    def word(fv, price):
        return build_context(_data(ws_target=fv, current_price=price))["valuation_word"]

    assert word(150.0, 100.0) == "Price 33% below target"
    assert word(50.0, 100.0) == "Price 100% above target"
    assert word(100.0, 100.0) == "Price in line with target"
    assert word(100.4, 100.0) == "Price in line with target"   # inside the band
    assert word(101.5, 100.0) == "Price 1% below target"       # outside it
    assert word(98.5, 100.0) == "Price 2% above target"


def test_zero_current_price_does_not_divide_by_zero():
    ctx = build_context(_data(ws_target=150.0, current_price=0.0))
    assert ctx["margin_of_safety_pct"] is None
    assert ctx["valuation_word"] == "—"


def test_render_survives_a_completely_empty_report():
    """Worst case: nothing but an empty dict. Must render, not raise."""
    html = render_html(build_context({}))
    assert "Important disclaimer" in html
    assert math.isfinite(1.0)  # sanity


# ── 4. The Caydex Fair Value Estimate (model dcf-v1) ──────────────────────────

def _with_caydex(**est):
    d = _data(ws_target=None)
    d.setdefault("wall_street_consensus", {})
    d["wall_street_consensus"]["caydex_fair_value"] = {
        "symbol": "TEST", "status": "ok", "fair_value": 150.0,
        "range_low": 120.0, "range_high": 180.0, **est,
    }
    return d


def test_the_caydex_estimate_is_labelled_with_its_range(monkeypatch):
    from app.services import pdf_report_service as pdf
    monkeypatch.setattr(pdf.settings, "DCF_ENABLED", True)
    ctx = build_context(_with_caydex(), fair_value_estimate=150.0)
    assert ctx["fair_value"] == 150.0
    assert ctx["fair_value_basis"].startswith("Caydex Fair Value Estimate")
    assert "not a price target" in ctx["fair_value_basis"]
    assert "not a recommendation" in ctx["fair_value_basis"]
    assert "$120–$180" in ctx["fair_value_basis"]
    assert ctx["valuation_word"].endswith("the estimate")


def test_the_kill_switch_drops_a_stored_estimate_from_the_pdf(monkeypatch):
    """DCF_ENABLED off after reports were generated with it on: the PDF must stop showing it."""
    from app.services import pdf_report_service as pdf
    monkeypatch.setattr(pdf.settings, "DCF_ENABLED", False)
    ctx = build_context(_with_caydex(), fair_value_estimate=None)
    assert ctx["fair_value"] is None and "Caydex" not in ctx["fair_value_basis"]


def test_a_refused_caydex_estimate_is_not_a_value(monkeypatch):
    from app.services import pdf_report_service as pdf
    monkeypatch.setattr(pdf.settings, "DCF_ENABLED", True)
    # a non-null fair_value on a REFUSED block: the status check must be what excludes it
    ctx = build_context(_with_caydex(status="refused", fair_value=150.0), fair_value_estimate=None)
    assert ctx["fair_value"] is None and ctx["valuation_word"] == "—"


def test_no_verdict_words_on_any_basis(monkeypatch):
    """Hard rule 4: the hero states a gap, never Undervalued / Overvalued / Fairly Valued."""
    from app.services import pdf_report_service as pdf
    monkeypatch.setattr(pdf.settings, "DCF_ENABLED", True)
    cases = [
        build_context(_with_caydex(), fair_value_estimate=150.0),
        build_context(_data(ws_target=None), fair_value_estimate=120.0),
        build_context(_data(ws_target=150.0), fair_value_estimate=None),
    ]
    for ctx in cases:
        html = render_html(ctx).lower()
        for word in ("undervalued", "overvalued", "fairly valued", "margin of safety"):
            assert word not in html, word
        assert ctx["valuation_word"].startswith("Price ")
