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
    with TestClient(app) as c:
        yield c


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
    import re

    text = path.read_text(encoding="utf-8")
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
