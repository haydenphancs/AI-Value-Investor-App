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
    # The shape that RAN, unbounded, on 2026-09-11 (session 9125dc57): a build on the line that
    # closes a multi-line `python -c "…"`. A line-by-line parse read it as "no build" (2026-09-25).
    f'cd "/repo/backend"; ./venv/bin/python -c "\nimport re\nprint(re.escape(\'x\'))\n"; cd ..; {D} {X} '
    f"-project frontend/ios/ios.xcodeproj -scheme ios -destination 'platform=iOS Simulator,name=iPhone 17 Pro' "
    f'build 2>&1 | grep -E "error:|warning:" | head -30',
    f"cd /repo; {D} {X} -scheme ios build 2>&1 | awk '\n/error:/ {{print}}\n/BUILD/ {{print}}\n'",  # quote opens after
    f"echo ${{#V}}; {X} -scheme ios build",                    # `#` mid-word is not a comment
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
    # Text edits that MENTION a build — docs, memory, commit messages — must not trip anything.
    f"python3 - <<'PY'\np = 'CLAUDE.md'; s = open(p).read()\nopen(p, 'w').write(s.replace('`{X} build`', '`{X} -jobs 2 build`'))\nPY",
    f"git commit -m \"$(cat <<'EOF'\nDon't run {X} -scheme ios build without -jobs; it's the panic.\nEOF\n)\"",
    f"bash -n .claude/hooks/pre-tool-use-{X}-guard.sh",       # a filename, not the word
    f"n=$(pgrep -f \"{X}|swift-frontend\" | wc -l)",           # a mention inside a judged substitution
]

# "Cannot judge" (exit 3): the guard then takes its conservative path — -jobs required, every gate,
# the watchdog. Exit 0 would let these skip every gate.
_UNSURE = [
    f"timeout 600 {X} -scheme ios build",                    # unknown wrapper
    f"xargs -I{{}} {X} -scheme {{}} build",
    f"python3 -c \"import subprocess; subprocess.run(['{X}', 'build'])\"",
    f"python3 - <<'PY'\nimport os\nos.system('{X} -scheme ios build')\nPY",
    f"osascript -e 'do shell script \"{X} build\"'",
    f"B={X}; $B -scheme ios build",                           # the command hidden in a variable
]


@pytest.mark.parametrize("cmd", _BLOCK)
def test_the_jobs_analyzer_blocks_compiling_invocations_without_jobs(cmd):
    assert _analyzer()(cmd), f"should be BLOCKED: {cmd}"


@pytest.mark.parametrize("cmd", _ALLOW)
def test_the_jobs_analyzer_allows_safe_or_non_invocations(cmd):
    assert _verdict()(cmd)[0] in (0, 20), f"should be ALLOWED: {cmd} → {_verdict()(cmd)}"


@pytest.mark.parametrize("cmd", _UNSURE)
def test_the_analyzer_fails_closed_when_it_cannot_see_through(cmd):
    """Exit 0 lets the guard skip EVERY gate and start no watchdog, so it is reserved for commands
    whose every `xcodebuild` is accounted for."""
    assert _verdict()(cmd)[0] == 3, f"should be CANNOT JUDGE (3): {cmd} → {_verdict()(cmd)}"


def _verdict():
    spec = importlib.util.spec_from_file_location("_xcodebuild_jobs_check_v", _ANALYZER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verdict


def test_line_continuations_are_joined_before_judging():
    """The canonical build command is written across `\\`-newline continuations; `-jobs 2` on one
    line must count for the `build` on another (and its absence must still block)."""
    violations = _analyzer()
    assert violations(f"{D} \\\n  {X} -project p -scheme ios \\\n  -destination 'x' build")
    assert not violations(f"{D} \\\n  {X} -jobs 2 -project p -scheme ios \\\n  -destination 'x' build")


def test_the_analyzer_exit_codes_are_distinct_from_a_crash():
    """1 is CPython's uncaught-exception status, so it must never mean "violation". 20 = a real,
    allowed build (the guard starts the watchdog on it); 0 = no build at all (a mention, a query)."""
    run = lambda c: subprocess.run([sys.executable, str(_ANALYZER), c], capture_output=True, text=True, timeout=30)
    assert run(f"{X} -scheme ios build").returncode == 10
    assert run(f"{X} -jobs 2 -scheme ios build").returncode == 20
    assert run(f"{X} -list -project p").returncode == 0
    assert run(f"grep -n {X} CLAUDE.md").returncode == 0
    assert run(f"timeout 600 {X} -scheme ios build").returncode == 3
    assert run("ls -la").returncode == 0


# ── the watchdog's lifecycle: only while Claude is BUILDING (the user's rule, 2026-09-25) ─────

_WATCHDOG = _HOOKS / "swift-build-memory-watchdog.sh"
_SIM_HOOK = _HOOKS / "pre-tool-use-sim-build-watchdog.sh"


def _code_lines(path: Path) -> list[str]:
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]


