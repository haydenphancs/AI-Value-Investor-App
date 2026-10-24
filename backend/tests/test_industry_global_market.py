"""The Moat row's published GLOBAL market estimates (owner decisions 2026-10-24).

`app/services/industry_global_market.py` turns the pinned government-document figures in
`backend/data/industry_global_markets.json` (chips, chip equipment, advertising, digital
advertising) into the card's 2026 → 2031 pair. These tests pin the arithmetic with hand-computed
values, the extension cap, the quotes behind every committed number, every guard that must
return None (never raise), and the fail-closed load of a missing or bad table.
"""

from __future__ import annotations

import copy
import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

import app.services.industry_global_market as glob
from app.services.industry_tam_service import INDUSTRY_TO_CENSUS

_CHIPS = "Semiconductors"
_EQUIP = "Semiconductor Equipment & Materials"
_ADS = "Advertising Agencies"
_DIGITAL = "Internet Content & Information"
_FOUR = (_CHIPS, _EQUIP, _ADS, _DIGITAL)
_TABLE = Path(glob._DATA_PATH)
_PROJ_KEYS = {"projection_basis", "projection_year", "projection_tam", "projection_target_year",
              "projection_target_tam", "projection_rate_pct", "projection_source_label",
              "projection_year_label", "projection_target_year_label"}


@pytest.fixture(autouse=True)
def _fresh_table():
    glob.reset_global_table()
    yield
    glob.reset_global_table()


def _row(industry, start=2026):
    return glob.project_global_market(industry=industry, start_year=start)


def _committed() -> dict:
    return json.loads(_TABLE.read_text(encoding="utf-8"))


def _point_at(monkeypatch, tmp_path, text: str) -> None:
    path = tmp_path / "industry_global_markets.json"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(glob, "_DATA_PATH", path)
    glob.reset_global_table()


# ── Hand-computed values (start 2026) ─────────────────────────────────────────────────

@pytest.mark.parametrize("industry, start_b, target_b, rate", [
    # EC: $1.0T in 2026 → $1.6T in 2030 (+12.47%/yr); 2031 one more year at that rate.
    (_CHIPS, 1000.0, round(1600.0 * 1.6 ** 0.25, 1), 12.5),                  # 1799.5
    # JRC: $113B in 2024 at 9.7%/yr to 2029; 2030–31 at the same rate.
    (_EQUIP, round(113.0 * 1.097 ** 2, 1), round(113.0 * 1.097 ** 7, 1), 9.7),  # 136.0 → 216.0
    # US ITC: $1.1T in 2024 at 7.5%/yr to 2029; 2030–31 at the same rate.
    (_ADS, round(1100.0 * 1.075 ** 2, 1), round(1100.0 * 1.075 ** 7, 1), 7.5),  # 1271.2 → 1825.0
    # 60% of it, at the same rate.
    (_DIGITAL, round(660.0 * 1.075 ** 2, 1), round(660.0 * 1.075 ** 7, 1), 7.5),  # 762.7 → 1095.0
])
def test_the_four_rows_match_hand_computed_values(industry, start_b, target_b, rate):
    row = _row(industry)
    p = row.projection
    assert (p.basis, p.start_year, p.target_year) == ("global_estimate", 2026, 2031)
    assert (p.start_tam_b, p.target_tam_b, p.rate_pct) == (start_b, target_b, rate)


def test_the_cards_figures_the_owner_will_see():
    """The plan's table, as the card prints it: $1.0T → $1.8T, ≈$136B → ≈$216B, ≈$1.27T → ≈$1.83T,
    ≈$0.76T → ≈$1.10T."""
    got = {i: (_row(i).projection.start_tam_b, _row(i).projection.target_tam_b) for i in _FOUR}
    assert got == {_CHIPS: (1000.0, 1799.5), _EQUIP: (136.0, 216.0),
                   _ADS: (1271.2, 1825.0), _DIGITAL: (762.7, 1095.0)}


