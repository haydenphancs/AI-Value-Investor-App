"""Report chat's live web search — the iOS half, pinned from the Swift source.

Owner decisions (2026-10-02): report chat may search the web, on explicit request only; the
thinking card shows a visible "Searching the web…" status while the search runs and a small
"Web search" badge on the finished answer; web source pills open the article in an IN-APP
browser; the pills are LIVE (the server does not store them by default), so a reopened chat
shows the badge but not the links.

Wire contract these scans pin (the server side is `chat_web_search_service` + the stream door):

* SSE `tool_start {"name": "web_search"}` → the card reads "Searching the web…";
* SSE `tool_step {"name", "args", "error"?, "skipped"?: true}` — `skipped` means the search did
  not run (the day's limit, or unavailable) and claims no progress;
* SSE `sources` re-sent as the FULL list; a web pill is
  `{"kind":"web","label":"Web","detail":<publisher>,"title","url":"https://…","published_at"}`;
* the `done` message's `thinking` may carry `"web_searched": true` (history rows too).

What regresses silently on iOS, and what each block pins:

* the decode stays TOTAL — one malformed pill must never blank a history;
* only an https address with a real host becomes a link (`ChatSource.webURL`), and only through
  the in-app browser (never `openInSystem`: `caydex://` is this app's own sign-in scheme);
* ONE sanitizer on every entry path (history, non-stream, `done`, the live frame);
* the handler reaches the card through every hop (a dropped hop is a dead button that renders);
* the browser is a SHEET (a nested cover would unmount the chat and re-run its `onAppear`);
* every ViewModel rebuild of `ChatThinking` carries the web-search state (one frame must not
  clobber another channel's state — the reasoning bug this file's neighbours already pin).

testing.md §3: comments are stripped string-aware (a trailing `//` comment can no longer satisfy
a scan, and `"https://"` inside a literal survives), every scan is brace-bound to its
declaration, and each guard was mutation-tested once by hand on a scratch copy of the tree.
"""

from __future__ import annotations

import collections
import functools
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_MODELS = _IOS / "Models/ChatConversationModels.swift"
_VM = _IOS / "ViewModels/ChatViewModel.swift"
_CARD = _IOS / "Views/Molecules/ThinkingProcessCard.swift"
_CONTENT = _IOS / "Views/Molecules/AIMessageContent.swift"
_LIST = _IOS / "Views/Organisms/ChatMessagesList.swift"
_SCREEN = _IOS / "Views/Screens/AIChatScreen.swift"
_BROWSER = _IOS / "Views/Modifiers/InAppBrowser.swift"


# ── scanning helpers ─────────────────────────────────────────────────────────

_CODE_TOKEN = re.compile(r'//|/\*|(#*)("""|")|[()]')
_BLOCK_TOKEN = re.compile(r"/\*|\*/")


