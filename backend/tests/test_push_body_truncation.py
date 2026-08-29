"""The inbox row keeps the WHOLE reason; only the lock-screen banner is shortened.

WHY THIS FILE EXISTS. `updates_insight_sweeper` sent `body=headline[:180]`, and that one
slice was the only body the system ever had: the same string went to APNs *and* into
`notification_events.body`. Every stored `ticker_move` body was therefore exactly 180
characters, ending mid-word — a real row read "…significantly beating analyst estimates of
$3.27, and sub". Measured in production: 12 of 12 recent bodies were exactly 180.

That defeated the Activity detail screen, which exists precisely to show the catalyst in
full and could only ever show a fragment. And note which text lost: a short fallback
headline ("Hydrogen Stocks Face Selloff", ~40-60 chars) passed through untouched, while the
grounded, cited catalyst — the sentence actually worth reading — was the one cut.

Truncation now happens at the APNs boundary, which is the layer that has the constraint.
"""

import pytest

from app.config import settings
import app.services.push_service as ps
from app.services.push_service import (
    BANNER_BODY_LIMIT,
    LEDGER_BODY_LIMIT,
    PushService,
    truncate_for_banner,
)
from app.services.push_dispatch_service import PushDispatchService


_REAL_CATALYST = (
    "Salesforce (CRM) shares surged after the company reported second-quarter fiscal 2027 "
    "adjusted earnings per share of $5.90, significantly beating analyst estimates of $3.27, "
    "and subsequently raised its full-year revenue guidance."
)


# ── the helper ───────────────────────────────────────────────────────────────


def test_a_short_body_is_returned_untouched():
    """No ellipsis on text that fits. The fallback headlines are all well under the limit,
    and appending "…" to a complete sentence would invent a truncation."""
    short = "Hydrogen Stocks Face Selloff"
    assert truncate_for_banner(short) == short


def test_truncation_never_cuts_mid_word():
    """The reported symptom, exactly: "…estimates of $3.27, and sub"."""
    out = truncate_for_banner(_REAL_CATALYST)
    assert out.endswith("…")
    tail = out.rstrip("…").split()[-1]
    assert tail in _REAL_CATALYST.split(), (
        f"last token {tail!r} is not a whole word from the source — the cut landed mid-word"
    )


# The first case is engineered so the word boundary lands EXACTLY after a comma; the second
# is the real catalyst. Only the first actually exercises the strip — asserting the property
# on the real string alone passed with the strip deleted, because that string's boundary
# happens to fall on a plain word. Caught by mutation.
@pytest.mark.parametrize(
    "text,limit",
    [("aaaa bbbb cccc, dddd eeee", 20), (_REAL_CATALYST, BANNER_BODY_LIMIT)],
)
def test_truncation_does_not_leave_a_dangling_separator(text, limit):
    """"guidance,…" reads as a typo, not as an abbreviation."""
    out = truncate_for_banner(text, limit)
    assert out.endswith("…"), "this case is supposed to truncate"
    assert not out.rstrip("…").endswith((",", ";", ":", "-", "—", " ")), (
        f"a separator survived before the ellipsis: {out!r}"
    )


@pytest.mark.parametrize("limit", [40, 80, 180, 500])
def test_the_result_never_exceeds_the_limit(limit):
    out = truncate_for_banner(_REAL_CATALYST, limit)
    assert len(out) <= limit, f"{len(out)} > {limit}"


def test_a_single_enormous_word_is_still_bounded():
    """A URL or a run-on token has no space to break on. Honouring a word boundary blindly
    would return almost nothing (or the whole string); it must still cut near the limit."""
    out = truncate_for_banner("x" * 400, 180)
    assert len(out) <= 180
    assert out.endswith("…")
    assert len(out) > 100, "a boundary-less string was thrown away instead of cut"


@pytest.mark.parametrize("value", ["", None, "   "])
def test_empty_input_is_safe(value):
    assert truncate_for_banner(value) == ""


# ── the separation: banner short, ledger whole ───────────────────────────────


def test_the_ledger_keeps_far_more_than_the_banner():
    """If these ever converge, storing the full reason stops meaning anything."""
    assert LEDGER_BODY_LIMIT > BANNER_BODY_LIMIT * 4, (
        f"ledger limit {LEDGER_BODY_LIMIT} is no longer meaningfully larger than the banner's "
        f"{BANNER_BODY_LIMIT} — the detail screen is back to showing a fragment"
    )


@pytest.mark.asyncio
async def test_the_apns_payload_is_shortened(monkeypatch):
    for name, value in (
        ("APNS_KEY_ID", "K"), ("APNS_TEAM_ID", "T"),
        ("APNS_AUTH_KEY", "-----PEM-----"), ("APNS_BUNDLE_ID", "com.phan.caydex"),
    ):
        monkeypatch.setattr(settings, name, value)

    captured = {}

    class _Resp:
        status_code = 200
        text = ""
        def json(self): return {}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr(ps.httpx, "AsyncClient", lambda **kw: _Client())
    svc = PushService()
    monkeypatch.setattr(svc, "_provider_jwt", lambda: "jwt")
    await svc.send_to_user(
        "u1", title="CRM +3.4%", body=_REAL_CATALYST,
        devices=[{"token": "t" * 64, "environment": "production"}],
    )
    sent_body = captured["json"]["aps"]["alert"]["body"]
    assert len(sent_body) <= BANNER_BODY_LIMIT
    assert sent_body.endswith("…")


def test_the_stored_row_keeps_the_full_reason():
    """The row the detail screen reads must NOT be the banner's 180 characters."""
    stored = {}

    class _Table:
        def insert(self, row):
            stored.update(row)
            return self
        def execute(self):
            return self

    class _Supa:
        def table(self, *_a, **_k): return _Table()

    svc = object.__new__(PushDispatchService)
    svc.supabase = _Supa()
    assert svc.claim_send("u1", "k1", kind="ticker_move", category="watchlist",
                          title="CRM +3.4%", body=_REAL_CATALYST) is True

    assert stored["body"] == _REAL_CATALYST, (
        "the ledger stored a truncated body — this is the original bug: the detail screen "
        "can only ever show what was written here"
    )
    assert len(stored["body"]) > BANNER_BODY_LIMIT


def test_the_sender_does_not_pre_truncate():
    """Source scan: the slice must not come back.

    Re-adding it anywhere upstream silently re-breaks the ledger, and nothing about the
    running system would look wrong — the notification still arrives, the row still says
    `sent`, and the body is simply short.
    """
    import inspect
    from app.services import updates_insight_sweeper

    src = inspect.getsource(updates_insight_sweeper)
    code = "\n".join(
        "" if line.strip().startswith("#") else line for line in src.splitlines()
    )
    assert "headline[:180]" not in code, (
        "updates_insight_sweeper truncates the body again. That string is ALSO the inbox "
        "row; shorten it at the APNs boundary (`truncate_for_banner`) instead."
    )
