"""No shipped Button whose action does nothing.

TestFlight 1.0(8) (wiser_learn E2): the Books player's Share button — and the ••• next to it — had
action closures holding nothing but a comment (`// Share action`, `// Show more options menu`).
Visible, enabled, and dead; nothing in the suite could see it. This sweeps the whole iOS tree.

What counts as dead: an action closure that is empty once comments are stripped, in any of the
spellings SwiftUI accepts —

    Button(action: { })              Button { } label: { … }
    Button("Title") { }              Button("Title", action: { })
    Button(role: .destructive) { } label: { … }

Exempt, by rule rather than by list: `role: .cancel`. Inside `.alert` / `.confirmationDialog` a
cancel button with an empty action is the idiom — the system dismisses the alert on any tap.
Previews (`#Preview`, `PreviewProvider`) are excluded.

`_KNOWN_DEAD` is SHRINK-ONLY and counted PER SITE: a listed file may hold exactly that many dead
buttons — a new one in the same file fails, and a fixed one fails until its count is lowered.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
IOS = REPO / "frontend/ios/ios"

# path (relative to IOS) → (dead sites tolerated, why).
_KNOWN_DEAD = {
    "Views/Screens/BookDetailView.swift":
        (1, "\"See All\" inside `private struct DiscussionSection`, which nothing instantiates (dead code)"),
}

_DEAD = [
    re.compile(r"\bButton\s*\(\s*action:\s*\{\s*\}"),
    re.compile(r"\bButton\s*(?:\(\s*role:\s*\.(?P<role1>\w+)\s*\))?\s*\{\s*\}\s*label\s*:"),
    re.compile(r"\bButton\s*\(\s*\"[^\"\n]*\"\s*(?:,\s*role:\s*\.(?P<role2>\w+))?\s*\)\s*\{\s*\}"),
    re.compile(r"\bButton\s*\(\s*\"[^\"\n]*\"\s*(?:,\s*role:\s*\.(?P<role3>\w+))?\s*,\s*action:\s*\{\s*\}\s*\)"),
]


def _strip_comments(src: str) -> str:
    """Comments become blank lines (line numbers survive). A comment-only action is the bug, so
    stripping is what turns `{ // Share action }` into the `{ }` the patterns look for."""
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            out.append("")
            continue
        m = re.search(r"\s//", line)
        if m and line[: m.start()].count('"') % 2 == 0:
            line = line[: m.start()]
        out.append(line)
    return "\n".join(out)


def _drop_previews(src: str) -> str:
    """Blank out `#Preview { … }` blocks and `PreviewProvider` types, keeping line numbers.

    Always moves forward: a marker with no body (e.g. inside a string literal) is skipped, and
    unbalanced braces fail loudly — re-searching from the start used to loop forever on both.
    """
    def blank(s: str, start: int):
        open_at = s.find("{", start)
        if open_at < 0:
            return None
        depth = 0
        for i in range(open_at, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    chunk = s[start : i + 1]
                    return s[:start] + re.sub(r"[^\n]", " ", chunk) + s[i + 1 :]
        pytest.fail(f"unbalanced braces in a preview at line {s[:start].count(chr(10)) + 1}")
    # Line-anchored: a "#Preview" inside a string literal is not a preview (and blanking from it
    # would hide the real code that follows).
    for pat in (re.compile(r"^[ \t]*#Preview\b", re.M),
                re.compile(r"^[ \t]*(?:private\s+|fileprivate\s+)?struct\s+\w+\s*:\s*PreviewProvider", re.M)):
        pos = 0
        while (m := pat.search(src, pos)):
            out = blank(src, m.start())
            if out is None:
                pos = m.end()
                continue
            src = out   # the marker itself was blanked, so the next search moves on
    return src


def _dead_sites(src: str) -> list[int]:
    lines = []
    for pat in _DEAD:
        for m in pat.finditer(src):
            role = next((v for k, v in m.groupdict().items() if v), None)
            if role == "cancel":
                continue
            lines.append(src[: m.start()].count("\n") + 1)
    return sorted(set(lines))


def _scan() -> dict[str, list[int]]:
    found = {}
    files = sorted(IOS.rglob("*.swift"))
    assert len(files) > 400, f"only {len(files)} Swift files — the sweep is scanning the wrong tree"
    for path in files:
        sites = _dead_sites(_drop_previews(_strip_comments(path.read_text())))
        if sites:
            found[str(path.relative_to(IOS))] = sites
    return found


def test_no_new_dead_buttons():
    found = _scan()
    new = {p: ls for p, ls in found.items() if len(ls) > _KNOWN_DEAD.get(p, (0, ""))[0]}
    assert not new, (
        "Buttons whose action does nothing (comments stripped). Wire them or remove them:\n  "
        + "\n  ".join(f"{p}:{ls}" for p, ls in new.items()))


def test_the_known_dead_list_only_shrinks():
    found = _scan()
    fixed = {p: (n, len(found.get(p, []))) for p, (n, _) in _KNOWN_DEAD.items()
             if len(found.get(p, [])) < n}
    assert not fixed, f"fixed — lower the count or remove from _KNOWN_DEAD: {fixed}"


def test_the_player_buttons_that_shipped_dead_are_live():
    """The TestFlight sites themselves, by name."""
    src = _drop_previews(_strip_comments((IOS / "Views/Screens/FullScreenAudioPlayer.swift").read_text()))
    assert not _dead_sites(src)


def test_an_unbalanced_preview_fails_loudly_instead_of_hanging():
    with pytest.raises(pytest.fail.Exception):
        _drop_previews('struct A {}\n#Preview {\n    Text("x")\n')


@pytest.mark.parametrize("snippet, dead", [
    ('Button(action: {\n    // Share action\n}) { Text("x") }', True),
    ("Button(action: {}) { Text(\"x\") }", True),
    ("Button { } label: { Text(\"x\") }", True),
    ("Button(role: .destructive) {\n} label: { Text(\"x\") }", True),
    ('Button("See All") { }', True),
    ('Button("See All", action: {})', True),
    ('Button("Cancel", role: .cancel) { }', False),
    ('Button("OK", role: .cancel, action: {})', False),
    ("Button(action: { show = true }) { Text(\"x\") }", False),
    ('Button("Go") { go() }', False),
    ("NavBackButton { }", False),
    ('#Preview {\n    Button("x") { }\n}', False),
    ("struct X_Previews: PreviewProvider {\n static var previews: some View { Button(\"x\") { } }\n}", False),
    ('let marker = "#Preview"\nButton("x") { }', True),
])
def test_the_detector_itself(snippet, dead):
    """Anti-vacuity: every spelling is caught, and the exemptions exempt only what they should."""
    src = _drop_previews(_strip_comments(snippet))
    assert bool(_dead_sites(src)) is dead, snippet
