"""The chat-session LIST endpoint selects exactly the columns it serializes.

`GET /chat/sessions` used to `select("*")`, so every page pulled each session's
`context_snapshot` (up to `CHAT_CONTEXT_MAX_CHARS` = 8000 chars) and, after
migration 130, `memory_summary` — neither of which `_row_to_session` serializes.
Pure wasted egress, multiplied by the page size.

The failure mode this guards is the opposite one: a narrowed select that DROPS a
column `_row_to_session` reads would raise a KeyError (or silently return a
default) at runtime, on a path no test exercises. So the assertion is derived from
`_row_to_session`'s own source via AST rather than from a hand-copied list — a
hardcoded expected-set would drift the moment someone adds a field.
"""

import ast
import inspect

from app.api.v1.endpoints import chat as chat_ep


def _keys_read_by_row_to_session() -> set[str]:
    """Every literal key `_row_to_session` reads off the row: row["x"] and row.get("x")."""
    tree = ast.parse(inspect.getsource(chat_ep._row_to_session))
    keys: set[str] = set()
    for node in ast.walk(tree):
        # row["id"]
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id == "row" and isinstance(node.slice, ast.Constant):
                if isinstance(node.slice.value, str):
                    keys.add(node.slice.value)
        # row.get("title") / row.get("message_count", 0)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "row" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            keys.add(node.args[0].value)
    return keys


def _selected_columns() -> set[str]:
    return {c.strip() for c in chat_ep._SESSION_LIST_COLUMNS.split(",") if c.strip()}


def test_the_scan_actually_found_the_fields():
    """Guard against the guard: an AST walk that matches nothing passes vacuously."""
    keys = _keys_read_by_row_to_session()
    assert len(keys) >= 10, f"expected ~11 row fields, found {sorted(keys)}"
    assert "id" in keys and "created_at" in keys


def test_every_serialized_field_is_selected():
    """A narrowed select that omits a needed column breaks the list endpoint."""
    missing = _keys_read_by_row_to_session() - _selected_columns()
    assert not missing, f"_row_to_session reads columns the list select omits: {sorted(missing)}"


def test_no_unused_column_is_selected():
    """The point of the change: don't ship bytes nobody reads."""
    extra = _selected_columns() - _keys_read_by_row_to_session()
    assert not extra, f"list select pulls columns _row_to_session ignores: {sorted(extra)}"


def test_the_heavy_text_columns_are_not_in_the_list_select():
    """Named explicitly so a future `select('*')` regression is unmistakable."""
    selected = _selected_columns()
    for heavy in ("context_snapshot", "memory_summary", "memory_summary_upto"):
        assert heavy not in selected
    assert "*" not in chat_ep._SESSION_LIST_COLUMNS
