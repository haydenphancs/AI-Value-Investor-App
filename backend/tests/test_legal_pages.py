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


# ── The in-app legal screens must agree with the served pages ─────────────────

_IOS_LEGAL_SCREENS = [
    "DisclaimersView.swift",
    "DisclaimerAcknowledgementView.swift",
    "TermsOfUseView.swift",
    "PrivacyPolicyView.swift",
]

_IOS_SCREENS_DIR = _BACKEND.parent / "frontend" / "ios" / "ios" / "Views" / "Screens"


def _swift_user_facing_strings(path: Path) -> str:
    """Concatenate the double-quoted string literals in a Swift file.

    Crude on purpose. Identifiers like `.warrenBuffett` and asset names like
    `"icon_persona_buffett"` are NOT user-facing, and the persona enum legitimately keys on
    `"warren_buffett"` to match `agent_personas.persona_key` — so this looks at displayed
    prose only, and the callers below scope it to the four legal screens. A blanket scan of
    `Views/` would fire on the whale trackers, which name real 13F filers as a matter of
    public record and are correct to do so.
    """
    import re

    text = path.read_text(encoding="utf-8")
    # `\n` must be excluded from the character class. Without it the match runs from one
    # literal's CLOSING quote to the next literal's OPENING quote — i.e. it returns the Swift
    # code between the strings instead of the strings, which silently makes every assertion
    # here test nothing.
    return "\n".join(re.findall(r'"([^"\\\n]{12,})"', text))


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


def test_a_missing_page_404s_rather_than_500s(client, monkeypatch, tmp_path):
    """If a deploy omits the files, the route must fail as a clean 404 (and log), not blow up
    as an unhandled 500 — the latter would also fire a Sentry alert per request."""
    from app import main as main_mod

    monkeypatch.setattr(main_mod, "_LEGAL_DIR", tmp_path)
    r = client.get("/privacy")
    assert r.status_code == 404