def test_opening_claude_does_not_start_the_watchdog():
    """"Claude open but not running anything → no watchdog." session-start.sh used to start it,
    and it then ran until reboot."""
    offending = [l for l in _code_lines(_HOOKS / "session-start.sh") if "swift-build-memory-watchdog" in l or "setsid" in l]
    assert not offending, f"session-start.sh starts the watchdog again: {offending}"


def test_the_watchdog_exits_by_itself_by_default():
    import re
    m = re.search(r'IDLE_EXIT_S="\$\{CAYDEX_SWIFT_WATCHDOG_IDLE_EXIT_S:-(\d+)\}"', _WATCHDOG.read_text(encoding="utf-8"))
    assert m, "IDLE_EXIT_S default not found"
    assert 0 < int(m.group(1)) <= 120, \
        f"default idle exit {m.group(1)}s — 0 means it never stops; the user wants it gone soon after a build"


def test_the_simulator_build_tool_starts_the_watchdog_and_never_blocks(tmp_path):
    settings = json.loads((_HOOKS.parent / "settings.json").read_text(encoding="utf-8"))
    matchers = {e["matcher"]: e for e in settings["hooks"]["PreToolUse"]}
    entry = matchers.get("mcp__Claude_Code_iOS_Simulator__build")
    assert entry and any(_SIM_HOOK.name in h["command"] for h in entry["hooks"]), \
        "the iOS Simulator build tool compiles without passing the Bash guard — it must start the watchdog"
    assert any("swift-build-memory-watchdog.sh" in l for l in _code_lines(_SIM_HOOK))
    lone = tmp_path / "hooks"                         # a copy with no watchdog beside it: spawns nothing
    lone.mkdir()
    shutil.copy(_SIM_HOOK, lone / _SIM_HOOK.name)
    r = subprocess.run(["/bin/bash", str(lone / _SIM_HOOK.name)], input='{"tool_input": {}}',
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stderr == "", r.stderr


def _guard_with_fake_watchdog(cmd: str, tmp_path: Path):
    """Run the real guard against a project dir whose 'watchdog' only leaves a marker file."""
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    marker = tmp_path / "spawned"
    (hooks / "swift-build-memory-watchdog.sh").write_text(f'#!/bin/bash\ntouch "{marker}"\n')
    r = _guard(cmd, tmp_path)
    for _ in range(40):                               # the spawn is detached; give it a moment
        if marker.exists():
            break
        subprocess.run(["sleep", "0.05"])
    return r, marker.exists()


def test_a_mention_or_a_query_does_not_start_the_watchdog(tmp_path):
    for cmd in (f"grep -n {X} CLAUDE.md", f"{X} -list -project p", f"git commit -m 'about {X}'"):
        r, spawned = _guard_with_fake_watchdog(cmd, tmp_path)
        assert not spawned, f"started the watchdog for a non-build: {cmd} ({r.stderr})"


def test_a_real_build_starts_the_watchdog(tmp_path):
    r, spawned = _guard_with_fake_watchdog(f"{X} -jobs 2 -scheme ios build", tmp_path)
    assert r.returncode == 0 and spawned, r.stderr


def _building_now() -> bool:
    ps = subprocess.run(["ps", "-Ao", "ucomm="], capture_output=True, text=True).stdout.split()
    return "swift-frontend" in ps or "xcodebuild" in ps


_POLLER = _HOOKS / "swift-build-watchdog-poller.sh"
_INSTALLER = _HOOKS / "install-swift-build-watchdog-agent.sh"


def test_the_launchd_poller_does_nothing_when_nothing_builds(tmp_path):
    """The user's own ⌘B builds are caught by a LaunchAgent that runs this every 5 s — so on an
    idle Mac it must exit at once and start nothing (the user's rule: no build → no watchdog)."""
    if _building_now():
        pytest.skip("a real build is running — the poller would rightly start the watchdog")
    import time
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": os.environ.get("HOME", "/tmp"),
           "CAYDEX_SWIFT_WATCHDOG_LOCK_DIR": str(tmp_path / "lock"),
           "CAYDEX_SWIFT_WATCHDOG_LOG": str(tmp_path / "wd.log")}
    t0 = time.monotonic()
    r = subprocess.run(["/bin/bash", str(_POLLER)], env=env, capture_output=True, text=True, timeout=30)
    if r.returncode != 0 or (tmp_path / "lock").exists():
        if _building_now():
            pytest.skip("a build started meanwhile")
    assert r.returncode == 0 and r.stderr == "", r.stderr
    assert time.monotonic() - t0 < 3, "the idle check must be near-instant — launchd runs it every 5 s"
    assert not (tmp_path / "lock").exists(), "the poller started a watchdog with no build running"


