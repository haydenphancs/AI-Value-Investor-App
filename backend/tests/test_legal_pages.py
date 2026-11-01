"""`/privacy`, `/terms` and `/support` must actually serve, in production.

App Store Connect requires a **Privacy Policy URL** and a **Support URL**, and a reviewer
opens both. A 404 there is a rejection and a lost review cycle.

The specific trap these tests exist for: the authored HTML lives at the repo root in
`documents/legal/`, but `backend/Dockerfile` builds with `backend/` as its context
(`COPY . .`). Anything outside `backend/` is therefore absent from the deployed container —
a route reading the repo-root path works perfectly on a laptop and 404s on Railway, which is
the worst possible place to discover it. So the served copies live at
`backend/app/templates/legal/`, and these tests pin both halves: that the files are inside the
deploy context, and that they still match the authored originals.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


_BACKEND = Path(__file__).resolve().parents[1]
_SERVED = _BACKEND / "app" / "templates" / "legal"
_AUTHORED = _BACKEND.parent / "documents" / "legal"

_PAGES = ["privacy", "terms", "support"]


@pytest.fixture(scope="module")
def client():
    # NOT `with TestClient(app)`. The context manager runs the app LIFESPAN, whose
    # startup jobs reach Supabase — one live call to production per run, just to serve
    # a static HTML page. These routes need no startup state, so skip it.
    yield TestClient(app)


# ── The pages serve ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("page", _PAGES)
def test_page_serves_html(client, page):
    r = client.get(f"/{page}")
    assert r.status_code == 200, f"/{page} returned {r.status_code} — ASC reviewers open these"
    assert r.headers["content-type"].startswith("text/html"), (
        f"/{page} must be HTML, not JSON — a reviewer sees raw text otherwise"
    )
    assert "<html" in r.text.lower()
    assert len(r.text) > 500, f"/{page} is suspiciously short — is it a stub?"


@pytest.mark.parametrize("page", _PAGES)
def test_page_file_is_inside_the_deploy_context(page):
    """The whole point. A file outside `backend/` does not exist on Railway."""
    path = _SERVED / f"{page}.html"
    assert path.is_file(), f"{path} missing — /{page} will 404 in production"
    assert _BACKEND in path.parents, "served page escaped the Docker build context"


# ── Content the pages must carry ──────────────────────────────────────────────

def test_support_page_has_a_contact_route(client):
    """ASC rejects a Support URL with no way to reach a human."""
    body = client.get("/support").text
    assert "support@caydexinvest.com" in body


def test_support_page_documents_account_deletion(client):
    """Apple requires account deletion to be discoverable. The privacy policy also promises
    it, so this page is where a reviewer confirms the promise is real."""
    body = client.get("/support").text.lower()
    assert "delete account" in body or "delete your account" in body


def test_support_page_carries_the_not_advice_disclaimer(client):
    """The app emits its own Buy/Sell technical rating on named securities, which is the
    likeliest hook for a 5.1.1(ix)/3.1.5-adjacent question. The disclaimer must be on the
    page a reviewer actually opens, not only inside the app."""
    body = client.get("/support").text.lower()
    assert "not investment advice" in body
    assert "broker-dealer" in body


def test_legal_pages_cross_link(client):
    """A reviewer landing on /support should be one click from the other two."""
    body = client.get("/support").text
    assert 'href="/privacy"' in body
    assert 'href="/terms"' in body


# ── The publisher's-exclusion prongs ──────────────────────────────────────────
#
# Advisers Act §202(a)(11)(D) as read in Lowe v. SEC and Lingley v. Seeking Alpha
# (S.D.N.Y. 2024) is the ONLY thing keeping a paid product that publishes Buy/Sell ratings
# on named securities out of investment-adviser status. The test has three prongs; the app
# used to document one. These pin the other two so nobody quietly deletes them as boilerplate.

def test_terms_state_the_output_is_impersonal(client):
    """The statutory line is whether the publication is adapted to a specific portfolio."""
    body = client.get("/terms").text.lower()
    assert "general and impersonal" in body
    assert "not adapted to your portfolio" in body


def test_terms_state_disinterestedness(client):
    """Lowe requires bona fide publication 'not designed to tout any security in which the
    publisher had an interest'. Before this, a repo-wide grep for no-position language
    returned zero hits."""
    body = client.get("/terms").text.lower()
    assert "hold no positions" in body
    assert "no compensation from any issuer" in body


def test_terms_disclaim_a_fiduciary_relationship(client):
    body = client.get("/terms").text.lower()
    assert "no fiduciary or advisory relationship" in body


@pytest.mark.parametrize("page", _PAGES)
def test_no_real_investor_name_is_published(page, client):
    """Migration 103 moved the personas to STYLE names, but `DisclaimersView.swift` kept
    shipping "(Buffett, Lynch, etc.)" for weeks afterwards — a 5.2.1 / right-of-publicity
    exposure that survived precisely because nothing checked. Guard the served pages too."""
    body = client.get(f"/{page}").text.lower()
    for name in ("buffett", "lynch", "munger", "dalio", "ackman", "cathie wood", "burry"):
        assert name not in body, f"/{page} names a real investor ({name!r})"


# ── Support page content the peer set all carries ─────────────────────────────

def test_support_page_names_every_data_source(client):
    """Naming only FMP and CoinGecko understated it — FRED, FINRA, ApeWisdom and EDGAR all
    feed user-visible numbers."""
    body = client.get("/support").text
    for source in ("Financial Modeling Prep", "CoinGecko", "FRED", "FINRA", "ApeWisdom", "EDGAR"):
        assert source in body, f"data source {source!r} not disclosed"


def test_support_page_discloses_the_13f_filing_lag(client):
    """`latest_filed_13f_quarter()` exists because 13F data is 45 days stale by law. Showing
    six-week-old institutional positions without saying so reads as live data."""
    body = client.get("/support").text
    assert "45 days" in body


def test_support_page_explains_the_rating_is_not_ai(client):
    """The technical meter is deterministic. The old in-app caption called it
    'AI-generated content', which implies a black box where there is a stated formula."""
    body = client.get("/support").text.lower()
    assert "rules-based" in body
    assert "not a forecast" in body


#: The general Learn AI line each surface must carry, exactly (owner decision 2026-11-01: weekly
#: pieces carry "no "AI-assisted" label", so these lines are the disclosure — web AND in-app).
_LEARN_AI_LINES = {
    "terms": "uses artificial intelligence to generate analysis, reports, summaries, and chat responses, to "
             "help write some Learn articles (each reviewed by Caydex before publication), and to narrate "
             "Learn content in a synthetic voice.",
    "support": "Some Learn articles are also written with AI assistance from public filings and reviewed by "
               "Caydex before they are published, and Learn narration uses a synthetic voice.",
}
_LEARN_AI_SCREENS = {"terms": "TermsOfUseView.swift", "support": "SupportView.swift"}


def _flat(text: str) -> str:
    return " ".join(text.replace("&ldquo;", "\u201c").replace("&rdquo;", "\u201d").split())


@pytest.mark.parametrize("page", ["terms", "support"])
def test_learn_ai_line_is_general_and_promises_no_label(client, page):
    """The web page AND its in-app mirror carry the general Learn AI line, word for word, and neither
    promises an "AI-assisted" label the pieces no longer wear."""
    served = _flat(client.get(f"/{page}").text)
    native = _flat(_swift_user_facing_strings(_IOS_SCREENS_DIR / _LEARN_AI_SCREENS[page]))
    for where, body in (("served", served), ("in-app", native)):
        assert _LEARN_AI_LINES[page] in body, f"{page} ({where}) lost the Learn AI line"
        assert "AI-assisted" not in body, f"{page} ({where}) promises an AI-assisted label"


def test_store_description_promises_no_learn_label():
    listing = (_BACKEND.parent / "documents" / "legal" / "app-store-listing.md").read_text(encoding="utf-8")
    block = re.search(r"^## Description \(4000 max\)\s*\n+```\n(.*?)\n```", listing, re.S | re.M).group(1)
    flat = " ".join(block.split())
    assert "AI-assisted" not in flat
    assert "AI also helps write and narrate some Learn articles, which Caydex reviews before publishing." in flat


def test_support_page_frames_fair_value_as_a_model_output(client):
    """A number labelled 'fair value' beside a live price is an implicit mispricing claim."""
    body = client.get("/support").text.lower()
    assert "not a price target" in body


def test_support_page_covers_subscriptions_and_refunds(client):
    body = client.get("/support").text
    assert "14.99" in body and "39.99" in body
    assert "reportaproblem.apple.com" in body


def test_support_page_repeats_the_impersonality_and_independence_claims(client):
    """These are the two prongs; they belong on the page a reviewer actually opens."""
    body = client.get("/support").text.lower()
    assert "general and impersonal" in body
    assert "hold no positions" in body


# ── No silent drift between the authored and served copies ────────────────────

@pytest.mark.parametrize("page", ["privacy", "terms"])
def test_served_copy_matches_the_authored_original(page):
    """`documents/legal/` is where these are written and reviewed; `app/templates/legal/` is
    what actually ships. Editing one and not the other publishes a policy that differs from
    the one on file — which for a privacy policy is a real problem, not a tidiness one.

    Skips when the authored copy is absent, because it is not in the deploy context and this
    suite may run there.
    """
    authored = _AUTHORED / f"{page}.html"
    if not authored.is_file():
        pytest.skip(f"{authored} not present (expected outside the repo checkout)")

    served = _SERVED / f"{page}.html"
    assert served.read_text(encoding="utf-8") == authored.read_text(encoding="utf-8"), (
        f"{served.name} has drifted from documents/legal/{page}.html — copy the authored "
        f"file over the served one so the published policy matches the reviewed one"
    )


# ── "We don't sell your data" must stay prominent ─────────────────────────────
#
# This is the single thing users most want to know, and it used to live in §5 — past the
# collection, use, AI-processing and service-provider sections — where almost nobody read it.
# It is now a summary box at the top. These tests stop it drifting back down or being
# softened, and stop it being claimed if the app ever starts doing the opposite.

def test_privacy_policy_says_it_does_not_sell_data_ABOVE_the_first_section():
    """Position matters, not just presence. Assert the claim appears BEFORE section 1."""
    body = (_SERVED / "privacy.html").read_text(encoding="utf-8")
    claim = body.lower().find("we do not sell your personal information")
    first_section = body.lower().find("1. information we collect")
    assert claim != -1, "the no-sale claim is missing entirely"
    assert first_section != -1, "could not locate section 1 — did the headings change?"
    assert claim < first_section, (
        "the no-sale claim has drifted below the first section again; it belongs in the "
        "summary at the top, which is the only place users reliably read"
    )


def test_privacy_summary_covers_the_five_reassurances(client):
    body = client.get("/privacy").text.lower()
    # No sale, and no CCPA-sense "sharing" either — the two are legally distinct.
    assert "we do not sell your personal information" in body
    assert "cross-context behavioural advertising" in body
    # No ad/tracking stack. Verified true: the project pins sentry-cocoa and GoogleSignIn,
    # neither of which is an ad SDK, and NSPrivacyTracking is false.
    assert "no advertisers, ad networks, or third-party trackers" in body
    assert "idfa" in body
    # The three app-specific ones.
    assert "never connect to your brokerage" in body
    assert "not sent to the ai" in body or "not transmitted with your message" in body
    assert "delete account" in body


def test_privacy_summary_does_not_override_the_full_policy(client):
    """A plain-language summary that reads as the operative text is its own risk. Keep the
    line that says the detail governs."""
    body = client.get("/privacy").text.lower()
    assert "the full detail below governs" in body


def test_in_app_privacy_policy_carries_the_same_summary():
    """`privacy.html` and `PrivacyPolicyView.swift` are hand-maintained mirrors with no
    automated sync, so the summary has to be asserted on both sides or it will drift."""
    path = _IOS_SCREENS_DIR / "PrivacyPolicyView.swift"
    if not path.is_file():
        pytest.skip(f"{path} not present")
    src = path.read_text(encoding="utf-8").lower()
    assert "we do not sell your personal information" in src
    assert "the short version" in src


# ── The in-app legal screens must agree with the served pages ─────────────────

_IOS_LEGAL_SCREENS = [
    "DisclaimersView.swift",
    "DisclaimerAcknowledgementView.swift",
    "TermsOfUseView.swift",
    "PrivacyPolicyView.swift",
    # Added with the screen itself: it describes the personas, so it is exactly the kind of
    # copy that reintroduces a real investor's name.
    "SupportView.swift",
]

_IOS_SCREENS_DIR = _BACKEND.parent / "frontend" / "ios" / "ios" / "Views" / "Screens"


def _swift_user_facing_strings(path: Path) -> str:
    r"""Concatenate the double-quoted string literals in a Swift file.

    Crude on purpose. Identifiers like `.warrenBuffett` and asset names like
    `"icon_persona_buffett"` are NOT user-facing, and the persona enum legitimately keys on
    `"warren_buffett"` to match `agent_personas.persona_key` — so this looks at displayed
    prose only, and the callers below scope it to the four legal screens. A blanket scan of
    `Views/` would fire on the whale trackers, which name real 13F filers as a matter of
    public record and are correct to do so.

    Two escaping traps, both of which silently made this return the wrong thing:

    1. A raw `\n` must be excluded from the character class. Without it the match runs from one
       literal's CLOSING quote to the next literal's OPENING quote — i.e. it returns the Swift
       code between the strings instead of the strings, which makes every assertion here pass
       on nothing.
    2. **A literal containing ANY backslash escape was skipped entirely.** The old pattern was
       `"([^"\\\n]{12,})"`, whose class excludes `\`, so the match simply failed on those
       literals rather than truncating — they became invisible. Measured when this was found:
       **11 skipped literals in `TermsOfUseView.swift` and 11 in `PrivacyPolicyView.swift`**,
       because those files write smart quotes as `\u{201C}` — the Terms intro paragraph and the
       "personas are simulations" paragraph were both unscanned. `DisclaimersView.swift` hid
       its warranty disclaimer the same way, via `\"as is\"`.

       That is a real hole, not a tidiness one: `test_in_app_legal_screen_names_no_real_investor`
       exists because a real investor's name shipped in this exact prose, and it was reading
       text with those paragraphs removed. A name inside any smart-quoted paragraph would not
       have been seen.

    So escapes are now matched AND decoded — `\u{201C}` becomes a real quote character, so an
    assertion can match prose as written rather than as escaped.
    """
    return _swift_literals_in(path.read_text(encoding="utf-8"))


def _swift_literals_in(text: str) -> str:
    """`_swift_user_facing_strings` over a piece of Swift source (e.g. one brace-bound section)."""
    # `(?:[^"\\\n]|\\.)` — an ordinary char, OR a backslash and whatever it escapes. That keeps
    # the literal's own closing quote unambiguous (`\"` is consumed by the second branch) while
    # no longer discarding the literal wholesale.
    raw = re.findall(r'"((?:[^"\\\n]|\\.){12,})"', text)

    def _unescape(s: str) -> str:
        s = re.sub(r"\\u\{([0-9A-Fa-f]{1,8})\}", lambda m: chr(int(m.group(1), 16)), s)
        return s.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")

    return "\n".join(_unescape(s) for s in raw)


def test_the_swift_scanner_sees_escaped_literals():
    r"""Anti-vacuity for the scanner itself — every cross-surface assertion below is only as
    good as what this returns.

    The original pattern `"([^"\\\n]{12,})"` skipped any literal containing a backslash, so a
    paragraph written with `\u{201C}` smart quotes was invisible **in full**. That silently
    hid 11 literals in `TermsOfUseView.swift` and 11 in `PrivacyPolicyView.swift`, including
    the Terms intro and the "personas are educational simulations" paragraph — the very prose
    `test_in_app_legal_screen_names_no_real_investor` exists to police.

    Proven by mutation when this was fixed: planting "Warren Buffett" inside that escaped
    paragraph left the old scanner reporting no match, while the current one fails the build.
    """
    src = (
        'let a = "plain paragraph, long enough to match"\n'
        'let b = "smart \\u{201C}quoted\\u{201D} paragraph naming Warren Buffett"\n'
        'let c = "escaped \\"as is\\" warranty disclaimer text"\n'
    )
    tmp = _BACKEND / "tests" / "_scanner_probe.swift"
    tmp.write_text(src, encoding="utf-8")
    try:
        out = _swift_user_facing_strings(tmp)
    finally:
        tmp.unlink()

    assert "plain paragraph" in out, "the ordinary case regressed"
    assert "Warren Buffett" in out, (
        "a literal carrying a \\u{...} escape is invisible again — the smart-quoted paragraphs "
        "in Terms and Privacy would go unscanned, which is how a real investor's name could sit "
        "in shipped prose with every guard green"
    )
    assert "“" in out and "”" in out, (
        "escapes must be DECODED, not just matched, so assertions can compare prose as written"
    )
    assert '"as is"' in out, 'an escaped \\" literal is still being dropped'


@pytest.mark.parametrize("screen", _IOS_LEGAL_SCREENS)
def test_in_app_legal_screen_names_no_real_investor(screen):
    """THE regression guard for `DisclaimersView.swift:27`.

    Migration 103 renamed the five personas to investing STYLE names across the backend, and
    Terms §3 was updated to match — but this screen kept telling users "The AI personas
    (Buffett, Lynch, etc.) … do not represent the actual views of these investors" long
    afterwards. It was the ONLY user-visible real investor name left in the binary, and it
    contradicted the very design decision it was describing. Nothing caught it because
    nothing was looking.
    """
    path = _IOS_SCREENS_DIR / screen
    if not path.is_file():
        pytest.skip(f"{path} not present")

    prose = _swift_user_facing_strings(path).lower()
    for name in ("buffett", "lynch", "munger", "dalio", "ackman", "cathie wood", "burry"):
        assert name not in prose, (
            f"{screen} shows a real investor's name to users ({name!r}). The personas are "
            f"style names (migration 103); naming a real person in a paid product is an "
            f"App Store 5.2.1 and right-of-publicity exposure."
        )


def test_in_app_terms_carry_the_same_two_prongs_as_the_web_terms():
    """The native screen and the hosted page are hand-maintained mirrors, so they drift.
    Both must carry the impersonality and disinterestedness language."""
    path = _IOS_SCREENS_DIR / "TermsOfUseView.swift"
    if not path.is_file():
        pytest.skip(f"{path} not present")

    prose = _swift_user_facing_strings(path).lower()
    assert "general and impersonal" in prose
    assert "hold no positions" in prose
    assert "no fiduciary or advisory relationship" in prose


def test_in_app_support_screen_mirrors_the_served_page(client):
    """`SupportView.swift` is the THIRD hand-maintained mirror, and the least protected one.

    `test_served_copy_matches_the_authored_original` is parametrized over privacy and terms
    only, because `documents/legal/support.html` does not exist — `support.html` lives solely
    under `backend/app/templates/legal/`. So there is no byte-level sync between the native
    screen and the served page at all, and the load-bearing claims have to be asserted on both
    sides or they drift apart silently.

    Asserting on BOTH sides is the point. Checking only the Swift file would let the web page
    rot; checking only the page would let the screen rot. These are the claims where a
    divergence is a legal or factual problem rather than a wording difference.
    """
    path = _IOS_SCREENS_DIR / "SupportView.swift"
    if not path.is_file():
        pytest.skip(f"{path} not present")

    native = _swift_user_facing_strings(path).lower()
    served = client.get("/support").text.lower()

    claims = [
        # The disclaimer that makes the whole product lawful to ship.
        "not investment advice",
        "broker-dealer",
        "general and impersonal",
        "hold no positions",
        # Factual claims a user could act on.
        "not a price target",
        "45 days",                  # the 13F lag
        "reportaproblem.apple.com",  # refunds are Apple's, not ours
        "14.99",
        "39.99",
        # Apple requires account deletion to be discoverable (5.1.1(v)).
        "delete account",
    ]
    for claim in claims:
        assert claim in native, f"SupportView.swift lost the claim {claim!r}"
        assert claim in served, f"support.html lost the claim {claim!r}"


def test_the_support_screen_does_not_depend_on_a_mail_app(client):
    """The bug this screen exists to fix.

    `Profile → Help & Support` used to be a bare `mailto:` opened with no completion handler.
    The Simulator ships no Mail app, so the row did nothing at all — silently, in every build.
    The screen must therefore surface the address as TEXT, not only behind a mail hand-off.

    The behaviour now lives in a shared molecule (`MailUnavailableCard.swift`) because a third
    screen — FeedbackView — needed the identical degradation and hand-rolling it again was how
    the first two variants came to differ. So this asserts the behaviour AT the component, and
    that each mail-CTA screen actually uses it. Pinning the literals inside `SupportView.swift`
    (as an earlier revision did) would fail the moment the logic is correctly shared, which is
    the wrong incentive — a guard should survive the refactor it is meant to protect.
    """
    shared = _IOS_SCREENS_DIR.parent / "Molecules" / "MailUnavailableCard.swift"
    if not shared.is_file():
        pytest.skip(f"{shared} not present")
    molecule = shared.read_text(encoding="utf-8")

    assert ".textSelection(.enabled)" in molecule, (
        "the address must be selectable, so a screen is useful on a device that cannot send "
        "mail at all — which is the entire failure being fixed"
    )
    assert "UIPasteboard.general.string" in molecule, "no copy affordance for the address"

    # One address, defined once. Cloudflare Email Routing carries support@ and copyright@ only —
    # feedback@ has NO route and silently bounced every message sent to it.
    app_info = _IOS_SCREENS_DIR.parent.parent / "Core" / "Utilities" / "AppInfo.swift"
    assert app_info.is_file(), "AppInfo.swift is the home for the support address"
    assert 'supportEmail = "support@caydexinvest.com"' in app_info.read_text(encoding="utf-8"), (
        "the support address must be defined in AppInfo, and must be support@"
    )

    # Every screen with a mail CTA must actually USE the shared degradation, not re-roll it.
    for screen in ("SupportView.swift", "FeedbackView.swift"):
        path = _IOS_SCREENS_DIR / screen
        if not path.is_file():
            continue
        src = path.read_text(encoding="utf-8")
        assert "MailUnavailableCard(" in src, (
            f"{screen} has a mail CTA but does not use MailUnavailableCard — a hand-rolled "
            f"copy is how the two existing variants drifted apart"
        )
        assert "UIPasteboard.general.string" not in src, (
            f"{screen} re-implements the copy affordance instead of using the shared molecule"
        )


def test_a_missing_page_404s_rather_than_500s(client, monkeypatch, tmp_path):
    """If a deploy omits the files, the route must fail as a clean 404 (and log), not blow up
    as an unhandled 500 — the latter would also fire a Sentry alert per request."""
    from app import main as main_mod

    monkeypatch.setattr(main_mod, "_LEGAL_DIR", tmp_path)
    r = client.get("/privacy")
    assert r.status_code == 404


# ── Credit-pack non-expiry (App Store Guideline 3.1.1) ───────────────────────
#
# Purchased credits are a CONSUMABLE. 3.1.1 forbids expiring them, `user_credits` keeps them in
# a second pool that `ensure_credit_period` deliberately never touches (migration 117), and
# `BuyCreditsView` tells the buyer they "never expire".
#
# Three surfaces said the opposite, flatly and without qualification: the Terms of Use (which
# is the App Store EULA, linked from the purchase sheet), the Help & Support page, and the
# paywall. "Unused credits do not roll over" / "Unused credits reset monthly" describes the
# MONTHLY pool and is true of it — but stated over the whole credit system it asserts that a
# balance the user paid for expires, which is both a compliance problem and a false statement
# about what the code does.
#
# Both directions are pinned: the unqualified claim must not come back, AND the carve-out must
# be present. A one-way ban would be satisfied by deleting the sentence entirely.

_CREDIT_COPY_SURFACES = [
    _BACKEND / "app" / "templates" / "legal" / "terms.html",
    _BACKEND / "app" / "templates" / "legal" / "support.html",
    _IOS_SCREENS_DIR / "TermsOfUseView.swift",
    _IOS_SCREENS_DIR / "SupportView.swift",
    _IOS_SCREENS_DIR / "PaywallView.swift",
]

# Phrases that assert expiry over the WHOLE credit system rather than the monthly pool.
_UNQUALIFIED_EXPIRY_CLAIMS = (
    "unused credits do not roll over",
    "unused credits reset monthly",
    "unused credits expire",
    "credits reset each month",
)


def _prose_only(path: Path) -> str:
    """The file's user-facing text, with COMMENTS REMOVED.

    ⚠️ Load-bearing, and it caught itself: the first version of this guard scanned the raw
    source, and the comment added ABOVE the corrected paywall string — which quotes the old
    wording to explain why it was wrong — tripped the assertion. A guard that fires on its own
    rationale is unusable, and the usual "fix" (softening the pattern) is how these go vacuous.

    Whole-line comments only, deliberately. Stripping a TRAILING `//` would mangle every URL
    literal (`"https://…"`), and a mangled literal is the invisible-input failure mode this
    suite has already been bitten by once.
    """
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip()
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            out.append("")
        else:
            out.append(line)
    text = "\n".join(out)
    # HTML comments too, for the served pages.
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    return text.lower()


@pytest.mark.parametrize(
    "path", _CREDIT_COPY_SURFACES, ids=lambda p: p.name
)
def test_no_surface_claims_purchased_credits_expire(path):
    if not path.is_file():
        pytest.skip(f"{path.name} is absent")
    # Collapse the wrapping so a claim split across source lines is still caught — the HTML
    # copies wrap mid-sentence, which is exactly how a phrase-level grep goes vacuous.
    flat = " ".join(_prose_only(path).split())
    for claim in _UNQUALIFIED_EXPIRY_CLAIMS:
        assert claim not in flat, (
            f"{path.name} states \"{claim}\" without qualifying it to the MONTHLY pool. "
            f"Purchased credits are a consumable and App Store Guideline 3.1.1 forbids "
            f"expiring them; `ensure_credit_period` never touches that pool."
        )


@pytest.mark.parametrize(
    "path",
    [
        _BACKEND / "app" / "templates" / "legal" / "terms.html",
        _BACKEND / "app" / "templates" / "legal" / "support.html",
        _IOS_SCREENS_DIR / "TermsOfUseView.swift",
        _IOS_SCREENS_DIR / "SupportView.swift",
    ],
    ids=lambda p: p.name,
)
def test_the_binding_copy_discloses_that_credit_packs_do_not_expire(path):
    """The other direction. Removing the false sentence is not enough — a user buying a pack
    from a sheet that links these documents has to be able to READ that it does not expire."""
    if not path.is_file():
        pytest.skip(f"{path.name} is absent")
    flat = " ".join(path.read_text(encoding="utf-8").lower().split())
    assert "credit pack" in flat, f"{path.name} never mentions credit packs at all"
    assert "do not expire" in flat or "never expire" in flat, (
        f"{path.name} does not state that purchased credits do not expire"
    )


def test_the_published_effective_dates_agree_across_every_mirror():
    """`Last updated` is the date a reader relies on, and it lives in FOUR places per document
    (authored HTML, served HTML, the in-app Swift mirror). It was never bumped for the
    2026-08-07 collected-data change, so all three privacy copies still claimed July 29 while
    describing a category added nine days later.
    """
    import re

    for doc, screen in (("terms", "TermsOfUseView.swift"), ("privacy", "PrivacyPolicyView.swift")):
        served = (_BACKEND / "app" / "templates" / "legal" / f"{doc}.html").read_text(encoding="utf-8")
        html_dates = re.findall(r"Last updated:\s*([A-Z][a-z]+ \d{1,2}, \d{4})", served)
        assert html_dates, f"{doc}.html has no 'Last updated' line"

        swift = (_IOS_SCREENS_DIR / screen).read_text(encoding="utf-8")
        swift_dates = re.findall(r'lastUpdated:\s*"([^"]+)"', swift)
        assert swift_dates, f"{screen} has no lastUpdated"

        assert html_dates[0] == swift_dates[0], (
            f"{doc}: served page says {html_dates[0]!r} but {screen} says {swift_dates[0]!r} — "
            f"the in-app mirror and the published page must state the same effective date"
        )


# ── Substantive parity: the in-app mirror must carry what the published page says ──
#
# WHY THIS EXISTS, and why the date test above was not enough. The 2026-08-13
# personalization change amended BOTH HTML copies with a "Learning preferences" carve-out
# and did not touch either Swift mirror. `test_the_published_effective_dates_agree_across_
# every_mirror` stayed green throughout, because it asserts the two dates AGREE — and they
# did, at the stale value. Its own docstring describes exactly this failure from
# 2026-08-07; agreement-of-dates cannot detect a mirror that was simply forgotten.
#
# This asserts agreement of CONTENT for the clauses that carry legal weight. It is
# deliberately scoped to specific sentences rather than diffing whole documents: the Swift
# mirror is prose-only by construction (no headings markup, no <em>), so a structural diff
# would be permanently red and would get deleted.
#
# The in-app Terms is the document a reader sees before tapping the personalization consent
# toggle in Settings, so a carve-out that exists only on the website is not one they agreed to.

_CARVE_OUT_CLAUSES = {
    "terms": (
        "TermsOfUseView.swift",
        [
            "learning preferences",
            "we use those preferences only to decide which information to show you first",
            "never change our analysis, ratings, scores, or estimates",
            "never make any output a recommendation that a security is suitable for you",
            "your finances, risk tolerance, time horizon, tax situation, or investment objectives",
            "turn personalized explanations off at any time",
        ],
    ),
    "privacy": (
        "PrivacyPolicyView.swift",
        [
            "learning preferences",
            "never to change our analysis, ratings, or estimates",
            "we do not ask for your finances, risk tolerance, time horizon, tax situation, or investment objectives",
            "personalized explanations are off until you turn them on",
        ],
    ),
}


def _normalized_prose(raw: str, *, strip_tags: bool) -> str:
    """Lowercased, tag-free, whitespace-collapsed text, so a phrase can span a line break.

    Both sides need this for different reasons: the HTML hard-wraps mid-sentence and puts
    `<em>`/`<strong>` inside clauses, and the Swift literals are one long line. Comparing
    either verbatim would fail on formatting rather than on meaning.
    """
    import html as _html

    text = raw
    if strip_tags:
        text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip().lower()


@pytest.mark.parametrize("doc", sorted(_CARVE_OUT_CLAUSES))
def test_the_published_page_states_the_personalization_carve_out(doc, client):
    """The website half. Without this, the clauses below could pass by both being absent."""
    body = _normalized_prose(client.get(f"/{doc}").text, strip_tags=True)
    for clause in _CARVE_OUT_CLAUSES[doc][1]:
        assert clause in body, f"/{doc} no longer states {clause!r}"


@pytest.mark.parametrize("doc", sorted(_CARVE_OUT_CLAUSES))
def test_the_in_app_mirror_states_the_personalization_carve_out(doc, client):
    """THE regression: shipped for the website, forgotten for the app."""
    screen, clauses = _CARVE_OUT_CLAUSES[doc]
    prose = _normalized_prose(
        _swift_user_facing_strings(_IOS_SCREENS_DIR / screen), strip_tags=False
    )
    for clause in clauses:
        assert clause in prose, (
            f"{screen} does not state {clause!r}, but the published /{doc} page does. "
            f"The in-app document is what a reader sees before consenting — amending only "
            f"the website leaves that consent uninformed."
        )


def test_the_carve_out_scan_is_not_vacuous():
    """Guard against the guard.

    Both assertions above are substring checks, and a substring check against an empty or
    mis-parsed haystack passes for `""` and fails for everything else — so a broken scanner
    would look like a broken document. Prove the Swift extractor still returns real prose,
    and that a sentence which is NOT in the carve-out is absent, so the haystack is not
    somehow matching everything.
    """
    for screen in ("TermsOfUseView.swift", "PrivacyPolicyView.swift"):
        prose = _normalized_prose(
            _swift_user_facing_strings(_IOS_SCREENS_DIR / screen), strip_tags=False
        )
        assert len(prose) > 2000, f"{screen} scan returned {len(prose)} chars — extractor drifted"
        assert "caydex" in prose, f"{screen} scan produced text without the product name"
        assert "we guarantee your investment returns" not in prose


# ── When credits refill: paid = per billing period, Free = the calendar month (196) ──
#
# Migration 196 moved a paid plan's allowance off the ET calendar month and onto its Apple
# billing period, with an upgrade carrying the unused paid credits up to the share of the
# period already used. The Terms (the App Store EULA, linked from the purchase sheet) and the
# Support page both used to promise the calendar-month reset and the old "raised mid-month"
# upgrade, so the website and the in-app mirrors are pinned to the new clauses TOGETHER —
# the 2026-08-13 carve-out shipped on the website and was forgotten in the app.
_CREDIT_PERIOD_CLAUSES = {
    "terms": (
        "terms.html",
        "TermsOfUseView.swift",
        [
            "free accounts receive a monthly allowance of credits that resets at the start of each calendar month",
            "paid subscription tiers provide an allowance for each billing period",
            "unused allowance credits do not roll over into the next billing period",
            "up to the share of the current billing period that has already passed",
        ],
    ),
    "support": (
        "support.html",
        "SupportView.swift",
        [
            "the free allowance resets at the start of each calendar month",
            "a pro or max allowance follows your billing period instead",
            "unused allowance credits do not roll over into the next period",
            "up to the share of your pro billing period that had already passed",
        ],
    ),
}
# The promises 196 retired. Either one back on a page states a reset the code no longer does.
_RETIRED_CREDIT_PROMISES = (
    "your monthly allowance resets at the start of each calendar month",
    "if you upgrade mid-month",
)


@pytest.mark.parametrize("doc", sorted(_CREDIT_PERIOD_CLAUSES))
def test_the_website_and_the_app_state_the_same_credit_period_rules(doc):
    html_name, screen, clauses = _CREDIT_PERIOD_CLAUSES[doc]
    html = _normalized_prose(
        (_BACKEND / "app" / "templates" / "legal" / html_name).read_text(encoding="utf-8"),
        strip_tags=True,
    )
    prose = _normalized_prose(
        _swift_user_facing_strings(_IOS_SCREENS_DIR / screen), strip_tags=False
    )
    assert len(prose) > 1000, f"{screen} scan returned {len(prose)} chars — extractor drifted"
    for clause in clauses:
        assert clause in html, f"{html_name} no longer states {clause!r}"
        assert clause in prose, f"{screen} does not state {clause!r}, but {html_name} does"
    for retired in _RETIRED_CREDIT_PROMISES:
        assert retired not in html, f"{html_name} still promises {retired!r}"
        assert retired not in prose, f"{screen} still promises {retired!r}"


def test_the_authored_terms_match_the_served_terms():
    """`documents/legal/terms.html` is the authored copy; the backend serves its own. A clause
    edited in one and not the other publishes two different EULAs."""
    served = (_BACKEND / "app" / "templates" / "legal" / "terms.html").read_text(encoding="utf-8")
    authored = (_BACKEND.parent / "documents" / "legal" / "terms.html").read_text(encoding="utf-8")
    assert served == authored


# ── The third-party-AI consent promise (App Review 5.1.2(i)) ──────────────────
#
# Privacy §3 makes three AFFIRMATIVE promises about Cay AI chat: that we ask explicit
# permission before the first send, that chat does not work until it is given, and that it is
# withdrawable at a NAMED location in Settings. Those are the sentences a reviewer reads
# against 5.1.2(i)/5.1.2(ii), and they are promises about BEHAVIOUR — the code half is pinned
# by `tests/test_ios_ai_consent_gate.py`.
#
# Same shape and same reason as the personalization carve-out above: `PrivacyPolicyView.swift`
# is a hand-maintained mirror with no automated sync, and the 2026-08-13 carve-out landed on
# the website and was forgotten in the app. This is the clause where that failure is worst —
# the in-app policy is the copy a user can reach from the consent flow itself.
#
# NOT duplicated here: `test_served_copy_matches_the_authored_original` already pins
# `documents/legal/privacy.html` byte-for-byte against the served copy, so asserting the
# authored file too would prove nothing extra.

_AI_CONSENT_SCREEN = "PrivacyPolicyView.swift"

_AI_CONSENT_CLAUSES = [
    # The disclosure prong of 5.1.2(i) — "clearly disclose where personal data will be shared
    # with third parties, including with third-party AI".
    "cay ai chat sends what you type to a third-party ai provider for processing",
    # Promise 1 — explicit permission, and it comes FIRST.
    "we ask for your explicit permission before this happens for the first time",
    # Promise 2 — the gate is real, not advisory.
    "chat does not work until you give it",
    # Promise 3 — withdrawable, at a location named EXACTLY as the Settings row is labelled.
    # `test_ios_ai_consent_gate.py::test_the_privacy_policy_names_the_row_that_actually_exists`
    # holds the other end: the row really is titled "AI Chat Data Permission", and it really is
    # inside `generalSection`.
    "you can withdraw that permission at any time in settings → general • ai chat data permission",
    # The honest consequence, and the scope limit — both are why declining is a real option.
    "chat then stops working until you allow it again",
    "the rest of the app is unaffected",
]


def test_the_published_page_states_the_ai_consent_promise(client):
    """The website half. Without this, the mirror test below could pass by both being absent."""
    body = _normalized_prose(client.get("/privacy").text, strip_tags=True)
    for clause in _AI_CONSENT_CLAUSES:
        assert clause in body, f"/privacy no longer states {clause!r}"


def test_the_in_app_mirror_states_the_ai_consent_promise():
    """The half that gets forgotten.

    `PrivacyPolicyView.swift` is reachable from inside the app, including from the consent
    sheet itself — a promise amended only on the website is not one the user reading the
    in-app policy was ever shown.
    """
    prose = _normalized_prose(
        _swift_user_facing_strings(_IOS_SCREENS_DIR / _AI_CONSENT_SCREEN), strip_tags=False
    )
    for clause in _AI_CONSENT_CLAUSES:
        assert clause in prose, (
            f"{_AI_CONSENT_SCREEN} does not state {clause!r}, but the published /privacy page "
            f"does. App Review 5.1.2(i) requires the disclosure AND the permission; the in-app "
            f"policy is the copy a user can reach without leaving the app."
        )


def test_the_ai_consent_scan_is_not_vacuous(client):
    """Guard against the guard.

    Both assertions above are substring checks, and a substring check against an empty or
    mis-parsed haystack passes for `""` and fails for everything else — so a broken scanner
    would look like a broken document. Prove BOTH haystacks are real prose (the carve-out
    version above checks only the Swift side), and that a sentence which is NOT in the policy
    is absent, so neither haystack is somehow matching everything.
    """
    swift = _normalized_prose(
        _swift_user_facing_strings(_IOS_SCREENS_DIR / _AI_CONSENT_SCREEN), strip_tags=False
    )
    assert len(swift) > 4000, (
        f"{_AI_CONSENT_SCREEN} scan returned {len(swift)} chars — extractor drifted"
    )
    assert "caydex" in swift, f"{_AI_CONSENT_SCREEN} scan produced text without the product name"

    web = _normalized_prose(client.get("/privacy").text, strip_tags=True)
    assert len(web) > 5000, f"/privacy scan returned {len(web)} chars — tag-stripping drifted"
    assert "we do not sell your personal information" in web, "/privacy scan is not the policy"

    for haystack, name in ((swift, _AI_CONSENT_SCREEN), (web, "/privacy")):
        assert "we send your chat messages to advertisers" not in haystack, (
            f"{name} matches a sentence it does not contain — the haystack is not real prose"
        )


# ── Web search (2026-10-02; every chat + automatic since 2026-10-09): privacy disclosure + the
#    provider's flow-down ──
#
# A query DERIVED from what the user typed now goes to a second third-party service (Brave
# Search), and Brave's terms (§4(c)) require that end users be bound by restrictions like its
# own: results are third-party, for personal use, and may not be scraped, stored or used to train
# an AI. Both promises live in three copies each (served HTML, documents/legal, the Swift
# mirror) — the authored copy is pinned byte-equal above, so the website and the in-app mirror
# are asserted here, the same shape as the AI-consent clauses.
#
# 2026-10-09 (owner decision 2026-10-08): search runs in ANY chat, when asked AND automatically
# when Caydex's own data cannot answer; signed-in only, limited per day; results are third-party,
# may be wrong, and are not Caydex's view. Every claim is one CODE enforces, or is worded as what
# it is: "instructed not to use web search for prices" (a model instruction — the tool
# descriptions, pinned below — not a code gate, so never "it never searches for prices"); the
# query is described by example ("such as"), because the model writes it; and the removal
# sentence is `sanitize_web_query` (pinned below). The copy keeps a CONSENT
# clause because it also covers the 1.0 and earlier 1.01 builds: the backend opens every-chat and
# automatic search only for an `X-AI-Consent-Version` of 3+, so a reader who never accepted the
# new sheet still gets at most the old report-chat, explicit-ask search.

# What §3 says about the web source list (title, publisher, date, link). True only while
# `CHAT_WEB_SOURCES_PERSIST` ships True: the turn's web pills go into the stored
# `chat_messages.rich_content.sources`, come back with the history (`_row_to_message`), and go
# with `DELETE /chat/sessions/{id}` or account deletion (`chat_sessions` in
# `_UNLINKED_USER_TABLES`, `chat_messages` cascading). The pre-2026-10-09 wording, true only
# while the default was False, is `_SOURCE_LIST_NOT_KEPT`.
_SOURCE_LIST_SAVED = "is saved with the answer, so it appears again when you reopen the conversation"
_SOURCE_LIST_DELETED = "it is deleted when you delete that conversation or your account"
_SOURCE_LIST_NOT_KEPT = "is shown with the answer but is not kept"

_WEB_SEARCH_CLAUSES = {
    "privacy": (
        "PrivacyPolicyView.swift",
        [
            "web search in chat: in any chat, cay ai may run a web search when you ask it to search the web",
            "and automatically when caydex\u2019s own data cannot answer your question",
            "cay ai is instructed not to use web search for prices or other market data",
            "a short search query that cay ai writes from your question",
            "such as the company or its ticker, the topic, and a time period",
            "is sent to our web search provider",
            "amounts, percentages, links, and email addresses are removed from the query before it is sent",
            "are never sent with it",
            "web search is available only while you are signed in",
            "limited to a set number of searches per day",
            # The source-list sentence (`_SOURCE_LIST_SAVED` / `_SOURCE_LIST_DELETED`, or the old
            # `_SOURCE_LIST_NOT_KEPT`) is NOT listed here: which one §3 must carry depends on
            # `CHAT_WEB_SOURCES_PERSIST`'s default, so
            # `test_the_source_list_promise_matches_the_shipped_persistence_switch` pins it in
            # all three copies — and a rollback is then §3 plus the default, nothing else.
            # Scoped to CHAT since 2026-10-28: the Updates Insights card keeps its web pages'
            # titles and links (owner decision; the paragraph below says so).
            "we keep no other copy of a chat\u2019s search results",
            "a temporary in-memory copy for a few minutes",
            "they may be outdated or wrong, they are not caydex\u2019s view",
            # The consent clause: true for every build the website copy covers.
            "searching in any chat, and searching without being asked, happen only after you have "
            "allowed ai chat on a permission screen that describes them",
            "until then, cay ai searches the web only in a chat about a research report, and only "
            "when you ask",
            "brave software (web search in cay ai chat and for updates insights, as described above)",
            # The Updates jump search (2026-10-28): what is sent (a company name and the move's
            # direction — `jump_web_search_service.build_query`), and that the shared card keeps
            # the pages' titles and links (`news_insight_service._web_first_sources`).
            "web search for updates insights: when a stock on the updates tab makes an unusually "
            "large move, caydex may search the web for the reason",
            "only the company\u2019s name and whether the stock is up or down are sent to our web "
            "search provider",
            "never anything about you",
            "is stored with the title and link of each web page it used, and every user who views "
            "that card sees them",
        ],
    ),
    "terms": (
        "TermsOfUseView.swift",
        [
            "web search results.",
            "when cay ai searches the web \u2014 because you asked it to, or automatically because "
            "caydex\u2019s own data could not answer your question",
            "web pages found by our third-party web search provider",
            "each shown with its publisher and, when known, its date",
            "third-party content that we do not control or verify",
            "they do not reflect caydex\u2019s views",
            "they may be outdated, inaccurate, or incomplete",
            "only for your own personal, non-commercial use",
            "you may not scrape, copy, store, cache, resell, or redistribute them",
            "build, train, or improve any artificial intelligence or machine learning model",
            # 2026-10-28: the flow-down covers the web pages an Updates Insights card lists too.
            "an updates insights card that explains an unusually large price move may also draw "
            "on, and list among its sources, web pages found by the same provider",
        ],
    ),
}

# Wording the 2026-10-09 behaviour makes FALSE: search is no longer report-chat-only, nor
# ask-only. Each must be gone from every copy — a one-way "new clause present" check would pass
# with the old restriction still standing beside it, publishing two contradictory promises.
_STALE_WEB_SEARCH_CLAIMS = (
    "web search in report chat",
    "only when you are chatting about a research report",
    "only when you ask cay ai to search the web in a report chat",
    "when you ask cay ai to search the web in a report chat",
    "search the web in a report chat",
)

_WEB_SEARCH_COPIES = [
    _SERVED / "privacy.html",
    _SERVED / "terms.html",
    _IOS_SCREENS_DIR / "PrivacyPolicyView.swift",
    _IOS_SCREENS_DIR / "TermsOfUseView.swift",
    _IOS_SCREENS_DIR / "AIDataConsentView.swift",
]


def _copy_prose(path: Path) -> str:
    """Normalised user-facing prose of one copy: tag-stripped HTML, or the Swift literals."""
    if path.suffix == ".swift":
        return _normalized_prose(_swift_user_facing_strings(path), strip_tags=False)
    return _normalized_prose(_prose_only(path), strip_tags=True)


@pytest.mark.parametrize("doc", sorted(_WEB_SEARCH_CLAUSES))
def test_the_published_page_states_the_web_search_clauses(doc, client):
    body = _normalized_prose(client.get(f"/{doc}").text, strip_tags=True)
    for clause in _WEB_SEARCH_CLAUSES[doc][1]:
        assert clause in body, f"/{doc} no longer states {clause!r}"


@pytest.mark.parametrize("doc", sorted(_WEB_SEARCH_CLAUSES))
def test_the_in_app_mirror_states_the_web_search_clauses(doc):
    screen, clauses = _WEB_SEARCH_CLAUSES[doc]
    prose = _normalized_prose(
        _swift_user_facing_strings(_IOS_SCREENS_DIR / screen), strip_tags=False
    )
    for clause in clauses:
        assert clause in prose, (
            f"{screen} does not state {clause!r}, but the published /{doc} page does — the "
            f"in-app copy is the one a user can reach without leaving the app"
        )


_PRIVACY_COPIES = [
    _SERVED / "privacy.html",
    _AUTHORED / "privacy.html",
    _IOS_SCREENS_DIR / "PrivacyPolicyView.swift",
]


def _privacy_ai_section(path: Path) -> str:
    """§3 "AI Processing" of one privacy copy as normalised prose, COMMENTS REMOVED
    (`_prose_only`) and BOUND to the section — from its heading to the next one — so the same
    words in the summary or §6 cannot satisfy a §3 check, and neither can a code comment."""
    text = _prose_only(path)  # lowercased
    if path.suffix == ".swift":
        start = text.index('heading: "ai processing"')
        end = text.index("legalsection(", start)
        return _normalized_prose(_swift_literals_in(text[start:end]), strip_tags=False)
    start = text.index("<h2>3. ai processing</h2>")
    end = text.index("<h2>", start + len("<h2>"))
    return _normalized_prose(text[start:end], strip_tags=True)


@pytest.mark.parametrize("path", _PRIVACY_COPIES, ids=lambda p: str(p.relative_to(_BACKEND.parent)))
def test_the_source_list_promise_matches_the_shipped_persistence_switch(path):
    """Privacy §3 says the web source list "is saved with the answer, so it appears again when you
    reopen the conversation" and is deleted with the conversation or the account. That is true
    only while `CHAT_WEB_SOURCES_PERSIST` ships True (owner decision 2026-10-09; on in production
    since 2026-10-03). Both directions, in every copy: the "saved" sentence ⇔ default True, and the
    old "not kept" wording ⇔ default False — exactly one of the two, so a copy can neither keep the
    old promise beside the new one nor drop the subject altogether. Flipping the default back
    without rewriting §3 in all three copies publishes a false privacy statement."""
    from app.config import Settings

    if not path.is_file():
        pytest.skip(f"{path} not present (expected outside the repo checkout)")
    default = Settings.model_fields["CHAT_WEB_SOURCES_PERSIST"].default
    assert isinstance(default, bool), f"CHAT_WEB_SOURCES_PERSIST default is {default!r}"
    section = _privacy_ai_section(path)
    saved = _SOURCE_LIST_SAVED in section
    deleted = _SOURCE_LIST_DELETED in section
    not_kept = _SOURCE_LIST_NOT_KEPT in section
    assert saved is default and deleted is default, (
        f"{path.name} §3 {'does not say' if default else 'says'} the source list is saved and "
        f"deleted with the conversation, but CHAT_WEB_SOURCES_PERSIST ships {default} — rewrite §3 "
        f"in the served HTML, documents/legal and PrivacyPolicyView.swift together with the default"
    )
    assert not_kept is (not default), (
        f"{path.name} §3 {'still says' if default else 'no longer says'} the source list "
        f"{_SOURCE_LIST_NOT_KEPT!r}, but CHAT_WEB_SOURCES_PERSIST ships {default}"
    )


def test_the_source_list_scan_is_bound_to_section_3(tmp_path):
    """Guard against the guard: each extracted §3 is real prose about web search, and it stops at
    the next heading (§4's provider list and §6's retention text are outside it), so the check
    above reads §3 and only §3 — and a COMMENT quoting the promise is not the promise."""
    for path in _PRIVACY_COPIES:
        if not path.is_file():
            continue
        section = _privacy_ai_section(path)
        assert len(section) > 1500, f"{path.name} §3 scan returned {len(section)} chars"
        assert "web search in chat: in any chat" in section, f"{path.name} §3 lost the web-search copy"
        assert "supabase" not in section, f"{path.name} §3 scan ran into §4"
        assert "deleting your account removes your account record" not in section, (
            f"{path.name} §3 scan ran into §6"
        )
    # Planted copies: the promise only in a comment is invisible; the same words as copy are seen.
    sentence = "The list of web sources it used " + _SOURCE_LIST_SAVED + "."
    html = ("<h2>3. AI Processing</h2>\n<!-- {s} -->\n<p>Web search in chat: in any chat.</p>{p}\n"
            "<h2>4. Service Providers</h2>\n<p>{s}</p>\n")
    swift = ('LegalSection(\n    heading: "AI Processing",\n    paragraphs: [\n'
             '        // "{s}"\n        "Web search in chat: in any chat."{p}\n    ]\n),\n'
             'LegalSection(\n    heading: "Service Providers",\n    paragraphs: ["{s}"]\n)\n')
    for name, template, copy in (("p.html", html, "\n<p>{s}</p>"),
                                 ("P.swift", swift, ',\n        "{s}"')):
        planted = tmp_path / name
        planted.write_text(template.format(s=sentence, p=""), encoding="utf-8")
        assert _SOURCE_LIST_SAVED not in _privacy_ai_section(planted), (
            f"{name}: a comment, or the next section, satisfied the §3 check"
        )
        planted.write_text(template.format(s=sentence, p=copy.format(s=sentence)), encoding="utf-8")
        assert _SOURCE_LIST_SAVED in _privacy_ai_section(planted), f"{name}: §3 copy went unseen"


def test_the_source_list_claims_match_the_code():
    """What §3 promises about the saved source list, checked against the code that keeps it:
    stored in the assistant row's `rich_content.sources` when persistence is on, read back by the
    history mapper (so a reopened chat shows it), and deleted with the conversation
    (`DELETE /chat/sessions/{id}` deletes the session's messages) and with the account
    (`chat_sessions` is purged; `chat_messages` cascades from it)."""
    import inspect

    from app.api.v1.endpoints import chat as chat_ep
    from app.api.v1.endpoints.users import _UNLINKED_USER_TABLES

    pill = {"kind": "web", "label": "Web", "detail": "Reuters", "title": "A title",
            "url": "https://www.reuters.com/a/b", "published_at": "2026-09-30"}
    base = {"label": "Cay research report", "detail": "AAPL"}

    live, stored = chat_ep._turn_sources([base], [pill], persist=True)
    assert stored == live == [base, pill]
    rich = chat_ep._rich_content_for_turn({"stages": []}, None, stored)
    assert rich["sources"] == [base, pill]
    replay = chat_ep._row_to_message({
        "id": "m1", "session_id": "s1", "role": "assistant", "content": "Answer.",
        "rich_content": rich, "created_at": "2026-10-09T00:00:00+00:00",
    })
    assert replay.sources == [base, pill], "a reopened chat no longer shows the saved source list"

    code = "\n".join(
        line for line in inspect.getsource(chat_ep.delete_chat_session).splitlines()
        if not line.lstrip().startswith("#")
    )
    assert re.search(r'table\(\s*"chat_messages"\s*\)\s*\.delete\(\)\s*\.eq\(\s*"session_id"', code), (
        "deleting a conversation no longer deletes its messages (and their saved source lists)"
    )
    assert "chat_sessions" in _UNLINKED_USER_TABLES, "account deletion no longer purges chats"
    snapshot = (_BACKEND / "database" / "schema_snapshot.sql").read_text(encoding="utf-8")
    assert re.search(
        r"chat_messages_session_id_fkey FOREIGN KEY \(session_id\) REFERENCES "
        r"public\.chat_sessions\(id\) ON DELETE CASCADE", snapshot,
    ), "chat_messages no longer cascades from chat_sessions — account deletion would leave them"


def test_the_consent_sheet_discloses_the_web_search_query_without_naming_the_vendor():
    path = _IOS_SCREENS_DIR / "AIDataConsentView.swift"
    prose = _normalized_prose(_swift_user_facing_strings(path), strip_tags=False)
    # Both triggers — the ask AND the automatic search — in every chat, or a v3 consent does not
    # cover what the server opens for it (App Review 5.1.2(i)).
    assert "in any chat, when you ask cay ai to search the web" in prose
    assert "or when caydex's own data can't answer your question" in prose
    assert "a short search query (such as the company, its ticker and the topic) goes to a web search provider" in prose
    assert "amounts, percentages and email addresses are removed from it" in prose
    # This text ships in the binary (a deploy cannot correct it), so it makes no promise about
    # prices: that rule is a model instruction, which only the Privacy Policy states — as one.
    assert "price" not in prose, "the consent sheet promises something about prices no code enforces"
    for vendor in ("brave", "google", "gemini", "bing", "openai"):
        assert vendor not in prose, vendor


@pytest.mark.parametrize("path", _WEB_SEARCH_COPIES, ids=lambda p: p.name)
def test_no_copy_still_limits_web_search_to_an_explicit_report_chat_ask(path):
    """The other direction (2026-10-09). A copy that still says "only when you ask, in a report
    chat" beside the new every-chat clause publishes two contradictory promises — and the old one
    is the one a reader relies on to say no search happened without their asking."""
    prose = _copy_prose(path)
    for claim in _STALE_WEB_SEARCH_CLAIMS:
        assert claim not in prose, f"{path.name} still says {claim!r}, which is no longer true"


# Promises the code does NOT enforce, which an earlier 2026-10-09 draft made flatly. The query is
# MODEL output (it can name a second company, or anything the user typed), and "never for prices"
# is an instruction to the model, checked by no code before the search runs. A privacy policy
# that states them as facts is false the first time the model strays.
_UNENFORCED_WEB_SEARCH_CLAIMS = (
    "never searches the web for prices",
    "never searches the web for market data",
    "naming only the company",
    "names only the company",
    "the query never includes",
    "never figures or your personal details",
)


@pytest.mark.parametrize("path", _WEB_SEARCH_COPIES, ids=lambda p: p.name)
def test_no_copy_states_an_unenforced_web_search_promise_as_fact(path):
    prose = _copy_prose(path)
    for claim in _UNENFORCED_WEB_SEARCH_CLAIMS:
        assert claim not in prose, f"{path.name} says {claim!r}, which no code enforces"


def test_the_instructed_not_for_prices_claim_matches_both_model_facing_descriptions():
    """Privacy §3 says Cay AI is INSTRUCTED not to use web search for prices or other market data.
    The instruction lives in two model-facing texts — the tool declaration and the capability line
    in the system prompt — and each must still SAY it (the phrase, not merely the word "prices",
    which "use it for prices" would also contain)."""
    from app.services.agents.chat_tools import TOOL_CAPABILITIES, TOOL_DESCRIPTIONS, WEB_SEARCH_TOOL

    body = _normalized_prose((_SERVED / "privacy.html").read_text(encoding="utf-8"), strip_tags=True)
    assert "cay ai is instructed not to use web search for prices or other market data" in body
    declaration = " ".join(TOOL_DESCRIPTIONS[WEB_SEARCH_TOOL].lower().split())
    capability = " ".join(TOOL_CAPABILITIES[WEB_SEARCH_TOOL].lower().split())
    assert "never use this for prices, quotes, price changes or other market data" in declaration, (
        "the web_search declaration no longer tells the model to keep prices off the web"
    )
    assert "never for prices, quotes or market data" in capability, (
        "the web_search capability line no longer tells the model to keep prices off the web"
    )


@pytest.mark.parametrize(
    "raw, dropped_tokens, absent_text, kept_tokens",
    [
        ("Apple revenue $391 billion 25% margin 1,234 jane.doe@example.com https://example.com/x 2025 Q3",
         ("$391", "391", "billion", "25%", "25", "1,234"), ("@", "example.com", "http", "$", "%"),
         ("Apple", "2025", "Q3")),
        ("Tesla 4.5 percent 12.3bn 900 jane@x.co www.tsla.com 10-K",
         ("4.5", "percent", "12.3bn", "900"), ("@", "jane", "tsla.com"), ("Tesla", "10-K")),
        ("NVDA lawsuit 1e9 \u20ac5,000 \u00a3300 \u00a540 -7% +12% 0.5",
         ("1e9", "5,000", "300", "40", "-7%", "7", "+12%", "12", "0.5"), ("\u20ac", "\u00a3", "\u00a5", "%"),
         ("NVDA", "lawsuit")),
    ],
)
def test_the_query_removal_claim_matches_the_sanitizer(raw, dropped_tokens, absent_text, kept_tokens):
    """Privacy §3 and the consent sheet say amounts, percentages, links and email addresses are
    removed from the query before it is sent. `sanitize_web_query` is the one choke point every
    query passes on its way to the provider (`run_web_search`); a year, a quarter and an SEC
    form stay, as the copy's "time period" says."""
    from app.services.chat_web_search_service import sanitize_web_query

    out = sanitize_web_query(raw)
    assert out is not None, raw
    tokens = out.split()
    for gone in dropped_tokens:
        assert gone not in tokens, f"{gone!r} survived: {out!r}"
    for text in absent_text:
        assert text not in out, f"{text!r} survived: {out!r}"
    for kept in kept_tokens:
        assert kept in tokens, f"{kept!r} was dropped: {out!r}"


@pytest.mark.parametrize("raw", [None, 42, "", "   ", "$391 25% 1,234", "jane@example.com https://x.com"])
def test_a_query_with_nothing_left_after_removal_is_never_sent(raw):
    """Degraded input: nothing searchable left → None (no search), never an empty or raw query."""
    from app.services.chat_web_search_service import sanitize_web_query

    assert sanitize_web_query(raw) is None


def test_the_published_effective_date_is_never_in_the_future():
    """A policy dated ahead of today tells a reader it took effect on a day that has not come.
    UTC is the reference: the served page is the same for every reader, and the backend's day
    rolls over in UTC. Monotonic — once true it stays true."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date()
    for doc in ("privacy", "terms"):
        for path in (_SERVED / f"{doc}.html", _BACKEND.parent / "documents" / "legal" / f"{doc}.html"):
            dates = re.findall(r"Last updated:\s*([A-Z][a-z]+ \d{1,2}, \d{4})", path.read_text(encoding="utf-8"))
            assert len(dates) == 1, f"{path}: expected one 'Last updated' line, found {dates}"
            stated = datetime.strptime(dates[0], "%B %d, %Y").date()
            assert stated <= today, f"{path.name} is dated {stated}, after today ({today} UTC)"


def test_the_stale_claim_scan_is_not_vacuous():
    """Guard against the guard: every copy is real prose that DOES discuss web search, and the
    stale phrases would be caught if planted — so an empty haystack cannot pass the test above."""
    for path in _WEB_SEARCH_COPIES:
        prose = _copy_prose(path)
        assert len(prose) > 1500, f"{path.name} scan returned {len(prose)} chars"
        assert "web search" in prose, f"{path.name} scan lost the web-search copy"
    planted = _normalized_prose(
        "<p><strong>Web search in report chat:</strong> only when you are chatting about a "
        "research report</p>", strip_tags=True,
    )
    assert any(claim in planted for claim in _STALE_WEB_SEARCH_CLAIMS)


def test_the_consent_clause_matches_the_consent_version_that_opens_the_new_flows():
    """The copy promises every-chat and automatic search only after a permission screen that
    describes them. That screen is consent v3 (`AIConsentStore.currentVersion`), and the app sends
    the ACCEPTED version as `X-AI-Consent-Version` so the server can hold the line. If the version
    or the header went away, the consent clause on all three copies would be unenforceable."""
    store = (_BACKEND.parent / "frontend" / "ios" / "ios" / "Core" / "Services"
             / "AIConsentStore.swift").read_text(encoding="utf-8")
    code = "\n".join(l for l in store.splitlines() if not l.lstrip().startswith("//"))
    match = re.search(r"static let currentVersion\s*=\s*(\d+)", code)
    assert match and int(match.group(1)) >= 3, "consent v3 is what discloses every-chat search"
    client = (_BACKEND.parent / "frontend" / "ios" / "ios" / "Core" / "Services"
              / "APIClient.swift").read_text(encoding="utf-8")
    client_code = "\n".join("" if l.lstrip().startswith("//") else re.sub(r"\s//.*$", "", l)
                            for l in client.splitlines())
    assert 'request.setValue(String(consentVersion), forHTTPHeaderField: "X-AI-Consent-Version")' in client_code
    body = _normalized_prose((_SERVED / "privacy.html").read_text(encoding="utf-8"), strip_tags=True)
    assert "allowed ai chat on a permission screen that describes them" in body


def test_the_summary_box_covers_the_web_search_on_both_sides(client):
    """The summary bullet lives in the Swift intro's multi-line string, which the literal
    scanner does not read — so, like `test_in_app_privacy_policy_carries_the_same_summary`, the
    raw source is checked (lowercased, whitespace collapsed)."""
    web = _normalized_prose(client.get("/privacy").text, strip_tags=True)
    swift = _normalized_prose((_IOS_SCREENS_DIR / "PrivacyPolicyView.swift").read_text(encoding="utf-8"),
                              strip_tags=False)
    for haystack, name in ((web, "/privacy"), (swift, "PrivacyPolicyView.swift")):
        assert "your identity is not sent to the ai or to our web search provider" in haystack, name
        assert "not transmitted with your message or with a web search" in haystack, name


def test_the_instructed_not_for_prices_claim_holds_for_every_web_tier_text():
    """Since 2026-10-08 the web tool is declared with its TIER's text (an explicit ask, a news ask,
    the automatic fallback). Privacy §3's "instructed not to use web search for prices or other
    market data" must hold for whichever one the model reads."""
    from app.services.agents.chat_tools import (
        WEB_SEARCH_MODES, WEB_SEARCH_TIER_CAPABILITIES, WEB_SEARCH_TIER_DESCRIPTIONS,
    )
    assert set(WEB_SEARCH_MODES) == set(WEB_SEARCH_TIER_DESCRIPTIONS) == set(WEB_SEARCH_TIER_CAPABILITIES)
    for mode in WEB_SEARCH_MODES:
        declaration = " ".join(WEB_SEARCH_TIER_DESCRIPTIONS[mode].lower().split())
        capability = " ".join(WEB_SEARCH_TIER_CAPABILITIES[mode].lower().split())
        assert "never use this for prices, quotes, price changes or other market data" in declaration, mode
        assert "never for prices, quotes or market data" in capability, mode


