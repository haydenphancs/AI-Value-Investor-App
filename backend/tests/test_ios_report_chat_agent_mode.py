"""Report chat as "Cay AI · Growth Hunter Agent" — the iOS half, pinned from the Swift source.

Owner decision (2026-10-02): a report chat names its analysis style as a MODE of Cay AI —
"Cay AI · Growth Hunter Agent" — in the grounding chip ONLY, while the server gives the answers
that method's tone. Cay AI is always the speaker; no real investor is named. There is
deliberately no greeting card (owner, same day: "the chip is enough").

What can regress silently on iOS, and what each block below pins:

* ONE agent-tag table. `ReportAgentPersona(agentTag:)` is the only place a report's `agent`
  tag ("lynch", legacy "dalio") becomes a persona; it must equal the backend's
  `AGENT_TAG_TO_KEY` ∪ `LEGACY_AGENT_TAGS`, or the badge, the chat label and the server's voice
  name different methods for one report.
* A CLOSED resolver. The chat's persona comes from the reference's second segment, matched
  only against the hard-coded `allCases`, with no default — an unknown token shows no mode
  rather than borrowing one (the backend's grounding parse DOES default; this must not).
* The copy. Name-free, no catchphrase, no buy/sell/hold, no "your agent"; the five approved
  "<Style> Agent" labels.
* The wiring. One `reportAgentMode` on the screen feeds the chip, its only surface; no greeting
  card or greeting row comes back; the softened "not available" chip still wins over the mode;
  the report screen builds its reference from the report ON SCREEN, not from the route's
  persona (which defaults to warren_buffett).

testing.md §3: comment-stripped (string-aware, trailing `//` comments too, line count
preserved), brace-bound to the declaration, wiring pinned as exact normalized STATEMENTS (a
substring check passed with the behaviour broken: `_ = pattern; return symbol`, `|| true`, a
fallback after `??`), and every guard was mutation-tested once by hand (see the final report of
the change).

Known limitations (accepted, recorded here rather than tuned away):

* The reference table below is REFERENCE-PARSE parity only. The server's voice is
  `resolve_voice_key(grounded, reference)`, where the persona of the report it actually
  grounds on WINS. Every reference this build sends carries the on-screen report's own persona,
  so the two agree; a history reference from an older build can still diverge:
  - a wrong default segment (`AAPL|warren_buffett` while the report id points to a Growth
    report) labels the wrong style in the chip (review #13);
  - an empty or unknown segment (`AAPL`, `AAPL||peter_lynch`, `AAPL|soros|<id>`) shows NO
    mode here, while the server still grounds a report (by its id, or via the resolver's
    default-persona cache lookup) and speaks that report's voice — voice without a label.
  The full fix is the server returning the voice key it chose; until then, "no label" is the
  safe direction (a label never names a method the reference does not).
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

import pytest

import _persona_name_guard as guard
from app.services.agents.persona_config import (
    _MAX_TAG_LEN,
    _PERSONA_REGISTRY,
    AGENT_TAG_TO_KEY,
    LEGACY_AGENT_TAGS,
    PERSONA_KEYS,
    get_persona_config,
)
from app.services.agents.report_voice_prompt import (
    _MAX_REFERENCE_LEN,
    report_persona_key,
    resolve_voice_key,
)
from app.services.agents.ticker_report_data_collector import _AGENT_MAP
from test_persona_display_order import _ios_all_cases_keys

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend/ios/ios"
_REPORT_MODELS = _IOS / "Models/TickerReportModels.swift"
_REPORT_RESPONSE = _IOS / "Models/TickerReportResponse.swift"
_RESEARCH_MODELS = _IOS / "Models/ResearchModels.swift"
_CHAT_MODELS = _IOS / "Models/ChatConversationModels.swift"
# Deleted 2026-10-02 (owner: "the chip is enough"); pinned ABSENT below.
_REMOVED_GREETING = _IOS / "Views/Molecules/ReportChatAgentGreeting.swift"
_CHIP = _IOS / "Views/Molecules/GroundedContextChip.swift"
_LIST = _IOS / "Views/Organisms/ChatMessagesList.swift"
_SCREEN = _IOS / "Views/Screens/AIChatScreen.swift"
_REPORT_VIEW = _IOS / "Views/Screens/TickerReportView.swift"
_REPORT_VM = _IOS / "ViewModels/TickerReportViewModel.swift"
_CHAT_VM = _IOS / "ViewModels/ChatViewModel.swift"

# The approved chip labels. Byte-exact: a reworded label is a decision, made here.
_APPROVED_LABELS = {
    "warren_buffett": "Quality Compounder Agent",
    "cathie_wood": "Disruption Seeker Agent",
    "peter_lynch": "Growth Hunter Agent",
    "bill_ackman": "Activist Concentrator Agent",
    "michael_burry": "Deep Value Skeptic Agent",
}


# ── scanning helpers ─────────────────────────────────────────────────────────

_CODE_TOKEN = re.compile(r'//|/\*|(#*)("""|")|[()]')
_BLOCK_TOKEN = re.compile(r"/\*|\*/")


