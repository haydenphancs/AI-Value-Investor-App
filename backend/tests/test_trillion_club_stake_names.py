"""The stake finder's two shared contract modules, pinned on live values.

``stake_contracts`` (closed vocabularies, the Telegram callback grammar, the reviewed filers
file) and ``stake_names`` (entity names, share classes, listings, CUSIPs, identity, kind,
settings parsed at call time, source titles and URLs). Every rule in ``stake_names`` was
written against the recorded SEC data under ``tests/fixtures/trillion_club_stakes``; the
cases below use those exact values (and the seed's own rows) wherever one exists.
Hermetic: no I/O beyond reading the fixtures, the seed and the filers file.
"""
from __future__ import annotations

import copy
import json
import logging
from datetime import date
from pathlib import Path

import pytest

from app.services.trillion_club import rules
from app.services.trillion_club import stake_contracts as C
from app.services.trillion_club import stake_names as N

BACKEND = Path(__file__).resolve().parents[1]
FIX = BACKEND / "tests" / "fixtures" / "trillion_club_stakes"
FILERS = BACKEND / "data" / "trillion_club_filers.json"
SEED = json.loads((BACKEND / "data" / "trillion_club_seed.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((FIX / "MANIFEST.json").read_text(encoding="utf-8"))
UUID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def _submissions(stem: str) -> dict:
    return json.loads((FIX / "json" / f"submissions_{stem}.json").read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════════════════
# stake_contracts — vocabularies
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("values", [
    C.ORIGINS, C.MACHINE_ORIGINS, C.FINDING_SOURCES, C.FINDING_OUTCOMES, C.FINDING_TIERS,
    C.FINDING_STATUSES, C.OWNER_DECISIONS, C.REASON_CODES, C.CALLBACK_VERBS, C.FILER_ROLES,
    C.AUTO_TIER_TOKENS, C.DEFERRED_TIER_TOKENS,
])
def test_every_vocabulary_is_a_tuple_of_unique_trimmed_strings(values):
    assert isinstance(values, tuple) and values
    assert len(set(values)) == len(values)
    assert all(isinstance(v, str) and v and v == v.strip() for v in values)


def test_the_vocabularies_nest_as_documented():
    assert C.ORIGINS == ("seed", "auto", "proposal")
    assert set(C.MACHINE_ORIGINS) == set(C.ORIGINS) - {"seed"}
    assert C.FINDING_SOURCES == ("sch13", "efts")
    assert C.FINDING_OUTCOMES == ("auto", "proposal", "heads_up", "skipped")
    assert C.TOMBSTONE_STATUSES <= C.TERMINAL_STATUSES <= set(C.FINDING_STATUSES)
    # A finding still moving through its writes is never terminal (it would never finish).
    for live in ("new", "staged", "noted", "proposed", "publishing", "error"):
        assert live in C.FINDING_STATUSES and live not in C.TERMINAL_STATUSES
    assert set(C.AUTO_TIER_TOKENS) <= set(C.FINDING_TIERS)
    assert set(C.DEFERRED_TIER_TOKENS) <= set(C.FINDING_TIERS)
    assert not set(C.AUTO_TIER_TOKENS) & set(C.DEFERRED_TIER_TOKENS)
    assert C.AUTO_TIER_TOKENS == ("13dg",) and C.DEFERRED_TIER_TOKENS == ("sec_closing",)
    assert C.AUTO_REPORTING_TYPES == ("CO",) and C.AFFILIATE_EXTRA_TYPES == ("OO",)
    assert C.MAX_ATTEMPTS >= 1 and C.PROPOSAL_TTL_DAYS == 14


def test_every_reason_has_a_digest_text():
    assert C.REASON_CODES == tuple(C.REASONS)
    for code, text in C.REASONS.items():
        assert code.replace("_", "").isalnum() and code == code.lower(), code
        assert isinstance(text, str) and text.strip() == text and len(text) >= 10, code


# ── the Telegram callback grammar ────────────────────────────────────────────────────────

@pytest.mark.parametrize("verb", C.CALLBACK_VERBS)
def test_a_callback_round_trips_in_40_bytes(verb):
    data = C.callback_data(verb, UUID)
    assert data == f"z:{verb}:{UUID}"
    assert len(data.encode("utf-8")) == 40 <= 64
    assert C.parse_callback_data(data) == (verb, UUID)


@pytest.mark.parametrize("verb, finding", [
    ("x", UUID), ("W", UUID), ("", UUID), ("ww", UUID),
    ("w", UUID.upper()), ("w", "not-a-uuid"), ("w", UUID + "0"), ("w", UUID[:-1]), ("w", ""),
])
def test_a_callback_that_would_not_round_trip_is_refused(verb, finding):
    with pytest.raises(ValueError):
        C.callback_data(verb, finding)


@pytest.mark.parametrize("data", [
    None, 42, b"z:w:" + UUID.encode(), "", "z:", f"y:w:{UUID}", f"z:x:{UUID}", f"z:W:{UUID}",
    f"z:w:{UUID}\n", f" z:w:{UUID}", f"z:w:{UUID}x", f"z:w:{UUID.upper()}", f"z:w:{UUID}:extra",
    "z:w:" + "a" * 70, f"z:w:{UUID[:-1]}\u00e9",
])
def test_anything_else_parses_as_no_callback(data):
    assert C.parse_callback_data(data) is None


def test_the_stake_letter_never_collides_with_learn():
    assert C.CALLBACK_PREFIX == "z:"
    assert C.parse_callback_data(f"y:p:{UUID}") is None


# ── the reviewed filers file ─────────────────────────────────────────────────────────────

def test_the_real_filers_file_loads_with_its_live_values():
    members = C.load_member_configs(str(FILERS))
    assert {"nvidia", "alphabet", "amazon", "eli-lilly", "spacex"} <= set(members)
    # Deliberately absent (the file's own note): whale_link Berkshire, client-asset JPMorgan.
    assert not {"berkshire", "berkshire-hathaway", "jpmorgan", "jpmorgan-chase"} & set(members)
    nvidia = members["nvidia"]
    assert nvidia.filer_ciks == ("0001045810",) and nvidia.source_name == "NVIDIA"
    alphabet = members["alphabet"]
    google = [f for f in alphabet.s1_filers if f.role == "affiliate"]
    assert [(f.cik, f.names, set(f.types)) for f in google] == [("0001824723", ("Google LLC",), {"OO", "CO"})]
    assert alphabet.filer_ciks == ("0001652044", "0001824723")
    lilly = members["eli-lilly"]
    assert N.names_match("ELI LILLY & Co", "Eli Lilly and Company")
    assert {"ELI LILLY & Co", "Eli Lilly and Company"} <= set(lilly.names)
    # Every CIK belongs to one member only, and every member/manifest CIK agrees.
    ciks = [f.cik for m in members.values() for f in m.s1_filers]
    assert len(ciks) == len(set(ciks))
    for slug, key in (("nvidia", "nvidia"), ("amazon", "amazon"), ("eli-lilly", "eli_lilly"),
                      ("alphabet", "alphabet")):
        member_role = [f.cik for f in members[slug].s1_filers if f.role == "member"]
        assert member_role == [MANIFEST["members"][key]["cik"]]
    assert MANIFEST["members"]["google_llc"]["cik"] == "0001824723"
    # S2-only members are allowed (no S1 filer at all).
    assert members["samsung"].s1_filers == () and members["samsung"].s2_aliases


def _good_file() -> dict:
    return {
        "version": 1,
        "members": [{
            "slug": "nvidia", "source_name": "NVIDIA", "names": ["NVIDIA Corporation", "NVIDIA CORP"],
            "s1_filers": [{"cik": "0001045810", "role": "member", "names": ["NVIDIA Corporation"],
                           "types": ["CO"]}],
            "s2_aliases": ["NVIDIA Corporation"],
        }, {
            "slug": "alphabet", "source_name": "Alphabet", "names": ["Alphabet Inc."],
            "s1_filers": [{"cik": "0001652044", "role": "member", "names": ["Alphabet Inc."], "types": ["CO"]},
                          {"cik": "0001824723", "role": "affiliate", "names": ["Google LLC"],
                           "types": ["OO", "CO"]}],
            "s2_aliases": [],
        }],
    }


def test_a_minimal_good_file_parses_to_member_configs():
    out = C.parse_member_configs(_good_file())
    assert list(out) == ["nvidia", "alphabet"]
    nv = out["nvidia"]
    assert nv == C.MemberConfig(
        slug="nvidia", source_name="NVIDIA", names=("NVIDIA Corporation", "NVIDIA CORP"),
        s1_filers=(C.FilerEntry("0001045810", "member", ("NVIDIA Corporation",), ("CO",)),),
        s2_aliases=("NVIDIA Corporation",),
    )
    assert out["alphabet"].filer_ciks == ("0001652044", "0001824723")


def _mutated(path, value):
    data = _good_file()
    node = data
    for key in path[:-1]:
        node = node[key]
    if value is _DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return data


_DELETE = object()


@pytest.mark.parametrize("path, value, needle", [
    (("version",), 2, "version 1"),
    (("version",), _DELETE, "version 1"),
    (("members",), [], "non-empty list"),
    (("members",), {"slug": "x"}, "non-empty list"),
    (("members", 0), "nvidia", "must be an object"),
    (("members", 0, "slug"), "NVIDIA", "bad slug"),
    (("members", 0, "slug"), "-nvidia", "bad slug"),
    (("members", 0, "slug"), "a" * 41, "bad slug"),
    (("members", 0, "slug"), None, "bad slug"),
    (("members", 1, "slug"), "nvidia", "listed twice"),
    (("members", 0, "source_name"), "", "bad source_name"),
    (("members", 0, "source_name"), "x" * 41, "bad source_name"),
    (("members", 0, "source_name"), 7, "bad source_name"),
    (("members", 0, "names"), [], "must be a list"),
    (("members", 0, "names"), "NVIDIA Corporation", "must be a list"),
    (("members", 0, "names"), [" NVIDIA Corporation"], "trimmed"),
    (("members", 0, "names"), [""], "trimmed"),
    (("members", 0, "names"), ["NVIDIA\tCorp"], "unprintable"),
    (("members", 0, "names"), ["NVIDIA CORP", "NVIDIA CORP"], "listed twice"),
    (("members", 0, "s2_aliases"), ["NVIDIA"], "multi-word legal name"),
    (("members", 0, "s2_aliases"), ["Ab Cd"], "multi-word legal name"),
    (("members", 0, "s2_aliases"), ['"NVIDIA Corporation"'], "quote or parenthesis"),
    (("members", 0, "s2_aliases"), ["NVIDIA (Corporation)"], "quote or parenthesis"),
    (("members", 0, "s2_aliases"), "NVIDIA Corporation", "list"),
    (("members", 0, "s1_filers"), {"cik": "0001045810"}, "s1_filers must be a list"),
    (("members", 0, "s1_filers", 0), "0001045810", "must be an object"),
    (("members", 0, "s1_filers", 0, "cik"), "1045810", "bad filer CIK"),
    (("members", 0, "s1_filers", 0, "cik"), 1045810, "bad filer CIK"),
    (("members", 0, "s1_filers", 0, "cik"), "0000000000", "bad filer CIK"),
    (("members", 0, "s1_filers", 0, "cik"), "00010458a0", "bad filer CIK"),
    (("members", 1, "s1_filers", 0, "cik"), "0001045810", "listed for nvidia and alphabet"),
    (("members", 0, "s1_filers", 0, "role"), "parent", "bad role"),
    (("members", 0, "s1_filers", 0, "types"), ["OO"], "not allowed for a member"),
    (("members", 1, "s1_filers", 1, "types"), ["HC"], "not allowed for a affiliate"),
    (("members", 0, "s1_filers", 0, "types"), [], "must be a list"),
    (("members", 0, "s1_filers", 0, "names"), [], "must be a list"),
    (("members", 1, "s1_filers"), [{"cik": "0001824723", "role": "affiliate", "names": ["Google LLC"],
                                    "types": ["OO"]}], "needs the member's own CIK"),
    # A `$` anchor would accept a trailing newline; the loader uses fullmatch.
    (("version",), True, "version 1"),
    (("version",), 1.0, "version 1"),
    (("members", 0, "slug"), "nvidia\n", "bad slug"),
    (("members", 0, "s1_filers", 0, "cik"), "0001045810\n", "bad filer CIK"),
    # EFTS refuses a non-ASCII query, and a long alias overflows the 1,000-character cap.
    (("members", 0, "s2_aliases"), ["NVIDIA Société Corporation"], "must be ASCII"),
    (("members", 0, "s2_aliases"), ["NVIDIA " + "x" * 80], "must be ASCII"),
    # The cover-line matcher reads MemberConfig.names only.
    (("members", 0, "s1_filers", 0, "names"), ["NVIDIA CORP /DE/"], "must all appear in nvidia.names"),
    # Unknown keys read as configured while doing nothing.
    (("note_typo",), "x", "unknown key"),
    (("members", 0, "affiliates"), [], "unknown key"),
    (("members", 0, "s1_filers", 0, "use_13f"), True, "unknown key"),
])
def test_every_bad_entry_stops_the_finder_naming_the_problem(path, value, needle):
    with pytest.raises(ValueError) as e:
        C.parse_member_configs(_mutated(path, value))
    assert needle in str(e.value)


def test_a_member_with_neither_source_is_refused():
    data = _good_file()
    data["members"][1]["s1_filers"] = []
    with pytest.raises(ValueError, match="neither s1_filers nor s2_aliases"):
        C.parse_member_configs(data)


@pytest.mark.parametrize("data", [None, [], "x", {"members": []}])
def test_a_file_that_is_not_an_object_is_refused(data):
    with pytest.raises(ValueError):
        C.parse_member_configs(data)


def test_parsing_never_mutates_the_input():
    data = _good_file()
    before = copy.deepcopy(data)
    C.parse_member_configs(data)
    assert data == before


def test_load_member_configs_raises_on_an_unreadable_file(tmp_path):
    bad = tmp_path / "filers.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        C.load_member_configs(str(bad))
    with pytest.raises(OSError):
        C.load_member_configs(str(tmp_path / "missing.json"))
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_good_file()), encoding="utf-8")
    assert set(C.load_member_configs(str(good))) == {"nvidia", "alphabet"}


# ══════════════════════════════════════════════════════════════════════════════════════
# stake_names — entity names
# ══════════════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("a, b", [
    ("ELI LILLY & Co", "Eli Lilly and Company"),            # SEC conformed vs the filing
    ("NVIDIA CORP", "NVIDIA Corporation"),
    ("AMAZON COM INC", "Amazon.com, Inc."),
    ("ADVANCED MICRO DEVICES INC", "Advanced Micro Devices, Inc."),
    ("SPACE EXPLORATION TECHNOLOGIES CORP", "Space Exploration Technologies Corp."),
    ("TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD", "Taiwan Semiconductor Manufacturing Company Limited"),
    ("Nebius Group N.V.", "NEBIUS GROUP NV"),
    ("Alphabet Inc.", "ALPHABET INC"),
])
def test_conformed_and_filed_names_fold_together(a, b):
    assert N.fold_entity_name(a) == N.fold_entity_name(b) != ""
    assert N.names_match(a, b)


