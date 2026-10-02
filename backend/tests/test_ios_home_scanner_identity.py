"""Home's scanner cards and signal rows keep their identity across the 60 s refresh, and the
Today's Top Movers Gainers/Losers choice is kept on this device.

TestFlight 1.0 (9) asked for every on-screen choice to be "set up once and permanently keep
them". On Home the Gainers/Losers toggle did not even survive a minute:

* `DailyScanner` and `ExclusiveSignal` declared `let id = UUID()`. `HomeRepository.mapScanners`
  / `mapSignals` re-run on every fetch, and `HomeDashboardViewModel` re-assigns `data` on its
  60 s auto-refresh, so every card got a NEW id once a minute.
* `DailyScannersSection` keys its ForEach and `.id(...)` on that id, so SwiftUI rebuilt each
  `ScannerCard` and its `@State moversMode` reset to `.gainers` — the toggle "snapped back".
* `HomeDashboardView.expandedScannerIDs` / `expandedSignalIDs` held the OLD ids, so every
  expanded card and signal row closed by itself on the same tick.

Fix: a stable id derived from the kind (signal rows add the lock state, because the
`SignalDisclosureRow` preview lists unlocked and locked copies of the same kinds side by side),
and the movers choice in `@AppStorage("caydex_home_movers_mode")`, stored as the bare case-name
token. A device display preference, so it is NOT cleared on sign-out.

There is no XCTest target (testing.md §3), so this pins the Swift source: comments are stripped
before every assertion, every check is brace-bounded to the declaration it means, and each test
asserts it is reading a real, non-trivial declaration (anti-vacuity).

Mutation-tested IN MEMORY (``pathlib.Path.read_text`` monkeypatched for the one target file —
the real Swift files are never touched). The table runs on every pass as
``test_each_mutation_is_killed``, and each mutation must fail with the assertion message that
names it (``pytest.raises(match=…)``), so a mutation cannot "pass" by tripping an unrelated
earlier check. Covered: a UUID id on either struct; a signal id from the kind alone, from a
constant locked branch (`isLocked ? "locked" : kind` — every locked row shares one id) or an
affix-less one; a kind built twice in the live mapping or the mock; `@State` / a renamed key /
`.constant(...)` / a `.losers` fallback / a label-valued setter / a write-back of the fallback /
a getter that ignores the saved token / a write through `$storedMoversMode`; a
`UserDefaults.standard.set(…, forKey:)` of the key inside the card or in another file; a raw
string on a `MoversMode` case; and the sign-out funnel clearing the key.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_MODELS = _IOS / "Models" / "HomeDashboardModels.swift"
_REPO = _IOS / "Core" / "Repositories" / "HomeRepository.swift"
_CARD = _IOS / "Views" / "Molecules" / "ScannerCard.swift"
_TOGGLE = _IOS / "Views" / "Molecules" / "MoversToggle.swift"
_SECTION = _IOS / "Views" / "Organisms" / "DailyScannersSection.swift"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"

_KEY = "caydex_home_movers_mode"


def _strip_swift_comments(src: str) -> str:
    """Drop block comments, whole-line `//` comments and trailing `//` tails.

    Load-bearing: the fix's own comments name `UUID()`, `@State` and `moversMode` while
    explaining why they are gone, so an un-stripped scan for their ABSENCE fails on prose and
    a scan for their PRESENCE passes on a revert whose comment survived. A tail needs leading
    whitespace, so a `https://` inside a string literal is not cut.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for raw in src.splitlines():
        if raw.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", raw))
    return "\n".join(out)


def _code(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path.name} moved"
    return _strip_swift_comments(path.read_text(encoding="utf-8"))


def _block(src: str, header: str, open_: str = "{", close: str = "}") -> str:
    """The balanced `open_`…`close` body that follows the ONLY `header` (a literal prefix).

    `[`/`]` bound an array literal (the mock signals), `{`/`}` a declaration.
    """
    assert src.count(header) == 1, f"expected exactly one `{header}`, found {src.count(header)}"
    start = src.index(open_, src.index(header) + len(header))
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_:
            depth += 1
        elif src[i] == close:
            depth -= 1
            if depth == 0:
                return src[start: i + 1]
    raise AssertionError(f"unbalanced `{open_}{close}` after `{header}`")


# ── 1. Stable identity ────────────────────────────────────────────────────────


