"""The Learn case library loader (`app/services/learn_weekly/case_library.py`) and, when it
exists, the REAL `backend/data/learn_case_library.json`.

The library is the only source of evergreen facts a Learn piece may state, and a person must
have verified each entry. So the loader is pinned to fail closed: an absent file is an empty
library (WARNING), a broken file or an unknown version is an empty library (ERROR), a malformed
entry is skipped (ERROR naming it), and only a verified + llm_eligible entry with facts is
usable — `facts_for` gives nothing for any other. A `quotes` key is refused anywhere.
"""

from __future__ import annotations

import copy
import json
import logging
import os
from urllib.parse import urlsplit

import pytest

from app.services.learn_weekly import case_library as cl
from app.services.learn_weekly.contracts import ORIGIN_LIBRARY

LOGGER = "app.services.learn_weekly.case_library"


def fact(fid="f1", text="A data centre needs power, cooling and network capacity.",
         url="https://www.sec.gov/Archives/edgar/data/1045810/x/nvda-10k.htm"):
    return {"id": fid, "text": text,
            "source": {"title": "Form 10-K", "url": url, "publisher": "SEC",
                       "date": "2025-02-26", "type": "sec_filing"}}


def entry(eid="dc-power", **over):
    data = {
        "id": eid, "kind": "mechanics", "title": "How data centres use power",
        "themes": ["powering-machine"], "category_fit": ["blueprints"],
        "companies": [{"name": "NVIDIA Corporation", "cik": "1045810", "ticker": "nvda"}],
        "sic": 3674, "era": "2020s", "status": None, "llm_eligible": True,
        "facts": [fact()], "verified_by": "Owner", "verified_at": "2026-10-20",
        "echo_tags": ["power"], "banned_claims": [], "catalog_subjects": [],
    }
    data.update(over)
    return data


def doc(*entries, version=1):
    return {"version": version, "entries": list(entries)}


# ── File-level outcomes ─────────────────────────────────────────────────


def test_an_absent_file_is_an_empty_library_with_a_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        library = cl.load_case_library(tmp_path / "nope.json")
    assert library.entries == ()
    assert library.usable_entries() == [] and library.primer_count("powering-machine") == 0
    assert any(r.levelno == logging.WARNING and "learn-library-absent" in r.getMessage()
               for r in caplog.records)


@pytest.mark.parametrize("content", ["{not json", "", "[" * 100_000])
def test_a_file_that_is_not_json_is_an_empty_library_with_an_error(tmp_path, caplog, content):
    path = tmp_path / "lib.json"
    path.write_text(content)
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        assert cl.load_case_library(path).entries == ()
    assert any(r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.parametrize("data", [
    [], "x", None, {"entries": [entry()]}, {"version": 2, "entries": [entry()]},
    {"version": True, "entries": [entry()]}, {"version": "1", "entries": [entry()]},
    {"version": 1, "entries": {"dc-power": entry()}},
])
def test_a_wrong_top_level_or_version_is_an_empty_library(data, caplog):
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        assert cl.parse_library(data).entries == ()
    assert any(r.levelno == logging.ERROR for r in caplog.records)


# ── Entry validation ────────────────────────────────────────────────────


def _bad(mutate):
    e = entry("broken")
    mutate(e)
    return e


MALFORMED = {
    "missing kind": _bad(lambda e: e.pop("kind")),
    "unknown kind": _bad(lambda e: e.update(kind="story")),
    "llm_eligible as a string": _bad(lambda e: e.update(llm_eligible="true")),
    "llm_eligible missing": _bad(lambda e: e.pop("llm_eligible")),
    "invalid cik": _bad(lambda e: e["companies"][0].update(cik="abc")),
    "bool cik": _bad(lambda e: e["companies"][0].update(cik=True)),
    "company without a name": _bad(lambda e: e["companies"][0].pop("name")),
    "http source": _bad(lambda e: e["facts"][0]["source"].update(url="http://sec.gov/x")),
    "javascript source": _bad(lambda e: e["facts"][0]["source"].update(url="javascript:x")),
    "missing source": _bad(lambda e: e["facts"][0].pop("source")),
    "source without a title": _bad(lambda e: e["facts"][0]["source"].pop("title")),
    "empty fact text": _bad(lambda e: e["facts"][0].update(text="   ")),
    "huge fact text": _bad(lambda e: e["facts"][0].update(text="x" * 1001)),
    "repeated fact id": _bad(lambda e: e["facts"].append(fact("f1", "Another one."))),
    "fact id with a colon": _bad(lambda e: e["facts"][0].update(id="a:b")),
    "facts not a list": _bad(lambda e: e.update(facts={"f1": fact()})),
    "quotes at entry level": _bad(lambda e: e.update(quotes=["A famous line."])),
    "quotes nested in a fact": _bad(lambda e: e["facts"][0].update(quotes=[])),
    "quotes nested in a source": _bad(lambda e: e["facts"][0]["source"].update(quotes="x")),
    "status not resolved": _bad(lambda e: e.update(status="pending")),
    "verified_at not a date": _bad(lambda e: e.update(verified_at="yesterday")),
    "theme not a slug": _bad(lambda e: e.update(themes=["Powering Machine"])),
    "themes not a list": _bad(lambda e: e.update(themes="powering-machine")),
    "missing title": _bad(lambda e: e.pop("title")),
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_a_malformed_entry_is_skipped_with_an_error_naming_it(name, caplog):
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        library = cl.parse_library(doc(entry("good"), MALFORMED[name]))
    assert [e.id for e in library.entries] == ["good"], name
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("broken" in m for m in errors), errors


@pytest.mark.parametrize("raw_id", [None, "", "a:b", "-x", "x" * 101, 5])
def test_an_entry_with_an_invalid_id_is_skipped(raw_id, caplog):
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        library = cl.parse_library(doc(entry("good"), entry(raw_id)))
    assert [e.id for e in library.entries] == ["good"]
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_non_object_entries_and_repeated_ids_are_skipped(caplog):
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        library = cl.parse_library(doc(entry("a"), "junk", None, 5,
                                       entry("a", title="A second copy")))
    assert [e.title for e in library.entries] == ["How data centres use power"]
    assert sum(r.levelno == logging.ERROR for r in caplog.records) == 4


def test_a_good_entry_is_normalised():
    e = cl.parse_library(doc(entry(companies=[
        {"name": "NVIDIA  Corporation", "cik": 1045810, "ticker": "nvda"},
        {"name": "OpenAI", "cik": None},
        {"name": "Private Co", "cik": ""},
    ]))).entries[0]
    assert e.companies[0] == cl.LibraryCompany("NVIDIA Corporation", "0001045810", "NVDA")
    assert e.companies[1].cik is None and e.companies[2].cik is None
    assert e.sic == "3674" and e.themes == ("powering-machine",)
    assert e.usable


# ── Usability ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("over", [
    {"verified_by": None}, {"verified_by": "  "}, {"verified_at": None},
    {"verified_by": None, "verified_at": None}, {"llm_eligible": False}, {"facts": []},
])
def test_unverified_ineligible_or_empty_entries_are_not_usable(over, caplog):
    library = cl.parse_library(doc(entry(**over)))
    assert len(library.entries) == 1, "loaded (it is well-formed) …"
    e = library.entries[0]
    assert not e.usable and library.usable_entries() == []  # … but never usable
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert cl.facts_for(e) == []
    assert "learn-library-unusable" in caplog.text


