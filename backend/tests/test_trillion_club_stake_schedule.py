"""The stake finder's S1 source: a member's own Schedule 13D/13G, parsed and judged.

Every recorded filing in ``tests/fixtures/trillion_club_stakes/xml`` (public SEC data,
recorded 2026-10-28; MANIFEST.json lists them) is pinned to its exact outcome, and every
outcome the selector can return is pinned on a synthetic twin built here. The parser runs
on hostile text too: DTDs, entities, UTF-16 decoded the wrong way, HTML, deep nesting.
Hermetic: no I/O beyond reading the fixtures.
"""
from __future__ import annotations

import ast
import dataclasses
import gc
import json
import pickle
import re
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

import pytest

from app.services.trillion_club import stake_schedule as S
from app.services.trillion_club.stake_contracts import (
    REASONS,
    FilerEntry,
    MemberConfig,
    load_member_configs,
)

BACKEND = Path(__file__).resolve().parents[1]
FIX = BACKEND / "tests" / "fixtures" / "trillion_club_stakes"
XML_DIR = FIX / "xml"
MEMBERS = load_member_configs(str(BACKEND / "data" / "trillion_club_filers.json"))
NVIDIA, ALPHABET, AMAZON, LILLY = (MEMBERS[s] for s in ("nvidia", "alphabet", "amazon", "eli-lilly"))


def _fixture(stem: str) -> str:
    return (XML_DIR / f"{stem}.xml").read_text(encoding="utf-8")


def _parse(stem: str) -> S.ScheduleFiling:
    return S.parse_schedule_xml(_fixture(stem))


# ══════════════════════════════════════════════════════════════════════════════════════
# 1. The recorded filings, each pinned to its outcome
# ══════════════════════════════════════════════════════════════════════════════════════

#: stem → (member, reason, chosen line, type, shares, percent, basis, cusip, derivative note)
RECORDED = {
    "nvidia_nebius_13g": (
        NVIDIA, "derivative_position", "NVIDIA Corporation", "CO", 22_256_412, "9.3",
        "of Class A", "N97284108", "incl. 21.1M warrant shares",
    ),
    "google_spacex_13g": (
        ALPHABET, None, "Alphabet Inc.", "CO", 551_189_500, "7.2", "of Class A", "84615Q103", None,
    ),
    "google_terawulf_13g": (
        ALPHABET, "derivative_position", "Alphabet Inc.", "CO", 41_011_803, "7.6",
        "of common stock", "88080T104", "incl. 41.0M warrant shares",
    ),
    "amazon_xenergy_13g": (
        AMAZON, None, "Amazon.com, Inc.", "CO", 65_836_948, "22.9", "of Class A", "98386P102", None,
    ),
    "lilly_proqr_13ga": (
        LILLY, None, "ELI LILLY & Co", "CO", 21_995_880, "15.6", "of ordinary shares", "N71542109", None,
    ),
    "lilly_ionetix_13g": (
        LILLY, None, "ELI LILLY & Co", "CO", 8_411_397, "7.9", "of common stock", None, None,
    ),
    "lilly_aktis_13d": (
        LILLY, None, "Eli Lilly and Company", "CO", 6_344_114, "11.9", "of common stock", "00973N102", None,
    ),
    "lilly_scribe_13d": (
        LILLY, None, "ELI LILLY & Co", "CO", 1_388_161, "7.3", "of common stock", "811033109", None,
    ),
    "lilly_trex_13d": (
        LILLY, None, "ELI LILLY & Co", "CO", 5_480_872, "19.9", "of common stock", "89531N100", None,
    ),
    # NVIDIA's CoreWeave 13G/A (Jan 26, 2026): an older 13G file (no schemaVersion) with a bare
    # issuerCusip — a plain Class A position of 11.5%.
    "nvidia_coreweave_13ga": (
        NVIDIA, None, "NVIDIA Corporation", "CO", 47_213_353, "11.5", "of Class A", "21873S108", None,
    ),
}


def test_every_recorded_schedule_is_pinned():
    manifest = json.loads((FIX / "MANIFEST.json").read_text(encoding="utf-8"))
    assert set(manifest["schedules"]) == set(RECORDED)
    assert {p.stem for p in XML_DIR.glob("*.xml")} == set(RECORDED)


@pytest.mark.parametrize("stem", sorted(RECORDED))
def test_recorded_schedule_outcome(stem):
    member, reason, line, type_, shares, percent, basis, cusip, note = RECORDED[stem]
    result = S.select_member_position(_parse(stem), member)
    assert result.reason == reason
    assert result.ok is (reason is None)
    assert result.person is not None and result.person.name == line and result.person.type == type_
    assert result.shares == shares
    assert result.percent == Decimal(percent)
    assert result.basis == basis
    assert result.cusip == cusip
    assert result.derivative_note == note
    # A counted warrant never also carries a quote; a clean position carries neither.
    assert result.derivative_quote is None


#: stem → (schema, submission type, issuer CIK, issuer name, cusips, class title, event date,
#: five-percent-or-less, filer CIKs, number of lines)
PARSED = {
    "nvidia_nebius_13g": ("13G", "SCHEDULE 13G", "0001513845", "Nebius Group N.V.", ("N97284108",),
                          "Class A Ordinary Shares", date(2026, 7, 13), False, ("0001045810",), 1),
    "google_spacex_13g": ("13G", "SCHEDULE 13G", "0001181412", "Space Exploration Technologies Corp.",
                          ("84615Q103",), "Class A Common Stock, $0.001 par value", date(2026, 6, 30),
                          None, ("0001824723",), 3),
    "google_terawulf_13g": ("13G", "SCHEDULE 13G", "0001083301", "TeraWulf Inc.", ("88080T104",),
                            "Common Stock, par value $0.001 per share", date(2026, 9, 11), None,
                            ("0001824723",), 3),
    "amazon_xenergy_13g": ("13G", "SCHEDULE 13G", "0002088896", "X-Energy, Inc.", ("98386P102",),
                           "Class A common stock, par value $0.0001", date(2026, 6, 30), None,
                           ("0001018724",), 1),
    "lilly_proqr_13ga": ("13G", "SCHEDULE 13G/A", "0001612940", "ProQR Therapeutics N.V.", ("N71542109",),
                         "Ordinary Shares, nominal value 0.04 EUR per share", date(2026, 6, 26), None,
                         ("0000059478",), 1),
    "lilly_ionetix_13g": ("13G", "SCHEDULE 13G", "0002108121", "Ionetix Corp / DE /", ("000000000",),
                          "Common Stock, par value $0.0001 per share", date(2026, 4, 9), False,
                          ("0000059478",), 1),
    "lilly_aktis_13d": ("13D", "SCHEDULE 13D", "0002035832", "Aktis Oncology, Inc.", ("00973N102",),
                        "Common Stock, $0.0001 par value per share", date(2026, 1, 12), False,
                        ("0000059478",), 1),
    "lilly_scribe_13d": ("13D", "SCHEDULE 13D", "0001853921", "Scribe Therapeutics, Inc.", ("811033109",),
                         "Common stock, $0.001 par value per share", date(2026, 7, 23), False,
                         ("0000059478",), 1),
    "lilly_trex_13d": ("13D", "SCHEDULE 13D", "0002139097", "TRex Bio, Inc.", ("89531N100",),
                       "Common Stock, par value $0.0001 per share", date(2026, 10, 13), False,
                       ("0000059478",), 1),
    "nvidia_coreweave_13ga": ("13G", "SCHEDULE 13G/A", "0001769628", "CoreWeave, Inc.", ("21873S108",),
                              "Class A common stock, par value $0.000005 per share", date(2026, 1, 23),
                              False, ("0001045810",), 1),
}


@pytest.mark.parametrize("stem", sorted(PARSED))
def test_recorded_schedule_fields(stem):
    schema, sub, cik, name, cusips, title, event, five, filers, n_lines = PARSED[stem]
    f = _parse(stem)
    assert (f.schema, f.submission_type, f.issuer_cik, f.issuer_name) == (schema, sub, cik, name)
    assert f.cusips == cusips
    assert f.class_title == title
    assert f.event_date == event
    assert f.five_percent_or_less is five
    assert f.filer_ciks == filers
    assert len(f.persons) == n_lines


def test_nebius_counts_the_pre_funded_warrant_from_the_cover_comment():
    f = _parse("nvidia_nebius_13g")
    (line,) = f.persons
    assert line.sole_voting == line.sole_dispositive == Decimal(22_256_412)
    assert line.shared_voting == line.shared_dispositive == Decimal(0)
    assert "21,065,936 Class A ordinary shares issuable upon exercise of a pre-funded" in line.comments
    assert line.member_of_group is None


def test_spacex_prefers_alphabets_own_co_line_and_reads_one_line_never_a_sum():
    f = _parse("google_spacex_13g")
    assert [p.name for p in f.persons] == ["Google LLC", "XXVI Holdings Inc.", "Alphabet Inc."]
    assert [p.type for p in f.persons] == ["OO", "CO", "CO"]
    r = S.select_member_position(f, ALPHABET)
    assert r.ok and r.person.name == "Alphabet Inc." and r.person.type == "CO"
    assert r.shares == 551_189_500                       # one line — never 3 × 551,189,500
    # The signature block repeats reportingPersonName: it is never read as a line.
    assert len(f.persons) == 3


def test_a_filing_about_the_member_is_never_its_stake():
    # The same SpaceX 13G seen from SpaceX (a member whose own CIK is the issuer): someone
    # else's filing ABOUT the member.
    r = S.select_member_position(_parse("google_spacex_13g"), MEMBERS["spacex"])
    assert (r.ok, r.reason, r.person) == (False, "about_member", None)


def test_a_member_with_no_line_in_the_filing():
    r = S.select_member_position(_parse("nvidia_nebius_13g"), AMAZON)
    assert (r.reason, r.person, r.shares) == ("member_line_missing", None, None)


def test_ionetix_zero_cusip_is_kept_as_filed_but_is_no_identity_key():
    f = _parse("lilly_ionetix_13g")
    assert f.cusips == ("000000000",)
    r = S.select_member_position(f, LILLY)
    assert r.ok and r.cusip is None
    # Item 4 says 7.98% beside the cover's 7.9: the cover line is the one that counts.
    assert "7.98%" in f.item4_text and r.percent == Decimal("7.9")


def test_trex_skips_the_empty_second_cusip_element():
    raw = _fixture("lilly_trex_13d")
    assert "<issuerCusipNumber/>" in raw
    assert _parse("lilly_trex_13d").cusips == ("89531N100",)


def test_coreweave_and_aktis_carry_a_bare_cusip_element():
    assert "<issuerCusip>21873S108</issuerCusip>" in _fixture("nvidia_coreweave_13ga")
    assert "<issuerCUSIP>00973N102</issuerCUSIP>" in _fixture("lilly_aktis_13d")
    assert _parse("nvidia_coreweave_13ga").cusips == ("21873S108",)
    assert _parse("lilly_aktis_13d").cusips == ("00973N102",)


@pytest.mark.parametrize("stem", ["lilly_aktis_13d", "lilly_scribe_13d"])
def test_the_underwriters_option_exemption_is_load_bearing(stem, monkeypatch):
    """Aktis and Scribe compute their percent "after giving effect to the full exercise of the
    underwriters' option". Without the positional exemption both would read as derivative
    positions — so the exemption, not luck, keeps them plain."""
    f = _parse(stem)
    assert "full exercise of the underwriters' option" in f.persons[0].comments
    assert S.select_member_position(f, LILLY).ok
    monkeypatch.setattr(S, "_EXEMPT_RES", ())
    r = S.select_member_position(f, LILLY)
    assert r.reason == "derivative_position"
    assert r.derivative_note is None and "underwriters" in r.derivative_quote


@pytest.mark.parametrize("stem", ["lilly_scribe_13d", "lilly_aktis_13d", "lilly_trex_13d"])
def test_a_13d_scans_item_5_never_the_item_3_history(stem):
    """Scribe's Item 3 tells how a Convertible Promissory Note became common stock; Aktis's and
    TRex's how convertible preferred did. That is history: the position text is Item 5(a)-(b)."""
    raw = _fixture(stem)
    assert re.search(r"<fundsSource>[^<]*[Cc]onver", raw)
    f = _parse(stem)
    assert "onvert" not in f.item4_text
    assert f.item4_text.startswith("The Reporting Person directly holds an aggregate of")
    assert "incorporated by reference" in f.item4_text          # Item 5(b) is read too
    assert "past 60 days" not in f.item4_text                   # 5(c), the trades, is not
    assert S.select_member_position(f, LILLY).ok


def _to_default_namespace(raw: str) -> str:
    out = re.sub(r"<(/?)sch:", r"<\1", raw)
    return out.replace('xmlns:sch="', 'xmlns="')


def _to_prefixed(raw: str, prefix: str = "sch") -> str:
    head, sep, rest = raw.partition("?>")
    rest = re.sub(r"<(/?)(?!sch:|com:)([A-Za-z])", rf"<\1{prefix}:\2", rest)
    rest = rest.replace(
        'xmlns="http://www.sec.gov/edgar/schedule13g"',
        f'xmlns:{prefix}="http://www.sec.gov/edgar/schedule13g"', 1,
    )
    # The address elements declare their own default namespace; re-point them too.
    rest = rest.replace('xmlns="http://www.sec.gov/edgar/common"', 'xmlns:com="http://www.sec.gov/edgar/common"')
    rest = re.sub(rf"<(/?){prefix}:(street1|street2|city|stateOrCountry|zipCode)\b", r"<\1com:\2", rest)
    return head + sep + rest


def test_prefixed_and_default_namespace_forms_parse_identically():
    nebius = _fixture("nvidia_nebius_13g")
    assert "<sch:reportingPersonName>" in nebius
    twin = _to_default_namespace(nebius)
    assert "<sch:" not in twin and "<reportingPersonName>" in twin
    assert S.parse_schedule_xml(twin) == S.parse_schedule_xml(nebius)

    spacex = _fixture("google_spacex_13g")
    prefixed = _to_prefixed(spacex)
    assert "<sch:reportingPersonName>" in prefixed and "<reportingPersonName>" not in prefixed
    assert S.parse_schedule_xml(prefixed) == S.parse_schedule_xml(spacex)


def test_a_wrong_prefix_or_undeclared_prefix_is_refused():
    nebius = _fixture("nvidia_nebius_13g")
    broken = nebius.replace('xmlns:sch="http://www.sec.gov/edgar/schedule13g"', "", 1)
    with pytest.raises(S.ScheduleParseError) as e:
        S.parse_schedule_xml(broken)
    assert e.value.code == "schedule_unparseable" and "unbound prefix" in str(e.value)


# ══════════════════════════════════════════════════════════════════════════════════════
# 2. Synthetic filings
# ══════════════════════════════════════════════════════════════════════════════════════

NS_13G = "http://www.sec.gov/edgar/schedule13g"
NS_13D = "http://www.sec.gov/edgar/schedule13D"


#: Rows 5-8 of a cover line, in order. ``powers=`` overrides them one by one; None leaves
#: that element out.
_POWER_TAGS = ("soleVotingPower", "sharedVotingPower", "soleDispositivePower", "sharedDispositivePower")


def _powers_xml(powers: Sequence[Optional[str]]) -> str:
    assert len(powers) == 4
    return "".join(f"<{tag}>{escape(v)}</{tag}>" for tag, v in zip(_POWER_TAGS, powers) if v is not None)


