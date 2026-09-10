"""Settings → AI & Research → "Default Analyst" must actually reach the Research tab.

There is no iOS test target, so these are source scans over the four Swift files that carry the
`default_persona` preference plus the backend allow-list that lets it sync.

The shipped defect (TestFlight 1.0(6)): `AnalysisPersona.settingsDefault` was read in exactly ONE
place — the stored-property initializer of `ResearchViewModel.selectedPersona`. That ViewModel is
a `@StateObject` on a view `ContentView` opacity-mounts once and never re-creates, and
`AppSettingsView` is a `fullScreenCover` above the whole tree, so the setting was read a single
time per app process. Changing it did nothing for the rest of the run, and the reporter's session
was under five minutes. Nothing was broken in the usual senses — the key name matched at every
site and the backend stored it faithfully — which is why only a *liveness* assertion catches it.

What is pinned here:

  1. KEY PARITY. `default_persona` is spelled identically at all four sites. Cheap, and it fences
     off the failure mode this bug was repeatedly mistaken for.
  2. THE SETTING HAS A LIVE CONSUMER. `settingsDefault` must be reachable from somewhere in
     `ResearchViewModel` other than the `selectedPersona` declaration. This is the assertion that
     goes red on the shipped bug.
  3. BOTH RE-SEED TRIGGERS ARE WIRED. `.caydexDefaultPersonaChanged` (a local edit) and
     `.caydexSettingsHydrated` (the server's value landing after launch, which is what made even
     a cold launch show Buffett on a fresh install).
  4. THE OVERRIDE FLAG CANNOT BE BYPASSED. `selectedPersona` is `private(set)` and `ContentView`
     holds no `$viewModel.selectedPersona` binding — the two picker surfaces write through a
     plain `@Binding`, so a direct binding would skip `selectPersona(_:)` and let a re-applied
     default silently overwrite a user's tap.
  5. `loadPersonas` DOES NOT FALL BACK TO `mapped[0]`. `GET /research/personas` has no ORDER BY,
     so that made arbitrary Postgres row order decide a user-visible default.

⚠️ Guard discipline (`project_source_scan_guard_vacuity`): comments are blanked, windows are
brace-bounded, and no window is bounded by the token it asserts. Mutation-tested — see
`test_the_guards_are_not_vacuous`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"

_RESEARCH_VM = _IOS / "ViewModels" / "ResearchViewModel.swift"
_RESEARCH_MODELS = _IOS / "Models" / "ResearchModels.swift"
_CONTENT_VIEW = _IOS / "ContentView.swift"
_APP_SETTINGS = _IOS / "Views" / "Screens" / "AppSettingsView.swift"
_SYNC_MANAGER = _IOS / "Core" / "Services" / "SettingsSyncManager.swift"
_BACKEND_SETTINGS = (
    Path(__file__).resolve().parents[1] / "app" / "services" / "user_settings_service.py"
)

_KEY = "default_persona"


def _code_only(src: str) -> str:
    """`src` with whole-line comments blanked (line numbering preserved).

    Load-bearing here: the fix is explained in comments that NAME every token these scans grep
    for — `settingsDefault`, `caydexDefaultPersonaChanged`, `mapped[0]`,
    `$viewModel.selectedPersona`. A raw-source scan would match the rationale and stay green
    with the code reverted. Whole-line only — stripping a trailing `//` would mangle URLs.
    """
    return "\n".join(
        "" if line.strip().startswith("//") else line
        for line in src.splitlines()
    )


def _balanced(src: str, opener: str, open_ch: str = "{", close_ch: str = "}") -> str:
    """The balanced `open_ch … close_ch` region introduced by `opener`.

    NOT `src[start:src.index(token, start)]`: bounding a window with the token you are asserting
    is circular — delete the token and the window grows until it finds another one elsewhere.
    """
    start = src.index(opener) + len(opener) - 1
    assert src[start] == open_ch, f"{opener!r} must end at its opening {open_ch!r}"
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_ch:
            depth += 1
        elif src[i] == close_ch:
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced {open_ch!r} after {opener!r}")


def _read(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present")
    return _code_only(path.read_text(encoding="utf-8"))


@pytest.fixture
def vm() -> str:
    return _read(_RESEARCH_VM)


def _vm_class(code: str) -> str:
    """The `ResearchViewModel` class body — NOT the whole file.

    The file also holds extensions and helper types; scoping to the class is what makes
    "`settingsDefault` is used somewhere other than the declaration" mean what it says.
    """
    return _balanced(code, "class ResearchViewModel: ObservableObject {")


# ── 1. key parity across every site that carries it ──────────────────────────

def test_the_swift_side_holds_exactly_one_copy_of_the_key():
    """Drift is impossible by construction, not by vigilance.

    Every Swift site goes through `AnalysisPersona.defaultPersonaStorageKey`, so the literal
    appears exactly once in the whole iOS tree — its declaration. A second literal is how the
    writer and the reader end up on different keys.
    """
    models = _read(_RESEARCH_MODELS)
    assert f'defaultPersonaStorageKey = "{_KEY}"' in models, "the key constant is gone"

    offenders = [
        path.relative_to(_IOS.parent).as_posix()
        for path in _IOS.rglob("*.swift")
        if f'"{_KEY}"' in _code_only(path.read_text(encoding="utf-8"))
        and path != _RESEARCH_MODELS
    ]
    assert not offenders, (
        f"raw {_KEY!r} literal outside its declaration: {offenders}. "
        "Use AnalysisPersona.defaultPersonaStorageKey so the sites cannot drift."
    )


@pytest.mark.parametrize(
    "path, label",
    [
        (_APP_SETTINGS, "AppSettingsView @AppStorage"),
        (_SYNC_MANAGER, "SettingsSyncManager stringKeys"),
    ],
)
def test_the_swift_writers_reference_the_shared_constant(path, label):
    """The flip side of the test above: "no literal" is also satisfied by not carrying the key
    at all. `AppSettingsView` writing it and `SettingsSyncManager` syncing it are both required.
    """
    assert "defaultPersonaStorageKey" in _read(path), (
        f"{label} no longer references the shared key constant"
    )


def test_the_backend_allow_lists_the_key():
    """`key_is_syncable` silently drops an unknown key name — it logs a count and moves on — so
    a backend that forgets this key makes the setting device-local with no error anywhere.
    """
    from app.services.user_settings_service import key_is_syncable

    assert key_is_syncable(_KEY) is True, "the backend would drop default_persona from the blob"
    assert f'"{_KEY}"' in _read(_BACKEND_SETTINGS)


# ── 2. the setting has a live consumer, not just a launch-time read ──────────

def test_the_default_analyst_is_re_read_after_launch(vm):
    """THE regression guard for the shipped bug.

    `settingsDefault` must be reachable from somewhere in `ResearchViewModel` other than the
    `selectedPersona` property declaration. With the bug, the declaration was its ONLY use, so
    the setting was read once per app process.
    """
    body = _vm_class(vm)
    uses = [ln for ln in body.splitlines() if "settingsDefault" in ln]
    assert uses, "ResearchViewModel no longer reads the Default Analyst setting at all"

    # Exclude BOTH the declaration and `loadPersonas`. Mutation-testing this guard showed that
    # keeping only `loadPersonas`'s read kept it green — but that read fires only when the
    # backend stops serving the selected key, which is not a re-seed path and not something a
    # user changing the setting can trigger. The claim is "re-read AFTER LAUNCH", so the window
    # has to be the re-seed path.
    reseed_window = body.replace(_balanced(body, "func loadPersonas() async {"), "")
    non_declaration = [
        ln for ln in reseed_window.splitlines()
        if "settingsDefault" in ln and "private(set) var selectedPersona" not in ln
    ]
    assert non_declaration, (
        "settingsDefault is read ONLY by the `selectedPersona` property initializer (and/or by "
        "loadPersonas, which no user action reaches). That initializer runs once per app "
        "process — the ViewModel is a @StateObject on a view ContentView never re-creates — so "
        "the setting cannot take effect until a relaunch. This is exactly the TestFlight bug: "
        "re-read it from applyDefaultPersona()."
    )


def test_apply_default_persona_respects_a_manual_pick(vm):
    """The re-seed must be gated on the override flag, or it fights the user: tap an analyst,
    and any hydrate/notification a moment later snaps it back.
    """
    body = _balanced(vm, "func applyDefaultPersona(force: Bool = false) {")
    assert "personaManuallyChosen" in body, (
        "applyDefaultPersona ignores the manual-override flag and will overwrite a user's tap"
    )
    assert "settingsDefault(in: personas)" in body, (
        "applyDefaultPersona must resolve against the FETCHED personas, not just allCases"
    )


def test_select_persona_marks_the_pick_as_manual(vm):
    body = _balanced(vm, "func selectPersona(_ persona: AnalysisPersona) {")
    assert "personaManuallyChosen = true" in body, (
        "selectPersona no longer records the pick, so applyDefaultPersona will overwrite it"
    )


def test_tab_activation_clears_the_override_and_re_seeds(vm):
    """"Pre-selected for new research" — a one-off pick lasts for the visit it was made in."""
    body = _balanced(vm, "func researchTabDidActivate() {")
    assert "personaManuallyChosen = false" in body
    assert "applyDefaultPersona()" in body


def test_the_tab_activation_hook_is_actually_called():
    """A method nothing invokes is not a fix. It must be armed from the live Research screen."""
    content = _read(_CONTENT_VIEW)
    assert "viewModel.researchTabDidActivate()" in content, (
        "ResearchViewWithBinding never calls researchTabDidActivate — the re-seed is dead code"
    )


# ── 3. both re-seed triggers are wired ───────────────────────────────────────

@pytest.mark.parametrize(
    "notification, why",
    [
        (
            ".caydexDefaultPersonaChanged",
            "a change made in Settings would not reach the Research tab until a relaunch",
        ),
        (
            ".caydexSettingsHydrated",
            "the SERVER's value lands after this ViewModel is built (hydrate runs from "
            "onAuthenticated), so a fresh install would open on Buffett even on a cold launch",
        ),
    ],
)
def test_both_re_seed_notifications_are_observed(vm, notification, why):
    body = _vm_class(vm)
    assert notification in body, f"{notification} is not observed: {why}"


def test_an_explicit_settings_change_outranks_an_earlier_manual_pick(vm):
    """The asymmetry between the two observers, and it is the difference between fixing the
    reported bug and half-fixing it.

    Settings is reached through `ProfileView`, a `.fullScreenCover` on the Research screen — and
    a cover does NOT change `\\.isActiveTab`, so `researchTabDidActivate()` never fires on the
    way back. Without `force` on the explicit change, "tap an analyst → Settings → change
    Default Analyst → return" leaves the tapped analyst in place: the reported symptom again,
    from a different cause. A hydrate must NOT force — it is background sync, it fires on every
    foreground and network restore, and it must never yank a deliberate pick.
    """
    body = _vm_class(vm)
    pairs = _balanced(body, "defaultPersonaObservers = [", open_ch="[", close_ch="]")

    changed = pairs.index("caydexDefaultPersonaChanged")
    hydrated = pairs.index("caydexSettingsHydrated")
    assert changed < hydrated, "the observer table was reordered; the checks below assume it"

    assert "true" in pairs[changed:hydrated], (
        "the explicit Default Analyst change no longer forces. Returning from Settings does not "
        "re-activate the tab, so a manual pick made earlier would keep winning."
    )
    assert "false" in pairs[hydrated:], (
        "a server hydrate now forces, so a background sync can overwrite the user's own tap"
    )


def test_the_change_notification_is_actually_posted():
    """The observer is half the wire. `AppSettingsView` must post on the picker's change —
    `.onDisappear`'s `push()` only syncs to the server, it tells this device nothing.
    """
    settings = _read(_APP_SETTINGS)
    assert "onChange(of: defaultPersona)" in settings, (
        "AppSettingsView does not react to the Default Analyst picker changing"
    )
    assert "caydexDefaultPersonaChanged" in settings, (
        "AppSettingsView never posts the change notification, so nothing observes it"
    )


def test_the_notification_name_is_declared():
    assert 'caydexDefaultPersonaChanged = Notification.Name(' in _read(_SYNC_MANAGER)


# ── 4. the override flag cannot be bypassed ──────────────────────────────────

def test_selected_persona_is_not_externally_writable(vm):
    """`private(set)` is what makes `selectPersona` the single writer. Without it a view can
    assign the property directly and skip the flag, and nothing would fail.
    """
    body = _vm_class(vm)
    assert "@Published private(set) var selectedPersona" in body, (
        "selectedPersona is externally writable again — a picker can bypass selectPersona()"
    )


def test_content_view_does_not_bind_straight_to_the_property():
    """`PersonaSelectionSection` and `PersonasSheet` write through a plain `@Binding`. Handing
    them `$viewModel.selectedPersona` sets the property directly, skipping `selectPersona(_:)`,
    so a re-applied default silently overwrites the user's tap. They get `personaBinding`,
    whose setter routes through the ViewModel.
    """
    content = _read(_CONTENT_VIEW)
    assert "$viewModel.selectedPersona" not in content, (
        "a persona picker binds directly to the property again, bypassing selectPersona()"
    )
    assert "viewModel.selectPersona($0)" in content, (
        "personaBinding no longer routes writes through the ViewModel's single writer"
    )


# ── 5. an unordered backend response cannot decide the default ───────────────

def test_load_personas_falls_back_to_the_setting_not_row_order(vm):
    """`GET /research/personas` has no ORDER BY, so `mapped[0]` handed arbitrary Postgres row
    order the power to pick a user-visible default.
    """
    body = _balanced(vm, "func loadPersonas() async {")
    assert "self.personas = mapped" in body, "loadPersonas no longer adopts the backend list"
    assert "mapped[0]" not in body, (
        "loadPersonas fell back to mapped[0] again — arbitrary row order decides the analyst"
    )
    assert "applyDefaultPersona(force:" in body, (
        "loadPersonas must re-derive through applyDefaultPersona, not assign selectedPersona "
        "itself — a third writer defeats the private(set) single-writer design"
    )


def test_selected_persona_has_exactly_two_writers(vm):
    """`private(set)` only guarantees the writers are IN this file. This pins that they are the
    two intended ones: the user's pick and the default being applied. A third assignment (the
    `loadPersonas` one that used to exist) is how the flag gets stranded on a persona that no
    longer exists.
    """
    import re

    body = _vm_class(vm)

    # Allowed regions, located by OFFSET so a write is attributed to the block it sits in.
    allowed = []
    for header in (
        "func selectPersona(_ persona: AnalysisPersona) {",
        "func applyDefaultPersona(force: Bool = false) {",
    ):
        block = _balanced(body, header)
        start = body.index(block)
        allowed.append((start, start + len(block)))

    # `\s*=` cannot match `selectedPersonaKeys` (next char is `K`), and `[^=]` excludes `==`.
    # It also does not match the declaration, which reads `var selectedPersona: AnalysisPersona
    # = …` — the type annotation sits between the name and the `=`. That is convenient rather
    # than load-bearing: the declaration is a first-paint seed, not a writer.
    writes = [m for m in re.finditer(r"\bselectedPersona\s*=[^=]", body)]
    assert len(writes) == 2, (
        f"expected exactly the two intended writers, found {len(writes)}"
    )

    stray = [
        body[max(0, m.start() - 40):m.end()].strip().splitlines()[-1]
        for m in writes
        if not any(lo <= m.start() < hi for lo, hi in allowed)
    ]
    assert not stray, (
        f"selectedPersona is assigned outside selectPersona/applyDefaultPersona: {stray}"
    )


def test_the_resolver_never_returns_a_persona_outside_the_pool(vm):
    """A resolved analyst that is not in `personas` highlights NO card on the carousel and makes
    the description card describe someone who is not on screen. Every fallback rung must stay
    inside the pool — an `allCases` rung escapes it whenever the backend stops serving a persona
    the user has stored.
    """
    body = _balanced(
        _read(_RESEARCH_MODELS),
        "static func settingsDefault(in candidates: [AnalysisPersona]) -> AnalysisPersona {",
    )
    assert "let pool =" in body, "the resolver no longer pins a single pool"
    assert "allCases.first { $0.key == storedKey }" not in body, (
        "the resolver can escape the candidate pool again and return an unserved persona"
    )
    # Every lookup rung must read from `pool`. Three of them: stored key, Buffett, first served.
    assert body.count("pool.first") >= 3, (
        "a fallback rung stopped resolving against the pool"
    )


def test_identity_change_re_derives_the_analyst(vm):
    """Per `.claude/rules/auth.md` §7 the previous account's state must not survive. The stored
    key is removed by `clearLocalForEndedSession()`, so this must be a FORCED re-derive.
    """
    body = _balanced(vm, "func handleIdentityChange(isActiveTab: Bool) async {")
    assert "applyDefaultPersona(force: true)" in body, (
        "the previous account's analyst survives a sign-out / account switch"
    )


def test_app_state_has_no_second_persona_copy():
    """A dead `ResearchState.selectedPersona = \"buffett\"` used to sit here — never read, and
    not even a valid persona key. A second copy is a ready-made way to render the wrong analyst.
    """
    body = _balanced(_read(_IOS / "Core" / "State" / "AppState.swift"), "final class ResearchState {")
    assert "selectedPersona" not in body, (
        "ResearchState grew a persona property again; the Research selection belongs to "
        "ResearchViewModel, which is the only thing that honours the user's setting"
    )


# ── anti-vacuity ─────────────────────────────────────────────────────────────

def test_the_guards_are_not_vacuous():
    """Mutation test. Rewrite the source back to its pre-fix shape IN MEMORY and confirm each
    window genuinely stops matching. A guard that cannot fail is not a guard.
    """
    vm_code = _read(_RESEARCH_VM)
    content = _read(_CONTENT_VIEW)

    # (1) The headline guard, mutated the way the bug actually shipped: `settingsDefault` read
    #     ONLY by the property declaration. The window must exclude `loadPersonas` too — a
    #     by-hand mutation proved the guard stayed green on that read alone, which is why the
    #     assertion below subtracts it.
    body = _vm_class(vm_code)
    reseed_window = body.replace(_balanced(body, "func loadPersonas() async {"), "")

    live_reads = [
        ln for ln in reseed_window.splitlines()
        if "settingsDefault" in ln and "private(set) var selectedPersona" not in ln
    ]
    assert live_reads, "no re-seed read exists to mutate; the guard proves nothing"

    broken = "\n".join(ln for ln in reseed_window.splitlines() if ln not in live_reads)
    assert not [
        ln for ln in broken.splitlines()
        if "settingsDefault" in ln and "private(set) var selectedPersona" not in ln
    ], "the mutation did not isolate the declaration — the guard would pass on the broken shape"

    # And prove the subtraction is real: `loadPersonas` must genuinely be excluded, or the
    # window is the whole class again and the guard is back to the vacuous version.
    assert len(reseed_window) < len(body), "loadPersonas was not excluded; scoping is decoration"

    # (2) Scoping is the guard for the class window. `_vm_class` must genuinely exclude the
    #     rest of the file, or "somewhere in ResearchViewModel" means "somewhere in the file".
    assert len(body) < len(vm_code), "the class window spans the whole file; scoping is decoration"

    # (3) The binding guard must be able to fail: re-introduce the direct binding.
    broken = content.replace("selectedPersona: personaBinding", "selectedPersona: $viewModel.selectedPersona")
    assert "$viewModel.selectedPersona" in broken, (
        "the mutation baseline is wrong — personaBinding is not what the pickers are given"
    )
    assert "$viewModel.selectedPersona" not in content, (
        "the current source already binds directly; the guard above is passing vacuously"
    )

    # (4) `_code_only` must actually blank comments, or every scan matches the rationale.
    assert "// " not in "\n".join(
        ln for ln in vm_code.splitlines() if ln.strip().startswith("//")
    ), "comment stripping is not working; these scans would match prose"
