"""Book reader warm start must never evict another narration (BookCoreDetailView).

`ensureBookEpisodeLoaded()` runs on `.onAppear` and on every core change. It PREPARES the reader's
own book narration (`AudioManager.load`, paused) so the play button starts instantly. `load()`
tears down whatever is loaded first, so with book A narrating (playing OR paused) and book B's
text opened purely to READ, the warm start replaced A with B: A's audio stopped and the mini
player switched to B. Reproduced on the iPhone 17 Pro simulator with both files in the local
narration cache (book 5 playing, then a core of book 4 opened).

The warm start now prepares only into an empty player — `hasActiveEpisode` is false (nothing
loaded, or an episode that was only prepared and never started, or one that already finished).
The check sits AFTER the `await` that resolves the playable URL, because the user can start
audio from the mini player during that suspension; a second check before the `Task` saves the
signed-URL request when the warm start cannot happen anyway.

The earlier egress guard is pinned alongside it: `load()` starts buffering, so a warm start from a
cache miss spent up to 45 MB of Storage egress on someone who only opened the text.

Source scans with comments stripped and declarations brace-bounded (testing.md §3).
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
IOS = REPO / "frontend/ios/ios"
BOOK_CORE = IOS / "Views/Screens/BookCoreDetailView.swift"

# `guard !audioManager.hasActiveEpisode else { return }` or `if audioManager.hasActiveEpisode { return }`.
_NOT_ACTIVE_GUARD = re.compile(
    r"guard\s+!\s*audioManager\.hasActiveEpisode\s+else\s*\{\s*return\s*\}"
    r"|if\s+audioManager\.hasActiveEpisode\s*\{\s*return\s*\}"
)
_CACHE_GUARD = re.compile(r"guard\b[^{]*LearnAudioCache\.shared\.cachedFile\(for:[^{]*else\s*\{\s*return\s*\}", re.S)
_LOAD = "audioManager.load("


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        m = re.search(r"\s//", line)
        if m and line[: m.start()].count('"') % 2 == 0:
            line = line[: m.start()]
        out.append(line)
    return "\n".join(out)


def _block_after(src: str, anchor: str) -> str:
    at = src.find(anchor)
    assert at >= 0, f"`{anchor}` not found"
    open_at = src.index("{", at)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at : i + 1]
    pytest.fail(f"unbalanced braces after `{anchor}`")


def _reader_struct() -> str:
    assert BOOK_CORE.exists(), f"{BOOK_CORE} is missing — every assertion below would be vacuous"
    return _block_after(_strip_comments(BOOK_CORE.read_text()), "struct BookCoreDetailView: View")


def _warm_start() -> str:
    return _block_after(_reader_struct(), "private func ensureBookEpisodeLoaded()")


def _warm_start_task() -> str:
    return _block_after(_warm_start(), "Task {")


def test_the_reader_prepares_audio_only_through_the_warm_start():
    reader = _reader_struct()
    assert reader.count(_LOAD) == 1, (
        "BookCoreDetailView must call `audioManager.load(` exactly once, inside "
        "`ensureBookEpisodeLoaded()` — a second prepare site would bypass the guards pinned below"
    )
    assert _LOAD in _warm_start()


def test_the_warm_start_rechecks_for_an_active_episode_after_the_await():
    task = _warm_start_task()
    await_at = task.find("await ")
    load_at = task.find(_LOAD)
    assert await_at >= 0, "the warm start no longer awaits the playable episode — re-read this test's premise"
    assert load_at > await_at, "`load(` must follow the URL resolution"
    guards = [m.start() for m in _NOT_ACTIVE_GUARD.finditer(task)]
    assert any(await_at < g < load_at for g in guards), (
        "`ensureBookEpisodeLoaded()` must return when `audioManager.hasActiveEpisode` is true, "
        "checked AFTER the await and BEFORE `audioManager.load(` — otherwise opening book B's "
        "text replaces the narration of book A the user is listening to"
    )


def test_the_warm_start_skips_the_url_request_when_another_episode_is_active():
    body = _warm_start()
    task_at = body.find("Task {")
    assert task_at >= 0
    assert _NOT_ACTIVE_GUARD.search(body[:task_at]), (
        "check `hasActiveEpisode` before spawning the Task too — resolving a signed URL for a warm "
        "start that cannot happen is a wasted request"
    )


def test_the_warm_start_only_prepares_from_the_local_cache():
    task = _warm_start_task()
    load_at = task.find(_LOAD)
    cache = _CACHE_GUARD.search(task)
    assert cache and cache.start() < load_at, (
        "the warm start must `guard` on `LearnAudioCache.shared.cachedFile(for:)` before "
        "`audioManager.load(` — `load()` starts buffering, and a cache miss spends Storage egress "
        "on a reader who never pressed play"
    )
