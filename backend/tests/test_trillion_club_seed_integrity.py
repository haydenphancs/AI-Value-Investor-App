"""Trillion-Dollar Club seed — integrity of the JSON and of the script that loads it.

`backend/data/trillion_club_seed.json` is shown to users as SOURCED FACT ("~25% · per
Microsoft 10-K") and `scripts/seed_trillion_club.py` writes it to PRODUCTION. So this file
pins three things, all hermetically (no network, no Supabase):

1. The JSON satisfies migration 175's CHECK constraints — parsed from the migration itself
   (enumerations, regexes, lengths, NOT NULLs), not copied here, so a migration change that
   the seed does not follow fails here.
2. The editorial rules: https primary sources, ISO dates, <= 90-character past-tense
   background, banned wording (word-boundary regexes with non-vacuity pairs), secondary
   rows never published, manual caps forced + complete, SK hynix never sized from its
   outlier ADR, 13F ingestion only for the four opted-in filers.
3. The script: every validator rule can fail (a mutation per rule), a dry run writes
   nothing, --apply is additive, --update overwrites only drifted columns, a read failure
   never looks like an empty table, and one bad row never aborts the rest.
"""

from __future__ import annotations

import ast
import copy
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

import pytest

from scripts import seed_trillion_club as seed_mod

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "database" / "migrations" / "175_trillion_club.sql"
SEED_JSON = BACKEND / "data" / "trillion_club_seed.json"
TODAY = date(2026, 9, 24)

_NEW_TABLES = ("trillion_club_companies", "trillion_club_stakes", "trillion_club_filings")


# ══════════════════════════════════════════════════════════════════════════════════════
# Migration parsing — the CHECKs are read from 175, never restated here
# ══════════════════════════════════════════════════════════════════════════════════════

_TYPE_RE = r"(?:TEXT|UUID|BOOLEAN|DOUBLE\s+PRECISION|INTEGER|DATE|TIMESTAMPTZ|JSONB)"


def _raw_block(table: str) -> str:
    sql = MIGRATION.read_text(encoding="utf-8")
    m = re.search(rf"CREATE TABLE IF NOT EXISTS public\.{table} \((.*?)\n\);", sql, re.S)
    assert m, f"CREATE TABLE for {table} not found in {MIGRATION.name}"
    return m.group(1)


def _strip_comments(text: str) -> str:
    return re.sub(r"--[^\n]*", "", text)


def _segments(table: str) -> Dict[str, str]:
    """{column or CONSTRAINT name: its full definition text (continuation lines included)}."""
    block = _strip_comments(_raw_block(table))
    starts = [(m.start(), m.group(1) or m.group(2)) for m in re.finditer(
        rf"^\s{{4}}(?:CONSTRAINT\s+(\w+)|(\w+)\s+{_TYPE_RE})", block, re.M)]
    out: Dict[str, str] = {}
    for i, (pos, name) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(block)
        out[name] = block[pos:end]
    return out


def _columns(table: str) -> List[str]:
    return [n for n, seg in _segments(table).items() if not seg.lstrip().startswith("CONSTRAINT")]


def _in_list(segment: str) -> tuple:
    m = re.search(r"\bIN\s*\(([^)]*)\)", segment)
    assert m, f"no IN (...) list in: {segment!r}"
    return tuple(re.findall(r"'([^']*)'", m.group(1)))


def _pattern(segment: str, column: str) -> re.Pattern:
    m = re.search(rf"{column}\s*~\s*'([^']+)'", segment)
    assert m, f"no regex CHECK on {column}"
    pat = m.group(1)
    return re.compile(pat[:-1] + r"\Z" if pat.endswith("$") else pat)  # Python `$` allows a "\n"


def _job_columns() -> List[str]:
    raw = _raw_block("trillion_club_companies")
    start, end = raw.index("Written by the daily job"), raw.index("-- Editorial")
    return re.findall(rf"^\s{{4}}(\w+)\s+{_TYPE_RE}", raw[start:end], re.M)


def _not_null(table: str) -> List[str]:
    return [n for n, seg in _segments(table).items()
            if not seg.lstrip().startswith("CONSTRAINT") and ("NOT NULL" in seg or "PRIMARY KEY" in seg)]


def _char_bounds(table: str) -> Dict[str, tuple]:
    out: Dict[str, tuple] = {}
    for seg in _segments(table).values():
        for col, lo, hi in re.findall(r"char_length\((\w+)\)\s+BETWEEN\s+(\d+)\s+AND\s+(\d+)", seg):
            out[col] = (int(lo), int(hi))
        for col, hi in re.findall(r"char_length\((\w+)\)\s*<=\s*(\d+)", seg):
            out[col] = (0, int(hi))
    return out


def _positive_columns(table: str) -> List[str]:
    return [n for n, seg in _segments(table).items()
            if re.search(rf"\b{n}\s*>\s*0\b", seg)]


@pytest.fixture(scope="module")
def seed() -> Dict[str, List[Dict[str, Any]]]:
    return seed_mod.load_seed(str(SEED_JSON))


@pytest.fixture(scope="module")
def companies(seed) -> Dict[str, Dict[str, Any]]:
    return {c["slug"]: c for c in seed["companies"]}


def test_migration_parser_is_not_vacuous():
    c_seg, s_seg = _segments("trillion_club_companies"), _segments("trillion_club_stakes")
    assert _in_list(c_seg["card_kind"]) == ("thirteen_f", "no_thirteen_f", "non_us", "whale_link")
    assert _in_list(s_seg["kind"])[0] == "private" and len(_in_list(s_seg["kind"])) == 5
    assert _in_list(c_seg["trillion_club_manual_cap_is_explicit"]) == ("force_in", "force_out")
    assert _pattern(c_seg["slug"], "slug").fullmatch("saudi-aramco")
    assert not _pattern(c_seg["slug"], "slug").fullmatch("Saudi Aramco")
    assert not _pattern(c_seg["slug"], "slug").search("nvidia\n")  # the `$` → `\Z` fix
    assert _char_bounds("trillion_club_stakes")["background"] == (0, 90)
    assert _char_bounds("trillion_club_stakes")["investee_name"] == (1, 60)
    assert "is_member" in _job_columns() and "published" not in _job_columns()
    assert {"ownership_pct", "disclosed_value_usd"} <= set(_positive_columns("trillion_club_stakes"))
    assert len(_columns("trillion_club_companies")) == 29 and len(_columns("trillion_club_stakes")) == 24
    assert len(_columns("trillion_club_filings")) == 16


# ══════════════════════════════════════════════════════════════════════════════════════
# 1. The seed satisfies migration 175 (parsed) and the script's constants match it
# ══════════════════════════════════════════════════════════════════════════════════════


def test_script_columns_are_exactly_the_hand_kept_columns_of_175():
    c_cols = set(_columns("trillion_club_companies"))
    s_cols = set(_columns("trillion_club_stakes"))
    assert set(seed_mod.JOB_COLUMNS) == set(_job_columns())
    assert set(seed_mod.COMPANY_COLUMNS) == c_cols - set(_job_columns()) - {"created_at", "updated_at"}
    assert set(seed_mod.STAKE_COLUMNS) == s_cols - {"id", "created_at", "updated_at"}
    assert len(set(seed_mod.COMPANY_COLUMNS)) == len(seed_mod.COMPANY_COLUMNS)
    assert len(set(seed_mod.STAKE_COLUMNS)) == len(seed_mod.STAKE_COLUMNS)


