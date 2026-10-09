"""The caller's plan reaches the chat tool handlers on every door (2026-10-08).

`chat_ownership_tool` unlocks congressional disclosures only for Pro and above, and its default
(no tier) is LOCKED — so a door that forgets to pass `user_tier` fails closed, silently: a paying
caller is told Congress is "on Caydex Pro". These guards pin the three door call sites in
`endpoints/chat.py` and the send path's handler build in `chat_service.generate_response`, by AST
(comments and strings cannot satisfy them), plus the shared starter warm that must NEVER pass a
paid tier (its answers are replayed to every user).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parents[1] / "app"
_CHAT = _APP / "api" / "v1" / "endpoints" / "chat.py"
_SERVICE = _APP / "services" / "chat_service.py"
_WARM = _APP / "services" / "chat_starter_warm_service.py"


def _calls(path: Path, attr: str):
    """Every call whose callee is `<anything>.attr(...)` or `attr(...)`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name == attr:
                out.append(node)
    return out


def _is_user_tier_of_user(call: ast.Call) -> bool:
    """`user_tier=user.get("tier")` exactly."""
    for kw in call.keywords:
        if kw.arg != "user_tier":
            continue
        v = kw.value
        return (isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
                and v.func.attr == "get" and isinstance(v.func.value, ast.Name)
                and v.func.value.id == "user" and len(v.args) == 1
                and isinstance(v.args[0], ast.Constant) and v.args[0].value == "tier")
    return False


def test_both_generate_response_doors_pass_the_callers_tier():
    calls = _calls(_CHAT, "generate_response")
    assert len(calls) == 2, "the send door and the stream→non-stream fallback"
    for call in calls:
        assert _is_user_tier_of_user(call), ast.unparse(call)[:200]


def test_the_stream_doors_handler_build_passes_the_callers_tier():
    calls = _calls(_CHAT, "build_chat_tool_handlers")
    assert len(calls) == 1
    assert _is_user_tier_of_user(calls[0]), ast.unparse(calls[0])[:200]


def test_generate_response_forwards_its_tier_to_the_handlers():
    tree = ast.parse(_SERVICE.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "generate_response")
    params = {a.arg: a for a in fn.args.args + fn.args.kwonlyargs}
    assert "user_tier" in params
    builds = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
              and getattr(n.func, "id", None) == "build_chat_tool_handlers"]
    assert len(builds) == 1
    kw = {k.arg: k.value for k in builds[0].keywords}
    assert isinstance(kw.get("user_tier"), ast.Name) and kw["user_tier"].id == "user_tier"


def test_the_ownership_fetch_forwards_the_tier():
    tree = ast.parse(_SERVICE.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "_fetch_ownership_data")
    defaults = dict(zip([a.arg for a in fn.args.args][-len(fn.args.defaults):], fn.args.defaults))
    assert isinstance(defaults.get("user_tier"), ast.Constant) and defaults["user_tier"].value is None
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "fetch_ownership"]
    assert len(calls) == 1
    kw = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kw.get("user_tier"), ast.Name) and kw["user_tier"].id == "user_tier"


def test_the_shared_starter_warm_never_passes_a_tier():
    for call in _calls(_WARM, "generate_response"):
        assert "user_tier" not in {k.arg for k in call.keywords}, (
            "starter answers are replayed to every user — a paid tier would leak Pro data")


@pytest.mark.parametrize("path", [_CHAT, _SERVICE])
def test_the_scan_is_not_vacuous(path):
    assert _calls(path, "build_chat_tool_handlers"), path
