"""Pure-function tests for `scripts/asc_review_resubmit.py` (hermetic — no ASC calls).

The script rewrites ONE paragraph of the App Review notes that Apple reads on every
submission. The failure modes worth pinning are the silent ones: a paragraph duplicated
instead of replaced, a neighbouring paragraph altered, notes pushed past App Store Connect's
4,000-character cap (the field had 924 characters left when this was written), and a notes
claim ("no other background mode") that the shipped Info.plist contradicts.
"""
from __future__ import annotations

import importlib.util
import plistlib
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "backend" / "scripts" / "asc_review_resubmit.py"
_INFO_PLIST = _REPO / "frontend" / "ios" / "ios" / "Info.plist"

_spec = importlib.util.spec_from_file_location("asc_review_resubmit", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

_NOTES = (
    "Caydex is an information and education tool.\n"
    "\n"
    "Demo account — please use this to review.\n"
    "\n"
    "Background modes. remote-notification — opt-in push. audio — narrated audio.\n"
    "\n"
    "Age rating 17+. "
)


def test_replaces_exactly_the_one_paragraph_and_nothing_else():
    out = mod.replace_paragraph(_NOTES, "Background modes.", "NEW")
    before, after = _NOTES.split("\n"), out.split("\n")
    assert len(before) == len(after)
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert changed == [4]
    assert after[4] == "NEW"


def test_is_idempotent():
    once = mod.replace_paragraph(_NOTES, "Background modes.", mod.NEW_PARAGRAPH)
    assert mod.replace_paragraph(once, "Background modes.", mod.NEW_PARAGRAPH) == once
    assert once.count(mod.NEW_PARAGRAPH) == 1


def test_refuses_when_the_paragraph_is_missing():
    with pytest.raises(mod.NotesError, match="no paragraph"):
        mod.replace_paragraph("Only a demo paragraph.", "Background modes.", "NEW")


def test_refuses_when_the_paragraph_is_ambiguous():
    twice = _NOTES + "\nBackground modes. again\n"
    with pytest.raises(mod.NotesError, match="ambiguous"):
        mod.replace_paragraph(twice, "Background modes.", "NEW")


def test_refuses_to_exceed_the_limit():
    with pytest.raises(mod.NotesError, match="over the 100 limit"):
        mod.replace_paragraph(_NOTES, "Background modes.", "x" * 200, limit=100)


def test_refuses_over_limit_notes_even_when_already_applied():
    already = "y" * 120 + "\n" + "NEW"
    with pytest.raises(mod.NotesError, match="over the 100 limit"):
        mod.replace_paragraph(already, "Background modes.", "NEW", limit=100)


def test_the_new_paragraph_fits_the_budget_it_was_written_for():
    """Live notes were 3,076 chars with a ~370-char paragraph; leave room for other edits."""
    assert len(mod.NEW_PARAGRAPH) <= 900, len(mod.NEW_PARAGRAPH)


def test_the_notes_background_mode_claim_matches_the_shipped_plist():
    """The paragraph tells App Review the app declares no other mode — that must stay true."""
    with _INFO_PLIST.open("rb") as fh:
        modes = sorted(plistlib.load(fh).get("UIBackgroundModes", []))
    assert "no other background mode" in mod.NEW_PARAGRAPH
    assert modes == ["audio"], (
        f"Info.plist declares {modes}, but the App Review notes paragraph says audio is the "
        "only background mode. Update NEW_PARAGRAPH (and re-run the script) or the plist."
    )


def test_the_new_paragraph_starts_with_the_old_marker_or_stays_findable():
    """A second run must find its own paragraph as 'already applied', not as 'missing'."""
    assert mod.NEW_PARAGRAPH not in _NOTES
    assert mod.replace_paragraph(
        mod.replace_paragraph(_NOTES, "Background modes.", mod.NEW_PARAGRAPH),
        "Background modes.",
        mod.NEW_PARAGRAPH,
    ).count("Background audio") == 1


@pytest.mark.parametrize(
    "name, content, expected",
    [
        ("rec.mov", b"x", None),
        ("rec.MP4", b"x", None),
        ("rec.gif", b"x", "expected one of"),
        ("rec.mov", b"", "is empty"),
    ],
)
def test_video_problem(tmp_path, name, content, expected):
    p = tmp_path / name
    p.write_bytes(content)
    got = mod.video_problem(p)
    if expected is None:
        assert got is None
    else:
        assert expected in got


def test_video_problem_missing_file(tmp_path):
    assert "not found" in mod.video_problem(tmp_path / "nope.mov")


# ── the full rewrite (all REPLACEMENTS) ─────────────────────────────────────────────────

_LIVE_SHAPE = (
    "Caydex is an information and education tool.\n\n"
    "AI-generated content. Reports are AI.\n\n"
    "Educational library. The Learn section contains original study guides.\n\n"
    "Demo account — please use this to review. Profile → Settings → Delete Account.\n\n"
    "In-app purchases. Two subscriptions and four packs.\n\n"
    "Background modes. remote-notification — push. audio — narration.\n\n"
    "Age rating 17+. Our Terms require users to be 18."
)


def test_apply_replacements_rewrites_every_paragraph_and_keeps_the_rest():
    out = mod.apply_replacements(_LIVE_SHAPE, mod.REPLACEMENTS)
    for _, paragraph in mod.REPLACEMENTS:
        assert out.count(paragraph) == 1
    assert "Caydex is an information and education tool." in out
    assert "In-app purchases. Two subscriptions and four packs." in out
    assert "Age rating 17+" not in out and "remote-notification" not in out
    assert mod.apply_replacements(out, mod.REPLACEMENTS) == out  # idempotent


def test_apply_replacements_checks_the_limit_once_on_the_result():
    with pytest.raises(mod.NotesError, match="over the 50 limit"):
        mod.apply_replacements(_LIVE_SHAPE, mod.REPLACEMENTS, limit=50)


# The live 1.0 review notes exactly as App Review APPROVED them (read from ASC 2026-10-05, no
# credentials in them). ASC copies them into 1.1, so this is what the script will rewrite.
_LIVE_NOTES = (_REPO / "backend" / "tests" / "data" / "asc_review_notes_1_0_2026_10_05.txt").read_text(encoding="utf-8")


def test_the_fixture_is_the_approved_live_text():
    assert len(_LIVE_NOTES) == 3561
    assert mod.AI_PARAGRAPH_APPROVED in _LIVE_NOTES.split("\n"), (
        "AI_PARAGRAPH_APPROVED is no longer the approved line verbatim — the start-marker swap "
        "would silently overwrite a drifted wording"
    )


def test_the_rewrite_of_the_live_notes_changes_only_the_ai_line_and_fits():
    out = mod.apply_replacements(_LIVE_NOTES, mod.REPLACEMENTS)
    before, after = _LIVE_NOTES.split("\n"), out.split("\n")
    assert len(before) == len(after)
    changed = [(b, a) for b, a in zip(before, after) if b != a]
    assert changed == [(mod.AI_PARAGRAPH_APPROVED, mod.AI_PARAGRAPH)], (
        "the 1.1 rewrite touches more than the AI paragraph — every other paragraph was approved"
    )
    # Keep a margin: ASC refuses the save outright at 4,000, and a later one-line fix needs room.
    assert len(out) <= mod.NOTES_LIMIT - 50, f"{len(out)} chars"
    assert mod.apply_replacements(out, mod.REPLACEMENTS) == out  # idempotent


# ── step 2: the one App Review attachment slot ───────────────────────────────

_ORDER_FORM = "DocuSign_FMP_-_Quote_-_Caydex_-_0003.pdf"


def test_an_empty_slot_is_reported_because_the_notes_promise_the_order_form():
    action, problems = mod.attachment_plan([], None)
    assert action == "skip_no_video"
    assert any("Order Form is attached" in p for p in problems)


def test_the_order_form_in_place_and_no_video_is_clean():
    assert mod.attachment_plan([_ORDER_FORM], None) == ("skip_no_video", [])


def test_a_video_never_takes_the_empty_slot_reserved_for_the_order_form():
    action, problems = mod.attachment_plan([], "bg-audio.mov")
    assert action == "refuse"
    assert any("reserved for the signed Order Form" in p for p in problems)


def test_a_video_never_replaces_the_order_form():
    action, problems = mod.attachment_plan([_ORDER_FORM], "bg-audio.mov")
    assert action == "refuse"
    assert any("not replacing" in p for p in problems)


def test_the_override_uploads_into_an_empty_slot_but_still_reports_the_missing_order_form():
    action, problems = mod.attachment_plan([], "bg-audio.mov", allow_video_in_empty_slot=True)
    assert action == "upload"
    assert any("Order Form is attached" in p for p in problems)


def test_an_already_attached_video_is_skipped():
    assert mod.attachment_plan(["bg-audio.mov"], "bg-audio.mov")[0] == "skip_already_attached"


_SWIFT = _REPO / "frontend" / "ios" / "ios"
_BACKEND_APP = _REPO / "backend" / "app"


def _swift_code(path: Path) -> str:
    """Comment-stripped Swift (testing.md rule 1): a `//` line or a trailing ` //` tail is
    blanked, so a comment that names a token cannot keep a backing row green after the code that
    makes the claim true is gone. `\\s//` keeps `"https://…"` literals intact."""
    import re as _re
    return "\n".join("" if l.strip().startswith("//") else _re.sub(r"\s//.*$", "", l)
                     for l in path.read_text(encoding="utf-8").splitlines())


def _brace_block(code: str, anchor: str) -> str:
    """The `{ … }` body that follows `anchor` (testing.md rule 2: brace-bound the declaration)."""
    start = code.index(anchor)
    open_at = code.index("{", start)
    depth = 0
    for i in range(open_at, len(code)):
        if code[i] == "{":
            depth += 1
        elif code[i] == "}":
            depth -= 1
            if depth == 0:
                return code[open_at:i + 1]
    raise AssertionError(f"unbalanced braces after {anchor!r}")


def _py_code(path: Path) -> str:
    """Python with every `#` comment removed by the tokenizer (string literals — the backing
    tokens of a config URL — stay byte-identical, unlike an `ast.unparse` that requotes them)."""
    import io
    import tokenize
    src = path.read_text(encoding="utf-8")
    toks = [t for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type != tokenize.COMMENT]
    return tokenize.untokenize(toks)


def _py_function_code(path: Path, name: str) -> str:
    """ONE module-level function's CODE: its AST with the docstring dropped, unparsed (testing.md
    rules 1-2) — a module docstring or a comment naming the guarantee can never back it."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(getattr(fn.body[0], "value", None), ast.Constant):
        fn.body = fn.body[1:]
    return ast.unparse(fn)


def _backing_text(path) -> str:
    """The code a row reads, comments (and, for a bound function, its docstring) stripped. `path`
    is a file, or (file, function name) to bind the token to one function's code."""
    if isinstance(path, tuple):
        return _py_function_code(*path)
    if path.suffix == ".swift":
        return _swift_code(path)
    if path.suffix == ".py":
        return _py_code(path)
    return path.read_text(encoding="utf-8")


def _backing_name(path) -> str:
    return f"{path[0].name}::{path[1]}" if isinstance(path, tuple) else path.name


# The synchronous election that makes "one web search per question" true: the GUARD and the
# assignment together (final review 2026-10-09: the token used to be "ONE SEARCH PER TURN", found
# only in the module docstring — deleting the election left the row green).
_ONE_SEARCH_ELECTION = "if turn._task is None:\n        turn._task = asyncio.ensure_future(_search_once("

# Rows shared by both web-search sentences: (claim in the notes, file, token that makes it true).
_SHARED_BACKING = [
    ("one web search per question",
     (_BACKEND_APP / "services" / "chat_web_search_service.py", "run_web_search"), _ONE_SEARCH_ELECTION),
    ("Brave Search API", _BACKEND_APP / "config.py", '"https://api.search.brave.com/'),
    ('"Web search"', _SWIFT / "Views" / "Molecules" / "ThinkingProcessCard.swift", 'text: "Web search"'),
    ("in-app Safari view", _SWIFT / "Views" / "Screens" / "AIChatScreen.swift", ".inAppBrowser(link: $browserLink"),
    # An explicit ask is disclosed on the consent sheet in any chat (report chat included).
    ("AI consent sheet", _SWIFT / "Views" / "Screens" / "AIDataConsentView.swift", "when you ask Cay AI to search the web"),
    ("Privacy Policy", _SWIFT / "Views" / "Screens" / "PrivacyPolicyView.swift", "Brave Software (web search in Cay AI chat"),
    ('"Chat with the report"', _SWIFT / "Views" / "Screens" / "TickerReportView.swift", 'placeholder: "Chat with the report'),
]
_ALL_CHATS_BACKING = _SHARED_BACKING + [
    ('"Ask Cay AI" bar', _SWIFT / "Views" / "Molecules" / "CaydexAIChatBar.swift", 'var placeholder: String = "Ask Cay AI..."'),
    ("AI consent sheet", _SWIFT / "Views" / "Screens" / "AIDataConsentView.swift", "In any chat, when you ask Cay AI to search the web"),
    ("Privacy Policy", _SWIFT / "Views" / "Screens" / "PrivacyPolicyView.swift", "in any chat, Cay AI may run a web search"),
]


@pytest.mark.parametrize(
    "sentence_name, backing",
    [("WEB_SEARCH_REPORT_CHAT", _SHARED_BACKING), ("WEB_SEARCH_ALL_CHATS", _ALL_CHATS_BACKING)],
)
def test_every_claim_in_each_web_search_sentence_is_backed_by_code(sentence_name, backing):
    sentence = getattr(mod, sentence_name)
    for claim, path, token in backing:
        assert claim in sentence, f"{sentence_name} no longer says {claim!r} — update this table"
        assert token in _backing_text(path), (
            f"{sentence_name}: {claim!r} is no longer true: {token!r} not in {_backing_name(path)} "
            "(comments stripped)"
        )


def test_the_one_search_row_reads_the_election_not_the_docstring():
    """Mutation, pinned: the row reads `run_web_search`'s code, so dropping the guard (every call
    would start a new search) fails it, and the module docstring's token never backs it."""
    path = (_BACKEND_APP / "services" / "chat_web_search_service.py", "run_web_search")
    code = _backing_text(path)
    assert _ONE_SEARCH_ELECTION in code
    unguarded = code.replace("if turn._task is None:\n        turn._task", "turn._task", 1)
    assert _ONE_SEARCH_ELECTION not in unguarded
    assert "ONE SEARCH PER TURN" not in code


def test_python_backing_text_strips_comments_but_keeps_literals(tmp_path):
    f = tmp_path / "x.py"
    f.write_text('URL = "https://api.search.brave.com/res"  # TOKEN_IN_COMMENT\n# another TOKEN\n')
    out = _backing_text(f)
    assert '"https://api.search.brave.com/' in out and "TOKEN" not in out


def test_the_default_notes_describe_only_what_the_backend_serves_today():
    """2026-10-09 review: the any-chat recipe was the DEFAULT while no backend could serve it — a
    stock page's chat had no web search at all, so an --apply would have submitted a false note
    (a 2.1 / 2.5.4 'cannot locate' risk). The default is the report-chat sentence, which the
    backend serves today; the any-chat one is opt-in behind the owner's confirmation."""
    ai_swap = dict((start, text) for start, text in mod.REPLACEMENTS if isinstance(start, str))
    assert ai_swap["AI-generated content."] == mod.AI_PARAGRAPH
    assert mod.AI_PARAGRAPH == mod.AI_PARAGRAPH_APPROVED + mod.WEB_SEARCH_REPORT_CHAT
    assert mod.AI_PARAGRAPH_ALL_CHATS == mod.AI_PARAGRAPH_APPROVED + mod.WEB_SEARCH_ALL_CHATS
    assert "Any chat" not in mod.AI_PARAGRAPH and '"Ask Cay AI"' not in mod.AI_PARAGRAPH
    assert mod.replacements() == mod.REPLACEMENTS
    default, all_chats = mod.replacements(False), mod.replacements(True)
    assert default[:-1] == all_chats[:-1], "the flag must change ONLY the AI paragraph"
    assert all_chats[-1] == ("AI-generated content.", mod.AI_PARAGRAPH_ALL_CHATS)


@pytest.mark.parametrize("sentence_name", ["WEB_SEARCH_REPORT_CHAT", "WEB_SEARCH_ALL_CHATS"])
def test_neither_sentence_claims_what_code_does_not_enforce(sentence_name):
    """No automatic-search recipe (that tier runs in shadow for a week first, so a reviewer
    would find nothing); no "only when the user asks" (the automatic tier, once on, makes it
    false); no promise about prices (that rule is a model instruction, not a code gate)."""
    sentence = getattr(mod, sentence_name)
    low = sentence.lower()
    assert "only when the user asks" not in low, "the automatic search would make this false"
    for automatic in ("by itself", "automatic", "can't answer", "cannot answer", "lawsuit"):
        assert automatic not in low, f"{sentence_name} gives an automatic-search recipe ({automatic!r})"
    assert "price" not in low, f"{sentence_name} promises something about prices that no code enforces"
    assert len(sentence) <= 389, f"{len(sentence)} chars — the live rewrite would pass {mod.NOTES_LIMIT - 50}"


def test_the_any_chat_recipe_cannot_be_routed_to_the_headlines_tool():
    """A "news" ask can be answered from the licensed-headlines tool instead of the web, so the
    any-chat recipe asks for something only the web has — and it keeps the report-chat recipe."""
    sentence = mod.WEB_SEARCH_ALL_CHATS
    assert '"search the web for ' in sentence, "the explicit-ask recipe is gone"
    recipe = sentence.split('"search the web for ', 1)[1].split('"', 1)[0]
    assert "news" not in recipe.lower() and "headline" not in recipe.lower(), recipe
    assert '"Chat with the report" bar' in sentence, "the report-chat recipe is gone"


def test_the_rewrite_with_the_any_chat_sentence_changes_only_the_ai_line_and_fits():
    out = mod.apply_replacements(_LIVE_NOTES, mod.replacements(True))
    before, after = _LIVE_NOTES.split("\n"), out.split("\n")
    assert len(before) == len(after)
    changed = [(b, a) for b, a in zip(before, after) if b != a]
    assert changed == [(mod.AI_PARAGRAPH_APPROVED, mod.AI_PARAGRAPH_ALL_CHATS)]
    assert len(out) <= mod.NOTES_LIMIT - 50, f"{len(out)} chars"
    assert mod.apply_replacements(out, mod.replacements(True)) == out  # idempotent
    # Re-running WITHOUT the flag (the switch went off) puts the report-chat sentence back.
    assert mod.apply_replacements(out, mod.REPLACEMENTS) == mod.apply_replacements(_LIVE_NOTES, mod.REPLACEMENTS)


_FIELD = "CHAT_WEB_SEARCH_ALL_CHATS_ENABLED"
_CONSENT = "CHAT_WEB_SEARCH_MIN_CONSENT_VERSION"


@pytest.mark.parametrize(
    "config_text, refused",
    [
        (None, True),                                                                   # no file
        ("class Settings:\n    OTHER: bool = False\n", True),                            # absent
        (f"class Settings:\n    # {_FIELD}: bool = False\n    X: int = 1\n", True),        # commented out
        (f"class Settings:\n    X: str = '{_FIELD}'\n", True),                          # only a string
        (f'class Settings:\n    """Switches.\n{_FIELD}: opens every chat\n"""\n', True),  # a docstring line
        (f"class Other:\n    {_FIELD}: bool = False\n", True),                          # another class
        (f"{_FIELD}: bool = False\n", True),                                            # module level
        (f"class Settings:\n    {_FIELD}_V2: bool = False\n", True),                      # a longer name
        (f"class Settings:\n    {_FIELD}: bool = (\n", True),                             # not Python
        # The switch alone is not enough (2026-10-09): the consent gate must be declared, ≥ 3.
        (f"class Settings:\n    {_FIELD}: bool = False\n", True),
        (f"class Settings:\n    {_FIELD}: bool = False\n    {_CONSENT}: int = 2\n", True),
        (f"class Settings:\n    {_FIELD}: bool = False\n    {_CONSENT}: int = Field(2, ge=2)\n", True),
        (f"class Settings:\n    {_FIELD}: bool = False\n    {_CONSENT}: int = True\n", True),
        (f"class Settings:\n    {_FIELD}: bool = False\n    {_CONSENT}: str = '3'\n", True),
        (f"class Settings:\n    {_FIELD}: bool = False\n    {_CONSENT}: int = LIMIT\n", True),
        (f"class Settings:\n    {_FIELD}: bool = False\nclass Other:\n    {_CONSENT}: int = 3\n", True),
        (f"class Settings:\n    {_FIELD}: bool = False\n    {_CONSENT}: int = 3\n", False),
        (f"class Settings(BaseSettings):\n    {_FIELD} : bool = Field(False)\n"
         f"    {_CONSENT}: int = Field(3, ge=3, le=999)\n", False),
        (f"class Settings:\n    {_FIELD}: bool = False\n    {_CONSENT}: int = Field(default=4)\n", False),
    ],
)
def test_the_any_chat_sentence_is_refused_until_the_backend_declares_its_switch(tmp_path, config_text, refused):
    config = tmp_path / "config.py"
    if config_text is not None:
        config.write_text(config_text, encoding="utf-8")
    problem = mod.all_chats_web_search_problem(config)
    assert (problem is not None) is refused, problem
    if refused:
        assert mod.ALL_CHATS_SWITCH in problem


def test_an_unreadable_config_refuses_rather_than_raises(tmp_path):
    config = tmp_path / "config.py"
    config.write_bytes(b"\xff\xfe\x00 not utf-8 \x80")
    problem = mod.all_chats_web_search_problem(config)
    assert problem is not None and "cannot read" in problem


def test_the_refusal_agrees_with_the_real_backend_config():
    """Not vacuous in either state: without the switch (or its consent gate) the any-chat sentence
    is refused; with both declared on `Settings` (PLAN A8, 2026-10-09) it is allowed."""
    import ast as _ast
    tree = _ast.parse((_BACKEND_APP / "config.py").read_text(encoding="utf-8"))
    settings = next(n for n in _ast.walk(tree) if isinstance(n, _ast.ClassDef) and n.name == "Settings")
    declared = any(isinstance(s, _ast.AnnAssign) and getattr(s.target, "id", None) == _FIELD
                   for s in settings.body)
    gated = any(isinstance(s, _ast.AnnAssign) and getattr(s.target, "id", None) == _CONSENT
                for s in settings.body)
    assert (mod.all_chats_web_search_problem() is None) is (declared and gated)


def test_the_script_and_the_backend_agree_on_the_names_and_the_consent_floor():
    """The script reads config.py by NAME (it never imports `app.*`): pin both names to the real
    settings, the floor to the Settings default and to its `ge=` bound — a lower env value would
    then fail the deploy, never quietly open every-chat search for an older consent."""
    from app.config import Settings
    assert mod.ALL_CHATS_SWITCH == _FIELD and mod.CONSENT_GATE_SETTING == _CONSENT
    assert mod.ALL_CHATS_SWITCH in Settings.model_fields and mod.CONSENT_GATE_SETTING in Settings.model_fields
    assert Settings.model_fields[_CONSENT].default >= mod.MIN_CONSENT_FOR_ALL_CHATS == 3
    ge = [m.ge for m in Settings.model_fields[_CONSENT].metadata if getattr(m, "ge", None) is not None]
    assert ge and ge[0] >= 3, "a consent gate below 3 must fail the deploy"


def test_the_any_chat_recipe_reads_as_an_explicit_ask_not_a_news_ask():
    """The any-chat recipe must FORCE the web search (an explicit ask), never route to the licensed
    headlines first (a news ask) — or the reviewer may see no web search at all."""
    from app.services.chat_intent import web_ask_kind
    assert web_ask_kind("search the web for Apple's latest product launch") == "explicit"
    assert "search the web for Apple's latest product launch" in mod.WEB_SEARCH_ALL_CHATS


def test_choose_replacements_refuses_the_flag_when_the_switch_is_missing(monkeypatch):
    monkeypatch.setattr(mod, "all_chats_web_search_problem", lambda *a, **k: "no switch")
    assert mod.choose_replacements(True) == ([], "no switch")
    reps, why = mod.choose_replacements(False)
    assert why is None and reps == mod.REPLACEMENTS


def test_choose_replacements_without_the_flag_never_consults_the_switch(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("the default path must not depend on the any-chat switch")
    monkeypatch.setattr(mod, "all_chats_web_search_problem", _boom)
    assert mod.choose_replacements(False) == (mod.REPLACEMENTS, None)


def test_choose_replacements_with_the_switch_present_gives_the_any_chat_sentence(monkeypatch):
    monkeypatch.setattr(mod, "all_chats_web_search_problem", lambda *a, **k: None)
    reps, why = mod.choose_replacements(True)
    assert why is None and reps == mod.replacements(True)


def test_main_refuses_the_any_chat_flag_before_any_credential_or_network_work(monkeypatch, capsys):
    def _never(*a, **k):
        raise AssertionError("main reached credentials / ASC despite the refusal")
    monkeypatch.setattr(mod, "all_chats_web_search_problem", lambda *a, **k: "no switch in config.py")
    monkeypatch.setattr(mod, "_fill_credentials_from_dotenv", _never)
    monkeypatch.setattr(mod, "_load_asc", _never)
    monkeypatch.setattr(mod.sys, "argv", ["asc_review_resubmit.py", "--apply", "--all-chats-web-search-is-on"])
    assert mod.main() == 1
    assert "no switch in config.py" in capsys.readouterr().out


def test_the_stock_page_chat_bar_is_the_ask_cay_ai_bar_the_notes_name():
    """The any-chat recipe says to type in a stock's "Ask Cay AI" bar. That bar is
    `TickerDetailAIBar`, which must keep `CaydexAIChatBar`'s default placeholder — an override
    would rename the bar the reviewer is told to look for."""
    detail = _swift_code(_SWIFT / "Views" / "Screens" / "TickerDetailView.swift")
    assert "TickerDetailAIBar(" in detail, "the stock page no longer uses TickerDetailAIBar"
    assert "contextType: .stock" in detail, "the stock page's chat is no longer a stock chat"
    bar = _swift_code(_SWIFT / "Views" / "Molecules" / "TickerDetailAIBar.swift")
    body = bar[bar.index("var body: some View"):bar.index("#Preview")]
    assert "CaydexAIChatBar(" in body, "TickerDetailAIBar no longer renders CaydexAIChatBar"
    assert "placeholder:" not in body, (
        "TickerDetailAIBar overrides the placeholder, so the bar no longer reads 'Ask Cay AI'"
    )


def test_the_shipped_consent_version_discloses_what_the_any_chat_notes_describe():
    """Every-chat search opens only for an accepted consent of 3+ (the `X-AI-Consent-Version`
    header). If the build under review shipped an older consent text, or stopped sending the
    header, the reviewer — who consents on that build — could never reach the any-chat recipe.
    Comment-stripped and brace-bound to `buildRequest` and its guarded read (testing.md §3)."""
    import re as _re
    store = _swift_code(_SWIFT / "Core" / "Services" / "AIConsentStore.swift")
    match = _re.search(r"static let currentVersion\s*=\s*(\d+)", store)
    assert match and int(match.group(1)) >= 3, "the build's consent text predates every-chat search"
    build = _brace_block(_swift_code(_SWIFT / "Core" / "Services" / "APIClient.swift"),
                         "private func buildRequest(for endpoint: APIEndpoint")
    guarded = _brace_block(build, "if let consentVersion = AIConsentStore.acceptedVersionForRequests()")
    assert 'request.setValue(String(consentVersion), forHTTPHeaderField: "X-AI-Consent-Version")' in guarded


def test_the_version_under_review_actually_gets_the_web_search_the_notes_describe():
    """The notes tell App Review to try web search, which is gated on the app version
    (`WEB_SEARCH_MIN_APP_VERSION`). If the gate ever moved above the version submitted, the
    reviewer could not find the feature — a 2.5.4-style 'cannot locate' rejection."""
    import re as _re
    from app.services.chat_web_search_service import WEB_SEARCH_MIN_APP_VERSION
    from app.core.client_app_version import parse_app_version

    pbx = (_REPO / "frontend" / "ios" / "ios.xcodeproj" / "project.pbxproj").read_text()
    versions = set(_re.findall(r"MARKETING_VERSION = ([0-9.]+);", pbx))
    assert len(versions) == 1, versions
    marketing_version = versions.pop()
    shipped = parse_app_version(marketing_version)
    assert shipped is not None and shipped >= WEB_SEARCH_MIN_APP_VERSION, (shipped, WEB_SEARCH_MIN_APP_VERSION)
    # EXACT string: ASC's filter[versionString] matches text, so "1.1.0" would find 0 versions.
    assert mod._VERSION == marketing_version, (mod._VERSION, marketing_version)


_IOS = _REPO / "frontend" / "ios" / "ios"


@pytest.mark.parametrize(
    "label, swift_file",
    [
        ('"General Settings"', "Views/Screens/ProfileView.swift"),
        ('"Plans"', "Views/Screens/ProfileView.swift"),
        ('"Add Credits"', "Views/Screens/ProfileView.swift"),
        ('"DANGER ZONE"', "Views/Screens/AppSettingsView.swift"),
        ('"Delete Account"', "Views/Screens/AppSettingsView.swift"),
        ('"AI-Enabled Books"', "Views/Organisms/AIBooksSection.swift"),
        ('"Money Moves"', "Views/Organisms/MoneyMovesSection.swift"),
        ('"Listen Now"', "Views/Atoms/PlayAudioButton.swift"),
        ('"Wiser"', "Models/HomeModels.swift"),
        ('"About & Legal"', "Views/Screens/ProfileView.swift"),
        ('"Disclaimers"', "Views/Screens/ProfileView.swift"),
    ],
)
def test_every_ui_label_the_notes_cite_exists(label, swift_file):
    """A tap path in the notes that names a label the app no longer shows is how a reviewer
    'cannot locate' a feature — the 2.5.4 rejection in one sentence."""
    assert label in (_IOS / swift_file).read_text(encoding="utf-8"), f"{label} not in {swift_file}"


# ── description / promo / IAP notes ─────────────────────────────────────────────────────


def test_swap_text_is_exact_idempotent_and_refuses_ambiguity():
    assert mod.swap_text("a X b", "X", "Y") == "a Y b"
    assert mod.swap_text("a Y b", "X", "Y") == "a Y b"  # already applied
    with pytest.raises(mod.NotesError, match="found 0"):
        mod.swap_text("nothing here", "X", "Y")
    with pytest.raises(mod.NotesError, match="found 2"):
        mod.swap_text("X and X", "X", "Y")


def test_apply_swaps_enforces_the_limit():
    with pytest.raises(mod.NotesError, match="over the 5 limit"):
        mod.apply_swaps("X", [("X", "YYYYYYYY")], limit=5)


def test_promotional_text_fits():
    assert len(mod.PROMOTIONAL_TEXT) <= mod.PROMOTIONAL_LIMIT
    assert not mod.PROMOTIONAL_TEXT.lower().startswith("new")


def test_the_plan_claims_match_the_real_entitlements():
    """2.3.2: the description must say what needs a plan — and must not say MORE than is true."""
    from app.services import entitlements as ent
    pro_max = {ent.TIER_PRO, ent.TIER_MAX}
    assert ent.SIGNALS_UNLOCKED_TIERS == pro_max
    assert ent.WHALE_DETAIL_UNLOCKED_TIERS == pro_max
    assert ent.CONGRESS_HOLDERS_UNLOCKED_TIERS == pro_max
    assert ent.LEARN_AUDIO_UNLOCKED_TIERS == pro_max
    assert ent.TIER_FREE in ent.JOURNEY_AUDIO_UNLOCKED_TIERS  # "Investor Journey narration is free"
    assert ent.UPDATES_TICKER_LIMITS[ent.TIER_FREE] < ent.UPDATES_TICKER_LIMITS[ent.TIER_PRO]
    assert ent.WHALE_FOLLOW_LIMITS[ent.TIER_FREE] == 1  # "following more investors"
    blob = " ".join(new for _, new in mod.DESCRIPTION_SWAPS)
    for phrase in ("signal tickers", "congressional data", "Money Moves and book narration",
                   "Investor Journey narration is free"):
        assert phrase in blob, phrase


def test_iap_notes_name_paths_that_exist():
    profile = (_IOS / "Views/Screens/ProfileView.swift").read_text(encoding="utf-8")
    assert '"Add Credits"' in profile and '"Plans"' in profile
    assert "Profile → Add Credits" in mod.CREDIT_PACK_NOTE
    assert "Profile → Plans" in mod.SUBSCRIPTION_NOTE_SWAP[1]


def test_a_reworded_paragraph_is_found_after_the_first_apply():
    """After the first --apply the live paragraph starts 'Background audio', not 'Background
    modes.' — a later wording fix must still find and replace it (not 'no paragraph')."""
    first = mod.apply_replacements(_LIVE_SHAPE, mod.REPLACEMENTS)
    edited = first.replace(mod.NEW_PARAGRAPH, "Background audio (UIBackgroundModes: audio). OLD WORDING.")
    again = mod.apply_replacements(edited, mod.REPLACEMENTS)
    assert "OLD WORDING" not in again and again.count(mod.NEW_PARAGRAPH) == 1


def test_the_notes_never_claim_an_attachment_asc_cannot_hold():
    """ASC allows ONE review attachment and it is the FMP Order Form (409 'max of 1 attachment',
    2026-09-24). A notes line saying the recording is 'attached to App Review Information' was
    false the moment it was written."""
    assert "attached to App Review Information" not in mod.NEW_PARAGRAPH
    assert "Resolution Center" in mod.NEW_PARAGRAPH
