"""Synced settings that REVERTED — every write path to a synced key must also push.

TestFlight 1.0(9): "I changed Extended hours to off but it turns on again. For everything in
here, it should ... set up once and permanently keep them." The chart sheet was fixed on its
own; this file pins the same class of defect on the SYNCED preferences
(`SettingsSyncManager.boolKeys` / `stringKeys` / `doubleKeys`).

The mechanism is one sentence: every cold launch HYDRATES, and the hydrate overwrites any synced
key the server does not already hold — so a synced key written locally WITHOUT a `push()` (which
is what marks it dirty, durably, before its PUT) is reverted on the next launch, silently.

  1. Learn audio speed picked in the PLAYER (`PlaybackSpeedSheet`, the mini player's cycle
     button) was written to "playback_speed" by a `$playbackSpeed` sink and never pushed. The
     same sink fired on subscribe and after every hydrate / session-end reset, so it also wrote
     the DEFAULT 1.0 into a key nobody had chosen. Now: one user-path setter persists + pushes;
     the sink only applies the rate; the store-to-live direction never writes.
  2. `AppSettingsView` (Default Analyst, Default Speed, Autoplay Next, Haptic Feedback) pushed
     only in `.onDisappear`, so a change made and then backgrounded/killed reverted. Now every
     synced `@AppStorage` row pushes in its own `.onChange`.
  3. `NotificationsSettingsView` loaded once and ignored `.caydexSettingsHydrated`, and the
     quiet-hours setters wrote BOTH ends from that stale copy — overwriting the account's other
     end. Now the screen reloads on hydrate and each setter writes only its own key.
  4. Settings → "Default Speed — For book & lesson narration" never reached Journey narration:
     `AIVoiceManager.playClip` used a bare `play()`. Now it sets `defaultRate` from the stored
     speed first (start and resume both honour it).
  5. The Notifications ViewModel wrote each change to UserDefaults at once but pushed only
     after a 500ms debounce, and a change is recorded as UNSAVED only inside `push()`
     (`pendingKeys`, durable, before any request; `deferLocalChange()` while un-hydrated). A
     launch hydrate landing inside those 500ms overwrote the change (the row flipped back), and
     a kill inside them lost it to the next launch's hydrate. Now every setter ends in a
     synchronous push, after its write: free while un-hydrated (a deferral sends nothing), one
     cancel-and-replace PUT per change once hydrated — the same policy as AppSettingsView.

Deliberately NOT done: a push in `iosApp`'s `.background` branch. `push()` PUTs the whole blob
whenever the session is authenticated + hydrated (it is not a no-op when nothing differs), so a
background push would be one request per backgrounding; the per-change pushes already mark keys
dirty durably before any request, which is what survives a kill.

Guard discipline (.claude/rules/testing.md §3): comments are stripped before every assertion,
every check is bounded to the brace-balanced declaration it means, nothing is bounded by the token
it asserts, and `test_the_guards_are_not_vacuous` mutates the sources IN MEMORY ONLY (a patched
`Path.read_text`; the Swift files on disk are never touched) and confirms each guard goes red
WITH ITS INTENDED MESSAGE (`match=`), so a mutation that merely breaks a lookup ("anchor not
found", "unbalanced block") does not count as caught.

Hand mutations run (each one KILLED — the named guard failed with its intended message):
  M1  PlaybackSpeedSheet: `setPlaybackSpeedFromUser(speed)` -> `playbackSpeed = speed`
  M2  GlobalMiniPlayer: `setPlaybackSpeedFromUser(speeds[nextIndex])` -> direct assignment
  M3  GlobalMiniPlayer: the setter call left only in a `//` comment, direct assignment below it
  M4  AudioManager: `@Published private(set) var playbackSpeed` -> `@Published var playbackSpeed`
  M5  AudioManager: the old `UserDefaults...set(..."playback_speed")` re-added to the sink
  M6  AudioManager: `SettingsSyncManager.shared.push()` dropped from the user-path setter
  M7  AudioManager: `adoptStoredPlaybackSpeed()` writes the key back (fallback write-back)
  M8  AudioManager: the hydrate observer assigns `self.playbackSpeed` instead of adopting
  M9  AppSettingsView: `.onChange(of: hapticFeedback)` closure emptied
  M10 AppSettingsView: `.onChange(of: autoplayNext)` deleted
  M11 AppSettingsView: Default Speed row assigns `AudioManager.shared.playbackSpeed` again
  M11b AppSettingsView: the same assignment ADDED beside the adopt call (direct-write check)
  M12 NotificationsSettingsView: the observer listens to the wrong notification
  M13 NotificationsSettingsView: the hydrate closure refreshes permission instead of load()
  M14 NotificationSettingsViewModel: setQuietStart also writes the END
  M15 NotificationSettingsViewModel: setQuietEnd calls the old both-ends `writeQuietTimes()`
  M16 NotificationSettingsViewModel: setQuietHoursEnabled writes the end unconditionally
  M17 AIVoiceManager: `newPlayer.defaultRate = ...` removed
  M18 AIVoiceManager: `defaultRate` set AFTER `play()`
  M19 AIVoiceManager: `narrationRate()` reads a different key
  M20 AudioManager: `SettingsSyncManager.shared.push()` added to the `$playbackSpeed` sink
  M21 AudioManager: the same push added to the `.caydexSettingsHydrated` observer
  M22 AudioManager: the same push added to `updatePlaybackSpeed` (the sink pushes indirectly)
  M23 AudioManager: the sink writes `set(Double(speed.rawValue), forKey: "playback_speed")`
      (a `)` inside the value slipped past the old `[^)]*` write pattern)
  M24 AudioManager: the sink writes via `setValue(..., forKey: "playback_speed")`
  M25 AudioManager: `adoptStoredPlaybackSpeed()` writes back via `setValue`
  M26 AudioManager: the property initializer `register(defaults:)`s the key
  M27 NotificationSettingsViewModel: `== nil || true` on the "end never stored" check
  M28 NotificationsSettingsView: the hydrate handler also pushes
  M29 NotificationSettingsViewModel: setToggle's push wrapped back in a 500ms sleep
  M30 NotificationSettingsViewModel: `pushNow()` pushes from a `Task` (next turn, not now)
  M31 NotificationSettingsViewModel: setQuietEnd pushes BEFORE its write
  M32 NotificationSettingsViewModel: setQuietStart's push dropped
  M33 NotificationSettingsViewModel: setQuietHoursEnabled pushes via `asyncAfter`
  M34 SettingsSyncManager: the un-hydrated branch of `push()` no longer marks the change
"""