@functools.lru_cache(maxsize=None)
def _strip_comments(src: str) -> str:
    """Every Swift comment removed, LINE COUNT PRESERVED (so a failure's position still points
    at the real line).

    String-aware, because a regex stripper is wrong in both directions: a whole-line-only
    pattern kept a TRAILING comment (`let base = "…|warren_buffett" // report.agent.personaKey`
    passed a guard on the comment's tokens), and a naive `//.*` would eat code after the `//`
    inside "https://…". So: `//` and `/* */` count only outside a literal; block comments NEST,
    as Swift's do; literals are "…", \"\"\"…\"\"\" and raw #"…"# (whose `\\` is not an escape), and
    an interpolation `\\( … )` is code again, which may hold literals of its own.
    """
    out: list = []
    i, n = 0, len(src)
    # Open frames: ["code", paren_depth] for an interpolation, ("str", close, escape, multiline).
    stack: list = []
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
                i = n if j == -1 else j                      # the newline itself is kept
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
                        stack.pop()                          # back inside the literal
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
            else:                                            # unterminated: recover at EOL
                out.append(src[i:pos])
                i = pos
                stack.pop()
    return "".join(out)


def _norm(text: str) -> str:
    """Whitespace runs → one space: a statement compared as written, whatever its wrapping."""
    return re.sub(r"\s+", " ", text).strip()


# A line that continues the previous expression (Swift allows a leading binary operator or
# `.member` on the next line): `return x\n    ?? fallback` is ONE return statement.
_CONTINUATION = re.compile(r"^\s*(?:\?\?|\|\||&&|==|!=|[.?:+\-*/<>])")


def _return_statements(block: str) -> list:
    """Every `return` in `block` as one normalized statement, continuation lines included and
    an unbalanced trailing `}` (a one-line `else { return nil }`) dropped."""
    lines = block.split("\n")
    out = []
    for idx, line in enumerate(lines):
        for m in re.finditer(r"\breturn\b", line):
            stmt = line[m.start():]
            j = idx + 1
            while j < len(lines) and _CONTINUATION.match(lines[j]):
                stmt += " " + lines[j]
                j += 1
            stmt = _norm(stmt)
            while stmt.count("}") > stmt.count("{") and stmt.endswith("}"):
                stmt = stmt[:-1].rstrip()
            out.append(stmt)
    return out


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


_SWIFT_LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
_CASE_LABEL = re.compile(r'\bcase\s+"(?:[^"\\\n]|\\.)*"\s*(?:,\s*"(?:[^"\\\n]|\\.)*"\s*)*:')


def _copy_literals(block: str) -> list:
    """The string literals a type can SHOW: every literal left once `case "…":` labels are
    removed. The labels are switch keys ("peter_lynch"), not copy — scanning them for investor
    names would make the registry guard and the hygiene guard contradict each other."""
    return _SWIFT_LITERAL.findall(_CASE_LABEL.sub("case _:", block))


def _mode_block() -> str:
    return _decl_block(_code(_CHAT_MODELS), "struct ReportChatAgentMode")


# ── 1. one agent-tag table, equal to the backend's ───────────────────────────

