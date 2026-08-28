"""Source-scan guards: every share must carry a link the recipient can act on.

TestFlight, build 1.0 (3), ORCL detail screen — *"For the share feature, it should send the
link to download for my app on apple store and my website"*. The share sheet was producing a
single plain String and nothing else: a company name, a price, and "Check it out on Caydex!"
— no URL, no rich preview, nothing to act on. The feature had in fact been scaffolded and
abandoned; `TickerDetailView` still carried a commented-out
`items.append(URL(string: "https://yourapp.com/stock/\\(ticker)")!)`.

Why a scan rather than a unit test: there is no XCTest target, and the failure mode is
DUPLICATION, not logic. The payload was built inline at eleven call sites, five of them
byte-identical copies of the same twenty lines, with zero test coverage — so nothing stopped
a sixth copy from drifting, and nothing noticed that one of them
(`BookDetailView`) shipped a live link to `app.example.com`, a dead host.

The first scan below is DERIVED, not enumerated: it walks every `.swift` file and requires
each `ShareSheet(items:)` to resolve through `ShareContent`, so a share added to a NEW screen
is caught rather than silently uncovered. That is the shape `test_ios_no_silent_url_open.py`
uses, and for the same reason.

⚠️ Comments are stripped before every assertion. The comments beside these fixes quote
`app.example.com`, `ShareContent` and `Check it out on Caydex!` verbatim, so an un-stripped
scan would pass on prose after the code was reverted — the vacuity documented in
`.claude/rules/testing.md` §3. `test_the_scanners_are_not_vacuous` proves the helpers bite.
"""

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_APP_INFO = _IOS / "Core/Utilities/AppInfo.swift"
_SHARE_CONTENT = _IOS / "Core/Utilities/ShareContent.swift"

_WEBSITE = "https://caydexinvest.com"

# Share sites that must NOT carry a marketing link, with the reason. Anything else that
# constructs a ShareSheet has to go through ShareContent.
_EXEMPT = {
    # Shares a drafted SUPPORT email (plus diagnostics) when Mail is unavailable. Appending
    # "get the app" to a bug report is wrong.
    "Views/Molecules/MailUnavailableCard.swift",
    # The ShareSheet atom's own #Preview.
    "Views/Atoms/ShareSheet.swift",
}

# The five asset-detail screens and the symbol each falls back to mid-load.
_DETAIL_SCREENS = [
    ("TickerDetailView.swift", "tickerSymbol"),
    ("IndexDetailView.swift", "indexSymbol"),
    ("CryptoDetailView.swift", "cryptoSymbol"),
    ("CommodityDetailView.swift", "commoditySymbol"),
    ("ETFDetailView.swift", "etfSymbol"),
]


def _strip_comments(src: str) -> str:
    """Drop `//` lines and trailing `//` tails. See the module docstring."""
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


def _swift_files():
    return sorted(_IOS.rglob("*.swift"))


# ── 1. Every share resolves through ShareContent ──────────────────────


def test_every_share_sheet_goes_through_share_content():
    violations: list[str] = []

    for path in _swift_files():
        rel = str(path.relative_to(_IOS))
        if rel in _EXEMPT:
            continue
        src = _strip_comments(path.read_text())
        for match in re.finditer(r"ShareSheet\(items:\s*([^)\n]*)", src):
            expr = match.group(1).strip()
            if "ShareContent" in expr:
                continue
            # A bare identifier is fine IF its declaration in the same file builds the
            # payload with ShareContent (this is how the five detail screens do it).
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expr):
                decl = re.search(rf"var {re.escape(expr)}\s*:\s*\[Any\]\s*{{", src)
                if decl and "ShareContent" in _decl_block(src, decl.group(0)[:-1]):
                    continue
            line = src[: match.start()].count("\n") + 1
            violations.append(f"{rel}:{line} — ShareSheet(items: {expr})")

    assert not violations, (
        "these share sheets build their payload inline instead of going through "
        "ShareContent, so the recipient gets no link to download the app:\n  "
        + "\n  ".join(violations)
        + "\n\nUse ShareContent.items(_:attaching:), or add the file to _EXEMPT here with "
        "a reason if a marketing link genuinely does not belong there."
    )


def test_the_exempt_list_still_matches_reality():
    """A stale exemption is a silent hole — it would let a real share site drift."""
    for rel in _EXEMPT:
        path = _IOS / rel
        assert path.exists(), f"_EXEMPT names {rel}, which no longer exists"
        assert "ShareSheet(items:" in _strip_comments(path.read_text()), (
            f"_EXEMPT names {rel}, which no longer constructs a ShareSheet — drop it"
        )


# ── 2. The link itself ────────────────────────────────────────────────