def test_script_enumerations_match_175_and_the_wire_schema():
    from app.schemas import trillion_club as wire

    c_seg, s_seg = _segments("trillion_club_companies"), _segments("trillion_club_stakes")
    assert tuple(_in_list(c_seg["card_kind"])) == tuple(wire.CARD_KINDS)
    assert tuple(_in_list(c_seg["cap_source"])) == seed_mod.CAP_SOURCES
    assert tuple(_in_list(c_seg["membership_mode"])) == seed_mod.MEMBERSHIP_MODES
    assert tuple(_in_list(s_seg["kind"])) == tuple(wire.STAKE_KINDS)
    assert tuple(_in_list(s_seg["value_basis"])) == tuple(wire.VALUE_BASES)
    assert tuple(_in_list(s_seg["source_confidence"])) == seed_mod.SOURCE_CONFIDENCES
    bounds = _char_bounds("trillion_club_stakes")
    assert bounds["investee_name"][1] == seed_mod.INVESTEE_NAME_MAX
    assert bounds["source_title"][1] == seed_mod.SOURCE_TITLE_MAX
    assert bounds["background"][1] == seed_mod.BACKGROUND_MAX
    assert _char_bounds("trillion_club_companies")["display_name"][1] == seed_mod.DISPLAY_NAME_MAX


def test_seed_rows_have_exactly_the_seeded_columns(seed):
    for c in seed["companies"]:
        assert list(c) == list(seed_mod.COMPANY_COLUMNS), c.get("slug")
        assert not set(c) & set(seed_mod.JOB_COLUMNS), c["slug"]
    for s in seed["stakes"]:
        assert list(s) == list(seed_mod.STAKE_COLUMNS), seed_mod.stake_key(s)


def _check_rows_against_175(rows, table):
    seg = _segments(table)
    problems = []
    enums = {n: _in_list(s) for n, s in seg.items()
             if not s.lstrip().startswith("CONSTRAINT") and re.search(r"\bIN\s*\(", s)}
    regexes = {n: _pattern(s, n) for n, s in seg.items()
               if not s.lstrip().startswith("CONSTRAINT") and re.search(rf"\b{n}\s*~\s*'", s)}
    bounds, positive, not_null = _char_bounds(table), _positive_columns(table), _not_null(table)
    for row in rows:
        rid = row.get("slug") or seed_mod.stake_key(row)
        for col, allowed in enums.items():
            if col in row and row[col] is not None and row[col] not in allowed:
                problems.append((rid, col, "enum"))
        for col, rx in regexes.items():
            if col in row and row[col] is not None and not rx.search(row[col]):
                problems.append((rid, col, "regex"))
        for col, (lo, hi) in bounds.items():
            if row.get(col) is not None and not lo <= len(row[col]) <= hi:
                problems.append((rid, col, "length"))
        for col in positive:
            v = row.get(col)
            if v is not None and not (isinstance(v, (int, float)) and math.isfinite(v) and v > 0):
                problems.append((rid, col, "positive"))
        for col in not_null:
            if col in row and row[col] is None:
                problems.append((rid, col, "not null"))
    return problems


def test_seed_satisfies_every_parsed_column_check_of_175(seed):
    assert _check_rows_against_175(seed["companies"], "trillion_club_companies") == []
    assert _check_rows_against_175(seed["stakes"], "trillion_club_stakes") == []
    ciks_rx = _pattern(_segments("trillion_club_companies")["ciks"], r"array_to_string\(ciks, ','\)")
    for c in seed["companies"]:
        assert ciks_rx.search(",".join(c["ciks"])), c["slug"]
    pct_seg = _segments("trillion_club_stakes")["ownership_pct"]
    assert "ownership_pct <= 100" in pct_seg
    assert all(s["ownership_pct"] is None or s["ownership_pct"] <= 100 for s in seed["stakes"])


def test_the_parsed_check_replay_catches_bad_rows(seed):
    bad_c = copy.deepcopy(seed["companies"][0])
    bad_c.update(slug="NVIDIA!", card_kind="bogus", home_country="usa", display_name="",
                 manual_cap_usd=-5)
    got = {(col, why) for _, col, why in _check_rows_against_175([bad_c], "trillion_club_companies")}
    assert {("slug", "regex"), ("card_kind", "enum"), ("home_country", "regex"),
            ("display_name", "length"), ("manual_cap_usd", "positive")} <= got
    bad_s = copy.deepcopy(seed["stakes"][0])
    bad_s.update(kind="public", source_url="http://x", investee_cusip="abc", background="x" * 91,
                 ownership_pct=0, as_of=None)
    got = {(col, why) for _, col, why in _check_rows_against_175([bad_s], "trillion_club_stakes")}
    assert {("kind", "enum"), ("source_url", "regex"), ("investee_cusip", "regex"),
            ("background", "length"), ("ownership_pct", "positive"), ("as_of", "not null")} <= got


def test_the_table_constraints_of_175_hold_on_the_seed(seed, companies):
    # trillion_club_manual_cap_is_explicit / fmp_cap_has_symbol / 13f_needs_cik / whale_link_kind
    for c in seed["companies"]:
        if c["cap_source"] == "manual":
            assert c["manual_cap_usd"] and c["manual_cap_as_of"] and c["manual_cap_source_url"], c["slug"]
            assert c["membership_mode"] in ("force_in", "force_out"), c["slug"]
        else:
            assert c["cap_symbol"], c["slug"]
        if c["use_13f"]:
            assert c["card_kind"] == "thirteen_f" and len(c["ciks"]) >= 1, c["slug"]
        assert c["link_whale"] == (c["card_kind"] == "whale_link"), c["slug"]
    for s in seed["stakes"]:
        assert s["disclosed_value_usd"] is None or s["value_basis"] is not None
        assert s["kind"] != "commitment" or s["value_basis"] in (None, "committed_up_to")
        assert not (s["published"] and s["source_confidence"] == "secondary")
    keys = [seed_mod.stake_key(s) for s in seed["stakes"]]
    assert len(keys) == len(set(keys)), "UNIQUE (company_slug, investee_name, kind) would reject the seed"
    slugs = [c["slug"] for c in seed["companies"]]
    assert len(slugs) == len(set(slugs))


def test_the_script_validator_accepts_the_seed(seed):
    assert seed_mod.validate_seed(seed, today=TODAY) == []


# ══════════════════════════════════════════════════════════════════════════════════════
# 2. Editorial rules on the real seed (asserted directly, independent of the validator)
# ══════════════════════════════════════════════════════════════════════════════════════


def test_every_source_is_https(seed):
    for s in seed["stakes"]:
        assert s["source_url"].startswith("https://") and " " not in s["source_url"], seed_mod.stake_key(s)
    for c in seed["companies"]:
        u = c["manual_cap_source_url"]
        assert u is None or u.startswith("https://"), c["slug"]


def test_every_date_is_iso_and_verified_today(seed):
    iso = re.compile(r"\d{4}-\d{2}-\d{2}")
    for s in seed["stakes"]:
        for col in ("as_of", "verified_on", "listed_since"):
            v = s[col]
            if v is not None:
                assert iso.fullmatch(v) and date.fromisoformat(v), (seed_mod.stake_key(s), col)
        # Re-verifying a row moves verified_on forward; it may never be in the future, and
        # a figure cannot describe a date later than the day it was checked.
        assert date.fromisoformat(s["verified_on"]) <= date.today(), seed_mod.stake_key(s)
        assert s["as_of"] <= s["verified_on"], seed_mod.stake_key(s)
    for c in seed["companies"]:
        for col in ("manual_cap_as_of", "reviewed_on"):
            assert c[col] is None or (iso.fullmatch(c[col]) and date.fromisoformat(c[col]))


def test_background_is_short_and_states_what_happened(seed):
    for s in seed["stakes"]:
        b = s["background"]
        if b is None:
            continue
        assert 1 <= len(b) <= 90, (seed_mod.stake_key(s), len(b))
        assert not seed_mod.FORECAST_COPY.search(b), b