def test_the_rows_header_scope_and_response_fields():
    row = _row(_CHIPS)
    fields = row.response_fields()
    assert set(fields) == _PROJ_KEYS | {"market_size_label", "scope_note"}
    assert fields["market_size_label"] == "Global chip market"
    assert fields["scope_note"] == "Worldwide semiconductor sales, all regions."
    assert (fields["projection_year_label"], fields["projection_target_year_label"]) == ("2026e", "2031e")
    labels = {i: _row(i).market_size_label for i in _FOUR}
    assert labels == {_CHIPS: "Global chip market", _EQUIP: "Global chip equipment",
                      _ADS: "Global advertising market", _DIGITAL: "Global digital ad market"}
    assert "excludes materials" in _row(_EQUIP).scope_note   # the FMP industry says "& Materials"


def test_the_pdf_source_label_credits_the_document_and_indicates_the_change():
    chips = _row(_CHIPS).projection.source_label
    assert chips == ("Global estimate: European Commission, Chips Act 2.0 proposal COM(2026) 504 "
                     "(Jun 2026), citing SIA and McKinsey, +12.5%/yr; 2031 extends the 2026–30 rate")
    equip = _row(_EQUIP).projection.source_label
    assert "JRC141323" in equip and "CC BY 4.0" in equip and "citing TechInsights" in equip
    assert equip.endswith("+9.7%/yr; 2030–31 extend the 2024–29 rate")
    digital = _row(_DIGITAL).projection.source_label
    assert "Pub. 5643" in digital and "citing Statista" in digital
    assert "digital = 60% of global ad revenue" in digital
    # The CC BY change note always survives whole (review 2026-10-24: a length check after the
    # [:240] cut could never fail).
    for industry in _FOUR:
        for start in (2026, 2027):
            label = _row(industry, start).projection.source_label
            assert label.endswith(" rate") and " extend" in label.rsplit(";", 1)[1], (industry, start, label)


def test_no_extension_note_while_the_target_is_a_stated_year(monkeypatch, tmp_path):
    doc = _committed()
    doc["industries"][_CHIPS]["segments"][0]["to_year"] = 2031
    _point_at(monkeypatch, tmp_path, json.dumps(doc))
    assert "extend" not in _row(_CHIPS).projection.source_label


# ── The path: interpolation, extension, the cap ────────────────────────────────────────

def test_a_later_start_moves_along_the_path():
    p = _row(_CHIPS, 2027).projection
    assert (p.start_year, p.target_year) == (2027, 2032)
    assert p.start_tam_b == round(1000.0 * 1.6 ** 0.25, 1)                  # 1124.7
    assert p.target_tam_b == round(1600.0 * 1.6 ** 0.5, 1)                  # 2 years past 2030
    assert p.source_label.endswith("2031–32 extend the 2026–30 rate")


def test_interpolation_hits_every_stated_point_exactly():
    for industry, year, value in ((_CHIPS, 2030, 1600.0), (_CHIPS, 2026, 1000.0),
                                  (_EQUIP, 2024, 113.0), (_ADS, 2024, 1100.0)):
        values, _, _ = glob._path(_committed()["industries"][industry], 3)
        assert math.isclose(values[year], value, rel_tol=1e-12), (industry, year)


@pytest.mark.parametrize("industry, last_ok_start", [(_CHIPS, 2028), (_EQUIP, 2027), (_ADS, 2027),
                                                     (_DIGITAL, 2027)])
def test_a_target_past_the_extension_cap_is_no_row(industry, last_ok_start, caplog):
    """At most 3 years past the source's last stated year (2030 / 2029): beyond that the card
    falls back to the US row and the WARNING asks for the yearly source re-check."""
    assert _row(industry, last_ok_start) is not None
    caplog.set_level(logging.WARNING, logger=glob.__name__)
    assert _row(industry, last_ok_start + 1) is None
    assert _row(industry, last_ok_start + 1) is None
    warns = [r for r in caplog.records if "extension_cap" in r.getMessage() or "re-check" in r.getMessage()]
    assert len(warns) == 1                                     # once per (industry, reason)


def test_a_start_before_the_sources_first_year_is_no_row(caplog):
    caplog.set_level(logging.WARNING, logger=glob.__name__)
    assert _row(_CHIPS, 2025) is None
    assert any("before the source's first year" in r.getMessage() for r in caplog.records)
    assert _row(_ADS, 2024) is not None and _row(_ADS, 2024).projection.start_tam_b == 1100.0