def test_daily_scanner_id_is_its_kind():
    """A per-instance `UUID()` re-mints the id on every 60 s fetch — the root cause."""
    body = _block(_code(_MODELS), "struct DailyScanner: Identifiable")
    # Anti-vacuity: this is the real model, not a stub that happens to share the name.
    assert "let kind: ScannerKind" in body and "let gainers: [ScannerEntry]" in body, body[:200]
    assert "UUID" not in body, (
        "DailyScanner mints a UUID again: every refresh rebuilds every ScannerCard, resets the "
        "Gainers/Losers toggle and closes expanded cards")
    assert re.search(r"\bvar\s+id\s*:\s*ScannerKind\s*\{\s*kind\s*\}", body), (
        "DailyScanner.id must be derived from its kind (one card per kind per payload)")
    assert not re.search(r"\blet\s+id\b", body), "a stored id is back — derive it from the kind"


def test_exclusive_signal_id_is_kind_plus_lock():
    """Kind alone collides in the preview that lists `signals + lockedSignals`; a UUID closes
    every expanded row on refresh."""
    body = _block(_code(_MODELS), "struct ExclusiveSignal: Identifiable")
    assert "let kind: String" in body and "drillDownKinds" in body, body[:200]
    assert "UUID" not in body, "ExclusiveSignal mints a UUID again: expanded rows close every 60 s"
    m = re.search(r"\bvar\s+id\s*:\s*String\s*\{([^}]*)\}", body)
    assert m, "ExclusiveSignal.id must be a computed String derived from the kind"
    expr = m.group(1).strip()
    tern = re.fullmatch(r"isLocked\s*\?\s*(.+?)\s*:\s*(.+)", expr, flags=re.S)
    assert tern, (
        f"ExclusiveSignal.id must combine kind AND isLocked, got `{expr}` — kind alone "
        "duplicates ForEach ids when a locked and an unlocked copy of a kind are listed together")
    # Mentioning both names is not enough: `isLocked ? "locked" : kind` gives EVERY locked row
    # of a Free user one shared id, and `isLocked ? "\(kind)" : kind` equals the unlocked id.
    # One branch is the bare kind; the other interpolates the kind PLUS a fixed affix.
    branches = [b.strip() for b in tern.groups()]
    other = branches[1] if branches[0] == "kind" else branches[0]
    affixed = re.fullmatch(r'"([^"\\]*)\\\(kind\)([^"\\]*)"', other)
    assert "kind" in branches and affixed and (affixed.group(1) + affixed.group(2)), (
        f"ExclusiveSignal.id branches must be the bare `kind` and the kind interpolated with a "
        f'fixed affix (e.g. "\\(kind)#locked"), got `{expr}` — a constant branch shares one id '
        "across every locked row, and an affix-less one collides with the unlocked row")
    assert not re.search(r"\blet\s+id\b", body), "a stored id is back — derive it"


def test_one_card_per_kind_in_live_and_mock_payloads():
    """The kind-derived ids are only unique because each payload carries each kind once. Two
    equal ids in one ForEach is undefined behaviour (rows vanish or swap state)."""
    repo = _code(_REPO)

    scanners = _block(repo, "private static func mapScanners(")
    built = re.findall(r"DailyScanner\(\s*kind:\s*\.(\w+)", scanners)
    assert len(built) == 3, f"mapScanners should build exactly three cards, found {built}"
    assert len(set(built)) == len(built), f"mapScanners builds a kind twice: {built}"

    signals = _block(repo, "private static func mapSignals(")
    kinds = re.findall(r'kind:\s*"(\w+)"', signals)
    assert len(kinds) == 4, f"mapSignals should emit four kinds, found {kinds}"
    assert len(set(kinds)) == len(kinds), f"mapSignals emits a kind twice: {kinds}"
    helper = _block(repo, "private static func signal(")
    assert helper.count("ExclusiveSignal(") == 1, "signal(from:) must build exactly one row per group"

    mock = _block(repo, "final class MockHomeRepository")
    listed = re.search(r"scanners:\s*\[([^\]]*)\]", mock)
    assert listed, "MockHomeRepository no longer lists its scanners"
    names = re.findall(r"Self\.(\w+)", listed.group(1))
    assert len(names) == 3, names
    mock_kinds = []
    for name in names:
        decl = re.search(rf"static let {name} = DailyScanner\(\s*kind:\s*\.(\w+)", mock)
        assert decl, f"mock scanner `{name}` not found"
        mock_kinds.append(decl.group(1))
    assert len(set(mock_kinds)) == 3, f"the mock repeats a scanner kind: {mock_kinds}"

    sample = _block(mock, "static let signals: [ExclusiveSignal] =", "[", "]")
    assert sample.count("ExclusiveSignal(") == 4, "MockHomeRepository.signals is not the four sample rows"
    sample_kinds = re.findall(r'kind:\s*"(\w+)"', sample)
    assert len(sample_kinds) == 4 and len(set(sample_kinds)) == 4, (
        f"MockHomeRepository.signals repeats a kind: {sample_kinds}")