def _line_13g(name: str, *, types: Sequence[str] = ("CO",), shares: str = "10000000",
              percent: str = "9.3", comments: Optional[str] = None, group: Optional[str] = None,
              sole: str = "10000000", shared: str = "0",
              powers: Optional[Sequence[Optional[str]]] = None) -> str:
    parts = [f"<reportingPersonName>{escape(name)}</reportingPersonName>"]
    if group is not None:
        parts.append(f"<memberGroup>{escape(group)}</memberGroup>")
    parts.append("<citizenshipOrOrganization>DE</citizenshipOrOrganization>")
    parts.append(
        "<reportingPersonBeneficiallyOwnedNumberOfShares>"
        + _powers_xml(powers if powers is not None else (sole, shared, sole, shared))
        + "</reportingPersonBeneficiallyOwnedNumberOfShares>"
    )
    parts.append(f"<reportingPersonBeneficiallyOwnedAggregateNumberOfShares>{escape(shares)}"
                 "</reportingPersonBeneficiallyOwnedAggregateNumberOfShares>")
    parts.append(f"<classPercent>{escape(percent)}</classPercent>")
    parts += [f"<typeOfReportingPerson>{escape(t)}</typeOfReportingPerson>" for t in types]
    if comments is not None:
        parts.append(f"<comments>{escape(comments)}</comments>")
    return "<coverPageHeaderReportingPersonDetails>" + "".join(parts) + "</coverPageHeaderReportingPersonDetails>"


def _g13(*lines: str, submission: str = "SCHEDULE 13G", issuer_cik: Optional[str] = "0001513845",
         issuer_name: str = "Nebius Group N.V.", cusips: Sequence[str] = ("N97284108",),
         bare_cusip: Optional[str] = None, class_title: str = "Class A Ordinary Shares",
         event: Optional[str] = "07/13/2026", item4: str = "See Row 9 of cover page for the Reporting Person.",
         five: Optional[str] = None, filer: str = "0001045810", namespace: str = NS_13G,
         declaration: str = '<?xml version="1.0" encoding="UTF-8"?>') -> str:
    if not lines:
        lines = (_line_13g("NVIDIA Corporation"),)
    cusip_xml = ""
    if cusips:
        cusip_xml = "<issuerCusips>" + "".join(
            f"<issuerCusipNumber>{c}</issuerCusipNumber>" for c in cusips) + "</issuerCusips>"
    if bare_cusip is not None:
        cusip_xml += f"<issuerCusip>{bare_cusip}</issuerCusip>"
    issuer_cik_xml = f"<issuerCik>{issuer_cik}</issuerCik>" if issuer_cik is not None else ""
    event_xml = (f"<eventDateRequiresFilingThisStatement>{event}</eventDateRequiresFilingThisStatement>"
                 if event is not None else "")
    five_xml = f"<classOwnership5PercentOrLess>{five}</classOwnership5PercentOrLess>" if five is not None else ""
    return (
        f"{declaration}<edgarSubmission xmlns=\"{namespace}\">"
        "<schemaVersion>X0202</schemaVersion>"
        f"<headerData><submissionType>{submission}</submissionType><filerInfo><filer><filerCredentials>"
        f"<cik>{filer}</cik><ccc>XXXXXXXX</ccc></filerCredentials></filer><liveTestFlag>LIVE</liveTestFlag>"
        "</filerInfo></headerData>"
        "<formData><coverPageHeader>"
        f"<securitiesClassTitle>{escape(class_title)}</securitiesClassTitle>{event_xml}"
        f"<issuerInfo>{issuer_cik_xml}<issuerName>{escape(issuer_name)}</issuerName>{cusip_xml}</issuerInfo>"
        "</coverPageHeader>"
        + "".join(lines)
        + f"<items><item4><amountBeneficiallyOwned>{escape(item4)}</amountBeneficiallyOwned></item4>"
        f"<item5><notApplicableFlag>Y</notApplicableFlag>{five_xml}</item5></items>"
        "<signatureInformation><reportingPersonName>Somebody Else Inc.</reportingPersonName>"
        "<signatureDetails><signature>/s/ A</signature><title>Secretary</title><date>07/20/2026</date>"
        "</signatureDetails></signatureInformation>"
        "</formData></edgarSubmission>"
    )


def _line_13d(name: str, *, types: Sequence[str] = ("CO",), shares: str = "1388161.00",
              percent: str = "7.3", comments: Optional[str] = None, group: Optional[str] = None,
              powers: Optional[Sequence[Optional[str]]] = None) -> str:
    parts = [f"<reportingPersonName>{escape(name)}</reportingPersonName>"]
    if group is not None:
        parts.append(f"<memberOfGroup>{escape(group)}</memberOfGroup>")
    parts.append(
        "<fundType>WC</fundType><citizenshipOrOrganization>IN</citizenshipOrOrganization>"
        + _powers_xml(powers if powers is not None else (shares, "0.00", shares, "0.00"))
        + f"<aggregateAmountOwned>{escape(shares)}</aggregateAmountOwned><percentOfClass>{escape(percent)}</percentOfClass>"
    )
    parts += [f"<typeOfReportingPerson>{escape(t)}</typeOfReportingPerson>" for t in types]
    if comments is not None:
        parts.append(f"<commentContent>{escape(comments)}</commentContent>")
    return "<reportingPersonInfo>" + "".join(parts) + "</reportingPersonInfo>"


def _d13(*lines: str, submission: str = "SCHEDULE 13D", item3: str = "", item5a: str = "",
         item5e: Optional[str] = "N/A", class_title: str = "Common Stock, $0.001 par value per share",
         cusip: str = "811033109", event: str = "07/23/2026") -> str:
    if not lines:
        lines = (_line_13d("ELI LILLY & Co"),)
    item5e_xml = f"<date5PercentOwnership>{escape(item5e)}</date5PercentOwnership>" if item5e is not None else ""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><edgarSubmission xmlns="{NS_13D}">'
        f"<headerData><submissionType>{submission}</submissionType><filerInfo><filer><filerCredentials>"
        "<cik>0000059478</cik></filerCredentials></filer></filerInfo></headerData>"
        "<formData><coverPageHeader>"
        f"<securitiesClassTitle>{escape(class_title)}</securitiesClassTitle><dateOfEvent>{event}</dateOfEvent>"
        f"<issuerInfo><issuerCIK>0001853921</issuerCIK><issuerCUSIP>{cusip}</issuerCUSIP>"
        "<issuerName>Scribe Therapeutics, Inc.</issuerName></issuerInfo></coverPageHeader>"
        "<reportingPersons>" + "".join(lines) + "</reportingPersons>"
        f"<items1To7><item3><fundsSource>{escape(item3)}</fundsSource></item3>"
        f"<item5><percentageOfClassSecurities>{escape(item5a)}</percentageOfClassSecurities>"
        "<numberOfShares>See Item 5(a).</numberOfShares>"
        "<transactionDesc>Bought 100 shares upon exercise of warrants on 07/01/2026.</transactionDesc>"
        f"{item5e_xml}</item5></items1To7>"
        "</formData></edgarSubmission>"
    )


def _select(xml: str, member: MemberConfig = NVIDIA) -> S.PositionResult:
    return S.select_member_position(S.parse_schedule_xml(xml), member)


def test_the_synthetic_baselines_are_clean_positions():
    r = _select(_g13())
    assert (r.ok, r.reason, r.shares, r.percent, r.basis, r.cusip) == (
        True, None, 10_000_000, Decimal("9.3"), "of Class A", "N97284108")
    r = _select(_d13(), LILLY)
    assert (r.ok, r.shares, r.percent, r.basis, r.cusip) == (True, 1_388_161, Decimal("7.3"), "of common stock", "811033109")


# ── 2a. refused before the parser ever runs ──────────────────────────────────────────────

def _refused(text, code="schedule_unparseable") -> S.ScheduleParseError:
    with pytest.raises(S.ScheduleParseError) as e:
        S.parse_schedule_xml(text)
    assert e.value.code == code, str(e.value)
    return e.value


@pytest.mark.parametrize("value", [None, b"<edgarSubmission/>", 42, ["<a/>"]])
def test_a_non_string_is_refused(value):
    assert "expected the decoded document as str" in str(_refused(value))


@pytest.mark.parametrize("value", ["", "   \n\t", "\ufeff", "\ufeff  \r\n"])
def test_an_empty_document_is_refused(value):
    assert "empty" in str(_refused(value))


def test_a_document_over_the_cap_is_refused_before_parsing():
    big = _g13(item4="x" * S.MAX_SCHEDULE_XML_CHARS)
    t0 = time.thread_time()
    err = _refused(big)
    assert "cap" in str(err) and time.thread_time() - t0 < 0.5
    # At the cap itself it still parses.
    filler = S.MAX_SCHEDULE_XML_CHARS - len(_g13(item4=""))
    assert S.parse_schedule_xml(_g13(item4="x" * filler)).item4_text == "x" * filler


DTD_PAYLOADS = [
    '<?xml version="1.0"?><!DOCTYPE edgarSubmission><edgarSubmission/>',
    '<?xml version="1.0"?><!doctype edgarSubmission><edgarSubmission/>',
    '<!DOCTYPE x [<!ELEMENT x ANY>]><x/>',
    '<?xml version="1.0"?><!DocType edgarSubmission SYSTEM "file:///etc/passwd"><edgarSubmission/>',
    '<?xml version="1.0"?><!ENTITY xxe SYSTEM "file:///etc/passwd"><edgarSubmission>&xxe;</edgarSubmission>',
    '<?xml version="1.0"?><!entity x "y"><edgarSubmission/>',
    '<?xml version="1.0"?><! ENTITY x "y"><edgarSubmission/>',
    '<?xml version="1.0"?><!ATTLIST edgarSubmission a CDATA "b"><edgarSubmission/>',
]


@pytest.mark.parametrize("payload", DTD_PAYLOADS)
def test_any_markup_declaration_is_refused_before_parsing(payload):
    _refused(payload)


def test_an_entity_declaration_is_refused_before_anything_is_classified():
    """The security refusal comes first: an HTML page that declares an entity is refused as
    unreadable, never classified as an old-format schedule."""
    err = _refused('<!DOCTYPE html [<!ENTITY x "y">]><html><body>SCHEDULE 13G &x;</body></html>')
    assert "entity declaration" in str(err)
    assert "entity declaration" in str(_refused('<html><!ENTITY x "y"><body/></html>'))


def test_a_dtd_inside_a_real_filing_is_refused():
    raw = _fixture("google_spacex_13g")
    head, sep, rest = raw.partition("?>")
    _refused(head + sep + "<!DOCTYPE edgarSubmission>" + rest)
    # ...and a comment or a CDATA section is fine (Workiva writes a comment into every file).
    ok = head + sep + "<!-- generated -->" + rest.replace("<schemaVersion>", "<![CDATA[]]><schemaVersion>", 1)
    assert S.parse_schedule_xml(ok) == S.parse_schedule_xml(raw)


def test_billion_laughs_is_refused_without_expanding_anything():
    lol = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        + "".join(f'<!ENTITY lol{i} "{"&lol" + str(i - 1) + ";" if i > 1 else "&lol;"}' * 10 + '">' for i in range(1, 10))
        + ']><edgarSubmission>&lol9;</edgarSubmission>'
    )
    t0 = time.thread_time()
    _refused(lol)
    assert time.thread_time() - t0 < 0.1


def _utf16_doc(extra_decl: str = "") -> str:
    body = _g13(declaration='<?xml version="1.0" encoding="UTF-16"?>' + extra_decl)
    return body


def test_a_utf16_document_decoded_properly_parses():
    raw = _utf16_doc().encode("utf-16")
    assert raw[:2] in (b"\xff\xfe", b"\xfe\xff")
    filing = S.parse_schedule_xml(raw.decode("utf-16"))
    assert filing.persons[0].name == "NVIDIA Corporation"


def test_a_utf16_document_with_a_dtd_is_refused_however_it_was_decoded():
    raw = _utf16_doc("<!DOCTYPE edgarSubmission>").encode("utf-16")
    _refused(raw.decode("utf-16"))                         # decoded properly
    for single_byte in ("latin-1", "cp1252"):
        err = _refused(raw.decode(single_byte, errors="replace"))   # decoded as bytes: NULs
        assert "markup declaration" in str(err)
    be = _utf16_doc("<!ENTITY x 'y'>").encode("utf-16-be")
    assert "markup declaration" in str(_refused(be.decode("latin-1")))


def test_a_utf16_document_decoded_as_bytes_is_refused_even_without_a_dtd():
    raw = _utf16_doc().encode("utf-16-le")
    assert "NUL" in str(_refused(raw.decode("latin-1")))
    _refused(_g13().replace("NVIDIA", "NVI\x00DIA"))


@pytest.mark.parametrize("text", [
    "<!DOCTYPE html><html><body><p>SCHEDULE 13G</p></body></html>",
    "<html><head><title>SC 13G</title></head><body>\u2026</body></html>",
    "<HTML><BODY>SCHEDULE 13D</BODY></HTML>",
    '<?xml version="1.0"?><!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0//EN" "x"><html/>',
    "<div><table><tr><td>Schedule 13G</td></tr></table></div>",
    "<SEC-DOCUMENT>0000059478-21-000001.txt : 20210210\n<TYPE>SC 13G\n</SEC-DOCUMENT>",
    "<DOCUMENT>\n<TYPE>SC 13G/A\n<TEXT>...</TEXT></DOCUMENT>",
    "UNITED STATES SECURITIES AND EXCHANGE COMMISSION\nSCHEDULE 13G\n(Rule 13d-102)",
    "SC 13D/A\nAmendment No. 3",
])
def test_an_old_format_schedule_is_legacy(text):
    _refused(text, "legacy_schedule")


def test_a_namespace_prefix_named_like_an_html_tag_is_not_html():
    """Without an XML declaration the head is sniffed for HTML tags; ``<p:…>`` / ``<type:…>``
    are namespace prefixes, not tags."""
    xml = _g13(declaration="").replace(f'<edgarSubmission xmlns="{NS_13G}">',
                                       f'<edgarSubmission xmlns="{NS_13G}" xmlns:p="urn:p" xmlns:type="urn:t">', 1)
    xml = xml.replace("<schemaVersion>X0202</schemaVersion>",
                      "<p:note>x</p:note><type:tag>y</type:tag><schemaVersion>X0202</schemaVersion>", 1)
    assert S.parse_schedule_xml(xml).persons[0].name == "NVIDIA Corporation"
    _refused(xml.replace("<p:note>x</p:note>", "<p>x</p>", 1), "legacy_schedule")


def test_an_xhtml_root_and_an_sc_submission_type_are_legacy():
    _refused('<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body/></html>', "legacy_schedule")
    _refused(_g13(submission="SC 13G"), "legacy_schedule")


@pytest.mark.parametrize("text, needle", [
    ("just some words", "not XML"),
    ("<edgarSubmission><headerData>", "not well-formed"),
    ("<edgarSubmission><a></b></edgarSubmission>", "not well-formed"),
    ("<a>\ud800</a>", "unreadable text"),
    ('<?xml version="1.0"?><informationTable/>', "not edgarSubmission"),
    (f'<edgarSubmission xmlns="{NS_13G}"><headerData/></edgarSubmission>', "headerData / formData"),
])
def test_text_that_is_not_a_schedule_is_unparseable(text, needle):
    assert needle in str(_refused(text))


@pytest.mark.parametrize("submission", ["", "SCHEDULE 13F", "13F-HR", "SCHEDULE 13G/A/A", "FORM 4"])
def test_an_unknown_submission_type_is_unparseable(submission):
    assert "is not a 13D/13G" in str(_refused(_g13(submission=submission)))


def test_the_submission_type_is_read_case_and_space_insensitively():
    assert S.parse_schedule_xml(_g13(submission=" schedule  13g/a ")).submission_type == "SCHEDULE 13G/A"