@pytest.mark.parametrize("start", [None, "2026", 2026.0, True, 1900, 2200, float("nan")])
def test_a_bad_start_year_is_no_row(start):
    assert _row(_CHIPS, start) is None


@pytest.mark.parametrize("industry", ["Software - Application", "Software - Infrastructure",
                                      "Telecom Services", "Aerospace & Defense", "", None, 42,
                                      "semiconductors"])
def test_every_other_industry_has_no_global_row(industry):
    assert _row(industry) is None


def test_the_global_industries_are_exactly_the_four():
    assert glob.global_market_industries() == frozenset(_FOUR)


def test_results_are_finite_and_never_negative_zero():
    for industry in _FOUR:
        for start in (2024, 2025, 2026, 2027):
            row = _row(industry, start)
            if row is None:
                continue
            p = row.projection
            assert all(math.isfinite(v) and v > 0 for v in (p.start_tam_b, p.target_tam_b))
            assert math.copysign(1.0, p.rate_pct) == 1.0


def test_a_falling_market_prints_a_minus_sign(monkeypatch, tmp_path):
    doc = _committed()
    doc["industries"][_ADS]["segments"][0]["rate_pct"] = -2.0
    _point_at(monkeypatch, tmp_path, json.dumps(doc))
    p = _row(_ADS).projection
    assert p.rate_pct == -2.0 and "−2.0%/yr" in p.source_label and p.target_tam_b < p.start_tam_b


# ── The committed table: every number carries its source's words ───────────────────────

def test_the_committed_table_is_valid():
    assert glob.validate_global_table(_committed()) == []


@pytest.mark.parametrize("industry, where, value, words", [
    (_CHIPS, "anchor", 1000.0, "USD 1 trillion by 2026"),
    (_CHIPS, "segment", 1600.0, "USD 1.6 trillion by 2030"),
    (_EQUIP, "anchor", 113.0, "reaching $113bn"),
    (_EQUIP, "segment", 9.7, "CAGR 2024-29 of 9.7%"),
    (_EQUIP, "check", 180.0, "$180bn by 2029"),
    (_ADS, "anchor", 1100.0, "$1.1 trillion in 2024"),
    (_ADS, "segment", 7.5, "around 7.5 percent in 2025–29"),
    (_DIGITAL, "anchor", 660.0, "roughly 60 percent of global advertising revenue"),
    (_DIGITAL, "segment", 7.5, "around 7.5 percent in 2025–29"),
])
def test_every_committed_figure_sits_beside_the_words_that_state_it(industry, where, value, words):
    """A number edited without its quote (or a quote without its number) fails here."""
    ind = _committed()["industries"][industry]
    seg = ind["segments"][0]
    block, figure = {
        "anchor": (ind["anchor"], ind["anchor"]["value_b"]),
        "segment": (seg, seg.get("end_value_b", seg.get("rate_pct"))),
        "check": (seg, seg.get("check_end_value_b")),
    }[where]
    assert figure == value
    assert words in block["quote"]


def test_the_digital_row_is_marked_derived_with_its_arithmetic():
    ind = _committed()["industries"][_DIGITAL]
    assert ind["anchor"]["kind"] == "derived" and ind["segments"][0]["kind"] == "derived"
    assert ind["anchor"]["value_b"] == pytest.approx(0.60 * _committed()["industries"][_ADS]["anchor"]["value_b"])
    assert "60%" in ind["anchor"]["derivation"]


def test_every_source_states_its_licence_and_what_it_cites():
    for name, src in _committed()["sources"].items():
        assert src["url"].startswith("https://") and src["licence_url"].startswith("https://"), name
        assert src["cites"].strip() and src["credit"].strip(), name
    assert "commercial" in _committed()["sources"]["ec_com_2026_504"]["licence_quote"]
    assert "CC BY 4.0" in _committed()["sources"]["jrc_141323"]["licence_quote"]


def test_every_committed_industry_is_one_the_report_produces():
    assert set(_committed()["industries"]) <= set(INDUSTRY_TO_CENSUS)


# ── The table: fail closed ─────────────────────────────────────────────────────────────

