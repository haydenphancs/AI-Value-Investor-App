"""The Congress block-list file (`data/congress_roster.json`) and its owner-run refresh script.

Members of Congress are never named in a company-news post (owner decision 2026-10-09). The roster
is the block-list's main source, so its SHAPE is pinned here: names only (no party, state,
district, term or id), at least one full Congress of current members, the canonical text format
the refresh script writes, and agreement with the whale registry's politicians.

The refresh script (`scripts/refresh_congress_roster.py`) is tested on its pure parts and its
dry-run / write paths with local fixture files — never the network (the suite is hermetic and the
script's fetch is the owner's to run).
"""

import ast
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.services.marketing import company_news_rules as R
from scripts import refresh_congress_roster as S

_BACKEND = Path(__file__).resolve().parents[1]
ROSTER = _BACKEND / "data" / "congress_roster.json"


def _doc():
    return json.loads(ROSTER.read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _the_real_roster_is_never_touched():
    """Every test here must leave the committed roster byte-identical (a write test once bound the
    default path at definition time and replaced the real file)."""
    before = ROSTER.read_bytes()
    mode = ROSTER.stat().st_mode
    yield
    assert ROSTER.read_bytes() == before, "a test modified data/congress_roster.json"
    assert ROSTER.stat().st_mode == mode


# ── the committed file ────────────────────────────────────────────────────────

def test_the_roster_file_has_the_pinned_shape():
    doc = _doc()
    assert set(doc) == {"_about", "fetched_on", "congress_start", "members"} == set(S.ROSTER_KEYS)
    assert "CC0" in doc["_about"] and "names only" in doc["_about"]
    fetched = date.fromisoformat(doc["fetched_on"])
    # rr11: the Congress its current members serve in — the one sitting on its fetch date
    start = date.fromisoformat(doc["congress_start"])
    assert (start.month, start.day) == (1, 3) and start.year % 2 == 1
    assert start == R.congress_start_on_or_before(fetched)
    members = doc["members"]
    assert len(members) >= R.ROSTER_MIN_MEMBERS
    assert sum(1 for m in members if m["current"] is True) >= R.ROSTER_MIN_MEMBERS
    for m in members:
        assert set(m) <= set(S.MEMBER_KEYS), m
        assert {"first", "last", "current"} <= set(m), m
        assert isinstance(m["first"], str) and m["first"].strip() == m["first"] and m["first"]
        assert isinstance(m["last"], str) and m["last"].strip() == m["last"] and m["last"]
        assert isinstance(m["current"], bool)
        for k in ("middle", "nickname", "official_full"):
            if k in m:
                assert isinstance(m[k], str) and m[k].strip()


def test_the_roster_validates_and_is_in_canonical_text_form():
    doc = _doc()
    S.validate_roster(doc)
    assert S.render(doc) == ROSTER.read_text(encoding="utf-8"), "write it with the refresh script only"
    keys = [(m["last"].lower(), m["first"].lower()) for m in doc["members"]]
    assert keys == sorted(keys)


def test_every_registry_politician_is_blocked_and_most_are_in_the_roster():
    registry = json.loads((_BACKEND / "data" / "whale_registry.json").read_text(encoding="utf-8"))
    politicians = [r["name"] for r in registry if r.get("category") == "politicians"]
    assert len(politicians) >= 11
    roster_names = {f"{m['first']} {m['last']}".lower() for m in _doc()["members"]}
    roster_names |= {m["official_full"].lower() for m in _doc()["members"] if "official_full" in m}
    for name in politicians:
        assert R.is_congress_name(name), name
    assert sum(1 for n in politicians if n.lower() in roster_names) >= 8


def test_the_roster_loads_through_the_rules_module():
    assert R.person_names_allowed() is True
    assert len(R.congress_names()) >= len(_doc()["members"])


def test_every_registry_politician_is_blocked_under_the_legal_forms_of_their_name():
    """Not tautological (review round 9): the registry names are the everyday forms ("Josh
    Gottheimer"); EDGAR files the LEGAL name, last first ("GOTTHEIMER JOSHUA S"). Every
    nickname-group twin of each politician's first name, under their surname, with and without a
    middle initial, is blocked."""
    registry = json.loads((_BACKEND / "data" / "whale_registry.json").read_text(encoding="utf-8"))
    groups = {n: g for g in R.NICKNAME_GROUPS for n in g}
    missed = []
    for row in registry:
        if row.get("category") != "politicians":
            continue
        first, *_mid, last = row["name"].lower().split()
        for alt in sorted(groups.get(first, {first})):
            for form in (f"{last} {alt}", f"{last} {alt} q", f"{alt} q. {last}"):
                if not R.is_congress_name(form.upper() if " q." not in form else form.title()):
                    missed.append(form)
    assert not missed, missed


def test_the_shipped_roster_is_fresh_for_the_launch():
    """The committed roster may name people on the first Company Weekly run (a fixed date: this
    never expires); `R.roster_fresh_for` is what turns names off as it ages."""
    assert R.roster_fetched_on() == date.fromisoformat(_doc()["fetched_on"])
    assert R.roster_fresh_for(R.COMPANY_WEEKLY_LAUNCH) is True


def test_the_shipped_roster_is_not_fresh_from_the_day_the_next_congress_is_sworn_in():
    """rr11 #1 / #2, for ANY committed roster (this holds after every refresh): the roster holds the
    Congress that began on its ``congress_start``; from the next odd-year January 3 — no grace — it
    is not fresh (both Form 4 series are refused) until it is refreshed. For today's file (the
    119th Congress, fetched 2026-10-09) that is: fresh on 2026-12-31, not fresh on 2027-01-03."""
    doc = _doc()
    held = date.fromisoformat(doc["congress_start"])
    fetched = date.fromisoformat(doc["fetched_on"])
    sworn_in = date(held.year + 2, 1, 3)
    assert R.roster_congress_start() == held
    assert R.roster_fresh_for(sworn_in) is False
    assert R.roster_fresh_for(sworn_in + timedelta(days=14)) is False      # round 10's grace is gone
    eve = sworn_in - timedelta(days=3)                                     # December 31
    assert R.roster_fresh_for(eve) is ((eve - fetched).days <= R.ROSTER_MAX_AGE_DAYS)


# ── the refresh script: pure parts ────────────────────────────────────────────

def _person(first, last, *, ends=("2027-01-03",), starts=None, bioguide=None, nickname=None, official=None,
            middle=None, **extra):
    """A source person. Each term starts two years before it ends unless ``starts`` says otherwise
    (``None`` in ``starts`` = a term with no start)."""
    name = {"first": first, "last": last, **({"nickname": nickname} if nickname else {}),
            **({"official_full": official} if official else {}), **({"middle": middle} if middle is not None else {})}
    starts = starts if starts is not None else tuple(f"{int(e[:4]) - 2}{e[4:]}" for e in ends)
    terms = [{**({"start": b} if b is not None else {}), "end": e, "party": "Z", "state": "ZZ", "district": 9}
             for b, e in zip(starts, ends)]
    return {"id": {"bioguide": bioguide or f"B{first}{last}"}, "name": name, "terms": terms,
            "bio": {"birthday": "1960-01-01"}, **extra}


def test_build_roster_keeps_current_and_recent_members_names_only():
    current = [_person("Alma", "Adams", official="Alma S. Adams"), _person("Garland", "Barr", nickname="Andy")]
    historical = [_person("Old", "Timer", ends=("1999-01-03",)),
                  _person("Recent", "Leaver", ends=("2019-01-03", "2021-01-03")),
                  _person("Gone", "Since", ends=("2021-01-02",)),
                  _person("No", "Terms", ends=())]
    doc = S.build_roster(current, historical, fetched_on="2026-10-09")
    names = [(m["first"], m["last"], m["current"]) for m in doc["members"]]
    assert names == [("Alma", "Adams", True), ("Garland", "Barr", True), ("Recent", "Leaver", False)]
    assert doc["members"][0] == {"first": "Alma", "last": "Adams", "official_full": "Alma S. Adams", "current": True}
    assert doc["members"][1] == {"first": "Garland", "last": "Barr", "nickname": "Andy", "current": True}
    flat = json.dumps(doc["members"])
    for leaked in ("party", "state", "district", "bioguide", "birthday", "terms", "ZZ", "start", "end"):
        assert leaked not in flat, leaked
    assert doc["fetched_on"] == "2026-10-09" and "2026-10-09" in doc["_about"]
    assert list(doc) == list(S.ROSTER_KEYS) and doc["congress_start"] == "2025-01-03"


def test_build_roster_keeps_the_middle_name_and_the_block_list_indexes_it(tmp_path, monkeypatch):
    """rr10 #4: the dataset's ``name.middle`` used to be dropped, so a member known by the middle
    name (Form 4: first + middle) lost the link. It is kept — a name, nothing else — and the rules
    module indexes it as a given name."""
    current = [_person("Joseph", "Zqxhollow", middle="Albert", nickname="Trey"),
               _person("Ann", "Zqxplain", middle="  "),                    # blank: not kept
               *_synthetic()[0]]
    doc = S.build_roster(current, [], fetched_on="2026-10-09")
    S.validate_roster(doc)
    by_last = {m["last"]: m for m in doc["members"]}
    assert by_last["Zqxhollow"] == {"first": "Joseph", "middle": "Albert", "last": "Zqxhollow", "nickname": "Trey",
                                    "current": True}
    assert "middle" not in by_last["Zqxplain"]
    path = tmp_path / "roster.json"
    path.write_text(S.render(doc), encoding="utf-8")
    R._congress_state.cache_clear()
    monkeypatch.setattr(R, "CONGRESS_ROSTER_PATH", path)
    try:
        assert R.person_names_allowed() is True
        assert R.is_congress_name("ZQXHOLLOW ALBERT") and R.is_congress_name("ZQXHOLLOW JOSEPH A")
        assert not R.is_congress_name("ZQXHOLLOW MARY")
    finally:
        monkeypatch.undo()
        R._congress_state.cache_clear()


def test_build_roster_prefers_current_and_skips_junk():
    current = [_person("Same", "Person", bioguide="X1"), "junk", {"name": "no id"}, _person(" ", "Blank")]
    historical = [_person("Same", "Person", bioguide="X1", ends=("2025-01-03",)), None]
    doc = S.build_roster(current, historical, fetched_on="2026-10-09")
    assert [(m["first"], m["current"]) for m in doc["members"]] == [("Same", True)]
    with pytest.raises(S.RosterError):
        S.build_roster({"not": "a list"}, [], fetched_on="2026-10-09")


def _synthetic(n_current=540, n_hist=10, *, ends=("2027-01-03",)):
    current = [_person(f"First{i}", f"Last{i:04d}", ends=ends) for i in range(n_current)]
    historical = [_person(f"Hist{i}", f"Gone{i:04d}", ends=("2023-01-03",)) for i in range(n_hist)]
    return current, historical


def test_validate_roster_refuses_what_the_reader_would_not_trust():
    current, historical = _synthetic()
    good = S.build_roster(current, historical, fetched_on="2026-10-09")
    S.validate_roster(good)
    small = S.build_roster(*_synthetic(n_current=R.ROSTER_MIN_MEMBERS - 1), fetched_on="2026-10-09")
    with pytest.raises(S.RosterError):
        S.validate_roster(small)
    for mutate in (
        lambda d: d.update(extra=1),
        lambda d: d.update(fetched_on="yesterday"),
        lambda d: d.update(members="x"),
        lambda d: d["members"][0].update(party="Z"),
        lambda d: d["members"][0].update(first=""),
        lambda d: d["members"][0].update(current="yes"),
        lambda d: d["members"].append(None),
        lambda d: d["members"][0].update(middle=""),
        lambda d: d["members"][0].update(middle=7),
        lambda d: d["members"][0].update(nickname=" "),
        # rr11: the Congress it holds — missing, malformed, not an odd-year January 3, older than
        # the one sitting on its build date (2026-10-09: the 119th, 2025-01-03), or after it
        lambda d: d.pop("congress_start"),
        lambda d: d.update(congress_start=None),
        lambda d: d.update(congress_start="x"),
        lambda d: d.update(congress_start=20250103),
        lambda d: d.update(congress_start="2025-01-04"),
        lambda d: d.update(congress_start="2026-01-03"),
        lambda d: d.update(congress_start="2023-01-03"),
        lambda d: d.update(congress_start="2027-01-03"),
    ):
        bad = json.loads(json.dumps(good))
        mutate(bad)
        with pytest.raises(S.RosterError):
            S.validate_roster(bad)


# ── rr11: congress_start — which Congress the roster holds, read from the source ──

def test_the_scripts_congress_start_mirrors_the_rules_module():
    day = date(2019, 1, 1)
    while day <= date(2031, 1, 10):
        assert S.congress_start_on_or_before(day) == R.congress_start_on_or_before(day), day
        day += timedelta(days=1)


def _house(n, start="2025-01-03", end="2027-01-03"):
    return [_person(f"Rep{i}", f"House{i:04d}", ends=(end,), starts=(start,)) for i in range(n)]


def _senate(n, start, end, tag):
    return [_person(f"Sen{tag}{i}", f"Senate{tag}{i:03d}", ends=(end,), starts=(start,)) for i in range(n)]


def _congress_119():
    """441 House seats + 100 senators in three classes, a special-election rep (start mid-Congress),
    the Puerto Rico commissioner's 4-year term and a member with an earlier term first."""
    return (_house(439)
            + [_person("Special", "Elected", ends=("2027-01-03",), starts=("2025-04-01",)),
               _person("Resident", "Commissioner", ends=("2029-01-03",), starts=("2025-01-03",)),
               _person("Long", "Server", ends=("2021-01-03", "2023-01-03", "2027-01-03"),
                       starts=("2019-01-03", "2021-01-03", "2025-01-03"))]
            + _senate(33, "2025-01-03", "2031-01-03", "a") + _senate(33, "2023-01-03", "2029-01-03", "b")
            + _senate(34, "2021-01-03", "2027-01-03", "c"))


def test_build_roster_reads_the_congress_from_the_members_latest_terms():
    """The rule: each current member's LATEST term start, snapped to the odd-year January 3 on or
    before it (the special election of 2025-04-01 counts toward the 119th); the January 3 shared by
    MORE THAN HALF of the current members. Senators keep older starts and do not move it."""
    doc = S.build_roster(_congress_119(), [], fetched_on="2026-10-09")
    assert doc["congress_start"] == "2025-01-03"
    S.validate_roster(doc)
    assert S.infer_congress_start([date(2025, 4, 1), date(2026, 3, 10), date(2023, 1, 3)]) == date(2025, 1, 3)
    # exactly half is no majority; unreadable starts count in the total and support nothing
    assert S.infer_congress_start([date(2025, 1, 3), date(2023, 1, 3)]) is None
    assert S.infer_congress_start([date(2025, 1, 3), None, None]) is None
    assert S.infer_congress_start([date(2025, 1, 3), date(2025, 1, 3), None]) == date(2025, 1, 3)
    assert S.infer_congress_start([]) is None and S.infer_congress_start([None]) is None
    assert S.latest_term_span(_person("A", "B", ends=("2021-01-03", "2027-01-03"),
                                      starts=("2019-01-03", "2025-01-03"))) == (date(2025, 1, 3), date(2027, 1, 3))
    assert S.latest_term_span({"terms": [{"start": "0001-01-03", "end": "x"}, "junk"]}) == (None, None)
    assert S.latest_term_span({"terms": "nope"}) == (None, None)


def test_a_dataset_with_no_majority_congress_is_refused():
    current = _house(300, start="2025-01-03") + _house(300, start="2023-01-03", end="2027-01-03")
    current = [dict(p, id={"bioguide": f"X{i}"}) for i, p in enumerate(current)]
    doc = S.build_roster(current, [], fetched_on="2026-10-09")
    assert doc["congress_start"] is None
    with pytest.raises(S.RosterError, match="congress_start is missing"):
        S.validate_roster(doc)
    no_starts = _synthetic(ends=("2027-01-03",))[0]
    for person in no_starts:
        for term in person["terms"]:
            term.pop("start")
    with pytest.raises(S.RosterError, match="congress_start is missing"):
        S.validate_roster(S.build_roster(no_starts, [], fetched_on="2026-10-09"))


def test_a_january_4_refresh_still_holding_the_119th_is_refused(sources, target, monkeypatch):
    """rr11 #1: on 2027-01-04 the CC0 dataset can still list the outgoing (119th) Congress — about
    540 'current' members whose terms end 2027-01-03. Round 10 accepted it (its fetched_on was after
    January 3) and trusted it until 2028. Now the build is refused and nothing is written; the owner
    retries a few days later."""
    stale = S.build_roster(*_synthetic(), fetched_on="2027-01-04")
    assert stale["congress_start"] == "2025-01-03" and sum(m["current"] for m in stale["members"]) == 540
    with pytest.raises(S.RosterError, match="does not list the new Congress yet"):
        S.validate_roster(stale)
    for day in ("2027-01-03", "2027-01-05", "2027-12-31", "2028-06-01"):
        with pytest.raises(S.RosterError):
            S.validate_roster(dict(stale, fetched_on=day, _about=S._about(day)))
    S.validate_roster(dict(stale, fetched_on="2027-01-02", _about=S._about("2027-01-02")))   # the eve: fine
    target_before = target.read_bytes()
    monkeypatch.setattr(S, "_today", lambda: date(2027, 1, 4))
    assert S.main(["--from-dir", str(sources), "--write"]) == 1
    assert target.read_bytes() == target_before
    assert sorted(p.name for p in target.parent.iterdir()) == ["congress_roster.json"]


def test_a_synthetic_120th_congress_roster_is_built_and_fresh_from_the_day_it_sits(tmp_path, monkeypatch):
    """The dataset lists the 120th Congress (terms 2027-01-03 → 2029-01-03): built on 2027-01-03 it
    validates, records congress_start 2027-01-03, and the rules module reads it as fresh that day."""
    current, historical = _synthetic(ends=("2029-01-03",))
    doc = S.build_roster(current, historical, fetched_on="2027-01-03")
    assert doc["congress_start"] == "2027-01-03"
    S.validate_roster(doc)
    path = tmp_path / "roster.json"
    S.write_roster(doc, path)
    R._congress_state.cache_clear()
    monkeypatch.setattr(R, "CONGRESS_ROSTER_PATH", path)
    try:
        assert R.roster_congress_start() == date(2027, 1, 3)
        assert R.roster_fresh_for(date(2027, 1, 3)) is True
        assert R.roster_fresh_for(date(2029, 1, 3)) is False       # the 121st Congress sits
    finally:
        monkeypatch.undo()
        R._congress_state.cache_clear()


def test_a_dataset_half_way_through_a_new_congress_is_refused():
    """A current member whose latest term ended on or before the majority's Congress start is an
    outgoing member the dataset has not moved yet — its new members may be missing too. Refused,
    while senators' and the 4-year commissioner's running terms never trip it."""
    new = _house(300, start="2027-01-03", end="2029-01-03")
    leftover = [_person(f"Out{i}", f"Going{i:04d}", ends=("2027-01-03",)) for i in range(240)]
    with pytest.raises(S.RosterError, match="mid-way through a new Congress"):
        S.build_roster(new + leftover, [], fetched_on="2027-01-04")
    with pytest.raises(S.RosterError, match="1 current member"):
        S.build_roster(_house(540, start="2027-01-03", end="2029-01-03") + leftover[:1], [], fetched_on="2027-01-04")
    # control: the same members moved to `historical` are fine (and still blocked by name)
    doc = S.build_roster(_house(540, start="2027-01-03", end="2029-01-03"), leftover, fetched_on="2027-01-04")
    S.validate_roster(doc)
    assert doc["congress_start"] == "2027-01-03" and sum(not m["current"] for m in doc["members"]) == 240


def test_main_records_the_build_date_and_the_congress(sources, target):
    assert S.main(["--from-dir", str(sources), "--write"]) == 0
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert (doc["fetched_on"], doc["congress_start"]) == ("2026-10-09", "2025-01-03")


def test_diff_summary():
    old = {"members": [{"first": "A", "last": "B"}, {"first": "C", "last": "D"}]}
    new = {"members": [{"first": "C", "last": "D"}, {"first": "E", "last": "F"}]}
    assert S.diff_summary(old, new) == (["E F"], ["A B"])
    assert S.diff_summary({}, new) == (["C D", "E F"], [])


def test_minimum_and_paths_are_pinned():
    assert S.ROSTER_MIN_MEMBERS == R.ROSTER_MIN_MEMBERS
    assert S.ROSTER_KEYS == ("_about", "fetched_on", "congress_start", "members")
    assert S.MEMBER_KEYS == ("first", "middle", "last", "nickname", "official_full", "current")
    assert S.ROSTER_PATH == ROSTER
    assert Path(R.CONGRESS_ROSTER_PATH).resolve() == ROSTER.resolve()
    assert S.SOURCE_BASE.startswith("https://unitedstates.github.io/")
    assert S.ALLOWED_HOSTS == {"unitedstates.github.io"}


# ── the refresh script: dry run and write, offline ────────────────────────────

@pytest.fixture
def sources(tmp_path):
    current, historical = _synthetic()
    src = tmp_path / "src"
    src.mkdir()
    (src / "legislators-current.json").write_text(json.dumps(current), encoding="utf-8")
    (src / "legislators-historical.json").write_text(json.dumps(historical), encoding="utf-8")
    return src


@pytest.fixture
def target(tmp_path, monkeypatch):
    path = tmp_path / "data" / "congress_roster.json"
    path.parent.mkdir()
    path.write_text(ROSTER.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(S, "ROSTER_PATH", path)

    async def _no_network():
        raise AssertionError("the network must not be reached")
    monkeypatch.setattr(S, "fetch_sources", _no_network)
    monkeypatch.setattr(S, "_today", lambda: date(2026, 10, 9))     # never the wall clock
    return path


def test_dry_run_is_the_default_and_writes_nothing(sources, target):
    before = target.read_bytes()
    assert S.main(["--from-dir", str(sources)]) == 0
    assert target.read_bytes() == before
    assert [p.name for p in target.parent.iterdir()] == ["congress_roster.json"]   # no temp file left


def test_write_replaces_only_the_roster_file(sources, target):
    assert S.main(["--from-dir", str(sources), "--write"]) == 0
    doc = json.loads(target.read_text(encoding="utf-8"))
    S.validate_roster(doc)
    assert len(doc["members"]) == 550
    assert target.read_text(encoding="utf-8") == S.render(doc)
    assert sorted(p.name for p in target.parent.iterdir()) == ["congress_roster.json"]
    assert target.stat().st_mode & 0o777 == 0o644


def test_write_roster_resolves_its_default_path_at_call_time(target):
    doc = S.build_roster(*_synthetic(), fetched_on="2026-10-09")
    S.write_roster(doc)
    assert json.loads(target.read_text(encoding="utf-8")) == doc
    with pytest.raises(S.RosterError):
        S.write_roster({"members": []}, target)
    assert json.loads(target.read_text(encoding="utf-8")) == doc


def test_a_truncated_source_writes_nothing_and_fails(tmp_path, target):
    src = tmp_path / "small"
    src.mkdir()
    current, historical = _synthetic(n_current=20)
    (src / "legislators-current.json").write_text(json.dumps(current), encoding="utf-8")
    (src / "legislators-historical.json").write_text(json.dumps(historical), encoding="utf-8")
    before = target.read_bytes()
    assert S.main(["--from-dir", str(src), "--write"]) == 1
    assert target.read_bytes() == before


def test_unreadable_sources_fail_cleanly(tmp_path, target):
    src = tmp_path / "bad"
    src.mkdir()
    (src / "legislators-current.json").write_text("{nope", encoding="utf-8")
    (src / "legislators-historical.json").write_text("[]", encoding="utf-8")
    assert S.main(["--from-dir", str(src), "--write"]) == 1
    assert S.main(["--from-dir", str(tmp_path / "missing")]) == 1


def test_the_script_writes_no_other_file_and_prints_nothing():
    tree = ast.parse(Path(S.__file__).read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    names = {(c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")) for c in calls}
    assert "print" not in names
    assert "write_text" not in names and "write_bytes" not in names
    opens = [c for c in calls if getattr(c.func, "id", None) == "open"]
    assert opens == [], "writes go through write_roster's temp file + os.replace only"