def test_the_carousel_keys_on_the_model_id():
    """The section's identity must be the model's id (now stable) — not a fresh value of its
    own, which would reintroduce the reset one layer up."""
    body = _block(_code(_SECTION), "struct DailyScannersSection: View")
    assert "ScannerCard(" in body, "not the carousel"
    assert r"id: \.element.id" in body and ".id(scanner.id)" in body, (
        "the carousel no longer keys on DailyScanner.id")
    assert "UUID" not in body, "the carousel mints its own ids"


# ── 2. The Gainers/Losers choice is persisted ────────────────────────────────


def test_movers_mode_is_persisted_as_a_token_without_write_back():
    body = _block(_code(_CARD), "struct ScannerCard: View")
    assert "MoversToggle(" in body and "scanner.gainers" in body, "not the movers card"

    assert body.count(f'@AppStorage("{_KEY}")') == 1, (
        f"the Gainers/Losers choice must be stored under `{_KEY}` (set once, kept on this device)")
    decl = re.search(
        rf'@AppStorage\("{_KEY}"\)\s*private\s+var\s+(\w+)\s*:\s*String\s*=\s*MoversMode\.gainers\.rawValue',
        body)
    assert decl, "the stored value must be a String TOKEN defaulting to the gainers token"
    stored = decl.group(1)

    assert not re.search(r"@State[^\n]*\bMoversMode\b", body), (
        "moversMode is back in @State — it resets whenever the card is rebuilt or the app relaunches")
    assert not re.search(r"@State[^\n]*\b" + re.escape(stored) + r"\b", body), (
        "the stored token is held in @State, not UserDefaults")

    # Read: derived, with the fallback on READ only.
    assert re.search(
        rf"var\s+moversMode\s*:\s*MoversMode\s*\{{\s*MoversMode\(rawValue:\s*{stored}\)\s*\?\?\s*\.gainers\s*\}}",
        body), "moversMode must be derived from the token, falling back to .gainers"

    # Write: the toggle is bound to the persisted value, and only the toggle writes the token.
    assert "MoversToggle(mode: moversModeBinding)" in body, (
        "the toggle is not bound to the persisted value")
    writes = re.findall(rf"\b{stored}\s*=(?!=)\s*([^\n}}]+)", body)
    assert [w.strip() for w in writes] == ["$0.rawValue"], (
        f"`{stored}` must be written ONLY by the toggle binding, as the case token; found {writes} "
        "(writing the fallback back would erase a stored preference a newer build understands)")
    # The assignment scan above cannot see a write through the property wrapper's projections
    # (`$x.wrappedValue = …`, `_x.wrappedValue = …`, or `$x` handed to another control).
    assert not re.search(rf"[$_]{stored}\b", body), (
        f"`{stored}` is reached through a projection (`${stored}` / `_{stored}`) — a second "
        "writer the assignment scan cannot see; only `moversModeBinding` may write the token")

    # ...and that binding must READ the persisted mode. `get: { .gainers }` writes correctly
    # but draws Gainers forever, so the saved Losers never shows.
    assert re.search(
        rf"var\s+moversModeBinding\s*:\s*Binding<MoversMode>\s*\{{\s*"
        rf"Binding\(\s*get:\s*\{{\s*moversMode\s*\}}\s*,\s*"
        rf"set:\s*\{{\s*{stored}\s*=\s*\$0\.rawValue\s*\}}\s*\)\s*\}}",
        body), (
        "moversModeBinding must READ `moversMode` and WRITE the token: "
        f"`Binding(get: {{ moversMode }}, set: {{ {stored} = $0.rawValue }})`")


