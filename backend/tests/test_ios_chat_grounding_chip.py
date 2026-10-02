"""The "Grounded on Research Report · AAPL" chip must follow the SERVER's verdict.

The chip used to key on the chat's context TYPE alone, so a report chat whose report could
not be found — the direct door with no report id, once the close-aligned
`ticker_report_cache` rolls over at the next weekday 18:00 ET close — ran ungrounded while
the chip still claimed the report. The backend now sends its verdict as `context_grounded`
(`chat_service.context_grounding_verdict`: True only when the resolver BUILT the block) on an
early `grounding` SSE frame, on the `done` message, on the non-stream reply and on every
history row. These scans pin the iOS half, which regresses silently: drop one link and the
chip goes back to claiming a report the answer never saw, with nothing failing.

Comment-stripped and brace-bound, per testing.md — the explanatory comments beside each
change contain every token asserted below.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_MODELS = _IOS / "Models/ChatConversationModels.swift"
_VM = _IOS / "ViewModels/ChatViewModel.swift"
_SCREEN = _IOS / "Views/Screens/AIChatScreen.swift"
_CHIP = _IOS / "Views/Molecules/GroundedContextChip.swift"


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^[ \t]*//.*$", "", src, flags=re.M)


def _code(path: Path) -> str:
    assert path.exists(), f"{path} moved — update this guard, do not delete it"
    return _strip_comments(path.read_text(encoding="utf-8"))


def _block_at(src: str, at: int, what: str) -> str:
    start = src.index("{", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    pytest.fail(f"unbalanced braces after {what!r}")


def _decl_block(src: str, prefix: str) -> str:
    at = src.find(prefix)
    assert at != -1, f"{prefix!r} not found — this scan has drifted"
    return _block_at(src, at, prefix)


def _call_args(src: str, at: int) -> str:
    """The parenthesised argument list of the call whose name starts at `at`."""
    start = src.index("(", at)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    pytest.fail("unbalanced parens in a GroundedContextChip call")


def _switch_arm(src: str, label: str) -> str:
    """One `case "<label>":` arm of the SSE switch, up to the next `case "…":`."""
    at = src.find(f'case "{label}":')
    assert at != -1, f'the `case "{label}":` stream arm is missing'
    nxt = re.compile(r'\n\s*case "[a-z_]+":').search(src, at + 1)
    return src[at:nxt.start() if nxt else len(src)]


# ── the wire model ───────────────────────────────────────────────────────────

def test_the_dto_decodes_the_verdict_as_optional_bool():
    """Optional, so an old backend (and every non-report turn, which sends null) decodes
    unchanged — the crash-on-decode class the parity tests exist for."""
    dto = _decl_block(_code(_MODELS), "struct ChatMessageDTO")
    assert re.search(r"\blet contextGrounded: Bool\?", dto), "ChatMessageDTO.contextGrounded must be `Bool?`"
    keys = _decl_block(dto, "enum CodingKeys")
    assert re.search(r'case contextGrounded = "context_grounded"', keys), (
        "the snake_case CodingKey is missing — the verdict would never decode"
    )


def test_the_history_verdict_reads_the_newest_assistant_row_that_has_one():
    dto = _decl_block(_code(_MODELS), "struct ChatMessageDTO")
    fn = _decl_block(dto, "static func latestGroundingVerdict(in messages: [ChatMessageDTO]) -> Bool?")
    assert "messages.last(where:" in fn, "the NEWEST verdict, not the first"
    assert '$0.role == "assistant"' in fn and "$0.contextGrounded != nil" in fn


def test_the_softened_copy_never_claims_grounding():
    ctx = _decl_block(_code(_MODELS), "enum ChatContextType")
    label = _decl_block(ctx, "var groundingUnavailableLabel: String")
    assert "Grounded on" not in label
    m = re.search(r"case \.tickerReport: return \"([^\"]+)\"", label)
    assert m and "not available" in m.group(1) and "generally" in m.group(1), label


# ── the view model ───────────────────────────────────────────────────────────

def test_the_view_model_publishes_the_verdict():
    vm = _code(_VM)
    assert re.search(r"@Published private\(set\) var contextGrounded: Bool\?", vm)


def test_every_context_assignment_resets_or_replaces_the_verdict():
    """A verdict is about ONE chat's grounding. Every function that (re)assigns the context
    type must (re)assign the verdict too, or a new report chat opens wearing the previous
    chat's "not available" notice — or, worse, an ungrounded chat keeps a stale True."""
    vm = _code(_VM)
    checked = []
    for m in re.finditer(r"\bfunc (\w+)\(", vm):
        body = _block_at(vm, m.start(), m.group(1))
        n_ctx = len(re.findall(r"\bcurrentContextType\s*=(?!=)", body))
        if not n_ctx:
            continue
        n_verdict = len(re.findall(r"\bcontextGrounded\s*=(?!=)", body))
        assert n_verdict >= n_ctx, (
            f"{m.group(1)} assigns currentContextType {n_ctx}x but contextGrounded {n_verdict}x"
        )
        checked.append(m.group(1))
    # Anti-vacuity: the four places that own a chat's identity today.
    for name in ("prepareGroundedConversation", "startNewConversation",
                 "loadConversation", "resetConversation"):
        assert name in checked, f"{name} no longer assigns the context type — re-check this guard"