@functools.lru_cache(maxsize=None)
def _strip_comments(src: str) -> str:
    """Every Swift comment removed, LINE COUNT PRESERVED.

    String-aware: `//` and `/* */` count only outside a literal (so "https://…" survives and a
    trailing comment does not); block comments nest, as Swift's do; literals are "…", \"\"\"…\"\"\"
    and raw #"…"#, and an interpolation `\\( … )` is code again.
    """
    out: list = []
    i, n = 0, len(src)
    stack: list = []   # ["code", paren_depth] for an interpolation, ("str", close, escape, multiline)
    while i < n:
        top = stack[-1] if stack else None
        if top is None or top[0] == "code":
            m = _CODE_TOKEN.search(src, i)
            if not m:
                out.append(src[i:])
                break
            out.append(src[i:m.start()])
            tok = m.group(0)
            if tok == "//":
                j = src.find("\n", m.end())
                i = n if j == -1 else j
            elif tok == "/*":
                depth, j = 1, m.end()
                while depth:
                    b = _BLOCK_TOKEN.search(src, j)
                    if not b:
                        j = n
                        break
                    depth += 1 if b.group(0) == "/*" else -1
                    j = b.end()
                out.append("\n" * src.count("\n", m.start(), j))
                i = j
            elif tok in ("(", ")"):
                out.append(tok)
                i = m.end()
                if top is not None:
                    top[1] += 1 if tok == "(" else -1
                    if top[1] == 0:
                        stack.pop()
            else:
                hashes = "#" * len(m.group(1))
                quote = m.group(2)
                out.append(tok)
                i = m.end()
                stack.append(("str", quote + hashes, "\\" + hashes, quote == '"""'))
        else:
            _, close, escape, multiline = top
            found = [
                (pos, kind)
                for pos, kind in (
                    (src.find(escape, i), "escape"),
                    (src.find(close, i), "close"),
                    (-1 if multiline else src.find("\n", i), "newline"),
                )
                if pos != -1
            ]
            if not found:
                out.append(src[i:])
                break
            pos, kind = min(found)
            if kind == "escape":
                k = pos + len(escape)
                out.append(src[i:k + 1])
                i = k + 1
                if src[k:k + 1] == "(":
                    stack.append(["code", 1])
            elif kind == "close":
                out.append(src[i:pos + len(close)])
                i = pos + len(close)
                stack.pop()
            else:
                out.append(src[i:pos])
                i = pos
                stack.pop()
    return "".join(out)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _code(path: Path) -> str:
    assert path.exists(), f"{path} moved — update this guard, do not delete it"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _matching(src: str, start: int, open_ch: str, close_ch: str, what: str) -> str:
    depth = 0
    for i in range(start, len(src)):
        if src[i] == open_ch:
            depth += 1
        elif src[i] == close_ch:
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    pytest.fail(f"unbalanced {open_ch}{close_ch} after {what!r}")


def _decl_block(src: str, prefix: str) -> str:
    """The brace-bound body of the first declaration starting with `prefix`."""
    at = src.find(prefix)
    assert at != -1, f"{prefix!r} not found — this scan has drifted"
    return _matching(src, src.index("{", at), "{", "}", prefix)


def _call_args(src: str, at: int, what: str) -> str:
    return _matching(src, src.index("(", at), "(", ")", what)


def _arm(vm: str, case: str, next_case: str) -> str:
    """One `case "…":` arm of the SSE switch, up to the next arm."""
    start = vm.index(f'case "{case}":')
    return vm[start:vm.index(f'case "{next_case}":', start)]


_SWIFT_LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"')


def _chat_source() -> str:
    return _decl_block(_code(_MODELS), "struct ChatSource")


def _chat_thinking() -> str:
    return _decl_block(_code(_MODELS), "struct ChatThinking")


# ── 1. the wire model: total decode, ids, links ──────────────────────────────

def test_chat_source_decodes_every_web_field_totally():
    """One malformed pill must never blank a conversation: array decoding is all-or-nothing, so
    a THROWING element decode collapses the whole `[ChatMessageDTO]` history."""
    src = _chat_source()
    keys = _norm(_decl_block(src, "private enum CodingKeys"))
    assert "case label, detail, kind, title, url" in keys
    assert 'case publishedAt = "published_at"' in keys, "the date's snake_case wire key"

    init = _decl_block(src, "init(from decoder: Decoder) throws")
    flat = _norm(init)
    assert "guard let c = try? decoder.container(keyedBy: CodingKeys.self) else {" in flat
    for field in ("kind", "title", "url", "publishedAt"):
        assert f"self.{field} = try? c.decode(String.self, forKey: .{field})" in flat, (
            f"{field} must decode with try? — a wrong type degrades the field, never the history"
        )
    assert "try c." not in init, "a throwing decode in a total decoder"
    else_branch = init[: init.index("return")]
    for field in ("kind", "title", "url", "publishedAt"):
        assert f"self.{field} = nil" in else_branch, f"the non-object branch leaves {field} unset"
    for field in ("kind", "title", "url", "publishedAt"):
        assert re.search(rf"\blet {field}: String\?", src), f"{field} must be an optional String"


def test_web_source_id_keys_on_the_url_and_grounding_ids_are_unchanged():
    body = _decl_block(_chat_source(), "var id: String")
    flat = _norm(body)
    assert 'if isWeb, let link = url?.trimmingCharacters(in: .whitespacesAndNewlines), !link.isEmpty {' in flat
    assert 'return "web|" + link' in flat
    assert 'return label + "|" + (detail ?? "")' in flat, (
        "grounding ids must stay `label|detail` — every stored row already has that id"
    )
    assert flat.index('"web|"') < flat.index('return label'), "the web branch comes first"