def test_the_consent_clause_is_enforced_by_the_backend_gate():
    """The copy says every-chat and automatic search happen only after the permission screen that
    describes them (consent v3). The backend's floor for those tiers must be at least that version,
    and a lower value must fail the deploy rather than quietly open them."""
    from app.config import Settings
    field = Settings.model_fields["CHAT_WEB_SEARCH_MIN_CONSENT_VERSION"]
    assert field.default >= 3
    assert any(getattr(m, "ge", None) is not None and m.ge >= 3 for m in field.metadata)


# ── Reward credits for a confirmed issue report (migration 199, 2026-10-17) ──
#
# Profile → Help Us Improve now reads "Report an issue · earn free credits". The owner checks each
# report by hand and only then grants (`scripts/grant_feedback_reward.py --apply`); nothing in the
# app or the backend grants on its own. Terms §5 is what makes that binding — sole discretion, not
# every report qualifies, no cash value, and never for a rating or review (App Store Guideline
# 3.2.2 forbids rewarding them). Privacy §1/§6 disclose what the report now carries and where it
# stays: the account ID, so a reward can reach the right account, and the email itself, which
# account deletion does not reach. Website and in-app mirror are pinned TOGETHER, for the same
# reason as the credit-period clauses: the 2026-08-13 carve-out shipped on the website and was
# forgotten in the app.
#
# No phrase carries an apostrophe, on purpose: the HTML writes `&rsquo;` and the Swift literals a
# straight `'` or `\u{2019}`, so an apostrophe would pin the encoding rather than the meaning.
_REWARD_CLAUSES = {
    "terms": (
        "terms.html",
        "TermsOfUseView.swift",
        [
            "reward credits",
            "at our sole discretion",
            "not every report qualifies",
            "have no cash value",
            "cannot be transferred",
            "never given for app store ratings or reviews",
            "change or end this program at any time",
            # The balance is shared with credit packs, so an Apple pack refund can take reward
            # credits with it (revoke_purchased_credits). Said, so the clause above is not the
            # only way the Terms say they can go.
            "if apple refunds a credit pack",
        ],
    ),
    "privacy": (
        "privacy.html",
        "PrivacyPolicyView.swift",
        [
            "includes your account id",
            "add reward credits to that account",
            "not deleted with your account",
            "to reply to you",
        ],
    ),
}
# What Privacy §1 said before reports could earn credits. It is false now that a confirmed report
# is also used to credit the account it names — kept beside the new clause, the two contradict.
_RETIRED_SUPPORT_REPORT_PURPOSE = "solely to reproduce and fix the problem"