@pytest.mark.parametrize("a, b", [
    ("NVIDIA Corporation", "NVIDIA Corporation Pension Plan"),
    ("Google LLC", "Alphabet Inc."),
    ("XXVI Holdings Inc.", "Alphabet Inc."),
    ("Meta Platforms, Inc.", "Meta Materials Inc."),
])
def test_different_entities_never_fold_together(a, b):
    assert not N.names_match(a, b)


@pytest.mark.parametrize("value", [None, 7, "", "   ", "!!!", "_-_", "()"])
def test_a_name_with_nothing_to_compare_folds_to_nothing(value):
    assert N.fold_entity_name(value) == ""
    assert N.names_match(value, value) is False             # two empty keys are no match


def test_a_bare_ampersand_folds_to_the_word_and():
    assert N.fold_entity_name("&") == "and"


def test_single_letters_join_and_legal_forms_unify():
    assert N.fold_entity_name("N.V.I.D.I.A. Corporation") == "nvidia corporation"
    assert N.fold_entity_name("AT&T Inc.") == "at and t incorporated"
    assert N.fold_entity_name("Foo Ltd") == N.fold_entity_name("Foo Limited") == "foo limited"


#: (as filed / conformed, display name or None). The issuers come from MANIFEST.json.
CLEAN = [
    ("Ionetix Corp / DE /", "Ionetix"),                    # the EDGAR state tag, then the suffix
    ("Aktis Oncology, Inc.", "Aktis Oncology"),            # a comma-led suffix
    ("Eli Lilly and Company", "Eli Lilly and Company"),    # stripping would leave "… and"
    ("Nebius Group N.V.", "Nebius Group"),
    ("ProQR Therapeutics N.V.", "ProQR Therapeutics"),
    ("X-Energy, Inc.", "X-Energy"),
    ("CoreWeave, Inc.", "CoreWeave"),
    ("Scribe Therapeutics, Inc.", "Scribe Therapeutics"),
    ("Space Exploration Technologies Corp.", "Space Exploration Technologies"),
    ("TRex Bio, Inc.", "TRex Bio"),
    ("TeraWulf Inc.", "TeraWulf"),
    ("Lumentum Holdings Inc.", "Lumentum Holdings"),
    ("Marvell Technology, Inc.", "Marvell Technology"),
    ("Meta Platforms, Inc.", "Meta Platforms"),
    ("Procter & Gamble Co", "Procter & Gamble"),
    ("Toyota Motor Corp /FI", "Toyota Motor"),
    ("AT&T Inc.", "AT&T"),                                   # a short acronym is not "all caps"
    ("AMD", "AMD"),
    ("Foo (Cayman)", "Foo (Cayman)"),
    ("INTEL CORP", None),                                    # EDGAR's conformed caps: never shown
    ("CLEANSPARK, INC.", None),
    ("SAMSUNG ELECTRONICS CO LTD /FI", None),
    ("TERAWULF INC.", None),
    ("A" * 61, None),                                        # never truncated
    ("Foo,", None), ("Foo &", None), ("Foo Inc. &", None), ("Inc.", None), ("", None), ("   ", None),
    (None, None), (12, None), ("Foo\u0007Bar", None),
]