def test_web_url_admits_https_with_a_real_host_only():
    """The address is a third party's. Only https, a public dotted host, no user info (which
    disguises the real host: `https://reuters.com@evil.example`), no port."""
    body = _decl_block(_chat_source(), "var webURL: URL?")
    assert set(_SWIFT_LITERAL.findall(body)) == {"https"}, (
        f"webURL may compare the scheme against \"https\" only, found {_SWIFT_LITERAL.findall(body)}"
    )
    flat = _norm(body)
    for clause in (
        "guard isWeb,",
        "raw.count <= Self.maxURLLength",
        "let parts = URLComponents(string: raw)",
        'parts.scheme?.lowercased() == "https"',
        "let host = parts.host, Self.isPublicHostName(host)",
        "parts.user == nil, parts.password == nil, parts.port == nil",
        "let resolved = parts.url",
    ):
        assert clause in flat, f"webURL lost `{clause}`"
    assert "URL(string" not in body, "parse with URLComponents (URL.host/user are deprecated)"

    host = _norm(_decl_block(_chat_source(), "private static func isPublicHostName(_ host: String) -> Bool"))
    for clause in ('name.contains(".")', '!name.contains(":")', '!name.hasSuffix(".local")',
                   '!name.hasSuffix(".localhost")', '!name.hasSuffix(".internal")',
                   "topLevel.contains(where: { $0.isLetter })"):
        assert clause in host, f"isPublicHostName lost `{clause}` (IP literal / localhost guard)"


def test_publisher_date_and_spoken_label():
    src = _chat_source()
    publisher = _norm(_decl_block(src, "var webPublisherName: String?"))
    assert "guard isWeb else { return nil }" in publisher
    assert "Self.oneLine(detail, cap: 48)" in publisher and "webHost.map" in publisher

    host = _norm(_decl_block(src, "var webHost: String?"))
    assert "guard let link = webURL," in host, "the host comes only from a validated link"
    assert 'host.hasPrefix("www.")' in host

    spoken = _norm(_decl_block(src, "var webAccessibilityLabel: String"))
    assert '"Web source"' in spoken and "webPublisherName" in spoken and "publishedDisplay" in spoken
    assert "webHost" in spoken, "VoiceOver must hear the destination host, not only a name"
    assert "Self.oneLine(title, cap: 160)" in spoken


def test_published_date_is_parsed_in_a_fixed_zone():
    """A date-only value read in the device zone lands a day early west of UTC, and a
    Buddhist/Japanese-calendar phone prints another year."""
    enum = _decl_block(_code(_MODELS), "private enum ChatSourceDate")
    assert enum.count("DateFormatter()") == 2
    assert enum.count('Locale(identifier: "en_US_POSIX")') == 2
    assert enum.count('TimeZone(identifier: "UTC")') == 2
    assert enum.count("Calendar(identifier: .gregorian)") == 2
    parser = _decl_block(enum, "static let parser: DateFormatter")
    assert '"yyyy-MM-dd"' in parser and "f.isLenient = false" in parser
    display = _norm(_decl_block(enum, "static func display(_ raw: String?) -> String?"))
    assert "value.count == 10" in display, "only a bare calendar date, never a timestamp"
    assert _norm(_decl_block(_chat_source(), "var publishedDisplay: String?")) == (
        "{ ChatSourceDate.display(publishedAt) }"
    )


def test_one_sanitizer_on_every_entry_path():
    """History, the non-stream reply and `done` all go through `toRichChatMessage`; the live
    `sources` frame goes through `decodeSources`. Both must call the SAME sanitizer, or a live
    pill can be one a reload drops (and the live path never dropped an empty label at all)."""
    models = _code(_MODELS)
    convert = _decl_block(_decl_block(models, "struct ChatMessageDTO"), "func toRichChatMessage()")
    assert "sources: ChatSource.sanitized(sources)" in _norm(convert)
    assert ".filter" not in convert, "the old empty-label-only filter is back"

    decode = _decl_block(_code(_VM), "private static func decodeSources(_ json: String)")
    assert "ChatSource.sanitized(" in decode

    body = _norm(_decl_block(_chat_source(), "static func sanitized(_ raw: [ChatSource]?) -> [ChatSource]?"))
    for clause in (
        "guard let raw else { return nil }",
        "if kept.count >= maxPills { break }",
        "if source.label.isEmpty { continue }",
        "if source.isWeb && source.webPublisherName == nil { continue }",
        "guard seen.insert(source.id).inserted else { continue }",
        "kept.append(source)",
    ):
        assert clause in body, f"sanitized lost `{clause}`"


