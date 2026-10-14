"""Every credit-history `kind` the backend emits has its own glyph and ink on iOS.

`credit_history_service.KIND_*` is a closed vocabulary the backend OWNS, and iOS switches on it
(`CreditTransactionDTO.iconName` / `iconColor`) with a neutral `default`. A kind the backend
adds without the matching Swift `case` is not a crash — it is a row drawn with the "unknown"
glyph, which is exactly what migration 194's `expire` kind would have looked like (and what an
older build still shows, by design). This guard makes the new-build half a test failure.

Source scan per testing.md: comments stripped (CreditHistoryModels.swift's comments mention
the kinds), each switch BRACE-BOUND to its own property so a `case` in the other one cannot
satisfy it. Mutation-tested by hand on 2026-10-12 (on in-memory copies of the Swift source):
deleting `case "expire"` from `iconName` or from `iconColor` fails
`test_every_kind_has_a_case_in_both_switches`, and so does a `case "expire"` left only in a
comment.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import credit_history_service as svc

_SWIFT = (Path(__file__).resolve().parents[2]
          / "frontend" / "ios" / "ios" / "Models" / "CreditHistoryModels.swift")


def _strip_swift_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for raw in src.splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", raw))
    return "\n".join(out)


def _property_body(code: str, decl: str) -> str:
    """The brace-balanced body of `var <decl>: ... {`."""
    m = re.search(rf"var\s+{decl}\s*:\s*\w+\s*\{{", code)
    assert m, f"`var {decl}` not found in CreditHistoryModels.swift — this guard is stale"
    depth, i = 1, m.end()
    while depth and i < len(code):
        depth += {"{": 1, "}": -1}.get(code[i], 0)
        i += 1
    return code[m.end():i - 1]


def _kinds() -> set[str]:
    kinds = {v for k, v in vars(svc).items() if k.startswith("KIND_") and isinstance(v, str)}
    assert {"spend", "refund", "expire", "other"} <= kinds, kinds
    return kinds - {svc.KIND_OTHER}  # `other` IS the default arm, by design


def _cases(body: str) -> set[str]:
    out = set()
    for line in re.findall(r"case\s+([^:]+):", body):
        out |= set(re.findall(r'"([^"]+)"', line))
    return out


@pytest.mark.parametrize("prop", ["iconName", "iconColor"])
def test_every_kind_has_a_case_in_both_switches(prop):
    code = _strip_swift_comments(_SWIFT.read_text(encoding="utf-8"))
    missing = _kinds() - _cases(_property_body(code, prop))
    assert not missing, (
        f"CreditHistoryModels.swift `{prop}` has no `case` for kind(s) {sorted(missing)} — the "
        f"row falls to the neutral default glyph/ink"
    )


def test_the_wire_description_lists_every_kind():
    from app.schemas.credit_history import CreditTransactionResponse

    description = CreditTransactionResponse.model_fields["kind"].description or ""
    listed = {k.strip() for k in description.split("|")}
    assert _kinds() | {svc.KIND_OTHER} <= listed, listed
