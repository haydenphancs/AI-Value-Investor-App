"""Every iOS build command the rulebook hands to an agent must be the memory-safe one.

WHY (2026-09-24/25): this is a 16 GB Mac. Two `swift-frontend` compilers from an agent's build
reached 39.2 GB and 37.1 GB (JetsamEvent-2026-09-24-220626), and the next morning the Mac
kernel-panicked. Every open session died both times. The existing build rules lived only in one
agent's private memory, while CLAUDE.md, the iOS rules and three skills all printed the build
command WITHOUT `-jobs` — so every session that followed the rulebook ran the unbounded form
(6.4 GB per compiler vs 1.88 GB with `-jobs 2`, measured 2026-09-10).

This pins the text half of the fix: every copy of the command carries `-jobs 1|2` and a pinned
simulator id, and CLAUDE.md keeps its "Machine safety — Swift builds" section. The runtime half
(refusing an unbounded build, killing a runaway compiler) is `.claude/hooks/`.

⚠️ CLAUDE.md, CLAUDE.local.md and `.claude/` are GITIGNORED (agent config on the owner's Mac,
which is also the only place builds run). There is no git history to restore them from, so a
mutation check must back up to a directory that exists and verify it before editing. This test
skips where the files are absent (a fresh clone).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, List, Tuple

import pytest

REPO = Path(__file__).resolve().parents[2]

# The files an agent reads for "how do I build" (all gitignored — see the module docstring).
_RULE_FILES = ["CLAUDE.md", ".claude/rules/ios-swiftui.md"]
_SKILL_GLOB = ".claude/skills/*/SKILL.md"
_LOCAL_FILE = "CLAUDE.local.md"

# Every iOS build command targets the simulator SDK; prose that merely names the tool does not.
_ANCHOR = "-sdk iphonesimulator"
_JOBS = re.compile(r"(?<!\S)-jobs\s+[12](?!\S)")
# A pinned device id, or the generic simulator destination — never `name=iPhone 17 Pro`, which
# matches three simulators on this Mac.
_PINNED = re.compile(r"-destination\s+'(?:id=[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}"
                     r"|generic/platform=iOS Simulator)'")


_needs_rulebook = pytest.mark.skipif(
    not (REPO / "CLAUDE.md").exists(), reason="CLAUDE.md is gitignored agent config; absent here")


def _files() -> List[Path]:
    out = [REPO / f for f in _RULE_FILES] + sorted(REPO.glob(_SKILL_GLOB))
    local = REPO / _LOCAL_FILE
    if local.exists():
        out.append(local)
    return out


def build_commands(text: str) -> Iterator[Tuple[int, str]]:
    """Each logical shell command containing the anchor: the physical line holding it plus any
    lines joined to it by a trailing backslash (before and after)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _ANCHOR not in line:
            continue
        start = i
        while start > 0 and lines[start - 1].rstrip().endswith("\\"):
            start -= 1
        end = i
        while end < len(lines) - 1 and lines[end].rstrip().endswith("\\"):
            end += 1
        yield i + 1, " ".join(l.rstrip().rstrip("\\") for l in lines[start:end + 1])


def unsafe_commands(text: str) -> List[str]:
    out = []
    for lineno, cmd in build_commands(text):
        if not _JOBS.search(cmd):
            out.append(f"line {lineno}: no `-jobs 1|2`: {cmd.strip()[:160]}")
        if not _PINNED.search(cmd):
            out.append(f"line {lineno}: simulator not pinned by id: {cmd.strip()[:160]}")
    return out


@_needs_rulebook
def test_every_build_command_in_the_rulebook_is_memory_bounded():
    problems, seen = [], 0
    for path in _files():
        text = path.read_text(encoding="utf-8")
        seen += sum(1 for _ in build_commands(text))
        problems += [f"{path.relative_to(REPO)} {p}" for p in unsafe_commands(text)]
    # Anti-vacuity: CLAUDE.md, the iOS rules and three skills each print the command.
    assert seen >= 5, f"only {seen} build commands found — did the anchor change?"
    assert problems == [], "\n".join(problems)


_SAFETY_MUSTS = ("-jobs 2", "subagent", "One Swift compile on this Mac at a time",
                 "pgrep -fl swift-frontend", "run_in_background", "39.2 GB", "10240 MB", "7168 MB",
                 "Never weaken either one",
                 # The root cause (2026-09-25): giant generated Swift literals.
                 "test_ios_no_giant_literals.py", "solver-expression-time-threshold")


def machine_safety_problems(text: str) -> List[str]:
    # `[^\n]*`, not `.*`: under DOTALL a greedy `.*` on the heading line swallows the whole
    # section and leaves the body empty (the first version of this test did exactly that).
    m = re.search(r"^## [^\n]*Machine safety — Swift builds[^\n]*\n(.*?)(?=^## )", text, re.M | re.S)
    if not m:
        return ["CLAUDE.md lost its 'Machine safety — Swift builds' section"]
    return [f"Machine safety section no longer says {must!r}" for must in _SAFETY_MUSTS
            if must not in m.group(1)]


@_needs_rulebook
def test_claude_md_keeps_the_machine_safety_section():
    assert machine_safety_problems((REPO / "CLAUDE.md").read_text(encoding="utf-8")) == []


@_needs_rulebook
def test_the_safety_checks_fail_on_a_weakened_rulebook():
    """In-memory mutations of the REAL files (never on disk: they are gitignored, so there is
    nothing to restore them from)."""
    claude = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert machine_safety_problems(claude.replace("Machine safety — Swift builds", "Swift builds", 1))
    assert machine_safety_problems(claude.replace("**10240 MB**", "**16 GB**", 1))
    assert machine_safety_problems(claude.replace("test_ios_no_giant_literals.py", "a test", 1))
    skill = (REPO / ".claude/skills/add-fmp-endpoint/SKILL.md").read_text(encoding="utf-8")
    assert unsafe_commands(skill) == []
    assert unsafe_commands(skill.replace(" -jobs 2 build", " build", 1))


# ── The checker itself must be able to fail ─────────────────────────────────────────────


@pytest.mark.parametrize("cmd,bad", [
    ("DEVELOPER_DIR=x tool -project p -scheme ios -sdk iphonesimulator "
     "-destination 'platform=iOS Simulator,name=iPhone 17 Pro' build", 2),
    ("tool -scheme ios -sdk iphonesimulator -destination 'id=57C9097B-08F1-4CB1-BF9A-035876F3604F' build", 1),
    ("tool -scheme ios -sdk iphonesimulator -jobs 8 -destination 'id=57C9097B-08F1-4CB1-BF9A-035876F3604F' build", 1),
    ("tool -scheme ios -sdk iphonesimulator -jobs 2 -destination 'id=57C9097B-08F1-4CB1-BF9A-035876F3604F' build", 0),
    ("tool -jobs 2 -scheme ios -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' build", 0),
    ("tool -jobs 2 -scheme ios -sdk iphonesimulator -destination 'platform=iOS Simulator,name=iPhone 17 Pro' build", 1),
    ("tool -project p \\\n  -scheme ios -sdk iphonesimulator \\\n"
     "  -destination 'id=57C9097B-08F1-4CB1-BF9A-035876F3604F' \\\n  -jobs 2 build", 0),
    ("tool -project p \\\n  -scheme ios -sdk iphonesimulator \\\n"
     "  -destination 'id=57C9097B-08F1-4CB1-BF9A-035876F3604F' build\n-jobs 2", 1),
])
def test_the_checker_catches_an_unbounded_command(cmd, bad):
    assert len(unsafe_commands(cmd)) == bad, unsafe_commands(cmd)
