"""Pins the Supabase Auth email templates IN THIS REPOSITORY, and the documents that point at them.

⚠️ WHAT THIS CAN AND CANNOT SEE
-------------------------------
Supabase, not the backend, sends the sign-up and password-reset emails, from templates that live
only in the Supabase dashboard. That dashboard has no history and no API this suite may call (the
suite is hermetic). So this module pins the DOCUMENTS: the canonical template files under
`documents/legal/supabase-email-templates/`, their README, and the runbooks that tell the owner
to paste them. **It does not, and cannot, check what Supabase is sending today.** A green run means
the files are right. Only the owner's manual check proves the live dashboard holds them: after any
change on the Emails screen, register a fresh address AND run Forgot password, and read both
emails (LAUNCH_CHECKLIST §5b(a), OWNER_TASKS §2.4). Treat this as drift documentation, not
production detection.

WHY IT EXISTS (2026-11-26)
--------------------------
LAUNCH_CHECKLIST §5b(a) gave only the Reset Password body, inline, and said of Confirm signup only
"check it is sensible". The reset wording ended up in the **Confirm signup** template: subject
"Confirm Your Signup", reset copy, an 8-digit code and no link. No app screen or backend route could
use a sign-up code. Every new email/password account was stuck, and the whole suite stayed green,
because nothing in the repo said what Confirm signup must contain (audit TG-2, SB-1, SB-2).

The fix ships both bodies as files that are pasted whole. This module pins the contract those
files carry:
  * Confirm signup holds the code (`{{ .Token }}`) AND the token-hash link to our own
    `/auth/confirm` page (for 1.0–1.03 installs, which have no code screen). It never holds
    `{{ .ConfirmationURL }}`: that implicit-flow link would park a live Supabase session in browser
    history, and `/auth/session-exchange` would mint a Caydex session from it (audit CC-02). It
    never holds reset wording.
  * Reset Password holds the code only: no link, no "confirm" wording.
  * Neither states a code length. The backend accepts 6–12 digits, and the length is a dashboard
    setting (8 today) that can change.
  * The link lands on a route that exists: the root GET/POST `/auth/confirm` page, and the code
    reaches `POST /api/v1/auth/confirm-email`.

`LAUNCH_CHECKLIST.md` and `LAUNCH_RUNBOOK.md` are GITIGNORED (they carry the Supabase project ref),
so their checks SKIP when the file is absent (a `git archive` export, CI), and FAIL when the file
is present but still carries the old inline template, the "6-digit" claim, or no pointer to the
files.

ROUND 2 (2026-11-26, review findings DOC-2, DOC-4, COMPAT-3, BE-AUTH-02, SEC-8, and the shared
contract's password rule, error codes and log redaction). The owner reads these docs to act, so a
doc that disagrees with the code is a wrong action, not a typo. Pinned below:
  * OWNER_TASKS: the IP Address Forwarding switch is a step AFTER the deploy (the backend's
    `Sb-Forwarded-For` header is inert until it is ON); the stuck-users step splits by app version
    (the new build's sign-in sends no email); a Rollback note restores the EXACT former
    `SUPABASE_JWT_SECRET`, never a placeholder, and never re-pastes the broken template. No tracked
    file carries the Supabase project ref.
  * §9.1: forwarding is conditional on the owner switch; the link's password residual is recorded
    next to the "code alone" sentence; the error-code table matches `error_response`'s defaults;
    every AUTH_/EMAIL_ code the auth docs name is real; the documented password rule IS the
    validator's (checked by behaviour, outliers included); the link page's quoted copy is the
    page's; the access-log claim holds for `redact_secrets`.
  * §9.2: the fresh-install purge names the real UserDefaults and App Group keys.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_TEMPLATES = _REPO / "documents" / "legal" / "supabase-email-templates"
_CONFIRM = _TEMPLATES / "confirm-signup.html"
_RESET = _TEMPLATES / "reset-password.html"
_CHANGE = _TEMPLATES / "change-email.html"
_README = _TEMPLATES / "README.md"
_CHECKLIST = _REPO / "documents" / "legal" / "LAUNCH_CHECKLIST.md"   # GITIGNORED — local only
_RUNBOOK = _REPO / "documents" / "legal" / "LAUNCH_RUNBOOK.md"       # GITIGNORED — local only
_OWNER_TASKS = _REPO / "documents" / "OWNER_TASKS.md"
_DESIGN = _REPO / "documents" / "System Design" / "SYSTEM_DESIGN_GUIDELINES.md"

#: The shared contract (plan §1, item 5): the exact link the Confirm-signup email must carry,
#: as the browser sees it after HTML-decoding the attribute.
_CONFIRM_LINK = "https://caydexinvest.com/auth/confirm?token_hash={{ .TokenHash }}&type=signup"
#: …and as it is spelled in the HTML source (`&` escaped inside an attribute).
_CONFIRM_LINK_SOURCE = _CONFIRM_LINK.replace("&", "&amp;")

_SUBJECTS = {
    "Confirm signup": ("Confirm your Caydex email", "confirm-signup.html"),
    "Reset Password": ("Reset your Caydex password", "reset-password.html"),
    "Change Email Address": ("Confirm your Caydex email change", "change-email.html"),
}

#: Every `{{ … }}` action, with any spacing. Go templates are case-sensitive: `{{ .token }}`
#: fails to render, and GoTrue then sends no email at all.
_ACTION = re.compile(r"\{\{-?\s*(.*?)\s*-?\}\}", re.S)

_VOID = {"meta", "br", "hr", "img", "input", "link", "area", "base", "col", "embed", "source",
         "track", "wbr"}


def _read(path: Path) -> str:
    assert path.is_file(), f"missing {path.relative_to(_REPO)}"
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Whitespace-folded, so a re-wrapped sentence still matches."""
    return " ".join(text.split())


