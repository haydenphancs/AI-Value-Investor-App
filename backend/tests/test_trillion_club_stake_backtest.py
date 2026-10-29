"""Trillion Club stake finder — the backtest over the recorded SEC filings (plan v3, "Tests").

``app/services/trillion_club/stake_finder.py`` is run, read-only (``classify_only``) and as a full
run (``run_stakes`` over the in-memory database), against the public SEC documents recorded under
``tests/fixtures/trillion_club_stakes`` with TODAY FROZEN at 2026-10-28 ET. Each case asserts the
OUTCOME apart from the facts, and every row the finder builds is compared with the seed's own row
for the same pair, field by field: each difference is pinned here with its reason, so a change in
either is a red test, never a silent drift.

§1 the harness (imported by the flow and rules suites) · §2 S1 with an empty stake table ·
§3 S1 against the real seed (identity dedupe) · §4 S2 with an empty table · §5 the Meta 424B5 →
ONE merged digest line · §6 S2 against the seed · §7 built rows vs the seed's rows · §8 the same
filings through ``run_stakes`` (auto under the tiers, the volume cap) · §9 every built row passes
the validators.

The fake SEC serves ONLY recorded data, plus clearly labelled synthetic stand-ins: an accession a
member's submissions list but whose XML was not recorded is served as a minimal 13G ABOUT that
member (an outside fund's filing — the real shape of those rows), TRex Bio's submissions (not
recorded; it listed as TRXB on Nasdaq) are synthesised, and the Meta preliminary 424B5 (…431040,
not recorded) is served the final 424B5's text. Hermetic: no network, no Supabase.
"""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pytest

import _trillion_club_stake_fakes as F
from app.integrations import sec_edgar
from app.services.trillion_club import stake_contracts as C
from app.services.trillion_club import stake_finder as SF
from app.services.trillion_club import stake_rules
from app.services.trillion_club import stake_store as SS
from app.services.trillion_club.stake_store import StakeStore
from app.services.trillion_club.store import TrillionClubStore
from app.utils.market_hours import ET

BACKEND = Path(__file__).resolve().parents[1]
FIX = BACKEND / "tests" / "fixtures" / "trillion_club_stakes"
MANIFEST = json.loads((FIX / "MANIFEST.json").read_text(encoding="utf-8"))
SEED = json.loads((BACKEND / "data" / "trillion_club_seed.json").read_text(encoding="utf-8"))
CONFIGS = C.load_member_configs(str(BACKEND / "data" / "trillion_club_filers.json"))

#: Today, frozen: 2026-10-28 18:30 ET (22:30 UTC) — the run slot the day the Meta 424B5 was final.
NOW = datetime(2026, 10, 28, 22, 30, tzinfo=timezone.utc)
TODAY = date(2026, 10, 28)
SINCE = date(2025, 10, 1)   # every recorded filing is inside [SINCE, TODAY]

# ═════════════════════════════════════════════════════════════════════════════════════════
# §1 The harness
# ═════════════════════════════════════════════════════════════════════════════════════════

_MEMBER_SUBMISSIONS = {
    "0001045810": "submissions_member_nvidia.json", "0001652044": "submissions_member_alphabet.json",
    "0001824723": "submissions_member_google_llc.json", "0001018724": "submissions_member_amazon.json",
    "0000059478": "submissions_member_eli_lilly.json",
}
TREX_CIK = "0002139097"
TREX_SUBMISSIONS = {  # SYNTHETIC: not recorded; TRex Bio listed as TRXB on Nasdaq (plan v3, "Found during review")
    "cik": TREX_CIK, "entityType": "operating", "name": "TRex Bio, Inc.", "tickers": ["TRXB"], "exchanges": ["Nasdaq"],
    "filings": {"files": [], "recent": {"accessionNumber": [], "form": [], "filingDate": [], "primaryDocument": [],
                                        "items": [], "reportDate": [], "acceptanceDateTime": [],
                                        "primaryDocDescription": []}},
}
META_PRELIMINARY = "0001193125-26-431040"
META_FINAL = "0001193125-26-436410"


def load_json(name: str) -> Dict[str, Any]:
    return json.loads((FIX / "json" / name).read_text(encoding="utf-8"))


def empty_submissions(cik: str, name: str = "", tickers: Sequence[str] = (), exchanges: Sequence[str] = ()) -> Dict[str, Any]:
    return {"cik": cik, "name": name, "tickers": list(tickers), "exchanges": list(exchanges),
            "filings": {"files": [], "recent": {"accessionNumber": [], "form": [], "filingDate": [],
                                                "primaryDocument": [], "items": [], "reportDate": []}}}


def add_filing(subs: Dict[str, Any], accession: str, form: str, filed: str,
               primary: str = "xslSCHEDULE_13G_X02/primary_doc.xml") -> Dict[str, Any]:
    """Prepend one filing to a submissions record (the newest first, as SEC lists them)."""
    recent = subs["filings"]["recent"]
    for key, value in (("accessionNumber", accession), ("form", form), ("filingDate", filed),
                       ("primaryDocument", primary), ("items", ""), ("reportDate", "")):
        recent.setdefault(key, []).insert(0, value)
    for key in list(recent):
        if key not in ("accessionNumber", "form", "filingDate", "primaryDocument", "items", "reportDate"):
            recent[key].insert(0, "")
    return subs


def synthetic_13g(*, filer_cik: str, issuer_cik: str, issuer_name: str, cusip: Optional[str] = "000000000",
                  class_title: str = "Common Stock, par value $0.001 per share", event: str = "09/30/2026",
                  persons: Sequence[Tuple[str, str, Any, Any]] = (("The Vanguard Group", "IA", "1000000", "6.1"),),
                  submission: str = "SCHEDULE 13G", item4: str = "", five_or_less: Optional[str] = None,
                  comments: str = "") -> str:
    """A minimal structured Schedule 13G, in the X0202 shape the recorded ones use."""
    people = "".join(
        f"<coverPageHeaderReportingPersonDetails><reportingPersonName>{name}</reportingPersonName>"
        f"<reportingPersonBeneficiallyOwnedNumberOfShares><soleVotingPower>{shares}</soleVotingPower>"
        f"<sharedVotingPower>0</sharedVotingPower><soleDispositivePower>{shares}</soleDispositivePower>"
        f"<sharedDispositivePower>0</sharedDispositivePower></reportingPersonBeneficiallyOwnedNumberOfShares>"
        f"<reportingPersonBeneficiallyOwnedAggregateNumberOfShares>{shares}"
        f"</reportingPersonBeneficiallyOwnedAggregateNumberOfShares><classPercent>{pct}</classPercent>"
        f"<typeOfReportingPerson>{kind}</typeOfReportingPerson><comments>{comments}</comments>"
        f"</coverPageHeaderReportingPersonDetails>"
        for name, kind, shares, pct in persons)
    cusips = f"<issuerCusips><issuerCusipNumber>{cusip}</issuerCusipNumber></issuerCusips>" if cusip else ""
    item5 = (f"<item5><classOwnership5PercentOrLess>{five_or_less}</classOwnership5PercentOrLess></item5>"
             if five_or_less else "<item5><notApplicableFlag>Y</notApplicableFlag></item5>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?><edgarSubmission xmlns="http://www.sec.gov/edgar/schedule13g">'
        f"<headerData><submissionType>{submission}</submissionType><filerInfo><filer><filerCredentials>"
        f"<cik>{filer_cik}</cik></filerCredentials></filer></filerInfo></headerData><formData><coverPageHeader>"
        f"<securitiesClassTitle>{class_title}</securitiesClassTitle>"
        f"<eventDateRequiresFilingThisStatement>{event}</eventDateRequiresFilingThisStatement>"
        f"<issuerInfo><issuerCik>{issuer_cik}</issuerCik><issuerName>{issuer_name}</issuerName>{cusips}</issuerInfo>"
        f"</coverPageHeader>{people}<items><item4><amountBeneficiallyOwned>{item4}</amountBeneficiallyOwned></item4>"
        f"{item5}</items></formData></edgarSubmission>"
    )


def _efts_hits() -> Dict[str, Tuple[Dict[str, Any], str]]:
    """Every recorded EDGAR full-text hit, by `_id`, with the alias of the search that found it."""
    out: Dict[str, Tuple[Dict[str, Any], str]] = {}
    for name, meta in MANIFEST["efts"].items():
        alias = meta["q"].split('"')[1]
        for hit in load_json(f"{name}.json")["hits"]["hits"]:
            out.setdefault(hit["_id"], (hit, alias))
    return out