def test_the_launch_agent_plist_is_valid_and_points_at_the_poller(tmp_path):
    r = subprocess.run(["/bin/bash", str(_INSTALLER), "print-plist"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    plist = tmp_path / "agent.plist"
    plist.write_text(r.stdout)
    lint = subprocess.run(["plutil", "-lint", str(plist)], capture_output=True, text=True)
    assert lint.returncode == 0, lint.stdout + lint.stderr
    import plistlib
    d = plistlib.loads(plist.read_bytes())
    assert d["Label"] == "com.caydex.swift-build-watchdog"
    assert d["ProgramArguments"] == ["/bin/bash", str(_POLLER)] and _POLLER.exists()
    assert d["StartInterval"] <= 5 and d["ThrottleInterval"] <= d["StartInterval"], \
        "launchd throttles to ThrottleInterval (default 10 s) — both must allow a 5 s check"
    assert d["AbandonProcessGroup"] is True, "launchd would reap the watchdog the job execs into"


def test_the_watchdog_exits_on_its_own_when_nothing_builds(tmp_path):
    if _building_now():
        pytest.skip("a real build is running — the watchdog would rightly stay alive")
    env = dict(os.environ, CAYDEX_SWIFT_WATCHDOG_IDLE_EXIT_S="2",
               CAYDEX_SWIFT_WATCHDOG_LOG=str(tmp_path / "wd.log"),
               CAYDEX_SWIFT_WATCHDOG_LOCK_DIR=str(tmp_path / "lock"))
    p = subprocess.Popen(["/bin/bash", str(_WATCHDOG)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        p.wait(timeout=20)
    except subprocess.TimeoutExpired:
        building = _building_now()
        p.terminate()
        p.wait(timeout=10)
        if building:
            pytest.skip("a real build started meanwhile — the watchdog rightly stayed alive")
        pytest.fail("the watchdog kept running with no build — it must stop by itself")
    assert "no Swift build for 2s" in (tmp_path / "wd.log").read_text(), (tmp_path / "wd.log").read_text()
    assert not (tmp_path / "lock").exists(), "the lock was not released"


# ── the real guard, end to end ────────────────────────────────────────────────────────────────

def _shims(tmp_path: Path, xcodebuild_running: bool = False, ps_table: str = "") -> dict:
    """PATH shims so the guard's gates are DETERMINISTIC: healthy swap, and a process table we
    choose — a real machine's swap level or another session's build must not decide a test."""
    bin_dir = tmp_path / "shimbin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "sysctl").write_text('#!/bin/bash\ncase "$*" in *vm.swapusage*) '
                                    'echo "total = 0.00M  used = 0.00M  free = 0.00M  (encrypted)";; '
                                    '*) exec /usr/sbin/sysctl "$@";; esac\n')
    (bin_dir / "pgrep").write_text('#!/bin/bash\n[ "$*" = "-x xcodebuild" ] && [ -n "$FAKE_XCODEBUILD" ] '
                                   '&& { echo 99999; exit 0; }\nexit 1\n')
    (tmp_path / "ps_table").write_text(ps_table)
    (bin_dir / "ps").write_text(f'#!/bin/bash\nif [ "$*" = "-Ao pid=,ppid=,ucomm=" ]; then cat "{tmp_path / "ps_table"}"; '
                                'exit 0; fi\nexec /bin/ps "$@"\n')
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": os.environ.get("HOME", "/tmp")}
    if xcodebuild_running:
        env["FAKE_XCODEBUILD"] = "1"
    return env


def _guard(cmd: str, tmp_path: Path, guard: Path = _GUARD, **shim) -> subprocess.CompletedProcess:
    # CLAUDE_PROJECT_DIR → an empty dir, so the guard finds no watchdog to spawn.
    env = dict(_shims(tmp_path, **shim), CLAUDE_PROJECT_DIR=str(tmp_path))
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
    r = _guard(f"{X} -jobs 2 -scheme ios build", tmp_path)
    _no_shell_errors(r)
    assert r.returncode == 0, r.stderr


def test_the_one_build_gate_still_applies_to_real_builds(tmp_path):
    r = _guard(f"{X} -jobs 2 -scheme ios build", tmp_path, xcodebuild_running=True)
    assert r.returncode == 2 and "already" in r.stderr, r.stderr
    # …but a command that only MENTIONS xcodebuild is never refused because someone is building.
    ok = _guard(f"grep -n {X} build.log", tmp_path, xcodebuild_running=True)
    assert ok.returncode == 0, ok.stderr


def test_the_one_build_gate_ignores_the_editors_own_compiler(tmp_path):
    """Xcode's editor agent runs swift-frontend to type-check the open file — that is not a build."""
    table = "100 1 SKAgent\n101 100 swift-frontend\n"
    r = _guard(f"{X} -jobs 2 -scheme ios build", tmp_path, ps_table=table)
    assert r.returncode == 0, r.stderr
    building = _guard(f"{X} -jobs 2 -scheme ios build", tmp_path, ps_table="200 1 SWBBuildService\n201 200 swift-frontend\n")
    assert building.returncode == 2 and "already" in building.stderr, building.stderr


def test_a_command_it_cannot_judge_takes_the_conservative_path(tmp_path):
    r = _guard(f"timeout 600 {X} -scheme ios build", tmp_path)
    _no_shell_errors(r)
    assert r.returncode == 2 and "could not verify" in r.stderr, r.stderr
    r2, spawned = _guard_with_fake_watchdog(f"timeout 600 {X} -jobs 2 -scheme ios build", tmp_path)
    assert r2.returncode == 0 and spawned, r2.stderr


def test_the_guard_falls_back_conservatively_without_its_analyzer(tmp_path):
    lone = tmp_path / "hooks"
    lone.mkdir()
    shutil.copy(_GUARD, lone / _GUARD.name)          # no analyzer beside it
    r = _guard(f"{X} -scheme ios build", tmp_path, lone / _GUARD.name)
    _no_shell_errors(r)
    assert r.returncode == 2 and "analyzer unavailable" in r.stderr, r.stderr
    ok = _guard(f"{X} -jobs 2 -scheme ios build", tmp_path, lone / _GUARD.name)
    assert ok.returncode == 0, ok.stderr


def test_other_command_running_tools_go_through_the_guard():
    """Monitor and the Terminal tool run shell commands too; without the guard they bypassed -jobs
    and the one-build gate."""
    settings = json.loads((_HOOKS.parent / "settings.json").read_text(encoding="utf-8"))
    guarded = [e["matcher"] for e in settings["hooks"]["PreToolUse"]
               if any(_GUARD.name in h["command"] for h in e["hooks"])]
    for tool in ("Bash", "Monitor", "mcp__terminal__run_in_terminal"):
        assert any(tool in m.split("|") for m in guarded), f"{tool} is not routed through the guard"


# ── the LaunchAgent poller and the Simulator hook, with a fake watchdog ───────────────────────

def _copy_with_fake_watchdog(script: Path, tmp_path: Path) -> tuple[Path, Path]:
    hooks = tmp_path / "hooks"
    hooks.mkdir(exist_ok=True)
    shutil.copy(script, hooks / script.name)
    marker = tmp_path / "started"
    (hooks / "swift-build-memory-watchdog.sh").write_text(f'#!/bin/bash\ntouch "{marker}"\n')
    return hooks / script.name, marker


def _wait_for(path: Path, seconds: float = 2.0) -> bool:
    for _ in range(int(seconds / 0.05)):
        if path.exists():
            return True
        subprocess.run(["sleep", "0.05"])
    return path.exists()


@pytest.mark.parametrize("table,expected", [
    ("", False),                                                          # nothing running
    ("100 1 SKAgent\n101 100 swift-frontend\n", False),                   # the editor type-checking
    ("100 1 Xcode\n101 100 SourceKitService\n102 101 swift-frontend\n", False),
    ("100 1 SWBBuildService\n101 100 swift-frontend\n", True),            # a ⌘B build
    ("100 1 zsh\n101 100 xcodebuild\n", True),                            # a terminal build
])
def test_the_poller_starts_the_watchdog_only_for_a_build(tmp_path, table, expected):
    poller, marker = _copy_with_fake_watchdog(_POLLER, tmp_path)
    env = dict(_shims(tmp_path, ps_table=table), CAYDEX_SWIFT_WATCHDOG_LOCK_DIR=str(tmp_path / "lock"))
    r = subprocess.run(["/bin/bash", str(poller)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert _wait_for(marker, 1.0 if expected else 0.5) == expected, f"table={table!r}"


@pytest.mark.parametrize("payload,expected", [
    ('{"tool_input": {"action": "build", "scheme": "ios"}}', True),
    ('{"tool_input": {"action": "build_status", "build_id": "x"}}', False),   # a poll, not a build
    ("not json", True),                                                       # unreadable → assume a build
])
def test_the_simulator_hook_starts_the_watchdog_only_for_a_build(tmp_path, payload, expected):
    hook, marker = _copy_with_fake_watchdog(_SIM_HOOK, tmp_path)
    r = subprocess.run(["/bin/bash", str(hook)], input=payload, capture_output=True, text=True,
                       env=_shims(tmp_path), timeout=30)
    assert r.returncode == 0 and r.stderr == "", r.stderr
    assert _wait_for(marker, 1.0 if expected else 0.5) == expected