def test_kinds_and_bases_are_allowed(seed):
    from app.schemas.trillion_club import STAKE_KINDS, VALUE_BASES

    assert {s["kind"] for s in seed["stakes"]} <= set(STAKE_KINDS)
    assert {s["value_basis"] for s in seed["stakes"]} - {None} <= set(VALUE_BASES)


def test_secondary_rows_are_never_published(seed):
    for s in seed["stakes"]:
        if s["source_confidence"] == "secondary":
            assert s["published"] is False, seed_mod.stake_key(s)
        assert s["source_confidence"] in ("primary", "secondary")


def test_manual_caps_are_forced_and_complete(seed):
    manual = [c for c in seed["companies"] if c["cap_source"] == "manual"]
    assert {c["slug"] for c in manual} == {"saudi-aramco", "samsung", "sk-hynix"}
    for c in manual:
        assert c["membership_mode"] in ("force_in", "force_out"), c["slug"]
        assert c["manual_cap_usd"] > 0 and math.isfinite(c["manual_cap_usd"])
        assert c["manual_cap_as_of"] and c["manual_cap_source_url"].startswith("https://")
        assert c["manual_fx_rate"] > 0 and c["manual_fx_source"], c["slug"]
    assert next(c for c in manual if c["slug"] == "saudi-aramco")["manual_fx_rate"] == 3.75
    assert {c["slug"]: c["membership_mode"] for c in manual} == {
        "saudi-aramco": "force_in", "samsung": "force_in", "sk-hynix": "force_out"}
    for c in seed["companies"]:
        if c["cap_source"] != "manual":
            assert all(c[k] is None for k in ("manual_cap_usd", "manual_cap_as_of",
                                              "manual_cap_source_url", "manual_fx_rate",
                                              "manual_fx_source")), c["slug"]


def test_sk_hynix_is_never_sized_from_its_adr_automatically(companies):
    # FMP's SKHY figure ($1.318T) is an outlier against two independent sources ($965B/$994B).
    sk = companies["sk-hynix"]
    assert not (sk["cap_source"] == "fmp_adr" and sk["membership_mode"] == "auto")
    assert sk["cap_source"] == "manual" and sk["membership_mode"] == "force_out"
    assert sk["published"] is False and sk["cap_symbol"] is None


def test_13f_ingestion_is_opted_in_for_exactly_the_four_filers(seed):
    opted = {c["slug"] for c in seed["companies"] if c["use_13f"]}
    assert opted == {"nvidia", "alphabet", "amazon", "amd"}
    for c in seed["companies"]:
        if c["use_13f"]:
            assert c["card_kind"] == "thirteen_f" and c["ciks"], c["slug"]
    # JPMorgan's 13F is a 7,720-row book of CLIENT assets; Walmart's is tiny. Never auto-on.
    for slug in ("jpmorgan", "walmart", "berkshire"):
        assert next(c for c in seed["companies"] if c["slug"] == slug)["use_13f"] is False


def test_every_published_company_has_a_cap_source(seed):
    for c in seed["companies"]:
        if not c["published"]:
            continue
        if c["cap_source"] == "manual":
            assert c["manual_cap_usd"] and c["manual_cap_as_of"], c["slug"]
        else:
            assert c["cap_source"] in ("fmp_us", "fmp_adr") and c["cap_symbol"], c["slug"]
        assert c["reviewed_on"], c["slug"]


def test_every_stake_belongs_to_a_seeded_company(seed, companies):
    for s in seed["stakes"]:
        assert s["company_slug"] in companies, seed_mod.stake_key(s)


# Verified against data.sec.gov/submissions on 2026-09-24 (name + tickers per CIK).
_EDGAR_CIKS = {
    "nvidia": "0001045810", "apple": "0000320193", "alphabet": "0001652044",
    "microsoft": "0000789019", "amazon": "0001018724", "tsmc": "0001046179",
    "spacex": "0001181412", "meta": "0001326801", "broadcom": "0001730168",
    "tesla": "0001318605", "micron": "0000723125", "berkshire": "0001067983",
    "eli-lilly": "0000059478", "amd": "0000002488", "sk-hynix": "0002120882",
    "jpmorgan": "0000019617", "walmart": "0000104169", "samsung": "0000879316",
}


def test_ciks_are_ten_digit_and_match_edgar(seed):
    for c in seed["companies"]:
        for cik in c["ciks"]:
            assert re.fullmatch(r"\d{10}", cik), (c["slug"], cik)
        want = _EDGAR_CIKS.get(c["slug"])
        assert c["ciks"] == ([want] if want else []), c["slug"]
    aramco = next(c for c in seed["companies"] if c["slug"] == "saudi-aramco")
    assert aramco["ciks"] == []  # no SEC filings at all


def test_the_roster_is_the_sixteen_members_plus_an_unpublished_watch_list(seed):
    published = {c["slug"] for c in seed["companies"] if c["published"]}
    assert published == {
        "nvidia", "apple", "alphabet", "microsoft", "amazon", "tsmc", "spacex", "meta",
        "broadcom", "saudi-aramco", "tesla", "samsung", "micron", "berkshire", "eli-lilly", "amd"}
    assert {c["slug"] for c in seed["companies"] if not c["published"]} == {
        "sk-hynix", "jpmorgan", "walmart"}
    kinds = {c["slug"]: c["card_kind"] for c in seed["companies"]}
    assert kinds["tsmc"] == "non_us" and kinds["berkshire"] == "whale_link"
    assert {s for s, k in kinds.items() if k == "non_us"} == {"tsmc", "saudi-aramco", "samsung", "sk-hynix"}


def test_berkshire_links_to_its_whale_by_the_same_cik(companies):
    """The service resolves the whale id from `whales.cik` (uq_whales_cik); the registry that
    seeds `whales` must carry the very CIK this card lists, or the link card never renders."""
    registry = json.loads((BACKEND / "data" / "whale_registry.json").read_text(encoding="utf-8"))
    brk_ciks = {w["cik"] for w in registry if (w.get("firm_name") or "") == "Berkshire Hathaway"}
    assert companies["berkshire"]["ciks"] == ["0001067983"]
    assert brk_ciks == {"0001067983"}
    assert companies["berkshire"]["link_whale"] is True
    assert companies["berkshire"]["symbol_aliases"] == ["BRK-A"]
    assert companies["alphabet"]["symbol_aliases"] == ["GOOG"]


def test_berkshire_13f_holdings_are_never_duplicated_as_stakes(seed):
    brk = [s for s in seed["stakes"] if s["company_slug"] == "berkshire"]
    assert {s["investee_name"] for s in brk} == {
        "Mitsubishi Corporation", "ITOCHU Corporation", "Mitsui & Co.", "Marubeni Corporation",
        "Sumitomo Corporation"}
    assert all(s["kind"] == "non_us_listed" and s["material"] for s in brk)
    assert all(s["investee_us_symbol"] is None and s["investee_cusip"] is None for s in brk)


def test_material_means_a_disclosed_amount_or_percentage(seed):
    for s in seed["stakes"]:
        if s["material"]:
            assert s["ownership_pct"] is not None or s["disclosed_value_usd"] is not None, seed_mod.stake_key(s)
    micron = [s for s in seed["stakes"] if s["company_slug"] == "micron"]
    assert [(s["investee_name"], s["material"]) for s in micron] == [("Anthropic", False)]


def test_members_without_a_material_stake_get_no_home_card(seed):
    material_slugs = {s["company_slug"] for s in seed["stakes"] if s["material"] and s["published"]}
    assert "broadcom" not in material_slugs and "spacex" not in material_slugs
    assert "micron" not in material_slugs
    for slug in ("apple", "microsoft", "meta", "tesla", "eli-lilly", "tsmc", "samsung",
                 "saudi-aramco", "berkshire", "nvidia", "amazon", "amd", "alphabet"):
        assert slug in material_slugs, slug