@pytest.mark.parametrize("raw, want", CLEAN)
def test_clean_investee_name(raw, want):
    assert N.clean_investee_name(raw) == want


def test_every_manifest_issuer_name_cleans_to_a_short_name_or_refuses():
    for key, issuer in MANIFEST["issuers"].items():
        out = N.clean_investee_name(issuer["name"])
        assert out is None or (len(out) <= N.INVESTEE_NAME_MAX and out == out.strip()), key
        if out is not None:
            assert not N.is_all_caps(out) and out[-1].isalnum() or out.endswith(")"), key


@pytest.mark.parametrize("name, want", [
    ("Private companies (not named)", frozenset()),          # a placeholder never matches
    ("Corning (pre-funded warrant)", frozenset({"corning"})),
    ("Nebius Group N.V.", frozenset({"nebius"})),
    ("The Holding Group", frozenset()),
    ("Advanced Vision Semiconductor Manufacturing", frozenset({"advanced", "vision", "semiconductor", "manufacturing"})),
    (None, frozenset()),
])
def test_significant_tokens(name, want):
    assert N.significant_tokens(name) == want


# ── share classes: every recorded title, plus what must never map ────────────────────────

#: Each recorded 13D/13G's securitiesClassTitle (tests/fixtures/trillion_club_stakes/xml).
RECORDED_TITLES = {
    "Class A Ordinary Shares": "of Class A",                                   # Nebius
    "Class A Common Stock, $0.001 par value": "of Class A",                    # SpaceX
    "Common Stock, par value $0.001 per share": "of common stock",             # TeraWulf
    "Class A common stock, par value $0.0001": "of Class A",                   # X-Energy
    "Ordinary Shares, nominal value 0.04 EUR per share": "of ordinary shares",  # ProQR
    "Common Stock, par value $0.0001 per share": "of common stock",            # Ionetix, TRex
    "Common Stock, $0.0001 par value per share": "of common stock",            # Aktis
    "Common stock, $0.001 par value per share": "of common stock",             # Scribe
    "Class A common stock, par value $0.000005 per share": "of Class A",       # CoreWeave
}