def test_app_info_declares_a_working_website_url():
    block = _decl_block(_APP_INFO.read_text(), "enum AppInfo")
    assert f'URL(string: "{_WEBSITE}")' in block, (
        f"AppInfo.websiteURL must be {_WEBSITE} — measured 2026-08-28 as the only path on "
        "that domain that answers 200 (/app, /download, /get and /ios all 404)"
    )
    assert "static var downloadURL: URL { appStoreURL ?? websiteURL }" in block, (
        "downloadURL must fall back to the website while appStoreAppID is blank, and prefer "
        "the App Store once it is set — that fallback is what makes launch day a "
        "one-constant flip"
    )


def test_the_app_store_id_is_still_unset():
    """`https://apps.apple.com/app/id6759525689` 404s until the app is approved (measured
    2026-08-28). Filling the id early ships a dead link to every share recipient AND breaks
    the "Rate the App" row, which returns nil while it is blank. Delete this test on the day
    the app goes live — that is the intended way to retire it."""
    block = _decl_block(_APP_INFO.read_text(), "enum AppInfo")
    assert 'static let appStoreAppID = ""' in block, (
        "appStoreAppID was filled in. If the app is now live on the App Store this test has "
        "done its job — delete it. If it is not live yet, revert: the id 404s and every "
        "share would point at a dead page."
    )


def test_no_placeholder_host_reaches_a_share_payload():
    """`BookDetailView` shipped `https://app.example.com/book/<id>` — a dead host every
    recipient of a book share was sent to.

    Scoped to files that actually CONSTRUCT a share, not the whole tree: `example.com` is
    the IANA-reserved documentation domain and is the RIGHT placeholder in a preview. Three
    files use it correctly that way — `MockStockRepository` (preview repository), a
    deliberately-unreachable image URL that exercises a failure state, and a `#Preview`
    email — and failing the build on those would just teach the next person to invent a
    real-looking host instead."""
    offenders = []
    for path in _swift_files():
        src = _strip_comments(path.read_text())
        if "ShareSheet(items:" not in src:
            continue
        if "example.com" in src:
            offenders.append(str(path.relative_to(_IOS)))
    assert not offenders, (
        f"placeholder host in a file that builds a share: {offenders}. "
        "Point at AppInfo.downloadURL, or at nothing."
    )


# ── 3. ShareContent's own contract ────────────────────────────────────


def test_share_content_appends_the_download_link_after_the_callers_items():
    block = _decl_block(_SHARE_CONTENT.read_text(), "enum ShareContent")
    assert "return extras + [caption, AppInfo.downloadURL]" in block, (
        "ShareContent must put the caller's `extras` FIRST — UIActivityViewController picks "
        "the activity from the first item's type, so a PDF or a publisher URL has to lead or "
        '"Save to Files" / AirDrop see a text share — and end with the download link.'
    )
    assert "AppInfo.downloadURL" in block, "the download link is gone"


# ── 4. The empty-share-sheet fix ──────────────────────────────────────


@pytest.mark.parametrize("screen,symbol", _DETAIL_SCREENS)
def test_detail_screens_share_something_even_before_the_data_lands(screen, symbol):
    """These built `shareItems` INSIDE `if let data`, so a share tapped mid-load presented
    UIActivityViewController with zero activity items — a blank sheet."""
    src = (_IOS / "Views/Screens" / screen).read_text()
    block = _decl_block(src, "private var shareItems: [Any]")
    assert f"return ShareContent.items({symbol})" in block, (
        f"{screen} has no mid-load fallback — if the data is nil the share sheet is empty. "
        f"Return ShareContent.items({symbol}) from a guard instead."
    )
    assert block.count("ShareContent.items(") == 2, (
        f"{screen} should build exactly two payloads: the loaded one and the fallback"
    )


# ── 5. Anti-vacuity ───────────────────────────────────────────────────


def test_the_scanners_are_not_vacuous():
    fake_src = (
        "struct Decoy: View {\n"
        '    var body: some View { ShareSheet(items: ShareContent.items("x")) }\n'
        "}\n"
        "\n"
        "enum ShareContent {\n"
        "    // return extras + [caption, AppInfo.downloadURL] example.com\n"
        "    static let attribution = 0\n"
        "}\n"
    )
    block = _decl_block(fake_src, "enum ShareContent")
    assert "AppInfo.downloadURL" not in block, "comments are not being stripped"
    assert "example.com" not in block, "comments are not being stripped"
    assert "ShareSheet" not in block, (
        "the scan leaked into the neighbouring `Decoy` declaration"
    )

    # And the payload regex must actually reject an inline payload.
    inline = _strip_comments('ShareSheet(items: [book.title, "by \\(a)"])')
    m = re.search(r"ShareSheet\(items:\s*([^)\n]*)", inline)
    assert m is not None and "ShareContent" not in m.group(1), (
        "the ShareSheet regex no longer matches an inline payload, so scan 1 is vacuous"
    )
