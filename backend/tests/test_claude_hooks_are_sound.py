"""
The build-safety hooks in .claude/hooks/ must parse, RUN, and keep their verdicts.

WHY: on 2026-09-25 an edit put a heredoc containing a backtick inside `$(...)` in
pre-tool-use-xcodebuild-guard.sh. bash 3.2 (macOS /bin/bash) mis-parses that, the hook exited 2
on the syntax error, and because it runs before EVERY Bash tool call it blocked every command in
every Claude session until fixed. Those hooks exist because a runaway Swift build kernel-panicked
this Mac (see test_ios_no_giant_literals.py), so a broken hook is either an outage or a hole.

`bash -n` alone is not enough: bash 3.2 does not parse the body of a `$(...)`, so a syntax error
in there fails OPEN at runtime (an empty substitution, rc 0) and `-n` stays green. Hence the
end-to-end runs of the real guard below.

`.claude/` is gitignored — on a clone without it these tests skip.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parents[2] / ".claude" / "hooks"
_GUARD = _HOOKS / "pre-tool-use-xcodebuild-guard.sh"
_ANALYZER = _HOOKS / "xcodebuild_jobs_check.py"

pytestmark = pytest.mark.skipif(not _HOOKS.is_dir(), reason=".claude/hooks not present (gitignored)")


def _shells() -> list[str]:
    found = ["/bin/bash"] if Path("/bin/bash").exists() else []
    path_bash = shutil.which("bash")
    if path_bash and path_bash not in found:
        found.append(path_bash)
    return found


@pytest.mark.parametrize("script", sorted(p.name for p in _HOOKS.glob("*.sh")) if _HOOKS.is_dir() else [])
def test_every_shell_hook_parses_under_every_bash(script):
    for shell in _shells():
        r = subprocess.run([shell, "-n", str(_HOOKS / script)], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, f"{script} does not parse under {shell}: {r.stderr.strip()}"


@pytest.mark.parametrize("script", sorted(p.name for p in _HOOKS.glob("*.py")) if _HOOKS.is_dir() else [])
def test_every_python_hook_compiles(script):
    compile((_HOOKS / script).read_text(encoding="utf-8"), script, "exec")


def test_the_watchdog_self_test_passes():
    watchdog = _HOOKS / "swift-build-memory-watchdog.sh"
    assert watchdog.exists()
    r = subprocess.run(["/bin/bash", str(watchdog), "--self-test"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "self-test OK" in r.stdout, r.stdout + r.stderr


# ── the -jobs analyzer ────────────────────────────────────────────────────────────────────────

def _analyzer():
    spec = importlib.util.spec_from_file_location("_xcodebuild_jobs_check", _ANALYZER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.violations


X = "xcode" + "build"
D = "DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer"

_BLOCK = [
    f"{D} {X} -project frontend/ios/ios.xcodeproj -scheme ios -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' build",
    f"{X} -project p -scheme ios",                              # default action is build
    f"{X} -list -project p && {X} -project p -scheme ios build",  # a query must not exempt the chain
    f"{X} -project p -scheme ios docbuild",
    f"{X} -project p -exportLocalizations -localizationPath out",  # builds to extract strings
    f"{X} -jobs 8 -scheme ios build",
    f"{X} -jobs 22 -scheme ios build",
    f'bash -c "{X} -scheme ios build"',
    f'bash -lc "{X} -scheme ios build"',                      # combined shell options
    f'zsh -ec "{X} -scheme ios build"',
    f"cd frontend/ios; {X} -scheme ios build",
    f"xcrun {X} -scheme ios build",
    f"xcrun -sdk iphonesimulator {X} -scheme ios build",       # wrapper option with a value
    f"time {D} {X} -scheme ios build",
    f"(cd frontend/ios && {X} -scheme ios build)",
    f"if {X} -scheme ios build; then echo ok; fi",             # shell grammar before the command
    f"for s in a b; do {X} -scheme $s build; done",
    f"{{ {X} -scheme ios build; }}",
    f"! {X} -scheme ios build",
    f'echo "$({X} -scheme ios build)"',                        # command substitution
    f"{X} -scheme ios build # -jobs 2",                        # a comment is not a flag
    f"{X} -scheme ios build 2>&1 | tee build.log",
    f"bash <<'EOF'\n{X} -scheme ios build\nEOF",               # a heredoc a SHELL reads
]
_ALLOW = [
    f"{D} {X} -jobs 2 -project frontend/ios/ios.xcodeproj -scheme ios -sdk iphonesimulator -destination 'generic/platform=iOS Simulator' build",
    f"{X} -list -project p",
    f"{X} -showBuildSettings -project p -scheme ios",
    f"{X} -project p -scheme ios clean",
    f"{X} -project p -scheme test -jobs 2 build",               # `test` here is a scheme, not the action
    f"grep -n {X} CLAUDE.md",
    f"git commit -m 'fix {X} build guard'",
    f'echo "{X} build"',
    f"{X} test-without-building -scheme ios",
    f"{X} -version",
    f"{X} -scheme ios -jobs 1 test",
    f"{X} -list && {X} -jobs 2 -scheme ios build",
    f"command -v {X}",
    f"xcrun --find {X}",
    f"which {X}",
    f"cat > f.sh <<'EOF'\n{X} -scheme ios build\nEOF",          # heredoc DATA, not a command
    f"git commit -m \"$(cat <<'EOF'\nrun {X} -scheme ios build first\nEOF\n)\"",
    f"if {X} -jobs 2 -scheme ios build; then echo ok; fi",
]


@pytest.mark.parametrize("cmd", _BLOCK)
def test_the_jobs_analyzer_blocks_compiling_invocations_without_jobs(cmd):
    assert _analyzer()(cmd), f"should be BLOCKED: {cmd}"


@pytest.mark.parametrize("cmd", _ALLOW)
def test_the_jobs_analyzer_allows_safe_or_non_invocations(cmd):
    assert not _analyzer()(cmd), f"should be ALLOWED: {cmd}"


def test_line_continuations_are_joined_before_judging():
    """The canonical build command is written across `\\`-newline continuations; `-jobs 2` on one
    line must count for the `build` on another (and its absence must still block)."""
    violations = _analyzer()
    assert violations(f"{D} \\\n  {X} -project p -scheme ios \\\n  -destination 'x' build")
    assert not violations(f"{D} \\\n  {X} -jobs 2 -project p -scheme ios \\\n  -destination 'x' build")


def test_the_analyzer_exit_codes_are_distinct_from_a_crash():
    """1 is CPython's uncaught-exception status, so it must never mean "violation"."""
    run = lambda c: subprocess.run([sys.executable, str(_ANALYZER), c], capture_output=True, text=True, timeout=30)
    assert run(f"{X} -scheme ios build").returncode == 10
    assert run(f"{X} -jobs 2 -scheme ios build").returncode == 0
    assert run("ls -la").returncode == 0