def _ios_tag_table() -> tuple:
    models = _code(_REPORT_MODELS)
    enum = _decl_block(models, "enum ReportAgentPersona")
    init = _decl_block(enum, "init?(agentTag raw: String)")
    tag_to_case = dict(re.findall(r'case "([a-z_]+)":\s*self = \.(\w+)', init))
    key_block = _decl_block(enum, "var personaKey: String")
    case_to_key = dict(re.findall(r'case \.(\w+):\s*return "([a-z_]+)"', key_block))
    head = enum[: enum.index("init?(agentTag")]
    cases = re.findall(r"^\s*case (\w+)\s*$", head, flags=re.M)
    return enum, init, key_block, tag_to_case, case_to_key, cases


def test_the_ios_tag_table_equals_the_backends():
    _, _, _, tag_to_case, case_to_key, cases = _ios_tag_table()
    assert tag_to_case, "the agent-tag switch was not parsed — this scan has drifted"
    ios = {tag: case_to_key[case] for tag, case in tag_to_case.items()}
    assert ios == {**AGENT_TAG_TO_KEY, **LEGACY_AGENT_TAGS}, (
        "iOS and the backend resolve a report's agent tag differently — the badge, the chat "
        "label and the server's voice would name different methods for one report"
    )
    # The other two backend sources of the same mapping.
    assert {tag: key for key, tag in _AGENT_MAP.items()}.items() <= ios.items()
    assert {cfg.agent_tag: key for key, cfg in _PERSONA_REGISTRY.items()}.items() <= ios.items()


def test_every_report_persona_names_its_backend_key_exhaustively():
    _, init, key_block, _, case_to_key, cases = _ios_tag_table()
    assert len(cases) == 5, cases
    assert set(case_to_key) == set(cases), "personaKey must name every case"
    assert set(case_to_key.values()) == PERSONA_KEYS
    assert "default" not in key_block, (
        "no `default:` in personaKey — a sixth case must not compile until it names its key"
    )
    # The init is the closed one: anything unknown is nil, never a persona.
    assert re.search(r"default:\s*return nil", init)
    assert re.search(rf"guard raw\.unicodeScalars\.count <= {_MAX_TAG_LEN} else {{ return nil }}", init), (
        "the over-long cut must match the backend's persona_key_from_tag (Python len = code points)"
    )
    assert re.search(r"trimmingCharacters\(in: \.whitespacesAndNewlines\)\.lowercased\(\)", init)


def test_the_display_mapping_uses_the_shared_table():
    body = _decl_block(_code(_REPORT_RESPONSE), "func toTickerReportData() -> TickerReportData")
    assert "ReportAgentPersona(agentTag: agent) ?? .buffett" in body
    assert 'case "dalio"' not in body and 'case "lynch"' not in body


def test_no_second_agent_tag_table_exists_on_ios():
    """`case "dalio"` (or any tag) switched on anywhere else is a second table that drifts."""
    _, init, *_ = _ios_tag_table()
    tag_case = re.compile(r'case "(?:buffett|wood|lynch|ackman|burry|dalio)"\s*:')
    hits = []
    for path in _IOS.rglob("*.swift"):
        src = _code(path)
        n = len(tag_case.findall(src))
        if path == _REPORT_MODELS:
            n -= len(tag_case.findall(init))
        if n:
            hits.append(f"{path.relative_to(_REPO)} ({n})")
    assert not hits, f"a second agent-tag table: {hits}"


# ── 2. the chat's persona resolver is closed ─────────────────────────────────

_TOKEN_SIG = "static func forPersonaToken(_ raw: String) -> AnalysisPersona?"
_REF_SIG = "static func forChatReference(_ referenceId: String?) -> AnalysisPersona?"


def _persona_statics() -> list:
    """The hard-coded personas' static names (`warrenBuffett`, …) — a fallback names one."""
    names = re.findall(r"static let (\w+) = AnalysisPersona\(", _code(_RESEARCH_MODELS))
    assert len(names) == 5, names
    return names


