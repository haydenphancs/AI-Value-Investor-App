"""The commodity profile card must describe the instrument whose price it shows.

Phase 4 moved the four metal screens onto physically-backed ETFs (GLD/SLV/PPLT/PALL) and
the two energy screens onto EIA spot prints served by FRED. The header price was relabelled
(the phase's load-bearing rule: "the label must name the instrument whose number is shown")
— the PROFILE CARD underneath was not:

  * the backend still sent `unit: "share"` into an iOS switch with no `"share"` case, so the
    Unit row fell through `default:` to `.contract` and read **"per contract"** under a fund
    share price;
  * the metal profiles still carried COMEX / NYMEX, futures trading hours, "100 troy ounces"
    and a $0.10 tick under a NYSE Arca ETF, and CL/NG carried NYMEX futures metadata over a
    daily spot settlement.

Two contracts, pinned from both sides:
  1. PARITY — every `unit` value the backend can emit has an explicit `case` in the iOS
     switch, so `default:` is unreachable from our own backend.
  2. HONESTY — no metal profile names a futures venue or contract, and the iOS card hides
     the Contract Size / Tick Size rows when the backend sends them empty (a fund share has
     no contract; the alternative is inventing an oz-per-share figure).

Source-scan guards go vacuous easily (`.claude/rules/testing.md` §3): comment-stripped
(`//` and `/* */`), brace-bounded to the declaration under test, mutation-tested by hand.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from app.services import commodity_service as cs

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_RESPONSE_MODELS = _IOS / "Models" / "CommodityDetailResponseModels.swift"
_MODELS = _IOS / "Models" / "CommodityDetailModels.swift"
_PROFILE_SECTION = _IOS / "Views" / "Organisms" / "CommodityDetailProfileSection.swift"


def _strip_swift_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for raw in src.splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"//.*$", "", raw))
    return "\n".join(out)


def _src(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path} moved"
    return _strip_swift_comments(path.read_text(encoding="utf-8"))


def _block(src: str, header: str) -> str:
    i = src.find(header)
    assert i != -1, f"guard is stale — {header!r} not found"
    start = src.find("{", i)
    assert start != -1, f"no opening brace after {header!r}"
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


def _unit_switch() -> str:
    body = _block(_src(_RESPONSE_MODELS), "func toModel() -> CommodityProfile")
    i = body.find("let resolvedUnit")
    assert i != -1, "guard is stale — `resolvedUnit` moved out of toModel()"
    return _block(body[i:], "switch")


# ── anti-vacuity ─────────────────────────────────────────────────────────────

def test_the_stripper_actually_strips():
    raw = _RESPONSE_MODELS.read_text(encoding="utf-8")
    assert "//" in raw, "no comments to strip — the control is meaningless"
    assert "//" not in _strip_swift_comments(raw)


# ── 1. parity: every backend unit has a case ─────────────────────────────────

def _backend_units() -> set[str]:
    return {str(meta.get("unit", "")) for meta in cs._COMMODITY_PROFILES.values()}


def test_backend_units_are_non_empty_and_include_share_and_the_energy_units():
    units = _backend_units()
    assert "share" in units, "the ETF-backed metal screens send unit='share'"
    assert {"barrel", "mmbtu"} <= units


@pytest.mark.parametrize("unit", sorted(_backend_units()))
def test_every_backend_unit_has_an_explicit_ios_case(unit):
    """The switch normalises `unit.lowercased().replacingOccurrences(of: "_", with: "")`."""
    key = unit.lower().replace("_", "")
    assert f'case "{key}":' in _unit_switch(), (
        f"backend emits unit={unit!r} but the iOS switch has no case for it — it would fall "
        "through `default:` to a WRONG concrete unit (this is how 'per contract' rendered "
        "under a fund share price)"
    )


def test_the_share_unit_exists_with_an_honest_label():
    body = _block(_src(_MODELS), "enum CommodityUnit")
    assert 'case share = "per share"' in body


def test_the_share_case_maps_to_the_share_unit():
    assert 'case "share": return .share' in _unit_switch()


# ── 2. honesty: metal profiles describe the fund, not a futures contract ─────

_FUTURES_TOKENS = ("COMEX", "NYMEX", "troy ounce", "Sun–Fri", "Sun-Fri", "barrels", "contract")


@pytest.mark.parametrize("root", ["GC", "SI", "PL", "PA"])
def test_metal_profiles_name_the_fund_venue_not_a_futures_exchange(root):
    meta = cs._COMMODITY_PROFILES[root]
    assert meta["source"] == cs._COMMODITY_SOURCE_ETF, "precondition: ETF-backed"
    assert meta["exchange"] == "NYSE Arca", meta["exchange"]
    for field in ("exchange", "trading_hours", "contract_size", "tick_size"):
        for tok in _FUTURES_TOKENS:
            assert tok.lower() not in str(meta[field]).lower(), (
                f"{root}.{field}={meta[field]!r} describes a futures contract under a fund "
                "share price"
            )
    assert meta["contract_size"] == "", "a fund share has no contract size — send empty, hide"


@pytest.mark.parametrize("root", ["CL", "NG"])
def test_energy_profiles_describe_a_spot_print_not_a_futures_contract(root):
    meta = cs._COMMODITY_PROFILES[root]
    assert meta["source"] == cs._COMMODITY_SOURCE_FRED, "precondition: FRED-backed"
    assert "NYMEX" not in meta["exchange"]
    assert "spot" in meta["exchange"].lower()
    assert meta["contract_size"] == "" and meta["tick_size"] == ""


# ── iOS hides the rows the backend left empty ────────────────────────────────

def test_the_profile_card_hides_contract_size_and_tick_size_when_empty():
    body = _block(_src(_PROFILE_SECTION), "var body: some View")
    for field, label in (("contractSize", "Contract Size"), ("tickSize", "Tick Size")):
        pat = (r"if\s*!profile\." + field + r"\.isEmpty\s*\{\s*"
               r"CompanyProfileRow\(label:\s*\"" + label + r"\"")
        assert re.search(pat, body), (
            f"the {label!r} row must be wrapped in `if !profile.{field}.isEmpty` — an ETF "
            "screen sends it empty and a blank row is the honest render"
        )


# ── 3. parity: every backend category has a case (same `default:` hazard as units) ──


def _category_switch() -> str:
    body = _block(_src(_RESPONSE_MODELS), "func toModel() -> CommodityProfile")
    i = body.find("let resolvedCategory")
    assert i != -1, "guard is stale — `resolvedCategory` moved out of toModel()"
    return _block(body[i:], "switch")


def _backend_categories() -> set[str]:
    return {str(meta.get("category", "")) for meta in cs._COMMODITY_PROFILES.values()}


def test_backend_categories_are_the_expected_set():
    cats = _backend_categories()
    assert cats and cats <= {"metals", "energy", "agriculture", "consumables"}, cats


@pytest.mark.parametrize("category", sorted(_backend_categories()))
def test_every_backend_category_has_an_explicit_ios_case(category):
    assert f'case "{category.lower()}":' in _category_switch(), (
        f"backend emits category={category!r} but the iOS switch has no case for it — it "
        "would fall through `default:` and be labelled Metals"
    )