def test_the_movers_key_is_named_once_in_the_whole_tree():
    """The in-card scan proves `ScannerCard` writes the token only through its toggle; it
    cannot see `UserDefaults.standard.set(…, forKey: "caydex_home_movers_mode")` anywhere —
    inside the card, in another view, or in a reset/cleanup routine. The literal key appears
    exactly once, in the card's `@AppStorage`, across the app, `Shared/` and the widget."""
    root = _IOS.parent  # frontend/ios: ios/, Shared/, CaydexWidgets/
    files = sorted(root.rglob("*.swift"))
    assert len(files) > 500 and _CARD in files, (
        f"scanned {len(files)} Swift files under {root} — the tree moved; this guard is vacuous")
    hits = {}
    for f in files:
        n = _strip_swift_comments(f.read_text(encoding="utf-8")).count(f'"{_KEY}"')
        if n:
            hits[str(f.relative_to(root))] = n
    assert hits == {str(_CARD.relative_to(root)): 1}, (
        f'"{_KEY}" must appear once, in ScannerCard\'s @AppStorage; found {hits} — a second '
        "reader/writer (e.g. a UserDefaults write-back or a reset) bypasses the toggle-only rule")


def test_movers_token_is_the_case_name():
    """`MoversMode.rawValue` IS the persisted token. A raw string on a case ("Gainers") would
    turn it into a display label and silently reset every saved choice."""
    body = _block(_code(_TOGGLE), "enum MoversMode: String")
    cases = re.findall(r"\bcase\s+([^\n]+)", body)
    assert [c.strip() for c in cases] == ["gainers", "losers"], (
        f"MoversMode cases must be bare `gainers` / `losers` (rawValue = case name), got {cases}")


def test_movers_preference_survives_sign_out():
    """A device display preference holding no account data (precedent:
    `caydex_preferred_chart_type`) — the end-of-session funnel must leave it alone."""
    body = _block(_code(_APP_STATE), "private func discardDataForEndedSession()")
    assert "WhaleService.shared.reset()" in body and len(body) > 1000, "not the discard funnel"
    assert _KEY not in body, f"`{_KEY}` is a device preference; do not clear it on sign-out"


# ── 3. The mutations above, re-run in memory on every pass ──────────────────

_SIGNAL_ID = 'var id: String { isLocked ? "\\(kind)#locked" : kind }'
_BINDING = "Binding(get: { moversMode }, set: { storedMoversMode = $0.rawValue })"
_MOVERS_GETTER = "private var moversMode: MoversMode { MoversMode(rawValue: storedMoversMode) ?? .gainers }"
_WRITE_BACK = f'UserDefaults.standard.set("gainers", forKey: "{_KEY}")'