def test_a_namespace_that_contradicts_the_type_is_unparseable():
    assert "namespace" in str(_refused(_g13(namespace=NS_13D)))
    # No namespace at all (or an unknown one) is fine: elements are matched by local name.
    assert S.parse_schedule_xml(_g13(namespace="urn:somewhere-else")).schema == "13G"
    assert S.parse_schedule_xml(_g13().replace(f' xmlns="{NS_13G}"', "")).schema == "13G"


def test_a_schedule_with_no_reporting_person_is_unparseable():
    xml = re.sub(r"<coverPageHeaderReportingPersonDetails>.*?</coverPageHeaderReportingPersonDetails>", "", _g13(),
                 flags=re.S)
    assert "no reporting person" in str(_refused(xml))
    xml = re.sub(r"<reportingPersons>.*?</reportingPersons>", "", _d13(), flags=re.S)
    assert "no reporting person" in str(_refused(xml))


def test_a_13d_laid_out_as_a_13g_finds_no_reporting_person():
    """Paths are fixed per schema: a 13G's person blocks under a 13D type are not read."""
    xml = _g13(submission="SCHEDULE 13D", namespace="urn:x")
    assert "no reporting person" in str(_refused(xml))


@pytest.mark.parametrize("cik", ["abc", "0", "0000000000", "12345678901", "-1", "1e5"])
def test_a_malformed_issuer_cik_is_unparseable(cik):
    assert "issuer CIK" in str(_refused(_g13(issuer_cik=cik)))


def test_a_malformed_filer_cik_is_unparseable():
    assert "filer CIK" in str(_refused(_g13(filer="CIK1045810")))


@pytest.mark.parametrize("event, needle", [
    ("2026-07-13", "MM/DD/YYYY"), ("13/07/2026x", "MM/DD/YYYY"), ("July 13, 2026", "MM/DD/YYYY"),
    ("02/30/2026", "calendar date"), ("13/13/2026", "calendar date"), ("07/13/1800", "out of range"),
])
def test_a_malformed_event_date_is_unparseable(event, needle):
    assert needle in str(_refused(_g13(event=event)))


def test_dates_are_month_first_and_padding_is_optional():
    assert S.parse_schedule_xml(_g13(event="7/4/2026")).event_date == date(2026, 7, 4)
    assert S.parse_schedule_xml(_g13(event="12/01/2026")).event_date == date(2026, 12, 1)
    assert S.parse_schedule_xml(_g13(event=None)).event_date is None


def test_deep_nesting_and_element_floods_stop_the_pull_parse_early():
    deep = _g13(item4="").replace(
        "<amountBeneficiallyOwned></amountBeneficiallyOwned>",
        "<amountBeneficiallyOwned>" + "<x>" * 500 + "</x>" * 500 + "</amountBeneficiallyOwned>",
    )
    assert "nested deeper" in str(_refused(deep))
    flood = _g13(item4="").replace(
        "<amountBeneficiallyOwned></amountBeneficiallyOwned>",
        "<amountBeneficiallyOwned>" + "<x/>" * (S.MAX_XML_ELEMENTS + 1) + "</amountBeneficiallyOwned>",
    )
    t0 = time.thread_time()
    assert "elements" in str(_refused(flood))
    assert time.thread_time() - t0 < 1.0
    huge_depth = "<edgarSubmission>" + "<a>" * 600_000
    t0 = time.thread_time()
    _refused(huge_depth)
    assert time.thread_time() - t0 < 0.5


def test_a_bom_and_leading_whitespace_are_tolerated():
    assert S.parse_schedule_xml("\ufeff\n  " + _g13()).persons[0].name == "NVIDIA Corporation"


def test_the_parse_error_carries_its_code_and_survives_pickling():
    err = _refused("<x>")
    assert isinstance(err, ValueError)
    assert str(err).startswith("schedule_unparseable: ")
    clone = pickle.loads(pickle.dumps(err))
    assert (clone.code, clone.message, str(clone)) == (err.code, err.message, str(err))
    with pytest.raises(ValueError):
        S.ScheduleParseError("member_line_missing", "not a parse code")


# ── 2b. values ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw, want", [
    ("22256412.00", Decimal(22256412)), ("22256412", Decimal(22256412)), ("0", Decimal(0)),
    ("0.00", Decimal(0)), ("1,388,161", Decimal(1388161)), ("1,388,161.00", Decimal(1388161)),
    ("  551189500 ", Decimal(551189500)),
    ("100.5", None), ("-3", None), ("N/A", None), ("", None), ("1e6", None), ("1,38,161", None),
    ("12,345,678,901,234,567", None), ("1388161.0000001", None), ("$1,000", None), ("1 000", None),
])
def test_share_counts_parse_strictly_and_must_be_integral(raw, want):
    f = S.parse_schedule_xml(_g13(_line_13g("NVIDIA Corporation", shares=raw)))
    assert f.persons[0].shares == want
    if want is not None:
        assert f.persons[0].shares.as_tuple().exponent == 0     # stored integral, no ".00"


@pytest.mark.parametrize("raw, want", [
    ("9.3", Decimal("9.3")), ("9.3%", Decimal("9.3")), ("9.3 %", Decimal("9.3")), ("7.20", Decimal("7.2")),
    ("0", Decimal(0)), ("100", Decimal(100)), ("15.6000", Decimal("15.6")),
    ("Less than 5%", None), ("N/A", None), ("101", None), ("7.98% of the class", None), ("-1", None),
    (".5", None), ("", None), ("9,3", None), ("5.1234567", None),
])
def test_percents_parse_strictly(raw, want):
    assert S.parse_schedule_xml(_g13(_line_13g("NVIDIA Corporation", percent=raw))).persons[0].percent == want


@pytest.mark.parametrize("raw, want", [("Y", True), ("y", True), ("N", False), ("n", False),
                                       ("YES", True), ("maybe", None), ("", None)])
def test_the_13g_five_percent_box(raw, want):
    assert S.parse_schedule_xml(_g13(five=raw)).five_percent_or_less is want
    assert S.parse_schedule_xml(_g13()).five_percent_or_less is None


@pytest.mark.parametrize("raw, want", [
    ("N/A", False), ("n/a", False), ("NA", False), ("Not applicable.", False), ("None", False),
    ("10/15/2026", True), ("October 15, 2026", True), ("Oct. 15, 2026", True), ("2026-10-15", True),
    ("See Item 5(a).", None), ("", None),
])
def test_the_13d_item_5e_date(raw, want):
    assert S.parse_schedule_xml(_d13(item5e=raw)).five_percent_or_less is want
    assert S.parse_schedule_xml(_d13(item5e=None)).five_percent_or_less is None


def test_type_codes_are_split_upper_cased_and_deduplicated():
    f = S.parse_schedule_xml(_g13(_line_13g("NVIDIA Corporation", types=("co", "HC, BK", "CO"))))
    assert f.persons[0].type == "CO,HC,BK"
    assert S.parse_schedule_xml(_g13(_line_13g("NVIDIA Corporation", types=()))).persons[0].type == ""


def test_13d_lines_read_comment_content_and_group_box():
    f = S.parse_schedule_xml(_d13(_line_13d("ELI LILLY & Co", comments="A note.", group="b")))
    (p,) = f.persons
    assert (p.comments, p.member_of_group, p.shares, p.percent) == ("A note.", "b", Decimal(1388161), Decimal("7.3"))
    assert p.sole_voting == p.sole_dispositive == Decimal(1388161) and p.shared_voting == Decimal(0)


# ── 2c. selection: role ──────────────────────────────────────────────────────────────────

def test_about_member_by_cik_by_affiliate_cik_and_by_name():
    assert _select(_g13(issuer_cik="0001045810")).reason == "about_member"
    # An affiliate's CIK counts (whatever the issuer calls itself) …
    xml = _g13(_line_13g("Alphabet Inc."), issuer_cik="0001824723", issuer_name="Someone Else Inc.")
    assert _select(xml, ALPHABET).reason == "about_member"
    # … and so does the member's own name under another CIK.
    xml = _g13(issuer_cik="0000000123", issuer_name="NVIDIA CORP")
    assert _select(xml).reason == "about_member"
    xml = _g13(_line_13g("Alphabet Inc."), issuer_cik="0000000123", issuer_name="Google LLC")
    assert _select(xml, ALPHABET).reason == "about_member"


def test_a_missing_issuer_cik_is_unparseable_unless_the_name_is_the_members():
    assert _select(_g13(issuer_cik=None)).reason == "schedule_unparseable"
    assert _select(_g13(issuer_cik=None, issuer_name="NVIDIA Corporation")).reason == "about_member"


def test_member_line_missing_when_only_strangers_report():
    r = _select(_g13(_line_13g("Vanguard Group Inc", types=("IA",))))
    assert r.reason == "member_line_missing" and r.person is None


def test_names_match_folded_never_as_typed():
    for name in ("NVIDIA CORP", "nvidia corporation", "NVIDIA Corp.", "N.V.I.D.I.A. Corporation"):
        r = _select(_g13(_line_13g(name)))
        assert r.ok, (name, r.reason)
    assert _select(_g13(_line_13g("NVIDIA Corporation Pension Plan"))).reason == "member_line_missing"


def test_member_lines_that_disagree_are_never_summed_or_maxed():
    two = _g13(_line_13g("NVIDIA Corporation", shares="100", percent="9.3"),
               _line_13g("NVIDIA CORP", shares="200", percent="9.3"))
    assert _select(two).reason == "member_lines_disagree"
    two = _g13(_line_13g("NVIDIA Corporation", percent="9.3"), _line_13g("NVIDIA CORP", percent="9.4"))
    assert _select(two).reason == "member_lines_disagree"
    # The member's own line against its affiliate's, same rule.
    pair = _g13(_line_13g("Google LLC", types=("OO",), shares="5"), _line_13g("Alphabet Inc.", shares="6"))
    assert _select(pair, ALPHABET).reason == "member_lines_disagree"
    # Lines that agree (written differently) are one position.
    same = _g13(_line_13g("NVIDIA Corporation", shares="100.00", percent="9.30"),
                _line_13g("NVIDIA CORP", shares="100", percent="9.3"))
    r = _select(same)
    assert r.ok and r.shares == 100 and r.person.name == "NVIDIA Corporation"


def test_an_unreadable_number_on_the_members_line_is_unparseable():
    r = _select(_g13(_line_13g("NVIDIA Corporation", shares="N/A")))
    assert r.reason == "schedule_unparseable" and r.person.name == "NVIDIA Corporation"
    assert _select(_g13(_line_13g("NVIDIA Corporation", percent="Less than 5%"))).reason == "schedule_unparseable"
    # ...but the same unreadable number on a STRANGER's line does not matter.
    r = _select(_g13(_line_13g("NVIDIA Corporation"), _line_13g("Some Fund LP", shares="N/A", types=("PN",))))
    assert r.ok


def test_an_exit_needs_no_numbers_when_the_five_percent_box_is_checked():
    r = _select(_g13(_line_13g("NVIDIA Corporation", shares="N/A", percent="Less than 5%"), five="Y"))
    assert r.reason == "exit_filing" and r.shares is None and r.percent is None


def test_the_affiliate_line_is_used_when_the_member_has_none():
    r = _select(_g13(_line_13g("Google LLC", types=("OO",))), ALPHABET)
    assert r.ok and r.person.name == "Google LLC" and r.person.type == "OO"


# ── 2d. selection: reporting type and group ──────────────────────────────────────────────

JPMORGAN = MemberConfig(
    slug="jpmorgan", source_name="JPMorgan", names=("JPMorgan Chase & Co.",),
    s1_filers=(FilerEntry(cik="0000019617", role="member", names=("JPMorgan Chase & Co.",), types=("CO",)),),
    s2_aliases=(),
)


def test_a_jpmorgan_shaped_client_asset_13g_is_never_an_own_account_stake():
    """A bank's 13G reports CLIENT holdings: parent type HC, the bank BK. Even if such a
    member were listed (it is not), the type keeps it out of the auto path."""
    xml = _g13(
        _line_13g("JPMorgan Chase & Co.", types=("HC",), shares="7000000", percent="6.1",
                  sole="6000000", shared="1000000"),
        _line_13g("JPMorgan Chase Bank, National Association", types=("BK",), shares="6500000", percent="5.7"),
        issuer_name="Some Issuer Inc.", issuer_cik="0000111111", filer="0000019617",
    )
    r = _select(xml, JPMORGAN)
    assert r.reason == "reporting_type" and r.person.type == "HC"
    assert r.shares == 7_000_000 and r.percent == Decimal("6.1")      # numbers kept for the proposal


@pytest.mark.parametrize("types", [("IA",), ("BK",), ("IC",), ("IV",), ("FI",), ("OO",), ("CO", "HC"), ()])
def test_the_members_own_line_must_be_exactly_co(types):
    assert _select(_g13(_line_13g("NVIDIA Corporation", types=types))).reason == "reporting_type"


def test_oo_is_accepted_only_for_an_affiliate_that_lists_it():
    assert _select(_g13(_line_13g("Google LLC", types=("OO",))), ALPHABET).ok
    assert _select(_g13(_line_13g("Google LLC", types=("CO",))), ALPHABET).ok
    co_only = MemberConfig(
        slug="alphabet", source_name="Alphabet", names=("Alphabet Inc.",),
        s1_filers=(FilerEntry("0001652044", "member", ("Alphabet Inc.",), ("CO",)),
                   FilerEntry("0001824723", "affiliate", ("Google LLC",), ("CO",))),
        s2_aliases=(),
    )
    assert _select(_g13(_line_13g("Google LLC", types=("OO",))), co_only).reason == "reporting_type"
    # A hand-built entry can never widen what a line may be: HC stays refused.
    odd = MemberConfig(
        slug="alphabet", source_name="Alphabet", names=("Alphabet Inc.",),
        s1_filers=(FilerEntry("0001652044", "member", ("Alphabet Inc.",), ("CO",)),
                   FilerEntry("0001824723", "affiliate", ("Google LLC",), ("HC", "OO"))),
        s2_aliases=(),
    )
    assert _select(_g13(_line_13g("Google LLC", types=("HC",))), odd).reason == "reporting_type"


def test_a_checked_group_box_with_an_outside_holder_is_a_group_filing():
    xml = _g13(_line_13g("NVIDIA Corporation", group="a"), _line_13g("Fund X LP", types=("PN",), group="a",
                                                                     shares="5", percent="0.1"))
    r = _select(xml)
    assert r.reason == "group_member" and r.percent == Decimal("9.3")
    # The box on the OUTSIDE holder's line alone is enough.
    xml = _g13(_line_13g("NVIDIA Corporation"), _line_13g("Fund X LP", types=("PN",), group="(a)"))
    assert _select(xml).reason == "group_member"


@pytest.mark.parametrize("group", ["b", "B", "(b)", "", "N", "false"])
def test_a_disclaimed_or_empty_group_box_is_not_a_group(group):
    xml = _g13(_line_13g("NVIDIA Corporation", group=group), _line_13g("Fund X LP", types=("PN",), group=group))
    assert _select(xml).ok


def test_an_unknown_group_box_value_fails_closed():
    xml = _g13(_line_13g("NVIDIA Corporation", group="X"), _line_13g("Fund X LP", types=("PN",)))
    assert _select(xml).reason == "group_member"


