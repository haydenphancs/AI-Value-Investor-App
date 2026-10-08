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


def test_every_claim_in_the_web_search_sentence_is_backed_by_code():
    added = mod.AI_PARAGRAPH[len(mod.AI_PARAGRAPH_APPROVED):]
    backing = [
        ("one web search per question",
         _REPO / "backend" / "app" / "services" / "chat_web_search_service.py", "ONE SEARCH PER TURN"),
        ("Brave Search API", _REPO / "backend" / "app" / "config.py", '"https://api.search.brave.com/'),
        ('tap "Web search"', _SWIFT / "Views" / "Molecules" / "ThinkingProcessCard.swift", 'text: "Web search"'),
        ("in-app Safari view", _SWIFT / "Views" / "Screens" / "AIChatScreen.swift", ".inAppBrowser(link: $browserLink"),
        ("AI consent sheet", _SWIFT / "Views" / "Screens" / "AIDataConsentView.swift", "search the web in a report chat"),
        ("Privacy Policy", _SWIFT / "Views" / "Screens" / "PrivacyPolicyView.swift", "Brave"),
        ('"Chat with the report"', _SWIFT / "Views" / "Screens" / "TickerReportView.swift", 'placeholder: "Chat with the report'),
    ]
    for claim, path, token in backing:
        assert claim in added, f"the notes no longer say {claim!r} — update this table"
        assert token in path.read_text(encoding="utf-8"), f"{claim!r} is no longer true: {token!r} not in {path.name}"


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