def test_every_recorded_class_title_is_listed():
    import re
    titles = set()
    for path in (FIX / "xml").glob("*.xml"):
        m = re.search(r"<(?:sch:)?securitiesClassTitle>([^<]*)<", path.read_text(encoding="utf-8"))
        titles.add(m.group(1))
    assert titles == set(RECORDED_TITLES)


@pytest.mark.parametrize("title, want", sorted(RECORDED_TITLES.items()))
def test_recorded_class_titles_map_to_a_basis(title, want):
    assert N.class_basis(title) == want


@pytest.mark.parametrize("title, want", [
    ("Ordinary Shares", "of ordinary shares"), ("Common Shares", "of common stock"),
    ("COMMON STOCK, NO PAR VALUE", "of common stock"), ("Class A Common Stock (no par value)", "of Class A"),
    ("Series A Preferred Stock", None), ("American Depositary Shares", None), ("ADSs", None),
    ("Class B Common Stock", None), ("Class C Capital Stock", None), ("Units", None),
    ("Warrants to purchase Common Stock", None), ("Rights", None), ("Convertible Notes due 2030", None),
    ("Limited Partnership Interests", None), ("Membership Interests", None), ("Capital Stock", None),
    ("", None), ("   ", None), (None, None), (7, None),
])
def test_class_basis_beyond_the_recorded_titles(title, want):
    assert N.class_basis(title) == want


def test_a_title_naming_two_classes_has_no_basis():
    # One percent cannot describe two classes, in either order (it used to map to the first).
    assert N.class_basis("Class A Common Stock and Class C Capital Stock") is None
    assert N.class_basis("Class C Capital Stock and Class A Common Stock") is None
    assert N.class_basis("Class A Common Stock and class a shares") == "of Class A"


# ── symbols, CUSIPs and listings ─────────────────────────────────────────────────────────

_SYMBOL_INPUTS = [None, "", "  ", "brk.b", "BRK/B", " nvda ", 123, "goog l", "BRK.B/C", "\tmsft\n", "nvda", "Ab.C"]


def test_normalize_symbol_is_the_read_paths_rule():
    from app.services.trillion_club_service import _norm_symbol

    for value in _SYMBOL_INPUTS:
        assert N.normalize_symbol(value) == _norm_symbol(value), value
    assert N.normalize_symbol("BRK.B") == N.normalize_symbol("brk/b") == "BRK-B"


