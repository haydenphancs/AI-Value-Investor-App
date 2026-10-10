"""
The compaction safety net in .claude/hooks/ must record, restore and stay inside its budget.

WHY: by 2026-10-09 this repo had auto-compacted 47 times in 24 sessions, each time shrinking about
970k tokens to about 20k. The summary is a retelling: the user's exact decisions, rejected
approaches, which files THIS session edited (parallel sessions share one working tree) and what was
verified are what drift. session_state.py keeps a per-session record on disk, and the SessionStart
hook prints it back VERBATIM after a compaction. CLAUDE.md "Long sessions — working state" is the
discipline that fills it.

What breaks it silently, and is pinned here:
  * Output over Claude Code's 10,000-character hook cap: Claude then sees a 2,000-character preview,
    and the checklist and state vanish with no error anywhere.
  * A session_id that walks out of the state dir.
  * A hook that raises or exits non-zero on a payload it did not expect. A hook must never cost the
    session; the worst it may do is skip its own bookkeeping.
  * Registration drift: a hook that is not wired into settings.json does nothing.

Every run is hermetic: CAYDEX_SESSION_STATE_DIR and CLAUDE_PROJECT_DIR point into tmp_path.
`.claude/` is gitignored — on a clone without it these tests skip.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_HOOKS = _ROOT / ".claude" / "hooks"
_SETTINGS = _ROOT / ".claude" / "settings.json"
_LEDGER = _HOOKS / "post-tool-use-session-files.sh"
_POST_COMPACT = _HOOKS / "post-compact.sh"
_SESSION_START = _HOOKS / "session-start.sh"
_STATUSLINE = _HOOKS / "statusline.sh"
_HOOK_CAP = 10_000

pytestmark = pytest.mark.skipif(not _HOOKS.is_dir(), reason=".claude/hooks not present (gitignored)")


def _run(script: Path, payload: str, tmp_path: Path, project: Path | None = None,
         state_dir: Path | None = None) -> subprocess.CompletedProcess:
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": os.environ.get("HOME", "/tmp"),
        "CLAUDE_PROJECT_DIR": str(project or tmp_path),
        "CAYDEX_SESSION_STATE_DIR": str(state_dir or tmp_path / "session-state"),
    }
    return subprocess.run(["/bin/bash", str(script)], input=payload, capture_output=True, text=True,
                          env=env, timeout=30)


def _edit(tmp_path: Path, sid: str, path: str, **kw) -> subprocess.CompletedProcess:
    return _run(_LEDGER, json.dumps({"session_id": sid, "tool_name": "Edit",
                                     "tool_input": {"file_path": path}}), tmp_path, **kw)


def _start(tmp_path: Path, source: str | None, sid: str | None = "s1", **kw) -> subprocess.CompletedProcess:
    payload: dict = {}
    if source is not None:
        payload["source"] = source
    if sid is not None:
        payload["session_id"] = sid
    return _run(_SESSION_START, json.dumps(payload), tmp_path, **kw)


# ── the ledger ────────────────────────────────────────────────────────────────────────────────

def test_the_ledger_records_each_file_once_in_first_edit_order(tmp_path):
    for p in ("backend/app/a.py", "backend/app/b.py", "backend/app/a.py"):
        r = _edit(tmp_path, "s1", str(tmp_path / p))
        assert r.returncode == 0 and r.stdout == "" and r.stderr == "", r.stderr
    ledger = (tmp_path / "session-state" / "s1.files").read_text().splitlines()
    assert ledger == ["backend/app/a.py", "backend/app/b.py"], "paths inside the project are stored relative, deduped"


def test_a_file_outside_the_project_is_kept_absolute(tmp_path):
    outside = tmp_path.parent / "elsewhere.md"
    _edit(tmp_path, "s1", str(outside), project=tmp_path / "proj")
    assert (tmp_path / "session-state" / "s1.files").read_text().splitlines() == [str(outside)]


def test_the_state_file_itself_is_not_work(tmp_path):
    _edit(tmp_path, "s1", str(tmp_path / "session-state" / "s1.md"))
    assert not (tmp_path / "session-state" / "s1.files").exists()


@pytest.mark.parametrize("payload", [
    "", "not json", "[]", "null",
    '{"session_id": "s1"}',                                            # no tool_input
    '{"tool_input": {"file_path": "/x/y.py"}}',                          # no session id
    '{"session_id": "s1", "tool_input": {"file_path": 5}}',              # wrong type
    '{"session_id": "s1", "tool_input": {"file_path": "   "}}',
    '{"session_id": "s1", "tool_input": "flat"}',
    '{"session_id": "../..", "tool_input": {"file_path": "/x/y.py"}}',   # sanitises to nothing
])
def test_the_ledger_never_fails_and_never_writes_on_a_bad_payload(tmp_path, payload):
    r = _run(_LEDGER, payload, tmp_path)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == "", (payload, r.stderr)
    assert not (tmp_path / "session-state").exists() or not any((tmp_path / "session-state").iterdir())


def test_a_session_id_cannot_leave_the_state_dir(tmp_path):
    state = tmp_path / "a" / "session-state"
    evil = "../../../escaped"
    _edit(tmp_path, evil, str(tmp_path / "x.py"), state_dir=state)
    _run(_POST_COMPACT, json.dumps({"session_id": evil, "trigger": "auto", "compact_summary": "S"}),
         tmp_path, state_dir=state)
    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert written and all(w.startswith("a/session-state/escaped.") for w in written), written


# ── post-compact ──────────────────────────────────────────────────────────────────────────────

def test_post_compact_archives_the_summary_and_stamps_the_ledger(tmp_path):
    for _ in range(2):  # two compactions inside one second must not overwrite each other
        r = _run(_POST_COMPACT, json.dumps({"session_id": "s1", "trigger": "auto",
                                            "compact_summary": "1. Primary Request: build X"}), tmp_path)
        assert r.returncode == 0 and r.stdout == "", r.stderr
    state = tmp_path / "session-state"
    archives = sorted(state.glob("s1.compact-*.md"))
    assert len(archives) == 2
    assert "1. Primary Request: build X" in archives[0].read_text()
    stamps = (state / "s1.files").read_text().splitlines()
    assert len(stamps) == 2 and all(s.startswith("# compacted ") and "(auto)" in s for s in stamps)


@pytest.mark.parametrize("payload", ["", "garbage", '{"trigger": "auto"}', '{"session_id": 7}'])
def test_post_compact_never_fails(tmp_path, payload):
    r = _run(_POST_COMPACT, payload, tmp_path)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""


def test_a_compaction_without_a_summary_still_stamps_the_ledger(tmp_path):
    _run(_POST_COMPACT, json.dumps({"session_id": "s1", "trigger": "weird"}), tmp_path)
    assert (tmp_path / "session-state" / "s1.files").read_text().startswith("# compacted ")
    assert "(unknown)" in (tmp_path / "session-state" / "s1.files").read_text()
    assert not list((tmp_path / "session-state").glob("s1.compact-*"))


# ── the restore block (SessionStart, source=compact) ─────────────────────────────────────────

def test_the_restore_block_brings_back_the_state_and_this_sessions_files(tmp_path):
    state = tmp_path / "session-state"
    state.mkdir()
    (state / "s1.md").write_text('# Goal\nShip X\n# Decisions\n- "Full safety net" (user)\n')
    _edit(tmp_path, "s1", str(tmp_path / "backend/app/a.py"))
    _edit(tmp_path, "other", str(tmp_path / "backend/app/not_mine.py"))
    _run(_POST_COMPACT, json.dumps({"session_id": "s1", "trigger": "auto", "compact_summary": "S"}), tmp_path)
    r = _start(tmp_path, "compact")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "Restored after compaction" in out
    assert '- "Full safety net" (user)' in out, "the state file comes back verbatim"
    assert "backend/app/a.py" in out and "not_mine.py" not in out, "only THIS session's files"
    assert "Compactions this session: 1" in out
    assert "git diff" in out and "Never redo a Done item" in out, "the checklist"
    assert "--- Recent commits ---" not in out, "the summary already covers git; the budget goes to the state"


def test_the_restore_block_says_so_when_there_is_no_state_yet(tmp_path):
    out = _start(tmp_path, "compact").stdout
    assert "no working-state file yet" in out and "write it now" in out


def test_the_restore_block_stays_under_the_hook_cap_whatever_the_state_holds(tmp_path):
    state = tmp_path / "session-state"
    state.mkdir()
    (state / "s1.md").write_text("# Goal\n" + ("x" * 99 + "\n") * 600 + "TAIL-MARKER\n")  # ~60 KB
    (state / "s1.files").write_text("".join(f"backend/{'d' * 300}/file_{i}.py\n" for i in range(500))
                                    + "".join(f"# compacted 2026-10-0{i % 9 + 1}T00:00:00+00:00 (auto)\n" for i in range(30)))
    r = _start(tmp_path, "compact")
    assert r.returncode == 0, r.stderr
    assert len(r.stdout) < _HOOK_CAP, f"{len(r.stdout)} chars — over the cap Claude sees only a 2,000-char preview"
    assert "Never redo a Done item" in r.stdout, "the checklist must survive any truncation"
    assert "truncated at 5000 of" in r.stdout and "TAIL-MARKER" not in r.stdout
    assert "more in" in r.stdout, "a long ledger points at the file instead of overflowing"


def test_the_restore_block_shows_each_files_git_status(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(proj)]
    subprocess.run([*git, "init", "-q"], check=True)
    for name in ("changed.py", "clean.py"):
        (proj / name).write_text("v1\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    (proj / "changed.py").write_text("v2\n")
    (proj / "new.py").write_text("n\n")
    for name in ("changed.py", "clean.py", "new.py"):
        _edit(tmp_path, "s1", str(proj / name), project=proj)
    out = _start(tmp_path, "compact", project=proj).stdout
    assert " M changed.py" in out and "== clean.py" in out and "?? new.py" in out, out


# ── SessionStart, every other source ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("source", ["startup", "resume", "clear", "fork"])
def test_every_other_start_prints_the_normal_block_and_the_state_path(tmp_path, source):
    r = _start(tmp_path, source)
    assert r.returncode == 0, r.stderr
    assert "=== AI Value Investor App — session context ===" in r.stdout
    assert "--- Recent commits ---" in r.stdout
    assert "Working-state file for this session: " in r.stdout and "s1.md" in r.stdout
    assert len(r.stdout) < 4000


def test_a_resumed_session_is_told_its_state_exists(tmp_path):
    (tmp_path / "session-state").mkdir()
    (tmp_path / "session-state" / "s1.md").write_text("# Goal\nX\n# Next\nY\n")
    assert "(exists, 4 lines — read it before continuing)" in _start(tmp_path, "resume").stdout


@pytest.mark.parametrize("payload", ["", "garbage", "[1]", '{"source": "compact"}', '{"session_id": "s1"}'])
def test_session_start_never_fails_on_a_bad_payload(tmp_path, payload):
    r = _run(_SESSION_START, payload, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "=== AI Value Investor App — session context ===" in r.stdout
    assert "Traceback" not in r.stdout + r.stderr


def test_a_compaction_without_a_session_id_says_so(tmp_path):
    assert "no session id" in _start(tmp_path, "compact", sid=None).stdout


def test_a_start_prunes_only_old_files_in_a_dir_it_owns(tmp_path):
    old = time.time() - 40 * 86400
    owned = tmp_path / "session-state"
    owned.mkdir()
    for name in ("gone.md", "gone.files", "s1.md", "keep.txt"):
        (owned / name).write_text("x")
        os.utime(owned / name, (old, old))
    (owned / "fresh.md").write_text("x")
    _start(tmp_path, "startup")
    assert sorted(p.name for p in owned.iterdir()) == ["fresh.md", "keep.txt", "s1.md"], \
        "old .md/.files go; the current session's own state and non-state files stay"

    foreign = tmp_path / "not-mine"
    foreign.mkdir()
    (foreign / "old.md").write_text("x")
    os.utime(foreign / "old.md", (old, old))
    _start(tmp_path, "startup", state_dir=foreign)
    assert (foreign / "old.md").exists(), "never sweep a directory not named session-state"


# ── the status line ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pct,expected", [
    (62.4, "ctx 62% · Opus 5.5"),
    (0, "ctx 0% · Opus 5.5"),
    (84.99, "ctx 84% · Opus 5.5"),
    (90, "⚠ ctx 90% — compaction near · Opus 5.5"),
    (None, "ctx – · Opus 5.5"),          # null before the first reply and right after a compaction
    ("62", "ctx – · Opus 5.5"),
    (True, "ctx – · Opus 5.5"),
    (-5, "ctx – · Opus 5.5"),
])
def test_the_status_line_shows_context_use(tmp_path, pct, expected):
    payload = json.dumps({"model": {"display_name": "Opus 5.5"}, "context_window": {"used_percentage": pct}})
    r = _run(_STATUSLINE, payload, tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == expected, r.stdout + r.stderr


@pytest.mark.parametrize("payload", ["", "garbage", '{"context_window": [1]}', '{"context_window": {"used_percentage": NaN}}'])
def test_the_status_line_never_fails(tmp_path, payload):
    r = _run(_STATUSLINE, payload, tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == "ctx –", r.stdout + r.stderr


# ── registration ──────────────────────────────────────────────────────────────────────────────

def _commands(entries: list[dict], matcher_ok) -> list[str]:
    return [h["command"] for e in entries if matcher_ok(e.get("matcher", "")) for h in e["hooks"]]


def test_settings_wire_every_piece():
    settings = json.loads(_SETTINGS.read_text(encoding="utf-8"))
    hooks = settings["hooks"]
    # SessionStart must fire on compaction: an empty matcher, or one naming `compact`.
    start = _commands(hooks["SessionStart"], lambda m: m == "" or "compact" in m.split("|"))
    assert any(_SESSION_START.name in c for c in start), "SessionStart does not fire after a compaction"
    assert any(_POST_COMPACT.name in c for c in _commands(hooks.get("PostCompact", []), lambda m: m in ("", "auto|manual", "manual|auto")))
    ledger = _commands(hooks["PostToolUse"], lambda m: {"Edit", "Write"} <= set(m.split("|")))
    assert any(_LEDGER.name in c for c in ledger), "the ledger must see both Edit and Write"
    assert _STATUSLINE.name in settings["statusLine"]["command"]
    for script in (_SESSION_START, _POST_COMPACT, _LEDGER, _STATUSLINE, _HOOKS / "session_state.py"):
        assert script.is_file(), f"{script.name} is registered but missing"


def test_claude_md_tells_the_summarizer_what_to_keep():
    claude_md = _ROOT / "CLAUDE.md"
    if not claude_md.is_file():
        pytest.skip("CLAUDE.md not present (gitignored)")
    text = claude_md.read_text(encoding="utf-8")
    # Claude Code's documented hook: a "Compact instructions" section in CLAUDE.md steers the summary.
    assert "## Compact instructions" in text
    section = text.split("## Compact instructions", 1)[1].split("\n## ", 1)[0]
    for must_keep in (".claude/session-state/", "user decision", "next step"):
        assert must_keep in section, f"Compact instructions no longer ask to keep: {must_keep}"