# ── 2. the thinking state ────────────────────────────────────────────────────

def test_chat_thinking_decodes_web_searched_strictly():
    src = _chat_thinking()
    assert 'case webSearched = "web_searched"' in _norm(_decl_block(src, "enum CodingKeys"))
    assert re.search(r"\blet webSearchState: WebSearchState\?", src)
    states = _norm(_decl_block(src, "enum WebSearchState"))
    for case in ("case searching", "case done", "case skipped"):
        assert case in states

    init = _decl_block(src, "init(from decoder: Decoder) throws")
    flat = _norm(init)
    assert (
        "self.webSearchState = (try? c.decode(Bool.self, forKey: .webSearched)) == true "
        "? WebSearchState.done : nil"
    ) in flat, "only a real `true` is a web-searched turn (absent / false / wrong type → nil)"
    assert "self.webSearchState = nil" in init[: init.index("return")], "the non-object branch"
    assert "try c." not in init

    encode = _norm(_decl_block(src, "func encode(to encoder: Encoder) throws"))
    assert "if webSearchState == .done { try c.encode(true, forKey: .webSearched) }" in encode
    for key in ("stages", "sourceCount", "elapsedMs", "reasoning"):
        assert f"forKey: .{key})" in encode, f"the hand-written encode dropped {key}"

    display = _norm(_decl_block(src, "var shouldDisplay: Bool"))
    assert "|| webSearchState == .done" in display, (
        "a web-searched answer always shows its card — the badge is its only marker"
    )


def test_every_thinking_rebuild_keeps_the_web_search_state():
    """Each frame patches ONE field of `ChatThinking` by rebuilding it. A rebuild that forgets
    `webSearchState:` silently resets the status — the reasoning paragraph was lost exactly
    this way once (see `appendThinkingStage`). The bubble's creation is the one exemption: it
    has nothing to carry."""
    vm = _code(_VM)
    calls = [_call_args(vm, m.start(), "ChatThinking(") for m in re.finditer(r"\bChatThinking\(", vm)]
    rebuilds = [c for c in calls if "reasoning:" in c]
    assert len(rebuilds) >= 4, f"expected ≥4 ChatThinking rebuilds in the ViewModel, found {len(rebuilds)}"
    missing = [_norm(c) for c in rebuilds if "webSearchState:" not in c]
    assert not missing, f"these rebuilds reset the web-search state: {missing}"
    exempt = [c for c in calls if "reasoning:" not in c]
    assert all("elapsedMs: nil" in c for c in exempt), (
        f"only the new bubble may omit the state: {[_norm(c) for c in exempt]}"
    )


def test_the_viewmodel_drives_the_state_from_the_frames():
    from app.services.agents.chat_tools import WEB_SEARCH_TOOL

    vm = _code(_VM)
    start = _norm(_arm(vm, "tool_start", "tool_step"))
    assert f'start.name == "{WEB_SEARCH_TOOL}"' in start, "the arm must match the backend's tool name"
    assert "setWebSearchState(id: ensureBubble(), state: .searching)" in start

    step = _norm(_arm(vm, "tool_step", "grounding"))
    assert "guard let step = Self.decodeToolFrame(event.data) else { continue }" in step
    assert f'if step.name == "{WEB_SEARCH_TOOL}" {{' in step
    assert "let settled: ChatThinking.WebSearchState = step.skipped ? .skipped : .done" in step
    assert "setWebSearchState(id: ensureBubble(), state: settled)" in step
    skip = step.index("if step.skipped { continue }")
    assert skip < step.index("appendThinkingStage("), "a skipped step must claim no progress"

    token = _norm(_arm(vm, "token", "reset"))
    assert "if webSearchState(id: id) == .searching { setWebSearchState(id: id, state: nil) }" in token
    reset = _norm(_arm(vm, "reset", "done"))
    assert "setWebSearchState(id: id, state: nil)" in reset

    frame = _norm(_decl_block(vm, "private static func decodeToolFrame(_ json: String)"))
    assert "skipped = try? c.decode(Bool.self, forKey: .skipped)" in frame, (
        "a wrong-typed flag must degrade to false, not drop the whole step"
    )
    assert "name = try? c.decode(String.self, forKey: .name)" in frame
    assert "let name = frame.name, !name.isEmpty" in frame
    assert "return (name: name, skipped: frame.skipped == true)" in frame

    patch = _norm(_decl_block(vm, "private func setWebSearchState(id: UUID, state: ChatThinking.WebSearchState?)"))
    assert "reasoning: t?.reasoning, webSearchState: state" in patch