def test_listing_kinds_carry_the_right_listing_fields(seed):
    for s in seed["stakes"]:
        k = seed_mod.stake_key(s)
        if s["kind"] == "us_listed_off_13f":
            assert s["investee_us_symbol"], k
        if s["kind"] == "non_us_listed":
            assert s["local_listing"] and s["investee_us_symbol"] is None, k
        if s["kind"] == "private":
            assert s["investee_us_symbol"] is None and s["local_listing"] is None, k
        if s["kind"] == "on_13f_note":
            assert s["company_slug"] in ("nvidia", "alphabet", "amazon", "amd"), k


def test_the_verified_investee_symbols(seed):
    """Symbols checked on EDGAR 2026-09-24 (tickers of each issuer's CIK). Ionetix has no
    ticker and its own 10-Q says its stock is not listed or quoted, so it is `private`."""
    sym = {(s["company_slug"], s["investee_name"]): s["investee_us_symbol"] for s in seed["stakes"]}
    assert sym[("eli-lilly", "ProQR Therapeutics")] == "PRQR"
    assert sym[("eli-lilly", "Aktis Oncology")] == "AKTS"
    assert sym[("eli-lilly", "Scribe Therapeutics")] == "SCTX"
    assert sym[("eli-lilly", "Ionetix")] is None
    assert sym[("nvidia", "Nebius")] == "NBIS" and sym[("nvidia", "Nokia")] == "NOK"
    assert sym[("amazon", "X-Energy")] == "XE" and sym[("samsung", "Corning")] == "GLW"
    assert sym[("tesla", "SpaceX")] == sym[("alphabet", "SpaceX")] == "SPCX"


# ── Banned wording ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "Alphabet", "Holdings", "Photonics", "XXVI Holdings Inc.", "Samsung Epis Holdings",
    "a follow-on offering", "Hotel Shilla", "Shotwell", "Betamax", "alphabetical", "copywriter",
    "Mitsui & Co.", "carried at $3.0B", "invested $2.00B",
    # A leading NOUN is not an instruction (the old start-of-field rule refused these).
    "Follow-on offering closed in Jan 2026.", "Mirror Biologics", "Copy.ai", "Mirror Holdings Ltd",
    "Follow-up financing closed in 2025.",
])
def test_banned_copy_lets_ordinary_names_through(text):
    assert not seed_mod.contains_banned_copy(text), text


@pytest.mark.parametrize("text", [
    "bullish", "loaded up", "Loaded Up on chips", "a hot stock", "smart money", "their top picks",
    "a bet on AI", "Bets", "SABIC-backed", "copy their trades", "Follow Berkshire",
    "mirroring these moves", "worth $5B", "bought shares", "high conviction", "vote of confidence",
    "an endorsement", "endorsed by", "secret stake", "a hidden gem", "bearish",
    # A leading imperative WITH an object is still an instruction.
    "Follow Berkshire into Japan's trading houses.", "Copy Ackman's portfolio", "Mirror insiders",
    "Stake since 2019. Follow NVIDIA.",
])
def test_banned_copy_catches_the_banned_phrases(text):
    assert seed_mod.contains_banned_copy(text), text


def test_no_seed_text_contains_banned_wording(seed):
    for c in seed["companies"]:
        for col in ("display_name", "manual_fx_source"):
            assert not seed_mod.contains_banned_copy(c[col]), (c["slug"], col)
    for s in seed["stakes"]:
        for col in seed_mod.STAKE_TEXT_FIELDS:
            assert not seed_mod.contains_banned_copy(s[col]), (seed_mod.stake_key(s), col, s[col])


def test_forecast_regex_is_not_vacuous():
    for bad in ("It will list next year.", "Expected to close in 2027.", "Plans to invest more.",
                "Likely to grow."):
        assert seed_mod.FORECAST_COPY.search(bad), bad
    for ok in ("Amazon agreed on Apr 13, 2026 to buy Globalstar.", "Willow Holdings",
               "Announced as Anthropic said it would scale Claude on Microsoft Azure."):
        assert not seed_mod.FORECAST_COPY.search(ok), ok


def test_every_published_stake_passes_the_read_time_validator(seed):
    """The service drops (with a WARNING) any stake its own validator rejects. A seeded row
    that the service would silently drop is a bug here, not there."""
    from app.services.trillion_club_service import stake_problem

    for s in seed["stakes"]:
        if s["published"]:
            assert stake_problem(s) is None, (seed_mod.stake_key(s), stake_problem(s))


# ══════════════════════════════════════════════════════════════════════════════════════
# 3. The validator — every rule proven able to fail (one mutation each)
# ══════════════════════════════════════════════════════════════════════════════════════


def _find(rows, **match):
    return next(r for r in rows if all(r.get(k) == v for k, v in match.items()))


def _mut_company(_slug, **changes):
    def apply(seed):
        _find(seed["companies"], slug=_slug).update(changes)
    return apply


def _mut_stake(_slug, _name, **changes):
    def apply(seed):
        _find(seed["stakes"], company_slug=_slug, investee_name=_name).update(changes)
    return apply


def _del(kind, col, **match):
    def apply(seed):
        del _find(seed[kind], **match)[col]
    return apply


def _dup(kind, **match):
    def apply(seed):
        seed[kind].append(copy.deepcopy(_find(seed[kind], **match)))
    return apply


