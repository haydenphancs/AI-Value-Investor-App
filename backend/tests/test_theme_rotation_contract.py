"""Emerging Frontiers monthly rotation + daily insights — backend⇄iOS contract and migration guards.

WHY THIS FILE EXISTS
--------------------
Migration 174 added a monthly rotation of each Home theme's stocks plus daily theme
insights, and with them new wire fields on the two theme responses and five new nested
response types. Three things must hold for that to ship without a decode crash or a
security regression, and none of them is visible from the happy path:

1. **Swift ⇄ Pydantic parity.** The iOS `APIClient` does NOT `convertFromSnakeCase`, so
   every wire key is spelled out in a `CodingKeys` enum. A drift is a decode crash (or a
   silently nil field) in production. Parity is proven by PARSING the Swift source —
   comments stripped, each struct brace-bounded, keys derived from its own `CodingKeys` —
   never by grepping a whole file (testing.md: a token in a comment or in a different
   type makes that kind of guard vacuous). The parser is mutation-tested in-file.
2. **Additive, optional fields.** An older backend / un-migrated database / theme not yet
   reviewed simply omits the new fields, so every new Swift property must be Optional and
   every new Pydantic field defaulted.
3. **Migration 174 hygiene.** Idempotent DDL, service-role-only grants on all four new
   tables AND on `trending_themes` (its 081 anon read door is closed), a SECURITY INVOKER
   publish function with a locked search_path, CHECK constraints that match the code's
   enums, and no column the rotation code touches that the migration never created.

Plus the Home service's mapping helpers (`_card_insight_fields`, `_detail_insight_fields`,
`_iso_date`, `_review_change_count`, `_theme_news`) against the REAL row shape written by
`theme_insights_service`, and `_build_themes` / `_build_theme_detail` end-to-end.

Hermetic: every Supabase / FMP / news read is stubbed; nothing here opens a socket.
"""

from __future__ import annotations

import ast
import json
import math
import re
import sys
import typing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from pydantic import BaseModel

import app.services.home_dashboard_service as hds
from app.schemas.home_dashboard import ThemesGroupResponse, TrendingThemeResponse
from app.schemas.themes_detail import (
    ThemeChangeResponse,
    ThemeConstituentResponse,
    ThemeDetailResponse,
    ThemeInsightResponse,
    ThemeNewsItemResponse,
    ThemePerformanceResponse,
    ThemePeriodReturnResponse,
)
from app.services.home_dashboard_service import (
    HomeDashboardService,
    _card_insight_fields,
    _detail_insight_fields,
    _iso_date,
    _review_change_count,
)
from app.services.theme_rotation import read_model
from app.services.theme_rotation.models import Action, Fit, Reason
from app.services.theme_rotation.read_model import LatestReview, build_review

from _price_fakes import PriceFromFMPFake

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
IOS = REPO / "frontend" / "ios" / "ios"
IOS_MODELS = IOS / "Models"
MIGRATIONS = BACKEND / "database" / "migrations"
MIGRATION_174 = MIGRATIONS / "174_theme_rotation_and_insights.sql"
ROTATION_PKG = BACKEND / "app" / "services" / "theme_rotation"


# ══════════════════════════════════════════════════════════════════════════════════════
# Swift source scanner — comments removed, string literals lifted out, structs bounded
# ══════════════════════════════════════════════════════════════════════════════════════