class _Collector(HTMLParser):
    """Collects hrefs, src attributes, the <title> and the visible text; checks tag balance."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []
        self.srcs: list[str] = []
        self.title = ""
        self.text: list[str] = []
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.comments: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "href" in a:
            self.hrefs.append(a["href"] or "")
        if "src" in a:
            self.srcs.append(a["src"] or "")
        if tag == "title":
            self._in_title = True
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        a = dict(attrs)
        if "href" in a:
            self.hrefs.append(a["href"] or "")
        if "src" in a:
            self.srcs.append(a["src"] or "")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closes {self.stack[-1:] or 'nothing'}")
            return
        self.stack.pop()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        else:
            self.text.append(data)

    def handle_comment(self, data):
        self.comments.append(data)


def _parse(path: Path) -> _Collector:
    c = _Collector()
    c.feed(_read(path))
    c.close()
    return c


def _actions(path: Path) -> list[str]:
    return [" ".join(a.split()) for a in _ACTION.findall(_read(path))]


def _readme_rows() -> dict[str, tuple[str, str]]:
    """template name -> (subject, body file), from the README's 'What to paste' table."""
    rows = {}
    for line in _read(_README).splitlines():
        m = re.match(r"^\|\s*([A-Za-z ]+?)\s*\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|\s*$", line)
        if m:
            rows[m.group(1)] = (m.group(2), m.group(3))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# The two template files
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [_CONFIRM, _RESET, _CHANGE], ids=["confirm", "reset", "change"])
def test_each_template_is_well_formed_html_with_no_comments_scripts_or_remote_resources(path):
    c = _parse(path)
    assert not c.errors and not c.stack, f"{path.name}: unbalanced tags {c.errors} open={c.stack}"
    # A comment is SENT with the email (visible in the source) and is where a stray
    # `{{ .ConfirmationURL }}` would hide from a reader of the rendered preview.
    assert not c.comments, f"{path.name} carries HTML comments; put notes in README.md instead"
    raw = _read(path).lower()
    assert "<script" not in raw, f"{path.name} carries a script"
    assert not c.srcs, f"{path.name} loads a remote resource (tracking pixel / image): {c.srcs}"
    assert '<meta charset="utf-8">' in raw, f"{path.name} must declare UTF-8 (it holds an em dash)"


def test_confirm_signup_carries_the_code_and_exactly_the_contract_link():
    actions = _actions(_CONFIRM)
    assert sorted(actions) == [".Token", ".TokenHash"], (
        f"confirm-signup.html may use exactly `{{{{ .Token }}}}` once and `{{{{ .TokenHash }}}}` "
        f"once (inside the link); found {actions}. Anything else — `.ConfirmationURL`, `.SiteURL`, "
        f"a misspelt `.token` — is either the implicit-flow session leak or an email GoTrue "
        f"cannot render."
    )
    c = _parse(_CONFIRM)
    assert c.hrefs == [_CONFIRM_LINK], (
        f"confirm-signup.html must carry ONE link, exactly {_CONFIRM_LINK!r} (the root "
        f"/auth/confirm page reads token_hash + type=signup); found {c.hrefs}"
    )
    assert _CONFIRM_LINK_SOURCE in _read(_CONFIRM), "the link's `&` must be written `&amp;`"


def test_confirm_signup_says_confirm_never_reset():
    raw = _read(_CONFIRM)
    text = _flat(" ".join(_parse(_CONFIRM).text))
    for forbidden in ("reset", "won't change", "won’t change", "ConfirmationURL"):
        assert forbidden.lower() not in raw.lower(), (
            f"confirm-signup.html contains {forbidden!r}: the 2026-11-26 dead end was the reset "
            f"wording pasted into this template"
        )
    for required in (
        "Enter this code in the Caydex app to finish creating your account:",
        "The code expires shortly.",
        "Using an older version of the app?",
        "Confirm my email",
        "instead, then go back to the app and sign in.",
        "If you didn't create a Caydex account, you can ignore this email.",
    ):
        assert required in text, f"confirm-signup.html lost the line {required!r}"


def test_reset_password_carries_the_code_only_and_never_confirm_wording():
    actions = _actions(_RESET)
    assert actions == [".Token"], (
        f"reset-password.html may use exactly `{{{{ .Token }}}}` once; found {actions}. The app "
        f"asks for a CODE — a link (`.ConfirmationURL`) here is a dead end."
    )
    c = _parse(_RESET)
    assert not c.hrefs, f"reset-password.html must carry no link at all; found {c.hrefs}"
    raw = _read(_RESET)
    assert "ConfirmationURL" not in raw
    assert not re.search(r"confirm", raw, re.I), (
        "reset-password.html says 'confirm' — the two templates' wording must never mix"
    )
    text = _flat(" ".join(c.text))
    for required in (
        "Use this code to reset your password:",
        "Enter it in the app along with your new password. The code expires shortly.",
        "If you didn't request this, you can ignore this email — your password won't change.",
    ):
        assert required in text, f"reset-password.html lost the line {required!r}"


@pytest.mark.parametrize("path", [_CONFIRM, _RESET, _CHANGE], ids=["confirm", "reset", "change"])
def test_templates_never_state_a_code_length(path):
    """The length is the dashboard's Email OTP Length (8 today; 6 in the old docs). The backend
    accepts 6–12, so the copy must not promise one — "6-digit" is exactly the drift (SB-7)."""
    text = _read(path)
    assert not re.search(r"\b\d+\s*-?\s*digit", text, re.I), f"{path.name} states a code length"
    assert not re.search(r"\b(six|seven|eight|nine|ten)[\s-]+digit", text, re.I), path.name


