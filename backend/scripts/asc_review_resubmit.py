#!/usr/bin/env python3
"""Answer the 2026-09-24 Guideline 2.5.4 rejection in App Store Connect's review metadata.

WHY
---
Caydex 1.0 (9) was rejected because App Review "could not locate any features that require
persistent audio". The feature is real — Learn narration keeps playing on the Home and Lock
Screen — but the review notes described it in one sentence with no path to it, and Apple asked
for a physical-device screen recording "in the Notes field of the App Review Information
section ... for future submissions". This script does the two metadata halves of that:

  1. Replaces the notes paragraph that starts "Background modes." with one that gives the
     reviewer a tap-by-tap path to the narration and points at the recording, and corrects
     three more that the 2026-09-24 pre-resubmission audit found inaccurate: the demo-account
     paragraph (deletion path; how to buy each IAP from a Max account), the library paragraph
     ("Learn" is the Wiser tab) and the age rating (18+ is selected, not 17+). Every other
     paragraph is left byte-identical.
  2. Uploads the recording as the App Review attachment — ONLY when the slot is free. ASC
     allows exactly one attachment (verified 2026-09-24: 409 "There can be max of 1
     attachment"), and Caydex's holds the signed FMP Order Form, so in practice the recording
     is attached to the Resolution Center reply and this step reports that and stops.

The Resolution Center REPLY is not here on purpose: it is a message sent on the developer's
behalf, and the developer sends it.

1.01 (2026-10-05; named 1.01 on 2026-10-08 — Apple reads it as 1.1): 1.0 (10) was approved and
released. `--version` (default 1.01) points the script
at the next version, whose notes ASC copies from 1.0 — the four 2026-09-24 paragraphs are already
there and no-op; the one change left is the AI paragraph's web-search sentence. It has TWO
versions (2026-10-09): by default the report-chat recipe (WEB_SEARCH_REPORT_CHAT), which the
backend serves today; with `--all-chats-web-search-is-on` a recipe for web search in any chat
(WEB_SEARCH_ALL_CHATS), which the owner passes only after turning that switch on in production
for the whole review — the script refuses it while this checkout's backend has no such switch.
Step 2 now reports a MISSING Order Form attachment (the notes promise it) and never fills the
only slot with the recording unless `--allow-video-in-empty-slot` is passed.

DEFAULTS TO --dry-run. The original notes are written to a backup directory first.

    ./venv/bin/python scripts/asc_review_resubmit.py                     # 1.01, dry run
    ./venv/bin/python scripts/asc_review_resubmit.py --apply             # 1.01, write
    ./venv/bin/python scripts/asc_review_resubmit.py --apply --all-chats-web-search-is-on   # switch ON in prod
    ./venv/bin/python scripts/asc_review_resubmit.py --version 1.0 --video ~/Desktop/caydex-bg-audio.mov

Credentials: ASC_KEY_ID / ASC_ISSUER_ID / ASC_PRIVATE_KEY_PATH from the environment, else from
backend/.env (via `pull_testflight_feedback._credentials`). Values are never printed.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
_BUNDLE = "com.phan.caydex"
_VERSION = "1.01"  # the next App Store version (Apple and the backend read it as 1.1); 1.0 was approved 2026-10-05
_BACKUP = Path(os.environ.get("ASC_BACKUP_DIR") or tempfile.gettempdir()) / "asc-review-resubmit-backup"

# App Store Connect's hard cap on App Review Information → Notes.
NOTES_LIMIT = 4000

# The paragraph being replaced begins with this. Matched on the stripped paragraph start so a
# leading space or a trailing edit elsewhere in the paragraph does not defeat it.
# A tuple: the ORIGINAL live paragraph began "Background modes."; after the first --apply it
# begins "Background audio". Either is this script's paragraph, so a later wording fix still
# finds it (str.startswith takes a tuple).
OLD_PARAGRAPH_START = ("Background modes.", "Background audio")

# ⚠️ Keep every paragraph below TRUE against the build under review. They name tap paths; if
# a screen is reorganised, re-walk the path on a device and edit this text before the next
# submission — a wrong path in the notes is how a reviewer "cannot locate" a real feature.
NEW_PARAGRAPH = (
    "Background audio (UIBackgroundModes: audio). Caydex narrates its education library, and "
    "the narration keeps playing after the user leaves the app or locks the screen, with play, "
    "pause, skip 15 s and scrubbing on the Lock Screen and in Control Center. Narration is a "
    "Pro/Max feature; the demo account is on Max, so it plays with no purchase. To hear it: "
    "sign in, tap the Wiser tab (last tab), tap the first Money Moves article, then tap Listen "
    "Now. Books work the same way (Wiser > AI-Enabled Books > any book > Listen Now). Then go "
    "to the Home Screen or lock the device: the narration continues. A screen recording of "
    "this on a physical iPhone accompanies our Resolution Center reply to the 2.5.4 rejection "
    "of 1.0 (9). The app declares no other background mode."
)

DEMO_PARAGRAPH = (
    "Demo account — please use this to review. Caydex requires an account. Our market-data "
    "licence (the signed Order Form is attached) grants End-User Display Rights only, which "
    "permit the data to be displayed solely through the licensee's authenticated platform, so we "
    "cannot serve it to a signed-out caller. The app also contains significant account-based "
    "features — credits, paid AI reports, subscriptions, watchlists and portfolios — and "
    "provides Sign in with Apple and in-app account deletion (tap the profile picture at the top "
    "right of Home > General Settings > Delete Account, under Danger Zone). Credentials are in "
    "the App Review sign-in fields. The account is on the Max plan and pre-loaded with credits, "
    "so every feature, including narration, works without a purchase. Every in-app purchase can "
    "still be bought in sandbox from this account: Profile > Plans for the Pro and Max "
    "subscriptions, and Profile > Add Credits for the four credit packs."
)

LIBRARY_PARAGRAPH = (
    "Educational library. The Wiser tab contains original study guides written by us that "
    "summarise the ideas of ten well-known investing books, plus original lessons and articles. "
    "No book text is reproduced; all narration audio is of our own writing."
)

AGE_PARAGRAPH = (
    "Age rating 18+. Our Terms require users to be 18 or the age of majority. The app contains "
    "no gambling, no unrestricted web access and no user-generated content visible to other "
    "users."
)

# The AI paragraph exactly as 1.0 was APPROVED with it (2026-10-05), plus one sentence for 1.01's
# web search (2026-10-02). Report chat's explicit search is gated to app versions ≥ 1.1 — 1.01
# parses as 1.1 (`chat_web_search_service.WEB_SEARCH_MIN_APP_VERSION`), so the reviewer of 1.01
# can use it while 1.0's in-app copy, which predates it, never meets it.
AI_PARAGRAPH_APPROVED = (
    "AI-generated content. Company analysis, the written research reports, and the in-app chat "
    "are generated by a large language model and labelled as AI-generated throughout. The app "
    "also displays several of its own computed indicators on named securities — a technical "
    "Buy/Sell meter, an estimated fair value, and a 0-100 company score. These are deterministic "
    "outputs of published formulas over public financial data, presented as information for the "
    "user's own research, not as a recommendation or personalised advice. Every one of these "
    "surfaces carries a \"not financial advice\" disclaimer, and the app requires a first-run "
    "acknowledgement before any analysis is shown. See Profile → About & Legal → Disclaimers."
)
# The web-search sentence, in two versions (2026-10-09). Every claim in each is tied to code by
# tests/test_asc_review_resubmit.py: one search per turn, the Brave integration, the "Web search"
# header that lists the sources (a date only when Brave gave one), the in-app Safari view, the
# consent-sheet row, the Privacy Policy, the report's "Chat with the report" bar and, for the
# any-chat version, a stock page's "Ask Cay AI" bar. Neither says "only when the user asks":
# the automatic search (consent v3) would make that false the day it is switched on. Neither gives
# an automatic-search recipe either — that tier runs in shadow first (PLAN: switch order), so a
# reviewer during that week would find nothing; add one only for a review window in which
# CHAT_AUTO_WEB_SEARCH_MODE is "on", behind its own confirmation flag. Budget: the live 1.0 notes
# are 3,561 chars and the test keeps the rewrite 50 under ASC's 4,000 cap, so each sentence has
# at most 389 chars.
#
# DEFAULT — true against today's backend: report chat, on the user's ask.
WEB_SEARCH_REPORT_CHAT = (
    " Report chat can also run one web search per question when the user asks (e.g. "
    "\"search the web for the latest news\"), via the Brave Search API; tap \"Web search\" above "
    "the answer for each source (publisher, and date when known), opening in an in-app Safari "
    "view. The Privacy Policy and AI consent sheet disclose it. Try it from any AI report's "
    "\"Chat with the report\" bar."
)
# ⚠️ TRUE ONLY while the backend's every-chat switch (`CHAT_WEB_SEARCH_ALL_CHATS_ENABLED`, plus
# the master `CHAT_REPORT_WEB_SEARCH_ENABLED`) is ON in production for the WHOLE review window —
# with it off, a stock page's chat has no web search and the reviewer "cannot locate" the
# feature (2.1 / 2.5.4). Selected only by `--all-chats-web-search-is-on`, which the owner passes
# after flipping the switch on Railway, and refused while this checkout has no such switch
# (`all_chats_web_search_problem`). The explicit recipe avoids the word "news" so it cannot be
# routed to the licensed-headlines tool instead of the web.
WEB_SEARCH_ALL_CHATS = (
    " Any chat can also run one web search per question when the user asks, via the Brave "
    "Search API: type \"search the web for Apple's latest product launch\" in a stock's "
    "\"Ask Cay AI\" bar or an AI report's \"Chat with the report\" bar, then tap \"Web search\" "
    "above the answer for its sources (publisher, date when known; in-app Safari view). The "
    "Privacy Policy and AI consent sheet disclose it."
)
AI_PARAGRAPH = AI_PARAGRAPH_APPROVED + WEB_SEARCH_REPORT_CHAT
AI_PARAGRAPH_ALL_CHATS = AI_PARAGRAPH_APPROVED + WEB_SEARCH_ALL_CHATS

# The backend setting that opens web search in every chat (PLAN A8). The any-chat sentence is
# refused while backend/app/config.py does not DECLARE it as a `Settings` field: the owner cannot
# have switched on, in production, a switch the shipped code does not have.
ALL_CHATS_SWITCH = "CHAT_WEB_SEARCH_ALL_CHATS_ENABLED"
# The consent gate beside it: every-chat search opens only for an `X-AI-Consent-Version` of at
# least this setting, and the notes (like the Privacy Policy's consent clause) are true only while
# it is at least 3 — consent v3 is the permission screen that discloses every-chat search.
CONSENT_GATE_SETTING = "CHAT_WEB_SEARCH_MIN_CONSENT_VERSION"
MIN_CONSENT_FOR_ALL_CHATS = 3
_CONFIG_PY = _HERE.parent / "app" / "config.py"


def _declared_int_default(value: Optional[ast.expr]) -> Optional[int]:
    """The declared default of a `Settings` field: `NAME: int = 3`, `Field(3, …)` or
    `Field(default=3, …)` — an int literal only (never a bool), else None."""
    if isinstance(value, ast.Call) and getattr(value.func, "id", getattr(value.func, "attr", None)) == "Field":
        candidates = list(value.args[:1]) + [k.value for k in value.keywords if k.arg == "default"]
        value = candidates[0] if candidates else None
    if isinstance(value, ast.Constant) and isinstance(value.value, int) and not isinstance(value.value, bool):
        return value.value
    return None


def all_chats_web_search_problem(config_path: Path = _CONFIG_PY) -> Optional[str]:
    """Why the any-chat web-search sentence cannot be submitted yet, or None.

    A source check on purpose (this script never imports `app.*`, whose Settings need the
    backend's environment), done on the AST: the switch must be a field DECLARED in
    `class Settings` (`NAME: bool = ...`) — a comment, a string, a docstring line or a field of
    another class does not count — AND the consent gate (`CHAT_WEB_SEARCH_MIN_CONSENT_VERSION`)
    must be declared there with a default of at least 3."""
    try:
        tree = ast.parse(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError, ValueError) as e:
        return f"cannot read {config_path.name} to confirm {ALL_CHATS_SWITCH} exists ({type(e).__name__})"
    switch = False
    consent: Optional[int] = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for stmt in node.body:
                if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                    continue
                if stmt.target.id == ALL_CHATS_SWITCH:
                    switch = True
                elif stmt.target.id == CONSENT_GATE_SETTING:
                    consent = _declared_int_default(stmt.value)
    if not switch:
        return (
            f"{config_path.name} declares no {ALL_CHATS_SWITCH} setting, so no production switch can "
            f"be on: the any-chat recipe would send the reviewer to a stock-page web search that "
            f"does not exist. Submit without --all-chats-web-search-is-on (report-chat recipe)."
        )
    if consent is None or consent < MIN_CONSENT_FOR_ALL_CHATS:
        return (
            f"{config_path.name} declares {ALL_CHATS_SWITCH} but its consent gate "
            f"{CONSENT_GATE_SETTING} is missing or below {MIN_CONSENT_FOR_ALL_CHATS} "
            f"({consent!r}): every-chat search would open for a consent that never disclosed it. "
            f"Submit without --all-chats-web-search-is-on (report-chat recipe)."
        )
    return None


def replacements(all_chats_web_search_on: bool = False) -> List[Tuple[object, str]]:
    """Every paragraph swap, with the AI paragraph's web-search sentence chosen by the owner's
    confirmation that the every-chat switch is ON in production (default: off → report chat)."""
    return [
        (OLD_PARAGRAPH_START, NEW_PARAGRAPH),
        ("Demo account", DEMO_PARAGRAPH),
        ("Educational library.", LIBRARY_PARAGRAPH),
        ("Age rating", AGE_PARAGRAPH),
        ("AI-generated content.", AI_PARAGRAPH_ALL_CHATS if all_chats_web_search_on else AI_PARAGRAPH),
    ]


def choose_replacements(all_chats_web_search_on: bool) -> Tuple[List[Tuple[object, str]], Optional[str]]:
    """(the swaps to apply, a refusal) — refused when the owner claims the every-chat switch is
    on but this checkout's backend has no such switch."""
    if all_chats_web_search_on:
        why = all_chats_web_search_problem()
        if why:
            return [], why
    return replacements(all_chats_web_search_on), None


# (paragraph start marker, replacement). Each marker must match exactly one live paragraph.
# The DEFAULT swaps (report-chat web-search sentence) — `replacements(True)` for the any-chat one.
REPLACEMENTS = replacements()

# ── version metadata (description / promotional text / copyright) ─────────────────────
#
# Guideline 2.3.2: an app description must make clear which features need an additional
# purchase. The 2026-09-24 audit (critic-1, confirmed) found the description advertising
# congressional trades, investor holdings and narration — all Pro/Max-gated in
# `entitlements.py` — with the only plan difference named being "Pro and Max raise [credits]".
# Each swap is exact-match: a live text that no longer contains `old` (and does not already
# contain `new`) is REFUSED, never guessed around.
DESCRIPTION_SWAPS = [
    (
        "with both filing and disclosure dates shown, because the gap between them matters.",
        "with both filing and disclosure dates shown, because the gap between them matters. "
        "Full holdings, trades and congressional data come with Pro or Max.",
    ),
    (
        "Narrated, with read-along highlighting, and it keeps playing when your screen locks.",
        "Narrated, with read-along highlighting, and it keeps playing when your screen locks "
        "(Money Moves and book narration with Pro or Max; Investor Journey narration is free).",
    ),
    (
        "Every account includes a monthly credit allowance for AI reports and chat. Pro and Max "
        "raise it, and credit packs are there if you want more.",
        "Every account includes a monthly credit allowance for AI reports and chat. Pro and Max "
        "raise it and unlock signal tickers, full investor holdings and trades, congressional "
        "data, following more investors, more Updates tickers, and Money Moves and book "
        "narration. Credit packs are there if you want more.",
    ),
    (
        "Terms of Use: https://caydexinvest.com/terms",
        "Terms of Use: https://caydexinvest.com/terms\n"
        "Apple Standard EULA: https://www.apple.com/legal/internet-services/itunes/dev/stdeula/",
    ),
]
DESCRIPTION_LIMIT = 4000

PROMOTIONAL_TEXT = (
    "Research any public company, see what actually moved a stock today, and follow "
    "institutional and congressional filings with Pro or Max."
)
PROMOTIONAL_LIMIT = 170

COPYRIGHT = "2026 Hayden Phan"

# ── in-app purchase review notes ─────────────────────────────────────────────────────────
SUBSCRIPTION_NOTE_SWAP = (
    "Reached from Profile → Upgrade Plan, shown in the screenshot.",
    "Reached from Profile → Upgrade Plan on a Free account, or Profile → Plans on a Pro/Max "
    "account; the review demo account is on Max and can still buy both Pro and Max in sandbox.",
)
SUBSCRIPTION_IDS = ("6802485159", "6802485389")  # pro.monthly, max.monthly

CREDIT_PACK_NOTE = (
    "Consumable credit pack. Reached from Profile → Add Credits (sign-in required; the test "
    "account is in the App Review sign-in fields). Credits are spent only inside the app on AI "
    "reports and Cay AI chat, never expire, and cannot be transferred or cashed out."
)
CREDIT_PACK_IDS = ("6802487770", "6802489829", "6802489853", "6802490274")


def swap_text(text: str, old: str, new: str) -> str:
    """Replace ONE exact occurrence of `old` with `new`; idempotent; refuses when ambiguous."""
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise NotesError(f"expected exactly one {old[:60]!r}…, found {count} — not touching it")
    return text.replace(old, new, 1)


def apply_swaps(text: str, swaps, limit: int) -> str:
    out = text
    for old, new in swaps:
        out = swap_text(out, old, new)
    if len(out) > limit:
        raise NotesError(f"result is {len(out)} chars, over the {limit} limit")
    return out


_VIDEO_TYPES = {".mov", ".mp4", ".m4v"}


class NotesError(ValueError):
    """The notes cannot be rewritten safely — never guessed around."""


def replace_paragraph(notes: str, start: str, new_paragraph: str, limit: int = NOTES_LIMIT) -> str:
    """Swap the ONE paragraph beginning with `start` for `new_paragraph`.

    Idempotent: notes that already carry `new_paragraph` come back unchanged. Refuses (raises
    `NotesError`) rather than guessing when the paragraph is missing, appears twice, or the
    result would exceed App Store Connect's limit — a silently truncated or duplicated
    paragraph is exactly the reviewer-facing mess this exists to prevent.
    """
    if new_paragraph in notes:
        if len(notes) > limit:
            raise NotesError(f"notes are {len(notes)} chars, over the {limit} limit")
        return notes
    lines = notes.split("\n")
    hits = [i for i, line in enumerate(lines) if line.strip().startswith(start)]
    if not hits:
        raise NotesError(f"no paragraph starts with {start!r} — not touching the notes")
    if len(hits) > 1:
        raise NotesError(f"{len(hits)} paragraphs start with {start!r} — ambiguous, not touching")
    lines[hits[0]] = new_paragraph
    out = "\n".join(lines)
    if len(out) > limit:
        raise NotesError(
            f"rewritten notes would be {len(out)} chars, over the {limit} limit — shorten "
            f"NEW_PARAGRAPH by {len(out) - limit}"
        )
    return out


def apply_replacements(notes: str, replacements, limit: int = NOTES_LIMIT) -> str:
    """Apply every (start, paragraph) swap, then enforce the limit ONCE on the result.

    Checked at the end, not per step: an intermediate state may be longer than the final one
    (a later paragraph can shrink), and it is only the final text App Store Connect stores.
    """
    out = notes
    for start, paragraph in replacements:
        out = replace_paragraph(out, start, paragraph, limit=10**9)
    if len(out) > limit:
        raise NotesError(
            f"rewritten notes would be {len(out)} chars, over the {limit} limit — shorten "
            f"the replacement paragraphs by {len(out) - limit}"
        )
    return out


def attachment_plan(
    names: List[str], video_name: Optional[str], *, allow_video_in_empty_slot: bool = False,
) -> "tuple[str, List[str]]":
    """Step 2's decision, pure: (action, problems). Action is one of `skip_no_video`,
    `skip_already_attached`, `refuse` or `upload`.

    * An EMPTY slot is always a problem: the demo-account paragraph tells App Review "the signed
      Order Form is attached" — the licence proof behind the account-only wall — and a NEW version
      (1.1) may start empty (whether ASC carries the attachment over is unverified).
    * ASC allows ONE attachment (409 "There can be max of 1 attachment", 2026-09-24): an occupied
      slot is never replaced, and an empty one is reserved for the Order Form unless overridden.
    """
    problems: List[str] = []
    if not names:
        problems.append(
            "no App Review attachment, but the notes say the signed Order Form is attached — "
            "attach it (App Review Information → Attachment) before submitting"
        )
    if video_name is None:
        return "skip_no_video", problems
    if video_name in names:
        return "skip_already_attached", problems
    if names:
        problems.append(
            f"ASC allows one review attachment and {names} already holds it — not replacing it. "
            "Attach the recording to the Resolution Center reply instead."
        )
        return "refuse", problems
    if not allow_video_in_empty_slot:
        problems.append(
            "the only review-attachment slot is reserved for the signed Order Form — not filling "
            "it with the recording (pass --allow-video-in-empty-slot to override)"
        )
        return "refuse", problems
    return "upload", problems


def video_problem(path: Path) -> Optional[str]:
    """Why this file cannot be the recording, or None when it can."""
    if not path.exists():
        return f"not found: {path}"
    if path.suffix.lower() not in _VIDEO_TYPES:
        return f"{path.name}: expected one of {sorted(_VIDEO_TYPES)}"
    if path.stat().st_size == 0:
        return f"{path.name} is empty"
    return None


def _load_asc():
    spec = importlib.util.spec_from_file_location("asc_audit", _HERE / "asc_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fill_credentials_from_dotenv() -> None:
    """asc_audit reads only the environment; backend/.env holds the three ASC_* values."""
    spec = importlib.util.spec_from_file_location("ptf", _HERE / "pull_testflight_feedback.py")
    ptf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ptf)
    for name, value in ptf._credentials().items():
        # Not setdefault: an EXPORTED-but-empty variable would survive it, and asc_audit
        # would then report a credential as missing that backend/.env actually holds.
        if not os.environ.get(name, "").strip():
            os.environ[name] = value


def _diff(before: str, after: str) -> None:
    for line in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        print(f"  {line}")


def _upload_attachment(asc, c, token: str, detail_id: str, video: Path) -> List[str]:
    data = video.read_bytes()
    print(f"    reserving {video.name} ({len(data):,} bytes)…")
    created = asc._write(c, token, "POST", "/v1/appStoreReviewAttachments", {
        "data": {"type": "appStoreReviewAttachments",
                 "attributes": {"fileName": video.name, "fileSize": len(data)},
                 "relationships": {"appStoreReviewDetail": {
                     "data": {"type": "appStoreReviewDetails", "id": detail_id}}}}
    })
    asset = created["data"]
    for i, op in enumerate(asset["attributes"]["uploadOperations"], 1):
        off, ln = int(op.get("offset") or 0), int(op.get("length") or 0)
        r = c.request(op.get("method", "PUT"), op["url"],
                      headers={h["name"]: h["value"] for h in op.get("requestHeaders") or []},
                      content=data[off:off + ln])
        if r.status_code >= 400:
            return [f"attachment chunk {i} failed: HTTP {r.status_code}"]
    done = asc._write(c, token, "PATCH", f"/v1/appStoreReviewAttachments/{asset['id']}", {
        "data": {"type": "appStoreReviewAttachments", "id": asset["id"],
                 "attributes": {"uploaded": True,
                                "sourceFileChecksum": hashlib.md5(data).hexdigest()}}})
    state = (done["data"]["attributes"].get("assetDeliveryState") or {})
    if state.get("errors"):
        return [f"attachment rejected by ASC: {state['errors']}"]
    print(f"    \033[32m✓\033[0m attached ({state.get('state')})")
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    ap.add_argument("--video", type=Path, help="the physical-device screen recording to attach")
    ap.add_argument("--version", default=_VERSION,
                    help=f"the App Store version to edit (default {_VERSION}; it must already exist in ASC)")
    ap.add_argument("--allow-video-in-empty-slot", action="store_true",
                    help="let --video fill an EMPTY attachment slot (normally reserved for the Order Form)")
    ap.add_argument("--all-chats-web-search-is-on", action="store_true",
                    help=f"the owner confirms {ALL_CHATS_SWITCH} is ON in production for the whole review, "
                         f"so the notes may give the any-chat web-search recipe (default: report chat only)")
    args = ap.parse_args()
    version_string: str = args.version
    print(f"\n\033[33m▸ {'APPLY' if args.apply else 'DRY RUN — nothing will be written'}\033[0m")

    # Before any credential or network work: an any-chat recipe the backend cannot serve is a
    # false note, so it is refused outright rather than reported after the fact.
    notes_replacements, refusal = choose_replacements(args.all_chats_web_search_is_on)
    if refusal:
        print(f"\033[31m✗\033[0m {refusal}")
        return 1
    print("  web-search sentence: "
          + ("ANY chat (owner confirmed the switch is on)" if args.all_chats_web_search_is_on
             else "report chat only (pass --all-chats-web-search-is-on once that switch is on)"))

    import httpx  # noqa: PLC0415 — keep the pure helpers importable without it

    problems: List[str] = []
    video = args.video.expanduser() if args.video else None
    if video is not None:
        why = video_problem(video)
        if why:
            print(f"\033[31m✗\033[0m {why}")
            return 1

    _fill_credentials_from_dotenv()
    asc = _load_asc()
    token = asc._token()
    _BACKUP.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=120.0) as c:
        app = asc._all(c, token, "/v1/apps", **{"filter[bundleId]": _BUNDLE})[0]
        versions = asc._all(c, token, f"/v1/apps/{app['id']}/appStoreVersions",
                            **{"filter[platform]": "IOS", "filter[versionString]": version_string})
        if len(versions) != 1:
            print(f"\033[31m✗\033[0m expected one iOS {version_string} version, found {len(versions)}")
            return 1
        version = versions[0]
        print(f"  {app['attributes']['name']} {version_string} · state {version['attributes'].get('appStoreState')}")

        detail = asc._get(c, token, f"/v1/appStoreVersions/{version['id']}/appStoreReviewDetail")["data"]
        notes: str = detail["attributes"].get("notes") or ""
        (_BACKUP / "review_notes.before.txt").write_text(notes, encoding="utf-8")

        # ── 1. notes ────────────────────────────────────────────────────────
        print(f"\n[1] review notes ({len(notes)} chars)")
        try:
            new_notes = apply_replacements(notes, notes_replacements)
        except NotesError as e:
            problems.append(f"notes: {e}")
            new_notes = notes
        if new_notes == notes:
            print("    unchanged")
        else:
            print(f"    {len(notes)} -> {len(new_notes)} chars (limit {NOTES_LIMIT})")
            _diff(notes, new_notes)
            (_BACKUP / "review_notes.after.txt").write_text(new_notes, encoding="utf-8")
            if args.apply:
                asc._write(c, token, "PATCH", f"/v1/appStoreReviewDetails/{detail['id']}",
                           {"data": {"type": "appStoreReviewDetails", "id": detail["id"],
                                     "attributes": {"notes": new_notes}}})
                print("    \033[32m✓\033[0m notes updated")

        # ── 2. the recording ────────────────────────────────────────────────
        existing: List[Dict] = asc._all(
            c, token, f"/v1/appStoreReviewDetails/{detail['id']}/appStoreReviewAttachments")
        names = [e["attributes"].get("fileName") for e in existing]
        print(f"\n[2] review attachments: {names or 'none'}")
        action, step_problems = attachment_plan(
            names, video.name if video is not None else None,
            allow_video_in_empty_slot=args.allow_video_in_empty_slot,
        )
        problems += step_problems
        if action == "skip_no_video":
            print("    no --video given — skipping")
        elif action == "skip_already_attached":
            print(f"    {video.name} already attached — skipping")
        elif action == "upload" and args.apply:
            problems += _upload_attachment(asc, c, token, detail["id"], video)
        elif action == "upload":
            print(f"    would upload {video.name} ({video.stat().st_size:,} bytes)")

        # ── 3. description / promotional text / copyright ─────────────────
        loc = next(l for l in asc._all(c, token, f"/v1/appStoreVersions/{version['id']}/appStoreVersionLocalizations")
                   if l["attributes"].get("locale") == "en-US")
        la = loc["attributes"]
        loc_changes = {}
        print("\n[3] version metadata (en-US)")
        try:
            desc = apply_swaps(la.get("description") or "", DESCRIPTION_SWAPS, DESCRIPTION_LIMIT)
            if desc != (la.get("description") or ""):
                loc_changes["description"] = desc
                (_BACKUP / "description.before.txt").write_text(la.get("description") or "", encoding="utf-8")
                print(f"    description: {len(la.get('description') or '')} -> {len(desc)} chars")
                _diff(la.get("description") or "", desc)
            else:
                print("    description: unchanged")
        except NotesError as e:
            problems.append(f"description: {e}")
        if len(PROMOTIONAL_TEXT) > PROMOTIONAL_LIMIT:
            problems.append(f"promotional text is {len(PROMOTIONAL_TEXT)} chars, over {PROMOTIONAL_LIMIT}")
        elif la.get("promotionalText") != PROMOTIONAL_TEXT:
            loc_changes["promotionalText"] = PROMOTIONAL_TEXT
            print(f"    promo: {la.get('promotionalText')!r}\n       -> {PROMOTIONAL_TEXT!r}")
        if loc_changes and args.apply:
            asc._write(c, token, "PATCH", f"/v1/appStoreVersionLocalizations/{loc['id']}",
                       {"data": {"type": "appStoreVersionLocalizations", "id": loc["id"],
                                 "attributes": loc_changes}})
            print("    \033[32m✓\033[0m localization updated")
        current_copyright = version["attributes"].get("copyright")
        if current_copyright != COPYRIGHT:
            print(f"    copyright: {current_copyright!r} -> {COPYRIGHT!r}")
            if args.apply:
                asc._write(c, token, "PATCH", f"/v1/appStoreVersions/{version['id']}",
                           {"data": {"type": "appStoreVersions", "id": version["id"],
                                     "attributes": {"copyright": COPYRIGHT}}})
                print("    \033[32m✓\033[0m copyright updated")

        # ── 4. in-app purchase review notes ─────────────────────────────────
        print("\n[4] in-app purchase review notes")
        for sid in SUBSCRIPTION_IDS:
            sub = asc._get(c, token, f"/v1/subscriptions/{sid}")["data"]["attributes"]
            try:
                note = swap_text(sub.get("reviewNote") or "", *SUBSCRIPTION_NOTE_SWAP)
            except NotesError as e:
                problems.append(f"subscription {sid}: {e}")
                continue
            if note == (sub.get("reviewNote") or ""):
                print(f"    {sub.get('productId')}: unchanged")
                continue
            print(f"    {sub.get('productId')}: path sentence rewritten")
            if args.apply:
                asc._write(c, token, "PATCH", f"/v1/subscriptions/{sid}",
                           {"data": {"type": "subscriptions", "id": sid, "attributes": {"reviewNote": note}}})
                print("      \033[32m✓\033[0m updated")
        for iid in CREDIT_PACK_IDS:
            iap = asc._get(c, token, f"/v2/inAppPurchases/{iid}")["data"]["attributes"]
            existing_note = iap.get("reviewNote") or ""
            if existing_note == CREDIT_PACK_NOTE:
                print(f"    {iap.get('productId')}: unchanged")
                continue
            if existing_note:
                problems.append(f"{iap.get('productId')} already has a different reviewNote — not overwriting")
                continue
            print(f"    {iap.get('productId')}: add review note")
            if args.apply:
                asc._write(c, token, "PATCH", f"/v2/inAppPurchases/{iid}",
                           {"data": {"type": "inAppPurchases", "id": iid, "attributes": {"reviewNote": CREDIT_PACK_NOTE}}})
                print("      \033[32m✓\033[0m updated")

    print(f"\nbackup: {_BACKUP}")
    if problems:
        print("\n\033[31m✗\033[0m issues:\n   • " + "\n   • ".join(problems))
        return 1
    if not args.apply:
        print("\nDry run only. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
