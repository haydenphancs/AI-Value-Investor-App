"""The reviewed stake-finder member file (`backend/data/trillion_club_filers.json`) against the seed.

WHY THIS FILE EXISTS
--------------------
`stake_contracts.parse_member_configs` validates the file's SHAPE (a bad entry stops the finder
with one ERROR). It cannot see whether the file agrees with the rest of the club, and every
disagreement here fails SILENTLY in production — the finder simply searches the wrong thing:

* a published member left out of the file gets no automatic search at all (one monthly
  heads-up), so a missing or typo'd slug quietly turns the feature off for that company;
* a member-role CIK that is not the company's own reads someone else's 13D/13Gs as the member's;
* an affiliate CIK copied into `trillion_club_companies.ciks` turns on 13F ingestion for it
  (that column drives the 13F jobs — the file's own note forbids it);
* an alias with a non-ASCII character makes `sec_edgar.full_text_search` refuse the whole query
  (it accepts printable ASCII only), and a bare word ("Apple") matches thousands of filings;
* a member-role reporting name missing from `names` never matches the member's own cover line;
* a `source_name` that differs from the seed's spelling makes every auto row's title disagree
  with the hand-kept rows ("Eli Lilly Schedule 13G" beside "Lilly Schedule 13D").

Each rule is a function returning problems, run on the real file (must be empty) and on a
mutated copy (must name the defect) — so no rule can pass vacuously.

Hermetic: reads two checked-in JSON files; no network, no database.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import pytest

from app.services.trillion_club import copy_rules
from app.services.trillion_club import stake_contracts as sc
from app.services.trillion_club import stake_names as sn

BACKEND = Path(__file__).resolve().parents[1]
FILERS = BACKEND / "data" / "trillion_club_filers.json"
SEED = BACKEND / "data" / "trillion_club_seed.json"

# Published, non-whale_link companies that are deliberately NOT in the filers file, each with
# the reason. Empty today: every one is listed. A company published later fails the coverage
# test until it is either listed or added here with its reason.
_NOT_LISTED_BY_DESIGN: Dict[str, str] = {}

# Companies that must never be listed, whatever their published state.
_NEVER_LIST: Dict[str, str] = {
    "berkshire": "whale_link: its holdings show on the whale profile, and gate 2 never searches "
                 "a whale_link company — listing it would only look like coverage",
    "jpmorgan": "a fiduciary filer: its 13Gs report CLIENT assets (type HC / BK), so every one "
                "would arrive as a proposal — the reason the finder is opt-in per member",
}

_TOP_KEYS = frozenset({"version", "reviewed_on", "note", "members"})
_MEMBER_KEYS = frozenset({"slug", "source_name", "names", "s1_filers", "s2_aliases", "intermediates"})
_FILER_KEYS = frozenset({"cik", "role", "names", "types"})
#: sec_edgar.full_text_search refuses anything but printable ASCII in the query.
_PRINTABLE_ASCII = re.compile(r"[\x20-\x7e]+")
_ALIAS_MAX = 100
_SOURCE_TITLE_MAX = 120   # migration 175: source_title is 1-120 characters


# ── loading ─────────────────────────────────────────────────────────────────────────────────


def _no_duplicate_keys(pairs):
    keys = [k for k, _ in pairs]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise ValueError(f"duplicate JSON keys {dupes}")
    return dict(pairs)


def _raw_filers() -> Dict[str, Any]:
    return json.loads(FILERS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def members() -> Dict[str, sc.MemberConfig]:
    return sc.load_member_configs(str(FILERS))


@pytest.fixture(scope="module")
def seed() -> Dict[str, List[Dict[str, Any]]]:
    return json.loads(SEED.read_text(encoding="utf-8"))


def _companies(seed: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
    return {c["slug"]: c for c in seed["companies"]}


# ── the rules (each returns its problems) ───────────────────────────────────────────────────


def coverage_problems(members: Mapping[str, sc.MemberConfig],
                      companies: Mapping[str, Mapping[str, Any]],
                      *, not_listed: Mapping[str, str] = _NOT_LISTED_BY_DESIGN,
                      never_list: Mapping[str, str] = _NEVER_LIST) -> List[str]:
    out: List[str] = []
    for slug in members:
        if slug not in companies:
            out.append(f"{slug}: listed in the filers file but not a seed company (a typo searches nothing)")
        if slug in never_list:
            out.append(f"{slug}: must never be listed — {never_list[slug]}")
        elif companies.get(slug, {}).get("card_kind") == "whale_link":
            out.append(f"{slug}: a whale_link company is never searched (gate 2) — remove it")
    for slug, c in companies.items():
        required = c.get("published") is True and c.get("card_kind") != "whale_link"
        if required and slug not in members and slug not in not_listed:
            out.append(f"{slug}: a published member with no filers entry and no reason in "
                       "_NOT_LISTED_BY_DESIGN — it would get no automatic search at all")
    for slug, why in not_listed.items():
        c = companies.get(slug)
        if c is None:
            out.append(f"_NOT_LISTED_BY_DESIGN[{slug!r}]: not a seed company (stale entry)")
        elif not (c.get("published") is True and c.get("card_kind") != "whale_link"):
            out.append(f"_NOT_LISTED_BY_DESIGN[{slug!r}]: not a published member — the entry is not needed")
        if slug in members:
            out.append(f"_NOT_LISTED_BY_DESIGN[{slug!r}]: but it IS listed — contradictory")
        if len(why.strip()) < 20:
            out.append(f"_NOT_LISTED_BY_DESIGN[{slug!r}]: needs a real reason")
    return out


def cik_problems(members: Mapping[str, sc.MemberConfig],
                 companies: Mapping[str, Mapping[str, Any]]) -> List[str]:
    out: List[str] = []
    owner_of = {cik: slug for slug, c in companies.items() for cik in (c.get("ciks") or [])}
    for slug, m in members.items():
        seed_ciks = set(companies.get(slug, {}).get("ciks") or [])
        for f in m.s1_filers:
            if f.role == "member" and f.cik not in seed_ciks:
                out.append(f"{slug}: member-role CIK {f.cik} is not one of its seed CIKs "
                           f"{sorted(seed_ciks)} — it would read someone else's 13D/13Gs as {slug}'s")
            if f.role == "affiliate" and f.cik in owner_of:
                out.append(f"{slug}: affiliate CIK {f.cik} is in {owner_of[f.cik]}'s "
                           "trillion_club_companies.ciks — that column drives 13F ingestion")
    return out


def _folded_tokens(name: str) -> List[str]:
    return sn.fold_entity_name(name).split()


def alias_problems(members: Mapping[str, sc.MemberConfig]) -> List[str]:
    out: List[str] = []
    claimed: Dict[str, str] = {}
    names_of: Dict[str, str] = {}
    for slug, m in members.items():
        for n in m.names + tuple(n for f in m.s1_filers for n in f.names):
            names_of.setdefault(sn.fold_entity_name(n), slug)
    for slug, m in members.items():
        own_names = m.names + tuple(n for f in m.s1_filers for n in f.names)
        for alias in m.s2_aliases:
            if not _PRINTABLE_ASCII.fullmatch(alias):
                out.append(f"{slug}: alias {alias!r} is not printable ASCII — full_text_search "
                           "refuses the whole query")
            if any(ch in alias for ch in '"()'):
                out.append(f"{slug}: alias {alias!r} has a quote or parenthesis (EFTS reads them literally)")
            if len(alias) > _ALIAS_MAX:
                out.append(f"{slug}: alias {alias!r} is over {_ALIAS_MAX} characters")
            if len(alias.split()) < 2:
                out.append(f"{slug}: alias {alias!r} is a bare word — it matches thousands of filings")
            if not sn.significant_tokens(alias):
                out.append(f"{slug}: alias {alias!r} names no company (only legal-form words)")
            tokens = _folded_tokens(alias)
            if not any(_folded_tokens(n)[: len(tokens)] == tokens for n in own_names):
                out.append(f"{slug}: alias {alias!r} is not the start of any of its own names "
                           f"{list(own_names)} — a copy-paste from another member?")
            key = " ".join(tokens)
            if key in claimed and claimed[key] != slug:
                out.append(f"{slug}: alias {alias!r} is also {claimed[key]}'s — one hit, two members")
            claimed.setdefault(key, slug)
            if names_of.get(key, slug) != slug:
                out.append(f"{slug}: alias {alias!r} is {names_of[key]}'s own name")
    return out


def name_problems(members: Mapping[str, sc.MemberConfig]) -> List[str]:
    out: List[str] = []
    owner: Dict[str, str] = {}
    for slug, m in members.items():
        member_keys = {sn.fold_entity_name(n) for n in m.names}
        for f in m.s1_filers:
            if f.role == "member":
                missing = [n for n in f.names if sn.fold_entity_name(n) not in member_keys]
                if missing:
                    out.append(f"{slug}: member-role filer {f.cik} files as {missing}, which are not "
                               "in the member's `names` — its own cover line would never match")
        every = set(m.names) | {n for f in m.s1_filers for n in f.names}
        for n in sorted(every):
            key = sn.fold_entity_name(n)
            if not key:
                out.append(f"{slug}: name {n!r} folds to nothing")
                continue
            if owner.setdefault(key, slug) != slug:
                out.append(f"{slug}: name {n!r} is also claimed by {owner[key]} — one cover line, two members")
    return out


def source_name_problems(members: Mapping[str, sc.MemberConfig],
                         stakes: Sequence[Mapping[str, Any]]) -> List[str]:
    out: List[str] = []
    for slug, m in members.items():
        title = sn.schedule_source_title(m.source_name, "SCHEDULE 13G/A", date(2026, 9, 30))
        if title is None or len(title) > _SOURCE_TITLE_MAX:
            out.append(f"{slug}: source_name {m.source_name!r} cannot make a 1-{_SOURCE_TITLE_MAX} "
                       f"character source_title ({title!r})")
        elif copy_rules.contains_banned_copy(title) or copy_rules.contains_banned_copy(m.source_name):
            out.append(f"{slug}: source_name {m.source_name!r} trips the copy rules — every auto "
                       "row's title would be refused")
        for s in stakes:
            t = s.get("source_title") or ""
            if s.get("company_slug") == slug and re.search(r"\bSchedule 13[DG]\b", t):
                if not t.startswith(f"{m.source_name} Schedule 13"):
                    out.append(f"{slug}: the seed row {s.get('investee_name')!r} is titled {t!r}, "
                               f"but source_name {m.source_name!r} would title the same filing "
                               "differently")
    return out


_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_CIK = re.compile(r"[0-9]{10}")


def format_problems(members: Mapping[str, sc.MemberConfig]) -> List[str]:
    """parse_member_configs checks slugs and CIKs with `re.match` and a `$` anchor, and Python's
    `$` also matches BEFORE a trailing newline — so "0001824723\\n" loads as an affiliate CIK and
    then never equals the issuer CIK a filing carries (the about_member gate misses). fullmatch."""
    out: List[str] = []
    for slug, m in members.items():
        if not _SLUG.fullmatch(slug) or m.slug != slug:
            out.append(f"{slug!r}: not a plain slug")
        for f in m.s1_filers:
            if not _CIK.fullmatch(f.cik) or f.cik == "0000000000":
                out.append(f"{slug}: filer CIK {f.cik!r} is not exactly 10 digits")
    return out


def key_problems(raw: Mapping[str, Any]) -> List[str]:
    """Unknown keys are IGNORED by parse_member_configs, so a misspelt or invented field
    ("affiliates", "use_13f") reads as configured while doing nothing."""
    out: List[str] = []
    extra = set(raw) - _TOP_KEYS
    if extra:
        out.append(f"top level: unknown keys {sorted(extra)}")
    for i, m in enumerate(raw.get("members") or []):
        if not isinstance(m, Mapping):
            continue
        if set(m) != _MEMBER_KEYS:
            out.append(f"members[{i}] ({m.get('slug')}): keys {sorted(m)} != {sorted(_MEMBER_KEYS)}")
        for j, f in enumerate(m.get("s1_filers") or []):
            if isinstance(f, Mapping) and set(f) != _FILER_KEYS:
                out.append(f"members[{i}].s1_filers[{j}]: keys {sorted(f)} != {sorted(_FILER_KEYS)}")
    return out


# ── the real file ───────────────────────────────────────────────────────────────────────────


def test_the_real_file_loads_through_the_contract_loader(members):
    assert len(members) >= 10, sorted(members)
    assert members == sc.parse_member_configs(_raw_filers())
    # Spot-check that the loader really read the reviewed entries (not an empty default).
    assert members["alphabet"].filer_ciks == ("0001652044", "0001824723")
    assert [f.role for f in members["alphabet"].s1_filers] == ["member", "affiliate"]
    assert "Google LLC" in members["alphabet"].s2_aliases


def test_the_file_has_no_duplicate_json_keys():
    """json.load keeps the LAST of two equal keys silently — a second "s2_aliases" in one
    member would drop the first list with no error anywhere."""
    json.loads(FILERS.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)


def test_the_file_has_only_known_keys():
    assert key_problems(_raw_filers()) == []


def test_slugs_and_ciks_are_exact(members):
    assert format_problems(members) == []


def test_reviewed_on_is_an_iso_date_not_in_the_future():
    raw = _raw_filers()
    reviewed = date.fromisoformat(raw["reviewed_on"])
    assert date(2026, 10, 1) <= reviewed <= date.today(), reviewed


def test_every_published_member_is_listed_or_excluded_with_a_reason(members, seed):
    assert coverage_problems(members, _companies(seed)) == []


def test_member_ciks_are_the_companys_own_and_affiliates_are_never_13f_ciks(members, seed):
    assert cik_problems(members, _companies(seed)) == []


def test_aliases_are_searchable_legal_names_of_their_own_member(members):
    assert alias_problems(members) == []


def test_reporting_names_reach_the_member_and_belong_to_one_member_only(members):
    assert name_problems(members) == []


def test_source_names_spell_titles_the_way_the_seed_does(members, seed):
    assert source_name_problems(members, seed["stakes"]) == []
    # Non-vacuity: the real seed holds Schedule 13 rows for these members, so the title rule
    # above compared real titles rather than an empty set.
    titled = {s["company_slug"] for s in seed["stakes"] if "Schedule 13" in s["source_title"]}
    assert {"nvidia", "alphabet", "amazon", "eli-lilly"} <= titled & set(members), titled


def test_members_without_s1_filers_still_search_by_alias(members):
    """Samsung and Aramco file no Schedule 13D/G with SEC: they are S2-only, and S2 needs aliases."""
    for slug, m in members.items():
        if not m.s1_filers:
            assert m.s2_aliases, f"{slug}: neither S1 filers nor S2 aliases"


# ── the rules can fail (each mutation must be named) ───────────────────────────────────────


def _replace_member(members, slug, **changes):
    out = dict(members)
    out[slug] = dataclasses.replace(members[slug], **changes)
    return out


def _replace_filer(members, slug, index, **changes):
    m = members[slug]
    filers = list(m.s1_filers)
    filers[index] = dataclasses.replace(filers[index], **changes)
    return _replace_member(members, slug, s1_filers=tuple(filers))


def test_coverage_rule_names_each_kind_of_gap(members, seed):
    companies = _companies(seed)
    dropped = {k: v for k, v in members.items() if k != "micron"}
    assert any(p.startswith("micron:") for p in coverage_problems(dropped, companies))
    # ... and a reasoned exclusion silences exactly that gap.
    assert coverage_problems(dropped, companies,
                             not_listed={"micron": "a deliberate, reviewed exclusion for the test"}) == []
    assert any("not a seed company" in p
               for p in coverage_problems({**members, "eli-lily": members["eli-lilly"]}, companies))
    assert any("must never be listed" in p
               for p in coverage_problems({**members, "berkshire": members["nvidia"]}, companies))
    assert any("contradictory" in p
               for p in coverage_problems(members, companies,
                                          not_listed={"nvidia": "listed AND excluded, a contradiction"}))
    assert any("not a published member" in p
               for p in coverage_problems(members, companies,
                                          not_listed={"walmart": "unpublished, so the entry is stale"}))
    assert any("needs a real reason" in p
               for p in coverage_problems(dropped, companies, not_listed={"micron": "n/a"}))
    unpublished = copy.deepcopy(companies)
    unpublished["micron"]["published"] = False
    assert coverage_problems(dropped, unpublished) == [], "an unpublished company need not be listed"


def test_cik_rule_names_a_wrong_member_cik_and_a_13f_affiliate(members, seed):
    companies = _companies(seed)
    wrong = _replace_filer(members, "nvidia", 0, cik="0000002488")       # AMD's CIK
    assert any("nvidia: member-role CIK 0000002488" in p for p in cik_problems(wrong, companies))
    leak = _replace_filer(members, "alphabet", 1, cik="0001067983")      # Berkshire's CIK
    assert any("affiliate CIK 0001067983 is in berkshire's" in p for p in cik_problems(leak, companies))


@pytest.mark.parametrize("alias, expect", [
    ("Société Générale SA", "not printable ASCII"),
    ("Tesla​, Inc.", "not printable ASCII"),
    ('Tesla "Inc."', "quote or parenthesis"),
    ("Tesla", "bare word"),
    ("Holdings Inc.", "names no company"),
    ("Tesla Motors, Inc.", "not the start of any of its own names"),
    ("Tesla, Inc. " + "x" * 100, "over 100 characters"),
])
def test_alias_rule_names_each_defect(members, alias, expect):
    mutated = _replace_member(members, "tesla", s2_aliases=(alias,))
    assert any(expect in p for p in alias_problems(mutated)), alias_problems(mutated)


def test_alias_rule_names_an_alias_two_members_share(members):
    shared = _replace_member(members, "tesla", s2_aliases=("Tesla, Inc.", "Apple Inc."),
                             names=members["tesla"].names + ("Apple Inc.",))
    problems = alias_problems(shared)
    assert any("also apple's" in p or "is apple's own name" in p for p in problems), problems


def test_name_rule_names_an_unmatched_member_line_and_a_shared_name(members):
    unmatched = _replace_filer(members, "micron", 0, names=("Micron Technology, Inc.", "MICRON TECH"))
    assert any("micron: member-role filer" in p for p in name_problems(unmatched))
    shared = _replace_member(members, "amd", names=members["amd"].names + ("NVIDIA CORP",))
    assert any("also claimed by" in p for p in name_problems(shared))


def test_source_name_rule_names_a_mismatch_and_banned_copy(members, seed):
    renamed = _replace_member(members, "eli-lilly", source_name="Eli Lilly")
    assert any("eli-lilly: the seed row" in p for p in source_name_problems(renamed, seed["stakes"]))
    banned = _replace_member(members, "micron", source_name="Smart Money")
    assert any("trips the copy rules" in p for p in source_name_problems(banned, seed["stakes"]))


def test_format_rule_names_a_trailing_newline_the_loader_lets_through(members):
    raw = _raw_filers()
    alphabet = next(m for m in raw["members"] if m["slug"] == "alphabet")
    alphabet["s1_filers"][1]["cik"] = "0001824723\n"              # Google LLC, the affiliate
    with pytest.raises(ValueError, match="bad filer CIK"):        # the loader refuses it now
        sc.parse_member_configs(raw)
    # ...and the format rule still names it on a config built around the loader.
    google = dataclasses.replace(members["alphabet"].s1_filers[1], cik="0001824723\n")
    loaded = {"alphabet": dataclasses.replace(
        members["alphabet"], s1_filers=(members["alphabet"].s1_filers[0], google))}
    assert any("filer CIK '0001824723\\n'" in p for p in format_problems(loaded))
    renamed = {"nvidia\n": dataclasses.replace(members["nvidia"], slug="nvidia\n")}
    assert any("not a plain slug" in p for p in format_problems(renamed))


def test_key_rule_names_unknown_keys():
    raw = _raw_filers()
    raw["members"][0]["affiliates"] = []
    raw["members"][1]["s1_filers"][0]["use_13f"] = True
    raw["extra"] = 1
    problems = key_problems(raw)
    assert len(problems) == 3, problems
    # The loader refuses each of the three on its own too.
    for level in ("file", "member", "filer"):
        one = _raw_filers()
        if level == "file":
            one["extra"] = 1
        elif level == "member":
            one["members"][0]["affiliates"] = []
        else:
            one["members"][1]["s1_filers"][0]["use_13f"] = True
        with pytest.raises(ValueError, match="unknown key"):
            sc.parse_member_configs(one)


def test_duplicate_key_hook_refuses_a_repeated_key():
    with pytest.raises(ValueError, match="duplicate JSON keys"):
        json.loads('{"members": [], "members": []}', object_pairs_hook=_no_duplicate_keys)