def _assert_no_fallback(block: str, what: str) -> None:
    """The spellings of "borrow a persona" that are not a `return` shape: a named static, an
    index into the list, a forced first, the server-fetched list, a default arm."""
    for name in _persona_statics():
        assert not re.search(rf"\.{name}\b", block), f"{what} names .{name} — a fallback"
    for banned in ("allCases[", ".first!", ".last", "default:", "BackendPersona", "fallbacks",
                   "settingsDefault"):
        assert banned not in block, f"{what} must not fall back ({banned!r})"
    assert not re.search(r"\bpersonas\b", block), f"{what}: never the server-fetched list"


def test_the_token_resolver_is_closed():
    """Pinned by SHAPE, not by a deny-list of spellings: `?? Self.warrenBuffett`,
    `?? allCases[0]` and `else { return Self.warrenBuffett }` all passed a substring ban."""
    token = _decl_block(_code(_RESEARCH_MODELS), _TOKEN_SIG)
    returns = _return_statements(token)
    allowed = {"return nil", "return allCases.first { $0.key == key }"}
    assert set(returns) <= allowed, (
        f"forPersonaToken returns {sorted(set(returns) - allowed)} — every exit is nil or the "
        "hard-coded match; anything else is a fallback"
    )
    assert returns.count("return allCases.first { $0.key == key }") == 1, returns
    assert returns[-1] == "return allCases.first { $0.key == key }", "the match is the last exit"
    # The only `??` maps a TAG to its key and otherwise keeps the token as given.
    assert token.count("??") == 1, "a second `??` is a fallback"
    assert re.search(
        r"^\s*let key = ReportAgentPersona\(agentTag: token\)\?\.personaKey \?\? token\s*$",
        token, flags=re.M,
    ), "one tag table, shared with the badge — and an unknown tag stays itself"
    assert '"' not in token, "no hard-coded key or name inside the resolver"
    _assert_no_fallback(token, "forPersonaToken")
    assert re.search(rf"guard raw\.unicodeScalars\.count <= {_MAX_TAG_LEN} else {{ return nil }}", token)
    assert re.search(r"guard !token\.isEmpty else \{ return nil \}", token)


def test_the_reference_split_keeps_empty_segments():
    ref = _decl_block(_code(_RESEARCH_MODELS), _REF_SIG)
    assert 'split(separator: "|", omittingEmptySubsequences: false)' in ref, (
        "Swift's default split DROPS empty parts: \"AAPL||peter_lynch\" would read Growth "
        "Hunter while the server sees an empty persona segment"
    )
    returns = _return_statements(ref)
    allowed = {"return nil", "return forPersonaToken(String(parts[1]))"}
    assert set(returns) <= allowed, (
        f"forChatReference returns {sorted(set(returns) - allowed)} — nil, or the RAW segment "
        "through the closed token rule, as the backend passes it"
    )
    assert returns.count("return forPersonaToken(String(parts[1]))") == 1, returns
    assert "??" not in ref, "no fallback after the token rule"
    assert re.findall(r'"[^"\n]*"', ref) == ['"|"'], "the separator is the only literal"
    _assert_no_fallback(ref, "forChatReference")
    assert re.search(
        rf"guard let referenceId, referenceId\.unicodeScalars\.count <= {_MAX_REFERENCE_LEN} else {{ return nil }}",
        ref,
    ), "the same over-long reference cut as report_voice_prompt.report_persona_key"
    assert re.search(r"guard parts\.count >= 2 else \{ return nil \}", ref)


# REFERENCE-PARSE parity, case by case. The expected column is what the Swift above computes
# (≤512 code points → split keeping empties → RAW segment [1] → ≤64 → trim → tag-or-key →
# allCases, no default); the backend side is computed live from the voice's own reference
# parse, `report_voice_prompt.report_persona_key`. This is NOT voice parity: the server's voice
# prefers the grounded report's persona over the reference (`resolve_voice_key`), so a None
# row below can still get a voice from a grounded report — see "Known limitations" above.
_REFERENCE_TABLE = [
    ("AAPL|peter_lynch", "peter_lynch"),
    ("AAPL|lynch", "peter_lynch"),
    ("AAPL| LYNCH |a1b2", "peter_lynch"),
    ("|peter_lynch", "peter_lynch"),
    ("AAPL|dalio|rid", "bill_ackman"),
    ("AAPL|michael_burry|", "michael_burry"),
    ("AAPL||peter_lynch", None),
    ("AAPL", None),
    ("AAPL|", None),
    ("|", None),
    ("", None),
    ("AAPL|soros", None),
    ("AAPL|warren", None),
    ("AAPL|buffett2", None),
    ("AAPL|charlie_munger", None),
    ("AAPL|" + "l" * 65, None),
    ("AAPL|" + " " * 60 + "lynch", None),          # over 64 BEFORE trimming
    ("AAPL|lynch|" + "x" * 600, None),             # over-long reference
    ("AAPL|lynch|" + "x" * (_MAX_REFERENCE_LEN - 11), "peter_lynch"),  # exactly at the cap
]


