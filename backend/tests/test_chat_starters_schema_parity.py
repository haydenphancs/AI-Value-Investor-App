"""Backend↔iOS shape parity for `GET /api/v1/chat/starters`.

A NEW module rather than three more entries in `test_ios_response_schema_parity.py`: that
one's docstring scopes itself to "the AUTH and MONEY paths" and asserts "ALL NINE PAIRS
AGREE TODAY", so widening it would quietly invalidate its own header.

THE PREDICATE IS NULLABILITY ALONE, for the reason that module works out at length: a
Pydantic field that is *defaulted* is still always on the wire and can never be `null`, so
`is_required()` is the wrong question. Only a value that can arrive as `null` against a
non-`Optional` Swift property crashes a decode.

These DTOs are deliberately more tolerant than the minimum — most fields decode through
`try?` with a fallback — because this endpoint's contract is that it degrades. The parity
that still matters is the one tolerance cannot save you from: a KEY RENAME, which turns
every field into its fallback and empties the chip row silently, with no crash to notice.
"""

import re
from pathlib import Path
from typing import Union, get_args, get_origin

import pytest

from app.schemas.chat_starters import (
    ChatStarterResponse,
    ChatStartersResponse,
    DetailStarterSetResponse,
)

_REPO = Path(__file__).resolve().parents[2]
_DTO_PATH = _REPO / "frontend/ios/ios/Models/ChatStartersModels.swift"
_STORE_PATH = _REPO / "frontend/ios/ios/Services/ChatStartersStore.swift"
_SERVICE_PATH = _REPO / "backend/app/services/chat_starters_service.py"

_PAIRS = (
    (ChatStartersResponse, "ChatStartersDTO"),
    (ChatStarterResponse, "ChatStarterDTO"),
    (DetailStarterSetResponse, "DetailStarterSetDTO"),
)


def _swift() -> str:
    return _DTO_PATH.read_text(encoding="utf-8")


def _struct_body(name: str) -> str:
    src = _swift()
    start = src.index(f"struct {name}")
    depth, i, opened = 0, src.index("{", start), False
    for i in range(src.index("{", start), len(src)):
        if src[i] == "{":
            depth += 1
            opened = True
        elif src[i] == "}":
            depth -= 1
            if opened and depth == 0:
                return src[start:i]
    raise AssertionError(f"unbalanced braces in {name}")


def _swift_properties(body: str) -> dict[str, bool]:
    """`{property: is_optional}` for the stored `let` properties of a struct."""
    out: dict[str, bool] = {}
    for match in re.finditer(r"^\s{4}let\s+(\w+)\s*:\s*([^\n=]+)$", body, re.M):
        name, swift_type = match.group(1), match.group(2).strip()
        out[name] = swift_type.endswith("?")
    return out


def _coding_keys(body: str) -> dict[str, str]:
    """`{swift property: wire key}`, defaulting to the property name."""
    block = re.search(r"enum CodingKeys[^{]*\{(.*?)\n\s{4}\}", body, re.S)
    assert block, "every DTO here declares CodingKeys explicitly"
    keys: dict[str, str] = {}
    for line in block.group(1).splitlines():
        line = line.strip()
        if not line.startswith("case "):
            continue
        for part in line[len("case "):].split(","):
            part = part.strip()
            if "=" in part:
                prop, wire = part.split("=", 1)
                keys[prop.strip()] = wire.strip().strip('"')
            elif part:
                keys[part] = part
    return keys


def _may_be_null(annotation) -> bool:
    return get_origin(annotation) is Union and type(None) in get_args(annotation)


@pytest.mark.parametrize("model, dto", _PAIRS, ids=[p[1] for p in _PAIRS])
def test_every_backend_field_exists_on_the_swift_side(model, dto):
    body = _struct_body(dto)
    keys = _coding_keys(body)
    wire_keys = set(keys.values())
    missing = [name for name in model.model_fields if name not in wire_keys]
    assert not missing, (
        f"{dto} has no CodingKey for {missing}. The decoder ignores unknown keys, so these "
        "would silently arrive as the fallback and the row would quietly empty."
    )


@pytest.mark.parametrize("model, dto", _PAIRS, ids=[p[1] for p in _PAIRS])
def test_no_nullable_backend_field_maps_to_a_non_optional_swift_property(model, dto):
    body = _struct_body(dto)
    props = _swift_properties(body)
    keys = _coding_keys(body)
    wire_to_prop = {wire: prop for prop, wire in keys.items()}

    violations = []
    for name, field in model.model_fields.items():
        prop = wire_to_prop.get(name)
        if prop is None or prop not in props:
            continue
        if _may_be_null(field.annotation) and not props[prop]:
            violations.append(f"{name} -> {dto}.{prop}")
    assert not violations, (
        f"nullable on the wire, non-optional in Swift: {violations}. One null crashes the decode."
    )


def test_the_symbol_placeholder_token_agrees_on_both_sides():
    """The one thing the generic predicate cannot see.

    The detail templates are transported as ordinary strings, so a rename of the
    placeholder is invisible to any type check — the backend would keep emitting
    `{symbol}` while the client substituted something else, and the client's own
    "drop what I cannot fill" guard would then delete EVERY detail chip. Silent, and it
    would look like the detail bars simply have no questions.
    """
    backend = _SERVICE_PATH.read_text(encoding="utf-8")
    store = _STORE_PATH.read_text(encoding="utf-8")
    catalogue = (_REPO / "backend/data/chat_starters.json").read_text(encoding="utf-8")

    assert "{symbol}" in catalogue, "the authored templates must carry the token"
    assert '"{symbol}"' in store, "iOS must substitute exactly that token"
    assert "{symbol}" in backend, (
        "the service documents the token; if this stops being true, check the contract "
        "has not been renamed on one side only"
    )


def test_the_detail_scopes_agree_on_both_sides():
    """A scope added on one side only is a pool nothing ever reads."""
    from app.services.chat_starters_service import _DETAIL_SCOPES

    body = _struct_body("DetailStarterSetDTO")
    props = set(_swift_properties(body))
    assert props == set(_DETAIL_SCOPES), (
        f"Swift has {sorted(props)}, backend has {sorted(_DETAIL_SCOPES)}"
    )

    enum_block = re.search(
        r"enum ChatStarterScope: String, CaseIterable \{\s*case ([^\n}]+)", _swift()
    )
    assert enum_block, "ChatStarterScope should stay a closed enum"
    cases = {c.strip() for c in enum_block.group(1).split(",")}
    assert cases == set(_DETAIL_SCOPES), f"scope enum drift: {sorted(cases)}"


def test_the_parity_scan_is_not_vacuous():
    """Prove the parser actually found fields, not an empty struct."""
    for _model, dto in _PAIRS:
        body = _struct_body(dto)
        assert len(_swift_properties(body)) >= 3, f"{dto}: parser found too few properties"
        assert len(_coding_keys(body)) >= 3, f"{dto}: parser found too few coding keys"

    # And that it can SEE an optional when there is one.
    props = _swift_properties(_struct_body("ChatStarterDTO"))
    assert props["symbol"] is True, "symbol is String? — the optional detector is broken"
    assert props["text"] is False, "text is String — the optional detector is over-eager"