def test_a_missing_table_disables_every_row_with_one_error(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(glob, "_DATA_PATH", tmp_path / "missing.json")
    glob.reset_global_table()
    caplog.set_level(logging.ERROR, logger=glob.__name__)
    assert all(_row(i) is None for i in _FOUR)
    assert glob.global_market_industries() == frozenset()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "GLOBAL MARKETS DISABLED" in errors[0].getMessage()


@pytest.mark.parametrize("text", ["{not json", json.dumps({"x": 1}), "[]", ""])
def test_a_corrupt_table_disables_every_row(monkeypatch, tmp_path, caplog, text):
    _point_at(monkeypatch, tmp_path, text)
    caplog.set_level(logging.ERROR, logger=glob.__name__)
    assert _row(_CHIPS) is None
    assert any("GLOBAL MARKETS DISABLED" in r.getMessage() for r in caplog.records)


def test_a_nan_literal_or_a_duplicate_key_disables_every_row(monkeypatch, tmp_path):
    raw = _TABLE.read_text(encoding="utf-8")
    _point_at(monkeypatch, tmp_path, raw.replace('"value_b": 1000.0', '"value_b": NaN', 1))
    assert _row(_CHIPS) is None and _row(_ADS) is None
    _point_at(monkeypatch, tmp_path, raw.replace('"schema_version": 1,', '"schema_version": 1, "schema_version": 1,', 1))
    assert _row(_CHIPS) is None


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(extra=1),
    lambda d: d.update(schema_version=2),
    lambda d: d.update(reviewed_on="24 Oct 2026"),
    lambda d: d.update(horizon_years=0),
    lambda d: d.update(max_extension_years=9),
    lambda d: d.update(max_extension_years=True),
    lambda d: d["sources"]["jrc_141323"].pop("credit"),
    lambda d: d["sources"]["jrc_141323"].update(url="http://publications.jrc.ec.europa.eu"),
    lambda d: d["sources"]["usitc_5643"].update(licence_quote="  "),
    lambda d: d["industries"][_CHIPS].update(label="Global semiconductor industry total market"),
    lambda d: d["industries"][_CHIPS].update(scope_note="x" * 101),
    lambda d: d["industries"][_CHIPS].update(source="wsts"),
    lambda d: d["industries"][_CHIPS].pop("scope_note"),
    lambda d: d["industries"][_CHIPS].update(colour="red"),
    lambda d: d["industries"][_CHIPS]["anchor"].update(value_b=-1000.0),
    lambda d: d["industries"][_CHIPS]["anchor"].update(value_b=True),
    lambda d: d["industries"][_CHIPS]["anchor"].update(year="2026"),
    lambda d: d["industries"][_CHIPS]["anchor"].pop("quote"),
    lambda d: d["industries"][_CHIPS]["anchor"].update(kind="estimated"),
    lambda d: d["industries"][_CHIPS]["anchor"].update(derivation="x"),          # stated + derivation
    lambda d: d["industries"][_DIGITAL]["anchor"].pop("derivation"),           # derived, no how
    lambda d: d["industries"][_CHIPS].update(segments=[]),
    lambda d: d["industries"][_CHIPS]["segments"][0].update(to_year=2026),     # not after the anchor
    lambda d: d["industries"][_CHIPS]["segments"][0].update(rate_pct=12.0),    # both end and rate
    lambda d: d["industries"][_ADS]["segments"][0].pop("rate_pct"),            # neither
    lambda d: d["industries"][_CHIPS]["segments"][0].update(end_value_b=16000.0),  # ≈ +78%/yr
    lambda d: d["industries"][_ADS]["segments"][0].update(rate_pct=float("inf")),
    lambda d: d["industries"][_EQUIP]["segments"][0].update(check_end_value_b=250.0),  # rate ≠ stated end
    lambda d: d["industries"][_CHIPS]["segments"][0].update(check_end_value_b=1600.0),  # check on an end segment
    lambda d: d["industries"][_CHIPS]["segments"].append({"to_year": 2029, "rate_pct": 5.0,
                                                          "kind": "stated", "quote": "q"}),  # goes backwards
])
def test_an_invalid_table_disables_every_row(monkeypatch, tmp_path, mutate):
    doc = copy.deepcopy(_committed())
    mutate(doc)
    assert glob.validate_global_table(doc), "the mutation must be caught by validate_global_table"
    _point_at(monkeypatch, tmp_path, json.dumps(doc))
    assert all(_row(i) is None for i in _FOUR)


