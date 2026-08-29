"""Source-scan guards: the Insights SOURCES list names the OUTLET, not the host.

TestFlight, Market Insights sheet: *"Why do we have 'youtube' here? News doesn't
have youtube right? Or it's from website search?"* — two rows subtitled
"youtube.com".

Not web search, and not bad data. Measured against the live feed, roughly a
QUARTER of `news/general-latest` is broadcast video — Bloomberg Markets and
Finance, CNBC Television, Schwab Network, Fox Business — and the feed links each
segment to its video upload, so `URL.host` says "youtube.com" for what is really
a Bloomberg clip. The outlet name was known all along: it rides on the corpus
row's `source_name` and was dropped on the way to the screen.

Both halves are needed, and the backend half is pinned elsewhere
(`test_insight_source_ranking.py`, `test_updates_schema_parity.py`,
`test_news_source_writers.py`). This file pins the client:

  * the row prefers the backend `publisher` and keeps `host` only as a fallback,
  * the fallback still exists, because cards cached before the field shipped
    carry no publisher for up to their 96h hard TTL,
  * the video marker is derived from the URL — so it labels those legacy cards
    too, which are the rows most likely to still read "youtube.com",
  * the DTO field is Optional, because a non-Optional `publisher` would fail to
    decode every one of those legacy cards and blank the whole sheet.

Comments are stripped before every assertion: the comments beside this change
quote "youtube.com", "publisher" and "host" verbatim, so an un-stripped scan
would pass on the prose after the code was reverted (`.claude/rules/testing.md`
§3). `test_the_scanners_are_not_vacuous` proves the helpers bite.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_IOS = _ROOT / "frontend/ios/ios"
_MODELS = _IOS / "Models/UpdatesModels.swift"
_DETAIL = _IOS / "Views/Screens/InsightsDetailView.swift"


def _strip_comments(src: str) -> str:
    out = []
    for line in src.splitlines():
        if line.strip().startswith("///") or line.strip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of a declaration, comments stripped.

    Brace-bounding matters here: `UpdatesModels.swift` is ~700 lines holding many
    DTOs and models, and `InsightsDetailView.swift` ends in a `#Preview` that
    constructs `InsightSource` rows WITH publishers — so a whole-file scan for
    `publisher` would pass on preview data after the live row was reverted.
    """
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


def _source_model() -> str:
    return _decl_block(_MODELS.read_text(), "struct InsightSource: Identifiable")


def _source_dto() -> str:
    return _decl_block(_MODELS.read_text(), "struct InsightSourceDTO")


def _source_row() -> str:
    return _decl_block(_DETAIL.read_text(), "private func sourceRow(")


# ── 1. The model prefers the publisher ────────────────────────────────


def test_the_model_carries_a_publisher():
    assert re.search(r"let publisher: String\?", _source_model()), (
        "InsightSource dropped `publisher`, so the row falls back to the URL host "
        "and broadcast segments are attributed to youtube.com again."
    )


def test_display_name_prefers_publisher_and_falls_back_to_host():
    body = _decl_block(_source_model(), "var displayName")
    assert "publisher" in body and "host" in body, (
        "displayName must consult BOTH: the publisher is the answer, the host is "
        "the fallback for cards cached before the field shipped."
    )
    assert body.index("publisher") < body.index("host"), (
        "host is being consulted before publisher — that is the original bug."
    )


def test_the_host_fallback_still_exists():
    """Deleting it would blank the subtitle on every legacy card."""
    assert re.search(r"var host: String\?", _source_model())


# ── 2. The video marker is derived on the client ──────────────────────


def test_the_video_marker_keys_off_the_video_hosts():
    body = _decl_block(_source_model(), "var isVideo")
    for host in ("youtube.com", "youtu.be", "m.youtube.com"):
        assert host in body, f"{host} no longer marks a source as video"


def test_the_video_marker_reads_the_url_not_a_backend_field():
    """Derived on-device on purpose: it must label cards cached BEFORE the
    backend change, which carry no publisher and are the confusing ones."""
    body = _decl_block(_source_model(), "var isVideo")
    assert "host" in body
    assert "publisher" not in body


# ── 3. The DTO decodes legacy cards ───────────────────────────────────


def test_the_dto_publisher_is_optional():
    dto = _source_dto()
    assert re.search(r"let publisher: String\?", dto), (
        "a non-Optional publisher fails to decode every card stored before the "
        "field existed — the whole Insights sheet, for up to its 96h hard TTL."
    )


# ── 4. The row renders the outlet ─────────────────────────────────────


def test_the_row_renders_display_name_not_the_bare_host():
    row = _source_row()
    assert "displayName" in row, "the source row went back to rendering the raw host"
    assert not re.search(r"Text\(\s*host\s*\)", row)


def test_the_row_shows_the_video_marker():
    assert "isVideo" in _source_row()


# ── anti-vacuity ──────────────────────────────────────────────────────


def test_the_scanners_are_not_vacuous():
    """Every helper above must actually bite. Mutate in memory and re-assert."""
    assert "publisher" in _source_model()
    assert "displayName" in _source_row()

    # Comment stripping is real: the prose beside this change names every token.
    commented = _strip_comments("// let publisher: String?\nlet x = 1\n")
    assert "publisher" not in commented

    # Brace-bounding is real: a matching header outside the target must not leak.
    sample = "struct A { let publisher: String? }\nstruct B { let other: Int }"
    assert "publisher" not in _decl_block(sample, "struct B")

    # A missing declaration fails loudly rather than returning "".
    with pytest.raises(AssertionError):
        _decl_block("struct Z {}", "struct NotHere")
