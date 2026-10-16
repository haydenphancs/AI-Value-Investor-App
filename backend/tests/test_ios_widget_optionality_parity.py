"""Optionality parity between `app/schemas/widget.py` and the Swift DTOs that decode it.

`test_ios_widget_parity.py` checks key NAMES (Swift ⊆ backend). It cannot see the failure that
actually ships: a field the backend may send as `null` that Swift declares non-Optional. That
throws `valueNotFound` at decode time — inside the widget extension, where `read()` returns nil
and the Home Screen tile shows its placeholder, with no error state and no crash report. The
same struct can be renamed, reordered and re-documented and stay green under a names-only scan.

So, for every Swift ⇄ Pydantic pair:

  1. Every Pydantic field whose annotation ADMITS None must be `T?` in Swift (when Swift
     decodes it at all — ignoring a field is allowed).
  2. Every non-Optional Swift stored property must map to a backend field that the CURRENT
     backend never sends as None: one whose annotation excludes None and that is required or
     has a non-None default (`gap_dominant: bool = False`, `tickers = []`).
  3. Every key ADDED after the first release (`_ADDITIVE_2026_09_30`, `_ADDITIVE_2026_10_14`,
     all registered in `_ADDITIVE_SETS`) must be `T?` in Swift, or be read with
     `decodeIfPresent` in that struct's own `init(from:)`. Rule 2 cannot see this: a
     default makes a field "always on the wire" only for JSON the current backend writes. The
     v1 envelope the INSTALLED app persisted, and any response from a not-yet-deployed or
     rolled-back backend, has no such key — and a synthesized `decode(Bool.self)` throws
     `keyNotFound` on it, blanking the Market tile through the migration and silently dropping
     old-backend rows through `LossyElement`. Kept as explicit lists, not a git baseline, so
     the suite stays hermetic; a key added later belongs in a NEW dated set, registered in
     `_ADDITIVE_SETS` (a test fails on an unregistered one).

Keyed on "the annotation admits None", NOT on `is_required()`: `runners_up` is not required but
can never be null, and Swift rightly declares it `[WidgetMover]` with `?? []`.

Scope: the eight wire structs in `Shared/WidgetSnapshotStore.swift` plus `WidgetTokenResponse`,
which lives in the APP target (`ios/Core/Services/WidgetRefreshService.swift`) and declares its
properties without `public`. Comments are stripped and each struct is brace-bound
(`.claude/rules/testing.md` §3); `test_the_scan_bites` mutates in-memory copies to prove the
check is not vacuous.
"""

from __future__ import annotations

import importlib
import re
import typing
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_STORE = _REPO / "frontend" / "ios" / "Shared" / "WidgetSnapshotStore.swift"
_REFRESH = _REPO / "frontend" / "ios" / "ios" / "Core" / "Services" / "WidgetRefreshService.swift"

_PAIRS: list[tuple[str, str, Path]] = [
    ("WidgetMoverSnapshot", "WidgetMoverPayload", _STORE),
    ("WidgetMover", "WidgetMoverResponse", _STORE),
    ("WidgetBasket", "WidgetBasketResponse", _STORE),
    ("WidgetMoveContext", "WidgetMoveContextResponse", _STORE),
    ("WidgetMarketContext", "WidgetMarketContextResponse", _STORE),
    ("WidgetIndex", "WidgetIndexResponse", _STORE),
    ("WidgetCause", "WidgetCauseResponse", _STORE),
    ("WidgetMarketBrief", "WidgetMarketBriefResponse", _STORE),
    ("WidgetTokenResponse", "WidgetTokenResponse", _REFRESH),
]


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(
        "" if line.strip().startswith("//") else re.sub(r"\s//.*$", "", line)
        for line in src.splitlines()
    )