@pytest.mark.parametrize("doc", sorted(_REWARD_CLAUSES))
def test_the_website_and_the_app_state_the_same_reward_credit_clauses(doc):
    html_name, screen, clauses = _REWARD_CLAUSES[doc]
    # Both HTML copies: the served one always, the authored one when this checkout carries it
    # (it is outside the deploy context). Byte-equality is pinned above; this keeps the clause
    # check honest even if that test is ever skipped.
    htmls = [_SERVED / html_name] + [p for p in (_AUTHORED / html_name,) if p.is_file()]
    prose = _copy_prose(_IOS_SCREENS_DIR / screen)
    assert len(prose) > 1000, f"{screen} scan returned {len(prose)} chars — extractor drifted"
    for html_path in htmls:
        html = _copy_prose(html_path)
        for clause in clauses:
            assert clause in html, (
                f"{html_path.relative_to(_BACKEND.parent)} no longer states {clause!r}"
            )
    for clause in clauses:
        assert clause in prose, f"{screen} does not state {clause!r}, but {html_name} does"


@pytest.mark.parametrize("path", _PRIVACY_COPIES, ids=lambda p: str(p.relative_to(_BACKEND.parent)))
def test_no_privacy_copy_still_says_reports_are_used_solely_to_fix_the_problem(path):
    """The other direction. A copy carrying the old purpose beside the new one publishes two
    contradictory promises, and the narrower one is what a reader would rely on."""
    if not path.is_file():
        pytest.skip(f"{path} not present (expected outside the repo checkout)")
    prose = _copy_prose(path)
    # Positive anchor first, so an empty or mis-parsed haystack cannot pass the ban below.
    assert "add reward credits to that account" in prose, f"{path.name} lost the new purpose"
    assert _RETIRED_SUPPORT_REPORT_PURPOSE not in prose, (
        f"{path.name} still says support reports are used {_RETIRED_SUPPORT_REPORT_PURPOSE!r}, "
        f"but a confirmed report is now also used to add reward credits to the account it names"
    )
    # The summary's old promise. Support mail, which now carries the account ID, survives an
    # in-app account deletion (§6), so "everything" overstated it. The Swift summary is one
    # multi-line literal the prose extractor does not read, so the raw text is checked too.
    raw = re.sub(r"\s+", " ", path.read_text(encoding="utf-8").lower())
    for text in (prose, raw):
        assert "you can delete everything" not in text, (
            f"{path.name}'s summary still says 'You can delete everything', but §6 says support "
            f"emails are kept until the user asks"
        )
    assert "emails you send to support are kept until you ask us to delete them" in raw, (
        f"{path.name}'s summary no longer says support emails outlive an in-app deletion"
    )