@pytest.mark.parametrize("tag", ["memberGroup", "memberOfGroup", "memberOfAGroup", "reportingPersonMemberGroup"])
def test_the_group_box_is_found_whatever_its_element_is_called(tag):
    line = _line_13g("NVIDIA Corporation").replace(
        "<citizenshipOrOrganization>", f"<{tag}>a</{tag}><citizenshipOrOrganization>", 1)
    f = S.parse_schedule_xml(_g13(line, _line_13g("Fund X LP", types=("PN",))))
    assert f.persons[0].member_of_group == "a" and f.persons[1].member_of_group is None
    assert S.select_member_position(f, NVIDIA).reason == "group_member"
    # 13D lines too.
    f = S.parse_schedule_xml(_d13(_line_13d("ELI LILLY & Co").replace(
        "<fundType>", f"<{tag}>a</{tag}><fundType>", 1), _line_13d("Fund X LP", types=("PN",))))
    assert S.select_member_position(f, LILLY).reason == "group_member"


def test_a_group_of_only_the_members_own_chain_is_not_outside():
    xml = _g13(_line_13g("Google LLC", types=("OO",), group="a"), _line_13g("Alphabet Inc.", group="a"))
    assert _select(xml, ALPHABET).ok


# ── 2d'. the member's chain, outside holders and power (review S1-1) ─────────────────────

#: Rows 5-8 (sole voting, shared voting, sole dispositive, shared dispositive).
SHARED_12M = ("0", "12000000", "0", "12000000")
SOLE_10M = ("10000000", "0", "10000000", "0")
SHARED_10M = ("0", "10000000", "0", "10000000")
VOTES_ONLY_10M = ("0", "10000000", "0", "0")
OTHER_ISSUER = dict(issuer_name="Acme Robotics Inc.", issuer_cik="0000999999", cusips=(),
                    class_title="Common Stock, par value $0.001 per share", filer="0001652044")


def _chain_line(name: str, types: Sequence[str] = ("CO",), *, group: Optional[str] = None,
                powers: Sequence[Optional[str]] = SHARED_12M, shares: str = "12000000",
                percent: str = "8.4") -> str:
    return _line_13g(name, types=types, shares=shares, percent=percent, group=group, powers=powers)


def test_the_reviewed_file_lists_alphabets_intermediate():
    """The recorded SpaceX / TeraWulf outcomes depend on it: XXVI Holdings Inc. sits between
    Google LLC and Alphabet Inc. and co-reports the block."""
    assert ALPHABET.intermediates == ("XXVI Holdings Inc.",)


def test_a_gv_style_chain_with_outside_fund_entities_is_shared_power():
    """Review S1-1 (a): a GV fund's 13G names Alphabet's chain AND the fund, its GP and
    another holding company — all sharing one block, group box (b). The fund's block is not
    Alphabet's own-account stake (GV / CapitalG are deferred by the plan)."""
    xml = _g13(_chain_line("GV 2019, L.P.", ("PN",), group="b"), _chain_line("GV 2019 GP, L.L.C.", ("OO",), group="b"),
               _chain_line("Alphabet Holdings LLC", ("OO",), group="b"), _chain_line("XXVI Holdings Inc.", group="b"),
               _chain_line("Alphabet Inc.", group="b"), **OTHER_ISSUER)
    r = _select(xml, ALPHABET)
    assert (r.ok, r.reason, r.person.name, r.shares, r.percent, r.basis) == (
        False, "shared_power", "Alphabet Inc.", 12_000_000, Decimal("8.4"), "of common stock")
    # Honest twin: the same shared block reported by the member's own chain alone is its stake.
    twin = _g13(_chain_line("Google LLC", ("OO",)), _chain_line("XXVI Holdings Inc."), _chain_line("Alphabet Inc."),
                **OTHER_ISSUER)
    assert _select(twin, ALPHABET).ok


SUPPORT_COMMENT = ("The Reporting Person may be deemed to beneficially own these shares solely because of the "
                   "Support Agreements described in Item 4. The Reporting Person expressly disclaims beneficial "
                   "ownership of all of these shares.")


@pytest.mark.parametrize("with_merger_sub", [True, False])
def test_an_acquirers_support_agreement_13d_holds_no_shares(with_merger_sub):
    """Review S1-1 (b): an acquirer reports the target's shares its support agreements let it
    VOTE (sole 0 / shared voting / dispositive 0). Lilly owns none of them — never "Lilly
    holds 19.9%". The same with Lilly as the only reporting person."""
    lines = [_line_13d("Eli Lilly and Company", shares="10000000", percent="19.9", comments=SUPPORT_COMMENT,
                       powers=VOTES_ONLY_10M)]
    if with_merger_sub:
        lines.append(_line_13d("Kestrel Merger Sub, Inc.", shares="10000000", percent="19.9", powers=VOTES_ONLY_10M))
    r = _select(_d13(*lines, class_title="Common Stock, $0.0001 par value per share"), LILLY)
    assert (r.ok, r.reason, r.person.name, r.shares, r.percent) == (
        False, "no_dispositive_power", "Eli Lilly and Company", 10_000_000, Decimal("19.9"))
    # Honest twin: the same 13D once Lilly holds the shares (sole dispositive power).
    lines[0] = _line_13d("Eli Lilly and Company", shares="10000000", percent="19.9",
                         powers=("10000000", "0", "10000000", "0"))
    assert _select(_d13(*lines[:1]), LILLY).ok


def test_the_member_and_an_unaffiliated_co_investor_sharing_power_with_no_group_box():
    """Review S1-1 (c): no box is checked, but the member and an outside LP share power over
    one block."""
    xml = _g13(_line_13g("NVIDIA Corporation", powers=SHARED_10M),
               _line_13g("Co-Invest Partners LP", types=("PN",), powers=SHARED_10M))
    r = _select(xml)
    assert (r.reason, r.person.name, r.shares, r.percent) == ("shared_power", "NVIDIA Corporation", 10_000_000,
                                                              Decimal("9.3"))


@pytest.mark.parametrize("member_powers, outside_powers, reason", [
    # an outside line shares power — voting, dispositive or both
    (SOLE_10M, SHARED_10M, "shared_power"),
    (SOLE_10M, ("0", "5", "10000000", "0"), "shared_power"),
    (SOLE_10M, ("10000000", "0", "0", "5"), "shared_power"),
    # an outside line that does not say (missing or unreadable) counts as sharing
    (SOLE_10M, ("10000000", None, "10000000", "0"), "shared_power"),
    (SOLE_10M, ("10000000", "0", "10000000", "N/A"), "shared_power"),
    # the member shares power while an outside holder reports: possibly with that holder
    (SHARED_10M, ("5000000", "0", "5000000", "0"), "shared_power"),
    (("10000000", None, "10000000", "0"), ("5000000", "0", "5000000", "0"), "shared_power"),
    # honest twins: each holder reports its own block with sole power only
    (SOLE_10M, ("5000000", "0", "5000000", "0"), None),
    (SOLE_10M, ("0", "0", "0", "0"), None),
])
def test_shared_power_with_a_holder_outside_the_chain(member_powers, outside_powers, reason):
    xml = _g13(_line_13g("NVIDIA Corporation", powers=member_powers),
               _line_13g("Other Fund LP", types=("PN",), shares="5000000", percent="4.6", powers=outside_powers))
    assert _select(xml).reason == reason


def test_shared_power_inside_the_members_own_chain_is_fine():
    # Review A (S1-LONE-SHARED-POWER): the member ALONE sharing power is no longer "fine". No
    # other line of its chain co-reports, so it shares with someone the filing does not name
    # (an acquirer's support agreements look exactly like this): a proposal, never auto.
    r = _select(_g13(_line_13g("NVIDIA Corporation", powers=SHARED_10M)))
    assert (r.ok, r.reason, r.shares, r.percent) == (False, "shared_power", 10_000_000, Decimal("9.3"))
    # Recorded: all three SpaceX lines share 551,189,500 shares, no line is outside.
    f = _parse("google_spacex_13g")
    assert all(p.sole_voting == 0 and p.shared_voting == Decimal(551_189_500) for p in f.persons)
    assert S.select_member_position(f, ALPHABET).ok
    # TeraWulf: the same three-line chain sharing one block — still the counted warrant proposal.
    r = S.select_member_position(_parse("google_terawulf_13g"), ALPHABET)
    assert (r.reason, r.derivative_note) == ("derivative_position", "incl. 41.0M warrant shares")


def _alphabet_pair(shares: str = "10000000", powers: Sequence[Optional[str]] = SOLE_10M,
                   item4: str = "See Row 9 of cover page for the Reporting Person.") -> str:
    """Google LLC + Alphabet Inc. on one block: a chain of TWO entities, so power shared inside
    it is the member's own (the dispositive rule is judged on its own here)."""
    return _g13(_line_13g("Google LLC", types=("OO",), shares=shares, powers=powers),
                _line_13g("Alphabet Inc.", shares=shares, powers=powers), item4=item4, **OTHER_ISSUER)


#: (powers, the reason on a member line ALONE, the reason inside Alphabet's two-entity chain)
@pytest.mark.parametrize("powers, lone_reason, chain_reason", [
    (VOTES_ONLY_10M, "no_dispositive_power", "no_dispositive_power"),   # votes the block, cannot sell it
    (("0", "0", "0", "0"), "no_dispositive_power", "no_dispositive_power"),
    (("10000000", "0", None, None), "no_dispositive_power", "no_dispositive_power"),  # rows 7 and 8 left out
    (("10000000", "0", "N/A", "n/a"), "no_dispositive_power", "no_dispositive_power"),  # rows 7 and 8 unreadable
    (("10000000", "0", None, "0"), "no_dispositive_power", "no_dispositive_power"),    # one missing, the other zero
    (("10000000", "0", "0", None), "no_dispositive_power", "no_dispositive_power"),
    # honest twins of the dispositive rule: either row alone is a power to sell. ALONE, a
    # shared or unstated row 8 is review A's chain of one sharing power (shared_power).
    (("0", "0", "10000000", None), "shared_power", None),
    (("0", "0", None, "10000000"), "shared_power", None),
    (("0", "0", "10000000", "N/A"), "shared_power", None),
    (SOLE_10M, None, None),
    (SHARED_10M, "shared_power", None),
])
def test_the_chosen_line_must_hold_a_power_to_sell(powers, lone_reason, chain_reason):
    assert _select(_g13(_line_13g("NVIDIA Corporation", powers=powers))).reason == lone_reason
    assert _select(_d13(_line_13d("ELI LILLY & Co", powers=powers)), LILLY).reason == lone_reason
    assert _select(_alphabet_pair(powers=powers), ALPHABET).reason == chain_reason


@pytest.mark.parametrize("shares, powers, lone_reason, chain_reason", [
    # an acquirer's 1,000-share toehold beside the 10M shares its support agreements let it vote
    ("10001000", ("1000", "10000000", "1000", "0"), "no_dispositive_power", "no_dispositive_power"),
    # the readable power falls short of the block (one unreadable number beside it)
    ("10000000", ("0", "0", "5000000", None), "no_dispositive_power", "no_dispositive_power"),
    ("10000000", ("0", "0", "4000000", "5999999"), "no_dispositive_power", "no_dispositive_power"),  # one short
    # honest twins: the power to sell covers the block exactly, or more (alone, a shared or
    # unstated row 8 is a chain of one sharing power)
    ("10000000", ("0", "0", "4000000", "6000000"), "shared_power", None),
    ("10000000", ("0", "0", "10000000", "N/A"), "shared_power", None),
    ("10000000", ("10000000", "0", "12000000", "0"), None, None),
])
def test_dispositive_power_must_cover_the_reported_block(shares, powers, lone_reason, chain_reason):
    assert _select(_g13(_line_13g("NVIDIA Corporation", shares=shares, powers=powers))).reason == lone_reason
    assert _select(_d13(_line_13d("ELI LILLY & Co", shares=shares, powers=powers)), LILLY).reason == lone_reason
    assert _select(_alphabet_pair(shares=shares, powers=powers), ALPHABET).reason == chain_reason


def _person(**fields) -> S.ReportingPerson:
    base = dict(name="X Corp", type="CO", shares=None, percent=None, comments="", member_of_group=None,
                sole_voting=None, shared_voting=None, sole_dispositive=None, shared_dispositive=None)
    return S.ReportingPerson(**{**base, **fields})


def test_the_power_helpers_on_their_own():
    """Pinned without a filing: the zero rule holds even with no readable aggregate."""
    D = Decimal
    assert S._no_dispositive_power(_person())
    assert S._no_dispositive_power(_person(sole_dispositive=D(0), shared_dispositive=D(0)))
    assert S._no_dispositive_power(_person(sole_dispositive=D(0)))
    assert not S._no_dispositive_power(_person(sole_dispositive=D(5)))
    assert not S._no_dispositive_power(_person(shares=D(5), shared_dispositive=D(5)))
    assert S._no_dispositive_power(_person(shares=D(6), sole_dispositive=D(2), shared_dispositive=D(3)))
    assert S._may_share_power(_person())
    assert S._may_share_power(_person(shared_voting=D(0)))
    assert S._may_share_power(_person(shared_voting=D(0), shared_dispositive=D(1)))
    assert S._may_share_power(_person(shared_voting=D(1), shared_dispositive=D(0)))
    assert not S._may_share_power(_person(shared_voting=D(0), shared_dispositive=D(0)))


def test_every_recorded_chosen_line_can_sell_its_whole_block():
    """Why the rule cannot touch a recorded filing: rows 7 + 8 equal row 9 on every one."""
    for stem, (member, *_rest) in RECORDED.items():
        r = S.select_member_position(_parse(stem), member)
        p = r.person
        assert (p.sole_dispositive or 0) + (p.shared_dispositive or 0) == p.shares, stem


def test_only_the_chosen_lines_dispositive_power_is_read():
    """Google LLC (the record holder) may report the power; the chosen line is Alphabet's."""
    xml = _g13(_chain_line("Google LLC", ("OO",), powers=SHARED_12M),
               _chain_line("Alphabet Inc.", powers=("0", "12000000", "0", "0")), **OTHER_ISSUER)
    assert _select(xml, ALPHABET).reason == "no_dispositive_power"


# ── intermediates ────────────────────────────────────────────────────────────────────────

SPACEX_BLOCK = ("0", "551189500", "0", "551189500")


@pytest.mark.parametrize("xxvi_shares, xxvi_percent", [("500000000", "7.2"), ("551189500", "6.5"), ("N/A", "7.2")])
def test_an_intermediate_reporting_a_different_block_disagrees(xxvi_shares, xxvi_percent):
    xxvi_powers = ("0", xxvi_shares, "0", xxvi_shares) if xxvi_shares.isdigit() else SPACEX_BLOCK
    xml = _g13(_line_13g("Google LLC", types=("OO",), shares="551189500", percent="7.2", powers=SPACEX_BLOCK),
               _line_13g("XXVI Holdings Inc.", shares=xxvi_shares, percent=xxvi_percent, powers=xxvi_powers),
               _line_13g("Alphabet Inc.", shares="551189500", percent="7.2", powers=SPACEX_BLOCK), **OTHER_ISSUER)
    assert _select(xml, ALPHABET).reason == "member_lines_disagree"
    # Honest twin: the same three lines on one block are Alphabet's own stake.
    twin = _g13(*(_line_13g(name, types=types, shares="551189500", percent="7.2", powers=SPACEX_BLOCK)
                  for name, types in (("Google LLC", ("OO",)), ("XXVI Holdings Inc.", ("CO",)),
                                      ("Alphabet Inc.", ("CO",)))), **OTHER_ISSUER)
    assert _select(twin, ALPHABET).ok