# ── 3. the card ──────────────────────────────────────────────────────────────

def test_the_header_says_searching_the_web_and_badges_a_web_answer():
    card = _decl_block(_code(_CARD), "struct ThinkingProcessCard")
    searching = _norm(_decl_block(card, "private var isSearchingWeb: Bool"))
    assert searching == "{ thinking.isActive && thinking.webSearchState == .searching }"
    badge = _norm(_decl_block(card, "private var showsWebSearchBadge: Bool"))
    assert badge == "{ !thinking.isActive && thinking.webSearchState == .done }"

    header_text = _norm(_decl_block(card, "private var headerText: String"))
    status = header_text.index('if isSearchingWeb { return "Searching the web…" }')
    assert status < header_text.index('"Thinking…"'), "the search status outranks \"Thinking…\""

    header = _decl_block(card, "private var header: some View")
    flat = _norm(header)
    assert 'if isSearchingWeb { Image(systemName: "globe")' in flat
    assert "if showsWebSearchBadge { webSearchBadge }" in flat

    pill = _norm(_decl_block(card, "private var webSearchBadge: some View"))
    assert 'text: "Web search"' in pill and 'systemImage: "globe"' in pill
    assert "color: AppColors.primaryBlue" in pill, "a text-role ink (TintedTagBadge draws the label in it)"

    count = _norm(_decl_block(card, "private var visibleSourceCount: Int"))
    assert count == "{ sources.isEmpty ? (thinking.sourceCount ?? 0) : sources.count }", (
        "count the pills on screen — the stored source_count excludes the live web pills"
    )


def test_grounding_pill_is_unchanged_and_not_a_button():
    card = _decl_block(_code(_CARD), "struct ThinkingProcessCard")
    grounding = _decl_block(card, "private func sourcePill(_ source: ChatSource)")
    assert 'Image(systemName: "doc.text.magnifyingglass")' in grounding
    assert "Button" not in grounding and "onOpenSource" not in grounding
    body = _norm(_decl_block(card, "private var expandedBody: some View"))
    assert (
        "ForEach(sources) { source in if source.isWeb { webSourcePill(source) } "
        "else { sourcePill(source) } }"
    ) in body


def test_web_pill_is_a_link_button_into_the_in_app_browser():
    code = _code(_CARD)
    card = _decl_block(code, "struct ThinkingProcessCard")
    assert re.search(r"var onOpenSource: \(\(URL\) -> Void\)\? = nil", card)

    pill = _decl_block(card, "private func webSourcePill(_ source: ChatSource)")
    flat = _norm(pill)
    assert "if let url = source.webURL, let onOpenSource {" in flat, (
        "tappable only with a validated link AND a handler — never a dead button"
    )
    link = flat[flat.index("if let url"):flat.index("} else {")]
    for clause in ("Button { onOpenSource(url) }", ".buttonStyle(.plain)",
                   ".accessibilityLabel(source.webAccessibilityLabel)", ".accessibilityHint(",
                   ".accessibilityAddTraits(.isLink)", "webPillLabel(source, isLink: true)"):
        assert clause in link, f"the link branch lost `{clause}`"
    assert ".accessibilityElement(children: .ignore)" not in link, (
        "on a Button it can drop the button trait — label + .isLink directly"
    )
    plain = flat[flat.index("} else {"):]
    assert "webPillLabel(source, isLink: false)" in plain and "Button" not in plain

    label = _decl_block(card, "private func webPillLabel(_ source: ChatSource, isLink: Bool)")
    assert 'Image(systemName: "globe")' in label
    assert 'if isLink { Image(systemName: "arrow.up.right")' in _norm(label)
    assert ".foregroundColor(AppColors.primaryBlue)" in label
    assert "Graphic" not in label and "Fill" not in label, "pill text must use a text-role token"

    for forbidden in ("UIApplication", "openURL", "Link(", ".sheet(", ".fullScreenCover(",
                      ".inAppBrowser(", "openInSystem", "openExternal"):
        assert forbidden not in code, f"the card must not present or open anything itself: {forbidden}"