@pytest.mark.parametrize("cusip", [
    "N97284108", "84615Q103", "88080T104", "98386P102", "N71542109", "00973N102", "811033109",
    "89531N100", "21873S108",
])
def test_every_recorded_cusip_is_an_identity_key(cusip):
    assert N.cusip_identity(cusip) == cusip
    assert N.cusip_identity(cusip.lower()) == cusip
    assert N.cusip_identity(f"  {cusip} ") == cusip
    assert N.cusip6(cusip) == cusip[:6]


def test_the_all_zero_placeholder_passes_its_check_digit_but_is_never_a_key():
    assert rules.cusip_check_digit("00000000") == 0          # why a check-digit test alone is not enough
    assert N.cusip_identity("000000000") is None
    assert N.cusip6("000000000") is None


@pytest.mark.parametrize("value", ["N97284109", "N9728410", "N972841088", "N97284 08", "", None, 97284108,
                                   "N9728410X"])
def test_a_malformed_cusip_is_no_key(value):
    assert N.cusip_identity(value) is None and N.cusip6(value) is None


#: Every recorded submissions file → (ticker, reason) from its own tickers/exchanges.
LISTINGS = {
    "issuer_aktis": ("AKTS", None), "issuer_cleanspark": ("CLSK", None), "issuer_coherent": ("COHR", None),
    "issuer_coreweave": ("CRWV", None), "issuer_intel": ("INTC", None),
    "issuer_ionetix": (None, "no_us_listing"), "issuer_lumentum": ("LITE", None),
    "issuer_marvell": ("MRVL", None), "issuer_meta": ("META", None), "issuer_nebius": ("NBIS", None),
    "issuer_nokia": ("NOK", None), "issuer_proqr": ("PRQR", None), "issuer_scribe": ("SCTX", None),
    "issuer_spacex": ("SPCX", None), "issuer_synopsys": ("SNPS", None), "issuer_terawulf": ("WULF", None),
    "issuer_xenergy": ("XE", None), "member_alphabet": (None, "ambiguous_listing"),
    "member_amazon": ("AMZN", None), "member_eli_lilly": ("LLY", None),
    "member_google_llc": (None, "no_us_listing"), "member_nvidia": ("NVDA", None),
}


def test_every_recorded_submissions_file_has_a_pinned_listing():
    stems = {p.stem.removeprefix("submissions_") for p in (FIX / "json").glob("submissions_*.json")}
    assert stems == set(LISTINGS)


@pytest.mark.parametrize("stem", sorted(LISTINGS))
def test_choose_us_common_ticker_on_recorded_submissions(stem):
    d = _submissions(stem)
    assert N.choose_us_common_ticker(d["tickers"], d["exchanges"]) == LISTINGS[stem]


def test_the_live_traps_behind_the_listing_rule():
    assert _submissions("issuer_cleanspark")["tickers"] == ["CLSK", "CLSKW"]      # warrant beside common
    assert _submissions("issuer_nokia")["exchanges"] == ["NYSE", "OTC"]           # OTC never counts
    assert _submissions("member_alphabet")["tickers"] == ["GOOGL", "GOOG", "GOOGM", "GOOGN"]


@pytest.mark.parametrize("tickers, exchanges, want", [
    (["FOO"], ["OTC"], (None, "no_us_listing")),
    ([], [], (None, "no_us_listing")),
    (None, ["Nasdaq"], (None, "no_us_listing")),
    ("FOO", "Nasdaq", (None, "no_us_listing")),
    (["FOO"], ["  nasdaq "], ("FOO", None)),
    (["FOO", "FOO"], ["Nasdaq", "NYSE"], ("FOO", None)),
    (["foo.u", "FOO"], ["NYSE", "NYSE"], ("FOO", None)),
    (["FOO", "FOO-WS", "FOO.RT"], ["NYSE", "NYSE", "NYSE"], ("FOO", None)),
    (["BRK.A", "BRK.B"], ["NYSE", "NYSE"], (None, "ambiguous_listing")),
    (["FOO", None, 7], ["Nasdaq", "Nasdaq", "Nasdaq"], ("FOO", None)),
    (["FOO", "BAR"], ["Nasdaq"], ("FOO", None)),                 # zip stops at the shorter list
    (["FOO"], [None], (None, "no_us_listing")),
    (["FOO"], ["CBOE"], ("FOO", None)),
])
def test_choose_us_common_ticker_edges(tickers, exchanges, want):
    assert N.choose_us_common_ticker(tickers, exchanges) == want


def test_a_lone_warrant_ticker_is_never_the_common_ticker():
    # The common trades OTC: its Nasdaq warrant must not become "the common ticker".
    assert N.choose_us_common_ticker(["FOO", "FOOW"], ["OTC", "Nasdaq"]) == (None, "no_us_listing")
    # Dashed preferred series beside the common are not a second class.
    assert N.choose_us_common_ticker(["BAC", "BAC-PB", "BAC-PE"], ["NYSE"] * 3) == ("BAC", None)
    # A plain ticker that merely ends in a suffix letter is still a common ticker.
    assert N.choose_us_common_ticker(["SNOW"], ["NYSE"]) == ("SNOW", None)


# ── kind and the settings read at call time ──────────────────────────────────────────────

@pytest.mark.parametrize("use_13f, on_13f, symbol, want", [
    (True, True, "NBIS", "on_13f_note"),
    (True, False, "NBIS", "us_listed_off_13f"),
    (False, True, "NBIS", "us_listed_off_13f"),
    (False, False, "NBIS", "us_listed_off_13f"),
    (True, True, None, None),
    (True, True, "", None),
    (False, False, None, None),
])
def test_decide_kind(use_13f, on_13f, symbol, want):
    assert N.decide_kind(use_13f=use_13f, on_latest_13f=on_13f, symbol=symbol) == want