def test_an_intermediate_is_never_the_chosen_line():
    # Alone, it is not the member's position at all.
    r = _select(_g13(_line_13g("XXVI Holdings Inc."), **OTHER_ISSUER), ALPHABET)
    assert (r.reason, r.person) == ("member_line_missing", None)
    # First in the filing, it is still passed over: the member's own line, else the affiliate's.
    r = _select(_g13(_line_13g("XXVI Holdings Inc."), _line_13g("Google LLC", types=("OO",)), **OTHER_ISSUER),
                ALPHABET)
    assert r.ok and r.person.name == "Google LLC"
    r = _select(_g13(_line_13g("XXVI Holdings Inc.", types=("HC",)), _line_13g("Alphabet Inc."), **OTHER_ISSUER),
                ALPHABET)
    assert r.ok and r.person.name == "Alphabet Inc."      # an intermediate's type is never judged


def test_a_member_config_without_intermediates_treats_xxvi_as_outside():
    bare = dataclasses.replace(ALPHABET, intermediates=())
    r = S.select_member_position(_parse("google_spacex_13g"), bare)
    assert (r.reason, r.person.name, r.percent) == ("shared_power", "Alphabet Inc.", Decimal("7.2"))
    r = S.select_member_position(_parse("google_terawulf_13g"), bare)
    assert (r.reason, r.derivative_note) == ("shared_power", "incl. 41.0M warrant shares")
    # A malformed value (a bare string, not a tuple) reads as no intermediates: fail closed.
    odd = dataclasses.replace(ALPHABET, intermediates="XXVI Holdings Inc.")
    assert S.select_member_position(_parse("google_spacex_13g"), odd).reason == "shared_power"
    # Another member's intermediate is nobody else's chain.
    assert S.select_member_position(_parse("google_spacex_13g"), ALPHABET).ok


def test_a_filing_about_an_intermediate_is_about_the_member():
    xml = _g13(_line_13g("Somebody Else Corp"), issuer_cik="0000123456", issuer_name="XXVI HOLDINGS INC")
    assert _select(xml, ALPHABET).reason == "about_member"
    assert _select(xml, NVIDIA).reason == "member_line_missing"


# ── the order of the new checks ──────────────────────────────────────────────────────────

def test_a_full_sell_out_is_an_exit_never_no_power():
    """Zero shares and zero power is how a member reports selling everything: an exit."""
    xml = _g13(_line_13g("NVIDIA Corporation", shares="0", percent="0", powers=("0", "0", "0", "0")))
    assert _select(xml).reason == "exit_filing"
    xml = _d13(_line_13d("ELI LILLY & Co", shares="0.00", percent="0.0", powers=("0", "0", "0", "0")),
               item5e="10/15/2026")
    assert _select(xml, LILLY).reason == "exit_filing"


@pytest.mark.parametrize("member_powers, group", [(SHARED_10M, None), (SHARED_10M, "a"), (SOLE_10M, "a")])
def test_a_sub_5_percent_joint_filing_is_an_exit_whoever_co_reports_it(member_powers, group):
    """Below 5% is the exit path only (plan v3, Numbers), whatever the power or the group box
    says: the safe direction (an exit only ever unpublishes)."""
    xml = _g13(_line_13g("NVIDIA Corporation", percent="3.1", powers=member_powers, group=group),
               _line_13g("Co-Invest Partners LP", types=("PN",), percent="3.1", powers=SHARED_10M, group=group))
    assert _select(xml).reason == "exit_filing"
    assert _select(_g13(_line_13g("NVIDIA Corporation", percent="3.1", group=group),
                        _line_13g("Fund X LP", types=("PN",)), five="Y")).reason == "exit_filing"


def test_no_dispositive_power_wins_over_the_group_box_and_shared_power():
    xml = _d13(_line_13d("ELI LILLY & Co", shares="10000000", percent="19.9", powers=VOTES_ONLY_10M, group="a"),
               _line_13d("Target Holder LP", types=("PN",), shares="10000000", percent="19.9", powers=VOTES_ONLY_10M))
    assert _select(xml, LILLY).reason == "no_dispositive_power"


@pytest.mark.parametrize("group, reason", [(None, "shared_power"), ("a", "group_member")])
def test_the_group_box_wins_over_shared_power_and_shared_power_over_the_band_and_composition(group, reason):
    r = _select(_g13(
        _line_13g("NVIDIA Corporation", percent="61.0", powers=SHARED_10M, comments=WARRANT_COMMENT, group=group),
        _line_13g("Co-Invest Partners LP", types=("PN",), percent="61.0", powers=SHARED_10M)))
    assert r.reason == reason and r.derivative_note == "incl. 2.0M warrant shares"


def test_a_class_problem_wins_over_the_outside_holder_checks():
    xml = _g13(_line_13g("NVIDIA Corporation", group="a", powers=SHARED_10M),
               _line_13g("Fund X LP", types=("PN",), group="a", powers=SHARED_10M),
               class_title="Series A Preferred Stock")
    assert _select(xml).reason == "class_unmapped"


# ── 2d''. review A (S1-LONE-SHARED-POWER): a chain of one, and a disclaimed position ────

def _lilly_alone(comments: Optional[str] = None, powers: Sequence[Optional[str]] = SHARED_10M, **kw) -> str:
    """The reviewer's shape: Lilly the ONLY reporting person on a merger target's 13D, 19.9%."""
    return _d13(_line_13d("Eli Lilly and Company", shares="10000000", percent="19.9", comments=comments,
                          powers=powers), class_title="Common Stock, $0.0001 par value per share", **kw)


def test_review_a_a_lone_support_agreement_13d_never_reads_as_the_members_holding():
    """Verbatim from the review: shared voting AND shared dispositive power over 10,000,000
    shares the support agreements reach, Lilly alone, 19.9%, the filer saying it holds them
    "solely because of the Support Agreements" and disclaims them. It was ok=True (auto:
    "Lilly holds 19.9%")."""
    r = _select(_lilly_alone(SUPPORT_COMMENT), LILLY)
    assert (r.ok, r.reason, r.person.name, r.shares, r.percent, r.basis) == (
        False, "ownership_disclaimed", "Eli Lilly and Company", 10_000_000, Decimal("19.9"), "of common stock")
    assert r.disclaimer_quote and "Support Agreements" in r.disclaimer_quote
    assert len(r.disclaimer_quote) <= S.MAX_QUOTE_CHARS
    # Part 1 on its own: with no text at all, a chain of one sharing power is the owner's call.
    r = _select(_lilly_alone(), LILLY)
    assert (r.ok, r.reason, r.disclaimer_quote) == (False, "shared_power", None)
    # The outcome no longer depends on whether the merger sub co-reports (before the fix, only
    # this variant was caught): with it, still the owner's call; with the text, disclaimed.
    sub = _line_13d("Kestrel Merger Sub, Inc.", shares="10000000", percent="19.9", powers=SHARED_10M)
    lilly = _line_13d("Eli Lilly and Company", shares="10000000", percent="19.9", powers=SHARED_10M)
    assert _select(_d13(lilly, sub), LILLY).reason == "shared_power"
    lilly = _line_13d("Eli Lilly and Company", shares="10000000", percent="19.9", powers=SHARED_10M,
                      comments=SUPPORT_COMMENT)
    assert _select(_d13(lilly, sub), LILLY).reason == "ownership_disclaimed"
    # Part 2 on its own: the same text beside SOLE power still says the block is not Lilly's.
    assert _select(_lilly_alone(SUPPORT_COMMENT, powers=SOLE_10M), LILLY).reason == "ownership_disclaimed"
    # Honest twin: Lilly alone, sole power, no such text — its own stake.
    assert _select(_lilly_alone(powers=SOLE_10M), LILLY).ok


@pytest.mark.parametrize("powers, reason", [
    (SHARED_10M, "shared_power"),
    (("10000000", "5", "10000000", "0"), "shared_power"),          # shared voting only
    (("10000000", "0", "10000000", "5"), "shared_power"),          # shared dispositive only
    (("0", "10000000", "10000000", "0"), "shared_power"),          # sells alone, votes with someone
    (("10000000", None, "10000000", "0"), "shared_power"),         # row 6 left out
    (("10000000", "N/A", "10000000", "0"), "shared_power"),        # row 6 unreadable
    (("10000000", "0", "10000000", None), "shared_power"),         # row 8 left out
    (("10000000", "0", "10000000", "n/a"), "shared_power"),        # row 8 unreadable
    # honest twins: sole power only, both shared rows stated as zero
    (SOLE_10M, None),
    (("10000000", "0.00", "10000000", "0.00"), None),
    (("10000000", "0", "12000000", "0"), None),
])
def test_a_chain_of_one_that_shares_power_or_does_not_say_is_shared_power(powers, reason):
    assert _select(_g13(_line_13g("NVIDIA Corporation", powers=powers))).reason == reason
    assert _select(_d13(_line_13d("ELI LILLY & Co", shares="10000000", powers=powers)), LILLY).reason == reason


def test_the_chain_is_counted_in_entities_never_in_lines():
    """Two lines of the member ITSELF (two spellings) are still a chain of one: a company
    cannot share power with itself."""
    two = _g13(_line_13g("NVIDIA Corporation", powers=SHARED_10M), _line_13g("NVIDIA CORP", powers=SHARED_10M))
    r = _select(two)
    assert (r.reason, r.person.name) == ("shared_power", "NVIDIA Corporation")
    assert _select(_g13(_line_13g("NVIDIA Corporation"), _line_13g("NVIDIA CORP"))).ok      # sole: fine
    # An affiliate alone, or the member alone, is a chain of one too.
    r = _select(_g13(_chain_line("Google LLC", ("OO",)), **OTHER_ISSUER), ALPHABET)
    assert (r.reason, r.person.name) == ("shared_power", "Google LLC")
    assert _select(_g13(_chain_line("Alphabet Inc."), **OTHER_ISSUER), ALPHABET).reason == "shared_power"
    # Honest twins: any two entities of the chain sharing one block are the member's own.
    for pair in ((("Google LLC", ("OO",)), ("Alphabet Inc.", ("CO",))),
                 (("XXVI Holdings Inc.", ("CO",)), ("Alphabet Inc.", ("CO",))),
                 (("Google LLC", ("OO",)), ("XXVI Holdings Inc.", ("CO",)))):
        assert _select(_g13(*(_chain_line(n, t) for n, t in pair), **OTHER_ISSUER), ALPHABET).ok, pair


@pytest.mark.parametrize("percent, powers, reason", [
    ("3.1", SHARED_10M, "exit_filing"),                    # the exit runs first
    ("19.9", VOTES_ONLY_10M, "no_dispositive_power"),      # then the power to sell
    ("61.0", SHARED_10M, "shared_power"),                  # shared power before the band
])
def test_a_chain_of_one_sharing_power_keeps_the_documented_order(percent, powers, reason):
    assert _select(_g13(_line_13g("NVIDIA Corporation", percent=percent, powers=powers))).reason == reason
    # ...and before the composition, whose warrant note still travels
    r = _select(_g13(_line_13g("NVIDIA Corporation", powers=SHARED_10M, comments=WARRANT_COMMENT)))
    assert (r.reason, r.derivative_note) == ("shared_power", "incl. 2.0M warrant shares")


#: Text that says the reported block is not the member's own. Each line must send a lone,
#: SOLE-power, plain-class position (otherwise ok) to the owner as ``ownership_disclaimed``.
DISCLAIMS = [
    SUPPORT_COMMENT,
    "The Reporting Person expressly disclaims beneficial ownership of all of these shares.",
    "Eli Lilly and Company disclaims beneficial ownership of the Subject Shares.",
    "Each of Parent and Merger Sub disclaim beneficial ownership of the Shares.",
    "The Reporting Person disclaims any beneficial ownership of the shares reported herein.",
    "The Reporting Person expressly disclaims, for purposes of Section 13(d) of the Exchange Act, beneficial "
    "ownership of the Shares.",
    "Neither the filing of this Schedule nor any of its contents shall be deemed to constitute an admission that "
    "the Reporting Person is the beneficial owner of any of the Shares, and such beneficial ownership is "
    "expressly disclaimed.",
    "Beneficial ownership of the Shares is disclaimed.",
    "The Reporting Person disclaims being the beneficial owner of the Shares.",
    "The Reporting Person disclaims that it is the beneficial owner of any of the Shares.",
    "The Reporting Person disclaims ownership of the Shares.",
    "The Reporting Person disclaims any pecuniary interest in the Shares.",
    "The Reporting Person disclaims any economic interest in the Shares.",
    "The Reporting Person disclaims that it beneficially owns the Shares.",
    "The Reporting Person files this statement disclaiming beneficial ownership of the Shares.",
    "Disclaimer of beneficial ownership.",
    "Disclaimers of beneficial ownership are set out in Item 4.",
    # the reporting person disclaims its OWN holding (by reference or by name)
    "The Reporting Person disclaims beneficial ownership of the shares held by it.",
    "Eli Lilly and Company disclaims beneficial ownership of the shares held by Eli Lilly and Company.",
    # another holder's shares: nothing says they are outside the reported count — fail closed
    "The Reporting Person disclaims beneficial ownership of the shares held by the GV Funds.",
    "The Reporting Person disclaims beneficial ownership of the shares held by GV 2019, L.P. except to the extent "
    "of its pecuniary interest therein.",
    "The Reporting Person disclaims beneficial ownership of the Shares, which are held of record by the Supporting "
    "Stockholders, except to the extent of its pecuniary interest therein.",
    "The Reporting Person disclaims beneficial ownership except to the extent of its pecuniary interest therein; "
    "the Shares are held by the Supporting Stockholders.",
    # the qualifier must sit in the disclaimer's OWN clause
    "The Reporting Person disclaims beneficial ownership of the Shares; each fund disclaims beneficial ownership "
    "except to the extent of its pecuniary interest therein.",
    # held only because of an agreement (support, voting, tender, lock-up — any agreement)
    "The Reporting Person may be deemed to beneficially own these shares solely because of the Support Agreements.",
    "As a result of the Support Agreements, the Reporting Person may be deemed to have acquired beneficial "
    "ownership of 10,000,000 shares.",
    "By reason of the Voting Agreements, the Reporting Person may be deemed to share voting power over the Shares.",
    "The Reporting Person may be deemed to beneficially own the Shares solely by virtue of the Tender and Support "
    "Agreements.",
    "The Shares are reported as a result of its entry into the Voting Agreement.",
    "The Shares are reported solely due to the Lock-Up Agreements.",
    "by reason of the execution and delivery of the Support Agreements",
    "beneficial ownership arises because of the Voting and Support Agreement",
    "solely as a result of the Agreement and Plan of Merger",
    "Beneficial ownership is reported solely because the Reporting Person may be deemed to share voting power under "
    "the Voting Agreements.",
    "on account of the Irrevocable Support Agreement",
    "as a consequence of the Stockholders Agreement",
    "Except as a result of the Support Agreements, the Reporting Person does not beneficially own any Shares.",
]
#: Honest twins: each sits next to a word the scan reads, and each must stay a plain position.
DISCLAIM_HONEST = [
    # a disclaimer of GROUP membership, never of ownership
    "Each Reporting Person disclaims membership in a group.",
    "The Reporting Person expressly disclaims membership in a group within the meaning of Rule 13d-5(b)(1) of the "
    "Exchange Act.",
    "The Reporting Person disclaims that it is a member of a group.",
    "Eli Lilly and Company beneficially owns the shares directly and disclaims membership in a group.",
    "Each Reporting Person disclaims membership in a group. Ownership percentages are based on 107,000,000 shares.",
    "Each Reporting Person disclaims membership in a group; its ownership percentage is based on 107,000,000 shares.",
    "The Reporting Person disclaims any obligation to update this information.",
    # the chain boilerplate: qualified by the pecuniary interest, naming no outside holder
    "The Reporting Person disclaims beneficial ownership of the securities reported herein except to the extent "
    "of its pecuniary interest therein.",
    "The Reporting Person disclaims beneficial ownership of the shares held by it except to the extent of its "
    "pecuniary interest therein.",
    "The Reporting Person disclaims beneficial ownership of the securities reported herein other than to the "
    "extent of its pecuniary interest therein.",
    # an agreement that is not why the shares are held
    "The Reporting Person acquired the shares pursuant to a Stock Purchase Agreement dated July 1, 2026.",
    "The shares are subject to a 180-day lock-up agreement with the underwriters of the initial public offering.",
    "As a result of the closing of the initial public offering, the Reporting Person entered into a lock-up "
    "agreement.",
    "The shares were acquired as a result of the initial public offering. The Reporting Person is party to a "
    "lock-up agreement.",
    "As a result, the Reporting Person may be deemed to beneficially own the shares; see the Joint Filing Agreement.",
    "Due to rounding, the percentages may not total 100%.",
    "The percentage decreased due to the Issuer's issuance of shares under the Merger Agreement.",
    "The Reporting Person holds the shares by virtue of its ownership of all of the outstanding shares of its "
    "subsidiary.",
]