def test_the_open_source_handler_reaches_the_card():
    """Each hop is a defaulted parameter, so a dropped hop still compiles — and renders every
    web pill as a plain label (or, worse, a button whose handler is nil one level down)."""
    lst = _code(_LIST)
    messages_list = _decl_block(lst, "struct ChatMessagesList")
    assert re.search(r"var onOpenSource: \(\(URL\) -> Void\)\? = nil", messages_list)
    at = messages_list.index("ChatMessageRow(")
    assert "onOpenSource: onOpenSource" in _call_args(messages_list, at, "ChatMessageRow(")

    row = _decl_block(lst, "struct ChatMessageRow")
    assert re.search(r"var onOpenSource: \(\(URL\) -> Void\)\? = nil", row)
    at = row.index("AIMessageContent(")
    assert "onOpenSource: onOpenSource" in _call_args(row, at, "AIMessageContent(")

    content = _decl_block(_code(_CONTENT), "struct AIMessageContent")
    assert re.search(r"var onOpenSource: \(\(URL\) -> Void\)\? = nil", content)
    at = content.index("ThinkingProcessCard(")
    assert "onOpenSource: onOpenSource" in _call_args(content, at, "ThinkingProcessCard(")

    screen = _code(_SCREEN)
    area = _decl_block(screen, "private var conversationArea: some View")
    at = area.index("ChatMessagesList(")
    assert "onOpenSource: openWebSource" in _call_args(area, at, "ChatMessagesList(")


def test_the_chat_screen_opens_web_sources_in_app_only_in_a_sheet():
    screen = _code(_SCREEN)
    decl = _decl_block(screen, "struct AIChatScreen: View")
    assert "@State private var browserLink: BrowserLink?" in decl, (
        "@State, not ViewModel state — the ViewModel outlives the cover and would re-present"
    )
    body = _decl_block(decl, "var body: some View")
    assert body.count(".inAppBrowser(") == 1
    assert ".inAppBrowser(link: $browserLink, style: .sheet)" in body, (
        "a SHEET: a nested cover unmounts the chat and re-runs its onAppear (a history reload)"
    )

    opener = _norm(_decl_block(decl, "private func openWebSource(_ url: URL)"))
    assert opener.index("guard SafariView.canOpen(url) else {") < opener.index(
        "browserLink = BrowserLink(url)"
    )
    assert "reportMutationFailure" in opener, "a refused tap is reported, never silent"
    for forbidden in ("openInSystem", "openExternal", "UIApplication"):
        assert forbidden not in opener, (
            f"web addresses are third-party: in-app only, never {forbidden} "
            "(`caydex://` is this app's own sign-in scheme)"
        )


def test_the_browser_modifier_offers_a_sheet_and_keeps_the_cover_default():
    code = _code(_BROWSER)
    styles = _norm(_decl_block(code, "enum InAppBrowserStyle"))
    assert "case cover" in styles and "case sheet" in styles

    modifier = _decl_block(code, "private struct InAppBrowserModifier: ViewModifier")
    flat = _norm(modifier)
    assert "case .cover: content .fullScreenCover(item: $link) { target in SafariView(url: target.url)" in flat
    assert "case .sheet: content .sheet(item: $link) { target in SafariView(url: target.url)" in flat
    assert "isPresented:" not in modifier, "item-based only: the form that works inside a cover"

    ext = _norm(_decl_block(code, "extension View"))
    assert (
        "func inAppBrowser(link: Binding<BrowserLink?>, style: InAppBrowserStyle = .cover) -> some View"
    ) in ext, "the default must stay the cover, so every existing call site is unchanged"
    assert "InAppBrowserModifier(link: link, style: style)" in ext