@pytest.mark.parametrize("raw, want, logged", [
    ("", frozenset(), False), ("   ", frozenset(), False), (None, frozenset(), False), (13, frozenset(), False),
    ("13dg", frozenset({"13dg"}), False), (" 13DG ", frozenset({"13dg"}), False),
    ("13dg,13dg", frozenset({"13dg"}), False),
    ("13dg, sec_closing", frozenset({"13dg"}), True),
    ("sec_closing", frozenset(), True),
    ("junk 13dg", frozenset({"13dg"}), True),
])
def test_parse_auto_tiers(raw, want, logged, caplog):
    with caplog.at_level(logging.ERROR, logger=N.__name__):
        assert N.parse_auto_tiers(raw) == want
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == (1 if logged else 0)
    if logged:
        assert "[tc-stakes-config]" in errors[0].getMessage()


def test_a_deferred_tier_is_named_as_deferred(caplog):
    with caplog.at_level(logging.ERROR, logger=N.__name__):
        N.parse_auto_tiers("sec_closing, junk")
    (record,) = [r for r in caplog.records if r.levelno == logging.ERROR]
    message = record.getMessage()
    assert "'sec_closing', 'junk'" in message and "deferred to a follow-up: ['sec_closing']" in message


@pytest.mark.parametrize("raw, want, logged", [
    ("", None, False), ("  ", None, False), (None, None, False), (20261101, None, False),
    ("2026-11-01", date(2026, 11, 1), False), (" 2026-11-01\n", date(2026, 11, 1), False),
    ("2026-13-01", None, True), ("2026-02-30", None, True), ("11/01/2026", None, True),
    ("2026-11-1", None, True), ("tomorrow", None, True), ("2026-11-01T00:00", None, True),
])
def test_parse_auto_since(raw, want, logged, caplog):
    with caplog.at_level(logging.ERROR, logger=N.__name__):
        assert N.parse_auto_since(raw) == want
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == (1 if logged else 0)


# ── source titles and URLs: the seed's own spelling ──────────────────────────────────────

_SOURCE_NAME = {"nvidia": "NVIDIA", "alphabet": "Alphabet", "amazon": "Amazon", "eli-lilly": "Lilly"}
#: The submissions file holding each seed row's accession (Google LLC files Alphabet's).
_SUBMISSIONS_OF = {"nvidia": "member_nvidia", "alphabet": "member_google_llc", "amazon": "member_amazon",
                   "eli-lilly": "member_eli_lilly"}


def _seed_schedule_rows():
    return [s for s in SEED["stakes"] if "Schedule 13" in (s.get("source_title") or "")]


def test_schedule_source_titles_reproduce_every_seed_row():
    rows = _seed_schedule_rows()
    assert len(rows) == 7
    members = C.load_member_configs(str(FILERS))
    for row in rows:
        slug = row["company_slug"]
        acc = rules.accession_from_link(row["source_url"])
        recent = _submissions(_SUBMISSIONS_OF[slug])["filings"]["recent"]
        i = recent["accessionNumber"].index(acc)
        title = N.schedule_source_title(
            members[slug].source_name, recent["form"][i], date.fromisoformat(recent["filingDate"][i]))
        assert title == row["source_title"], (slug, row["investee_name"])
        assert members[slug].source_name == _SOURCE_NAME[slug]


@pytest.mark.parametrize("sub, want", [
    ("SCHEDULE 13G", "NVIDIA Schedule 13G (Jul 20, 2026)"),
    ("schedule 13g/a", "NVIDIA Schedule 13G/A (Jul 20, 2026)"),
    (" SCHEDULE 13D ", "NVIDIA Schedule 13D (Jul 20, 2026)"),
    ("SCHEDULE 13D/A", "NVIDIA Schedule 13D/A (Jul 20, 2026)"),
    ("SC 13G", None), ("SCHEDULE  13G", None), ("", None), (None, None),
])
def test_schedule_source_title(sub, want):
    assert N.schedule_source_title("NVIDIA", sub, date(2026, 7, 20)) == want


def test_the_day_is_never_zero_padded():
    assert N.schedule_source_title("Lilly", "SCHEDULE 13D", date(2026, 1, 5)) == "Lilly Schedule 13D (Jan 5, 2026)"


@pytest.mark.parametrize("name, form, day, want", [
    ("Intel", "8-K", date(2025, 12, 29), "Intel 8-K (Dec 29, 2025)"),
    ("Synopsys", "8-K", date(2025, 12, 1), "Synopsys 8-K (Dec 1, 2025)"),
    ("Coherent", " 8-K ", date(2026, 3, 2), "Coherent 8-K (Mar 2, 2026)"),
    ("Nokia", "6-K", date(2025, 11, 13), "Nokia 6-K (Nov 13, 2025)"),
])
def test_document_source_titles_match_the_seed(name, form, day, want):
    assert N.document_source_title(name, form, day) == want
    assert any(s.get("source_title") == want for s in SEED["stakes"])


def test_every_recorded_schedule_primary_document_splits_into_xml_and_folder():
    seen = 0
    for stem in ("member_nvidia", "member_alphabet", "member_amazon", "member_eli_lilly", "member_google_llc"):
        recent = _submissions(stem)["filings"]["recent"]
        for form, doc in zip(recent["form"], recent["primaryDocument"]):
            if form.startswith("SCHEDULE 13"):
                xml, folder = N.schedule_document_paths(doc)
                assert xml == "primary_doc.xml"
                assert folder in ("xslSCHEDULE_13G_X01", "xslSCHEDULE_13G_X02", "xslSCHEDULE_13D_X01",
                                  "xslSCHEDULE_13D_X02"), doc
                seen += 1
    assert seen >= 30