# ── the real guard, end to end ────────────────────────────────────────────────────────────────

def _guard(cmd: str, tmp_path: Path, guard: Path = _GUARD) -> subprocess.CompletedProcess:
    # CLAUDE_PROJECT_DIR → an empty dir, so the guard finds no watchdog to spawn.
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path))
    payload = json.dumps({"tool_input": {"command": cmd}})
    return subprocess.run(["/bin/bash", str(guard)], input=payload, capture_output=True, text=True,
                          env=env, timeout=60)


def _no_shell_errors(r):
    assert "syntax error" not in r.stderr and "command substitution" not in r.stderr \
        and "unexpected EOF" not in r.stderr, r.stderr


def test_the_guard_passes_ordinary_commands_untouched(tmp_path):
    r = _guard("ls -la", tmp_path)
    assert r.returncode == 0 and r.stderr == "", r.stderr


def test_the_guard_blocks_a_build_without_jobs(tmp_path):
    r = _guard(f"{X} -scheme ios build", tmp_path)
    _no_shell_errors(r)
    assert r.returncode == 2 and "-jobs" in r.stderr, r.stderr


def test_the_guard_admits_a_compliant_build(tmp_path):
    """0 — or 2 only from the concurrency / swap gates when something else is building right now."""
    r = _guard(f"{X} -jobs 2 -scheme ios build", tmp_path)
    _no_shell_errors(r)
    assert r.returncode == 0 or ("already" in r.stderr or "swap" in r.stderr), r.stderr


def test_the_guard_falls_back_conservatively_without_its_analyzer(tmp_path):
    lone = tmp_path / "hooks"
    lone.mkdir()
    shutil.copy(_GUARD, lone / _GUARD.name)          # no analyzer beside it
    r = _guard(f"{X} -scheme ios build", tmp_path, lone / _GUARD.name)
    _no_shell_errors(r)
    assert r.returncode == 2 and "analyzer unavailable" in r.stderr, r.stderr
    ok = _guard(f"{X} -jobs 2 -scheme ios build", tmp_path, lone / _GUARD.name)
    assert ok.returncode == 0 or ("already" in ok.stderr or "swap" in ok.stderr), ok.stderr