def test_change_email_carries_the_code_and_the_new_address_only():
    """Audit CC-09 (2026-11-26). Supabase sends this ONE template twice per change — to the
    current address and to the new one, each with its own `{{ .Token }}` — so it may use exactly
    the code and the new address. `{{ .Email }}` (the CURRENT address) would tell whoever owns a
    mistyped new address whose account it is; `{{ .ConfirmationURL }}` is the implicit-flow
    session leak (CC-02) and, for an app that asks for codes, a dead end."""
    actions = _actions(_CHANGE)
    assert sorted(actions) == [".NewEmail", ".Token"], (
        f"change-email.html may use exactly `{{{{ .Token }}}}` and `{{{{ .NewEmail }}}}` once each; "
        f"found {actions}"
    )
    c = _parse(_CHANGE)
    assert not c.hrefs, f"change-email.html must carry no link at all; found {c.hrefs}"
    raw = _read(_CHANGE)
    assert "ConfirmationURL" not in raw and ".Email }}" not in raw.replace(".NewEmail }}", "")
    assert not re.search(r"reset", raw, re.I), "change-email.html says 'reset'"
    text = _flat(" ".join(c.text))
    for required in (
        "Someone asked to change the email address of a Caydex account to",
        "To approve it, enter this code in the Caydex app:",
        "We sent one code to the account's current email and another to the new one.",
        "The app asks for both. The codes expire shortly.",
        "If you didn't ask for this, don't share this code. Without it the email stays the same.",
    ):
        assert required in text, f"change-email.html lost the line {required!r}"


def test_readme_says_secure_email_change_stays_on():
    """The app asks for TWO codes. With Secure email change off, Supabase sends one (to the new
    address only): the app would wait forever, and a password alone would move the account."""
    text = _flat(_read(_README))
    assert "keep **Secure email change** ON" in text
    assert "double_confirm_changes = true" in text
    assert "[auth.email.template.email_change]" in text
    assert "POST /api/v1/auth/change-email/confirm" in text


def test_the_change_email_codes_land_on_real_routes():
    routes = _routes()
    assert "POST" in routes.get("/api/v1/auth/change-email", set())
    assert "POST" in routes.get("/api/v1/auth/change-email/confirm", set())


def test_owner_tasks_points_at_the_change_email_template():
    text = _read(_OWNER_TASKS)
    assert "documents/legal/supabase-email-templates/change-email.html" in text
    assert "Secure email change" in text


# ─────────────────────────────────────────────────────────────────────────────
# README: the subjects the owner pastes, and the files they name
# ─────────────────────────────────────────────────────────────────────────────

def test_readme_names_both_templates_with_their_exact_subjects():
    rows = _readme_rows()
    assert rows == {name: (subj, f) for name, (subj, f) in _SUBJECTS.items()}, (
        f"README.md's 'What to paste' table must list exactly {_SUBJECTS}; found {rows}"
    )
    for _subject, filename in rows.values():
        assert (_TEMPLATES / filename).is_file(), f"README names {filename}, which does not exist"


@pytest.mark.parametrize("name", sorted(_SUBJECTS))
def test_each_template_title_matches_its_readme_subject(name):
    subject, filename = _SUBJECTS[name]
    title = _flat(_parse(_TEMPLATES / filename).title)
    assert title == subject, (
        f"{filename}'s <title> is {title!r} but its subject is {subject!r}: the title is how the "
        f"owner tells the two bodies apart while pasting"
    )


def test_readme_says_it_pins_files_not_the_dashboard_and_never_to_use_confirmation_url():
    text = _flat(_read(_README))
    assert "cannot read the live dashboard" in text
    assert "Never use `{{ .ConfirmationURL }}`" in text
    assert "after ANY change on the Emails screen" in text


# ─────────────────────────────────────────────────────────────────────────────
# The link and the code land on routes that exist
# ─────────────────────────────────────────────────────────────────────────────

def _routes() -> dict[str, set[str]]:
    import app.main as main_mod

    out: dict[str, set[str]] = {}
    for r in main_mod.app.routes:
        path = getattr(r, "path", None)
        if path:
            out.setdefault(path, set()).update(getattr(r, "methods", None) or ())
    return out


def test_the_link_and_the_code_land_on_real_routes():
    routes = _routes()
    assert len(routes) >= 100, "anti-vacuity: the scan must read the real app"
    assert {"GET", "POST"} <= routes.get("/auth/confirm", set()), (
        "confirm-signup.html links to /auth/confirm, but the app serves "
        f"{sorted(routes.get('/auth/confirm', set())) or 'no such route'} there: the GET must "
        "render the button page and the POST must verify"
    )
    assert "POST" in routes.get("/api/v1/auth/confirm-email", set()), (
        "the Confirm-signup code has no route to land on: POST /api/v1/auth/confirm-email"
    )


def test_the_page_accepts_the_link_the_template_renders():
    """Render the template's link the way GoTrue does and hand its query to the page's own
    validator. A drift on either side (`type=email`, a renamed parameter, a tightened hash rule)
    would send every 1.0–1.03 user to the "expired" page, which is the same dead end again."""
    import hashlib
    from urllib.parse import parse_qs, urlsplit

    from app.api.email_confirm_page import is_valid_link

    # GoTrue's TokenHash: hex(sha224(email + otp)), 56 lowercase hex characters.
    token_hash = hashlib.sha224(b"someone@example.com" + b"12345678").hexdigest()
    (href,) = _parse(_CONFIRM).hrefs
    rendered = href.replace("{{ .TokenHash }}", token_hash)
    parts = urlsplit(rendered)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "caydexinvest.com", "/auth/confirm")
    q = parse_qs(parts.query, keep_blank_values=True)
    assert set(q) == {"token_hash", "type"}, q
    assert is_valid_link(q["token_hash"][0], q["type"][0]), (
        f"the /auth/confirm page refuses the link confirm-signup.html renders: {rendered}"
    )
    # Anti-vacuity: the validator is not a yes-machine.
    assert not is_valid_link(q["token_hash"][0], "email")
    assert not is_valid_link("not-hex", q["type"][0])