@pytest.mark.parametrize("value, want", [
    ("primary_doc.xml", ("primary_doc.xml", None)),
    (" xslSCHEDULE_13D_X02/primary_doc.xml ", ("primary_doc.xml", "xslSCHEDULE_13D_X02")),
    ("../primary_doc.xml", None), ("xsl/primary_doc.xml", None), ("xslFoo/other.xml", None),
    ("xslA/xslB/primary_doc.xml", None), ("xsl" + "A" * 41 + "/primary_doc.xml", None),
    ("xslA-B/primary_doc.xml", None), ("d123456dsc13g.htm", None), ("", None), (None, None),
])
def test_schedule_document_paths_refuse_anything_else(value, want):
    assert N.schedule_document_paths(value) == want


def test_archive_url_reproduces_the_seeds_nebius_link():
    nebius = next(s for s in SEED["stakes"] if s["investee_name"] == "Nebius")
    url = N.archive_url("0001045810", "0001045810-26-000062", "primary_doc.xml", "xslSCHEDULE_13G_X01")
    assert url == nebius["source_url"]
    assert N.archive_url("0000050863", "0000050863-25-000204", "intc-20251226.htm") == next(
        s["source_url"] for s in SEED["stakes"] if s["investee_name"] == "Intel")


# ── identity: is this investee already on file for the member? ───────────────────────────

ROWS = [
    {"investee_name": "Nebius", "investee_cusip": "N97284108", "investee_us_symbol": "NBIS"},
    {"investee_name": "Nscale", "investee_cusip": None, "investee_us_symbol": None},
    {"investee_name": "Alphabet", "investee_us_symbol": "GOOGL"},
    {"investee_name": "Private companies (not named)"},
    {"investee_name": "X-Energy", "investee_cik": "0002088896"},
    "not a row",
    None,
]


def _index():
    return N.IdentityIndex(ROWS, symbol_aliases={"GOOGL": ["GOOG", ""], "": ["X"]})


def test_identifiers_decide():
    idx = _index()
    assert idx.match(investee_cik="0002088896").by == "cik"
    m = idx.match(cusip="N97284207")                    # another class of the same issuer (CUSIP-6)
    assert m.by == "cusip" and m.row["investee_name"] == "Nebius"
    assert idx.match(symbol="nbis").by == "symbol"
    m = idx.match(symbol="GOOG")                          # the club's symbol aliases
    assert m.by == "symbol" and m.row["investee_name"] == "Alphabet"
    assert idx.match(investee_cik="0000000001") is None
    assert idx.match(cusip="000000000") is None           # the placeholder is no key


def test_a_name_is_only_a_possible_match_and_only_without_comparable_identifiers():
    idx = _index()
    m = idx.match(name="Nscale Ltd")
    assert m.by == "name" and m.row["investee_name"] == "Nscale"
    # An IPO'd Nscale (a symbol the private row lacks) still only POSSIBLY matches it.
    assert idx.match(name="Nscale", symbol="NSCL").by == "name"
    # Nebius HAS a symbol: a different symbol means a different company, whatever the name.
    assert idx.match(name="Nebius Group N.V.", symbol="ZZZ") is None
    assert idx.match(name="Nebius Group N.V.").by == "name"
    # A placeholder row never name-matches; neither does an empty candidate.
    assert idx.match(name="Private companies") is None
    assert idx.match() is None


def test_identity_against_the_real_seed():
    nvidia_rows = [s for s in SEED["stakes"] if s["company_slug"] == "nvidia"]
    idx = N.IdentityIndex(nvidia_rows)
    # The Nebius 13G the finder would read (CUSIP N97284108, symbol NBIS) is the seed's row.
    assert idx.match(cusip="N97284108").row["investee_name"] == "Nebius"
    assert idx.match(symbol="NBIS").row["investee_name"] == "Nebius"
    # Intel's closing 8-K (CUSIP 458140100) is the seed's Intel row.
    assert idx.match(cusip="458140100").row["investee_name"] == "Intel"
    # An investee NVIDIA has no row for matches nothing.
    assert idx.match(cusip="98386P102", symbol="XE", name="X-Energy") is None
    alphabet_rows = [s for s in SEED["stakes"] if s["company_slug"] == "alphabet"]
    assert N.IdentityIndex(alphabet_rows).match(cusip="84615Q103").row["investee_name"] == "SpaceX"


def test_unique_preserving():
    assert N.unique_preserving([3, 1, 3, 2, 1]) == [3, 1, 2]
    assert N.unique_preserving([]) == []
    assert N.unique_preserving("abca") == ["a", "b", "c"]


def test_stake_names_exports_what_it_defines():
    for name in N.__all__:
        assert hasattr(N, name), name


# ── Review round 1 (2026-10-28): PBC names, the subset fallback, two-class titles, intermediates ──

_SEED_STAKES = json.loads((Path(__file__).resolve().parent.parent / "data" / "trillion_club_seed.json")
                          .read_text(encoding="utf-8"))["stakes"]


@pytest.mark.parametrize("slug, candidate, want_row", [
    # A listed issuer files under its PBC name; the seed's private row has no identifier at all.
    ("amazon", "Anthropic, PBC", "Anthropic"),
    ("amazon", "Anthropic PBC", "Anthropic"),
    ("nvidia", "OpenAI Group PBC", "OpenAI"),
    ("microsoft", "OpenAI", "OpenAI Group PBC"),
    ("eli-lilly", "TRex Bio, Inc.", "TRex Bio"),
])
def test_a_pbc_or_suffixed_name_is_a_possible_match_for_the_seed_row(slug, candidate, want_row):
    idx = N.IdentityIndex([s for s in _SEED_STAKES if s["company_slug"] == slug])
    hit = idx.match(investee_cik="0001234567", cusip=None, symbol="ZZZQ", name=candidate)
    assert hit is not None and hit.by == "name" and hit.row["investee_name"] == want_row