@pytest.mark.parametrize("text", DISCLAIMS)
def test_a_disclaimed_position_goes_to_the_owner_in_both_schemas(text):
    """As a 13G cover comment, as the 13G's Item 4 text and as a 13D Item 5(a) text."""
    for xml, member in ((_g13(_line_13g("NVIDIA Corporation", comments=text), class_title="Common Stock"), NVIDIA),
                        (_g13(item4=text, class_title="Common Stock"), NVIDIA),
                        (_d13(item5a=text), LILLY)):
        r = _select(xml, member)
        assert (r.ok, r.reason) == (False, "ownership_disclaimed"), (text, r.reason)
        assert r.disclaimer_quote and len(r.disclaimer_quote) <= S.MAX_QUOTE_CHARS
        assert r.derivative_note is None and r.derivative_quote is None
        assert r.shares is not None and r.percent is not None    # the numbers are kept for the line


@pytest.mark.parametrize("text", DISCLAIM_HONEST)
def test_an_honest_twin_of_a_disclaimer_stays_plain_in_both_schemas(text):
    for xml, member in ((_g13(_line_13g("NVIDIA Corporation", comments=text), class_title="Common Stock"), NVIDIA),
                        (_g13(item4=text, class_title="Common Stock"), NVIDIA),
                        (_d13(item5a=text), LILLY)):
        r = _select(xml, member)
        assert (r.ok, r.disclaimer_quote) == (True, None), (text, r.reason)


@pytest.mark.parametrize("text, reason", [
    # chain boilerplate: the disclaimed shares are held inside the member's own reviewed chain
    ("Each of Alphabet Inc. and XXVI Holdings Inc. disclaims beneficial ownership of the shares held by Google "
     "LLC except to the extent of its pecuniary interest therein.", None),
    ("Alphabet Inc. disclaims beneficial ownership of the shares held directly by Google LLC.", None),
    ("Alphabet Inc. and XXVI Holdings Inc. disclaim beneficial ownership of the shares owned of record by Google "
     "LLC, a Delaware limited liability company.", None),
    ("Each Reporting Person disclaims beneficial ownership of the shares held by the other Reporting Persons.", None),
    ("Alphabet Inc. disclaims beneficial ownership of the shares held by Google LLC and XXVI Holdings Inc.", None),
    ("Alphabet Inc. disclaims beneficial ownership of the shares held by Google LLC. Google LLC is a Delaware "
     "limited liability company.", None),
    ("Alphabet Inc. disclaims beneficial ownership of the shares held through Google LLC.", None),
    # must-fire twins: a holder outside the chain, beside or instead of the chain's own
    ("Alphabet Inc. disclaims beneficial ownership of the shares held directly by Google LLC and GV 2019, L.P.",
     "ownership_disclaimed"),
    ("Alphabet Inc. disclaims beneficial ownership of the shares held by GV 2019 GP, L.L.C. except to the extent "
     "of its pecuniary interest therein.", "ownership_disclaimed"),
    ("Alphabet Inc. disclaims beneficial ownership of the shares held by Google LLC; the shares are held for the "
     "account of GV 2019, L.P. and held by GV 2019, L.P.", "ownership_disclaimed"),
    ("Alphabet Inc. disclaims beneficial ownership of the shares held directly by Google LLC except to the extent "
     "of its pecuniary interest therein, and of the shares held by CapitalG LP.", "ownership_disclaimed"),
    ("Alphabet Inc. disclaims beneficial ownership of the shares held through GV 2019, L.P. except to the extent "
     "of its pecuniary interest therein.", "ownership_disclaimed"),
    ("The shares held by GV 2019, L.P., as to which Alphabet Inc. disclaims beneficial ownership except to the "
     "extent of its pecuniary interest therein, are reported here.", "ownership_disclaimed"),
])
def test_a_disclaimer_naming_only_the_members_chain_is_its_own_stake(text, reason):
    chain = (_chain_line("Google LLC", ("OO",)), _chain_line("XXVI Holdings Inc."), _chain_line("Alphabet Inc."))
    r = _select(_g13(*chain, item4=text, **OTHER_ISSUER), ALPHABET)
    assert r.reason == reason, (text, r.reason)
    assert (r.disclaimer_quote is None) is (reason is None)


def test_the_other_reporting_persons_are_the_chain_only_when_nobody_else_reports():
    text = "Each Reporting Person disclaims beneficial ownership of the shares held by the other Reporting Persons."
    sole = ("12000000", "0", "12000000", "0")
    chain = [_chain_line("Google LLC", ("OO",), powers=sole), _chain_line("Alphabet Inc.", powers=sole)]
    assert _select(_g13(*chain, item4=text, **OTHER_ISSUER), ALPHABET).ok
    # an outside line reports too (sole power, another block): "the other Reporting Persons" are
    # no longer only the chain
    fund = _line_13g("Other Fund LP", types=("PN",), shares="5000000", percent="4.6", powers=SOLE_10M)
    assert _select(_g13(*chain, fund, item4=text, **OTHER_ISSUER), ALPHABET).reason == "ownership_disclaimed"
    assert _select(_g13(*chain, fund, **OTHER_ISSUER), ALPHABET).ok             # honest twin: no text
    # a chain of one has no "other Reporting Persons" at all
    lone = _g13(_line_13g("NVIDIA Corporation"), item4=text)
    assert _select(lone).reason == "ownership_disclaimed"


def test_the_chain_a_disclaimer_may_name_is_the_members_own():
    """Google LLC is Alphabet's affiliate, not NVIDIA's: for NVIDIA it is an outside holder."""
    text = "NVIDIA Corporation disclaims beneficial ownership of the shares held by Google LLC."
    assert _select(_g13(_line_13g("NVIDIA Corporation"), item4=text)).reason == "ownership_disclaimed"
    text = "Alphabet Inc. disclaims beneficial ownership of the shares held by Google LLC."
    assert _select(_g13(_chain_line("Google LLC", ("OO",)), _chain_line("Alphabet Inc."), item4=text,
                        **OTHER_ISSUER), ALPHABET).ok


def test_a_self_reference_names_no_other_holder_and_a_name_with_and_is_one_name():
    """"held by Eli Lilly and Company" is Lilly itself (the whole phrase is read as one name
    before it is split on "and"): no other holder, so the pecuniary qualifier decides."""
    own = "Eli Lilly and Company disclaims beneficial ownership of the shares held by Eli Lilly and Company"
    assert _select(_d13(item5a=own + " except to the extent of its pecuniary interest therein."), LILLY).ok
    assert _select(_d13(item5a=own + "."), LILLY).reason == "ownership_disclaimed"
    # The same words in NVIDIA's filing name another company.
    r = _select(_g13(item4=own + " except to the extent of its pecuniary interest therein."))
    assert r.reason == "ownership_disclaimed"


def test_more_held_by_phrases_than_the_bound_fail_closed():
    chain_keys, self_keys = {"google llc", "alphabet incorporated"}, {"alphabet incorporated"}
    for n, want in ((S._MAX_HOLDERS, {"chain"}), (S._MAX_HOLDERS + 1, {"chain", "outside"})):
        text = "held by Google LLC; " * n
        assert S._holder_kinds(text, 0, len(text), chain_keys, self_keys) == want, n
    # Through the selector: n chain holders inside the disclaimer's own clause.
    chain = (_chain_line("Google LLC", ("OO",)), _chain_line("Alphabet Inc."))
    for n, reason in ((S._MAX_HOLDERS, None), (S._MAX_HOLDERS + 1, "ownership_disclaimed")):
        text = ("Alphabet Inc. disclaims beneficial ownership of the shares held by Google LLC"
                + " (held by Google LLC)" * (n - 1) + ".")
        assert _select(_g13(*chain, item4=text, **OTHER_ISSUER), ALPHABET).reason == reason, n


def test_own_names_that_fold_apart_are_still_one_entity():
    """A member listed under two names that do not fold together (a former name, say) is one
    company: its two lines sharing power are still a chain of one."""
    renamed = dataclasses.replace(NVIDIA, names=("NVIDIA Corporation", "NVIDIA Holdings International Corp"))
    two = _g13(_line_13g("NVIDIA Corporation", powers=SHARED_10M),
               _line_13g("NVIDIA Holdings International Corp", powers=SHARED_10M))
    assert S.select_member_position(S.parse_schedule_xml(two), renamed).reason == "shared_power"


def test_the_disclaimer_check_runs_after_the_exit_the_power_to_sell_the_type_and_the_class():
    text = "The Reporting Person expressly disclaims beneficial ownership of all of these shares."
    # a full sell-out (or any sub-5% filing) stays an exit
    xml = _g13(_line_13g("NVIDIA Corporation", shares="0", percent="0", powers=("0", "0", "0", "0"), comments=text))
    assert _select(xml).reason == "exit_filing"
    assert _select(_g13(_line_13g("NVIDIA Corporation", percent="3.1", comments=text))).reason == "exit_filing"
    assert _select(_d13(_line_13d("ELI LILLY & Co", comments=text), item5e="10/15/2026"), LILLY).reason == "exit_filing"
    # voting power only is still no_dispositive_power (the review S1-1 support-agreement test)
    assert _select(_lilly_alone(text, powers=VOTES_ONLY_10M), LILLY).reason == "no_dispositive_power"
    assert _select(_g13(_line_13g("NVIDIA Corporation", types=("HC",), comments=text))).reason == "reporting_type"
    assert _select(_g13(_line_13g("NVIDIA Corporation", comments=text),
                        class_title="Series A Preferred Stock")).reason == "class_unmapped"
    # ...and the quote travels whatever the final reason
    assert "disclaims beneficial ownership" in _select(xml).disclaimer_quote


def test_the_disclaimer_check_wins_over_the_group_box_shared_power_the_band_and_the_composition():
    text = "The Reporting Person expressly disclaims beneficial ownership of all of these shares."
    group = _g13(_line_13g("NVIDIA Corporation", group="a", comments=text), _line_13g("Fund X LP", types=("PN",),
                                                                                      group="a"))
    shared = _g13(_line_13g("NVIDIA Corporation", powers=SHARED_10M, comments=text),
                  _line_13g("Co-Invest Partners LP", types=("PN",), powers=SHARED_10M))
    above = _g13(_line_13g("NVIDIA Corporation", percent="61.0", comments=text))
    warrant = _g13(_line_13g("NVIDIA Corporation", comments=text + " " + WARRANT_COMMENT))
    for xml in (group, shared, above, warrant):
        assert _select(xml).reason == "ownership_disclaimed"
    r = _select(warrant)
    assert (r.derivative_note, r.derivative_quote) == ("incl. 2.0M warrant shares", None)


def test_an_outside_line_holding_the_same_block_is_read_like_the_composition_scan():
    """The co-reporting merger sub's own comment describes the same block."""
    sub = _line_13d("Kestrel Merger Sub, Inc.", shares="10000000", percent="19.9", powers=SHARED_10M,
                    comments="Merger Sub expressly disclaims beneficial ownership of all of these shares.")
    lilly = _line_13d("Eli Lilly and Company", shares="10000000", percent="19.9", powers=SHARED_10M)
    assert _select(_d13(lilly, sub), LILLY).reason == "ownership_disclaimed"
    # ...but a line holding ANOTHER block is not read (as for the composition scan)
    other = _line_13d("Kestrel Merger Sub, Inc.", shares="5000000", percent="9.9", powers=SOLE_10M,
                      comments="Merger Sub expressly disclaims beneficial ownership of all of these shares.")
    lilly = _line_13d("Eli Lilly and Company", shares="10000000", percent="19.9", powers=SOLE_10M)
    assert _select(_d13(lilly, other), LILLY).ok


def test_the_13d_history_is_never_read_for_a_disclaimer():
    """Item 3 (and the 5(c) trades) are history, exactly as for the composition scan."""
    r = _select(_d13(item3="As a result of the Support Agreements the Reporting Person disclaims beneficial "
                           "ownership of the Shares."), LILLY)
    assert r.ok and r.disclaimer_quote is None


def test_every_recorded_schedule_carries_no_disclaimer():
    for stem, (member, *_rest) in RECORDED.items():
        r = S.select_member_position(_parse(stem), member)
        assert r.disclaimer_quote is None, stem
        assert r.reason != "ownership_disclaimed", stem


@pytest.mark.parametrize("text", [
    # a negated disclaimer reads as one (negation is never parsed)
    "The Reporting Person has not disclaimed beneficial ownership of any of the Shares.",
    # any agreement inside a "solely because of / by reason of / as a result of" phrase counts
    "The percentage rose as a result of the Issuer's repurchases under the Share Repurchase Agreement.",
    # an unlisted subsidiary's defined short name is not in the reviewed chain
    "Amazon.com, Inc. disclaims beneficial ownership of the shares held by NV Holdings except to the extent of its "
    "pecuniary interest therein.",
    # an appositive the holder scan does not strip
    "Alphabet Inc. disclaims beneficial ownership of the shares held by its wholly-owned subsidiary Google LLC.",
    # the chain holder is named outside the disclaimer's own clause: nothing ties "the Shares"
    # to it (an exemption is read only in the disclaimer's clause; firing evidence in its sentence)
    "The shares are held by Google LLC; Alphabet Inc. disclaims beneficial ownership of the Shares.",
])
def test_the_accepted_over_blocks_fail_closed_to_the_owner(text):
    """Honest in intent, but no positional rule tells them from the real thing: each costs the
    owner a heads-up, never a wrong row. Seen live, one becomes a scoped exemption with a
    must-fire twin."""
    chain = (_chain_line("Google LLC", ("OO",)), _chain_line("XXVI Holdings Inc."), _chain_line("Alphabet Inc."))
    for xml, member in ((_g13(item4=text, class_title="Common Stock"), NVIDIA), (_g13(*chain, item4=text,
                                                                                     **OTHER_ISSUER), ALPHABET)):
        r = _select(xml, member)
        assert (r.reason, bool(r.disclaimer_quote)) == ("ownership_disclaimed", True), text


def test_more_disclaimer_words_than_the_bound_fail_closed():
    one = "Each Reporting Person disclaims membership in a group. "
    assert _select(_g13(_line_13g("NVIDIA Corporation", comments=one * S.MAX_DISCLAIMER_HITS))).ok
    r = _select(_g13(_line_13g("NVIDIA Corporation", comments=one * (S.MAX_DISCLAIMER_HITS + 1))))
    assert r.reason == "ownership_disclaimed" and r.disclaimer_quote