# ─────────────────────────────────────────────────────────────────────────────
# The documents that tell the owner to paste them
# ─────────────────────────────────────────────────────────────────────────────

_PATHS = ("documents/legal/supabase-email-templates/confirm-signup.html",
          "documents/legal/supabase-email-templates/reset-password.html")


def _fenced_blocks(text: str) -> list[str]:
    return re.findall(r"```.*?```", text, flags=re.S)


def test_launch_checklist_points_at_both_files_and_holds_no_inline_template():
    if not _CHECKLIST.exists():
        pytest.skip("LAUNCH_CHECKLIST.md is gitignored and absent here (git archive / CI)")
    text = _CHECKLIST.read_text(encoding="utf-8")
    for p in _PATHS:
        assert p in text, f"LAUNCH_CHECKLIST.md no longer points at {p}"
    assert "### (a) Supabase Auth settings — exact values" in text
    assert "After ANY change on the Emails screen" in text
    # The old inline Reset body is what got pasted into the wrong slot: an inline copy is a
    # second source of truth that drifts. The files are the only bodies.
    assert "Use this code to reset your Caydex password" not in text
    inline = [b for b in _fenced_blocks(text) if "{{ .Token }}" in b or "{{ .ConfirmationURL }}" in b]
    assert not inline, f"LAUNCH_CHECKLIST.md carries an inline template body again: {inline[:1]}"
    assert not re.search(r"\b6-digit\b", text), "LAUNCH_CHECKLIST.md says 6-digit; the code is 8"


def test_launch_runbook_points_at_both_files():
    if not _RUNBOOK.exists():
        pytest.skip("LAUNCH_RUNBOOK.md is gitignored and absent here (git archive / CI)")
    text = _RUNBOOK.read_text(encoding="utf-8")
    for p in _PATHS:
        assert p in text, f"LAUNCH_RUNBOOK.md no longer points at {p}"
    assert not re.search(r"\b6-digit\b", text), "LAUNCH_RUNBOOK.md says 6-digit; the code is 8"


def test_owner_tasks_points_at_both_files_and_retires_the_reset_only_tick():
    text = _read(_OWNER_TASKS)
    for p in _PATHS:
        assert p in text, f"OWNER_TASKS.md no longer points at {p}"
    assert "documents/legal/supabase-email-templates/README.md" in text
    # The 2026-10-05 tick recorded ONLY the reset template, and was taken as "templates done".
    assert "**SUPERSEDED 2026-11-26**" in text
    assert "- [x] Supabase SMTP → Resend, plus `{{ .Token }}` in the reset-password template" not in text


def _design_section_9_1() -> str:
    text = _read(_DESIGN)
    start = text.index("### 9.1 Authentication Flow")
    end = text.index("### 9.2 ", start)
    return text[start:end]


def test_design_doc_describes_the_code_flow_and_keeps_its_corrections():
    sec = _flat(_design_section_9_1())
    assert _PATHS[0] in sec, "§9.1 must name the canonical Confirm-signup template"
    assert "POST /auth/confirm-email" in sec and "GET /auth/confirm" in sec
    assert not re.search(r"\b6-digit\b", sec), "§9.1 says 6-digit; the code is 8 (backend 6–12)"
    # Audit SEC-3: refresh tokens are re-issued, never rotated. Audit SEC-2: the password stamp
    # is not checked by the router-level gate, so "any JWT … is rejected" was an overclaim.
    assert "refresh tokens 7 d and rotate" not in sec
    assert "any JWT minted before the change is rejected" not in sec
    assert "re-issued, not rotated" in sec


# ─────────────────────────────────────────────────────────────────────────────
# Round 2 — OWNER_TASKS: the forwarding switch, the stuck users, the rollback
# ─────────────────────────────────────────────────────────────────────────────

_OWNER_ITEM_TITLE = "**Email sign-up confirmation: deploy, paste both email templates, then the app**"


def _owner_item() -> str:
    """The §2.1 'Email sign-up confirmation' item, up to the next top-level checklist item."""
    text = _read(_OWNER_TASKS)
    start = text.index(_OWNER_ITEM_TITLE)
    end = text.index("\n- [", start)
    return text[start:end]


def _owner_steps() -> list[tuple[int, str]]:
    """The item's numbered steps (`  N. …` at the item's indent), in document order."""
    item = _owner_item()
    marks = list(re.finditer(r"(?m)^  (\d+)\. ", item))
    assert len(marks) >= 8, f"anti-vacuity: expected the item's 8+ numbered steps, found {len(marks)}"
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else item.find("\n  **Rollback**", m.end())
        out.append((int(m.group(1)), item[m.start():end if end != -1 else len(item)]))
    return out


def _owner_step(title: str) -> tuple[int, str]:
    hits = [(n, body) for n, body in _owner_steps() if body.split("\n", 1)[0].find(title) != -1]
    assert len(hits) == 1, f"expected exactly one step titled {title!r}; found {[n for n, _ in hits]}"
    return hits[0]


def test_owner_tasks_steps_are_numbered_one_to_n_in_order():
    nums = [n for n, _ in _owner_steps()]
    assert nums == list(range(1, len(nums) + 1)), f"the steps are misnumbered: {nums}"