# ── 4. the backend↔iOS contract ──────────────────────────────────────────────

def _ios_wire_keys() -> set:
    keys = _decl_block(_chat_source(), "private enum CodingKeys")
    wire = set()
    for case in re.findall(r"case\s+([^\n]+)", keys):
        for part in case.split(","):
            part = part.strip()
            m = re.fullmatch(r'(\w+)\s*=\s*"([^"]+)"', part)
            wire.add(m.group(2) if m else part)
    return wire


def test_the_backend_web_pill_keys_are_what_ios_decodes():
    """Run the backend's real pill builder over fake search rows (no network) and check every
    key it emits is one the iOS decoder maps — a renamed key decodes as nil forever, silently.
    Outliers: a non-https link and a repeated host produce no extra pill."""
    from app.services import chat_web_search_service as svc

    wire = _ios_wire_keys()
    assert wire == {"label", "detail", "kind", "title", "url", "published_at"}, wire

    raw = {"results": [
        {"title": "Company outlines its product roadmap", "url": "https://www.reuters.com/technology/roadmap",
         "description": "A report on the roadmap.", "page_age": "2026-10-01T08:00:00"},
        {"title": "Industry overview", "url": "https://news.example.org/industry",
         "description": "An overview of the industry."},
        {"title": "Same host again", "url": "https://www.reuters.com/business/other",
         "description": "Another article."},
        {"title": "Not a link", "url": "javascript:alert(1)", "description": "x"},
        {"title": "Plain http", "url": "http://insecure.example.net/a", "description": "x"},
        "not an object",
    ]}
    outcome = svc._digest(raw, "company roadmap", collections.defaultdict(int))
    pills = outcome.pills
    assert len(pills) == 2, f"expected the two distinct https hosts only, got {pills}"
    for pill in pills:
        assert set(pill) <= wire, f"the backend emits keys iOS never decodes: {set(pill) - wire}"
        assert pill["kind"] == "web" and pill["label"] == "Web"
        assert isinstance(pill["detail"], str) and pill["detail"]
        assert pill["url"].startswith("https://")
        assert pill["published_at"] is None or re.fullmatch(r"\d{4}-\d{2}-\d{2}", pill["published_at"])
    assert pills[0]["detail"] == "Reuters" and pills[0]["published_at"] == "2026-10-01"
    assert pills[1]["detail"] == "news.example.org", "an unknown outlet shows its bare host"


def test_the_web_search_tool_name_matches_the_ios_label_case():
    from app.services.agents.chat_tools import WEB_SEARCH_TOOL

    label = _decl_block(_code(_VM), "static func thinkingLabel(forTool name: String)")
    assert f'case "{WEB_SEARCH_TOOL}":' in label


# ── 5. anti-vacuity ──────────────────────────────────────────────────────────

def test_the_comment_stripper_strips_and_keeps_literals():
    sample = 'let a = "https://x" // trailing web_search\n/* block /* nested */ */ let b = 1\n'
    out = _strip_comments(sample)
    assert '"https://x"' in out, "a // inside a literal is not a comment"
    assert "web_search" not in out and "nested" not in out
    assert out.count("\n") == sample.count("\n"), "line count preserved"
    raw = _CARD.read_text(encoding="utf-8")
    assert "⚠️ `.opacity` ALONE" in raw and "⚠️ `.opacity` ALONE" not in _code(_CARD)


@pytest.mark.parametrize("path,prefix,minimum", [
    (_MODELS, "struct ChatSource", 2000),
    (_MODELS, "struct ChatThinking", 1000),
    (_CARD, "private func webSourcePill(_ source: ChatSource)", 300),
    (_CARD, "private var header: some View", 300),
    (_VM, "private static func decodeToolFrame(_ json: String)", 300),
    (_SCREEN, "private func openWebSource(_ url: URL)", 150),
    (_BROWSER, "private struct InAppBrowserModifier: ViewModifier", 300),
])
def test_the_scanned_blocks_are_real_code(path, prefix, minimum):
    block = _decl_block(_code(path), prefix)
    assert len(block) > minimum, f"{prefix!r} is only {len(block)} chars — the scan found a stub"