@pytest.mark.parametrize("reference,expected", _REFERENCE_TABLE)
def test_the_backend_parses_each_reference_the_way_ios_does(reference, expected):
    got = report_persona_key(reference)
    assert got == expected, (
        f"{reference!r}: the backend's reference parse reads {got!r}, iOS reads {expected!r} — "
        "with no grounded report to override it, the chat label and the server's voice would "
        "name different methods"
    )
    if expected is not None:
        assert expected in _ios_all_cases_keys(), "iOS matches only its hard-coded allCases"


def test_the_grounded_report_outranks_the_reference_for_the_voice():
    """The recorded limitation, pinned so a fix (the server returning its voice key) is a
    visible decision: the grounded report's tag wins, so the voice can differ from what the
    reference alone — and therefore the iOS label — says."""
    assert resolve_voice_key("lynch", "AAPL|warren_buffett") == "peter_lynch"   # review #13
    assert resolve_voice_key("lynch", "AAPL") == "peter_lynch"     # a voice, but no iOS label
    assert report_persona_key("AAPL") is None
    assert resolve_voice_key(None, "AAPL|lynch") == "peter_lynch"  # nothing grounded: the ref
    assert resolve_voice_key(None, "AAPL||lynch") is None


# ── 3. the approved labels ───────────────────────────────────────────────────

def _ios_style_names() -> dict:
    """key -> `compactName` of each hard-coded persona (its name without a leading "The")."""
    src = _code(_RESEARCH_MODELS)
    names = dict(re.findall(
        r'static let \w+ = AnalysisPersona\(\s*key: "([^"]+)",\s*name: "([^"]+)"', src
    ))
    out = {}
    for key, name in names.items():
        words = name.split(" ")
        out[key] = " ".join(words[1:]) if len(words) > 1 and words[0].lower() == "the" else name
    return out


def test_the_mode_label_is_the_style_name_plus_agent():
    mode = _mode_block()
    assert re.search(r"var styleName: String \{ persona\.compactName \}", mode)
    label = _decl_block(mode, "var chatModeLabel: String")
    assert '"\\(styleName) Agent"' in label
    for banned in ("agentLabel", "shortName"):
        assert banned not in label, (
            f"chatModeLabel must not read {banned} — AnalysisPersona.agentLabel is \"GARP Agent\""
        )
    styles = _ios_style_names()
    assert set(styles) == PERSONA_KEYS
    for key in PERSONA_KEYS:
        ios_label = f"{styles[key]} Agent"
        backend_label = get_persona_config(key).display_name.removeprefix("The ") + " Agent"
        assert ios_label == backend_label == _APPROVED_LABELS[key], key


# ── 4. copy hygiene ──────────────────────────────────────────────────────────

_ADVICE_OR_HOLDER = re.compile(
    r"\b(?:buy|sell|hold|buying|selling)\b|\byour agent\b|\badvis[oe]r\b|\bmy portfolio\b"
    r"|\bI (?:own|bought|hold|sold)\b|\b(?:ideal|right|suited|suitable|perfect) for\b",
    re.IGNORECASE,
)


def _copy_problems(text: str) -> list:
    return (
        guard.violations(text)
        + guard.directive_violations(text)
        + [m.group(0) for m in _ADVICE_OR_HOLDER.finditer(text)]
    )


def _rendered_copy() -> list:
    """The chip exactly as each persona renders it (the mode's only visible copy)."""
    styles = _ios_style_names()
    return [f"Cay AI · {styles[key]} Agent · AAPL report" for key in PERSONA_KEYS]


