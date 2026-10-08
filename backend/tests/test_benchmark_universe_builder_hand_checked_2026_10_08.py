"""The hand-checked issuer table of `scripts/build_benchmark_universe.py` (2026-10-08).

The US-only rebuild of 2026-10-08 kept rows that are NOT an operating company's common
share, under plain issuer names no name or symbol rule can read. A read-only FMP scan of the
TTM ratios of all 2,971 kept rows found seven same-industry pairs with identical margins,
current ratio, D/E and ROE — the same statements voting twice:

    BNH = BN, CCZ = CMCSA, STRC = MSTR, FWONA = FWONK, RZC = RGA, MGR = AMG, HCXY = HTGC

and the 0.02%-turnover WARNING named the rest (ESRT's three OP-unit listings, TransCanada
PipeLines / KKR Group Finance / Aegon Funding / Maiden Holdings North America notes, BIP
Bermuda Holdings I's preferred, and APXT — a 2025 SPAC FMP serves AvePoint's statements
for). Each was confirmed on its FMP profile (one CIK with the parent). The ELC row
("… COLLATERAL TR MT") is Entergy Louisiana's bonds; the debt-word rule now reads FMP's
abbreviation.

Names marked "captured" are FMP's own strings from that probe. Hermetic: pure functions.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

import scripts.build_benchmark_universe as bu

FLOOR = 500_000_000


def _row(sym: str, name: str, *, industry: str = "Asset Management", cap: Any = 5e9,
         price: Any = 20.0, avg_volume: Any = 1_000_000, **over: Any) -> Dict[str, Any]:
    row = {
        "symbol": sym, "companyName": name, "marketCap": cap,
        "sector": "Financial Services", "industry": industry,
        "price": price, "volume": avg_volume, "avgVolume": avg_volume,
        "exchange": "NYSE", "exchangeShortName": "NYSE", "country": "US",
        "isEtf": False, "isFund": False, "isActivelyTrading": True,
    }
    row.update(over)
    return row


def _kept(rows: List[Dict[str, Any]]) -> List[str]:
    kept, _, _ = bu._filter_rows(rows, FLOOR)
    return sorted(r["symbol"] for r in kept)


# (symbol, FMP companyName — captured 2026-10-08) of every row the table must drop.
_DROPPED = [
    ("MGR", "Affiliated Managers Group, Inc."),
    ("RZC", "Reinsurance Group of America, Incorporated"),
    ("HCXY", "Hercules Capital Inc"),
    ("STRC", "Strategy Inc"),
    ("FWONA", "Formula One Group"),
    ("CCZ", "Comcast Holdings Corp."),
    ("BNH", "Brookfield Finance Inc."),
    ("FISK", "Empire State Realty OP, L.P."),
    ("OGCP", "Empire State Realty OP, L.P."),
    ("ESBA", "Empire State Realty OP, L.P."),
    ("TCPA", "TransCanada PipeLines Limited 6"),
    ("KKRS", "KKR Group Finance Co. IX LLC 4."),
    ("AEFC", "Aegon Funding Company LLC"),
    ("MHNC", "Maiden Holdings North America, Ltd."),
    ("BIPI", "BIP Bermuda Holdings I Limited"),
    ("APXT", "Apex Treasury Corporation Class A"),
    # Added 2026-10-08 after read-only profile probes (names captured from FMP):
    ("CRBD", "Corebridge Financial Inc. 6.375"),           # CRBG's debentures, one CIK
    ("SPME", "Sound Point Meridian Capital, Inc."),        # a CLO fund's security, no statements
    ("RML", "Resolution Minerals Ltd. Sponsored ADR"),     # $13.0B ADR vs A$44.5M RML.AX
    # Added 2026-10-09 after read-only profile + shares-float probes (names captured):
    ("PHOS", "First Phosphate Corp. Sponsored ADR"),       # cap = 10 × FMP's share count
    ("SGLD", "Scorpio Gold Corporation American Depositary Shares"),  # = 20 × it
    # Admitted when the server-side cap filter went; confirmed by profiles (2026-10-09):
    ("TRNI", "Trinity Capital Inc."),                     # TRIN's notes, one CIK
    ("DCBG", "Dime Commercial Bancshares, Inc."),         # Dime security, CUSIP 25432X300
    ("ECCU", "Eagle Point Credit Company Inc."),          # a closed-end fund's notes
    ("ECCV", "Eagle Point Credit Company Inc."),
    ("AOMN", "Angel Oak Mortgage REIT, Inc. 9"),          # AOMR's notes
    ("TVC", "Tennessee Valley Authority"),                # TVA bonds: no stock exists
    ("TVE", "Tennessee Valley Authority PARRS A 2029"),
]

# The operating commons beside them (captured), which must keep their vote.
_KEPT = [
    ("AMG", "Affiliated Managers Group, Inc."),
    ("RGA", "Reinsurance Group of America, Incorporated"),
    ("HTGC", "Hercules Capital, Inc."),
    ("MSTR", "Strategy Inc"),
    ("FWONK", "Liberty Media Corporation"),
    ("CMCSA", "Comcast Corporation"),
    ("BN", "Brookfield Corporation"),
    ("ESRT", "Empire State Realty Trust, Inc."),
    ("TRP", "TC Energy Corporation"),
    ("KKR", "KKR & Co. Inc."),
    ("AEG", "Aegon Ltd."),
    ("BIP", "Brookfield Infrastructure Partners L.P."),
    ("AVPT", "AvePoint, Inc."),
    ("BNT", "Brookfield Wealth Solutions Ltd."),   # its own insurer's statements: votes
    ("CRBG", "Corebridge Financial, Inc."),
    ("TRIN", "Trinity Capital Inc."),
    ("DCOM", "Dime Community Bancshares, Inc."),
    ("AOMR", "Angel Oak Mortgage, Inc."),
    ("EIC", "Eagle Point Income Company Inc."),   # a different issuer key
]


@pytest.mark.parametrize("sym, name", _DROPPED)
def test_each_hand_checked_row_is_dropped(sym, name):
    assert bu._drop_reason(_row(sym, name), FLOOR) == "hand_checked"


@pytest.mark.parametrize("sym, name", _KEPT)
def test_the_operating_commons_keep_their_vote(sym, name):
    assert bu._drop_reason(_row(sym, name), FLOOR) is None


def test_every_entry_is_reachable_from_a_captured_name():
    """A key `_issuer_key` can never produce is a dead entry: each one must be the key of
    a captured FMP name."""
    reachable = {bu._issuer_key(name) for _, name in _DROPPED}
    assert set(bu._HAND_CHECKED_ISSUERS) == reachable


def test_each_exempt_common_shares_its_issuers_key():
    """The exemption is by symbol inside the issuer's key: the common must actually carry
    that key (else the entry drops nothing it should keep — or keeps nothing)."""
    names = dict(_KEPT)
    for key, (common, _) in bu._HAND_CHECKED_ISSUERS.items():
        if common is not None and common != "FWONK":
            assert bu._issuer_key(names[common]) == key, common


def test_a_renamed_fwonk_keeps_the_formula_one_vote():
    """FWONK is exempt although FMP names it "Liberty Media Corporation" today: were it
    renamed "Formula One Group", the entry must still leave one F1 series voting."""
    rows = [
        _row("FWONK", "Formula One Group", industry="Entertainment", price=90.0),
        _row("FWONA", "Formula One Group", industry="Entertainment", avg_volume=100_000),
    ]
    assert _kept(rows) == ["FWONK"]
    assert _kept([_row("FWONK", "Liberty Media Corporation", industry="Entertainment"),
                  _row("FWONA", "Formula One Group", industry="Entertainment")]) == ["FWONK"]


def test_a_whole_note_family_goes_not_just_its_most_liquid_row():
    """Dropping MGR by symbol let its sibling note MGRB win `_one_vote_per_issuer` and vote
    in its place. Mutation: key the table by symbol → MGRB survives."""
    rows = [
        _row("AMG", "Affiliated Managers Group, Inc.", avg_volume=500_000, price=250.0),
        _row("MGR", "Affiliated Managers Group, Inc.", avg_volume=20_000),
        _row("MGRB", "Affiliated Managers Group, Inc.", avg_volume=15_000),
        _row("MGRD", "Affiliated Managers Group, Inc.", avg_volume=10_000),
        _row("MGRE", "Affiliated Managers Group, Inc.", avg_volume=5_000),
    ]
    assert _kept(rows) == ["AMG"]


def test_strategys_preferreds_leave_and_mstr_stays():
    rows = [_row("MSTR", "Strategy Inc", industry="Software - Application", price=300.0)] + [
        _row(s, "Strategy Inc", industry="Software - Application", price=100.0)
        for s in ("STRC", "STRK", "STRF", "STRD")
    ]
    assert _kept(rows) == ["MSTR"]


def test_esrts_op_units_leave_and_esrt_stays():
    rows = [_row("ESRT", "Empire State Realty Trust, Inc.", industry="REIT - Diversified")] + [
        _row(s, "Empire State Realty OP, L.P.", industry="REIT - Diversified")
        for s in ("FISK", "OGCP", "ESBA")
    ]
    assert _kept(rows) == ["ESRT"]


def test_a_reused_symbol_under_a_new_name_is_kept():
    """Keyed by name: a symbol that moves to a new company is never dropped by an old
    entry."""
    assert bu._drop_reason(_row("MGR", "Mega Resources Corp."), FLOOR) is None
    assert bu._drop_reason(_row("APXT", "Apex Technology Holdings, Inc."), FLOOR) is None


def test_a_name_that_merely_starts_like_an_entry_is_kept():
    # Key equality, not a prefix: "Strategy" must not swallow another company.
    assert bu._drop_reason(_row("STRA", "Strategic Education, Inc."), FLOOR) is None
    assert bu._drop_reason(
        _row("XYZ", "Comcast Holdings Spectrum Media Corp."), FLOOR,
    ) is None


def test_hand_checked_is_an_expected_info_drop():
    assert "hand_checked" in bu._EXPECTED_DROP_NOTES
    assert "hand_checked" not in bu._UNEXPECTED_DROP_REASONS


def test_the_drop_label_names_the_row():
    label = bu._drop_label(_row("MGR", "Affiliated Managers Group, Inc."), "hand_checked")
    assert label == 'MGR "Affiliated Managers Group, Inc."'


# ── "Collateral Tr": the debt-word rule reads FMP's cut name ─────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "Entergy Louisiana, LLC COLLATERAL TR MT",            # captured (ELC)
        "Entergy Louisiana, LLC Collateral Trust Mortgage Bonds",
        "Entergy Arkansas, LLC First Mortgage Bonds",         # unchanged
    ],
)
def test_utility_mortgage_bonds_are_not_common(name):
    assert bu._drop_reason(_row("ELC", name, industry="Regulated Electric"), FLOOR) == (
        "not_common_share"
    )


@pytest.mark.parametrize(
    "sym, name",
    [
        ("ETR", "Entergy Corporation"),
        ("TREX", "Trex Company, Inc."),
        ("TRP", "TC Energy Corporation"),
        ("CTRE", "CareTrust REIT, Inc."),
        ("COLL", "Collegium Pharmaceutical, Inc."),
    ],
)
def test_the_abbreviation_does_not_touch_operating_names(sym, name):
    assert bu._drop_reason(_row(sym, name), FLOOR) is None


# ── the 2026-10-08 additions, verified by read-only profile probes ────────────────────


def test_corebridge_votes_once_through_crbg_across_industries():
    """FMP files the debentures under Insurance - Life and the common under Asset
    Management, so no per-industry rule could pair them. Mutation: delete the
    "corebridge financial" entry → CRBD kept."""
    market = {
        "Asset Management": [_row("CRBG", "Corebridge Financial, Inc.", cap=1.54e10,
                                  price=34.45, avg_volume=4.5e6)],
        "Insurance - Life": [_row("CRBD", "Corebridge Financial Inc. 6.375", cap=5.01e8,
                                  industry="Insurance - Life", price=20.88, avg_volume=55_695)],
    }
    result = bu._filter_market(market, 0)
    assert {i: [r["symbol"] for r in rows] for i, rows in result.kept.items()} == {
        "Asset Management": ["CRBG"], "Insurance - Life": []}
    assert result.dropped == {"hand_checked": 1}


@pytest.mark.parametrize("floor", [0, FLOOR, 10**13])
def test_the_new_entries_drop_at_any_floor(floor):
    """`hand_checked` is decided before the cap, so the floor-0 industry file drops them
    too (RML's mispriced $13.0B cap would otherwise lead Other Precious Metals)."""
    for sym, name in (("CRBD", "Corebridge Financial Inc. 6.375"),
                      ("SPME", "Sound Point Meridian Capital, Inc."),
                      ("RML", "Resolution Minerals Ltd. Sponsored ADR")):
        assert bu._drop_reason(_row(sym, name, cap=1.3e10), floor) == "hand_checked"


def test_the_clo_funds_listings_both_leave():
    """SPMC is flagged isFund (dropped as `fund`), SPME is not (dropped by hand)."""
    rows = [_row("SPMC", "Sound Point Meridian Capital Inc", isFund=True, cap=1.7e8),
            _row("SPME", "Sound Point Meridian Capital, Inc.", cap=5.08e8, price=24.75,
                 avg_volume=2_968),
            _row("BLK", "BlackRock, Inc.", cap=1.5e11)]
    kept, dropped, _ = bu._filter_rows(rows, 0)
    assert [r["symbol"] for r in kept] == ["BLK"]
    assert dropped == {"fund": 1, "hand_checked": 1}


# PHOS / SGLD as FMP served them on 2026-10-09: (symbol, name, profile cap, price,
# shares-float outstandingShares, screener avgVolume). Both answer `ratios-ttm` with [].
_MISPRICED_ADRS = [
    ("PHOS", "First Phosphate Corp. Sponsored ADR", 2_387_909_297, 13.27, 17_994_795, 45_471),
    ("SGLD", "Scorpio Gold Corporation American Depositary Shares", 1_054_315_395, 3.48,
     15_148_210, 681_168),
]


@pytest.mark.parametrize("sym, name, cap, price, outstanding, avg_volume", _MISPRICED_ADRS)
def test_the_adrs_priced_on_the_ordinary_count_are_hand_checked(
        sym, name, cap, price, outstanding, avg_volume):
    """The review of 2026-10-09 checked each ADR the server-side cap filter used to hide
    against FMP's OWN share count: PHOS's cap is 10 × it (~$239M real) and SGLD's 20 × it
    (~$53M real; SGN.V's ordinary count). Neither turnover is under the thin line (PHOS
    ~0.025%, SGLD ~0.22% a day), so no WARNING named them: only the table can. Dropped at
    every floor — the floor-0 industry file would carry the inflated cap into its HHI and
    cap-ranked competitor candidates. Mutation: delete either entry → kept."""
    implied = cap / price
    assert implied / outstanding == pytest.approx(round(implied / outstanding), rel=1e-5)
    assert round(implied / outstanding) in (10, 20)
    assert bu._daily_turnover(_row(sym, name, cap=cap, price=price,
                                   avg_volume=avg_volume)) > bu._THIN_DAILY_TURNOVER
    for floor in (0, FLOOR):
        assert bu._drop_reason(_row(sym, name, cap=cap, price=price, avg_volume=avg_volume,
                                    exchangeShortName="NASDAQ", industry="Gold"),
                               floor) == "hand_checked"


def test_the_checked_adrs_with_a_sound_cap_are_not_hand_checked():
    """FOIL's cap agrees with FMP's share count (reviewer probe 2026-10-09), so it keeps
    its row; Resolution Minerals' ordinary (RML.AX) is a dotted foreign listing, never the
    table's business."""
    assert bu._drop_reason(_row("FOIL", "Londian Wason New Energy Tech, Inc. Unsponsored ADR",
                                cap=1.38e9, exchangeShortName="NASDAQ"), FLOOR) is None
    assert bu._drop_reason(_row("RML.AX", "Resolution Minerals Limited"), 0) == "foreign_suffix"


def test_no_other_captured_name_collides_with_the_new_keys():
    """A new key must drop only its own issuer's rows: every OTHER captured name (dropped
    or kept) maps to a different key."""
    new_keys = {"corebridge financial", "sound point meridian capital",
                "resolution minerals sponsored adr", "first phosphate sponsored adr",
                "scorpio gold american"}
    owners = {"CRBD", "CRBG", "SPME", "RML", "PHOS", "SGLD"}
    for sym, name in _DROPPED + _KEPT:
        if sym not in owners:
            assert bu._issuer_key(name) not in new_keys, (sym, name)