def test_owner_tasks_turns_on_ip_forwarding_only_after_the_deploy():
    """DOC-2: the `Sb-Forwarded-For` header is inert until the owner's switch is ON, and no doc
    told the owner it existed. The switch must come AFTER the deploy (before it, nothing sends the
    header) and must carry its two conditions and a check."""
    deploy_n, _ = _owner_step("**Deploy the backend**")
    fwd_n, fwd = _owner_step("**Turn on IP Address Forwarding")
    assert deploy_n < fwd_n, f"forwarding is step {fwd_n}, before the deploy (step {deploy_n})"
    flat = _flat(fwd)
    for needle in (
        "only AFTER step 1's deploy is live",
        "security_sb_forwarded_for_enabled",
        "`Sb-Forwarded-For`",
        "`sb_secret_`",               # Supabase honours the header only with a secret key
        "Railway's edge is the only proxy",
        "**Check it took effect:**",
        "lower them back",             # the single-IP sizing is too loose once it is ON
        "**If you ever turn it OFF again,**",
    ):
        assert needle in flat, f"the forwarding step lost {needle!r}"
    item = _flat(_owner_item())
    # The old advice was unconditional: "raise three limits for a single-IP backend … per-IP
    # limits are shared by all users". It is true only while the switch is OFF.
    assert "raise three limits for a single-IP backend" not in item
    _, dash = _owner_step("**Supabase dashboard.**")
    assert "Until step 3's switch is ON" in _flat(dash)
    assert f"{fwd_n}" == "3", "step 2's rate-limit bullet says 'until step 3': keep them in step"


def test_no_tracked_doc_carries_the_supabase_project_ref():
    """The project ref lives only in the gitignored launch docs. A Management API URL in a TRACKED
    doc must use a placeholder (a ref is 20 lowercase letters)."""
    ref_url = re.compile(r"api\.supabase\.com/v1/projects/[a-z]{20}\b")
    for path in (_OWNER_TASKS, _DESIGN, _README):
        hits = ref_url.findall(_read(path))
        assert not hits, f"{path.name} carries the Supabase project ref: {hits}"
    assert "projects/<project ref>/config/auth" in _owner_item(), "anti-vacuity: the URL moved"


def test_owner_tasks_stuck_users_step_splits_by_app_version():
    """DOC-4: the new build's sign-in sends NO email (the auto-resend was removed), so "sign in
    again, open the new email, enter the code (new build)" left those users waiting forever."""
    _, body = _owner_step("**Users stuck since about 2026-10-05.**")
    flat = _flat(body)
    assert "open the new email, enter the code (new build)" not in flat
    new_build = flat[flat.index("**The new build"):flat.index("**1.0–1.03:**")]
    old_build = flat[flat.index("**1.0–1.03:**"):flat.index("**Either way:**")]
    assert "**Send a new code**" in new_build and "NEWEST" in new_build
    assert "sends nothing" in new_build, "the new-build bullet must say sign-in sends no email"
    assert "re-sends the email" in old_build and "NEWEST" in old_build
    assert "FIRST registered" in old_build and "Forgot password" in old_build   # BE-AUTH-02
    from app.api.v1.endpoints import auth as auth_ep
    window_minutes = auth_ep.MAIL_SEND_WINDOW_SECONDS // 60
    assert f"at most 3 emails per address every {window_minutes} minutes" in flat, (
        "OWNER_TASKS states a different resend window from the code")


def test_owner_tasks_rollback_restores_the_exact_secret_and_never_the_old_template():
    """COMPAT-3. A placeholder secret is worse than none: the old code trusts ANY HS256 token
    signed with whatever SUPABASE_JWT_SECRET holds, so a guessable value is a forged session for
    anyone. And the "previous" Confirm-signup template is the broken one."""
    item = _owner_item()
    assert item.count("\n  **Rollback**") == 1, "the item needs exactly one Rollback note"
    last_step_n, last_step = _owner_steps()[-1]
    assert item.index("\n  **Rollback**") > item.index(last_step), "Rollback must follow the steps"
    rb = _flat(item[item.index("\n  **Rollback**"):])
    for needle in (
        "EXACT former value, never a placeholder",
        "Railway's variable history or your password manager",
        "**Do NOT re-paste the old Confirm signup template.**",
        "no `/auth/confirm` page",
        "raise them again",
    ):
        assert needle in rb, f"the Rollback note lost {needle!r}"
    _, secret_step = _owner_step("**Delete `SUPABASE_JWT_SECRET`")
    assert "**Rollback**" in secret_step, "the delete-the-secret step must point at the Rollback note"
    assert "Never put it in the repo, a document or a chat." in _flat(secret_step)


# ─────────────────────────────────────────────────────────────────────────────
# Round 2 — SYSTEM_DESIGN_GUIDELINES §9.1 / §9.2 against the code
# ─────────────────────────────────────────────────────────────────────────────

def test_design_doc_forwarding_is_conditional_on_the_owner_switch():
    sec = _flat(_design_section_9_1())
    assert "**Supabase sees one caller.**" not in sec, (
        "§9.1 again says Supabase sees one caller unconditionally; the backend sends "
        "Sb-Forwarded-For, and only the owner's switch decides whether it counts"
    )
    for needle in ("`Sb-Forwarded-For`", "**IP Address Forwarding**", "`sb_secret_`",
                   "only while the switch is OFF", "non-admin verb", "Railway's edge is the only proxy"):
        assert needle in sec, f"§9.1 lost {needle!r}"
    # …and the code half the paragraph describes is real.
    from app.database import GOTRUE_FORWARDED_FOR_HEADER
    from app.dependencies import forward_client_ip_to_gotrue

    assert GOTRUE_FORWARDED_FOR_HEADER == "Sb-Forwarded-For"
    assert callable(forward_client_ip_to_gotrue)