def test_the_mode_copy_is_name_free_and_advice_free():
    literals = _copy_literals(_mode_block())
    assert any("Agent" in lit for lit in literals), "the literal scan found no copy"
    problems = [(lit[:50], p) for lit in literals for p in _copy_problems(lit)]
    problems += [(text[:50], p) for text in _rendered_copy() for p in _copy_problems(text)]
    assert not problems, problems


def test_the_copy_scanner_skips_switch_keys_but_catches_returned_copy():
    """Anti-vacuity, both directions (review #1): the registry's own keys contain investor
    surnames and must not trip the scan; a name inside a RETURNED line must."""
    clean = 'switch key {\n case "peter_lynch":\n return "growth at a reasonable price"\n }'
    assert not [p for lit in _copy_literals(clean) for p in _copy_problems(lit)]
    planted = 'switch key {\n case "peter_lynch":\n return "the Peter Lynch way: buy what you know"\n }'
    found = [p for lit in _copy_literals(planted) for p in _copy_problems(lit)]
    assert "Peter Lynch" in found and "buy" in found, found
    assert _copy_problems("a tenbagger, as I own it")


def test_no_view_calls_cay_ai_your_agent():
    """"Your agent" implies agency on the user's behalf (a fiduciary connotation)."""
    hits = []
    for path in _IOS.rglob("*.swift"):
        for lit in _SWIFT_LITERAL.findall(_code(path)):
            if re.search(r"\byour agent\b", lit, re.IGNORECASE):
                hits.append(f"{path.relative_to(_REPO)}: {lit[:60]}")
    assert not hits, hits


# ── 5. the gate and the single source ────────────────────────────────────────

def test_only_a_report_chat_gets_a_mode():
    """Exact normalized statements: `contextType == .tickerReport || contextType == .stock`
    and `_ = pattern; return symbol` both passed the old substring checks."""
    mode = _mode_block()
    init = _norm(_decl_block(mode, "init?(contextType: ChatContextType?, referenceId: String?)"))
    assert (
        "{ guard contextType == .tickerReport, "
        "let persona = AnalysisPersona.forChatReference(referenceId) "
        "else { return nil } "
    ) in init, "the one gate: a report chat and a known (closed-registry) persona"
    for assignment in (
        "self.persona = persona",
        "self.ticker = Self.validatedTicker(in: referenceId)",
    ):
        assert f" {assignment} " in f"{init} ", f"the init lost `{assignment}`"

    ticker = _decl_block(mode, "private static func validatedTicker(in referenceId: String?) -> String")
    flat = _norm(ticker)
    assert '.split(separator: "|", omittingEmptySubsequences: false).first' in flat
    assert "let symbol = first.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()" in flat
    assert r'let pattern = #"^\^?[A-Z0-9][A-Z0-9.\-]{0,14}$"#' in flat, "the backend's symbol pattern"
    returns = _return_statements(ticker)
    applied = 'return symbol.range(of: pattern, options: .regularExpression) != nil ? symbol : ""'
    assert set(returns) == {'return ""', applied}, (
        f"validatedTicker returns {returns} — the symbol only when the pattern MATCHES, else \"\""
    )


def test_the_screen_builds_one_mode_and_feeds_the_chip():
    screen = _code(_SCREEN)
    prop = _decl_block(screen, "private var reportAgentMode: ReportChatAgentMode?")
    assert "viewModel.currentContextType" in prop and "viewModel.currentReferenceId" in prop
    assert len(re.findall(r"\bReportChatAgentMode\(", screen)) == 1, "one construction site"

    content = _decl_block(screen, "private var chatContent: some View")
    calls = [m.start() for m in re.finditer(r"\bGroundedContextChip\(", content)]
    assert len(calls) == 1
    chip = _call_args(content, calls[0], "GroundedContextChip(")
    assert "agentModeLabel: reportAgentMode?.chatModeLabel" in chip
    assert "referenceLabel: reportAgentMode?.ticker ?? groundingReferenceLabel" in chip, (
        "in a mode, the chip's ticker is the mode's VALIDATED symbol"
    )

    area = _decl_block(screen, "private var conversationArea: some View")
    at = area.find("ChatMessagesList(")
    assert at != -1
    assert "reportAgentMode" not in _call_args(area, at, "ChatMessagesList("), (
        "the message list draws no mode row — the chip is the mode's only surface"
    )


