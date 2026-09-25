"""Trillion-Dollar Club Bets — the backend ⇄ iOS contract, parsed AND executed.

WHY THIS FILE EXISTS
--------------------
`HomeDashboardResponse.trillion_club` rides inside the Home dashboard, and
`GET /home/trillion-club/{slug}` feeds the detail screen. The iOS `APIClient` does not
`convertFromSnakeCase`, so every wire key is spelled out in a Swift `CodingKeys` enum: a drift
is a decode failure in production. Two halves, and neither is enough alone:

1. **Parsed parity** — the Swift source of `Models/TrillionClubModels.swift` is scanned
   (comments stripped, string literals lifted out, each struct brace-bounded, keys taken from
   its OWN `CodingKeys`) and compared field-for-field and type-for-type with
   `app/schemas/trillion_club.py`. Same approach as `test_theme_rotation_contract.py`; the
   scanner is copied rather than imported so this contract does not break when that file's
   unrelated imports do. Every Swift property must be Optional (the section is additive).
2. **Executed decode** — there is no XCTest target, so the models file (Foundation-only on
   purpose) is piped through `xcrun swift -` with a harness that decodes payloads the REAL
   Pydantic models dump, plus malformed ones: a group that is not an object, elements of the
   wrong type, unknown enum strings, unsourced stakes, impossible dates, negative counts. The
   one invariant that matters most is asserted directly: a malformed `trillion_club` can never
   fail the Home dashboard decode it rides in.

Hermetic: no network, no Supabase. The executed half skips (never fails) on a host without
`xcrun`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from pydantic import BaseModel

from app.schemas import trillion_club as tc
from app.schemas.home_dashboard import HomeDashboardResponse
from app.schemas.trillion_club import (
    ClubChangeCountsResponse,
    ClubChangeResponse,
    ClubHistoryPointResponse,
    ClubHoldingResponse,
    ClubMemberBriefResponse,
    ClubStakeResponse,
    TrillionClubCompanyResponse,
    TrillionClubDetailResponse,
    TrillionClubGroupResponse,
)

REPO = Path(__file__).resolve().parents[2]
IOS = REPO / "frontend" / "ios" / "ios"
MODELS_SWIFT = IOS / "Models" / "TrillionClubModels.swift"
HOME_MODELS_SWIFT = IOS / "Models" / "HomeDashboardModels.swift"


# ══════════════════════════════════════════════════════════════════════════════════════
# Swift source scanner — comments removed, string literals lifted out, structs bounded
# (copied from test_theme_rotation_contract.py; the parser tests below re-prove it here)
# ══════════════════════════════════════════════════════════════════════════════════════


def _skip_interpolation(src: str, i: int) -> int:
    depth = 1
    n = len(src)
    while i < n:
        c = src[i]
        if c == '"':
            _, i = _read_string(src, i)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unterminated string interpolation")


def _read_string(src: str, i: int) -> Tuple[str, int]:
    hashes = 0
    while src[i] == "#":
        hashes += 1
        i += 1
    if src.startswith('"""', i):
        close = '"""' + "#" * hashes
        j = src.find(close, i + 3)
        if j == -1:
            raise ValueError("unterminated multi-line string")
        return src[i + 3:j], j + len(close)
    close = '"' + "#" * hashes
    i += 1
    start = i
    n = len(src)
    while i < n:
        if src[i] == "\\" and hashes == 0:
            if src.startswith("\\(", i):
                i = _skip_interpolation(src, i + 2)
                continue
            i += 2
            continue
        if src[i] == "\n":
            raise ValueError("unterminated string literal")
        if src.startswith(close, i):
            return src[start:i], i + len(close)
        i += 1
    raise ValueError("unterminated string literal")


def scan_swift(src: str) -> Tuple[str, List[str]]:
    """(code, strings): comments removed (Swift block comments NEST), every string literal
    replaced by `"__S<i>__"` whose content is `strings[i]`."""
    out: List[str] = []
    strings: List[str] = []
    i, n = 0, len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        if src.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if src.startswith("/*", i):
                    depth += 1
                    i += 2
                elif src.startswith("*/", i):
                    depth -= 1
                    i += 2
                else:
                    i += 1
            if depth:
                raise ValueError("unterminated block comment")
            out.append(" ")
            continue
        c = src[i]
        if c == '"' or src.startswith('#"', i):
            content, i = _read_string(src, i)
            out.append(f'"__S{len(strings)}__"')
            strings.append(content)
            continue
        out.append(c)
        i += 1
    return "".join(out), strings