class FakeSEC:
    """The SEC client the finder calls — ``get_submissions``, ``get_filing_document`` and
    ``full_text_search`` with the real signatures — serving the recorded fixtures.

    ``recorded_only`` (the default) answers a search with the hits whose DOCUMENT was recorded and
    whose recorded text holds the searched alias and a deal phrase (EFTS's own rule); False also
    returns the recorded hits of documents that were not recorded (their fetch answers 404).
    ``submissions`` / ``documents`` / ``hits`` add or override data; ``fail`` maps a method name to
    a list of exceptions raised in turn (``None`` = answer normally)."""

    def __init__(self, *, recorded_only: bool = True, submissions: Optional[Mapping[str, Any]] = None,
                 documents: Optional[Mapping[Tuple[str, str], Optional[str]]] = None,
                 hits: Optional[Sequence[Dict[str, Any]]] = None, about_member_default: bool = True):
        self.recorded_only = recorded_only
        self.calls: List[Tuple[str, Tuple[Any, ...]]] = []
        self.fail: Dict[str, List[Optional[BaseException]]] = {}
        self.about_member_default = about_member_default
        self._subs: Dict[str, Any] = {}
        for cik, file in _MEMBER_SUBMISSIONS.items():
            self._subs[cik] = load_json(file)
        for issuer, meta in MANIFEST["issuers"].items():
            self._subs.setdefault(meta["cik"], load_json(f"submissions_issuer_{issuer}.json"))
        self._subs[TREX_CIK] = copy.deepcopy(TREX_SUBMISSIONS)
        for slug, cfg in CONFIGS.items():
            for f in cfg.s1_filers:
                self._subs.setdefault(f.cik, empty_submissions(f.cik))
        self._subs.update(copy.deepcopy(dict(submissions or {})))
        self._docs: Dict[Tuple[str, str], Optional[str]] = {}
        for name, meta in MANIFEST["schedules"].items():
            self._docs[(meta["accession"], "primary_doc.xml")] = (FIX / "xml" / f"{name}.xml").read_text(encoding="utf-8")
        for name, meta in MANIFEST["documents"].items():
            self._docs[(meta["accession"], meta["document"])] = (FIX / "html" / f"{name}.htm").read_text(encoding="utf-8")
        for name, meta in MANIFEST["excerpts"].items():
            self._docs[(meta["accession"], meta["document"])] = (FIX / "text" / f"{name}.txt").read_text(encoding="utf-8")
        # SYNTHETIC: the preliminary 424B5 (not recorded) is served the final's text.
        self._docs[(META_PRELIMINARY, "d94886d424b5.htm")] = self._docs[(META_FINAL, "d94886d424b5.htm")]
        self._docs.update(dict(documents or {}))
        self._hits = _efts_hits()
        for hit in hits or ():
            self._hits[hit["_id"]] = (hit, hit.get("_alias", ""))

    def submissions_of(self, cik: str) -> Dict[str, Any]:
        return self._subs[cik]

    def set_submissions(self, cik: str, value: Any) -> None:
        self._subs[cik] = value

    def set_document(self, accession: str, document: str, text: Optional[str]) -> None:
        self._docs[(accession, document)] = text

    def add_hit(self, source: Dict[str, Any], document: str) -> None:
        self._hits[f"{source['adsh']}:{document}"] = ({"_id": f"{source['adsh']}:{document}", "_source": source}, "")

    def _take_failure(self, method: str) -> None:
        queue = self.fail.get(method)
        if queue:
            exc = queue.pop(0)
            if exc is not None:
                raise exc

    async def get_submissions(self, cik: Any) -> Optional[Dict[str, Any]]:
        cik = sec_edgar.normalize_cik(cik)
        self.calls.append(("get_submissions", (cik,)))
        self._take_failure("get_submissions")
        value = self._subs.get(cik)
        return copy.deepcopy(value) if value is not None else None

    async def get_filing_document(self, cik: Any, accession: Any, document: Any, *, max_bytes: Optional[int] = None
                                  ) -> Optional[str]:
        self.calls.append(("get_filing_document", (sec_edgar.normalize_cik(cik), accession, document, max_bytes)))
        self._take_failure("get_filing_document")
        if (accession, document) in self._docs:
            return self._docs[(accession, document)]
        if document == "primary_doc.xml" and self.about_member_default and self._lists(cik, accession):
            # SYNTHETIC: a schedule the member's submissions list but that was not recorded — an
            # outside holder's 13G ABOUT the member (the real shape of those rows).
            return synthetic_13g(filer_cik="0000102909", issuer_cik=sec_edgar.normalize_cik(cik),
                                 issuer_name="The Member", cusip=None)
        return None

    def _lists(self, cik: Any, accession: str) -> bool:
        subs = self._subs.get(sec_edgar.normalize_cik(cik)) or {}
        return accession in ((subs.get("filings") or {}).get("recent") or {}).get("accessionNumber", [])

    async def full_text_search(self, query: str, *, start: date, end: date, forms: Sequence[str] = (),
                               ciks: Sequence[Any] = (), offset: int = 0) -> Dict[str, Any]:
        self.calls.append(("full_text_search", (query, start, end, tuple(forms), offset)))
        self._take_failure("full_text_search")
        alias = query.split('"')[1]
        phrases = [p for p in query.split('"')[3::2]]
        found: List[Dict[str, Any]] = []
        for hid, (hit, searched_alias) in sorted(self._hits.items()):
            src = hit["_source"]
            day = date.fromisoformat(src["file_date"])
            if not start <= day <= end or src["root_forms"][0] not in forms:
                continue
            acc, doc = hid.split(":", 1)
            text = self._docs.get((acc, doc))
            if text is not None:
                low = self._searchable(acc, doc, text)
                if alias.casefold() not in low or not any(p.casefold() in low for p in phrases):
                    continue
            elif self.recorded_only or searched_alias != alias:
                continue
            found.append(copy.deepcopy(hit))
        page = found[offset:offset + 100]
        return {
            "hits": {"hits": page, "total": {"relation": "eq", "value": len(found)}},
            "query": {"_source": {"exclude": ["doc_text"]}, "from": offset, "size": 100, "query": {"bool": {
                "filter": [{"terms": {"root_forms": list(forms)}},
                           {"range": {"file_date": {"gte": start.isoformat(), "lte": end.isoformat()}}}],
                "minimum_should_match": 1, "must": [{"match_phrase": {"doc_text": alias}}], "must_not": [],
                "should": [{"match_phrase": {"doc_text": p}} for p in phrases]}}},
        }

    def _searchable(self, acc: str, doc: str, text: str) -> str:
        """What EFTS matches a phrase in: the document's TEXT (markup gone), casefolded."""
        key = (acc, doc, len(text))
        cached = self._text_cache.get(key) if hasattr(self, "_text_cache") else None
        if cached is None:
            from app.utils.filing_text import html_to_text

            cached = " ".join(html_to_text(text, document=doc, max_chars=None).split()).casefold()
            if not hasattr(self, "_text_cache"):
                self._text_cache: Dict[Tuple[str, str, int], str] = {}
            self._text_cache[key] = cached
        return cached

    def count(self, method: str) -> int:
        return sum(1 for m, _ in self.calls if m == method)


