"""Guards for the ONE saved Money Move topic (iOS side).

WHY THIS FILE EXISTS. `MoneyMoveBookmarkStore` is the sixth device-global store in this app: a
`UserDefaults` key with no user id in it. `WhaleService.followedWhaleIds`, the four Learn stores
and `SearchHistoryStore` each shipped the same bug first — the next account to sign in on the
phone inherited the previous user's data, and the store's own push then wrote it into THEIR rows.
`.claude/rules/auth.md` §7 is the rule; test 1 is the enforcement.

The rest pin the decisions that make this store safe to be short: it is single-valued (so no
list reconciliation is possible), it never adopts a server answer over an unsynced local write
(so an offline un-bookmark is not resurrected), it is hydrated lazily off the launch path, and
the toggle is hidden for an article with no slug (so it cannot silently do nothing).

Per `.claude/rules/testing.md` §3 and `project_source_scan_guard_vacuity`, every scan here is
comment-stripped, brace-bounded to the declaration under test, and was mutation-tested by hand.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"

_APP_STATE = _IOS / "Core/State/AppState.swift"
_STORE = _IOS / "Services/MoneyMoveBookmarkStore.swift"
_LIST_VIEW = _IOS / "Views/Screens/MoneyMovesDetailView.swift"
_ARTICLE_VIEW = _IOS / "Views/Screens/MoneyMoveArticleDetailView.swift"
_ENDPOINTS = _IOS / "Core/Services/APIEndpoint.swift"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails.

    Load-bearing: every rationale comment in these files names the very tokens scanned for
    below — `pendingRemoval`, `hydrate`, `slug`, `BookmarkStore`. An un-stripped scan would pass
    on the prose after the code it describes was reverted.
    """
    out = []
    for line in src.splitlines():
        if line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of a declaration, comments stripped."""
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return _strip_comments(src[open_brace : i + 1])
    pytest.fail(f"unbalanced braces after {header!r}")


# ── 1. The cross-account bleed guard — the important one ───────────────────────


def test_the_saved_topic_is_cleared_when_a_session_ends():
    block = _decl_block(_read(_APP_STATE), "private func discardDataForEndedSession()")

    # Anti-vacuity: prove this is the real session-end funnel, not an empty or renamed block.
    assert "LearnIdentityEpoch.bump()" in block, "scan drifted — not the session-end funnel"
    assert "BookmarkStore.shared.reset()" in block, "scan drifted — not the session-end funnel"

    assert "MoneyMoveBookmarkStore.shared.reset()" in block, (
        "auth.md §7: the saved Money Move topic lives under a device-global defaults key with no "
        "user id. Without this reset the next account on the phone inherits it AND pushUnsynced "
        "writes it into their own rows."
    )


# ── 2. Single-valued, structurally — not by comment ────────────────────────────


def test_the_store_holds_one_optional_slug_not_a_list():
    src = _strip_comments(_read(_STORE))

    decl = re.search(r"@Published[^\n]*var\s+bookmarkedSlug\s*:\s*([^\n=]+)", src)
    assert decl, "bookmarkedSlug is gone or no longer @Published — this scan has drifted"
    declared = decl.group(1).strip()
    assert declared == "String?", (
        f"the saved topic must be a single optional slug, found {declared!r}. A collection here "
        "reintroduces every ordering/tombstone problem BookmarkStore exists to solve, and the "
        "screen can only ever render one row."
    )
    assert "private(set)" in decl.group(0), "writes must go through toggle()/reset(), not the view"


def test_toggle_displaces_rather_than_accumulating():
    block = _decl_block(_read(_STORE), "func toggle(slug: String)")

    # Anti-vacuity: this really is the write path.
    assert "bumpLocalVersion()" in block and "persistLocal()" in block, "scan drifted"
    # Saving a new topic must ASSIGN, never append/insert into a collection.
    assert "bookmarkedSlug = s" in block, "toggle no longer assigns the new slug"
    assert not re.search(r"\.(append|insert)\(", block), (
        "toggle must replace the saved topic, not accumulate topics"
    )


# ── 3. An unsynced local write is never overwritten by the server ──────────────


def test_hydrate_pushes_local_before_it_would_adopt_a_stale_server_value():
    block = _decl_block(_read(_STORE), "private func performHydrate() async")

    assert "getMoneyMoveBookmark" in block, "scan drifted — this is not the hydrate path"

    guard_at = block.find("hasUnsyncedWrite")
    adopt_at = block.find("adopt(resp.bookmark)")
    assert guard_at != -1, (
        "hydrate must refuse to adopt the server's value while a local change is unsynced — "
        "otherwise an un-bookmark made offline is resurrected by the next GET (the server still "
        "has the row) and the user's explicit tap is silently reversed."
    )
    assert adopt_at != -1, "hydrate no longer adopts a server value — this scan has drifted"
    assert guard_at < adopt_at, "the unsynced-write check must run BEFORE the adopt"


def test_every_response_is_guarded_by_both_the_version_and_the_identity_epoch():
    src = _read(_STORE)
    for fn in (
        "private func performHydrate() async",
        "private func pushSet(_ slug: String) async",
        "private func pushRemove(_ slug: String) async",
    ):
        block = _decl_block(src, fn)
        # The COMPARISON, not the mere mention. Each of these functions opens by snapshotting
        # `let epoch = LearnIdentityEpoch.current` / `let token = localVersion`, so a
        # presence-only scan stays green with the guard itself deleted — verified: removing the
        # `guard` from pushSet left an earlier draft of this test passing.
        assert re.search(r"epoch\s*==\s*LearnIdentityEpoch\.current", block), (
            f"{fn}: without the epoch COMPARISON the previous account's bookmark is adopted "
            "after a sign-out and then pushed into the new account's rows"
        )
        assert re.search(r"token\s*==\s*localVersion", block), (
            f"{fn}: without the version COMPARISON a response that predates a local write is "
            "merged, putting back what the user just changed"
        )


# ── 4. Lazily hydrated — no new launch request ─────────────────────────────────


def test_the_saved_topic_is_hydrated_from_the_screen_not_the_launch_fan_out():
    app_state = _read(_APP_STATE)
    fan_out = _decl_block(app_state, "private func hydrateLearnStores()")
    assert "BookmarkStore.shared.hydrate()" in fan_out, "scan drifted — not the auth fan-out"
    assert "MoneyMoveBookmarkStore" not in fan_out, (
        "the saved-topic row exists on one screen with its own .task, so hydrating it from the "
        "auth fan-out would add a request to every signed-in launch for a screen most launches "
        "never open — 'clear eagerly, fetch lazily'."
    )

    screen = _strip_comments(_read(_LIST_VIEW))
    task = re.search(r"\.task\s*\{(.*?)\n        \}", screen, re.S)
    assert task, "MoneyMovesDetailView's .task block was not found — this scan has drifted"
    body = task.group(1)
    # Anti-vacuity: the same block must still do the thing it has always done.
    assert "MoneyMovesContentStore.shared.prefetch()" in body, "scan drifted — wrong .task block"
    assert "bookmarks.hydrate()" in body, "the screen must hydrate the saved topic on open"


# ── 5. Un-saveable articles show no control ────────────────────────────────────


def test_the_toggle_is_hidden_for_an_article_with_no_slug():
    """
    `createArticleFromMove` builds a placeholder article for an unauthored card and leaves `slug`
    empty, so there is no id to save under. A button that silently does nothing is worse than no
    button — and `toggle` would drop the write anyway.
    """
    block = _decl_block(_read(_ARTICLE_VIEW), "private var bookmarkState: Bool?")
    assert "shown.slug.isEmpty" in block, "the saved state must be nil for a slug-less article"
    assert "shown.slug" in block and "article.slug" not in block, (
        "must read `shown`, not `article` — a Related-articles tap swaps the article in place, "
        "and keying off `article` leaves the button reporting the topic the reader opened with"
    )

    tap = _decl_block(_read(_ARTICLE_VIEW), "private func handleBookmarkTapped()")
    assert "shown.slug.isEmpty" in tap, "the tap handler must also refuse an empty slug"


# ── 6. Auth policy parity with the backend ─────────────────────────────────────


def test_the_new_endpoints_are_guest_allowed():
    """
    Backend uses `get_learn_identity`, which resolves a signed-out caller to a per-install
    identity. Gating these on a token would delete a working feature for every guest
    (auth.md §1a). `test_ios_auth_policy_parity.py` checks the whole matrix; this pins the case.
    """
    block = _decl_block(_read(_ENDPOINTS), "nonisolated var authPolicy: AuthPolicy")
    guest_arm = re.search(
        r"case\s+([^:]*?getMoneyMoveBookmark.*?):\s*\n\s*return\s+\.(\w+)", block, re.S
    )
    assert guest_arm, "getMoneyMoveBookmark has no authPolicy arm"
    assert guest_arm.group(2) == "guestAllowed", (
        f"expected .guestAllowed, found .{guest_arm.group(2)}"
    )
    arm_cases = guest_arm.group(1)
    for case in ("setMoneyMoveBookmark", "removeMoneyMoveBookmark"):
        assert case in arm_cases, f"{case} is not in the guest-allowed arm"