def test_an_exact_token_match_beats_a_subset_match():
    rows = [{"investee_name": "Anthropic Labs"}, {"investee_name": "Anthropic"}]
    hit = N.IdentityIndex(rows).match(name="Anthropic, PBC")
    assert hit.row["investee_name"] == "Anthropic" and hit.by == "name"
    sub = N.IdentityIndex([{"investee_name": "Anthropic Labs"}]).match(name="Anthropic, PBC")
    assert sub is not None and sub.row["investee_name"] == "Anthropic Labs"


def test_a_subset_name_never_overrides_a_comparable_identifier():
    # Both rows carry a CIK: the identifiers decide, and different CIKs are different companies.
    rows = [{"investee_name": "Anthropic", "investee_cik": "0000000001"}]
    assert N.IdentityIndex(rows).match(investee_cik="0000000002", name="Anthropic, PBC") is None
    # Placeholder rows never name-match anything.
    assert N.IdentityIndex([{"investee_name": "Private companies (not named)"}]).match(name="Acme Corp") is None


@pytest.mark.parametrize("raw, want", [
    ("Anthropic, PBC", "Anthropic"),
    ("OpenAI Group PBC", "OpenAI Group"),
    ("Acme Public Benefit Corporation", "Acme"),
    ("Samsung Electronics Co., Ltd.", "Samsung Electronics"),
    ("Foo Pte. Ltd.", "Foo"),
    ("Bar Pty Ltd", "Bar"),
    ("Baz B.V.", "Baz"),
    ("Qux K.K.", "Qux"),
])
def test_more_legal_forms_are_stripped_from_display_names(raw, want):
    assert N.clean_investee_name(raw) == want


@pytest.mark.parametrize("title", [
    # A par clause with no comma before the second class used to swallow it.
    "Common Stock, par value $0.0001 per share and Series A Convertible Preferred Stock",
    "Common Stock, par value $0.0001 per share and Warrants to purchase Common Stock",
    "Ordinary Shares, nominal value EUR 0.04 per share and American Depositary Shares",
    "Common Stock, no par value and Series B Preferred Stock",
    # A Series letter is mapped like a Class letter: never.
    "Series B Common Stock",
    "Series C Common Stock, par value $0.01 per share",
    "Common Stock, par value $0.01 per share, and associated Preferred Stock Purchase Rights",
])
def test_a_second_class_or_a_series_anywhere_in_the_title_has_no_basis(title):
    assert N.class_basis(title) is None


@pytest.mark.parametrize("title, want", [
    ("Class A Common Stock, par value $0.0001 per share", "of Class A"),
    ("Class A Ordinary Shares, par value US$0.0001 per share", "of Class A"),
    ("Common Stock, $0.001 par value per share", "of common stock"),
    ("Ordinary Shares, nominal value EUR 0.12 per share", "of ordinary shares"),
])
def test_plain_titles_with_par_clauses_still_map(title, want):
    assert N.class_basis(title) == want


def test_intermediates_parse_and_are_refused_when_they_collide():
    data = _good_file()
    data["members"][1]["intermediates"] = ["XXVI Holdings Inc."]
    assert C.parse_member_configs(data)["alphabet"].intermediates == ("XXVI Holdings Inc.",)
    assert C.parse_member_configs(_good_file())["alphabet"].intermediates == ()  # optional key
    for bad, needle in [(["Alphabet Inc."], "already a member or filer name"),
                        (["Google LLC"], "already a member or filer name"),
                        (["XXVI Holdings Société"], "must be ASCII"),
                        (["XXVI Holdings Inc.", "XXVI Holdings Inc."], "listed twice")]:
        d = _good_file()
        d["members"][1]["intermediates"] = bad
        with pytest.raises(ValueError, match=needle):
            C.parse_member_configs(d)
    d = _good_file()
    d["members"][0]["s1_filers"] = []
    d["members"][0]["intermediates"] = ["NVIDIA International Holdings Inc."]
    with pytest.raises(ValueError, match="intermediates need s1_filers"):
        C.parse_member_configs(d)


def test_the_real_alphabet_entry_lists_its_reviewed_intermediate():
    cfg = C.load_member_configs(str(Path(__file__).resolve().parent.parent / "data" / "trillion_club_filers.json"))
    assert cfg["alphabet"].intermediates == ("XXVI Holdings Inc.",)
    assert all(m.intermediates == () for slug, m in cfg.items() if slug != "alphabet")


# ── Gate 10: one pure function for the run and the preview (review A PV-3) ─────────────────────


@pytest.mark.parametrize("filed_on, tiers, since, want", [
    (date(2026, 10, 20), frozenset({"13dg"}), date(2026, 10, 1), None),
    (date(2026, 10, 1), frozenset({"13dg"}), date(2026, 10, 1), None),           # on the day: live
    (date(2026, 9, 30), frozenset({"13dg"}), date(2026, 10, 1), "before_auto_since"),
    (date(2026, 10, 20), frozenset({"13dg"}), None, "before_auto_since"),        # no AUTO_SINCE
    (None, frozenset({"13dg"}), date(2026, 10, 1), "before_auto_since"),         # no date: fail closed
    (date(2026, 10, 20), frozenset(), date(2026, 10, 1), "tier_off"),
    (date(2026, 10, 20), frozenset({"sec_closing"}), date(2026, 10, 1), "tier_off"),
])
def test_auto_tier_gate(filed_on, tiers, since, want):
    assert N.auto_tier_gate(filed_on, tiers=tiers, since=since) == want
    assert want is None or want in C.REASONS