class FakeNotifier:
    """A ``StakeNotifier`` that records every call. ``fail`` holds the kinds that answer as a failed
    send (None / False): auto, proposal, edit, lifecycle, mark, digest."""

    def __init__(self, *, available: bool = True):
        self._available = available
        self.fail: set = set()
        self.calls: List[Tuple[str, Dict[str, Any]]] = []
        self._next = 5000

    def available(self) -> bool:
        return self._available

    def _id(self) -> int:
        self._next += 1
        return self._next

    def _record(self, kind: str, **payload: Any) -> None:
        self.calls.append((kind, copy.deepcopy(payload)))

    async def send_auto_note(self, finding: Mapping[str, Any], row: Mapping[str, Any]) -> Optional[int]:
        self._record("auto", finding=dict(finding), row=dict(row))
        return None if "auto" in self.fail else self._id()

    async def send_proposal(self, finding: Mapping[str, Any], row: Mapping[str, Any], *, trial: bool,
                            evidence: Sequence[str]) -> Optional[int]:
        self._record("proposal", finding=dict(finding), row=dict(row), trial=trial, evidence=list(evidence))
        return None if "proposal" in self.fail else self._id()

    async def edit_proposal(self, finding: Mapping[str, Any], row: Mapping[str, Any], *, message_id: int,
                            trial: bool, evidence: Sequence[str]) -> bool:
        self._record("edit", finding=dict(finding), row=dict(row), message_id=message_id, trial=trial,
                     evidence=list(evidence))
        return "edit" not in self.fail

    async def send_lifecycle_note(self, finding: Mapping[str, Any], row: Optional[Mapping[str, Any]], *, text: str,
                                  offer_withdraw: bool) -> Optional[int]:
        self._record("lifecycle", finding=dict(finding), row=dict(row or {}), text=text, offer_withdraw=offer_withdraw)
        return None if "lifecycle" in self.fail else self._id()

    async def mark_message(self, *, message_id: int, finding_id: str, verdict: str, buttons: Sequence[str]) -> bool:
        self._record("mark", message_id=message_id, finding_id=finding_id, verdict=verdict, buttons=list(buttons))
        return "mark" not in self.fail

    async def send_digest(self, lines: Sequence[str]) -> bool:
        self._record("digest", lines=list(lines))
        return "digest" not in self.fail

    def of(self, kind: str) -> List[Dict[str, Any]]:
        return [p for k, p in self.calls if k == kind]

    @property
    def loud(self) -> int:
        return sum(1 for k, _ in self.calls if k in ("auto", "proposal", "lifecycle"))

    def digest_lines(self) -> List[str]:
        return [line for p in self.of("digest") for line in p["lines"]]


@pytest.mark.parametrize("method", ["get_submissions", "get_filing_document", "full_text_search"])
def test_the_fake_sec_has_the_real_clients_signatures(method):
    """Every finder suite drives FakeSEC; a fake MORE lenient than the real client (a renamed or
    newly keyword-only parameter) would keep them green while production raised TypeError. The
    parameters must match the real ``SECEdgarClient`` exactly: names, kinds and defaults."""
    import inspect

    real = inspect.signature(getattr(sec_edgar.SECEdgarClient, method))
    fake = inspect.signature(getattr(FakeSEC, method))
    shape = lambda sig: [(p.name, p.kind, p.default) for p in sig.parameters.values()]
    assert shape(fake) == shape(real), (method, fake, real)
    assert inspect.iscoroutinefunction(getattr(FakeSEC, method)) is inspect.iscoroutinefunction(
        getattr(sec_edgar.SECEdgarClient, method))


@pytest.mark.parametrize("method", ["available", "send_auto_note", "send_proposal", "edit_proposal",
                                    "send_lifecycle_note", "mark_message", "send_digest"])
def test_the_fake_notifier_has_the_protocols_signatures(method):
    """The same for Telegram: FakeNotifier, the real TelegramStakeNotifier and the NullNotifier
    all take exactly the StakeNotifier protocol's parameters."""
    import inspect

    from app.services.trillion_club.stake_review import TelegramStakeNotifier

    shape = lambda f: ([(p.name, p.kind, p.default) for p in inspect.signature(f).parameters.values()],
                       inspect.iscoroutinefunction(f))
    want = shape(getattr(C.StakeNotifier, method))
    for impl in (FakeNotifier, TelegramStakeNotifier, SF.NullNotifier):
        assert shape(getattr(impl, method)) == want, (impl.__name__, method)


def seed_companies() -> List[Dict[str, Any]]:
    """The seed's company rows as the daily job leaves them (published → a member)."""
    return [{**c, "is_member": bool(c["published"])} for c in SEED["companies"]]


def seed_stakes() -> List[Dict[str, Any]]:
    """The seed's stake rows as the table holds them after 205 (origin seed, synthetic ids)."""
    out = []
    for i, s in enumerate(SEED["stakes"]):
        out.append({**s, "id": f"00000000-0000-4000-9000-{i:012d}", "origin": "seed", "finding_id": None,
                    "withdrawn_at": None, "owner_kept_at": None, "investee_cik": None})
    return out


#: The members' latest 13F lines that make the seed's notes notes (the seed calls these rows
#: on_13f_note — NVIDIA's, Alphabet's and Amazon's 13Fs list them).
HOLDINGS = {
    "nvidia": {"cusips": frozenset({"N97284108", "21873S108", "458140100", "19247G107", "871607107", "654902204"}),
               "symbols": frozenset({"NBIS", "CRWV", "INTC", "COHR", "SNPS", "NOK"})},
    "alphabet": {"cusips": frozenset({"84615Q103"}), "symbols": frozenset({"SPCX"})},
    "amazon": {"cusips": frozenset({"98386P102"}), "symbols": frozenset({"XE"})},
}


def classify(sec: Optional[FakeSEC] = None, *, stakes: Optional[List[Dict[str, Any]]] = None,
             holdings: Optional[Mapping[str, Any]] = HOLDINGS, since: date = SINCE, now: datetime = NOW,
             configs: Optional[Mapping[str, C.MemberConfig]] = None) -> List[Dict[str, Any]]:
    return asyncio.run(SF.classify_only(now=now, sec=sec or FakeSEC(), companies=seed_companies(),
                                        stakes=stakes if stakes is not None else [], member_configs=configs or CONFIGS,
                                        since=since, holdings=holdings))