def _struct_block(src: str, name: str) -> str:
    code = _strip_comments(src)
    m = re.search(rf"\bstruct\s+{name}\s*(?::[^{{]*)?\{{", code)
    if not m:
        raise LookupError(f"struct {name} not found")
    depth = 0
    for i in range(m.end() - 1, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[m.end() - 1 : i + 1]
    raise LookupError(f"unbalanced braces in struct {name}")


_PROPERTY = re.compile(
    r"^\s*(?:(?:public|internal|private|fileprivate)\s+)?(?:let|var)\s+(\w+)\s*:\s*([^={]+?)\s*(?:=.*)?$"
)


def _stored_properties(block: str) -> dict[str, str]:
    """`name -> type` for the struct's OWN stored properties (depth 1; no computed, no static)."""
    props: dict[str, str] = {}
    depth = 0
    for line in block.splitlines():
        if depth == 1 and "{" not in line and "static " not in line:
            m = _PROPERTY.match(line)
            if m:
                props[m.group(1)] = m.group(2).strip()
        depth += line.count("{") - line.count("}")
    return props


def _wire_to_property(block: str, props: dict[str, str]) -> dict[str, str]:
    """The wire key each stored property decodes from — CodingKeys when declared, else its name."""
    ck = re.search(r"enum CodingKeys[^{]*\{(.*?)\}", block, re.S)
    if not ck:
        return {name: name for name in props}
    mapping: dict[str, str] = {}
    for line in ck.group(1).splitlines():
        line = line.strip()
        if not line.startswith("case "):
            continue
        for part in line[len("case "):].split(","):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                prop, wire = part.split("=", 1)
                mapping[wire.strip().strip('"')] = prop.strip()
            else:
                mapping[part] = part
    return mapping


def _admits_none(annotation) -> bool:
    return annotation is None or type(None) in typing.get_args(annotation)


def _is_optional(swift_type: str) -> bool:
    return swift_type.endswith("?") or swift_type.startswith("Optional<")


def _model(name: str):
    return getattr(importlib.import_module("app.schemas.widget"), name)


# (Swift struct, wire key) — every key the 2026-09-30 widget change added. No persisted v1
# snapshot and no older backend carries any of them, so each must decode when ABSENT.
_ADDITIVE_2026_09_30: frozenset[tuple[str, str]] = frozenset({
    ("WidgetIndex", "short_label"),
    ("WidgetIndex", "rolling_24h"),
    ("WidgetIndex", "asset_type"),
    ("WidgetMover", "rolling_24h"),
    ("WidgetMover", "asset_type"),
    ("WidgetCause", "detail_aged"),
    ("WidgetMoverSnapshot", "group_name"),
    ("WidgetMoverSnapshot", "holdings_count"),
    ("WidgetMoverSnapshot", "up_count"),
    ("WidgetMoverSnapshot", "down_count"),
    ("WidgetMoverSnapshot", "flat_count"),
    ("WidgetMoverSnapshot", "top_gainers"),
    ("WidgetMoverSnapshot", "top_losers"),
    ("WidgetMoverSnapshot", "market_assets"),
})

# (Swift struct, wire key) — the 2026-10-14 cause note: the always-true context line under an
# established cause's headline. An App Group snapshot written before it, and an older
# backend, carry no such key.
_ADDITIVE_2026_10_14: frozenset[tuple[str, str]] = frozenset({
    ("WidgetCause", "note"),
})

# Every dated set, by the day its keys first went on the wire. The messages print the date,
# so a key belongs in the set of the day IT was added — never appended to an older one.
_ADDITIVE_SETS: dict[str, frozenset[tuple[str, str]]] = {
    "2026-09-30": _ADDITIVE_2026_09_30,
    "2026-10-14": _ADDITIVE_2026_10_14,
}


def _set_name(date: str) -> str:
    return "_ADDITIVE_" + date.replace("-", "_")


def _custom_init(block: str) -> str:
    """The struct's own `init(from decoder:)` body, or "" when decoding is synthesized."""
    m = re.search(r"\binit\s*\(\s*from\s+decoder\s*:\s*Decoder\s*\)[^{]*\{", block)
    if not m:
        return ""
    depth = 0
    for i in range(m.end() - 1, len(block)):
        if block[i] == "{":
            depth += 1
        elif block[i] == "}":
            depth -= 1
            if depth == 0:
                return block[m.end() - 1 : i + 1]
    raise LookupError("unbalanced braces in init(from:)")


def _additive_problems(block: str, swift_name: str) -> list[str]:
    """Rule 3: each additive key decodes when absent."""
    props = _stored_properties(block)
    mapping = _wire_to_property(block, props)
    init = _custom_init(block)
    problems = []
    for date, keys in _ADDITIVE_SETS.items():
        for struct, wire in sorted(keys):
            if struct != swift_name:
                continue
            prop = mapping.get(wire)
            if prop is None or prop not in props:
                problems.append(
                    f"additive key `{wire}` (new on {date}) no longer maps to a {swift_name} "
                    f"property — renamed? update `{_set_name(date)}` with the new name, never "
                    "drop the entry"
                )
                continue
            if _is_optional(props[prop]):
                continue
            tolerant = re.search(rf"\bdecodeIfPresent\([^\n]*forKey:\s*\.{prop}\)", init)
            strict = re.search(rf"\bdecode\([^\n]*forKey:\s*\.{prop}\)", init)
            if not init or not tolerant or strict:
                problems.append(
                    f"`{wire}` is new on {date}, but `{prop}: {props[prop]}` is non-Optional "
                    f"and not read with decodeIfPresent in {swift_name}'s own init(from:) — a "
                    "snapshot persisted before that day, or an older backend, omits it, and "
                    "keyNotFound blanks the tile"
                )
    return problems


def _optionality_problems(block: str, model, swift_name: str) -> list[str]:
    props = _stored_properties(block)
    mapping = _wire_to_property(block, props)
    fields = model.model_fields
    problems = _additive_problems(block, swift_name)
    for wire, field in fields.items():
        prop = mapping.get(wire)
        if prop is None or prop not in props:
            continue  # Swift may ignore a field; the names test covers misspellings.
        if _admits_none(field.annotation) and not _is_optional(props[prop]):
            problems.append(
                f"`{wire}` may be null on the wire but Swift declares `{prop}: {props[prop]}` "
                f"— a null throws valueNotFound and blanks the tile"
            )
    for wire, prop in mapping.items():
        if prop not in props or _is_optional(props[prop]):
            continue
        field = fields.get(wire)
        if field is None:
            problems.append(f"non-Optional `{prop}` maps to `{wire}`, which the backend never sends")
        elif _admits_none(field.annotation):
            problems.append(f"non-Optional `{prop}` maps to nullable `{wire}`")
        elif not field.is_required() and field.default is None and field.default_factory is None:
            problems.append(f"non-Optional `{prop}` maps to `{wire}`, whose default is None")
    return problems


@pytest.mark.parametrize("swift, pydantic, path", _PAIRS, ids=[p[0] for p in _PAIRS])
def test_nullable_backend_fields_are_optional_in_swift(swift, pydantic, path):
    block = _struct_block(path.read_text(encoding="utf-8"), swift)
    assert _stored_properties(block), f"no stored properties parsed from {swift} — scan drifted"
    problems = _optionality_problems(block, _model(pydantic), swift)
    assert problems == [], f"{swift} ⇄ {pydantic}: {problems}"


@pytest.mark.parametrize("swift, pydantic, path", _PAIRS, ids=[p[0] for p in _PAIRS])
def test_every_swift_wire_property_is_accounted_for(swift, pydantic, path):
    """Each stored property must decode from SOME wire key — a property the mapping misses
    would be checked by nothing above."""
    block = _struct_block(path.read_text(encoding="utf-8"), swift)
    props = _stored_properties(block)
    mapped = set(_wire_to_property(block, props).values())
    assert set(props) <= mapped, f"{swift} properties with no wire key: {sorted(set(props) - mapped)}"


# (struct, path, exact text, regression) — applied to an in-memory COPY only.
_MUTATIONS = [
    ("WidgetMover", "WidgetMoverResponse", _STORE,
     "public let companyName: String?", "public let companyName: String"),
    ("WidgetMoverSnapshot", "WidgetMoverPayload", _STORE,
     "public let holdingsCount: Int?", "public let holdingsCount: Int"),
    ("WidgetIndex", "WidgetIndexResponse", _STORE,
     "public let shortLabel: String?", "public let shortLabel: String"),
    ("WidgetCause", "WidgetCauseResponse", _STORE,
     "public let detailAged: String?", "public let detailAged: String"),
    ("WidgetCause", "WidgetCauseResponse", _STORE,
     "public let note: String?", "public let note: String"),
    ("WidgetMarketBrief", "WidgetMarketBriefResponse", _STORE,
     "public let generatedAt: Date?", "public let generatedAt: Date"),
    ("WidgetMoveContext", "WidgetMoveContextResponse", _STORE,
     "public let industryName: String?", "public let industryName: String"),
    # Rule 3. `rolling_24h: bool = False` satisfies rule 2, so before rule 3 a non-Optional
    # `Bool` passed — on BOTH structs (the anchor appears in each, hence block-scoped edits).
    ("WidgetMover", "WidgetMoverResponse", _STORE,
     "public let rolling24h: Bool?", "public let rolling24h: Bool"),
    ("WidgetIndex", "WidgetIndexResponse", _STORE,
     "public let rolling24h: Bool?", "public let rolling24h: Bool"),
    ("WidgetMoverSnapshot", "WidgetMoverPayload", _STORE,
     "topGainers = try c.decodeIfPresent(LossyArray<WidgetMover>.self, forKey: .topGainers)?.elements ?? []",
     "topGainers = try c.decode(LossyArray<WidgetMover>.self, forKey: .topGainers).elements"),
    ("WidgetMoverSnapshot", "WidgetMoverPayload", _STORE,
     "marketAssets = try c.decodeIfPresent(LossyArray<WidgetIndex>.self, forKey: .marketAssets)?.elements ?? []",
     "marketAssets = []"),
]


@pytest.mark.parametrize(
    "swift, pydantic, path, old, new", _MUTATIONS,
    ids=[f"{m[0]}-{i}" for i, m in enumerate(_MUTATIONS)],
)
def test_the_scan_bites(swift, pydantic, path, old, new):
    """Applied INSIDE the struct's block: `rolling24h: Bool?` is in two structs, and a
    file-wide first-match replace would only ever mutate the first."""
    block = _struct_block(path.read_text(encoding="utf-8"), swift)
    assert block.count(old) == 1, f"mutation anchor gone from {swift} — update the table"
    mutated = block.replace(old, new, 1)
    assert _optionality_problems(mutated, _model(pydantic), swift), f"{swift} stayed green with {new!r}"


def test_the_additive_list_is_live():
    """Each listed key, in every dated set, still exists on BOTH sides — a stale entry would
    check nothing."""
    store = _STORE.read_text(encoding="utf-8")
    pydantic_of = {swift: pydantic for swift, pydantic, _path in _PAIRS}
    for date, keys in _ADDITIVE_SETS.items():
        assert keys, f"`{_set_name(date)}` is empty — it would check nothing"
        for swift, wire in sorted(keys):
            assert wire in _model(pydantic_of[swift]).model_fields, (
                f"backend dropped `{wire}` (new on {date}, `{_set_name(date)}`)"
            )
            block = _struct_block(store, swift)
            assert wire in _wire_to_property(block, _stored_properties(block)), (
                f"{swift} no longer decodes `{wire}` (new on {date}, `{_set_name(date)}`)"
            )


def test_the_dated_sets_are_registered_and_disjoint():
    """A key is new on ONE day. In two sets it would print two dates; a set defined but not
    in `_ADDITIVE_SETS` would be checked by nothing — rule 3 and the test above walk the
    registry, not the module."""
    defined = {
        name: value for name, value in globals().items()
        if re.fullmatch(r"_ADDITIVE_\d{4}_\d{2}_\d{2}", name)
    }
    registered = {_set_name(date): keys for date, keys in _ADDITIVE_SETS.items()}
    assert set(defined) == set(registered), (
        f"dated sets not in _ADDITIVE_SETS: {sorted(set(defined) - set(registered))}; "
        f"registered under a name that does not exist: {sorted(set(registered) - set(defined))}"
    )
    for name, keys in registered.items():
        assert defined[name] is keys, f"`{name}` is registered under the wrong date"
    dates = sorted(_ADDITIVE_SETS)
    for i, a in enumerate(dates):
        for b in dates[i + 1 :]:
            both = _ADDITIVE_SETS[a] & _ADDITIVE_SETS[b]
            assert not both, f"{sorted(both)} listed as new on both {a} and {b}"


def test_rule_three_walks_every_dated_set():
    """Rule 3 ALONE (not rule 1, which a nullable annotation also trips) must flag a key
    from the newest set, naming its day — proof it reads the registry, not one set."""
    block = _struct_block(_STORE.read_text(encoding="utf-8"), "WidgetCause")
    old = "public let note: String?"
    assert block.count(old) == 1, "mutation anchor gone from WidgetCause — update this test"
    problems = _additive_problems(block.replace(old, "public let note: String", 1), "WidgetCause")
    assert any("`note`" in p and "2026-10-14" in p for p in problems), problems
    assert _additive_problems(block, "WidgetCause") == []


def test_rule_three_accepts_a_tolerant_custom_init():
    """Control: a non-Optional array read with `decodeIfPresent … ?? []` in its own init is
    the CORRECT shape (WidgetMoverSnapshot's), and must not be flagged."""
    block = _struct_block(
        "struct WidgetMoverSnapshot: Codable {\n"
        "    public let topGainers: [WidgetMover]\n"
        "    enum CodingKeys: String, CodingKey { case topGainers = \"top_gainers\" }\n"
        "    public init(from decoder: Decoder) throws {\n"
        "        let c = try decoder.container(keyedBy: CodingKeys.self)\n"
        "        topGainers = try c.decodeIfPresent([WidgetMover].self, forKey: .topGainers) ?? []\n"
        "    }\n"
        "}\n",
        "WidgetMoverSnapshot",
    )
    problems = _additive_problems(block, "WidgetMoverSnapshot")
    assert not [p for p in problems if "top_gainers" in p and "non-Optional" in p], problems


def test_a_comment_does_not_count_as_a_property():
    block = _struct_block("struct X: Codable {\n    // let a: Int\n    let b: Int?\n}\n", "X")
    assert _stored_properties(block) == {"b": "Int?"}


def test_computed_and_static_members_are_not_stored_properties():
    src = (
        "struct X: Codable {\n"
        "    public let a: Int?\n"
        "    public var b: Bool { a != nil }\n"
        "    static let c: Int = 1\n"
        "    enum CodingKeys: String, CodingKey { case a }\n"
        "    func f() { let d: Int = 2 }\n"
        "}\n"
    )
    assert _stored_properties(_struct_block(src, "X")) == {"a": "Int?"}