def test_the_load_takes_the_verdict_from_history():
    body = _decl_block(_code(_VM), "func loadConversation(sessionId: String)")
    assert re.search(
        r"contextGrounded = ChatMessageDTO\.latestGroundingVerdict\(in: history\.messages\)", body
    )


def test_a_missing_verdict_never_clears_a_known_one():
    """nil = an old server or a type with no verdict. If it cleared the state, the softened
    chip would flip back to "Grounded on Research Report" on the next turn's `done`."""
    fn = _decl_block(_code(_VM), "private func adoptGroundingVerdict(_ verdict: Bool?)")
    assert re.search(r"guard let verdict else \{ return \}", fn)
    assert re.search(r"\bcontextGrounded = verdict\b", fn)
    assert not re.search(r"contextGrounded = nil", fn)


def test_the_grounding_frame_is_decoded_and_adopted():
    vm = _code(_VM)
    arm = _switch_arm(vm, "grounding")
    assert re.search(r"adoptGroundingVerdict\(Self\.decodeGroundingVerdict\(event\.data\)\)", arm)
    assert "ensureBubble" not in arm, "a chip event must not open an empty assistant bubble"
    dec = _decl_block(vm, "private static func decodeGroundingVerdict(_ json: String) -> Bool?")
    assert 'case contextGrounded = "context_grounded"' in dec
    assert "try?" in dec, "a malformed frame is no verdict, never a thrown stream error"


def test_the_done_frame_adopts_the_persisted_verdict():
    """The fallback re-resolves, so `done` can differ from the early frame; it is final."""
    arm = _switch_arm(_code(_VM), "done")
    assert re.search(r"adoptGroundingVerdict\(dto\.contextGrounded\)", arm)


def test_the_non_stream_reply_adopts_the_verdict():
    body = _decl_block(_code(_VM), "private func sendMessageToSession(")
    assert re.search(r"adoptGroundingVerdict\(response\.contextGrounded\)", body)


def test_every_history_adoption_carries_the_verdict():
    """Three places replace `messages` from history (load, and the two reconcile paths);
    each must take the verdict with it."""
    vm = _code(_VM)
    adoptions = len(re.findall(r"messages = history\.messages\.map", vm))
    verdicts = len(re.findall(r"latestGroundingVerdict\(in: history\.messages\)", vm))
    assert adoptions >= 3, "a history adoption moved — re-check this guard"
    assert verdicts >= adoptions, f"{adoptions} history adoptions but {verdicts} verdict reads"


# ── the screen and the chip ──────────────────────────────────────────────────

def test_the_screen_passes_the_verdict_to_its_only_chip():
    screen = _code(_SCREEN)
    content = _decl_block(screen, "private var chatContent: some View")
    calls = [m.start() for m in re.finditer(r"\bGroundedContextChip\(", content)]
    assert len(calls) == 1, "one chip, under the top bar"
    assert re.search(r"groundingArrived:\s*viewModel\.contextGrounded", _call_args(content, calls[0]))
    assert len(re.findall(r"\bGroundedContextChip\(", screen)) == 1, "a second, unguarded chip"


def test_no_other_screen_renders_the_chip_without_a_verdict():
    for path in (_IOS / "Views").rglob("*.swift"):
        if path == _CHIP:
            continue
        src = _code(path)
        for m in re.finditer(r"\bGroundedContextChip\(", src):
            assert "groundingArrived:" in _call_args(src, m.start()), (
                f"{path.relative_to(_REPO)} renders the chip without the server's verdict"
            )


def test_the_chip_softens_only_on_an_explicit_false():
    """`== false`, never `!= true`: nil is an old server or a type with no verdict, and must
    render exactly as before."""
    chip = _decl_block(_code(_CHIP), "struct GroundedContextChip")
    assert re.search(r"\bvar groundingArrived: Bool\? = nil\b", chip), "every other caller defaults to no verdict"
    assert re.search(r"var groundingUnavailable: Bool \{ groundingArrived == false \}", chip)
    label = _decl_block(chip, "private var labelText: String")
    soft = label.find("if groundingUnavailable")
    assert soft != -1, "labelText no longer branches on the verdict"
    branch = _block_at(label, soft, "if groundingUnavailable")
    assert "return contextType.groundingUnavailableLabel" in branch
    assert "Grounded on" not in branch
    assert soft < label.find("Grounded on"), "the softened branch must win before any claim"