def by_key(found: Iterable[Mapping[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for f in found:
        key = (f["slug"], f["accession"])
        assert key not in out, f"two findings for {key}"
        out[key] = dict(f)
    return out


def acc_of(name: str) -> str:
    for section in ("schedules", "documents", "excerpts"):
        if name in MANIFEST[section]:
            return MANIFEST[section][name]["accession"]
    raise KeyError(name)


@pytest.fixture(autouse=True)
def _hermetic_finder(monkeypatch):
    """Zero the store's read backoff, and pin the people gate's roster check to this suite's
    frozen today (the real roster stays in force: names are still checked)."""
    monkeypatch.setattr(SS, "READ_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(SF, "_monotonic", lambda: 0.0)


# ═════════════════════════════════════════════════════════════════════════════════════════
# §2 S1 — the members' own 13D/13G, with an EMPTY stake table
# ═════════════════════════════════════════════════════════════════════════════════════════

S1_EXPECTED = {
    # name: (slug, outcome, reason, kind)
    "nvidia_nebius_13g": ("nvidia", C.OUTCOME_PROPOSAL, "derivative_position", "on_13f_note"),
    "nvidia_coreweave_13ga": ("nvidia", C.OUTCOME_AUTO, "plain_13dg", "on_13f_note"),
    "google_spacex_13g": ("alphabet", C.OUTCOME_AUTO, "plain_13dg", "on_13f_note"),
    "google_terawulf_13g": ("alphabet", C.OUTCOME_PROPOSAL, "derivative_position", "us_listed_off_13f"),
    "amazon_xenergy_13g": ("amazon", C.OUTCOME_AUTO, "plain_13dg", "on_13f_note"),
    "lilly_proqr_13ga": ("eli-lilly", C.OUTCOME_AUTO, "plain_13dg", "us_listed_off_13f"),
    "lilly_aktis_13d": ("eli-lilly", C.OUTCOME_AUTO, "plain_13dg", "us_listed_off_13f"),
    "lilly_scribe_13d": ("eli-lilly", C.OUTCOME_AUTO, "plain_13dg", "us_listed_off_13f"),
    "lilly_ionetix_13g": ("eli-lilly", C.OUTCOME_PROPOSAL, "no_us_listing", "private"),
    "lilly_trex_13d": ("eli-lilly", C.OUTCOME_AUTO, "plain_13dg", "us_listed_off_13f"),
}


@pytest.fixture(scope="module")
def s1_empty() -> Dict[Tuple[str, str], Dict[str, Any]]:
    return by_key(f for f in classify(FakeSEC(), stakes=[]) if f["source"] == C.SOURCE_SCH13)


@pytest.mark.parametrize("name", sorted(S1_EXPECTED))
def test_s1_outcome_of_each_recorded_schedule_with_no_row_on_file(name, s1_empty):
    slug, outcome, reason, kind = S1_EXPECTED[name]
    f = s1_empty[(slug, acc_of(name))]
    assert (f["outcome"], f["reason"], f["tier"]) == (outcome, reason, C.TIER_13DG), f
    assert f["row"] is not None and f["row"]["kind"] == kind
    assert f["trial"] is False and f["spans"] == []


def test_s1_every_unrecorded_schedule_in_the_members_lists_is_about_the_member(s1_empty):
    recorded = {acc_of(n) for n in MANIFEST["schedules"]}
    others = [f for key, f in s1_empty.items() if key[1] not in recorded]
    assert others, "the members' submissions list filings about them by others"
    assert {(f["outcome"], f["reason"]) for f in others} == {(C.OUTCOME_SKIPPED, "about_member")}
    assert all(f["row"] is None for f in others)


def test_s1_nebius_is_a_warrant_proposal_whose_basis_names_the_warrant_shares(s1_empty):
    """Plan v3: Nebius → proposal (warrant); the basis carries the count (the Rivian convention)."""
    row = s1_empty[("nvidia", acc_of("nvidia_nebius_13g"))]["row"]
    assert row["ownership_basis"] == "of Class A, incl. 21.1M warrant shares"
    assert row["ownership_pct"] == 9.3 and row["investee_cusip"] == "N97284108" and row["investee_us_symbol"] == "NBIS"


def test_s1_terawulf_is_a_warrant_proposal_from_google_llc(s1_empty):
    f = s1_empty[("alphabet", acc_of("google_terawulf_13g"))]
    assert f["filer_cik"] if "filer_cik" in f else True
    assert f["row"]["ownership_basis"] == "of common stock, incl. 41.0M warrant shares"
    assert f["row"]["investee_name"] == "TeraWulf" and f["row"]["investee_us_symbol"] == "WULF"
    assert f["row"]["source_title"] == "Alphabet Schedule 13G (Sep 25, 2026)"
    assert f["source_url"].startswith("https://www.sec.gov/Archives/edgar/data/1824723/")


def test_s1_ionetix_is_a_private_proposal_with_no_cusip(s1_empty):
    """Ionetix: the all-zero CUSIP is a placeholder (NULL, never an identity key) and SEC lists no
    exchange → a proposal of kind private, never auto."""
    f = s1_empty[("eli-lilly", acc_of("lilly_ionetix_13g"))]
    assert f["row"]["investee_cusip"] is None and f["row"]["investee_us_symbol"] is None
    assert f["row"]["kind"] == "private" and f["investee_cik"] == "0002108121"
    assert C.REASONS[f["reason"]].startswith("no U.S. exchange listing")


def test_s1_a_club_company_investee_takes_its_display_name(s1_empty):
    """SpaceX is in the registry: the row says "SpaceX", never SEC's issuer name."""
    row = s1_empty[("alphabet", acc_of("google_spacex_13g"))]["row"]
    assert row["investee_name"] == "SpaceX"


def test_s1_rows_carry_the_machine_row_fields(s1_empty):
    for name in S1_EXPECTED:
        slug = S1_EXPECTED[name][0]
        row = s1_empty[(slug, acc_of(name))]["row"]
        assert row["source_confidence"] == "primary" and row["material"] is False and row["tied_to_deal"] is False
        assert row["background"] is None and row["listed_since"] is None and row["local_listing"] is None
        assert row["disclosed_value_usd"] is None and row["value_basis"] is None
        assert row["sort_order"] == SF.SORT_ORDER == 900 and row["published"] is False
        assert row["verified_on"] == TODAY.isoformat()
        assert row["source_url"].startswith("https://www.sec.gov/Archives/edgar/data/")
        assert set(row) == set(stake_rules.STAKE_COLUMNS) | {"investee_cik"}


def test_s1_without_holdings_no_row_is_a_13f_note(s1_empty):
    """No 13F holdings known (the preview's case): a row is never on_13f_note."""
    found = by_key(f for f in classify(FakeSEC(), stakes=[], holdings=None) if f["source"] == C.SOURCE_SCH13)
    kinds = {f["row"]["kind"] for f in found.values() if f["row"]}
    assert kinds == {"us_listed_off_13f", "private"}


# ═════════════════════════════════════════════════════════════════════════════════════════
# §3 S1 against the REAL seed — identity dedupe makes them heads-ups
# ═════════════════════════════════════════════════════════════════════════════════════════

#: "existing_row" = a heads-up "old → new"; "same" / "older" = SKIPPED existing_row (a re-filing
#: that shows nothing the hand-kept row does not, or one older than it — review round 2: the 45-day
#: backfill at go-live would otherwise send "SpaceX · 7.2% → SpaceX · 7.2%" lines).
S1_AGAINST_SEED = {
    "nvidia_nebius_13g": "existing_row",       # CUSIP N97284108 — the basis gains the warrant shares
    "nvidia_coreweave_13ga": "older",          # Jan 23 13G/A vs the seed's Apr 15 figure
    "google_spacex_13g": "same",               # CUSIP 84615Q103
    "google_terawulf_13g": None,               # no seed row: still a proposal
    "amazon_xenergy_13g": "same",
    "lilly_proqr_13ga": "same",
    "lilly_aktis_13d": "same",                 # the seed adds "$112M invested"; the 13D states none
    "lilly_scribe_13d": "same",
    # The seed's Ionetix and TRex Bio rows are private: no CIK, CUSIP or symbol — the name is
    # their only key, so the finder can only say POSSIBLE match (stake_names.IdentityIndex).
    # (The P2 contract's "TRex → heads-up existing_row" is reported as a contract bug.)
    "lilly_ionetix_13g": "possible_match",
    "lilly_trex_13d": "possible_match",
}

_SKIP_DETAIL = {"same": "nothing the hand-kept row does not already show", "older": "older than the hand-kept row"}


@pytest.fixture(scope="module")
def s1_seed() -> Dict[Tuple[str, str], Dict[str, Any]]:
    return by_key(f for f in classify(FakeSEC(), stakes=seed_stakes()) if f["source"] == C.SOURCE_SCH13)


@pytest.mark.parametrize("name", sorted(S1_AGAINST_SEED))
def test_s1_against_the_seed(name, s1_seed):
    slug = S1_EXPECTED[name][0]
    f = s1_seed[(slug, acc_of(name))]
    want = S1_AGAINST_SEED[name]
    if want is None:
        assert (f["outcome"], f["reason"]) == (C.OUTCOME_PROPOSAL, "derivative_position")
        return
    if want in _SKIP_DETAIL:
        assert (f["outcome"], f["reason"], f["row"]) == (C.OUTCOME_SKIPPED, "existing_row", None), f
        assert f["detail"].startswith(_SKIP_DETAIL[want]), f["detail"]
        return
    assert (f["outcome"], f["reason"]) == (C.OUTCOME_HEADS_UP, want), f
    assert f["row"] is None, "a heads-up proposes no row"
    assert f["detail"], "the line says which row it matched"


def test_s1_the_seed_match_names_the_row_it_hit(s1_seed):
    f = s1_seed[("eli-lilly", acc_of("lilly_trex_13d"))]
    assert "TRex Bio" in f["detail"]
    f = s1_seed[("alphabet", acc_of("google_spacex_13g"))]
    assert "SpaceX" in f["detail"] and "7.2%" in f["detail"]
    f = s1_seed[("nvidia", acc_of("nvidia_nebius_13g"))]
    assert f["detail"].startswith("Nebius · 9.3% of Class A") and "incl. 21.1M warrant shares" in f["detail"]


# ═════════════════════════════════════════════════════════════════════════════════════════
# §4 S2 — other companies' filings, with an EMPTY stake table
# ═════════════════════════════════════════════════════════════════════════════════════════

S2_EXPECTED = {
    # (slug, document name): (outcome, tier, reason, investee_name, kind)
    ("nvidia", "intel_closing_8k"): (C.OUTCOME_PROPOSAL, C.TIER_SEC_CLOSING, "t1_closing", "Intel", "on_13f_note"),
    ("nvidia", "coherent_closing_8k"): (C.OUTCOME_PROPOSAL, C.TIER_SEC_CLOSING, "t1_closing", "Coherent", "on_13f_note"),
    ("nvidia", "synopsys_closing_8k"): (C.OUTCOME_PROPOSAL, C.TIER_SEC_CLOSING, "t1_closing", "Synopsys", "on_13f_note"),
    ("nvidia", "coreweave_closing_8k"): (C.OUTCOME_PROPOSAL, C.TIER_SEC_CLOSING, "t1_closing", "CoreWeave",
                                         "on_13f_note"),
    ("nvidia", "marvell_preferred_8k"): (C.OUTCOME_PROPOSAL, C.TIER_INSTRUMENT, "instrument",
                                         "Marvell Technology (convertible preferred)", "us_listed_off_13f"),
    ("nvidia", "lumentum_preferred_8k"): (C.OUTCOME_PROPOSAL, C.TIER_INSTRUMENT, "instrument",
                                          "Lumentum Holdings (convertible preferred)", "us_listed_off_13f"),
    ("nvidia", "nokia_directed_6k"): (C.OUTCOME_PROPOSAL, C.TIER_DIRECTED, "t4_directed", "Nokia", "on_13f_note"),
    ("nvidia", "amazon_openai_ex101"): (C.OUTCOME_SKIPPED, C.TIER_OTHER, "co_investor_only", None, None),
    ("amazon", "amazon_openai_ex101"): (C.OUTCOME_HEADS_UP, C.TIER_OTHER, "member_filed", None, None),
    ("nvidia", "iambic_424b4"): (C.OUTCOME_SKIPPED, C.TIER_OTHER, "commercial_only", None, None),
    ("alphabet", "inuvo_noise_8k"): (C.OUTCOME_SKIPPED, C.TIER_OTHER, "commercial_only", None, None),
    ("alphabet", "marvell_google_warrant_ex41"): (C.OUTCOME_PROPOSAL, C.TIER_INSTRUMENT, "instrument",
                                                  "Marvell Technology (warrant)", "us_listed_off_13f"),
    ("nvidia", "meta_424b5_final"): (C.OUTCOME_HEADS_UP, C.TIER_IOI, "t3_indication", None, None),
    ("amd", "meta_424b5_final"): (C.OUTCOME_HEADS_UP, C.TIER_IOI, "t3_indication", None, None),
    ("broadcom", "meta_424b5_final"): (C.OUTCOME_HEADS_UP, C.TIER_IOI, "t3_indication", None, None),
    ("meta", "meta_424b5_final"): (C.OUTCOME_HEADS_UP, C.TIER_OTHER, "member_filed", None, None),
}


def s2_only_configs() -> Dict[str, C.MemberConfig]:
    """The real member configs with S1 off — S2's own outcomes, untouched by rows S1 made."""
    import dataclasses

    return {slug: dataclasses.replace(cfg, s1_filers=(), intermediates=()) for slug, cfg in CONFIGS.items()}


@pytest.fixture(scope="module")
def s2_empty() -> Dict[Tuple[str, str], Dict[str, Any]]:
    return by_key(f for f in classify(FakeSEC(), stakes=[], configs=s2_only_configs()) if f["source"] == C.SOURCE_EFTS)


def test_s2_after_s1_in_one_run_the_coreweave_closing_meets_the_13g_row():
    """Both halves in ONE classification: NVIDIA's CoreWeave 13G/A (auto) is on file by the time
    the CoreWeave closing 8-K is read — another company's filing about the member's own row is a
    heads-up ("a newer filing for an existing row"), never a second row and never a replace."""
    found = by_key(classify(FakeSEC(), stakes=[]))
    assert found[("nvidia", acc_of("nvidia_coreweave_13ga"))]["outcome"] == C.OUTCOME_AUTO
    f = found[("nvidia", acc_of("coreweave_closing_8k"))]
    assert (f["outcome"], f["reason"], f["row"]) == (C.OUTCOME_HEADS_UP, "existing_row", None)
    assert f["detail"].startswith("CoreWeave · 11.5% of Class A")


@pytest.mark.parametrize("key", sorted(S2_EXPECTED))
def test_s2_outcome_of_each_recorded_document_with_no_row_on_file(key, s2_empty):
    slug, name = key
    outcome, tier, reason, investee, kind = S2_EXPECTED[key]
    f = s2_empty[(slug, acc_of(name))]
    assert (f["outcome"], f["tier"], f["reason"]) == (outcome, tier, reason), f
    if outcome == C.OUTCOME_PROPOSAL:
        assert f["row"]["investee_name"] == investee and f["row"]["kind"] == kind
        assert f["spans"], "a proposal carries its verified evidence"
    else:
        assert f["row"] is None


def test_s2_only_an_8k_closing_is_a_trial(s2_empty):
    """Phase 1: an 8-K closing is a proposal marked "Trial — would auto-publish"; nothing else is."""
    for key, (outcome, tier, *_rest) in S2_EXPECTED.items():
        f = s2_empty[(key[0], acc_of(key[1]))]
        assert f["trial"] is (tier == C.TIER_SEC_CLOSING), key


def test_s2_the_closings_carry_the_amount_invested(s2_empty):
    amounts = {"intel_closing_8k": 5e9, "coherent_closing_8k": 2e9, "synopsys_closing_8k": 2e9,
               "coreweave_closing_8k": 2e9, "marvell_preferred_8k": 2e9, "lumentum_preferred_8k": 2e9}
    for name, amount in amounts.items():
        row = s2_empty[("nvidia", acc_of(name))]["row"]
        assert (row["disclosed_value_usd"], row["value_basis"]) == (amount, "invested"), name
        assert row["ownership_pct"] is None and row["ownership_basis"] is None
    for name in ("nokia_directed_6k",):
        row = s2_empty[("nvidia", acc_of(name))]["row"]
        assert row["disclosed_value_usd"] is None and row["value_basis"] is None
    row = s2_empty[("alphabet", acc_of("marvell_google_warrant_ex41"))]["row"]
    assert row["disclosed_value_usd"] is None


def test_s2_the_meta_preliminary_and_final_are_heads_ups_for_each_cornerstone_investor(s2_empty):
    for slug in ("nvidia", "amd", "broadcom"):
        for acc in (META_PRELIMINARY, META_FINAL):
            f = s2_empty[(slug, acc)]
            assert (f["tier"], f["reason"], f["outcome"]) == (C.TIER_IOI, "t3_indication", C.OUTCOME_HEADS_UP)
            assert f["investee_cik"] == "0001326801"


def test_s2_a_document_the_fixtures_did_not_record_is_a_fetch_failed_heads_up():
    """With every recorded hit (documents not recorded answer 404): a heads-up with the link,
    never a skip — and a recorded document of the same filing still wins."""
    found = by_key(f for f in classify(FakeSEC(recorded_only=False), stakes=[], configs=s2_only_configs())
                   if f["source"] == C.SOURCE_EFTS)
    lucid = found[("nvidia", "0001104659-26-019140")]
    assert (lucid["outcome"], lucid["reason"]) == (C.OUTCOME_HEADS_UP, "fetch_failed")
    assert lucid["source_url"].endswith("/tm2533685-1_424b7.htm")
    marvell = found[("nvidia", acc_of("marvell_preferred_8k"))]
    assert marvell["outcome"] == C.OUTCOME_PROPOSAL, "the 8-K beats its unrecorded EX-3.1"
    multi = found[("nvidia", "0001193125-26-021087")]
    assert (multi["outcome"], multi["reason"], multi["investee_cik"]) == (C.OUTCOME_HEADS_UP, "multi_cik", None)


# ═════════════════════════════════════════════════════════════════════════════════════════
# §5 The Meta 424B5 → ONE digest line for NVIDIA, AMD and Broadcom
# ═════════════════════════════════════════════════════════════════════════════════════════


def _ledger_rows(found: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """classify_only's findings as the digest reads them from the ledger (status new)."""
    out = []
    for i, f in enumerate(found):
        meta: Dict[str, Any] = {"_investee": "Meta Platforms"}
        out.append({"id": f"00000000-0000-4000-8000-{i:012d}", "company_slug": f["slug"], "status": "new",
                    "outcome": f["outcome"], "reason": f["reason"], "investee_cik": f["investee_cik"],
                    "accession": f["accession"], "form": f["form"], "filed_on": f["filed_on"],
                    "source_url": f["source_url"], "created_at": f"2026-10-28T22:30:00.{i:06d}+00:00",
                    "notified_at": None, "stake_id": None, "proposed_row": meta})
    return out


def test_the_meta_case_is_one_merged_digest_line():
    sec = FakeSEC()
    raws = []
    ctx = SF._Ctx(today=TODAY, companies=SF._parse_registry(seed_companies())[0],
                  company_rows=SF._parse_registry(seed_companies())[1], configs=CONFIGS)
    disc = SF._Discovery(ctx=ctx, sec=SF._SecGate(sec, deadline=1e18), start=date(2026, 10, 1), end=TODAY,
                         known=_no_known, holdings=_no_holdings, fail=lambda s, m: raws.append(m))

    async def collect():
        out = []
        for slug in ("nvidia", "amd", "broadcom"):
            others = [a for s, c in CONFIGS.items() if s != slug for a in c.s2_aliases]
            async for raw in disc.s2(CONFIGS[slug], others):
                out.append(raw)
        return out

    found = [r for r in asyncio.run(collect()) if r.reason == "t3_indication"]
    assert len(found) == 6, "two accessions × three members"
    rows = []
    for i, r in enumerate(found):
        payload_meta = {"_investee": r.label, "_t3": r.t3}
        rows.append({"id": f"00000000-0000-4000-8000-{i:012d}", "company_slug": r.slug, "status": "new",
                     "outcome": C.OUTCOME_HEADS_UP, "reason": r.reason, "investee_cik": r.investee_cik,
                     "accession": r.accession, "form": r.form, "filed_on": r.filed_on.isoformat(),
                     "source_url": r.source_url, "created_at": f"2026-10-28T22:30:00.{i:06d}+00:00",
                     "notified_at": None, "stake_id": None, "proposed_row": payload_meta})
    lines, marks, deduped = SF._digest_lines(rows, [], ctx=ctx, now=NOW, waiting=0, alerts=[], missing=[])
    t3 = [line for line in lines if "non-binding interest" in line]
    assert len(t3) == 1, lines
    assert t3[0].startswith("• Meta Platforms 424B5 names NVIDIA, AMD, Broadcom: non-binding interest, up to "
                            "$5.0B combined https://www.sec.gov/Archives/edgar/data/1326801/"), t3[0]
    assert len(marks) == 6 and deduped == [], "every finding the line stands for is marked when it is sent"


async def _no_known(_slug: str, _accs: List[str]) -> Dict[str, Any]:
    return {}


async def _no_holdings(_slug: str) -> None:
    return None


def test_a_t3_line_delivered_before_dedupes_the_final_prospectus():
    """The 10-26 run reported NVIDIA's line; the final 424B5 on 10-28 is the same (member,
    investee, reason) within 30 days → no second line, and it is still marked reported."""
    ctx = SF._Ctx(today=TODAY, companies=SF._parse_registry(seed_companies())[0],
                  company_rows=SF._parse_registry(seed_companies())[1], configs=CONFIGS)
    prior = {"id": "00000000-0000-4000-8000-000000000099", "company_slug": "nvidia", "status": "reported",
             "reason": "t3_indication", "investee_cik": "0001326801", "accession": META_PRELIMINARY,
             "notified_at": "2026-10-26T22:31:00+00:00", "created_at": "2026-10-26T22:30:00+00:00"}
    new = {"id": "00000000-0000-4000-8000-000000000100", "company_slug": "nvidia", "status": "new",
           "outcome": C.OUTCOME_HEADS_UP, "reason": "t3_indication", "investee_cik": "0001326801",
           "accession": META_FINAL, "form": "424B5", "filed_on": "2026-10-28", "source_url": "https://x",
           "created_at": "2026-10-28T22:30:00+00:00", "notified_at": None, "stake_id": None,
           "proposed_row": {"_t3": {"key": "Cornerstone Investors", "company": "Meta Platforms",
                                    "members": ["NVIDIA Corporation"], "amount": 5e9}}}
    lines, marks, deduped = SF._digest_lines([new], [prior], ctx=ctx, now=NOW, waiting=0, alerts=[], missing=[])
    assert not any("non-binding" in line for line in lines)
    assert [d["id"] for d in deduped] == [new["id"]] and marks == []


def test_the_worst_case_digest_fits_under_the_notifiers_backstop():
    """Review A TG-2: the notifier drops lines past MAX_DIGEST_PARTS messages. The finder's own
    size budget must keep its LONGEST possible digest (every line at the notifier's 1,000-char
    cap, wide characters included) under that backstop, with the overflow left for the next one."""
    from app.services.trillion_club import stake_review as R

    ctx = SF._Ctx(today=TODAY, companies=SF._parse_registry(seed_companies())[0],
                  company_rows=SF._parse_registry(seed_companies())[1], configs=CONFIGS)
    pending = []
    for i in range(SF.DIGEST_MAX_LINES * 3):
        pending.append({"id": f"00000000-0000-4000-8000-{i:012d}", "company_slug": "nvidia", "status": "new",
                        "outcome": C.OUTCOME_HEADS_UP, "reason": "existing_row", "investee_cik": f"{i:010d}",
                        "accession": f"0001045810-26-{i:06d}", "form": "8-K", "filed_on": "2026-10-28",
                        "source_url": "https://www.sec.gov/Archives/edgar/data/1/" + "é" * 900,
                        "created_at": "2026-10-28T22:30:00+00:00", "notified_at": None, "stake_id": None,
                        "proposed_row": {"_investee": "Ä" * 80, "_detail": "ß" * 200}})
    lines, marks, _ = SF._digest_lines(pending, [], ctx=ctx, now=NOW, waiting=3, alerts=["a"], missing=["x"])
    parts, dropped = R.digest_parts(lines, today=TODAY)
    assert dropped == 0 and len(parts) < R.MAX_DIGEST_PARTS, (len(parts), dropped)
    assert lines[-1].startswith("• +") or any(l.startswith("• +") for l in lines), "the overflow is announced"
    assert 0 < len(marks) < len(pending), "only what was shown is marked; the rest waits"


def _t3_finding(slug: str, t3: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": f"00000000-0000-4000-8000-0000000001{len(slug):02d}", "company_slug": slug, "status": "new",
            "outcome": C.OUTCOME_HEADS_UP, "reason": "t3_indication", "investee_cik": "0001326801",
            "accession": META_FINAL, "form": "424B5", "filed_on": "2026-10-28", "source_url": "https://x",
            "created_at": "2026-10-28T22:30:00+00:00", "notified_at": None, "stake_id": None,
            "proposed_row": {"_t3": t3}}


@pytest.mark.parametrize("t3, says, never", [
    # "NVIDIA and Beta Capital LLC have indicated … up to $1.0 billion": one club name, a group figure
    ({"key": "k", "company": "Acme Robotics", "members": ["NVIDIA Corporation"], "combined": True,
      "amount": 1e9}, "names NVIDIA and others: non-binding interest, up to $1.0B combined", None),
    # "the Cornerstone Investors" (a defined group the club aliases are not listed in)
    ({"key": "Cornerstone Investors", "company": "Acme Robotics", "members": [], "combined": True,
      "amount": 2e9}, "names NVIDIA and others: non-binding interest, up to $2.0B combined", None),
    # the member alone heads the subject: its own figure, never "combined"
    ({"key": "k", "company": "Acme Robotics", "members": [], "combined": False, "amount": 1e9},
     "names NVIDIA: non-binding interest, up to $1.0B", "combined"),
])
def test_a_t3_group_figure_is_never_one_members_own(t3, says, never):
    """Review round 2: the line used to say "combined" only when it could list two club names, so
    a group's figure beside ONE club member read as that member's own amount."""
    ctx = SF._Ctx(today=TODAY, companies=SF._parse_registry(seed_companies())[0],
                  company_rows=SF._parse_registry(seed_companies())[1], configs=CONFIGS)
    line = SF._t3_line([_t3_finding("nvidia", t3)], ctx)
    assert says in line, line
    assert never is None or never not in line, line


# ═════════════════════════════════════════════════════════════════════════════════════════
# §6 S2 against the REAL seed — the recorded closings are all on file already
# ═════════════════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def s2_seed() -> Dict[Tuple[str, str], Dict[str, Any]]:
    return by_key(f for f in classify(FakeSEC(), stakes=seed_stakes()) if f["source"] == C.SOURCE_EFTS)


@pytest.mark.parametrize("name, why", [
    ("intel_closing_8k", "same"), ("coherent_closing_8k", "same"), ("synopsys_closing_8k", "same"),
    ("nokia_directed_6k", "same"),            # the 6-K states no percent; the seed's 2.9% stands
    ("coreweave_closing_8k", "older"), ("marvell_preferred_8k", "older"), ("lumentum_preferred_8k", "older"),
])
def test_s2_against_the_seed_every_recorded_nvidia_deal_is_on_file_already(name, why, s2_seed):
    """Every recorded closing is the very filing a seed row was built from (or older than its
    figure): never a row, never a digest line."""
    f = s2_seed[("nvidia", acc_of(name))]
    assert (f["outcome"], f["reason"], f["row"]) == (C.OUTCOME_SKIPPED, "existing_row", None), f
    assert f["detail"].startswith(_SKIP_DETAIL[why]), f["detail"]


def test_s2_against_the_seed_google_llcs_marvell_warrant_is_still_a_proposal(s2_seed):
    """Alphabet has no Marvell row: the warrant (found in review, not in the seed) is proposed."""
    f = s2_seed[("alphabet", acc_of("marvell_google_warrant_ex41"))]
    assert (f["outcome"], f["reason"]) == (C.OUTCOME_PROPOSAL, "instrument")


# ═════════════════════════════════════════════════════════════════════════════════════════
# §7 Every built row against the seed's own row for the same pair
# ═════════════════════════════════════════════════════════════════════════════════════════

#: (slug, seed investee_name, seed kind) → (finding source, fixture name)
PAIRS = {
    ("nvidia", "Nebius", "on_13f_note"): ("s1", "nvidia_nebius_13g"),
    ("alphabet", "SpaceX", "on_13f_note"): ("s1", "google_spacex_13g"),
    ("amazon", "X-Energy", "on_13f_note"): ("s1", "amazon_xenergy_13g"),
    ("eli-lilly", "ProQR Therapeutics", "us_listed_off_13f"): ("s1", "lilly_proqr_13ga"),
    ("eli-lilly", "Aktis Oncology", "us_listed_off_13f"): ("s1", "lilly_aktis_13d"),
    ("eli-lilly", "Scribe Therapeutics", "us_listed_off_13f"): ("s1", "lilly_scribe_13d"),
    ("eli-lilly", "Ionetix", "private"): ("s1", "lilly_ionetix_13g"),
    ("eli-lilly", "TRex Bio", "private"): ("s1", "lilly_trex_13d"),
    ("nvidia", "CoreWeave", "on_13f_note"): ("s1", "nvidia_coreweave_13ga"),
    ("nvidia", "Intel", "on_13f_note"): ("s2", "intel_closing_8k"),
    ("nvidia", "Coherent", "on_13f_note"): ("s2", "coherent_closing_8k"),
    ("nvidia", "Synopsys", "on_13f_note"): ("s2", "synopsys_closing_8k"),
    ("nvidia", "Marvell Technology (convertible preferred)", "us_listed_off_13f"): ("s2", "marvell_preferred_8k"),
    ("nvidia", "Lumentum Holdings (convertible preferred)", "us_listed_off_13f"): ("s2", "lumentum_preferred_8k"),
    ("nvidia", "Nokia", "on_13f_note"): ("s2", "nokia_directed_6k"),
}
_COMPARED = ("investee_name", "kind", "ownership_pct", "ownership_basis", "as_of", "investee_us_symbol",
             "investee_cusip", "disclosed_value_usd", "value_basis", "source_title", "source_url")
_SEC = "https://www.sec.gov/Archives/edgar/data"

#: Every difference between the built row and the seed's, with its reason. Anything not listed
#: must be EQUAL.
DIFFERENCES: Dict[Tuple[str, str], Dict[str, Tuple[Any, Any, str]]] = {
    ("nvidia", "Nebius"): {
        "investee_name": ("Nebius Group", "Nebius", "SEC's name minus one legal suffix; the seed shortened it"),
        "ownership_basis": ("of Class A, incl. 21.1M warrant shares", "of Class A",
                            "the finder names the warrant shares in the basis (owner decision 2026-10-28); the "
                            "seed tells it in its background line"),
    },
    ("alphabet", "SpaceX"): {
        "source_url": (f"{_SEC}/1824723/000119312526352289/xslSCHEDULE_13G_X02/primary_doc.xml",
                       f"{_SEC}/1181412/000119312526352289/xslSCHEDULE_13G_X01/primary_doc.xml",
                       "the plan cites the FILER's path and SEC's current XSL; the seed used the issuer's"),
    },
    ("amazon", "X-Energy"): {
        "source_url": (f"{_SEC}/1018724/000110465926092071/xslSCHEDULE_13G_X02/primary_doc.xml",
                       f"{_SEC}/2088896/000110465926092071/xslSCHEDULE_13G_X01/primary_doc.xml",
                       "filer path and current XSL (as SpaceX)"),
    },
    ("eli-lilly", "Aktis Oncology"): {
        "disclosed_value_usd": (None, 112000000.0, "a 13D gives no amount paid: the seed added it from the S-1"),
        "value_basis": (None, "invested", "goes with the amount"),
        "source_url": (f"{_SEC}/59478/000119312526013469/xslSCHEDULE_13D_X01/primary_doc.xml",
                       f"{_SEC}/59478/000119312526013469/xslSCHEDULE_13D_X02/primary_doc.xml",
                       "submissions list this 13D under XSL X01; the seed cites X02"),
    },
    ("eli-lilly", "Scribe Therapeutics"): {
        "disclosed_value_usd": (None, 35000000.0, "the seed's amount is from the IPO prospectus, not the 13D"),
        "value_basis": (None, "invested", "goes with the amount"),
    },
    ("eli-lilly", "TRex Bio"): {
        "kind": ("us_listed_off_13f", "private", "TRex listed (TRXB) after the seed row was written"),
        "ownership_pct": (19.9, None, "the seed row predates the 13D (from the S-1, 'more than 5%')"),
        "ownership_basis": ("of common stock", None, "goes with the percent"),
        "as_of": ("2026-10-13", "2026-06-30", "the 13D's event vs the S-1's balance-sheet date"),
        "investee_us_symbol": ("TRXB", None, "listed after the seed row"),
        "investee_cusip": ("89531N100", None, "the 13D gives it; a private row had none"),
        "source_title": ("Lilly Schedule 13D (Oct 16, 2026)", "TRex Bio S-1 (Sep 18, 2026)", "another source"),
        "source_url": (f"{_SEC}/59478/000005947826000095/xslSCHEDULE_13D_X02/primary_doc.xml",
                       f"{_SEC}/2139097/000119312526395671/ck0002139097-20260918.htm", "another source"),
    },
    ("nvidia", "CoreWeave"): {
        "ownership_pct": (11.5, 10.66, "the 13G/A (Jan 2026) vs the seed's later proxy statement"),
        "as_of": ("2026-01-23", "2026-04-15", "another source"),
        "source_title": ("NVIDIA Schedule 13G/A (Jan 26, 2026)", "CoreWeave proxy statement (Apr 22, 2026)",
                         "another source"),
        "source_url": (f"{_SEC}/1045810/000104581026000008/xslSCHEDULE_13G_X01/primary_doc.xml",
                       f"{_SEC}/1769628/000176962826000191/crwv-20260422.htm", "another source"),
    },
    ("nvidia", "Intel"): {
        "investee_cusip": (None, "458140100", "an 8-K carries no CUSIP; the read path links the note by symbol"),
    },
    ("nvidia", "Coherent"): {"investee_cusip": (None, "19247G107", "as Intel")},
    ("nvidia", "Synopsys"): {"investee_cusip": (None, "871607107", "as Intel")},
    ("nvidia", "Marvell Technology (convertible preferred)"): {
        "as_of": ("2026-03-31", "2026-08-01", "the closing 8-K vs the seed's later 10-Q"),
        "source_title": ("Marvell Technology 8-K (Mar 31, 2026)", "Marvell 10-Q (quarter ended Aug 1, 2026)",
                         "another source"),
        "source_url": (f"{_SEC}/1835632/000119312526134462/d113606d8k.htm",
                       f"{_SEC}/1835632/000183563226000025/mrvl-20260801.htm", "another source"),
    },
    ("nvidia", "Lumentum Holdings (convertible preferred)"): {
        "as_of": ("2026-03-02", "2026-06-27", "the closing 8-K vs the seed's later 10-K"),
        "source_title": ("Lumentum Holdings 8-K (Mar 2, 2026)", "Lumentum 10-K (fiscal year ended Jun 27, 2026)",
                         "another source"),
        "source_url": (f"{_SEC}/1633978/000119312526085412/d41019d8k.htm",
                       f"{_SEC}/1633978/000162828026057358/lite-20260627.htm", "another source"),
    },
    ("nvidia", "Nokia"): {
        "investee_cusip": (None, "654902204", "a 6-K carries no CUSIP"),
        "ownership_pct": (None, 2.9, "the template reads the 2.90% only into the detail (no basis it can pin)"),
        "ownership_basis": (None, "of shares after the issuance", "goes with the percent"),
    },
}


@pytest.fixture(scope="module")
def built_rows(s1_empty, s2_empty) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
    out = {}
    for key, (source, name) in PAIRS.items():
        found = (s1_empty if source == "s1" else s2_empty)[(key[0], acc_of(name))]
        out[key] = found["row"]
    return out


def _seed_row(slug: str, name: str, kind: str) -> Dict[str, Any]:
    rows = [s for s in SEED["stakes"] if (s["company_slug"], s["investee_name"], s["kind"]) == (slug, name, kind)]
    assert len(rows) == 1, (slug, name, kind)
    return rows[0]


@pytest.mark.parametrize("pair", sorted(PAIRS))
def test_each_built_row_matches_the_seeds_row_except_the_pinned_differences(pair, built_rows):
    built, seed = built_rows[pair], _seed_row(*pair)
    pinned = DIFFERENCES.get((pair[0], pair[1]), {})
    for col in _COMPARED:
        if col in pinned:
            new, old, why = pinned[col]
            assert (built[col], seed[col]) == (new, old), (pair, col, why)
            assert why
        else:
            assert built[col] == seed[col], (pair, col, built[col], seed[col])


def test_the_difference_table_names_only_compared_columns_and_real_pairs():
    for (slug, name), cols in DIFFERENCES.items():
        assert any(k[0] == slug and k[1] == name for k in PAIRS), (slug, name)
        assert set(cols) <= set(_COMPARED)


def test_machine_rows_never_carry_the_seeds_prose_flags(built_rows):
    """The seed marks most of these material, tied to a deal and gives a background line; a
    machine row never does (migration 205's machine_shape; the owner adds prose by Keep or seed)."""
    for pair, row in built_rows.items():
        seed = _seed_row(*pair)
        assert row["background"] is None and row["tied_to_deal"] is False and row["material"] is False
        assert seed["background"] is not None or seed["material"] in (True, False)


# ═════════════════════════════════════════════════════════════════════════════════════════
# §8 The same filings through run_stakes: auto under the tiers, and the volume cap
# ═════════════════════════════════════════════════════════════════════════════════════════


def run_finder(fake: F.FakeSupabase, sec: FakeSEC, notifier: FakeNotifier, *, now: datetime = NOW,
               configs: Optional[Mapping[str, C.MemberConfig]] = None) -> Dict[str, Any]:
    return asyncio.run(SF.run_stakes(now, sec=sec, store=StakeStore(fake), notifier=notifier,
                                     member_configs=configs or CONFIGS, club_store=TrillionClubStore(fake)))


@pytest.fixture
def switched_on(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "TRILLION_CLUB_JOBS_ENABLED", True)
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_FINDER_ENABLED", True)
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "Caydex test bot test@example.com")
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_TIERS", "13dg")
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_SINCE", "2025-01-01")
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_LOOKBACK_DAYS", 120)
    return settings


def test_a_full_run_over_the_recorded_schedules_publishes_three_and_caps_the_rest(switched_on):
    """Empty stake table, AUTO_TIERS=13dg, AUTO_SINCE before every filing, a 120-day lookback
    (2026-06-30 … 10-28): the plain own 13D/13Gs in the window — X-Energy (Aug 6), SpaceX (Aug 14),
    Scribe (Jul 30), ProQR (Jul 24), TRex (Oct 16) — are auto-eligible; the daily cap publishes 3
    and the rest go out as proposals marked volume_cap. Nebius / TeraWulf / Ionetix are proposals."""
    fake = F.make_fake(include_stakes=False)
    for slug, h in HOLDINGS.items():
        cik = next(c["ciks"][0] for c in SEED["companies"] if c["slug"] == slug)
        fake.add_filing(cik, "2026-Q2", "complete", [{"cusip": c, "symbol": None} for c in h["cusips"]])
    notifier = FakeNotifier()
    summary = run_finder(fake, FakeSEC(), notifier)
    live = [s for s in fake.rows(F.STAKES) if s["published"]]
    assert len(live) == 3 and {s["origin"] for s in live} == {"auto"}
    assert len(notifier.of("auto")) == 3
    findings = {f["accession"]: f for f in fake.rows(F.FINDINGS)}
    auto_eligible = {acc_of(n) for n in ("amazon_xenergy_13g", "google_spacex_13g", "lilly_scribe_13d",
                                          "lilly_proqr_13ga", "lilly_trex_13d")}
    capped = [findings[a] for a in auto_eligible if findings[a]["reason"] == "volume_cap"]
    assert len(capped) == 2 and {f["outcome"] for f in capped} == {C.OUTCOME_PROPOSAL}
    assert {f["status"] for f in capped} == {C.STATUS_PROPOSED}
    published = [findings[a] for a in auto_eligible if findings[a]["status"] == C.STATUS_PUBLISHED]
    assert len(published) == 3
    for name in ("nvidia_nebius_13g", "google_terawulf_13g", "lilly_ionetix_13g"):
        f = findings[acc_of(name)]
        assert (f["outcome"], f["status"]) == (C.OUTCOME_PROPOSAL, C.STATUS_PROPOSED), name
    assert any("cap reached" in line for line in notifier.digest_lines())
    assert summary["ok"] is True, summary["failures"]


def test_with_the_default_tiers_nothing_publishes_on_its_own(switched_on, monkeypatch):
    monkeypatch.setattr(switched_on, "TRILLION_CLUB_STAKE_AUTO_TIERS", "")
    fake = F.make_fake(include_stakes=False)
    notifier = FakeNotifier()
    run_finder(fake, FakeSEC(), notifier)
    assert not any(s["published"] for s in fake.rows(F.STAKES)), "no tier enabled: no row goes live alone"
    assert notifier.of("auto") == []
    tier_off = [f for f in fake.rows(F.FINDINGS) if f["reason"] == "tier_off"]
    assert tier_off and {f["status"] for f in tier_off} == {C.STATUS_PROPOSED}


# ═════════════════════════════════════════════════════════════════════════════════════════
# §9 Every row the finder builds passes the stake validators
# ═════════════════════════════════════════════════════════════════════════════════════════


def test_every_built_row_passes_stake_problems_and_the_read_path(s1_empty, s2_empty):
    from app.services.trillion_club_service import stake_problem

    companies = {c["slug"]: c for c in seed_companies()}
    rows = [f["row"] for f in list(s1_empty.values()) + list(s2_empty.values()) if f["row"]]
    assert len(rows) >= 15
    for row in rows:
        cols = {c: row[c] for c in stake_rules.STAKE_COLUMNS}
        assert stake_rules.stake_problems({**cols, "published": True}, companies, today=TODAY) == [], row
        assert stake_problem({**cols, "published": True}, TODAY) is None, row
        origin = "auto" if row["kind"] in ("on_13f_note", "us_listed_off_13f") and row["disclosed_value_usd"] is None \
            else "proposal"
        assert SS.stake_check_violations({**cols, "origin": origin, "finding_id": SF._PLACEHOLDER_FINDING,
                                          "investee_cik": row["investee_cik"]}) == [], row


def test_a_second_full_run_over_the_same_filings_writes_and_sends_nothing(switched_on):
    fake = F.make_fake(include_stakes=True)
    notifier = FakeNotifier()
    sec = FakeSEC()
    first = run_finder(fake, sec, notifier)
    assert first["ok"] is True, first["failures"]
    writes, calls = len(fake.writes()), len(notifier.calls)
    second = run_finder(fake, sec, notifier, now=NOW + timedelta(hours=1))
    assert second["ok"] is True and second["items"] == 0
    assert len(fake.writes()) == writes and len(notifier.calls) == calls
    assert fake.misuse == []