def test_ownership_disclaimed_is_listed_right_after_no_dispositive_power():
    assert "ownership_disclaimed" in REASONS
    rs = S.POSITION_REASONS
    assert rs.index("ownership_disclaimed") == rs.index("no_dispositive_power") + 1
    doc = S.select_member_position.__doc__
    assert doc.index("``no_dispositive_power``") < doc.index("``ownership_disclaimed``") < doc.index("``group_member``")


# ── 2e. selection: class, numbers and their order ───────────────────────────────────────

@pytest.mark.parametrize("title", [
    "Series A Preferred Stock", "American Depositary Shares", "Class B Common Stock", "Units",
    "Warrants to purchase Common Stock", "Class A Common Stock and Class C Capital Stock",
    "Class C Capital Stock and Class A Common Stock", "Limited Partnership Interests", "",
])
def test_a_class_that_is_not_plain_common_is_unmapped(title):
    r = _select(_g13(class_title=title))
    assert r.reason == "class_unmapped" and r.basis is None


#: Review S1-4: a series designator anywhere in the title, one or two of them.
SERIES_TITLES = [
    "Series B Common Stock",
    "Series C Common Stock, par value $0.01 per share",
    "Series A Common Stock and Series C Common Stock",
    "Series A common stock, Series B common stock",
    "Common Stock, Series 1",
    "Class A Common Stock, Series A",
    "Common Stock, par value $0.0001 per share and Series A Convertible Preferred Stock",
    "Common Stock, no par value and Series B Preferred Stock",
]
#: Two class letters (already refused by class_basis): the module's own guard agrees.
TWO_CLASS_TITLES = ["Class A Common Stock and Class C Capital Stock", "Class B and Class A Common Stock"]


@pytest.mark.parametrize("title", SERIES_TITLES + TWO_CLASS_TITLES)
def test_a_series_or_two_class_title_is_unmapped_by_both_modules(title, monkeypatch):
    assert S.class_basis(title) is None
    r = _select(_g13(class_title=title))
    assert (r.reason, r.basis) == ("class_unmapped", None)
    # The module's own guard holds even if class_basis ever mapped the title (one rule, kept
    # in both places): a 3% series filing is still no exit of the member's common position.
    monkeypatch.setattr(S, "class_basis", lambda t: "of common stock")
    assert _select(_g13(class_title=title)).reason == "class_unmapped"
    assert _select(_g13(_line_13g("NVIDIA Corporation", percent="3.0"), class_title=title)).reason == "class_unmapped"


@pytest.mark.parametrize("title, basis", [
    # honest twins: a plain class whose words come near the guards
    ("Class A Common Stock, $0.001 par value", "of Class A"),
    ("Common Stock (the Seriesless class)", "of common stock"),
    ("Common Stock, par value $0.001 per share, of the Classic Co.", "of common stock"),
    ("Class A Ordinary Shares", "of Class A"),
])
def test_the_class_guard_does_not_refuse_a_plain_class(title, basis, monkeypatch):
    monkeypatch.setattr(S, "class_basis", lambda t: basis)
    r = _select(_g13(class_title=title))
    assert (r.ok, r.basis) == (True, basis)


@pytest.mark.parametrize("percent, reason", [
    ("4.99", "exit_filing"), ("0", "exit_filing"), ("5", None), ("5.0", None), ("50", None),
    ("50.0", None), ("50.01", "percent_above_50"), ("100", "percent_above_50"),
])
def test_the_percent_band(percent, reason):
    assert _select(_g13(_line_13g("NVIDIA Corporation", percent=percent))).reason == reason


def test_the_five_percent_box_is_an_exit_even_beside_a_higher_percent():
    assert _select(_g13(five="Y")).reason == "exit_filing"
    assert _select(_g13(five="N")).ok


def test_a_13d_item_5e_date_is_an_exit():
    assert _select(_d13(item5e="10/15/2026"), LILLY).reason == "exit_filing"
    assert _select(_d13(item5e="N/A"), LILLY).ok


WARRANT_COMMENT = ("The 10,000,000 shares reported herein consist of 8,000,000 Class A ordinary shares and "
                   "2,000,000 Class A ordinary shares issuable upon exercise of a warrant held by NVIDIA.")


def test_an_exit_wins_over_the_composition_and_still_names_the_warrants():
    r = _select(_g13(_line_13g("NVIDIA Corporation", percent="3.1", comments=WARRANT_COMMENT)))
    assert r.reason == "exit_filing"
    assert r.derivative_note == "incl. 2.0M warrant shares"


def test_a_class_problem_wins_over_an_exit():
    """A 3% filing on a PREFERRED class is not an exit of the member's common position."""
    r = _select(_g13(_line_13g("NVIDIA Corporation", percent="3.0"), class_title="Series A Preferred Stock"))
    assert r.reason == "class_unmapped" and r.percent == Decimal("3.0")


@pytest.mark.parametrize("make, reason", [
    (lambda c: _g13(_line_13g("NVIDIA Corporation", percent="61.0", comments=c)), "percent_above_50"),
    (lambda c: _g13(_line_13g("NVIDIA Corporation", types=("HC",), comments=c)), "reporting_type"),
    (lambda c: _g13(_line_13g("NVIDIA Corporation", comments=c, group="a"),
                    _line_13g("Fund X LP", types=("PN",), shares="1", percent="0.1")), "group_member"),
    (lambda c: _g13(_line_13g("NVIDIA Corporation", comments=c, powers=SHARED_10M),
                    _line_13g("Fund X LP", types=("PN",), shares="1", percent="0.1", powers=SHARED_10M)),
     "shared_power"),
    (lambda c: _g13(_line_13g("NVIDIA Corporation", comments=c, powers=VOTES_ONLY_10M)), "no_dispositive_power"),
    (lambda c: _g13(_line_13g("NVIDIA Corporation", comments=c, powers=SHARED_10M)), "shared_power"),   # alone
    (lambda c: _g13(_line_13g("NVIDIA Corporation", comments=SUPPORT_COMMENT + " " + c)), "ownership_disclaimed"),
    (lambda c: _g13(_line_13g("NVIDIA Corporation", comments=c)), "derivative_position"),
])
def test_every_proposal_reason_still_carries_the_warrant_note(make, reason):
    r = _select(make(WARRANT_COMMENT))
    assert r.reason == reason and r.derivative_note == "incl. 2.0M warrant shares"
    assert r.derivative_quote is None


def test_the_first_usable_cusip_is_the_identity():
    assert _select(_g13(cusips=("N97284109", "N97284108"))).cusip == "N97284108"   # bad check digit skipped
    assert _select(_g13(cusips=("", "n97284108"))).cusip == "N97284108"
    assert _select(_g13(cusips=())).cusip is None                                  # missing → allowed
    assert _select(_g13(cusips=())).ok
    assert _select(_g13(cusips=(), bare_cusip="N97284108")).cusip == "N97284108"
    f = S.parse_schedule_xml(_g13(cusips=("N97284108", "N97284108", "x" * 30)))
    assert f.cusips == ("N97284108",)


def test_the_position_result_keeps_its_invariants():
    with pytest.raises(ValueError):
        S.PositionResult(True, "exit_filing", None, None, None, None, None, None, None)
    with pytest.raises(ValueError):
        S.PositionResult(False, None, None, None, None, None, None, None, None)
    with pytest.raises(ValueError):
        S.PositionResult(False, "not_a_reason", None, None, None, None, None, None, None)
    # an ok position never carries a disclaimer quote; any other reason may
    with pytest.raises(ValueError):
        S.PositionResult(True, None, None, None, None, None, None, None, None, "disclaims beneficial ownership")
    assert S.PositionResult(False, "exit_filing", None, None, None, None, None, None, None, "q").disclaimer_quote == "q"
    assert S.PositionResult(True, None, None, None, None, None, None, None, None).disclaimer_quote is None


def test_every_code_the_module_can_return_is_a_contract_reason():
    assert set(S.PARSE_ERROR_CODES) <= set(REASONS)
    assert set(S.POSITION_REASONS) <= set(REASONS)
    assert len(set(S.POSITION_REASONS)) == len(S.POSITION_REASONS)
    assert {"shared_power", "no_dispositive_power", "ownership_disclaimed"} <= set(S.POSITION_REASONS)


# ══════════════════════════════════════════════════════════════════════════════════════
# 3. The composition scan
# ══════════════════════════════════════════════════════════════════════════════════════

def _scan(*texts: str, shares: Optional[int] = 100_000_000):
    return S._scan_composition(list(texts), shares)


HONEST = [
    # Aktis / Scribe, verbatim: the issuer's IPO share count.
    "This percentage is calculated based upon 53,296,950 outstanding shares of Common Stock of the Issuer "
    "following the consummation of its initial public offering, after giving effect to the full exercise "
    "of the underwriters' option to purchase an additional 2,647,500 shares of Common Stock.",
    "after giving effect to the exercise in full by the underwriters of their option to purchase additional shares",
    "the underwriters exercised their over-allotment option in full on July 1, 2026",
    "including the underwriters\u2019 over-allotment option",
    "the underwriters' exercise in full of their option to purchase additional shares",
    "after the over-allotment option and the greenshoe option",
    "after the exercise of the greenshoe option",
    "Amazon exercises sole voting and dispositive power over the shares held by NV Holdings.",
    "Google LLC may be deemed to exercise shared voting and investment power over the shares.",
    "Limited Liability Company",
    "See Row 9 of cover page for the Reporting Person.",
    "Amazon has sole voting and investment power with respect to the shares held by NV Holdings.",
    "The representations and warranties in the agreement are customary.",   # "warranties" ≠ warrant
    "Optional redemption is not available.",                                  # "optional" ≠ option
]
#: Review S1-2's honest twins: each sits next to one of the words added to the trigger.
NEW_HONEST = [
    # bare "exchange" never counts
    "as disclosed in the Issuer's Form 10-Q filed with the Securities and Exchange Commission on August 4, 2026",
    "beneficial ownership is determined under Rule 13d-3 of the Securities Exchange Act of 1934, as amended",
    "The Issuer's Common Stock is listed on the New York Stock Exchange.",
    "The Reporting Person acquired the shares in exchange for its interest in Acme Holdings LLC.",
    "upon the closing of the exchange offer, the Reporting Person held 1,000,000 shares",
    # "conver…" that is no conversion
    "following conversations with the Issuer's management",
    "Conversely, the Reporting Person holds no other securities of the Issuer.",
    # "preferred" / "RSU" inside other words
    "the shares referred to in Row 9 are held directly",
    "pursuant to Rule 13d-1(c)",
    "The shares are restricted securities within the meaning of Rule 144.",
    # the schedules' own dividend wording (Item 6 / Item 5(d)) is no right to acquire shares
    "No other person is known to have the right to receive or the power to direct the receipt of dividends "
    "from, or the proceeds from the sale of, the shares.",
    "No other person is known to have the right to receive, or the power to direct the receipt of, dividends "
    "from, or the proceeds from the sale of, such securities.",
    "The Reporting Person has the right to receive dividends on the shares it holds.",
    # a right that is not a right to buy shares, and plain past purchases
    "subject to a right of first refusal in favor of the Issuer",
    "the Reporting Person subscribed for the shares in the Issuer's initial public offering",
    "the Reporting Person purchased the shares in a private placement",
]
HONEST += NEW_HONEST


@pytest.mark.parametrize("text", HONEST)
def test_honest_position_text_is_not_a_derivative(text):
    assert _scan(text).found is False


MUST_FLAG = [
    "upon exercise of its option to purchase additional shares",        # the MEMBER's option
    "the exercise of warrants held by the underwriters",
    "includes the underwriters' warrants",
    "exercise of voting power over 1,000 shares issuable upon conversion",
    "includes 2,000,000 shares issuable upon conversion of the Notes",
    "the Reporting Person holds convertible notes",
    "the right to acquire 1,000,000 shares within 60 days",
    "shares underlying the Series A preferred",
    "a pre-funded warrant",
    "a prefunded warrant",
    "stock options granted to the Reporting Person",
    "the Warrant is exercisable within 60 days",
    "options to purchase 50,000 shares exercisable by the underwriters",
    "the underwriters' option shares and 5,000 shares underlying warrants",
]
#: Review S1-2: the six phrasings the reviewer reproduced (12 of 12 came back plain), verbatim.
REVIEW_PROBES = [
    "The shares reported include 2,000,000 shares of Common Stock that may be acquired upon conversion of 2,000 "
    "shares of Series A Preferred Stock held by the Reporting Person.",
    "Ownership is stated on an as-converted basis, assuming conversion of all Series A Preferred Stock.",
    "Includes 3,000,000 shares receivable upon the conversion of the 2030 Notes.",
    "Includes 4,000,000 shares the Reporting Person may receive upon redemption or exchange of an equal number "
    "of Common Units.",
    "Includes 500,000 shares the Reporting Person has the right to purchase within 60 days.",
    "Includes 100,000 shares deliverable upon settlement of restricted stock units.",
]
#: One sentence per word added to the trigger, each holding no other trigger word.
NEW_TRIGGERS = [
    "the Reporting Person may convert its Class B shares into Class A shares at any time",   # convert
    "each Class B share converts into one Class A share",                                    # converts
    "assuming the Class B shares are converted",                                             # converted
    "after converting its Class B shares",                                                   # converting
    "receivable upon the conversion of the 2030 Notes",                                      # conversion
    "after giving effect to all conversions",                                                # conversions
    "on an as-converted basis",                                                              # as-converted
    "on an as converted basis",
    "together with 2,000 shares of Series A Preferred Stock",                                # preferred
    "includes 10,000 RSUs that vest within 60 days",                                         # RSUs
    "an RSU award granted to the Reporting Person",                                          # RSU
    "deliverable upon settlement of restricted stock units",                                 # restricted stock units
    "includes 10,000 restricted share units",
    "includes 3,000,000 LLC Units exchangeable for shares of Class A Common Stock",           # exchangeable
    "may receive upon redemption or exchange of an equal number of Common Units",            # upon … exchange of
    "shares received upon the exchange of its Class B interests",
    "upon exchange of the Holdings Units",
    "upon the redemption or exchange of its LLC Units",
    "has the right to purchase 500,000 shares within 60 days",                               # right to purchase
    "holds rights to purchase 500,000 shares",
    "the right to receive 1,000,000 shares of Common Stock upon the closing",                 # right to receive
    "rights to subscribe for 2,000,000 new shares",                                          # rights to subscribe
    # the dividend exemption covers only its own span
    "the right to receive dividends and the right to receive 500,000 shares upon the closing",
]
MUST_FLAG += REVIEW_PROBES + NEW_TRIGGERS


@pytest.mark.parametrize("text", MUST_FLAG)
def test_every_other_derivative_wording_is_flagged(text):
    assert _scan(text).found is True


@pytest.mark.parametrize("text", REVIEW_PROBES + NEW_TRIGGERS)
def test_a_new_trigger_word_sends_the_position_to_the_owner_in_both_schemas(text):
    """As a 13G cover comment and as a 13D Item 5(a) text, on plain common stock."""
    r = _select(_g13(_line_13g("NVIDIA Corporation", comments=text), class_title="Common Stock"))
    assert (r.ok, r.reason, r.derivative_note) == (False, "derivative_position", None)
    assert r.derivative_quote and len(r.derivative_quote) <= S.MAX_QUOTE_CHARS
    r = _select(_d13(item5a=text), LILLY)
    assert (r.ok, r.reason, r.derivative_note) == (False, "derivative_position", None)
    assert r.derivative_quote