def test_usable_entries_filters_by_theme_and_kind_and_primer_count_counts_primers():
    library = cl.parse_library(doc(
        entry("m1", kind="mechanics", themes=["silicon-rush"]),
        entry("c1", kind="company", themes=["silicon-rush", "powering-machine"]),
        entry("k1", kind="case", themes=["silicon-rush"]),
        entry("m2", kind="mechanics", themes=["silicon-rush"], verified_by=None),
        entry("m3", kind="mechanics", themes=["powering-machine"]),
    ))
    assert [e.id for e in library.usable_entries()] == ["m1", "c1", "k1", "m3"]
    assert [e.id for e in library.usable_entries(theme="silicon-rush")] == ["m1", "c1", "k1"]
    assert [e.id for e in library.usable_entries(kinds=["case"])] == ["k1"]
    assert [e.id for e in library.usable_entries(kinds="case")] == ["k1"]
    assert [e.id for e in library.usable_entries("silicon-rush", ("company",))] == ["c1"]
    assert library.primer_count("silicon-rush") == 2   # m1 + c1; the case and m2 do not count
    assert library.primer_count("powering-machine") == 2
    assert library.primer_count("unknown-theme") == 0


def test_facts_for_builds_cited_library_facts():
    e = cl.parse_library(doc(entry(facts=[fact("f1"), fact("f2", "Second fact.")]))).entries[0]
    facts = cl.facts_for(e)
    assert [f.id for f in facts] == ["L:dc-power:f1", "L:dc-power:f2"]
    assert all(f.origin == ORIGIN_LIBRARY and f.entry_id == "dc-power" for f in facts)
    assert all(f.url.startswith("https://") and f.accession is None for f in facts)
    with pytest.raises(TypeError):
        cl.facts_for({"id": "dc-power"})


# ── The cached default loader ───────────────────────────────────────────


def test_the_default_loader_rereads_an_edited_file(tmp_path, monkeypatch):
    path = tmp_path / "learn_case_library.json"
    monkeypatch.setattr(cl, "LIBRARY_PATH", path)
    assert cl.usable_entries() == []                      # absent
    path.write_text(json.dumps(doc(entry("a"))))
    assert [e.id for e in cl.usable_entries()] == ["a"]
    assert cl.get_case_library() is cl.get_case_library()  # unchanged file: one parse
    path.write_text(json.dumps(doc(entry("a"), entry("b", kind="company"))))
    os.utime(path, ns=(10**18, 10**18))                    # a new mtime even on a fast clock
    assert [e.id for e in cl.usable_entries()] == ["a", "b"]
    assert cl.primer_count("powering-machine") == 2


# ── The REAL library file (skipped while another session is still drafting it) ──


def _real():
    if not cl.LIBRARY_PATH.exists():
        pytest.skip(f"{cl.LIBRARY_PATH.name} not present yet")
    return json.loads(cl.LIBRARY_PATH.read_text(encoding="utf-8"))


def _walk_keys(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk_keys(v)


def test_the_real_file_parses_with_no_entry_skipped(caplog):
    raw = _real()
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        library = cl.parse_library(copy.deepcopy(raw), source="real")
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == []
    assert len(library.entries) == len(raw["entries"])


def test_the_real_file_has_no_quotes_key_anywhere():
    assert "quotes" not in set(_walk_keys(_real()))


def test_every_real_fact_is_sourced_over_https():
    for e in _real()["entries"]:
        for f in e.get("facts") or []:
            url = (f.get("source") or {}).get("url")
            parts = urlsplit(url or "")
            assert parts.scheme == "https" and parts.netloc, (e.get("id"), f.get("id"), url)


def test_every_real_usable_entry_names_its_verifier_and_date():
    library = cl.parse_library(_real(), source="real")
    for e in library.usable_entries():
        assert e.verified_by and e.verified_at and e.llm_eligible