_MUTATIONS = [
    # companies
    ("slug-case", _mut_company("nvidia", slug="NVIDIA"), "does not match"),
    ("slug-newline", _mut_company("nvidia", slug="nvidia\n"), "does not match"),
    ("slug-dup", _dup("companies", slug="apple"), "duplicate slug"),
    ("name-empty", _mut_company("apple", display_name=""), "display_name"),
    ("name-long", _mut_company("apple", display_name="A" * 61), "display_name"),
    ("name-banned", _mut_company("apple", display_name="Hot Picks"), "banned"),
    ("cik-short", _mut_company("apple", ciks=["320193"]), "10-digit"),
    ("cik-int", _mut_company("apple", ciks=[320193]), "10-digit"),
    ("cik-dup", _mut_company("apple", ciks=["0000320193", "0000320193"]), "duplicates"),
    ("card-kind", _mut_company("apple", card_kind="thirteenf"), "card_kind"),
    ("cap-source", _mut_company("apple", cap_source="fmp"), "cap_source"),
    ("mode", _mut_company("apple", membership_mode="auto_in"), "membership_mode"),
    ("bool-str", _mut_company("apple", published="true"), "published must be true/false"),
    ("bool-int", _mut_company("apple", use_13f=1), "use_13f must be true/false"),
    ("country", _mut_company("apple", home_country="usa"), "home_country"),
    ("symbol", _mut_company("apple", cap_symbol="aapl"), "cap_symbol"),
    ("alias-dup", _mut_company("alphabet", symbol_aliases=["GOOG", "GOOG"]), "symbol_aliases"),
    ("alias-self", _mut_company("alphabet", symbol_aliases=["GOOGL"]), "symbol_aliases"),
    ("manual-auto", _mut_company("samsung", membership_mode="auto"), "explicit call"),
    ("manual-no-url", _mut_company("samsung", manual_cap_source_url=None), "manual_cap_source_url"),
    ("manual-http", _mut_company("samsung", manual_cap_source_url="http://x.com"), "https"),
    ("manual-nan", _mut_company("samsung", manual_cap_usd=float("nan")), "positive finite"),
    ("manual-inf", _mut_company("samsung", manual_cap_usd=float("inf")), "positive finite"),
    ("manual-neg", _mut_company("samsung", manual_cap_usd=-1.0), "positive finite"),
    ("manual-bool", _mut_company("samsung", manual_cap_usd=True), "positive finite"),
    ("manual-no-fx", _mut_company("samsung", manual_fx_rate=None), "manual_fx_rate"),
    ("manual-bad-date", _mut_company("samsung", manual_cap_as_of="2026-02-30"), "ISO date"),
    ("fmp-no-symbol", _mut_company("apple", cap_symbol=None), "needs a cap_symbol"),
    ("fmp-leftover", _mut_company("apple", manual_fx_rate=1.0), "manual cap columns"),
    ("13f-wrong-kind", _mut_company("microsoft", use_13f=True), "use_13f needs"),
    ("13f-no-cik", _mut_company("nvidia", ciks=[]), "use_13f needs"),
    ("whale-link", _mut_company("berkshire", link_whale=False), "link_whale"),
    ("whale-link-other", _mut_company("apple", link_whale=True), "link_whale"),
    ("non-us-country", _mut_company("tsmc", home_country="US"), "non-US home_country"),
    ("us-card-abroad", _mut_company("apple", home_country="JP"), "needs card_kind non_us"),
    ("unreviewed", _mut_company("apple", reviewed_on=None), "reviewed_on"),
    ("job-column", _mut_company("apple", is_member=True), "job-written"),
    ("extra-column", _mut_company("apple", notes="x"), "unknown columns"),
    ("missing-column", _del("companies", "reviewed_on", slug="apple"), "missing columns"),
    # stakes
    ("stake-orphan", _mut_stake("apple", "Globalstar Licensee LLC", company_slug="pear"), "not a seeded company"),
    ("stake-kind", _mut_stake("apple", "Globalstar Licensee LLC", kind="public"), "kind"),
    ("stake-name-long", _mut_stake("apple", "Globalstar Licensee LLC", investee_name="G" * 61), "investee_name"),
    ("stake-name-space", _mut_stake("apple", "Globalstar Licensee LLC", investee_name=" Globalstar"), "investee_name"),
    ("cusip-lower", _mut_stake("tesla", "SpaceX", investee_cusip="84615q103"), "investee_cusip"),
    ("cusip-short", _mut_stake("tesla", "SpaceX", investee_cusip="84615Q10"), "investee_cusip"),
    ("pct-zero", _mut_stake("apple", "Globalstar Licensee LLC", ownership_pct=0), "ownership_pct"),
    ("pct-over", _mut_stake("apple", "Globalstar Licensee LLC", ownership_pct=100.5), "ownership_pct"),
    ("pct-nan", _mut_stake("apple", "Globalstar Licensee LLC", ownership_pct=float("nan")), "ownership_pct"),
    ("pct-bool", _mut_stake("apple", "Globalstar Licensee LLC", ownership_pct=True), "ownership_pct"),
    ("pct-str", _mut_stake("apple", "Globalstar Licensee LLC", ownership_pct="20"), "ownership_pct"),
    ("pct-no-basis", _mut_stake("apple", "Globalstar Licensee LLC", ownership_basis=None), "go together"),
    ("basis-no-pct", _mut_stake("tesla", "SpaceX", ownership_basis="of Class A"), "go together"),
    ("value-zero", _mut_stake("tesla", "SpaceX", disclosed_value_usd=0), "disclosed_value_usd"),
    ("value-no-basis", _mut_stake("tesla", "SpaceX", value_basis=None), "needs a value_basis"),
    ("basis-no-value", _mut_stake("apple", "Globalstar Licensee LLC", disclosed_value_usd=None), "without a disclosed"),
    ("basis-unknown", _mut_stake("tesla", "SpaceX", value_basis="market_value"), "value_basis"),
    ("commitment-basis", _mut_stake("microsoft", "Anthropic", value_basis="invested"), "committed_up_to"),
    ("up-to-not-commitment", _mut_stake("tesla", "SpaceX", value_basis="committed_up_to"), "only for kind commitment"),
    ("as-of-bad", _mut_stake("tesla", "SpaceX", as_of="2026-13-01"), "as_of"),
    ("as-of-us-format", _mut_stake("tesla", "SpaceX", as_of="Jun 30, 2026"), "as_of"),
    ("as-of-after-verify", _mut_stake("tesla", "SpaceX", as_of="2026-09-30"), "after verified_on"),
    ("verified-future", _mut_stake("tesla", "SpaceX", verified_on="2026-09-25", as_of="2026-06-30"), "in the future"),
    ("verified-missing", _mut_stake("tesla", "SpaceX", verified_on=None), "verified_on"),
    ("listed-since-bad", _mut_stake("tesla", "SpaceX", listed_since="June 2026"), "listed_since"),
    ("url-http", _mut_stake("tesla", "SpaceX", source_url="http://www.sec.gov/x"), "https"),
    ("url-space", _mut_stake("tesla", "SpaceX", source_url="https://www.sec.gov/a b"), "https"),
    ("url-bare", _mut_stake("tesla", "SpaceX", source_url="https://"), "https"),
    ("title-long", _mut_stake("tesla", "SpaceX", source_title="T" * 121), "source_title"),
    ("title-empty", _mut_stake("tesla", "SpaceX", source_title=""), "source_title"),
    ("confidence", _mut_stake("tesla", "SpaceX", source_confidence="tertiary"), "source_confidence"),
    ("secondary-published", _mut_stake("tesla", "SpaceX", source_confidence="secondary"), "never be published"),
    ("material-str", _mut_stake("tesla", "SpaceX", material="true"), "material must be true/false"),
    ("sort-negative", _mut_stake("tesla", "SpaceX", sort_order=-1), "sort_order"),
    ("sort-bool", _mut_stake("tesla", "SpaceX", sort_order=True), "sort_order"),
    ("sort-float", _mut_stake("tesla", "SpaceX", sort_order=1.5), "sort_order"),
    ("background-long", _mut_stake("tesla", "SpaceX", background="x" * 91), "background"),
    ("background-empty", _mut_stake("tesla", "SpaceX", background=""), "background"),
    ("background-forecast", _mut_stake("tesla", "SpaceX", background="The lock-up will end in December."), "forecast"),
    ("background-banned", _mut_stake("tesla", "SpaceX", background="A bullish move."), "banned"),
    ("backed", _mut_stake("tesla", "SpaceX", ownership_basis="SABIC-backed"), "banned"),
    ("name-banned-stake", _mut_stake("tesla", "SpaceX", investee_name="SpaceX (hot pick)"), "banned"),
    ("material-no-amount", _mut_stake("micron", "Anthropic", material=True), "material needs"),
    ("note-on-non-filer", _mut_stake("tesla", "SpaceX", kind="on_13f_note"), "only on a thirteen_f card"),
    ("off13f-no-symbol", _mut_stake("tesla", "SpaceX", investee_us_symbol=None), "needs the investee_us_symbol"),
    ("non-us-no-listing", _mut_stake("tsmc", "Xintec", local_listing=None), "needs a local_listing"),
    ("private-with-symbol", _mut_stake("apple", "Globalstar Licensee LLC", investee_us_symbol="GSAT"), "has no listing"),
    ("stake-dup", _dup("stakes", company_slug="tesla", investee_name="SpaceX"), "duplicate (company_slug"),
    ("stake-near-dup", lambda seed: seed["stakes"].append(dict(
        copy.deepcopy(_find(seed["stakes"], company_slug="tesla", investee_name="SpaceX")),
        investee_name="SPACEX")), "near-duplicate"),
    ("sort-int4-overflow", _mut_stake("tesla", "SpaceX", sort_order=2 ** 31), "sort_order"),
    ("nul-in-url", _mut_stake("tesla", "SpaceX", source_url="https://www.sec.gov/\u0000"), "cannot store"),
    ("invisible-in-name", _mut_stake("tesla", "SpaceX", investee_name="Space\u200bX"), "invisible"),
    ("tab-in-background", _mut_stake("tesla", "SpaceX", background="Invested\tin 2026."), "invisible"),
    ("company-reclaims-cik", lambda seed: seed["companies"].append(dict(
        copy.deepcopy(_find(seed["companies"], slug="apple")), slug="apple-2", cap_symbol="AAPL2",
        detail_symbol="AAPL2", logo_symbol="AAPL2", published=False)), "already claimed"),
    ("stake-missing-col", _del("stakes", "background", company_slug="tesla", investee_name="SpaceX"), "missing columns"),
    ("stake-extra-col", _mut_stake("tesla", "SpaceX", id="x"), "unknown columns"),
]