def test_there_is_no_greeting_card():
    """Owner, 2026-10-02: "the chip is enough" — the code-authored greeting card (header, intro
    and footnote above the first message) was removed. Pinned absent, so it does not drift back
    as a molecule, a list row, a message or a ViewModel string."""
    assert not _REMOVED_GREETING.exists(), "the greeting molecule is gone; the chip is the surface"
    hits = []
    for path in _IOS.rglob("*.swift"):
        code = _code(path)
        for token in ("ReportChatAgentGreeting", "agentGreeting", "Hi, I'm Cay AI"):
            if token in code:
                hits.append(f"{path.relative_to(_REPO)}: {token}")
    assert not hits, hits
    mode = _mode_block()
    for gone in ("var greeting", "var footnote", "methodLine"):
        assert gone not in mode, f"ReportChatAgentMode regained {gone!r}"
    lst = _decl_block(_code(_LIST), "struct ChatMessagesList")
    assert "ReportChatAgentMode" not in lst, "the message list takes no mode"
    assert "ReportChatAgentMode" not in _code(_CHAT_VM), "the mode is never ViewModel state"


# ── 7. the chip ──────────────────────────────────────────────────────────────

def test_the_chip_names_the_mode_only_after_the_softened_branch():
    chip = _decl_block(_code(_CHIP), "struct GroundedContextChip")
    decl = re.search(r"var agentModeLabel: String\? = nil", chip)
    assert decl and chip.index("var groundingArrived") < decl.start()
    active = _decl_block(chip, "private var activeModeLabel: String?")
    assert (
        "{ guard contextType == .tickerReport, "
        "let mode = agentModeLabel?.trimmingCharacters(in: .whitespaces), "
        "!mode.isEmpty else { return nil } return mode }"
    ) == _norm(active), "a report chat and a non-blank label — exactly (`|| true` passed a substring)"

    label = _decl_block(chip, "private var labelText: String")
    assert "if groundingUnavailable { return contextType.groundingUnavailableLabel }" in _norm(label)
    soft = label.find("if groundingUnavailable {")
    mode_at = label.find("if contextType == .tickerReport, let mode = activeModeLabel {")
    claim = label.find("Grounded on")
    assert -1 < soft < mode_at < claim, "softened notice, then the mode, then the plain claim"
    branch = _decl_block(label[mode_at:], "if contextType == .tickerReport")
    assert '"Cay AI · \\(mode) · \\(ref) report"' in branch
    assert '"Cay AI · \\(mode)"' in branch
    assert re.search(r"\.lineLimit\(groundingUnavailable \|\| activeModeLabel != nil \? 2 : 1\)", chip)
    body = _decl_block(chip, "var body: some View")
    a11y = body.find(".accessibilityElement(children: .ignore)")
    assert -1 < a11y < body.find(".accessibilityLabel(labelText"), (
        "one element first, or the glyph and text are not read as one label"
    )


# ── 8. the report screen sends the report's own persona ──────────────────────