def test_design_doc_records_the_link_password_residual_next_to_the_code_alone_sentence():
    """BE-AUTH-02: the code path re-proves or sets the password; the LINK confirms on the token
    alone and keeps the first registrant's. Undocumented, the SEC-6 sentence read as covering both."""
    sec = _flat(_design_section_9_1())
    code_alone = sec.index("Signing in on the code alone was rejected")
    residual = sec.index("**The link does not have this protection, and that is an accepted residual.**")
    assert 0 < residual - code_alone < 300, "the residual must sit right after the code-alone sentence"
    for needle in ("whichever password GoTrue already holds", "FIRST registrant's",
                   '"Code + link"', "(1.02 and 1.03 in practice)"):
        assert needle in sec, f"§9.1's link residual lost {needle!r}"


def _section_9_1_code_table() -> dict[str, tuple[int, str]]:
    """code -> (first status, action) from §9.1's 'emailed code' table."""
    rows: dict[str, tuple[int, str]] = {}
    for line in _design_section_9_1().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 5 or not re.fullmatch(r"`[A-Z_]+`", cells[1]):
            continue
        status = re.match(r"(\d{3})", cells[2])
        action = re.fullmatch(r"`([a-z_]+)`", cells[3])
        assert status and action, f"unparseable code-table row: {line}"
        rows[cells[1].strip("`")] = (int(status.group(1)), action.group(1))
    return rows


def test_design_doc_code_table_matches_the_error_contract():
    """C2: the table the client authors read must agree with what `auth_error` sends by default."""
    from app.api.error_response import _DEFAULT_ACTIONS, _DEFAULT_STATUS, ErrorCode

    rows = _section_9_1_code_table()
    assert {"AUTH_CODE_INVALID", "AUTH_UNAVAILABLE", "AUTH_CONFIRMED_SIGN_IN_FAILED",
            "AUTH_PASSWORD_REJECTED", "AUTH_CODE_USED"} <= set(rows), f"table rows: {sorted(rows)}"
    bad = []
    for name, (status, action) in rows.items():
        code = ErrorCode[name]
        if _DEFAULT_STATUS.get(code) != status or _DEFAULT_ACTIONS.get(code) != action:
            bad.append(f"{name}: doc {status}/{action}, code "
                       f"{_DEFAULT_STATUS.get(code)}/{_DEFAULT_ACTIONS.get(code)}")
    assert not bad, "§9.1's code table disagrees with error_response:\n  " + "\n  ".join(bad)
    sec = _flat(_design_section_9_1())
    # The copy contract the screens rely on (C2).
    assert '`user_message` always begins "Your email is confirmed"' in sec
    assert "We couldn't set your new password, and that code has now been used." in sec
    assert "That password can't be used. Send a new code, then choose a different password." in sec


def test_every_auth_error_code_the_docs_name_is_a_real_error_code():
    from app.api.error_response import ErrorCode

    texts = {
        "SYSTEM_DESIGN_GUIDELINES §9.1": _design_section_9_1(),
        "OWNER_TASKS sign-up item": _owner_item(),
        "templates README": _read(_README),
    }
    if _CHECKLIST.exists():
        chk = _CHECKLIST.read_text(encoding="utf-8")
        texts["LAUNCH_CHECKLIST §5b"] = chk[chk.index("## 5b."):chk.index("### (b)", chk.index("## 5b."))]
    named: dict[str, set[str]] = {
        where: set(re.findall(r"`((?:AUTH|EMAIL)_[A-Z_]+)`", text)) for where, text in texts.items()
    }
    everything = set().union(*named.values())
    assert {"AUTH_CONFIRMED_SIGN_IN_FAILED", "AUTH_CODE_USED"} <= everything, "anti-vacuity"
    missing = {where: sorted(n for n in names if n not in ErrorCode.__members__)
               for where, names in named.items()}
    missing = {w: m for w, m in missing.items() if m}
    assert not missing, f"the auth docs name error codes the backend does not have: {missing}"


#: GoTrue's `lower_upper_letters_digits_symbols` symbol class — the shared contract's C1 set.
_GOTRUE_SYMBOLS = "!@#$%^&*()_+-=[]{};':\"|<>?,./`~\\"


def _documented_symbols() -> str:
    m = re.search(r"symbol from exactly these 32: `` (.+?) ``", _design_section_9_1())
    assert m, "§9.1 no longer states the symbol set as `` … `` after 'symbol from exactly these 32:'"
    return m.group(1)


def test_design_doc_states_gotrues_exact_symbol_set():
    doc = _documented_symbols()
    assert len(_GOTRUE_SYMBOLS) == len(set(_GOTRUE_SYMBOLS)) == 32
    assert doc == _GOTRUE_SYMBOLS, f"§9.1's symbol set drifted: {doc!r} != {_GOTRUE_SYMBOLS!r}"
    from app.schemas.auth import PASSWORD_SYMBOLS

    assert PASSWORD_SYMBOLS == doc, f"the backend's PASSWORD_SYMBOLS {PASSWORD_SYMBOLS!r} != §9.1's {doc!r}"
    sec = _flat(_design_section_9_1())
    for needle in ("at most 72 UTF-8 **bytes**", "Password is too long: 72 bytes at most (emoji and "
                   "accented letters use more than one).", "counts toward no class",
                   "A symbol such as ! @ # $ — spaces don't count"):
        assert needle in sec, f"§9.1's password rule lost {needle!r}"


def _accepts(password: str) -> bool:
    from app.schemas.auth import _validate_password_strength

    try:
        _validate_password_strength(password)
    except ValueError:
        return False
    return True