def test_an_unknown_industry_name_disables_only_that_row(monkeypatch, tmp_path, caplog):
    doc = _committed()
    doc["industries"]["Semiconductor"] = copy.deepcopy(doc["industries"][_CHIPS])   # a typo
    _point_at(monkeypatch, tmp_path, json.dumps(doc))
    caplog.set_level(logging.ERROR, logger=glob.__name__)
    assert _row("Semiconductor") is None
    assert _row(_CHIPS) is not None
    assert "Semiconductor" not in glob.global_market_industries()
    assert any("DISABLED for 'Semiconductor'" in r.getMessage() for r in caplog.records)


def test_a_stale_table_logs_an_error_and_still_serves(monkeypatch, tmp_path, caplog):
    doc = _committed()
    doc["reviewed_on"] = "2020-01-01"
    _point_at(monkeypatch, tmp_path, json.dumps(doc))
    caplog.set_level(logging.ERROR, logger=glob.__name__)
    assert _row(_CHIPS) is not None
    assert any("GLOBAL MARKETS STALE" in r.getMessage() for r in caplog.records)


def test_staleness_starts_18_months_after_the_review_on_the_eastern_date(caplog):
    """548 days after `reviewed_on` (ET): not stale on day 548, stale on day 549 — and 03:00 UTC
    on day 549 is still day 548 in New York."""
    caplog.set_level(logging.ERROR, logger=glob.__name__)
    reviewed = datetime.strptime(_committed()["reviewed_on"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    from datetime import timedelta

    for now, stale in ((reviewed + timedelta(days=548, hours=12), False),
                       (reviewed + timedelta(days=549, hours=3), False),     # 23:00 ET the day before
                       (reviewed + timedelta(days=549, hours=12), True)):
        glob.reset_global_table()
        caplog.clear()
        assert glob._load(now=now) is not None                              # served either way
        assert any("STALE" in r.getMessage() for r in caplog.records) is stale, now


def test_the_committed_table_is_not_stale_today(caplog):
    """Fails ~18 months after `reviewed_on`: the yearly source re-check (OWNER_TASKS.md) is due."""
    caplog.set_level(logging.ERROR, logger=glob.__name__)
    assert _row(_CHIPS) is not None
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_an_arithmetic_failure_is_no_row_and_logged(monkeypatch, caplog):
    def boom(ind, max_ext):
        raise OverflowError("too big")
    monkeypatch.setattr(glob, "_path", boom)
    caplog.set_level(logging.ERROR, logger=glob.__name__)
    assert _row(_CHIPS) is None
    assert any("Global market row failed for 'Semiconductors'" in r.getMessage() for r in caplog.records)


# ── Review round 2026-10-24 ────────────────────────────────────────────────────────────

def test_a_credit_too_long_for_the_source_line_is_refused():
    """A credit lengthened at the yearly re-check must not cut the change note off the PDF line."""
    doc = copy.deepcopy(_committed())
    doc["sources"]["usitc_5643"]["credit"] = ("U.S. International Trade Commission, Recent Trends in "
                                              "U.S. Services Trade: 2025 Annual Report, Pub. 5643 (Jul 2025), "
                                              "citing Statista Market Insights, Advertising: Market data & analysis")
    problems = glob.validate_global_table(doc)
    assert any("change note would be cut" in p and _DIGITAL in p for p in problems), problems
    assert glob.validate_global_table(_committed()) == []
    longest = max(glob._longest_source_label(_committed(), ind, 3)
                  for ind in _committed()["industries"].values())
    assert longest <= glob._SOURCE_LABEL_MAX


def test_the_longest_label_bound_really_is_the_longest():
    doc = _committed()
    for industry in _FOUR:
        bound = glob._longest_source_label(doc, doc["industries"][industry], doc["max_extension_years"])
        for start in range(2020, 2035):
            row = glob.estimate_with_table(doc, industry=industry, start_year=start)
            if row is not None:
                assert len(row.projection.source_label) <= bound, (industry, start)


def test_a_start_past_the_range_is_reported_as_the_extension_cap_not_before_the_source(caplog):
    caplog.set_level(logging.WARNING, logger=glob.__name__)
    assert _row(_ADS, 2033) is None
    messages = [r.getMessage() for r in caplog.records]
    assert any("re-check the source" in m for m in messages)
    assert not any("before the source's first year" in m for m in messages)


def test_the_warn_once_memo_is_per_industry(caplog):
    """At the 2028 start three rows fall back in one process: each is named once (review: a
    reason-only key named only the first)."""
    caplog.set_level(logging.WARNING, logger=glob.__name__)
    for industry in (_EQUIP, _ADS, _DIGITAL, _EQUIP, _ADS):
        assert _row(industry, 2028) is None
    warns = [r.getMessage() for r in caplog.records if "re-check the source" in r.getMessage()]
    assert len(warns) == 3
    for industry in (_EQUIP, _ADS, _DIGITAL):
        assert sum(industry in m for m in warns) == 1, industry


@pytest.mark.parametrize("start", [None, "2026", 2026.0, True])
def test_a_start_that_is_not_a_year_is_logged(start, caplog):
    caplog.set_level(logging.WARNING, logger=glob.__name__)
    assert _row(_CHIPS, start) is None
    assert any("is not a year" in r.getMessage() for r in caplog.records)


def test_a_review_date_in_the_future_is_an_error_and_the_committed_one_is_not(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.ERROR, logger=glob.__name__)
    doc = _committed()
    doc["reviewed_on"] = "2062-10-24"
    _point_at(monkeypatch, tmp_path, json.dumps(doc))
    assert _row(_CHIPS) is not None
    assert any("is in the future" in r.getMessage() for r in caplog.records)
    reviewed = datetime.strptime(_committed()["reviewed_on"], "%Y-%m-%d").date()
    assert reviewed <= datetime.now(timezone.utc).date()


def test_the_jrc_licence_record_keeps_its_third_party_carve_out():
    quote = _committed()["sources"]["jrc_141323"]["licence_quote"]
    assert "not owned by the European Union permission must be sought" in quote
    assert "TechInsights" in _committed()["sources"]["jrc_141323"]["cites"]


# ── The shared all-or-nothing test (PDF, chat; iOS mirrors it) ──────────────────────────

def _stored_row(**over):
    md = {"tam_basis": "measured", "tam_year": 2024, "current_tam": 116.2,
          **_row(_CHIPS).response_fields()}
    md.update(over)
    return md


def test_a_freshly_built_global_row_is_complete():
    for industry in _FOUR:
        assert glob.is_complete_global_row({"tam_basis": "measured", **_row(industry).response_fields()})


@pytest.mark.parametrize("over", [
    {"tam_basis": "company_quote"}, {"tam_basis": None}, {"projection_basis": "bls_output"},
    {"projection_year": "2026"}, {"projection_year": 2026.0}, {"projection_year": True},
    {"projection_target_year": 2026}, {"projection_target_year": 2201},
    {"projection_tam": 0}, {"projection_tam": float("nan")}, {"projection_tam": "1000"},
    {"projection_target_tam": None}, {"projection_target_tam": float("inf")},
    {"projection_rate_pct": None}, {"projection_rate_pct": float("nan")}, {"projection_rate_pct": "12.5"},
    {"projection_rate_pct": 1000.1}, {"projection_rate_pct": True},
    {"market_size_label": None}, {"market_size_label": "  "}, {"market_size_label": 7},
    {"projection_source_label": None}, {"projection_source_label": ""},
])
def test_a_damaged_global_row_is_not_complete(over):
    assert not glob.is_complete_global_row(_stored_row(**over))


def test_not_a_dict_is_not_a_global_row():
    for value in (None, [], "row", 3):
        assert glob.is_complete_global_row(value) is False


def test_the_rate_bound_is_the_pdfs():
    from app.services.pdf_report_service import _GROWTH_PCT_MAX_ABS

    assert glob.GLOBAL_RATE_MAX_ABS == _GROWTH_PCT_MAX_ABS


def test_every_global_row_still_draws_this_year():
    """Goes red on the first ET day a committed row would fall back to the US figure (1 Jan 2028
    for chip equipment / advertising / digital ads unless the yearly source re-check moved the
    table forward) — the staleness ERROR alone would come ~4 months later."""
    from app.services.industry_tam_projection import current_year_et

    year = current_year_et()
    assert all(_row(industry, year) is not None for industry in _FOUR), year
