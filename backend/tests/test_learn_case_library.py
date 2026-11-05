"""The Learn case library loader (`app/services/learn_weekly/case_library.py`) and, when it
exists, the REAL `backend/data/learn_case_library.json`.

The library is the only source of evergreen facts a Learn piece may state, and a person must
have verified each entry. So the loader is pinned to fail closed: an absent file is an empty
library (WARNING), a broken file or an unknown version is an empty library (ERROR), a malformed
entry is skipped (ERROR naming it), and only a verified + llm_eligible entry with facts is
usable — `facts_for` gives nothing for any other. A `quotes` key is refused anywhere.

The real file's Value Trap cases and company primers are pinned too: catalog slugs exist in the
bundle, cases stay resolved and fenced, and no case echo tag can file a live company's piece under
Value Traps.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
from pathlib import Path
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


def test_no_real_fact_cites_fmp():
    """Facts frozen into a weekly piece never come from FMP (Order Form: no downloadable data)."""
    for e in _real()["entries"]:
        for f in e.get("facts") or []:
            host = urlsplit((f.get("source") or {}).get("url") or "").netloc.lower()
            assert "financialmodelingprep" not in host, (e.get("id"), f.get("id"))


# ── The REAL file's catalog subjects, Value Trap cases and company primers ──

_BACKEND = Path(__file__).resolve().parents[1]
_BUNDLE = _BACKEND / "data" / "money_moves.json"
_THEME_MIGRATIONS = ("081_trending_themes.sql", "084_retheme_ticker_baskets.sql")
_THEME_SLUG_RE = re.compile(r"\(\s*'([a-z0-9-]+)'\s*,|slug\s*=\s*'([a-z0-9-]+)'")
# A US ticker, lower-cased: 1-5 letters, optionally a class suffix ("brk-b", "brk.b").
_TICKER_LIKE = re.compile(r"^[a-z]{1,5}([.-][a-z]{1,2})?$")


def _bundle_articles():
    articles = json.loads(_BUNDLE.read_text(encoding="utf-8"))["articles"]
    slugs = {a["slug"] for a in articles if isinstance(a.get("slug"), str)}
    assert slugs, f"{_BUNDLE.name} lists no article slugs"  # a vacuous bundle proves nothing
    return articles, slugs


def _theme_slugs():
    text = "".join((_BACKEND / "database" / "migrations" / name).read_text(encoding="utf-8")
                   for name in _THEME_MIGRATIONS)
    slugs = {a or b for a, b in _THEME_SLUG_RE.findall(text)}
    assert {"silicon-rush", "powering-machine"} <= slugs, sorted(slugs)
    return slugs


def test_every_real_catalog_subject_is_a_slug_in_the_bundle():
    _, slugs = _bundle_articles()
    for e in _real()["entries"]:
        for slug in e.get("catalog_subjects") or []:
            assert slug in slugs, (e.get("id"), slug)


def test_no_real_case_headlines_a_catalog_article_it_does_not_name():
    """A weekly piece is never about the main subject of a catalog article in the same category
    (plan §1.5), so a case whose company headlines a catalog article of a category it fits must
    list that slug in `catalog_subjects` — which is what keeps Kodak, now a hand-written catalog
    article, out of the library."""
    articles, _ = _bundle_articles()
    library = cl.parse_library(_real(), source="real")
    for e in library.usable_entries(kinds=("case",)):
        for co in e.companies:
            short = co.name.split()[0]
            for a in articles:
                if (a.get("category") in e.category_fit
                        and re.search(rf"(?<!\w){re.escape(short)}(?!\w)", a.get("title") or "",
                                      re.IGNORECASE)):
                    assert a["slug"] in e.catalog_subjects, (e.id, a["slug"])


def test_the_real_value_trap_cases_are_resolved_cited_and_fenced():
    """The Monday slot's evergreen Value Traps pick is a resolved `case` entry, so the library
    must hold some. Each is a business-model story with no wrongdoing and no price data: its
    banned claims rule out misconduct and stock prices, and every company it lists is an SEC
    filer (the fact sheet drops a figure tied to a company with no CIK)."""
    library = cl.parse_library(_real(), source="real")
    cases = library.usable_entries(kinds=("case",))
    assert len([e for e in cases if "valueTraps" in e.category_fit]) >= 3
    for e in cases:
        assert e.status == "resolved", e.id
        banned = [b.lower() for b in e.banned_claims]
        assert any("misconduct" in b for b in banned), e.id
        assert any("stock price" in b for b in banned), e.id
        assert e.companies and all(co.cik for co in e.companies), e.id


def test_no_real_case_echo_tag_can_file_a_live_company_under_value_traps():
    """`topic.category_for` files a Deep Dive under Value Traps when a resolved case's echo tag
    equals the candidate key (a theme slug or a ticker), a subject's ticker or a subject's
    `sic:<code>`. Any of those on a case would put a LIVE company's piece under Value Traps:
    Nokia's SIC 3663 is also Qualcomm's (a company primer) and Blockbuster's 7841 is Netflix's.
    Case echo tags stay descriptive words; a `sic:` or ticker tag needs the owner's decision and
    a change here."""
    library = cl.parse_library(_real(), source="real")
    themes = _theme_slugs() | {t for e in library.entries for t in e.themes}
    tickers = {co.ticker.lower() for e in library.entries for co in e.companies if co.ticker}
    cases = library.usable_entries(kinds=("case",))
    assert cases
    for e in cases:
        assert e.echo_tags, e.id
        for tag in e.echo_tags:
            t = tag.strip().lower()
            assert not t.startswith("sic:"), (e.id, tag)
            assert t not in themes, (e.id, tag)
            assert t not in tickers and not _TICKER_LIKE.match(t), (e.id, tag)


def test_every_real_company_primer_names_one_sec_filer_on_a_theme():
    """A company primer is what lets a single-company Deep Dive run (`company_primer_ciks`), so
    each names exactly one SEC filer and sits on a theme, and no filer has two primers."""
    library = cl.parse_library(_real(), source="real")
    primers = library.usable_entries(kinds=(cl.KIND_COMPANY,))
    assert primers
    for e in primers:
        assert len(e.companies) == 1, e.id
        assert e.companies[0].cik and e.companies[0].ticker, e.id
        assert e.themes, e.id
    assert len(library.company_primer_ciks()) == len(primers)