@pytest.mark.parametrize("text", NEW_HONEST)
def test_an_honest_twin_of_a_new_trigger_stays_plain_in_both_schemas(text):
    assert _select(_g13(_line_13g("NVIDIA Corporation", comments=text), class_title="Common Stock")).ok
    assert _select(_d13(item5a=text), LILLY).ok


@pytest.mark.parametrize("extra", [
    "The Reporting Person also holds 3,000,000 LLC Units exchangeable for Class A ordinary shares.",
    "It may also receive shares upon the exchange of its Holdings Units.",
    # no "units", "notes" or "preferred" beside them: only the every-hit-is-a-warrant rule
    # keeps these from being named as warrant shares
    "It also holds 3,000,000 Class B shares exchangeable for Class A ordinary shares.",
    "It may also receive 1,000,000 shares upon the redemption or exchange of its Class B interests.",
    "It also has the right to purchase 500,000 shares within 60 days.",
    "It also has the right to receive 500,000 shares at the closing.",
    "Its 1,000 shares of Series A Preferred Stock vote with the ordinary shares.",
    "Percentages are stated on an as-converted basis.",
    "It may convert its Class B shares.",
    "It also holds 10,000 RSUs.",
    "It also holds rights to subscribe for 100,000 new shares.",
])
def test_a_warrant_count_is_named_only_when_every_hit_is_a_warrant(extra):
    """A parsed warrant count names the basis only when nothing but warrants is mentioned;
    beside any other trigger word the filer is quoted instead."""
    assert _scan(WARRANT_COMMENT, shares=10_000_000).note == "incl. 2.0M warrant shares"
    for texts in ([WARRANT_COMMENT + " " + extra], [extra + " " + WARRANT_COMMENT], [WARRANT_COMMENT, extra]):
        c = _scan(*texts, shares=10_000_000)
        assert (c.found, c.note) == (True, None), texts
        assert c.quote and len(c.quote) <= S.MAX_QUOTE_CHARS
    r = _select(_g13(_line_13g("NVIDIA Corporation", comments=WARRANT_COMMENT + " " + extra)))
    assert (r.reason, r.derivative_note) == ("derivative_position", None) and r.derivative_quote


@pytest.mark.parametrize("extra", [
    "The percent is based on the Issuer's Form 10-Q filed with the Securities and Exchange Commission.",
    "Beneficial ownership is determined under Rule 13d-3 of the Securities Exchange Act of 1934.",
    "No other person is known to have the right to receive or the power to direct the receipt of dividends "
    "from, or the proceeds from the sale of, the shares.",
])
def test_a_warrant_count_survives_honest_neighbours(extra):
    c = _scan(WARRANT_COMMENT + " " + extra, shares=10_000_000)
    assert (c.found, c.note, c.quote) == (True, "incl. 2.0M warrant shares", None)


def test_an_unlisted_honest_phrase_fails_closed_to_the_owner():
    """Accepted over-block: "exercised" here is about the over-allotment option, but no
    exemption covers this word order, so the position goes to the owner (a tap, never a wrong
    row). Seen live it becomes one more positional exemption with a must-reject twin."""
    c = _scan("assuming the over-allotment option is not exercised")
    assert c.found and c.quote == "assuming the over-allotment option is not exercised"


def test_nebius_comment_counts_one_warrant():
    (line,) = _parse("nvidia_nebius_13g").persons
    c = _scan(line.comments, shares=22_256_412)
    assert (c.found, c.note, c.quote) == (True, "incl. 21.1M warrant shares", None)


def test_terawulf_mentions_the_same_count_twice_and_it_is_counted_once():
    f = _parse("google_terawulf_13g")
    assert f.item4_text.count("41,011,803 shares of Common Stock that are issuable upon the exercise of warrants") == 2
    c = _scan(f.item4_text, shares=41_011_803)
    assert c.note == "incl. 41.0M warrant shares"


@pytest.mark.parametrize("text, shares, note", [
    (WARRANT_COMMENT, 10_000_000, "incl. 2.0M warrant shares"),
    ("NVIDIA holds warrants to purchase 750,000 shares of common stock.", 1_000_000, "incl. 750,000 warrant shares"),
    ('a warrant (the "Warrant") to purchase up to 58,970,907 shares of Common Stock', 60_000_000,
     "incl. 59.0M warrant shares"),
    ("41,011,803 shares underlying the warrants", 41_011_803, "incl. 41.0M warrant shares"),
    ("warrants exercisable for 3,700,000 shares of Class A common stock", 158_400_000, "incl. 3.7M warrant shares"),
    ("1,000,000 shares that are issuable upon exercise of the Series A Warrants", 1_000_000, "incl. 1.0M warrant shares"),
])
def test_a_single_warrant_count_names_the_warrant_shares(text, shares, note):
    c = _scan(text, shares=shares)
    assert (c.found, c.note, c.quote) == (True, note, None)


@pytest.mark.parametrize("text, shares", [
    # two different warrants: never summed into one figure
    ("1,000,000 shares issuable upon exercise of the A Warrant and 2,000,000 shares issuable upon exercise "
     "of the B Warrant", 10_000_000),
    # more warrant shares than the whole line reports
    (WARRANT_COMMENT, 1_500_000),
    # the count may be someone else's
    ("This figure excludes 2,000,000 shares issuable upon exercise of warrants held by Fund X.", 10_000_000),
    ("The Reporting Person disclaims beneficial ownership of 2,000,000 shares issuable upon exercise of warrants.",
     10_000_000),
    # a warrant beside another instrument: the note would hide the rest
    (WARRANT_COMMENT + " It also holds convertible notes.", 10_000_000),
    ("2,000,000 shares issuable upon exercise of warrants and upon conversion of the Series A Preferred", 10_000_000),
    # an option, not a warrant
    ("2,000,000 shares issuable upon exercise of options and warrants", 10_000_000),
    # a warrant with no count at all
    ("NVIDIA also holds a warrant.", 10_000_000),
    # a series name with a digit: not read as a count, so quoted
    ("2,000,000 shares issuable upon exercise of the 2026 warrants", 10_000_000),
    # a dollar amount is never a share count
    ("NVIDIA paid $2,000,000 shares issuable upon exercise of a warrant", 10_000_000),
    ("approximately 2.0 million shares issuable upon exercise of the warrant", 10_000_000),
])
def test_an_uncountable_derivative_quotes_the_filer_instead(text, shares):
    c = _scan(text, shares=shares)
    assert c.found and c.note is None
    assert c.quote and len(c.quote) <= S.MAX_QUOTE_CHARS


def test_the_line_shares_must_be_known_for_a_note():
    assert _scan(WARRANT_COMMENT, shares=None).note is None


def test_more_than_fifty_counts_are_never_read_as_one_warrant():
    many = " ".join("2,000,000 shares issuable upon exercise of the warrant." for _ in range(60))
    c = _scan(many, shares=10 ** 12)
    assert c.found and c.note is None


def test_the_quote_is_the_filers_own_words_cut_at_word_boundaries():
    filler = " ".join(f"word{i}" for i in range(120))
    text = f"Preamble ends here. {filler} the Reporting Person holds a convertible note {filler} done. Next."
    c = _scan(text)
    q = c.quote
    assert c.note is None and len(q) <= S.MAX_QUOTE_CHARS
    assert "convertible" in q and q.startswith("\u2026") and q.endswith("\u2026")
    inner = q.strip("\u2026")
    assert inner in " ".join(text.split())
    assert "Preamble" not in q and "Next" not in q
    # no word is cut in half
    words = set(" ".join(text.split()).split())
    assert all(w in words for w in inner.split())


def test_a_short_sentence_is_quoted_whole_and_abbreviations_do_not_end_it():
    text = "First sentence. Nebius Group N.V. granted Holdings Inc. a convertible note. Last one."
    assert _scan(text).quote == "Nebius Group N.V. granted Holdings Inc. a convertible note."
    text = "Line one.\n\nA convertible note\nis held.\n\nOther paragraph."
    assert _scan(text).quote == "A convertible note is held."


@pytest.mark.parametrize("n, want", [
    (0, "0"), (999_999, "999,999"), (750_000, "750,000"), (1_000_000, "1.0M"), (21_065_936, "21.1M"),
    (41_011_803, "41.0M"), (3_749_999, "3.7M"), (3_750_000, "3.8M"), (999_949_999, "999.9M"),
    (999_950_000, "1.0B"), (1_234_567_890, "1.2B"),
])
def test_share_counts_are_formatted_like_the_seed(n, want):
    assert S.format_share_count(n) == want


def test_the_basis_with_its_note_stays_inside_the_60_character_column():
    longest = "of ordinary shares, incl. " + S.format_share_count(999_949_999) + " warrant shares"
    assert len(longest) <= 60


# ══════════════════════════════════════════════════════════════════════════════════════
# 4. Purity and time
# ══════════════════════════════════════════════════════════════════════════════════════

_MODULE = BACKEND / "app" / "services" / "trillion_club" / "stake_schedule.py"


def test_the_module_stays_pure():
    """Run inside a worker thread on SEC text: stdlib + the two contract modules only — no
    Supabase, no HTTP client, no settings, no LLM, no FMP."""
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    modules = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    modules |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert modules <= {
        "__future__", "re", "xml.etree.ElementTree", "bisect", "dataclasses", "datetime",
        "decimal", "typing",
        "app.services.trillion_club.stake_contracts", "app.services.trillion_club.stake_names",
    }, modules


def _patterns() -> List[Tuple[str, "re.Pattern[str]"]]:
    out = []
    for name, val in vars(S).items():
        if isinstance(val, re.Pattern):
            out.append((name, val))
        elif isinstance(val, tuple):
            out += [(f"{name}[{i}]", x) for i, x in enumerate(val) if isinstance(x, re.Pattern)]
    return out


def _cpu_pair(small: Callable[[], object], big: Callable[[], object], rounds: int = 3) -> Tuple[float, float]:
    was = gc.isenabled()
    gc.disable()
    try:
        ts = tb = float("inf")
        for _ in range(rounds):
            t0 = time.thread_time()
            small()
            ts = min(ts, time.thread_time() - t0)
            t0 = time.thread_time()
            big()
            tb = min(tb, time.thread_time() - t0)
        return ts, tb
    finally:
        if was:
            gc.enable()


#: Degenerate runs: digits and groups (the counts), share nouns, the exemption and gap
#: words, markup and whitespace.
_UNITS = ("a.", "9,", "1,000,000 ", "0", "warrant ", "shares ", "exercise of ", "underwriters' ",
          "class a ", "<!", " ", "\n", "a-", "of the ", "issuable upon exercise of a ", "N.V. ",
          "1,000 shares of common stock that are issuable upon the exercise of ",
          # the review S1-2 / S1-4 additions: the exchange, right and series forms
          "upon the redemption or exchange of ", "right to receive, or the power to direct the receipt of, ",
          "rights to ", "restricted share ", "series 1 ", "conver",
          # review A (S1-LONE-SHARED-POWER): the disclaimer, holder and agreement forms
          "disclaims ", "beneficial ownership ", "held directly by ", "owned of record by ", "as a result of ",
          "solely because ", "by reason of the ", "agreement ", "except to the extent of ", "pecuniary interest ",
          ", a ", " and ", "the other reporting persons ", "L.P. ")
_LADDER = (3_000, 12_000)
_RATIO_LIMIT = 8.0       # linear grows ~4x across the rung, quadratic ~16x
_FLOOR = 0.002
_CEILING_CHARS = 300_000
_CEILING_SECONDS = 0.5


def _linear(rx: "re.Pattern[str]", unit: str) -> bool:
    small = (unit * _LADDER[1])[:_LADDER[0]]
    big = (unit * _LADDER[1])[:_LADDER[1]]
    for _attempt in range(2):            # a scheduler hiccup does not repeat; a quadratic does
        ts, tb = _cpu_pair(lambda: list(rx.finditer(small)), lambda: list(rx.finditer(big)), rounds=2)
        ratio = tb / max(ts, 1e-6)
        if tb < _FLOOR or ratio < _RATIO_LIMIT:
            return True
        if ratio > 2 * _RATIO_LIMIT:
            return False                 # unmistakable: no second measurement needed
    return False


def _timed(rx: "re.Pattern[str]", unit: str, n: int) -> float:
    text = (unit * (n // len(unit) + 1))[:n]
    t0 = time.thread_time()
    list(rx.finditer(text))
    return time.thread_time() - t0


def test_every_compiled_pattern_is_linear():
    pats = _patterns()
    assert len(pats) >= 38, [n for n, _ in pats]
    assert any(n.startswith("_EXEMPT_RES[") for n, _ in pats)
    assert any(n.startswith("_WARRANT_COUNT_RES[") for n, _ in pats)
    # the patterns the review fixes added or widened are swept too
    assert {"_DERIVATIVE_RE", "_WARRANT_WORD_RE", "_SERIES_RE", "_TWO_CLASSES_RE",
            "_EXEMPT_RES[7]"} <= {n for n, _ in pats}
    # review A (S1-LONE-SHARED-POWER): the disclaimer scan's patterns
    assert {"_DISCLAIM_RE", "_OWNERSHIP_WORD_RE", "_PECUNIARY_RE", "_HELD_BY_RE", "_HOLDER_STOP_RE",
            "_HOLDER_SPLIT_RE", "_AGREEMENT_CAUSE_RE"} <= {n for n, _ in pats}
    slow = [(name, unit) for name, rx in pats for unit in _UNITS if not _linear(rx, unit)]
    assert slow == [], slow
    # Then an absolute ceiling, in two steps so a pattern the ratio missed fails in seconds
    # (a quadratic at 30,000 characters) instead of hanging the suite at 300,000.
    for name, rx in pats:
        for unit in _UNITS:
            assert _timed(rx, unit, _CEILING_CHARS // 10) < _CEILING_SECONDS / 10, (name, unit)
            assert _timed(rx, unit, _CEILING_CHARS) < _CEILING_SECONDS, (name, unit)


def test_the_linearity_check_is_not_vacuous():
    """A pattern retried from every position of a run (quadratic) must fail the check that
    every module pattern passes; a plain linear one must pass it."""
    assert not _linear(re.compile(r"(?:a|a\.)*\.x"), "a.")
    assert _linear(re.compile(r"a\.x"), "a.")


@pytest.mark.parametrize("unit", [
    "1,000,000 shares issuable upon exercise of ", "warrants to purchase ", "underwriters' option ",
    "exercise of the underwriters' ", "convertible ", "excluding ", ". ", "N.V. ", "a" * 50 + " ",
    "the right to receive dividends ", "upon the redemption or exchange of ",
    # review A (S1-LONE-SHARED-POWER): the disclaimer scan's anchors and windows
    "disclaims beneficial ownership of the shares held by ", "disclaims membership in a group and ",
    "beneficial ownership is disclaimed ", "held by Google LLC and ", "as a result of the support ",
    "except to the extent of its pecuniary interest ", "disclaims ", "; ",
])
def test_a_near_cap_comment_is_judged_in_bounded_time(unit):
    filler = S.MAX_SCHEDULE_XML_CHARS - len(_g13()) - 100
    comment = (unit * (filler // len(unit) + 1))[:filler]
    xml = _g13(_line_13g("NVIDIA Corporation", comments=comment))
    t0 = time.thread_time()
    r = _select(xml)
    assert time.thread_time() - t0 < 3.0, unit
    assert r.reason in (None, "derivative_position", "ownership_disclaimed")
    if r.derivative_quote is not None:
        assert len(r.derivative_quote) <= S.MAX_QUOTE_CHARS
    if r.disclaimer_quote is not None:
        assert len(r.disclaimer_quote) <= S.MAX_QUOTE_CHARS