def _skip_interpolation(src: str, i: int) -> int:
    """`i` is just past `\\(`. Returns the index just past the matching `)`, stepping over
    any string literal nested inside the interpolation (`"\\(a ? "x" : "y")"`)."""
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
    """Read the Swift string literal starting at `i` (`"`, `\"\"\"` or a raw `#"`).
    Returns (raw content, index just past the closing delimiter)."""
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
    replaced by the placeholder `"__S<i>__"` whose content is `strings[i]`. Braces and `//`
    inside strings therefore cannot confuse the structure scan."""
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


def _match_brace(code: str, start: int) -> int:
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
    """The depth-0 text of a type body: every nested `{...}` collapsed to `{}`."""
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
    """The body of exactly ONE `struct <name>` (word-bounded) in the scanned code."""
    matches = list(re.finditer(rf"\b{kind}\s+{re.escape(name)}\b[^{{]*\{{", code))
    assert len(matches) == 1, f"expected exactly one `{kind} {name}`, found {len(matches)}"
    start = matches[0].end()
    return code[start:_match_brace(code, start)]


_MODIFIERS = r"(?:(?:@\w+(?:\([^)]*\))?|private|fileprivate|internal|public|open|lazy|weak|final)(?:\(set\))?\s+)*"
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
        # A `let` with an initial value is never decoded by synthesized Decodable.
        return self.kw == "var" or not self.has_init


@dataclass(frozen=True)
class SwiftStruct:
    name: str
    props: Dict[str, SwiftProp]
    coding_keys: Optional[Dict[str, str]]    # case name → wire key; None = no CodingKeys

    def wire_keys(self) -> Dict[str, str]:
        """Property name → wire key, the way synthesized Decodable resolves it."""
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
            continue                                    # type-level, never decoded
        brace, eq = line.find("{"), line.find("=")
        if brace != -1 and (eq == -1 or brace < eq):
            continue                                    # computed property / observer
        m = _STORED.match(line.replace("{}", ""))
        if m is None:
            # e.g. `let a, b: Int` or an untyped `var x = 1` — refuse to guess.
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
        ck_body = _flatten(body[start:_match_brace(body, start)])
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


def _swift_file(filename: str) -> Tuple[str, List[str]]:
    return scan_swift((IOS_MODELS / filename).read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════════════════
# 0. The parser itself — proven non-vacuous before anything leans on it
# ══════════════════════════════════════════════════════════════════════════════════════

_SYNTHETIC_SWIFT = r'''
// struct FakeDTO { let ghost: Int }   ← a comment must never be parsed
/* outer /* nested */ still a comment: let hidden: Int */
struct FakeDTOExtra: Decodable { let extra: Int }
struct FakeDTO: Decodable {
    let slug: String
    /// Doc comment with a brace { and a fake case: case bogus = "bogus"
    let imageUrl: String?   // trailing comment "with quotes"
    var score: Double? = nil
    let constant: Int = 3
    static let shared = 1
    var computed: String { "{ not a key }" }
    var multiLine: String {
        let inner: Int = 1
        return "\(inner > 0 ? "a" : "b")"
    }
    let url: String? // "https://example.com/{x}"
    enum Inner { case a, b }
    enum CodingKeys: String, CodingKey {
        case slug, score
        // case commented = "commented_out"
        case imageUrl = "image_url"
        case url = "u//rl"
    }
    func helper() -> String { "}" }
}
'''


def test_scanner_strips_nested_block_comments_and_keeps_string_braces_out():
    code, strings = scan_swift(_SYNTHETIC_SWIFT)
    assert "ghost" not in code and "hidden" not in code and "bogus" not in code
    assert "commented_out" not in code and "commented_out" not in strings
    assert "{ not a key }" in strings            # lifted out of the code …
    assert "{ not a key }" not in code           # … so its brace cannot unbalance anything
    assert "u//rl" in strings                    # a `//` inside a string is not a comment


def test_parser_reads_exactly_the_structs_own_coding_keys():
    code, strings = scan_swift(_SYNTHETIC_SWIFT)
    s = parse_struct(code, strings, "FakeDTO")
    assert s.coding_keys == {"slug": "slug", "score": "score", "imageUrl": "image_url",
                             "url": "u//rl"}
    # computed / static / nested-scope declarations are not stored properties
    assert set(s.props) == {"slug", "imageUrl", "score", "constant", "url"}
    assert s.props["imageUrl"].optional and not s.props["slug"].optional
    assert s.props["constant"].decoded is False          # `let` with an initial value
    assert s.props["score"].decoded is True               # `var` with a default
    # word-bounded: FakeDTOExtra is a different struct
    assert parse_struct(code, strings, "FakeDTOExtra").props.keys() == {"extra"}


def test_parser_without_coding_keys_uses_property_names():
    code, strings = scan_swift("struct P: Decodable {\n let period: String\n let theme: Double?\n}\n")
    assert parse_struct(code, strings, "P").wire_keys() == {"period": "period", "theme": "theme"}


def test_parser_refuses_a_declaration_it_cannot_read():
    code, strings = scan_swift("struct Q: Decodable {\n let a, b: Int\n}\n")
    with pytest.raises(ValueError):
        parse_struct(code, strings, "Q")


def test_parser_rejects_ambiguous_struct_name():
    code, strings = scan_swift("struct A {}\nstruct A {}\n")
    with pytest.raises(AssertionError):
        parse_struct(code, strings, "A")


def test_parity_check_catches_a_mutated_coding_key():
    """Mutation test of the real parity check: rename one raw value in the REAL Swift
    source and the derived key set must stop matching the Pydantic model."""
    src = (IOS_MODELS / "ThemeDetailModels.swift").read_text(encoding="utf-8")
    assert 'case isNew = "is_new"' in src
    code, strings = scan_swift(src.replace('case isNew = "is_new"', 'case isNew = "isNew"'))
    keys = set(parse_struct(code, strings, "ThemeConstituentDTO").wire_keys().values())
    assert keys != set(ThemeConstituentResponse.model_fields)


def test_optionality_check_catches_a_mutated_optional():
    src = (IOS_MODELS / "ThemeDetailModels.swift").read_text(encoding="utf-8")
    assert "let updatedOn: String?" in src
    code, strings = scan_swift(src.replace("let updatedOn: String?", "let updatedOn: String"))
    assert parse_struct(code, strings, "ThemeDetailDTO").props["updatedOn"].optional is False


def test_comment_only_mention_does_not_satisfy_the_parser():
    """A CodingKeys case that survives only in a comment must NOT count as a key."""
    src = (IOS_MODELS / "HomeDashboardModels.swift").read_text(encoding="utf-8")
    assert '        case spark1m = "spark_1m"\n' in src
    mutated = src.replace('        case spark1m = "spark_1m"\n', '        // case spark1m = "spark_1m"\n')
    code, strings = scan_swift(mutated)
    assert "spark1m" not in parse_struct(code, strings, "TrendingThemeDTO").coding_keys


# ══════════════════════════════════════════════════════════════════════════════════════
# 1. Swift ⇄ Pydantic parity, per struct
# ══════════════════════════════════════════════════════════════════════════════════════

# (Swift file, DTO, Pydantic response). ThemesGroupDTO is included: it is the wrapper the
# whole section decodes through.
PAIRS: List[Tuple[str, str, type]] = [
    ("HomeDashboardModels.swift", "TrendingThemeDTO", TrendingThemeResponse),
    ("HomeDashboardModels.swift", "ThemesGroupDTO", ThemesGroupResponse),
    ("ThemeDetailModels.swift", "ThemeDetailDTO", ThemeDetailResponse),
    ("ThemeDetailModels.swift", "ThemeConstituentDTO", ThemeConstituentResponse),
    ("ThemeDetailModels.swift", "ThemeChangeDTO", ThemeChangeResponse),
    ("ThemeDetailModels.swift", "ThemePeriodReturnDTO", ThemePeriodReturnResponse),
    ("ThemeDetailModels.swift", "ThemePerformanceDTO", ThemePerformanceResponse),
    ("ThemeDetailModels.swift", "ThemeInsightDTO", ThemeInsightResponse),
    ("ThemeDetailModels.swift", "ThemeNewsItemDTO", ThemeNewsItemResponse),
]
_PAIR_IDS = [p[1] for p in PAIRS]
_MODEL_TO_DTO = {model.__name__: dto for _, dto, model in PAIRS}
_SWIFT_SCALARS = {str: "String", int: "Int", float: "Double", bool: "Bool"}


def _struct(filename: str, dto: str) -> SwiftStruct:
    code, strings = _swift_file(filename)
    return parse_struct(code, strings, dto)


def _py_shape(annotation: Any) -> Tuple[Any, bool]:
    """(shape, nullable) where shape is "String"/"Int"/…/<DTO name> or ("list", shape)."""
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
        assert not inner_nullable, f"a nullable list element cannot decode into Swift: {annotation!r}"
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


@pytest.mark.parametrize("filename,dto,model", PAIRS, ids=_PAIR_IDS)
def test_swift_wire_keys_equal_pydantic_fields(filename, dto, model):
    swift = _struct(filename, dto)
    wire = set(swift.wire_keys().values())
    fields = set(model.model_fields)
    assert wire == fields, (
        f"{dto} decodes {sorted(wire - fields)} that {model.__name__} never sends, and "
        f"{model.__name__} sends {sorted(fields - wire)} that {dto} ignores"
    )


@pytest.mark.parametrize("filename,dto,model", PAIRS, ids=_PAIR_IDS)
def test_coding_keys_cover_exactly_the_decoded_properties(filename, dto, model):
    swift = _struct(filename, dto)
    decoded = {p.name for p in swift.props.values() if p.decoded}
    assert decoded, f"{dto}: parsed no stored properties — the parser went vacuous"
    if swift.coding_keys is not None:
        assert set(swift.coding_keys) == decoded, (
            f"{dto}: CodingKeys cases {sorted(swift.coding_keys)} vs stored {sorted(decoded)}")
    # snake_case on the wire, always (the APIClient does not convert case)
    for key in swift.wire_keys().values():
        assert re.fullmatch(r"[a-z][a-z0-9_]*", key), f"{dto}: non-snake wire key {key!r}"


@pytest.mark.parametrize("filename,dto,model", PAIRS, ids=_PAIR_IDS)
def test_swift_property_types_match_pydantic_types(filename, dto, model):
    swift = _struct(filename, dto)
    wire = swift.wire_keys()
    for prop_name, key in wire.items():
        s_shape, _ = _swift_shape(swift.props[prop_name].type)
        p_shape, _ = _py_shape(model.model_fields[key].annotation)
        assert s_shape == p_shape, f"{dto}.{prop_name} is {s_shape}, {model.__name__}.{key} is {p_shape}"


@pytest.mark.parametrize("filename,dto,model", PAIRS, ids=_PAIR_IDS)
def test_every_non_optional_swift_property_is_always_sent_non_null(filename, dto, model):
    """A non-Optional Swift property decodes only if the key is ALWAYS present and never
    null: the Pydantic field must not admit None, and must be required or carry a
    non-None default (model_dump emits every field)."""
    swift = _struct(filename, dto)
    for prop_name, key in swift.wire_keys().items():
        prop = swift.props[prop_name]
        field = model.model_fields[key]
        _, nullable = _py_shape(field.annotation)
        if nullable:
            assert prop.optional, (
                f"{model.__name__}.{key} may be null but {dto}.{prop_name} is non-Optional "
                f"— one null crashes the whole decode")
        if not prop.optional:
            assert field.is_required() or field.default is not None, (
                f"{dto}.{prop_name} is non-Optional but {model.__name__}.{key} defaults to None")


def _dummy(annotation: Any) -> Any:
    shape, nullable = _py_shape(annotation)
    if nullable:
        return None
    if isinstance(shape, tuple):
        return []
    return {"String": "x", "Int": 1, "Double": 1.0, "Bool": True}.get(shape) or _minimal(
        next(m for _, d, m in PAIRS if d == shape))


def _minimal(model: type) -> BaseModel:
    """The worst case the backend can emit: only required fields, every default left."""
    return model(**{name: _dummy(f.annotation)
                    for name, f in model.model_fields.items() if f.is_required()})


@pytest.mark.parametrize("filename,dto,model", PAIRS, ids=_PAIR_IDS)
def test_minimal_payload_decodes_under_swift_optionality(filename, dto, model):
    """Runtime twin of the static check: serialise the MINIMAL instance the way the app
    does (Starlette JSONResponse → allow_nan=False) and walk the Swift properties."""
    payload = json.loads(json.dumps(_minimal(model).model_dump(), allow_nan=False))
    swift = _struct(filename, dto)
    for prop_name, key in swift.wire_keys().items():
        if not swift.props[prop_name].optional:
            assert payload.get(key) is not None, f"{dto}.{prop_name}: key {key!r} missing/null"


# ══════════════════════════════════════════════════════════════════════════════════════
# 2. The fields this feature added are Optional on BOTH sides
# ══════════════════════════════════════════════════════════════════════════════════════

_NEW_SWIFT_FIELDS = {
    ("HomeDashboardModels.swift", "TrendingThemeDTO"): ["updatedOn", "changeCount", "return1m", "spark1m"],
    ("ThemeDetailModels.swift", "ThemeConstituentDTO"): ["role", "isNew"],
    ("ThemeDetailModels.swift", "ThemeDetailDTO"): ["updatedOn", "changes", "performance", "insight", "news"],
}
_NEW_PY_FIELDS = {
    TrendingThemeResponse: ["updated_on", "change_count", "return_1m", "spark_1m"],
    ThemeConstituentResponse: ["role", "is_new"],
    ThemeDetailResponse: ["updated_on", "changes", "performance", "insight", "news"],
}
# The nested types exist only since 174. A property may be non-Optional ONLY when the
# backend always sends it non-null (a required str, or a non-None default).
_NESTED = ["ThemeChangeDTO", "ThemePeriodReturnDTO", "ThemePerformanceDTO", "ThemeInsightDTO",
           "ThemeNewsItemDTO"]
_NESTED_EXPECTED_NON_OPTIONAL = {
    "ThemeChangeDTO": {"ticker", "action"},
    "ThemePeriodReturnDTO": {"period"},
    "ThemePerformanceDTO": set(),
    "ThemeInsightDTO": set(),
    "ThemeNewsItemDTO": {"title"},
}


@pytest.mark.parametrize("key", list(_NEW_SWIFT_FIELDS), ids=[k[1] for k in _NEW_SWIFT_FIELDS])
def test_new_swift_fields_are_declared_optional(key):
    filename, dto = key
    swift = _struct(filename, dto)
    for name in _NEW_SWIFT_FIELDS[key]:
        assert name in swift.props, f"{dto}.{name} not found — renamed?"
        assert swift.props[name].type.endswith("?"), (
            f"{dto}.{name} is `{swift.props[name].type}` — an older backend omits it, so a "
            f"non-Optional declaration fails the WHOLE response decode")


@pytest.mark.parametrize("dto", _NESTED)
def test_nested_dto_fields_optional_unless_backend_always_sends_them(dto):
    swift = _struct("ThemeDetailModels.swift", dto)
    model = next(m for _, d, m in PAIRS if d == dto)
    wire = swift.wire_keys()
    non_optional = {p for p in wire if not swift.props[p].optional}
    for prop_name in non_optional:
        field = model.model_fields[wire[prop_name]]
        _, nullable = _py_shape(field.annotation)
        always_sent = not nullable and (field.is_required() or field.default is not None)
        assert always_sent, f"{dto}.{prop_name} is non-Optional but may be absent/null"
    assert non_optional == _NESTED_EXPECTED_NON_OPTIONAL[dto]


@pytest.mark.parametrize("model", list(_NEW_PY_FIELDS), ids=lambda m: m.__name__)
def test_new_pydantic_fields_are_defaulted_and_empty(model):
    for name in _NEW_PY_FIELDS[model]:
        field = model.model_fields[name]
        assert not field.is_required(), f"{model.__name__}.{name} must be optional/defaulted"
        assert field.default in (None, []), f"{model.__name__}.{name} default {field.default!r}"


def test_responses_build_without_any_174_field():
    """The pre-174 construction paths (an un-migrated DB, an old code path) still validate,
    and every new field lands as null / [] — which the Optional Swift side reads as absent."""
    card = TrendingThemeResponse(slug="s", title="T", accent_hex="22D3EE", ticker_count=3)
    dumped = card.model_dump()
    assert all(dumped[k] is None for k in _NEW_PY_FIELDS[TrendingThemeResponse])
    detail = ThemeDetailResponse(slug="s", title="T", accent_hex="22D3EE")
    d = detail.model_dump()
    assert d["updated_on"] is None and d["performance"] is None and d["insight"] is None
    assert d["changes"] == [] and d["news"] == []
    c = ThemeConstituentResponse(ticker="NVDA").model_dump()
    assert c["role"] is None and c["is_new"] is None


def test_api_client_does_not_convert_snake_case():
    """Every explicit snake_case raw value above assumes a plain JSONDecoder."""
    code, _ = scan_swift((IOS / "Core" / "Services" / "APIClient.swift").read_text(encoding="utf-8"))
    assert "JSONDecoder()" in code
    assert "convertFromSnakeCase" not in code


# ══════════════════════════════════════════════════════════════════════════════════════
# 3. Home mapping helpers against the theme_insights_service ROW SHAPE
# ══════════════════════════════════════════════════════════════════════════════════════


def _period(theme: Any = None, bench: Any = None, status: str = "ok") -> Dict[str, Any]:
    """One `performance.periods[<label>]` entry exactly as compute_period writes it."""
    return {"start_date": "2026-08-21", "end_date": "2026-09-22", "sessions": 21,
            "theme_return_pct": theme, "benchmark_return_pct": bench,
            "excess_return_pct": None, "covered_count": 5, "coverage_pct": 100.0,
            "status": status}


def _series(theme: Any, bench: Any = None) -> Dict[str, Any]:
    n = len(theme) if isinstance(theme, list) else 0
    return {"start_date": "2026-08-21", "end_date": "2026-09-22", "step": 1,
            "dates": [f"d{i}" for i in range(n)], "theme": theme,
            "benchmark": bench if bench is not None else [100.0] * n}


def _insights_row(periods: Optional[Dict[str, Any]] = None, *,
                  one_year: Any = None, one_month: Any = None,
                  perf_as_of: Any = "2026-09-22", summary: Any = "Chip names led.",
                  headline: Any = "Chips rally", summary_as_of: Any = "2026-09-22",
                  drivers: Any = None,
                  basket: Any = ("NVDA", "AMD", "ALAB", "CRDO")) -> Dict[str, Any]:
    return {
        "slug": "silicon-rush",
        "as_of": "2026-09-22",
        "performance": {"method": "equal_weight", "label": "current stocks, equal-weighted",
                        "as_of": perf_as_of, "benchmark_symbol": "SPY",
                        "periods": periods if periods is not None else {},
                        # the list the row was computed on — _SILICON_ROW's by default
                        "constituents": [{"ticker": t, "day_change_pct": 0.5}
                                         for t in basket]},
        "series": {"base": 100.0, "benchmark_symbol": "SPY",
                   "one_year": one_year if one_year is not None else {},
                   "one_month": one_month if one_month is not None else {}},
        "summary_headline": headline,
        "summary_text": summary,
        "summary_as_of": summary_as_of,
        "drivers": drivers if drivers is not None else [{"ticker": "nvda", "note": "beat"}],
    }


_FULL_PERIODS = {"1D": _period(1.1, 0.4), "1M": _period(4.2, 1.5),
                 "YTD": _period(-12.5, 8.0), "1Y": _period(30.0, 15.0)}


# ── _iso_date ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("2026-10-01", "2026-10-01"),
    (date(2026, 10, 1), "2026-10-01"),
    (datetime(2026, 10, 1, 23, 59, tzinfo=timezone.utc), "2026-10-01"),
    ("2026-10-01T12:00:00+00:00", "2026-10-01"),
    ("2026-10-01 12:00:00", "2026-10-01"),
    ("2024-02-29", "2024-02-29"),            # leap day is real
])
def test_iso_date_accepts_real_dates(value, expected):
    assert _iso_date(value) == expected


@pytest.mark.parametrize("value", [
    None, "", 0, False, [], "not-a-date", "2026-02-30", "2026-13-01", "2025-02-29",
    20261001, "20261001", "10/01/2026", "  ", "0000-00-00",
])
def test_iso_date_rejects_garbage_as_none(value):
    assert _iso_date(value) is None


# ── _review_change_count ────────────────────────────────────────────────────────────────

def _decision(slug: str, ticker: str, action: str, *, exposure: Any = None,
              source: Any = None, reason: str = "") -> Dict[str, Any]:
    parts: Dict[str, Any] = {}
    if exposure is not None:
        parts["exposure"] = exposure
    if source is not None:
        parts["exposure_source"] = source
    return {"slug": slug, "ticker": ticker, "action": action,
            "reason_text": reason, "score_parts": parts}


_REVIEW_ROWS = [
    _decision("silicon-rush", "NVDA", "kept", exposure=0.9, source="segments"),
    _decision("silicon-rush", "AMD", "kept", exposure=0.3, source="industry"),
    _decision("silicon-rush", "ALAB", "added", exposure=0.7, source="description",
              reason="Added: its core business is built around this theme."),
    _decision("silicon-rush", "CRDO", "returned", exposure=0.6, source="unknown",
              reason="Back: it again ranks among the companies most tied to this theme."),
    _decision("silicon-rush", "INTC", "removed", exposure=0.2, source="segments",
              reason="Rotated out: still related, but other on-theme companies ranked ahead of it in our monthly review."),
    _decision("silicon-rush", "GFS", "removed",
              reason="Removed: its size or trading volume fell below our minimums."),
    _decision("cyber-wars", "CRWD", "kept", exposure=0.95, source="segments"),
]


def _review() -> LatestReview:
    return build_review("2026-10-01", _REVIEW_ROWS)


@pytest.mark.parametrize("review,slug,as_of,expected", [
    (None, "silicon-rush", "2026-10-01", None),
    (LatestReview(run_month=None), "silicon-rush", "2026-10-01", None),
    ("REVIEW", "never-reviewed", "2026-10-01", None),
    ("REVIEW", "silicon-rush", None, None),               # reviewed but no published date
    ("REVIEW", "silicon-rush", "garbage", None),
    ("REVIEW", "silicon-rush", "2026-10-01", 2),          # max(2 in, 2 out) = 2
    ("REVIEW", "cyber-wars", "2026-10-01", 0),            # reviewed, nothing better: honest 0
    ("REVIEW", "silicon-rush", date(2026, 10, 1), 2),
    (object(), "silicon-rush", "2026-10-01", None),       # not a review at all
])
def test_review_change_count(review, slug, as_of, expected):
    if review == "REVIEW":
        review = _review()
    result = _review_change_count(review, slug, as_of)
    assert result == expected
    if expected is not None:
        assert type(result) is int


# ── _card_insight_fields ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("row", [None, {}])
def test_card_fields_absent_row_is_empty(row):
    assert _card_insight_fields(row) == {}


def test_card_fields_percent_becomes_fraction_and_spark_passes_through():
    row = _insights_row(_FULL_PERIODS, one_month=_series([100.0, 101.5, 99.0, 104.2]))
    out = _card_insight_fields(row)
    assert set(out) == {"return_1m", "spark_1m"}
    assert out["return_1m"] == pytest.approx(0.042)
    assert out["spark_1m"] == [100.0, 101.5, 99.0, 104.2]
    # and the fields slot straight into the response the card decodes
    card = TrendingThemeResponse(slug="s", title="T", accent_hex="22D3EE", ticker_count=4, **out)
    json.dumps(card.model_dump(), allow_nan=False)


@pytest.mark.parametrize("pct,expected", [
    (-12.5, -0.125), (0.0, 0.0), (-100.0, -1.0), (250.0, 2.5), ("4.2", 0.042), (1e6, 1e4),
])
def test_card_return_1m_conversion_values(pct, expected):
    out = _card_insight_fields(_insights_row({"1M": _period(pct, 1.0)}))
    assert out["return_1m"] == pytest.approx(expected)


@pytest.mark.parametrize("period", [
    _period(4.2, 1.0, status="low_coverage"),
    _period(4.2, 1.0, status="insufficient_history"),
    _period(4.2, 1.0, status="OK"),                # an unknown status is not "ok"
    {k: v for k, v in _period(4.2, 1.0).items() if k != "status"},   # older writer
    _period(None, 1.0),
    _period(float("nan"), 1.0),
    _period(float("inf"), 1.0),
    _period("abc", 1.0),
    "4.2",                                         # a period that is not a dict
    None,
])
def test_card_return_1m_is_none_unless_measured(period):
    out = _card_insight_fields(_insights_row({"1M": period}))
    assert out["return_1m"] is None


def test_card_return_1m_absent_period_is_none():
    assert _card_insight_fields(_insights_row({"1D": _period(1.0)}))["return_1m"] is None


@pytest.mark.parametrize("theme", [
    [100.0, None, 102.0],                 # one gap → whole series dropped, never shifted
    [100.0, float("nan"), 102.0],
    [100.0, float("inf")],
    [100.0, "x"],
    [100.0],                              # one point is not a line
    [],
    None,
    "100,101",
    {"0": 100.0, "1": 101.0},
])
def test_card_spark_with_any_gap_or_too_short_is_none(theme):
    out = _card_insight_fields(_insights_row(_FULL_PERIODS, one_month=_series(theme)))
    assert out["spark_1m"] is None
    assert out["return_1m"] == pytest.approx(0.042)       # the return still stands alone


def test_card_spark_coerces_ints_to_floats():
    out = _card_insight_fields(_insights_row({}, one_month=_series([100, 101, 99])))
    assert out["spark_1m"] == [100.0, 101.0, 99.0]
    assert all(isinstance(v, float) for v in out["spark_1m"])


# ── _detail_insight_fields ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("row", [None, {}])
def test_detail_fields_absent_row_is_empty(row):
    assert _detail_insight_fields(row) == {}


def test_detail_fields_full_row():
    row = _insights_row(_FULL_PERIODS,
                        one_year=_series([100.0, 110.0, 130.0], [100.0, 105.0, 115.0]),
                        headline="  Chips rally  ", summary="  Chip names led the theme.  ",
                        drivers=[{"ticker": "nvda", "note": "beat"}, {"ticker": "Amd", "note": "x"}])
    out = _detail_insight_fields(row)
    perf = out["performance"]
    assert isinstance(perf, ThemePerformanceResponse)
    assert [p.period for p in perf.periods] == ["1M", "YTD", "1Y"]     # 1D is not a detail row
    assert [p.theme for p in perf.periods] == pytest.approx([0.042, -0.125, 0.30])
    assert [p.benchmark for p in perf.periods] == pytest.approx([0.015, 0.08, 0.15])
    assert perf.as_of == "2026-09-22"
    assert perf.benchmark_label == "S&P 500 ETF"
    assert perf.theme_series == [100.0, 110.0, 130.0]
    assert perf.benchmark_series == [100.0, 105.0, 115.0]
    ins = out["insight"]
    assert isinstance(ins, ThemeInsightResponse)
    assert ins.headline == "Chips rally" and ins.summary == "Chip names led the theme."
    assert ins.as_of == "2026-09-22"
    assert ins.tickers == ["NVDA", "AMD"]
    detail = ThemeDetailResponse(slug="s", title="T", accent_hex="22D3EE", **out)
    json.dumps(detail.model_dump(), allow_nan=False)


def test_detail_non_ok_period_nulls_theme_but_keeps_benchmark():
    periods = {"1M": _period(4.2, 1.5, status="low_coverage"), "1Y": _period(30.0, 15.0)}
    perf = _detail_insight_fields(_insights_row(periods))["performance"]
    by = {p.period: p for p in perf.periods}
    assert by["1M"].theme is None and by["1M"].benchmark == pytest.approx(0.015)
    assert by["YTD"].theme is None and by["YTD"].benchmark is None     # absent → both null
    assert by["1Y"].theme == pytest.approx(0.30)


@pytest.mark.parametrize("periods", [
    {},
    {"1D": _period(1.1, 0.4)},                                   # only 1D: not a detail row
    {"1M": _period(4.2, 1.5, status="low_coverage"),
     "YTD": _period(None, 2.0), "1Y": _period(float("nan"), 3.0)},  # benchmark only
    {"1m": _period(4.2), "ytd": _period(1.0)},                   # wrong-case labels
])
def test_detail_performance_omitted_when_no_period_has_a_theme_value(periods):
    out = _detail_insight_fields(_insights_row(periods))
    assert "performance" not in out
    assert "insight" in out                                        # insight is independent


def test_detail_theme_series_gap_drops_both_series_but_keeps_periods():
    row = _insights_row(_FULL_PERIODS, one_year=_series([100.0, None, 120.0], [100.0, 101.0, 102.0]))
    perf = _detail_insight_fields(row)["performance"]
    assert perf.theme_series == [] and perf.benchmark_series == []
    assert perf.periods[0].theme == pytest.approx(0.042)


def test_detail_benchmark_series_gap_drops_only_the_benchmark():
    # build_index_series writes None where the benchmark lacks a bar — the realistic case
    row = _insights_row(_FULL_PERIODS, one_year=_series([100.0, 110.0, 120.0], [100.0, None, 102.0]))
    perf = _detail_insight_fields(row)["performance"]
    assert perf.theme_series == [100.0, 110.0, 120.0]
    assert perf.benchmark_series == []


def test_detail_benchmark_series_of_different_length_is_dropped():
    row = _insights_row(_FULL_PERIODS, one_year=_series([100.0, 110.0, 120.0], [100.0, 101.0]))
    perf = _detail_insight_fields(row)["performance"]
    assert perf.theme_series == [100.0, 110.0, 120.0]
    assert perf.benchmark_series == []


@pytest.mark.parametrize("perf_as_of,row_as_of,expected", [
    ("2026-09-22", "2026-09-19", "2026-09-22"),
    (None, "2026-09-19", "2026-09-19"),          # falls back to the row's own date
    ("", "2026-09-19", "2026-09-19"),
    (None, None, None),
    ("garbage", None, None),
])
def test_detail_performance_as_of(perf_as_of, row_as_of, expected):
    row = _insights_row(_FULL_PERIODS, perf_as_of=perf_as_of)
    row["as_of"] = row_as_of
    assert _detail_insight_fields(row)["performance"].as_of == expected


@pytest.mark.parametrize("summary", ["", "   \n\t ", None])
def test_detail_insight_omitted_when_summary_empty(summary):
    out = _detail_insight_fields(_insights_row(_FULL_PERIODS, summary=summary))
    assert "insight" not in out
    assert "performance" in out


def test_detail_insight_tolerates_missing_headline_and_messy_drivers():
    row = _insights_row({}, headline=None, summary_as_of="nope",
                        drivers=[{"ticker": "nvda"}, {"ticker": ""}, {"note": "no ticker"},
                                 "AMD", None, 7, {"ticker": None}, {"ticker": "tsm", "note": "x"}])
    ins = _detail_insight_fields(row)["insight"]
    assert ins.headline == ""
    assert ins.as_of is None
    assert ins.tickers == ["NVDA", "TSM"]


def test_detail_insight_drivers_not_a_list_yield_no_tickers():
    row = _insights_row({})
    row["drivers"] = {"ticker": "NVDA"}                       # iterating a dict = its keys
    assert _detail_insight_fields(row)["insight"].tickers == []
    row["drivers"] = None
    assert _detail_insight_fields(row)["insight"].tickers == []


# ── The REAL writer's output, read back through the REAL normalizer ─────────────────────

def _trading_days(end: date, count: int) -> List[date]:
    from app.utils.market_hours import is_trading_day

    out: List[date] = []
    d = end
    while len(out) < count:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def test_round_trip_from_compute_theme_performance():
    """Build a row with theme_insights_service's own pure writer and its own read-side
    normalizer, then map it: the Home helpers must read exactly what the writer wrote."""
    from app.services import theme_insights_service as tis

    days = _trading_days(date(2026, 9, 22), 300)
    as_of = days[-1]
    closes = {
        "AAA": {d: 100.0 + i * 0.5 for i, d in enumerate(days)},
        "BBB": {d: 200.0 - i * 0.2 for i, d in enumerate(days)},
        "CCC": {d: 50.0 + (i % 7) for i, d in enumerate(days)},
    }
    bench = {d: 400.0 + i * 0.1 for i, d in enumerate(days)}
    perf = tis.compute_theme_performance(closes, bench, as_of, benchmark_symbol="SPY")
    assert perf.usable, perf.reason
    raw = {"slug": "s", "as_of": as_of, "performance": json.dumps(perf.performance),
           "series": perf.series, "summary_headline": "H", "summary_text": "S",
           "summary_as_of": as_of, "drivers": [{"ticker": "aaa", "note": "n"}]}
    row = tis._normalize_row(raw)          # JSONB-as-text is decoded exactly as in prod

    card = _card_insight_fields(row)
    stored_1m = perf.performance["periods"]["1M"]["theme_return_pct"]
    assert card["return_1m"] == pytest.approx(stored_1m / 100.0)
    assert card["spark_1m"] == perf.series["one_month"]["theme"]
    assert len(card["spark_1m"]) >= 2

    detail = _detail_insight_fields(row)
    p = detail["performance"]
    for got in p.periods:
        stored = perf.performance["periods"][got.period]
        assert got.theme == pytest.approx(stored["theme_return_pct"] / 100.0)
        assert got.benchmark == pytest.approx(stored["benchmark_return_pct"] / 100.0)
    assert p.theme_series == perf.series["one_year"]["theme"]
    assert p.benchmark_series == perf.series["one_year"]["benchmark"]
    assert p.as_of == as_of.isoformat()
    assert detail["insight"].tickers == ["AAA"]
    json.dumps(ThemeDetailResponse(slug="s", title="T", accent_hex="22D3EE", **detail).model_dump(),
               allow_nan=False)


# ── Malformed nested JSONB (a Studio hand-edit; _normalize_row only guards the TOP level) ──

@pytest.mark.parametrize("mutate", [
    lambda r: r["performance"].__setitem__("periods", ["1M", 4.2]),
    lambda r: r["series"].__setitem__("one_month", [100.0, 101.0]),
], ids=["periods-is-a-list", "one_month-is-a-list"])
def test_regression_card_insight_fields_crash_on_nested_non_dict(mutate):
    """REGRESSION (fixed 2026-09-23). Was: A nested JSONB value of the wrong type must degrade to "no insight", like every
    other malformation here. It raises AttributeError instead — and _build_themes calls
    this helper unguarded inside its card loop (see the end-to-end regression test below)."""
    row = _insights_row(_FULL_PERIODS, one_month=_series([100.0, 101.0]))
    mutate(row)
    try:
        out = _card_insight_fields(row)
    except AttributeError as exc:  # pragma: no cover — the bug
        pytest.fail(f"_card_insight_fields raised {type(exc).__name__}: {exc}")
    assert out.get("return_1m") is None or isinstance(out["return_1m"], float)


@pytest.mark.parametrize("mutate", [
    lambda r: r["performance"].__setitem__("periods", ["1M", 4.2]),
    lambda r: r["series"].__setitem__("one_year", [100.0, 101.0]),
], ids=["periods-is-a-list", "one_year-is-a-list"])
def test_regression_detail_insight_fields_crash_on_nested_non_dict(mutate):
    row = _insights_row(_FULL_PERIODS, one_year=_series([100.0, 101.0]))
    mutate(row)
    try:
        _detail_insight_fields(row)
    except AttributeError as exc:  # pragma: no cover — the bug
        pytest.fail(f"_detail_insight_fields raised {type(exc).__name__}: {exc}")


# ── _theme_news ─────────────────────────────────────────────────────────────────────────

class _FakeNews:
    def __init__(self, payload: Any = None, exc: Optional[BaseException] = None):
        self.payload, self.exc, self.calls = payload, exc, []

    async def get_index_news(self, symbol, limit=50, news_tickers=""):
        self.calls.append({"symbol": symbol, "limit": limit, "news_tickers": news_tickers})
        if self.exc is not None:
            raise self.exc
        return self.payload


def _patch_news(monkeypatch, fake: _FakeNews) -> None:
    from app.services import news_cache_service

    # `_theme_news` imports get_news_cache_service INSIDE the function → patch the source.
    monkeypatch.setattr(news_cache_service, "get_news_cache_service", lambda: fake)


def _article(i: int, **over: Any) -> Dict[str, Any]:
    a = {"headline": f"Headline {i}", "source_name": "Reuters",
         "article_url": f"https://news.example/{i}", "published_at": "2026-09-22T10:00:00Z",
         "related_tickers": ["nvda", "amd"]}
    a.update(over)
    return a


@pytest.mark.asyncio
async def test_theme_news_maps_articles_and_caps(monkeypatch):
    articles = [_article(0, headline="  Padded  ", source_name="  Bloomberg "),
                _article(1, headline="   "),                          # blank → skipped
                _article(2, related_tickers=[], article_url="", published_at=None),
                _article(3, related_tickers=None)]
    articles += [_article(i) for i in range(4, 20)]
    fake = _FakeNews({"articles": articles})
    _patch_news(monkeypatch, fake)
    tickers = [f"T{i}" for i in range(20)]
    items = await hds._theme_news("silicon-rush", tickers)

    assert len(items) == 8                                          # capped
    assert all(isinstance(x, ThemeNewsItemResponse) for x in items)
    assert items[0].title == "Padded" and items[0].source == "Bloomberg"
    assert items[0].ticker == "NVDA"                                 # first related, upper-cased
    assert items[1].title == "Headline 2"                            # the blank one dropped
    assert items[1].ticker is None and items[1].url is None and items[1].published_at is None
    assert items[2].ticker is None
    assert fake.calls == [{"symbol": "THEME-silicon-rush", "limit": 8,
                           "news_tickers": ",".join(tickers[:12])}]
    json.dumps([x.model_dump() for x in items], allow_nan=False)


@pytest.mark.asyncio
async def test_theme_news_without_tickers_never_calls_the_service(monkeypatch):
    fake = _FakeNews({"articles": [_article(0)]})
    _patch_news(monkeypatch, fake)
    assert await hds._theme_news("s", []) == []
    assert fake.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, {}, {"articles": None}, {"articles": []}])
async def test_theme_news_empty_payloads(monkeypatch, payload):
    _patch_news(monkeypatch, _FakeNews(payload))
    assert await hds._theme_news("s", ["NVDA"]) == []


@pytest.mark.asyncio
async def test_theme_news_service_failure_degrades_to_empty(monkeypatch):
    _patch_news(monkeypatch, _FakeNews(exc=RuntimeError("fmp quota")))
    assert await hds._theme_news("s", ["NVDA"]) == []


# ══════════════════════════════════════════════════════════════════════════════════════
# 4. _build_theme_detail / _build_themes end-to-end
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _clean_home_caches():
    for cache in (HomeDashboardService._themes_cache, HomeDashboardService._themes_inflight,
                  HomeDashboardService._theme_detail_cache,
                  HomeDashboardService._theme_detail_inflight):
        cache.clear()
    yield
    for cache in (HomeDashboardService._themes_cache, HomeDashboardService._themes_inflight,
                  HomeDashboardService._theme_detail_cache,
                  HomeDashboardService._theme_detail_inflight):
        cache.clear()


class _FakeFMP:
    """Canned quotes keyed by canonical symbol; records every batch request."""

    def __init__(self, quotes: Dict[str, Dict[str, Any]], fail: bool = False):
        self.quotes, self.fail, self.batch_calls = quotes, fail, []

    async def get_batch_quotes_bulk(self, symbols):
        self.batch_calls.append(list(symbols))
        if self.fail:
            raise RuntimeError("quote endpoint down")
        return [self.quotes[s] for s in symbols if s in self.quotes]


def _q(sym: str, name: str, cap: float, pct: float = 1.0) -> Dict[str, Any]:
    return {"symbol": sym, "name": name, "price": 100.0, "changesPercentage": pct, "marketCap": cap}


_QUOTES = {
    "NVDA": _q("NVDA", "NVIDIA Corporation", 4e12, 2.0),
    "AMD": _q("AMD", "Advanced Micro Devices", 3e11, -1.0),
    "ALAB": _q("ALAB", "Astera Labs", 2e10, 5.0),
    "CRDO": _q("CRDO", "Credo Technology", 1e10, 3.0),
    "INTC": _q("INTC", "Intel Corporation", 9e10),
    "GFS": _q("GFS", "GlobalFoundries", 3e10),
    "CRWD": _q("CRWD", "CrowdStrike", 9e10, 1.0),
}
@pytest.fixture(autouse=True)
def _card_clock(monkeypatch):
    """The card trend is drawn only from a recent insights row; these rows are dated
    2026-09-22, so pin the clock to that evening."""
    monkeypatch.setattr(hds, "_theme_insights_now",
                        lambda: datetime(2026, 9, 23, 1, 0, tzinfo=timezone.utc))


_SILICON_ROW = {"slug": "silicon-rush", "title": "The Silicon Rush", "subtitle": "Chips",
                "image_url": None, "accent_hex": "22D3EE",
                "tickers": ["NVDA", "AMD", "ALAB", "CRDO"], "tickers_as_of": "2026-10-01"}


def _service(quotes=None, *, row: Any = "__unset__", rows: Any = "__unset__", fail=False):
    svc = HomeDashboardService()
    svc.fmp = _FakeFMP(_QUOTES if quotes is None else quotes, fail=fail)  # type: ignore[assignment]
    svc.price = PriceFromFMPFake(svc.fmp)
    if row != "__unset__":
        svc._read_theme_row = lambda slug: row  # type: ignore[assignment]
    if rows != "__unset__":
        svc._read_theme_rows = lambda: rows  # type: ignore[assignment]
    return svc


def _async_value(value: Any, calls: Optional[list] = None):
    async def _f(*args, **kwargs):
        if calls is not None:
            calls.append(args)
        return value
    return _f


def _patch_sources(monkeypatch, *, review: Any = None, insights: Any = None, news: Any = None,
                   insight_calls: Optional[list] = None) -> None:
    monkeypatch.setattr(hds, "_latest_theme_review",
                        _async_value(review if review is not None else LatestReview(run_month=None)))
    monkeypatch.setattr(hds, "_latest_theme_insights",
                        _async_value(insights if insights is not None else {}, insight_calls))
    monkeypatch.setattr(hds, "_theme_news", _async_value(news if news is not None else []))


@pytest.mark.asyncio
async def test_detail_applies_review_roles_new_and_changes(monkeypatch):
    insights = {"silicon-rush": _insights_row(_FULL_PERIODS, one_year=_series([100.0, 120.0]))}
    news = [ThemeNewsItemResponse(title="Chips rally", ticker="NVDA")]
    _patch_sources(monkeypatch, review=_review(), insights=insights, news=news)
    svc = _service(row=dict(_SILICON_ROW))
    detail = await svc._build_theme_detail("silicon-rush")

    assert [c.ticker for c in detail.constituents] == ["NVDA", "AMD", "ALAB", "CRDO"]
    # Roles are the REVIEW's call (read_model.role_of decides what counts as a pure play);
    # the detail's contract is to carry them onto the right rows, unchanged.
    reviewed = _review().themes["silicon-rush"]
    assert {c.ticker: c.role for c in detail.constituents} == {
        t: reviewed.roles.get(t) for t in ("NVDA", "AMD", "ALAB", "CRDO")}
    assert detail.constituents[0].role == "pure_play"         # 90% of revenue in segments
    assert detail.constituents[1].role == "diversified"       # known, smaller share
    assert detail.constituents[3].role is None                # no evidence → no tag
    assert {c.ticker: c.is_new for c in detail.constituents} == {
        "NVDA": False, "AMD": False, "ALAB": True, "CRDO": True}
    # added → returned → removed, ticker order within each
    assert [(c.action, c.ticker) for c in detail.changes] == [
        ("added", "ALAB"), ("returned", "CRDO"), ("removed", "GFS"), ("removed", "INTC")]
    names = {c.ticker: c.company_name for c in detail.changes}
    assert names == {"ALAB": "Astera Labs", "CRDO": "Credo Technology",
                     "GFS": "GlobalFoundries", "INTC": "Intel Corporation"}
    assert detail.changes[0].reason.startswith("Added:")
    # removed names are fetched SEPARATELY, and only for tickers not already in the list
    assert svc.fmp.batch_calls == [["ALAB", "AMD", "CRDO", "NVDA"], ["GFS", "INTC"]]
    assert detail.updated_on == "2026-10-01"
    assert detail.performance is not None and detail.insight is not None
    assert detail.news == news
    json.dumps(detail.model_dump(), allow_nan=False)


@pytest.mark.asyncio
async def test_detail_removed_ticker_still_listed_is_not_refetched(monkeypatch):
    """An editor re-added a removed stock in Studio after the run: its name comes from the
    live list, and no extra quote call is made."""
    _patch_sources(monkeypatch, review=_review())
    row = dict(_SILICON_ROW, tickers=["NVDA", "AMD", "ALAB", "CRDO", "INTC", "GFS"])
    svc = _service(row=row)
    detail = await svc._build_theme_detail("silicon-rush")
    assert len(svc.fmp.batch_calls) == 1
    assert {c.ticker: c.company_name for c in detail.changes}["INTC"] == "Intel Corporation"


@pytest.mark.asyncio
async def test_detail_added_ticker_edited_out_after_the_review_still_gets_a_name(monkeypatch):
    """An editor removed an ADDED stock in Studio after publish: the change row still
    names it (looked up with the removals), and it is not in the constituents."""
    _patch_sources(monkeypatch, review=_review())
    svc = _service(row=dict(_SILICON_ROW, tickers=["NVDA", "AMD", "CRDO"]))
    detail = await svc._build_theme_detail("silicon-rush")
    assert "ALAB" not in {c.ticker for c in detail.constituents}
    assert {c.ticker: c.company_name for c in detail.changes}["ALAB"] == "Astera Labs"
    assert sorted(svc.fmp.batch_calls[1]) == ["ALAB", "GFS", "INTC"]


@pytest.mark.asyncio
async def test_detail_renders_exactly_as_before_when_nothing_is_published(monkeypatch):
    _patch_sources(monkeypatch)                       # empty review, no insights, no news
    row = {k: v for k, v in _SILICON_ROW.items() if k != "tickers_as_of"}
    svc = _service(row=row)
    detail = await svc._build_theme_detail("silicon-rush")
    assert len(detail.constituents) == 4
    assert all(c.role is None and c.is_new is None for c in detail.constituents)
    dumped = detail.model_dump()
    assert dumped["updated_on"] is None and dumped["changes"] == []
    assert dumped["performance"] is None and dumped["insight"] is None and dumped["news"] == []
    assert svc.fmp.batch_calls == [["ALAB", "AMD", "CRDO", "NVDA"]]    # no name fetch


@pytest.mark.asyncio
async def test_detail_review_for_other_themes_only_leaves_this_one_untouched(monkeypatch):
    review = build_review("2026-10-01", [r for r in _REVIEW_ROWS if r["slug"] == "cyber-wars"])
    _patch_sources(monkeypatch, review=review)
    detail = await _service(row=dict(_SILICON_ROW))._build_theme_detail("silicon-rush")
    assert detail.changes == []
    assert all(c.role is None and c.is_new is None for c in detail.constituents)
    # Not in the latest review → no "Reviewed <date> · No changes" card at all.
    assert detail.updated_on is None


@pytest.mark.asyncio
async def test_detail_name_lookup_failure_keeps_changes_with_blank_names(monkeypatch):
    _patch_sources(monkeypatch, review=_review())
    svc = _service(row=dict(_SILICON_ROW))
    real = svc.fmp.get_batch_quotes_bulk

    async def _second_call_fails(symbols):
        if svc.fmp.batch_calls:                       # the constituents call already happened
            svc.fmp.batch_calls.append(list(symbols))
            raise RuntimeError("names down")
        return await real(symbols)

    svc.fmp.get_batch_quotes_bulk = _second_call_fails  # type: ignore[assignment]
    detail = await svc._build_theme_detail("silicon-rush")
    names = {c.ticker: c.company_name for c in detail.changes}
    assert names["GFS"] == "" and names["INTC"] == ""      # iOS falls back to the ticker
    assert names["ALAB"] == "Astera Labs"


@pytest.mark.asyncio
async def test_detail_quote_outage_still_ships_review_and_insights(monkeypatch):
    _patch_sources(monkeypatch, review=_review(),
                   insights={"silicon-rush": _insights_row(_FULL_PERIODS)})
    svc = _service(row=dict(_SILICON_ROW), fail=True)
    detail = await svc._build_theme_detail("silicon-rush")
    assert detail.constituents == []
    assert [c.ticker for c in detail.changes] == ["ALAB", "CRDO", "GFS", "INTC"]
    assert all(c.company_name == "" for c in detail.changes)
    assert detail.performance is not None


@pytest.mark.asyncio
async def test_detail_survives_every_side_read_failing(monkeypatch):
    """The REAL degrade guards (not the patched wrappers): the review read, the insights
    read and the news service all raise, and the list still renders."""
    from app.services import theme_insights_service

    async def _boom(*a, **k):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(read_model, "latest_review", _boom)
    monkeypatch.setattr(theme_insights_service, "get_latest_insights", _boom)
    _patch_news(monkeypatch, _FakeNews(exc=RuntimeError("news down")))
    svc = _service(row=dict(_SILICON_ROW))
    detail = await svc.get_theme_detail("silicon-rush")
    assert detail is not None and len(detail.constituents) == 4
    assert detail.changes == [] and detail.performance is None and detail.insight is None
    assert detail.news == []
    assert all(c.role is None and c.is_new is None for c in detail.constituents)


@pytest.mark.asyncio
async def test_detail_survives_the_read_model_itself_failing(monkeypatch):
    """One level deeper: the Supabase read inside read_model raises; latest_review degrades
    to an empty review and never propagates."""
    monkeypatch.setattr(read_model, "_cache", (0.0, 0.0, None))
    monkeypatch.setattr(read_model, "_inflight", None)

    def _read_fails():
        raise RuntimeError("relation theme_rotation_runs does not exist")   # pre-174 DB

    monkeypatch.setattr(read_model, "_read_latest", _read_fails)
    monkeypatch.setattr(hds, "_latest_theme_insights", _async_value({}))
    monkeypatch.setattr(hds, "_theme_news", _async_value([]))
    detail = await _service(row=dict(_SILICON_ROW))._build_theme_detail("silicon-rush")
    assert detail.changes == [] and len(detail.constituents) == 4


@pytest.mark.asyncio
async def test_regression_one_malformed_insights_row_fails_the_whole_detail_screen(monkeypatch):
    """REGRESSION (fixed 2026-09-23). Was: Same nested-JSONB crash on the detail path: `_detail_insight_fields` raises inside
    `_build_theme_detail`, `get_theme_detail` re-raises, and the endpoint answers an error
    instead of the company list. Expected: the list renders; the valid period returns
    still show and only the malformed chart series is dropped (iOS hides a chart under 2
    points)."""
    bad = _insights_row(_FULL_PERIODS, one_year=_series([100.0, 110.0]))
    bad["series"]["one_year"] = [100.0, 110.0]
    _patch_sources(monkeypatch, review=_review(), insights={"silicon-rush": bad})
    svc = _service(row=dict(_SILICON_ROW))
    try:
        detail = await svc.get_theme_detail("silicon-rush")
    except AttributeError as exc:  # pragma: no cover — the bug
        pytest.fail(f"get_theme_detail raised {type(exc).__name__}: {exc}")
    assert detail is not None and len(detail.constituents) == 4
    assert detail.performance is not None
    assert detail.performance.theme_series == [] and detail.performance.benchmark_series == []


@pytest.mark.asyncio
async def test_regression_detail_review_lookups_miss_a_dotted_class_share_ticker(monkeypatch):
    """REGRESSION (fixed 2026-09-23). Was: The detail joins everything through `_canonical_symbol` (BRK.B → BRK-B) because FMP
    disagrees on the delimiter, and `trending_themes.tickers` may hold the dotted form (see
    test_home_dashboard_themes.py::test_theme_change_class_share_join_resolves). The
    rotation stores decision tickers with `.upper()` only, so the review is keyed "BRK.B"
    while the constituent is "BRK-B": its role and "New" tag are lost, and every dotted
    change row (added or removed) loses its company name, because `_company_names` returns
    canonical keys that `names.get(ch.ticker)` then looks up with the dotted form."""
    review = build_review("2026-10-01", [
        _decision("t", "BRK.B", "added", exposure=0.8, source="segments", reason="Added: x"),
        _decision("t", "NVDA", "kept", exposure=0.9, source="segments"),
        _decision("t", "HEI.A", "removed", reason="Removed: y"),
    ])
    _patch_sources(monkeypatch, review=review)
    quotes = {"BRK-B": _q("BRK-B", "Berkshire Hathaway", 1e12), "NVDA": _QUOTES["NVDA"],
              "HEI-A": _q("HEI-A", "HEICO Corporation", 3e10)}
    row = {"slug": "t", "title": "T", "tickers": ["BRK.B", "NVDA"], "tickers_as_of": "2026-10-01"}
    detail = await _service(quotes, row=row)._build_theme_detail("t")

    brk = next(c for c in detail.constituents if c.ticker == "BRK-B")
    problems = []
    if brk.role != "pure_play":
        problems.append(f"BRK-B role={brk.role!r} (review has 'pure_play' under 'BRK.B')")
    if brk.is_new is not True:
        problems.append(f"BRK-B is_new={brk.is_new!r} (added this month)")
    hei = next(c for c in detail.changes if c.action == "removed")
    if hei.company_name != "HEICO Corporation":
        problems.append(f"removed HEI.A company_name={hei.company_name!r}")
    added = next(c for c in detail.changes if c.action == "added")
    if added.company_name != "Berkshire Hathaway":
        problems.append(f"added BRK.B company_name={added.company_name!r}")
    assert not problems, "; ".join(problems)


# ── _build_themes ───────────────────────────────────────────────────────────────────────

_CARD_ROWS = [
    dict(_SILICON_ROW, sort_order=0),
    {"slug": "cyber-wars", "title": "Cyber Wars", "accent_hex": "F43F5E",
     "tickers": ["CRWD"], "sort_order": 1},                          # never published
]


@pytest.mark.asyncio
async def test_cards_carry_review_and_insight_fields(monkeypatch):
    insight_calls: list = []
    insights = {"silicon-rush": _insights_row(_FULL_PERIODS,
                                              one_month=_series([100.0, 98.0, 104.2]))}
    _patch_sources(monkeypatch, review=_review(), insights=insights, insight_calls=insight_calls)
    result = await _service(rows=[dict(r) for r in _CARD_ROWS])._build_themes()
    by = {t.slug: t for t in result.themes}

    s = by["silicon-rush"]
    assert s.updated_on == "2026-10-01" and s.change_count == 2
    assert s.return_1m == pytest.approx(0.042) and s.spark_1m == [100.0, 98.0, 104.2]
    assert s.ticker_count == 4

    c = by["cyber-wars"]
    # reviewed (CRWD kept) but tickers_as_of never written → no "Updated"/count claims
    assert c.updated_on is None and c.change_count is None
    assert c.return_1m is None and c.spark_1m is None
    assert insight_calls == [(["silicon-rush", "cyber-wars"],)]
    json.dumps(result.model_dump(), allow_nan=False)


@pytest.mark.asyncio
async def test_card_with_date_but_not_in_latest_review_has_no_count(monkeypatch):
    """A theme dated by an OLDER run but absent from the latest review: the date stands,
    the count (which describes the latest review) does not."""
    review = build_review("2026-10-01", [r for r in _REVIEW_ROWS if r["slug"] == "cyber-wars"])
    _patch_sources(monkeypatch, review=review)
    result = await _service(rows=[dict(_SILICON_ROW, sort_order=0)])._build_themes()
    t = result.themes[0]
    assert t.updated_on == "2026-10-01" and t.change_count is None


@pytest.mark.asyncio
async def test_cards_render_exactly_as_before_when_nothing_is_published(monkeypatch):
    _patch_sources(monkeypatch)
    rows = [{k: v for k, v in r.items() if k != "tickers_as_of"} for r in _CARD_ROWS]
    result = await _service(rows=rows)._build_themes()
    assert [t.slug for t in result.themes] == ["silicon-rush", "cyber-wars"]
    for t in result.themes:
        d = t.model_dump()
        assert all(d[k] is None for k in _NEW_PY_FIELDS[TrendingThemeResponse])
    assert result.themes[0].change_percent == pytest.approx(2.25)   # avg(2, -1, 5, 3)


@pytest.mark.asyncio
async def test_cards_survive_review_and_insights_reads_failing(monkeypatch):
    from app.services import theme_insights_service

    async def _boom(*a, **k):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(read_model, "latest_review", _boom)
    monkeypatch.setattr(theme_insights_service, "get_latest_insights", _boom)
    result = await _service(rows=[dict(r) for r in _CARD_ROWS]).get_themes()
    assert [t.slug for t in result.themes] == ["silicon-rush", "cyber-wars"]
    s = result.themes[0]
    assert s.updated_on == "2026-10-01"            # the row's own date survives
    assert s.change_count is None and s.return_1m is None and s.spark_1m is None


@pytest.mark.asyncio
async def test_cards_nan_laden_insights_never_reach_the_wire(monkeypatch):
    insights = {"silicon-rush": _insights_row(
        {"1M": _period(float("nan"), float("inf"))},
        one_month=_series([100.0, float("nan"), 101.0]))}
    _patch_sources(monkeypatch, review=_review(), insights=insights)
    result = await _service(rows=[dict(_SILICON_ROW, sort_order=0)])._build_themes()
    json.dumps(result.model_dump(), allow_nan=False)       # would raise on NaN/inf
    assert result.themes[0].return_1m is None and result.themes[0].spark_1m is None


@pytest.mark.asyncio
async def test_regression_one_malformed_insights_row_hides_every_theme_card(monkeypatch):
    """REGRESSION (fixed 2026-09-23). Was: Blast radius of the nested-JSONB crash: ONE theme's hand-edited insights row makes
    `_build_themes` raise, `get_themes` degrades to an EMPTY group, and Home hides the whole
    Emerging Frontiers section — for every theme, on every request, until the row is fixed.
    Expected: that one card simply has no insight fields."""
    bad = _insights_row(_FULL_PERIODS)
    bad["performance"]["periods"] = ["1M", 4.2]
    _patch_sources(monkeypatch, review=_review(), insights={"silicon-rush": bad})
    result = await _service(rows=[dict(r) for r in _CARD_ROWS]).get_themes()
    assert [t.slug for t in result.themes] == ["silicon-rush", "cyber-wars"]


# ══════════════════════════════════════════════════════════════════════════════════════
# 5. Migration 174 — static checks
# ══════════════════════════════════════════════════════════════════════════════════════


def sql_statements(sql: str) -> List[str]:
    """Statements with `--` / `/* */` comments removed and whitespace collapsed. `;` inside
    '…' strings or $tag$…$tag$ bodies does not split."""
    stmts: List[str] = []
    buf: List[str] = []
    i, n = 0, len(sql)
    while i < n:
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            assert j != -1, "unterminated block comment"
            i = j + 2
            continue
        c = sql[i]
        if c == "'":
            j = i + 1
            while True:
                j = sql.find("'", j)
                assert j != -1, "unterminated string"
                if sql.startswith("''", j):
                    j += 2
                    continue
                break
            buf.append(sql[i:j + 1])
            i = j + 1
            continue
        if c == "$":
            m = re.match(r"\$(\w*)\$", sql[i:i + 64])
            if m:
                tag = m.group(0)
                j = sql.find(tag, i + len(tag))
                assert j != -1, f"unterminated {tag} body"
                buf.append(sql[i:j + len(tag)])
                i = j + len(tag)
                continue
        if c == ";":
            s = " ".join("".join(buf).split())
            if s:
                stmts.append(s)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    tail = " ".join("".join(buf).split())
    assert not tail, f"trailing statement without ';': {tail[:80]!r}"
    return stmts


def _split_top_level(text: str) -> List[str]:
    parts, depth, cur, in_str = [], 0, [], False
    for ch in text:
        if ch == "'":
            in_str = not in_str
        if not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append("".join(cur).strip())
                cur = []
                continue
        cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


_CONSTRAINT_WORDS = ("PRIMARY", "UNIQUE", "CHECK", "CONSTRAINT", "FOREIGN", "EXCLUDE")


def create_table_columns(stmt: str) -> Dict[str, str]:
    """column name → its definition text, for one CREATE TABLE statement."""
    body = stmt[stmt.index("(") + 1:stmt.rindex(")")]
    cols: Dict[str, str] = {}
    for item in _split_top_level(body):
        first = item.split()[0]
        if first.upper() in _CONSTRAINT_WORDS:
            continue
        cols[first.lower()] = item
    return cols


@pytest.fixture(scope="module")
def m174() -> List[str]:
    return sql_statements(MIGRATION_174.read_text(encoding="utf-8"))


def _stmt(stmts: List[str], pattern: str) -> List[str]:
    rx = re.compile(pattern, re.I)
    return [s for s in stmts if rx.search(s)]


_NEW_TABLES = ("theme_rotation_runs", "theme_rotation_decisions", "theme_relevance_cache",
               "theme_daily_insights")


def _tables_174(m174) -> Dict[str, Dict[str, str]]:
    out = {}
    for s in _stmt(m174, r"^CREATE TABLE"):
        name = re.search(r"CREATE TABLE IF NOT EXISTS (?:public\.)?(\w+)", s, re.I).group(1)
        out[name] = create_table_columns(s)
    return out


def _migrations_sorted() -> List[Path]:
    return sorted(p for p in MIGRATIONS.glob("*.sql") if re.match(r"^\d{3}_", p.name))


def test_174_is_the_file_under_test_and_parses(m174):
    assert MIGRATION_174.exists()
    assert len(m174) > 30
    assert set(_tables_174(m174)) == set(_NEW_TABLES)


def test_174_runs_in_one_transaction(m174):
    assert m174[0].upper() == "BEGIN"
    assert m174[-1].upper() == "COMMIT"
    assert sum(s.upper() in ("BEGIN", "COMMIT") for s in m174) == 2


def test_174_every_create_and_add_is_idempotent(m174):
    problems = []
    for s in m174:
        u = s.upper()
        if u.startswith("CREATE TABLE") and not u.startswith("CREATE TABLE IF NOT EXISTS"):
            problems.append(s[:80])
        if re.match(r"CREATE (UNIQUE )?INDEX", u) and "IF NOT EXISTS" not in u.split(" ON ")[0]:
            problems.append(s[:80])
        if u.startswith("CREATE FUNCTION"):
            problems.append(s[:80])                          # must be CREATE OR REPLACE
        if re.match(r"CREATE (TRIGGER|TYPE|SEQUENCE|VIEW)\b", u):
            problems.append(s[:80])
        for add in re.findall(r"ADD COLUMN (?!IF NOT EXISTS)\w+", u):
            problems.append(f"{add} in {s[:60]}")
        if u.startswith("INSERT") and "ON CONFLICT" not in u:
            problems.append(s[:80])
        if u.startswith("UPDATE") and " WHERE " not in u:
            problems.append(s[:80])
        if u.startswith("DROP") and " IF EXISTS " not in u:
            problems.append(s[:80])
    assert not problems, problems


def test_174_every_policy_is_dropped_before_it_is_created(m174):
    created = 0
    for idx, s in enumerate(m174):
        m = re.match(r'CREATE POLICY "([^"]+)" ON (\S+)', s, re.I)
        if not m:
            continue
        created += 1
        name, table = m.groups()
        drops = [p for p in m174[:idx]
                 if re.match(rf'DROP POLICY IF EXISTS "{re.escape(name)}" ON {re.escape(table)}\b', p, re.I)]
        assert drops, f"CREATE POLICY {name} on {table} has no preceding DROP POLICY IF EXISTS"
    assert created == 4


def test_174_function_is_create_or_replace(m174):
    fns = _stmt(m174, r"^CREATE OR REPLACE FUNCTION public\.publish_theme_rotation\(")
    assert len(fns) == 1


@pytest.mark.parametrize("table", _NEW_TABLES)
def test_174_new_table_is_service_role_only(m174, table):
    t = rf"public\.{table}"
    assert _stmt(m174, rf"^ALTER TABLE {t} ENABLE ROW LEVEL SECURITY$"), f"{table}: RLS"
    assert _stmt(m174, rf"^REVOKE ALL ON {t} FROM anon, authenticated$"), f"{table}: revoke"
    assert _stmt(m174, rf"^GRANT ALL ON {t} TO service_role$"), f"{table}: service grant"
    assert _stmt(m174, rf'^CREATE POLICY "\w+" ON {t} FOR ALL TO service_role USING \(true\) WITH CHECK \(true\)$')
    # no policy or grant for a client role anywhere
    for s in _stmt(m174, rf"\b{t}\b"):
        assert not re.search(r"\bTO (anon|authenticated|public)\b", s, re.I), s
    assert _stmt(m174, rf"^COMMENT ON TABLE {t} IS "), f"{table}: COMMENT ON TABLE (atlas prefers it)"


def test_174_closes_the_trending_themes_read_door(m174):
    t = r"public\.trending_themes"
    assert _stmt(m174, rf'^DROP POLICY IF EXISTS "trending_themes_select_all" ON {t}$')
    assert _stmt(m174, rf"^REVOKE ALL ON {t} FROM anon, authenticated$")
    assert _stmt(m174, rf"^GRANT ALL ON {t} TO service_role$")
    # RLS was enabled in 081; nothing ever disables it
    all_stmts = {p.name: sql_statements(p.read_text(encoding="utf-8")) for p in _migrations_sorted()}
    enabled = [n for n, ss in all_stmts.items()
               if _stmt(ss, r"ALTER TABLE (public\.)?trending_themes ENABLE ROW LEVEL SECURITY")]
    assert enabled and enabled[0] <= MIGRATION_174.name
    for name, ss in all_stmts.items():
        assert not _stmt(ss, r"trending_themes DISABLE ROW LEVEL SECURITY"), name
    # and nothing from 174 on re-opens it
    for name, ss in all_stmts.items():
        if name < MIGRATION_174.name:
            continue
        assert not _stmt(ss, r'CREATE POLICY "trending_themes_select_all"'), name
        for s in _stmt(ss, r"^GRANT .* ON (TABLE )?(public\.)?trending_themes\b"):
            assert not re.search(r"\bTO .*(anon|authenticated|public)\b", s, re.I), (name, s)


def test_174_every_serial_sequence_is_granted(m174):
    tables = _tables_174(m174)
    serials = [(t, c) for t, cols in tables.items() for c, d in cols.items()
               if re.search(r"\b(BIG|SMALL)?SERIAL\b", d, re.I)]
    assert serials == [("theme_rotation_decisions", "id")]
    for t, c in serials:
        assert _stmt(m174, rf"^GRANT USAGE, SELECT ON SEQUENCE public\.{t}_{c}_seq TO service_role$")


def _publish_fn(m174) -> str:
    return _stmt(m174, r"^CREATE OR REPLACE FUNCTION public\.publish_theme_rotation\(")[0]


def test_174_publish_function_is_invoker_with_locked_search_path(m174):
    fn = _publish_fn(m174)
    header = fn[:fn.index("$$")]
    assert "SECURITY DEFINER" not in header.upper()
    assert re.search(r"SET search_path = public, pg_temp", header)
    params = re.search(r"publish_theme_rotation\((.*?)\) RETURNS", fn, re.S).group(1)
    types = tuple(p.split()[1].upper() for p in _split_top_level(params))
    assert types == ("UUID", "JSONB", "DATE")
    sig = r"public\.publish_theme_rotation\(UUID, JSONB, DATE\)"
    assert _stmt(m174, rf"^REVOKE ALL ON FUNCTION {sig} FROM PUBLIC$")
    assert _stmt(m174, rf"^REVOKE ALL ON FUNCTION {sig} FROM anon, authenticated$")
    assert _stmt(m174, rf"^GRANT EXECUTE ON FUNCTION {sig} TO service_role$")
    for s in _stmt(m174, r"^GRANT .*publish_theme_rotation"):
        assert re.search(r"TO service_role$", s), s
    # every REVOKE/GRANT names the SAME signature the CREATE declares
    for s in _stmt(m174, r"ON FUNCTION public\.publish_theme_rotation"):
        assert re.search(sig, s), s


def test_publish_rpc_call_matches_the_sql_signature(m174):
    """service.py's rpc() payload ⇄ the SQL parameter names, the basket entry keys, and the
    two outcomes the service treats as success."""
    fn = _publish_fn(m174)
    params = re.search(r"publish_theme_rotation\((.*?)\) RETURNS", fn, re.S).group(1)
    sql_params = {p.split()[0] for p in _split_top_level(params)}
    src = (ROTATION_PKG / "service.py").read_text(encoding="utf-8")
    call = re.search(r'rpc\("publish_theme_rotation",\s*\{(.*?)\}\)', src, re.S)
    assert call, "service.py no longer calls publish_theme_rotation"
    assert set(re.findall(r'"(p_\w+)"\s*:', call.group(1))) == sql_params
    publish = re.search(r"async def _publish\(.*?\n    async def ", src, re.S).group(0)
    # `expected` is the array EXACTLY as stored (the SQL compares raw arrays), falling back
    # to the plan's list only for a theme the run did not read.
    assert re.search(r'\{"expected":\s*result\.stored_baskets\.get\(slug,\s*plan\.before\),'
                     r'\s*"tickers":\s*plan\.after\}', publish)
    assert "v_entry -> 'expected'" in fn and "v_entry -> 'tickers'" in fn
    assert "RETURN 'published'" in fn and "RETURN 'already_published'" in fn
    assert 'outcome in ("published", "already_published")' in publish


def test_174_unique_run_index_excludes_preview(m174):
    idx = _stmt(m174, r"^CREATE UNIQUE INDEX IF NOT EXISTS uq_theme_rotation_runs_month_mode ")
    assert len(idx) == 1
    m = re.search(r"ON public\.theme_rotation_runs \(run_month, mode\) WHERE (.*)$", idx[0])
    assert m, idx[0]
    where = m.group(1)
    assert "'live'" in where and "'dry_run'" in where and "preview" not in where


def test_174_new_oil_relabel_is_guarded_by_its_real_old_value(m174):
    upd = _stmt(m174, r"^UPDATE public\.trending_themes SET category")
    assert len(upd) == 1
    m = re.search(r"SET category = '([^']+)'.* WHERE slug = 'the-new-oil' AND category = '([^']+)'$", upd[0])
    assert m, upd[0]
    new, old = m.groups()
    assert new == "Critical Minerals"
    # The guard value must be what the row actually holds, or the UPDATE is a silent no-op:
    # 081 seeded it, and no migration in between changed it.
    seed = (MIGRATIONS / "081_trending_themes.sql").read_text(encoding="utf-8")
    assert re.search(rf"\('the-new-oil',\s*'{re.escape(old)}'", seed)
    for p in _migrations_sorted():
        if "081" < p.name[:3] < "174":
            for s in _stmt(sql_statements(p.read_text(encoding="utf-8")), r"SET .*category"):
                assert "the-new-oil" not in s, (p.name, s)


def test_174_job_state_seeds_are_insert_only(m174):
    from app.services.theme_rotation.scheduler import (
        JOB_THEME_INSIGHTS_DAILY,
        JOB_THEME_ROTATION_MONTHLY,
    )

    for job in (JOB_THEME_ROTATION_MONTHLY, JOB_THEME_INSIGHTS_DAILY):
        ins = _stmt(m174, rf"^INSERT INTO public\.notification_job_state .*'{job}'")
        assert len(ins) == 1, job
        assert ins[0].endswith("ON CONFLICT (job) DO NOTHING"), ins[0]


def _check_values(coldef: str) -> set:
    m = re.search(r"CHECK \(\w+ IN \((.*?)\)\)", coldef, re.I)
    assert m, coldef
    return set(re.findall(r"'([^']+)'", m.group(1)))


def test_174_check_constraints_match_code_enums(m174):
    tables = _tables_174(m174)
    assert _check_values(tables["theme_rotation_decisions"]["action"]) == {a.value for a in Action}
    assert _check_values(tables["theme_relevance_cache"]["verdict"]) == {f.value for f in Fit}
    assert _check_values(tables["theme_rotation_runs"]["mode"]) == {"live", "dry_run", "preview"}
    assert _check_values(tables["theme_rotation_runs"]["status"]) == {
        "in_progress", "computed", "published", "failed"}
    # reason_code is free TEXT; the enum values the code writes must at least be non-empty
    assert all(r.value for r in Reason)
    # the read model only ever shows actions the constraint admits
    assert set(read_model._SHOWN_ACTIONS) <= {a.value for a in Action}


def _trending_themes_columns() -> set:
    cols: set = set()
    for p in _migrations_sorted():
        if p.name > MIGRATION_174.name:
            break
        for s in sql_statements(p.read_text(encoding="utf-8")):
            if re.match(r"CREATE TABLE (IF NOT EXISTS )?(public\.)?trending_themes\b", s, re.I):
                cols |= set(create_table_columns(s))
            if re.match(r"ALTER TABLE (public\.)?trending_themes\b", s, re.I):
                cols |= {c.lower() for c in re.findall(r"ADD COLUMN (?:IF NOT EXISTS )?(\w+)", s, re.I)}
    return cols


def _code_columns() -> Dict[str, set]:
    """Every column the rotation package names against a table: `.select("…")`, filters,
    `.order`, `.update({...})` literal keys, the insert/upsert row dicts, and the
    `trending_themes` column list."""
    used: Dict[str, set] = {}
    for py in sorted(ROTATION_PKG.glob("*.py")):
        src = py.read_text(encoding="utf-8")
        for m in re.finditer(r'table\("(\w+)"\)(.*?)\.execute\(\)', src, re.S):
            table, chain = m.group(1), m.group(2)
            cols = used.setdefault(table, set())
            for sel in re.findall(r'\.select\("([^"]*)"\)', chain):
                cols |= {c.strip() for c in sel.split(",") if c.strip()}
            cols |= set(re.findall(r'\.(?:eq|neq|lt|lte|gt|gte|in_|is_|order)\("(\w+)"', chain))
            for upd in re.findall(r"\.update\(\{(.*?)\}\)", chain, re.S):
                cols |= set(re.findall(r'"(\w+)"\s*:', upd))
            for oc in re.findall(r'on_conflict="([^"]+)"', chain):
                cols |= {c.strip() for c in oc.split(",")}

    def dict_keys(path: Path, func: str, *, in_append: bool = False, target: str = "row") -> set:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func)
        keys: set = set()
        for node in ast.walk(fn):
            d = None
            if in_append and isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "append":
                d = node.args[0] if node.args and isinstance(node.args[0], ast.Dict) else None
            if not in_append and isinstance(node, ast.Assign) and \
                    any(isinstance(t, ast.Name) and t.id == target for t in node.targets):
                d = node.value if isinstance(node.value, ast.Dict) else None
            if d is not None:
                keys |= {k.value for k in d.keys if isinstance(k, ast.Constant)}
        assert keys, f"{path.name}:{func} — found no row dict (the scan went vacuous)"
        return keys

    used.setdefault("theme_rotation_runs", set()).update(
        dict_keys(ROTATION_PKG / "service.py", "_claim_run"))
    used.setdefault("theme_rotation_decisions", set()).update(
        dict_keys(ROTATION_PKG / "service.py", "_record", in_append=True))
    used.setdefault("theme_relevance_cache", set()).update(
        dict_keys(ROTATION_PKG / "llm_gate.py", "_store"))
    tree = ast.parse((ROTATION_PKG / "service.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_read_themes")
    full = next(n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "full" for t in n.targets))
    used.setdefault("trending_themes", set()).update(
        c.strip() for c in ast.literal_eval(full.value).split(","))
    return used


def test_rotation_code_only_touches_columns_the_schema_has(m174):
    tables = {t: set(cols) for t, cols in _tables_174(m174).items()}
    tables["trending_themes"] = _trending_themes_columns()
    used = _code_columns()
    assert set(used) <= set(tables), f"code touches tables 174 never creates: {set(used) - set(tables)}"
    assert used["theme_rotation_decisions"] >= {"run_id", "slug", "ticker", "action", "reason_code"}
    missing = {t: sorted(cols - tables[t]) for t, cols in used.items() if cols - tables[t]}
    assert not missing, f"columns used by services/theme_rotation but absent from the schema: {missing}"
    # the 174 columns on trending_themes really are added by 174
    assert {"tickers_as_of", "rotation_enabled", "pinned_tickers", "blocked_tickers"} <= tables["trending_themes"]


def test_relevance_cache_upsert_conflict_target_is_the_primary_key(m174):
    stmt = _stmt(m174, r"^CREATE TABLE IF NOT EXISTS public\.theme_relevance_cache")[0]
    pk = re.search(r"PRIMARY KEY \(([^)]+)\)", stmt).group(1)
    src = (ROTATION_PKG / "llm_gate.py").read_text(encoding="utf-8")
    oc = re.search(r'on_conflict="([^"]+)"', src).group(1)
    assert [c.strip() for c in oc.split(",")] == [c.strip() for c in pk.split(",")]


# ══════════════════════════════════════════════════════════════════════════════════════
# 6. Atlas curation + the grants test know about the new tables
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def curation():
    scripts = str(BACKEND / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import schema_curation  # noqa: E402

    return schema_curation


@pytest.mark.parametrize("table", _NEW_TABLES)
def test_schema_curation_has_174_table(curation, m174, table):
    doc = curation.CURATION.get(f"public.{table}")
    assert doc is not None, f"public.{table} missing from scripts/schema_curation.py"
    assert doc.domain in curation.DOMAIN_ORDER
    assert doc.purpose.strip() or _stmt(m174, rf"^COMMENT ON TABLE public\.{table} IS ")
    cols = set(_tables_174(m174)[table])
    assert doc.key and set(doc.key) <= cols, f"{table}: curated key columns {set(doc.key) - cols} do not exist"


def test_schema_curation_trending_themes_keys_exist(curation):
    doc = curation.CURATION["public.trending_themes"]
    assert set(doc.key) <= _trending_themes_columns()


def _literal_assign(path: Path, name: str) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None)
        if isinstance(target, ast.Name) and target.id == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path.name}")


def test_grants_test_lists_the_174_tables():
    path = BACKEND / "tests" / "test_table_grants_service_role_only.py"
    service_only = _literal_assign(path, "_SERVICE_ROLE_ONLY")
    client = _literal_assign(path, "_CLIENT_ACCESS_BY_DESIGN")
    assert set(service_only["174"]) == set(_NEW_TABLES) | {"trending_themes"}
    assert "trending_themes" not in client
    # listed exactly once across all migrations' groups
    everywhere = [t for group in service_only.values() for t in group]
    for t in set(_NEW_TABLES) | {"trending_themes"}:
        assert everywhere.count(t) == 1, t


# ══════════════════════════════════════════════════════════════════════════════════════
# 7. Identity: no user-facing text names the model vendor
# ══════════════════════════════════════════════════════════════════════════════════════

_VENDOR = re.compile(
    r"\b(gemini|google|openai|anthropic|claude|chatgpt|gpt(-?\d[\w.]*)?|bard|deepmind|llm)\b"
    r"|\bai model|\blanguage model|\bmachine[- ]learning model", re.I)


def test_vendor_regex_is_not_vacuous():
    for s in ("Scored by Gemini", "an AI model decides", "GPT-4o", "Anthropic's Claude",
              "Google says", "a large language model"):
        assert _VENDOR.search(s), s
    for s in ("Removed after an editorial review.", "standard", "Aggressive", "googly"):
        assert not _VENDOR.search(s), s


def test_reasons_user_text_is_complete_and_vendor_free():
    from app.services.theme_rotation import reasons
    from app.services.theme_rotation.models import Action as A

    shown = set(reasons.ALL_USER_TEXT)
    assert set(reasons._TEXT.values()) | set(reasons._ADDED_BY_SOURCE.values()) | {
        reasons._ADDED_BY_FUNDS, reasons._ADDED_ADJACENT} == shown
    # every line user_reason can actually return is in the scanned set
    parts_samples = [None, {}, {"etf_pts": 20}, {"etf_pts": 13.9, "fit": "adjacent"},
                     {"exposure_source": "segments"}, {"exposure_source": "description"},
                     {"exposure_source": "weird"}, {"etf_pts": True}]
    for action in A:
        for reason in Reason:
            for parts in parts_samples:
                line = reasons.user_reason(action, reason, parts)
                if line is not None:
                    assert line in shown, (action, reason, parts, line)
    # and every string literal in the module (docstrings aside) is vendor-free
    tree = ast.parse(Path(reasons.__file__).read_text(encoding="utf-8"))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                  and n.body and isinstance(n.body[0], ast.Expr)
                  and isinstance(n.body[0].value, ast.Constant)}
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                and isinstance(n.value, str) and id(n) not in docstrings]
    assert len(literals) >= len(shown)
    for text in literals + sorted(shown):
        assert not _VENDOR.search(text), text


def _view_strings(path: Path, struct: Optional[str] = None) -> List[str]:
    code, strings = scan_swift(path.read_text(encoding="utf-8"))
    scope = type_body(code, struct) if struct else code
    return [strings[int(i)] for i in re.findall(r'"__S(\d+)__"', scope)]


def test_methodology_sheet_is_vendor_free():
    strings = _view_strings(IOS / "Views" / "Screens" / "ThemeDetailView.swift", "ThemeMethodologySheet")
    assert "How we pick stocks" in strings                       # brace-bound, not vacuous
    assert len([s for s in strings if len(s) > 40]) >= 5
    assert any("not a recommendation" in s.lower() for s in strings)
    for s in strings:
        assert not _VENDOR.search(s), s


def test_changes_card_is_vendor_free():
    strings = _view_strings(IOS / "Views" / "Molecules" / "ThemeChangesCard.swift", "ThemeChangesCard")
    assert "What changed this month" in strings
    assert any("Not a recommendation" in s for s in strings)
    for s in strings:
        assert not _VENDOR.search(s), s


@pytest.mark.parametrize("path", sorted(
    list((IOS / "Views").rglob("Theme*.swift")) + list((IOS / "Views").rglob("TrendingTheme*.swift"))
    + [IOS_MODELS / "ThemeDetailModels.swift"]), ids=lambda p: p.name)
def test_every_theme_surface_string_is_vendor_free(path):
    """Belt and braces over the rest of the theme UI (the insight card renders generated
    text, so its chrome must never attribute it to a vendor)."""
    strings = _view_strings(path)
    assert strings, f"{path.name}: no string literals found — scanner went vacuous"
    for s in strings:
        assert not _VENDOR.search(s), f"{path.name}: {s!r}"


# ── 2026-09-23 fix pass ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    ("2026-10-01", "2026-10-01"),
    ("2026-10-01T18:30:00+00:00", "2026-10-01"),
    (date(2026, 10, 1), "2026-10-01"),
    ("2026-1-1", None),                 # strptime alone accepted this; iOS parses YYYY-MM-DD
    ("2026-02-30", None),
    ("20261001", None),
    ("", None), (None, None), ("not a date", None),
])
def test_iso_date_is_strict_yyyy_mm_dd(value, expected):
    assert _iso_date(value) == expected


def test_driver_tickers_are_trimmed_and_blank_ones_dropped():
    row = _insights_row(_FULL_PERIODS)
    row["summary_text"] = "Chip names led."
    row["drivers"] = [{"ticker": " nvda ", "note": "x"}, {"ticker": "   "}, {"ticker": None},
                      "AMD", {"ticker": "amd"}]
    out = _detail_insight_fields(row)
    assert out["insight"].tickers == ["NVDA", "AMD"]


def test_drivers_that_are_not_a_list_are_ignored():
    row = _insights_row(_FULL_PERIODS)
    row["summary_text"] = "Chip names led."
    row["drivers"] = {"ticker": "NVDA"}
    assert _detail_insight_fields(row)["insight"].tickers == []


# ── 2026-09-23 adversarial-review fixes: stale basket, card age, degraded review ─────────

_OLD_BASKET = ("NVDA", "AMD", "GFS", "INTC")          # the list BEFORE the rotation


def test_card_drops_numbers_computed_on_another_basket():
    row = _insights_row(_FULL_PERIODS, one_month=_series([100.0, 104.0]), basket=_OLD_BASKET)
    assert _card_insight_fields(row, list(_SILICON_ROW["tickers"])) == {}
    same = _insights_row(_FULL_PERIODS, one_month=_series([100.0, 104.0]))
    assert _card_insight_fields(same, ["nvda", "AMD", "ALAB", "CRDO"])["return_1m"] == \
        pytest.approx(0.042)                           # case / order do not matter


def test_detail_drops_performance_but_keeps_the_dated_summary_on_another_basket():
    row = _insights_row(_FULL_PERIODS, one_year=_series([100.0, 120.0]), basket=_OLD_BASKET,
                        drivers=[{"ticker": "GFS"}, {"ticker": "nvda"}])
    out = _detail_insight_fields(row, list(_SILICON_ROW["tickers"]))
    assert "performance" not in out
    assert out["insight"].summary == "Chip names led."
    assert out["insight"].tickers == ["NVDA"]          # GFS left the list: no chip


def test_a_row_without_its_basket_is_not_trusted_when_the_list_is_known():
    row = _insights_row(_FULL_PERIODS, one_month=_series([100.0, 104.0]))
    del row["performance"]["constituents"]
    assert _card_insight_fields(row, ["NVDA"]) == {}
    assert _card_insight_fields(row)["return_1m"] == pytest.approx(0.042)   # no list: no check


@pytest.mark.parametrize("now, shown", [
    (datetime(2026, 9, 23, 1, 0, tzinfo=timezone.utc), True),     # the evening it ran
    (datetime(2026, 9, 25, 22, 0, tzinfo=timezone.utc), True),    # 3 sessions later
    (datetime(2026, 9, 28, 22, 0, tzinfo=timezone.utc), False),   # a stalled job
])
def test_card_trend_is_drawn_only_from_a_recent_row(now, shown):
    row = _insights_row(_FULL_PERIODS, one_month=_series([100.0, 104.0]))
    assert bool(_card_insight_fields(row, list(_SILICON_ROW["tickers"]), now=now)) is shown


@pytest.mark.asyncio
async def test_detail_built_on_a_failed_review_read_is_cached_briefly(monkeypatch):
    import time as _time

    hds.HomeDashboardService._theme_detail_cache.clear()
    _patch_sources(monkeypatch, review=LatestReview(run_month=None, degraded=True))
    svc = _service(row=dict(_SILICON_ROW))
    detail = await svc.get_theme_detail("silicon-rush")
    assert detail.updated_on is None and detail.changes == []
    stamp = hds.HomeDashboardService._theme_detail_cache["silicon-rush"][0]
    age = _time.time() - stamp
    assert hds._THEME_DETAIL_CACHE_TTL_SECONDS - age <= hds._THEME_DETAIL_DEGRADED_TTL_SECONDS + 1
    hds.HomeDashboardService._theme_detail_cache.clear()

    _patch_sources(monkeypatch, review=_review())
    await svc.get_theme_detail("silicon-rush")
    assert _time.time() - hds.HomeDashboardService._theme_detail_cache["silicon-rush"][0] < 5
    hds.HomeDashboardService._theme_detail_cache.clear()