@pytest.mark.parametrize("name,mutate,expect", _MUTATIONS, ids=[m[0] for m in _MUTATIONS])
def test_each_validator_rule_can_fail(seed, name, mutate, expect):
    bad = copy.deepcopy(seed)
    mutate(bad)
    problems = seed_mod.validate_seed(bad, today=TODAY)
    assert any(expect in p for p in problems), (name, problems)


def test_a_rejected_row_names_its_identifiers(seed):
    bad = copy.deepcopy(seed)
    _mut_stake("tesla", "SpaceX", source_url="http://x")(bad)
    (problem,) = seed_mod.validate_seed(bad, today=TODAY)
    assert "tesla" in problem and "SpaceX" in problem and "us_listed_off_13f" in problem


# ── Loading ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("payload,expect", [
    ('{"companies": [], "stakes": [], "extra": []}', "exactly"),
    ('[{"slug": "x"}]', "exactly"),
    ('{"companies": {}, "stakes": []}', "list of objects"),
    ('{"companies": [1], "stakes": []}', "list of objects"),
    ('{"companies": [{"manual_cap_usd": NaN}], "stakes": []}', "non-finite"),
    ('{"companies": [{"manual_cap_usd": Infinity}], "stakes": []}', "non-finite"),
    ('{"companies": [], "stakes": [', "cannot read"),
])
def test_load_seed_rejects_malformed_files(tmp_path, payload, expect):
    p = tmp_path / "seed.json"
    p.write_text(payload, encoding="utf-8")
    with pytest.raises(seed_mod.SeedError, match=expect):
        seed_mod.load_seed(str(p))


def test_load_seed_missing_file(tmp_path):
    with pytest.raises(seed_mod.SeedError, match="cannot read"):
        seed_mod.load_seed(str(tmp_path / "nope.json"))


def test_empty_seed_is_valid_but_plans_nothing():
    empty = {"companies": [], "stakes": []}
    assert seed_mod.validate_seed(empty, today=TODAY) == []
    plan = seed_mod.plan_sync(empty, [], [])
    assert not plan.has_changes and plan.orphan_companies == [] and plan.orphan_stakes == []


# ══════════════════════════════════════════════════════════════════════════════════════
# 4. Planning and applying — against an in-memory fake Supabase (never the real one)
# ══════════════════════════════════════════════════════════════════════════════════════


class _Boom(RuntimeError):
    pass


class _FakeQuery:
    def __init__(self, db: "_FakeSupabase", table: str):
        self.db, self.table_name = db, table
        self.op, self.payload, self.filters = None, None, []

    def select(self, cols):
        self.op, self.payload = "select", cols
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def execute(self):
        db = self.db
        db.calls.append((self.op, self.table_name, copy.deepcopy(self.payload), list(self.filters)))
        fail = db.fail.get((self.op, self.table_name))
        if fail is not None and fail(self.payload, self.filters):
            raise _Boom(f"{self.op} {self.table_name} failed")
        rows = db.tables.setdefault(self.table_name, [])
        if self.op == "select":
            return type("R", (), {"data": copy.deepcopy(rows)})()
        if self.op == "insert":
            if self.table_name == seed_mod.STAKES_TABLE:
                slugs = {c["slug"] for c in db.tables.get(seed_mod.COMPANIES_TABLE, [])}
                if self.payload["company_slug"] not in slugs:
                    raise _Boom("FK violation")
            rows.append(copy.deepcopy(self.payload))
            return type("R", (), {"data": [self.payload]})()
        if self.op == "update":
            hit = [r for r in rows if all(r.get(c) == v for c, v in self.filters)]
            for r in hit:
                r.update(copy.deepcopy(self.payload))
            return type("R", (), {"data": hit})()
        raise AssertionError(f"unexpected op {self.op}")


class _FakeSupabase:
    """Only select / insert / update exist — a delete would be an AttributeError."""

    def __init__(self, tables=None, fail=None):
        self.tables = copy.deepcopy(tables or {})
        self.fail = fail or {}
        self.calls: List[tuple] = []

    def table(self, name):
        return _FakeQuery(self, name)

    def writes(self):
        return [c for c in self.calls if c[0] in ("insert", "update")]


def _db_from_seed(seed):
    return {seed_mod.COMPANIES_TABLE: copy.deepcopy(seed["companies"]),
            seed_mod.STAKES_TABLE: copy.deepcopy(seed["stakes"])}


def test_plan_on_an_empty_database_inserts_everything(seed):
    plan = seed_mod.plan_sync(seed, [], [])
    assert len(plan.company_inserts) == len(seed["companies"])
    assert len(plan.stake_inserts) == len(seed["stakes"])
    assert not plan.company_updates and not plan.stake_updates
    for row in plan.company_inserts:
        assert not set(row) & set(seed_mod.JOB_COLUMNS)


def test_plan_tolerates_postgrest_representations(seed):
    db_c = copy.deepcopy(seed["companies"])
    for c in db_c:
        if c["manual_cap_usd"] is not None:
            c["manual_cap_usd"] = int(c["manual_cap_usd"])  # DOUBLE back as an int
        c["is_member"] = True                                # job columns are not compared
    db_s = copy.deepcopy(seed["stakes"])
    for s in db_s:
        s["id"] = "00000000-0000-0000-0000-000000000000"
        if s["ownership_pct"] is not None:
            s["ownership_pct"] = float(s["ownership_pct"])
    plan = seed_mod.plan_sync(seed, db_c, db_s)
    assert not plan.has_changes, (plan.company_updates, plan.stake_updates)


def test_plan_reports_drift_column_by_column(seed):
    db = _db_from_seed(seed)
    _find(db[seed_mod.COMPANIES_TABLE], slug="nvidia")["published"] = False
    _find(db[seed_mod.STAKES_TABLE], company_slug="tesla", investee_name="SpaceX")["disclosed_value_usd"] = 3.0e9
    del _find(db[seed_mod.STAKES_TABLE], company_slug="meta", investee_name="Scale AI")["background"]
    plan = seed_mod.plan_sync(seed, db[seed_mod.COMPANIES_TABLE], db[seed_mod.STAKES_TABLE])
    assert plan.company_updates == [("nvidia", {"published": (False, True)})]
    diffs = dict(plan.stake_updates)
    assert diffs[("tesla", "SpaceX", "us_listed_off_13f")] == {"disclosed_value_usd": (3.0e9, 3.007e9)}
    assert diffs[("meta", "Scale AI", "private")]["background"][0] == "<absent>"


def test_bool_and_number_never_compare_equal():
    assert not seed_mod._same(True, 1) and not seed_mod._same(0, False)
    assert seed_mod._same(1.66e12, 1660000000000) and not seed_mod._same(1.66e12, 1.67e12)
    assert not seed_mod._same(["GOOG"], ["GOOG", "GOOGL"]) and seed_mod._same([], [])
    assert not seed_mod._same(None, 0) and seed_mod._same(None, None)