def test_documented_password_rule_is_the_validators_rule():
    """The doc's rule, checked against `_validate_password_strength` by BEHAVIOUR (C1).

    Each documented symbol alone makes a valid password; everything outside the 32 (a space
    inside, a non-ASCII punctuation mark, an accented or full-width letter, an emoji) counts as no
    class; the cap is 72 BYTES, not characters. A doc that says one thing while the validator does
    another is how the superset shipped: GoTrue refused passwords our side passed, after the reset
    code was spent."""
    from app.schemas.auth import _validate_password_strength

    doc = _documented_symbols()
    for sym in doc:
        pw = f"Abcd{sym}efg1"
        assert _accepts(pw), f"the doc lists {sym!r} as a symbol, but the validator refuses {pw!r}"
    for ch in (" ", " ", "　", "é", "€", "¿", "·", "—", "😀", "Ａ", "ｌ"):
        pw = f"Abcd{ch}efg1"
        assert not _accepts(pw), f"{ch!r} is outside the 32 and counts as no class, yet {pw!r} passed"
    # ASCII-only letter and digit classes.
    assert not _accepts("Ébcdefg1!"), "a non-ASCII capital is not 'an uppercase letter (A–Z)'"
    assert not _accepts("ABCDéFG1!"), "a non-ASCII small letter is not 'a lowercase letter (a–z)'"
    assert not _accepts("Abcdefg١!"), "an Arabic-Indic digit is not 'a number (0–9)'"
    # Length: 8 characters, 72 BYTES.
    assert _accepts("Aa1!bcde") and not _accepts("Aa1!bcd")
    assert _accepts("Aa1!" + "b" * 68), "exactly 72 bytes must pass"
    assert not _accepts("Aa1!" + "b" * 69), "73 bytes must be refused (GoTrue's cap)"
    assert _accepts("Aa1!" + "é" * 34), "34 two-byte letters + 4 = 72 bytes must pass"
    assert not _accepts("Aa1!" + "é" * 35), "39 characters / 74 bytes: the cap counts BYTES"
    assert not _accepts("Aa1!" + "😀" * 18), "22 characters / 76 bytes must be refused"
    try:
        _validate_password_strength("Aa1!" + "b" * 69)
    except ValueError as e:
        assert "72 bytes" in str(e), f"the too-long refusal must name the byte cap: {e}"
    # Whitespace at either end, and every missing class named at once.
    assert not _accepts(" Aa1!bcde") and not _accepts("Aa1!bcde ")
    try:
        _validate_password_strength("aaaaaaaa")
    except ValueError as e:
        msg = str(e).lower()
        assert all(w in msg for w in ("uppercase", "number", "symbol")), (
            f"a refusal must name every missing class: {e}"
        )
    else:
        raise AssertionError("'aaaaaaaa' passed the password rule")


def test_design_doc_quotes_the_link_pages_real_copy():
    """C4 / BE-AUTH-02: §9.1 quotes the success page, which now points at Forgot password."""
    from app.api import email_confirm_page as page

    sec = _flat(_design_section_9_1())
    quoted = re.search(r'success: "([^"]+)"', sec)
    assert quoted, "§9.1 no longer quotes the link page's success copy as success: \"…\""
    assert quoted.group(1) == _flat(page.COPY_SUCCESS), (
        f"§9.1 quotes {quoted.group(1)!r}, but the page says {page.COPY_SUCCESS!r}"
    )
    assert "Forgot password" in page.COPY_SUCCESS
    assert "newest" in page.COPY_FAILURE.lower(), "the failure copy must send readers to the NEWEST email"
    assert "each new email replaces the earlier ones" in sec.lower()


def test_design_doc_names_the_access_log_redaction_and_it_holds():
    """C6 / DOC-5: the doc says `token_hash` never reaches our logs. Hold it to that for the
    redaction every log line (and the access-log filter's rewrite) goes through."""
    import hashlib

    from app.log_redaction import redact_secrets

    sec = _flat(_design_section_9_1())
    for needle in ("`uvicorn.access`", "`token_hash`", "exactly five", "`sb-forwarded-for`",
                   "`client_ip`", "`app.api.email_confirm_page`", "logs an expired signature at DEBUG"):
        assert needle in sec, f"§9.1's logging paragraph lost {needle!r}"
    token_hash = hashlib.sha224(b"someone@example.com12345678").hexdigest()
    line = f'10.0.0.1:5555 - "GET /auth/confirm?token_hash={token_hash}&type=signup HTTP/1.1" 200'
    out = redact_secrets(line)
    assert token_hash not in out, f"redact_secrets leaves the link's token_hash in place: {out}"
    assert "type=signup" in out, "anti-vacuity: the redaction must not blank the whole line"


def test_design_doc_auth_tokens_row_names_the_real_purge_keys():
    """SEC-8: the fresh-install purge decides on two keys; the doc must name the real ones."""
    text = _read(_DESIGN)
    sec92 = text[text.index("### 9.2 "):text.index("### 9.3 ")]
    (row,) = [line for line in sec92.splitlines() if line.startswith("| Auth tokens |")]
    for needle in ("`AuthService.purgeSessionLeftByAnEarlierInstall`", "`auth.install-marker.v1`",
                   "`widget.api.baseURL`", "An update keeps the session",
                   "The device proof and the guest install id are never cleared."):
        assert needle in row, f"§9.2's Auth tokens row lost {needle!r}"

    def code(path: Path) -> str:
        return re.sub(r"//[^\n]*", "", _read(path))

    ios = _REPO / "frontend" / "ios"
    auth = code(ios / "ios" / "Core" / "Services" / "AuthService.swift")
    assert re.search(r'installMarkerKey\s*=\s*"auth\.install-marker\.v1"', auth)
    assert re.search(r"\bfunc purgeSessionLeftByAnEarlierInstall\b", auth)
    widget = code(ios / "Shared" / "WidgetAPIConfig.swift")
    assert re.search(r'baseURLOverrideKey\s*=\s*"widget\.api\.baseURL"', widget)


def test_readme_names_the_forwarding_switch_and_the_links_password():
    text = _flat(_read(_README))
    assert "IP Address Forwarding" in text
    assert "FIRST registered" in text and "Forgot password" in text
    assert "`AUTH_CONFIRMED_SIGN_IN_FAILED`" in text and "`AUTH_CODE_USED`" in text