def match_brace(code: str, start: int) -> int:
    """`start` is just past an opening `{`; returns the index of its matching `}`."""
    depth = 1
    for i in range(start, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced braces")


def _flatten(body: str) -> str:
    out: List[str] = []
    depth = 0
    for ch in body:
        if ch == "{":
            if depth == 0:
                out.append("{")
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                out.append("}")
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def type_body(code: str, name: str, kind: str = "struct") -> str:
    """The body of exactly ONE `<kind> <name>` (word-bounded) in the scanned code."""
    matches = list(re.finditer(rf"\b{kind}\s+{re.escape(name)}\b[^{{]*\{{", code))
    assert len(matches) == 1, f"expected exactly one `{kind} {name}`, found {len(matches)}"
    start = matches[0].end()
    return code[start:match_brace(code, start)]


_MODIFIERS = r"(?:(?:@\w+(?:\([^)]*\))?|private|fileprivate|internal|public|open|lazy|weak|final|nonisolated)(?:\(set\))?\s+)*"
_DECL_START = re.compile(rf"^\s*{_MODIFIERS}(?:static\s+|class\s+)?(?:let|var)\b")
_STORED = re.compile(
    rf"^\s*{_MODIFIERS}(?P<static>static\s+|class\s+)?(?P<kw>let|var)\s+(?P<name>\w+)\s*:\s*"
    r"(?P<type>[^=]+?)\s*(?P<init>=.*)?$"
)


@dataclass(frozen=True)
class SwiftProp:
    name: str
    type: str
    kw: str
    has_init: bool

    @property
    def optional(self) -> bool:
        return self.type.endswith("?")

    @property
    def decoded(self) -> bool:
        return self.kw == "var" or not self.has_init


@dataclass(frozen=True)
class SwiftStruct:
    name: str
    props: Dict[str, SwiftProp]
    coding_keys: Optional[Dict[str, str]]

    def wire_keys(self) -> Dict[str, str]:
        if self.coding_keys is not None:
            return dict(self.coding_keys)
        return {p.name: p.name for p in self.props.values() if p.decoded}


def parse_struct(code: str, strings: List[str], name: str) -> SwiftStruct:
    body = type_body(code, name)
    flat = _flatten(body)
    props: Dict[str, SwiftProp] = {}
    for line in flat.splitlines():
        if not _DECL_START.match(line):
            continue
        if re.match(rf"^\s*{_MODIFIERS}(?:static|class)\s", line):
            continue
        brace, eq = line.find("{"), line.find("=")
        if brace != -1 and (eq == -1 or brace < eq):
            continue
        m = _STORED.match(line.replace("{}", ""))
        if m is None:
            raise ValueError(f"{name}: unparseable declaration {line.strip()!r}")
        if m.group("static"):
            continue
        typ = m.group("type").strip()
        if "," in typ:
            raise ValueError(f"{name}: multi-binding declaration {line.strip()!r}")
        props[m.group("name")] = SwiftProp(m.group("name"), typ, m.group("kw"),
                                           m.group("init") is not None)

    coding_keys: Optional[Dict[str, str]] = None
    ck = [m for m in re.finditer(r"\benum\s+CodingKeys\b[^{]*\{", body)
          if _flatten(body[:m.start()]).count("{") == _flatten(body[:m.start()]).count("}")]
    if ck:
        assert len(ck) == 1, f"{name}: more than one CodingKeys"
        start = ck[0].end()
        ck_body = _flatten(body[start:match_brace(body, start)])
        coding_keys = {}
        for stmt in re.findall(r"\bcase\s+([^\n;]+)", ck_body):
            for item in stmt.split(","):
                item = item.strip()
                mm = re.fullmatch(r'(\w+)(?:\s*=\s*"__S(\d+)__")?', item)
                if mm is None:
                    raise ValueError(f"{name}: unparseable CodingKeys case {item!r}")
                case = mm.group(1)
                assert case not in coding_keys, f"{name}: duplicate CodingKeys case {case}"
                coding_keys[case] = strings[int(mm.group(2))] if mm.group(2) else case
    return SwiftStruct(name, props, coding_keys)


def enum_raw_values(code: str, strings: List[str], name: str) -> Dict[str, str]:
    """{case: raw value} for a `String` raw-value enum — a bare `case x` has raw value "x"."""
    body = _flatten(type_body(code, name, kind="enum"))
    out: Dict[str, str] = {}
    for stmt in re.findall(r"^\s*case\s+([^\n]+)$", body, flags=re.MULTILINE):
        for item in stmt.split(","):
            item = item.strip()
            mm = re.fullmatch(r'(\w+)(?:\s*=\s*"__S(\d+)__")?', item)
            if mm is None:
                raise ValueError(f"{name}: unparseable enum case {item!r}")
            out[mm.group(1)] = strings[int(mm.group(2))] if mm.group(2) else mm.group(1)
    return out


def _models_source() -> str:
    if not MODELS_SWIFT.is_file():
        pytest.skip(f"{MODELS_SWIFT} not present")
    return MODELS_SWIFT.read_text(encoding="utf-8")


def _scanned(src: Optional[str] = None) -> Tuple[str, List[str]]:
    return scan_swift(_models_source() if src is None else src)


# ══════════════════════════════════════════════════════════════════════════════════════
# 0. The parser itself — proven non-vacuous before anything leans on it
# ══════════════════════════════════════════════════════════════════════════════════════

_SYNTHETIC = r'''
// nonisolated struct GhostDTO: Codable { let ghost: Int? }
/* outer /* nested */ let hidden: Int */
nonisolated struct FakeDTO: Codable, Sendable {
    let slug: String?
    /// a doc comment with a brace { and case bogus = "bogus"
    let imageUrl: String?   // "trailing { quote"
    enum CodingKeys: String, CodingKey {
        case slug
        // case commented = "commented_out"
        case imageUrl = "image_url"
    }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let inner: Int = 1
        slug = "\(inner > 0 ? "a" : "b")"
        imageUrl = nil
    }
}
nonisolated enum FakeKind: String, CaseIterable {
    case one = "one_wire"
    case two
    case unknown
    init(wire: String?) { self = .unknown }
}
'''


def test_scanner_strips_comments_and_lifts_strings():
    code, strings = scan_swift(_SYNTHETIC)
    assert "ghost" not in code and "hidden" not in code and "bogus" not in code
    assert "commented_out" not in code and "commented_out" not in strings
    assert "trailing { quote" not in code


def test_parser_reads_own_coding_keys_and_skips_the_init_body():
    code, strings = scan_swift(_SYNTHETIC)
    s = parse_struct(code, strings, "FakeDTO")
    assert s.coding_keys == {"slug": "slug", "imageUrl": "image_url"}
    # `let c` / `let inner` live inside init(from:) — never stored properties.
    assert set(s.props) == {"slug", "imageUrl"}
    assert s.props["slug"].optional


def test_enum_parser_reads_raw_values():
    code, strings = scan_swift(_SYNTHETIC)
    assert enum_raw_values(code, strings, "FakeKind") == {
        "one": "one_wire", "two": "two", "unknown": "unknown"}


def test_parser_refuses_ambiguous_or_missing_struct():
    code, strings = scan_swift("struct A {}\nstruct A {}\n")
    with pytest.raises(AssertionError):
        parse_struct(code, strings, "A")
    with pytest.raises(AssertionError):
        parse_struct(code, strings, "Missing")


# ══════════════════════════════════════════════════════════════════════════════════════
# 1. Swift ⇄ Pydantic parity, per struct
# ══════════════════════════════════════════════════════════════════════════════════════

PAIRS: List[Tuple[str, type]] = [
    ("ClubChangeCountsDTO", ClubChangeCountsResponse),
    ("ClubHoldingDTO", ClubHoldingResponse),
    ("ClubChangeDTO", ClubChangeResponse),
    ("ClubStakeDTO", ClubStakeResponse),
    ("ClubMemberBriefDTO", ClubMemberBriefResponse),
    ("ClubHistoryPointDTO", ClubHistoryPointResponse),
    ("TrillionClubCompanyDTO", TrillionClubCompanyResponse),
    ("TrillionClubGroupDTO", TrillionClubGroupResponse),
    ("TrillionClubDetailDTO", TrillionClubDetailResponse),
]
_IDS = [p[0] for p in PAIRS]
_MODEL_TO_DTO = {model.__name__: dto for dto, model in PAIRS}
_SWIFT_SCALARS = {str: "String", int: "Int", float: "Double", bool: "Bool"}


def _py_shape(annotation: Any) -> Tuple[Any, bool]:
    nullable = False
    if typing.get_origin(annotation) is typing.Union:
        args = typing.get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        assert len(non_none) == 1, f"unsupported union {annotation!r}"
        nullable = len(non_none) < len(args)
        annotation = non_none[0]
    if typing.get_origin(annotation) in (list, List):
        (inner,) = typing.get_args(annotation)
        inner_shape, inner_nullable = _py_shape(inner)
        assert not inner_nullable, f"nullable list element: {annotation!r}"
        return ("list", inner_shape), nullable
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _MODEL_TO_DTO[annotation.__name__], nullable
    return _SWIFT_SCALARS[annotation], nullable


def _swift_shape(swift_type: str) -> Tuple[Any, bool]:
    t = swift_type.strip()
    optional = t.endswith("?")
    t = t.rstrip("?").strip()
    if t.startswith("[") and t.endswith("]"):
        inner = t[1:-1].strip()
        assert not inner.endswith("?"), f"optional element type {swift_type!r}"
        return ("list", inner), optional
    return t, optional


def _struct(dto: str, src: Optional[str] = None) -> SwiftStruct:
    code, strings = _scanned(src)
    return parse_struct(code, strings, dto)


@pytest.mark.parametrize("dto,model", PAIRS, ids=_IDS)
def test_swift_wire_keys_equal_pydantic_fields(dto, model):
    wire = set(_struct(dto).wire_keys().values())
    fields = set(model.model_fields)
    assert wire == fields, (
        f"{dto} decodes {sorted(wire - fields)} that {model.__name__} never sends, and "
        f"{model.__name__} sends {sorted(fields - wire)} that {dto} ignores")


@pytest.mark.parametrize("dto,model", PAIRS, ids=_IDS)
def test_coding_keys_cover_exactly_the_stored_properties(dto, model):
    swift = _struct(dto)
    assert swift.coding_keys is not None, f"{dto}: no CodingKeys — the wire keys are implicit"
    decoded = {p.name for p in swift.props.values() if p.decoded}
    assert decoded, f"{dto}: parsed no stored properties — the parser went vacuous"
    assert set(swift.coding_keys) == decoded, (
        f"{dto}: CodingKeys {sorted(swift.coding_keys)} vs stored {sorted(decoded)}")
    for key in swift.wire_keys().values():
        assert re.fullmatch(r"[a-z][a-z0-9_]*", key), f"{dto}: non-snake wire key {key!r}"


@pytest.mark.parametrize("dto,model", PAIRS, ids=_IDS)
def test_swift_property_types_match_pydantic_types(dto, model):
    swift = _struct(dto)
    for prop_name, key in swift.wire_keys().items():
        s_shape, _ = _swift_shape(swift.props[prop_name].type)
        p_shape, _ = _py_shape(model.model_fields[key].annotation)
        assert s_shape == p_shape, f"{dto}.{prop_name} is {s_shape}, {model.__name__}.{key} is {p_shape}"


@pytest.mark.parametrize("dto,model", PAIRS, ids=_IDS)
def test_every_swift_property_is_optional(dto, model):
    """The section is additive: an older backend, a failed build, a field the backend drops
    tomorrow — every one must decode as nil, never as a thrown `keyNotFound`."""
    swift = _struct(dto)
    required = [p.name for p in swift.props.values() if p.decoded and not p.optional]
    assert not required, f"{dto} has non-Optional properties {required}"


@pytest.mark.parametrize("dto,model", PAIRS, ids=_IDS)
def test_every_dto_decodes_field_by_field(dto, model):
    """Each DTO has its own `init(from:)` that decodes every CodingKeys case through
    `ClubDecode` — so one wrong-typed field is nil, not a failed card."""
    code, strings = _scanned()
    body = type_body(code, dto)
    m = re.search(r"init\(from decoder: Decoder\) throws \{", body)
    assert m, f"{dto} has no custom init(from:) — a wrong type would fail the whole element"
    init_body = body[m.end():match_brace(body, m.end())]
    for case in _struct(dto).coding_keys:
        assert re.search(rf"\b{case}\s*=\s*ClubDecode\.(field|list)\(c,\s*\.{case}\)", init_body), (
            f"{dto}.{case} is not decoded through ClubDecode")


def test_the_group_decoder_cannot_throw():
    """The group rides inside the Home dashboard — a `try` that can escape its init would
    blank EVERY Home section on one malformed field."""
    code, _ = _scanned()
    body = type_body(code, "TrillionClubGroupDTO")
    m = re.search(r"init\(from decoder: Decoder\) throws \{", body)
    assert m
    init_body = body[m.end():match_brace(body, m.end())]
    assert "try?" in init_body and re.search(r"\btry\b(?!\?)", init_body) is None, (
        "TrillionClubGroupDTO.init(from:) contains a throwing `try` — a malformed group would "
        "fail the whole Home decode")


# ── mutation tests of the parity checks above ────────────────────────────────────────


def test_parity_catches_a_mutated_coding_key():
    src = _models_source()
    anchor = 'case newlyListed = "newly_listed"'
    assert src.count(anchor) == 2, "anchor moved — update the mutation"
    mutated = src.replace(anchor, 'case newlyListed = "newlyListed"', 1)
    keys = set(_struct("ClubHoldingDTO", mutated).wire_keys().values())
    assert keys != set(ClubHoldingResponse.model_fields)


def test_optionality_check_catches_a_non_optional_property():
    src = _models_source()
    anchor = "    let investeeName: String?\n"
    assert anchor in src
    mutated = src.replace(anchor, "    let investeeName: String\n")
    swift = _struct("ClubStakeDTO", mutated)
    assert swift.props["investeeName"].optional is False


def test_type_check_catches_a_mutated_type():
    src = _models_source()
    anchor = "    let positionCount: Int?\n"
    at = src.index("struct ClubHistoryPointDTO")
    hit = src.index(anchor, at)
    mutated = src[:hit] + "    let positionCount: Double?\n" + src[hit + len(anchor):]
    swift = _struct("ClubHistoryPointDTO", mutated)
    s_shape, _ = _swift_shape(swift.props["positionCount"].type)
    p_shape, _ = _py_shape(ClubHistoryPointResponse.model_fields["position_count"].annotation)
    assert s_shape != p_shape


def test_a_commented_out_key_does_not_count():
    src = _models_source()
    anchor = '        case lockedHoldingsCount = "locked_holdings_count"\n'
    assert anchor in src
    mutated = src.replace(anchor, '        // case lockedHoldingsCount = "locked_holdings_count"\n')
    assert "lockedHoldingsCount" not in _struct("TrillionClubDetailDTO", mutated).coding_keys


# ══════════════════════════════════════════════════════════════════════════════════════
# 2. Enum raw values ⇄ backend constants
# ══════════════════════════════════════════════════════════════════════════════════════

_ENUMS = [
    ("ClubCardKind", set(tc.CARD_KINDS)),
    ("ClubChangeKind", set(tc.CHANGE_KINDS)),
    ("ClubStakeKind", set(tc.STAKE_KINDS)),
    ("ClubValueBasis", set(tc.VALUE_BASES)),
    ("ClubComparison", {tc.COMPARISON_QUARTER, tc.COMPARISON_FIRST_FILING, tc.COMPARISON_GAP}),
    ("ClubNotice", set(tc.NOTICE_KINDS)),
]


@pytest.mark.parametrize("enum,backend", _ENUMS, ids=[e[0] for e in _ENUMS])
def test_enum_raw_values_match_backend_constants(enum, backend):
    code, strings = _scanned()
    raw = enum_raw_values(code, strings, enum)
    assert raw.get("unknown") == "unknown", f"{enum} has no .unknown fallback case"
    wire = set(raw.values()) - {"unknown"}
    assert wire == backend, (
        f"{enum}: iOS knows {sorted(wire - backend)} the backend never sends, and the backend "
        f"sends {sorted(backend - wire)} iOS would hide as unknown")
    assert "unknown" not in backend, "the backend must never send the iOS fallback sentinel"


def test_enum_parity_catches_a_mutated_raw_value():
    src = _models_source()
    anchor = 'case committedUpTo = "committed_up_to"'
    assert anchor in src
    code, strings = scan_swift(src.replace(anchor, 'case committedUpTo = "committed"'))
    assert set(enum_raw_values(code, strings, "ClubValueBasis").values()) - {"unknown"} != set(tc.VALUE_BASES)


# ══════════════════════════════════════════════════════════════════════════════════════
# 3. The Home dashboard carries the group
# ══════════════════════════════════════════════════════════════════════════════════════


def test_home_dashboard_dto_decodes_trillion_club_optionally():
    src = HOME_MODELS_SWIFT.read_text(encoding="utf-8")
    code, strings = scan_swift(src)
    home = parse_struct(code, strings, "HomeDashboardResponseDTO")
    assert home.coding_keys.get("trillionClub") == "trillion_club"
    assert home.props["trillionClub"].type == "TrillionClubGroupDTO?"
    field = HomeDashboardResponse.model_fields["trillion_club"]
    assert field.annotation is TrillionClubGroupResponse
    assert not field.is_required()


def test_home_dashboard_data_carries_the_presentation_group():
    code, _ = scan_swift(HOME_MODELS_SWIFT.read_text(encoding="utf-8"))
    body = _flatten(type_body(code, "HomeDashboardData"))
    assert re.search(r"\bvar trillionClub: TrillionClubGroup = \.empty\b", body)


def test_default_dashboard_dump_is_an_empty_group():
    resp = HomeDashboardResponse(market_status_text="Markets Closed", market_is_open=False, pulse=[])
    dumped = json.loads(json.dumps(resp.model_dump(), allow_nan=False))
    assert dumped["trillion_club"] == {"companies": [], "also_in_club": []}


# ══════════════════════════════════════════════════════════════════════════════════════
# 4. EXECUTED: the real Swift decoder + mapper over real and malformed payloads
# ══════════════════════════════════════════════════════════════════════════════════════


def _full_company(**over: Any) -> TrillionClubCompanyResponse:
    base = dict(
        slug="nvidia", name="NVIDIA", card_kind=tc.CARD_THIRTEEN_F, logo_symbol="NVDA",
        detail_symbol="NVDA", market_cap=5.445e12, market_cap_as_of="2026-09-23",
        period="2026-Q2", period_end="2026-06-30", filed_on="2026-08-14",
        amended_on=None, next_due="2026-11-16", position_count=8, total_value=63_439_974_569.0,
        top_holdings=[
            ClubHoldingResponse(name="Intel", symbol="INTC", weight=0.4727, shares=214_776_632.0,
                                value=29_989_261_126.0, change=tc.CHANGE_UNCHANGED),
            ClubHoldingResponse(name="SpaceX", symbol="SPCX", weight=0.3307, shares=122_764_805.0,
                                value=20.98e9, change=tc.CHANGE_NEWLY_REPORTED, newly_listed=True,
                                club_member_slug="spacex"),
            ClubHoldingResponse(name="CoreWeave", symbol="CRWV", weight=0.0741, shares=47_213_353.0,
                                value=4.7e9, change=tc.CHANGE_UNCHANGED),
        ],
        change_counts=ClubChangeCountsResponse(newly_reported=1, unchanged=7),
        comparison=tc.COMPARISON_QUARTER, prev_period="2026-Q1",
        stakes=[ClubStakeResponse(
            investee_name="Anthropic", kind="commitment", disclosed_value=1e10,
            value_basis="committed_up_to", as_of="2025-11-18",
            source_title="Microsoft, NVIDIA and Anthropic announcement",
            source_url="https://blogs.microsoft.com/x", tied_to_deal=True, verified_on="2026-09-24")],
        stake_count=3,
    )
    base.update(over)
    return TrillionClubCompanyResponse(**base)


def _dump(model: BaseModel) -> str:
    # Starlette's JSONResponse is allow_nan=False — the same bytes the app would send.
    return json.dumps(model.model_dump(), allow_nan=False)


def _swift_literal(s: str) -> str:
    assert '"##' not in s
    return '##"""\n' + s + '\n"""##'


def _payloads() -> Dict[str, str]:
    group_full = TrillionClubGroupResponse(
        companies=[
            _full_company(),
            _full_company(slug="microsoft", name="Microsoft", card_kind=tc.CARD_NO_THIRTEEN_F,
                          logo_symbol="MSFT", period=None, period_end=None, filed_on=None,
                          next_due=None, position_count=None, total_value=None, top_holdings=[],
                          change_counts=None, comparison=None, prev_period=None,
                          whale_id="should-be-ignored"),
            _full_company(slug="berkshire", name="Berkshire Hathaway", card_kind=tc.CARD_WHALE_LINK,
                          whale_id="warren-buffett", top_holdings=[], position_count=None),
        ],
        also_in_club=[ClubMemberBriefResponse(slug="broadcom", name="Broadcom")],
    )
    detail_full = TrillionClubDetailResponse(
        company=_full_company(),
        holdings=list(_full_company().top_holdings),
        changes=[
            ClubChangeResponse(name="SpaceX", symbol="SPCX", change=tc.CHANGE_NEWLY_REPORTED,
                               newly_listed=True, shares=122_764_805.0),
            ClubChangeResponse(name="Marvell", symbol="MRVL", change=tc.CHANGE_NO_LONGER_REPORTED,
                               prev_shares=396_352.0),
            ClubChangeResponse(name="Rivian", symbol="RIVN", change=tc.CHANGE_INCREASED,
                               shares=5_200_000.0, prev_shares=4_800_000.0, share_change=400_000.0),
        ],
        stakes=list(_full_company().stakes),
        history=[ClubHistoryPointResponse(period="2026-Q1", period_end="2026-03-31",
                                          total_value=18.37e9, position_count=8)],
        is_locked=True, tier_required="pro", locked_holdings_count=5, locked_history_count=2,
        other_members=[ClubMemberBriefResponse(slug="micron", name="Micron")],
    )
    return {
        "GROUP_FULL": _dump(group_full),
        "GROUP_EMPTY": _dump(TrillionClubGroupResponse()),
        "DETAIL_FULL": _dump(detail_full),
        "DETAIL_MIN": _dump(TrillionClubDetailResponse(company=TrillionClubCompanyResponse(
            slug="tsmc", name="TSMC", card_kind=tc.CARD_NON_US))),
        "HOME_DEFAULT": json.dumps({"other": 1, **{"trillion_club": json.loads(_dump(TrillionClubGroupResponse()))}}),
    }


HARNESS = r'''
var failures = 0
var checks = 0
func check(_ name: String, _ got: String?, _ expect: String?) {
    checks += 1
    if got == expect { print("ok|\(name)") }
    else { failures += 1; print("FAIL|\(name)|got=\(String(describing: got))|expect=\(String(describing: expect))") }
}
func checkTrue(_ name: String, _ cond: Bool) { check(name, String(cond), "true") }
func group(_ json: String) -> TrillionClubGroup {
    TrillionClubGroup(dto: try? JSONDecoder().decode(TrillionClubGroupDTO.self, from: Data(json.utf8)))
}
func detail(_ json: String) -> TrillionClubDetail? {
    guard let dto = try? JSONDecoder().decode(TrillionClubDetailDTO.self, from: Data(json.utf8)) else { return nil }
    return TrillionClubDetail(dto: dto)
}
/// Stands in for `HomeDashboardResponseDTO`: a sibling field plus the group, decoded the
/// synthesized way (`decodeIfPresent`) — exactly how the real DTO decodes it.
struct Home: Decodable {
    let other: Int
    let trillionClub: TrillionClubGroupDTO?
    enum CodingKeys: String, CodingKey { case other; case trillionClub = "trillion_club" }
}
func home(_ json: String) -> Home? { try? JSONDecoder().decode(Home.self, from: Data(json.utf8)) }

// ── 1. What the real Pydantic models dump decodes and maps ──────────────────
let full = group(GROUP_FULL)
check("full.count", String(full.companies.count), "3")
check("full.also", full.alsoInClub.map(\.name).joined(separator: ","), "Broadcom")
let nv = full.companies.first
check("nv.kind", nv?.kind.rawValue, "thirteen_f")
check("nv.stat", nv?.holdingsStatLine, "8 U.S.-listed holdings · $63.4B reported")
check("nv.change", nv?.changeLine, "vs Q1: 1 newly reported")
check("nv.dates", nv?.filingDatesLine, "Holdings on Jun 30, 2026 · filed Aug 14, 2026")
check("nv.cap", nv?.marketValueLine, "Market value $5.45T as of Sep 23, 2026")
check("nv.due", nv?.nextDueLine, "Next 13F due by Nov 16, 2026")
check("nv.top", nv?.topHoldings.map { "\($0.name) \($0.weightText ?? "-")" }.joined(separator: " · "),
      "Intel 47% · SpaceX 33% · CoreWeave 7%")
check("nv.spacex.change", nv?.topHoldings[1].change?.pillLabel, "Newly reported")
check("nv.spacex.member", nv?.topHoldings[1].clubMemberSlug, "spacex")
check("nv.stake.figure", nv?.stakes.first?.figureText, "committed up to $10B")
check("nv.stake.source", nv?.stakes.first?.sourceText,
      "per Microsoft, NVIDIA and Anthropic announcement, Nov 18, 2025")
check("nv.stake.chips", nv?.stakes.first?.chips(onThirteenFCard: true).map(\.label).joined(separator: ","),
      "Commitment,Tied to a deal")
check("nv.heading", nv?.stakesHeading, "Also holds stakes its 13F doesn't list")
let ms = full.companies.count > 1 ? full.companies[1] : nil
check("ms.explainer", ms?.explainer,
      "Microsoft doesn't file a 13F, so there's no quarterly list of its U.S.-listed holdings.")
check("ms.whale_ignored", ms?.whaleId, nil)
check("ms.stat_nil", ms?.holdingsStatLine, nil)
let brk = full.companies.count > 2 ? full.companies[2] : nil
check("brk.whale", brk?.whaleId, "warren-buffett")
check("brk.explainer", brk?.explainer,
      "Berkshire Hathaway files 13Fs as an investor — see its full portfolio on its profile.")
checkTrue("empty.group", group(GROUP_EMPTY).isEmpty)

let d = detail(DETAIL_FULL)
checkTrue("detail.decodes", d != nil)
check("detail.locked", d?.lockedHoldingsText, "+5 more holdings")
check("detail.unchanged", d?.unchangedText, "7 holdings unchanged")
check("detail.changes", d?.changes.map { $0.change?.rawValue ?? "-" }.joined(separator: ","),
      "newly_reported,no_longer_reported,increased")
check("detail.newlisted", d?.changes.first?.changeLine, "122.8M shares · first 13F since it began trading")
check("detail.gone", d?.changes.dropFirst().first?.changeLine, "Previously 396,352 shares")
check("detail.more", d?.changes.last?.changeLine, "4.8M → 5.2M shares (+400,000)")
check("detail.history", d?.history.first?.title, "Q1 2026 · Mar 31, 2026")
check("detail.history.detail", d?.history.first?.detail, "8 holdings · $18.4B reported")
check("detail.members", d?.otherMembers.map(\.slug).joined(separator: ","), "micron")
checkTrue("detail.isLocked", d?.isLocked == true)
let dm = detail(DETAIL_MIN)
checkTrue("detail.min", dm != nil && dm!.holdings.isEmpty && dm!.lockedHoldingsText == nil)
check("detail.min.explainer", dm?.company.explainer, "Each stake names its source and date.")
check("detail.history_lock", String(d?.showsHistoryLock ?? false), "true")
check("detail.history_count", d?.lockedHistoryCount.map { String($0) }, "2")
check("nv.more", nv?.moreStakesText(shown: 1), "+2 more in the details")

// ── 2. A malformed group NEVER fails the dashboard it rides in ───────────────
for (name, raw) in [("string", "\"oops\""), ("number", "123"), ("array", "[1,2]"),
                    ("companies_string", "{\"companies\": \"x\"}"),
                    ("companies_objects_of_junk", "{\"companies\": [1, \"a\", null, {}]}")] {
    let h = home("{\"other\": 1, \"trillion_club\": \(raw)}")
    checkTrue("home.survives.\(name)", h?.other == 1)
    checkTrue("home.hidden.\(name)", TrillionClubGroup(dto: h?.trillionClub).isEmpty)
}
checkTrue("home.null", home("{\"other\": 1, \"trillion_club\": null}")?.other == 1)
checkTrue("home.absent", home("{\"other\": 1}")?.trillionClub == nil)
checkTrue("home.default", home(HOME_DEFAULT).map { TrillionClubGroup(dto: $0.trillionClub).isEmpty } == true)

// ── 3. One bad element is dropped; its siblings survive ──────────────────────
let mixed = group(#"""
{"companies": [
  {"slug": "nvidia", "name": "NVIDIA", "card_kind": "thirteen_f",
   "top_holdings": [{"name": "Intel", "weight": "abc", "shares": -5, "value": 1e9},
                    "junk", {"symbol": "COHR"}, {}],
   "change_counts": {"newly_reported": -3, "increased": "two", "decreased": 2},
   "comparison": "quarter", "prev_period": "2025-Q4", "period": "2026-Q1",
   "position_count": "8", "market_cap": -1},
  {"slug": 5, "name": "Bad slug type", "card_kind": "thirteen_f"},
  {"slug": "Upper-Case", "name": "Bad slug", "card_kind": "non_us"},
  {"slug": "nvidia", "name": "Duplicate", "card_kind": "non_us"},
  {"slug": "future", "name": "Future kind", "card_kind": "space_elevator"},
  {"slug": "nameless", "name": "   ", "card_kind": "non_us"},
  {"slug": "aramco", "name": "Saudi Aramco", "card_kind": "non_us", "notice": "latest_not_in",
   "whale_id": "someone", "logo_symbol": "2222 SR"}
 ],
 "also_in_club": [{"slug": "broadcom", "name": "Broadcom"}, {"slug": "broadcom", "name": "Dup"},
                  {"slug": "", "name": "No slug"}, {"slug": "x"}, 7]}
"""#)
check("mixed.slugs", mixed.companies.map(\.slug).joined(separator: ","), "nvidia,aramco")
check("mixed.first_wins", mixed.companies.first?.name, "NVIDIA")
check("mixed.also", mixed.alsoInClub.map(\.slug).joined(separator: ","), "broadcom")
let mnv = mixed.companies.first
check("mixed.holdings", mnv?.topHoldings.map(\.name).joined(separator: ","), "Intel,COHR")
check("mixed.bad_weight", mnv?.topHoldings.first?.weightText, nil)
check("mixed.neg_shares", mnv?.topHoldings.first?.sharesText, nil)
check("mixed.symbol_as_name", mnv?.topHoldings.last?.symbol, "COHR")
check("mixed.counts", mnv?.changeLine, "vs Q4 2025: 2 with fewer shares")
check("mixed.position_count", mnv?.holdingsStatLine, nil)
check("mixed.neg_cap", mnv?.marketValueLine, nil)
let aramco = mixed.companies.last
check("mixed.notice_non13f_hidden", aramco?.noticeText, nil)
check("mixed.whale_non_link", aramco?.whaleId, nil)
check("mixed.bad_logo_symbol", aramco?.logoSymbol, nil)

// ── 4. Stakes need a name, a known kind, a source and a real date ────────────
let stakes = group(#"""
{"companies": [{"slug": "msft", "name": "Microsoft", "card_kind": "no_thirteen_f", "stakes": [
  {"investee_name": "OK", "kind": "private", "ownership_pct": 27, "ownership_basis": "as-converted",
   "as_of": "2025-10-28", "source_title": "Microsoft 10-K", "source_url": "https://x.com/a"},
  {"investee_name": "No source", "kind": "private", "as_of": "2025-10-28"},
  {"investee_name": "Bad date", "kind": "private", "as_of": "2026-02-30", "source_title": "S"},
  {"investee_name": "Future kind", "kind": "crypto_vault", "as_of": "2025-01-01", "source_title": "S"},
  {"investee_name": "Http", "kind": "commitment", "as_of": "2025-01-01", "source_title": "S",
   "source_url": "http://insecure.example.com", "disclosed_value": 5e9, "value_basis": "committed_up_to"},
  {"investee_name": "Unknown basis", "kind": "private", "as_of": "2025-01-01", "source_title": "S",
   "disclosed_value": 5e9, "value_basis": "worth_a_lot", "ownership_pct": 150},
  {"investee_name": "Stale", "kind": "non_us_listed", "as_of": "2025-01-01", "source_title": "S",
   "is_stale": true, "verified_on": "2026-01-02", "listed_since": "2026-06-12"}
]}]}
"""#).companies.first?.stakes ?? []
check("stakes.kept", stakes.map(\.investeeName).joined(separator: ","), "OK,Http,Unknown basis,Stale")
check("stakes.ok.figure", stakes.first?.figureText, "27% as-converted")
checkTrue("stakes.ok.url", stakes.first?.sourceURL != nil)
checkTrue("stakes.http.no_url", stakes.count > 1 && stakes[1].sourceURL == nil)
check("stakes.http.figure", stakes.count > 1 ? stakes[1].figureText : nil, "committed up to $5B")
check("stakes.unknown_basis", stakes.count > 2 ? stakes[2].figureText : nil, nil)
check("stakes.stale", stakes.last?.staleText, "Last checked Jan 2, 2026 — may be out of date.")
check("stakes.listed_off_13f_card", stakes.last?.chips(onThirteenFCard: false).map(\.label).joined(separator: ","),
      "Non-U.S. listed")
check("stakes.listed_on_13f_card", stakes.last?.chips(onThirteenFCard: true).map(\.label).joined(separator: ","),
      "Non-U.S. listed,Listed since Jun 12, 2026 — not on a 13F yet")

// ── 5. Change rows: unchanged and unknown never listed ───────────────────────
let rows = detail(#"""
{"company": {"slug": "amd", "name": "AMD", "card_kind": "thirteen_f", "comparison": "quarter",
             "period": "2026-Q2", "prev_period": "2026-Q1",
             "change_counts": {"corporate_action": 1}},
 "changes": [{"name": "A", "change": "unchanged"}, {"name": "B", "change": "sold_out"},
             {"name": "C", "change": "corporate_action", "shares": 2000000, "prev_shares": 1000000,
              "share_change": 1000000}, {"change": "increased"}],
 "history": [{"period": "2026-Q1"}, {"period": "2026-Q1"}, {"period": "Q1 2026"}, {"period": "2026-Q5"}],
 "locked_holdings_count": -4}
"""#)
check("rows.kept", rows?.changes.map(\.name).joined(separator: ","), "C")
check("rows.corp", rows?.changes.first?.changeLine, "1M → 2M shares (+1M)")
check("rows.quarter", rows?.company.changeLine, "vs Q1: 1 corporate action")
check("rows.history", rows?.history.map(\.period.label).joined(separator: ","), "Q1 2026")
check("rows.locked", rows?.lockedHoldingsText, nil)
checkTrue("detail.no_company", detail(#"{"company": {"slug": "x"}, "holdings": []}"#) == nil)
checkTrue("detail.company_not_object", detail(#"{"company": 3}"#) == nil)

// ── 6. Copy for every card state ─────────────────────────────────────────────
func company(_ json: String) -> TrillionClubCompany? { group("{\"companies\": [\(json)]}").companies.first }
check("copy.first_filing", company(#"{"slug": "a", "name": "A", "card_kind": "thirteen_f", "comparison": "first_filing", "notice": "first_filing"}"#)?.changeLine,
      "First 13F on file — no earlier quarter to compare")
check("copy.first_filing_notice_deduped", company(#"{"slug": "a", "name": "A", "card_kind": "thirteen_f", "comparison": "first_filing", "notice": "first_filing"}"#)?.noticeText, nil)
check("copy.latest_not_in", company(#"{"slug": "a", "name": "A", "card_kind": "thirteen_f", "period": "2026-Q4", "notice": "latest_not_in"}"#)?.noticeText,
      "We haven't received the Q1 2027 filing yet — showing Q4 2026.")
check("copy.amended", company(#"{"slug": "a", "name": "A", "card_kind": "thirteen_f", "notice": "amended", "amended_on": "2026-09-02"}"#)?.noticeText,
      "Includes an amended filing from Sep 2, 2026.")
check("copy.no_newer", company(#"{"slug": "a", "name": "A", "card_kind": "thirteen_f", "period": "2026-Q2", "notice": "no_newer_filing"}"#)?.noticeText,
      "No newer 13F found — the latest on file is Q2 2026.")
check("copy.unknown_notice", company(#"{"slug": "a", "name": "A", "card_kind": "thirteen_f", "notice": "mystery"}"#)?.noticeText, nil)
check("copy.zero_holdings", company(#"{"slug": "a", "name": "A", "card_kind": "thirteen_f", "period": "2026-Q2", "position_count": 0, "total_value": 0}"#)?.holdingsStatLine,
      "No U.S.-listed stock holdings reported for Q2 2026")
check("copy.one_holding", company(#"{"slug": "a", "name": "A", "card_kind": "thirteen_f", "period": "2026-Q2", "position_count": 1}"#)?.holdingsStatLine,
      "1 U.S.-listed holding")
check("copy.no_changes", company(#"{"slug": "a", "name": "A", "card_kind": "thirteen_f", "comparison": "quarter", "change_counts": {"unchanged": 5}}"#)?.changeLine,
      "vs the quarter before: no share-count changes")
check("copy.manual_cap", company(#"{"slug": "a", "name": "A", "card_kind": "non_us", "market_cap": 1660000000000, "market_cap_as_of": "2025-12-31", "cap_is_manual": true}"#)?.marketValueLine,
      "Market value about $1.66T as of Dec 31, 2025")
check("copy.no_cap_date", company(#"{"slug": "a", "name": "A", "card_kind": "non_us", "market_cap": 1660000000000}"#)?.marketValueLine,
      "Market value $1.66T")
check("also.drops_carded", group(#"{"companies": [{"slug": "a", "name": "A", "card_kind": "non_us"}], "also_in_club": [{"slug": "a", "name": "A"}, {"slug": "b", "name": "B"}]}"#).alsoInClub.map(\.slug).joined(separator: ","), "b")
check("copy.filed_only", company(#"{"slug": "a", "name": "A", "card_kind": "thirteen_f", "filed_on": "2026-08-14"}"#)?.filingDatesLine,
      "Filed Aug 14, 2026")

// ── 7. Numbers and dates ──────────────────────────────────────────────────────
typealias F = TrillionClubFormat
check("usd.zero", F.dollars(0), "$0")
check("usd.small", F.dollars(512), "$512")
check("usd.k", F.dollars(950_000), "$950K")
check("usd.k_to_m", F.dollars(999_960), "$1M")
check("usd.m", F.dollars(400_000_000), "$400M")
check("usd.b_small", F.dollars(4_420_000_000), "$4.42B")
check("usd.b_trim", F.dollars(2_000_000_000), "$2B")
check("usd.b_to_10", F.dollars(9_996_000_000), "$10B")
check("usd.b_large", F.dollars(63_439_974_569), "$63.4B")
check("usd.b_to_t", F.dollars(999_960_000_000), "$1T")
check("usd.t", F.dollars(5_445_000_000_000), "$5.45T")
check("usd.neg", F.dollars(-1), nil)
check("usd.nan", F.dollars(.nan), nil)
check("usd.inf", F.dollars(.infinity), nil)
check("usd.nil", F.dollars(nil), nil)
check("w.normal", F.weight(0.4727), "47%")
check("w.small", F.weight(0.004), "<1%")
check("w.almost_all", F.weight(0.996), ">99%")
check("w.all", F.weight(1.0), "100%")
check("w.over", F.weight(1.2), nil)
check("w.zero", F.weight(0), nil)
check("w.neg", F.weight(-0.1), nil)
check("w.nan", F.weight(.nan), nil)
check("own.whole", F.ownership(25), "25%")
check("own.just_short", F.ownership(99.97), "99.97%")
check("own.just_short_double", F.ownership(99.95), "99.95%")
check("own.hair_short", F.ownership(99.999), "<100%")
check("own.one_decimal_short", F.ownership(99.94), "99.9%")
check("own.round_down_high", F.ownership(99.6), "99.6%")
check("own.full", F.ownership(100), "100%")
check("own.decimal", F.ownership(9.3), "9.3%")
check("own.tiny", F.ownership(0.05), "<0.1%")
check("own.over", F.ownership(150), nil)
check("own.zero", F.ownership(0), nil)
check("sh.grouped", F.shares(833_325), "833,325")
check("sh.m", F.shares(214_776_632), "214.8M")
check("sh.b", F.shares(1_250_000_000), "1.25B")
check("sh.m_to_b", F.shares(999_960_000), "1B")
check("sh.m_trim", F.shares(1_000_000), "1M")
check("sh.neg", F.shares(-1), nil)
check("sh.signed_neg", F.signedShares(-1_200_000), "-1.2M")
check("sh.signed_zero", F.signedShares(0), nil)
check("grouped.neg", F.grouped(-1234567), "-1,234,567")
check("grouped.min", String(F.grouped(Int.min).hasPrefix("-9,223,372")), "true")
check("sh.signed_fraction", F.signedShares(0.4), nil)
check("sh.signed_one", F.signedShares(0.6), "+1")
check("date.long", ClubDate(iso: "2026-06-30")?.long, "Jun 30, 2026")
check("date.timestamp", ClubDate(iso: "2026-06-30T23:59:00-08:00")?.long, "Jun 30, 2026")
check("date.leap", ClubDate(iso: "2024-02-29")?.long, "Feb 29, 2024")
check("date.not_leap", ClubDate(iso: "2026-02-29")?.long, nil)
check("date.century", ClubDate(iso: "2100-02-29")?.long, nil)
check("date.garbage", ClubDate(iso: "June 30")?.long, nil)
check("date.signed", ClubDate(iso: "+026-06-30")?.long, nil)
check("date.month13", ClubDate(iso: "2026-13-01")?.long, nil)
check("date.spoken", ClubDate(iso: "2026-06-12")?.spoken, "June 12, 2026")
check("period.next_year", ClubPeriod(wire: "2026-Q4")?.next.label, "Q1 2027")
check("period.bad", ClubPeriod(wire: "2026-Q0")?.label, nil)
check("period.relative_same", ClubPeriod(wire: "2026-Q1")?.label(relativeTo: ClubPeriod(wire: "2026-Q2")), "Q1")
check("period.relative_cross", ClubPeriod(wire: "2025-Q4")?.label(relativeTo: ClubPeriod(wire: "2026-Q1")), "Q4 2025")
check("pill.new_help", ClubChangeKind.newlyReported.helpText, "First time on a 13F — not necessarily a new purchase")
check("pill.gone_help", ClubChangeKind.noLongerReported.helpText,
      "Left the filing — sold, merged, too small to report, or kept confidential")
check("pill.unknown", ClubChangeKind(wire: "whatever").pillLabel, nil)
check("chip.a11y", ClubChip.privateCompany.accessibilityText, "Private: not publicly traded, so there's no market price.")

// ── 7b. Hardening fixes (2026-09-24) ─────────────────────────────────────────
// Logo: a U.S. ticker, or (since 2026-09-24) a local listing with a KNOWN exchange suffix —
// the CDN has Aramco's and Samsung's logos, and the views show the monogram while one loads.
// A dotted class share, a bare local code or an unknown suffix is never guessed at.
for (raw, want) in [("2222.SR", "2222.SR"), ("005930.KS", "005930.KS"), ("0700.hk", "0700.HK"),
                    ("BRK.B", nil), ("ABCDEF", nil), ("BRK-BB", nil), ("2222", nil), ("2222.XX", nil),
                    ("2222.SR.X", nil), (".SR", nil), ("ABCDEFGHIJK.SR", nil),
                    ("-B", nil), ("NVDA", "NVDA"), ("brk-b", "BRK-B"), ("A", "A"), ("GOOGL", "GOOGL")] as [(String, String?)] {
    check("logo.\(raw)", company(#"{"slug": "a", "name": "A", "card_kind": "non_us", "logo_symbol": "\#(raw)"}"#)?.logoSymbol, want)
}
check("logo.monogram_aramco", company(#"{"slug": "a", "name": "Saudi Aramco", "card_kind": "non_us", "logo_symbol": "2222.SR"}"#)?.monogram, "S")
// A logo never makes a company routable: the stock page comes only from `detail_symbol`.
check("logo.not_routable", company(#"{"slug": "a", "name": "Saudi Aramco", "card_kind": "non_us", "logo_symbol": "2222.SR"}"#)?.detailSymbol, nil)
check("logo.monogram_digit_name", company(#"{"slug": "a", "name": "3M Company", "card_kind": "no_thirteen_f"}"#)?.monogram, "M")
check("logo.monogram_lower", company(#"{"slug": "a", "name": "eBay", "card_kind": "no_thirteen_f"}"#)?.monogram, "E")

// Stake count: counted from EVERY published stake; no server count → no number claimed.
let counted = company(#"""
{"slug": "s", "name": "Samsung", "card_kind": "non_us", "stake_count": 9, "stakes": [
  {"investee_name": "A", "kind": "private", "as_of": "2026-06-30", "source_title": "S"},
  {"investee_name": "B", "kind": "private", "as_of": "2026-06-30", "source_title": "S"},
  {"investee_name": "C", "kind": "private", "as_of": "2026-06-30", "source_title": "S"}]}
"""#)
check("more.counted", counted?.moreStakesText(shown: 2), "+7 more in the details")
check("more.all_shown", company(#"{"slug": "s", "name": "S", "card_kind": "non_us", "stake_count": 2}"#)?.moreStakesText(shown: 2), nil)
check("more.no_count", company(#"""
{"slug": "s", "name": "S", "card_kind": "non_us", "stakes": [
  {"investee_name": "A", "kind": "private", "as_of": "2026-06-30", "source_title": "S"},
  {"investee_name": "B", "kind": "private", "as_of": "2026-06-30", "source_title": "S"},
  {"investee_name": "C", "kind": "private", "as_of": "2026-06-30", "source_title": "S"}]}
"""#)?.moreStakesText(shown: 2), "More in the details")
check("more.no_count_none", company(#"{"slug": "s", "name": "S", "card_kind": "non_us"}"#)?.moreStakesText(shown: 0), nil)
check("more.negative", company(#"{"slug": "s", "name": "S", "card_kind": "non_us", "stake_count": -4}"#)?.stakeCount.map { String($0) }, nil)
check("more.below_card", company(#"""
{"slug": "s", "name": "S", "card_kind": "non_us", "stake_count": 1, "stakes": [
  {"investee_name": "A", "kind": "private", "as_of": "2026-06-30", "source_title": "S"},
  {"investee_name": "B", "kind": "private", "as_of": "2026-06-30", "source_title": "S"}]}
"""#)?.stakeCount.map { String($0) }, "2")

// ── 7c. The 2026-09-24 simplified UI ─────────────────────────────────────────
// The Home tile's one line: never empty, and its stakes never count a note on a 13F holding
// (`other_stake_count`) — NVIDIA's Intel note is already one of its 8 holdings.
let filerCard = #"{"slug": "n", "name": "NVIDIA", "card_kind": "thirteen_f", "period": "2026-Q2", "position_count": 8, "stake_count": 5, "other_stake_count": 2}"#
check("line.filer", company(filerCard)?.cardLine, "8 holdings · 2 stakes")
check("line.a11y", company(filerCard)?.cardAccessibilityText, "NVIDIA. 8 holdings · 2 stakes.")
check("line.singular", company(#"{"slug": "n", "name": "N", "card_kind": "thirteen_f", "period": "2026-Q2", "position_count": 1, "other_stake_count": 1}"#)?.cardLine, "1 holding · 1 stake")
check("line.grouped", company(#"{"slug": "n", "name": "N", "card_kind": "thirteen_f", "period": "2026-Q2", "position_count": 1234}"#)?.cardLine, "1,234 holdings")
check("line.notes_only", company(#"{"slug": "a", "name": "Alphabet", "card_kind": "thirteen_f", "period": "2026-Q2", "position_count": 28, "stake_count": 1, "other_stake_count": 0}"#)?.cardLine, "28 holdings")
// A filer before its first filing claims no holdings; nor does a filing that lists none.
// …and its detail is then ONE list of every stake, notes included, so all are counted.
check("line.no_filing", company(#"{"slug": "a", "name": "AMD", "card_kind": "thirteen_f", "position_count": 5, "stake_count": 3, "other_stake_count": 2}"#)?.cardLine, "3 stakes")
check("line.zero_holdings", company(#"{"slug": "n", "name": "N", "card_kind": "thirteen_f", "period": "2026-Q2", "position_count": 0, "other_stake_count": 1}"#)?.cardLine, "1 stake")
check("line.non_filer", company(#"{"slug": "m", "name": "Microsoft", "card_kind": "no_thirteen_f", "position_count": 9, "stake_count": 3, "other_stake_count": 3}"#)?.cardLine, "3 stakes")
check("line.nothing", company(#"{"slug": "n", "name": "N", "card_kind": "non_us"}"#)?.cardLine, "See details")
// An older backend (no `other_stake_count`): the card's own stakes stand in — never the
// all-stakes `stake_count`, which counts notes.
check("line.fallback", company(#"""
{"slug": "n", "name": "N", "card_kind": "thirteen_f", "period": "2026-Q2", "position_count": 8, "stake_count": 5, "stakes": [
  {"investee_name": "A", "kind": "private", "as_of": "2026-06-30", "source_title": "S"},
  {"investee_name": "B", "kind": "private", "as_of": "2026-06-30", "source_title": "S"}]}
"""#)?.cardLine, "8 holdings · 2 stakes")
// Without the Holdings split the detail lists every stake — Meta's 4, one of them not on its
// card — so the all-stakes count is the one that matches, server field or not.
check("line.unsegmented_counts_all", company(#"""
{"slug": "m", "name": "Meta", "card_kind": "no_thirteen_f", "stake_count": 4, "stakes": [
  {"investee_name": "A", "kind": "private", "as_of": "2026-06-30", "source_title": "S"}]}
"""#)?.cardLine, "4 stakes")
check("line.negative_count", company(#"{"slug": "s", "name": "S", "card_kind": "non_us", "other_stake_count": -3}"#)?.otherStakeCount.map { String($0) }, nil)

// The detail's holding line: symbol and value (no shares); a first 13F because it began
// trading says so, so its "Newly reported" pill never reads as a purchase.
let simple = detail(#"""
{"company": {"slug": "n", "name": "N", "card_kind": "thirteen_f", "period": "2026-Q2", "comparison": "quarter"},
 "holdings": [
   {"name": "Intel", "symbol": "INTC", "weight": 0.47, "shares": 214776632, "value": 29989261126, "change": "unchanged"},
   {"name": "SpaceX", "symbol": "SPCX", "shares": 122764805, "value": 20980000000, "change": "newly_reported", "newly_listed": true},
   {"name": "Private Co", "value": 5000000, "change": "increased", "newly_listed": true}],
 "changes": [{"name": "Arm Holdings", "change": "no_longer_reported", "prev_shares": 100},
             {"name": "Snowflake", "change": "no_longer_reported"},
             {"name": "SpaceX", "change": "newly_reported", "shares": 5}]}
"""#)
check("short.plain", simple?.holdings[0].shortHoldingLine, "INTC · $30B")
check("short.newly_listed", simple?.holdings[1].shortHoldingLine, "SPCX · $21B · first 13F since it began trading")
check("short.listed_only_when_new", simple?.holdings[2].shortHoldingLine, "$5M")
check("short.a11y_keeps_shares", simple?.holdings[0].holdingLine, "INTC · 214.8M shares · $30B")
check("gone.names", simple?.noLongerReportedText, "No longer reported: Arm Holdings, Snowflake")
// Pro: every changed holding is already a pill in the list, so only the "gone" line remains.
check("unlisted.pro", simple?.unlistedChangeLines.joined(separator: " | "), "No longer reported: Arm Holdings, Snowflake")
// Free: the top 3 holdings, but EVERY changed row — the rest are named, never dropped.
let freeDetail = detail(#"""
{"company": {"slug": "n", "name": "N", "card_kind": "thirteen_f", "period": "2026-Q2", "comparison": "quarter"},
 "is_locked": true, "locked_holdings_count": 5,
 "holdings": [
   {"name": "Intel", "symbol": "INTC", "value": 30000000000, "change": "unchanged"},
   {"name": "SpaceX", "symbol": "SPCX", "value": 21000000000, "change": "newly_reported"},
   {"name": "CoreWeave", "symbol": "CRWV", "value": 4700000000, "change": "unchanged"}],
 "changes": [{"name": "SpaceX", "symbol": "SPCX", "change": "newly_reported"},
             {"name": "Arm Holdings", "symbol": "ARM", "change": "newly_reported"},
             {"name": "Coherent", "symbol": "COHR", "change": "increased"},
             {"name": "Synopsys", "symbol": "SNPS", "change": "decreased"},
             {"name": "Nokia", "symbol": "NOK", "change": "corporate_action"},
             {"name": "Snowflake", "change": "no_longer_reported"}]}
"""#)
// Same 13F row under two spellings of its name: the symbol says it is already a pill.
check("unlisted.symbol_match", detail(#"""
{"company": {"slug": "n", "name": "N", "card_kind": "thirteen_f", "period": "2026-Q2", "comparison": "quarter"},
 "holdings": [{"name": "Space Exploration Technologies Corp.", "symbol": "SPCX", "change": "newly_reported"}],
 "changes": [{"name": "SpaceX", "symbol": "SPCX", "change": "newly_reported"}]}
"""#).map { String($0.unlistedChangeLines.count) }, "0")
check("unlisted.free", freeDetail?.unlistedChangeLines.joined(separator: " | "),
      "Newly reported: Arm Holdings | Increased shares: Coherent | Decreased shares: Synopsys | Corporate action: Nokia | No longer reported: Snowflake")
// After a gap or a first filing nothing was compared: no pill, no clause, no "gone" line.
for comparison in ["gap", "first_filing"] {
    let uncompared = detail(#"""
    {"company": {"slug": "n", "name": "N", "card_kind": "thirteen_f", "period": "2026-Q2", "comparison": "\#(comparison)"},
     "holdings": [{"name": "SpaceX", "symbol": "SPCX", "value": 20980000000, "change": "newly_reported", "newly_listed": true}],
     "changes": [{"name": "Arm Holdings", "change": "no_longer_reported"}]}
    """#)
    check("gone.\(comparison)", uncompared?.noLongerReportedText, nil)
    check("unlisted.\(comparison)", String(uncompared?.unlistedChangeLines.count ?? -1), "0")
    check("short.\(comparison)", uncompared?.holdings.first?.shortHoldingLine, "SPCX · $21B")
    checkTrue("short.\(comparison).no_pill", uncompared?.holdings.first?.change == nil)
}
check("gone.none", detail(#"{"company": {"slug": "n", "name": "N", "card_kind": "thirteen_f", "period": "2026-Q2", "comparison": "quarter"}}"#)?.noLongerReportedText, nil)

// A stake row's figure line is never empty: without a figure it says what the stake IS.
func stakeRowOf(_ json: String) -> ClubStake? {
    detail(#"{"company": {"slug": "m", "name": "Meta", "card_kind": "no_thirteen_f"}, "stakes": [\#(json)]}"#)?.stakes.first
}
check("figure.warrant", stakeRowOf(#"{"investee_name": "AMD", "kind": "commitment", "as_of": "2026-06-27", "source_title": "AMD 10-Q", "tied_to_deal": true}"#)?.rowFigureText,
      "Commitment — not a reported holding")
check("figure.committed", stakeRowOf(#"{"investee_name": "Anthropic", "kind": "commitment", "disclosed_value": 10000000000, "value_basis": "committed_up_to", "as_of": "2025-11-18", "source_title": "S"}"#)?.rowFigureText,
      "committed up to $10B")
check("figure.private", stakeRowOf(#"{"investee_name": "Anthropic", "kind": "private", "as_of": "2026-06-30", "source_title": "S"}"#)?.rowFigureText,
      "Private stake — no figure disclosed")
check("figure.private_unknown_basis", stakeRowOf(#"{"investee_name": "X", "kind": "private", "disclosed_value": 5, "value_basis": "rumoured", "as_of": "2026-06-30", "source_title": "S"}"#)?.rowFigureText,
      "Private stake — no figure disclosed")
check("figure.non_us_listing", stakeRowOf(#"{"investee_name": "Bahri", "kind": "non_us_listed", "local_listing": "Tadawul", "as_of": "2026-06-30", "source_title": "S"}"#)?.rowFigureText,
      "Listed in Tadawul — no figure disclosed")
check("figure.non_us", stakeRowOf(#"{"investee_name": "Bahri", "kind": "non_us_listed", "as_of": "2026-06-30", "source_title": "S"}"#)?.rowFigureText,
      "Listed outside the U.S. — no figure disclosed")
check("figure.off_13f", stakeRowOf(#"{"investee_name": "SpaceX", "kind": "us_listed_off_13f", "as_of": "2026-06-30", "source_title": "S"}"#)?.rowFigureText,
      "U.S.-listed, not on a 13F — no figure disclosed")
// A commitment sized as a percent (a warrant) says it is a commitment; "committed up to"
// already says so on its own.
check("figure.commitment_pct", stakeRowOf(#"{"investee_name": "AMD", "kind": "commitment", "ownership_pct": 10, "ownership_basis": "of shares", "as_of": "2026-06-27", "source_title": "S"}"#)?.rowFigureText,
      "Commitment · 10% of shares")
check("figure.commitment_invested", stakeRowOf(#"{"investee_name": "X", "kind": "commitment", "disclosed_value": 5000000000, "value_basis": "invested", "as_of": "2026-06-27", "source_title": "S"}"#)?.rowFigureText,
      "Commitment · $5B invested")
check("figure.pct", stakeRowOf(#"{"investee_name": "OpenAI", "kind": "private", "ownership_pct": 25, "ownership_basis": "approximate", "as_of": "2026-06-30", "source_title": "S"}"#)?.rowFigureText,
      "25% approximate")

// Whale link: the profile sentence only when there is a profile to open.
check("whale.no_profile", company(#"{"slug": "b", "name": "Berkshire Hathaway", "card_kind": "whale_link"}"#)?.explainer, nil)
check("whale.profile", company(#"{"slug": "b", "name": "Berkshire Hathaway", "card_kind": "whale_link", "whale_id": "w"}"#)?.explainer,
      "Berkshire Hathaway files 13Fs as an investor — see its full portfolio on its profile.")

// Commitment: names its source; claims neither an agreement to invest nor that nothing is held.
check("chip.commitment_source", ClubChip.commitment.accessibilityText(source: "AMD 10-Q"),
      "Commitment: a commitment or right disclosed in AMD 10-Q, not a reported holding.")
check("chip.commitment_nosource", ClubChip.commitment.accessibilityText,
      "Commitment: a commitment or right disclosed in its source, not a reported holding.")
check("chip.commitment_blank_source", ClubChip.commitment.accessibilityText(source: "  "),
      "Commitment: a commitment or right disclosed in its source, not a reported holding.")
let warrant = company(#"""
{"slug": "m", "name": "Meta", "card_kind": "no_thirteen_f", "stakes": [
  {"investee_name": "AMD", "kind": "commitment", "as_of": "2026-06-27", "source_title": "AMD 10-Q"}]}
"""#)?.stakes.first
// The detail row (no chips since 2026-09-24) still says what the warrant is, and where from.
check("chip.stake_row_says_commitment", warrant?.rowFigureText, "Commitment — not a reported holding")
checkTrue("chip.stake_names_source", warrant?.sourceText.contains("AMD 10-Q") == true)
checkTrue("chip.sentence_flag", ClubChip.listedSince(ClubDate(iso: "2026-06-12")!).isSentence && !ClubChip.commitment.isSentence && !ClubChip.clubMember.isSentence)

// Gap: nothing was compared — no counts, no pills, no rows, no "unchanged".
let gap = detail(#"""
{"company": {"slug": "amd", "name": "AMD", "card_kind": "thirteen_f", "comparison": "gap",
             "period": "2026-Q2", "change_counts": {"unchanged": 0},
             "top_holdings": [{"name": "Intel", "symbol": "INTC", "weight": 0.5, "change": "unchanged"},
                              {"name": "SpaceX", "symbol": "SPCX", "weight": 0.3, "change": "newly_reported", "newly_listed": true}]},
 "holdings": [{"name": "Intel", "symbol": "INTC", "weight": 0.5, "change": "unchanged"},
              {"name": "SpaceX", "symbol": "SPCX", "weight": 0.3, "change": "newly_reported", "newly_listed": true}],
 "changes": [{"name": "SpaceX", "change": "newly_reported", "shares": 5}]}
"""#)
check("gap.line", gap?.company.changeLine, "No filing for the quarter before Q2 2026 to compare with.")
check("gap.line_no_period", company(#"{"slug": "a", "name": "A", "card_kind": "thirteen_f", "comparison": "gap", "period_end": "2026-06-30"}"#)?.changeLine,
      "No filing for the quarter before to compare with.")
check("gap.card_pills", gap?.company.topHoldings.compactMap { $0.change?.rawValue }.joined(separator: ","), "")
check("gap.detail_pills", gap?.holdings.compactMap { $0.change?.rawValue }.joined(separator: ","), "")
checkTrue("gap.newly_listed_dropped", gap?.holdings.allSatisfy { !$0.newlyListed } == true)
check("gap.rows", String(gap?.changes.count ?? -1), "0")
checkTrue("gap.counts", gap?.company.changeCounts == nil)
check("gap.unchanged", gap?.unchangedText, nil)
check("gap.empty_text", gap?.changesEmptyText, nil)
checkTrue("gap.voiceover", gap?.holdings.allSatisfy { !$0.accessibilityText.contains("Unchanged") } == true)
check("quarter.empty_text", rows?.changesEmptyText, "No share-count changes vs the quarter before.")
check("first.empty_text", detail(#"{"company": {"slug": "a", "name": "A", "card_kind": "thirteen_f", "period": "2026-Q2", "comparison": "first_filing"}}"#)?.changesEmptyText, nil)
check("unchanged.unknown_comparison", detail(#"{"company": {"slug": "a", "name": "A", "card_kind": "thirteen_f", "period": "2026-Q2", "comparison": "brand_new", "change_counts": {"unchanged": 4}}}"#)?.unchangedText, nil)
check("unchanged.quarter", detail(#"{"company": {"slug": "a", "name": "A", "card_kind": "thirteen_f", "period": "2026-Q2", "comparison": "quarter", "change_counts": {"unchanged": 4}}}"#)?.unchangedText, "4 holdings unchanged")

// A 13F filer with NO filing on file: nothing about a filing, stakes instead of segments.
let noFiling = detail(#"""
{"company": {"slug": "nvidia", "name": "NVIDIA", "card_kind": "thirteen_f", "next_due": "2026-11-16",
             "notice": "no_newer_filing", "stakes": [{"investee_name": "Intel", "kind": "on_13f_note", "as_of": "2025-09-18", "source_title": "S"}]},
 "stakes": [{"investee_name": "Intel", "kind": "on_13f_note", "as_of": "2025-09-18", "source_title": "S"}]}
"""#)
checkTrue("nofiling.segments", noFiling?.showsThirteenFSegments == false)
checkTrue("nofiling.has_filing", noFiling?.company.hasFilingOnFile == false)
check("nofiling.explainer", noFiling?.company.explainer, "Holdings appear after the first 13F is processed.")
check("nofiling.stat", noFiling?.company.holdingsStatLine, nil)
check("nofiling.due", noFiling?.company.nextDueLine, nil)
check("nofiling.notice", noFiling?.company.noticeText, nil)
check("nofiling.heading", noFiling?.company.stakesHeading, "Disclosed stakes")
checkTrue("withfiling.segments", d?.showsThirteenFSegments == true)
check("withfiling.explainer", nv?.explainer, nil)

// History lock: only over something.
for (json, want) in [(#"{"is_locked": true, "locked_history_count": 0}"#, false),
                     (#"{"is_locked": true, "locked_history_count": 3}"#, true),
                     (#"{"is_locked": true}"#, true),
                     (#"{"is_locked": true, "locked_history_count": -2}"#, false),
                     (#"{"is_locked": false, "locked_history_count": 3}"#, false)] {
    let body = json.dropFirst().dropLast()
    let h = detail(#"{"company": {"slug": "a", "name": "A", "card_kind": "thirteen_f", "period": "2026-Q2"}, "# + body + "}")
    check("history_lock.\(body)", String(h?.showsHistoryLock ?? !want), String(want))
}

// ── 8. The bundled samples decode through the real path ───────────────────────
check("samples.group", TrillionClubSamples.group.companies.map(\.slug).joined(separator: ","),
      "nvidia,microsoft,tsmc,berkshire")
checkTrue("samples.detail", TrillionClubSamples.nvidiaDetailLocked?.lockedHoldingsCount == 5)

print("DONE|\(failures)|\(checks)")
'''


def _run_harness() -> str:
    if not shutil.which("xcrun"):
        pytest.skip("xcrun unavailable — Swift cannot be executed on this host")
    consts = "\n".join(f"let {name} = {_swift_literal(value)}" for name, value in _payloads().items())
    program = _models_source() + "\n" + consts + "\n" + HARNESS
    try:
        proc = subprocess.run(["xcrun", "swift", "-"], input=program, text=True,
                              capture_output=True, timeout=300)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not run swift: {type(exc).__name__}: {exc}")
    if "DONE|" not in proc.stdout:
        pytest.fail("Swift harness did not complete (a SwiftUI import in the models file?)\n"
                    f"stdout:\n{proc.stdout[-3000:]}\nstderr:\n{proc.stderr[-4000:]}")
    return proc.stdout


@pytest.fixture(scope="module")
def swift_output() -> str:
    return _run_harness()


def test_models_file_is_foundation_only():
    """The harness can only run a Foundation file — a SwiftUI import would silently turn the
    whole executed half into a skip-or-fail on every run."""
    code, _ = _scanned()
    imports = set(re.findall(r"^\s*import\s+(\w+)", code, flags=re.MULTILINE))
    assert imports <= {"Foundation", "os"}, f"TrillionClubModels.swift imports {sorted(imports)}"
    assert "Color(" not in code and "AppColors" not in code, "colours belong in the views"


def test_every_executed_check_passes(swift_output: str):
    failures = [line for line in swift_output.splitlines() if line.startswith("FAIL|")]
    assert not failures, "\n  ".join(failures)


def test_the_harness_actually_ran_its_checks(swift_output: str):
    done = [line for line in swift_output.splitlines() if line.startswith("DONE|")]
    assert done, "no DONE line"
    _, fails, total = done[-1].split("|")
    assert int(fails) == 0
    assert int(total) >= 140, f"only {total} checks ran — the harness was truncated"
    oks = [line for line in swift_output.splitlines() if line.startswith("ok|")]
    assert len(oks) == int(total)