def test_orphans_are_reported_never_deleted(seed, caplog):
    db = _db_from_seed(seed)
    db[seed_mod.COMPANIES_TABLE].append(dict(_find(seed["companies"], slug="apple"), slug="oracle"))
    db[seed_mod.STAKES_TABLE].append(dict(_find(seed["stakes"], company_slug="apple"),
                                          investee_name="Something Else"))
    fake = _FakeSupabase(db)
    with caplog.at_level("WARNING"):
        assert seed_mod.main(["--apply", "--update"], sb=fake) == 0
    assert fake.writes() == []
    assert "DRIFT" in caplog.text and "oracle" in caplog.text and "Something Else" in caplog.text
    assert len(fake.tables[seed_mod.COMPANIES_TABLE]) == len(seed["companies"]) + 1


def test_dry_run_is_the_default_and_writes_nothing(seed):
    assert seed_mod.build_parser().parse_args([]).apply is False
    fake = _FakeSupabase()
    assert seed_mod.main([], sb=fake) == 0
    assert fake.writes() == []
    assert [c[:2] for c in fake.calls] == [("select", seed_mod.COMPANIES_TABLE),
                                           ("select", seed_mod.STAKES_TABLE)]


def test_apply_inserts_companies_before_stakes_and_is_idempotent(seed):
    fake = _FakeSupabase()
    assert seed_mod.main(["--apply"], sb=fake) == 0
    ops = [(op, t) for op, t, *_ in fake.writes()]
    assert ops.count(("insert", seed_mod.COMPANIES_TABLE)) == len(seed["companies"])
    assert ops.count(("insert", seed_mod.STAKES_TABLE)) == len(seed["stakes"])
    last_company = max(i for i, o in enumerate(ops) if o[1] == seed_mod.COMPANIES_TABLE)
    first_stake = min(i for i, o in enumerate(ops) if o[1] == seed_mod.STAKES_TABLE)
    assert last_company < first_stake
    for op, table, payload, _ in fake.writes():
        assert not set(payload) & set(seed_mod.JOB_COLUMNS)
        assert "id" not in payload
    before = len(fake.calls)
    assert seed_mod.main(["--apply", "--update"], sb=fake) == 0
    assert [c for c in fake.calls[before:] if c[0] != "select"] == []


def test_apply_without_update_leaves_drift_alone(seed):
    db = _db_from_seed(seed)
    _find(db[seed_mod.COMPANIES_TABLE], slug="nvidia")["published"] = False
    fake = _FakeSupabase(db)
    assert seed_mod.main(["--apply"], sb=fake) == 0
    assert fake.writes() == []
    assert _find(fake.tables[seed_mod.COMPANIES_TABLE], slug="nvidia")["published"] is False


def test_update_overwrites_only_the_drifted_columns(seed):
    db = _db_from_seed(seed)
    _find(db[seed_mod.COMPANIES_TABLE], slug="nvidia").update(published=False, is_member=True)
    _find(db[seed_mod.STAKES_TABLE], company_slug="tesla", investee_name="SpaceX")["background"] = "old"
    fake = _FakeSupabase(db)
    assert seed_mod.main(["--apply", "--update"], sb=fake) == 0
    writes = fake.writes()
    assert len(writes) == 2
    (_, t1, p1, f1), (_, t2, p2, f2) = writes
    assert t1 == seed_mod.COMPANIES_TABLE and f1 == [("slug", "nvidia")]
    assert set(p1) == {"published", "updated_at"} and p1["published"] is True
    assert t2 == seed_mod.STAKES_TABLE
    assert f2 == [("company_slug", "tesla"), ("investee_name", "SpaceX"), ("kind", "us_listed_off_13f")]
    assert set(p2) == {"background", "updated_at"}
    # the job-written column was not touched
    assert _find(fake.tables[seed_mod.COMPANIES_TABLE], slug="nvidia")["is_member"] is True


def test_update_without_apply_is_refused():
    fake = _FakeSupabase()
    assert seed_mod.main(["--update"], sb=fake) == 2
    assert fake.calls == []
    assert seed_mod.main(["--validate-only", "--apply"], sb=fake) == 2
    assert fake.calls == []


def test_validate_only_never_touches_the_database():
    fake = _FakeSupabase()
    assert seed_mod.main(["--validate-only"], sb=fake) == 0
    assert fake.calls == []


def test_an_invalid_seed_writes_nothing_and_reads_nothing(seed, tmp_path):
    bad = copy.deepcopy(seed)
    _mut_stake("tesla", "SpaceX", source_confidence="secondary")(bad)
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    fake = _FakeSupabase()
    assert seed_mod.main(["--apply", "--seed", str(p)], sb=fake) == 2
    assert fake.calls == []


def test_a_read_failure_is_never_treated_as_an_empty_table():
    fake = _FakeSupabase(fail={("select", seed_mod.STAKES_TABLE): lambda p, f: True})
    assert seed_mod.main(["--apply"], sb=fake) == 1
    assert fake.writes() == []


def test_one_failed_row_does_not_abort_the_rest(seed, caplog):
    fake = _FakeSupabase(fail={
        ("insert", seed_mod.COMPANIES_TABLE): lambda p, f: p["slug"] == "apple",
        ("insert", seed_mod.STAKES_TABLE): lambda p, f: p["investee_name"] == "Scale AI",
    })
    with caplog.at_level("ERROR"):
        assert seed_mod.main(["--apply"], sb=fake) == 1
    inserted_c = {r["slug"] for r in fake.tables[seed_mod.COMPANIES_TABLE]}
    inserted_s = {seed_mod.stake_key(r) for r in fake.tables[seed_mod.STAKES_TABLE]}
    assert inserted_c == {c["slug"] for c in seed["companies"]} - {"apple"}
    # Apple's stake was skipped (its company is missing), not attempted into an FK error.
    assert not any(k[0] == "apple" for k in inserted_s)
    assert not any(c[0] == "insert" and c[1] == seed_mod.STAKES_TABLE and c[2]["company_slug"] == "apple"
                   for c in fake.calls)
    assert len(inserted_s) == len(seed["stakes"]) - 1 - 1
    assert "FAILED to insert company apple" in caplog.text
    assert "SKIPPED stake apple / Globalstar Licensee LLC" in caplog.text
    assert "FAILED to insert stake meta / Scale AI (private): _Boom" in caplog.text


def test_a_failed_update_is_counted_and_the_rest_continue(seed):
    db = _db_from_seed(seed)
    _find(db[seed_mod.COMPANIES_TABLE], slug="nvidia")["published"] = False
    _find(db[seed_mod.COMPANIES_TABLE], slug="apple")["published"] = False
    fake = _FakeSupabase(db, fail={("update", seed_mod.COMPANIES_TABLE):
                                   lambda p, f: ("slug", "nvidia") in f})
    assert seed_mod.main(["--apply", "--update"], sb=fake) == 1
    assert _find(fake.tables[seed_mod.COMPANIES_TABLE], slug="apple")["published"] is True


# ══════════════════════════════════════════════════════════════════════════════════════
# 5. Script hygiene
# ══════════════════════════════════════════════════════════════════════════════════════

_SCRIPT = BACKEND / "scripts" / "seed_trillion_club.py"


def test_the_script_says_it_writes_to_production():
    doc = ast.get_docstring(ast.parse(_SCRIPT.read_text(encoding="utf-8")))
    assert "PRODUCTION" in doc and "--apply" in doc and "never deletes" in doc.lower()