from __future__ import annotations

import pathlib
import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_AUDIO = _IOS / "Services" / "AudioManager.swift"
_FULL_PLAYER = _IOS / "Views" / "Screens" / "FullScreenAudioPlayer.swift"
_MINI_PLAYER = _IOS / "Views" / "Molecules" / "GlobalMiniPlayer.swift"
_APP_SETTINGS = _IOS / "Views" / "Screens" / "AppSettingsView.swift"
_VOICE = _IOS / "Services" / "AIVoiceManager.swift"
_NOTIF_VIEW = _IOS / "Views" / "Screens" / "NotificationsSettingsView.swift"
_NOTIF_VM = _IOS / "ViewModels" / "NotificationSettingsViewModel.swift"
_SYNC = _IOS / "Core" / "Services" / "SettingsSyncManager.swift"

pytestmark = pytest.mark.skipif(not _IOS.is_dir(), reason="iOS tree not present")


# ── helpers ──────────────────────────────────────────────────────────────────

def _strip_comments(src: str) -> str:
    """`src` with `/* */` and `//` comments removed, string literals kept.

    Load-bearing: the comments next to each fix QUOTE the broken code ("used to write
    playback_speed", "a bare play()"), so an unstripped scan passes on prose after a revert.
    A `//` counts as a comment only when an EVEN number of quotes precedes it on its line, so
    `"https://..."` inside a literal survives.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    out = []
    for line in src.splitlines():
        cut = len(line)
        for m in re.finditer(r"//", line):
            if line[: m.start()].count('"') % 2 == 0:
                cut = m.start()
                break
        out.append(line[:cut])
    return "\n".join(out)


def _code(path: Path) -> str:
    assert path.exists(), f"missing {path} — a guard over a missing file proves nothing"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _block_after(src: str, anchor: str, open_ch: str = "{", close_ch: str = "}") -> str:
    """The brace-balanced block that starts at the first `open_ch` AFTER `anchor`.

    Bounded by structure, never by the token being asserted — deleting that token cannot
    grow the window into a neighbouring declaration. Searched from the anchor's END, so a
    `[String]` type annotation inside the anchor is not mistaken for the literal's bracket.
    """
    assert anchor in src, f"anchor {anchor!r} not found"
    start = src.index(open_ch, src.index(anchor) + len(anchor))
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_ch:
            depth += 1
        elif src[i] == close_ch:
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced block after {anchor!r}")


def _spans(src: str, anchor: str) -> tuple[int, int]:
    """(start, end) offsets of `_block_after(src, anchor)` inside `src`."""
    block = _block_after(src, anchor)
    start = src.index(block, src.index(anchor))
    return start, start + len(block)


_DIRECT_SPEED_WRITE = re.compile(r"\bplaybackSpeed\s*=(?!=)")
_SPEED_KEY_WRITE = re.compile(r"\.set\([^)]*forKey:\s*\"playback_speed\"\s*\)")
_SPEED_KEY = '"playback_speed"'
# Any UserDefaults mutator (`set`, `setValue`, `setObject`, `removeObject`, `register`) — and,
# through `set\w*`, a call into `setPlaybackSpeedFromUser` from a path that is not a person.
_STORE_WRITE = re.compile(r"\.(?:set\w*|removeObject|register)\s*\(")
_SPEED_DECL = "@Published private(set) var playbackSpeed: PlaybackSpeed ="


def _initializer_span(src: str) -> tuple[int, int]:
    """(start, end) of the `playbackSpeed` declaration plus its initializer expression.

    Bounded by layout, not by the key: the declaration line ends in `=`, so the initializer is
    exactly the NEXT line. A reformat that breaks that shape fails here, loudly, instead of
    letting the window grow over a neighbouring declaration.
    """
    assert _SPEED_DECL in src, "the playbackSpeed declaration drifted — the scan below is blind"
    start = src.index(_SPEED_DECL)
    decl_end = src.index("\n", start)
    assert src[start:decl_end].rstrip().endswith("="), (
        "the playbackSpeed initializer is no longer on its own line — re-anchor this scan"
    )
    return start, src.index("\n", decl_end + 1)


# ── 1. player speed controls take the user path ──────────────────────────────

def test_player_controls_route_through_the_user_setter():
    sheet = _block_after(_code(_FULL_PLAYER), "struct PlaybackSpeedSheet: View")
    assert "audioManager.setPlaybackSpeedFromUser(speed)" in sheet, (
        "the full-screen speed sheet no longer calls the user-path setter, so the pick is "
        "never pushed and the next launch's hydrate reverts it"
    )
    assert not _DIRECT_SPEED_WRITE.search(sheet), (
        "the full-screen speed sheet assigns playbackSpeed directly again"
    )

    cycle = _block_after(_code(_MINI_PLAYER), "private func cyclePlaybackSpeed()")
    assert "audioManager.setPlaybackSpeedFromUser(speeds[nextIndex])" in cycle, (
        "the mini player's speed button no longer calls the user-path setter"
    )
    assert not _DIRECT_SPEED_WRITE.search(cycle), (
        "the mini player assigns playbackSpeed directly again"
    )


def test_playback_speed_is_not_externally_writable():
    """`private(set)` is what makes the two methods the only writers; the compiler then
    enforces the rule. Backed by a tree-wide scan so a lost `private(set)` is caught here too."""
    audio = _code(_AUDIO)
    assert "@Published private(set) var playbackSpeed: PlaybackSpeed" in audio, (
        "playbackSpeed is externally writable again — a view can bypass "
        "setPlaybackSpeedFromUser and the choice is never pushed"
    )
    offenders = []
    for path in sorted(_IOS.rglob("*.swift")):
        if path == _AUDIO:
            continue
        if re.search(r"\.playbackSpeed\s*=(?!=)", _strip_comments(path.read_text(encoding="utf-8"))):
            offenders.append(str(path.relative_to(_IOS)))
    assert not offenders, f"direct playbackSpeed assignment outside AudioManager: {offenders}"


def test_the_user_setter_persists_and_pushes():
    setter = _block_after(_code(_AUDIO), "func setPlaybackSpeedFromUser(_ speed: PlaybackSpeed)")
    assert "playbackSpeed = speed" in setter
    assert _SPEED_KEY_WRITE.search(setter), "the user-path setter no longer persists the key"
    assert "SettingsSyncManager.shared.push()" in setter, (
        "the user-path setter writes a SYNCED key without push(): the next cold launch's "
        "hydrate overwrites it with the server's older value"
    )


def test_only_the_user_setter_writes_the_speed_key_or_the_live_value():
    """The sink, the hydrate observer and the session-end reset must neither write the key nor
    push — otherwise "never chose" becomes "chose 1x" and replays into the next account."""
    audio = _code(_AUDIO)
    setter = _spans(audio, "func setPlaybackSpeedFromUser(_ speed: PlaybackSpeed)")
    adopt = _spans(audio, "func adoptStoredPlaybackSpeed()")

    initializer = _initializer_span(audio)

    key_writes = [m.start() for m in _SPEED_KEY_WRITE.finditer(audio)]
    assert key_writes, "no write of playback_speed found at all — the scan drifted"
    assert all(setter[0] <= at < setter[1] for at in key_writes), (
        "playback_speed is written outside setPlaybackSpeedFromUser (the old $playbackSpeed "
        "sink write, or a fallback written back by the store-to-live path)"
    )

    # The pattern above is a positive check, not a fence: it stops at the first `)` and knows
    # only `.set(`, so `set(Double(x), forKey:)` and `setValue(` walk straight past it. The
    # fence is WHERE the key may appear at all: its three declared places, and in the two
    # read-only ones nothing may write.
    key_uses = [m.start() for m in re.finditer(re.escape(_SPEED_KEY), audio)]
    assert key_uses, "playback_speed is not referenced at all — the scan drifted"
    allowed = (setter, adopt, initializer)
    stray = [audio.count("\n", 0, at) + 1 for at in key_uses
             if not any(lo <= at < hi for lo, hi in allowed)]
    assert not stray, (
        f'"playback_speed" appears outside its three declared places (the initializer, '
        f"setPlaybackSpeedFromUser, adoptStoredPlaybackSpeed) at stripped line(s) {stray} — "
        f"a sink or observer writing the synced key without push() is the revert this fixes"
    )

    live_writes = [m.start() for m in _DIRECT_SPEED_WRITE.finditer(audio)]
    assert len(live_writes) >= 2, "the setter / adopt assignments are gone — the scan drifted"
    assert all(setter[0] <= at < setter[1] or adopt[0] <= at < adopt[1] for at in live_writes), (
        "playbackSpeed is assigned outside its two declared writers"
    )

    adopt_body = audio[adopt[0]: adopt[1]]
    assert _SPEED_KEY in adopt_body, "adoptStoredPlaybackSpeed no longer reads the store"
    assert not _STORE_WRITE.search(adopt_body) and "push(" not in adopt_body, (
        "the store-to-live path writes or pushes — a fallback would be recorded as a choice"
    )
    init_text = audio[initializer[0]: initializer[1]]
    assert _SPEED_KEY in init_text, "the initializer no longer reads the store — scan drifted"
    assert not _STORE_WRITE.search(init_text), (
        "the playbackSpeed initializer writes the store — every launch would record a speed "
        "nobody chose"
    )

    observers = _block_after(audio, "private func setupObservers()")
    assert "$playbackSpeed" in observers and "updatePlaybackSpeed(speed)" in observers, (
        "the rate-applying sink is gone — the scan below would pass on nothing"
    )
    assert ("push(" not in observers and "SettingsSyncManager" not in observers
            and "setPlaybackSpeedFromUser" not in observers), (
        "a speed observer pushes or takes the user path — the $playbackSpeed sink fires on "
        "subscribe (before any hydrate) and the hydrate observer after every hydrate / session "
        "end, so a push there marks keys nobody changed as unsaved"
    )
    # Indirect routes too (a push added to `updatePlaybackSpeed`, which the sink calls): in
    # this file the only way into the sync machine is the one user-path setter.
    sync_refs = [m.start() for m in re.finditer(r"SettingsSyncManager|\bpush\(", audio)]
    assert sync_refs and all(setter[0] <= at < setter[1] for at in sync_refs), (
        "AudioManager reaches SettingsSyncManager outside setPlaybackSpeedFromUser — only a "
        "person choosing a speed may push"
    )
    hydrated = _block_after(observers, "publisher(for: .caydexSettingsHydrated)")
    assert "adoptStoredPlaybackSpeed()" in hydrated, (
        "the hydrate observer no longer routes through the read-only adopt path"
    )


# ── 2. AppSettingsView pushes on each synced row's change ────────────────────

def _synced_key_lists() -> str:
    sync = _code(_SYNC)
    return "".join(
        _block_after(sync, f"static let {name}: [String] =", "[", "]")
        for name in ("boolKeys", "stringKeys", "doubleKeys")
    )


def test_app_settings_pushes_on_every_synced_row_change():
    settings = _code(_APP_SETTINGS)
    synced = _synced_key_lists()
    rows = re.findall(r"@AppStorage\(([^)]+)\)\s*private var (\w+)", settings)
    synced_rows = [(key.strip(), name) for key, name in rows if key.strip() in synced]
    # Anti-vacuity: all four synced rows must be found, or the loop below checks nothing.
    assert {name for _, name in synced_rows} >= {
        "defaultPersona", "playbackSpeedRaw", "autoplayNext", "hapticFeedback",
    }, f"the synced @AppStorage rows drifted: {synced_rows}"

    for key, name in synced_rows:
        anchor = f".onChange(of: {name})"
        assert anchor in settings, (
            f"{name} ({key}) has no .onChange — a change made before the screen closes is "
            f"reverted by the next launch's hydrate"
        )
        closure = _block_after(settings, anchor)
        assert "SettingsSyncManager.shared.push()" in closure, (
            f".onChange(of: {name}) does not push — the synced key {key} stays local-only"
        )

    assert "SettingsSyncManager.shared.push()" in _block_after(settings, ".onDisappear"), (
        "the onDisappear backstop push is gone"
    )


def test_the_default_speed_row_adopts_instead_of_assigning():
    closure = _block_after(_code(_APP_SETTINGS), ".onChange(of: playbackSpeedRaw)")
    assert "AudioManager.shared.adoptStoredPlaybackSpeed()" in closure, (
        "the Default Speed row no longer brings the live player in line with the stored speed"
    )
    assert not _DIRECT_SPEED_WRITE.search(closure), (
        "the Default Speed row assigns the live speed (with a coerced fallback) again"
    )


# ── 3. notifications screen: reload on hydrate, quiet setters own one key ────

def test_notifications_screen_reloads_on_hydrate():
    body = _block_after(_code(_NOTIF_VIEW), "var body: some View")
    anchor = ".onReceive(NotificationCenter.default.publisher(for: .caydexSettingsHydrated))"
    assert anchor in body, (
        "the Notifications screen ignores .caydexSettingsHydrated — a hydrate landing while it "
        "is open leaves stale rows that the next tap writes back and pushes"
    )
    closure = _block_after(body, anchor)
    assert "viewModel.load()" in closure, "the hydrate observer does not re-read the store"
    assert "push" not in closure.lower() and "SettingsSyncManager" not in closure, (
        "the hydrate observer pushes — a hydrate is the SERVER's state arriving (or a session "
        "ending); sending it straight back is a PUT per hydrate that records no user change"
    )


_QUIET_KEYS = ('"notify_quiet_start"', '"notify_quiet_end"')
_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")


def _calls(body: str) -> set[str]:
    return set(_CALL.findall(body))


def test_quiet_time_setters_write_only_their_own_key():
    vm = _code(_NOTIF_VM)
    assert "writeQuietTimes" not in vm, "the both-ends writer is back"

    for fn, own, other in (
        ("func setQuietStart(_ date: Date)", '"notify_quiet_start"', '"notify_quiet_end"'),
        ("func setQuietEnd(_ date: Date)", '"notify_quiet_end"', '"notify_quiet_start"'),
    ):
        body = _block_after(vm, fn)
        assert f"writeQuietTime(date, forKey: {own})" in body, f"{fn} no longer writes {own}"
        assert other not in body, f"{fn} also writes {other} from the stale copy"
        assert _calls(body) <= {"writeQuietTime", "pushNow"}, (
            f"{fn} calls something that may write the other end: {sorted(_calls(body))}"
        )

    helper = _block_after(vm, "private func writeQuietTime(_ date: Date, forKey key: String)")
    assert helper.count(".set(") == 1 and "forKey: key)" in helper, (
        "writeQuietTime must write exactly the one key it is given"
    )
    assert not any(k in helper for k in _QUIET_KEYS), "writeQuietTime hard-codes a quiet key"


def test_enabling_quiet_hours_writes_only_absent_ends():
    body = _block_after(_code(_NOTIF_VM), "func setQuietHoursEnabled(_ value: Bool)")
    remainder = body
    for key, field in (('"notify_quiet_start"', "quietStart"), ('"notify_quiet_end"', "quietEnd")):
        guard = f"if defaults.object(forKey: {key}) == nil"
        assert guard in body, f"setQuietHoursEnabled no longer checks {key} is absent first"
        assert re.search(re.escape(guard) + r"\s*\{", body), (
            f"the absent-check on {key} is not the whole condition — an extra clause "
            f"(`|| …`) can make the write unconditional again"
        )
        block = _block_after(body, guard)
        assert f"writeQuietTime({field}, forKey: {key})" in block
        remainder = remainder.replace(body[body.index(guard): body.index(block) + len(block)], "")
    assert "writeQuietTime(" not in remainder and not any(k in remainder for k in _QUIET_KEYS), (
        "setQuietHoursEnabled writes a quiet-hours end unconditionally — a stored end set on "
        "another device is overwritten from this screen's copy"
    )


# Anything that runs the push LATER than the write: a Task hop, a sleep, a timer, a delay.
_DEFERRAL = re.compile(
    r"\bTask\b|\bawait\b|\basyncAfter\b|\bsleep\b|\bTimer\b|\bdebounce\b|\bperform\w*\("
)
_NOTIF_SETTERS = (
    "func setToggle(_ key: String, _ value: Bool)",
    "func setQuietHoursEnabled(_ value: Bool)",
    "func setQuietStart(_ date: Date)",
    "func setQuietEnd(_ date: Date)",
)


def test_every_notification_change_is_marked_unsaved_in_the_same_step():
    """No window between "written to UserDefaults" and "recorded as unsaved".

    A change becomes unsaved only inside `SettingsSyncManager.push()`. While it sat behind a
    500ms debounce, a launch hydrate landing in that window overwrote it (the row flipped back)
    and a kill in it lost it. So each setter's LAST statement is a direct `pushNow()` — after
    every write, with nothing deferring it — and `pushNow()` calls `push()` synchronously.
    """
    vm = _code(_NOTIF_VM)
    push_now = _block_after(vm, "func pushNow()")
    assert "SettingsSyncManager.shared.push()" in push_now, "pushNow() no longer pushes"
    assert not _DEFERRAL.search(push_now), (
        "pushNow() does not push synchronously — the change is unrecorded until it runs"
    )

    for fn in _NOTIF_SETTERS:
        body = _block_after(vm, fn)
        assert not _DEFERRAL.search(body), (
            f"{fn} defers its push — until it runs the change is in UserDefaults but not "
            f"recorded as unsaved, so a hydrate landing in the gap reverts it"
        )
        assert body[1:-1].strip().endswith("pushNow()"), (
            f"{fn} does not end with pushNow() — a write after the push (or no push) leaves "
            f"that write unrecorded"
        )


def test_push_marks_an_unhydrated_change_before_returning():
    """The premise of the test above, on the path the race actually takes.

    The gap only opens while the session is un-hydrated (a hydrate is in flight), and there
    `push()` sends nothing: it must record the change through `deferLocalChange()` before it
    returns, so the hydrate's snapshot of `pendingKeys` re-asserts it over the server's blob.
    The hydrated send path's marking is pinned by `test_ios_settings_sync_ordering.py`.
    """
    sync = _code(_SYNC)
    push = _block_after(sync, "func push()")
    gate = _block_after(push, "guard hasHydrated else")
    assert "deferLocalChange()" in gate and "hydrate()" in gate, (
        "push()'s un-hydrated branch no longer records the change as unsaved before it returns"
    )
    assert gate.index("deferLocalChange()") < gate.index("hydrate()"), (
        "push()'s un-hydrated branch records the change only after kicking the hydrate"
    )
    assert "deferLocalChange()" in _block_after(push, "if !isAuthenticated"), (
        "push() no longer records a .restoring session's change"
    )
    marker = _block_after(sync, "private func deferLocalChange()")
    assert "pendingKeys.formUnion(changedKeysVsServer())" in marker, (
        "deferLocalChange() no longer adds the changed keys to the durable pendingKeys"
    )


# ── 4. Journey narration honours Default Speed ───────────────────────────────

def test_play_clip_sets_default_rate_from_the_stored_speed():
    voice = _code(_VOICE)
    body = _block_after(voice, "func playClip(named name: String")
    m = re.search(r"newPlayer\.defaultRate\s*=\s*Self\.(\w+)\(\)", body)
    assert m, "playClip no longer sets defaultRate — Journey ignores Settings → Default Speed"
    assert "newPlayer.play()" in body
    assert m.start() < body.index("newPlayer.play()"), (
        "defaultRate is set after play(), so the clip starts at 1x"
    )

    rate = _block_after(voice, f"func {m.group(1)}()")
    assert 'UserDefaults.standard.object(forKey: "playback_speed")' in rate, (
        "the narration rate is not read from the synced playback_speed key"
    )
    assert "PlaybackSpeed(rawValue:" in rate and "?? .normal" in rate, (
        "the stored speed is not clamped to a known PlaybackSpeed with a 1x fallback"
    )
    assert ".set(" not in rate, "the narration rate path writes the preference back"

    resume = _block_after(voice, "func resume()")
    assert "player.play()" in resume and ".rate =" not in resume, (
        "resume() must restart the same player at its defaultRate"
    )
    speak = _block_after(voice, "func speak(_ text: String")
    assert "AVSpeechUtteranceDefaultSpeechRate * 0.9" in speak, (
        "the on-device speech fallback rate changed — it was meant to stay as it was"
    )


# ── anti-vacuity: in-memory mutations ────────────────────────────────────────

_MUTATIONS = [
    # (label, file, old, new, guard, the message the guard must fail WITH)
    ("M1", _FULL_PLAYER, "audioManager.setPlaybackSpeedFromUser(speed)",
     "audioManager.playbackSpeed = speed", test_player_controls_route_through_the_user_setter,
     "the full-screen speed sheet no longer calls the user-path setter"),
    ("M2", _MINI_PLAYER, "audioManager.setPlaybackSpeedFromUser(speeds[nextIndex])",
     "audioManager.playbackSpeed = speeds[nextIndex]",
     test_player_controls_route_through_the_user_setter,
     "the mini player's speed button no longer calls the user-path setter"),
    ("M3", _MINI_PLAYER, "audioManager.setPlaybackSpeedFromUser(speeds[nextIndex])",
     "// audioManager.setPlaybackSpeedFromUser(speeds[nextIndex])\n"
     "            audioManager.playbackSpeed = speeds[nextIndex]",
     test_player_controls_route_through_the_user_setter,
     "the mini player's speed button no longer calls the user-path setter"),
    ("M4", _AUDIO, "@Published private(set) var playbackSpeed",
     "@Published var playbackSpeed", test_playback_speed_is_not_externally_writable,
     "playbackSpeed is externally writable again"),
    ("M5", _AUDIO, "self?.updatePlaybackSpeed(speed)",
     'self?.updatePlaybackSpeed(speed)\n'
     '                UserDefaults.standard.set(speed.rawValue, forKey: "playback_speed")',
     test_only_the_user_setter_writes_the_speed_key_or_the_live_value,
     "playback_speed is written outside setPlaybackSpeedFromUser"),
    ("M6", _AUDIO, "SettingsSyncManager.shared.push()", "",
     test_the_user_setter_persists_and_pushes,
     "the user-path setter writes a SYNCED key without push()"),
    ("M7", _AUDIO, "        guard resolved != playbackSpeed else { return }",
     '        UserDefaults.standard.set(resolved.rawValue, forKey: "playback_speed")\n'
     "        guard resolved != playbackSpeed else { return }",
     test_only_the_user_setter_writes_the_speed_key_or_the_live_value,
     "playback_speed is written outside setPlaybackSpeedFromUser"),
    ("M8", _AUDIO, ".sink { [weak self] _ in self?.adoptStoredPlaybackSpeed() }",
     ".sink { [weak self] _ in self?.playbackSpeed = .normal }",
     test_only_the_user_setter_writes_the_speed_key_or_the_live_value,
     "playbackSpeed is assigned outside its two declared writers"),
    ("M9", _APP_SETTINGS, ".onChange(of: hapticFeedback) { _, _ in SettingsSyncManager.shared.push() }",
     ".onChange(of: hapticFeedback) { _, _ in }",
     test_app_settings_pushes_on_every_synced_row_change,
     ".onChange(of: hapticFeedback) does not push"),
    ("M10", _APP_SETTINGS, ".onChange(of: autoplayNext) { _, _ in SettingsSyncManager.shared.push() }",
     "", test_app_settings_pushes_on_every_synced_row_change,
     'autoplayNext ("autoplay_next") has no .onChange'),
    ("M11", _APP_SETTINGS, "AudioManager.shared.adoptStoredPlaybackSpeed()",
     "AudioManager.shared.playbackSpeed = PlaybackSpeed(rawValue: playbackSpeedRaw) ?? .normal",
     test_the_default_speed_row_adopts_instead_of_assigning,
     "the Default Speed row no longer brings the live player in line"),
    ("M11b", _APP_SETTINGS, "AudioManager.shared.adoptStoredPlaybackSpeed()",
     "AudioManager.shared.adoptStoredPlaybackSpeed()\n"
     "            AudioManager.shared.playbackSpeed = PlaybackSpeed(rawValue: playbackSpeedRaw) ?? .normal",
     test_the_default_speed_row_adopts_instead_of_assigning,
     "the Default Speed row assigns the live speed"),
    ("M12", _NOTIF_VIEW, "publisher(for: .caydexSettingsHydrated)",
     "publisher(for: .caydexDefaultPersonaChanged)",
     test_notifications_screen_reloads_on_hydrate,
     "the Notifications screen ignores .caydexSettingsHydrated"),
    ("M13", _NOTIF_VIEW,
     "publisher(for: .caydexSettingsHydrated)) { _ in\n            viewModel.load()",
     "publisher(for: .caydexSettingsHydrated)) { _ in\n"
     "            Task { await viewModel.refreshPermission() }",
     test_notifications_screen_reloads_on_hydrate,
     "the hydrate observer does not re-read the store"),
    ("M14", _NOTIF_VM, 'writeQuietTime(date, forKey: "notify_quiet_start")',
     'writeQuietTime(date, forKey: "notify_quiet_start")\n'
     '        writeQuietTime(quietEnd, forKey: "notify_quiet_end")',
     test_quiet_time_setters_write_only_their_own_key,
     'func setQuietStart(_ date: Date) also writes "notify_quiet_end" from the stale copy'),
    ("M15", _NOTIF_VM, 'writeQuietTime(date, forKey: "notify_quiet_end")',
     "writeQuietTimes()", test_quiet_time_setters_write_only_their_own_key,
     "the both-ends writer is back"),
    ("M16", _NOTIF_VM,
     "        SettingsSyncManager.shared.refreshDeviceTimezone()\n        pushNow()",
     '        writeQuietTime(quietEnd, forKey: "notify_quiet_end")\n'
     "        SettingsSyncManager.shared.refreshDeviceTimezone()\n        pushNow()",
     test_enabling_quiet_hours_writes_only_absent_ends,
     "setQuietHoursEnabled writes a quiet-hours end unconditionally"),
    ("M17", _VOICE, "newPlayer.defaultRate = Self.narrationRate()", "",
     test_play_clip_sets_default_rate_from_the_stored_speed,
     "playClip no longer sets defaultRate"),
    ("M18", _VOICE, "newPlayer.defaultRate = Self.narrationRate()\n        newPlayer.play()",
     "newPlayer.play()\n        newPlayer.defaultRate = Self.narrationRate()",
     test_play_clip_sets_default_rate_from_the_stored_speed,
     "defaultRate is set after play()"),
    ("M19", _VOICE, 'let stored = UserDefaults.standard.object(forKey: "playback_speed") as? Double\n'
     "        let speed = stored.flatMap",
     'let stored = UserDefaults.standard.object(forKey: "narration_speed") as? Double\n'
     "        let speed = stored.flatMap",
     test_play_clip_sets_default_rate_from_the_stored_speed,
     "the narration rate is not read from the synced playback_speed key"),
    # ── review round: observers must not push; the key's write locations; the timing gap ──
    ("M20", _AUDIO, "self?.updatePlaybackSpeed(speed)",
     "self?.updatePlaybackSpeed(speed)\n                SettingsSyncManager.shared.push()",
     test_only_the_user_setter_writes_the_speed_key_or_the_live_value,
     "a speed observer pushes or takes the user path"),
    ("M21", _AUDIO, ".sink { [weak self] _ in self?.adoptStoredPlaybackSpeed() }",
     ".sink { [weak self] _ in self?.adoptStoredPlaybackSpeed(); SettingsSyncManager.shared.push() }",
     test_only_the_user_setter_writes_the_speed_key_or_the_live_value,
     "a speed observer pushes or takes the user path"),
    ("M22", _AUDIO, "    private func updatePlaybackSpeed(_ speed: PlaybackSpeed) {\n",
     "    private func updatePlaybackSpeed(_ speed: PlaybackSpeed) {\n"
     "        SettingsSyncManager.shared.push()\n",
     test_only_the_user_setter_writes_the_speed_key_or_the_live_value,
     "AudioManager reaches SettingsSyncManager outside setPlaybackSpeedFromUser"),
    ("M23", _AUDIO, "self?.updatePlaybackSpeed(speed)",
     'self?.updatePlaybackSpeed(speed)\n'
     '                UserDefaults.standard.set(Double(speed.rawValue), forKey: "playback_speed")',
     test_only_the_user_setter_writes_the_speed_key_or_the_live_value,
     '"playback_speed" appears outside its three declared places'),
    ("M24", _AUDIO, "self?.updatePlaybackSpeed(speed)",
     'self?.updatePlaybackSpeed(speed)\n'
     '                UserDefaults.standard.setValue(speed.rawValue, forKey: "playback_speed")',
     test_only_the_user_setter_writes_the_speed_key_or_the_live_value,
     '"playback_speed" appears outside its three declared places'),
    ("M25", _AUDIO, "        guard resolved != playbackSpeed else { return }",
     '        UserDefaults.standard.setValue(resolved.rawValue, forKey: "playback_speed")\n'
     "        guard resolved != playbackSpeed else { return }",
     test_only_the_user_setter_writes_the_speed_key_or_the_live_value,
     "the store-to-live path writes or pushes"),
    ("M26", _AUDIO,
     'PlaybackSpeed(rawValue: UserDefaults.standard.double(forKey: "playback_speed")) ?? .normal',
     '{ UserDefaults.standard.register(defaults: ["playback_speed": 1.0]); '
     'return PlaybackSpeed(rawValue: UserDefaults.standard.double(forKey: "playback_speed")) '
     '?? .normal }()',
     test_only_the_user_setter_writes_the_speed_key_or_the_live_value,
     "the playbackSpeed initializer writes the store"),
    ("M27", _NOTIF_VM, 'if defaults.object(forKey: "notify_quiet_end") == nil {',
     'if defaults.object(forKey: "notify_quiet_end") == nil || true {',
     test_enabling_quiet_hours_writes_only_absent_ends,
     'the absent-check on "notify_quiet_end" is not the whole condition'),
    ("M28", _NOTIF_VIEW,
     "publisher(for: .caydexSettingsHydrated)) { _ in\n            viewModel.load()",
     "publisher(for: .caydexSettingsHydrated)) { _ in\n            viewModel.load()\n"
     "            viewModel.pushNow()",
     test_notifications_screen_reloads_on_hydrate,
     "the hydrate observer pushes"),
    ("M29", _NOTIF_VM, "        UserDefaults.standard.set(value, forKey: key)\n        pushNow()",
     "        UserDefaults.standard.set(value, forKey: key)\n"
     "        Task { try? await Task.sleep(nanoseconds: 500_000_000); pushNow() }",
     test_every_notification_change_is_marked_unsaved_in_the_same_step,
     "func setToggle(_ key: String, _ value: Bool) defers its push"),
    ("M30", _NOTIF_VM, "    func pushNow() {\n        SettingsSyncManager.shared.push()",
     "    func pushNow() {\n        Task { SettingsSyncManager.shared.push() }",
     test_every_notification_change_is_marked_unsaved_in_the_same_step,
     "pushNow() does not push synchronously"),
    ("M31", _NOTIF_VM, 'writeQuietTime(date, forKey: "notify_quiet_end")\n        pushNow()',
     'pushNow()\n        writeQuietTime(date, forKey: "notify_quiet_end")',
     test_every_notification_change_is_marked_unsaved_in_the_same_step,
     "func setQuietEnd(_ date: Date) does not end with pushNow()"),
    ("M32", _NOTIF_VM, 'writeQuietTime(date, forKey: "notify_quiet_start")\n        pushNow()',
     'writeQuietTime(date, forKey: "notify_quiet_start")',
     test_every_notification_change_is_marked_unsaved_in_the_same_step,
     "func setQuietStart(_ date: Date) does not end with pushNow()"),
    ("M33", _NOTIF_VM,
     "        SettingsSyncManager.shared.refreshDeviceTimezone()\n        pushNow()",
     "        SettingsSyncManager.shared.refreshDeviceTimezone()\n"
     "        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.pushNow() }",
     test_every_notification_change_is_marked_unsaved_in_the_same_step,
     "func setQuietHoursEnabled(_ value: Bool) defers its push"),
    ("M34", _SYNC,
     "            deferLocalChange()\n            #if DEBUG\n"
     "            print(\"ℹ️ [Settings] push deferred (not hydrated)",
     "            #if DEBUG\n"
     "            print(\"ℹ️ [Settings] push deferred (not hydrated)",
     test_push_marks_an_unhydrated_change_before_returning,
     "push()'s un-hydrated branch no longer records the change as unsaved"),
]


def test_the_comment_stripper_is_load_bearing():
    """The AudioManager comment quotes the old sink write; the stripper must remove it while
    keeping a `//` that sits inside a string literal."""
    assert _strip_comments('let u = "https://x" // push()') == 'let u = "https://x" '
    raw = _AUDIO.read_text(encoding="utf-8")
    assert "This sink used to write" in raw
    assert "This sink used to write" not in _strip_comments(raw)


@pytest.mark.parametrize(
    "label,path,old,new,guard,expected", _MUTATIONS, ids=[m[0] for m in _MUTATIONS]
)
def test_the_guards_are_not_vacuous(monkeypatch, label, path, old, new, guard, expected):
    original = path.read_text(encoding="utf-8")
    assert old in original, f"{label}: mutation anchor drifted — the mutation would be a no-op"
    mutated = original.replace(old, new, 1)
    assert mutated != original

    real_read_text = pathlib.Path.read_text

    def fake_read_text(self, *args, **kwargs):
        if Path(self).resolve() == path.resolve():
            return mutated
        return real_read_text(self, *args, **kwargs)

    # The guard must be GREEN on the real source first, or "it failed" proves nothing.
    guard()

    # In memory only: the file on disk is never written (other suites read it concurrently).
    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    # `match=`: the guard must fail for the reason it exists. A mutation that only breaks one
    # of this file's lookups ("anchor not found", "unbalanced block") is NOT a kill.
    with pytest.raises(AssertionError, match=re.escape(expected)):
        guard()