# (file, anchor, replacement, guard, the assertion message the guard must fail WITH). The
# message is matched so a mutation cannot pass by tripping an unrelated, earlier assertion.
_MUTATIONS = [
    (_MODELS, "var id: ScannerKind { kind }", "let id = UUID()",
     test_daily_scanner_id_is_its_kind, "DailyScanner mints a UUID again"),
    (_MODELS, "var id: ScannerKind { kind }", "var id: UUID { UUID() }",
     test_daily_scanner_id_is_its_kind, "DailyScanner mints a UUID again"),
    (_MODELS, _SIGNAL_ID, "let id = UUID()",
     test_exclusive_signal_id_is_kind_plus_lock, "ExclusiveSignal mints a UUID again"),
    (_MODELS, _SIGNAL_ID, "var id: String { kind }",
     test_exclusive_signal_id_is_kind_plus_lock, "ExclusiveSignal.id must combine kind AND isLocked"),
    # Review gap 1: both names present, ids still collide.
    (_MODELS, _SIGNAL_ID, 'var id: String { isLocked ? "locked" : kind }',
     test_exclusive_signal_id_is_kind_plus_lock, "ExclusiveSignal.id branches must be the bare `kind`"),
    (_MODELS, _SIGNAL_ID, 'var id: String { isLocked ? "\\(kind)" : kind }',
     test_exclusive_signal_id_is_kind_plus_lock, "ExclusiveSignal.id branches must be the bare `kind`"),
    (_MODELS, _SIGNAL_ID, "var id: String { isLocked ? kind : kind }",
     test_exclusive_signal_id_is_kind_plus_lock, "ExclusiveSignal.id branches must be the bare `kind`"),
    (_REPO, "kind: .volume,", "kind: .movers,",
     test_one_card_per_kind_in_live_and_mock_payloads, "mapScanners builds a kind twice"),
    (_REPO, 'kind: "earnings",\n            title: "Earnings Shockers",\n            iconSystemName: "bolt.fill",\n            accent: AppColors.accentYellow,\n            topSymbol',
     'kind: "whale",\n            title: "Earnings Shockers",\n            iconSystemName: "bolt.fill",\n            accent: AppColors.accentYellow,\n            topSymbol',
     test_one_card_per_kind_in_live_and_mock_payloads, "MockHomeRepository.signals repeats a kind"),
    (_CARD, f'@AppStorage("{_KEY}") private var storedMoversMode', "@State private var storedMoversMode",
     test_movers_mode_is_persisted_as_a_token_without_write_back, "the Gainers/Losers choice must be stored under"),
    (_CARD, f'@AppStorage("{_KEY}")', '@AppStorage("home_movers_mode")',
     test_movers_mode_is_persisted_as_a_token_without_write_back, "the Gainers/Losers choice must be stored under"),
    (_CARD, "MoversToggle(mode: moversModeBinding)", "MoversToggle(mode: .constant(moversMode))",
     test_movers_mode_is_persisted_as_a_token_without_write_back, "the toggle is not bound to the persisted value"),
    (_CARD, "MoversMode(rawValue: storedMoversMode) ?? .gainers", "MoversMode(rawValue: storedMoversMode) ?? .losers",
     test_movers_mode_is_persisted_as_a_token_without_write_back, "moversMode must be derived from the token"),
    (_CARD, "storedMoversMode = $0.rawValue", 'storedMoversMode = "\\($0)".capitalized',
     test_movers_mode_is_persisted_as_a_token_without_write_back, "must be written ONLY by the toggle binding"),
    (_CARD, _MOVERS_GETTER,
     _MOVERS_GETTER + "\n    private func heal() { storedMoversMode = MoversMode.gainers.rawValue }",
     test_movers_mode_is_persisted_as_a_token_without_write_back, "must be written ONLY by the toggle binding"),
    # Review gap 2: the getter ignores the saved choice.
    (_CARD, _BINDING, "Binding(get: { .gainers }, set: { storedMoversMode = $0.rawValue })",
     test_movers_mode_is_persisted_as_a_token_without_write_back, "moversModeBinding must READ `moversMode`"),
    # Review gap 3: writes the in-card assignment scan cannot see.
    (_CARD, _MOVERS_GETTER,
     _MOVERS_GETTER + "\n    private func heal() { $storedMoversMode.wrappedValue = MoversMode.gainers.rawValue }",
     test_movers_mode_is_persisted_as_a_token_without_write_back, "is reached through a projection"),
    (_CARD, _MOVERS_GETTER, _MOVERS_GETTER + "\n    private func heal() { " + _WRITE_BACK + " }",
     test_the_movers_key_is_named_once_in_the_whole_tree, "must appear once, in ScannerCard's @AppStorage"),
    (_MODELS, "struct DailyScanner: Identifiable {",
     "enum MoversReset { static func run() { " + _WRITE_BACK + " } }\n\nstruct DailyScanner: Identifiable {",
     test_the_movers_key_is_named_once_in_the_whole_tree, "must appear once, in ScannerCard's @AppStorage"),
    (_TOGGLE, "    case gainers\n", '    case gainers = "Gainers"\n',
     test_movers_token_is_the_case_name, "MoversMode cases must be bare"),
    (_APP_STATE, "        WhaleService.shared.reset()\n",
     f'        WhaleService.shared.reset()\n        UserDefaults.standard.removeObject(forKey: "{_KEY}")\n',
     test_movers_preference_survives_sign_out, "is a device preference; do not clear it on sign-out"),
]


@pytest.mark.parametrize(
    "path,old,new,test,message",
    _MUTATIONS,
    ids=[f"{p.name}:{i}" for i, (p, *_rest) in enumerate(_MUTATIONS)],
)
def test_each_mutation_is_killed(monkeypatch, path, old, new, test, message):
    """Each guard above must go red on the regression it names, WITH the message that names
    it. Patched in memory only — other sessions' tests read these Swift files concurrently,
    so they are never rewritten."""
    real_read_text = pathlib.Path.read_text
    original = real_read_text(path, encoding="utf-8")
    assert old in original, (
        f"mutation anchor `{old[:60]}` is gone from {path.name} — re-derive this mutation "
        "against the new source rather than deleting it")
    mutated = original.replace(old, new, 1)
    assert mutated != original

    def fake_read_text(self, *args, **kwargs):
        if pathlib.Path(self) == path:
            return mutated
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    # The unmutated source passes (the plain tests above prove it); mutated, it must fail.
    with pytest.raises(AssertionError, match=re.escape(message)):
        test()