def test_the_script_never_deletes_and_imports_no_client_at_module_level():
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not calls & {"delete", "upsert", "rpc"}, calls & {"delete", "upsert", "rpc"}
    top_imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any(isinstance(n, ast.ImportFrom) and n.module == "app.database" for n in top_imports)
    assert not any(isinstance(n, ast.ImportFrom) and (n.module or "").startswith("app.integrations")
                   for n in ast.walk(tree))


# ══════════════════════════════════════════════════════════════════════════════════════
# 6. Collateral pins — grants, atlas curation, pending-table list
# ══════════════════════════════════════════════════════════════════════════════════════


def _literal_assign(path: Path, name: str) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None)
        if isinstance(target, ast.Name) and target.id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path.name}")


def test_grants_test_lists_the_175_tables():
    path = BACKEND / "tests" / "test_table_grants_service_role_only.py"
    service_only = _literal_assign(path, "_SERVICE_ROLE_ONLY")
    client = _literal_assign(path, "_CLIENT_ACCESS_BY_DESIGN")
    assert set(service_only["175"]) == set(_NEW_TABLES)
    everywhere = [t for group in service_only.values() for t in group]
    for t in _NEW_TABLES:
        assert everywhere.count(t) == 1, t
        assert t not in client, t
    # and the migration really does revoke + grant each one
    sql = _strip_comments(MIGRATION.read_text(encoding="utf-8"))
    for t in _NEW_TABLES:
        assert re.search(rf"REVOKE ALL ON public\.{t}\s+FROM anon, authenticated;", sql), t
        assert re.search(rf"GRANT ALL ON public\.{t}\s+TO service_role;", sql), t
        assert re.search(rf"ALTER TABLE public\.{t}\s+ENABLE ROW LEVEL SECURITY;", sql), t


def test_schema_doc_lists_the_175_tables_as_pending():
    pending = _literal_assign(BACKEND / "tests" / "test_schema_doc_generator.py",
                              "_PENDING_MIGRATION_TABLES")
    assert {f"public.{t}" for t in _NEW_TABLES} <= pending


def test_atlas_curation_names_only_real_175_columns():
    """The atlas's stale-column guard skips PENDING tables, so until 175 is applied and
    re-dumped this is the only check that a curated key column really exists."""
    import sys

    sys.path.insert(0, str(BACKEND / "scripts"))
    try:
        import schema_curation as cur
    finally:
        sys.path.remove(str(BACKEND / "scripts"))
    for t in _NEW_TABLES:
        doc = cur.CURATION[f"public.{t}"]
        assert doc.domain in {d.key for d in cur.DOMAINS}
        assert doc.purpose and "175" in doc.note
        cols = set(_columns(t))
        assert cols, t
        stale = [k for k in doc.key if k not in cols]
        assert stale == [], (t, stale)


# ══════════════════════════════════════════════════════════════════════════════════════
# 7. Regressions from the 2026-09-24 hardening pass
# ══════════════════════════════════════════════════════════════════════════════════════


def test_the_copy_rules_are_one_shared_module():
    """The seed script and the request path (trillion_club_service) must refuse the SAME
    wording, so the rules live once in app/services/trillion_club/copy_rules.py. A second
    copy in the script is how "bought"/"worth"/"endorsed"/a leading "Follow …" drifted."""
    from app.services.trillion_club import copy_rules

    assert seed_mod.BANNED_COPY is copy_rules.BANNED_COPY
    assert seed_mod.FORECAST_COPY is copy_rules.FORECAST_COPY
    assert seed_mod.STAKE_TEXT_FIELDS is copy_rules.STAKE_TEXT_FIELDS
    assert seed_mod.contains_banned_copy is copy_rules.contains_banned_copy
    assert seed_mod.contains_forecast is copy_rules.contains_forecast
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    assigned = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)}
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert not assigned & {"BANNED_COPY", "FORECAST_COPY", "STAKE_TEXT_FIELDS"}, "re-declared in the script"
    assert not defined & {"contains_banned_copy", "contains_forecast"}, "re-declared in the script"


def test_the_copy_rules_module_stays_pure():
    """It is imported by the seed script (which must validate without credentials) and by the
    request path: stdlib only — no Supabase, no FMP, no settings."""
    path = BACKEND / "app" / "services" / "trillion_club" / "copy_rules.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    modules |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert modules <= {"__future__", "re", "typing"}, modules


def test_anthropic_commitments_say_part_was_already_invested(seed):
    """Anthropic's Series G release (Feb 12, 2026, re-read 2026-09-24) says the round
    "includes a portion of the previously announced investments from Microsoft and NVIDIA".
    The two Nov 18, 2025 "up to" commitments must not read as money not yet invested, so
    each carries exactly that — attributed and dated — in its background."""
    rows = {s["company_slug"]: s for s in seed["stakes"]
            if s["investee_name"] == "Anthropic" and s["kind"] == "commitment"}
    assert set(rows) == {"nvidia", "microsoft"}
    for slug, want in (("nvidia", 1.0e10), ("microsoft", 5.0e9)):
        s = rows[slug]
        assert s["background"] == ("Anthropic said its Feb 12, 2026 Series G included a portion "
                                   "of this investment."), slug
        assert (s["value_basis"], s["disclosed_value_usd"], s["as_of"]) == ("committed_up_to", want, "2025-11-18")
        assert s["verified_on"] >= "2026-02-12", slug   # checked after the release it cites


def test_berkshire_profile_title_matches_its_2025_letter():
    """The Berkshire card links to the whale profile, which renders `title` under the name.
    Berkshire's 2025 letter (signed "Gregory E. Abel, Chief Executive Officer, February 28,
    2026") calls Warren "Berkshire's Chairman". The owner pushes the title to the `whales`
    row with scripts/sync_whale_registry.py."""
    registry = json.loads((BACKEND / "data" / "whale_registry.json").read_text(encoding="utf-8"))
    (brk,) = [w for w in registry if w.get("cik") == "0001067983"]
    assert brk["name"] == "Warren Buffett"
    assert brk["title"] == "Berkshire Hathaway Chairman"
    assert "CEO" not in brk["title"] and "chairman" in brk["description"].lower()


@pytest.mark.parametrize("raw,why", [
    (b'{"companies": [], "stakes": [\xff]}', "UnicodeDecodeError"),
    (b'{"companies": [{"manual_cap_usd": 1' + b"0" * 5000 + b'}], "stakes": []}', "ValueError"),
    (b'{"companies": [], "stakes": ' + b"[" * 100000 + b"]" * 100000 + b"}", "RecursionError"),
    (b'{"companies": [], "companies": [], "stakes": []}', "duplicate key"),
], ids=["not-utf8", "int-past-4300-digits", "nested-100k-deep", "duplicate-top-level-key"])
def test_an_unreadable_seed_file_exits_2_never_a_traceback(tmp_path, raw, why):
    """load_seed turns every way json.load can fail into SeedError, so main() reports it and
    exits 2 (a 5,000-digit integer is valid JSON that Python refuses to convert)."""
    p = tmp_path / "seed.json"
    p.write_bytes(raw)
    with pytest.raises(seed_mod.SeedError, match=why):
        seed_mod.load_seed(str(p))
    assert seed_mod.main(["--validate-only", "--seed", str(p)]) == 2


def test_a_crash_inside_a_row_check_is_a_problem_not_a_traceback(seed, monkeypatch):
    """Fail-closed backstop: a rule that raises on some unforeseen value becomes a named
    problem (exit 2, nothing written), never an exception out of validate_seed."""
    def boom(row, *a, **k):
        raise TypeError("unforeseen value")

    monkeypatch.setattr(seed_mod, "stake_problems", boom)
    problems = seed_mod.validate_seed(seed, today=TODAY)
    assert len(problems) == len(seed["stakes"])
    assert all("could not be validated (TypeError: unforeseen value)" in p for p in problems)