def test_the_report_chat_reference_comes_from_the_report_on_screen():
    view = _code(_REPORT_VIEW)
    send = _decl_block(view, "private func handleReportChatSend()")
    # The whole statement: `"…|warren_buffett" // report.agent.personaKey` passed a substring
    # check over a stripper that kept trailing comments.
    assert re.search(
        r'^\s*let base = "\\\(report\.symbol\)\|\\\(report\.agent\.personaKey\)"\s*$', send, flags=re.M
    ), "the reference's persona segment is the report ON SCREEN's own persona key"
    flat = _norm(send)
    assert 'reference = "\\(base)|\\(rid)"' in flat and "} else { reference = base }" in flat
    assert "referenceId: reference" in flat
    assert "viewModel.personaKey" not in send
    assert "contextType: .tickerReport" in send
    guard_at = send.find("guard let report = viewModel.reportData")
    clear_at = send.find('viewModel.aiInputText = ""')
    assert -1 < guard_at < clear_at, "guard before clearing, or the typed question is lost"
    seeded = re.search(r"if\s+seeded\s*\{", send)
    assert seeded, "present only when the seed went out (a streaming answer blocks it)"
    seeded_block = _decl_block(send[seeded.start():], "if seeded")
    assert 'viewModel.aiInputText = ""' in seeded_block and "showAIChat = true" in seeded_block
    assert send.count('viewModel.aiInputText = ""') == 1 and send.count("showAIChat = true") == 1

    # The bar disables send while an answer streams; without it the refused seed is a dead tap.
    bars = [m.start() for m in re.finditer(r"\bCaydexAIChatBar\(", view)]
    assert len(bars) == 1, bars
    bar = _norm(_call_args(view, bars[0], "CaydexAIChatBar("))
    assert "onSend: handleReportChatSend, isBusy: chatViewModel.isAITyping )" in bar, bar

    vm = _code(_REPORT_VM)
    assert not re.search(r"\bvar personaKey\b", vm), (
        "the route's persona defaults to warren_buffett on a notification open — never the chat's"
    )
    users = [p.relative_to(_REPO) for p in _IOS.rglob("*.swift") if "viewModel.personaKey" in _code(p)]
    assert not users, users


# ── 9. the scanners themselves ───────────────────────────────────────────────

def test_the_comment_stripper_drops_comments_and_keeps_line_numbers():
    src = 'let a = 1\n// "Peter Lynch"\n/* block\n "Buffett" */\nlet b = "ok"\n'
    out = _strip_comments(src)
    assert "Peter Lynch" not in out and "Buffett" not in out
    assert out.count("\n") == src.count("\n")
    assert 'let b = "ok"' in out


@pytest.mark.parametrize("src,kept,dropped", [
    # A TRAILING comment is a comment (the hole: its tokens satisfied a substring guard).
    ('let base = "\\(s)|warren_buffett" // report.agent.personaKey\n',
     'let base = "\\(s)|warren_buffett" ', "report.agent.personaKey"),
    # `//` inside a literal is code.
    ('let url = "https://caydex.com" // note\n', 'let url = "https://caydex.com" ', "note"),
    # A raw string: `\\` is not an escape, `"` alone does not close it, `//` inside is code.
    ('let p = #"^\\^?[A-Z]"//x"# // tail\n', 'let p = #"^\\^?[A-Z]"//x"# ', "tail"),
    # An escaped quote does not close the literal.
    ('let q = "a \\" // b" // tail\n', 'let q = "a \\" // b" ', "tail"),
    # An interpolation is code again and may hold a literal with `//` inside.
    ('let i = "\\(x ?? "//") y" // tail\n', 'let i = "\\(x ?? "//") y" ', "tail"),
    # A multi-line literal keeps its `//` and its newlines.
    ('let m = """\na // b\n""" // tail\n', 'let m = """\na // b\n""" ', "tail"),
    # Block comments NEST, as Swift's do.
    ("a /* x /* y */ z */ b\n", "a  b", "z"),
    # An unterminated literal recovers at the end of its line.
    ('let u = "open\nlet v = 1 // tail\n', "let v = 1 ", "tail"),
])
def test_the_comment_stripper_is_string_aware(src, kept, dropped):
    out = _strip_comments(src)
    assert kept in out, out
    assert dropped not in out, out
    assert out.count("\n") == src.count("\n")


def test_the_return_scanner_joins_continuations_and_drops_closing_braces():
    block = (
        "{\n guard a else { return nil }\n"
        "    return allCases.first { $0.key == key }\n        ?? .warrenBuffett\n}"
    )
    assert _return_statements(block) == [
        "return nil", "return allCases.first { $0.key == key } ?? .warrenBuffett",
    ]


def test_the_brace_bounder_returns_a_strict_subset():
    src = _code(_CHAT_MODELS)
    block = _mode_block()
    inner = _decl_block(block, "private static func validatedTicker(in referenceId: String?) -> String")
    assert 0 < len(inner) < len(block) < len(src)
    assert "struct ChatMessageDTO" not in block, "the bound ran past the struct"