def test_launch_checklist_rate_limits_are_conditional_and_forwarding_has_a_row():
    if not _CHECKLIST.exists():
        pytest.skip("LAUNCH_CHECKLIST.md is gitignored and absent here (git archive / CI)")
    text = _CHECKLIST.read_text(encoding="utf-8")
    rows = {m.group(1): m.group(0) for m in re.finditer(r"(?m)^\| \*\*([^*]+)\*\* \|.*$", text)}
    assert "IP Address Forwarding" in rows, "§5b(a) lost its IP Address Forwarding row"
    assert "`security_sb_forwarded_for_enabled`" in rows["IP Address Forwarding"]
    assert "`sb_secret_`" in rows["IP Address Forwarding"]
    rate = rows["Rate limits"]
    assert "Raise all three for a single-IP backend" not in rate
    assert "**While IP Address Forwarding is OFF**" in rate and "**Once it is ON**" in rate
    pw = rows["Password requirements"]
    assert "never as a superset" in pw and "**72 bytes**" in pw
    assert "must never be STRICTER" not in pw


# MUTATION_LOG (2026-11-26, by hand via a scratch script, each file restored afterwards; the
# route test was deselected because the /auth/confirm route had not landed yet):
#   M1  add `<a href="{{ .ConfirmationURL }}">x</a>` to confirm-signup.html
#       -> test_confirm_signup_carries_the_code_and_exactly_the_contract_link FAILED   ✅
#       -> test_confirm_signup_says_confirm_never_reset FAILED                         ✅
#   M2  `type=signup` -> `type=email` in the confirm link
#       -> test_confirm_signup_carries_the_code_and_exactly_the_contract_link FAILED   ✅
#   M3  append the reset body's "your password won't change" to confirm-signup.html
#       -> test_confirm_signup_says_confirm_never_reset FAILED                         ✅
#   M4  `{{ .Token }}` -> `{{ .token }}` in reset-password.html
#       -> test_reset_password_carries_the_code_only_and_never_confirm_wording FAILED  ✅
#   M5  the README's reset subject -> "Reset Your Password"
#       -> test_readme_names_both_templates_with_their_exact_subjects FAILED           ✅
#   M6  "The code expires shortly." -> "The 6-digit code expires shortly." (reset)
#       -> test_templates_never_state_a_code_length[reset] FAILED                      ✅
#       -> test_reset_password_carries_the_code_only_and_never_confirm_wording FAILED  ✅
#   M7  drop the `</p>` after the code in confirm-signup.html
#       -> test_each_template_is_well_formed_html_…[confirm] FAILED                    ✅
#   M8  reset-password.html `<title>` -> "Reset password"
#       -> test_each_template_title_matches_its_readme_subject[Reset Password] FAILED  ✅
#   M9  add "6-digit" to LAUNCH_CHECKLIST.md; M9b drop its reset-template path
#       -> test_launch_checklist_points_at_both_files_and_holds_no_inline_template FAILED (both) ✅
#   M10 un-mark the superseded reset-only tick in OWNER_TASKS.md
#       -> test_owner_tasks_points_at_both_files_and_retires_the_reset_only_tick FAILED ✅
#   M11 restore "refresh tokens 7 d and rotate" in §9.1
#       -> test_design_doc_describes_the_code_flow_and_keeps_its_corrections FAILED    ✅
#   M12 rename the link's `token_hash=` parameter to `hash=` in confirm-signup.html
#       -> test_the_page_accepts_the_link_the_template_renders FAILED                  ✅
#
# ROUND 2 (2026-11-26), IN MEMORY: `_read` / Path.read_text / module attributes patched per
# mutation, nothing on disk touched (scratch harness `mutate_doc_parity.py`). 23/23 killed:
#   M13 forwarding step no longer "only AFTER step 1's deploy"       -> …ip_forwarding_only_after_the_deploy ✅
#   M14 "raise three limits for a single-IP backend" restored        -> …ip_forwarding_only_after_the_deploy ✅
#   M15 a 20-letter project ref in the Management API URL            -> …carries_the_supabase_project_ref     ✅
#   M16 the new-build bullet promises an email again                 -> …stuck_users_step_splits_by_app_version ✅
#   M17 "any placeholder value"; M17b "Re-paste the previous template" -> …rollback_restores_the_exact_secret… ✅✅
#   M18 two steps numbered 3                                         -> …steps_are_numbered_one_to_n_in_order ✅
#   M19 "**Supabase sees one caller.**" restored in §9.1             -> …forwarding_is_conditional_on_the_owner_switch ✅
#   M20 the link residual paragraph removed                          -> …records_the_link_password_residual… ✅
#   M21 AUTH_CODE_USED action retry_later; M22 confirmed status 401; M22b row un-backticked
#                                                                    -> …code_table_matches_the_error_contract ✅✅✅
#   M23 §9.1 names AUTH_CODE_SPENT                                   -> …auth_error_code_the_docs_name_is_a_real… ✅
#   M24 the backslash dropped from the documented symbol set         -> …states_gotrues_exact_symbol_set      ✅
#   M25 validator reverted to the superset (any non-alnum, 128 chars) -> …password_rule_is_the_validators_rule ✅
#   M26 page COPY_SUCCESS reverted; M26b the doc's quote drifts      -> …quotes_the_link_pages_real_copy      ✅✅
#   M27 redact_secrets made the identity                             -> …access_log_redaction_and_it_holds    ✅
#   M28 doc marker key v2; M28b AuthService.swift key v2             -> …auth_tokens_row_names_the_real_purge_keys ✅✅
#   M29 README drops "IP Address Forwarding"                         -> …readme_names_the_forwarding_switch…  ✅
#   M30 checklist single-IP row restored; M30b "never STRICTER" restored -> …checklist_rate_limits_are_conditional… ✅✅
